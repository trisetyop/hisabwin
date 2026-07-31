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
import random
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DEVICE_TYPE = "telescope"
DEVICE_NUMBER = 0
DEVICE_TIPE_DIDUKUNG = {"telescope", "camera"}  # dua device di SATU mock server ini
DURASI_SLEW_DETIK = 2.5  # simulasi mount butuh waktu bergerak, bukan instan
SITE_LAT_DEG = -6.583140
SITE_LON_DEG = 106.631207


# --- Konversi Alt/Az <-> RA/Dec ------------------------------------------
# Mount ASLI tahu Alt/Az & RA/Dec SEKALIGUS scr konsisten (dua arah pandang
# yg sama, dihitung dari waktu+lokasi) -- mock ini SEBELUMNYA cuma update
# koordinat yg persis diperintah slew (mis. Alt/Az doang kalau slew lewat
# arahkan_ke_altaz), jadi RA/Dec ketinggalan di nilai awal 0.0 selamanya.
# Diperbaiki dgn transformasi standar astronomi bola (dicek round-trip
# numerik dulu sblm dipasang -- Alt/Az->RA/Dec->Alt/Az balik ke titik yg
# sama persis utk beberapa kasus uji, termasuk deklinasi tinggi). LST pakai
# rumus GMST standar (akurasi detik busur, lebih dari cukup utk mock/
# testing -- BUKAN dimaksudkan presisi observasi sungguhan).
def _julian_date_utc_sekarang():
    u = datetime.now(timezone.utc)
    a = (14 - u.month) // 12
    y = u.year + 4800 - a
    m = u.month + 12 * a - 3
    jdn = u.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    jam_pecahan = (u.hour + u.minute / 60.0 + u.second / 3600.0 + u.microsecond / 3.6e9) / 24.0
    return jdn + jam_pecahan - 0.5


def _lst_derajat(lon_deg):
    jd = _julian_date_utc_sekarang()
    d = jd - 2451545.0
    gmst = (280.46061837 + 360.98564736629 * d) % 360.0
    return (gmst + lon_deg) % 360.0


def _altaz_ke_radec(az_deg, alt_deg, lat_deg=SITE_LAT_DEG, lon_deg=SITE_LON_DEG):
    az, alt, lat = map(math.radians, (az_deg, alt_deg, lat_deg))
    dec = math.asin(math.sin(alt) * math.sin(lat) + math.cos(alt) * math.cos(lat) * math.cos(az))
    H = math.atan2(-math.sin(az) * math.cos(alt),
                    math.cos(lat) * math.sin(alt) - math.sin(lat) * math.cos(alt) * math.cos(az))
    ra_deg = (_lst_derajat(lon_deg) - math.degrees(H)) % 360.0
    return ra_deg / 15.0, math.degrees(dec)


def _radec_ke_altaz(ra_jam, dec_deg, lat_deg=SITE_LAT_DEG, lon_deg=SITE_LON_DEG):
    H_deg = (_lst_derajat(lon_deg) - ra_jam * 15.0 + 180.0) % 360.0 - 180.0
    H, dec, lat = map(math.radians, (H_deg, dec_deg, lat_deg))
    alt = math.asin(math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(H))
    az = math.atan2(math.sin(H), math.cos(H) * math.sin(lat) - math.tan(dec) * math.cos(lat))
    return (math.degrees(az) + 180.0) % 360.0, math.degrees(alt)


class MountSimulasi:
    """Status & gerakan mount tiruan -- satu instance dipakai bersama oleh
    semua request (mount fisik cuma satu, meski banyak client bisa nanya)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.terhubung = False
        self.tracking = False
        self.slewing = False
        self.parked = False
        # Posisi awal netral: Az=180 (Selatan), Alt=45 -- RA/Dec dihitung
        # KONSISTEN dari situ (bukan 0.0 sembarangan spt sebelumnya).
        self.az_deg = 180.0
        self.alt_deg = 45.0
        self.ra_jam, self.dec_deg = _altaz_ke_radec(self.az_deg, self.alt_deg)
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
                    self.az_deg, self.alt_deg = _radec_ke_altaz(self.ra_jam, self.dec_deg)
                else:
                    self.az_deg = a0 + (a - a0) * frac
                    self.alt_deg = b0 + (b - b0) * frac
                    self.ra_jam, self.dec_deg = _altaz_ke_radec(self.az_deg, self.alt_deg)
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


class KameraSimulasi:
    """Kamera tiruan -- simulasikan exposure ASYNC (StartExposure lalu
    ImageReady jadi True stlh durasi berlalu, bukan instan) & hasilkan
    gambar SINTETIS (blob terang + noise, BUKAN foto langit sungguhan --
    cukup buat nguji jalur live-view: capture berulang -> tampil ->
    simpan manual). Pola async-nya SENGAJA sama persis dgn MountSimulasi
    (thread terpisah + lock) drpd bikin pendekatan baru."""

    LEBAR, TINGGI = 320, 240  # kecil sengaja -- cukup utk uji pipeline, bukan resolusi asli

    def __init__(self):
        self.lock = threading.Lock()
        self.terhubung = False
        self.state = 0          # CameraStates: 0=Idle,2=Exposing,3=Reading,4=Download
        self.image_ready = False
        self.durasi_terakhir = 0.0
        self.gambar = None       # list-of-list (row-major), None kalau blm pernah exposure
        self._thread_exposure = None
        self._n_exposure = 0    # dipakai geser posisi blob tiap capture, biar preview "hidup"

    def mulai_exposure(self, durasi_detik, cahaya=True):
        with self.lock:
            self.state = 2  # Exposing
            self.image_ready = False
            self.durasi_terakhir = durasi_detik
        self._thread_exposure = threading.Thread(
            target=self._kerja_exposure, args=(durasi_detik,), daemon=True)
        self._thread_exposure.start()

    def _kerja_exposure(self, durasi_detik):
        time.sleep(max(durasi_detik, 0.05))
        with self.lock:
            self.state = 3  # Reading
        time.sleep(0.15)  # simulasi waktu baca sensor, singkat
        with self.lock:
            self.state = 4  # Download
            self._n_exposure += 1
            self.gambar = self._buat_gambar_sintetis(self._n_exposure)
        time.sleep(0.05)
        with self.lock:
            self.state = 0  # Idle
            self.image_ready = True

    def abort_exposure(self):
        with self.lock:
            self.state = 0
            self.image_ready = False

    def _buat_gambar_sintetis(self, n_exposure):
        """Blob terang (mensimulasikan piringan Bulan/Matahari) yg
        posisinya GESER TIAP EXPOSURE (spt objek beneran bergerak
        pelan di FOV krn rotasi Bumi/mount belum sinkron sempurna) +
        noise acak -- stdlib random/math doang, TANPA numpy (mock ini
        sengaja dependency-minim)."""
        cx = self.LEBAR / 2 + 15 * math.sin(n_exposure * 0.3)
        cy = self.TINGGI / 2 + 10 * math.cos(n_exposure * 0.3)
        radius = 28
        img = []
        for y in range(self.TINGGI):
            baris = []
            for x in range(self.LEBAR):
                d2 = (x - cx) ** 2 + (y - cy) ** 2
                dasar = 3000 if d2 <= radius * radius else 200
                noise = random.randint(-150, 150)
                baris.append(max(0, min(65535, dasar + noise)))
            img.append(baris)
        return img


mount = MountSimulasi()
kamera = KameraSimulasi()


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
    "sitelatitude": lambda: SITE_LAT_DEG,
    "sitelongitude": lambda: SITE_LON_DEG,
    "siteelevation": lambda: 229.0,
    "athome": lambda: False,
    "atpark": lambda: False,
}

_GET_STATIS_KAMERA = {
    "name": lambda: "HisabWin Mock Camera",
    "description": lambda: "Simulator kamera -- gambar SINTETIS (blob+noise), bukan foto asli",
    "driverinfo": lambda: "alpaca_mock_server.py (HisabWin project)",
    "driverversion": lambda: "1.0",
    "interfaceversion": lambda: 3,
    "supportedactions": lambda: [],
    "pixelsizex": lambda: 5.0,
    "pixelsizey": lambda: 5.0,
    "electronsperadu": lambda: 1.0,
    "fullwellcapacity": lambda: 65535.0,
    "readoutmode": lambda: 0,
    "readoutmodes": lambda: ["Normal"],
    "fastreadout": lambda: False,
    "cooleron": lambda: False,
    "coolerpower": lambda: 0.0,
    "ccdtemperature": lambda: 20.0,
    "heatsinktemperature": lambda: 20.0,
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
        if jenis not in DEVICE_TIPE_DIDUKUNG or nomor != DEVICE_NUMBER:
            self._error(0x400, f"Device {jenis}/{nomor} tidak ada di mock ini")
            return

        if jenis == "camera":
            self._get_kamera(atribut)
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

    def _get_kamera(self, atribut):
        if atribut == "connected":
            self._ok(kamera.terhubung)
        elif atribut == "camerastate":
            self._ok(kamera.state)
        elif atribut == "imageready":
            self._ok(kamera.image_ready)
        elif atribut == "imagearray":
            with kamera.lock:
                if kamera.gambar is None:
                    self._error(0x40D, "Belum ada exposure yg selesai")
                    return
                gambar = kamera.gambar
            self._ok(gambar)  # JSON polos (list-of-list) -- alpyca otomatis
                               # deteksi ini BUKAN application/imagebytes lewat
                               # Content-Type & pakai jalur JSON (dicek langsung
                               # ke source alpyca camera.py: _get_imagedata()),
                               # jadi TIDAK perlu implementasi ImageBytes biner.
        elif atribut == "lastexposureduration":
            self._ok(kamera.durasi_terakhir)
        elif atribut == "cameraxsize":
            self._ok(KameraSimulasi.LEBAR)
        elif atribut == "cameraysize":
            self._ok(KameraSimulasi.TINGGI)
        elif atribut in ("maxadu",):
            self._ok(65535)
        elif atribut in ("sensortype",):
            self._ok(0)  # Monochrome
        elif atribut in ("sensorname",):
            self._ok("MockSensor")
        elif atribut in ("hasshutter",):
            self._ok(False)
        elif atribut in ("canabortexposure", "canstopexposure", "canfastreadout",
                          "canasymmetricbin", "cansetccdtemperature", "canpulseguide",
                          "cangetcoolerpower"):
            self._ok(False)
        elif atribut in ("exposuremin",):
            self._ok(0.001)
        elif atribut in ("exposuremax",):
            self._ok(600.0)
        elif atribut in ("exposureresolution",):
            self._ok(0.001)
        elif atribut in ("gain", "gainmin", "offset", "offsetmin", "binx", "biny",
                          "startx", "starty"):
            self._ok(0 if atribut != "binx" and atribut != "biny" else 1)
        elif atribut in ("gainmax", "offsetmax"):
            self._ok(100)
        elif atribut in ("maxbinx", "maxbiny"):
            self._ok(1)
        elif atribut in ("numx",):
            self._ok(KameraSimulasi.LEBAR)
        elif atribut in ("numy",):
            self._ok(KameraSimulasi.TINGGI)
        elif atribut in _GET_STATIS_KAMERA:
            self._ok(_GET_STATIS_KAMERA[atribut]())
        else:
            self._error(0x400, f"Atribut GET kamera '{atribut}' tidak diimplementasikan di mock")

    def do_PUT(self):
        info = self._parse_path()
        if info is None:
            self._error(0x400, "Path tidak dikenali")
            return
        jenis, nomor, atribut, _q = info
        if jenis not in DEVICE_TIPE_DIDUKUNG or nomor != DEVICE_NUMBER:
            self._error(0x400, f"Device {jenis}/{nomor} tidak ada di mock ini")
            return

        panjang = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(panjang).decode("utf-8") if panjang else ""
        data = {k: v[0] for k, v in parse_qs(body).items()}

        if jenis == "camera":
            self._put_kamera(atribut, data)
            return

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

    def _put_kamera(self, atribut, data):
        if atribut == "connected":
            kamera.terhubung = data.get("Connected", "true").lower() == "true"
            self._ok(None)
        elif atribut == "startexposure":
            if not kamera.terhubung:
                self._error(0x407, "Belum Connected")
                return
            try:
                durasi = float(data["Duration"])
            except (KeyError, ValueError):
                self._error(0x401, "Duration tidak valid")
                return
            cahaya = data.get("Light", "true").lower() == "true"
            kamera.mulai_exposure(durasi, cahaya)
            self._ok(None)
        elif atribut == "abortexposure":
            kamera.abort_exposure()
            self._ok(None)
        elif atribut in ("binx", "biny", "startx", "starty", "gain", "offset",
                         "fastreadout", "readoutmode"):
            self._ok(None)  # diterima tapi diabaikan -- mock tak butuh ini utk uji live-view
        else:
            self._error(0x400, f"Atribut PUT kamera '{atribut}' tidak diimplementasikan di mock")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=11111)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AlpacaHandler)
    print(f"Alpaca mock server jalan di http://{args.host}:{args.port} "
          f"(device: telescope/0, camera/0) -- Ctrl+C utk berhenti")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
