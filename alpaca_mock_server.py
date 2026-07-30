# ============================================================
#  Alpaca Mock Server -- simulator mount GoTo (ASCOM Alpaca REST API)
#  Buat testing integrasi teleskop TANPA hardware asli. Implementasi
#  minimal-tapi-cukup dari ITelescopeV4 (subset yg dipakai alpyca /
#  KontrolTeleskop di kontrol_teleskop.py): Connected, Tracking, slew
#  RA/Dec & Alt/Az (async, disimulasikan bergerak halus ~2 detik, bukan
#  instan), AbortSlew, plus properti umum (Name, Description, Can*).
#
#  Protokol Alpaca: HTTP GET/PUT ke /api/v1/{device_type}/{device_number}/
#  {attribute}, param ClientID & ClientTransactionID wajib ada (dikirim
#  otomatis oleh alpyca), respons JSON {"Value":..., "ErrorNumber":0,
#  "ErrorMessage":""}. Dicek langsung ke source alpyca (device.py) biar
#  persis, bukan dugaan dari dokumentasi.
#
#  Jalankan: python3 alpaca_mock_server.py [--port 11111]
#  Lalu di HisabWin/kontrol_teleskop.py, sambung ke "127.0.0.1:11111".
# ============================================================
import argparse
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DEVICE_TYPE = "telescope"
DEVICE_NUMBER = 0
DURASI_SLEW_DETIK = 2.5  # simulasi mount butuh waktu bergerak, bukan instan


class MountSimulasi:
    """Status & gerakan mount tiruan -- satu instance dipakai bersama oleh
    semua request (mount fisik cuma satu, meski banyak client bisa nanya)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.terhubung = False
        self.tracking = False
        self.slewing = False
        self.parked = False
        # Posisi mulai netral (RA=jam sideris kasar, Dec=0, Alt/Az langit
        # tengah) -- tidak penting persis krn cuma simulasi.
        self.ra_jam = 0.0
        self.dec_deg = 0.0
        self.az_deg = 180.0
        self.alt_deg = 45.0
        self._target = None  # ("radec", ra, dec) atau ("altaz", az, alt)
        self._thread_slew = None

    def _gerak_halus(self, mode, a, b):
        """Jalan di thread terpisah: interpolasi linear posisi selama
        DURASI_SLEW_DETIK, lalu tandai slewing selesai. Linear cukup utk
        simulasi -- mount asli jelas tidak linear (percepatan/perlambatan),
        tapi yg mau diuji di sini cuma "apakah HisabWin bisa mengirim
        perintah & mendeteksi slew selesai dgn benar", bukan realisme
        fisik gerakan mount."""
        langkah = 40
        jeda = DURASI_SLEW_DETIK / langkah
        with self.lock:
            if mode == "radec":
                a0, b0 = self.ra_jam, self.dec_deg
            else:
                a0, b0 = self.az_deg, self.alt_deg
        for i in range(1, langkah + 1):
            time.sleep(jeda)
            frac = i / langkah
            with self.lock:
                if mode == "radec":
                    self.ra_jam = a0 + (a - a0) * frac
                    self.dec_deg = b0 + (b - b0) * frac
                else:
                    self.az_deg = a0 + (a - a0) * frac
                    self.alt_deg = b0 + (b - b0) * frac
        with self.lock:
            self.slewing = False
            self._target = None

    def mulai_slew_radec(self, ra_jam, dec_deg):
        with self.lock:
            self.slewing = True
            self._target = ("radec", ra_jam, dec_deg)
        self._thread_slew = threading.Thread(
            target=self._gerak_halus, args=("radec", ra_jam, dec_deg), daemon=True)
        self._thread_slew.start()

    def mulai_slew_altaz(self, az_deg, alt_deg):
        with self.lock:
            self.slewing = True
            self._target = ("altaz", az_deg, alt_deg)
        self._thread_slew = threading.Thread(
            target=self._gerak_halus, args=("altaz", az_deg, alt_deg), daemon=True)
        self._thread_slew.start()

    def abort(self):
        with self.lock:
            self.slewing = False
            self._target = None


mount = MountSimulasi()

# nama_attribute -> (getter, tipe) buat GET; ditambah default kapabilitas
_GET_STATIS = {
    "name": lambda: "HisabWin Mock Mount",
    "description": lambda: "Simulator mount GoTo -- HANYA utk testing, bukan hardware asli",
    "driverinfo": lambda: "alpaca_mock_server.py (HisabWin project)",
    "driverversion": lambda: "1.0",
    "interfaceversion": lambda: 4,
    "supportedactions": lambda: [],
    "canslew": lambda: True,
    "canslewasync": lambda: True,
    "canslewaltaz": lambda: True,
    "canslewaltazasync": lambda: True,
    "cansync": lambda: True,
    "cansyncaltaz": lambda: True,
    "cansettracking": lambda: True,
    "canpark": lambda: False,
    "canunpark": lambda: False,
    "canfindhome": lambda: False,
    "alignmentmode": lambda: 0,
    "equatorialsystem": lambda: 1,  # J2000
    "doesrefraction": lambda: False,
    "sitelatitude": lambda: -6.583140,
    "sitelongitude": lambda: 106.631207,
    "siteelevation": lambda: 229.0,
    "athome": lambda: False,
    "atpark": lambda: False,
}


class AlpacaHandler(BaseHTTPRequestHandler):
    server_version = "HisabWinAlpacaMock/1.0"

    def log_message(self, fmt, *args):
        print(f"[mock] {self.address_string()} {fmt % args}")

    def _kirim_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, value):
        self._kirim_json({"Value": value, "ClientTransactionID": 0,
                           "ServerTransactionID": 0, "ErrorNumber": 0, "ErrorMessage": ""})

    def _error(self, nomor, pesan):
        self._kirim_json({"ClientTransactionID": 0, "ServerTransactionID": 0,
                           "ErrorNumber": nomor, "ErrorMessage": pesan})

    def _parse_path(self):
        parsed = urlparse(self.path)
        bagian = [p for p in parsed.path.split("/") if p]
        # ["api", "v1", "telescope", "0", "<attribute>"]
        if len(bagian) != 5 or bagian[0] != "api" or bagian[1] != "v1":
            return None
        _, _, jenis, nomor, atribut = bagian
        return jenis.lower(), int(nomor), atribut.lower(), dict(parse_qs(parsed.query))

    def do_GET(self):
        info = self._parse_path()
        if info is None:
            self._error(0x400, "Path tidak dikenali")
            return
        jenis, nomor, atribut, _q = info
        if jenis != DEVICE_TYPE or nomor != DEVICE_NUMBER:
            self._error(0x400, f"Device {jenis}/{nomor} tidak ada di mock ini")
            return

        if atribut == "connected":
            self._ok(mount.terhubung)
        elif atribut == "tracking":
            self._ok(mount.tracking)
        elif atribut == "slewing":
            self._ok(mount.slewing)
        elif atribut == "rightascension":
            self._ok(mount.ra_jam)
        elif atribut == "declination":
            self._ok(mount.dec_deg)
        elif atribut == "azimuth":
            self._ok(mount.az_deg)
        elif atribut == "altitude":
            self._ok(mount.alt_deg)
        elif atribut == "siderealtime":
            self._ok((time.time() / 3600.0) % 24.0)  # asal-asalan, bukan sideris asli
        elif atribut in _GET_STATIS:
            self._ok(_GET_STATIS[atribut]())
        else:
            self._error(0x400, f"Atribut GET '{atribut}' tidak diimplementasikan di mock")

    def do_PUT(self):
        info = self._parse_path()
        if info is None:
            self._error(0x400, "Path tidak dikenali")
            return
        jenis, nomor, atribut, _q = info
        if jenis != DEVICE_TYPE or nomor != DEVICE_NUMBER:
            self._error(0x400, f"Device {jenis}/{nomor} tidak ada di mock ini")
            return

        panjang = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(panjang).decode("utf-8") if panjang else ""
        data = {k: v[0] for k, v in parse_qs(body).items()}

        if atribut == "connected":
            nilai = data.get("Connected", "true").lower() == "true"
            mount.terhubung = nilai
            self._ok(None)
        elif atribut == "tracking":
            if not mount.terhubung:
                self._error(0x407, "Belum Connected")
                return
            mount.tracking = data.get("Tracking", "true").lower() == "true"
            self._ok(None)
        elif atribut in ("slewtocoordinatesasync", "slewtocoordinates"):
            if not mount.terhubung:
                self._error(0x407, "Belum Connected")
                return
            if not mount.tracking:
                self._error(0x40B, "Tracking harus ON dulu sebelum slew RA/Dec")
                return
            try:
                ra = float(data["RightAscension"])
                dec = float(data["Declination"])
            except (KeyError, ValueError):
                self._error(0x401, "RightAscension/Declination tidak valid")
                return
            if not (-90.0 <= dec <= 90.0):
                self._error(0x401, "Declination di luar -90..90")
                return
            mount.mulai_slew_radec(ra, dec)
            self._ok(None)
        elif atribut in ("slewtoaltazasync", "slewtoaltaz"):
            if not mount.terhubung:
                self._error(0x407, "Belum Connected")
                return
            try:
                az = float(data["Azimuth"])
                alt = float(data["Altitude"])
            except (KeyError, ValueError):
                self._error(0x401, "Azimuth/Altitude tidak valid")
                return
            if not (-90.0 <= alt <= 90.0):
                self._error(0x401, "Altitude di luar -90..90")
                return
            mount.mulai_slew_altaz(az % 360.0, alt)
            self._ok(None)
        elif atribut == "abortslew":
            mount.abort()
            self._ok(None)
        else:
            self._error(0x400, f"Atribut PUT '{atribut}' tidak diimplementasikan di mock")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=11111)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AlpacaHandler)
    print(f"Alpaca mock server jalan di http://{args.host}:{args.port} "
          f"(device: telescope/0) -- Ctrl+C utk berhenti")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
