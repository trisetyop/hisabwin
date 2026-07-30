# ============================================================
#  kontrol_teleskop.py -- Lapisan integrasi mount GoTo ke HisabWin
#
#  Pakai library resmi ASCOM Initiative "alpyca" (pip install alpyca,
#  import sbg `alpaca`) -- BUKAN implementasi HTTP manual sendiri, biar
#  cocok dgn mount ASLI apapun yg support Alpaca (native, atau lewat
#  ASCOM Remote Server utk driver COM lama, atau lewat jembatan INDI->
#  Alpaca) TANPA HisabWin perlu tau bedanya.
#
#  Kenapa Alpaca (bukan ASCOM COM klasik / INDI langsung): HisabWin
#  ditarget cross-platform (Windows+Linux). ASCOM COM klasik cuma jalan
#  di Windows. INDI native kuat di Linux tapi binding Python-nya
#  (pyindi-client) butuh library C ter-compile, tidak portable enteng.
#  Alpaca murni HTTP+JSON -- persis spt requests.get() yg SUDAH dipakai
#  di seluruh file hisabwin.py ini utk AWS Terrain Tiles/Overpass, jadi
#  tidak nambah kelas dependency baru.
#
#  BELUM PUNYA MOUNT ASLI: pakai alpaca_mock_server.py (satu folder ini)
#  sbg pengganti sementara -- protokolnya identik, jadi kode di sini
#  TIDAK PERLU DIUBAH begitu nanti pakai mount sungguhan; tinggal ganti
#  host:port ke alamat mount aslinya.
# ============================================================
from alpaca.telescope import Telescope
from alpaca.exceptions import (
    NotConnectedException, InvalidValueException, InvalidOperationException,
    DriverException, AlpacaRequestException,
)


class KesalahanTeleskop(Exception):
    """Dibungkus dari exception alpyca supaya pemanggil (GUI HisabWin)
    cukup tangkap SATU jenis exception, tidak perlu tau detail internal
    alpyca/ASCOM."""


class KontrolTeleskop:
    """Satu mount GoTo, diakses lewat protokol ASCOM Alpaca.

    Pemakaian dasar:
        kt = KontrolTeleskop("127.0.0.1:11111")
        kt.sambung()
        kt.arahkan_ke_radec(ra_jam=5.2, dec_deg=18.7)
        while kt.sedang_slew():
            time.sleep(0.5)
        kt.putus()
    """

    def __init__(self, alamat, nomor_perangkat=0):
        """alamat: "host:port" mount Alpaca, mis. "127.0.0.1:11111" (mock)
        atau "192.168.1.50:11111" (mount asli di jaringan lokal)."""
        self.alamat = alamat
        self._t = Telescope(alamat, nomor_perangkat)
        self._tersambung = False

    # -- Koneksi --------------------------------------------------------
    def sambung(self):
        try:
            self._t.Connected = True
        except (AlpacaRequestException, DriverException) as e:
            raise KesalahanTeleskop(f"Gagal sambung ke mount di {self.alamat}: {e}") from e
        self._tersambung = True

    def putus(self):
        try:
            self._t.Connected = False
        except Exception:
            pass  # sengaja diabaikan -- "putus koneksi" tidak boleh gagal2 amat
        self._tersambung = False

    def tersambung(self):
        return self._tersambung

    def info_mount(self):
        """Nama & deskripsi mount -- buat ditampilkan di GUI biar user
        yakin nyambung ke mount yg benar (terutama kalau nanti ada lebih
        dari satu mount Alpaca di jaringan yg sama)."""
        try:
            return {"nama": self._t.Name, "deskripsi": self._t.Description}
        except Exception as e:
            raise KesalahanTeleskop(f"Gagal membaca info mount: {e}") from e

    # -- Slew (arahkan) ---------------------------------------------------
    def arahkan_ke_radec(self, ra_jam, dec_deg):
        """ra_jam: Right Ascension dlm JAM (0-24, BUKAN derajat -- ini
        konvensi Alpaca/ASCOM). dec_deg: Declination dlm derajat (-90..90).
        Mount HARUS support tracking equatorial (Tracking=True dulu)."""
        self._pastikan_tersambung()
        try:
            if not self._t.Tracking:
                self._t.Tracking = True
            self._t.SlewToCoordinatesAsync(ra_jam, dec_deg)
        except InvalidValueException as e:
            raise KesalahanTeleskop(f"Koordinat RA/Dec di luar jangkauan: {e}") from e
        except InvalidOperationException as e:
            raise KesalahanTeleskop(f"Mount menolak perintah slew (cek Tracking/Parked): {e}") from e
        except (AlpacaRequestException, DriverException) as e:
            raise KesalahanTeleskop(f"Gagal kirim perintah slew: {e}") from e

    def arahkan_ke_altaz(self, az_deg, alt_deg):
        """az_deg: Azimuth 0-360 dari Utara searah jarum jam (SAMA persis
        konvensi yg dipakai profil cakrawala/simulasi hilal di app ini,
        jadi bisa oper langsung tanpa konversi). alt_deg: Altitude
        -90..90. Butuh mount yg CanSlewAltAz (lihat cek_kapabilitas())."""
        self._pastikan_tersambung()
        try:
            self._t.SlewToAltAzAsync(az_deg % 360.0, alt_deg)
        except InvalidValueException as e:
            raise KesalahanTeleskop(f"Koordinat Az/Alt di luar jangkauan: {e}") from e
        except InvalidOperationException as e:
            raise KesalahanTeleskop(f"Mount menolak perintah slew Alt/Az: {e}") from e
        except (AlpacaRequestException, DriverException) as e:
            raise KesalahanTeleskop(f"Gagal kirim perintah slew: {e}") from e

    def hentikan_slew(self):
        try:
            self._t.AbortSlew()
        except Exception as e:
            raise KesalahanTeleskop(f"Gagal menghentikan slew: {e}") from e

    def sedang_slew(self):
        try:
            return bool(self._t.Slewing)
        except Exception as e:
            raise KesalahanTeleskop(f"Gagal membaca status slew: {e}") from e

    def posisi_sekarang(self):
        """Return dict posisi mount SEKARANG (bukan target) -- RA/Dec &
        Alt/Az sekaligus, buat dibandingkan dgn posisi hilal hasil
        simulasikan_hilal() di GUI (mis. tampilkan "mount vs target"
        biar user tau seberapa dekat sblm slew selesai)."""
        self._pastikan_tersambung()
        try:
            return {
                "ra_jam": self._t.RightAscension, "dec_deg": self._t.Declination,
                "az_deg": self._t.Azimuth, "alt_deg": self._t.Altitude,
            }
        except Exception as e:
            raise KesalahanTeleskop(f"Gagal membaca posisi mount: {e}") from e

    def cek_kapabilitas(self):
        """Dipanggil sekali stlh sambung() -- GUI bisa pakai ini utk
        nonaktifkan tombol "Arahkan (Alt/Az)" kalau mount cuma equatorial
        (CanSlewAltAz False), drpd user coba lalu baru dapat error."""
        self._pastikan_tersambung()
        try:
            return {
                "bisa_slew_radec": bool(self._t.CanSlew),
                "bisa_slew_altaz": bool(self._t.CanSlewAltAz),
                "bisa_atur_tracking": bool(self._t.CanSetTracking),
            }
        except Exception as e:
            raise KesalahanTeleskop(f"Gagal membaca kapabilitas mount: {e}") from e

    def _pastikan_tersambung(self):
        if not self._tersambung:
            raise KesalahanTeleskop("Belum sambung() ke mount")
