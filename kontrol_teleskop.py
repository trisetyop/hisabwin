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
from alpaca.camera import Camera
from alpaca.exceptions import (
    NotConnectedException, InvalidValueException, InvalidOperationException,
    DriverException, AlpacaRequestException,
)


class KesalahanKamera(Exception):
    """Sama spt KesalahanTeleskop -- satu jenis exception drpd GUI harus
    tau detail internal alpyca/ASCOM Camera."""


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


class KontrolKamera:
    """Satu kamera (device Alpaca TERPISAH dari mount -- bisa di host:port
    yg sama, bisa juga beda, krn di dunia nyata kamera & mount SERINGKALI
    driver/software berbeda). Didesain utk LIVE VIEW MANUAL & KONTINU:
    hilal itu tidak bisa "dijadwalkan" persis kapan tampak, jadi TIDAK ADA
    capture otomatis di sini (mis. tersambung ke slew selesai) -- yang ada
    cuma (a) exposure BERULANG terus-menerus utk preview hidup, dan (b)
    fungsi simpan_frame_sekarang() yg pemanggilannya 100% keputusan
    manusia, kapan saja, independen dari siklus preview.

    Pemakaian dasar:
        kk = KontrolKamera("127.0.0.1:11111")
        kk.sambung()
        kk.mulai_exposure(0.5)          # ulangi terus dari GUI (loop after())
        while not kk.siap(): time.sleep(0.05)
        larik = kk.ambil_larik()        # numpy 2D/3D, siap ditampilkan
        # ... tampilkan larik ke GUI ...
        # kapanpun user klik "Simpan": kk.simpan_ke_png(larik, path)
    """

    def __init__(self, alamat, nomor_perangkat=0):
        self.alamat = alamat
        self._k = Camera(alamat, nomor_perangkat)
        self._tersambung = False

    def sambung(self):
        try:
            self._k.Connected = True
        except (AlpacaRequestException, DriverException) as e:
            raise KesalahanKamera(f"Gagal sambung ke kamera di {self.alamat}: {e}") from e
        self._tersambung = True

    def putus(self):
        try:
            self._k.Connected = False
        except Exception:
            pass
        self._tersambung = False

    def tersambung(self):
        return self._tersambung

    def info_kamera(self):
        try:
            return {"nama": self._k.Name, "deskripsi": self._k.Description,
                    "lebar": self._k.CameraXSize, "tinggi": self._k.CameraYSize}
        except Exception as e:
            raise KesalahanKamera(f"Gagal membaca info kamera: {e}") from e

    def mulai_exposure(self, durasi_detik, cahaya=True):
        """Mulai SATU exposure (async -- kembali segera, cek siap() utk tau
        kapan selesai). GUI yg pakai ini utk live-view manggil fungsi ini
        BERULANG (lihat siap()->ambil_larik()->mulai_exposure() lagi) dari
        loop self.after() -- BUKAN dipanggil sekali lalu berhenti."""
        self._pastikan_tersambung()
        try:
            self._k.StartExposure(durasi_detik, cahaya)
        except InvalidValueException as e:
            raise KesalahanKamera(f"Durasi exposure di luar jangkauan: {e}") from e
        except (AlpacaRequestException, DriverException) as e:
            raise KesalahanKamera(f"Gagal mulai exposure: {e}") from e

    def siap(self):
        try:
            return bool(self._k.ImageReady)
        except Exception as e:
            raise KesalahanKamera(f"Gagal cek status exposure: {e}") from e

    def ambil_larik(self):
        """Ambil hasil exposure TERAKHIR sbg array numpy 2D (mono) atau 3D
        (warna, HWC) -- siap dioper ke PIL.Image.fromarray() utk
        ditampilkan/disimpan. alpyca otomatis tangani JSON ATAU ImageBytes
        biner tergantung apa yg dikirim server (lihat catatan di
        alpaca_mock_server.py -- mock ini pakai JSON polos)."""
        self._pastikan_tersambung()
        try:
            import numpy as np
            data = self._k.ImageArray
            return np.array(data, dtype=np.uint16)
        except Exception as e:
            raise KesalahanKamera(f"Gagal ambil data gambar: {e}") from e

    def hentikan_exposure(self):
        try:
            self._k.AbortExposure()
        except Exception as e:
            raise KesalahanKamera(f"Gagal menghentikan exposure: {e}") from e

    @staticmethod
    def simpan_ke_png(larik, path):
        """Simpan array numpy (dari ambil_larik()) ke file PNG -- dipanggil
        MURNI atas klik manual user (tombol "📸 Simpan Frame Ini"), TIDAK
        PERNAH dipanggil otomatis dari loop preview. Auto-scale 16-bit/nilai
        arbiter mock ke 8-bit spy tampil layak di PNG biasa."""
        from PIL import Image
        arr = larik.astype("float64")
        lo, hi = arr.min(), arr.max()
        if hi > lo:
            arr8 = ((arr - lo) / (hi - lo) * 255.0).astype("uint8")
        else:
            arr8 = arr.astype("uint8")
        Image.fromarray(arr8).save(path)

    def _pastikan_tersambung(self):
        if not self._tersambung:
            raise KesalahanKamera("Belum sambung() ke kamera")
