# ============================================================
#  hisabwin_api.py -- API scripting utk "pro user"
#
#  Dua cara pakai:
#   1) DARI DALAM app (akordeon 🐍 Skrip Python) -- skrip yg dijalankan di
#      situ otomatis dapat variabel `hisabwin` (modul ini) & `app`
#      (jembatan ke state GUI yg SEDANG JALAN: profil cakrawala aktif,
#      koneksi teleskop/kamera, kernel yg sudah dimuat -- lihat kelas
#      Aplikasi di bawah).
#   2) STANDALONE dari command line: `import hisabwin_api` di skrip .py
#      biasa (di folder yg sama dgn hisabwin.py) -- tanpa GUI sama sekali,
#      cocok utk automasi/batch (cron job, dipanggil dari skrip lain, dst).
#
#  KENAPA MODUL TERPISAH (bukan skrip pro-user langsung `import hisabwin`):
#  fungsi internal hisabwin.py (>14000 baris) bisa berubah nama/signature
#  kapan saja seiring pengembangan -- modul ini jadi LAPISAN STABIL yg
#  pro user bisa andalkan, jadi refactor internal tidak otomatis merusak
#  skrip yg sudah ditulis orang lain (selama fungsi2 DI SINI tetap sama).
#
#  KENAPA TIDAK `import hisabwin` LANGSUNG DI ATAS FILE INI (pola yg
#  dipakai server.py): kalau modul ini di-import DARI DALAM proses
#  hisabwin.py yg SEDANG JALAN (skenario #1 di atas), hisabwin.py saat itu
#  terdaftar sbg `__main__` di sys.modules, BUKAN `hisabwin` -- `import
#  hisabwin` polos di sini akan diam2 MENGEKSEKUSI ULANG seluruh file
#  hisabwin.py (14000+ baris, termasuk side-effect spt starmap.
#  inisialisasi()) sbg modul KEDUA yg terpisah dari GUI yg sungguhan
#  berjalan -- boros & skrip jadi tidak nyambung ke state GUI yg live.
#  _modul_inti() di bawah ini yg urus supaya connect ke modul yg BENAR di
#  kedua skenario (lihat komentarnya).
# ============================================================
import sys

_hw = None  # cache -- lihat _modul_inti()


def _modul_inti():
    """Return modul hisabwin.py yg BENAR tergantung skenario pemakaian
    (lihat catatan panjang di atas):
      - Dipanggil dari skrip DI DALAM app yg jalan -> sys.modules['__main__']
        SUDAH ADALAH hisabwin.py yg jalan itu -- pakai apa adanya, JANGAN
        import ulang.
      - Dipanggil standalone (skrip lain yg `import hisabwin_api`) ->
        sys.modules['__main__'] adalah skrip PEMANGGIL (bukan hisabwin),
        jadi baru di sini `import hisabwin` sungguhan (modul terpisah,
        lengkap, tanpa GUI aktif -- aman krn hisabwin.py membungkus
        pembuatan window-nya di `if __name__ == "__main__":`)."""
    global _hw
    if _hw is not None:
        return _hw
    m = sys.modules.get("__main__")
    if m is not None and hasattr(m, "hitung_waktu_sholat_otomatis"):
        _hw = m
    else:
        import hisabwin as _modul
        _hw = _modul
    return _hw


# -- Waktu sholat & kiblat --------------------------------------------
def hitung_sholat(tanggal, lat, lon, zona, elevasi_m=0.0, mode="ringan", ts=None, eph=None,
                   sudut_fajar=-20.0, sudut_isya=-18.0, mazhab_ashar="syafii"):
    """Waktu sholat SATU HARI. `tanggal`: datetime.datetime, ATAU tuple
    (tahun, bulan, hari). mode='jpl' butuh ts/eph (lihat muat_kernel()) --
    kalau tidak diisi, otomatis balik ke 'ringan' (sama spt GUI).
    sudut_fajar/sudut_isya derajat NEGATIF di bawah ufuk (Kemenag RI
    default -20/-18; lihat PRESET_SUDUT() utk preset lain).
    mazhab_ashar: 'syafii' atau 'hanafi'.

    Return dict (jam desimal 0-24): imsak, subuh, terbit, dhuha, dzuhur,
    ashar, maghrib, isya, kiblat_v (arah kiblat drjt dari Utara searah
    jarum jam), kiblat_s (jarak great-circle ke Kakbah, km)."""
    from datetime import datetime
    hw = _modul_inti()
    if not isinstance(tanggal, datetime):
        tanggal = datetime(*tanggal)
    return hw.hitung_waktu_sholat_otomatis(
        tanggal, lat, lon, zona, mode=mode, ts=ts, eph=eph, elevasi_m=elevasi_m,
        sudut_fajar=sudut_fajar, sudut_isya=sudut_isya, mazhab_ashar=mazhab_ashar)


def PRESET_SUDUT():
    """Dict preset {label: (sudut_fajar, sudut_isya)} yg sama dgn dropdown
    di GUI (Kemenag RI, Muhammadiyah, MWL, Egyptian)."""
    return dict(_modul_inti().PRESET_SUDUT)


# -- Kalender Hijriyah --------------------------------------------------
def hitung_hijriyah(tahun, bulan, hari, kriteria="urfi", ts=None, eph=None, mode="ringan"):
    """Tanggal Hijriyah dari tanggal Masehi (tahun/bulan/hari = angka biasa).
    kriteria:
      'urfi'   -- tabular/perkiraan, CEPAT, tanpa hisab (default).
      'mabims' -- hisab ijtimak + kriteria hilal MABIMS (tinggi hilal
                  toposentris >=3 DAN elongasi >=6.4 drjt) ASLI, spt
                  akordeon 🌙 Visibilitas.
      'khgt'   -- hisab + kriteria KHGT Muhammadiyah (PKG1/PKG2). PALING
                  LAMBAT dari 3 opsi -- kalau bikin skrip loop banyak
                  tanggal, pertimbangkan cache/kurangi panggilan.
    Return dict {tahun_h, bulan_h, nama_bulan_h, hari_h}."""
    hw = _modul_inti()
    if kriteria == "urfi":
        th, bh, hh = hw.masehi_ke_hijriyah_urfi(tahun, bulan, hari)
        return {"tahun_h": th, "bulan_h": bh,
                "nama_bulan_h": hw._NAMA_BULAN_HIJRIYAH[bh - 1], "hari_h": hh}
    hasil = hw.masehi_ke_hijriyah_kriteria(tahun, bulan, hari, kriteria, ts, eph, mode=mode)
    return {"tahun_h": hasil["tahun_h"], "bulan_h": hasil["bulan_h"],
            "nama_bulan_h": hasil["nama_bulan_h"], "hari_h": hasil["hari_h"]}


# -- Posisi benda langit --------------------------------------------------
def posisi_realtime(lat, lon, objek="bulan", elevasi_m=0.0, mode="ringan", ts=None, eph=None):
    """Posisi (Az/Alt) sebuah objek PERSIS SEKARANG (waktu sistem saat
    dipanggil). objek: salah satu id dari katalog_objek() -- default
    'bulan' (aplikasi ini fokus hisab-rukyat hilal). mode='ringan' cuma
    dukung 'bulan'/'matahari' (VSOP87/ELP2000 tdk cakup planet) -- planet
    butuh mode='jpl' + ts/eph (lihat muat_kernel()), melempar ValueError
    kalau tidak, bukan hasil salah diam2.
    Return dict {"az": derajat, "alt": derajat, "label": nama tampilan}."""
    hw = _modul_inti()
    return hw.hitung_posisi_realtime_objek(lat, lon, elevasi_m, objek, mode, ts=ts, eph=eph)


def katalog_objek(eph=None):
    """Dict {id: label} semua objek yg didukung posisi_realtime() --
    Bulan, Matahari, + 8 planet (Merkurius..Pluto, planet butuh eph)."""
    return _modul_inti().katalog_objek_teleskop(eph)


# -- Profil Cakrawala (horizon medan nyata) ------------------------------
def profil_cakrawala(lat, lon, tinggi_mata=2.0, radius_km=30, n_azimuth=180, n_sample=40,
                      progress_cb=None):
    """Profil elevasi horizon 360 drjt dari SATU titik pengamat (medan
    nyata, lihat akordeon 🏔️ Profil Cakrawala) -- BUTUH INTERNET (AWS
    Terrain Tiles), bisa makan waktu (tergantung n_azimuth x n_sample).
    Return dict siap dioper ke fungsi lain di modul ini yg butuh
    parameter `profil` (field-nya sama persis dgn yg dipakai GUI)."""
    hw = _modul_inti()
    return hw.hitung_profil_cakrawala(
        lat, lon, tinggi_mata=tinggi_mata, radius_km=radius_km, n_azimuth=n_azimuth,
        n_sample=n_sample, progress_cb=progress_cb or (lambda msg: None))


# -- Cetak PDF ------------------------------------------------------------
def cetak_almanak(tahun, bulan, lat, lon, zona, path_output, format="jadwal", elevasi_m=0.0,
                   mode="ringan", ts=None, eph=None, kriteria_hijriyah="urfi",
                   sudut_fajar=-20.0, sudut_isya=-18.0, mazhab_ashar="syafii",
                   nama_lokasi=None, progress_cb=None):
    """Cetak PDF almanak SATU BULAN penuh (sama persis dgn akordeon 🗓️
    Cetak Almanak). format: 'jadwal' (tabel 6 waktu sholat/hari) atau
    'kalender' (grid kalender Minggu-Sabtu, cuma Maghrib+fase bulan per
    sel). Return path_output kalau berhasil.

    Cocok dipanggil BERULANG dari skrip (mis. loop 12 bulan sekaligus, atau
    beberapa lokasi) -- tiap panggilan independen, tidak ada state
    tersisa antar panggilan."""
    hw = _modul_inti()
    fungsi = hw.buat_pdf_almanak_bulanan if format == "jadwal" else hw.buat_pdf_kalender_bulanan
    return fungsi(
        tahun, bulan, lat, lon, zona, path_output, elevasi_m=elevasi_m, mode=mode, ts=ts, eph=eph,
        sudut_fajar=sudut_fajar, sudut_isya=sudut_isya, mazhab_ashar=mazhab_ashar,
        nama_lokasi=nama_lokasi, kriteria_hijriyah=kriteria_hijriyah,
        progress_cb=progress_cb or (lambda msg: None))


# -- Kernel efemeris JPL ---------------------------------------------------
def muat_kernel(path_bsp="de421.bsp"):
    """Muat kernel efemeris JPL (buat mode='jpl': presisi lebih tinggi &
    posisi planet). Return (ts, eph) -- oper ke parameter ts=/eph= fungsi
    lain di modul ini. Skrip yg CUMA butuh Bulan/Matahari mode ringan
    (presisi beberapa detik busur, cukup utk kebanyakan keperluan) TIDAK
    perlu panggil ini sama sekali -- lebih cepat & tanpa file kernel.

    Dipanggil dari skrip DI DALAM app yg jalan? Pertimbangkan pakai
    app.ts/app.eph (lihat kelas Aplikasi) drpd muat_kernel() lagi di sini
    -- app.ts/app.eph sudah dimuat sekali oleh GUI saat startup, muat
    ulang di sini cuma boros memori (file kernel bisa ratusan MB)."""
    from skyfield.api import load
    ts = load.timescale()
    eph = load(path_bsp)
    return ts, eph


class Aplikasi:
    """Jembatan skrip -> state GUI yg SEDANG JALAN. HANYA berfungsi kalau
    skrip dijalankan dari akordeon 🐍 Skrip Python DI DALAM app (variabel
    `app` sudah otomatis tersedia di situ) -- skrip standalone command-line
    tidak punya `app` (tidak ada GUI yg jalan)."""

    def __init__(self, instance_app):
        self._app = instance_app

    @property
    def profil_cakrawala(self):
        """dict profil cakrawala AKTIF (hasil hitung/muat terakhir di
        akordeon 🏔️), None kalau belum ada -- pakai ini drpd panggil
        profil_cakrawala() lagi & tunggu unduh ulang dari internet."""
        return self._app._hasil_cakrawala_terakhir

    @property
    def teleskop(self):
        """Instance KontrolTeleskop kalau SEDANG tersambung di GUI
        (akordeon 🔭 Kontrol Teleskop), None kalau belum. Pakai koneksi
        yg SUDAH ADA ini -- JANGAN bikin KontrolTeleskop(...) baru dari
        skrip, satu mount cuma bisa 1 koneksi aktif & bisa rebutan
        perintah dgn GUI."""
        return self._app.kt_teleskop

    @property
    def kamera(self):
        """Instance KontrolKamera kalau SEDANG tersambung, None kalau
        belum -- sama alasannya dgn teleskop di atas."""
        return self._app.kk_kamera

    @property
    def ts(self):
        """Skyfield timescale yg SUDAH dimuat GUI, None kalau kernel blm
        selesai/belum dimuat (cek status di panel Status kiri)."""
        return self._app.ts

    @property
    def eph(self):
        """Kernel efemeris JPL yg SUDAH dimuat GUI, None kalau belum."""
        return self._app.eph

    def log(self, pesan):
        """Tulis satu baris ke panel Status GUI (sama spt log internal
        app) -- selain print() biasa yg tampil di konsol skrip sendiri."""
        self._app._log(str(pesan))
