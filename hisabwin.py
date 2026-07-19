# ==== Install dependencies (jalankan sekali di terminal) ====
# pip install skyfield cartopy numpy matplotlib
# (tkinter biasanya sudah bawaan Python di Windows/Mac; di Linux: sudo apt install python3-tk)

"""
HisabWin — Peta Visibilitas Hilal (MABIMS & Muhammadiyah), versi GUI sederhana.

Alur aplikasi:
  1. User memasukkan tahun Masehi, klik "Cari Ijtimak".
  2. Daftar semua waktu ijtimak (konjungsi) tahun tsb muncul di listbox,
     user memilih salah satu lalu klik "Lanjut".
  3. User memilih "Hari Ijtimak" atau "Sehari Setelah Ijtimak" (radio button).
  4. Klik "Tampilkan Peta" -> program menghitung grid lalu membuka 2 jendela
     peta (kriteria MABIMS & kriteria Muhammadiyah), masing-masing dengan
     toolbar zoom/pan/simpan bawaan matplotlib.

Ini adalah pembungkus GUI dari skrip terminal aslinya. Logika astronomi
(ijtimak, sunset presisi, elongasi, tinggi hilal) TIDAK diubah — hanya
cara interaksi (input/tampilan) yang diganti dari terminal ke jendela GUI,
dan bagian plotting dipindah ke thread utama agar aman dipakai bersama Tkinter.
"""

import calendar
import math
import csv
import os
import queue
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import matplotlib


def _resource_base_dir():
    """Folder tempat aset bundel (de421.bsp, logo.png, mask NPZ, dll) berada.

    JANGAN pakai path relatif ('de421.bsp') atau cwd secara implisit --
    itu cuma kebetulan benar saat dijalankan lewat 'python hisabwin.py'
    di folder yang sama. Setelah dibundel PyInstaller (--onedir), CWD saat
    exe di-double-click adalah folder ROOT instalasi, sedangkan file yang
    ditambahkan lewat --add-data ada di 'root/_internal/' (default PyInstaller
    6.x). sys._MEIPASS selalu menunjuk ke folder itu dengan benar, baik utk
    --onedir (folder _internal) maupun --onefile (folder ekstraksi sementara).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


# =========================================================
#  MANAJEMEN KERNEL JPL (de421 bawaan + de440/de441 opsional)
# =========================================================
#
# de421.bsp SELALU dibundel bersama aplikasi (lihat _resource_base_dir) --
# ini tetap jadi default & satu-satunya yang dijamin ada tanpa internet.
# de440 (lebih presisi, rentang 1550-2650) dan de441 (rentang sangat
# panjang, dipecah NASA jadi 2 bagian ~1.5GB masing-masing) TIDAK dibundel
# karena ukurannya besar -- disediakan lewat dialog "Kelola Kernel JPL"
# supaya user bisa unduh sendiri kalau mau, kapan saja diinginkan.
#
# Untuk de441, aplikasi ini HANYA menawarkan de441_part-2.bsp (1969 M -
# 17191 M) -- cukup untuk hisab hilal masa kini & masa depan -- bukan
# part-1 (13.200 SM-1969 M) yang jarang relevan utk kalender kamariah dan
# akan menambah unduhan ~1.5GB lagi kalau ikut diunduh.
KERNEL_CATALOG = {
    "de421": {
        "label": "DE421 (bawaan aplikasi)",
        "cakupan": "1900 - 2050",
        "files": [{"nama": "de421.bsp", "url": None, "size_mb": 17}],
        "bundled": True,
    },
    "de440": {
        "label": "DE440 (lebih presisi)",
        "cakupan": "1550 - 2650",
        "files": [{
            "nama": "de440.bsp",
            "url": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440.bsp",
            "size_mb": 114,
        }],
        "bundled": False,
    },
    "de441": {
        "label": "DE441 (rentang sangat panjang, hanya bagian modern)",
        "cakupan": "1969 M - 17191 M (bagian kuno 13.200 SM-1969 M sengaja tidak ditawarkan, jarang relevan)",
        "files": [{
            "nama": "de441_part-2.bsp",
            "url": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de441_part-2.bsp",
            "size_mb": 1500,
        }],
        "bundled": False,
    },
}

KERNEL_DEFAULT_ID = "de421"


def _folder_cache_kernel_jpl():
    """Folder tempat kernel JPL yang DIUNDUH (de440/de441) disimpan --
    folder CACHE per-user (mis. %LOCALAPPDATA%\\HisabWin\\kernels di
    Windows), TERPISAH dari folder instalasi/aset bawaan (de421.bsp tetap
    dibaca dari _resource_base_dir()). Sengaja dipisah supaya:
      1. Uninstall/reinstall aplikasi tidak ikut menghapus kernel besar
         yang sudah susah payah diunduh (~114 MB - 1.5 GB).
      2. Folder instalasi (kadang read-only / butuh admin di Program Files)
         tidak perlu ditulisi saat runtime.
    Dibuat otomatis kalau belum ada.
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    folder = os.path.join(base, "HisabWin", "kernels")
    os.makedirs(folder, exist_ok=True)
    return folder


def _path_preferensi_kernel():
    """File teks kecil (di folder cache yang sama) yang menyimpan id kernel
    JPL yang terakhir dipilih user sebagai 'aktif' (mis. 'de440'). Kalau
    file tidak ada / isinya tidak dikenal, dianggap KERNEL_DEFAULT_ID."""
    return os.path.join(_folder_cache_kernel_jpl(), "kernel_aktif.txt")


def muat_kernel_aktif():
    """Baca id kernel aktif tersimpan; fallback ke default kalau belum
    pernah diset atau file rusak/tidak dikenal."""
    try:
        with open(_path_preferensi_kernel(), "r", encoding="utf-8") as f:
            kid = f.read().strip()
        if kid in KERNEL_CATALOG:
            return kid
    except OSError:
        pass
    return KERNEL_DEFAULT_ID


def simpan_kernel_aktif(kernel_id):
    """Simpan id kernel aktif terpilih ke file preferensi."""
    try:
        with open(_path_preferensi_kernel(), "w", encoding="utf-8") as f:
            f.write(kernel_id)
    except OSError:
        pass


def _path_file_kernel(kernel_id, info_file):
    """Path lokal (baca ATAU tulis) untuk satu file kernel tertentu.
    de421 (bundled) -> folder aset bawaan (read-only).
    de440/de441 (unduhan) -> folder cache per-user."""
    if KERNEL_CATALOG[kernel_id]["bundled"]:
        return os.path.join(_resource_base_dir(), info_file["nama"])
    return os.path.join(_folder_cache_kernel_jpl(), info_file["nama"])


def status_kernel(kernel_id):
    """True kalau SEMUA file kernel ini sudah tersedia secara lokal
    (bundled, atau sudah pernah selesai diunduh)."""
    info = KERNEL_CATALOG[kernel_id]
    return all(os.path.isfile(_path_file_kernel(kernel_id, f)) for f in info["files"])


def path_utama_kernel(kernel_id):
    """Path file .bsp utama yang dipakai skyfield.load() untuk kernel ini.
    (Semua kernel di katalog ini hanya terdiri dari satu file .bsp, jadi
    cukup ambil file pertama.)"""
    info = KERNEL_CATALOG[kernel_id]
    return _path_file_kernel(kernel_id, info["files"][0])


def hapus_kernel(kernel_id):
    """Hapus file kernel yang sudah diunduh (TIDAK berlaku utk de421
    bawaan -- dijaga supaya tidak sengaja terhapus)."""
    info = KERNEL_CATALOG[kernel_id]
    if info["bundled"]:
        raise ValueError("Kernel bawaan aplikasi tidak boleh dihapus dari sini.")
    for f in info["files"]:
        path = _path_file_kernel(kernel_id, f)
        if os.path.isfile(path):
            os.remove(path)


def unduh_kernel(kernel_id, progress_cb=lambda persen, teks: None, event_batal=None):
    """Unduh semua file kernel ini satu per satu, lapor progres lewat
    progress_cb(persen_0_100, teks_status). Ditulis dulu ke '<nama>.part'
    lalu di-rename ke nama asli setelah SELESAI utuh -- supaya file yang
    setengah jadi (koneksi putus di tengah jalan) tidak pernah dianggap
    'sudah lengkap' oleh status_kernel().

    event_batal: threading.Event opsional -- kalau di-set() oleh thread
    lain (tombol "Batal" di dialog), unduhan dihentikan secepatnya dan
    file .part yang belum selesai dihapus.

    Melempar Exception dengan pesan yang bisa ditampilkan ke user kalau
    gagal (jaringan/HTTP/dibatalkan)."""
    info = KERNEL_CATALOG[kernel_id]
    if info["bundled"]:
        raise ValueError("Kernel bawaan tidak perlu (dan tidak bisa) diunduh.")

    n_file = len(info["files"])
    for idx, f in enumerate(info["files"]):
        path_final = _path_file_kernel(kernel_id, f)
        if os.path.isfile(path_final):
            continue  # file ini sudah ada (mis. lanjutan unduhan multi-file yg sempat berhenti)
        path_part = path_final + ".part"
        try:
            with urllib.request.urlopen(f["url"], timeout=30) as resp:
                total = resp.length or (f["size_mb"] * 1024 * 1024)
                sudah = 0
                with open(path_part, "wb") as out:
                    while True:
                        if event_batal is not None and event_batal.is_set():
                            raise RuntimeError("Unduhan dibatalkan oleh user.")
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                        sudah += len(chunk)
                        persen_file = min(99.0, sudah / total * 100.0) if total else 0.0
                        # progres keseluruhan mempertimbangkan file ke berapa dari n_file
                        persen_total = (idx * 100.0 + persen_file) / n_file
                        progress_cb(persen_total,
                                    f"Mengunduh {f['nama']} — {sudah / (1024 * 1024):.0f} MB"
                                    + (f" / ~{total / (1024 * 1024):.0f} MB" if total else ""))
            os.replace(path_part, path_final)
        except Exception:
            if os.path.isfile(path_part):
                try:
                    os.remove(path_part)
                except OSError:
                    pass
            raise
    progress_cb(100.0, "Selesai.")


matplotlib.use("TkAgg")

import cartopy
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.shapereader as shpreader
import matplotlib.pyplot as plt
import numpy as np
import shapely  # dipakai untuk shapely.contains_xy (vectorized, no Python loop)

# HIPOTESA PERFORMA CARTOPY (belum diverifikasi lewat profiling -- lihat
# catatan di masing-masing buat_figure_* untuk hipotesa lain yang SUDAH
# diterapkan). Ini soal folder CACHE, beda dari bug de421.bsp/logo.png/mask
# yang sudah diperbaiki (itu soal path relatif ke folder instalasi/CWD).
#
# cartopy secara default menyimpan shapefile Natural Earth (LAND/OCEAN/
# BORDERS/coastlines) di folder cache PER-USER (mis. %LOCALAPPDATA%\cartopy
# di Windows), diunduh dari internet saat PERTAMA KALI dipakai. Untuk
# aplikasi yang didesain 100% offline (alasan yang sama kenapa mode "ringan"
# VSOP87/ELP2000 dibuat -- supaya tidak butuh unduhan apapun), sebaiknya
# shapefile Natural Earth 110m juga dibundel (mis. lewat --add-data) &
# cartopy diarahkan ke situ LEBIH DULU -- supaya generate peta pertama kali
# di komputer user yang offline tidak diam-diam mencoba akses internet dulu
# (bisa lambat menunggu timeout) sebelum akhirnya gagal.
#
# Baris ini AMAN ditambahkan SEKARANG walau shapefile-nya belum dibundel --
# kalau foldernya belum ada, cartopy otomatis balik ke perilaku default
# (cache per-user + unduh kalau perlu), jadi tidak ada risiko regresi sama
# sekali. Begitu folder 'cartopy_data' (hasil menyalin isi
# cartopy.config['data_dir'] dari mesin developer setelah generate peta
# sekali, lalu di-bundle lewat --add-data) ditambahkan, baris ini otomatis
# memakainya duluan.
_folder_data_cartopy = os.path.join(_resource_base_dir(), "cartopy_data")
if os.path.isdir(_folder_data_cartopy):
    cartopy.config["pre_existing_data_dir"] = _folder_data_cartopy
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from skyfield import almanac
from skyfield.api import load, wgs84
import skyfield.timelib
from skyfield.nutationlib import iau2000b_radians

# Skyfield secara default selalu memakai model nutasi IAU2000A penuh (687 suku
# luni-solar + 687 suku planeter = ~1374 suku trigonometri per titik waktu)
# lewat Time._nutation_angles_radians -- ini presisi sub-milidetik-busur,
# jauh melebihi kebutuhan hisab hilal (cukup akurasi menit busur utk kriteria
# MABIMS/KHGT). Ganti ke IAU2000B (77 suku luni-solar, 0 planeter): selisih
# maksimum thd IAU2000A cuma ~0.4 milidetik busur (diverifikasi utk tanggal
# uji 2040), tapi ~40-47x lebih cepat -- pipeline PKG1/PKG2 Muhammadiyah mode
# JPL turun dari ~2.75 detik jadi ~0.49 detik. Di-patch di sini (level modul,
# sebelum ts/eph dipakai di manapun) supaya berlaku otomatis utk SEMUA
# pemanggilan .apparent() di seluruh kode tanpa perlu ubah logika hisab.
skyfield.timelib.iau2000a_radians = iau2000b_radians

BULAN_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

HARI_ID = ["Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"]

# =========================================================
#  TEMA TAMPILAN (modern & sederhana -- flat, palet terbatas)
#  Cuma warna & font, TIDAK ada perubahan logika apapun di bawah ini.
# =========================================================
WARNA_BG = "#F4F6F8"          # latar utama jendela (abu-abu sangat terang)
WARNA_PANEL = "#FFFFFF"       # latar kartu/kontrol/entry
WARNA_AKSEN = "#0F6E5B"       # hijau tosca gelap -- warna aksen utama (tombol aksi, tab aktif)
WARNA_AKSEN_HOVER = "#0B5747"
WARNA_TEKS = "#1F2937"        # abu-abu hampir hitam
WARNA_TEKS_MUTED = "#6B7280"  # abu-abu redup (subjudul, catatan)
WARNA_BORDER = "#E1E5EA"
FONT_UTAMA = ("Segoe UI", 10)
FONT_UTAMA_BOLD = ("Segoe UI", 10, "bold")
FONT_JUDUL = ("Segoe UI", 18, "bold")
FONT_KECIL = ("Segoe UI", 8)
FONT_TAB_AKTIF = ("Segoe UI", 11, "bold")  # tab notebook yang sedang aktif -- sengaja
                                            # dibuat lebih BESAR (bukan cuma bold), karena
                                            # bold-saja lewat style.map tidak kelihatan
                                            # bedanya di beberapa sistem/render font


# =========================================================
#  UTILITAS
# =========================================================

def format_waktu_ijtimak(dt):
    """Format datetime UTC jadi string 'dd Bulan yyyy — HH:MM UTC'."""
    return f"{dt.day:02d} {BULAN_ID[dt.month - 1]} {dt.year} — {dt.hour:02d}:{dt.minute:02d} UTC"


def ke_utc_datetime(t):
    """Konversi seragam ke datetime UTC biasa, entah 't' objek Time skyfield
    (punya .utc_datetime()) atau sudah berupa datetime polos (mode Ringan)."""
    return t.utc_datetime() if hasattr(t, "utc_datetime") else t


def _ke_naif(dt):
    """Lepas tzinfo (kalau ada) dari sebuah datetime, supaya aman
    dibandingkan (<, >, ==) dengan datetime lain yang naive.

    Dibutuhkan krn dua sumber waktu di kode ini TIDAK selalu konsisten
    soal tzinfo: waktu ijtimak dari cari_ijtimak_tahun_ringan() (dipakai
    apa adanya oleh bandingkan_kalender_mabims_khgt(), independen dari
    mode) selalu NAIVE, sedangkan hitung_fajar_nz() mengembalikan datetime
    AWARE (via Skyfield .utc_datetime()) saat mode='jpl' tapi NAIVE saat
    mode='ringan'. Membandingkan langsung (mis. waktu_ijtimak <
    waktu_fajar_nz) meledak dgn TypeError 'can't compare offset-naive and
    offset-aware datetimes' begitu salah satu aware dan satunya naive.
    Kedua datetime yg dibandingkan di sini sama2 berbasis UTC, jadi
    melepas tzinfo (bukan mengonversinya) aman dan tidak mengubah nilai
    jam/menitnya."""
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo is not None else dt


def cari_ijtimak_tahun(tahun, ts, eph, mode="jpl"):
    """Mencari semua waktu ijtimak (new moon / fase=0) sepanjang tahun tsb.
    mode='jpl'   -> skyfield + JPL DE421 (presisi tinggi, perlu file .bsp)
    mode='ringan'-> VSOP87+ELP2000 (tanpa file eksternal), lihat cari_ijtimak_tahun_ringan
    Return list/array yang tiap elemennya bisa dipanggil ke_utc_datetime(...).
    """
    if mode == "ringan":
        return cari_ijtimak_tahun_ringan(tahun)
    t0 = ts.utc(tahun, 1, 1)
    t1 = ts.utc(tahun + 1, 1, 1)
    t, y = almanac.find_discrete(t0, t1, almanac.moon_phases(eph))
    ijtimak_times = t[y == 0]
    return ijtimak_times


def cari_istiqbal_tahun(tahun, ts, eph, mode="jpl"):
    """Analog cari_ijtimak_tahun() di atas, tapi utk waktu ISTIQBAL
    (oposisi geosentris/purnama -- fase=2 di almanac.moon_phases: 0=bulan
    baru, 1=perbani awal, 2=purnama, 3=perbani akhir) sepanjang tahun tsb.
    Dipakai sbg basis waktu kandidat gerhana BULAN mode Presisi (analog
    cari_ijtimak_tahun dipakai gerhana Matahari).
    mode='jpl'   -> skyfield + JPL DE421 (presisi tinggi, perlu file .bsp)
    mode='ringan'-> VSOP87+ELP2000 (tanpa file eksternal), lihat cari_istiqbal_tahun_ringan
    Return list/array yang tiap elemennya bisa dipanggil ke_utc_datetime(...).
    """
    if mode == "ringan":
        return cari_istiqbal_tahun_ringan(tahun)
    t0 = ts.utc(tahun, 1, 1)
    t1 = ts.utc(tahun + 1, 1, 1)
    t, y = almanac.find_discrete(t0, t1, almanac.moon_phases(eph))
    istiqbal_times = t[y == 2]
    return istiqbal_times


def equation_of_time_menit(tanggal):
    """
    Perkiraan Equation of Time (menit) memakai formula Spencer (1971).
    Dipakai untuk mengoreksi tebakan awal waktu sunset, karena matahari
    TIDAK selalu transit tepat jam 12:00 UT di bujur 0.
    """
    day_of_year = tanggal.timetuple().tm_yday
    total_hari = 366 if (tanggal.year % 4 == 0 and (tanggal.year % 100 != 0 or tanggal.year % 400 == 0)) else 365
    gamma = 2 * np.pi / total_hari * (day_of_year - 1)
    eot_menit = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )
    return eot_menit


# =========================================================
#  MODE "RINGAN" -- VSOP87 (ringkas, Meeus Ch.25) + ELP2000-82B (tabel
#  penuh Meeus Table 47.A/47.B), 100% vectorized numpy, TANPA file
#  eksternal apapun (tidak perlu download de421.bsp / apapun dari internet).
#
#  Akurasi: Matahari & Bulan tervalidasi cocok dengan contoh baku buku
#  Jean Meeus "Astronomical Algorithms" (Ex. 25.a & 47.a) sampai beberapa
#  detik busur -- pada praktiknya SETARA dengan JPL DE421 untuk kebutuhan
#  kriteria hilal (ambang 3-8 derajat). Bedanya cuma di ukuran instalasi
#  (tidak perlu unduh apa pun) dan konsistensi nutasi/ΔT yang sedikit
#  disederhanakan (~0.5 detik busur, jauh di bawah level yang berpengaruh
#  ke keputusan kriteria).
# =========================================================

"""
Modul astronomi 'Ringan': VSOP87 (versi ringkas Meeus, Ch.25) untuk Matahari
dan ELP2000-82B (tabel penuh Meeus Table 47.A/47.B) untuk Bulan.
Semua vectorized (numpy), tidak butuh file eksternal apapun.
Koefisien ELP2000 diverifikasi identik dengan pymeeus (yang sudah divalidasi
cocok dengan contoh baku buku Meeus 47.a).
"""

# ---- Tabel ELP2000-82B (Meeus Table 47.A): longitude & distance ----
_LR = np.array([
[0,0,1,0,6288774.0,-20905355.0],[2,0,-1,0,1274027.0,-3699111.0],
[2,0,0,0,658314.0,-2955968.0],[0,0,2,0,213618.0,-569925.0],
[0,1,0,0,-185116.0,48888.0],[0,0,0,2,-114332.0,-3149.0],
[2,0,-2,0,58793.0,246158.0],[2,-1,-1,0,57066.0,-152138.0],
[2,0,1,0,53322.0,-170733.0],[2,-1,0,0,45758.0,-204586.0],
[0,1,-1,0,-40923.0,-129620.0],[1,0,0,0,-34720.0,108743.0],
[0,1,1,0,-30383.0,104755.0],[2,0,0,-2,15327.0,10321.0],
[0,0,1,2,-12528.0,0.0],[0,0,1,-2,10980.0,79661.0],
[4,0,-1,0,10675.0,-34782.0],[0,0,3,0,10034.0,-23210.0],
[4,0,-2,0,8548.0,-21636.0],[2,1,-1,0,-7888.0,24208.0],
[2,1,0,0,-6766.0,30824.0],[1,0,-1,0,-5163.0,-8379.0],
[1,1,0,0,4987.0,-16675.0],[2,-1,1,0,4036.0,-12831.0],
[2,0,2,0,3994.0,-10445.0],[4,0,0,0,3861.0,-11650.0],
[2,0,-3,0,3665.0,14403.0],[0,1,-2,0,-2689.0,-7003.0],
[2,0,-1,2,-2602.0,0.0],[2,-1,-2,0,2390.0,10056.0],
[1,0,1,0,-2348.0,6322.0],[2,-2,0,0,2236.0,-9884.0],
[0,1,2,0,-2120.0,5751.0],[0,2,0,0,-2069.0,0.0],
[2,-2,-1,0,2048.0,-4950.0],[2,0,1,-2,-1773.0,4130.0],
[2,0,0,2,-1595.0,0.0],[4,-1,-1,0,1215.0,-3958.0],
[0,0,2,2,-1110.0,0.0],[3,0,-1,0,-892.0,3258.0],
[2,1,1,0,-810.0,2616.0],[4,-1,-2,0,759.0,-1897.0],
[0,2,-1,0,-713.0,-2117.0],[2,2,-1,0,-700.0,2354.0],
[2,1,-2,0,691.0,0.0],[2,-1,0,-2,596.0,0.0],
[4,0,1,0,549.0,-1423.0],[0,0,4,0,537.0,-1117.0],
[4,-1,0,0,520.0,-1571.0],[1,0,-2,0,-487.0,-1739.0],
[2,1,0,-2,-399.0,0.0],[0,0,2,-2,-381.0,-4421.0],
[1,1,1,0,351.0,0.0],[3,0,-2,0,-340.0,0.0],
[4,0,-3,0,330.0,0.0],[2,-1,2,0,327.0,0.0],
[0,2,1,0,-323.0,1165.0],[1,1,-1,0,299.0,0.0],
[2,0,3,0,294.0,0.0],[2,0,-1,-2,0.0,8752.0],
])

# ---- Tabel ELP2000-82B (Meeus Table 47.B): latitude ----
_B = np.array([
[0,0,0,1,5128122.0],[0,0,1,1,280602.0],[0,0,1,-1,277693.0],
[2,0,0,-1,173237.0],[2,0,-1,1,55413.0],[2,0,-1,-1,46271.0],
[2,0,0,1,32573.0],[0,0,2,1,17198.0],[2,0,1,-1,9266.0],
[0,0,2,-1,8822.0],[2,-1,0,-1,8216.0],[2,0,-2,-1,4324.0],
[2,0,1,1,4200.0],[2,1,0,-1,-3359.0],[2,-1,-1,1,2463.0],
[2,-1,0,1,2211.0],[2,-1,-1,-1,2065.0],[0,1,-1,-1,-1870.0],
[4,0,-1,-1,1828.0],[0,1,0,1,-1794.0],[0,0,0,3,-1749.0],
[0,1,-1,1,-1565.0],[1,0,0,1,-1491.0],[0,1,1,1,-1475.0],
[0,1,1,-1,-1410.0],[0,1,0,-1,-1344.0],[1,0,0,-1,-1335.0],
[0,0,3,1,1107.0],[4,0,0,-1,1021.0],[4,0,-1,1,833.0],
[0,0,1,-3,777.0],[4,0,-2,1,671.0],[2,0,0,-3,607.0],
[2,0,2,-1,596.0],[2,-1,1,-1,491.0],[2,0,-2,1,-451.0],
[0,0,3,-1,439.0],[2,0,2,1,422.0],[2,0,-3,-1,421.0],
[2,1,-1,1,-366.0],[2,1,0,1,-351.0],[4,0,0,1,331.0],
[2,-1,1,1,315.0],[2,-2,0,-1,302.0],[0,0,1,3,-283.0],
[2,1,1,-1,-229.0],[1,1,0,-1,223.0],[1,1,0,1,223.0],
[0,1,-2,-1,-220.0],[2,1,-1,-1,-220.0],[1,0,1,1,-185.0],
[2,-1,-2,-1,181.0],[0,1,2,1,-177.0],[4,0,-2,-1,176.0],
[4,-1,-1,-1,166.0],[1,0,1,-1,-164.0],[4,0,1,-1,132.0],
[1,0,-1,-1,-119.0],[4,-1,0,-1,115.0],[2,-2,0,1,107.0],
])


def julian_day(tahun, bulan, hari_desimal):
    """JD (UT) dari kalender Gregorian -- algoritma standar Meeus Ch.7."""
    Y, M = np.asarray(tahun, dtype=float), np.asarray(bulan, dtype=float)
    D = np.asarray(hari_desimal, dtype=float)
    Y = np.where(M <= 2, Y - 1, Y)
    M = np.where(M <= 2, M + 12, M)
    A = np.floor(Y / 100.0)
    B = 2 - A + np.floor(A / 4.0)
    jd = np.floor(365.25 * (Y + 4716)) + np.floor(30.6001 * (M + 1)) + D + B - 1524.5
    return jd


def jd_ke_gregorian(jd):
    """Kebalikan dari julian_day(): JD (UT, skalar) -> (tahun, bulan, hari_int)
    kalender Gregorian -- algoritma standar Meeus Ch.7. Bagian jam/menit
    dari JD dibuang (dibulatkan ke hari kalender terdekat), karena
    konverter kalender di GUI cuma butuh presisi tanggal, bukan jam."""
    jd = jd + 0.5
    Z = math.floor(jd)
    F = jd - Z
    if Z < 2299161:
        A = Z
    else:
        alpha = math.floor((Z - 1867216.25) / 36524.25)
        A = Z + 1 + alpha - math.floor(alpha / 4.0)
    B = A + 1524
    C = math.floor((B - 122.1) / 365.25)
    D = math.floor(365.25 * C)
    E = math.floor((B - D) / 30.6001)
    hari_desimal = B - D - math.floor(30.6001 * E) + F
    bulan = int(E - 1) if E < 14 else int(E - 13)
    tahun = int(C - 4716) if bulan > 2 else int(C - 4715)
    hari = int(round(hari_desimal))
    # Penanganan tepi: pembulatan hari_desimal bisa melewati batas bulan
    # (mis. 30.999 -> dibulatkan 31 padahal bulan itu cuma 30 hari).
    if hari > _hari_dalam_bulan_gregorian(tahun, bulan):
        hari = 1
        bulan += 1
        if bulan > 12:
            bulan = 1
            tahun += 1
    return tahun, bulan, hari


def _hari_dalam_bulan_gregorian(tahun, bulan):
    """Jumlah hari dalam satu bulan Gregorian (memperhitungkan tahun kabisat)."""
    if bulan == 2:
        kabisat = (tahun % 4 == 0 and tahun % 100 != 0) or (tahun % 400 == 0)
        return 29 if kabisat else 28
    return [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][bulan - 1]


def delta_t_detik(tahun, bulan=1):
    """Delta-T (TT-UT) dalam detik. Himpunan polinomial lengkap Espenak-Meeus
    (Five Millennium Canon, 2006) untuk rentang 1000-2005, diperbarui dengan
    ekstrapolasi termutakhir Espenak (2014) untuk 2005 ke atas.
    Akurasi terbaik di 1900-2050 (< 1 detik). Sebelum 1600 atau setelah 3000
    akurasinya menurun (bisa puluhan detik/menit) karena sifatnya
    rekonstruksi historis / ekstrapolasi jangka panjang -- wajar untuk ΔT."""
    y = np.asarray(tahun, dtype=float) + (bulan - 0.5) / 12.0

    def poly(t, coeffs):
        r = np.zeros_like(t)
        for c in reversed(coeffs):
            r = r * t + c
        return r

    dt_500_1600 = poly((y - 1000.0) / 100.0,
                        [1574.2, -556.01, 71.23472, 0.319781,
                         -0.8503463, -0.005050998, 0.0083572073])
    dt_1600_1700 = poly(y - 1600.0, [120.0, -0.9808, -0.01532, 1.0 / 7129.0])
    dt_1700_1800 = poly(y - 1700.0, [8.83, 0.1603, -0.0059285, 0.00013336,
                                      -1.0 / 1174000.0])
    dt_1800_1860 = poly(y - 1800.0, [13.72, -0.332447, 0.0068612, 0.0041116,
                                      -0.00037436, 0.0000121272,
                                      -0.0000001699, 0.000000000875])
    dt_1860_1900 = poly(y - 1860.0, [7.62, 0.5737, -0.251754, 0.01680668,
                                      -0.0004473624, 1.0 / 233174.0])
    dt_1900_1920 = poly(y - 1900.0, [-2.79, 1.494119, -0.0598939, 0.0061966, -0.000197])
    dt_1920_1941 = poly(y - 1920.0, [21.20, 0.84493, -0.076100, 0.0020936])
    dt_1941_1961 = poly(y - 1950.0, [29.07, 0.407, -1.0 / 233.0, 1.0 / 2547.0])
    dt_1961_1986 = poly(y - 1975.0, [45.45, 1.067, -1.0 / 260.0, -1.0 / 718.0])
    dt_1986_2005 = poly(y - 2000.0, [63.86, 0.3345, -0.060374, 0.0017275,
                                      0.000651814, 0.00002373599])
    dt_2005_2015 = 64.69 + 0.2930 * (y - 2005.0)
    dt_2015_up = 67.62 + 0.3645 * (y - 2015.0) + 0.0039755 * (y - 2015.0) ** 2
    u_far = (y - 1820.0) / 100.0
    dt_far = -20.0 + 32.0 * u_far ** 2

    condlist = [
        y < 1600, y < 1700, y < 1800, y < 1860, y < 1900,
        y < 1920, y < 1941, y < 1961, y < 1986, y < 2005,
        y < 2015, y < 3000,
    ]
    choicelist = [
        dt_500_1600, dt_1600_1700, dt_1700_1800, dt_1800_1860, dt_1860_1900,
        dt_1900_1920, dt_1920_1941, dt_1941_1961, dt_1961_1986, dt_1986_2005,
        dt_2005_2015, dt_2015_up,
    ]
    return np.select(condlist, choicelist, default=dt_far)


def nutasi_singkat(T):
    """Nutasi longitude & obliquity, deret ringkas 4 suku (~0.5" akurasi;
    lebih dari cukup untuk kebutuhan kita)."""
    Omega = np.radians((125.04452 - 1934.136261 * T) % 360)
    L = np.radians((280.4665 + 36000.7698 * T) % 360)
    Lp = np.radians((218.3165 + 481267.8813 * T) % 360)
    dpsi = (-17.20 * np.sin(Omega) - 1.32 * np.sin(2 * L)
            - 0.23 * np.sin(2 * Lp) + 0.21 * np.sin(2 * Omega)) / 3600.0
    deps = (9.20 * np.cos(Omega) + 0.57 * np.cos(2 * L)
            + 0.10 * np.cos(2 * Lp) - 0.09 * np.cos(2 * Omega)) / 3600.0
    return dpsi, deps  # derajat


def posisi_matahari(T):
    """Meeus Ch.25 -- posisi Matahari geosentris apparent. T = abad Julian TT
    dari J2000.0. Return: ra_deg, dec_deg, lambda_deg, R_au (semua array)."""
    L0 = 280.46646 + 36000.76983 * T + 0.0003032 * T ** 2
    M = 357.52911 + 35999.05029 * T - 0.0001537 * T ** 2
    e = 0.016708634 - 0.000042037 * T - 0.0000001267 * T ** 2
    Mr = np.radians(M % 360)
    C = ((1.914602 - 0.004817 * T - 0.000014 * T ** 2) * np.sin(Mr)
         + (0.019993 - 0.000101 * T) * np.sin(2 * Mr)
         + 0.000289 * np.sin(3 * Mr))
    true_long = L0 + C
    true_anom = M + C
    R = (1.000001018 * (1 - e ** 2)) / (1 + e * np.cos(np.radians(true_anom)))
    Omega = 125.04 - 1934.136 * T
    lam = true_long - 0.00569 - 0.00478 * np.sin(np.radians(Omega))
    eps0 = 23 + 26 / 60 + 21.448 / 3600 - (46.8150 * T + 0.00059 * T ** 2
                                           - 0.001813 * T ** 3) / 3600
    eps = eps0 + 0.00256 * np.cos(np.radians(Omega))
    lam_r, eps_r = np.radians(lam), np.radians(eps)
    ra = np.degrees(np.arctan2(np.cos(eps_r) * np.sin(lam_r), np.cos(lam_r))) % 360
    dec = np.degrees(np.arcsin(np.sin(eps_r) * np.sin(lam_r)))
    return ra, dec, lam % 360, R


def posisi_bulan(T):
    """Meeus Ch.47 / ELP2000-82B (tabel penuh) -- posisi Bulan geosentris.
    T = abad Julian TT dari J2000.0 (array/scalar numpy).
    Return: ra_deg, dec_deg, lambda_deg, beta_deg, delta_km, parallax_deg."""
    T = np.atleast_1d(np.asarray(T, dtype=float))

    Lp = 218.3164477 + 481267.88123421 * T - 0.0015786 * T**2 + T**3/538841 - T**4/65194000
    D  = 297.8501921 + 445267.1114034 * T - 0.0018819 * T**2 + T**3/545868 - T**4/113065000
    M  = 357.5291092 + 35999.0502909 * T - 0.0001536 * T**2 + T**3/24490000
    Mp = 134.9633964 + 477198.8675055 * T + 0.0087414 * T**2 + T**3/69699.9 + T**4/14712000
    F  = 93.2720950 + 483202.0175233 * T - 0.0036539 * T**2 - T**3/3526000 + T**4/863310000
    A1 = 119.75 + 131.849 * T
    A2 = 53.09 + 479264.290 * T
    A3 = 313.45 + 481266.484 * T
    E = 1.0 - 0.002516 * T - 0.0000074 * T**2
    E2 = E ** 2

    Dr, Mr, Mpr, Fr = (np.radians(x % 360) for x in (D, M, Mp, F))
    Lpr = np.radians(Lp % 360)
    A1r, A2r, A3r = (np.radians(x % 360) for x in (A1, A2, A3))

    args = np.stack([Dr, Mr, Mpr, Fr], axis=-1)  # (..., 4)

    arg_lr = args @ _LR[:, :4].T  # (..., 60)
    e_pow_lr = np.where(np.abs(_LR[:, 1]) == 1, E[..., None],
                 np.where(np.abs(_LR[:, 1]) == 2, E2[..., None], 1.0))
    sigmal = np.sum(e_pow_lr * _LR[:, 4] * np.sin(arg_lr), axis=-1)
    sigmar = np.sum(e_pow_lr * _LR[:, 5] * np.cos(arg_lr), axis=-1)
    sigmal += 3958.0*np.sin(A1r) + 1962.0*np.sin(Lpr - Fr) + 318.0*np.sin(A2r)

    arg_b = args @ _B[:, :4].T
    e_pow_b = np.where(np.abs(_B[:, 1]) == 1, E[..., None],
                np.where(np.abs(_B[:, 1]) == 2, E2[..., None], 1.0))
    sigmab = np.sum(e_pow_b * _B[:, 4] * np.sin(arg_b), axis=-1)
    sigmab += (-2235.0*np.sin(Lpr) + 382.0*np.sin(A3r) + 175.0*np.sin(A1r - Fr)
               + 175.0*np.sin(A1r + Fr) + 127.0*np.sin(Lpr - Mpr) - 115.0*np.sin(Lpr + Mpr))

    lam = (Lp + sigmal / 1e6) % 360
    beta = sigmab / 1e6
    delta = 385000.56 + sigmar / 1e3
    parallax = np.degrees(np.arcsin(6378.14 / delta))

    dpsi, deps = nutasi_singkat(T)
    lam_app = lam + dpsi
    eps0 = 23 + 26/60 + 21.448/3600 - (46.8150*T + 0.00059*T**2 - 0.001813*T**3)/3600
    eps = eps0 + deps

    lam_r, beta_r, eps_r = np.radians(lam_app), np.radians(beta), np.radians(eps)
    ra = np.degrees(np.arctan2(
        np.sin(lam_r)*np.cos(eps_r) - np.tan(beta_r)*np.sin(eps_r), np.cos(lam_r))) % 360
    dec = np.degrees(np.arcsin(
        np.sin(beta_r)*np.cos(eps_r) + np.cos(beta_r)*np.sin(eps_r)*np.sin(lam_r)))
    return ra, dec, lam_app % 360, beta, delta, parallax


def gast_derajat(jd_ut, T, dpsi_deg, eps_deg):
    """Greenwich Apparent Sidereal Time, dalam derajat (Meeus Ch.12).
    jd_ut = JD (UT), T = abad Julian TT dari J2000 (dipakai jg utk GMST)."""
    d0 = jd_ut - 2451545.0
    gmst = (280.46061837 + 360.98564736629 * d0
            + 0.000387933 * T ** 2 - T ** 3 / 38710000.0) % 360
    eq_equinox = dpsi_deg * np.cos(np.radians(eps_deg))  # koreksi nutasi -> GAST
    return (gmst + eq_equinox) % 360


def altitude_geosentris(lat_deg, dec_deg, H_deg):
    """Altitude 'geosentris' (tanpa koreksi paralaks) -- dipakai utk kriteria
    tinggi hilal geosentris Muhammadiyah, sama persis definisinya dgn kode asli."""
    lat_r, dec_r, H_r = np.radians(lat_deg), np.radians(dec_deg), np.radians(H_deg)
    return np.degrees(np.arcsin(np.sin(lat_r) * np.sin(dec_r)
                                 + np.cos(lat_r) * np.cos(dec_r) * np.cos(H_r)))


def altitude_topocentris_bulan(alt_geo_deg, parallax_deg):
    """Koreksi paralaks utk altitude Bulan (Bumi dianggap bulat sempurna --
    pendekatan standar 'parallax in altitude' ~ pi*cos(alt); parallax Bulan
    bisa sampai ~1 derajat & PENTING karena ambang kriteria cuma 3-5 derajat).
    Hasilnya masih altitude TRUE (geometris), BELUM dikoreksi refraksi --
    lihat koreksi_refraksi() untuk langkah berikutnya."""
    p = parallax_deg * np.cos(np.radians(alt_geo_deg))
    return alt_geo_deg - p


def koreksi_refraksi(alt_true_deg):
    """Refraksi atmosfer standar (formula Saemundsson, Meeus Eq.16.4),
    atmosfer standar 10°C & 1010 mbar. Input: altitude TRUE/geometris
    (belum terefraksi), derajat. Return: besar refraksi (derajat, SELALU
    positif) yang harus DITAMBAHKAN ke altitude true untuk mendapat
    altitude APPARENT (yang benar-benar terlihat pengamat).
    Akurasi ~0.07' turun sampai ke horizon; wajar dipakai untuk kriteria
    hilal karena jauh lebih presisi daripada mengabaikannya sama sekali
    (refraksi di dekat horizon bisa ~0.5 derajat, signifikan dibanding
    ambang kriteria 3-8 derajat)."""
    h0 = np.asarray(alt_true_deg, dtype=float)
    R_arcmin = 1.02 / np.tan(np.radians(h0 + 10.3 / (h0 + 5.11)))
    return R_arcmin / 60.0


def cari_ijtimak_tahun_ringan(tahun):
    """Mencari semua waktu ijtimak (konjungsi, selisih longitude Bulan-Matahari
    = 0) sepanjang tahun, tanpa file eksternal. Sampling tiap 6 jam (jauh lebih
    rapat dari jarak antar-ijtimak ~29.5 hari, jadi tidak ada yang kelewat),
    lalu disempurnakan dengan interpolasi linear ke presisi menit.
    Return: list of datetime (UTC).
    """
    t0 = datetime(tahun, 1, 1)
    total_jam = 366 * 24 + 48  # sedikit lebih dari setahun, jaga2 tahun kabisat
    jam_arr = np.arange(0, total_jam, 6.0)
    waktu_arr = [t0 + timedelta(hours=float(h)) for h in jam_arr]

    tahun_a = np.array([w.year for w in waktu_arr])
    bulan_a = np.array([w.month for w in waktu_arr])
    hari_a = np.array([w.day + (w.hour + w.minute/60.0)/24.0 for w in waktu_arr])

    jd_ut = julian_day(tahun_a, bulan_a, hari_a)
    dt_arr = delta_t_detik(tahun_a, bulan_a)
    T = (jd_ut + dt_arr / 86400.0 - 2451545.0) / 36525.0

    _, _, lam_sun, _ = posisi_matahari(T)
    _, _, lam_moon, _, _, _ = posisi_bulan(T)

    selisih = (lam_moon - lam_sun + 180) % 360 - 180  # -180..180

    hasil = []
    naik = np.where((selisih[:-1] < 0) & (selisih[1:] >= 0))[0]
    for i in naik:
        frac = -selisih[i] / (selisih[i + 1] - selisih[i])
        jam_c = jam_arr[i] + frac * (jam_arr[i + 1] - jam_arr[i])
        waktu_ijtimak = t0 + timedelta(hours=float(jam_c))
        if waktu_ijtimak.year == tahun:
            hasil.append(waktu_ijtimak)
    return hasil


def cari_istiqbal_tahun_ringan(tahun):
    """Mencari semua waktu ISTIQBAL (oposisi/purnama, selisih longitude
    Bulan-Matahari = 180 derajat) sepanjang tahun -- basis waktu gerhana
    BULAN, sama seperti ijtimak basis gerhana MATAHARI. Struktur & metode
    PERSIS sama dgn cari_ijtimak_tahun_ringan(), cuma titik potongnya
    digeser 180 derajat (bukan cari selisih==0, tapi selisih==180)."""
    t0 = datetime(tahun, 1, 1)
    total_jam = 366 * 24 + 48
    jam_arr = np.arange(0, total_jam, 6.0)
    waktu_arr = [t0 + timedelta(hours=float(h)) for h in jam_arr]

    tahun_a = np.array([w.year for w in waktu_arr])
    bulan_a = np.array([w.month for w in waktu_arr])
    hari_a = np.array([w.day + (w.hour + w.minute/60.0)/24.0 for w in waktu_arr])

    jd_ut = julian_day(tahun_a, bulan_a, hari_a)
    dt_arr = delta_t_detik(tahun_a, bulan_a)
    T = (jd_ut + dt_arr / 86400.0 - 2451545.0) / 36525.0

    _, _, lam_sun, _ = posisi_matahari(T)
    _, _, lam_moon, _, _, _ = posisi_bulan(T)

    # beda dgn ijtimak: selisih digeser 180 derajat dulu sblm di-wrap ke
    # -180..180, supaya titik nol yg dicari jadi pas oposisi (bukan konjungsi)
    selisih = (lam_moon - lam_sun) % 360 - 180

    hasil = []
    naik = np.where((selisih[:-1] < 0) & (selisih[1:] >= 0))[0]
    for i in naik:
        frac = -selisih[i] / (selisih[i + 1] - selisih[i])
        jam_c = jam_arr[i] + frac * (jam_arr[i + 1] - jam_arr[i])
        waktu_istiqbal = t0 + timedelta(hours=float(jam_c))
        if waktu_istiqbal.year == tahun:
            hasil.append(waktu_istiqbal)
    return hasil


# =========================================================
#  HISAB URFI -- PELABELAN TAHUN/BULAN HIJRIYAH
#
#  Aplikasi ini AGNOSTIK terhadap kalender Hijriyah: semua fungsi di atas
#  (ijtimak, istiqbal, evaluasi_pkg, dst) cuma tahu WAKTU ASTRONOMIS
#  (datetime UTC), tidak tahu ini "bulan apa, tahun berapa H". Modul ini
#  isinya hisab URFI (tabular/aritmatik, BUKAN astronomis) yang dipakai
#  SEMATA-MATA sbg "penunjuk arah" -- tebakan awal utk mencocokkan tiap
#  ijtimak asli ke label (tahun H, bulan H) yang benar, BUKAN sbg sumber
#  kebenaran tanggal itu sendiri.
#
#  KENAPA INI VALID (bukan cuma "cara gampang"): siklus urfi 30-tahun
#  dirancang supaya rata2 panjang bulannya HAMPIR PERSIS sama dgn rata2
#  bulan sinodis asli (10.631 hari / 360 bulan = 29,53056 hari, vs bulan
#  sinodis asli 29,530589 hari -- beda cuma ~0,01 hari per 30 tahun,
#  praktis tanpa drift kumulatif jangka pendek/menengah). Satu2nya sumber
#  selisih per-bulan ya variasi kecepatan Bulan asli (perige/apoge, biasa
#  cuma nggeser ijtimak asli beberapa jam-1 hari dari urfi), BUKAN
#  kesalahan sistematis yg menumpuk -- makanya "cari ijtimak asli
#  terdekat dari tebakan urfi" itu robust & tidak ambigu dlm skala
#  tahun-ke-tahun (dua ijtimak asli berturutan tidak akan pernah sama2
#  jadi kandidat terdekat ke urfi bulan yang sama).
#
#  Formula & pola kabisat (11 tahun kabisat dari 30, di posisi
#  {2,5,7,10,13,16,18,21,24,26,29} dlm siklus) adalah pola "tipe IIa" /
#  "Kuwaiti algorithm" -- pola PALING UMUM dipakai (al-Fazari,
#  al-Khwarizmi, al-Battani, Toledan/Alfonsine Tables, Microsoft), SUDAH
#  DIVERIFIKASI terhadap referensi independen (Wikipedia "Tabular Islamic
#  calendar": 18 Februari 2026 = 1 Ramadan 1447 H tabular -- formula di
#  bawah menghasilkan tanggal PERSIS SAMA).
# =========================================================

_EPOCH_URFI_JD = 1948439.5   # JD 1 Muharram 1 H (tabular, tengah malam)
_NAMA_BULAN_HIJRIYAH = ["Muharram", "Safar", "Rabiul Awal", "Rabiul Akhir",
                        "Jumadil Awal", "Jumadil Akhir", "Rajab", "Syaban",
                        "Ramadan", "Syawal", "Dzulqaidah", "Dzulhijjah"]


def _urfi_kabisat(tahun_h):
    """True kalau tahun_h (tahun Hijriyah) kabisat menurut siklus 30-tahun
    tipe IIa/Kuwaiti -- posisi kabisat dlm siklus: 2,5,7,10,13,16,18,21,24,26,29."""
    posisi = ((tahun_h - 1) % 30) + 1
    return posisi in (2, 5, 7, 10, 13, 16, 18, 21, 24, 26, 29)


def _urfi_ke_jd(tahun_h, bulan_h, hari_h=1):
    """Konversi tanggal urfi (tabular, BUKAN astronomis) -> Julian Day.
    Formula standar type-IIa/Kuwaiti (lihat catatan modul di atas),
    epoch _EPOCH_URFI_JD = 1 Muharram 1 H."""
    return (hari_h
            + math.ceil(29.5 * (bulan_h - 1))
            + (tahun_h - 1) * 354
            + math.floor((3 + 11 * tahun_h) / 30)
            + _EPOCH_URFI_JD - 1)


def _jd_ke_urfi(jd):
    """Konversi Julian Day -> tanggal urfi (tahun_h, bulan_h, hari_h).
    Kebalikan dari _urfi_ke_jd() -- estimasi awal analitik lalu dikoreksi
    dgn loop kecil (biasanya konvergen dlm 0-1 iterasi, standar teknik dari
    Calendrical Calculations/Dershowitz & Reingold)."""
    jd_bulat = math.floor(jd) + 0.5
    tahun_h = math.floor((30 * (jd_bulat - _EPOCH_URFI_JD) + 10646) / 10631)

    while _urfi_ke_jd(tahun_h + 1, 1, 1) <= jd_bulat:
        tahun_h += 1
    while _urfi_ke_jd(tahun_h, 1, 1) > jd_bulat:
        tahun_h -= 1

    bulan_h = 1
    while bulan_h < 12 and _urfi_ke_jd(tahun_h, bulan_h + 1, 1) <= jd_bulat:
        bulan_h += 1

    hari_h = int(jd_bulat - _urfi_ke_jd(tahun_h, bulan_h, 1) + 1)
    return tahun_h, bulan_h, hari_h


def _cari_label_hijriyah_urfi(jd_ijtimak):
    """Untuk satu ijtimak asli (Julian Day-nya), cari label (tahun_h,
    bulan_h) yg PALING COCOK -- bandingkan jarak ke awal-bulan-urfi yg
    'memuat' ijtimak ini (dari _jd_ke_urfi) VS awal-bulan-urfi SETELAHNYA,
    ambil yg jaraknya PALING DEKAT. Ini implementasi persis dari ide
    "tebakan urfi sbg initial guess, ijtimak asli terdekat yg dipakai
    sbg patokan real" -- di sini arahnya dibalik (dari ijtimak asli,
    cari label urfi terdekat), tapi hasilnya ekuivalen."""
    tahun_h, bulan_h, _ = _jd_ke_urfi(jd_ijtimak)
    jd_awal_ini = _urfi_ke_jd(tahun_h, bulan_h, 1)

    tahun_h2, bulan_h2 = (tahun_h, bulan_h + 1) if bulan_h < 12 else (tahun_h + 1, 1)
    jd_awal_next = _urfi_ke_jd(tahun_h2, bulan_h2, 1)

    if abs(jd_ijtimak - jd_awal_next) < abs(jd_ijtimak - jd_awal_ini):
        return tahun_h2, bulan_h2
    return tahun_h, bulan_h


def beri_label_hijriyah(ijtimak_list):
    """Tempel label (tahun Hijriyah, nama bulan Hijriyah) ke tiap ijtimak
    ASLI (astronomis) dlm ijtimak_list (list datetime UTC, mis. gabungan
    hasil cari_ijtimak_tahun_ringan() lintas beberapa tahun Masehi berturut2
    supaya urutannya utuh -- SATU tahun Masehi biasanya memotong 1-2 tahun
    Hijriyah di tengah).

    Return: list of dict {'waktu_ijtimak', 'tahun_h', 'bulan_h',
    'nama_bulan_h'}, terurut sesuai urutan input.
    """
    hasil = []
    for waktu_ijtimak in ijtimak_list:
        jd = julian_day(waktu_ijtimak.year, waktu_ijtimak.month,
                         waktu_ijtimak.day + (waktu_ijtimak.hour
                                               + waktu_ijtimak.minute / 60.0
                                               + waktu_ijtimak.second / 3600.0) / 24.0)
        tahun_h, bulan_h = _cari_label_hijriyah_urfi(jd)
        hasil.append({"waktu_ijtimak": waktu_ijtimak, "tahun_h": tahun_h,
                      "bulan_h": bulan_h, "nama_bulan_h": _NAMA_BULAN_HIJRIYAH[bulan_h - 1]})
    return hasil



def masehi_ke_hijriyah_urfi(tahun, bulan, hari):
    """Konversi tanggal Masehi (Gregorian) -> tanggal Hijriyah URFI/tabular
    (tipe IIa/Kuwaiti, siklus 30-tahun) -- BUKAN hasil rukyat/hisab
    astronomis (ijtimak asli), melainkan perkiraan kalender tabular yang
    umum dipakai utk konversi cepat. Return (tahun_h, bulan_h, hari_h)."""
    jd = julian_day(tahun, bulan, float(hari))
    jd = float(np.asarray(jd).reshape(()))
    return _jd_ke_urfi(jd)


def hijriyah_urfi_ke_masehi(tahun_h, bulan_h, hari_h):
    """Kebalikan dari masehi_ke_hijriyah_urfi(): tanggal Hijriyah URFI/tabular
    -> tanggal Masehi (Gregorian). Return (tahun, bulan, hari)."""
    jd = _urfi_ke_jd(tahun_h, bulan_h, hari_h)
    return jd_ke_gregorian(jd)


def nama_hari_dari_jd(jd):
    """Nama hari (Indonesia) dari sebuah Julian Day. Formula standar:
    floor(JD + 1.5) mod 7 -> 0=Minggu, 1=Senin, ..., 6=Sabtu (JD 2451545.0,
    1 Jan 2000 12:00 TT, jatuh Sabtu -- dipakai sbg patokan validasi)."""
    indeks = int(math.floor(jd + 1.5)) % 7
    return HARI_ID[indeks]


RE_EKUATOR_KM = 6378.137   # WGS84
RE_KUTUB_KM = 6356.752
_ECC2_BUMI = 1 - (RE_KUTUB_KM / RE_EKUATOR_KM) ** 2
AU_KM = 149597870.7


def _vektor_matahari_bulan_gast(waktu, mode="ringan", ts=None, eph=None):
    """Posisi geosentris Matahari & Bulan dalam Kartesian ekuator langit (km),
    plus GAST (derajat) & beta (lintang ekliptika Bulan, derajat), pada satu
    waktu (datetime UTC). Dipakai bersama oleh HAMPIR SEMUA fungsi gerhana
    di file ini (langsung atau lewat versi batch-nya) -- makanya menambah
    mode='jpl' di SINI SAJA (+ versi batch-nya) otomatis membuat SELURUH
    pipeline gerhana (kandidat, lintasan, kontak, dsb) presisi, TANPA perlu
    mengubah geometri/logika di fungsi manapun yg memanggilnya -- mereka
    semua cuma konsumsi (P_sun, P_moon, gast, beta), tidak peduli dari
    mana asalnya.

    mode='ringan' (default) -> VSOP87 ringkas + ELP2000-82B (fungsi ini
    sendiri, TANPA file eksternal apapun).
    mode='jpl' -> didelegasikan ke _vektor_matahari_bulan_gast_jpl()
    (Skyfield + ephemeris JPL DE421 penuh, butuh ts & eph -- app.ts/app.eph
    yg sudah dimuat di tempat lain, SAMA persis yg dipakai mode Presisi di
    tab2 lain spt Efemeris/Waktu Sholat).
    """
    if mode == "jpl":
        return _vektor_matahari_bulan_gast_jpl(waktu, ts, eph)

    jd_ut = julian_day(waktu.year, waktu.month,
                        waktu.day + (waktu.hour + waktu.minute / 60.0
                                     + waktu.second / 3600.0) / 24.0)
    dt_detik = delta_t_detik(waktu.year, waktu.month)
    T = (jd_ut + dt_detik / 86400.0 - 2451545.0) / 36525.0

    ra_sun, dec_sun, _, R_au = posisi_matahari(T)
    ra_moon, dec_moon, _, beta, delta_moon_km, _ = posisi_bulan(T)
    dpsi, deps = nutasi_singkat(T)
    eps0 = (23 + 26/60 + 21.448/3600
            - (46.8150*T + 0.00059*T**2 - 0.001813*T**3) / 3600)
    gast = float(np.ravel(gast_derajat(jd_ut, T, dpsi, eps0 + deps))[0])

    R_sun_km = float(np.ravel(R_au)[0]) * AU_KM
    R_moon_km = float(np.ravel(delta_moon_km)[0])
    ra_s, dec_s = float(np.ravel(ra_sun)[0]), float(np.ravel(dec_sun)[0])
    ra_m, dec_m = float(np.ravel(ra_moon)[0]), float(np.ravel(dec_moon)[0])

    def _ke_kartesian(ra, dec, r):
        rar, decr = np.radians(ra), np.radians(dec)
        return np.array([r * np.cos(decr) * np.cos(rar),
                          r * np.cos(decr) * np.sin(rar),
                          r * np.sin(decr)])

    # Kerangka ekuator langit geosentris (RA dari equinox, dec dari ekuator
    # langit) SEJAJAR kerangka Bumi-tetap krn berbagi sumbu z (poros rotasi
    # Bumi = poros ekuator langit) -- beda RA vs bujur Bumi murni soal GAST.
    P_sun = _ke_kartesian(ra_s, dec_s, R_sun_km)
    P_moon = _ke_kartesian(ra_m, dec_m, R_moon_km)
    return P_sun, P_moon, gast, float(np.ravel(beta)[0])


def _vektor_matahari_bulan_gast_jpl(waktu, ts, eph):
    """Versi PRESISI (Skyfield + ephemeris JPL DE421) dari
    _vektor_matahari_bulan_gast() -- KONTRAK OUTPUT SAMA PERSIS (P_sun,
    P_moon geosentris Kartesian ekuator-langit-tanggal km, gast derajat,
    beta lintang ekliptika Bulan-tanggal derajat) supaya bisa saling
    ditukar tanpa mengubah kode geometri di pemanggilnya. Posisi
    Matahari/Bulan dihitung APPARENT (geometris+cahaya+aberasi, dari
    Bumi) dgn RA/Dec EKUATOR TANGGAL (epoch='date') -- konsisten dgn
    RA versi 'ringan' (dari equinox tanggal itu sendiri, bukan J2000)."""
    earth, sun, moon = eph["earth"], eph["sun"], eph["moon"]
    t = ts.utc(waktu.year, waktu.month, waktu.day,
               waktu.hour, waktu.minute, waktu.second + waktu.microsecond / 1e6)

    astro_sun = earth.at(t).observe(sun).apparent()
    astro_moon = earth.at(t).observe(moon).apparent()
    ra_sun, dec_sun, dist_sun = astro_sun.radec(epoch='date')
    ra_moon, dec_moon, dist_moon = astro_moon.radec(epoch='date')
    beta_moon, _, _ = astro_moon.ecliptic_latlon(epoch='date')
    gast = float(t.gast) * 15.0   # jam -> derajat

    def _ke_kartesian(ra_deg, dec_deg, r):
        rar, decr = np.radians(ra_deg), np.radians(dec_deg)
        return np.array([r * np.cos(decr) * np.cos(rar),
                          r * np.cos(decr) * np.sin(rar),
                          r * np.sin(decr)])

    P_sun = _ke_kartesian(ra_sun.hours * 15.0, dec_sun.degrees, dist_sun.km)
    P_moon = _ke_kartesian(ra_moon.hours * 15.0, dec_moon.degrees, dist_moon.km)
    return P_sun, P_moon, gast, float(beta_moon.degrees)


def _vektor_matahari_bulan_gast_batch(waktu_dasar, menit_offset, mode="ringan", ts=None, eph=None):
    """Versi VEKTOR (numpy) dari _vektor_matahari_bulan_gast() -- hitung
    SEKALIGUS utk banyak titik waktu (waktu_dasar + menit_offset[i] menit),
    bukan for-loop Python manggil versi skalar berkali-kali.

    Semua fungsi di bawah (julian_day, posisi_matahari, posisi_bulan,
    nutasi_singkat, gast_derajat) SUDAH menerima array numpy -- jadi
    perbaikannya murni di sisi PEMANGGIL: kumpulkan semua titik waktu jadi
    satu array, panggil sekali, bukan panggil fungsi2 mahal itu (terutama
    posisi_bulan, deret ELP2000 60-suku) satu-per-satu per titik.

    delta_t_detik() SENGAJA dihitung SEKALI (pakai tahun/bulan waktu_dasar,
    bukan per-titik) -- delta T cuma berubah berarti dalam skala TAHUN,
    bukan jam/menit, jadi aman dipakai bersama utk seluruh jendela +-4 jam
    yg dipakai fungsi2 pemindaian gerhana di file ini.

    mode='jpl' -> didelegasikan ke _vektor_matahari_bulan_gast_batch_jpl()
    (butuh ts & eph), lihat catatan lengkap soal kenapa mode ditambahkan
    di sini di docstring _vektor_matahari_bulan_gast().

    Return: P_sun (N,3 km), P_moon (N,3 km), gast (N, derajat), beta (N, derajat).
    """
    if mode == "jpl":
        return _vektor_matahari_bulan_gast_batch_jpl(waktu_dasar, menit_offset, ts, eph)

    menit_offset = np.asarray(menit_offset, dtype=float)

    dt_detik = float(delta_t_detik(waktu_dasar.year, waktu_dasar.month))
    hari_dasar = (waktu_dasar.day + (waktu_dasar.hour + waktu_dasar.minute / 60.0
                                      + waktu_dasar.second / 3600.0) / 24.0)
    hari_arr = hari_dasar + menit_offset / 1440.0

    jd_ut = julian_day(waktu_dasar.year, waktu_dasar.month, hari_arr)
    T = (jd_ut + dt_detik / 86400.0 - 2451545.0) / 36525.0

    ra_sun, dec_sun, _, R_au = posisi_matahari(T)
    ra_moon, dec_moon, _, beta, delta_moon_km, _ = posisi_bulan(T)
    dpsi, deps = nutasi_singkat(T)
    eps0 = (23 + 26/60 + 21.448/3600
            - (46.8150*T + 0.00059*T**2 - 0.001813*T**3) / 3600)
    gast = np.asarray(gast_derajat(jd_ut, T, dpsi, eps0 + deps))

    R_sun_km = np.asarray(R_au) * AU_KM
    R_moon_km = np.asarray(delta_moon_km)
    ra_s, dec_s = np.asarray(ra_sun), np.asarray(dec_sun)
    ra_m, dec_m = np.asarray(ra_moon), np.asarray(dec_moon)

    def _ke_kartesian_batch(ra, dec, r):
        rar, decr = np.radians(ra), np.radians(dec)
        return np.stack([r * np.cos(decr) * np.cos(rar),
                          r * np.cos(decr) * np.sin(rar),
                          r * np.sin(decr)], axis=-1)  # shape (N, 3)

    P_sun = _ke_kartesian_batch(ra_s, dec_s, R_sun_km)
    P_moon = _ke_kartesian_batch(ra_m, dec_m, R_moon_km)
    return P_sun, P_moon, gast, np.asarray(beta)


def _vektor_matahari_bulan_gast_batch_jpl(waktu_dasar, menit_offset, ts, eph):
    """Versi VEKTOR dari _vektor_matahari_bulan_gast_jpl() -- Skyfield/JPL
    NATIVE mendukung array waktu (objek Time bisa berisi banyak elemen),
    jadi satu panggilan .observe()/.apparent() sudah otomatis vektor,
    TIDAK perlu for-loop Python manggil versi skalar berkali-kali (sama
    semangatnya dgn _vektor_matahari_bulan_gast_batch() versi ringan)."""
    menit_offset = np.asarray(menit_offset, dtype=float)
    earth, sun, moon = eph["earth"], eph["sun"], eph["moon"]
    t = ts.utc(waktu_dasar.year, waktu_dasar.month, waktu_dasar.day,
               waktu_dasar.hour, waktu_dasar.minute + menit_offset,
               waktu_dasar.second + waktu_dasar.microsecond / 1e6)

    astro_sun = earth.at(t).observe(sun).apparent()
    astro_moon = earth.at(t).observe(moon).apparent()
    ra_sun, dec_sun, dist_sun = astro_sun.radec(epoch='date')
    ra_moon, dec_moon, dist_moon = astro_moon.radec(epoch='date')
    beta_moon, _, _ = astro_moon.ecliptic_latlon(epoch='date')
    gast = np.asarray(t.gast) * 15.0

    def _ke_kartesian_batch(ra_deg, dec_deg, r):
        rar, decr = np.radians(ra_deg), np.radians(dec_deg)
        return np.stack([r * np.cos(decr) * np.cos(rar),
                          r * np.cos(decr) * np.sin(rar),
                          r * np.sin(decr)], axis=-1)

    P_sun = _ke_kartesian_batch(np.asarray(ra_sun.hours) * 15.0,
                                 np.asarray(dec_sun.degrees), np.asarray(dist_sun.km))
    P_moon = _ke_kartesian_batch(np.asarray(ra_moon.hours) * 15.0,
                                  np.asarray(dec_moon.degrees), np.asarray(dist_moon.km))
    return P_sun, P_moon, gast, np.asarray(beta_moon.degrees)


def _jarak_sumbu_ke_pusat_bumi_km_batch(P_sun, P_moon):
    """Versi vektor dari _jarak_sumbu_ke_pusat_bumi_km() -- P_sun/P_moon
    berbentuk (N, 3). Return array (N,) jarak (km)."""
    d = P_moon - P_sun
    return np.linalg.norm(np.cross(P_sun, d), axis=1) / np.linalg.norm(d, axis=1)


def _titik_bayangan_ellipsoid_batch(P_sun, P_moon, gast):
    """Versi vektor dari _titik_bayangan_ellipsoid(). P_sun/P_moon (N,3),
    gast (N,). Return (kena_bumi (N, bool), lat_geodetik (N,), lon (N,),
    gamma (N,)) -- nilai lat/lon TIDAK BERARTI di baris yang kena_bumi=False
    (garis tidak menyentuh ellipsoid di titik itu), pemanggil wajib filter
    pakai kena_bumi sebelum memakai lat/lon."""
    d = P_moon - P_sun
    Px, Py, Pz = P_sun[:, 0], P_sun[:, 1], P_sun[:, 2]
    dx, dy, dz = d[:, 0], d[:, 1], d[:, 2]
    A = (dx**2 + dy**2) / RE_EKUATOR_KM**2 + dz**2 / RE_KUTUB_KM**2
    B = 2*(Px*dx + Py*dy) / RE_EKUATOR_KM**2 + 2*Pz*dz / RE_KUTUB_KM**2
    C = (Px**2 + Py**2) / RE_EKUATOR_KM**2 + Pz**2 / RE_KUTUB_KM**2 - 1
    diskriminan = B**2 - 4*A*C
    kena_bumi = diskriminan >= 0

    gamma = np.linalg.norm(np.cross(P_sun, d), axis=1) / np.linalg.norm(d, axis=1) / RE_EKUATOR_KM

    # np.where dulu sebelum sqrt supaya gak keluar RuntimeWarning "invalid value"
    # dari sqrt(negatif) di baris2 yg kena_bumi=False (hasilnya dibuang kok,
    # tapi numpy tetap ngitung elementwise utk SELURUH array dulu).
    disk_aman = np.where(kena_bumi, diskriminan, 0.0)
    t_dekat = (-B - np.sqrt(disk_aman)) / (2*A)

    titik = P_sun + t_dekat[:, None] * d
    x, y, z = titik[:, 0], titik[:, 1], titik[:, 2]
    lon_geosentris = np.degrees(np.arctan2(y, x)) % 360
    lat_geosentris = np.arctan2(z, np.sqrt(x**2 + y**2))
    lat_geodetik = np.degrees(np.arctan2(np.tan(lat_geosentris), 1 - _ECC2_BUMI))
    lon_hit = ((lon_geosentris - gast + 180) % 360) - 180
    return kena_bumi, lat_geodetik, lon_hit, gamma


def _subtitik_sumbu_bayangan_batch(P_sun, P_moon, gast):
    """Versi vektor dari _subtitik_sumbu_bayangan() -- SELALU menghasilkan
    titik (proyeksi radial titik-terdekat-ke-pusat-Bumi kalau garis meleset
    dari ellipsoid), beda dari _titik_bayangan_ellipsoid_batch yg butuh
    garis betul2 tembus. P_sun/P_moon (N,3), gast (N,). Return
    (lat_geodetik (N,), lon (N,))."""
    d = P_moon - P_sun
    Px, Py, Pz = P_sun[:, 0], P_sun[:, 1], P_sun[:, 2]
    dx, dy, dz = d[:, 0], d[:, 1], d[:, 2]
    A = (dx**2 + dy**2) / RE_EKUATOR_KM**2 + dz**2 / RE_KUTUB_KM**2
    B = 2*(Px*dx + Py*dy) / RE_EKUATOR_KM**2 + 2*Pz*dz / RE_KUTUB_KM**2
    C = (Px**2 + Py**2) / RE_EKUATOR_KM**2 + Pz**2 / RE_KUTUB_KM**2 - 1
    diskriminan = B**2 - 4*A*C
    kena_bumi = diskriminan >= 0

    disk_aman = np.where(kena_bumi, diskriminan, 0.0)
    t_dekat = (-B - np.sqrt(disk_aman)) / (2*A)

    d_norm = np.linalg.norm(d, axis=1)
    u = d / d_norm[:, None]
    t_proyeksi = np.sum(-P_sun * u, axis=1)   # dot(-P_sun, u) per baris

    # PENTING: t_dekat adalah pecahan SEPANJANG d (satuannya "fraksi", t=1
    # persis di titik Bulan) -- makanya dipasangkan dengan d. t_proyeksi
    # sebaliknya dihitung lewat dot product dengan u (VEKTOR SATUAN), jadi
    # satuannya km, bukan fraksi -- harus dipasangkan dengan u juga, BUKAN d
    # (memasangkannya dengan d salah total: menyisakan skala sisa sebesar
    # |d| ~ 1 SA, membuat titik hasil melenceng liar, arahnya condong ke
    # arah d itu sendiri yang justru mendekati ANTIPODA dari lokasi
    # gerhana sesungguhnya -- lihat versi skalar _subtitik_sumbu_bayangan()
    # di atas sbg acuan yang benar: kasus meleset memakai `P_sun + t * u`).
    t_pakai = np.where(kena_bumi, t_dekat, t_proyeksi)
    vektor_arah = np.where(kena_bumi[:, None], d, u)
    titik = P_sun + t_pakai[:, None] * vektor_arah
    x, y, z = titik[:, 0], titik[:, 1], titik[:, 2]
    lon_geosentris = np.degrees(np.arctan2(y, x)) % 360
    lat_geosentris = np.arctan2(z, np.sqrt(x**2 + y**2))
    lat_geodetik = np.degrees(np.arctan2(np.tan(lat_geosentris), 1 - _ECC2_BUMI))
    lon_hit = ((lon_geosentris - gast + 180) % 360) - 180
    return lat_geodetik, lon_hit


def _radius_bayangan_km_batch(P_sun, P_moon):
    """Versi vektor dari _radius_bayangan_km(). P_sun/P_moon (N,3).
    Return (gamma_km (N,), r_umbra_km (N,), r_penumbra_km (N,))."""
    d = P_moon - P_sun
    d_sm = np.linalg.norm(d, axis=1)
    u = d / d_sm[:, None]
    gamma_km = np.linalg.norm(np.cross(P_sun, u), axis=1)

    t_dari_matahari = np.sum(-P_sun * u, axis=1)
    t_dari_bulan = t_dari_matahari - d_sm

    r_umbra_km = R_BULAN_KM - t_dari_bulan * (R_MATAHARI_KM - R_BULAN_KM) / d_sm
    r_penumbra_km = R_BULAN_KM + t_dari_bulan * (R_MATAHARI_KM + R_BULAN_KM) / d_sm
    return gamma_km, r_umbra_km, r_penumbra_km


def _jarak_bulan_ke_sumbu_bayangan_bumi_km_batch(P_sun, P_moon):
    """Versi GERHANA BULAN dari _radius_bayangan_km_batch(): geometri kerucut
    bayangan yg sama persis (segitiga sebangun Matahari-benda), TAPI dgn
    peran benda pengoklusi ditukar -- di gerhana Matahari, Bulan yg
    mengoklusi & bayangannya dievaluasi di permukaan Bumi; di gerhana Bulan,
    BUMI yg mengoklusi (radius RE_EKUATOR_KM, bukan R_BULAN_KM) & bayangannya
    dievaluasi di POSISI BULAN (bukan di permukaan sesuatu).

    Sumbu bayangan Bumi = garis dari Matahari lewat PUSAT Bumi diperpanjang
    ke arah antisolar (krn geosentris, P_bumi=titik asal, jadi arahnya
    tinggal -P_sun ternormalisasi). P_sun/P_moon (N,3) km.

    Return: (jarak_bulan_ke_sumbu_km (N,), r_umbra_km (N,), r_penumbra_km (N,))
    -- r_umbra/r_penumbra di sini itu radius KERUCUT BAYANGAN BUMI di
    JARAK BULAN saat itu (beda dari makna serupa di fungsi gerhana Matahari,
    yg radiusnya kerucut bayangan BULAN)."""
    Re = RE_EKUATOR_KM
    d_se = np.linalg.norm(P_sun, axis=1)          # jarak Matahari-Bumi
    u = -P_sun / d_se[:, None]                     # arah antisolar (sumbu bayangan Bumi)

    t_dari_bumi = np.sum(P_moon * u, axis=1)        # proyeksi posisi Bulan ke sumbu
    titik_sumbu = t_dari_bumi[:, None] * u
    jarak_bulan_ke_sumbu = np.linalg.norm(P_moon - titik_sumbu, axis=1)

    r_umbra_km = Re - t_dari_bumi * (R_MATAHARI_KM - Re) / d_se
    r_penumbra_km = Re + t_dari_bumi * (R_MATAHARI_KM + Re) / d_se
    return jarak_bulan_ke_sumbu, r_umbra_km, r_penumbra_km


def _jarak_sumbu_ke_pusat_bumi_km(waktu):
    """Jarak tegak lurus (km) dari pusat Bumi ke garis sumbu bayangan
    (garis Matahari-Bulan diperpanjang). Ini yang diminimalkan utk mencari
    waktu GREATEST ECLIPSE sesungguhnya -- analog parameter gamma Besselian
    (di sini masih dalam km, bukan radius Bumi)."""
    P_sun, P_moon, _, _ = _vektor_matahari_bulan_gast(waktu)
    d = P_moon - P_sun
    return np.linalg.norm(np.cross(P_sun, d)) / np.linalg.norm(d)


def _cari_waktu_greatest_lunar_eclipse(waktu_istiqbal, jendela_menit=180, langkah_menit=3,
                                        mode="ringan", ts=None, eph=None):
    """Versi GERHANA BULAN dari _cari_waktu_greatest_eclipse() -- refine
    waktu istiqbal (oposisi geosentris) ke waktu GREATEST ECLIPSE
    sesungguhnya (saat Bulan paling dekat ke sumbu bayangan Bumi). Struktur
    & alasan PERSIS sama dgn versi Matahari, cuma jarak yg diminimalkan
    beda (jarak Bulan-ke-sumbu, bukan jarak sumbu-ke-pusat-Bumi)."""
    offset = np.arange(-jendela_menit, jendela_menit + langkah_menit, langkah_menit, dtype=float)
    P_sun, P_moon, _, _ = _vektor_matahari_bulan_gast_batch(waktu_istiqbal, offset, mode, ts, eph)
    jarak, _, _ = _jarak_bulan_ke_sumbu_bayangan_bumi_km_batch(P_sun, P_moon)
    i = int(np.argmin(jarak))

    if 0 < i < len(offset) - 1:
        y0, y1, y2 = jarak[i - 1], jarak[i], jarak[i + 1]
        penyebut = (y0 - 2*y1 + y2)
        koreksi = 0.5 * (y0 - y2) / penyebut if penyebut != 0 else 0.0
        offset_halus = offset[i] + koreksi * langkah_menit
    else:
        offset_halus = offset[i]

    return waktu_istiqbal + timedelta(minutes=float(offset_halus))


def _cari_waktu_greatest_eclipse(waktu_ijtimak, jendela_menit=180, langkah_menit=3,
                                  mode="ringan", ts=None, eph=None):
    """Refine waktu ijtimak GEOSENTRIS ke waktu GREATEST ECLIPSE sesungguhnya
    (saat sumbu bayangan paling dekat ke pusat Bumi) -- KEDUANYA bisa beda
    (terverifikasi lewat pengujian manual thd gerhana asli: offsetnya kecil,
    ~beberapa menit saja utk kasus2 yg dicoba, TAPI koreksi ini penting utk
    akurasi lokasi -- tanpa refinement ini, tebakan lat/lon greatest eclipse
    bisa meleset >1 derajat).

    Metode: sampling kasar +-jendela_menit di sekitar ijtimak (default +-3
    jam, tiap 3 menit -- jauh lebih rapat dari skala perubahan geometri
    gerhana), ambil titik minimum, lalu refine lewat interpolasi parabola
    3 titik di sekitarnya (presisi sub-menit tanpa perlu sampling super
    rapat).
    """
    offset = np.arange(-jendela_menit, jendela_menit + langkah_menit, langkah_menit, dtype=float)
    P_sun, P_moon, _, _ = _vektor_matahari_bulan_gast_batch(waktu_ijtimak, offset, mode, ts, eph)
    jarak = _jarak_sumbu_ke_pusat_bumi_km_batch(P_sun, P_moon)
    i = int(np.argmin(jarak))

    # interpolasi parabola pakai 3 titik (i-1, i, i+1) kalau bukan di ujung
    if 0 < i < len(offset) - 1:
        y0, y1, y2 = jarak[i - 1], jarak[i], jarak[i + 1]
        penyebut = (y0 - 2*y1 + y2)
        koreksi = 0.5 * (y0 - y2) / penyebut if penyebut != 0 else 0.0
        offset_halus = offset[i] + koreksi * langkah_menit
    else:
        offset_halus = offset[i]

    return waktu_ijtimak + timedelta(minutes=float(offset_halus))


def _titik_bayangan_ellipsoid(waktu, mode="ringan", ts=None, eph=None):
    """Titik potong garis sumbu bayangan (Matahari-Bulan diperpanjang) dgn
    permukaan ELLIPSOID WGS84 Bumi, di waktu tertentu (idealnya waktu
    greatest eclipse hasil _cari_waktu_greatest_eclipse(), bukan waktu
    ijtimak mentah). Return (lat_geodetik, lon, gamma_radius_bumi) atau
    None kalau garis tidak menyentuh ellipsoid (gerhana parsial-saja,
    umbra lewat di luar Bumi)."""
    P_sun, P_moon, gast, _ = _vektor_matahari_bulan_gast(waktu, mode, ts, eph)
    d = P_moon - P_sun

    Px, Py, Pz = P_sun
    dx, dy, dz = d
    A = (dx**2 + dy**2) / RE_EKUATOR_KM**2 + dz**2 / RE_KUTUB_KM**2
    B = 2*(Px*dx + Py*dy) / RE_EKUATOR_KM**2 + 2*Pz*dz / RE_KUTUB_KM**2
    C = (Px**2 + Py**2) / RE_EKUATOR_KM**2 + Pz**2 / RE_KUTUB_KM**2 - 1
    diskriminan = B**2 - 4*A*C
    if diskriminan < 0:
        return None

    gamma = (np.linalg.norm(np.cross(P_sun, d)) / np.linalg.norm(d)) / RE_EKUATOR_KM

    # akar sisi-DEKAT (dari arah Matahari menuju Bulan lalu Bumi)
    t_dekat = (-B - np.sqrt(diskriminan)) / (2*A)
    x, y, z = P_sun + t_dekat * d
    lon_geosentris = np.degrees(np.arctan2(y, x)) % 360
    lat_geosentris = np.arctan2(z, np.sqrt(x**2 + y**2))
    lat_geodetik = np.degrees(np.arctan2(np.tan(lat_geosentris), 1 - _ECC2_BUMI))
    lon_hit = ((lon_geosentris - gast + 180) % 360) - 180
    return lat_geodetik, lon_hit, gamma


def _subtitik_sumbu_bayangan(waktu, mode="ringan", ts=None, eph=None):
    """Titik pusat proyeksi bayangan pada permukaan Bumi (lat geodetik, lon).
    Jika sumbu bayangan menembus Bumi, mengembalikan titik perpotongan (sisi dekat).
    Jika meleset, mengembalikan proyeksi radial dari titik terdekat sumbu bayangan
    ke pusat Bumi (sebagai pendekatan terdekat).
    """
    P_sun, P_moon, gast, _ = _vektor_matahari_bulan_gast(waktu, mode, ts, eph)
    d = P_moon - P_sun

    Px, Py, Pz = P_sun
    dx, dy, dz = d
    A = (dx**2 + dy**2) / RE_EKUATOR_KM**2 + dz**2 / RE_KUTUB_KM**2
    B = 2*(Px*dx + Py*dy) / RE_EKUATOR_KM**2 + 2*Pz*dz / RE_KUTUB_KM**2
    C = (Px**2 + Py**2) / RE_EKUATOR_KM**2 + Pz**2 / RE_KUTUB_KM**2 - 1
    diskriminan = B**2 - 4*A*C

    if diskriminan >= 0:
        # Menembus Bumi -- gunakan titik perpotongan di sisi dekat
        t_dekat = (-B - np.sqrt(diskriminan)) / (2*A)
        x, y, z = P_sun + t_dekat * d
    else:
        # Melenceng/meleset dari Bumi -- gunakan proyeksi radial titik terdekat sumbu
        u = d / np.linalg.norm(d)
        t = float(np.dot(-P_sun, u))
        x, y, z = P_sun + t * u

    lon_geosentris = np.degrees(np.arctan2(y, x)) % 360
    lat_geosentris = np.arctan2(z, np.sqrt(x**2 + y**2))
    lat_geodetik = np.degrees(np.arctan2(np.tan(lat_geosentris), 1 - _ECC2_BUMI))
    lon_hit = ((lon_geosentris - gast + 180) % 360) - 180
    return lat_geodetik, lon_hit


R_MATAHARI_KM = 696000.0   # radius fisik Matahari
R_BULAN_KM = 1737.4        # radius fisik Bulan


def _radius_bayangan_km(waktu, mode="ringan", ts=None, eph=None):
    """Radius umbra & penumbra (km) pada bidang tegak lurus sumbu bayangan
    yang melewati titik sumbu TERDEKAT ke pusat Bumi (titik yang sama dipakai
    _jarak_sumbu_ke_pusat_bumi_km) -- dihitung dari geometri kerucut bayangan
    (segitiga sebangun berdasar radius fisik Matahari & Bulan serta jarak
    Matahari-Bulan). Ini pendekatan geometris sederhana (BUKAN Besselian
    elements penuh spt dipakai NASA/Meeus utk presisi maksimum), tapi cukup
    akurat (order ~1-2% di radius bayangan) utk MEMPERKIRAKAN waktu kontak
    P1/U1/U2/U3/U4/P4 pada peta lintasan -- konsisten dgn semangat "ringan"
    (approx tapi cukup) yg dipakai di seluruh modul gerhana ini.

    Return: (gamma_km, r_umbra_km, r_penumbra_km)
      gamma_km      : jarak tegak lurus sumbu bayangan ke pusat Bumi (km).
      r_umbra_km    : radius umbra pada bidang tsb (km). POSITIF = umbra
                       sungguhan (bayangan gelap, berpotensi gerhana TOTAL);
                       NEGATIF = sumbu sudah lewat titik puncak kerucut umbra,
                       besarnya (abs) jadi radius ANTUMBRA (gerhana CINCIN).
                       Uji kontak U1/U4 pakai abs(r_umbra_km) krn kedua kasus
                       (umbra ATAU antumbra menyentuh Bumi) sama2 dihitung
                       sbg "bayangan gelap/cincin menyentuh Bumi".
      r_penumbra_km : radius penumbra pada bidang tsb (km), selalu positif.
    """
    P_sun, P_moon, _, _ = _vektor_matahari_bulan_gast(waktu, mode, ts, eph)
    d = P_moon - P_sun
    d_sm = np.linalg.norm(d)          # jarak Matahari-Bulan
    u = d / d_sm                       # arah sumbu, dari Matahari menuju Bulan

    gamma_km = np.linalg.norm(np.cross(P_sun, u))

    # jarak (sepanjang sumbu, dari Bulan) ke titik sumbu terdekat ke pusat Bumi
    t_dari_matahari = float(np.dot(-P_sun, u))
    t_dari_bulan = t_dari_matahari - d_sm

    r_umbra_km = R_BULAN_KM - t_dari_bulan * (R_MATAHARI_KM - R_BULAN_KM) / d_sm
    r_penumbra_km = R_BULAN_KM + t_dari_bulan * (R_MATAHARI_KM + R_BULAN_KM) / d_sm

    return float(gamma_km), float(r_umbra_km), float(r_penumbra_km)


def cari_gerhana_matahari_kandidat_ringan(tahun, ambang_beta_derajat=1.8,
                                           mode="ringan", ts=None, eph=None):
    """Deteksi KANDIDAT gerhana matahari sepanjang tahun, dari waktu2 ijtimak
    yang sudah ada (cari_ijtimak_tahun_ringan, ATAU cari_ijtimak_tahun mode
    'jpl' kalau mode='jpl' -- lihat parameter mode di bawah). Metode:

    1) FILTER KANDIDAT: pas ijtimak, bujur ekliptika Matahari & Bulan SAMA
       persis (itu definisi konjungsi) -- jadi "jarak sudut" keduanya cuma
       ditentukan oleh lintang ekliptika Bulan (beta) saat itu. Kalau |beta|
       cukup kecil (ambang klasik/"ecliptic limit" ~1.5-1.8 derajat, Meeus),
       piringan Matahari & Bulan cukup dekat utk kemungkinan gerhana.
       Ambang 1.8 derajat sengaja agak longgar (lebih baik false-positive
       yang disaring belakangan drpd kelewat kandidat asli).

    2) REFINE WAKTU: cari waktu GREATEST ECLIPSE sesungguhnya (bukan cuma
       pas ijtimak) lewat _cari_waktu_greatest_eclipse() -- minimalkan
       jarak sumbu bayangan ke pusat Bumi. PENTING utk akurasi lokasi:
       diverifikasi manual thd gerhana asli 8 Apr 2024 & 2 Okt 2024, hasil
       lat/lon greatest eclipse setelah refinement ini presisi sampai
       <0.5 derajat dari data aktual (vs >1 derajat kalau pakai waktu
       ijtimak mentah).

    3) TEBAKAN LOKASI: titik potong garis 3D Matahari-Bulan dgn ELLIPSOID
       WGS84 Bumi di waktu greatest eclipse hasil refinement (bukan cuma
       arah sub-titik -- lihat _titik_bayangan_ellipsoid()).

    mode='ringan' (default) -> VSOP87+ELP2000, TIDAK butuh ts/eph sama
    sekali (basis waktu ijtimak dari cari_ijtimak_tahun_ringan).
    mode='jpl' -> Skyfield + ephemeris JPL DE421 penuh (butuh ts & eph),
    basis waktu ijtimak dari cari_ijtimak_tahun(mode='jpl') (almanac
    moon_phases, presisi tinggi) -- SELURUH pipeline geometri di bawahnya
    (greatest eclipse, radius bayangan, titik jatuh) ikut presisi juga krn
    semua cuma memanggil _vektor_matahari_bulan_gast(_batch) dgn mode/ts/
    eph yg sama (lihat catatan di fungsi itu).

    Return: list of dict, satu per kandidat, dengan keys:
      'waktu_ijtimak' (datetime), 'waktu_greatest_eclipse' (datetime),
      'beta' (derajat), 'kena_bumi' (bool), 'gamma' (radius Bumi, None
      kalau kena_bumi=False), 'lat_perkiraan', 'lon_perkiraan' (derajat,
      None kalau kena_bumi=False -- artinya kemungkinan gerhana PARSIAL
      saja di suatu wilayah luas, bukan berarti tidak ada gerhana sama
      sekali; umbra/antumbra lewat di luar Bumi tapi penumbra masih bisa
      menyentuh permukaan).
    """
    if mode == "jpl":
        ijtimak_list = [_ke_naif(ke_utc_datetime(t)) for t in cari_ijtimak_tahun(tahun, ts, eph, mode="jpl")]
    else:
        ijtimak_list = cari_ijtimak_tahun_ringan(tahun)
    hasil = []

    for waktu_ijtimak in ijtimak_list:
        _, _, _, beta = _vektor_matahari_bulan_gast(waktu_ijtimak, mode, ts, eph)

        entri = {"waktu_ijtimak": waktu_ijtimak, "waktu_greatest_eclipse": None,
                 "beta": beta, "kena_bumi": False, "gamma": None,
                 "lat_perkiraan": None, "lon_perkiraan": None}

        if abs(beta) < ambang_beta_derajat:
            waktu_greatest = _cari_waktu_greatest_eclipse(waktu_ijtimak, mode=mode, ts=ts, eph=eph)
            gamma_km, _, r_penumbra_km = _radius_bayangan_km(waktu_greatest, mode, ts, eph)
            if gamma_km <= RE_EKUATOR_KM + r_penumbra_km:
                entri["waktu_greatest_eclipse"] = waktu_greatest

                titik = _titik_bayangan_ellipsoid(waktu_greatest, mode, ts, eph)
                if titik is not None:
                    lat_geodetik, lon_hit, gamma = titik
                    entri["kena_bumi"] = True
                    entri["gamma"] = gamma
                    entri["lat_perkiraan"] = lat_geodetik
                    entri["lon_perkiraan"] = lon_hit

        hasil.append(entri)

    return hasil


def cari_gerhana_bulan_kandidat_ringan(tahun, ambang_beta_derajat=1.5,
                                        mode="ringan", ts=None, eph=None):
    """Deteksi KANDIDAT gerhana BULAN sepanjang tahun, dari waktu2 istiqbal
    (oposisi/purnama). Struktur & filosofi PERSIS sama dgn
    cari_gerhana_matahari_kandidat_ringan(), cuma basis waktunya istiqbal
    (bukan ijtimak) dan geometrinya "Bulan menembus bayangan Bumi" (bukan
    "bayangan Bulan menyentuh Bumi").

    mode='ringan' (default) -> VSOP87+ELP2000, basis waktu istiqbal dari
    cari_istiqbal_tahun_ringan().
    mode='jpl' -> Skyfield + ephemeris JPL DE421 penuh (butuh ts & eph),
    basis waktu istiqbal dari cari_istiqbal_tahun(mode='jpl') (almanac
    moon_phases) -- lihat catatan lengkap soal alasan/mekanisme ini di
    docstring cari_gerhana_matahari_kandidat_ringan(), berlaku sama persis
    di sini.

    1) FILTER KANDIDAT: |beta| Bulan pas istiqbal < ambang_beta_derajat.
       Ambang gerhana Bulan (~1.0-1.5 derajat) sedikit beda dari gerhana
       Matahari (~1.5-1.8 derajat) krn geometrinya beda (radius bayangan
       Bumi vs radius piringan Bulan) -- 1.5 derajat dipilih cukup longgar
       spy tidak ada gerhana penumbral tipis yg kelewat (lebih baik
       false-positive yg disaring lewat magnitudo drpd kelewat kandidat asli).

    2) REFINE WAKTU: cari waktu GREATEST ECLIPSE sesungguhnya lewat
       _cari_waktu_greatest_lunar_eclipse() -- minimalkan jarak Bulan ke
       sumbu bayangan Bumi (bukan cuma pas istiqbal).

    3) KLASIFIKASI JENIS & MAGNITUDO: bandingkan jarak Bulan-ke-sumbu thd
       radius umbra/penumbra Bumi di jarak Bulan saat itu (ditambah/dikurang
       radius fisik Bulan sendiri, R_BULAN_KM, krn Bulan bukan titik).
       Magnitudo umbral >= 1.0 berarti TOTAL (seluruh piringan Bulan masuk
       umbra), 0 < magnitudo umbral < 1.0 berarti SEBAGIAN (umbral), kalau
       magnitudo umbral <= 0 tapi magnitudo penumbral > 0 berarti
       PENUMBRAL SAJA (jauh lebih tipis/kurang kentara scr visual).

    Return: list of dict, satu per kandidat, dengan keys:
      'waktu_istiqbal', 'waktu_greatest_eclipse' (None kalau tdk lolos
      filter beta), 'beta', 'jenis' ('total'/'sebagian'/'penumbral'/
      'tidak ada gerhana'), 'magnitudo_umbral', 'magnitudo_penumbral'
      (None kalau tdk lolos filter beta).
    """
    if mode == "jpl":
        istiqbal_list = [_ke_naif(ke_utc_datetime(t)) for t in cari_istiqbal_tahun(tahun, ts, eph, mode="jpl")]
    else:
        istiqbal_list = cari_istiqbal_tahun_ringan(tahun)
    hasil = []

    for waktu_istiqbal in istiqbal_list:
        _, _, _, beta = _vektor_matahari_bulan_gast(waktu_istiqbal, mode, ts, eph)

        entri = {"waktu_istiqbal": waktu_istiqbal, "waktu_greatest_eclipse": None,
                 "beta": beta, "jenis": "tidak ada gerhana",
                 "magnitudo_umbral": None, "magnitudo_penumbral": None}

        if abs(beta) < ambang_beta_derajat:
            waktu_greatest = _cari_waktu_greatest_lunar_eclipse(waktu_istiqbal, mode=mode, ts=ts, eph=eph)
            entri["waktu_greatest_eclipse"] = waktu_greatest

            P_sun, P_moon, _, _ = _vektor_matahari_bulan_gast_batch(
                waktu_greatest, np.array([0.0]), mode, ts, eph)
            jarak, r_umbra, r_penumbra = _jarak_bulan_ke_sumbu_bayangan_bumi_km_batch(P_sun, P_moon)
            jarak, r_umbra, r_penumbra = float(jarak[0]), float(r_umbra[0]), float(r_penumbra[0])

            mag_umbral = (r_umbra + R_BULAN_KM - jarak) / (2 * R_BULAN_KM)
            mag_penumbral = (r_penumbra + R_BULAN_KM - jarak) / (2 * R_BULAN_KM)
            entri["magnitudo_umbral"] = mag_umbral
            entri["magnitudo_penumbral"] = mag_penumbral

            if mag_umbral >= 1.0:
                entri["jenis"] = "total"
            elif mag_umbral > 0.0:
                entri["jenis"] = "sebagian"
            elif mag_penumbral > 0.0:
                entri["jenis"] = "penumbral"
            # else: tetap "tidak ada gerhana" (lolos filter beta kasar tapi
            # geometri sebenarnya ternyata meleset -- mirip kasus solar yg
            # kena_bumi=False, wajar terjadi krn ambang beta sengaja longgar)

        hasil.append(entri)

    return hasil


def _alt_matahari_saja(tanggal, jam_utc_flat, lat_flat, lon_flat):
    """Versi RINGAN dari _altaz_matahari_bulan() yang CUMA menghitung altitude
    Matahari -- sengaja TIDAK memanggil posisi_bulan() (deret ELP2000, ~60
    suku) sama sekali.

    Dipakai khusus di tahap PENCARIAN WINDOW waktu sunset (bisection/linear-
    interpolation utk menemukan alt_matahari == -0.8333), yang secara logika
    memang cuma butuh posisi Matahari -- posisi Bulan di tahap ini TIDAK
    PERNAH dipakai (lihat _altaz_matahari_bulan asli: 3 dari 4 nilai
    kembaliannya (alt_moon_topo, alt_moon_geo, elong) dibuang begitu saja
    oleh pemanggilnya di tahap ini). posisi_bulan() adalah >20x lebih mahal
    per elemen dibanding posisi_matahari() (profiling: ELP2000 60-suku vs
    VSOP87 term yang jauh lebih sedikit), jadi menghindarinya di sini
    memangkas signifikan waktu hitung_grid_ringan()/PKG 2 Amerika (mode
    ringan) -- posisi Bulan baru dihitung SEKALI lagi lewat
    _altaz_matahari_bulan() di titik sunset final yang sudah presisi.
    """
    tahun_a = np.full(jam_utc_flat.shape, tanggal.year, dtype=float)
    bulan_a = np.full(jam_utc_flat.shape, tanggal.month, dtype=float)
    hari_a = tanggal.day + jam_utc_flat / 24.0

    jd_ut = julian_day(tahun_a, bulan_a, hari_a)
    dt = delta_t_detik(tanggal.year, tanggal.month)
    T = (jd_ut + dt / 86400.0 - 2451545.0) / 36525.0

    ra_s, dec_s, _, _ = posisi_matahari(T)
    dpsi, deps = nutasi_singkat(T)
    eps = (23 + 26/60 + 21.448/3600 - (46.8150*T)/3600) + deps

    gast = gast_derajat(jd_ut, T, dpsi, eps)
    lst = (gast + lon_flat) % 360
    H_sun = ((lst - ra_s + 180) % 360) - 180

    return altitude_geosentris(lat_flat, dec_s, H_sun)


def _altaz_matahari_bulan(tanggal, jam_utc_flat, lat_flat, lon_flat):
    """Hitung (alt_matahari_geo, alt_bulan_topo, alt_bulan_geo, elongasi,
    ra_sun, dec_sun, ra_moon, dec_moon, parallax) utk kombinasi (waktu, lokasi)
    yang sudah di-flatten -- dipanggil dari fungsi window-refinement/grid.
    jam_utc_flat boleh di luar 0-24 (dinormalisasi sendiri oleh julian_day)."""
    tahun_a = np.full(jam_utc_flat.shape, tanggal.year, dtype=float)
    bulan_a = np.full(jam_utc_flat.shape, tanggal.month, dtype=float)
    hari_a = tanggal.day + jam_utc_flat / 24.0

    jd_ut = julian_day(tahun_a, bulan_a, hari_a)
    dt = delta_t_detik(tanggal.year, tanggal.month)
    T = (jd_ut + dt / 86400.0 - 2451545.0) / 36525.0

    ra_s, dec_s, _, _ = posisi_matahari(T)
    ra_m, dec_m, _, _, _, par_m = posisi_bulan(T)
    dpsi, deps = nutasi_singkat(T)
    eps = (23 + 26/60 + 21.448/3600 - (46.8150*T)/3600) + deps

    gast = gast_derajat(jd_ut, T, dpsi, eps)
    lst = (gast + lon_flat) % 360

    H_sun = ((lst - ra_s + 180) % 360) - 180
    H_moon = ((lst - ra_m + 180) % 360) - 180

    alt_sun = altitude_geosentris(lat_flat, dec_s, H_sun)
    alt_moon_geo = altitude_geosentris(lat_flat, dec_m, H_moon)
    alt_moon_topo_true = altitude_topocentris_bulan(alt_moon_geo, par_m)
    # Koreksi refraksi HANYA di altitude toposentris Bulan (yang benar-benar
    # dilihat pengamat). alt_sun sengaja dibiarkan tanpa refraksi eksplisit
    # karena ambang sunset -0.8333 derajat sudah baku mengasumsikan refraksi
    # standar; alt_moon_geo (geosentris) juga sengaja tanpa refraksi karena
    # itu besaran teoretis untuk kriteria Muhammadiyah, bukan hasil amatan.
    alt_moon_topo = alt_moon_topo_true + koreksi_refraksi(alt_moon_topo_true)

    cos_elong = (np.sin(np.radians(dec_s)) * np.sin(np.radians(dec_m))
                 + np.cos(np.radians(dec_s)) * np.cos(np.radians(dec_m))
                 * np.cos(np.radians(ra_s - ra_m)))
    elong = np.degrees(np.arccos(np.clip(cos_elong, -1.0, 1.0)))

    return alt_sun, alt_moon_topo, alt_moon_geo, elong


def hitung_grid_ringan(tanggal, progress_cb=lambda msg: None, lat_range=None, lon_range=None):
    """Versi 'Ringan' dari hitung_grid() -- struktur & nama output IDENTIK
    dengan hitung_grid() asli (JPL), jadi bisa dipakai langsung oleh semua
    fungsi evaluasi/plotting yang sama tanpa perubahan apapun."""
    if lat_range is None:
        lat_range = np.arange(-90, 91, 4)
    if lon_range is None:
        lon_range = np.arange(-180, 181, 4)

    lons_2d, lats_2d = np.meshgrid(lon_range, lat_range)
    lats = lats_2d.ravel()
    lons = lons_2d.ravel()
    N = len(lats)

    progress_cb("1/4 (ringan): Menghitung 'clue' waktu terbenam (Trigonometri Bola)...")

    dt0 = delta_t_detik(tanggal.year, tanggal.month)
    jd0 = julian_day(tanggal.year, tanggal.month, tanggal.day + 0.5)
    T0 = (jd0 + dt0 / 86400.0 - 2451545.0) / 36525.0
    _, dec_sun0, _, _ = posisi_matahari(np.array([T0]))
    dec_rad = np.radians(dec_sun0[0])
    lat_rad = np.radians(lats)

    h0_rad = np.radians(-0.8333)
    cos_h = (np.sin(h0_rad) - np.sin(lat_rad) * np.sin(dec_rad)) / (np.cos(lat_rad) * np.cos(dec_rad))
    valid_mask = (cos_h >= -1.0) & (cos_h <= 1.0)
    valid_indices = np.where(valid_mask)[0]

    precise_sunset_hours = np.full(N, np.nan)
    eot_jam = equation_of_time_menit(tanggal) / 60.0

    progress_cb(f"2/4 (ringan): Mengevaluasi jendela waktu ekstrem untuk {len(valid_indices)} titik "
                f"(batch, tanpa loop Python)...")

    n_window = 5
    idx = valid_indices
    M = len(idx)

    if M > 0:
        h_rad = np.arccos(cos_h[idx])
        h_hours = np.degrees(h_rad) / 15.0
        sunset_utc_guess = 12.0 - eot_jam - (lons[idx] / 15.0) + h_hours

        offsets = np.linspace(-0.33, 0.33, n_window)
        t_window_2d = sunset_utc_guess[:, None] + offsets[None, :]

        lat_rep = np.repeat(lats[idx], n_window)
        lon_rep = np.repeat(lons[idx], n_window)
        t_flat = t_window_2d.ravel()

        alt_sun_flat = _alt_matahari_saja(tanggal, t_flat, lat_rep, lon_rep)
        alts_2d = alt_sun_flat.reshape(M, n_window)

        is_above = alts_2d > -0.8333
        crossings = is_above[:, :-1] & ~is_above[:, 1:]
        has_cross = crossings.any(axis=1)
        first_cross = np.argmax(crossings, axis=1)

        rows = np.where(has_cross)[0]
        c = first_cross[rows]

        alt1 = alts_2d[rows, c]
        alt2 = alts_2d[rows, c + 1]
        t1 = t_window_2d[rows, c]
        t2 = t_window_2d[rows, c + 1]

        fraction = (-0.8333 - alt1) / (alt2 - alt1)
        precise_sunset_hours[idx[rows]] = t1 + fraction * (t2 - t1)

    final_valid_locs = np.where(~np.isnan(precise_sunset_hours))[0]

    progress_cb(f"3/4 (ringan): Menghitung elongasi & tinggi hilal untuk {len(final_valid_locs)} titik...")

    elong_grid_1d = np.full(N, np.nan)
    alt_grid_1d = np.full(N, np.nan)
    geo_alt_grid_1d = np.full(N, np.nan)
    hours_utc_grid_1d = np.full(N, np.nan)

    if len(final_valid_locs) > 0:
        progress_cb("4/4 (ringan): Menghitung tinggi hilal geosentris & toposentris (batch)...")
        t_sunsets = precise_sunset_hours[final_valid_locs]
        lat_f = lats[final_valid_locs]
        lon_f = lons[final_valid_locs]

        alt_sun_f, alt_moon_topo_f, alt_moon_geo_f, elong_f = _altaz_matahari_bulan(
            tanggal, t_sunsets, lat_f, lon_f)

        elong_grid_1d[final_valid_locs] = elong_f
        alt_grid_1d[final_valid_locs] = alt_moon_topo_f
        geo_alt_grid_1d[final_valid_locs] = alt_moon_geo_f
        hours_utc_grid_1d[final_valid_locs] = t_sunsets

    elong_grid = elong_grid_1d.reshape(len(lat_range), len(lon_range))
    alt_grid = alt_grid_1d.reshape(len(lat_range), len(lon_range))
    geo_alt_grid = geo_alt_grid_1d.reshape(len(lat_range), len(lon_range))
    hours_utc_grid = hours_utc_grid_1d.reshape(len(lat_range), len(lon_range))

    progress_cb("Selesai! Semua grid (mode Ringan) berhasil dihitung.")

    lon_mesh, lat_mesh = np.meshgrid(lon_range, lat_range)

    return {
        "elong_grid": elong_grid,
        "alt_grid": alt_grid,
        "geo_alt_grid": geo_alt_grid,
        "hours_utc_grid": hours_utc_grid,
        "lon_mesh": lon_mesh,
        "lat_mesh": lat_mesh,
    }


def hitung_fajar_nz_ringan(tanggal_lokal, sudut_fajar=-18.0, lat_ref=-37.6905, lon_ref=178.5500):
    """Versi Ringan dari hitung_fajar_nz() -- cari waktu fajar (UTC) di titik
    referensi Selandia Baru pada 'tanggal_lokal', horizon = sudut_fajar."""
    jam = np.arange(-14.0, 14.0 + 1/120.0, 1/120.0)  # tiap 30 detik, jendela -14..+14 jam
    lat_rep = np.full(jam.shape, lat_ref)
    lon_rep = np.full(jam.shape, lon_ref)
    alt_sun, _, _, _ = _altaz_matahari_bulan(tanggal_lokal, jam, lat_rep, lon_rep)

    is_above = alt_sun > sudut_fajar
    naik = (~is_above[:-1]) & is_above[1:]  # transisi naik = fajar (matahari terbit dr bawah horizon fajar)
    idx = np.where(naik)[0]
    if len(idx) == 0:
        return None
    i = idx[0]
    frac = (sudut_fajar - alt_sun[i]) / (alt_sun[i+1] - alt_sun[i])
    jam_c = jam[i] + frac * (jam[i+1] - jam[i])
    return tanggal_lokal + timedelta(hours=float(jam_c))

# =========================================================
#  KRITERIA MUHAMMADIYAH (KHGT) — PKG 1 & PKG 2
#
#  PKG 1 (Parameter Kalender Global 1): bulan baru dimulai jika sebelum
#    pukul 24.00 UTC, di manapun di bumi, saat matahari terbenam sudah
#    terpenuhi tinggi hilal geosentris >=5 derajat DAN elongasi >=8 derajat.
#  PKG 2 (Parameter Kalender Global 2, fallback bila PKG 1 tidak terpenuhi):
#    bulan baru tetap dimulai jika (a) ijtimak terjadi sebelum fajar di
#    Selandia Baru pada hari lokal berikutnya, DAN (b) kriteria tinggi
#    hilal >=5 derajat & elongasi >=8 derajat terpenuhi di DARATAN UTAMA
#    benua Amerika (bukan pulau lepas pantai), sekalipun kejadian itu
#    baru terjadi setelah pukul 24.00 UTC.
# =========================================================

# Titik referensi Selandia Baru: East Cape, Pulau Utara — titik daratan utama
# NZ yang paling timur / paling awal menyambut fajar (bukan pulau kecil lepas
# pantai seperti Kepulauan Chatham).
NZ_REF_LAT = -37.6905
NZ_REF_LON = 178.5500

# Sudut depresi matahari yang dipakai sebagai definisi "fajar" (fajar shadiq /
# twilight astronomis). Bisa disesuaikan jika ingin memakai konvensi lain.
SUDUT_FAJAR_DERAJAT = -18.0

# Cache modul-level untuk poligon daratan utama benua Amerika, supaya file
# shapefile Natural Earth hanya dibaca & diproses satu kali.
_mainland_amerika_cache = None


def hitung_fajar_nz(tanggal_lokal, ts, eph, sudut_fajar=SUDUT_FAJAR_DERAJAT, mode="jpl"):
    """
    Mencari waktu fajar (UTC) di titik referensi Selandia Baru pada
    'tanggal_lokal' (tanggal kalender, dicari di sekitar tengah malam
    lokal NZ yaitu kira-kira UTC+13), memakai horizon = sudut_fajar.
    Mengembalikan objek datetime (UTC) atau None bila tidak ditemukan.
    mode='ringan' -> pakai hitung_fajar_nz_ringan (VSOP87+ELP2000).
    """
    if mode == "ringan":
        return hitung_fajar_nz_ringan(tanggal_lokal, sudut_fajar=sudut_fajar,
                                       lat_ref=NZ_REF_LAT, lon_ref=NZ_REF_LON)
    sun, earth = eph['sun'], eph['earth']
    topo = wgs84.latlon(NZ_REF_LAT, NZ_REF_LON)

    # Catatan performa: implementasi lama pakai almanac.risings_and_settings()
    # + almanac.find_discrete() -- pencarian diskrit generik Skyfield yang
    # men-sampling lalu bisection berulang sampai presisi ~1 detik, dan
    # terukur (profiling) memicu ~297 pemanggilan jplephem.spk.generate()
    # cuma untuk SATU titik referensi NZ. Padahal presisi yang benar-benar
    # dibutuhkan di sini cuma level detik/menit (dibandingkan waktu ijtimak
    # yang presisinya juga di orde itu), bukan sub-detik.
    #
    # Diganti dengan teknik yang SAMA seperti pencarian sunset di
    # hitung_grid_jpl()/hitung_fajar_nz_ringan(): satu array waktu yang
    # di-observe dalam SATU panggilan batch (bukan pencarian iteratif),
    # lalu interpolasi linear di sekitar titik transisi horizon. Jauh lebih
    # sedikit pemanggilan ephemeris utk hasil yang presisinya setara.
    jam = np.arange(-14.0, 14.0 + 1/12.0, 1/12.0)  # tiap 5 menit, jendela -14..+14 jam
    t_all = ts.utc(tanggal_lokal.year, tanggal_lokal.month, tanggal_lokal.day, jam)
    alt, _, _ = (earth + topo).at(t_all).observe(sun).apparent().altaz()
    alt_deg = alt.degrees

    is_above = alt_deg > sudut_fajar
    naik = (~is_above[:-1]) & is_above[1:]  # transisi naik = fajar
    idx = np.where(naik)[0]
    if len(idx) == 0:
        return None
    i = idx[0]
    frac = (sudut_fajar - alt_deg[i]) / (alt_deg[i + 1] - alt_deg[i])
    jam_c = jam[i] + frac * (jam[i + 1] - jam[i])

    t_final = ts.utc(tanggal_lokal.year, tanggal_lokal.month, tanggal_lokal.day, float(jam_c))
    return t_final.utc_datetime()


# Aset pra-olah daratan utama benua Amerika, dibundel satu folder dengan skrip ini.
#
# Jalur CEPAT (default): matriks boolean pra-raster 'mainland_amerika_mask.npz'
# (lookup O(1) per titik, dibuat sekali dengan generate_mask.py dari WKT).
#
# Jalur FALLBACK: poligon WKT 'mainland_amerika.wkt' (union Amerika Utara+
# Tengah & Amerika Selatan, pulau lepas & Greenland sudah dibuang, sudah
# disederhanakan dgn shapely.simplify(toleransi 0.01°)), atau shapefile Natural
# Earth 50m jika WKT juga tidak ada (lihat _muat_mainland_amerika_dari_shapefile).
_SCRIPT_DIR = _resource_base_dir()
ASET_MAINLAND_AMERIKA_NPZ = os.path.join(_SCRIPT_DIR, "mainland_amerika_mask.npz")
ASET_MAINLAND_AMERIKA = os.path.join(_SCRIPT_DIR, "mainland_amerika.wkt")

# Cache modul-level untuk matriks raster NPZ (jalur cepat PKG 2).
_amerika_mask_npz_cache = None


def _muat_amerika_mask_npz():
    """
    Memuat & meng-cache matriks boolean pra-raster dari 'mainland_amerika_mask.npz'.
    Return dict dengan kunci 'available' (bool) dan, bila ada, 'mask', 'lon_min',
    'lat_max', 'res'. Jalur ini jauh lebih cepat daripada shapely.contains_xy.
    """
    global _amerika_mask_npz_cache
    if _amerika_mask_npz_cache is not None:
        return _amerika_mask_npz_cache

    try:
        mask_data = np.load(ASET_MAINLAND_AMERIKA_NPZ)
        _amerika_mask_npz_cache = {
            "available": True,
            "mask": mask_data["mask"],
            "lon_min": float(mask_data["lon_min"]),
            "lat_max": float(mask_data["lat_max"]),
            "res": float(mask_data["res"]),
        }
    except FileNotFoundError:
        _amerika_mask_npz_cache = {"available": False}

    return _amerika_mask_npz_cache


def _muat_mainland_amerika():
    """
    Memuat & meng-cache poligon daratan UTAMA benua Amerika (fallback PKG 2
    bila NPZ tidak tersedia). Dua jalur:

      1) CEPAT: baca aset '{aset}' yang sudah dipra-olah & disederhanakan.
      2) FALLBACK (lambat): unduh & olah shapefile Natural Earth 50m penuh lewat
         cartopy, lihat _muat_mainland_amerika_dari_shapefile().
    """.format(aset=os.path.basename(ASET_MAINLAND_AMERIKA))
    global _mainland_amerika_cache
    if _mainland_amerika_cache is not None:
        return _mainland_amerika_cache

    if os.path.exists(ASET_MAINLAND_AMERIKA):
        with open(ASET_MAINLAND_AMERIKA, "r", encoding="utf-8") as f:
            _mainland_amerika_cache = shapely.from_wkt(f.read())
        return _mainland_amerika_cache

    _mainland_amerika_cache = _muat_mainland_amerika_dari_shapefile()
    return _mainland_amerika_cache


def _muat_mainland_amerika_dari_shapefile():
    """
    FALLBACK saja (lambat): membangun ulang poligon daratan utama benua
    Amerika langsung dari shapefile Natural Earth 50m via cartopy -- hanya
    dipakai kalau aset pra-olah 'mainland_amerika.wkt' tidak ada di sebelah
    skrip ini. Butuh koneksi internet (cartopy akan mengunduh shapefile-nya
    sendiri kalau belum ada di cache lokal cartopy) dan memproses ~500+
    poligon daratan dunia, jadi jauh lebih lambat daripada jalur aset WKT.

    Pendekatan: ambil beberapa poligon daratan terbesar (berdasar luas)
    yang beririsan dengan rentang bujur benua Amerika. Poligon benua utama
    jauh lebih besar dari poligon pulau mana pun di kawasan itu, sehingga
    cukup ambil 2 poligon terbesar (Amerika Utara & Amerika Selatan) dan
    buang Greenland secara eksplisit (poligon besar tapi bukan bagian
    daratan Amerika yang menyatu).

    Catatan: data Natural Earth resolusi 50m kadang memuat poligon yang
    invalid secara topologi (self-intersection tipis di garis pantai).
    Titik query yang jatuh persis di area itu bisa membuat operasi
    'contains' gagal/melempar galat GEOS. Maka tiap poligon dibersihkan
    dulu dengan buffer(0) (teknik standar utk memperbaiki self-intersection
    tanpa mengubah bentuk luar poligon secara berarti).

    Untuk meregenerasi aset 'mainland_amerika.wkt' (mis. kalau ingin
    memperbarui ke rilis Natural Earth terbaru): jalankan fungsi ini,
    lalu simpan hasilnya dengan:
        shapely.to_wkt(hasil.simplify(0.01, preserve_topology=True),
                        rounding_precision=4)
    ke file 'mainland_amerika.wkt' di folder yang sama dengan skrip ini.
    """
    shp = shpreader.natural_earth(resolution='50m', category='physical', name='land')
    kandidat = []
    for geom in shpreader.Reader(shp).geometries():
        # Filter bbox DULU (murah) sebelum operasi shapely yang mahal
        # (is_valid/buffer(0)) -- supaya poligon di luar benua Amerika
        # (Asia, Afrika, Antartika, dll) tidak ikut diproses percuma.
        minx, miny, maxx, maxy = geom.bounds
        if maxx < -170 or minx > -34:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        kandidat.append(geom)

    kandidat.sort(key=lambda g: g.area, reverse=True)

    mainland = []
    for g in kandidat:
        minx, miny, maxx, maxy = g.bounds
        cx = (minx + maxx) / 2.0
        # Buang Greenland: poligon besar tapi di kuadran timur laut & lintang
        # tinggi, terpisah dari daratan utama Amerika Utara.
        if cx > -60 and miny > 55:
            continue
        mainland.append(g)
        if len(mainland) >= 2:   # cukup: 1 poligon Amerika Utara + 1 Amerika Selatan
            break

    # Digabung jadi satu geometri (union) supaya pengecekan titik cukup
    # sekali panggilan shapely.contains_xy, bukan di-OR-kan manual per poligon.
    return shapely.union_all(mainland) if mainland else None


def _mask_mainland_amerika_raster(lat_mesh, lon_mesh, mask_info):
    """Lookup vectorized O(1) dari matriks boolean pra-raster (NPZ)."""
    am_mask = mask_info["mask"]
    lon_min = mask_info["lon_min"]
    lat_max = mask_info["lat_max"]
    res = mask_info["res"]
    nrows, ncols = am_mask.shape

    lon_min_bb, lon_max_bb = AMERIKA_LON_RANGE
    lat_min_bb, lat_max_bb = AMERIKA_LAT_RANGE
    mask = np.zeros(lat_mesh.shape, dtype=bool)
    kasar = ((lon_mesh >= lon_min_bb) & (lon_mesh <= lon_max_bb) &
             (lat_mesh >= lat_min_bb) & (lat_mesh <= lat_max_bb))
    if not np.any(kasar):
        return mask

    lons = lon_mesh[kasar]
    lats = lat_mesh[kasar]
    cols = ((lons - lon_min) / res).astype(np.intp)
    rows = ((lat_max - lats) / res).astype(np.intp)

    valid = (rows >= 0) & (rows < nrows) & (cols >= 0) & (cols < ncols)
    darat = np.zeros(lons.shape, dtype=bool)
    if np.any(valid):
        darat[valid] = am_mask[rows[valid], cols[valid]]
    mask[kasar] = darat
    return mask


def _mask_mainland_amerika_shapely(lat_mesh, lon_mesh):
    """Fallback: vectorized shapely.contains_xy dari poligon WKT/shapefile."""
    geom = _muat_mainland_amerika()
    mask = np.zeros(lat_mesh.shape, dtype=bool)
    if geom is None:
        return mask

    lon_min, lon_max = AMERIKA_LON_RANGE
    lat_min, lat_max = AMERIKA_LAT_RANGE
    kasar = (lon_mesh >= lon_min) & (lon_mesh <= lon_max) & \
            (lat_mesh >= lat_min) & (lat_mesh <= lat_max)

    if np.any(kasar):
        mask[kasar] = shapely.contains_xy(geom, lon_mesh[kasar], lat_mesh[kasar])
    return mask


# ---------------------------------------------------------------------
# Override titik khusus: dua titik "membandel" di ujung barat Semenanjung
# Alaska yang gampang salah klasifikasi oleh mask/poligon otomatis, karena
# daratan di sana menyempit jadi genting/tanjung tipis (di bawah resolusi
# mask raster 0.1° / ~11 km maupun toleransi simplifikasi poligon WKT 0.01°):
#
#   - Cold Bay, AK   : kota di daratan utama Semenanjung Alaska, tepat di
#                      leher sempit dekat Teluk Izembek / Cold Bay itu
#                      sendiri.
#   - Morzhovoi, AK  : desa (kini tak berpenghuni) di daratan utama
#                      Semenanjung Alaska, dekat Bechevin Bay.
#
# Keduanya bagian sah dari daratan utama Amerika (AS), tapi kadang lolos
# sebagai "laut"/bukan-mainland gara-gara resolusi data. Dampaknya ke hasil
# PKG 2 secara keseluruhan kecil (cuma 2 titik di ujung Aleutian), tapi
# supaya tidak keliru dibuang, kita paksa True lewat override koordinat
# manual berikut -- tanpa mengubah mask/poligon utama sama sekali.
TITIK_KHUSUS_AMERIKA = [
    ("Cold Bay, AK", 55.2055, -162.7085),
    ("Morzhovoi, AK", 54.9069, -163.3189),
]
# Radius toleransi (derajat, dibandingkan langsung lat/lon, bukan haversine)
# -- cukup lebar untuk menangkap titik grid terdekat baik pada grid kasar
# 1° (tahap 1 PKG 2) maupun grid halus 0.25° (tahap 2), tapi tetap sempit
# supaya tidak "membocorkan" status daratan ke titik laut lain di sekitarnya.
RADIUS_TITIK_KHUSUS_DERAJAT = 0.6


def _terapkan_override_titik_khusus_amerika(lat_mesh, lon_mesh, mask):
    """Memaksa True pada titik grid yang berada dalam radius toleransi dari
    salah satu TITIK_KHUSUS_AMERIKA (lihat catatan di atas). Dipanggil di
    akhir buat_mask_mainland_amerika() supaya berlaku otomatis di semua
    jalur (raster/shapely, grid kasar/halus)."""
    for _nama, lat0, lon0 in TITIK_KHUSUS_AMERIKA:
        dekat = (np.abs(lat_mesh - lat0) <= RADIUS_TITIK_KHUSUS_DERAJAT) & \
                (np.abs(lon_mesh - lon0) <= RADIUS_TITIK_KHUSUS_DERAJAT)
        mask[dekat] = True
    return mask


def buat_mask_mainland_amerika(lat_mesh, lon_mesh):
    """Mengembalikan array boolean sama bentuk dengan lat_mesh/lon_mesh:
    True jika titik grid tsb berada di daratan utama benua Amerika.

    Jalur utama: lookup O(1) dari 'mainland_amerika_mask.npz' (res 0.1°).
    Fallback: shapely.contains_xy dari WKT/shapefile Natural Earth.

    Bbox kasar memakai konstanta AMERIKA_LAT_RANGE/AMERIKA_LON_RANGE yang
    SAMA dengan yang dipakai pencarian PKG 2, supaya tidak ada titik yang
    tereliminasi keliru gara-gara dua kotak pembungkus yang berbeda.

    Setelah mask dasar didapat, diterapkan override kecil untuk dua titik
    "membandel" (False Pass & Morzhovoi, lihat TITIK_KHUSUS_AMERIKA) yang
    sering salah terklasifikasi gara-gara selat sempit di bawah resolusi
    data mainland.
    """
    mask_info = _muat_amerika_mask_npz()
    if mask_info.get("available"):
        mask = _mask_mainland_amerika_raster(lat_mesh, lon_mesh, mask_info)
    else:
        mask = _mask_mainland_amerika_shapely(lat_mesh, lon_mesh)
    return _terapkan_override_titik_khusus_amerika(lat_mesh, lon_mesh, mask)


def _hitung_titik_flat(tanggal, ts, eph, lats, lons, mode="jpl"):
    """Versi 'scattered-point' dari inti hitung_grid_jpl/hitung_grid_ringan:
    menerima array lat/lon 1D APA ADANYA (tidak harus persegi/meshgrid) dan
    mengembalikan (elong, alt, geo_alt, hours_utc) untuk titik-titik itu saja.

    Dipakai khusus oleh PKG 2 tahap 1 supaya titik-titik LAUTAN di dalam
    bounding box benua Amerika (yang jelas tidak mungkin lolos syarat
    "daratan utama Amerika") tidak ikut dihitung astronominya sama sekali --
    bukan cuma dibuang belakangan setelah dihitung. Logikanya identik 1:1
    dengan hitung_grid_jpl/hitung_grid_ringan, hanya operasinya berjalan di
    atas himpunan titik yang sudah disaring (biasanya jauh lebih sedikit
    daripada N = jumlah_lat * jumlah_lon kotak pembatas)."""
    N = len(lats)
    elong = np.full(N, np.nan)
    alt = np.full(N, np.nan)
    geo_alt = np.full(N, np.nan)
    hours_utc = np.full(N, np.nan)
    if N == 0:
        return elong, alt, geo_alt, hours_utc

    if mode == "ringan":
        dt0 = delta_t_detik(tanggal.year, tanggal.month)
        jd0 = julian_day(tanggal.year, tanggal.month, tanggal.day + 0.5)
        T0 = (jd0 + dt0 / 86400.0 - 2451545.0) / 36525.0
        _, dec_sun0, _, _ = posisi_matahari(np.array([T0]))
        dec_rad = np.radians(dec_sun0[0])
    else:
        sun, moon, earth = eph['sun'], eph['moon'], eph['earth']
        t_ref = ts.utc(tanggal.year, tanggal.month, tanggal.day, 12)
        _, dec, _ = earth.at(t_ref).observe(sun).apparent().radec()
        dec_rad = dec.radians

    lat_rad = np.radians(lats)
    h0_rad = np.radians(-0.8333)
    cos_h = (np.sin(h0_rad) - np.sin(lat_rad) * np.sin(dec_rad)) / (np.cos(lat_rad) * np.cos(dec_rad))
    valid_mask = (cos_h >= -1.0) & (cos_h <= 1.0)
    idx = np.where(valid_mask)[0]
    M = len(idx)
    if M == 0:
        return elong, alt, geo_alt, hours_utc

    eot_jam = equation_of_time_menit(tanggal) / 60.0
    h_hours = np.degrees(np.arccos(cos_h[idx])) / 15.0
    sunset_utc_guess = 12.0 - eot_jam - (lons[idx] / 15.0) + h_hours

    n_window = 5
    offsets = np.linspace(-0.33, 0.33, n_window)
    t_window_2d = sunset_utc_guess[:, None] + offsets[None, :]
    lat_rep = np.repeat(lats[idx], n_window)
    lon_rep = np.repeat(lons[idx], n_window)
    t_flat = t_window_2d.ravel()

    if mode == "ringan":
        alt_sun_flat = _alt_matahari_saja(tanggal, t_flat, lat_rep, lon_rep)
    else:
        t_micro_all = ts.utc(tanggal.year, tanggal.month, tanggal.day, t_flat)
        topo_all = wgs84.latlon(lat_rep, lon_rep)
        alt_sun_ap, _, _ = (earth + topo_all).at(t_micro_all).observe(sun).apparent().altaz()
        alt_sun_flat = alt_sun_ap.degrees

    alts_2d = alt_sun_flat.reshape(M, n_window)
    is_above = alts_2d > -0.8333
    crossings = is_above[:, :-1] & ~is_above[:, 1:]
    has_cross = crossings.any(axis=1)
    first_cross = np.argmax(crossings, axis=1)
    rows = np.where(has_cross)[0]
    c = first_cross[rows]
    alt1, alt2 = alts_2d[rows, c], alts_2d[rows, c + 1]
    t1, t2 = t_window_2d[rows, c], t_window_2d[rows, c + 1]
    fraction = (-0.8333 - alt1) / (alt2 - alt1)

    precise_sunset_hours = np.full(N, np.nan)
    precise_sunset_hours[idx[rows]] = t1 + fraction * (t2 - t1)

    final_valid_locs = np.where(~np.isnan(precise_sunset_hours))[0]
    if len(final_valid_locs) == 0:
        return elong, alt, geo_alt, hours_utc

    t_sunsets_hours = precise_sunset_hours[final_valid_locs]
    lat_f = lats[final_valid_locs]
    lon_f = lons[final_valid_locs]

    if mode == "ringan":
        alt_sun_f, alt_moon_topo_f, alt_moon_geo_f, elong_f = _altaz_matahari_bulan(
            tanggal, t_sunsets_hours, lat_f, lon_f)
        elong[final_valid_locs] = elong_f
        alt[final_valid_locs] = alt_moon_topo_f
        geo_alt[final_valid_locs] = alt_moon_geo_f
        hours_utc[final_valid_locs] = t_sunsets_hours
    else:
        t_sunsets = ts.utc(tanggal.year, tanggal.month, tanggal.day, t_sunsets_hours)
        geo_sun = earth.at(t_sunsets).observe(sun).apparent()
        geo_moon = earth.at(t_sunsets).observe(moon).apparent()
        elong[final_valid_locs] = geo_sun.separation_from(geo_moon).degrees

        topo_final = wgs84.latlon(lat_f, lon_f)
        # temperature_C/pressure_mbar diisi -> Skyfield menerapkan refraksi
        # atmosfer standar (formula Bennett) ke altitude toposentris Bulan.
        # Sengaja TIDAK diisi utk altaz() Matahari (dipakai cuma sbg trigger
        # sunset -0.8333 derajat, yang sudah baku mengasumsikan refraksi
        # standar sendiri) -- lihat komentar di hitung_grid_jpl().
        alt_moon, az_moon, d_moon = (earth + topo_final).at(t_sunsets).observe(moon).apparent().altaz(
            temperature_C=10.0, pressure_mbar=1010.0)
        alt[final_valid_locs] = alt_moon.degrees

        ra, dec_moon, _ = geo_moon.radec(epoch='date')
        gast = t_sunsets.gast
        lst = (gast + lon_f / 15.0) % 24.0
        H_deg = (lst - ra.hours) * 15.0
        lat_r, dec_r, H_r = np.radians(lat_f), dec_moon.radians, np.radians(H_deg)
        geo_alt_f = np.degrees(np.arcsin(
            np.sin(lat_r) * np.sin(dec_r) + np.cos(lat_r) * np.cos(dec_r) * np.cos(H_r)
        ))
        geo_alt[final_valid_locs] = geo_alt_f
        hours_utc[final_valid_locs] = t_sunsets_hours

    return elong, alt, geo_alt, hours_utc


# =========================================================
#  PERHITUNGAN GRID (dijalankan di background thread)
#  Tidak melakukan plotting apa pun di sini — hanya angka.
#  progress_cb(str) dipakai untuk melaporkan progres ke GUI.
# =========================================================

def hitung_grid(tanggal, ts, eph, progress_cb=lambda msg: None, lat_range=None, lon_range=None,
                 mode="jpl"):
    """Dispatcher: mode='jpl' pakai skyfield+JPL DE421 (hitung_grid_jpl di
    bawah), mode='ringan' pakai VSOP87+ELP2000 (hitung_grid_ringan, tanpa
    file eksternal). Struktur dict hasil KEDUANYA identik."""
    if mode == "ringan":
        return hitung_grid_ringan(tanggal, progress_cb=progress_cb,
                                   lat_range=lat_range, lon_range=lon_range)
    return hitung_grid_jpl(tanggal, ts, eph, progress_cb=progress_cb,
                            lat_range=lat_range, lon_range=lon_range)


def hitung_grid_jpl(tanggal, ts, eph, progress_cb=lambda msg: None, lat_range=None, lon_range=None):
    sun, moon, earth = eph['sun'], eph['moon'], eph['earth']

    if lat_range is None:
        lat_range = np.arange(-90, 91, 4)
    if lon_range is None:
        lon_range = np.arange(-180, 181, 4)

    lons_2d, lats_2d = np.meshgrid(lon_range, lat_range)
    lats = lats_2d.ravel()
    lons = lons_2d.ravel()
    N = len(lats)

    progress_cb("1/4: Menghitung 'clue' waktu terbenam dengan Trigonometri Bola...")

    t_ref = ts.utc(tanggal.year, tanggal.month, tanggal.day, 12)
    _, dec, _ = earth.at(t_ref).observe(sun).apparent().radec()
    dec_rad = dec.radians
    lat_rad = np.radians(lats)

    h0_rad = np.radians(-0.8333)
    cos_h = (np.sin(h0_rad) - np.sin(lat_rad) * np.sin(dec_rad)) / (np.cos(lat_rad) * np.cos(dec_rad))
    valid_mask = (cos_h >= -1.0) & (cos_h <= 1.0)
    valid_indices = np.where(valid_mask)[0]

    precise_sunset_hours = np.full(N, np.nan)

    eot_menit = equation_of_time_menit(tanggal)
    eot_jam = eot_menit / 60.0

    progress_cb(f"2/4: Mengevaluasi jendela waktu ekstrem (±20 menit) untuk {len(valid_indices)} titik "
                f"(batch, tanpa loop Python)...")

    n_window = 5
    idx = valid_indices
    M = len(idx)

    if M > 0:
        h_rad = np.arccos(cos_h[idx])
        h_hours = np.degrees(h_rad) / 15.0
        sunset_utc_guess = 12.0 - eot_jam - (lons[idx] / 15.0) + h_hours

        offsets = np.linspace(-0.33, 0.33, n_window)
        t_window_2d = sunset_utc_guess[:, None] + offsets[None, :]

        lat_rep = np.repeat(lats[idx], n_window)
        lon_rep = np.repeat(lons[idx], n_window)
        t_flat = t_window_2d.ravel()

        t_micro_all = ts.utc(tanggal.year, tanggal.month, tanggal.day, t_flat)
        topo_all = wgs84.latlon(lat_rep, lon_rep)

        alt, _, _ = (earth + topo_all).at(t_micro_all).observe(sun).apparent().altaz()
        alts_2d = alt.degrees.reshape(M, n_window)

        is_above = alts_2d > -0.8333
        crossings = is_above[:, :-1] & ~is_above[:, 1:]
        has_cross = crossings.any(axis=1)
        first_cross = np.argmax(crossings, axis=1)

        rows = np.where(has_cross)[0]
        c = first_cross[rows]

        alt1 = alts_2d[rows, c]
        alt2 = alts_2d[rows, c + 1]
        t1 = t_window_2d[rows, c]
        t2 = t_window_2d[rows, c + 1]

        fraction = (-0.8333 - alt1) / (alt2 - alt1)
        precise_sunset_hours[idx[rows]] = t1 + fraction * (t2 - t1)

    final_valid_locs = np.where(~np.isnan(precise_sunset_hours))[0]

    progress_cb(f"3/4: Menghitung elongasi & tinggi hilal toposentris untuk {len(final_valid_locs)} titik (batch)...")

    elong_grid_1d = np.full(N, np.nan)
    alt_grid_1d = np.full(N, np.nan)
    geo_alt_grid_1d = np.full(N, np.nan)
    hours_utc_grid_1d = np.full(N, np.nan)

    if len(final_valid_locs) > 0:
        t_sunsets = ts.utc(tanggal.year, tanggal.month, tanggal.day,
                            precise_sunset_hours[final_valid_locs])

        geo_sun = earth.at(t_sunsets).observe(sun).apparent()
        geo_moon = earth.at(t_sunsets).observe(moon).apparent()
        elong_grid_1d[final_valid_locs] = geo_sun.separation_from(geo_moon).degrees

        topo_final = wgs84.latlon(lats[final_valid_locs], lons[final_valid_locs])
        # Refraksi atmosfer standar (10°C, 1010 mbar) diterapkan Skyfield ke
        # altitude toposentris Bulan -- lihat penjelasan lengkap di
        # hitung_grid()/_hitung_titik_flat() untuk alasan kenapa altaz()
        # Matahari di atas (variabel `alt`) sengaja dibiarkan tanpa refraksi.
        alt_moon, az_moon, d_moon = (earth + topo_final).at(t_sunsets).observe(moon).apparent().altaz(
            temperature_C=10.0, pressure_mbar=1010.0)
        alt_grid_1d[final_valid_locs] = alt_moon.degrees

        progress_cb("4/4: Menghitung tinggi hilal geosentris & jam UTC sunset (batch, tanpa loop)...")

        ra, dec_moon, _ = geo_moon.radec(epoch='date')
        gast = t_sunsets.gast

        lat_f = lats[final_valid_locs]
        lon_f = lons[final_valid_locs]

        lst = (gast + lon_f / 15.0) % 24.0
        H_deg = (lst - ra.hours) * 15.0

        lat_r = np.radians(lat_f)
        dec_r = dec_moon.radians
        H_r = np.radians(H_deg)

        geo_alt = np.degrees(np.arcsin(
            np.sin(lat_r) * np.sin(dec_r) + np.cos(lat_r) * np.cos(dec_r) * np.cos(H_r)
        ))
        geo_alt_grid_1d[final_valid_locs] = geo_alt

        hours_utc_grid_1d[final_valid_locs] = precise_sunset_hours[final_valid_locs]

    elong_grid = elong_grid_1d.reshape(len(lat_range), len(lon_range))
    alt_grid = alt_grid_1d.reshape(len(lat_range), len(lon_range))
    geo_alt_grid = geo_alt_grid_1d.reshape(len(lat_range), len(lon_range))
    hours_utc_grid = hours_utc_grid_1d.reshape(len(lat_range), len(lon_range))

    progress_cb("Selesai! Semua grid (MABIMS + Muhammadiyah) berhasil dihitung.")

    lon_mesh, lat_mesh = np.meshgrid(lon_range, lat_range)

    return {
        "elong_grid": elong_grid,
        "alt_grid": alt_grid,
        "geo_alt_grid": geo_alt_grid,
        "hours_utc_grid": hours_utc_grid,
        "lon_mesh": lon_mesh,
        "lat_mesh": lat_mesh,
    }


# =========================================================
#  GRID REGIONAL INDONESIA (untuk peta tinggi hilal & elongasi ala BMKG)
#
#  Grid global (default 4°) terlalu kasar untuk peta skala nasional --
#  kontur ketinggian hilal/elongasi di sekitar wilayah Indonesia jadi
#  terlihat patah-patah/kotak-kotak.
#
#  TAPI menghitung astronomi (skyfield/VSOP87) di TIAP titik grid halus
#  langsung juga boros -- resolusi halus di area kecil bisa menghasilkan
#  titik lebih banyak daripada grid global sekalipun cakupannya lebih
#  sempit (lihat catatan performa di bawah).
#
#  Solusinya: dua tahap, mirip cara "tebak kasar dulu baru diperhalus"
#  yang sudah dipakai di pencarian PKG 2 Amerika --
#    1) Hitung astronomi SUNGGUHAN (skyfield/VSOP87) hanya di grid KASAR
#       (INDONESIA_RESOLUSI_KASAR, mis. 1°) -- titiknya sedikit, jadi cepat.
#    2) INTERPOLASI bilinear (murni numpy, TANPA panggilan skyfield lagi
#       sama sekali) dari grid kasar itu ke grid HALUS
#       (INDONESIA_RESOLUSI_HALUS, mis. 0.15°) untuk digambar konturnya.
#
#  Tinggi hilal & elongasi berubah mulus terhadap posisi (bukan fungsi
#  yang bergejolak/chaotic), jadi interpolasi bilinear dari grid 1° sudah
#  lebih dari cukup akurat untuk keperluan VISUALISASI peta -- ini beda
#  dari pencarian zona PKG 2 Amerika (yang butuh nilai eksak per titik
#  untuk keputusan lolos/tidak, makanya di sana dihitung sungguhan, bukan
#  interpolasi).
# =========================================================

INDONESIA_LAT_RANGE = (-11.0, 6.0)
INDONESIA_LON_RANGE = (94.0, 142.0)

# Tahap 1: resolusi KASAR -- ini yang benar-benar dihitung ke skyfield/VSOP87.
INDONESIA_RESOLUSI_KASAR = 1.0
# Tahap 2: resolusi HALUS -- hasil interpolasi bilinear (nyaris gratis,
# murni numpy), dipakai untuk digambar kontur per-1-derajatnya.
INDONESIA_RESOLUSI_HALUS = 0.05

# Catatan performa (ilustrasi jumlah titik):
#   Grid global (skyfield)         : 46 x 91  =  4.186 titik
#   Indonesia kasar 1°  (skyfield) : 18 x 49  =    882 titik  <- yang dihitung skyfield
#   Indonesia halus 0.15° (interp) : 115 x 321 = 36.915 titik <- gratis (numpy saja)


def _interpolasi_bilinear_grid(lat_kasar, lon_kasar, nilai_kasar, lat_mesh_halus, lon_mesh_halus):
    """Interpolasi bilinear murni-numpy dari grid regular kasar (lat_kasar,
    lon_kasar naik urut, nilai_kasar shape (len(lat_kasar), len(lon_kasar)))
    ke titik-titik grid halus manapun (lat_mesh_halus/lon_mesh_halus, boleh
    meshgrid). TIDAK ada panggilan skyfield/VSOP87 di sini -- cuma aljabar
    array biasa, jadi jauh lebih cepat daripada menghitung ulang astronomi
    di tiap titik halus.

    NaN pada titik kasar (mis. tidak ada sunset) akan otomatis menyebar ke
    titik halus tetangganya lewat aritmetika NaN -- tidak dipaksakan jadi
    angka."""
    lon_idx = np.clip(np.searchsorted(lon_kasar, lon_mesh_halus, side="right") - 1,
                       0, len(lon_kasar) - 2)
    lat_idx = np.clip(np.searchsorted(lat_kasar, lat_mesh_halus, side="right") - 1,
                       0, len(lat_kasar) - 2)

    lon0, lon1 = lon_kasar[lon_idx], lon_kasar[lon_idx + 1]
    lat0, lat1 = lat_kasar[lat_idx], lat_kasar[lat_idx + 1]

    tx = (lon_mesh_halus - lon0) / (lon1 - lon0)
    ty = (lat_mesh_halus - lat0) / (lat1 - lat0)

    v00 = nilai_kasar[lat_idx, lon_idx]
    v01 = nilai_kasar[lat_idx, lon_idx + 1]
    v10 = nilai_kasar[lat_idx + 1, lon_idx]
    v11 = nilai_kasar[lat_idx + 1, lon_idx + 1]

    v0 = v00 * (1 - tx) + v01 * tx
    v1 = v10 * (1 - tx) + v11 * tx
    return v0 * (1 - ty) + v1 * ty


def hitung_grid_indonesia(tanggal, ts, eph, progress_cb=lambda msg: None, mode="jpl"):
    """Grid khusus wilayah Indonesia untuk peta tinggi hilal & elongasi
    bergaya BMKG (kontur per 1 derajat), dihitung DUA TAHAP:

      1) Astronomi sungguhan (skyfield/VSOP87, lewat hitung_grid() yang
         sama dipakai grid global) hanya pada grid KASAR
         (INDONESIA_RESOLUSI_KASAR derajat) -- titiknya sedikit, cepat.
      2) Interpolasi bilinear (numpy saja, lihat _interpolasi_bilinear_grid)
         dari grid kasar itu ke grid HALUS (INDONESIA_RESOLUSI_HALUS
         derajat) untuk digambar konturnya -- tanpa panggilan skyfield lagi.

    Hasil akhir (dict) formatnya identik dengan hitung_grid() biasa, jadi
    tetap kompatibel langsung dengan buat_figure_indonesia_*."""
    lat_kasar = np.arange(INDONESIA_LAT_RANGE[0],
                           INDONESIA_LAT_RANGE[1] + INDONESIA_RESOLUSI_KASAR / 2,
                           INDONESIA_RESOLUSI_KASAR)
    lon_kasar = np.arange(INDONESIA_LON_RANGE[0],
                           INDONESIA_LON_RANGE[1] + INDONESIA_RESOLUSI_KASAR / 2,
                           INDONESIA_RESOLUSI_KASAR)

    progress_cb(f"Indonesia tahap 1/2: menghitung grid kasar astronomi "
                f"({len(lat_kasar)}x{len(lon_kasar)} titik, {INDONESIA_RESOLUSI_KASAR}°)...")
    grid_kasar = hitung_grid(tanggal, ts, eph, progress_cb=lambda m: None,
                              lat_range=lat_kasar, lon_range=lon_kasar, mode=mode)

    lat_halus = np.arange(INDONESIA_LAT_RANGE[0],
                           INDONESIA_LAT_RANGE[1] + INDONESIA_RESOLUSI_HALUS / 2,
                           INDONESIA_RESOLUSI_HALUS)
    lon_halus = np.arange(INDONESIA_LON_RANGE[0],
                           INDONESIA_LON_RANGE[1] + INDONESIA_RESOLUSI_HALUS / 2,
                           INDONESIA_RESOLUSI_HALUS)
    lon_mesh_halus, lat_mesh_halus = np.meshgrid(lon_halus, lat_halus)

    progress_cb(f"Indonesia tahap 2/2: interpolasi bilinear ke grid halus "
                f"({len(lat_halus)}x{len(lon_halus)} titik, {INDONESIA_RESOLUSI_HALUS}°, "
                f"tanpa hitung ulang astronomi)...")

    def interp(nama_field):
        return _interpolasi_bilinear_grid(lat_kasar, lon_kasar, grid_kasar[nama_field],
                                           lat_mesh_halus, lon_mesh_halus)

    return {
        "elong_grid": interp("elong_grid"),
        "alt_grid": interp("alt_grid"),
        "geo_alt_grid": interp("geo_alt_grid"),
        "hours_utc_grid": interp("hours_utc_grid"),
        "lon_mesh": lon_mesh_halus,
        "lat_mesh": lat_mesh_halus,
    }


def _gambar_peta_dasar_indonesia(ax):
    """Elemen peta dasar (pantai, batas negara, gridlines) yang dipakai
    bersama oleh peta tinggi hilal & peta elongasi Indonesia."""
    ax.set_extent([INDONESIA_LON_RANGE[0], INDONESIA_LON_RANGE[1],
                   INDONESIA_LAT_RANGE[0], INDONESIA_LAT_RANGE[1]],
                  crs=ccrs.PlateCarree())
    # PENTING: cfeature.LAND/OCEAN/BORDERS bawaan cartopy TERNYATA (dikonfirmasi
    # via tracing langsung ke shapereader.natural_earth() saat savefig) memakai
    # AdaptiveScaler yang SAMA seperti cfeature.COASTLINE -- bukan skala tetap
    # 110m seperti yang terlihat dari repr default-nya. Untuk extent Indonesia
    # (~17-46 derajat), scaler ini otomatis naik ke 50m utk KETIGANYA (LAND,
    # OCEAN, BORDERS), bukan cuma garis pantai. .with_scale("110m") membuat
    # SALINAN dgn skala tetap (bukan adaptif) -- baris ini yang benar-benar
    # menghindari kebutuhan 2 set shapefile (110m+50m) utk peta ini.
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="lightgray")
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="lightblue")
    ax.add_feature(cfeature.BORDERS.with_scale("110m"), linewidth=0.5, edgecolor="dimgray")
    # resolution='110m' dipatok eksplisit (bukan biarkan default 'auto') --
    # DIKONFIRMASI lewat pengujian: extent Indonesia yang kecil membuat
    # AdaptiveScaler cartopy otomatis pilih '50m' utk coastline SAJA (LAND/
    # OCEAN di atas tetap 110m krn scale-nya memang dipatok tetap, bukan
    # auto), artinya cartopy butuh 2 set shapefile berbeda (110m+50m) utk
    # SATU peta ini. Dengan 110m eksplisit, cuma 1 set shapefile yang perlu
    # di-cache/bundle -- konsisten dgn LAND/OCEAN, dan cukup detail utk skala
    # peta regional Indonesia (beda beberapa piksel garis pantai tidak
    # kelihatan di skala ini).
    ax.coastlines(resolution="110m", linewidth=0.6)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False


def buat_figure_indonesia_tinggi_hilal(grids_id, tanggal):
    """Peta tinggi hilal toposentris khusus wilayah Indonesia, polos tanpa heatmap,
    dengan garis kontur di setiap 0.2 derajat — gaya peta kontur bersih —
    plus garis tebal untuk ambang MABIMS (3°)."""
    lon_mesh, lat_mesh = grids_id["lon_mesh"], grids_id["lat_mesh"]
    alt_grid = grids_id["alt_grid"]

    fig = plt.figure(figsize=(10, 7), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    _gambar_peta_dasar_indonesia(ax)

    valid = ~np.isnan(alt_grid)
    if np.any(valid):
        lo = np.floor(np.nanmin(alt_grid) / 0.2) * 0.2
        hi = np.ceil(np.nanmax(alt_grid) / 0.2) * 0.2
    else:
        lo, hi = -2.0, 10.0
    levels_02 = np.arange(lo, hi + 0.001, 0.2)

    cs = ax.contour(lon_mesh, lat_mesh, alt_grid, levels=levels_02,
                     colors="black", linewidths=0.5, transform=ccrs.PlateCarree())
    ax.clabel(cs, fmt="%.1f°", fontsize=7, inline=True)

    # Ambang kriteria MABIMS (tinggi hilal >=3°) ditebalkan supaya menonjol
    ax.contour(lon_mesh, lat_mesh, alt_grid, levels=[3.0], colors="blue",
               linewidths=2.2, transform=ccrs.PlateCarree())

    ax.set_title("Peta Tinggi Hilal Toposentris — Wilayah Indonesia\n"
                 f"{tanggal.strftime('%d %B %Y')}  (garis hitam: tiap 0.2°, garis biru tebal: 3° / MABIMS)",
                 fontsize=11, pad=12)
    return fig


def buat_figure_indonesia_elongasi(grids_id, tanggal):
    """Peta elongasi khusus wilayah Indonesia, polos tanpa heatmap,
    dengan garis kontur di setiap 0.2 derajat — gaya peta kontur bersih —
    plus garis tebal untuk ambang MABIMS (6.4°)."""
    lon_mesh, lat_mesh = grids_id["lon_mesh"], grids_id["lat_mesh"]
    elong_grid = grids_id["elong_grid"]

    fig = plt.figure(figsize=(10, 7), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    _gambar_peta_dasar_indonesia(ax)

    valid = ~np.isnan(elong_grid)
    if np.any(valid):
        lo = np.floor(np.nanmin(elong_grid) / 0.2) * 0.2
        hi = np.ceil(np.nanmax(elong_grid) / 0.2) * 0.2
    else:
        lo, hi = 0.0, 12.0
    levels_02 = np.arange(lo, hi + 0.001, 0.2)

    cs = ax.contour(lon_mesh, lat_mesh, elong_grid, levels=levels_02,
                     colors="black", linewidths=0.5, transform=ccrs.PlateCarree())
    ax.clabel(cs, fmt="%.1f°", fontsize=7, inline=True)

    # Ambang kriteria MABIMS (elongasi >=6.4°) ditebalkan
    ax.contour(lon_mesh, lat_mesh, elong_grid, levels=[6.4], colors="red",
               linewidths=2.2, transform=ccrs.PlateCarree())

    ax.set_title("Peta Elongasi — Wilayah Indonesia\n"
                 f"{tanggal.strftime('%d %B %Y')}  (garis hitam: tiap 0.2°, garis merah tebal: 6.4° / MABIMS)",
                 fontsize=11, pad=12)
    return fig


# =========================================================
#  PEMBUATAN FIGURE (dijalankan di thread utama / main thread)
# =========================================================

def buat_figure_mabims(grids, tanggal):
    lon_mesh, lat_mesh = grids["lon_mesh"], grids["lat_mesh"]
    elong_grid, alt_grid = grids["elong_grid"], grids["alt_grid"]

    fig = plt.figure(figsize=(13, 7.2), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    # LAND/OCEAN dipatok .with_scale("110m") jg di sini utk konsisten/robust --
    # extent dunia [-180,180,-90,90] saat ini memang sudah otomatis resolve ke
    # 110m lewat AdaptiveScaler bawaan cfeature.LAND/OCEAN, TAPI itu bergantung
    # implisit pada extent selalu dunia penuh; kalau nanti ada yg nambah
    # ax.set_extent() utk zoom di fungsi ini, adaptive scaler akan diam-diam
    # minta shapefile lebih detail (persis bug yg ditemukan di peta Indonesia).
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="lightgray")
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="lightblue")
    ax.coastlines(resolution="110m", linewidth=0.5)

    gl = ax.gridlines(draw_labels=True, linewidth=0.3, alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False

    mabims_zone = (elong_grid >= 6.4) & (alt_grid >= 3)

    ax.contourf(lon_mesh, lat_mesh, mabims_zone.astype(int),
                levels=[0.5, 1.5], colors=["green"], alpha=0.4,
                transform=ccrs.PlateCarree())
    ax.contour(lon_mesh, lat_mesh, alt_grid, levels=[3],
               colors="blue", linewidths=1.5, transform=ccrs.PlateCarree())
    ax.contour(lon_mesh, lat_mesh, elong_grid, levels=[6.4],
               colors="red", linewidths=1.5, transform=ccrs.PlateCarree())

    no_sunset = np.isnan(alt_grid)
    ax.contourf(lon_mesh, lat_mesh, no_sunset.astype(int),
                levels=[0.5, 1.5], colors=["dimgray"], alpha=0.3,
                transform=ccrs.PlateCarree())

    ax.set_title(f"Peta Kriteria MABIMS — Elongasi ≥6.4° & Tinggi Hilal ≥3° (Toposentris)\n"
                 f"{tanggal.strftime('%d %B %Y')}",
                 fontsize=12, pad=14)

    legend_elems_mabims = [
        plt.Line2D([0], [0], color="blue", lw=1.5, label="Tinggi hilal = 3°"),
        plt.Line2D([0], [0], color="red", lw=1.5, label="Elongasi = 6.4°"),
        plt.Rectangle((0, 0), 1, 1, fc="green", alpha=0.4, label="Zona memenuhi kriteria MABIMS"),
        plt.Rectangle((0, 0), 1, 1, fc="dimgray", alpha=0.3, label="Tidak ada sunset (siang/malam kutub)"),
    ]
    ax.legend(handles=legend_elems_mabims, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              borderaxespad=0, fontsize=9, framealpha=0.9)
    # fig.tight_layout() dihapus -- sudah digantikan constrained_layout=True
    # di plt.figure() saat pembuatan figure (hipotesa: tight_layout() memicu
    # render-pass tambahan yang lebih mahal saat berinteraksi dgn GeoAxes
    # cartopy dibanding constrained_layout yg terintegrasi ke layout engine).
    return fig


# =========================================================
#  PENCARIAN ZONA PKG 2 DI DARATAN AMERIKA (multi-tahap, adaptif)
#
#  Zona yang memenuhi elongasi>=8 & tinggi>=5 derajat secara bersamaan di
#  dekat batas kriteria sering kali SANGAT SEMPIT (dalam praktiknya bisa
#  hanya beberapa km, seperti kasus Bethel, Alaska pada 17 Feb 2026 dengan
#  elongasi 8°00'06"). Grid global 4 derajat (~440 km) bisa saja MELEWATI
#  zona sesempit itu meski garis kontur yang diinterpolasi terlihat
#  menyentuh daratan. Karena itu pencarian PKG 2 dilakukan bertahap:
#    Tahap 1 : grid 1 derajat di seluruh bounding box benua Amerika.
#    Tahap 2 : bila belum ada titik yang lolos, tapi ada titik yang
#              "dekat" ambang batas, perkecil area & perhalus grid ke
#              0.25 derajat di sekitar titik terdekat itu untuk verifikasi.
# =========================================================

AMERIKA_LAT_RANGE = (-58, 75)
AMERIKA_LON_RANGE = (-172, -30)

# =========================================================
#  ZONA "SEMPIT" -- dipakai supaya grid tahap 1 PKG 2 boleh dibikin lebih
#  KASAR secara umum (hemat komputasi), TAPI tetap dipertahankan halus di
#  pesisir yang menyempit tajam jadi genting/tanjung/kepulauan tipis --
#  tempat grid kasar berisiko "melompati" daratan sempit yang justru sering
#  jadi kandidat PKG 2 (lihat kasus Bethel, Alaska, elongasi 8°00'06" di
#  komentar atas).
#
#  Diturunkan dari analisis titik_terbarat_benua_amerika.json: untuk tiap
#  baris lintang (resolusi 1°), file itu mencatat bujur-barat-terjauh yang
#  masih daratan utama benua Amerika. Baris-baris di mana nilai itu
#  melompat tajam (>=2 derajat) dari baris sebelumnya menandai pesisir yang
#  tiba-tiba menyempit -- itulah 3 kelompok zona di bawah ini:
#    - Genting Amerika Tengah & Meksiko selatan: lompatan 2-5 derajat
#      berturut-turut dari Panama (7-8 LU) sampai leher Meksiko (22-23 LU).
#    - Pesisir fjord Chile selatan: lompatan di 54-55 LS.
#    - Pesisir British Columbia - Semenanjung Alaska - Kepulauan Aleutian:
#      lompatan sampai +30 derajat di 54-55 LU (semenanjung Alaska yang
#      menjorok jauh ke barat lewat leher yang sangat sempit).
# =========================================================
ZONA_SEMPIT_AMERIKA = [
    # (lat_min, lat_max, lon_min, lon_max, label)
    (5, 24, -113.0, -75.0, "Genting Amerika Tengah & Meksiko selatan"),
    (-58, -52, -77.0, -69.0, "Pesisir fjord Chile selatan"),
    (48, 61, -167.0, -122.0, "Pesisir BC - Semenanjung Alaska & Kep. Aleutian"),
]

RES_KASAR_PKG2 = 2.0          # resolusi default tahap 1 (sebelumnya 1.0 di semua tempat)
RES_HALUS_ZONA_SEMPIT_PKG2 = 1.0  # resolusi di dalam ZONA_SEMPIT_AMERIKA (dipertahankan spt semula)


def _bangun_sumbu_adaptif(lo, hi, res_kasar, interval_halus, res_halus):
    """Bangun 1 sumbu (lat ATAU lon) dari lo..hi: pakai res_kasar di
    sebagian besar rentang, tapi di dalam interval_halus (list of (a,b))
    pakai res_halus yang lebih rapat. Hasilnya array 1D biasa, urut, tanpa
    duplikat -- masih bisa dipakai np.meshgrid seperti biasa, hanya saja
    spasinya tidak seragam (renggang di area luas/laut lepas, rapat di
    pesisir sempit)."""
    titik = set(np.round(np.arange(lo, hi + res_kasar, res_kasar), 6).tolist())
    for a, b in interval_halus:
        a2, b2 = max(lo, a), min(hi, b)
        if a2 <= b2:
            titik.update(np.round(np.arange(a2, b2 + res_halus, res_halus), 6).tolist())
    return np.array(sorted(titik))


def _sumbu_kasar_pkg2_amerika():
    """lat_kasar & lon_kasar tahap 1 PKG 2: kasar (RES_KASAR_PKG2) secara
    umum, halus (RES_HALUS_ZONA_SEMPIT_PKG2) di dalam ZONA_SEMPIT_AMERIKA."""
    lat_halus = [(zmin, zmax) for (zmin, zmax, _, _, _) in ZONA_SEMPIT_AMERIKA]
    lon_halus = [(xmin, xmax) for (_, _, xmin, xmax, _) in ZONA_SEMPIT_AMERIKA]
    lat_kasar = _bangun_sumbu_adaptif(AMERIKA_LAT_RANGE[0], AMERIKA_LAT_RANGE[1],
                                       RES_KASAR_PKG2, lat_halus, RES_HALUS_ZONA_SEMPIT_PKG2)
    lon_kasar = _bangun_sumbu_adaptif(AMERIKA_LON_RANGE[0], AMERIKA_LON_RANGE[1],
                                       RES_KASAR_PKG2, lon_halus, RES_HALUS_ZONA_SEMPIT_PKG2)
    return lat_kasar, lon_kasar


def cari_zona_pkg2_amerika(tanggal, ts, eph, progress_cb=lambda msg: None, mode="jpl"):
    """
    Mengembalikan dict berisi lon_mesh/lat_mesh/zona (boolean) hasil pencarian
    ber-tahap kriteria PKG 2 (elongasi>=8 & tinggi hilal geosentris>=5) yang
    dibatasi pada daratan utama benua Amerika. Jika tidak ditemukan sama
    sekali, zona berisi semua False.
    """
    lat_kasar, lon_kasar = _sumbu_kasar_pkg2_amerika()
    lon_mesh1, lat_mesh1 = np.meshgrid(lon_kasar, lat_kasar)

    # Saring dulu titik mana yang benar-benar daratan (murah: cuma cek
    # poligon, TANPA panggilan Skyfield sama sekali) -- baru astronomi
    # dihitung untuk titik-titik itu saja. Bounding box Amerika sebagian
    # besar berisi lautan (Pasifik/Atlantik), jadi ini biasanya memangkas
    # jumlah titik yang perlu dihitung sampai tinggal sebagian kecil saja.
    mask_info = _muat_amerika_mask_npz()
    if mask_info.get("available"):
        progress_cb("PKG 2: memuat mask raster daratan Amerika (NPZ)...")
    else:
        progress_cb("PKG 2: memuat data peta daratan Amerika "
                    "(NPZ tidak ditemukan — jalankan generate_mask.py; "
                    "fallback WKT/shapefile, lambat di pemanggilan pertama)...")
    mask1 = buat_mask_mainland_amerika(lat_mesh1, lon_mesh1)
    n_total = mask1.size
    n_darat = int(mask1.sum())
    progress_cb(f"PKG 2 tahap 1/2: pemindaian grid adaptif "
                f"({RES_KASAR_PKG2:g}° umum, {RES_HALUS_ZONA_SEMPIT_PKG2:g}° di pesisir sempit) "
                f"di benua Amerika ({n_darat} titik daratan dari {n_total} titik kotak pembatas, "
                f"~{100 * n_darat / n_total:.0f}%)...")

    lats1_flat, lons1_flat = lat_mesh1.ravel(), lon_mesh1.ravel()
    sel = np.where(mask1.ravel())[0]
    elong_f, alt_f, geo_alt_f, hours_f = _hitung_titik_flat(
        tanggal, ts, eph, lats1_flat[sel], lons1_flat[sel], mode=mode)

    elong_1d = np.full(n_total, np.nan)
    geo_alt_1d = np.full(n_total, np.nan)
    alt_1d = np.full(n_total, np.nan)
    hours_1d = np.full(n_total, np.nan)
    elong_1d[sel], geo_alt_1d[sel] = elong_f, geo_alt_f
    alt_1d[sel], hours_1d[sel] = alt_f, hours_f

    grid1 = {
        "elong_grid": elong_1d.reshape(lat_mesh1.shape),
        "alt_grid": alt_1d.reshape(lat_mesh1.shape),
        "geo_alt_grid": geo_alt_1d.reshape(lat_mesh1.shape),
        "hours_utc_grid": hours_1d.reshape(lat_mesh1.shape),
        "lon_mesh": lon_mesh1,
        "lat_mesh": lat_mesh1,
    }

    zona1 = (grid1["elong_grid"] >= 8) & (grid1["geo_alt_grid"] >= 5) & mask1

    if np.any(zona1):
        return {"lon_mesh": grid1["lon_mesh"], "lat_mesh": grid1["lat_mesh"],
                "zona": zona1, "ditemukan": True, "tahap": 1}

    # Cari titik "hampir lolos" (dalam toleransi 1.5 derajat dari kedua ambang
    # batas) sebagai kandidat area untuk diperhalus.
    toleransi = 0.5
    dekat = mask1 & ~np.isnan(grid1["geo_alt_grid"]) & \
        (grid1["geo_alt_grid"] >= 5 - toleransi) & (grid1["elong_grid"] >= 8 - toleransi)

    if not np.any(dekat):
        return {"lon_mesh": grid1["lon_mesh"], "lat_mesh": grid1["lat_mesh"],
                "zona": zona1, "ditemukan": False, "tahap": 1}

    lat_c = grid1["lat_mesh"][dekat]
    lon_c = grid1["lon_mesh"][dekat]
    lat_min, lat_max = lat_c.min() - 1.5, lat_c.max() + 1.5
    lon_min, lon_max = lon_c.min() - 1.5, lon_c.max() + 1.5

    progress_cb(f"PKG 2 tahap 2/2: memperhalus grid (0.25°) di sekitar "
                f"{lat_c.mean():.1f}°, {lon_c.mean():.1f}° ...")
    lat_halus = np.arange(lat_min, lat_max + 0.25, 0.25)
    lon_halus = np.arange(lon_min, lon_max + 0.25, 0.25)
    grid2 = hitung_grid(tanggal, ts, eph, progress_cb=lambda m: None,
                         lat_range=lat_halus, lon_range=lon_halus, mode=mode)

    mask2 = buat_mask_mainland_amerika(grid2["lat_mesh"], grid2["lon_mesh"])
    zona2 = (grid2["elong_grid"] >= 8) & (grid2["geo_alt_grid"] >= 5) & mask2

    return {"lon_mesh": grid2["lon_mesh"], "lat_mesh": grid2["lat_mesh"],
            "zona": zona2, "ditemukan": bool(np.any(zona2)), "tahap": 2}


def _cek_pkg1_terpenuhi(grids):
    """Cek cepat (murni baca array, tanpa Skyfield) apakah kriteria PKG 1
    Muhammadiyah (elongasi>=8 & tinggi hilal geosentris>=5, sebelum pukul
    24.00 UTC) terpenuhi di manapun pada grid global. Dipisah dari
    evaluasi_pkg supaya bisa dipanggil duluan di _hitung_grid_thread untuk
    memutuskan apakah hasil spekulatif PKG 2 Amerika perlu ditunggu."""
    elong_grid, geo_alt_grid, hours_utc_grid = (
        grids["elong_grid"], grids["geo_alt_grid"], grids["hours_utc_grid"]
    )
    cutoff_mask = (hours_utc_grid >= 0) & (hours_utc_grid <= 24)
    muhammadiyah_zone = (elong_grid >= 8) & (geo_alt_grid >= 5)
    return bool(np.any(np.where(cutoff_mask, muhammadiyah_zone, False)))


def evaluasi_pkg(grids, tanggal, waktu_ijtimak=None, ts=None, eph=None,
                  progress_cb=lambda msg: None, mode="jpl", pkg2_precomputed=None):
    """
    Evaluasi murni (tanpa plotting) status PKG 1 & PKG 2 Muhammadiyah untuk
    'tanggal'. Aman dipanggil dari background thread karena tidak menyentuh
    matplotlib sama sekali -- cocok dipakai sebelum figure dibuat di thread
    utama, supaya pencarian PKG 2 (yang berat) tidak membekukan GUI.
    mode='ringan' -> tidak butuh ts/eph sama sekali (VSOP87+ELP2000).

    pkg2_precomputed: dict opsional {"hasil_pkg2":..., "waktu_fajar_nz":...}
    berisi hasil pencarian PKG 2 Amerika & fajar NZ yang SUDAH dihitung
    duluan secara SPEKULATIF/PARALEL (lihat _hitung_grid_thread) -- biasanya
    dijalankan di thread lain BERSAMAAN dengan grid global, sebelum tahu
    pasti apakah PKG 1 lolos atau tidak. Kalau diisi, evaluasi_pkg TIDAK
    menghitung ulang cari_zona_pkg2_amerika/hitung_fajar_nz dari nol
    (yang paling berat: pemindaian daratan benua Amerika) -- tinggal pakai
    hasil yang sudah siap.
    """
    elong_grid, geo_alt_grid, hours_utc_grid = (
        grids["elong_grid"], grids["geo_alt_grid"], grids["hours_utc_grid"]
    )

    muhammadiyah_zone = (elong_grid >= 8) & (geo_alt_grid >= 5)

    # ---- PKG 1: kriteria terpenuhi di manapun sebelum pukul 24.00 UTC ----
    cutoff_mask = (hours_utc_grid >= 0) & (hours_utc_grid <= 24)
    zona_pkg1 = np.where(cutoff_mask, muhammadiyah_zone, False)
    no_sunset_masked = np.where(cutoff_mask, np.isnan(geo_alt_grid), False)
    pkg1_terpenuhi = bool(np.any(zona_pkg1))

    # ---- PKG 2: fallback, hanya dievaluasi jika PKG 1 tidak terpenuhi ----
    pkg2_terpenuhi = False
    pkg2_ijtimak_ok = None
    waktu_fajar_nz = None
    hasil_pkg2 = None  # dict dari cari_zona_pkg2_amerika (grid halus, hanya terisi jika PKG1 gagal)

    bisa_hitung = (mode == "ringan") or (ts is not None and eph is not None)

    if not pkg1_terpenuhi:
        if pkg2_precomputed is not None:
            # --- Hasil spekulatif sudah tersedia (dihitung paralel dengan
            #     grid global) -- tinggal dipakai, tidak perlu hitung ulang. ---
            progress_cb("PKG 1 tidak terpenuhi -- memakai hasil PKG 2 Amerika "
                        "yang sudah dihitung paralel sebelumnya.")
            hasil_pkg2 = pkg2_precomputed.get("hasil_pkg2")
            waktu_fajar_nz = pkg2_precomputed.get("waktu_fajar_nz")
            if waktu_ijtimak is not None and waktu_fajar_nz is not None:
                pkg2_ijtimak_ok = _ke_naif(waktu_ijtimak) < _ke_naif(waktu_fajar_nz)
            pkg2_amerika_ok = bool(hasil_pkg2["ditemukan"]) if hasil_pkg2 is not None else False
            pkg2_terpenuhi = bool(pkg2_ijtimak_ok) and pkg2_amerika_ok
        else:
            # --- Jalur lama (dipanggil sendirian, tanpa spekulasi paralel):
            #     (a) fajar NZ dan (b) pemindaian Amerika independen satu
            #     sama lain, jadi tetap dijalankan paralel di sini juga. ---
            hasil_ab = {}

            def _hitung_a():
                if waktu_ijtimak is not None and bisa_hitung:
                    progress_cb("Memeriksa syarat PKG 2: fajar di Selandia Baru...")
                    hasil_ab["waktu_fajar_nz"] = hitung_fajar_nz(
                        tanggal + timedelta(days=1), ts, eph, mode=mode)

            def _hitung_b():
                if bisa_hitung:
                    hasil_ab["hasil_pkg2"] = cari_zona_pkg2_amerika(
                        tanggal, ts, eph, progress_cb=progress_cb, mode=mode)

            thread_a = threading.Thread(target=_hitung_a, daemon=True)
            thread_a.start()
            _hitung_b()
            thread_a.join()

            waktu_fajar_nz = hasil_ab.get("waktu_fajar_nz")
            if waktu_ijtimak is not None and waktu_fajar_nz is not None:
                pkg2_ijtimak_ok = _ke_naif(waktu_ijtimak) < _ke_naif(waktu_fajar_nz)

            hasil_pkg2 = hasil_ab.get("hasil_pkg2")
            pkg2_amerika_ok = bool(hasil_pkg2["ditemukan"]) if hasil_pkg2 is not None else False

            pkg2_terpenuhi = bool(pkg2_ijtimak_ok) and pkg2_amerika_ok

    return {
        "zona_pkg1": zona_pkg1,
        "no_sunset_masked": no_sunset_masked,
        "pkg1_terpenuhi": pkg1_terpenuhi,
        "pkg2_terpenuhi": pkg2_terpenuhi,
        "pkg2_ijtimak_ok": pkg2_ijtimak_ok,
        "waktu_fajar_nz": waktu_fajar_nz,
        "hasil_pkg2": hasil_pkg2,
        "waktu_ijtimak": waktu_ijtimak,
    }


def buat_figure_muhammadiyah(grids, tanggal, evaluasi):
    lon_mesh, lat_mesh = grids["lon_mesh"], grids["lat_mesh"]
    elong_grid, geo_alt_grid, hours_utc_grid = (
        grids["elong_grid"], grids["geo_alt_grid"], grids["hours_utc_grid"]
    )

    zona_pkg1 = evaluasi["zona_pkg1"]
    no_sunset_masked = evaluasi["no_sunset_masked"]
    pkg1_terpenuhi = evaluasi["pkg1_terpenuhi"]
    pkg2_terpenuhi = evaluasi["pkg2_terpenuhi"]
    pkg2_ijtimak_ok = evaluasi["pkg2_ijtimak_ok"]
    waktu_fajar_nz = evaluasi["waktu_fajar_nz"]
    hasil_pkg2 = evaluasi["hasil_pkg2"]
    waktu_ijtimak = evaluasi["waktu_ijtimak"]

    fig = plt.figure(figsize=(13, 7.2), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    # LAND/OCEAN dipatok .with_scale("110m") jg di sini utk konsisten/robust --
    # extent dunia [-180,180,-90,90] saat ini memang sudah otomatis resolve ke
    # 110m lewat AdaptiveScaler bawaan cfeature.LAND/OCEAN, TAPI itu bergantung
    # implisit pada extent selalu dunia penuh; kalau nanti ada yg nambah
    # ax.set_extent() utk zoom di fungsi ini, adaptive scaler akan diam-diam
    # minta shapefile lebih detail (persis bug yg ditemukan di peta Indonesia).
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="lightgray")
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="lightblue")
    ax.coastlines(resolution="110m", linewidth=0.5)

    gl = ax.gridlines(draw_labels=True, linewidth=0.3, alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False

    if pkg1_terpenuhi:
        warna_zona, label_zona = "orange", "Zona memenuhi kriteria (PKG 1 & PKG 2 terpenuhi)"
        if np.any(zona_pkg1):
            ax.contourf(lon_mesh, lat_mesh, zona_pkg1.astype(int),
                        levels=[0.5, 1.5], colors=[warna_zona], alpha=0.45,
                        transform=ccrs.PlateCarree())
    elif pkg2_terpenuhi:
        warna_zona, label_zona = "gold", "Zona memenuhi PKG 2 (fallback, daratan utama Amerika)"
        zona2 = hasil_pkg2["zona"]
        lon2, lat2 = hasil_pkg2["lon_mesh"], hasil_pkg2["lat_mesh"]
        ax.contourf(lon2, lat2, zona2.astype(int),
                    levels=[0.5, 1.5], colors=[warna_zona], alpha=0.6,
                    transform=ccrs.PlateCarree())
        # Zonanya bisa sangat sempit (dekat batas ambang), jadi ditandai juga
        # dengan penanda titik supaya tetap terlihat di peta skala dunia.
        lat_c = lat2[zona2].mean()
        lon_c = lon2[zona2].mean()
        ax.plot(lon_c, lat_c, marker="*", color="darkorange", markersize=14,
                markeredgecolor="black", transform=ccrs.PlateCarree(), zorder=5)
    else:
        warna_zona, label_zona = "orange", "Zona memenuhi kriteria Muhammadiyah"

    ax.contour(lon_mesh, lat_mesh, geo_alt_grid, levels=[5],
               colors="blue", linewidths=1.5, transform=ccrs.PlateCarree())
    ax.contour(lon_mesh, lat_mesh, elong_grid, levels=[8],
               colors="red", linewidths=1.5, transform=ccrs.PlateCarree())
    ax.contourf(lon_mesh, lat_mesh, no_sunset_masked.astype(int),
                levels=[0.5, 1.5], colors=["dimgray"], alpha=0.3,
                transform=ccrs.PlateCarree())
    ax.contour(lon_mesh, lat_mesh, hours_utc_grid, levels=[0, 24],
               colors="black", linewidths=1.8, linestyles="dashed",
               transform=ccrs.PlateCarree())

    if pkg1_terpenuhi:
        status_teks = "PKG 1 & PKG 2 terpenuhi"
    elif pkg2_terpenuhi:
        status_teks = "PKG 1 tidak terpenuhi — fallback ke PKG 2: terpenuhi"
    else:
        status_teks = "PKG 1 & PKG 2 tidak terpenuhi"

    ax.set_title(f"Peta Kriteria Muhammadiyah — Elongasi ≥8° & Tinggi Hilal ≥5° (Geosentris)\n"
                 f"{tanggal.strftime('%d %B %Y')}  —  {status_teks}",
                 fontsize=12, pad=14)

    legend_elems_muh = [
        plt.Line2D([0], [0], color="blue", lw=1.5, label="Tinggi hilal geosentris = 5°"),
        plt.Line2D([0], [0], color="red", lw=1.5, label="Elongasi geosentris = 8°"),
        plt.Line2D([0], [0], color="black", lw=1.8, linestyle="dashed",
                   label="Batas cutoff (sunset di luar rentang UTC 0-24\ntanggal target) — batas PKG 1"),
        plt.Rectangle((0, 0), 1, 1, fc=warna_zona, alpha=0.45, label=label_zona),
        plt.Rectangle((0, 0), 1, 1, fc="dimgray", alpha=0.3, label="Tidak ada sunset"),
    ]
    ax.legend(handles=legend_elems_muh, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              borderaxespad=0, fontsize=9, framealpha=0.9)

    # --- Catatan status PKG 2 (hanya ditampilkan kalau PKG 1 tidak terpenuhi) ---
    if not pkg1_terpenuhi:
        baris = []
        if waktu_fajar_nz is not None:
            cek = "OK" if pkg2_ijtimak_ok else "TIDAK terpenuhi"
            baris.append(f"Ijtimak {waktu_ijtimak.strftime('%d %b %Y %H:%M')} UTC vs "
                         f"fajar NZ {waktu_fajar_nz.strftime('%d %b %Y %H:%M')} UTC -> {cek}")
        else:
            baris.append("Waktu fajar NZ tidak dapat dihitung.")
        if hasil_pkg2 is not None:
            ket = "terpenuhi" if hasil_pkg2["ditemukan"] else "TIDAK terpenuhi"
            tahap = hasil_pkg2["tahap"]
            baris.append(f"Kriteria 5°/8° di daratan utama Amerika: {ket} "
                         f"(pencarian tahap {tahap}, resolusi "
                         f"{f'adaptif {RES_KASAR_PKG2:g}°/{RES_HALUS_ZONA_SEMPIT_PKG2:g}°' if tahap == 1 else '0.25°'})")
        else:
            baris.append("Kriteria 5°/8° di daratan utama Amerika: tidak dapat diperiksa.")
        catatan = "Cek syarat PKG 2:\n" + "\n".join(baris)
        fig.text(0.01, 0.01, catatan, fontsize=8, va="bottom", ha="left",
                  color="dimgray", wrap=True)

    # fig.tight_layout() dihapus -- sudah digantikan constrained_layout=True
    # di plt.figure() saat pembuatan figure (hipotesa: tight_layout() memicu
    # render-pass tambahan yang lebih mahal saat berinteraksi dgn GeoAxes
    # cartopy dibanding constrained_layout yg terintegrasi ke layout engine).
    return fig

# =========================================================
#  PERBANDINGAN KALENDER MABIMS vs KHGT MUHAMMADIYAH
#  Menyambung dari beri_label_hijriyah(): tiap ijtimak asli yg sudah punya
#  label (tahun H, bulan H) dipakai sbg ANCHOR utk mengevaluasi kedua
#  kriteria (MABIMS lokal Indonesia & KHGT/Muhammadiyah global) di malam
#  yg sama, lalu dibandingkan tanggal Masehi awal bulannya.
# =========================================================

# =========================================================
#  TITIK SAMPLING MABIMS -- pesisir barat Sumatra & pesisir selatan
#  Jawa bagian barat.
#
#  _cek_mabims_terpenuhi() dulu memindai grid PENUH seluruh Indonesia
#  (hitung_grid_indonesia: 18x49 = 882 titik astronomi sungguhan lalu
#  diinterpolasi ke 115x321 = 36.915 titik) HANYA untuk menjawab
#  pertanyaan boolean "ada/tidak ada SATU titik di Indonesia yang lolos
#  kriteria MABIMS" -- brutal & mubazir utk kebutuhan sesederhana itu,
#  apalagi dipanggil berulang (tiap bulan x sampai beberapa hari
#  tambahan) oleh bandingkan_kalender_mabims_khgt().
#
#  Padahal secara astronomis, di Indonesia hilal tertinggi pada malam
#  manapun praktis SELALU muncul di titik-titik paling barat & lintang
#  ekstrem: matahari terbenam paling akhir (dlm skala jam UTC) di
#  sanalah, jadi Bulan (yang terbenam belakangan lagi) sudah paling
#  tinggi/paling jauh elongasinya saat matahari tepat terbenam. Titik itu
#  ada di dua jalur: (1) pesisir barat Sumatra, dari ujung utara di Pulau
#  Weh/Sabang turun ke selatan, dan (2) pesisir selatan Jawa bagian
#  barat, dari Ujung Kulon ke timur sampai Pangandaran.
#
#  Jadi cukup dicek di titik-titik pesisir ini saja (belasan titik,
#  bukan ribuan) -- jauh lebih cepat, kesimpulan lolos/tidaknya kriteria
#  praktis tidak berubah. Koordinat berikut estimasi kota/desa pesisir
#  (bukan hasil geocoding presisi tinggi) -- cukup akurat utk hisab,
#  toleransi beberapa km tidak mengubah kesimpulan lolos/tidak.
# =========================================================
TITIK_SAMPLING_MABIMS = [
    # --- Ujung utara & pesisir barat Sumatra (Aceh) ---
    ("Sabang (kota)",                    5.8942,  95.3192),
    ("Meulingge / Tugu KM 0 Sabang",     5.9010,  95.2160),
    ("Banda Aceh",                       5.5500,  95.3175),
    ("Meulaboh",                         4.1330,  96.1170),
    ("Tapaktuan",                        3.2667,  97.1833),
    # --- Sampling turun ke selatan sepanjang pesisir barat Sumatra ---
    ("Sinabang (P. Simeulue)",           2.4800,  96.3800),
    ("Sibolga",                          1.7500,  98.7833),
    ("Painan",                          -1.3500, 100.5833),
    ("Bengkulu",                        -3.8000, 102.2667),
    ("Krui (Pesisir Barat, Lampung)",   -5.1700, 103.9300),
    ("Bakauheni / ujung selatan Sumatra", -5.8700, 105.7500),
    # --- Pesisir selatan Jawa bagian barat ---
    ("Ujung Kulon",                     -6.7597, 105.2100),
    ("Pelabuhan Ratu",                  -7.0167, 106.0500),
    ("Pangandaran",                     -7.6667, 108.6000),
]

_TITIK_SAMPLING_MABIMS_LAT = np.array([t[1] for t in TITIK_SAMPLING_MABIMS])
_TITIK_SAMPLING_MABIMS_LON = np.array([t[2] for t in TITIK_SAMPLING_MABIMS])


def _cek_mabims_terpenuhi(tanggal, ts, eph, mode="ringan"):
    """Cek kriteria MABIMS Baru (tinggi hilal toposentris >=3 derajat DAN
    elongasi >=6.4 derajat) terpenuhi di WILAYAH INDONESIA pada malam
    'tanggal'. True kalau ADA SETIDAKNYA SATU titik di antara
    TITIK_SAMPLING_MABIMS yg memenuhi -- asumsi praktis: kriteria
    terpenuhi di MANA SAJA di Indonesia dianggap terpenuhi scr nasional
    (sidang isbat), sama seperti cari_zona_pkg2_amerika yg juga cukup
    'ada satu titik' utk KHGT. Kalau ternyata Kemenag pakai titik acuan
    spesifik (bukan 'ada satu titik di mana saja'), sesuaikan fungsi ini.

    Dihitung lewat _hitung_titik_flat() (versi 'scattered-point', sama
    yg dipakai PKG 2 Amerika utk titik-titik daratan hasil saringan
    mask) -- HANYA di titik-titik pesisir barat Sumatra/selatan Jawa
    (lihat komentar TITIK_SAMPLING_MABIMS di atas), bukan grid penuh."""
    elong, alt, _, _ = _hitung_titik_flat(
        tanggal, ts, eph, _TITIK_SAMPLING_MABIMS_LAT, _TITIK_SAMPLING_MABIMS_LON, mode=mode)
    terpenuhi = (alt >= 3.0) & (elong >= 6.4)
    return bool(np.any(terpenuhi))


def _tentukan_awal_bulan(tanggal_ijtimak, waktu_ijtimak, kriteria, ts, eph,
                          mode="ringan", maks_hari_tambahan=3):
    """Tentukan tanggal Masehi (datetime, tengah malam) MULAI bulan baru
    menurut 'kriteria' ('mabims' atau 'khgt'), dari tanggal_ijtimak
    (datetime tengah malam hari ijtimak). Cek kriteria di malam
    tanggal_ijtimak; kalau terpenuhi, bulan mulai malam itu; kalau tidak,
    coba malam2 berikutnya (jaga2, meski praktiknya nyaris selalu cukup
    di hari ijtimak atau H+1).

    waktu_ijtimak (datetime presisi, bukan cuma tengah malam) tetap dipakai
    APA ADANYA utk kriteria 'khgt' (dibutuhkan evaluasi_pkg utk cek PKG 2 --
    ijtimak sebelum fajar NZ) -- TIDAK ikut bergeser meski tanggal_cek maju
    beberapa hari, krn waktu ijtimak asli ya cuma satu, tidak berubah.
    """
    for tambahan in range(maks_hari_tambahan + 1):
        tanggal_cek = tanggal_ijtimak + timedelta(days=tambahan)
        if kriteria == "mabims":
            terpenuhi = _cek_mabims_terpenuhi(tanggal_cek, ts, eph, mode=mode)
        else:
            grids = hitung_grid(tanggal_cek, ts, eph, mode=mode)
            evaluasi = evaluasi_pkg(grids, tanggal_cek, waktu_ijtimak=waktu_ijtimak,
                                     ts=ts, eph=eph, mode=mode)
            terpenuhi = evaluasi["pkg1_terpenuhi"] or evaluasi["pkg2_terpenuhi"]
        if terpenuhi:
            # Kriteria terpenuhi pada MALAM tanggal_cek (maghrib tanggal_cek
            # s.d. dini hari berikutnya). Awal bulan Hijriyah dimulai sejak
            # maghrib itu, dan tanggal Masehi yg lazim diumumkan sbg "1
            # [bulan]" adalah hari SETELAHNYA (mis. hilal terlihat malam 26,
            # maka "besok tanggal 27 adalah 1 Muharram") -- bukan tanggal_cek
            # itu sendiri. Tanpa +1 ini, semua tanggal keluar mundur 1 hari.
            return tanggal_cek + timedelta(days=1)
    return None   # jaga2 -- semestinya tidak pernah kejadian dlm praktik


def bandingkan_kalender_mabims_khgt(tahun_h, ts=None, eph=None, mode="ringan",
                                     progress_cb=lambda msg: None):
    """Bangun tabel perbandingan AWAL BULAN Hijriyah versi MABIMS vs KHGT
    Muhammadiyah, utk SATU tahun Hijriyah (tahun_h), dari ijtimak ASLI
    (astronomis) yg dilabeli otomatis lewat beri_label_hijriyah() (hisab
    urfi cuma dipakai sbg penunjuk arah label bulan, BUKAN sbg sumber
    tanggal -- tanggal MABIMS/KHGT selalu dari astronomi sungguhan).

    mode='ringan' -> tidak butuh ts/eph (VSOP87+ELP2000, dipakai default).
    mode='jpl' -> presisi tinggi, WAJIB isi ts & eph.

    Return: list of dict (bulan 1..12), keys:
      'bulan_h', 'nama_bulan_h', 'waktu_ijtimak',
      'tanggal_mabims', 'tanggal_khgt' (datetime tengah malam, awal bulan
      versi masing2 kriteria), 'beda' (bool, True kalau kedua tanggal
      beda -- potensi beda hari raya/awal bulan antar kriteria).
    """
    # Cari rentang tahun Masehi yg mencakup tahun_h penuh (1 Hijriyah tahun
    # selalu overlap 1-2 tahun Masehi, kadang nyerempet perbatasan) --
    # perkiraan kasar (622 + tahun_h*0.970229) lalu diperlebar +-1 tahun
    # Masehi sbg margin aman.
    tahun_m_perkiraan = int(622 + tahun_h * 0.970229)
    ijtimak_semua = []
    for tahun_m in range(tahun_m_perkiraan - 1, tahun_m_perkiraan + 2):
        ijtimak_semua.extend(cari_ijtimak_tahun_ringan(tahun_m))
    ijtimak_semua.sort()

    label_semua = beri_label_hijriyah(ijtimak_semua)
    label_tahun_ini = [l for l in label_semua if l["tahun_h"] == tahun_h]
    label_tahun_ini.sort(key=lambda l: l["bulan_h"])

    hasil = []
    for l in label_tahun_ini:
        waktu_ijtimak = l["waktu_ijtimak"]
        tanggal_ijtimak = datetime(waktu_ijtimak.year, waktu_ijtimak.month, waktu_ijtimak.day)

        progress_cb(f"Menghitung {l['nama_bulan_h']} {tahun_h} H "
                    f"(ijtimak {waktu_ijtimak.strftime('%d %b %Y')})...")

        tanggal_mabims = _tentukan_awal_bulan(tanggal_ijtimak, waktu_ijtimak, "mabims",
                                               ts, eph, mode=mode)
        tanggal_khgt = _tentukan_awal_bulan(tanggal_ijtimak, waktu_ijtimak, "khgt",
                                             ts, eph, mode=mode)

        hasil.append({
            "bulan_h": l["bulan_h"], "nama_bulan_h": l["nama_bulan_h"],
            "waktu_ijtimak": waktu_ijtimak,
            "tanggal_mabims": tanggal_mabims, "tanggal_khgt": tanggal_khgt,
            "beda": (tanggal_mabims != tanggal_khgt),
        })

    return hasil


def _cari_ijtimak_sekitar_tanggal(tanggal_masehi):
    """Kumpulkan & urutkan semua ijtimak (ringan) dari tahun Masehi
    tanggal_masehi -1/+0/+1 -- cukup sbg 'kolam pencarian' utk dapat ijtimak
    tepat sebelum & sesudah tanggal_masehi (margin 1 tahun krn kalender
    Hijriyah bergeser ~11 hari/tahun Masehi, jadi ijtimak yg relevan hampir
    selalu ada di tahun Masehi yg sama, tapi kadang nyerempet ke tahun
    sebelah -- terutama dekat 1 Januari)."""
    ijtimak_semua = []
    for tahun_m in (tanggal_masehi.year - 1, tanggal_masehi.year, tanggal_masehi.year + 1):
        ijtimak_semua.extend(cari_ijtimak_tahun_ringan(tahun_m))
    ijtimak_semua.sort()
    return ijtimak_semua


def masehi_ke_hijriyah_kriteria(tahun, bulan, hari, kriteria, ts, eph, mode="ringan",
                                 progress_cb=lambda msg: None):
    """Konversi tanggal Masehi -> Hijriyah menurut kriteria astronomis ASLI
    ('mabims' atau 'khgt'), BUKAN kalender urfi/tabular. TIDAK menghitung
    tabel setahun penuh -- cukup cari IJTIMAK YANG BARU SAJA LEWAT sebelum
    tanggal target sbg titik acuan, panggil _tentukan_awal_bulan() (fungsi
    asli, TIDAK diubah) sekali utk dapat awal bulan itu, dan sekali lagi
    utk ijtimak berikutnya sbg batas atas -- kalau tanggal target ada di
    antara keduanya, itu bulannya. Kalau ternyata di luar rentang itu
    (kasus tepi, jarang), coba geser mundur/maju satu ijtimak.

    Return dict {'tahun_h', 'bulan_h', 'nama_bulan_h', 'hari_h',
    'tanggal_awal_bulan'}. Raises ValueError kalau tak ditemukan."""
    target = datetime(tahun, bulan, hari)
    ijtimak_semua = _cari_ijtimak_sekitar_tanggal(target)

    # index ijtimak TERAKHIR yg tanggalnya (hari kalender) sudah <= target --
    # itulah ijtimak acuan yg "baru saja lewat".
    idx_acuan = None
    for i, waktu_ij in enumerate(ijtimak_semua):
        if datetime(waktu_ij.year, waktu_ij.month, waktu_ij.day) <= target:
            idx_acuan = i
        else:
            break
    if idx_acuan is None:
        raise ValueError("Tidak ada ijtimak yang ditemukan sebelum tanggal ini.")

    def _awal_bulan(i):
        waktu_ij = ijtimak_semua[i]
        tanggal_ij = datetime(waktu_ij.year, waktu_ij.month, waktu_ij.day)
        return _tentukan_awal_bulan(tanggal_ij, waktu_ij, kriteria, ts, eph, mode=mode)

    # Coba idx_acuan dulu (kasus normal), lalu geser mundur/maju 1 ijtimak
    # sbg jaga2 kasus tepi (mis. kriteria baru terpenuhi H+1/H+2 setelah
    # ijtimak, jadi awal bulan versi kriteria bisa "menjorok" lewat batas
    # tanggal ijtimak berikutnya).
    for i in (idx_acuan, idx_acuan - 1, idx_acuan + 1):
        if not (0 <= i < len(ijtimak_semua)):
            continue
        awal = _awal_bulan(i)
        if awal is None:
            continue
        akhir = _awal_bulan(i + 1) if i + 1 < len(ijtimak_semua) else None
        if awal <= target and (akhir is None or target < akhir):
            waktu_ij = ijtimak_semua[i]
            jd_ij = julian_day(waktu_ij.year, waktu_ij.month,
                                waktu_ij.day + (waktu_ij.hour + waktu_ij.minute / 60.0
                                                 + waktu_ij.second / 3600.0) / 24.0)
            jd_ij = float(np.asarray(jd_ij).reshape(()))
            tahun_h, bulan_h = _cari_label_hijriyah_urfi(jd_ij)
            return {"tahun_h": tahun_h, "bulan_h": bulan_h,
                    "nama_bulan_h": _NAMA_BULAN_HIJRIYAH[bulan_h - 1],
                    "hari_h": (target - awal).days + 1, "tanggal_awal_bulan": awal}

    raise ValueError("Tidak ditemukan bulan Hijriyah yang mengandung tanggal ini "
                      "menurut kriteria terpilih.")


def hijriyah_kriteria_ke_masehi(tahun_h, bulan_h, hari_h, kriteria, ts, eph, mode="ringan",
                                 progress_cb=lambda msg: None):
    """Kebalikan dari masehi_ke_hijriyah_kriteria(): tanggal Hijriyah (tahun_h,
    bulan_h, hari_h) menurut kriteria astronomis ASLI ('mabims'/'khgt') ->
    tanggal Masehi. Sama seperti versi m2h, TIDAK menghitung tabel setahun
    penuh -- cukup cari SATU ijtimak yg berlabel (tahun_h, bulan_h) sbg
    acuan (dgn beri_label_hijriyah(), fungsi asli), lalu _tentukan_awal_bulan()
    sekali utk ijtimak itu & sekali utk ijtimak berikutnya (batas atas/
    panjang bulan). Return (tahun, bulan, hari). Raises ValueError kalau
    bulan tsb tak ditemukan atau hari_h melebihi panjang bulan sesungguhnya."""
    # Perkiraan kasar (urfi) tanggal Masehi bulan ini, cuma dipakai utk
    # menentukan tahun Masehi mana yg perlu dicari ijtimak-nya -- BUKAN
    # sumber tanggal akhir.
    tahun_m_kasar, bulan_m_kasar, hari_m_kasar = hijriyah_urfi_ke_masehi(tahun_h, bulan_h, 1)
    ijtimak_semua = _cari_ijtimak_sekitar_tanggal(datetime(tahun_m_kasar, bulan_m_kasar, hari_m_kasar))

    label_semua = beri_label_hijriyah(ijtimak_semua)
    idx = next((i for i, l in enumerate(label_semua)
                if l["tahun_h"] == tahun_h and l["bulan_h"] == bulan_h), None)
    if idx is None:
        raise ValueError(f"Bulan ke-{bulan_h} tahun {tahun_h} H tidak ditemukan "
                          "menurut kriteria terpilih.")

    def _awal_bulan(i):
        waktu_ij = label_semua[i]["waktu_ijtimak"]
        tanggal_ij = datetime(waktu_ij.year, waktu_ij.month, waktu_ij.day)
        return _tentukan_awal_bulan(tanggal_ij, waktu_ij, kriteria, ts, eph, mode=mode)

    awal = _awal_bulan(idx)
    if awal is None:
        raise ValueError("Kriteria tidak pernah terpenuhi setelah ijtimak bulan ini "
                          "(seharusnya tidak terjadi dlm praktik).")
    akhir = _awal_bulan(idx + 1) if idx + 1 < len(label_semua) else None
    panjang_bulan = (akhir - awal).days if akhir is not None else 30
    if not (1 <= hari_h <= panjang_bulan):
        raise ValueError(f"Bulan ini cuma {panjang_bulan} hari menurut kriteria terpilih.")

    tanggal_masehi = awal + timedelta(days=hari_h - 1)
    return tanggal_masehi.year, tanggal_masehi.month, tanggal_masehi.day



    lon_mesh, lat_mesh = grids["lon_mesh"], grids["lat_mesh"]
    elong_grid, geo_alt_grid, hours_utc_grid = (
        grids["elong_grid"], grids["geo_alt_grid"], grids["hours_utc_grid"]
    )

    zona_pkg1 = evaluasi["zona_pkg1"]
    no_sunset_masked = evaluasi["no_sunset_masked"]
    pkg1_terpenuhi = evaluasi["pkg1_terpenuhi"]
    pkg2_terpenuhi = evaluasi["pkg2_terpenuhi"]
    pkg2_ijtimak_ok = evaluasi["pkg2_ijtimak_ok"]
    waktu_fajar_nz = evaluasi["waktu_fajar_nz"]
    hasil_pkg2 = evaluasi["hasil_pkg2"]
    waktu_ijtimak = evaluasi["waktu_ijtimak"]

    fig = plt.figure(figsize=(13, 7.2), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    # LAND/OCEAN dipatok .with_scale("110m") jg di sini utk konsisten/robust --
    # extent dunia [-180,180,-90,90] saat ini memang sudah otomatis resolve ke
    # 110m lewat AdaptiveScaler bawaan cfeature.LAND/OCEAN, TAPI itu bergantung
    # implisit pada extent selalu dunia penuh; kalau nanti ada yg nambah
    # ax.set_extent() utk zoom di fungsi ini, adaptive scaler akan diam-diam
    # minta shapefile lebih detail (persis bug yg ditemukan di peta Indonesia).
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="lightgray")
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="lightblue")
    ax.coastlines(resolution="110m", linewidth=0.5)

    gl = ax.gridlines(draw_labels=True, linewidth=0.3, alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False

    if pkg1_terpenuhi:
        warna_zona, label_zona = "orange", "Zona memenuhi kriteria (PKG 1 & PKG 2 terpenuhi)"
        if np.any(zona_pkg1):
            ax.contourf(lon_mesh, lat_mesh, zona_pkg1.astype(int),
                        levels=[0.5, 1.5], colors=[warna_zona], alpha=0.45,
                        transform=ccrs.PlateCarree())
    elif pkg2_terpenuhi:
        warna_zona, label_zona = "gold", "Zona memenuhi PKG 2 (fallback, daratan utama Amerika)"
        zona2 = hasil_pkg2["zona"]
        lon2, lat2 = hasil_pkg2["lon_mesh"], hasil_pkg2["lat_mesh"]
        ax.contourf(lon2, lat2, zona2.astype(int),
                    levels=[0.5, 1.5], colors=[warna_zona], alpha=0.6,
                    transform=ccrs.PlateCarree())
        # Zonanya bisa sangat sempit (dekat batas ambang), jadi ditandai juga
        # dengan penanda titik supaya tetap terlihat di peta skala dunia.
        lat_c = lat2[zona2].mean()
        lon_c = lon2[zona2].mean()
        ax.plot(lon_c, lat_c, marker="*", color="darkorange", markersize=14,
                markeredgecolor="black", transform=ccrs.PlateCarree(), zorder=5)
    else:
        warna_zona, label_zona = "orange", "Zona memenuhi kriteria Muhammadiyah"

    ax.contour(lon_mesh, lat_mesh, geo_alt_grid, levels=[5],
               colors="blue", linewidths=1.5, transform=ccrs.PlateCarree())
    ax.contour(lon_mesh, lat_mesh, elong_grid, levels=[8],
               colors="red", linewidths=1.5, transform=ccrs.PlateCarree())
    ax.contourf(lon_mesh, lat_mesh, no_sunset_masked.astype(int),
                levels=[0.5, 1.5], colors=["dimgray"], alpha=0.3,
                transform=ccrs.PlateCarree())
    ax.contour(lon_mesh, lat_mesh, hours_utc_grid, levels=[0, 24],
               colors="black", linewidths=1.8, linestyles="dashed",
               transform=ccrs.PlateCarree())

    if pkg1_terpenuhi:
        status_teks = "PKG 1 & PKG 2 terpenuhi"
    elif pkg2_terpenuhi:
        status_teks = "PKG 1 tidak terpenuhi — fallback ke PKG 2: terpenuhi"
    else:
        status_teks = "PKG 1 & PKG 2 tidak terpenuhi"

    ax.set_title(f"Peta Kriteria Muhammadiyah — Elongasi ≥8° & Tinggi Hilal ≥5° (Geosentris)\n"
                 f"{tanggal.strftime('%d %B %Y')}  —  {status_teks}",
                 fontsize=12, pad=14)

    legend_elems_muh = [
        plt.Line2D([0], [0], color="blue", lw=1.5, label="Tinggi hilal geosentris = 5°"),
        plt.Line2D([0], [0], color="red", lw=1.5, label="Elongasi geosentris = 8°"),
        plt.Line2D([0], [0], color="black", lw=1.8, linestyle="dashed",
                   label="Batas cutoff (sunset di luar rentang UTC 0-24\ntanggal target) — batas PKG 1"),
        plt.Rectangle((0, 0), 1, 1, fc=warna_zona, alpha=0.45, label=label_zona),
        plt.Rectangle((0, 0), 1, 1, fc="dimgray", alpha=0.3, label="Tidak ada sunset"),
    ]
    ax.legend(handles=legend_elems_muh, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              borderaxespad=0, fontsize=9, framealpha=0.9)

    # --- Catatan status PKG 2 (hanya ditampilkan kalau PKG 1 tidak terpenuhi) ---
    if not pkg1_terpenuhi:
        baris = []
        if waktu_fajar_nz is not None:
            cek = "OK" if pkg2_ijtimak_ok else "TIDAK terpenuhi"
            baris.append(f"Ijtimak {waktu_ijtimak.strftime('%d %b %Y %H:%M')} UTC vs "
                         f"fajar NZ {waktu_fajar_nz.strftime('%d %b %Y %H:%M')} UTC -> {cek}")
        else:
            baris.append("Waktu fajar NZ tidak dapat dihitung.")
        if hasil_pkg2 is not None:
            ket = "terpenuhi" if hasil_pkg2["ditemukan"] else "TIDAK terpenuhi"
            tahap = hasil_pkg2["tahap"]
            baris.append(f"Kriteria 5°/8° di daratan utama Amerika: {ket} "
                         f"(pencarian tahap {tahap}, resolusi "
                         f"{f'adaptif {RES_KASAR_PKG2:g}°/{RES_HALUS_ZONA_SEMPIT_PKG2:g}°' if tahap == 1 else '0.25°'})")
        else:
            baris.append("Kriteria 5°/8° di daratan utama Amerika: tidak dapat diperiksa.")
        catatan = "Cek syarat PKG 2:\n" + "\n".join(baris)
        fig.text(0.01, 0.01, catatan, fontsize=8, va="bottom", ha="left",
                  color="dimgray", wrap=True)

    # fig.tight_layout() dihapus -- sudah digantikan constrained_layout=True
    # di plt.figure() saat pembuatan figure (hipotesa: tight_layout() memicu
    # render-pass tambahan yang lebih mahal saat berinteraksi dgn GeoAxes
    # cartopy dibanding constrained_layout yg terintegrasi ke layout engine).
    return fig


# =========================================================
#  GERHANA MATAHARI -- LINTASAN (CENTRAL LINE)
#  Menyambung dari cari_gerhana_matahari_kandidat_ringan(): titik greatest
#  eclipse cuma SATU titik di waktu tunggal; utk lintasan lengkap (garis
#  tengah gerhana total/cincin melintasi Bumi), scan beberapa jam di
#  sekitarnya & sambung semua titik yg kena ellipsoid.
# =========================================================

def hitung_lintasan_gerhana_matahari(waktu_greatest_eclipse, jendela_menit=150, langkah_menit=2,
                                      mode="ringan", ts=None, eph=None):
    """Hitung lintasan (central line) gerhana matahari dgn scan waktu di
    sekitar waktu_greatest_eclipse (hasil cari_gerhana_matahari_kandidat_ringan
    utk entri yg kena_bumi=True). Prinsipnya sama persis dgn
    _titik_bayangan_ellipsoid() (satu titik), tinggal diulang tiap
    langkah_menit sepanjang jendela_menit di kedua sisi.

    mode/ts/eph diteruskan apa adanya ke _vektor_matahari_bulan_gast_batch()
    -- lihat catatan mode Presisi di sana.

    jendela_menit default 150 (2.5 jam) cukup utk mencakup durasi umbra/
    antumbra menyentuh Bumi (biasanya <3 jam dari awal sampai akhir lintasan
    total/cincin), langkah 2 menit cukup rapat utk garis mulus di peta dunia.

    Return: list of dict {'waktu', 'lat', 'lon', 'gamma'}, terurut dari
    ujung barat (awal) ke ujung timur (akhir) lintasan. List kosong kalau
    ternyata tidak ada satu titik pun yang kena Bumi (mestinya sudah
    disaring lebih dulu lewat 'kena_bumi' di cari_gerhana_matahari_kandidat_ringan,
    tapi tetap dicek di sini utk keamanan).
    """
    menit = np.arange(-jendela_menit, jendela_menit + langkah_menit, langkah_menit, dtype=float)
    P_sun, P_moon, gast, _ = _vektor_matahari_bulan_gast_batch(waktu_greatest_eclipse, menit, mode, ts, eph)
    kena_bumi, lat, lon, gamma = _titik_bayangan_ellipsoid_batch(P_sun, P_moon, gast)

    lintasan = []
    for i in np.where(kena_bumi)[0]:
        waktu = waktu_greatest_eclipse + timedelta(minutes=float(menit[i]))
        lintasan.append({"waktu": waktu, "lat": float(lat[i]), "lon": float(lon[i]),
                          "gamma": float(gamma[i])})
    return lintasan


def hitung_bayangan_penumbra_gerhana_matahari(waktu_greatest_eclipse, jendela_menit=240, langkah_menit=4,
                                               mode="ringan", ts=None, eph=None):
    """Hitung JEJAK bayangan PENUMBRA (bayang-bayang kabur Bulan) sepanjang
    waktu, dipakai utk menggambar ARSIRAN wilayah yang berpotensi melihat
    gerhana SEBAGIAN di peta dunia -- beda dgn hitung_lintasan_gerhana_matahari
    yang cuma menjejak sumbu (garis tengah total/cincin, dan cuma ada kalau
    umbra/antumbra betul2 kena Bumi).

    Di tiap langkah_menit sepanjang jendela_menit, dicek dgn gamma_km &
    r_penumbra_km (dari _radius_bayangan_km, geometri kerucut sederhana --
    lihat catatan "ringan" di sana) apakah penumbra SEDANG menyentuh Bumi
    (gamma_km <= Re + r_penumbra_km, definisi yang sama dipakai P1/P4 di
    cari_kontak_gerhana_matahari). Kalau ya, dicatat pusat lingkaran
    (_subtitik_sumbu_bayangan -- proyeksi radial, SELALU ada titik, beda dgn
    _titik_bayangan_ellipsoid yang butuh sumbu betul2 tembus ellipsoid) dan
    radius penumbra saat itu (km). Rangkaian lingkaran2 inilah yang nanti
    digambar bertumpuk (alpha rendah) di buat_figure_lintasan_gerhana_matahari
    sbg arsiran semi-gelap -- makin banyak lingkaran saling tumpuk (dekat
    greatest eclipse / dekat garis tengah), makin gelap arsirannya, meniru
    makin besarnya fraksi Matahari tertutup di situ.

    jendela_menit default 240 (4 jam) SAMA dgn default cari_kontak_gerhana_matahari
    supaya jejak ini konsisten mencakup rentang P1..P4 (durasi gerhana
    sebagian sedunia terpanjang yang pernah tercatat ~6 jam, jadi +-4 jam
    sudah longgar). langkah_menit 4 (lebih jarang dari central line yang 2)
    krn lingkaran penumbra jauh lebih besar & berubah lebih halus drpd
    lintasan sempit umbra/antumbra -- cukup rapat utk arsiran mulus tanpa
    bikin terlalu banyak lingkaran (lambat digambar).

    Return: list of dict {'waktu','lat','lon','r_penumbra_km'}, terurut dari
    P1 (awal) ke P4 (akhir). List kosong kalau penumbra ternyata tidak
    pernah menyentuh Bumi sepanjang jendela (semestinya sangat jarang utk
    kandidat yang sudah lolos filter beta di cari_gerhana_matahari_kandidat_ringan).
    """
    Re = RE_EKUATOR_KM
    menit = np.arange(-jendela_menit, jendela_menit + langkah_menit, langkah_menit, dtype=float)
    P_sun, P_moon, gast, _ = _vektor_matahari_bulan_gast_batch(waktu_greatest_eclipse, menit, mode, ts, eph)
    gamma_km, _r_umbra_km, r_penumbra_km = _radius_bayangan_km_batch(P_sun, P_moon)
    lat, lon = _subtitik_sumbu_bayangan_batch(P_sun, P_moon, gast)

    menyentuh = gamma_km <= Re + r_penumbra_km

    # radius efektif lingkaran iris di permukaan Bumi (km) -- vektor dari
    # cabang if/else skalar aslinya: kalau sumbu di luar permukaan (gamma>Re),
    # radius iris dihitung dari sudut potong kerucut vs bola; kalau sumbu
    # sudah menembus permukaan, radius efektifnya ya radius penumbra itu
    # sendiri. np.where dievaluasi elementwise utk SELURUH array dulu
    # (termasuk baris yg nanti tidak dipakai krn menyentuh=False), makanya
    # np.clip dipakai supaya arccos tidak NaN pada baris yg gamma_km sangat
    # jauh (rasio di luar [-1,1]).
    cos_theta = np.clip((gamma_km**2 + Re**2 - r_penumbra_km**2) / (2 * gamma_km * Re), -1.0, 1.0)
    r_eff_di_luar = Re * np.arccos(cos_theta)
    r_eff = np.where(gamma_km > Re, r_eff_di_luar, r_penumbra_km)

    dipakai = menyentuh & (r_eff > 1.0)
    jejak = []
    for i in np.where(dipakai)[0]:
        waktu = waktu_greatest_eclipse + timedelta(minutes=float(menit[i]))
        jejak.append({"waktu": waktu, "lat": float(lat[i]), "lon": float(lon[i]),
                      "r_penumbra_km": float(r_eff[i])})
    return jejak


def cari_kontak_gerhana_matahari(waktu_greatest_eclipse, jendela_menit=240, langkah_menit=2,
                                  mode="ringan", ts=None, eph=None):
    """Cari 6 waktu "kontak umum" gerhana matahari -- istilah baku yg dipakai
    NASA/BMKG utk PETA LINTASAN SEDUNIA (beda dgn 4 kontak LOKAL C1-C4 utk
    satu titik pengamat tertentu):

      P1 : penumbra PERTAMA menyentuh permukaan Bumi
           (gerhana sebagian mulai terlihat di suatu tempat di Bumi)
      U1 : umbra/antumbra PERTAMA menyentuh permukaan Bumi
           (gerhana total/cincin mulai bisa terlihat sekilas di suatu titik,
           belum tentu di sepanjang garis tengah/lintasan)
      U2 : sumbu bayangan (garis tengah) PERTAMA menyentuh Bumi
           -- awal lintasan total/cincin, lihat hitung_lintasan_gerhana_matahari
      U3 : sumbu bayangan (garis tengah) TERAKHIR menyentuh Bumi -- akhir lintasan
      U4 : umbra/antumbra TERAKHIR menyentuh permukaan Bumi
      P4 : penumbra TERAKHIR menyentuh permukaan Bumi
           (gerhana sebagian berakhir di seluruh Bumi)

    ("Greatest eclipse" TIDAK dihitung ulang di sini -- sudah tersedia dari
    _cari_waktu_greatest_eclipse / cari_gerhana_matahari_kandidat_ringan,
    dipakai sbg pusat jendela pencarian.)

    Metode: sampling gamma_km/r_umbra_km/r_penumbra_km (dari _radius_bayangan_km)
    tiap langkah_menit di +-jendela_menit sekitar greatest eclipse (default
    +-4 jam, cukup longgar utk gerhana matahari manapun -- durasi P1..P4
    tipikal di bawah itu), lalu titik potong "gamma_km == Re + radius_bayangan"
    dicari lewat INTERPOLASI LINEAR antar dua sampel berdekatan (bukan akar
    presisi tinggi -- cukup utk label menit di peta, sejalan dgn tingkat
    presisi hitung_lintasan_gerhana_matahari yg juga "ringan").

    Return: dict {'P1', 'U1', 'U2', 'U3', 'U4', 'P4'} -> datetime UTC atau
    None. None utk U1..U4 berarti umbra/antumbra sama sekali tidak menyentuh
    Bumi (gerhana PARSIAL SAJA sedunia -- lihat 'kena_bumi' di
    cari_gerhana_matahari_kandidat_ringan). None utk P1/P4 semestinya jarang
    terjadi kalau kandidat sudah lolos filter beta di sana.
    """
    menit = np.arange(-jendela_menit, jendela_menit + langkah_menit, langkah_menit, dtype=float)
    P_sun, P_moon, _, _ = _vektor_matahari_bulan_gast_batch(waktu_greatest_eclipse, menit, mode, ts, eph)
    gamma, r_umbra, r_penumbra = _radius_bayangan_km_batch(P_sun, P_moon)

    Re = RE_EKUATOR_KM   # pendekatan BOLA (cukup utk label kontak menit;
                          # lintasan detail di hitung_lintasan_gerhana_matahari
                          # sudah pakai ellipsoid WGS84 penuh)

    def _kontak_masuk_keluar(radius_bayangan_km):
        """radius_bayangan_km: array radius (r_penumbra ATAU abs(r_umbra))
        sepanjang waktu_arr. Return (t_masuk, t_keluar) hasil interpolasi
        linear titik potong gamma_km - (Re + radius) == 0, atau (None, None)
        kalau bayangan itu tidak pernah menyentuh Bumi di seluruh jendela."""
        selisih = gamma - (Re + radius_bayangan_km)
        di_dalam = selisih <= 0
        if not np.any(di_dalam):
            return None, None
        idx = np.where(di_dalam)[0]
        i_masuk, i_keluar = int(idx[0]), int(idx[-1])

        def _interp(i0, i1):
            y0, y1 = selisih[i0], selisih[i1]
            frac = 0.0 if y1 == y0 else y0 / (y0 - y1)
            m = menit[i0] + frac * (menit[i1] - menit[i0])
            return waktu_greatest_eclipse + timedelta(minutes=float(m))

        t_masuk = _interp(i_masuk - 1, i_masuk) if i_masuk > 0 else \
            waktu_greatest_eclipse + timedelta(minutes=float(menit[i_masuk]))
        t_keluar = _interp(i_keluar, i_keluar + 1) if i_keluar < len(menit) - 1 else \
            waktu_greatest_eclipse + timedelta(minutes=float(menit[i_keluar]))
        return t_masuk, t_keluar

    p1, p4 = _kontak_masuk_keluar(r_penumbra)
    u1, u4 = _kontak_masuk_keluar(np.abs(r_umbra))
    u2, u3 = _kontak_masuk_keluar(np.zeros_like(r_umbra))   # sumbu tepat menyentuh Bumi

    return {"P1": p1, "U1": u1, "U2": u2, "U3": u3, "U4": u4, "P4": p4}


def cari_kontak_gerhana_bulan(waktu_greatest_eclipse, jendela_menit=240, langkah_menit=2,
                               mode="ringan", ts=None, eph=None):
    """Versi GERHANA BULAN dari cari_kontak_gerhana_matahari() -- 7 waktu
    kontak standar (istilah baku NASA/BMKG utk gerhana Bulan):

      P1 : Bulan PERTAMA menyentuh penumbra (gerhana sebagian mulai kentara
           scr visual samar -- P1 sendiri nyaris tak terlihat mata telanjang)
      U1 : Bulan PERTAMA menyentuh umbra (gerhana sebagian mulai jelas kentara)
      U2 : Bulan SELURUHNYA masuk umbra (gerhana TOTAL mulai) -- None kalau
           magnitudo umbral < 1.0 (gerhana cuma sebagian/penumbral, tidak
           pernah total)
      Greatest : titik tengah, dari _cari_waktu_greatest_lunar_eclipse()
           (TIDAK dihitung ulang di sini, dipakai sbg pusat jendela)
      U3 : Bulan mulai keluar dari umbra (akhir totalitas)
      U4 : Bulan seluruhnya keluar umbra (akhir gerhana sebagian)
      P4 : Bulan seluruhnya keluar penumbra (akhir gerhana sebagian2 juga)

    Metode: PERSIS sama dgn cari_kontak_gerhana_matahari() -- sampling
    jarak Bulan-ke-sumbu tiap langkah_menit di +-jendela_menit sekitar
    greatest eclipse, titik potong dicari lewat interpolasi linear.

    Return: dict {'P1','U1','U2','Greatest','U3','U4','P4'} -> datetime UTC
    atau None. None utk U2/U3 brarti gerhana TIDAK total (cuma sebagian/
    penumbral -- lihat 'jenis' di cari_gerhana_bulan_kandidat_ringan). None
    utk U1/U4 brarti gerhana PENUMBRAL SAJA (Bulan tidak pernah menyentuh
    umbra sama sekali).
    """
    menit = np.arange(-jendela_menit, jendela_menit + langkah_menit, langkah_menit, dtype=float)
    P_sun, P_moon, _, _ = _vektor_matahari_bulan_gast_batch(waktu_greatest_eclipse, menit, mode, ts, eph)
    jarak, r_umbra, r_penumbra = _jarak_bulan_ke_sumbu_bayangan_bumi_km_batch(P_sun, P_moon)

    def _kontak_masuk_keluar(radius_km):
        """radius_km: array radius (r_penumbra+R_BULAN_KM, r_umbra+R_BULAN_KM,
        ATAU r_umbra-R_BULAN_KM) sepanjang menit. Return (t_masuk, t_keluar)
        hasil interpolasi linear titik potong jarak - radius_km == 0, atau
        (None, None) kalau Bulan tidak pernah "di dalam" radius itu di
        seluruh jendela (mis. gerhana tidak sampai total -> None utk U2/U3)."""
        selisih = jarak - radius_km
        di_dalam = selisih <= 0
        if not np.any(di_dalam):
            return None, None
        idx = np.where(di_dalam)[0]
        i_masuk, i_keluar = int(idx[0]), int(idx[-1])

        def _interp(i0, i1):
            y0, y1 = selisih[i0], selisih[i1]
            frac = 0.0 if y1 == y0 else y0 / (y0 - y1)
            m = menit[i0] + frac * (menit[i1] - menit[i0])
            return waktu_greatest_eclipse + timedelta(minutes=float(m))

        t_masuk = _interp(i_masuk - 1, i_masuk) if i_masuk > 0 else \
            waktu_greatest_eclipse + timedelta(minutes=float(menit[i_masuk]))
        t_keluar = _interp(i_keluar, i_keluar + 1) if i_keluar < len(menit) - 1 else \
            waktu_greatest_eclipse + timedelta(minutes=float(menit[i_keluar]))
        return t_masuk, t_keluar

    p1, p4 = _kontak_masuk_keluar(r_penumbra + R_BULAN_KM)
    u1, u4 = _kontak_masuk_keluar(r_umbra + R_BULAN_KM)
    u2, u3 = _kontak_masuk_keluar(r_umbra - R_BULAN_KM)   # negatif kalau r_umbra<R_BULAN_KM -> otomatis None,None (benar: tak pernah total)

    return {"P1": p1, "U1": u1, "U2": u2, "Greatest": waktu_greatest_eclipse,
            "U3": u3, "U4": u4, "P4": p4}


def _aman_tambah_fitur(ax, feature, **kwargs):
    """Bungkus ax.add_feature() dengan try/except.

    Cartopy (via GEOS/JTS di baliknya) punya bug lama yang cuma muncul di
    proyeksi non-persegi-panjang (mis. Orthographic/mode 'globe'): kalau
    sebuah polygon fitur (LAND/OCEAN/BORDERS dari shapefile Natural Earth)
    kebetulan tangent/pas menyentuh garis batas piringan proyeksi saat
    di-clip, GEOS melempar "IllegalArgumentException: point array must
    contain 0 or > 1 elements" -- exception dari library C internal yang
    TIDAK bisa dicegah dari sisi data kita (beda kasus dgn radius horizon
    gerhana Bulan yang memang bisa diperbaiki, lihat RADIUS_HORIZON_M).
    Di mode 'datar' (PlateCarree) praktis tidak pernah kena krn tidak ada
    "tepi piringan" utk di-tangent-i.

    Daripada satu fitur "sial" bikin SELURUH peta gagal tampil (dan user
    kena dialog error tanpa peta sama sekali), fitur yg gagal cukup
    dilewati -- peta tetap muncul, cuma tanpa lapis itu."""
    try:
        ax.add_feature(feature, **kwargs)
        return True
    except Exception as e:
        print(f"[peringatan] Lapis peta dilewati (gagal digambar): {e}")
        return False


def _aman_tambah_geometri(ax, geoms, crs, **kwargs):
    """Versi _aman_tambah_fitur() utk ax.add_geometries() (lingkaran
    penumbra/horizon buatan sendiri, bukan shapefile Natural Earth) --
    bug GEOS yang sama bisa juga kena di sini kalau lingkarannya kebetulan
    tangent ke tepi piringan proyeksi Orthographic."""
    try:
        ax.add_geometries(geoms, crs, **kwargs)
        return True
    except Exception as e:
        print(f"[peringatan] Geometri peta dilewati (gagal digambar): {e}")
        return False


def buat_figure_lintasan_gerhana_matahari(kandidat_gerhana, jendela_menit=150, langkah_menit=2,
                                           mode_peta="datar", mode="ringan", ts=None, eph=None):
    """Peta dunia gerhana matahari, dari satu entri kandidat hasil
    cari_gerhana_matahari_kandidat_ringan().

    mode/ts/eph: mode perhitungan astronomi ('ringan'/'jpl') yang DIPAKAI
    ULANG di sini utk semua perhitungan detail (kontak umum, lintasan,
    jejak penumbra, titik sumbu bayangan) -- idealnya SAMA dengan mode yang
    dipakai saat mencari kandidat_gerhana ini (cari_gerhana_matahari_kandidat_
    ringan), supaya seluruh peta konsisten satu tingkat presisi. Style peta konsisten dgn
    buat_figure_mabims/muhammadiyah (PlateCarree, LAND/OCEAN/COASTLINE
    dipatok .with_scale("110m") -- lihat catatan di
    _gambar_peta_dasar_indonesia soal kenapa ini penting).

    mode_peta: 'datar' (default, PlateCarree/proyeksi persegi panjang biasa,
    seluruh dunia sekali pandang) atau 'globe' (Orthographic -- proyeksi
    bola 3D, dipusatkan PERSIS di titik greatest eclipse, mensimulasikan
    Bumi dilihat dari sudut pandang si titik tsb menghadap penuh ke
    pengamat). SEMUA data (lintasan, arsiran penumbra, marker) memakai
    transform=ccrs.PlateCarree()/Geodetic() apa adanya -- itu CRS data
    (lat/lon biasa), bukan proyeksi tampilan, jadi kode penggambarannya
    TIDAK perlu diubah sama sekali antara dua mode; cukup ganti proyeksi
    axes-nya saja.

    Menggambar DUA lapis informasi sekaligus:
      1) ARSIRAN wilayah penumbra (bayang-bayang kabur Bulan) -- daerah yang
         berpotensi melihat gerhana SEBAGIAN, dari hitung_bayangan_penumbra_
         gerhana_matahari(). Digambar sbg banyak lingkaran geodesic bertumpuk
         alpha rendah shg makin ke tengah jalur (dekat greatest eclipse)
         arsirannya makin gelap -- kasar meniru makin besarnya fraksi
         Matahari tertutup di situ. SELALU digambar kalau ada (utk kandidat
         kena_bumi=True MAUPUN False, karena penumbra jauh lebih luas drpd
         umbra/antumbra & tetap ada meski sumbu bayangan sendiri meleset
         dari Bumi).
      2) Kalau kandidat kena_bumi=True: lintasan garis tengah (central line)
         total/cincin dari hitung_lintasan_gerhana_matahari() -- SAMA seperti
         sebelumnya, tidak diubah. Kalau kena_bumi=False (gerhana PARSIAL
         SAJA sedunia), lapis ini dilewati -- petanya cuma arsiran penumbra
         saja, tanpa garis tengah/marker U2-U3 (memang tidak ada).
    """
    waktu_greatest = kandidat_gerhana["waktu_greatest_eclipse"]
    kena_bumi = bool(kandidat_gerhana.get("kena_bumi"))
    kontak = cari_kontak_gerhana_matahari(waktu_greatest, mode=mode, ts=ts, eph=eph)

    # Titik pusat "greatest eclipse" -- dihitung DULU (sebelum axes dibuat),
    # karena ccrs.Orthographic butuh central_longitude/central_latitude
    # SAAT KONSTRUKSI, tidak bisa diubah belakangan. Sumbernya SAMA dengan
    # yang dipakai buat marker bintang di bawah (lat_perkiraan/lon_perkiraan
    # utk kena_bumi=True, atau proyeksi radial _subtitik_sumbu_bayangan utk
    # kena_bumi=False) -- supaya "pusat globe" & "titik greatest eclipse"
    # SELALU sama persis, bukan dua sumber yang bisa beda.
    if kena_bumi:
        lat_pusat = kandidat_gerhana["lat_perkiraan"]
        lon_pusat = kandidat_gerhana["lon_perkiraan"]
    else:
        lat_pusat, lon_pusat = _subtitik_sumbu_bayangan(waktu_greatest, mode, ts, eph)

    # Jendela dipakai utk memindai jejak (lintasan & penumbra) diperlebar
    # otomatis kalau perlu, supaya SELALU mencakup penuh P1..P4 (bukan cuma
    # jendela_menit default/legacy yang tadinya dipatok pas-pasan utk
    # lintasan total/cincin yang sempit) -- P1/P4 dari cari_kontak_gerhana_
    # matahari() (jendela default 240 menit/4 jam, longgar utk gerhana
    # manapun) dipakai sbg acuan seberapa lebar jejak penumbra perlu dipindai.
    def _menit_offset(t):
        return abs((t - waktu_greatest).total_seconds()) / 60.0 if t is not None else 0.0

    jendela_efektif = max(jendela_menit, _menit_offset(kontak["P1"]), _menit_offset(kontak["P4"])) + 10

    jejak_penumbra = hitung_bayangan_penumbra_gerhana_matahari(
        waktu_greatest, jendela_menit=jendela_efektif, langkah_menit=max(langkah_menit * 2, 4),
        mode=mode, ts=ts, eph=eph)

    if mode_peta == "globe":
        proyeksi = ccrs.Orthographic(central_longitude=lon_pusat, central_latitude=lat_pusat)
        fig = plt.figure(figsize=(9.5, 9), constrained_layout=True)
    else:
        proyeksi = ccrs.PlateCarree()
        fig = plt.figure(figsize=(13, 7.2), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=proyeksi)
    if mode_peta == "globe":
        ax.set_global()
    else:
        ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    _aman_tambah_fitur(ax, cfeature.LAND.with_scale("110m"), facecolor="lightgray")
    _aman_tambah_fitur(ax, cfeature.OCEAN.with_scale("110m"), facecolor="lightblue")
    _aman_tambah_fitur(ax, cfeature.BORDERS.with_scale("110m"), linewidth=0.4, edgecolor="dimgray")
    try:
        ax.coastlines(resolution="110m", linewidth=0.5)
    except Exception as e:
        print(f"[peringatan] Garis pantai dilewati (gagal digambar): {e}")

    import cartopy.geodesic as cgeo
    geodesic = cgeo.Geodesic()

    # ---- Lapis 1: arsiran penumbra (wilayah terdampak gerhana sebagian) ----
    # Banyak lingkaran (radius sungguhan per waktu, dari geometri
    # kerucut bayangan) ditumpuk dgn alpha rendah -- efeknya jadi gradasi
    # abu-abu semi gelap, wajar makin pekat di tengah jalur (tempat lingkaran2
    # saling tumpuk paling banyak, dekat garis tengah/greatest eclipse) &
    # makin pudar ke tepi (dekat P1/P4, gerhana sebagian baru mulai/mau usai).
    for p in jejak_penumbra:
        lingkaran = geodesic.circle(lon=p["lon"], lat=p["lat"], radius=p["r_penumbra_km"] * 1000, n_samples=60)
        poly = shapely.Polygon(lingkaran)
        _aman_tambah_geometri(ax, [poly], ccrs.Geodetic(), facecolor="dimgray", edgecolor="none",
                              alpha=0.045, zorder=2)

    # Batas terluar arsiran (lingkaran penumbra pertama=P1 & terakhir=P4)
    # digambar ulang dgn pola HATCH supaya "tepi wilayah terdampak" terlihat
    # jelas sbg garis, bukan cuma gradasi abu-abu yang menghilang halus.
    for p in ([jejak_penumbra[0], jejak_penumbra[-1]] if jejak_penumbra else []):
        lingkaran = geodesic.circle(lon=p["lon"], lat=p["lat"], radius=p["r_penumbra_km"] * 1000, n_samples=60)
        poly = shapely.Polygon(lingkaran)
        _aman_tambah_geometri(ax, [poly], ccrs.Geodetic(), facecolor="none", edgecolor="dimgray",
                              linewidth=0.6, hatch="////", alpha=0.55, zorder=3)

    # ---- Lapis 2: lintasan garis tengah (hanya kalau kena_bumi) ----
    lintasan = hitung_lintasan_gerhana_matahari(waktu_greatest, jendela_efektif, langkah_menit,
                                                 mode=mode, ts=ts, eph=eph) \
        if kena_bumi else []
    lats = [p["lat"] for p in lintasan]
    lons = [p["lon"] for p in lintasan]

    if lintasan:
        # Lintasan digambar per-SEGMEN (bukan satu ax.plot() polos) utk
        # menghindari garis "melompat" horizontal kalau lintasan kebetulan
        # melewati garis bujur 180/-180 (anti-meridian) -- lompatan bujur
        # besar antar titik berurutan jadi penanda segmen baru.
        seg_lon, seg_lat = [lons[0]], [lats[0]]
        for i in range(1, len(lons)):
            if abs(lons[i] - lons[i - 1]) > 180:
                ax.plot(seg_lon, seg_lat, color="red", linewidth=2.2,
                        transform=ccrs.PlateCarree(), zorder=5)
                seg_lon, seg_lat = [], []
            seg_lon.append(lons[i])
            seg_lat.append(lats[i])
        ax.plot(seg_lon, seg_lat, color="red", linewidth=2.2, transform=ccrs.PlateCarree(),
                label="Lintasan gerhana (garis tengah)", zorder=5)

        ax.plot(kandidat_gerhana["lon_perkiraan"], kandidat_gerhana["lat_perkiraan"],
                marker="*", color="darkred", markersize=16, transform=ccrs.PlateCarree(),
                label=f"Greatest eclipse (gamma={kandidat_gerhana['gamma']:.3f})", zorder=6)

        # Titik U2 (awal lintasan total/cincin) & U3 (akhir lintasan) -- ujung
        # barat & timur garis tengah, dari titik pertama/terakhir 'lintasan'
        # yg memang sudah difilter cuma yg kena_bumi. Ditandai beda dgn
        # bintang greatest eclipse supaya jelas mana AWAL/AKHIR jalur, mana
        # PUNCAK.
        if len(lons) >= 2:
            ax.plot(lons[0], lats[0], marker="o", color="orange", markersize=8,
                    markeredgecolor="black", markeredgewidth=0.6, transform=ccrs.PlateCarree(),
                    label="U2 — awal lintasan total/cincin", zorder=6)
            ax.plot(lons[-1], lats[-1], marker="o", color="darkorange", markersize=8,
                    markeredgecolor="black", markeredgewidth=0.6, transform=ccrs.PlateCarree(),
                    label="U3 — akhir lintasan total/cincin", zorder=6)
    else:
        # kena_bumi=False -> tidak ada garis tengah/bintang lat_perkiraan
        # (memang None). Tetap tandai titik greatest eclipse supaya peta
        # tidak kosong dari penanda -- pakai proyeksi radial sumbu bayangan
        # (_subtitik_sumbu_bayangan, SELALU ada titik) sbg perkiraan lokasi
        # "puncak" gerhana sebagian ini. (lat_pusat/lon_pusat sudah dihitung
        # di atas -- titik yang sama persis dipakai sbg pusat mode 'globe'.)
        ax.plot(lon_pusat, lat_pusat, marker="*", color="darkred", markersize=16,
                transform=ccrs.PlateCarree(),
                label="Greatest eclipse (perkiraan, parsial saja)", zorder=6)

    if mode_peta == "globe":
        ax.gridlines(draw_labels=False, linewidth=0.3, alpha=0.5)
    else:
        gl = ax.gridlines(draw_labels=True, linewidth=0.3, alpha=0.5)
        gl.top_labels = False
        gl.right_labels = False

    judul_jenis = "Lintasan Gerhana Matahari" if kena_bumi else "Gerhana Matahari Sebagian (Parsial Saja)"
    ax.set_title(f"{judul_jenis} — {waktu_greatest.strftime('%d %B %Y')}\n"
                 f"Greatest eclipse: {waktu_greatest.strftime('%H:%M:%S')} UTC",
                 fontsize=12, pad=14)

    # Proxy handle utk arsiran penumbra (ax.add_geometries tidak dipanggil dgn
    # label= krn dipanggil berkali-kali -- kalau diberi label, tiap
    # pemanggilan akan jadi entri legend terpisah/berulang).
    from matplotlib.patches import Patch
    handles, labels = ax.get_legend_handles_labels()
    if jejak_penumbra:
        handles.append(Patch(facecolor="dimgray", edgecolor="dimgray", alpha=0.5,
                              hatch="////",
                              label="Wilayah gerhana sebagian (bayang-bayang/penumbra)"))
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              borderaxespad=0, fontsize=9, framealpha=0.9)

    # ---- Kotak info kontak umum: P1, U1, U2, Greatest, U3, U4, P4 ----
    # (P1/P4 = penumbra pertama/terakhir sentuh Bumi -- gerhana sebagian
    #  mulai/berakhir sedunia; U1/U4 = umbra ATAU antumbra pertama/terakhir
    #  sentuh Bumi; U2/U3 = garis tengah pertama/terakhir sentuh Bumi;
    #  lihat docstring cari_kontak_gerhana_matahari utk definisi lengkap.
    #  Utk kandidat kena_bumi=False, U1..U4 memang None -- ditampilkan
    #  "tidak terjadi", konsisten dgn tidak adanya lintasan total/cincin.)
    def _fmt(t):
        return t.strftime("%H:%M:%S") + " UTC" if t is not None else "— (tidak terjadi)"

    baris_info = [
        f"P1 (awal sebagian)        : {_fmt(kontak['P1'])}",
        f"U1 (awal umbra/antumbra)  : {_fmt(kontak['U1'])}",
        f"U2 (awal total/cincin)    : {_fmt(kontak['U2'])}",
        f"Greatest eclipse          : {waktu_greatest.strftime('%H:%M:%S')} UTC",
        f"U3 (akhir total/cincin)   : {_fmt(kontak['U3'])}",
        f"U4 (akhir umbra/antumbra) : {_fmt(kontak['U4'])}",
        f"P4 (akhir sebagian)       : {_fmt(kontak['P4'])}",
    ]
    teks_kontak = "Kontak Umum Gerhana (UTC)\n" + "\n".join(baris_info)
    ax.text(1.01, 0.0, teks_kontak, transform=ax.transAxes,
            fontsize=8, family="monospace", va="bottom", ha="left",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.85,
                      edgecolor="gray", linewidth=0.6))

    return fig


def _subtitik_bulan(waktu, mode="ringan", ts=None, eph=None):
    """Titik di Bumi tempat Bulan tepat di zenith (posisi pengamat terbaik
    utk melihat gerhana Bulan pas waktu itu). KONSEPNYA BEDA dari
    _subtitik_sumbu_bayangan() (dipakai gerhana Matahari -- itu titik di
    SUMBU BAYANGAN Matahari-Bulan): di sini murni "Bulan ada tepat di atas
    kepala", dipakai sbg pusat lingkaran wilayah visibilitas gerhana Bulan
    (yg SELALU meliputi hampir separuh Bumi -- beda total dari gerhana
    Matahari yg jalurnya sempit)."""
    _, P_moon, gast, _ = _vektor_matahari_bulan_gast(waktu, mode, ts, eph)
    x, y, z = P_moon
    lat = np.degrees(np.arcsin(z / np.linalg.norm(P_moon)))
    lon = ((np.degrees(np.arctan2(y, x)) - gast + 180) % 360) - 180
    return float(lat), float(lon)


def buat_figure_visibilitas_gerhana_bulan(kandidat_gerhana, kontak=None, mode_peta="datar",
                                           mode="ringan", ts=None, eph=None):
    """Peta dunia wilayah visibilitas gerhana Bulan. KONSEP PETANYA BEDA

    mode/ts/eph: mode perhitungan astronomi ('ringan'/'jpl'), idealnya SAMA
    dengan mode yang dipakai saat mencari kandidat_gerhana ini
    (cari_gerhana_bulan_kandidat_ringan) -- lihat catatan yang sama di
    buat_figure_lintasan_gerhana_matahari().
    TOTAL dari gerhana Matahari (yg jalur totalitasnya sempit, cuma
    beberapa ratus km lebar): gerhana Bulan SELALU terlihat SERENTAK dari
    SELURUH belahan Bumi yg sedang malam & Bulan-nya di atas horizon --
    jadi yg digambar bukan "lintasan", tapi WILAYAH (hampir separuh Bumi).

    mode_peta: 'datar' (default, PlateCarree) atau 'globe' (Orthographic,
    dipusatkan di titik Bulan tepat di zenith saat greatest eclipse --
    "greatest point" versi gerhana Bulan) -- lihat catatan lengkap soal
    kenapa kode penggambaran datanya tidak perlu berubah sama sekali di
    buat_figure_lintasan_gerhana_matahari().

    Dua lapis lingkaran horizon (radius geodesic 90 derajat dari
    _subtitik_bulan(), pakai cartopy.geodesic sama seperti arsiran
    penumbra di buat_figure_lintasan_gerhana_matahari):
      1) Lingkaran P1 & P4 (garis putus2) -- batas horizon TERLUAS,
         wilayah GABUNGAN keduanya adalah tempat yg BISA melihat SEBAGIAN
         dari gerhana (meski cuma sekilas saat Bulan terbit/terbenam di
         tengah proses gerhana).
      2) Lingkaran greatest eclipse (arsiran solid) -- wilayah UTAMA,
         tempat Bulan di atas horizon PAS puncak gerhana (kandidat lokasi
         terbaik utk mengamati momen paling gelap/paling tertutup).

    Parameter kandidat_gerhana: satu entri dari
    cari_gerhana_bulan_kandidat_ringan() dgn jenis != 'tidak ada gerhana'.
    kontak: dict hasil cari_kontak_gerhana_bulan(), dihitung otomatis kalau
    tidak diberikan.
    """
    if kandidat_gerhana.get("jenis", "tidak ada gerhana") == "tidak ada gerhana":
        raise ValueError("Kandidat ini tidak menghasilkan gerhana Bulan sama sekali "
                          "(jenis='tidak ada gerhana') -- tidak ada apa2 utk digambar.")

    waktu_greatest = kandidat_gerhana["waktu_greatest_eclipse"]
    if kontak is None:
        kontak = cari_kontak_gerhana_bulan(waktu_greatest, mode=mode, ts=ts, eph=eph)

    import cartopy.geodesic as cgeo
    geodesic = cgeo.Geodesic()
    RE_RATA_RATA_M = 6371000.0
    # Seperempat keliling Bumi (90 derajat) = batas horizon SEBENARNYA.
    # TAPI 90 derajat persis = TEPAT di tepi piringan proyeksi Orthographic
    # (mode 'globe') -- lingkaran yg menyentuh persis batas piringan itu
    # memicu bug clipping GEOS/JTS di cartopy ("IllegalArgumentException:
    # point array must contain 0 or > 1 elements", muncul saat proyeksi
    # meng-clip polygon yg tangent/pas di garis batas). Dikecilkan 0.1%
    # (99.9%) supaya sedikit di DALAM piringan -- beda visual tidak
    # signifikan (<0.1% radius) tapi menghindari kasus tangent tsb sama
    # sekali, di mode 'datar' maupun 'globe'.
    RADIUS_HORIZON_M = (np.pi / 2) * 0.999 * RE_RATA_RATA_M

    def _lingkaran_horizon(waktu):
        lat, lon = _subtitik_bulan(waktu, mode, ts, eph)
        titik = geodesic.circle(lon=lon, lat=lat, radius=RADIUS_HORIZON_M, n_samples=120)
        return shapely.Polygon(titik), lat, lon

    # Titik pusat "greatest eclipse" (Bulan tepat di zenith) -- dihitung DULU,
    # sebelum axes dibuat, sama alasannya dgn versi gerhana Matahari.
    lat_pusat, lon_pusat = _subtitik_bulan(waktu_greatest, mode, ts, eph)

    if mode_peta == "globe":
        proyeksi = ccrs.Orthographic(central_longitude=lon_pusat, central_latitude=lat_pusat)
        fig = plt.figure(figsize=(9.5, 9), constrained_layout=True)
    else:
        proyeksi = ccrs.PlateCarree()
        fig = plt.figure(figsize=(13, 7.2), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=proyeksi)
    if mode_peta == "globe":
        ax.set_global()
    else:
        ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    _aman_tambah_fitur(ax, cfeature.LAND.with_scale("110m"), facecolor="lightgray")
    _aman_tambah_fitur(ax, cfeature.OCEAN.with_scale("110m"), facecolor="lightblue")
    _aman_tambah_fitur(ax, cfeature.BORDERS.with_scale("110m"), linewidth=0.4, edgecolor="dimgray")
    try:
        ax.coastlines(resolution="110m", linewidth=0.5)
    except Exception as e:
        print(f"[peringatan] Garis pantai dilewati (gagal digambar): {e}")

    # ---- Lapis 1: batas horizon terluar (P1 & P4, kalau ada) ----
    for t in (kontak.get("P1"), kontak.get("P4")):
        if t is not None:
            poly, _, _ = _lingkaran_horizon(t)
            _aman_tambah_geometri(ax, [poly], ccrs.Geodetic(), facecolor="none",
                                  edgecolor="darkorange", linewidth=1.3, linestyle="--",
                                  alpha=0.85, zorder=3)

    # ---- Lapis 2: wilayah utama, greatest eclipse ----
    poly_greatest, lat_g, lon_g = _lingkaran_horizon(waktu_greatest)
    _aman_tambah_geometri(ax, [poly_greatest], ccrs.Geodetic(), facecolor="navy",
                          edgecolor="darkblue", linewidth=1.5, alpha=0.25, zorder=2)

    ax.plot(lon_g, lat_g, marker="*", color="gold", markeredgecolor="black",
            markeredgewidth=0.8, markersize=17, transform=ccrs.PlateCarree(), zorder=6)

    gl = ax.gridlines(draw_labels=True, linewidth=0.3, alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False

    judul_jenis = {"total": "TOTAL", "sebagian": "SEBAGIAN",
                   "penumbral": "PENUMBRAL"}.get(kandidat_gerhana["jenis"], kandidat_gerhana["jenis"])
    ax.set_title(f"Visibilitas Gerhana Bulan {judul_jenis} — {waktu_greatest.strftime('%d %B %Y')}\n"
                 f"Greatest eclipse: {waktu_greatest.strftime('%H:%M:%S')} UTC   "
                 f"Magnitudo umbral: {kandidat_gerhana['magnitudo_umbral']:.3f}",
                 fontsize=12, pad=14)

    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    handles = [
        Patch(facecolor="navy", edgecolor="darkblue", alpha=0.35,
              label="Bulan di atas horizon saat greatest eclipse"),
        Line2D([0], [0], color="darkorange", linestyle="--", linewidth=1.3,
               label="Batas horizon P1/P4 (sekilas saat Bulan terbit/terbenam)"),
        Line2D([0], [0], marker="*", color="gold", markeredgecolor="black",
               markersize=12, linestyle="None", label="Titik Bulan tepat di zenith"),
    ]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              borderaxespad=0, fontsize=9, framealpha=0.9)

    def _fmt(t):
        return t.strftime("%H:%M:%S") + " UTC" if t is not None else "— (tidak terjadi)"

    baris_info = [
        f"P1 (awal sebagian/penumbra) : {_fmt(kontak['P1'])}",
        f"U1 (awal umbra)             : {_fmt(kontak['U1'])}",
        f"U2 (awal TOTAL)             : {_fmt(kontak['U2'])}",
        f"Greatest eclipse            : {waktu_greatest.strftime('%H:%M:%S')} UTC",
        f"U3 (akhir TOTAL)            : {_fmt(kontak['U3'])}",
        f"U4 (akhir umbra)            : {_fmt(kontak['U4'])}",
        f"P4 (akhir sebagian/penumbra): {_fmt(kontak['P4'])}",
    ]
    teks_kontak = "Kontak Umum Gerhana Bulan (UTC)\n" + "\n".join(baris_info)
    ax.text(1.01, 0.0, teks_kontak, transform=ax.transAxes,
            fontsize=8, family="monospace", va="bottom", ha="left",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.85,
                      edgecolor="gray", linewidth=0.6))

    return fig


#  Koordinat diisi manual (mode Desimal ATAU DMS/Derajat-Menit-Detik),
#  lengkap dengan pilihan zona waktu. Lokasi terakhir yang dipakai
#  disimpan otomatis ke file teks lokal (lihat _path_file_lokasi),
#  jadi tidak perlu diketik ulang tiap buka aplikasi.
#
#  Deklinasi & equation of time memakai posisi_matahari()/
#  equation_of_time_menit() yang SUDAH ADA di atas (VSOP87 ringkas) --
#  tidak menduplikasi model astronomi, cukup 1x per tanggal (deklinasi
#  berubah sangat lambat dalam sehari sehingga aman dipakai untuk
#  semua sudut jam waktu sholat, praktik umum di kalkulator hisab).
#
#  Arah kiblat dihitung dengan DUA metode sekaligus supaya bisa
#  dibandingkan:
#    - Spherical (bola bumi sferis)      -> formula bearing great-circle
#    - Vincenty (elipsoid WGS84, inverse)-> lebih presisi krn Bumi pepat
# =========================================================

KAABAH_LAT = 21.4225
KAABAH_LON = 39.8262

# WGS84
_WGS84_A = 6378137.0
_WGS84_F = 1 / 298.257223563
_WGS84_B = _WGS84_A * (1 - _WGS84_F)

ZONA_WAKTU_PILIHAN = [
    ("WIB (UTC+7)", 7.0),
    ("WITA (UTC+8)", 8.0),
    ("WIT (UTC+9)", 9.0),
    ("UTC+0", 0.0),
    ("Custom...", None),
]

PRESET_SUDUT = {
    "Kemenag RI (Fajr -20°, Isya -18°)": (-20.0, -18.0),
    "Muhammadiyah (Fajr -18°, Isya -18°)": (-18.0, -18.0),
    "MWL (Fajr -18°, Isya -17°)": (-18.0, -17.0),
    "Egyptian (Fajr -19.5°, Isya -17.5°)": (-19.5, -17.5),
}


def dms_ke_desimal(derajat, menit, detik, arah=None):
    """Konversi Derajat-Menit-Detik -> desimal. 'arah' opsional: 'S' atau 'W'
    membuat hasil negatif (kalau derajat sendiri sudah negatif, itu dipakai)."""
    nilai = abs(float(derajat)) + float(menit) / 60.0 + float(detik) / 3600.0
    negatif = (arah in ("S", "W")) or (float(derajat) < 0)
    return -nilai if negatif else nilai


def desimal_ke_dms(nilai):
    """Konversi desimal -> (derajat, menit, detik, positif_bool)."""
    positif = nilai >= 0
    nilai_abs = abs(nilai)
    d = int(nilai_abs)
    sisa_menit = (nilai_abs - d) * 60.0
    m = int(sisa_menit)
    s = (sisa_menit - m) * 60.0
    return d, m, s, positif


def format_dms(nilai, jenis="lat"):
    """String DMS siap tampil, mis. '6°11′59.7″ LS' / '106°49′1.2″ BT'."""
    d, m, s, positif = desimal_ke_dms(nilai)
    if jenis == "lat":
        arah = "LU" if positif else "LS"
    else:
        arah = "BT" if positif else "BB"
    return f"{d}°{m:02d}′{s:04.1f}″ {arah}"


def qibla_spherical(lat_deg, lon_deg):
    """Arah kiblat (azimuth dari Utara, searah jarum jam, 0-360°) memakai
    formula bearing great-circle di bola bumi sferis (bumi dianggap bulat
    sempurna)."""
    lat1, lon1 = np.radians(lat_deg), np.radians(lon_deg)
    lat2, lon2 = np.radians(KAABAH_LAT), np.radians(KAABAH_LON)
    dlon = lon2 - lon1
    x = np.sin(dlon) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    azimuth = np.degrees(np.arctan2(x, y)) % 360.0

    # Jarak great-circle (haversine) untuk pembanding, dalam km.
    dlat = lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    jarak_km = 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return float(azimuth), float(jarak_km)


def qibla_vincenty(lat_deg, lon_deg, max_iterasi=200, toleransi=1e-12):
    """Arah kiblat (azimuth awal, 0-360°) & jarak (km) memakai formula
    inverse Vincenty di elipsoid WGS84 (memperhitungkan bumi pepat/oblate,
    lebih presisi daripada model bola sferis)."""
    import math

    lat1, lon1 = math.radians(lat_deg), math.radians(lon_deg)
    lat2, lon2 = math.radians(KAABAH_LAT), math.radians(KAABAH_LON)
    a, f, b = _WGS84_A, _WGS84_F, _WGS84_B

    L = lon2 - lon1
    U1 = math.atan((1 - f) * math.tan(lat1))
    U2 = math.atan((1 - f) * math.tan(lat2))
    sinU1, cosU1 = math.sin(U1), math.cos(U1)
    sinU2, cosU2 = math.sin(U2), math.cos(U2)

    lam = L
    sin_sigma = 0.0
    cos_sigma = 1.0
    cos_2sigma_m = 0.0
    cos_sq_alpha = 1.0
    for _ in range(max_iterasi):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.sqrt((cosU2 * sin_lam) ** 2 +
                               (cosU1 * sinU2 - sinU1 * cosU2 * cos_lam) ** 2)
        if sin_sigma == 0:
            return 0.0, 0.0  # titik berimpit dengan Ka'bah (praktis tak terjadi)
        cos_sigma = sinU1 * sinU2 + cosU1 * cosU2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cosU1 * cosU2 * sin_lam / sin_sigma
        cos_sq_alpha = 1 - sin_alpha ** 2
        cos_2sigma_m = (cos_sigma - 2 * sinU1 * sinU2 / cos_sq_alpha) if cos_sq_alpha != 0 else 0.0
        C = f / 16 * cos_sq_alpha * (4 + f * (4 - 3 * cos_sq_alpha))
        lam_lama = lam
        lam = L + (1 - C) * f * sin_alpha * (
            sigma + C * sin_sigma * (
                cos_2sigma_m + C * cos_sigma * (-1 + 2 * cos_2sigma_m ** 2)))
        if abs(lam - lam_lama) < toleransi:
            break

    u_sq = cos_sq_alpha * (a ** 2 - b ** 2) / b ** 2
    A = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    B = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    delta_sigma = B * sin_sigma * (
        cos_2sigma_m + B / 4 * (
            cos_sigma * (-1 + 2 * cos_2sigma_m ** 2) - B / 6 * cos_2sigma_m *
            (-3 + 4 * sin_sigma ** 2) * (-3 + 4 * cos_2sigma_m ** 2)))
    jarak_m = b * A * (sigma - delta_sigma)

    azimuth1 = math.degrees(math.atan2(
        cosU2 * math.sin(lam), cosU1 * sinU2 - sinU1 * cosU2 * math.cos(lam))) % 360.0

    return azimuth1, jarak_m / 1000.0


def _deklinasi_dan_eqt(tanggal):
    """Deklinasi Matahari (derajat) pada tengah hari tanggal tsb (VSOP87
    ringkas, posisi_matahari yang sudah ada), plus Equation of Time (menit,
    formula Spencer yang sudah ada)."""
    jd_ut = julian_day(tanggal.year, tanggal.month, tanggal.day + 0.5)
    dt_hari = delta_t_detik(tanggal.year, tanggal.month) / 86400.0
    T = (jd_ut + dt_hari - 2451545.0) / 36525.0
    _, dec, _, _ = posisi_matahari(np.array([T]))
    eqt_menit = equation_of_time_menit(tanggal)
    return float(dec[0]), float(eqt_menit)


def _sudut_jam_matahari(lat_deg, dec_deg, alt_deg):
    """Sudut jam (derajat, 0-180) saat Matahari berada pada altitude
    tertentu, untuk lintang & deklinasi tertentu. None kalau altitude
    tsb tidak pernah tercapai di lintang ini (mis. lintang kutub)."""
    lat_r, dec_r, alt_r = np.radians(lat_deg), np.radians(dec_deg), np.radians(alt_deg)
    penyebut = np.cos(lat_r) * np.cos(dec_r)
    if penyebut == 0:
        return None
    cos_h = (np.sin(alt_r) - np.sin(lat_r) * np.sin(dec_r)) / penyebut
    if cos_h < -1 or cos_h > 1:
        return None
    return float(np.degrees(np.arccos(cos_h)))


def hitung_waktu_sholat(tanggal, lat_deg, lon_deg, zona_offset_jam, elevasi_m=0.0,
                         sudut_fajar=-20.0, sudut_isya=-18.0, ihtiyat_menit=2.0,
                         imsak_sebelum_fajr_menit=10.0, mazhab_ashar="syafii"):
    """Hitung waktu-waktu sholat (dalam jam desimal, zona waktu setempat).
    mazhab_ashar: 'syafii' (faktor bayangan=1) atau 'hanafi' (faktor=2).
    Return dict nama_waktu -> jam_desimal (bisa None kalau tak terhitung,
    mis. di lintang tinggi saat musim tertentu)."""
    dec, eqt = _deklinasi_dan_eqt(tanggal)

    dhuhur = 12.0 - eqt / 60.0 - lon_deg / 15.0 + zona_offset_jam

    dip_derajat = 0.0347 * np.sqrt(max(elevasi_m, 0.0))  # koreksi kerendahan ufuk
    alt_terbit_terbenam = -0.833 - dip_derajat

    faktor_bayangan = 2.0 if mazhab_ashar == "hanafi" else 1.0
    alt_ashar = np.degrees(np.arctan(1.0 / (faktor_bayangan + np.tan(np.radians(abs(lat_deg - dec))))))

    def _offset_jam(alt_deg):
        h = _sudut_jam_matahari(lat_deg, dec, alt_deg)
        return None if h is None else h / 15.0

    hasil = {}
    h_fajar = _offset_jam(sudut_fajar)
    h_terbit = _offset_jam(alt_terbit_terbenam)
    h_ashar = _offset_jam(alt_ashar)
    h_isya = _offset_jam(sudut_isya)

    hasil["subuh"] = None if h_fajar is None else dhuhur - h_fajar
    hasil["terbit"] = None if h_terbit is None else dhuhur - h_terbit
    hasil["dhuha"] = None if h_terbit is None else dhuhur - h_terbit + (20.0 / 60.0)
    hasil["dzuhur"] = dhuhur
    hasil["ashar"] = None if h_ashar is None else dhuhur + h_ashar
    hasil["maghrib"] = None if h_terbit is None else dhuhur + h_terbit
    hasil["isya"] = None if h_isya is None else dhuhur + h_isya
    hasil["imsak"] = None if hasil["subuh"] is None else hasil["subuh"] - imsak_sebelum_fajr_menit / 60.0

    # Ihtiyat (jeda kehati-hatian) menit, ditambahkan ke semua waktu KECUALI
    # Imsak (sudah dimajukan sendiri di atas) dan Dhuha.
    for kunci in ("subuh", "terbit", "dzuhur", "ashar", "maghrib", "isya"):
        if hasil[kunci] is not None:
            hasil[kunci] = hasil[kunci] + ihtiyat_menit / 60.0

    # Hitung Waktu Kiblat (Vincenty & Spherical)
    az_spherical, _ = qibla_spherical(lat_deg, lon_deg)
    az_vincenty, _ = qibla_vincenty(lat_deg, lon_deg)

    def _kiblat_time(az):
        phi = np.radians(lat_deg)
        decl = np.radians(dec)
        a = 1.0 / np.tan(np.radians(az))
        b = -np.sin(phi)
        c = -np.cos(phi) * np.tan(decl)
        R = np.sqrt(a*a + b*b)
        if R == 0:
            return None
        ratio = c / R
        if abs(ratio) > 1.0:
            return None
        theta = np.arctan2(b, a)
        H_rad = np.pi / 2.0 + np.arccos(ratio) - theta
        H_rad = (H_rad + np.pi) % (2.0 * np.pi) - np.pi
        H_deg = np.degrees(H_rad)
        return (12.0 + H_deg / 15.0 - eqt / 60.0 + (zona_offset_jam * 15.0 - lon_deg) / 15.0) % 24.0

    hasil["kiblat_v"] = _kiblat_time(az_vincenty)
    hasil["kiblat_s"] = _kiblat_time(az_spherical)

    return hasil


def _observer_skyfield(eph, lat_deg, lon_deg, elevasi_m=0.0):
    """Titik pengamat (observer) topocentric untuk Skyfield, di atas permukaan
    Bumi pada koordinat & elevasi tertentu.
    Return sepasang (observer, topos):
      - observer = eph["earth"] + topos, dipakai utk .at(t).observe(...) (altaz dsb).
      - topos    = objek geografis mentah (wgs84.latlon), dipakai utk fungsi almanac
                   seperti meridian_transits() yang MELAKUKAN SENDIRI penjumlahan
                   dengan eph["earth"] secara internal -- kalau observer yang sudah
                   dijumlah earth dipakai lagi di situ, terjadi penjumlahan ganda
                   dan Skyfield melempar ValueError ("you can only add two vectors...").
    """
    topos = wgs84.latlon(lat_deg, lon_deg, elevation_m=max(elevasi_m, 0.0))
    observer = eph["earth"] + topos
    return observer, topos


def _pilih_lintasan_terdekat(t_arr, y_arr, ingin_naik, acuan_dt, sebelum_acuan):
    """Dari hasil almanac.find_discrete (t_arr = waktu-waktu perlintasan,
    y_arr = 1 kalau nilai berubah dari False->True/'naik', 0 kalau
    True->False/'turun'), pilih SATU perlintasan yang arahnya sesuai
    (ingin_naik) dan berada sebelum/sesudah waktu acuan (acuan_dt, biasanya
    waktu transit/dzuhur astronomis) sesuai sebelum_acuan. Kalau ada
    beberapa kandidat, ambil yang paling dekat dengan acuan_dt.
    Return datetime UTC, atau None kalau tidak ada perlintasan yang cocok
    (mis. lintang kutub saat altitude target tidak pernah tercapai)."""
    target_y = 1 if ingin_naik else 0
    kandidat = []
    for t, y in zip(t_arr, y_arr):
        if int(y) != target_y:
            continue
        dt = ke_utc_datetime(t)
        if sebelum_acuan and dt <= acuan_dt:
            kandidat.append(dt)
        elif not sebelum_acuan and dt >= acuan_dt:
            kandidat.append(dt)
    if not kandidat:
        return None
    return min(kandidat, key=lambda d: abs((d - acuan_dt).total_seconds()))


def hitung_waktu_sholat_skyfield(tanggal, lat_deg, lon_deg, zona_offset_jam, ts, eph,
                                  elevasi_m=0.0, sudut_fajar=-20.0, sudut_isya=-18.0,
                                  ihtiyat_menit=2.0, imsak_sebelum_fajr_menit=10.0,
                                  mazhab_ashar="syafii"):
    """Versi presisi tinggi dari hitung_waktu_sholat(): posisi Matahari
    topocentric dihitung langsung dari ephemeris JPL DE421 via Skyfield
    (bukan VSOP87 ringkas + rumus Equation of Time terpisah), dan waktu
    tiap perlintasan altitude dicari dengan almanac.find_discrete (bukan
    rumus sudut jam analitik). Butuh ts & eph (mode Presisi) sudah dimuat.
    Struktur hasil (dict, jam desimal zona setempat) sama persis dengan
    hitung_waktu_sholat(), supaya kedua mode bisa dipakai bergantian oleh
    kode pemanggil (GUI) tanpa perubahan lain."""
    kunci_kosong = ("imsak", "subuh", "terbit", "dhuha", "dzuhur", "ashar", "maghrib", "isya")
    hasil_kosong = {k: None for k in kunci_kosong}

    observer, topos = _observer_skyfield(eph, lat_deg, lon_deg, elevasi_m)
    sun = eph["sun"]

    # Jendela pencarian: satu hari lokal penuh + buffer 3 jam di kedua sisi
    # (jaga-jaga kalau transit/terbit/terbenam jatuh dekat batas hari, atau
    # offset zona waktu custom yang tidak lazim).
    t0_dt = tanggal - timedelta(hours=zona_offset_jam) - timedelta(hours=3)
    t1_dt = tanggal + timedelta(days=1) - timedelta(hours=zona_offset_jam) + timedelta(hours=3)
    t0 = ts.utc(t0_dt.year, t0_dt.month, t0_dt.day, t0_dt.hour, t0_dt.minute, t0_dt.second)
    t1 = ts.utc(t1_dt.year, t1_dt.month, t1_dt.day, t1_dt.hour, t1_dt.minute, t1_dt.second)

    # -- Dzuhur = transit atas (upper meridian transit) Matahari --
    f_transit = almanac.meridian_transits(eph, sun, topos)
    t_tr, y_tr = almanac.find_discrete(t0, t1, f_transit)
    transit_dt = None
    for tt, yy in zip(t_tr, y_tr):
        if int(yy) == 1:  # 1 = upper transit (tengah hari matahari, bukan tengah malam)
            dt = ke_utc_datetime(tt)
            if (dt + timedelta(hours=zona_offset_jam)).date() == tanggal.date():
                transit_dt = dt
                break
    if transit_dt is None:
        return hasil_kosong

    def _lintas(target_alt_deg):
        def f(t):
            alt, _, _ = observer.at(t).observe(sun).apparent().altaz()
            return alt.degrees >= target_alt_deg
        f.step_days = 2.0 / 1440.0  # resolusi pencarian ~2 menit, cukup halus utk gerak Matahari
        return almanac.find_discrete(t0, t1, f)

    def _waktu_jam(target_alt_deg, ingin_naik, sebelum_acuan):
        t_arr, y_arr = _lintas(target_alt_deg)
        dt = _pilih_lintasan_terdekat(t_arr, y_arr, ingin_naik, transit_dt, sebelum_acuan)
        if dt is None:
            return None
        lokal = dt + timedelta(hours=zona_offset_jam)
        return lokal.hour + lokal.minute / 60.0 + lokal.second / 3600.0

    dip_derajat = 0.0347 * np.sqrt(max(elevasi_m, 0.0))  # koreksi kerendahan ufuk
    alt_terbit_terbenam = -0.833 - dip_derajat

    # Deklinasi geosentris Matahari saat transit, dipakai utk altitude Ashar
    # (rumus bayangan Meeus/hisab klasik -- sama seperti mode Ringan).
    dec_deg = float(eph["earth"].at(ts.from_datetime(transit_dt)).observe(sun).apparent().radec(epoch='date')[1].degrees)
    faktor_bayangan = 2.0 if mazhab_ashar == "hanafi" else 1.0
    alt_ashar = float(np.degrees(np.arctan(
        1.0 / (faktor_bayangan + np.tan(np.radians(abs(lat_deg - dec_deg)))))))

    hasil = {}
    hasil["subuh"] = _waktu_jam(sudut_fajar, True, True)          # naik, pagi, sebelum transit
    hasil["terbit"] = _waktu_jam(alt_terbit_terbenam, True, True)  # naik, pagi, sebelum transit
    hasil["dhuha"] = None if hasil["terbit"] is None else hasil["terbit"] + (20.0 / 60.0)

    transit_lokal = transit_dt + timedelta(hours=zona_offset_jam)
    hasil["dzuhur"] = transit_lokal.hour + transit_lokal.minute / 60.0 + transit_lokal.second / 3600.0

    hasil["ashar"] = _waktu_jam(alt_ashar, False, False)             # turun, sore, sesudah transit
    hasil["maghrib"] = _waktu_jam(alt_terbit_terbenam, False, False)  # turun, sore, sesudah transit
    hasil["isya"] = _waktu_jam(sudut_isya, False, False)             # turun, malam, sesudah transit
    hasil["imsak"] = None if hasil["subuh"] is None else hasil["subuh"] - imsak_sebelum_fajr_menit / 60.0

    for kunci in ("subuh", "terbit", "dzuhur", "ashar", "maghrib", "isya"):
        if hasil[kunci] is not None:
            hasil[kunci] = hasil[kunci] + ihtiyat_menit / 60.0

    # Hitung Waktu Kiblat (Vincenty & Spherical)
    az_spherical, _ = qibla_spherical(lat_deg, lon_deg)
    az_vincenty, _ = qibla_vincenty(lat_deg, lon_deg)

    def _kiblat_time(az):
        phi = np.radians(lat_deg)
        decl = np.radians(dec_deg)
        a = 1.0 / np.tan(np.radians(az))
        b = -np.sin(phi)
        c = -np.cos(phi) * np.tan(decl)
        R = np.sqrt(a*a + b*b)
        if R == 0:
            return None
        ratio = c / R
        if abs(ratio) > 1.0:
            return None
        theta = np.arctan2(b, a)
        H_rad = np.pi / 2.0 + np.arccos(ratio) - theta
        H_rad = (H_rad + np.pi) % (2.0 * np.pi) - np.pi
        H_deg = np.degrees(H_rad)
        transit_utc = transit_dt.hour + transit_dt.minute / 60.0 + transit_dt.second / 3600.0
        return (transit_utc + H_deg / 15.0 + zona_offset_jam) % 24.0

    hasil["kiblat_v"] = _kiblat_time(az_vincenty)
    hasil["kiblat_s"] = _kiblat_time(az_spherical)

    return hasil


def hitung_waktu_sholat_otomatis(tanggal, lat_deg, lon_deg, zona_offset_jam, mode="ringan",
                                  ts=None, eph=None, **kwargs):
    """Dispatcher: pilih metode hisab sesuai mode.
    mode='ringan' -> VSOP87 ringkas + rumus Equation of Time (hitung_waktu_sholat,
                      tanpa file eksternal, cocok dipakai di semua kondisi).
    mode='jpl'    -> Skyfield + ephemeris JPL DE421 (hitung_waktu_sholat_skyfield,
                      lebih presisi, tapi butuh ts & eph sudah dimuat).
    Kalau mode='jpl' diminta tapi ts/eph belum siap, otomatis jatuh balik
    ke mode Ringan supaya pemanggil tidak perlu menangani error terpisah."""
    if mode == "jpl" and ts is not None and eph is not None:
        return hitung_waktu_sholat_skyfield(tanggal, lat_deg, lon_deg, zona_offset_jam,
                                             ts, eph, **kwargs)
    return hitung_waktu_sholat(tanggal, lat_deg, lon_deg, zona_offset_jam, **kwargs)


def _label_jam_dari_desimal(jam_desimal):
    """'HH:MM' dari jam desimal (0-24), dibulatkan ke menit terdekat."""
    jl = jam_desimal % 24.0
    hh = int(jl)
    mm = int(round((jl - hh) * 60))
    if mm == 60:
        mm = 0
        hh = (hh + 1) % 24
    return f"{hh:02d}:{mm:02d}"


def _cari_rts_dari_sampel(jam_lokal_arr, alt_arr, ambang_deg=-0.8333):
    """Dari sampel altitude (apparent, sudah terefraksi) sepanjang satu hari
    -- jam_lokal_arr (jam desimal 0..24) & alt_arr (derajat, boleh berisi
    None untuk titik yang gagal dibaca) -- cari waktu TERBIT (naik lewat
    ambang), TRANSIT (kulminasi atas / altitude maksimum), & TERBENAM
    (turun lewat ambang), semua dalam jam desimal LOKAL. Dipakai oleh mode
    Ringan & Online (Horizons); mode Presisi/JPL pakai fungsi almanac
    bawaan Skyfield sendiri (lihat _hitung_rts_jpl), yang tidak butuh
    sampling manual.

    ambang_deg default -0.8333 derajat: konvensi yang sama dipakai di
    seluruh HisabWin untuk ambang terbit/terbenam Matahari (refraksi +
    semi-diameter standar, lihat SUDUT_TERBIT_TERBENAM/hitung_waktu_sholat)
    -- dipakai juga untuk Bulan di sini sebagai pendekatan yang cukup
    akurat untuk kebutuhan tabel (selisih semi-diameter & parallax Bulan
    terhadap ambang Matahari umumnya < 1 menit busur, di bawah presisi
    interpolasi linear antar sampel).

    Rise/set dicari lewat interpolasi LINEAR antara dua sampel yang
    mengapit perlintasan ambang (hanya perlintasan PERTAMA yang diambil,
    cukup untuk kasus lintang rendah/menengah tanpa hari kutub). Transit
    dicari dari sampel altitude tertinggi, lalu diperhalus lewat fit
    parabola 3-titik di sekitarnya. Return dict {"terbit":.., "transit":..,
    "terbenam":..} (nilai None kalau ambang tidak pernah dilintasi, mis.
    Bulan yang sedang circumpolar/tidak pernah terbit di hari itu)."""
    n = len(alt_arr)
    terbit = None
    terbenam = None
    for i in range(n - 1):
        a1, a2 = alt_arr[i], alt_arr[i + 1]
        if a1 is None or a2 is None:
            continue
        if terbit is None and a1 < ambang_deg <= a2:
            frac = (ambang_deg - a1) / (a2 - a1)
            terbit = jam_lokal_arr[i] + frac * (jam_lokal_arr[i + 1] - jam_lokal_arr[i])
        if terbenam is None and a1 >= ambang_deg > a2:
            frac = (a1 - ambang_deg) / (a1 - a2)
            terbenam = jam_lokal_arr[i] + frac * (jam_lokal_arr[i + 1] - jam_lokal_arr[i])

    alt_valid = [(-999.0 if a is None else a) for a in alt_arr]
    idx_max = int(np.argmax(alt_valid))
    transit = float(jam_lokal_arr[idx_max])
    if 0 < idx_max < n - 1 and alt_valid[idx_max] > -999.0:
        y0, y1, y2 = alt_valid[idx_max - 1], alt_valid[idx_max], alt_valid[idx_max + 1]
        if y0 > -999.0 and y2 > -999.0:
            denom = (y0 - 2 * y1 + y2)
            if denom != 0:
                delta = 0.5 * (y0 - y2) / denom
                langkah = jam_lokal_arr[idx_max + 1] - jam_lokal_arr[idx_max]
                transit = float(jam_lokal_arr[idx_max] + delta * langkah)

    return {"terbit": terbit, "transit": transit, "terbenam": terbenam}


def _hitung_rts_ringan(tanggal, lat_deg, lon_deg, zona_offset_jam, elevasi_m=0.0):
    """RTS (terbit/transit/terbenam) Matahari & Bulan mode RINGAN.

    Matahari: pakai formula sudut-jam TERTUTUP (_sudut_jam_matahari, lewat
    hitung_waktu_sholat) -- deklinasi Matahari nyaris konstan sepanjang
    hari jadi tidak perlu iterasi/sampling, presisinya identik dengan
    waktu terbit/dzuhur/maghrib yang sudah dipakai di tab Waktu Sholat.

    Bulan: deklinasi & parallax berubah cukup cepat sepanjang hari (beda
    dengan Matahari), jadi TIDAK dipakai formula tertutup -- disampling
    tiap ~2 menit (721 titik, vektor numpy, BUKAN loop Python) sepanjang
    hari lalu dicari lewat _cari_rts_dari_sampel(), pola yang sama dengan
    hitung_tabel_efemeris_ringan()."""
    sholat = hitung_waktu_sholat(tanggal, lat_deg, lon_deg, zona_offset_jam,
                                  elevasi_m=elevasi_m, ihtiyat_menit=0.0)
    matahari = {"terbit": sholat["terbit"], "transit": sholat["dzuhur"], "terbenam": sholat["maghrib"]}

    n = 721
    jam_lokal = np.linspace(0.0, 24.0, n)
    jam_utc = jam_lokal - zona_offset_jam
    tahun_a = np.full(jam_utc.shape, tanggal.year, dtype=float)
    bulan_a = np.full(jam_utc.shape, tanggal.month, dtype=float)
    hari_a = tanggal.day + jam_utc / 24.0

    jd_ut = julian_day(tahun_a, bulan_a, hari_a)
    dt_hari = delta_t_detik(tanggal.year, tanggal.month) / 86400.0
    T = (jd_ut + dt_hari - 2451545.0) / 36525.0

    ra_m, dec_m, _, _, _, par_m = posisi_bulan(T)
    dpsi, deps = nutasi_singkat(T)
    eps = (23 + 26 / 60 + 21.448 / 3600 - (46.8150 * T) / 3600) + deps
    gast = gast_derajat(jd_ut, T, dpsi, eps)
    lst = (gast + lon_deg) % 360
    H_moon = ((lst - ra_m + 180) % 360) - 180

    alt_moon_geo = altitude_geosentris(lat_deg, dec_m, H_moon)
    alt_moon_true = altitude_topocentris_bulan(alt_moon_geo, par_m)
    alt_moon_app = alt_moon_true + koreksi_refraksi(alt_moon_true)

    bulan = _cari_rts_dari_sampel(list(jam_lokal), list(alt_moon_app))
    return {"matahari": matahari, "bulan": bulan}


def _jam_lokal_dari_time_jika_hari_ini(t, zona_offset_jam, tanggal_lokal):
    """Konversi waktu Skyfield/datetime UTC 't' ke jam desimal LOKAL, TAPI
    hanya kalau tanggal lokalnya sama dengan tanggal_lokal -- kalau jatuh
    di hari sebelum/sesudahnya (bisa terjadi karena jendela pencarian
    _hitung_rts_jpl() sengaja dilebarkan), return None supaya tidak salah
    ambil kejadian hari lain."""
    dt_utc = ke_utc_datetime(t)
    dt_lokal = dt_utc + timedelta(hours=zona_offset_jam)
    if dt_lokal.date() != tanggal_lokal.date():
        return None
    return dt_lokal.hour + dt_lokal.minute / 60.0 + dt_lokal.second / 3600.0


def _hitung_rts_jpl(tanggal, lat_deg, lon_deg, zona_offset_jam, ts, eph, elevasi_m=0.0):
    """RTS mode Presisi: pakai almanac.find_risings/find_settings/
    find_transits BAWAAN Skyfield (horizon_degrees dibiarkan default/None
    -> Skyfield otomatis pakai ambang standar KHUSUS PER TARGET, refraksi +
    jari-jari piringan sudah diperhitungkan sendiri berdasarkan jarak
    aktual target -- lebih presisi daripada ambang tunggal -0.8333 yang
    dipakai mode Ringan/Online, dan tidak butuh sampling manual sama
    sekali). Jendela pencarian dilebarkan +-6 jam dari batas hari lokal
    supaya kejadian yang jatuh persis di sekitar tengah malam lokal tetap
    tertangkap, lalu difilter balik ke tanggal lokal yang diminta."""
    observer, _ = _observer_skyfield(eph, lat_deg, lon_deg, elevasi_m)
    sun = eph["sun"]
    moon = eph["moon"]

    awal_lokal = datetime(tanggal.year, tanggal.month, tanggal.day) - timedelta(hours=6)
    akhir_lokal = awal_lokal + timedelta(hours=36)
    awal_utc = awal_lokal - timedelta(hours=zona_offset_jam)
    akhir_utc = akhir_lokal - timedelta(hours=zona_offset_jam)
    t0 = ts.utc(awal_utc.year, awal_utc.month, awal_utc.day, awal_utc.hour, awal_utc.minute, awal_utc.second)
    t1 = ts.utc(akhir_utc.year, akhir_utc.month, akhir_utc.day, akhir_utc.hour, akhir_utc.minute, akhir_utc.second)

    def _pilih_hari_ini(t_arr):
        for t in t_arr:
            jam = _jam_lokal_dari_time_jika_hari_ini(t, zona_offset_jam, tanggal)
            if jam is not None:
                return jam
        return None

    def _proses(target):
        t_naik, _ = almanac.find_risings(observer, target, t0, t1)
        t_turun, _ = almanac.find_settings(observer, target, t0, t1)
        t_transit = almanac.find_transits(observer, target, t0, t1)
        return {
            "terbit": _pilih_hari_ini(t_naik),
            "transit": _pilih_hari_ini(t_transit),
            "terbenam": _pilih_hari_ini(t_turun),
        }

    return {"matahari": _proses(sun), "bulan": _proses(moon)}


def _hitung_rts_horizons(tanggal, lat_deg, lon_deg, zona_offset_jam, elevasi_m=0.0,
                          interval_menit_rts=5):
    """RTS mode Online (JPL Horizons): sampling altitude apparent
    (REFRACTED, lewat _minta_horizons) tiap `interval_menit_rts` menit
    (default 5 menit -- SENGAJA lebih halus dari interval tabel utama yang
    dipilih user, supaya waktu naik/transit/turun cukup presisi walau
    tabelnya sendiri mis. per jam) sepanjang hari, lalu dicari lewat
    _cari_rts_dari_sampel() -- pola sama dengan mode Ringan, supaya ketiga
    mode sebanding. WAJIB koneksi internet: 2 request HTTP TAMBAHAN (di
    luar 2 request tabel utama), satu per benda langit."""
    n_langkah = int(round(24.0 * 60.0 / interval_menit_rts))
    jam_lokal = np.linspace(0.0, 24.0, n_langkah + 1)
    waktu_mulai_utc = datetime(tanggal.year, tanggal.month, tanggal.day) - timedelta(hours=zona_offset_jam)
    waktu_akhir_utc = waktu_mulai_utc + timedelta(hours=24)

    try:
        import requests
        teks_sun = _minta_horizons("10", lat_deg, lon_deg, elevasi_m,
                                    waktu_mulai_utc, waktu_akhir_utc, interval_menit_rts, "4")
        teks_moon = _minta_horizons("301", lat_deg, lon_deg, elevasi_m,
                                     waktu_mulai_utc, waktu_akhir_utc, interval_menit_rts, "4")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            "Gagal menghubungi JPL Horizons (ssd.jpl.nasa.gov) saat mencari waktu "
            f"terbit/transit/terbenam. Detail teknis: {e}") from e

    baris_sun = _parse_baris_csv_horizons(teks_sun)
    baris_moon = _parse_baris_csv_horizons(teks_moon)

    def _alt_arr(baris):
        # QUANTITIES='4' -> HANYA azimuth & elevation apparent, jadi SELALU
        # 2 kolom TERAKHIR tiap baris. JANGAN gabung dgn kuantitas lain (mis.
        # '20' utk jarak) di sini -- kalau ada kuantitas lain SETELAH '4',
        # b[-2:] akan ambil kolom kuantitas itu (mis. delta/deldot), BUKAN
        # az/el, dan altitude yang dipakai pencarian RTS jadi salah total
        # (nilainya bukan derajat altitude sama sekali) -- ini bug yang
        # sempat kejadian saat masih pakai QUANTITIES='4,20'.
        hasil = []
        for b in baris:
            try:
                _az, el = (float(x) for x in b[-2:])
            except (ValueError, IndexError):
                el = None
            hasil.append(el)
        return hasil

    return {
        "matahari": _cari_rts_dari_sampel(list(jam_lokal), _alt_arr(baris_sun)),
        "bulan": _cari_rts_dari_sampel(list(jam_lokal), _alt_arr(baris_moon)),
    }


def hitung_rts(tanggal, lat_deg, lon_deg, zona_offset_jam, mode="ringan",
                ts=None, eph=None, elevasi_m=0.0):
    """Dispatcher RTS (terbit/transit/terbenam Matahari & Bulan): pola sama
    persis seperti hitung_tabel_efemeris() -- pilih implementasi sesuai
    mode, mode='jpl' tapi ts/eph belum siap jatuh balik ke mode Ringan,
    mode='horizons' yang gagal (mis. tidak ada internet) dibiarkan naik apa
    adanya ke pemanggil. Return dict {"matahari": {...}, "bulan": {...}},
    tiap isinya {"terbit":.., "transit":.., "terbenam":..} dalam jam
    desimal LOKAL (atau None kalau tidak melintasi ambang hari itu)."""
    if mode == "horizons":
        return _hitung_rts_horizons(tanggal, lat_deg, lon_deg, zona_offset_jam, elevasi_m=elevasi_m)
    if mode == "jpl" and ts is not None and eph is not None:
        return _hitung_rts_jpl(tanggal, lat_deg, lon_deg, zona_offset_jam, ts, eph, elevasi_m=elevasi_m)
    return _hitung_rts_ringan(tanggal, lat_deg, lon_deg, zona_offset_jam, elevasi_m=elevasi_m)


def hitung_tabel_efemeris_ringan(tanggal, lat_deg, lon_deg, zona_offset_jam,
                                  interval_menit=60, elevasi_m=0.0):
    """Tabel efemeris (posisi Matahari & Bulan) mode RINGAN (VSOP87 +
    ELP2000-82B, tanpa file eksternal), tiap `interval_menit` menit
    sepanjang satu hari waktu setempat (00:00 s.d. 24:00, inklusif kedua
    ujung). Dipakai untuk tab "Tabel Efemeris" di GUI.

    Altitude yang dilaporkan sudah APPARENT (refraksi atmosfer standar
    ditambahkan; khusus Bulan, paralaks juga sudah dikoreksi) -- posisi
    yang benar-benar terlihat pengamat, sama pendekatannya dengan
    alt_moon_topo di _altaz_matahari_bulan().

    Return: list of dict, satu per baris waktu, dengan kunci:
      jam_lokal (jam desimal), label_jam ('HH:MM'),
      az_matahari, alt_matahari, dec_matahari (derajat),
      az_bulan, alt_bulan, dec_bulan (derajat), jarak_bulan_km,
      elongasi_deg, fraksi_iluminasi_persen.
    """
    n_langkah = int(round(24.0 * 60.0 / interval_menit))
    jam_lokal = np.linspace(0.0, 24.0, n_langkah + 1)
    jam_utc = jam_lokal - zona_offset_jam

    tahun_a = np.full(jam_utc.shape, tanggal.year, dtype=float)
    bulan_a = np.full(jam_utc.shape, tanggal.month, dtype=float)
    hari_a = tanggal.day + jam_utc / 24.0

    jd_ut = julian_day(tahun_a, bulan_a, hari_a)
    dt_hari = delta_t_detik(tanggal.year, tanggal.month) / 86400.0
    T = (jd_ut + dt_hari - 2451545.0) / 36525.0

    ra_s, dec_s, _, _ = posisi_matahari(T)
    ra_m, dec_m, _, _, jarak_m, par_m = posisi_bulan(T)
    dpsi, deps = nutasi_singkat(T)
    eps = (23 + 26 / 60 + 21.448 / 3600 - (46.8150 * T) / 3600) + deps

    gast = gast_derajat(jd_ut, T, dpsi, eps)
    lst = (gast + lon_deg) % 360

    H_sun = ((lst - ra_s + 180) % 360) - 180
    H_moon = ((lst - ra_m + 180) % 360) - 180

    def _az_alt_geo(dec_deg, H_deg):
        """Azimuth (dari Utara, searah jarum jam) & altitude geosentris."""
        lat_r = np.radians(lat_deg)
        dec_r, H_r = np.radians(dec_deg), np.radians(H_deg)
        alt = altitude_geosentris(lat_deg, dec_deg, H_deg)
        alt_r = np.radians(alt)
        sin_az = -np.sin(H_r) * np.cos(dec_r) / np.cos(alt_r)
        cos_az = (np.sin(dec_r) - np.sin(alt_r) * np.sin(lat_r)) / (np.cos(alt_r) * np.cos(lat_r))
        az = np.degrees(np.arctan2(sin_az, cos_az)) % 360
        return az, alt

    az_sun, alt_sun_geo = _az_alt_geo(dec_s, H_sun)
    az_moon, alt_moon_geo = _az_alt_geo(dec_m, H_moon)

    alt_sun_app = alt_sun_geo + koreksi_refraksi(alt_sun_geo)
    alt_moon_true = altitude_topocentris_bulan(alt_moon_geo, par_m)
    alt_moon_app = alt_moon_true + koreksi_refraksi(alt_moon_true)

    cos_elong = (np.sin(np.radians(dec_s)) * np.sin(np.radians(dec_m))
                 + np.cos(np.radians(dec_s)) * np.cos(np.radians(dec_m))
                 * np.cos(np.radians(ra_s - ra_m)))
    elong = np.degrees(np.arccos(np.clip(cos_elong, -1.0, 1.0)))
    # Perkiraan fraksi iluminasi piringan Bulan dari elongasi geosentris
    # (fase Bulan): 0% saat konjungsi (elongasi 0), 100% saat purnama
    # (elongasi 180) -- pendekatan standar yang mengabaikan sedikit selisih
    # antara elongasi & sudut fase akibat jarak Bumi-Bulan-Matahari
    # terhingga (< 0.2% di seluruh siklus, cukup akurat untuk tabel ini).
    fraksi_iluminasi = (1.0 - np.cos(np.radians(elong))) / 2.0 * 100.0

    hasil = []
    for i in range(len(jam_lokal)):
        hasil.append({
            "jam_lokal": float(jam_lokal[i]),
            "label_jam": _label_jam_dari_desimal(jam_lokal[i]),
            "az_matahari": float(az_sun[i]), "alt_matahari": float(alt_sun_app[i]),
            "dec_matahari": float(dec_s[i]),
            "az_bulan": float(az_moon[i]), "alt_bulan": float(alt_moon_app[i]),
            "dec_bulan": float(dec_m[i]), "jarak_bulan_km": float(jarak_m[i]),
            "elongasi_deg": float(elong[i]),
            "fraksi_iluminasi_persen": float(fraksi_iluminasi[i]),
        })
    return hasil


def hitung_tabel_efemeris_jpl(tanggal, lat_deg, lon_deg, zona_offset_jam, ts, eph,
                               interval_menit=60, elevasi_m=0.0):
    """Versi presisi tinggi dari hitung_tabel_efemeris_ringan(): posisi
    Matahari & Bulan topocentric (azimuth/altitude apparent) dihitung
    langsung dari ephemeris JPL DE421 via Skyfield, dan fraksi iluminasi
    Bulan memakai almanac.fraction_illuminated() (bukan pendekatan
    elongasi geosentris). Struktur hasil (list of dict) sama persis
    dengan versi Ringan, supaya kedua mode bisa dipakai bergantian oleh
    kode pemanggil (GUI) tanpa perubahan lain."""
    n_langkah = int(round(24.0 * 60.0 / interval_menit))
    jam_lokal = np.linspace(0.0, 24.0, n_langkah + 1)
    jam_utc = jam_lokal - zona_offset_jam

    observer, _ = _observer_skyfield(eph, lat_deg, lon_deg, elevasi_m)
    earth = eph["earth"]
    sun = eph["sun"]
    moon = eph["moon"]

    t = ts.utc(tanggal.year, tanggal.month, tanggal.day, jam_utc)

    # Refraksi atmosfer standar (10°C, 1010 mbar) WAJIB diisi eksplisit di
    # sini -- tanpa temperature_C/pressure_mbar, altaz() Skyfield cuma
    # mengembalikan altitude geometris (belum terefraksi), TIDAK konsisten
    # dengan hitung_tabel_efemeris_ringan() (yang eksplisit menambahkan
    # koreksi_refraksi() ke alt_matahari & alt_bulan) maupun
    # hitung_tabel_efemeris_horizons() (yang minta APPARENT=REFRACTED ke
    # JPL Horizons) -- lihat juga pola yang sama di hitung_grid_jpl().
    alt_sun, az_sun, _ = observer.at(t).observe(sun).apparent().altaz(
        temperature_C=10.0, pressure_mbar=1010.0)
    alt_moon, az_moon, jarak_moon = observer.at(t).observe(moon).apparent().altaz(
        temperature_C=10.0, pressure_mbar=1010.0)

    # Deklinasi & elongasi dihitung dari posisi GEOSENTRIS (diamati dari
    # pusat Bumi, bukan dari lokasi pengamat) -- konsisten dengan makna
    # "deklinasi" & "elongasi" di bagian lain aplikasi ini (mis.
    # _altaz_matahari_bulan), yang tidak bergantung pada lokasi pengamat.
    pos_sun_geo = earth.at(t).observe(sun).apparent()
    pos_moon_geo = earth.at(t).observe(moon).apparent()
    _, dec_sun, _ = pos_sun_geo.radec(epoch='date')
    _, dec_moon, _ = pos_moon_geo.radec(epoch='date')
    elong = pos_sun_geo.separation_from(pos_moon_geo)

    fraksi_iluminasi = almanac.fraction_illuminated(eph, "moon", t) * 100.0

    hasil = []
    for i in range(len(jam_lokal)):
        hasil.append({
            "jam_lokal": float(jam_lokal[i]),
            "label_jam": _label_jam_dari_desimal(jam_lokal[i]),
            "az_matahari": float(az_sun.degrees[i]), "alt_matahari": float(alt_sun.degrees[i]),
            "dec_matahari": float(dec_sun.degrees[i]),
            "az_bulan": float(az_moon.degrees[i]), "alt_bulan": float(alt_moon.degrees[i]),
            "dec_bulan": float(dec_moon.degrees[i]), "jarak_bulan_km": float(jarak_moon.km[i]),
            "elongasi_deg": float(elong.degrees[i]),
            "fraksi_iluminasi_persen": float(fraksi_iluminasi[i]),
        })
    return hasil


HORIZONS_API_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
HORIZONS_TIMEOUT_DETIK = 25
HORIZONS_AU_KE_KM = 149597870.7


def _minta_horizons(command, lat_deg, lon_deg, elevasi_m, waktu_mulai_utc, waktu_akhir_utc,
                     interval_menit, quantities):
    """Satu kali panggilan JPL Horizons API (Observer ephemeris, TOPOSENTRIK
    persis di lat/lon/elevasi yang diberikan lewat SITE_COORD) -- dipakai
    oleh hitung_tabel_efemeris_horizons(). Mengembalikan teks respons mentah
    (format CSV, lihat _parse_baris_csv_horizons). WAJIB koneksi internet;
    lempar requests.exceptions.RequestException kalau gagal (timeout, DNS,
    tidak ada internet, dll) -- ditangkap & dijelaskan ulang oleh pemanggil."""
    try:
        import requests
    except ImportError as e:
        raise RuntimeError(
            "Mode Online (JPL Horizons API) butuh paket Python 'requests' yang "
            "belum terpasang. Pasang dulu dengan: pip install requests") from e

    params = {
        "format": "text",
        "COMMAND": f"'{command}'",
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "OBSERVER",
        "CENTER": "'coord@399'",
        "COORD_TYPE": "GEODETIC",
        # SITE_COORD = 'bujur,lintang,elevasi(km)' -- bujur timur positif,
        # sama konvensinya dengan lon_deg yang dipakai di seluruh HisabWin.
        "SITE_COORD": f"'{lon_deg:.6f},{lat_deg:.6f},{elevasi_m / 1000.0:.4f}'",
        "START_TIME": f"'{waktu_mulai_utc.strftime('%Y-%m-%d %H:%M')}'",
        "STOP_TIME": f"'{waktu_akhir_utc.strftime('%Y-%m-%d %H:%M')}'",
        "STEP_SIZE": f"'{interval_menit}m'",
        "QUANTITIES": f"'{quantities}'",
        "CSV_FORMAT": "YES",
        "ANG_FORMAT": "DEG",
        # REFRACTED supaya altitude yang dikembalikan JPL sudah termasuk
        # koreksi refraksi atmosfer standar -- setara "apparent" di dua
        # mode lain (Ringan & Presisi lokal), jadi tiga mode bisa
        # dibandingkan apel-ke-apel.
        "APPARENT": "REFRACTED",
        "EXTRA_PREC": "YES",
    }
    resp = requests.get(HORIZONS_API_URL, params=params, timeout=HORIZONS_TIMEOUT_DETIK)
    resp.raise_for_status()
    return resp.text


def _parse_baris_csv_horizons(teks):
    """Ambil baris data di antara penanda '$$SOE'/'$$EOE' dari respons teks
    JPL Horizons (CSV_FORMAT=YES), dikembalikan sebagai list of list-of-str
    (tiap baris sudah displit koma & di-strip, kolom kosong di ujung -- sisa
    trailing comma bawaan Horizons -- dibuang)."""
    if "$$SOE" not in teks or "$$EOE" not in teks:
        raise RuntimeError(
            "Format respons JPL Horizons tidak dikenali (penanda data "
            "'$$SOE'/'$$EOE' tidak ditemukan). Kemungkinan parameter query "
            "keliru atau ada pesan error dari server. Cuplikan respons:\n"
            + teks[:300])
    blok = teks.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    baris_hasil = []
    for baris in blok.strip().splitlines():
        if not baris.strip():
            continue
        kolom = [k.strip() for k in baris.split(",")]
        while kolom and kolom[-1] == "":
            kolom.pop()
        baris_hasil.append(kolom)
    return baris_hasil


def hitung_tabel_efemeris_horizons(tanggal, lat_deg, lon_deg, zona_offset_jam,
                                    interval_menit=60, elevasi_m=0.0):
    """Versi ONLINE dari tabel efemeris: RA/DEC & azimuth/altitude apparent
    (topocentric, refraksi standar sudah termasuk) Matahari & Bulan diambil
    langsung dari JPL Horizons System (SSD/JPL, NASA) lewat API publiknya --
    https://ssd.jpl.nasa.gov/api/horizons.api -- BUKAN dihitung sendiri oleh
    HisabWin. WAJIB ADA KONEKSI INTERNET: tiap kali dipanggil, fungsi ini
    mengirim 2 request HTTP (satu untuk Matahari, satu untuk Bulan) ke
    server JPL, dan akan gagal kalau tidak ada internet atau server sedang
    tidak bisa dihubungi.

    Elongasi & fraksi iluminasi Bulan TETAP dihitung sendiri secara
    geometris dari RA/DEC yang didapat dari Horizons (formula identik
    dengan mode Ringan/Presisi) -- bukan diambil dari kolom elongasi bawaan
    Horizons, supaya definisi "elongasi"/"iluminasi" konsisten di ketiga
    mode, dan supaya parsing tidak bergantung pada kolom gabungan
    angka+huruf penanda (leading/trailing) yang formatnya kurang baku untuk
    diandalkan sebagai kontrak API jangka panjang.

    Struktur hasil (list of dict) sama persis dengan dua mode lain, supaya
    ketiganya bisa dipakai bergantian oleh GUI tanpa perubahan lain.
    """
    n_langkah = int(round(24.0 * 60.0 / interval_menit))
    jam_lokal = np.linspace(0.0, 24.0, n_langkah + 1)

    waktu_mulai_utc = datetime(tanggal.year, tanggal.month, tanggal.day) - timedelta(hours=zona_offset_jam)
    waktu_akhir_utc = waktu_mulai_utc + timedelta(hours=24)

    # QUANTITIES='2,4,20' -> RA apparent, DEC apparent, Azimuth, Elevation
    # apparent, jarak (delta, dalam AU) & laju jaraknya (deldot) -- lima
    # kuantitas ini kolom CSV-nya murni numerik & posisinya baku (SELALU
    # jadi 6 kolom TERAKHIR tiap baris, apapun kolom lain -- mis. penanda
    # siang/malam & bulan naik/turun -- yang disisipkan Horizons di depan).
    # Karena itu diambil dgn indexing negatif (baris[-6:]), bukan dari awal.
    try:
        import requests
        teks_sun = _minta_horizons("10", lat_deg, lon_deg, elevasi_m,
                                    waktu_mulai_utc, waktu_akhir_utc, interval_menit, "2,4,20")
        teks_moon = _minta_horizons("301", lat_deg, lon_deg, elevasi_m,
                                     waktu_mulai_utc, waktu_akhir_utc, interval_menit, "2,4,20")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            "Gagal menghubungi JPL Horizons (ssd.jpl.nasa.gov). Pastikan "
            f"komputer ini terhubung ke internet. Detail teknis: {e}") from e

    baris_sun = _parse_baris_csv_horizons(teks_sun)
    baris_moon = _parse_baris_csv_horizons(teks_moon)

    if len(baris_sun) != n_langkah + 1 or len(baris_moon) != n_langkah + 1:
        raise RuntimeError(
            "Jumlah baris data dari JPL Horizons tidak sesuai perkiraan "
            f"(Matahari: {len(baris_sun)} baris, Bulan: {len(baris_moon)} baris, "
            f"diharapkan {n_langkah + 1} baris). Coba ulangi lagi, atau pakai "
            "interval waktu yang lain.")

    def _ambil_kolom_numerik(baris, label):
        try:
            ra, dec, az, el, delta_au, _deldot = (float(x) for x in baris[-6:])
        except (ValueError, IndexError) as e:
            raise RuntimeError(
                f"Gagal membaca baris data {label} dari JPL Horizons -- format "
                f"respons tidak sesuai dugaan. Baris mentah: {baris}") from e
        return ra, dec, az, el, delta_au

    hasil = []
    for i in range(len(jam_lokal)):
        ra_s, dec_s, az_s, alt_s, _ = _ambil_kolom_numerik(baris_sun[i], "Matahari")
        ra_m, dec_m, az_m, alt_m, delta_au_m = _ambil_kolom_numerik(baris_moon[i], "Bulan")
        jarak_m_km = delta_au_m * HORIZONS_AU_KE_KM

        cos_elong = (np.sin(np.radians(dec_s)) * np.sin(np.radians(dec_m))
                     + np.cos(np.radians(dec_s)) * np.cos(np.radians(dec_m))
                     * np.cos(np.radians(ra_s - ra_m)))
        elong = float(np.degrees(np.arccos(np.clip(cos_elong, -1.0, 1.0))))
        fraksi_iluminasi = (1.0 - np.cos(np.radians(elong))) / 2.0 * 100.0

        hasil.append({
            "jam_lokal": float(jam_lokal[i]),
            "label_jam": _label_jam_dari_desimal(jam_lokal[i]),
            "az_matahari": az_s, "alt_matahari": alt_s, "dec_matahari": dec_s,
            "az_bulan": az_m, "alt_bulan": alt_m, "dec_bulan": dec_m,
            "jarak_bulan_km": jarak_m_km,
            "elongasi_deg": elong,
            "fraksi_iluminasi_persen": float(fraksi_iluminasi),
        })
    return hasil


def hitung_tabel_efemeris(tanggal, lat_deg, lon_deg, zona_offset_jam, mode="ringan",
                           ts=None, eph=None, interval_menit=60, elevasi_m=0.0):
    """Dispatcher tabel efemeris: pilih sumber data sesuai mode, pola sama
    seperti hitung_waktu_sholat_otomatis(). mode='jpl' tapi ts/eph belum
    siap otomatis jatuh balik ke mode Ringan. mode='horizons' memanggil
    JPL Horizons API online (lihat hitung_tabel_efemeris_horizons) -- kalau
    gagal (mis. tidak ada internet), exception-nya dibiarkan naik apa
    adanya ke pemanggil (bukan jatuh balik diam-diam ke mode offline),
    supaya pengguna tahu datanya BUKAN dari JPL seperti yang diminta."""
    if mode == "horizons":
        return hitung_tabel_efemeris_horizons(tanggal, lat_deg, lon_deg, zona_offset_jam,
                                               interval_menit=interval_menit, elevasi_m=elevasi_m)
    if mode == "jpl" and ts is not None and eph is not None:
        return hitung_tabel_efemeris_jpl(tanggal, lat_deg, lon_deg, zona_offset_jam, ts, eph,
                                          interval_menit=interval_menit, elevasi_m=elevasi_m)
    return hitung_tabel_efemeris_ringan(tanggal, lat_deg, lon_deg, zona_offset_jam,
                                         interval_menit=interval_menit, elevasi_m=elevasi_m)


def hitung_jadwal_sholat_bulan_jpl_vectorized(tahun, bulan, lat_deg, lon_deg, zona_offset_jam, ts, eph,
                                               elevasi_m=0.0, sudut_fajar=-20.0, sudut_isya=-18.0,
                                               ihtiyat_menit=2.0, imsak_sebelum_fajr_menit=10.0,
                                               mazhab_ashar="syafii"):
    """Hitung jadwal sholat untuk satu bulan penuh menggunakan metode vectorized
    di mode Presisi (Skyfield + JPL DE421), melompati pencarian iteratif find_discrete."""
    jumlah_hari = calendar.monthrange(tahun, bulan)[1]
    hari_arr = np.arange(1, jumlah_hari + 1)

    observer, topos = _observer_skyfield(eph, lat_deg, lon_deg, elevasi_m)
    sun = eph["sun"]
    earth = eph["earth"]

    # 1. Taksiran transit awal (UTC jam)
    t_noon_hours = 12.0 - lon_deg / 15.0
    t_noon = ts.utc(tahun, bulan, hari_arr, t_noon_hours)

    pos_noon = earth.at(t_noon).observe(sun).apparent()
    ra_noon, dec_noon, _ = pos_noon.radec(epoch='date')
    gast_noon = t_noon.gast

    H_noon = (gast_noon + lon_deg / 15.0 - ra_noon.hours + 12.0) % 24.0 - 12.0
    transit_utc = t_noon_hours - H_noon

    # Refinement pertama untuk transit presisi tinggi
    t_transit = ts.utc(tahun, bulan, hari_arr, transit_utc)
    pos_transit = earth.at(t_transit).observe(sun).apparent()
    ra_tr, dec_tr, _ = pos_transit.radec(epoch='date')
    gast_tr = t_transit.gast

    H_tr = (gast_tr + lon_deg / 15.0 - ra_tr.hours + 12.0) % 24.0 - 12.0
    transit_utc_refined = transit_utc - H_tr

    dec_rad = dec_tr.radians

    # 2. Hitung Hour Angle (H)
    phi = np.radians(lat_deg)
    dip_derajat = 0.0347 * np.sqrt(max(elevasi_m, 0.0))
    alt_terbit_terbenam = -0.833 - dip_derajat

    def get_hour_angle(alt_deg):
        alt_rad = np.radians(alt_deg)
        cos_H = (np.sin(alt_rad) - np.sin(phi) * np.sin(dec_rad)) / (np.cos(phi) * np.cos(dec_rad))
        invalid = (cos_H < -1.0) | (cos_H > 1.0)
        cos_H = np.clip(cos_H, -1.0, 1.0)
        H = np.degrees(np.arccos(cos_H)) / 15.0
        H[invalid] = np.nan
        return H

    H_subuh = get_hour_angle(sudut_fajar)
    H_terbit = get_hour_angle(alt_terbit_terbenam)
    H_isya = get_hour_angle(sudut_isya)

    faktor_bayangan = 2.0 if mazhab_ashar == "hanafi" else 1.0
    alt_ashar = np.degrees(np.arctan(1.0 / (faktor_bayangan + np.tan(np.abs(phi - dec_rad)))))
    H_ashar = get_hour_angle(alt_ashar)

    # 3. Taksiran waktu awal kejadian
    t_subuh_approx = transit_utc_refined - H_subuh
    t_terbit_approx = transit_utc_refined - H_terbit
    t_ashar_approx = transit_utc_refined + H_ashar
    t_maghrib_approx = transit_utc_refined + H_terbit
    t_isya_approx = transit_utc_refined + H_isya

    # 4. Refinement 1-step Newton-Raphson untuk tiap kejadian
    def refine_event_time(t_approx, target_alt_deg):
        valid = ~np.isnan(t_approx)
        if not np.any(valid):
            return t_approx

        t_approx_valid = t_approx[valid]
        hari_valid = hari_arr[valid]

        t_eval = ts.utc(tahun, bulan, hari_valid, t_approx_valid)
        obs_eval = observer.at(t_eval).observe(sun).apparent()
        alt_eval, _, _ = obs_eval.altaz()
        alt_deg = alt_eval.degrees

        ra_eval, dec_eval, _ = obs_eval.radec(epoch='date')
        gast_eval = t_eval.gast

        H_eval = (gast_eval + lon_deg / 15.0 - ra_eval.hours + 12.0) % 24.0 - 12.0
        H_rad = np.radians(H_eval * 15.0)
        dec_r = dec_eval.radians

        alt_rad = np.radians(alt_deg)
        numerator = -15.0 * np.cos(phi) * np.cos(dec_r) * np.sin(H_rad)
        denominator = np.cos(alt_rad)

        denominator = np.where(np.abs(denominator) < 1e-4, 1e-4, denominator)
        d_alt_dt = numerator / denominator
        d_alt_dt = np.where(np.abs(d_alt_dt) < 1e-4, 1e-4 * np.sign(d_alt_dt), d_alt_dt)

        if isinstance(target_alt_deg, np.ndarray):
            target_alt_valid = target_alt_deg[valid]
        else:
            target_alt_valid = target_alt_deg

        delta_t = (target_alt_valid - alt_deg) / d_alt_dt
        delta_t = np.clip(delta_t, -0.5, 0.5)

        refined = t_approx_valid + delta_t

        res = np.full_like(t_approx, np.nan)
        res[valid] = refined
        return res

    t_subuh = refine_event_time(t_subuh_approx, sudut_fajar)
    t_terbit = refine_event_time(t_terbit_approx, alt_terbit_terbenam)
    t_ashar = refine_event_time(t_ashar_approx, alt_ashar)
    t_maghrib = refine_event_time(t_maghrib_approx, alt_terbit_terbenam)
    t_isya = refine_event_time(t_isya_approx, sudut_isya)

    # 5. Konversi ke waktu lokal & berikan Ihtiyat
    ihtiyat_jam = ihtiyat_menit / 60.0
    imsak_offset_jam = imsak_sebelum_fajr_menit / 60.0

    def ke_lokal_ihtiyat(t_utc, tambah_ihtiyat=True):
        val = (t_utc + zona_offset_jam) % 24.0
        if tambah_ihtiyat:
            val = val + ihtiyat_jam
        return [float(v) if not np.isnan(v) else None for v in val]

    subuh_lokal = ke_lokal_ihtiyat(t_subuh, tambah_ihtiyat=True)
    terbit_lokal = ke_lokal_ihtiyat(t_terbit, tambah_ihtiyat=True)
    dzuhur_lokal = ke_lokal_ihtiyat(transit_utc_refined, tambah_ihtiyat=True)
    ashar_lokal = ke_lokal_ihtiyat(t_ashar, tambah_ihtiyat=True)
    maghrib_lokal = ke_lokal_ihtiyat(t_maghrib, tambah_ihtiyat=True)
    isya_lokal = ke_lokal_ihtiyat(t_isya, tambah_ihtiyat=True)

    # Dhuha dan imsak dihitung dari nilai murni
    dhuha_lokal = ke_lokal_ihtiyat(t_terbit + 20.0 / 60.0, tambah_ihtiyat=False)
    imsak_lokal = ke_lokal_ihtiyat(t_subuh - imsak_offset_jam, tambah_ihtiyat=False)

    # Hitung Waktu Kiblat Vectorized
    az_spherical, _ = qibla_spherical(lat_deg, lon_deg)
    az_vincenty, _ = qibla_vincenty(lat_deg, lon_deg)

    def get_kiblat_time_vectorized(az_target):
        phi = np.radians(lat_deg)
        a = 1.0 / np.tan(np.radians(az_target))
        b = -np.sin(phi)
        c = -np.cos(phi) * np.tan(dec_rad)
        R = np.sqrt(a*a + b*b)
        ratio = c / R
        invalid = (ratio < -1.0) | (ratio > 1.0)
        ratio = np.clip(ratio, -1.0, 1.0)
        theta = np.arctan2(b, a)
        H_rad = np.pi / 2.0 + np.arccos(ratio) - theta
        H_rad = (H_rad + np.pi) % (2.0 * np.pi) - np.pi
        H_hours = np.degrees(H_rad) / 15.0
        
        t_utc = transit_utc_refined + H_hours
        val = (t_utc + zona_offset_jam) % 24.0
        val[invalid] = np.nan
        return [float(v) if not np.isnan(v) else None for v in val]

    kiblat_v_lokal = get_kiblat_time_vectorized(az_vincenty)
    kiblat_s_lokal = get_kiblat_time_vectorized(az_spherical)

    jadwal = []
    for i in range(jumlah_hari):
        tanggal = datetime(tahun, bulan, i + 1)
        waktu_dict = {
            "imsak": imsak_lokal[i],
            "subuh": subuh_lokal[i],
            "terbit": terbit_lokal[i],
            "dhuha": dhuha_lokal[i],
            "dzuhur": dzuhur_lokal[i],
            "ashar": ashar_lokal[i],
            "maghrib": maghrib_lokal[i],
            "isya": isya_lokal[i],
            "kiblat_v": kiblat_v_lokal[i],
            "kiblat_s": kiblat_s_lokal[i],
        }
        jadwal.append((tanggal, waktu_dict))

    return jadwal


def hitung_jadwal_sholat_bulan(tahun, bulan, lat_deg, lon_deg, zona_offset_jam, mode="ringan",
                                ts=None, eph=None, progress_cb=lambda msg: None, **kwargs):
    """Hitung jadwal sholat untuk SATU BULAN PENUH (semua tanggal di bulan
    tsb, otomatis menyesuaikan jumlah hari termasuk tahun kabisat).
    Return list berisi tuple (tanggal:datetime, hasil:dict) berurutan dari
    tanggal 1 sampai akhir bulan."""
    if mode == "jpl" and ts is not None and eph is not None:
        progress_cb(f"Menghitung jadwal sholat sebulan secara vectorized (mode Presisi)...")
        return hitung_jadwal_sholat_bulan_jpl_vectorized(
            tahun, bulan, lat_deg, lon_deg, zona_offset_jam, ts, eph, **kwargs)

    jumlah_hari = calendar.monthrange(tahun, bulan)[1]
    jadwal = []
    for hari in range(1, jumlah_hari + 1):
        tanggal = datetime(tahun, bulan, hari)
        progress_cb(f"Menghitung jadwal sholat {tanggal.strftime('%d %B %Y')} "
                     f"({hari}/{jumlah_hari})...")
        waktu = hitung_waktu_sholat_otomatis(
            tanggal, lat_deg, lon_deg, zona_offset_jam, mode=mode, ts=ts, eph=eph, **kwargs)
        jadwal.append((tanggal, waktu))
    return jadwal


def format_jam_desimal(jam):
    """Jam desimal (boleh negatif / >24, akan dibungkus modulo 24) -> 'HH:MM'."""
    if jam is None:
        return "—"
    jam_dibungkus = jam % 24.0
    total_menit = int(round(jam_dibungkus * 60.0))
    if total_menit >= 24 * 60:
        total_menit -= 24 * 60
    jj, mm = divmod(total_menit, 60)
    return f"{jj:02d}:{mm:02d}"


def _blend_warna(warna_depan, warna_belakang, alpha):
    """Campur 'warna_depan' ke atas 'warna_belakang' dgn opacity alpha
    (0..1), hasilnya berupa SATU warna solid biasa (bukan transparansi
    sungguhan) -- dipakai sbg trik bikin efek bayangan/shadow lembut,
    krn tk.PhotoImage cuma dukung transparansi BINER (ada/tidak ada,
    lewat transparency_set), bukan alpha sebagian. Valid selama warna
    latar yg dipakai (warna_belakang) memang PASTI itu warna sungguhan
    di baliknya (makanya selalu dipanggil dgn warna latar yg sudah
    diketahui pasti, mis. warna isi tab itu sendiri, atau WARNA_BG)."""
    def _ke_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    r1, g1, b1 = _ke_rgb(warna_depan)
    r2, g2, b2 = _ke_rgb(warna_belakang)
    r = round(r1 * alpha + r2 * (1 - alpha))
    g = round(g1 * alpha + g2 * (1 - alpha))
    b = round(b1 * alpha + b2 * (1 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"


def _pasang_bayangan_kartu(parent, tinggi=6, alpha_puncak=0.16):
    """Bikin & pasang satu strip tk.Canvas tipis (bayangan lembut,
    gradasi dari agak gelap di atas memudar ke WARNA_BG di bawah) --
    dipakai sbg efek "drop shadow" di bawah kartu/panel (mis. tiap
    bagian akordeon), memberi kesan kartu itu sedikit "terangkat" dari
    latar. Dipakai Canvas (bukan gambar statis) krn Canvas bisa gambar
    ulang otomatis mengikuti lebar sungguhan tiap kali di-resize
    (event <Configure>) -- jadi tidak perlu trik 9-slice segala.

    Return widget canvas-nya: PEMANGGIL WAJIB pack_forget()+pack() ulang
    widget ini tiap kali urutan tampilan di atasnya berubah (mis. saat
    akordeon dibuka/ditutup dan body-nya di-pack/pack_forget), supaya
    bayangan ini tetap "nempel" jadi elemen PALING BAWAH -- pack_forget
    lalu pack lagi otomatis menaruhnya di akhir urutan tumpukan pack."""
    kanvas = tk.Canvas(parent, height=tinggi, highlightthickness=0, bd=0, bg=WARNA_BG)

    def _gambar_ulang(event=None):
        kanvas.delete("all")
        lebar = kanvas.winfo_width()
        if lebar <= 1:
            return
        for i in range(tinggi):
            alpha = alpha_puncak * (1 - i / tinggi)
            warna = _blend_warna("#000000", WARNA_BG, alpha)
            kanvas.create_rectangle(0, i, lebar, i + 1, fill=warna, outline="")

    kanvas.bind("<Configure>", _gambar_ulang)
    return kanvas


def _buat_gambar_tab_bulat(lebar, tinggi, radius, warna_isi,
                            tinggi_bayangan=0, alpha_puncak_bayangan=0.22):
    """Bikin satu tk.PhotoImage (lebar x tinggi) berbentuk tab ala browser
    modern (Chrome dkk): sudut ATAS membulat (radius px), sudut BAWAH
    tetap siku (biar nyambung rapi dgn isi/konten di bawah tab). Piksel
    di luar bentuk itu (ujung sudut atas yg "terpotong") dibuat TRANSPARAN
    supaya warna asli di belakang tab (latar notebook) tetap kelihatan --
    itulah yg menciptakan ilusi sudut membulat, tanpa perlu Pillow atau
    file ikon eksternal apapun (gambar digambar manual pixel-demi-pixel,
    sama seperti ikon × di ClosableNotebook di bawah).

    Kalau tinggi_bayangan > 0: sejumlah baris PALING BAWAH (di dalam
    bentuk tab itu sendiri, TIDAK menambah tinggi gambar total) digelapkan
    bertahap (makin gelap makin dekat ke tepi bawah) -- efek "inner
    shadow"/emboss sederhana yg memberi kesan tab sedikit cembung/
    terangkat, tanpa mengubah ukuran gambar sama sekali (jadi TIDAK
    mengganggu perhitungan tinggi baris tab yg sudah dijaga hati-hati di
    _terapkan_tema -- lihat komentar di sana soal kenapa ukuran dasar
    sengaja dibuat besar).

    Dipakai sbg elemen gambar ttk (lihat 'border=radius' di
    style.element_create pemanggilnya) dgn mode "9-slice": kotak radius
    px di tiap ujung dijaga tetap tajam, cuma bagian tengahnya yg melar
    mengikuti lebar tab sungguhan (yg berubah2 sesuai panjang teks
    label) -- jadi satu gambar kecil ini cukup utk tab selebar apapun.
    """
    img = tk.PhotoImage(width=lebar, height=tinggi)
    pusat_y = radius - 0.5
    pusat_x_kiri = radius - 0.5
    pusat_x_kanan = lebar - radius - 0.5

    warna_per_baris = [warna_isi] * tinggi
    if tinggi_bayangan > 0:
        for i in range(min(tinggi_bayangan, tinggi)):
            y = tinggi - 1 - i
            alpha = alpha_puncak_bayangan * (1 - i / tinggi_bayangan)
            warna_per_baris[y] = _blend_warna("#000000", warna_isi, alpha)

    for y in range(tinggi):
        warna_baris = warna_per_baris[y]
        for x in range(lebar):
            if y >= radius:
                isi = True
            elif x < radius:
                dx, dy = x - pusat_x_kiri, y - pusat_y
                isi = (dx * dx + dy * dy) <= radius * radius
            elif x >= lebar - radius:
                dx, dy = x - pusat_x_kanan, y - pusat_y
                isi = (dx * dx + dy * dy) <= radius * radius
            else:
                isi = True
            if isi:
                img.put(warna_baris, (x, y))
            else:
                img.transparency_set(x, y, True)
    return img


# =========================================================
#  NOTEBOOK YANG TAB-NYA BISA DITUTUP (tombol × kecil di tiap tab)
#  Dipakai KHUSUS untuk notebook peta hasil perhitungan (self.notebook)
#  di jendela utama -- TIDAK menimpa style "TNotebook" bawaan, jadi
#  notebook lain di aplikasi ini (mis. notebook_hasil_sholat, yang
#  tab-nya memang tidak boleh ditutup user) tidak ikut terpengaruh.
#
#  Cara kerja tombol ×: pakai style-element ttk bernama "close" yang
#  gambarnya dibuat sendiri lewat tk.PhotoImage (digambar pixel demi
#  pixel), jadi TIDAK perlu file ikon eksternal apapun. Klik-tekan pada
#  area × lalu klik-lepas di area × yang SAMA -> event virtual
#  "<<NotebookTabClosed>>" dikirim; method _on_tab_peta_ditutup di
#  HisabWinApp yang menangani pembersihan sebenarnya (tutup figure
#  matplotlib, hapus dari self._tab_peta, dsb).
# =========================================================

# =========================================================
#  DIALOG: Kelola Kernel JPL (unduh/hapus/pilih de421-de440-de441)
# =========================================================

class DialogKernelJPL(tk.Toplevel):
    """Popup modal untuk melihat status, mengunduh, menghapus, dan memilih
    kernel JPL (.bsp) yang dipakai Mode Presisi. Dipanggil dari HisabWinApp
    lewat tombol "Kelola Kernel JPL..." di sebelah pilihan Mode Perhitungan.

    on_kernel_diganti: callback tanpa argumen, dipanggil setelah user
    menekan "Pakai kernel ini" pada kernel yang BEDA dari yang sedang aktif
    -- HisabWinApp yang menentukan apa yang perlu terjadi selanjutnya
    (reset self.eph/self.ts, muat ulang di background, dsb)."""

    def __init__(self, parent, on_kernel_diganti=lambda: None):
        super().__init__(parent)
        self.title("Kelola Kernel JPL")
        self.geometry("580x560")
        self.minsize(480, 360)
        self.transient(parent)
        self.configure(bg=WARNA_BG)

        self._on_kernel_diganti = on_kernel_diganti
        self._antrian = queue.Queue()
        self._event_batal = None
        self._kernel_sedang_diunduh = None
        self._baris_widget = {}  # kernel_id -> dict widget per baris

        ttk.Label(
            self, text="Kernel JPL yang tersedia",
            font=FONT_UTAMA_BOLD, foreground=WARNA_TEKS,
        ).pack(anchor="w", padx=14, pady=(14, 2))
        ttk.Label(
            self,
            text="de421 selalu tersedia (bawaan aplikasi, tanpa internet). "
                 "de440/de441 opsional -- unduh sekali, dipakai berulang.",
            foreground=WARNA_TEKS_MUTED, wraplength=520, justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))

        frame_list_luar = ttk.Frame(self)
        frame_list_luar.pack(fill="both", expand=True, padx=14)

        list_canvas = tk.Canvas(frame_list_luar, highlightthickness=0, bg=WARNA_BG)
        list_scrollbar = ttk.Scrollbar(frame_list_luar, orient="vertical", command=list_canvas.yview)
        list_canvas.configure(yscrollcommand=list_scrollbar.set)
        list_canvas.pack(side="left", fill="both", expand=True)
        list_scrollbar.pack(side="right", fill="y")

        frame_list = ttk.Frame(list_canvas)
        list_window = list_canvas.create_window((0, 0), window=frame_list, anchor="nw")

        def _list_on_configure(event):
            list_canvas.configure(scrollregion=list_canvas.bbox("all"))
        frame_list.bind("<Configure>", _list_on_configure)

        def _list_canvas_resize(event):
            list_canvas.itemconfig(list_window, width=event.width)
        list_canvas.bind("<Configure>", _list_canvas_resize)

        def _list_mousewheel(event):
            list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        parent._pasang_scroll_mousewheel(list_canvas, _list_mousewheel)

        kernel_aktif = muat_kernel_aktif()
        for kernel_id, info in KERNEL_CATALOG.items():
            self._buat_baris_kernel(frame_list, kernel_id, info, aktif=(kernel_id == kernel_aktif))

        # --- area progres (disembunyikan sampai ada unduhan berjalan) ---
        self._frame_progres = ttk.Frame(self)
        self._label_progres = ttk.Label(self._frame_progres, text="", foreground=WARNA_TEKS_MUTED)
        self._label_progres.pack(anchor="w")
        self._progressbar = ttk.Progressbar(self._frame_progres, mode="determinate", maximum=100)
        self._progressbar.pack(fill="x", pady=(2, 4))
        self._btn_batal = ttk.Button(self._frame_progres, text="Batal Unduh", command=self._on_batal)
        self._btn_batal.pack(anchor="e")

        ttk.Button(self, text="Tutup", command=self.destroy).pack(anchor="e", padx=14, pady=12)

        self.protocol("WM_DELETE_WINDOW", self._on_tutup)
        self._poll_antrian()

    # ---------------- baris per kernel ----------------

    def _buat_baris_kernel(self, parent, kernel_id, info, aktif):
        frame = ttk.LabelFrame(parent, text=info["label"])
        frame.pack(fill="x", pady=6)

        total_mb = sum(f["size_mb"] for f in info["files"])
        ttk.Label(frame, text=f"Cakupan: {info['cakupan']}", foreground=WARNA_TEKS_MUTED
                  ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(frame, text=f"Ukuran unduhan: ~{total_mb} MB" if not info["bundled"] else "Sudah tersimpan di aplikasi",
                  foreground=WARNA_TEKS_MUTED).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))

        label_status = ttk.Label(frame, text="", font=FONT_UTAMA_BOLD)
        label_status.grid(row=0, column=1, rowspan=2, sticky="e", padx=8)

        frame_tombol = ttk.Frame(frame)
        frame_tombol.grid(row=2, column=0, columnspan=2, sticky="e", padx=8, pady=(0, 8))
        btn_unduh = ttk.Button(frame_tombol, text="Unduh", command=lambda k=kernel_id: self._on_unduh(k))
        btn_hapus = ttk.Button(frame_tombol, text="Hapus", command=lambda k=kernel_id: self._on_hapus(k))
        btn_pakai = ttk.Button(frame_tombol, text="Pakai kernel ini", style="Aksen.TButton",
                                command=lambda k=kernel_id: self._on_pakai(k))
        btn_unduh.pack(side="left", padx=3)
        btn_hapus.pack(side="left", padx=3)
        btn_pakai.pack(side="left", padx=3)

        frame.columnconfigure(0, weight=1)
        self._baris_widget[kernel_id] = {
            "label_status": label_status, "btn_unduh": btn_unduh,
            "btn_hapus": btn_hapus, "btn_pakai": btn_pakai,
        }
        self._refresh_baris(kernel_id, aktif=aktif)

    def _refresh_baris(self, kernel_id, aktif=None):
        info = KERNEL_CATALOG[kernel_id]
        w = self._baris_widget[kernel_id]
        tersedia = status_kernel(kernel_id)
        if aktif is None:
            aktif = (muat_kernel_aktif() == kernel_id)

        if aktif:
            w["label_status"].config(text="✅ Aktif", foreground=WARNA_AKSEN)
        elif tersedia:
            w["label_status"].config(text="Tersedia", foreground=WARNA_TEKS)
        else:
            w["label_status"].config(text="Belum diunduh", foreground=WARNA_TEKS_MUTED)

        w["btn_unduh"].config(state="disabled" if (info["bundled"] or tersedia) else "normal")
        w["btn_hapus"].config(state="normal" if (tersedia and not info["bundled"] and not aktif) else "disabled")
        w["btn_pakai"].config(state="disabled" if (aktif or not tersedia) else "normal")

    def _refresh_semua_baris(self):
        for kernel_id in KERNEL_CATALOG:
            self._refresh_baris(kernel_id)

    # ---------------- aksi: unduh ----------------

    def _on_unduh(self, kernel_id):
        info = KERNEL_CATALOG[kernel_id]
        total_mb = sum(f["size_mb"] for f in info["files"])
        if not messagebox.askyesno(
                "Unduh kernel JPL",
                f"Unduh {info['label']} (~{total_mb} MB) dari server NASA/NAIF?\n\n"
                "Butuh koneksi internet dan bisa memakan waktu cukup lama "
                "tergantung kecepatan jaringan.", parent=self):
            return

        self._kernel_sedang_diunduh = kernel_id
        self._event_batal = threading.Event()
        self._frame_progres.pack(fill="x", padx=14, pady=(0, 4))
        self._progressbar.config(value=0)
        self._label_progres.config(text=f"Bersiap mengunduh {info['label']}...")
        self._set_semua_tombol(state="disabled")
        self._btn_batal.config(state="normal")

        threading.Thread(target=self._thread_unduh, args=(kernel_id, self._event_batal), daemon=True).start()

    def _thread_unduh(self, kernel_id, event_batal):
        def progress_cb(persen, teks):
            self._antrian.put(("progres", persen, teks))
        try:
            unduh_kernel(kernel_id, progress_cb=progress_cb, event_batal=event_batal)
            self._antrian.put(("unduh_ok", kernel_id))
        except Exception as e:
            self._antrian.put(("unduh_gagal", kernel_id, str(e)))

    def _on_batal(self):
        if self._event_batal is not None:
            self._event_batal.set()
        self._btn_batal.config(state="disabled")
        self._label_progres.config(text="Membatalkan...")

    def _set_semua_tombol(self, state):
        for w in self._baris_widget.values():
            for key in ("btn_unduh", "btn_hapus", "btn_pakai"):
                try:
                    w[key].config(state=state)
                except tk.TclError:
                    pass

    # ---------------- aksi: hapus & pakai ----------------

    def _on_hapus(self, kernel_id):
        info = KERNEL_CATALOG[kernel_id]
        if not messagebox.askyesno(
                "Hapus kernel JPL",
                f"Hapus file {info['label']} yang sudah diunduh? "
                "Kamu perlu mengunduhnya lagi kalau ingin memakainya lagi.", parent=self):
            return
        try:
            hapus_kernel(kernel_id)
            self._refresh_semua_baris()
        except (OSError, ValueError) as e:
            messagebox.showerror("Gagal menghapus", str(e), parent=self)

    def _on_pakai(self, kernel_id):
        simpan_kernel_aktif(kernel_id)
        self._refresh_semua_baris()
        self._on_kernel_diganti()

    # ---------------- polling hasil thread unduhan ----------------

    def _poll_antrian(self):
        try:
            while True:
                pesan = self._antrian.get_nowait()
                tipe = pesan[0]
                if tipe == "progres":
                    _, persen, teks = pesan
                    self._progressbar.config(value=persen)
                    self._label_progres.config(text=teks)
                elif tipe == "unduh_ok":
                    _, kernel_id = pesan
                    self._frame_progres.pack_forget()
                    self._set_semua_tombol(state="normal")
                    self._refresh_semua_baris()
                    self._kernel_sedang_diunduh = None
                    messagebox.showinfo(
                        "Selesai", f"{KERNEL_CATALOG[kernel_id]['label']} berhasil diunduh.",
                        parent=self)
                elif tipe == "unduh_gagal":
                    _, kernel_id, pesan_error = pesan
                    self._frame_progres.pack_forget()
                    self._set_semua_tombol(state="normal")
                    self._refresh_semua_baris()
                    self._kernel_sedang_diunduh = None
                    messagebox.showerror("Unduhan gagal/dibatalkan", pesan_error, parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll_antrian)

    def _on_tutup(self):
        if self._kernel_sedang_diunduh is not None:
            if not messagebox.askyesno(
                    "Unduhan sedang berjalan",
                    "Kernel masih sedang diunduh. Tutup jendela ini dan batalkan unduhan?",
                    parent=self):
                return
            if self._event_batal is not None:
                self._event_batal.set()
        self.destroy()


class ClosableNotebook(ttk.Notebook):
    _style_sudah_disiapkan = False

    def __init__(self, *args, **kwargs):
        if not ClosableNotebook._style_sudah_disiapkan:
            self._siapkan_style_close()
            ClosableNotebook._style_sudah_disiapkan = True

        kwargs["style"] = "Closable.TNotebook"
        super().__init__(*args, **kwargs)

        self._tab_ditekan = None  # index tab yang sedang ditekan tombol ×-nya
        self.tab_ditutup_terakhir = None  # diisi widget-name tab yg baru saja ditutup

        # add=True supaya binding ini jalan LEBIH DULU daripada binding
        # bawaan ttk::notebook (yang menangani pindah tab saat klik) --
        # jadi klik pada tombol × tidak ikut memindahkan tab.
        self.bind("<ButtonPress-1>", self._on_tekan, add=True)
        self.bind("<ButtonRelease-1>", self._on_lepas, add=True)

    # ---- gambar ikon tombol × (tanpa file eksternal) ----
    @staticmethod
    def _buat_icon_close(ukuran, warna_x, warna_bulatan=None):
        """Bikin satu tk.PhotoImage kotak (ukuran x ukuran) berisi tanda ×,
        dengan latar TRANSPARAN (dipakai transparency_set) supaya warna
        latar tab (hijau saat aktif / putih saat tidak aktif) tetap
        kelihatan di sekeliling ×-nya, bukan malah ketutup kotak."""
        img = tk.PhotoImage(width=ukuran, height=ukuran)
        for y in range(ukuran):
            for x in range(ukuran):
                img.transparency_set(x, y, True)

        pusat = (ukuran - 1) / 2.0
        if warna_bulatan:
            radius = (ukuran / 2.0) - 1.0
            for y in range(ukuran):
                for x in range(ukuran):
                    if (x - pusat) ** 2 + (y - pusat) ** 2 <= radius * radius:
                        img.put(warna_bulatan, (x, y))
                        img.transparency_set(x, y, False)

        # Gambar tanda × yang ramping, tajam, dan simetris (1 pixel tebal)
        tepi = 5
        for i in range(tepi, ukuran - tepi):
            for (xx, yy) in ((i, i), (i, ukuran - 1 - i)):
                if 0 <= xx < ukuran and 0 <= yy < ukuran:
                    img.put(warna_x, (xx, yy))
                    img.transparency_set(xx, yy, False)
        return img

    @classmethod
    def _siapkan_style_close(cls):
        style = ttk.Style()
        ukuran = 16
        # Disimpan sebagai atribut class supaya tidak "dibuang" oleh
        # garbage collector Python (kalau tidak, gambar jadi hilang/putus
        # begitu fungsi ini selesai, walau sudah dipakai style ttk).
        cls._img_close_normal = cls._buat_icon_close(ukuran, WARNA_TEKS_MUTED)
        cls._img_close_hover = cls._buat_icon_close(ukuran, "white", warna_bulatan="#E5484D")
        cls._img_close_tekan = cls._buat_icon_close(ukuran, "white", warna_bulatan="#B91C1C")

        style.element_create(
            "Closable.close", "image", cls._img_close_normal,
            ("active", "pressed", "!disabled", cls._img_close_tekan),
            ("active", "!disabled", cls._img_close_hover),
            border=0, sticky="", width=22,
        )

        # Style "Closable.TNotebook" mewarisi tampilan TNotebook biasa
        # (warna/padding/font tab sudah diatur lewat "TNotebook.Tab" di
        # _terapkan_tema), cuma layout tab-nya ditambah elemen tombol ×
        # di sebelah kanan label.
        #
        # PENTING: "padx" BUKAN opsi yang sah di dalam spec layout ttk --
        # opsi yang diterima Tk cuma -side/-sticky/-expand/-border/-unit/
        # -children. Menyelipkan "padx" di sini (seperti versi sebelumnya)
        # membuat Tcl gagal mem-parse nilai -children dan aplikasi crash
        # sejak start dengan TclError: "Invalid -children value". Jarak
        # visual antara label & tombol × di sini cukup diatur lewat
        # "width" pada element_create di atas (memberi ruang kosong di
        # sekitar gambar ×, karena gambarnya sendiri transparan).
        style.layout("Closable.TNotebook", style.layout("TNotebook"))
        # Elemen akar "Rounded.tab" (sudut atas membulat ala Chrome) sudah
        # dibuat & didaftarkan di HisabWinApp._terapkan_tema() -- fungsi
        # itu SELALU dipanggil lebih dulu, sebelum notebook manapun
        # (termasuk ClosableNotebook ini) dibangun, jadi elemen itu
        # dijamin sudah ada di titik ini. Dipakai lagi di sini (bukan
        # "Notebook.tab" bawaan) supaya tab yg punya tombol × ini ikut
        # membulat, konsisten dgn notebook lain.
        style.layout("Closable.TNotebook.Tab", [
            ("Rounded.tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"sticky": "nswe", "children": [
                    ("Notebook.focus", {"sticky": "nswe", "children": [
                        ("Notebook.label", {"side": "left", "sticky": ""}),
                        ("Closable.close", {"side": "left", "sticky": ""}),
                    ]}),
                ]}),
            ]}),
        ])
        style.configure("Closable.TNotebook", background=WARNA_BG, borderwidth=0)
        style.configure("Closable.TNotebook.Tab", background=WARNA_AKSEN, foreground="white",
                         padding=(14, 10), font=FONT_TAB_AKTIF, borderwidth=0)
        style.map("Closable.TNotebook.Tab",
                  background=[("selected", WARNA_AKSEN), ("!selected", WARNA_PANEL)],
                  foreground=[("selected", "white"), ("!selected", WARNA_TEKS_MUTED)],
                  font=[("!selected", FONT_UTAMA)],
                  padding=[("!selected", (14, 6))])

    # ---- interaksi klik tombol × ----
    def _on_tekan(self, event):
        elemen = self.identify(event.x, event.y)
        if "close" not in elemen:
            return
        index = self.index(f"@{event.x},{event.y}")
        self.state(["pressed"])
        self._tab_ditekan = index
        return "break"  # cegah binding bawaan notebook ikut memproses klik ini

    def _on_lepas(self, event):
        if not self.instate(["pressed"]):
            return
        elemen = self.identify(event.x, event.y)
        self.state(["!pressed"])
        if "close" not in elemen:
            self._tab_ditekan = None
            return
        index = self.index(f"@{event.x},{event.y}")
        if index == self._tab_ditekan:
            tab_id = self.tabs()[index]
            self.tab_ditutup_terakhir = tab_id
            self.event_generate("<<NotebookTabClosed>>")
        self._tab_ditekan = None
        return "break"


# =========================================================
#  APLIKASI UTAMA — HisabWin
#  (Peta kini ditampilkan sebagai TAB di jendela utama, bukan jendela
#  popup terpisah — lihat method _tampilkan_peta / _tab_peta_frame.)
# =========================================================

class HisabWinApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HisabWin — Peta Visibilitas Hilal")
        self.geometry("1400x800")
        self.minsize(1000, 600)
        self.resizable(True, True)  # peta digambar di tab jendela utama, perlu ruang lebih & bisa diubah ukurannya

        self._terapkan_tema()

        # --- Logo aplikasi (logo.png di folder yang sama dengan script ini).
        #     Dipakai untuk icon jendela (favicon) & header -- kalau filenya
        #     tidak ada, aplikasi tetap jalan normal tanpa logo (bukan error). ---
        self._logo_icon_img = self._muat_logo(ukuran_max=128)
        if self._logo_icon_img is not None:
            try:
                self.iconphoto(True, self._logo_icon_img)
            except Exception as e:
                print(f"Gagal memasang icon jendela dari logo.png: {e}")

        self.ts = None
        self.eph = None
        self.ijtimak_times = None
        self.tanggal_terpilih = None
        self.waktu_ijtimak_terpilih = None
        self.mode = tk.StringVar(value="jpl")  # default: perilaku lama (presisi JPL)

        # Variabel untuk memilih kriteria peta yang akan dihitung
        self.hitung_mabims = tk.BooleanVar(value=True)
        self.hitung_khgt = tk.BooleanVar(value=True)
        self.hitung_alt = tk.BooleanVar(value=True)
        self.hitung_elong = tk.BooleanVar(value=True)

        # Menyimpan tab-peta yang sudah pernah dibuat: nama_tab -> {"frame":..., "fig":...}
        # supaya saat "Tampilkan Peta" ditekan lagi, kanvas & figure LAMA diganti
        # (bukan menumpuk tab baru terus-menerus).
        self._tab_peta = {}

        # Daftar kandidat gerhana matahari hasil pencarian tahun terakhir
        # (list of dict dari cari_gerhana_matahari_kandidat_ringan, sudah
        # difilter -- hanya entri yang benar2 kandidat gerhana, lihat
        # handler "gerhana_ok" di _poll_antrian). Index list ini SEJAJAR
        # dengan baris di self.listbox_gerhana.
        self.kandidat_gerhana = []

        # Jenis gerhana yang terakhir kali dicari ("matahari" atau "bulan")
        # -- dipakai supaya _on_tampilkan_gerhana tahu figure-builder mana
        # yang harus dipanggil utk kandidat yang sedang dipilih di listbox.
        self._jenis_gerhana_terakhir = "matahari"

        # Mode ('ringan'/'jpl') yang dipakai saat pencarian kandidat gerhana
        # terakhir kali -- dipakai supaya _on_tampilkan_gerhana menghitung
        # detail peta (lintasan/kontak/dsb) dgn presisi yg SAMA dgn yang
        # dipakai mencari kandidatnya (bukan otomatis ikut self.mode SAAT
        # tombol "Tampilkan Peta" ditekan, yang bisa saja sudah berubah).
        self._mode_gerhana_terakhir = "ringan"

        # Flag: apakah perlu otomatis mencari ulang ijtimak setelah ephemeris JPL
        # selesai dimuat (dipicu saat user ganti mode padahal sudah pernah mencari).
        self._auto_cari_pending = False

        # Hasil perbandingan kalender MABIMS vs KHGT Muhammadiyah terakhir:
        # (tahun_h, mode, hasil_list) -- disimpan supaya tombol "Simpan ke CSV"
        # bisa mengekspor tanpa menghitung ulang.
        self._hasil_kalbanding_terakhir = None
        self._tab_kalbanding_ditambahkan = False

        # Hasil tabel efemeris (posisi Matahari & Bulan tiap interval waktu)
        # terakhir: list of dict dari hitung_tabel_efemeris() -- disimpan
        # supaya tombol "Simpan ke CSV" bisa mengekspor tanpa menghitung ulang.
        self._hasil_efemeris_terakhir = None
        self._tab_efemeris_ditambahkan = False

        self.antrian = queue.Queue()
        self._poll_after_id = None  # id job after() yg sedang terjadwal (lihat _on_close)

        self._bangun_ui()
        self._poll_after_id = self.after(100, self._poll_antrian)
        self._terapkan_mode_awal()

        # Tutup splash screen PyInstaller (--splash) sekarang, karena UI utama
        # sudah selesai dibangun & siap ditampilkan. Kalau app dijalankan
        # langsung dari python (bukan hasil build exe), modul pyi_splash
        # tidak akan ada -> import gagal, tapi itu normal, jadi diabaikan saja.
        try:
            import pyi_splash
            pyi_splash.close()
        except ImportError:
            pass

        # Batalkan job after() yang masih terjadwal SEBELUM window dihancurkan.
        # Tanpa ini, saat user menutup window sementara _poll_antrian masih
        # menunggu di antrian Tk, Tk akan mencoba memanggil command
        # "...poll_antrian" pada interpreter yang sudah didestroy -> error
        # "invalid command name ... (\"after\" script)".
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _muat_logo(self, ukuran_max=64):
        """Muat 'logo.png' dari folder yang sama dengan script ini, sebagai
        objek gambar Tk (PhotoImage) siap pakai untuk iconphoto() maupun
        ttk.Label(image=...). Kalau Pillow (PIL) tersedia, gambar di-resize
        proporsional dulu supaya rapi di header/icon; kalau tidak ada
        Pillow, fallback ke tk.PhotoImage bawaan Tk (Tk 8.6+ sudah bisa baca
        PNG langsung, tapi tanpa resize otomatis).

        Kalau logo.png tidak ditemukan atau gagal dibaca, mengembalikan None
        -- BUKAN error fatal, aplikasi tetap jalan normal tanpa logo."""
        folder_script = _resource_base_dir()
        path_logo = os.path.join(folder_script, "logo.png")

        if not os.path.isfile(path_logo):
            return None

        try:
            from PIL import Image, ImageTk
            img = Image.open(path_logo)
            img.thumbnail((ukuran_max, ukuran_max))
            return ImageTk.PhotoImage(img)
        except ImportError:
            try:
                return tk.PhotoImage(file=path_logo)
            except Exception as e:
                print(f"Gagal memuat logo.png (tanpa Pillow): {e}")
                return None
        except Exception as e:
            print(f"Gagal memuat logo.png: {e}")
            return None

    def _tampilkan_tab_awal(self, wrapper_notebook):
        """Tampilkan gambar 'bg.png' (dari folder yang sama dengan
        script/exe ini -- lihat _resource_base_dir) MENUTUPI seluruh area
        notebook kanan, supaya begitu aplikasi baru dibuka -- SEBELUM user
        memproses apapun dan SEBELUM ada tab peta/hasil lain sama sekali
        -- area kanan tidak kosong begitu saja, melainkan menampilkan
        gambar latar tsb.

        SENGAJA berupa overlay (place() di atas notebook), BUKAN tab
        notebook sungguhan -- supaya tidak ikut mendapat tombol × bawaan
        ClosableNotebook (notebook ini semua tabnya closable lewat 1
        style yang sama, tidak bisa dikecualikan per-tab).

        Overlay ini otomatis dilepas begitu tab "sungguhan" pertama
        muncul (lihat _hapus_tab_awal, dipanggil dari _tab_peta_frame dan
        semua _pastikan_tab_*_tampil). Kalau bg.png tidak ada/gagal
        dibaca, method ini tidak melakukan apa-apa -- BUKAN error fatal,
        aplikasi tetap jalan normal, cuma notebook kanan tampil kosong
        seperti sebelumnya."""
        path_bg = os.path.join(_resource_base_dir(), "bg.png")
        if not os.path.isfile(path_bg):
            return

        label_bg = tk.Label(wrapper_notebook, bg=WARNA_PANEL, borderwidth=0, highlightthickness=0)

        try:
            from PIL import Image, ImageTk
            img_asli = Image.open(path_bg).convert("RGB")
        except ImportError:
            img_asli = None
            # Tanpa Pillow: tampilkan apa adanya (tanpa resize mengikuti
            # ukuran jendela) lewat PhotoImage bawaan Tk.
            try:
                img_tk = tk.PhotoImage(file=path_bg)
                label_bg.configure(image=img_tk)
                label_bg.image = img_tk  # cegah digarbage-collect
            except Exception as e:
                print(f"Gagal memuat bg.png (tanpa Pillow): {e}")
        except Exception as e:
            img_asli = None
            print(f"Gagal memuat bg.png: {e}")

        if img_asli is not None:
            def _render(lebar, tinggi):
                lebar, tinggi = max(lebar, 1), max(tinggi, 1)
                # mode "cover": diperbesar/diperkecil supaya menutupi
                # seluruh area tanpa distorsi, kelebihannya dipotong
                # simetris -- bukan di-stretch paksa jadi gepeng.
                rasio = max(lebar / img_asli.width, tinggi / img_asli.height)
                w_baru = max(int(img_asli.width * rasio), 1)
                h_baru = max(int(img_asli.height * rasio), 1)
                img_resize = img_asli.resize((w_baru, h_baru), Image.LANCZOS)
                x_potong = (w_baru - lebar) // 2
                y_potong = (h_baru - tinggi) // 2
                img_crop = img_resize.crop((x_potong, y_potong, x_potong + lebar, y_potong + tinggi))
                img_tk = ImageTk.PhotoImage(img_crop)
                label_bg.configure(image=img_tk)
                label_bg.image = img_tk  # cegah digarbage-collect

            def _render_ulang(event):
                try:
                    _render(event.width, event.height)
                except Exception as e:
                    # ImageTk.PhotoImage/Image.resize bisa gagal (mis. ukuran
                    # 0 sesaat, atau Tcl/Tk versi tertentu di build PyInstaller
                    # yang beda dari lingkungan dev) -- Tkinter MENELAN
                    # exception dari callback <Configure> ini secara diam-diam
                    # (cuma print ke stderr, tak terlihat di build --windowed),
                    # jadi overlay bisa kosong TANPA error apapun yang
                    # kelihatan. Log eksplisit di sini supaya kalau masih
                    # bermasalah, penyebabnya jelas kelihatan (jalankan build
                    # TANPA --windowed sekali utk melihat console-nya).
                    print(f"Gagal merender bg.png: {e}")

            label_bg.bind("<Configure>", _render_ulang)

        # place() menumpuk overlay ini PERSIS di atas notebook (yang
        # dipack fill+expand di wrapper yang sama), menutupinya penuh
        # sampai dilepas lewat _hapus_tab_awal().
        label_bg.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._label_bg_awal = label_bg

        if img_asli is not None:
            # Paksa render PERTAMA secara langsung -- jangan cuma andalkan
            # event <Configure>, yang di beberapa versi Tcl/Tk (terutama di
            # dalam ttk.PanedWindow) bisa telat/tidak terpicu sama sekali
            # saat widget pertama kali dibuat, sehingga overlay tetap kosong
            # walau bg.png berhasil dimuat & tidak ada error apapun.
            label_bg.update_idletasks()
            lebar_awal = label_bg.winfo_width()
            tinggi_awal = label_bg.winfo_height()
            if lebar_awal > 1 and tinggi_awal > 1:
                try:
                    _render(lebar_awal, tinggi_awal)
                except Exception as e:
                    print(f"Gagal merender bg.png (render awal): {e}")
            # Kalau lebar/tinggi masih 1x1 di titik ini (window belum
            # sempat di-layout sama sekali), biarkan -- binding
            # <Configure> di atas akan menangkap ukuran valid pertama yang
            # benar-benar terjadi begitu window benar-benar tampil.

    def _hapus_tab_awal(self):
        """Lepas overlay sambutan bg.png (kalau masih ada) -- dipanggil
        tepat sebelum tab 'sungguhan' pertama (peta/Waktu Sholat/
        Perbandingan Kalender/Tabel Efemeris) ditambahkan, supaya overlay
        bg.png tidak lagi menutupi notebook setelah aplikasi mulai
        benar-benar dipakai."""
        label_bg = getattr(self, "_label_bg_awal", None)
        if label_bg is None:
            return
        self._label_bg_awal = None
        try:
            label_bg.place_forget()
            label_bg.destroy()
        except tk.TclError:
            pass

    def _buat_bagian_akordeon(self, parent, judul, buka_awal=True, on_open=None):
        """Bikin satu 'bagian' akordeon yang bisa dilipat/dibuka dengan
        mengklik headernya: header (panah + judul, warna aksen) dan body
        (ttk.Frame kosong -- pemanggil memasang widget-widget isinya di
        situ, seperti biasa memasang ke frame apapun).
        Return (body_frame, fungsi_buka, fungsi_tutup) supaya kode lain
        (mis. saat pindah tab notebook) bisa buka/tutup bagian ini
        secara terprogram."""
        wadah = ttk.Frame(parent)
        wadah.pack(fill="x", padx=8, pady=(0, 6))

        state = {"buka": buka_awal}

        header = tk.Frame(wadah, bg=WARNA_AKSEN, cursor="hand2")
        header.pack(fill="x")
        label_panah = tk.Label(header, text=("▾" if buka_awal else "▸"), bg=WARNA_AKSEN,
                                fg="white", font=FONT_UTAMA_BOLD, padx=8, pady=6)
        label_panah.pack(side="left")
        label_judul = tk.Label(header, text=judul, bg=WARNA_AKSEN, fg="white",
                                font=FONT_UTAMA_BOLD, pady=6, anchor="w")
        label_judul.pack(side="left", fill="x", expand=True)

        body = ttk.Frame(wadah)
        if buka_awal:
            body.pack(fill="x")

        # Bayangan lembut di BAWAH kartu akordeon ini (lihat
        # _pasang_bayangan_kartu) -- selalu di-pack_forget()+pack() ulang
        # di _toggle() supaya tetap jadi elemen PALING BAWAH baik saat
        # body terbuka maupun tertutup.
        bayangan = _pasang_bayangan_kartu(wadah, tinggi=6)
        bayangan.pack(fill="x")

        def _toggle(event=None):
            if state["buka"]:
                body.pack_forget()
                label_panah.config(text="▸")
            else:
                body.pack(fill="x")
                label_panah.config(text="▾")
                if on_open is not None:
                    on_open()
            bayangan.pack_forget()
            bayangan.pack(fill="x")
            state["buka"] = not state["buka"]

        def _buka():
            if not state["buka"]:
                _toggle()

        def _tutup():
            if state["buka"]:
                _toggle()

        for widget in (header, label_panah, label_judul):
            widget.bind("<Button-1>", _toggle)

        return body, _buka, _tutup

    def _pasang_scroll_mousewheel(self, canvas, handler):
        """Pasang scroll roda-mouse ke `canvas`, TAPI hanya aktif selagi
        kursor benar-benar di atas `canvas` tsb (bind/unbind saat
        Enter/Leave). Dulu dipasang lewat bind_all() langsung secara
        permanen -- itu jadi rebutan/bentrok kalau ada lebih dari satu
        panel yang bisa discroll (mis. panel Kontrol di tab Hilal vs
        panel Input di tab Waktu Sholat): binding yang belakangan pasang
        akan 'membajak' roda mouse dari SEMUA panel lain selamanya,
        sehingga panel lainnya terasa tidak bisa discroll sama sekali."""
        def _saat_masuk(event):
            canvas.bind_all("<MouseWheel>", handler)

        def _saat_keluar(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _saat_masuk)
        canvas.bind("<Leave>", _saat_keluar)

    def _terapkan_tema(self):
        """Styling flat & modern-sederhana untuk semua widget ttk, sekali
        dipasang di awal. Cuma warna/font (lihat konstanta WARNA_*/FONT_*
        di atas) -- TIDAK menyentuh logika aplikasi sama sekali."""
        self.configure(bg=WARNA_BG)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")  # basis tema flat, bawaan Tk, tanpa dependensi tambahan
        except tk.TclError:
            pass

        style.configure(".", background=WARNA_BG, foreground=WARNA_TEKS, font=FONT_UTAMA)
        style.configure("TFrame", background=WARNA_BG)
        style.configure("TLabel", background=WARNA_BG, foreground=WARNA_TEKS)
        style.configure("TPanedwindow", background=WARNA_BG)

        style.configure("TLabelframe", background=WARNA_BG, bordercolor=WARNA_BORDER,
                         relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=WARNA_BG, foreground=WARNA_TEKS,
                         font=FONT_UTAMA_BOLD)

        style.configure("TRadiobutton", background=WARNA_BG, foreground=WARNA_TEKS, font=FONT_UTAMA)
        style.map("TRadiobutton", background=[("active", WARNA_BG)])

        style.configure("TEntry", fieldbackground=WARNA_PANEL, foreground=WARNA_TEKS,
                         bordercolor=WARNA_BORDER, lightcolor=WARNA_BORDER, darkcolor=WARNA_BORDER,
                         padding=6)

        # --- Tombol biasa (netral) ---
        style.configure("TButton", background=WARNA_PANEL, foreground=WARNA_TEKS,
                         bordercolor=WARNA_BORDER, relief="flat", padding=(10, 6), font=FONT_UTAMA)
        style.map("TButton",
                  background=[("active", WARNA_BORDER), ("disabled", WARNA_BG)],
                  foreground=[("disabled", WARNA_TEKS_MUTED)])

        # --- Tombol aksen (aksi utama: Cari Ijtimak, Tampilkan Peta) ---
        style.configure("Aksen.TButton", background=WARNA_AKSEN, foreground="white",
                         bordercolor=WARNA_AKSEN, relief="flat", padding=(12, 8), font=FONT_UTAMA_BOLD)
        style.map("Aksen.TButton",
                  background=[("active", WARNA_AKSEN_HOVER), ("disabled", WARNA_BORDER)],
                  foreground=[("disabled", WARNA_TEKS_MUTED)])

        # --- Notebook (tab peta) ---
        style.configure("TNotebook", background=WARNA_BG, borderwidth=0)
        # PENTING: ukuran DASAR/default tab sengaja dibuat besar (padding
        # lega + FONT_TAB_AKTIF) -- ini yang dipakai ttk untuk MENGHITUNG
        # tinggi baris tab pada saat notebook pertama kali dibangun.
        # Tab yang SEDANG TIDAK AKTIF baru dikecilkan lewat style.map
        # (padding lebih tipis + font lebih kecil + warna pudar) di bawah.
        # Kenapa dibalik begini (bukan besarkan yang aktif lewat map)?
        # Karena baris tab cuma dihitung SEKALI di awal berdasar ukuran
        # dasar/default -- kalau ukuran besar baru muncul lewat map saat
        # tab jadi aktif, baris itu sudah kadung pas untuk ukuran kecil
        # dan tab aktif jadi KEPOTONG di atas (persis yang terjadi
        # sebelumnya). Dengan ukuran besar sebagai dasar, baris tab dari
        # awal sudah dialokasikan cukup tinggi, jadi tab aktif selalu
        # tampil penuh/menjulang, dan tab tidak aktif yang mengecil ke
        # dalam baris yang sama (jadi kelihatan mundur ke belakang).
        style.configure("TNotebook.Tab", background=WARNA_AKSEN, foreground="white",
                         padding=(14, 10), font=FONT_TAB_AKTIF, borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", WARNA_AKSEN), ("!selected", WARNA_PANEL)],
                  foreground=[("selected", "white"), ("!selected", WARNA_TEKS_MUTED)],
                  font=[("!selected", FONT_UTAMA)],
                  padding=[("!selected", (14, 6))])

        # --- Tab BULAT ala Chrome (sudut ATAS membulat, bawah tetap
        #     siku spy nyambung rapi dgn isi di bawahnya) -- ttk/tema
        #     "clam" bawaan cuma bisa gambar tab persegi lewat elemen
        #     "Notebook.tab", jadi elemen itu diganti dgn elemen gambar
        #     custom "Rounded.tab" (dibuat manual pixel-demi-pixel,
        #     TANPA butuh Pillow/file ikon eksternal -- sama seperti
        #     tombol X di ClosableNotebook di bawah). Gambar dipakai sbg
        #     "9-slice" (border=RADIUS_TAB) supaya sudut tetap tajam
        #     berapa pun lebar tab-nya (menyesuaikan panjang teks label),
        #     cuma bagian tengahnya yg melar. Dipasang di sini (bukan di
        #     ClosableNotebook) supaya SEMUA notebook di aplikasi ini
        #     (termasuk notebook_hasil_sholat yg pakai "TNotebook" polos)
        #     ikut membulat, konsisten.
        RADIUS_TAB = 10
        lebar_img_tab, tinggi_img_tab = 4 * RADIUS_TAB, 40
        img_tab_nonaktif = _buat_gambar_tab_bulat(
            lebar_img_tab, tinggi_img_tab, RADIUS_TAB, WARNA_PANEL,
            tinggi_bayangan=5, alpha_puncak_bayangan=0.12)
        img_tab_aktif = _buat_gambar_tab_bulat(
            lebar_img_tab, tinggi_img_tab, RADIUS_TAB, WARNA_AKSEN,
            tinggi_bayangan=7, alpha_puncak_bayangan=0.28)
        img_tab_hover = _buat_gambar_tab_bulat(
            lebar_img_tab, tinggi_img_tab, RADIUS_TAB, WARNA_BORDER,
            tinggi_bayangan=5, alpha_puncak_bayangan=0.12)
        # Disimpan di instance (bukan variabel lokal) spy tidak dibuang
        # garbage collector selama style ttk masih memakainya.
        self._img_tab_bulat = (img_tab_nonaktif, img_tab_aktif, img_tab_hover)
        style.element_create(
            "Rounded.tab", "image", img_tab_nonaktif,
            ("selected", img_tab_aktif),
            ("active", "!selected", img_tab_hover),
            border=RADIUS_TAB, sticky="nsew",
        )
        # Layout tab biasa ("TNotebook.Tab", dipakai notebook_hasil_sholat
        # dkk) -- struktur children SAMA seperti bawaan tema clam, cuma
        # elemen akar "Notebook.tab" diganti "Rounded.tab".
        style.layout("TNotebook.Tab", [
            ("Rounded.tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"sticky": "nswe", "children": [
                    ("Notebook.focus", {"sticky": "nswe", "children": [
                        ("Notebook.label", {"side": "top", "sticky": ""}),
                    ]}),
                ]}),
            ]}),
        ])

        # --- Scrollbar ---
        style.configure("TScrollbar", background=WARNA_PANEL, troughcolor=WARNA_BG,
                         bordercolor=WARNA_BG, arrowcolor=WARNA_TEKS_MUTED, relief="flat")

    def _on_close(self):
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None

        # Tutup semua figure matplotlib yang masih terdaftar di pyplot
        # (fig_mabims, fig_muh, dll dibuat via plt.figure(), jadi tanpa ini
        # referensinya tetap "hidup" di state global pyplot walau window
        # sudah dihancurkan).
        try:
            for info in self._tab_peta.values():
                plt.close(info["fig"])
        except Exception:
            pass
        try:
            plt.close("all")
        except Exception:
            pass

        self.destroy()

        # Paksa proses benar-benar berhenti. Beberapa native library (cartopy/
        # shapely/PROJ, atau thread daemon skyfield yang sedang menunggu I/O)
        # kadang butuh waktu lama untuk keluar sendiri sehingga proses masih
        # terlihat di Task Manager walau window sudah tertutup. os._exit()
        # langsung mematikan interpreter tanpa menunggu itu semua.
        os._exit(0)

    def _terapkan_mode_awal(self):
        """Dipanggil sekali saat start: mode 'ringan' tidak butuh apa2, jadi
        langsung siap. Mode 'jpl' perlu memuat de421.bsp dulu di background."""
        if self.mode.get() == "ringan":
            self._log("Mode Ringan (VSOP87+ELP2000) aktif — tidak perlu unduh apa pun. Siap dipakai.")
            self.btn_cari.config(state="normal")
        else:
            self._log("Mode Presisi (JPL DE421) aktif — memuat ephemeris de421.bsp, mohon tunggu...")
            threading.Thread(target=self._muat_ephemeris, daemon=True).start()

    def _on_ganti_mode(self):
        """Dipanggil saat user mengganti radio button mode.

        Hasil ijtimak antar mode ('jpl' vs 'ringan') bisa sedikit berbeda,
        jadi daftar ijtimak lama di listbox SELALU direset dulu supaya user
        tidak salah pilih ijtimak dari mode yang lain. Jika sebelumnya sudah
        pernah mencari ijtimak (dan kolom tahun masih berisi angka valid),
        pencarian otomatis diulang sesuai mode yang baru dipilih.
        """
        pernah_mencari = self.ijtimak_times is not None

        # --- reset daftar ijtimak & langkah setelahnya ---
        self.ijtimak_times = None
        self.listbox_ijtimak.delete(0, "end")
        self.btn_proses.config(state="disabled")

        teks_tahun = self.entry_tahun.get().strip()
        tahun_valid = teks_tahun.isdigit() and len(teks_tahun) == 4

        if self.mode.get() == "ringan":
            self._log("Beralih ke Mode Ringan (VSOP87+ELP2000) — tidak perlu unduh apa pun.")
            self.btn_cari.config(state="normal")
            if pernah_mencari and tahun_valid:
                self._log("Daftar ijtimak direset, mencari ulang otomatis untuk Mode Ringan...")
                self._on_cari_ijtimak()
            else:
                self._log("Daftar ijtimak direset. Silakan klik \"Cari Ijtimak\" lagi.")
        else:
            self._log("Beralih ke Mode Presisi (JPL DE421).")
            if self.eph is not None:
                self.btn_cari.config(state="normal")
                if pernah_mencari and tahun_valid:
                    self._log("Daftar ijtimak direset, mencari ulang otomatis untuk Mode Presisi...")
                    self._on_cari_ijtimak()
                else:
                    self._log("Daftar ijtimak direset. Silakan klik \"Cari Ijtimak\" lagi.")
            else:
                self.btn_cari.config(state="disabled")
                self._log("Daftar ijtimak direset. Memuat ephemeris de421.bsp, mohon tunggu...")
                # tunda pencarian ulang sampai ephemeris_ok diterima (lihat _poll_antrian)
                self._auto_cari_pending = pernah_mencari and tahun_valid
                threading.Thread(target=self._muat_ephemeris, daemon=True).start()

    def _on_kelola_kernel_jpl(self):
        DialogKernelJPL(self, on_kernel_diganti=self._on_kernel_jpl_diganti)

    def _on_kernel_jpl_diganti(self):
        """Dipanggil dari DialogKernelJPL setelah user menekan "Pakai
        kernel ini" pada kernel yang berbeda dari yang sedang aktif.

        Kernel .bsp yang dipakai skyfield.load() hanya diambil SEKALI saat
        _muat_ephemeris dipanggil dan disimpan di self.eph -- jadi ganti
        preferensi saja TIDAK otomatis membuat perhitungan berikutnya
        memakai kernel baru. self.eph/self.ts di-reset di sini supaya
        _muat_ephemeris() dipanggil ulang (baik sekarang kalau sedang mode
        Presisi, atau nanti begitu user pindah ke mode Presisi)."""
        self.eph = None
        self.ts = None
        self._log("Kernel JPL aktif diganti. Ephemeris akan dimuat ulang.")
        if self.mode.get() == "jpl":
            pernah_mencari = self.ijtimak_times is not None
            teks_tahun = self.entry_tahun.get().strip()
            tahun_valid = teks_tahun.isdigit() and len(teks_tahun) == 4
            self.ijtimak_times = None
            self.listbox_ijtimak.delete(0, "end")
            self.btn_proses.config(state="disabled")
            self.btn_cari.config(state="disabled")
            self._auto_cari_pending = pernah_mencari and tahun_valid
            threading.Thread(target=self._muat_ephemeris, daemon=True).start()

    # ---------------- UI ----------------

    def _bangun_ui(self):
        pad = {"padx": 10, "pady": 6}

        header_frame = ttk.Frame(self)
        header_frame.pack(pady=(14, 0))

        self._logo_header_img = self._muat_logo(ukuran_max=56)
        if self._logo_header_img is not None:
            ttk.Label(header_frame, image=self._logo_header_img).pack(side="left", padx=(0, 10))

        header_teks_frame = ttk.Frame(header_frame)
        header_teks_frame.pack(side="left")

        header = ttk.Label(header_teks_frame, text="HisabWin", font=FONT_JUDUL, foreground=WARNA_TEKS)
        header.pack(anchor="w")
        subheader = ttk.Label(header_teks_frame, text="Peta Visibilitas Hilal — Kriteria MABIMS & Muhammadiyah",
                               foreground=WARNA_TEKS_MUTED)
        subheader.pack(anchor="w")

        tk.Frame(self, bg=WARNA_AKSEN, height=2).pack(fill="x", padx=8, pady=(10, 0))

        # --- Layout arrange (PanedWindow horizontal): panel Kontrol SELALU
        #     tampil di kiri (bukan tab yang harus diklik), sementara peta
        #     hasil (MABIMS/Muhammadiyah/dsb) tampil di notebook sebelah
        #     kanan begitu perhitungan selesai — SEMUA di jendela utama yang
        #     sama, tidak ada lagi jendela popup terpisah. ---
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        # --- Panel kiri: Kontrol (bisa discroll kalau kepanjangan) ---
        panel_luar = ttk.Frame(self.paned, width=340)
        panel_luar.pack_propagate(False)
        self.paned.add(panel_luar, weight=0)

        kontrol_canvas = tk.Canvas(panel_luar, highlightthickness=0, bg=WARNA_BG)
        kontrol_scrollbar = ttk.Scrollbar(panel_luar, orient="vertical", command=kontrol_canvas.yview)
        kontrol_canvas.configure(yscrollcommand=kontrol_scrollbar.set)
        kontrol_canvas.pack(side="left", fill="both", expand=True)
        kontrol_scrollbar.pack(side="right", fill="y")

        tab_kontrol = ttk.Frame(kontrol_canvas)
        kontrol_window = kontrol_canvas.create_window((0, 0), window=tab_kontrol, anchor="nw")

        def _kontrol_on_configure(event):
            kontrol_canvas.configure(scrollregion=kontrol_canvas.bbox("all"))
        tab_kontrol.bind("<Configure>", _kontrol_on_configure)

        def _kontrol_canvas_resize(event):
            kontrol_canvas.itemconfig(kontrol_window, width=event.width)
        kontrol_canvas.bind("<Configure>", _kontrol_canvas_resize)

        def _kontrol_mousewheel(event):
            kontrol_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._pasang_scroll_mousewheel(kontrol_canvas, _kontrol_mousewheel)

        # --- Panel kanan: notebook berisi tab-tab peta hasil perhitungan ---
        # Dibungkus 1 wrapper frame supaya gambar sambutan bg.png bisa
        # ditumpuk DI ATAS notebook (overlay lewat place(), lihat
        # _tampilkan_tab_awal) -- BUKAN dijadikan tab notebook sungguhan,
        # supaya tidak ikut mendapat tombol × bawaan ClosableNotebook.
        wrapper_notebook = ttk.Frame(self.paned)
        self.paned.add(wrapper_notebook, weight=1)
        self.notebook = ClosableNotebook(wrapper_notebook)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_ganti_tab_notebook)
        self.notebook.bind("<<NotebookTabClosed>>", self._on_tab_notebook_ditutup)
        self.notebook.bind("<Button-3>", self._on_right_click_tab)
        self.notebook.bind("<Button-2>", self._on_right_click_tab)

        # Overlay sambutan bg.png -- tampil duluan, MENUTUPI notebook,
        # SEBELUM ada tab lain apapun (peta/Waktu Sholat/dsb). Otomatis
        # dilepas nanti begitu tab "sungguhan" pertama ditambahkan
        # (lihat _hapus_tab_awal).
        self._label_bg_awal = None
        self._tampilkan_tab_awal(wrapper_notebook)

        # --- Log status (selalu tampil paling atas, di LUAR akordeon --
        #     dipakai bersama oleh perhitungan Hilal maupun Waktu Sholat.
        #     Sengaja diletakkan di atas, sebelum kedua akordeon, supaya
        #     selalu langsung terlihat/terbaca -- tidak ketutup/terdorong
        #     ke bawah kalau salah satu akordeon dibuka.) ---
        frame_log = ttk.LabelFrame(tab_kontrol, text="Status")
        frame_log.pack(fill="x", **pad)
        self.text_log = tk.Text(
            frame_log, height=8, wrap="word", state="disabled",
            bg=WARNA_PANEL, fg=WARNA_TEKS, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=WARNA_BORDER, highlightcolor=WARNA_AKSEN,
            font=FONT_UTAMA, padx=2, pady=2)
        self.text_log.pack(fill="both", expand=True, padx=6, pady=6)

        # =====================================================
        # Bilah kiri (tab_kontrol) diringkas jadi 2 bagian akordeon yang
        # bisa dilipat/dibuka -- bagian Hilal & bagian Waktu Sholat TIDAK
        # lagi jadi 2 panel terpisah berdampingan, tapi ditumpuk di SATU
        # bilah paling kiri yang sama. Bagian yang relevan dengan tab
        # notebook yang sedang aktif otomatis terbuka (lihat
        # _on_ganti_tab_notebook), bagian yang lain otomatis terlipat
        # supaya tidak makan tempat & tidak membingungkan. Keduanya
        # sengaja mulai dalam keadaan TERTUTUP (buka_awal=False) saat
        # aplikasi baru dibuka -- baru terbuka begitu user benar-benar
        # berinteraksi (mis. pindah tab notebook).
        # =====================================================
        body_hilal, self._buka_akordeon_hilal, self._tutup_akordeon_hilal = \
            self._buat_bagian_akordeon(
                tab_kontrol, "🌙 Visibilitas",
                buka_awal=False,
                on_open=lambda: (self._tutup_akordeon_sholat(), self._tutup_akordeon_gerhana(),
                                  self._tutup_akordeon_kalbanding(), self._tutup_akordeon_konverter(),
                                  self._tutup_akordeon_efemeris()))

        # --- Langkah 0: mode perhitungan ---
        frame0 = ttk.LabelFrame(body_hilal, text="0. Mode Perhitungan")
        frame0.pack(fill="x", **pad)

        self.radio_jpl = ttk.Radiobutton(
            frame0, text="Presisi (JPL DE421 — perlu unduh ±17 MB sekali)",
            value="jpl", variable=self.mode, command=self._on_ganti_mode)
        self.radio_ringan = ttk.Radiobutton(
            frame0, text="Ringan (VSOP87 + ELP2000-82B — tanpa unduh apa pun)",
            value="ringan", variable=self.mode, command=self._on_ganti_mode)
        self.radio_jpl.grid(row=0, column=0, padx=10, pady=4, sticky="w")
        self.radio_ringan.grid(row=1, column=0, padx=10, pady=4, sticky="w")
        ttk.Label(
            frame0,
            text="Catatan: mode Ringan akurat sampai beberapa detik busur "
                 "(setara JPL untuk kriteria hilal umum). Untuk kasus PKG 2 yang\n"
                 "sangat mepet ambang (selisih hanya beberapa detik busur), "
                 "gunakan mode Presisi untuk kepastian ekstra.",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
        ).grid(row=2, column=0, padx=10, pady=(0, 4), sticky="w")
        ttk.Button(
            frame0, text="⚙ Kelola Kernel JPL...", command=self._on_kelola_kernel_jpl,
        ).grid(row=3, column=0, padx=10, pady=(0, 8), sticky="w")

        # --- Langkah 1: tahun ---
        frame1 = ttk.LabelFrame(body_hilal, text="1. Pilih Tahun")
        frame1.pack(fill="x", **pad)

        ttk.Label(frame1, text="Tahun Masehi:").grid(row=0, column=0, padx=6, pady=6)
        self.entry_tahun = ttk.Entry(frame1, width=10)
        self.entry_tahun.insert(0, str(datetime.now().year))
        self.entry_tahun.grid(row=0, column=1, padx=6, pady=6)

        self.btn_cari = ttk.Button(frame1, text="Cari Ijtimak", command=self._on_cari_ijtimak,
                                    state="disabled", style="Aksen.TButton")
        self.btn_cari.grid(row=0, column=2, padx=6, pady=6)

        # --- Langkah 2: pilih ijtimak ---
        frame2 = ttk.LabelFrame(body_hilal, text="2. Pilih Ijtimak (Konjungsi)")
        frame2.pack(fill="both", **pad)

        list_container = ttk.Frame(frame2)
        list_container.pack(fill="both", expand=True, padx=6, pady=6)

        scrollbar = ttk.Scrollbar(list_container)
        scrollbar.pack(side="right", fill="y")

        self.listbox_ijtimak = tk.Listbox(
            list_container, height=6, yscrollcommand=scrollbar.set,
            bg=WARNA_PANEL, fg=WARNA_TEKS, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=WARNA_BORDER, highlightcolor=WARNA_AKSEN,
            selectbackground=WARNA_AKSEN, selectforeground="white", font=FONT_UTAMA)
        self.listbox_ijtimak.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox_ijtimak.yview)

        # --- Langkah 3: hari ---
        frame3 = ttk.LabelFrame(body_hilal, text="3. Pilih Tanggal Peta")
        frame3.pack(fill="x", **pad)

        self.pilihan_hari = tk.StringVar(value="ijtimak")
        self.radio_ijtimak = ttk.Radiobutton(frame3, text="Hari Ijtimak", value="ijtimak",
                                              variable=self.pilihan_hari)
        self.radio_setelah = ttk.Radiobutton(frame3, text="Sehari Setelah Ijtimak", value="setelah",
                                              variable=self.pilihan_hari)
        self.radio_ijtimak.grid(row=0, column=0, padx=10, pady=6, sticky="w")
        self.radio_setelah.grid(row=0, column=1, padx=10, pady=6, sticky="w")

        # --- Langkah 4: pilihan kriteria ---
        frame4 = ttk.LabelFrame(body_hilal, text="4. Pilihan Kriteria Peta")
        frame4.pack(fill="x", **pad)

        chk_opts = {
            "bg": WARNA_BG,
            "fg": WARNA_TEKS,
            "activebackground": WARNA_BG,
            "activeforeground": WARNA_TEKS,
            "selectcolor": WARNA_PANEL,
            "font": FONT_UTAMA,
            "bd": 0,
            "highlightthickness": 0,
            "anchor": "w"
        }
        self.chk_mabims = tk.Checkbutton(frame4, text="MABIMS Global", variable=self.hitung_mabims, **chk_opts)
        self.chk_khgt = tk.Checkbutton(frame4, text="KHGT Global", variable=self.hitung_khgt, **chk_opts)
        self.chk_alt = tk.Checkbutton(frame4, text="Alt Lokal (RI)", variable=self.hitung_alt, **chk_opts)
        self.chk_elong = tk.Checkbutton(frame4, text="Elongasi Lokal (RI)", variable=self.hitung_elong, **chk_opts)

        self.chk_mabims.grid(row=0, column=0, padx=10, pady=4, sticky="w")
        self.chk_khgt.grid(row=1, column=0, padx=10, pady=4, sticky="w")
        self.chk_alt.grid(row=2, column=0, padx=10, pady=4, sticky="w")
        self.chk_elong.grid(row=3, column=0, padx=10, pady=4, sticky="w")

        self.btn_proses = ttk.Button(body_hilal, text="Tampilkan Peta", command=self._on_proses,
                                      state="disabled", style="Aksen.TButton")
        self.btn_proses.pack(pady=8)

        # --- Bagian akordeon ke-2: input Waktu Sholat & Kiblat (terlipat
        #     di awal -- baru terbuka otomatis begitu tab "Waktu Sholat &
        #     Kiblat" dipilih, lihat _on_ganti_tab_notebook) ---
        self._body_akordeon_sholat, self._buka_akordeon_sholat, self._tutup_akordeon_sholat = \
            self._buat_bagian_akordeon(
                tab_kontrol, "🕌 Waktu Sholat & Kiblat",
                buka_awal=False,
                on_open=lambda: (self._tutup_akordeon_hilal(), self._tutup_akordeon_gerhana(),
                                  self._tutup_akordeon_kalbanding(), self._tutup_akordeon_konverter(),
                                  self._tutup_akordeon_efemeris()))

        # --- Tab tambahan: Waktu Sholat & Arah Kiblat (permanen, selalu ada) ---
        self._bangun_tab_sholat()

        # --- Bagian akordeon ke-3: input Gerhana Matahari (terlipat di
        #     awal juga -- terbuka otomatis begitu tab peta gerhana dipilih,
        #     lihat _on_ganti_tab_notebook). Membuka bagian ini otomatis
        #     melipat 2 bagian lainnya (Hilal & Sholat), begitu juga
        #     sebaliknya -- supaya bilah kiri tetap ringkas. ---
        self._body_akordeon_gerhana, self._buka_akordeon_gerhana, self._tutup_akordeon_gerhana = \
            self._buat_bagian_akordeon(
                tab_kontrol, "☀️ Gerhana",
                buka_awal=False,
                on_open=lambda: (self._tutup_akordeon_hilal(), self._tutup_akordeon_sholat(),
                                  self._tutup_akordeon_kalbanding(), self._tutup_akordeon_konverter(),
                                  self._tutup_akordeon_efemeris()))
        self._bangun_akordeon_gerhana(self._body_akordeon_gerhana, pad)

        # --- Bagian akordeon ke-4: Perbandingan Kalender MABIMS vs KHGT
        #     Muhammadiyah (terlipat di awal juga -- terbuka otomatis begitu
        #     tab hasil perbandingan dipilih, lihat _on_ganti_tab_notebook).
        #     Memakai bandingkan_kalender_mabims_khgt() yang sudah ada
        #     (murni logika astronomi, TIDAK diubah) -- bagian ini cuma
        #     pembungkus GUI-nya (input tahun H + tombol, tabel hasil). ---
        self._body_akordeon_kalbanding, self._buka_akordeon_kalbanding, self._tutup_akordeon_kalbanding = \
            self._buat_bagian_akordeon(
                tab_kontrol, "📅 Perbandingan Kalender",
                buka_awal=False,
                on_open=lambda: (self._tutup_akordeon_hilal(), self._tutup_akordeon_sholat(),
                                  self._tutup_akordeon_gerhana(), self._tutup_akordeon_konverter(),
                                  self._tutup_akordeon_efemeris()))
        self._bangun_akordeon_kalbanding(self._body_akordeon_kalbanding, pad)

        # --- Tab hasil perbandingan (permanen, sama seperti tab Waktu
        #     Sholat -- dibangun sekali di sini, baru dimunculkan di
        #     notebook kanan begitu tombol "Bandingkan" pertama kali
        #     menghasilkan sesuatu, lihat _pastikan_tab_kalbanding_tampil). ---
        self._bangun_tab_kalbanding()

        # --- Bagian akordeon ke-5: Konverter Kalender Masehi <-> Hijriyah
        #     (terlipat di awal juga, mengikuti pola 4 akordeon sebelumnya).
        #     Konversi murni tabular/urfi (siklus 30-tahun tipe IIa/Kuwaiti),
        #     BUKAN hasil rukyat/hisab ijtimak -- cukup akurat & instan utk
        #     kebutuhan konversi tanggal sehari-hari (bukan penentuan awal
        #     bulan resmi, yang tetap memakai bagian 🌙 Visibilitas /
        #     📅 Perbandingan Kalender di atas). Tidak perlu tab notebook
        #     terpisah -- hasilnya cukup ditampilkan sbg label di badan
        #     akordeon ini sendiri (tidak ada peta/tabel yang perlu digambar). ---
        self._body_akordeon_konverter, self._buka_akordeon_konverter, self._tutup_akordeon_konverter = \
            self._buat_bagian_akordeon(
                tab_kontrol, "🔄 Konverter Kalender",
                buka_awal=False,
                on_open=lambda: (self._tutup_akordeon_hilal(), self._tutup_akordeon_sholat(),
                                  self._tutup_akordeon_gerhana(), self._tutup_akordeon_kalbanding(),
                                  self._tutup_akordeon_efemeris()))
        self._bangun_akordeon_konverter(self._body_akordeon_konverter, pad)

        # --- Bagian akordeon ke-6: Tabel Efemeris (posisi Matahari & Bulan
        #     tiap interval waktu dalam satu hari -- azimuth, tinggi/altitude
        #     apparent, deklinasi, elongasi & fraksi iluminasi Bulan). Sama
        #     pola dengan akordeon Perbandingan Kalender: input di sini,
        #     hasilnya tabel di tab notebook kanan (permanen, dibangun oleh
        #     _bangun_tab_efemeris()). ---
        self._body_akordeon_efemeris, self._buka_akordeon_efemeris, self._tutup_akordeon_efemeris = \
            self._buat_bagian_akordeon(
                tab_kontrol, "📊 Tabel Efemeris",
                buka_awal=False,
                on_open=lambda: (self._tutup_akordeon_hilal(), self._tutup_akordeon_sholat(),
                                  self._tutup_akordeon_gerhana(), self._tutup_akordeon_kalbanding(),
                                  self._tutup_akordeon_konverter()))
        self._bangun_akordeon_efemeris(self._body_akordeon_efemeris, pad)
        self._bangun_tab_efemeris()

    def _on_ganti_tab_notebook(self, event=None):
        """Dipanggil tiap kali tab notebook kanan (peta/Waktu Sholat)
        berganti. Bagian akordeon di bilah kiri yang relevan dengan tab
        yang sedang dilihat otomatis dibuka, bagian yang lain otomatis
        dilipat -- supaya bilah kiri tetap ringkas, bukan 2 panel penuh
        berdampingan seperti sebelumnya."""
        try:
            tab_terpilih = self.notebook.tab(self.notebook.select(), "text")
        except tk.TclError:
            return
        if "Sholat" in tab_terpilih or "Kiblat" in tab_terpilih:
            self._buka_akordeon_sholat()
            self._tutup_akordeon_hilal()
            self._tutup_akordeon_gerhana()
            self._tutup_akordeon_kalbanding()
        elif "Gerhana" in tab_terpilih:
            self._buka_akordeon_gerhana()
            self._tutup_akordeon_hilal()
            self._tutup_akordeon_sholat()
            self._tutup_akordeon_kalbanding()
        elif "Perbandingan" in tab_terpilih:
            self._buka_akordeon_kalbanding()
            self._tutup_akordeon_hilal()
            self._tutup_akordeon_sholat()
            self._tutup_akordeon_gerhana()
            self._tutup_akordeon_efemeris()
        elif "Efemeris" in tab_terpilih:
            self._buka_akordeon_efemeris()
            self._tutup_akordeon_hilal()
            self._tutup_akordeon_sholat()
            self._tutup_akordeon_gerhana()
            self._tutup_akordeon_kalbanding()
        else:
            # Tab peta Hilal (MABIMS, Muhammadiyah, Indonesia, dll)
            self._buka_akordeon_hilal()
            self._tutup_akordeon_sholat()
            self._tutup_akordeon_gerhana()
            self._tutup_akordeon_kalbanding()
            self._tutup_akordeon_efemeris()

    def _on_tab_notebook_ditutup(self, event=None):
        """Dipanggil begitu user klik tombol × di salah satu tab notebook
        kanan (lihat ClosableNotebook). Membersihkan state yang terkait
        dengan tab tsb, lalu benar-benar melepasnya dari notebook."""
        tab_id = self.notebook.tab_ditutup_terakhir
        if not tab_id:
            return
        self.notebook.tab_ditutup_terakhir = None
        self._tutup_tab_by_id(tab_id)

    def _tutup_tab_by_id(self, tab_id):
        """Menutup tab berdasarkan ID widget tab-nya. Membersihkan figure
        matplotlib dan state peta jika tab tersebut adalah tab peta."""
        try:
            widget_tab = self.nametowidget(tab_id)
        except (KeyError, tk.TclError):
            widget_tab = None

        if widget_tab is not None and widget_tab is getattr(self, "_frame_sholat", None):
            # Tab "Waktu Sholat & Kiblat" cuma DISEMBUNYIKAN (bukan
            # dihancurkan) -- isinya (input lokasi, hasil terakhir, dll)
            # tetap dipertahankan, dan tab ini otomatis muncul lagi begitu
            # user menekan salah satu tombol Hitung di bagian Waktu Sholat
            # (lihat _pastikan_tab_sholat_tampil).
            self.notebook.forget(tab_id)
            self._tab_sholat_ditambahkan = False
            return

        if widget_tab is not None and widget_tab is getattr(self, "_frame_kalbanding", None):
            # Sama seperti tab Waktu Sholat & Kiblat di atas -- tab
            # "Perbandingan Kalender" cuma disembunyikan, tabel hasil
            # terakhir tetap dipertahankan (lihat _pastikan_tab_kalbanding_tampil).
            self.notebook.forget(tab_id)
            self._tab_kalbanding_ditambahkan = False
            return

        if widget_tab is not None and widget_tab is getattr(self, "_frame_efemeris", None):
            # Sama seperti tab Perbandingan Kalender di atas -- tab
            # "Tabel Efemeris" cuma disembunyikan, tabel hasil terakhir
            # tetap dipertahankan (lihat _pastikan_tab_efemeris_tampil).
            self.notebook.forget(tab_id)
            self._tab_efemeris_ditambahkan = False
            return

        # Selain itu berarti salah satu tab peta (mabims/muhammadiyah/
        # id_tinggi/id_elongasi) -- hancurkan frame & figure-nya sekalian,
        # lalu hapus dari self._tab_peta supaya kalau "Tampilkan Peta"
        # ditekan lagi nanti, tab tsb dibuat ulang dari nol (bukan dianggap
        # masih ada).
        nama_tab_ditemukan = None
        for nama_tab, info in list(self._tab_peta.items()):
            if str(info["frame"]) == tab_id:
                nama_tab_ditemukan = nama_tab
                break

        self.notebook.forget(tab_id)

        if nama_tab_ditemukan is not None:
            info = self._tab_peta.pop(nama_tab_ditemukan)
            if info.get("fig") is not None:
                try:
                    plt.close(info["fig"])
                except Exception:
                    pass

        if widget_tab is not None:
            try:
                widget_tab.destroy()
            except tk.TclError:
                pass

    def _on_right_click_tab(self, event):
        """Menampilkan menu konteks klik kanan pada tab (Tutup Tab,
        Tutup Tab Lain, Tutup Semua Tab)."""
        element = self.notebook.identify(event.x, event.y)
        if "client" in element:
            return  # Klik di dalam area konten tab, bukan di tab header

        try:
            clicked_index = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return  # Klik di luar tab header (misalnya di sisa area kosong bilah tab)

        all_tabs = self.notebook.tabs()
        if not all_tabs:
            return

        clicked_tab_id = all_tabs[clicked_index]

        # Membuat menu konteks styled
        menu = tk.Menu(self.notebook, tearoff=0, bg=WARNA_PANEL, fg=WARNA_TEKS,
                       activebackground=WARNA_AKSEN, activeforeground="white")

        menu.add_command(label="Tutup Tab",
                         command=lambda: self._tutup_tab_by_id(clicked_tab_id))
        menu.add_command(label="Tutup Tab Lain",
                         command=lambda: self._tutup_tab_lain(clicked_tab_id))
        menu.add_command(label="Tutup Semua Tab",
                         command=self._tutup_semua_tab)

        menu.post(event.x_root, event.y_root)

    def _tutup_tab_lain(self, keep_tab_id):
        """Menutup semua tab kecuali tab yang ditentukan."""
        all_tabs = list(self.notebook.tabs())
        for tab_id in all_tabs:
            if tab_id != keep_tab_id:
                self._tutup_tab_by_id(tab_id)

    def _tutup_semua_tab(self):
        """Menutup seluruh tab yang terbuka."""
        all_tabs = list(self.notebook.tabs())
        for tab_id in all_tabs:
            self._tutup_tab_by_id(tab_id)

    # ---------------- Tab peta (di jendela utama, bukan popup) ----------------

    def _tab_peta_frame(self, nama_tab, judul_tab):
        """Ambil frame tab untuk satu peta (mis. 'mabims'/'muhammadiyah').
        Jika tab dengan nama tsb sudah pernah dibuat sebelumnya, isinya
        dibersihkan dulu (dan figure lamanya ditutup) supaya saat user
        menekan "Tampilkan Peta" berkali-kali, kanvas lama diGANTI —
        bukan menumpuk tab-tab baru terus-menerus."""
        info = self._tab_peta.get(nama_tab)
        if info is not None:
            for widget in info["frame"].winfo_children():
                widget.destroy()
            if info.get("fig") is not None:
                plt.close(info["fig"])
            self.notebook.tab(info["frame"], text=judul_tab)
            return info["frame"]

        self._hapus_tab_awal()
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=judul_tab)
        self._tab_peta[nama_tab] = {"frame": frame, "fig": None}
        return frame

    def _tampilkan_peta(self, nama_tab, judul_tab, fig):
        """Gambar sebuah figure matplotlib (lengkap dengan toolbar zoom/pan/
        simpan bawaan) langsung di dalam tab jendela utama."""
        frame = self._tab_peta_frame(nama_tab, judul_tab)
        canvas = FigureCanvasTkAgg(fig, master=frame)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, frame)
        toolbar.update()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._tab_peta[nama_tab]["fig"] = fig
        return frame

    def _pastikan_tab_sholat_tampil(self):
        """Munculkan tab 'Waktu Sholat & Kiblat' di notebook kanan kalau
        belum pernah dimunculkan (dipanggil pertama kali user menekan
        salah satu tombol Hitung di bagian akordeon Waktu Sholat), lalu
        pindah ke tab tsb supaya hasilnya langsung terlihat."""
        if not self._tab_sholat_ditambahkan:
            self._hapus_tab_awal()
            self.notebook.add(self._frame_sholat, text="🕌 Waktu Sholat & Kiblat")
            self._tab_sholat_ditambahkan = True
        self.notebook.select(self._frame_sholat)

    def _log(self, pesan):
        self.text_log.configure(state="normal")
        self.text_log.insert("end", pesan + "\n")
        self.text_log.see("end")
        self.text_log.configure(state="disabled")

    # ---------------- Gerhana Matahari (kandidat & lintasan) ----------------

    def _bangun_akordeon_gerhana(self, body, pad):
        """Isi badan akordeon "☀️ Gerhana Matahari/🌑 Bulan": pilih tahun ->
        cari kandidat (cari_gerhana_matahari_kandidat_ringan() /
        cari_gerhana_bulan_kandidat_ringan(), memakai self.mode/self.ts/
        self.eph global -- SAMA seperti bagian 🌙 Visibilitas & tab lain,
        lihat _on_ganti_mode) -> pilih satu kandidat -> tampilkan peta
        lintasan/visibilitas (hitung_lintasan_gerhana_matahari + buat_figure_*,
        sudah ada, tidak diubah sama sekali di sini -- method2 di bawah cuma
        pembungkus GUI-nya."""

        ttk.Label(
            body,
            text="Deteksi kandidat gerhana matahari/bulan sepanjang satu "
                 "tahun Masehi, lalu gambar peta lintasan/visibilitasnya "
                 "untuk kandidat yang terpilih. Memakai Mode Perhitungan "
                 "yang sama seperti bagian 🌙 Visibilitas di atas "
                 "(Ringan/Presisi).",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
            wraplength=280,
        ).pack(fill="x", padx=10, pady=(4, 6))

        # --- Langkah 1: jenis & tahun ---
        frame1 = ttk.LabelFrame(body, text="1. Pilih Jenis & Tahun")
        frame1.pack(fill="x", **pad)

        # Pilihan jenis gerhana yang dicari -- menentukan fungsi pencarian
        # (cari_gerhana_matahari_kandidat_ringan vs cari_gerhana_bulan_kandidat_ringan)
        # dan figure-builder mana yang dipakai nanti di _on_tampilkan_gerhana.
        self.var_jenis_gerhana = tk.StringVar(value="matahari")
        frame_jenis = ttk.Frame(frame1)
        frame_jenis.grid(row=0, column=0, columnspan=3, padx=6, pady=(6, 0), sticky="w")
        ttk.Radiobutton(frame_jenis, text="☀️ Matahari", value="matahari",
                         variable=self.var_jenis_gerhana).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(frame_jenis, text="🌑 Bulan", value="bulan",
                         variable=self.var_jenis_gerhana).pack(side="left")

        ttk.Label(frame1, text="Tahun Masehi:").grid(row=1, column=0, padx=6, pady=6)
        self.entry_tahun_gerhana = ttk.Entry(frame1, width=10)
        self.entry_tahun_gerhana.insert(0, str(datetime.now().year))
        self.entry_tahun_gerhana.grid(row=1, column=1, padx=6, pady=6)

        self.btn_cari_gerhana = ttk.Button(
            frame1, text="Cari Gerhana", command=self._on_cari_gerhana,
            style="Aksen.TButton")
        self.btn_cari_gerhana.grid(row=1, column=2, padx=6, pady=6)

        # Mode tampilan peta: "datar" (PlateCarree, seluruh dunia sekali
        # pandang) atau "globe" (Orthographic, dipusatkan PERSIS di titik
        # greatest eclipse -- lihat buat_figure_lintasan_gerhana_matahari/
        # buat_figure_visibilitas_gerhana_bulan, param mode_peta sudah ada
        # di sana, di sini cuma menyalurkan pilihan user ke situ).
        #
        # Dipecah jadi label + radio DI BARIS TERPISAH (bukan satu baris
        # panjang) -- panel akordeon di sidebar cuma ~280px, sedangkan versi
        # satu-baris sebelumnya (label + 2 radio, salah satunya berteks
        # "Globe (pusat greatest eclipse)") melebihi itu & kepotong/tumpang
        # tindih di tepi panel.
        self.var_mode_peta_gerhana = tk.StringVar(value="datar")
        ttk.Label(frame1, text="Tampilan peta:").grid(
            row=2, column=0, columnspan=3, padx=6, pady=(4, 0), sticky="w")
        frame_mode_peta = ttk.Frame(frame1)
        frame_mode_peta.grid(row=3, column=0, columnspan=3, padx=6, pady=(0, 2), sticky="w")
        ttk.Radiobutton(frame_mode_peta, text="🗺️ Datar", value="datar",
                         variable=self.var_mode_peta_gerhana).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(frame_mode_peta, text="🌐 Globe", value="globe",
                         variable=self.var_mode_peta_gerhana).pack(side="left")
        ttk.Label(frame1, text="(mode Globe dipusatkan di titik greatest eclipse)",
                  font=FONT_KECIL, foreground=WARNA_TEKS_MUTED).grid(
            row=4, column=0, columnspan=3, padx=6, pady=(0, 6), sticky="w")

        # --- Langkah 2: pilih kandidat ---
        frame2 = ttk.LabelFrame(body, text="2. Pilih Gerhana")
        frame2.pack(fill="both", **pad)

        list_container = ttk.Frame(frame2)
        list_container.pack(fill="both", expand=True, padx=6, pady=6)

        scrollbar = ttk.Scrollbar(list_container)
        scrollbar.pack(side="right", fill="y")

        self.listbox_gerhana = tk.Listbox(
            list_container, height=6, yscrollcommand=scrollbar.set,
            bg=WARNA_PANEL, fg=WARNA_TEKS, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=WARNA_BORDER, highlightcolor=WARNA_AKSEN,
            selectbackground=WARNA_AKSEN, selectforeground="white", font=FONT_UTAMA)
        self.listbox_gerhana.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox_gerhana.yview)

        ttk.Label(
            frame2,
            text="🔴 Total/Cincin = lintasan garis tengah tersedia.\n"
                 "🟡 Parsial saja = umbra tidak menyentuh Bumi, tanpa lintasan.",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
        ).pack(fill="x", padx=6, pady=(0, 6))

        # --- Langkah 3: tampilkan peta lintasan ---
        self.btn_tampilkan_gerhana = ttk.Button(
            body, text="Tampilkan Peta Lintasan", command=self._on_tampilkan_gerhana,
            state="disabled", style="Aksen.TButton")
        self.btn_tampilkan_gerhana.pack(pady=8)

    def _on_cari_gerhana(self):
        teks_tahun = self.entry_tahun_gerhana.get().strip()
        if not (teks_tahun.isdigit() and len(teks_tahun) == 4):
            messagebox.showerror("Input tidak valid", "Masukkan angka tahun 4 digit, mis. 2026.")
            return

        tahun = int(teks_tahun)
        jenis = self.var_jenis_gerhana.get()  # "matahari" atau "bulan"
        mode = self.mode.get()

        if mode == "jpl" and self.eph is None:
            messagebox.showwarning(
                "Ephemeris belum siap",
                "Mode Presisi (JPL DE421) dipilih di bagian Visibilitas, tapi "
                "ephemeris-nya belum selesai dimuat. Tunggu sebentar, atau "
                "beralih ke Mode Ringan dulu di bagian 🌙 Visibilitas.")
            return

        self.btn_cari_gerhana.config(state="disabled")
        self.btn_tampilkan_gerhana.config(state="disabled")
        self.listbox_gerhana.delete(0, "end")
        self.kandidat_gerhana = []
        label_jenis = "matahari" if jenis == "matahari" else "bulan"
        self._log(f"Mencari kandidat gerhana {label_jenis} tahun {tahun}...")

        threading.Thread(target=self._cari_gerhana_thread, args=(tahun, jenis, mode), daemon=True).start()

    def _cari_gerhana_thread(self, tahun, jenis="matahari", mode="ringan"):
        try:
            # Fungsi astronomi (sudah ada, tidak diubah) -- mode/self.ts/
            # self.eph diteruskan apa adanya ke fungsi kandidat yang sesuai.
            # jenis menentukan fungsi mana yang dipanggil: gerhana Matahari
            # (ijtimak) atau gerhana Bulan (istiqbal).
            if jenis == "bulan":
                kandidat_mentah = cari_gerhana_bulan_kandidat_ringan(
                    tahun, mode=mode, ts=self.ts, eph=self.eph)
            else:
                kandidat_mentah = cari_gerhana_matahari_kandidat_ringan(
                    tahun, mode=mode, ts=self.ts, eph=self.eph)
            self.antrian.put(("gerhana_ok", (jenis, kandidat_mentah, mode)))
        except Exception as e:
            # Sengaja pakai jenis pesan KHUSUS ("gerhana_error"), bukan "error"
            # generik -- handler "error" generik cuma me-re-enable btn_cari
            # (Hilal) & btn_proses, TIDAK menyentuh btn_cari_gerhana, sehingga
            # tombol ini akan macet ter-disabled selamanya kalau error dikirim
            # lewat jalur generik.
            self.antrian.put(("gerhana_error", f"Gagal mencari kandidat gerhana: {e}"))

    def _on_tampilkan_gerhana(self):
        seleksi = self.listbox_gerhana.curselection()
        if not seleksi:
            messagebox.showwarning("Belum dipilih",
                                    "Pilih salah satu kandidat gerhana dari daftar terlebih dahulu.")
            return

        idx = seleksi[0]
        kandidat = self.kandidat_gerhana[idx]

        self.btn_tampilkan_gerhana.config(state="disabled")
        self._log("Menghitung & menggambar peta gerhana "
                   f"{format_waktu_ijtimak(kandidat['waktu_greatest_eclipse'])}...")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            # buat_figure_lintasan_gerhana_matahari()/buat_figure_visibilitas_
            # gerhana_bulan() sudah ada -- di sini murni memanggil yang sesuai
            # jenis (self._jenis_gerhana_terakhir) lalu menaruh hasilnya ke tab
            # notebook, dengan pola yang sama seperti _tampilkan_peta untuk peta
            # Hilal (MABIMS/Muhammadiyah). Perhitungannya ringan (hanya scan
            # beberapa jam di sekitar greatest eclipse, bukan grid dunia penuh)
            # sehingga aman dipanggil langsung di sini (thread utama) tanpa
            # perlu thread terpisah -- konsisten dgn aturan app ini bahwa SEMUA
            # pembuatan figure matplotlib harus di thread utama.
            tgl_str = kandidat["waktu_greatest_eclipse"].strftime("%d %B %Y")
            mode_peta = self.var_mode_peta_gerhana.get()  # "datar" atau "globe"
            # mode/ts/eph SAMA dgn yang dipakai mencari kandidat ini (lihat
            # catatan self._mode_gerhana_terakhir) -- bukan self.mode saat ini,
            # supaya tidak ada mismatch presisi kalau user sempat ganti mode
            # di antara "Cari Gerhana" & "Tampilkan Peta Lintasan".
            mode = self._mode_gerhana_terakhir
            if self._jenis_gerhana_terakhir == "bulan":
                fig = buat_figure_visibilitas_gerhana_bulan(
                    kandidat, mode_peta=mode_peta, mode=mode, ts=self.ts, eph=self.eph)
                judul_tab = f"🌑 Gerhana Bulan — {tgl_str}"
            else:
                fig = buat_figure_lintasan_gerhana_matahari(
                    kandidat, mode_peta=mode_peta, mode=mode, ts=self.ts, eph=self.eph)
                judul_tab = f"☀️ Gerhana Matahari — {tgl_str}"
            frame = self._tampilkan_peta("gerhana", judul_tab, fig)
            self.notebook.select(frame)
            self._log("Peta gerhana selesai ditampilkan.")
        except Exception as e:
            self._log(f"ERROR: {e}")
            messagebox.showerror("Terjadi kesalahan", str(e))
        finally:
            self.config(cursor="")
            self.btn_tampilkan_gerhana.config(state="normal")

    # ---------------- Perbandingan Kalender MABIMS vs KHGT Muhammadiyah ----------------

    def _bangun_akordeon_kalbanding(self, body, pad):
        """Isi badan akordeon "📅 Perbandingan Kalender": pilih tahun
        Hijriyah -> tombol "Bandingkan" memanggil bandingkan_kalender_mabims_khgt()
        (sudah ada, tidak diubah sama sekali di sini -- method2 di bawah
        cuma pembungkus GUI-nya) -> hasilnya (12 bulan, tanggal awal bulan
        versi MABIMS vs KHGT, ditandai kalau beda) ditampilkan sbg tabel
        di tab notebook kanan."""

        ttk.Label(
            body,
            text="Bandingkan tanggal awal tiap bulan Hijriyah sepanjang satu "
                 "tahun H menurut kriteria MABIMS (Indonesia) vs KHGT "
                 "Muhammadiyah (global), dari ijtimak astronomis yang sama. "
                 "Baris yang berbeda tanggal ditandai merah muda.",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
            wraplength=280,
        ).pack(fill="x", padx=10, pady=(4, 6))

        frame1 = ttk.LabelFrame(body, text="1. Pilih Tahun Hijriyah")
        frame1.pack(fill="x", **pad)

        ttk.Label(frame1, text="Tahun Hijriyah:").grid(row=0, column=0, padx=6, pady=6)
        self.entry_tahun_kalbanding = ttk.Entry(frame1, width=10)
        # Perkiraan awal tahun H saat ini (rumus urfi yg sama dipakai di
        # beri_label_hijriyah -- cukup sbg NILAI AWAL isian, bukan sumber
        # tanggal, jadi tidak masalah kalau meleset 1 tahun).
        tahun_h_perkiraan = math.floor((datetime.now().year - 622) / 0.970229)
        self.entry_tahun_kalbanding.insert(0, str(tahun_h_perkiraan))
        self.entry_tahun_kalbanding.grid(row=0, column=1, padx=6, pady=6)

        self.btn_bandingkan_kalender = ttk.Button(
            frame1, text="Bandingkan", command=self._on_bandingkan_kalender,
            style="Aksen.TButton")
        self.btn_bandingkan_kalender.grid(row=0, column=2, padx=6, pady=6)

        ttk.Label(
            frame1,
            text="Memakai Mode Perhitungan yang sama seperti bagian "
                 "🌙 Visibilitas di atas (Ringan/Presisi).",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
            wraplength=280,
        ).grid(row=1, column=0, columnspan=3, padx=6, pady=(0, 6), sticky="w")

    def _on_bandingkan_kalender(self):
        teks_tahun = self.entry_tahun_kalbanding.get().strip()
        if not (teks_tahun.isdigit() and 1 <= len(teks_tahun) <= 4):
            messagebox.showerror("Input tidak valid", "Masukkan angka tahun Hijriyah, mis. 1447.")
            return

        tahun_h = int(teks_tahun)
        mode = self.mode.get()

        if mode == "jpl" and self.eph is None:
            messagebox.showwarning(
                "Ephemeris belum siap",
                "Mode Presisi (JPL DE421) dipilih di bagian Visibilitas, tapi "
                "ephemeris-nya belum selesai dimuat. Tunggu sebentar, atau "
                "beralih ke Mode Ringan dulu di bagian 🌙 Visibilitas.")
            return

        self.btn_bandingkan_kalender.config(state="disabled")
        self._log(f"\nMembandingkan kalender MABIMS vs KHGT Muhammadiyah untuk tahun {tahun_h} H...")

        threading.Thread(target=self._bandingkan_kalender_thread,
                          args=(tahun_h, mode), daemon=True).start()

    def _bandingkan_kalender_thread(self, tahun_h, mode):
        try:
            progress_cb = lambda msg: self.antrian.put(("progress", msg))
            hasil = bandingkan_kalender_mabims_khgt(
                tahun_h, ts=self.ts, eph=self.eph, mode=mode, progress_cb=progress_cb)
            self.antrian.put(("kalbanding_ok", (tahun_h, mode, hasil)))
        except Exception as e:
            self.antrian.put(("kalbanding_error", f"Gagal membandingkan kalender: {e}"))

    def _bangun_tab_kalbanding(self):
        """Bangun (sekali saja, permanen) frame tab hasil perbandingan
        kalender -- pola sama seperti _bangun_tab_sholat: dibuat di awal,
        baru dimasukkan ke notebook kanan begitu ada hasil pertama kali
        (lihat _pastikan_tab_kalbanding_tampil)."""
        frame = ttk.Frame(self.notebook)
        self._frame_kalbanding = frame

        panel_hasil = ttk.Frame(frame)
        panel_hasil.pack(fill="both", expand=True, padx=8, pady=8)

        self.label_judul_kalbanding = ttk.Label(
            panel_hasil, text="Belum ada perbandingan dihitung.", font=FONT_UTAMA_BOLD)
        self.label_judul_kalbanding.pack(anchor="w", pady=(0, 2))

        self.label_ringkasan_kalbanding = ttk.Label(
            panel_hasil, text="", foreground=WARNA_TEKS_MUTED)
        self.label_ringkasan_kalbanding.pack(anchor="w", pady=(0, 8))

        kolom = ("bulan_h", "waktu_ijtimak", "tanggal_mabims", "tanggal_khgt", "status")
        judul_kolom = {"bulan_h": "Bulan Hijriyah", "waktu_ijtimak": "Waktu Ijtimak (UTC)",
                        "tanggal_mabims": "Awal Bulan — MABIMS", "tanggal_khgt": "Awal Bulan — KHGT",
                        "status": "Status"}
        self.tree_kalbanding = ttk.Treeview(
            panel_hasil, columns=kolom, show="headings", height=14)
        for kunci in kolom:
            self.tree_kalbanding.heading(kunci, text=judul_kolom[kunci])
            lebar = 170 if kunci == "waktu_ijtimak" else (150 if "tanggal" in kunci else 130)
            self.tree_kalbanding.column(kunci, width=lebar, anchor="center")
        self.tree_kalbanding.tag_configure("beda", background="#FDECEA")
        self.tree_kalbanding.tag_configure("sama", background=WARNA_PANEL)

        scroll_tree_y = ttk.Scrollbar(panel_hasil, orient="vertical",
                                       command=self.tree_kalbanding.yview)
        self.tree_kalbanding.configure(yscrollcommand=scroll_tree_y.set)
        self.tree_kalbanding.pack(side="left", fill="both", expand=True)
        scroll_tree_y.pack(side="left", fill="y")

        panel_bawah = ttk.Frame(frame)
        panel_bawah.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(
            panel_bawah,
            text="🟥 Baris merah muda = tanggal awal bulan MABIMS dan KHGT berbeda "
                 "(potensi beda hari raya/awal bulan antar kriteria).",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED,
        ).pack(anchor="w")
        self.btn_simpan_csv_kalbanding = ttk.Button(
            panel_bawah, text="Simpan ke File .csv",
            command=self._on_simpan_csv_kalbanding, state="disabled")
        self.btn_simpan_csv_kalbanding.pack(anchor="e", pady=(6, 0))

    def _pastikan_tab_kalbanding_tampil(self):
        if not self._tab_kalbanding_ditambahkan:
            self._hapus_tab_awal()
            self.notebook.add(self._frame_kalbanding, text="📅 Perbandingan Kalender")
            self._tab_kalbanding_ditambahkan = True
        self.notebook.select(self._frame_kalbanding)

    def _tampilkan_kalbanding(self, tahun_h, mode, hasil):
        self._hasil_kalbanding_terakhir = (tahun_h, mode, hasil)

        self.tree_kalbanding.delete(*self.tree_kalbanding.get_children())
        jumlah_beda = 0
        for b in hasil:
            teks_mabims = b["tanggal_mabims"].strftime("%d %B %Y") if b["tanggal_mabims"] else "—"
            teks_khgt = b["tanggal_khgt"].strftime("%d %B %Y") if b["tanggal_khgt"] else "—"
            if b["beda"]:
                jumlah_beda += 1
                status, tag = "⚠️ Beda", "beda"
            else:
                status, tag = "✅ Sama", "sama"
            self.tree_kalbanding.insert("", "end", tags=(tag,), values=(
                f"{b['bulan_h']}. {b['nama_bulan_h']}",
                format_waktu_ijtimak(b["waktu_ijtimak"]),
                teks_mabims, teks_khgt, status,
            ))

        metode_label = "Presisi (Skyfield + JPL DE421)" if mode == "jpl" else "Ringan (VSOP87+ELP2000)"
        self.label_judul_kalbanding.config(
            text=f"Perbandingan Kalender {tahun_h} H — MABIMS vs KHGT Muhammadiyah "
                 f"({len(hasil)} bulan, metode: {metode_label})")
        if len(hasil) == 0:
            self.label_ringkasan_kalbanding.config(
                text="Tidak ditemukan bulan Hijriyah pada tahun tersebut.")
        else:
            self.label_ringkasan_kalbanding.config(
                text=f"{jumlah_beda} dari {len(hasil)} bulan berbeda tanggal awal bulan "
                     f"antara MABIMS dan KHGT Muhammadiyah.")

        self.btn_simpan_csv_kalbanding.config(state="normal" if hasil else "disabled")
        self._pastikan_tab_kalbanding_tampil()
        self._log(f"Perbandingan kalender {tahun_h} H selesai "
                   f"({jumlah_beda} dari {len(hasil)} bulan berbeda).")
        self.btn_bandingkan_kalender.config(state="normal")

    # =====================================================
    #  Akordeon ke-5: Konverter Kalender Masehi <-> Hijriyah
    # =====================================================
    def _bangun_akordeon_konverter(self, body, pad):
        """Isi badan akordeon "🔄 Konverter Kalender": dua arah konversi
        (Masehi -> Hijriyah / Hijriyah -> Masehi), dengan 3 pilihan kriteria:
        - Urfi (cepat): kalender tabular (masehi_ke_hijriyah_urfi() /
          hijriyah_urfi_ke_masehi()) -- instan, sinkron, tanpa astronomi.
        - MABIMS / KHGT Muhammadiyah: kriteria astronomis ASLI, dibangun di
          atas bandingkan_kalender_mabims_khgt() yg sudah ada lewat
          masehi_ke_hijriyah_kriteria()/hijriyah_kriteria_ke_masehi() --
          perlu dihitung di thread terpisah (bisa perlu beberapa detik,
          krn menjalankan pencarian ijtimak & evaluasi kriteria hilal
          sungguhan), pola sama seperti "📅 Perbandingan Kalender"."""

        ttk.Label(
            body,
            text="Konversi tanggal Masehi <-> Hijriyah. Kriteria Urfi = "
                 "kalender tabular (instan, perkiraan). Kriteria MABIMS/KHGT "
                 "= dari hasil hisab ijtimak & kriteria hilal ASLI (sama "
                 "seperti bagian 📅 Perbandingan Kalender), lebih akurat tapi "
                 "perlu waktu hitung beberapa detik.",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
            wraplength=280,
        ).pack(fill="x", padx=10, pady=(4, 6))

        self.arah_konverter = tk.StringVar(value="m2h")
        self.kriteria_konverter = tk.StringVar(value="urfi")

        frame_arah = ttk.LabelFrame(body, text="1. Arah Konversi")
        frame_arah.pack(fill="x", **pad)
        ttk.Radiobutton(
            frame_arah, text="Masehi → Hijriyah", value="m2h",
            variable=self.arah_konverter, command=self._on_ganti_arah_konverter,
        ).grid(row=0, column=0, padx=10, pady=4, sticky="w")
        ttk.Radiobutton(
            frame_arah, text="Hijriyah → Masehi", value="h2m",
            variable=self.arah_konverter, command=self._on_ganti_arah_konverter,
        ).grid(row=1, column=0, padx=10, pady=4, sticky="w")

        frame_kriteria = ttk.LabelFrame(body, text="2. Kriteria")
        frame_kriteria.pack(fill="x", **pad)
        ttk.Radiobutton(
            frame_kriteria, text="Urfi / Tabular (cepat, perkiraan)", value="urfi",
            variable=self.kriteria_konverter,
        ).grid(row=0, column=0, padx=10, pady=4, sticky="w")
        ttk.Radiobutton(
            frame_kriteria, text="MABIMS (Indonesia)", value="mabims",
            variable=self.kriteria_konverter,
        ).grid(row=1, column=0, padx=10, pady=4, sticky="w")
        ttk.Radiobutton(
            frame_kriteria, text="KHGT (Muhammadiyah)", value="khgt",
            variable=self.kriteria_konverter,
        ).grid(row=2, column=0, padx=10, pady=4, sticky="w")
        ttk.Label(
            frame_kriteria,
            text="MABIMS/KHGT memakai Mode Perhitungan yang sama seperti "
                 "bagian 🌙 Visibilitas di atas (Ringan/Presisi).",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
            wraplength=280,
        ).grid(row=3, column=0, padx=10, pady=(0, 4), sticky="w")

        frame_input = ttk.LabelFrame(body, text="3. Masukkan Tanggal")
        frame_input.pack(fill="x", **pad)
        # Catatan: panel kiri (tab_kontrol) lebarnya TETAP (fixed 340px,
        # lihat panel_luar), jadi field Hari/Bulan/Tahun TIDAK BOLEH
        # ditaruh berjajar dalam 1 baris x 6 kolom -- itu meluber ke
        # kanan dan bikin field "Tahun" ketutup/tenggelam di luar panel
        # (tidak kelihatan sama sekali). Ditumpuk 3 baris supaya selalu
        # muat berapa pun lebar panelnya.
        frame_input.columnconfigure(1, weight=1)

        sekarang = datetime.now()
        ttk.Label(frame_input, text="Hari:").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        self.entry_konv_hari = ttk.Entry(frame_input, width=10)
        self.entry_konv_hari.insert(0, str(sekarang.day))
        self.entry_konv_hari.grid(row=0, column=1, padx=6, pady=4, sticky="w")

        ttk.Label(frame_input, text="Bulan:").grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.entry_konv_bulan = ttk.Entry(frame_input, width=10)
        self.entry_konv_bulan.insert(0, str(sekarang.month))
        self.entry_konv_bulan.grid(row=1, column=1, padx=6, pady=4, sticky="w")

        ttk.Label(frame_input, text="Tahun:").grid(row=2, column=0, padx=6, pady=4, sticky="w")
        self.entry_konv_tahun = ttk.Entry(frame_input, width=10)
        self.entry_konv_tahun.insert(0, str(sekarang.year))
        self.entry_konv_tahun.grid(row=2, column=1, padx=6, pady=4, sticky="w")

        self.label_konv_bulan_nama = ttk.Label(
            frame_input, text="", font=FONT_KECIL, foreground=WARNA_TEKS_MUTED,
            justify="left", wraplength=280)
        self.label_konv_bulan_nama.grid(row=3, column=0, columnspan=2, padx=6, pady=(2, 6), sticky="w")

        self.btn_konversi = ttk.Button(
            body, text="Konversi", command=self._on_konversi_kalender, style="Aksen.TButton")
        self.btn_konversi.pack(pady=6)

        frame_hasil = ttk.LabelFrame(body, text="Hasil")
        frame_hasil.pack(fill="x", **pad)
        self.label_hasil_konverter = ttk.Label(
            frame_hasil, text="Belum ada konversi.", font=FONT_UTAMA_BOLD,
            justify="left", wraplength=280)
        self.label_hasil_konverter.pack(anchor="w", padx=10, pady=10)

        self._on_ganti_arah_konverter()

    def _on_ganti_arah_konverter(self):
        """Perbarui label bantu nama bulan sesuai arah konversi yang
        dipilih -- Masehi pakai BULAN_ID, Hijriyah pakai _NAMA_BULAN_HIJRIYAH."""
        if self.arah_konverter.get() == "m2h":
            teks = "Isi tanggal Masehi (Gregorian), mis. bulan 1=Januari ... 12=Desember."
        else:
            nama_bulan = ", ".join(f"{i + 1}={n}" for i, n in enumerate(_NAMA_BULAN_HIJRIYAH))
            teks = f"Isi tanggal Hijriyah, bulan: {nama_bulan}."
        self.label_konv_bulan_nama.config(text=teks)

    def _on_konversi_kalender(self):
        teks_hari = self.entry_konv_hari.get().strip()
        teks_bulan = self.entry_konv_bulan.get().strip()
        teks_tahun = self.entry_konv_tahun.get().strip()

        if not (teks_hari.isdigit() and teks_bulan.isdigit() and
                teks_tahun.isdigit() and len(teks_tahun) <= 5):
            messagebox.showerror(
                "Input tidak valid", "Isi Hari/Bulan/Tahun dengan angka, mis. 18 / 7 / 2026.")
            return

        hari, bulan, tahun = int(teks_hari), int(teks_bulan), int(teks_tahun)
        if not (1 <= bulan <= 12):
            messagebox.showerror("Input tidak valid", "Bulan harus di antara 1 dan 12.")
            return
        if not (1 <= hari <= 30):
            messagebox.showerror("Input tidak valid", "Tanggal harus di antara 1 dan 30.")
            return

        arah = self.arah_konverter.get()
        kriteria = self.kriteria_konverter.get()

        if arah == "m2h" and kriteria == "urfi" and \
                hari > _hari_dalam_bulan_gregorian(tahun, bulan):
            messagebox.showerror(
                "Input tidak valid",
                f"{BULAN_ID[bulan - 1]} {tahun} cuma punya "
                f"{_hari_dalam_bulan_gregorian(tahun, bulan)} hari.")
            return

        if kriteria == "urfi":
            # --- Jalur cepat (sinkron): kalender tabular, tidak butuh
            #     ijtimak/ephemeris apapun, jadi langsung dihitung di sini. ---
            try:
                if arah == "m2h":
                    tahun_h, bulan_h, hari_h = masehi_ke_hijriyah_urfi(tahun, bulan, hari)
                    jd = julian_day(tahun, bulan, float(hari))
                    jd = float(np.asarray(jd).reshape(()))
                    nama_hari = nama_hari_dari_jd(jd)
                    teks_hasil = (
                        f"{nama_hari}, {hari:02d} {BULAN_ID[bulan - 1]} {tahun} M\n"
                        f"=  {hari_h:02d} {_NAMA_BULAN_HIJRIYAH[bulan_h - 1]} {tahun_h} H "
                        f"(kriteria Urfi)")
                else:
                    tahun_m, bulan_m, hari_m = hijriyah_urfi_ke_masehi(tahun, bulan, hari)
                    jd = _urfi_ke_jd(tahun, bulan, hari)
                    nama_hari = nama_hari_dari_jd(jd)
                    teks_hasil = (
                        f"{hari:02d} {_NAMA_BULAN_HIJRIYAH[bulan - 1]} {tahun} H (kriteria Urfi)\n"
                        f"=  {nama_hari}, {hari_m:02d} {BULAN_ID[bulan_m - 1]} {tahun_m} M")
            except (ValueError, IndexError) as e:
                messagebox.showerror("Gagal konversi", f"Tanggal tidak bisa dikonversi: {e}")
                return

            self.label_hasil_konverter.config(text=teks_hasil)
            self._log(f"Konverter Kalender: {teks_hasil.replace(chr(10), '  ')}")
            return

        # --- Jalur MABIMS/KHGT: kriteria astronomis ASLI, perlu hitung
        #     ijtimak & evaluasi kriteria hilal (bisa perlu beberapa detik)
        #     -- dijalankan di thread terpisah, sama seperti
        #     _on_bandingkan_kalender(), supaya GUI tidak macet. ---
        mode = self.mode.get()
        if mode == "jpl" and self.eph is None:
            messagebox.showwarning(
                "Ephemeris belum siap",
                "Mode Presisi (JPL DE421) dipilih di bagian Visibilitas, tapi "
                "ephemeris-nya belum selesai dimuat. Tunggu sebentar, atau "
                "beralih ke Mode Ringan dulu di bagian 🌙 Visibilitas.")
            return

        self.btn_konversi.config(state="disabled")
        label_kriteria = "MABIMS" if kriteria == "mabims" else "KHGT Muhammadiyah"
        self._log(f"\nMenghitung konversi kalender (kriteria {label_kriteria})...")

        threading.Thread(target=self._konversi_kriteria_thread,
                          args=(arah, kriteria, tahun, bulan, hari, mode), daemon=True).start()

    def _konversi_kriteria_thread(self, arah, kriteria, tahun, bulan, hari, mode):
        label_kriteria = "MABIMS" if kriteria == "mabims" else "KHGT Muhammadiyah"
        try:
            progress_cb = lambda msg: self.antrian.put(("progress", msg))
            if arah == "m2h":
                info = masehi_ke_hijriyah_kriteria(
                    tahun, bulan, hari, kriteria, self.ts, self.eph, mode=mode,
                    progress_cb=progress_cb)
                jd = julian_day(tahun, bulan, float(hari))
                jd = float(np.asarray(jd).reshape(()))
                nama_hari = nama_hari_dari_jd(jd)
                teks_hasil = (
                    f"{nama_hari}, {hari:02d} {BULAN_ID[bulan - 1]} {tahun} M\n"
                    f"=  {info['hari_h']:02d} {info['nama_bulan_h']} {info['tahun_h']} H "
                    f"(kriteria {label_kriteria})")
            else:
                tahun_m, bulan_m, hari_m = hijriyah_kriteria_ke_masehi(
                    tahun, bulan, hari, kriteria, self.ts, self.eph, mode=mode,
                    progress_cb=progress_cb)
                jd = julian_day(tahun_m, bulan_m, float(hari_m))
                jd = float(np.asarray(jd).reshape(()))
                nama_hari = nama_hari_dari_jd(jd)
                teks_hasil = (
                    f"{hari:02d} {_NAMA_BULAN_HIJRIYAH[bulan - 1]} {tahun} H "
                    f"(kriteria {label_kriteria})\n"
                    f"=  {nama_hari}, {hari_m:02d} {BULAN_ID[bulan_m - 1]} {tahun_m} M")
            self.antrian.put(("konv_kriteria_ok", teks_hasil))
        except (ValueError, IndexError) as e:
            self.antrian.put(("konv_kriteria_error", f"Tanggal tidak bisa dikonversi: {e}"))
        except Exception as e:
            self.antrian.put(("konv_kriteria_error", f"Gagal menghitung konversi: {e}"))

    def _on_simpan_csv_kalbanding(self):
        if not self._hasil_kalbanding_terakhir:
            messagebox.showwarning("Belum ada data", "Hitung perbandingan kalender terlebih dahulu.")
            return
        tahun_h, mode, hasil = self._hasil_kalbanding_terakhir
        nama_default = f"perbandingan_kalender_{tahun_h}H.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV (Comma-separated)", "*.csv"), ("Semua File", "*.*")],
            initialfile=nama_default,
            title="Simpan Perbandingan Kalender")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                penulis = csv.writer(f)
                penulis.writerow(["Bulan H", "Nama Bulan H", "Waktu Ijtimak (UTC)",
                                   "Awal Bulan MABIMS", "Awal Bulan KHGT Muhammadiyah", "Beda"])
                for b in hasil:
                    penulis.writerow([
                        b["bulan_h"], b["nama_bulan_h"],
                        b["waktu_ijtimak"].strftime("%d-%m-%Y %H:%M"),
                        b["tanggal_mabims"].strftime("%d-%m-%Y") if b["tanggal_mabims"] else "-",
                        b["tanggal_khgt"].strftime("%d-%m-%Y") if b["tanggal_khgt"] else "-",
                        "Ya" if b["beda"] else "Tidak",
                    ])
            messagebox.showinfo("Tersimpan", f"Perbandingan kalender disimpan ke:\n{path}")
        except OSError as e:
            messagebox.showerror("Gagal menyimpan", f"Tidak bisa menulis file CSV:\n{e}")

    # =====================================================
    #  Akordeon ke-6: Tabel Efemeris
    # =====================================================
    def _bangun_akordeon_efemeris(self, body, pad):
        """Isi badan akordeon "📊 Tabel Efemeris": koordinat lokasi, tanggal,
        zona waktu & interval waktu, lalu tombol untuk menghitung tabel
        posisi Matahari & Bulan (azimuth, tinggi/altitude apparent,
        deklinasi, elongasi, fraksi iluminasi Bulan) tiap interval waktu
        sepanjang satu hari. Memakai Mode Perhitungan yang sama seperti
        bagian 🌙 Visibilitas di atas (Ringan/Presisi), sama seperti pola
        di 📅 Perbandingan Kalender."""

        ttk.Label(
            body,
            text="Tabel posisi Matahari & Bulan (azimuth, tinggi/altitude, "
                 "deklinasi, elongasi & fraksi iluminasi Bulan) tiap interval "
                 "waktu sepanjang satu hari, untuk koordinat & tanggal tertentu.",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left",
            wraplength=280,
        ).pack(fill="x", padx=10, pady=(4, 6))

        frame_koord = ttk.LabelFrame(body, text="1. Koordinat Lokasi")
        frame_koord.pack(fill="x", **pad)
        ttk.Label(frame_koord, text="Lintang:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.entry_lat_efemeris = ttk.Entry(frame_koord, width=12)
        self.entry_lat_efemeris.insert(0, "-6.2")
        self.entry_lat_efemeris.grid(row=0, column=1, padx=4, pady=4)
        ttk.Label(frame_koord, text="° (+LU / -LS)", font=FONT_KECIL,
                  foreground=WARNA_TEKS_MUTED).grid(row=0, column=2, sticky="w")
        ttk.Label(frame_koord, text="Bujur:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.entry_lon_efemeris = ttk.Entry(frame_koord, width=12)
        self.entry_lon_efemeris.insert(0, "106.8")
        self.entry_lon_efemeris.grid(row=1, column=1, padx=4, pady=4)
        ttk.Label(frame_koord, text="° (+BT / -BB)", font=FONT_KECIL,
                  foreground=WARNA_TEKS_MUTED).grid(row=1, column=2, sticky="w")
        ttk.Label(frame_koord, text="Elevasi (mdpl):").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.entry_elevasi_efemeris = ttk.Entry(frame_koord, width=12)
        self.entry_elevasi_efemeris.insert(0, "0")
        self.entry_elevasi_efemeris.grid(row=2, column=1, padx=4, pady=4)

        frame_zona = ttk.LabelFrame(body, text="2. Zona Waktu")
        frame_zona.pack(fill="x", **pad)
        self.var_zona_label_efemeris = tk.StringVar(value=ZONA_WAKTU_PILIHAN[0][0])
        combo_zona_efemeris = ttk.Combobox(
            frame_zona, textvariable=self.var_zona_label_efemeris, state="readonly",
            values=[z[0] for z in ZONA_WAKTU_PILIHAN], width=16)
        combo_zona_efemeris.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        combo_zona_efemeris.bind("<<ComboboxSelected>>", self._on_ganti_zona_efemeris)
        ttk.Label(frame_zona, text="Offset UTC (jam):").grid(row=0, column=1, padx=(10, 4))
        # PENTING: entry dibuat dulu dalam state NORMAL, baru di-insert, baru
        # di-disable setelahnya -- ttk.Entry MENOLAK .insert() kalau widget
        # sudah dalam state="disabled" saat konstruksi (gagal diam-diam,
        # tanpa error), makanya kalau langsung state="disabled" dari awal
        # kotak offset UTC-nya akan tampil KOSONG walau sudah di-insert("7").
        self.entry_zona_custom_efemeris = ttk.Entry(frame_zona, width=6)
        self.entry_zona_custom_efemeris.insert(0, "7")
        self.entry_zona_custom_efemeris.config(state="disabled")
        self.entry_zona_custom_efemeris.grid(row=0, column=2, padx=4)

        frame_tgl = ttk.LabelFrame(body, text="3. Tanggal")
        frame_tgl.pack(fill="x", **pad)
        hari_ini = datetime.now()
        ttk.Label(frame_tgl, text="Tanggal:").grid(row=0, column=0, padx=4, pady=6)
        self.entry_tgl_hari_efemeris = ttk.Entry(frame_tgl, width=4)
        self.entry_tgl_hari_efemeris.insert(0, str(hari_ini.day))
        self.entry_tgl_hari_efemeris.grid(row=0, column=1, padx=2)
        ttk.Label(frame_tgl, text="Bulan:").grid(row=0, column=2, padx=4)
        self.entry_tgl_bulan_efemeris = ttk.Entry(frame_tgl, width=4)
        self.entry_tgl_bulan_efemeris.insert(0, str(hari_ini.month))
        self.entry_tgl_bulan_efemeris.grid(row=0, column=3, padx=2)
        ttk.Label(frame_tgl, text="Tahun:").grid(row=0, column=4, padx=4)
        self.entry_tgl_tahun_efemeris = ttk.Entry(frame_tgl, width=6)
        self.entry_tgl_tahun_efemeris.insert(0, str(hari_ini.year))
        self.entry_tgl_tahun_efemeris.grid(row=0, column=5, padx=2)

        frame_interval = ttk.LabelFrame(body, text="4. Interval Waktu")
        frame_interval.pack(fill="x", **pad)
        self.var_interval_efemeris = tk.StringVar(value="60 menit")
        combo_interval = ttk.Combobox(
            frame_interval, textvariable=self.var_interval_efemeris, state="readonly",
            values=["10 menit", "15 menit", "30 menit", "60 menit", "120 menit"], width=12)
        combo_interval.grid(row=0, column=0, padx=6, pady=6, sticky="w")

        frame_sumber = ttk.LabelFrame(body, text="5. Sumber Data")
        frame_sumber.pack(fill="x", **pad)
        self.var_mode_sumber_efemeris = tk.StringVar(value="ringan")
        ttk.Radiobutton(
            frame_sumber, text="Ringan (VSOP87+ELP2000, offline)",
            variable=self.var_mode_sumber_efemeris, value="ringan",
            command=self._on_ganti_sumber_efemeris,
        ).pack(anchor="w", padx=6, pady=(6, 0))
        ttk.Radiobutton(
            frame_sumber, text="Presisi -- Skyfield + JPL DE421 (offline, file lokal)",
            variable=self.var_mode_sumber_efemeris, value="jpl",
            command=self._on_ganti_sumber_efemeris,
        ).pack(anchor="w", padx=6)
        ttk.Radiobutton(
            frame_sumber, text="JPL Horizons API (online, ssd.jpl.nasa.gov)",
            variable=self.var_mode_sumber_efemeris, value="horizons",
            command=self._on_ganti_sumber_efemeris,
        ).pack(anchor="w", padx=6)
        self.label_peringatan_sumber_efemeris = ttk.Label(
            frame_sumber, text="", font=FONT_KECIL, foreground=WARNA_TEKS_MUTED,
            justify="left", wraplength=280,
        )
        self.label_peringatan_sumber_efemeris.pack(fill="x", padx=6, pady=(2, 6))
        self._on_ganti_sumber_efemeris()

        self.btn_buat_efemeris = ttk.Button(
            body, text="Buat Tabel Efemeris", command=self._on_buat_efemeris,
            style="Aksen.TButton")
        self.btn_buat_efemeris.pack(fill="x", padx=10, pady=(4, 10))

    def _on_ganti_zona_efemeris(self, event=None):
        label = self.var_zona_label_efemeris.get()
        offset = dict(ZONA_WAKTU_PILIHAN).get(label)
        if offset is None:  # "Custom..."
            self.entry_zona_custom_efemeris.config(state="normal")
        else:
            self.entry_zona_custom_efemeris.config(state="normal")
            self.entry_zona_custom_efemeris.delete(0, "end")
            self.entry_zona_custom_efemeris.insert(0, str(offset))
            self.entry_zona_custom_efemeris.config(state="disabled")

    def _on_ganti_sumber_efemeris(self):
        """Perbarui catatan kecil di bawah pilihan Sumber Data, khususnya
        peringatan kebutuhan internet untuk mode Horizons API."""
        mode = self.var_mode_sumber_efemeris.get()
        if mode == "horizons":
            teks = ("⚠ Mode ini mengirim request ke server JPL (ssd.jpl.nasa.gov) "
                    "setiap kali tabel dibuat. WAJIB ADA KONEKSI INTERNET aktif "
                    "-- kalau tidak, proses akan gagal dengan pesan error.")
        elif mode == "jpl":
            teks = ("Dihitung dari file ephemeris JPL DE421 yang sudah dibundel "
                    "bersama aplikasi (de421.bsp) -- tidak perlu internet, tapi "
                    "perlu ephemeris-nya sudah selesai dimuat di awal aplikasi.")
        else:
            teks = ("Dihitung sendiri (VSOP87+ELP2000) tanpa file eksternal "
                    "maupun koneksi internet sama sekali.")
        self.label_peringatan_sumber_efemeris.config(text=teks)

    def _on_buat_efemeris(self):
        try:
            try:
                lat = float(self.entry_lat_efemeris.get().strip().replace(",", "."))
                lon = float(self.entry_lon_efemeris.get().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Koordinat tidak valid. Contoh format: -6.200000")
            if not (-90 <= lat <= 90):
                raise ValueError("Lintang harus di antara -90 dan 90 derajat.")
            if not (-180 <= lon <= 180):
                raise ValueError("Bujur harus di antara -180 dan 180 derajat.")
            try:
                elevasi = float(self.entry_elevasi_efemeris.get().strip().replace(",", ".") or "0")
            except ValueError:
                raise ValueError("Elevasi tidak valid. Isi angka (mdpl), atau kosongkan/isi 0.")
            try:
                zona_offset = float(self.entry_zona_custom_efemeris.get().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Offset UTC zona waktu tidak valid. Contoh: 7 atau 7.5")
            try:
                hari = int(self.entry_tgl_hari_efemeris.get())
                bulan = int(self.entry_tgl_bulan_efemeris.get())
                tahun = int(self.entry_tgl_tahun_efemeris.get())
                tanggal = datetime(tahun, bulan, hari)
            except ValueError:
                raise ValueError("Tanggal tidak valid. Pastikan hari/bulan/tahun berupa angka & tanggal ada.")
        except ValueError as e:
            messagebox.showerror("Input tidak valid", str(e))
            return

        interval_menit = int(self.var_interval_efemeris.get().split()[0])
        mode = self.var_mode_sumber_efemeris.get()

        if mode == "jpl" and self.eph is None:
            messagebox.showwarning(
                "Ephemeris belum siap",
                "Sumber Data 'Presisi (JPL DE421)' dipilih, tapi ephemeris "
                "lokalnya belum selesai dimuat. Tunggu sebentar, atau pilih "
                "sumber data 'Ringan' / 'JPL Horizons API' dulu.")
            return

        self.btn_buat_efemeris.config(state="disabled")
        self._log(f"\nMenghitung tabel efemeris untuk {tanggal.strftime('%d %B %Y')} "
                   f"({lat:.4f}, {lon:.4f})...")

        threading.Thread(
            target=self._buat_efemeris_thread,
            args=(tanggal, lat, lon, zona_offset, elevasi, interval_menit, mode),
            daemon=True).start()

    def _buat_efemeris_thread(self, tanggal, lat, lon, zona_offset, elevasi, interval_menit, mode):
        try:
            hasil = hitung_tabel_efemeris(
                tanggal, lat, lon, zona_offset, mode=mode, ts=self.ts, eph=self.eph,
                interval_menit=interval_menit, elevasi_m=elevasi)
            rts = hitung_rts(
                tanggal, lat, lon, zona_offset, mode=mode, ts=self.ts, eph=self.eph,
                elevasi_m=elevasi)
            self.antrian.put(("efemeris_ok", (tanggal, lat, lon, zona_offset, mode, hasil, rts)))
        except Exception as e:
            self.antrian.put(("efemeris_error", f"Gagal menghitung tabel efemeris: {e}"))

    def _bangun_tab_efemeris(self):
        """Bangun (sekali saja, permanen) frame tab hasil Tabel Efemeris --
        pola sama seperti _bangun_tab_kalbanding: dibuat di awal, baru
        dimasukkan ke notebook kanan begitu ada hasil pertama kali (lihat
        _pastikan_tab_efemeris_tampil)."""
        frame = ttk.Frame(self.notebook)
        self._frame_efemeris = frame

        panel_hasil = ttk.Frame(frame)
        panel_hasil.pack(fill="both", expand=True, padx=8, pady=8)

        self.label_judul_efemeris = ttk.Label(
            panel_hasil, text="Belum ada tabel efemeris dihitung.", font=FONT_UTAMA_BOLD)
        self.label_judul_efemeris.pack(anchor="w", pady=(0, 4))

        self.label_rts_efemeris = ttk.Label(
            panel_hasil, text="", font=FONT_KECIL, foreground=WARNA_TEKS_MUTED, justify="left")
        self.label_rts_efemeris.pack(anchor="w", pady=(0, 8))

        kolom = ("jam", "az_matahari", "alt_matahari", "dec_matahari",
                 "az_bulan", "alt_bulan", "dec_bulan", "elongasi", "fraksi_iluminasi")
        judul_kolom = {
            "jam": "Jam (Lokal)", "az_matahari": "Az. Matahari", "alt_matahari": "Tinggi Matahari",
            "dec_matahari": "Dek. Matahari", "az_bulan": "Az. Bulan", "alt_bulan": "Tinggi Bulan",
            "dec_bulan": "Dek. Bulan", "elongasi": "Elongasi", "fraksi_iluminasi": "Iluminasi Bulan",
        }
        self.tree_efemeris = ttk.Treeview(
            panel_hasil, columns=kolom, show="headings", height=20)
        for kunci in kolom:
            self.tree_efemeris.heading(kunci, text=judul_kolom[kunci])
            lebar = 90 if kunci == "jam" else 120
            self.tree_efemeris.column(kunci, width=lebar, anchor="center")
        self.tree_efemeris.tag_configure("siang", background="#FFF9E6")
        self.tree_efemeris.tag_configure("malam", background=WARNA_PANEL)

        scroll_tree_y = ttk.Scrollbar(panel_hasil, orient="vertical",
                                       command=self.tree_efemeris.yview)
        self.tree_efemeris.configure(yscrollcommand=scroll_tree_y.set)
        self.tree_efemeris.pack(side="left", fill="both", expand=True)
        scroll_tree_y.pack(side="left", fill="y")

        panel_bawah = ttk.Frame(frame)
        panel_bawah.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(
            panel_bawah,
            text="🟨 Baris kuning = Matahari di atas ufuk (siang hari) pada jam tsb.",
            font=FONT_KECIL, foreground=WARNA_TEKS_MUTED,
        ).pack(anchor="w")
        self.btn_simpan_csv_efemeris = ttk.Button(
            panel_bawah, text="Simpan ke File .csv",
            command=self._on_simpan_csv_efemeris, state="disabled")
        self.btn_simpan_csv_efemeris.pack(anchor="e", pady=(6, 0))

    def _pastikan_tab_efemeris_tampil(self):
        if not self._tab_efemeris_ditambahkan:
            self._hapus_tab_awal()
            self.notebook.add(self._frame_efemeris, text="📊 Tabel Efemeris")
            self._tab_efemeris_ditambahkan = True
        self.notebook.select(self._frame_efemeris)

    def _tampilkan_efemeris(self, tanggal, lat, lon, zona_offset, mode, hasil, rts):
        self._hasil_efemeris_terakhir = (tanggal, lat, lon, zona_offset, mode, hasil, rts)

        def _fmt(jam):
            return _label_jam_dari_desimal(jam) if jam is not None else "--:--"

        m, b = rts["matahari"], rts["bulan"]
        self.label_rts_efemeris.config(
            text=f"☀ Matahari — Terbit {_fmt(m['terbit'])}  |  Transit {_fmt(m['transit'])}  |  "
                 f"Terbenam {_fmt(m['terbenam'])}     "
                 f"🌙 Bulan — Terbit {_fmt(b['terbit'])}  |  Transit {_fmt(b['transit'])}  |  "
                 f"Terbenam {_fmt(b['terbenam'])}")

        self.tree_efemeris.delete(*self.tree_efemeris.get_children())
        for b in hasil:
            tag = "siang" if b["alt_matahari"] > 0 else "malam"
            self.tree_efemeris.insert("", "end", tags=(tag,), values=(
                b["label_jam"],
                f"{b['az_matahari']:.2f}°", f"{b['alt_matahari']:+.2f}°", f"{b['dec_matahari']:+.2f}°",
                f"{b['az_bulan']:.2f}°", f"{b['alt_bulan']:+.2f}°", f"{b['dec_bulan']:+.2f}°",
                f"{b['elongasi_deg']:.2f}°", f"{b['fraksi_iluminasi_persen']:.1f}%",
            ))

        metode_label = {
            "jpl": "Presisi -- Skyfield + JPL DE421 (offline)",
            "horizons": "Online -- JPL Horizons API (ssd.jpl.nasa.gov)",
        }.get(mode, "Ringan (VSOP87+ELP2000, offline)")
        self.label_judul_efemeris.config(
            text=f"Tabel Efemeris — {tanggal.strftime('%d %B %Y')}  |  "
                 f"Lokasi: {lat:.4f}, {lon:.4f} (UTC{zona_offset:+g})  |  "
                 f"Metode: {metode_label}  |  {len(hasil)} baris")

        self.btn_simpan_csv_efemeris.config(state="normal" if hasil else "disabled")
        self._pastikan_tab_efemeris_tampil()
        self._log(f"Tabel efemeris {tanggal.strftime('%d %B %Y')} selesai ({len(hasil)} baris).")
        self.btn_buat_efemeris.config(state="normal")

    def _on_simpan_csv_efemeris(self):
        if not self._hasil_efemeris_terakhir:
            messagebox.showwarning("Belum ada data", "Buat tabel efemeris terlebih dahulu.")
            return
        tanggal, lat, lon, zona_offset, mode, hasil, rts = self._hasil_efemeris_terakhir
        nama_default = f"tabel_efemeris_{tanggal.strftime('%Y%m%d')}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV (Comma-separated)", "*.csv"), ("Semua File", "*.*")],
            initialfile=nama_default,
            title="Simpan Tabel Efemeris")
        if not path:
            return

        def _fmt(jam):
            return _label_jam_dari_desimal(jam) if jam is not None else ""

        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                penulis = csv.writer(f)
                penulis.writerow([f"Tabel Efemeris {tanggal.strftime('%d-%m-%Y')}",
                                   f"Lokasi {lat:.6f},{lon:.6f}", f"UTC{zona_offset:+g}"])
                penulis.writerow(["Matahari", "Terbit", _fmt(rts["matahari"]["terbit"]),
                                   "Transit", _fmt(rts["matahari"]["transit"]),
                                   "Terbenam", _fmt(rts["matahari"]["terbenam"])])
                penulis.writerow(["Bulan", "Terbit", _fmt(rts["bulan"]["terbit"]),
                                   "Transit", _fmt(rts["bulan"]["transit"]),
                                   "Terbenam", _fmt(rts["bulan"]["terbenam"])])
                penulis.writerow([])
                penulis.writerow(["Jam (Lokal)", "Azimuth Matahari", "Tinggi Matahari",
                                   "Deklinasi Matahari", "Azimuth Bulan", "Tinggi Bulan",
                                   "Deklinasi Bulan", "Jarak Bulan (km)", "Elongasi",
                                   "Fraksi Iluminasi Bulan (%)"])
                for b in hasil:
                    penulis.writerow([
                        b["label_jam"], f"{b['az_matahari']:.3f}", f"{b['alt_matahari']:.3f}",
                        f"{b['dec_matahari']:.3f}", f"{b['az_bulan']:.3f}", f"{b['alt_bulan']:.3f}",
                        f"{b['dec_bulan']:.3f}", f"{b['jarak_bulan_km']:.0f}",
                        f"{b['elongasi_deg']:.3f}", f"{b['fraksi_iluminasi_persen']:.1f}",
                    ])
            messagebox.showinfo("Tersimpan", f"Tabel efemeris disimpan ke:\n{path}")
        except OSError as e:
            messagebox.showerror("Gagal menyimpan", f"Tidak bisa menulis file CSV:\n{e}")

    # ---------------- Tab Waktu Sholat & Arah Kiblat ----------------

    def _path_file_lokasi(self):
        """Path file teks lokal tempat lokasi terakhir (koordinat, zona
        waktu, dll) disimpan/dimuat -- selalu di folder yang sama dengan
        exe/script ini.

        Beda dengan aset baca-saja (de421.bsp, logo.png, dll) yang harus
        dicari lewat _resource_base_dir()/_MEIPASS: file INI ditulis ulang
        saat runtime, jadi harus mengarah ke folder exe yang SEBENARNYA
        (sys.executable), bukan folder ekstraksi/bundle PyInstaller --
        kalau tidak, setting bisa gagal tersimpan (folder _internal/temp
        tidak selalu dimaksudkan sebagai tempat persisten) atau malah
        "hilang" tiap kali app dibuka ulang (folder onefile diekstrak ke
        temp baru setiap start)."""
        if getattr(sys, "frozen", False):
            folder_script = os.path.dirname(os.path.abspath(sys.executable))
        else:
            try:
                folder_script = os.path.dirname(os.path.abspath(__file__))
            except NameError:
                folder_script = os.getcwd()
        return os.path.join(folder_script, "hisabwin_lokasi.txt")

    def _bangun_tab_sholat(self):
        # Frame-nya dibuat sekarang (supaya semua widget di dalamnya siap
        # dipakai sejak awal), TAPI belum langsung ditambahkan ke notebook
        # (self.notebook.add) -- disimpan dulu di self._frame_sholat, baru
        # benar-benar dimunculkan sebagai tab begitu ada hasil pertama yang
        # perlu ditampilkan (lihat _pastikan_tab_sholat_tampil). Dengan
        # begitu saat aplikasi baru dibuka, notebook kanan masih kosong
        # (belum ada tab sama sekali) dan tidak ada tab manapun yang jadi
        # "aktif" -- jadi kedua bagian akordeon di bilah kiri tetap
        # sama-sama terbuka seperti kondisi awalnya, tidak ada yang
        # otomatis dilipat oleh _on_ganti_tab_notebook.
        frame_sholat = ttk.Frame(self.notebook)
        self._frame_sholat = frame_sholat
        self._tab_sholat_ditambahkan = False

        pad = {"padx": 10, "pady": 6}

        # ===================== PANEL INPUT =====================
        # Dulu input di sini dipasang di panel kiri terpisah (di dalam tab
        # ini sendiri, lewat PanedWindow + canvas scroll sendiri). Sekarang
        # dipindah ke badan akordeon "🕌 Input — Waktu Sholat & Kiblat" di
        # bilah paling kiri (tab_kontrol) -- sejalan dengan bagian Hilal
        # yang sudah lebih dulu dipindah ke akordeon (lihat _bangun_tab_kontrol).
        # Bilah kiri itu sendiri sudah scrollable (dibungkus kontrol_canvas),
        # jadi tidak perlu canvas/scrollbar sendiri lagi di sini.
        isi_input = self._body_akordeon_sholat

        # --- Mode koordinat: Desimal / DMS ---
        frame_mode = ttk.LabelFrame(isi_input, text="Mode Koordinat")
        frame_mode.pack(fill="x", **pad)
        self.mode_koordinat_sholat = tk.StringVar(value="desimal")
        ttk.Radiobutton(frame_mode, text="Desimal (mis. -6.200000)", value="desimal",
                         variable=self.mode_koordinat_sholat,
                         command=self._on_ganti_mode_koordinat_sholat).grid(
            row=0, column=0, padx=10, pady=4, sticky="w")
        ttk.Radiobutton(frame_mode, text="DMS (Derajat° Menit′ Detik″)", value="dms",
                         variable=self.mode_koordinat_sholat,
                         command=self._on_ganti_mode_koordinat_sholat).grid(
            row=1, column=0, padx=10, pady=4, sticky="w")

        # --- Metode hisab: Ringan (VSOP87) / Presisi (Skyfield + JPL DE421) ---
        frame_metode = ttk.LabelFrame(isi_input, text="Metode Hisab")
        frame_metode.pack(fill="x", **pad)
        self.mode_sholat = tk.StringVar(value="ringan")
        ttk.Radiobutton(
            frame_metode, text="Ringan (VSOP87, instan, tanpa file)", value="ringan",
            variable=self.mode_sholat, command=self._on_ganti_mode_sholat
        ).grid(row=0, column=0, padx=10, pady=4, sticky="w")
        ttk.Radiobutton(
            frame_metode, text="Presisi (Skyfield + ephemeris JPL DE421)", value="jpl",
            variable=self.mode_sholat, command=self._on_ganti_mode_sholat
        ).grid(row=1, column=0, padx=10, pady=4, sticky="w")
        self.label_status_metode_sholat = ttk.Label(
            frame_metode, text="Mode Ringan aktif — siap dipakai langsung.",
            foreground=WARNA_TEKS_MUTED, font=FONT_KECIL, wraplength=330, justify="left")
        self.label_status_metode_sholat.grid(row=2, column=0, padx=10, pady=(0, 6), sticky="w")

        # --- Koordinat ---
        frame_koord = ttk.LabelFrame(isi_input, text="Koordinat Lokasi (manual)")
        frame_koord.pack(fill="x", **pad)

        # -- baris Desimal --
        self.frame_desimal_sholat = ttk.Frame(frame_koord)
        self.frame_desimal_sholat.pack(fill="x", padx=6, pady=4)
        ttk.Label(self.frame_desimal_sholat, text="Lintang:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.entry_lat_desimal = ttk.Entry(self.frame_desimal_sholat, width=14)
        self.entry_lat_desimal.grid(row=0, column=1, padx=4, pady=4)
        ttk.Label(self.frame_desimal_sholat, text="° (+LU / -LS)",
                  font=FONT_KECIL, foreground=WARNA_TEKS_MUTED).grid(row=0, column=2, sticky="w")
        ttk.Label(self.frame_desimal_sholat, text="Bujur:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.entry_lon_desimal = ttk.Entry(self.frame_desimal_sholat, width=14)
        self.entry_lon_desimal.grid(row=1, column=1, padx=4, pady=4)
        ttk.Label(self.frame_desimal_sholat, text="° (+BT / -BB)",
                  font=FONT_KECIL, foreground=WARNA_TEKS_MUTED).grid(row=1, column=2, sticky="w")

        # -- baris DMS --
        self.frame_dms_sholat = ttk.Frame(frame_koord)
        # (pack dilakukan di _on_ganti_mode_koordinat_sholat, tergantung mode aktif)

        def _baris_dms(master, label, arah_a, arah_b, row):
            ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            e_d = ttk.Entry(master, width=5)
            e_m = ttk.Entry(master, width=5)
            e_s = ttk.Entry(master, width=6)
            e_d.grid(row=row, column=1, padx=2)
            ttk.Label(master, text="°").grid(row=row, column=2)
            e_m.grid(row=row, column=3, padx=2)
            ttk.Label(master, text="′").grid(row=row, column=4)
            e_s.grid(row=row, column=5, padx=2)
            ttk.Label(master, text="″").grid(row=row, column=6)
            arah_var = tk.StringVar(value=arah_a)
            combo_arah = ttk.Combobox(master, textvariable=arah_var, values=[arah_a, arah_b],
                                       width=4, state="readonly")
            combo_arah.grid(row=row, column=7, padx=(6, 4))
            return e_d, e_m, e_s, arah_var

        (self.entry_lat_d, self.entry_lat_m, self.entry_lat_s,
         self.var_lat_arah) = _baris_dms(self.frame_dms_sholat, "Lintang:", "LU", "LS", 0)
        (self.entry_lon_d, self.entry_lon_m, self.entry_lon_s,
         self.var_lon_arah) = _baris_dms(self.frame_dms_sholat, "Bujur:", "BT", "BB", 1)

        # --- Elevasi ---
        frame_elev = ttk.Frame(frame_koord)
        frame_elev.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Label(frame_elev, text="Elevasi (mdpl, opsional):").pack(side="left", padx=4)
        self.entry_elevasi = ttk.Entry(frame_elev, width=8)
        self.entry_elevasi.insert(0, "0")
        self.entry_elevasi.pack(side="left", padx=4)

        # --- Zona waktu ---
        frame_zona = ttk.LabelFrame(isi_input, text="Zona Waktu")
        frame_zona.pack(fill="x", **pad)
        self.var_zona_label = tk.StringVar(value=ZONA_WAKTU_PILIHAN[0][0])
        combo_zona = ttk.Combobox(frame_zona, textvariable=self.var_zona_label, state="readonly",
                                   values=[z[0] for z in ZONA_WAKTU_PILIHAN], width=16)
        combo_zona.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        combo_zona.bind("<<ComboboxSelected>>", self._on_ganti_zona_sholat)

        ttk.Label(frame_zona, text="Offset UTC (jam):").grid(row=0, column=1, padx=(10, 4))
        # Sama seperti entry_zona_custom_efemeris -- lihat catatan di sana
        # soal kenapa urutannya harus normal -> insert -> disable.
        self.entry_zona_custom = ttk.Entry(frame_zona, width=6)
        self.entry_zona_custom.insert(0, "7")
        self.entry_zona_custom.config(state="disabled")
        self.entry_zona_custom.grid(row=0, column=2, padx=4)

        # --- Tanggal (untuk perhitungan harian) ---
        frame_tgl = ttk.LabelFrame(isi_input, text="Tanggal (Hitung Harian)")
        frame_tgl.pack(fill="x", **pad)
        hari_ini = datetime.now()
        ttk.Label(frame_tgl, text="Tanggal:").grid(row=0, column=0, padx=4, pady=6)
        self.entry_tgl_hari = ttk.Entry(frame_tgl, width=4)
        self.entry_tgl_hari.insert(0, str(hari_ini.day))
        self.entry_tgl_hari.grid(row=0, column=1, padx=2)
        ttk.Label(frame_tgl, text="Bulan:").grid(row=0, column=2, padx=4)
        self.entry_tgl_bulan = ttk.Entry(frame_tgl, width=4)
        self.entry_tgl_bulan.insert(0, str(hari_ini.month))
        self.entry_tgl_bulan.grid(row=0, column=3, padx=2)
        ttk.Label(frame_tgl, text="Tahun:").grid(row=0, column=4, padx=4)
        self.entry_tgl_tahun = ttk.Entry(frame_tgl, width=6)
        self.entry_tgl_tahun.insert(0, str(hari_ini.year))
        self.entry_tgl_tahun.grid(row=0, column=5, padx=2)

        # --- Bulan & Tahun (khusus untuk Jadwal Sebulan) ---
        frame_bulan_jadwal = ttk.LabelFrame(isi_input, text="Bulan & Tahun (Jadwal Sebulan)")
        frame_bulan_jadwal.pack(fill="x", **pad)
        ttk.Label(frame_bulan_jadwal, text="Bulan:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.var_bulan_jadwal = tk.StringVar(value=BULAN_ID[hari_ini.month - 1])
        combo_bulan_jadwal = ttk.Combobox(
            frame_bulan_jadwal, textvariable=self.var_bulan_jadwal, state="readonly",
            values=BULAN_ID, width=14)
        combo_bulan_jadwal.grid(row=0, column=1, padx=4, pady=6, sticky="w")
        ttk.Label(frame_bulan_jadwal, text="Tahun:").grid(row=0, column=2, padx=(10, 4), pady=6)
        self.entry_tahun_jadwal = ttk.Entry(frame_bulan_jadwal, width=6)
        self.entry_tahun_jadwal.insert(0, str(hari_ini.year))
        self.entry_tahun_jadwal.grid(row=0, column=3, padx=4, pady=6)

        # --- Preset sudut & pengaturan lanjutan ---
        frame_preset = ttk.LabelFrame(isi_input, text="Kriteria Sudut & Ihtiyat")
        frame_preset.pack(fill="x", **pad)
        self.var_preset_sudut = tk.StringVar(value=list(PRESET_SUDUT.keys())[0])
        combo_preset = ttk.Combobox(frame_preset, textvariable=self.var_preset_sudut, state="readonly",
                                     values=list(PRESET_SUDUT.keys()), width=32)
        combo_preset.grid(row=0, column=0, columnspan=2, padx=6, pady=6, sticky="w")

        ttk.Label(frame_preset, text="Mazhab Ashar:").grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.var_mazhab_ashar = tk.StringVar(value="syafii")
        combo_mazhab = ttk.Combobox(frame_preset, textvariable=self.var_mazhab_ashar, state="readonly",
                                     values=["syafii", "hanafi"], width=10)
        combo_mazhab.grid(row=1, column=1, padx=6, pady=4, sticky="w")

        ttk.Label(frame_preset, text="Ihtiyat (menit):").grid(row=2, column=0, padx=6, pady=4, sticky="w")
        self.entry_ihtiyat = ttk.Entry(frame_preset, width=6)
        self.entry_ihtiyat.insert(0, "2")
        self.entry_ihtiyat.grid(row=2, column=1, padx=6, pady=4, sticky="w")

        ttk.Label(frame_preset, text="Imsak (menit sebelum Subuh):").grid(row=3, column=0, padx=6, pady=4, sticky="w")
        self.entry_imsak_offset = ttk.Entry(frame_preset, width=6)
        self.entry_imsak_offset.insert(0, "10")
        self.entry_imsak_offset.grid(row=3, column=1, padx=6, pady=4, sticky="w")

        # --- Tombol aksi ---
        frame_tombol = ttk.Frame(isi_input)
        frame_tombol.pack(fill="x", **pad)
        self.btn_hitung_sholat = ttk.Button(
            frame_tombol, text="Hitung Waktu Sholat Hari Ini && Kiblat",
            command=self._on_hitung_sholat, style="Aksen.TButton")
        self.btn_hitung_sholat.pack(fill="x", pady=(0, 4))
        self.btn_hitung_jadwal_bulan = ttk.Button(
            frame_tombol, text="Tampilkan Jadwal Sholat Satu Bulan Penuh",
            command=self._on_hitung_jadwal_bulan, style="Aksen.TButton")
        self.btn_hitung_jadwal_bulan.pack(fill="x", pady=(0, 4))
        ttk.Button(frame_tombol, text="Simpan Lokasi Ini (dipakai lagi saat dibuka nanti)",
                   command=self._on_simpan_lokasi).pack(fill="x", pady=2)
        self.btn_simpan_hasil = ttk.Button(
            frame_tombol, text="Simpan Hasil Hari Ini ke File .txt",
            command=self._on_simpan_hasil_sholat, state="disabled")
        self.btn_simpan_hasil.pack(fill="x", pady=2)
        self.btn_simpan_csv_bulan = ttk.Button(
            frame_tombol, text="Simpan Jadwal Sebulan ke File .csv",
            command=self._on_simpan_csv_jadwal_bulan, state="disabled")
        self.btn_simpan_csv_bulan.pack(fill="x", pady=2)

        # ===================== PANEL HASIL =====================
        # Sekarang mengisi seluruh tab (bukan lagi separuh panel di
        # samping input -- input sudah pindah ke akordeon bilah kiri).
        panel_hasil = ttk.Frame(frame_sholat)
        panel_hasil.pack(fill="both", expand=True, padx=8, pady=8)

        ttk.Label(panel_hasil, text="Hasil Perhitungan", font=FONT_UTAMA_BOLD).pack(
            anchor="w", padx=10, pady=(10, 4))

        notebook_hasil_sholat = ttk.Notebook(panel_hasil)
        notebook_hasil_sholat.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.notebook_hasil_sholat = notebook_hasil_sholat

        # -- Tab 1: hasil hari ini (teks, seperti sebelumnya) --
        frame_hasil_hari_ini = ttk.Frame(notebook_hasil_sholat)
        notebook_hasil_sholat.add(frame_hasil_hari_ini, text="Hari Ini")
        self.frame_hasil_hari_ini = frame_hasil_hari_ini

        self.text_hasil_sholat = tk.Text(
            frame_hasil_hari_ini, wrap="word", state="disabled",
            bg=WARNA_PANEL, fg=WARNA_TEKS, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=WARNA_BORDER, highlightcolor=WARNA_AKSEN,
            font=("Consolas", 11), padx=10, pady=10)
        self.text_hasil_sholat.pack(fill="both", expand=True)

        # -- Tab 2: jadwal sebulan penuh (tabel) --
        frame_hasil_bulan = ttk.Frame(notebook_hasil_sholat)
        notebook_hasil_sholat.add(frame_hasil_bulan, text="Jadwal Sebulan")
        self.frame_hasil_bulan = frame_hasil_bulan

        self.label_judul_jadwal_bulan = ttk.Label(
            frame_hasil_bulan, text="Belum ada jadwal dihitung.", font=FONT_UTAMA_BOLD)
        self.label_judul_jadwal_bulan.pack(anchor="w", padx=4, pady=(4, 6))

        kolom_jadwal = ("tanggal", "imsak", "subuh", "terbit", "dhuha",
                         "dzuhur", "ashar", "maghrib", "isya", "kiblat_v", "kiblat_s")
        judul_kolom = {"tanggal": "Tanggal", "imsak": "Imsak", "subuh": "Subuh",
                        "terbit": "Terbit", "dhuha": "Dhuha", "dzuhur": "Dzuhur",
                        "ashar": "Ashar", "maghrib": "Maghrib", "isya": "Isya",
                        "kiblat_v": "Kiblat (V)", "kiblat_s": "Kiblat (S)"}
        self.tree_jadwal_bulan = ttk.Treeview(
            frame_hasil_bulan, columns=kolom_jadwal, show="headings", height=20)
        for kunci in kolom_jadwal:
            self.tree_jadwal_bulan.heading(kunci, text=judul_kolom[kunci])
            lebar = 90 if kunci == "tanggal" else (80 if "kiblat" in kunci else 60)
            self.tree_jadwal_bulan.column(kunci, width=lebar, anchor="center")
        scroll_tree_y = ttk.Scrollbar(frame_hasil_bulan, orient="vertical",
                                       command=self.tree_jadwal_bulan.yview)
        self.tree_jadwal_bulan.configure(yscrollcommand=scroll_tree_y.set)
        self.tree_jadwal_bulan.pack(side="left", fill="both", expand=True)
        scroll_tree_y.pack(side="left", fill="y")

        self._hasil_sholat_terakhir = None  # teks laporan harian terakhir, siap disimpan ke .txt
        self._jadwal_bulan_terakhir = None  # (tanggal_dt, dict_waktu) list terakhir, siap ekspor CSV
        self._ephemeris_loading = False  # penjaga supaya tidak memuat de421.bsp dobel

        # Terapkan mode koordinat default (pack frame yang sesuai) & coba
        # muat lokasi terakhir yang tersimpan; kalau tidak ada, pakai default.
        self._on_ganti_mode_koordinat_sholat()
        self._on_ganti_zona_sholat()
        self._muat_lokasi_awal()

    def _on_ganti_mode_koordinat_sholat(self):
        if self.mode_koordinat_sholat.get() == "desimal":
            self.frame_dms_sholat.pack_forget()
            self.frame_desimal_sholat.pack(fill="x", padx=6, pady=4)
        else:
            self.frame_desimal_sholat.pack_forget()
            self.frame_dms_sholat.pack(fill="x", padx=6, pady=4)

    def _on_ganti_mode_sholat(self):
        """Dipanggil saat radio Metode Hisab (Ringan/Presisi) diganti di tab
        Waktu Sholat. Mode Presisi butuh ts & eph (ephemeris JPL DE421) --
        kalau belum dimuat sama sekali (mis. user belum pernah buka tab
        Hilal), muat sekarang di background thread yang sama dengan yang
        dipakai tab Hilal (self._muat_ephemeris), supaya tidak dobel-muat."""
        if self.mode_sholat.get() == "ringan":
            self.label_status_metode_sholat.config(
                text="Mode Ringan aktif — siap dipakai langsung.")
            return

        if self.eph is not None:
            self.label_status_metode_sholat.config(
                text="Mode Presisi aktif — ephemeris JPL DE421 sudah siap.")
        elif self._ephemeris_loading:
            self.label_status_metode_sholat.config(
                text="Mode Presisi aktif — masih memuat ephemeris de421.bsp, mohon tunggu...")
        else:
            self.label_status_metode_sholat.config(
                text="Mode Presisi aktif — memuat ephemeris de421.bsp, mohon tunggu...")
            self._ephemeris_loading = True
            self._log("Memuat ephemeris de421.bsp untuk hisab Waktu Sholat mode Presisi...")
            threading.Thread(target=self._muat_ephemeris, daemon=True).start()

    def _on_ganti_zona_sholat(self, event=None):
        label = self.var_zona_label.get()
        offset = dict(ZONA_WAKTU_PILIHAN).get(label)
        if offset is None:  # "Custom..."
            self.entry_zona_custom.config(state="normal")
        else:
            self.entry_zona_custom.config(state="normal")
            self.entry_zona_custom.delete(0, "end")
            self.entry_zona_custom.insert(0, str(offset))
            self.entry_zona_custom.config(state="disabled")

    def _ambil_koordinat_sholat(self):
        """Baca & validasi input koordinat sesuai mode aktif (desimal/DMS).
        Melempar ValueError dengan pesan jelas kalau tidak valid."""
        if self.mode_koordinat_sholat.get() == "desimal":
            try:
                lat = float(self.entry_lat_desimal.get().strip().replace(",", "."))
                lon = float(self.entry_lon_desimal.get().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Koordinat desimal tidak valid. Contoh format: -6.200000")
        else:
            try:
                lat = dms_ke_desimal(
                    self.entry_lat_d.get(), self.entry_lat_m.get(), self.entry_lat_s.get(),
                    arah=self.var_lat_arah.get())
                lon = dms_ke_desimal(
                    self.entry_lon_d.get(), self.entry_lon_m.get(), self.entry_lon_s.get(),
                    arah=self.var_lon_arah.get())
            except ValueError:
                raise ValueError("Koordinat DMS tidak valid. Isi derajat/menit/detik dengan angka.")

        if not (-90 <= lat <= 90):
            raise ValueError("Lintang harus di antara -90 dan 90 derajat.")
        if not (-180 <= lon <= 180):
            raise ValueError("Bujur harus di antara -180 dan 180 derajat.")
        return lat, lon

    def _ambil_zona_offset_sholat(self):
        try:
            return float(self.entry_zona_custom.get().strip().replace(",", "."))
        except ValueError:
            raise ValueError("Offset UTC zona waktu tidak valid. Contoh: 7 atau 7.5")

    def _ambil_tanggal_sholat(self):
        try:
            hari = int(self.entry_tgl_hari.get())
            bulan = int(self.entry_tgl_bulan.get())
            tahun = int(self.entry_tgl_tahun.get())
            return datetime(tahun, bulan, hari)
        except ValueError:
            raise ValueError("Tanggal tidak valid. Pastikan hari/bulan/tahun berupa angka & tanggal ada.")

    def _on_hitung_sholat(self):
        self._pastikan_tab_sholat_tampil()
        try:
            lat, lon = self._ambil_koordinat_sholat()
            zona_offset = self._ambil_zona_offset_sholat()
            tanggal = self._ambil_tanggal_sholat()
            try:
                elevasi = float(self.entry_elevasi.get().strip().replace(",", ".") or "0")
            except ValueError:
                raise ValueError("Elevasi tidak valid. Isi angka (mdpl), atau kosongkan/isi 0.")
            try:
                ihtiyat = float(self.entry_ihtiyat.get().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Ihtiyat (menit) tidak valid.")
            try:
                imsak_offset = float(self.entry_imsak_offset.get().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Offset Imsak (menit) tidak valid.")
        except ValueError as e:
            messagebox.showerror("Input tidak valid", str(e))
            return

        sudut_fajar, sudut_isya = PRESET_SUDUT[self.var_preset_sudut.get()]
        mazhab = self.var_mazhab_ashar.get()
        mode_hisab = self.mode_sholat.get()

        if mode_hisab == "jpl" and self.eph is None:
            messagebox.showwarning(
                "Ephemeris belum siap",
                "Mode Presisi (Skyfield + JPL DE421) dipilih, tapi ephemeris de421.bsp "
                "belum selesai dimuat. Perhitungan ini akan pakai Mode Ringan dulu; "
                "coba lagi sebentar setelah status di panel kiri menunjukkan siap.")
            mode_hisab = "ringan"

        waktu = hitung_waktu_sholat_otomatis(
            tanggal, lat, lon, zona_offset, mode=mode_hisab, ts=self.ts, eph=self.eph,
            elevasi_m=elevasi, sudut_fajar=sudut_fajar, sudut_isya=sudut_isya,
            ihtiyat_menit=ihtiyat, imsak_sebelum_fajr_menit=imsak_offset, mazhab_ashar=mazhab)

        az_spherical, jarak_spherical = qibla_spherical(lat, lon)
        az_vincenty, jarak_vincenty = qibla_vincenty(lat, lon)
        selisih_az = abs(az_spherical - az_vincenty)

        label_zona = self.var_zona_label.get()
        baris = []
        baris.append("=" * 46)
        baris.append("  WAKTU SHOLAT & ARAH KIBLAT — HisabWin")
        baris.append("=" * 46)
        baris.append(f"Tanggal        : {tanggal.strftime('%d %B %Y')}")
        baris.append(f"Koordinat      : {lat:.6f}, {lon:.6f}")
        baris.append(f"                 ({format_dms(lat, 'lat')}, {format_dms(lon, 'lon')})")
        baris.append(f"Elevasi        : {elevasi:.0f} mdpl")
        baris.append(f"Zona waktu     : {label_zona} (UTC{zona_offset:+g})")
        baris.append(f"Metode hisab   : "
                      f"{'Presisi (Skyfield + JPL DE421)' if mode_hisab == 'jpl' else 'Ringan (VSOP87)'}")
        baris.append(f"Kriteria sudut : {self.var_preset_sudut.get()}")
        baris.append(f"Mazhab Ashar   : {mazhab.capitalize()}   |   Ihtiyat: {ihtiyat:g} menit")
        baris.append("")
        baris.append("-- Waktu Sholat --")
        label_waktu = [
            ("Imsak", "imsak"), ("Subuh", "subuh"), ("Terbit", "terbit"), ("Dhuha", "dhuha"),
            ("Dzuhur", "dzuhur"), ("Ashar", "ashar"), ("Maghrib", "maghrib"), ("Isya", "isya"),
            ("Kiblat (V)", "kiblat_v"), ("Kiblat (S)", "kiblat_s"),
        ]
        for label, kunci in label_waktu:
            baris.append(f"  {label:<10}: {format_jam_desimal(waktu[kunci])}")
        baris.append("")
        baris.append("-- Arah Kiblat (dari lokasi ini, azimuth dari Utara searah jarum jam) --")
        baris.append(f"  Metode Spherical (bola bumi): {az_spherical:7.3f}°   |  jarak ± {jarak_spherical:,.1f} km")
        baris.append(f"  Metode Vincenty (elipsoid)  : {az_vincenty:7.3f}°   |  jarak ± {jarak_vincenty:,.1f} km")
        baris.append(f"  Selisih kedua metode        : {selisih_az:.4f}°")
        baris.append("")
        baris.append("Catatan: Vincenty memperhitungkan bentuk Bumi yang sedikit pepat "
                      "(elipsoid WGS84),\nsecara umum sedikit lebih akurat dibanding model bola "
                      "sferis, meskipun selisihnya\nbiasanya kecil untuk kebutuhan arah kiblat harian.")

        teks_hasil = "\n".join(baris)
        self._hasil_sholat_terakhir = teks_hasil

        self.text_hasil_sholat.configure(state="normal")
        self.text_hasil_sholat.delete("1.0", "end")
        self.text_hasil_sholat.insert("1.0", teks_hasil)
        self.text_hasil_sholat.configure(state="disabled")
        self.btn_simpan_hasil.config(state="normal")
        self.notebook_hasil_sholat.select(self.frame_hasil_hari_ini)

    def _on_hitung_jadwal_bulan(self):
        self._pastikan_tab_sholat_tampil()
        try:
            lat, lon = self._ambil_koordinat_sholat()
            zona_offset = self._ambil_zona_offset_sholat()
            try:
                bulan = BULAN_ID.index(self.var_bulan_jadwal.get()) + 1
                tahun = int(self.entry_tahun_jadwal.get().strip())
                if not (1 <= bulan <= 12):
                    raise ValueError
                datetime(tahun, bulan, 1)  # validasi tahun/bulan wajar
            except (ValueError, IndexError):
                raise ValueError("Bulan/Tahun jadwal tidak valid. Pilih Bulan & isi Tahun (4 digit).")
            try:
                elevasi = float(self.entry_elevasi.get().strip().replace(",", ".") or "0")
            except ValueError:
                raise ValueError("Elevasi tidak valid. Isi angka (mdpl), atau kosongkan/isi 0.")
            try:
                ihtiyat = float(self.entry_ihtiyat.get().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Ihtiyat (menit) tidak valid.")
            try:
                imsak_offset = float(self.entry_imsak_offset.get().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Offset Imsak (menit) tidak valid.")
        except ValueError as e:
            messagebox.showerror("Input tidak valid", str(e))
            return

        sudut_fajar, sudut_isya = PRESET_SUDUT[self.var_preset_sudut.get()]
        mazhab = self.var_mazhab_ashar.get()
        mode_hisab = self.mode_sholat.get()

        if mode_hisab == "jpl" and self.eph is None:
            messagebox.showwarning(
                "Ephemeris belum siap",
                "Mode Presisi (Skyfield + JPL DE421) dipilih, tapi ephemeris de421.bsp "
                "belum selesai dimuat. Jadwal sebulan ini akan dihitung dengan Mode Ringan "
                "dulu; coba lagi setelah ephemeris siap untuk hasil presisi Skyfield.")
            mode_hisab = "ringan"

        self.btn_hitung_jadwal_bulan.config(state="disabled")
        self.btn_hitung_sholat.config(state="disabled")
        self._log(f"\nMenghitung jadwal sholat {BULAN_ID[bulan - 1]} {tahun} "
                   f"(metode: {'Presisi Skyfield' if mode_hisab == 'jpl' else 'Ringan'})...")

        kwargs = dict(elevasi_m=elevasi, sudut_fajar=sudut_fajar, sudut_isya=sudut_isya,
                       ihtiyat_menit=ihtiyat, imsak_sebelum_fajr_menit=imsak_offset,
                       mazhab_ashar=mazhab)
        threading.Thread(
            target=self._hitung_jadwal_bulan_thread,
            args=(tahun, bulan, lat, lon, zona_offset, mode_hisab, kwargs),
            daemon=True).start()

    def _hitung_jadwal_bulan_thread(self, tahun, bulan, lat, lon, zona_offset, mode_hisab, kwargs):
        try:
            progress_cb = lambda msg: self.antrian.put(("progress", msg))
            jadwal = hitung_jadwal_sholat_bulan(
                tahun, bulan, lat, lon, zona_offset, mode=mode_hisab,
                ts=self.ts, eph=self.eph, progress_cb=progress_cb, **kwargs)
            self.antrian.put(("jadwal_bulan_ok", (tahun, bulan, mode_hisab, jadwal)))
        except Exception as e:
            self.antrian.put(("jadwal_bulan_error", f"Gagal menghitung jadwal sebulan: {e}"))

    def _tampilkan_jadwal_bulan(self, tahun, bulan, mode_hisab, jadwal):
        self._jadwal_bulan_terakhir = (tahun, bulan, mode_hisab, jadwal)

        self.tree_jadwal_bulan.delete(*self.tree_jadwal_bulan.get_children())
        for tanggal, waktu in jadwal:
            nama_hari = tanggal.strftime("%a")
            self.tree_jadwal_bulan.insert("", "end", values=(
                f"{tanggal.day:02d} {nama_hari}",
                format_jam_desimal(waktu["imsak"]),
                format_jam_desimal(waktu["subuh"]),
                format_jam_desimal(waktu["terbit"]),
                format_jam_desimal(waktu["dhuha"]),
                format_jam_desimal(waktu["dzuhur"]),
                format_jam_desimal(waktu["ashar"]),
                format_jam_desimal(waktu["maghrib"]),
                format_jam_desimal(waktu["isya"]),
                format_jam_desimal(waktu["kiblat_v"]),
                format_jam_desimal(waktu["kiblat_s"]),
            ))

        metode_label = "Presisi (Skyfield + JPL DE421)" if mode_hisab == "jpl" else "Ringan (VSOP87)"
        self.label_judul_jadwal_bulan.config(
            text=f"Jadwal Sholat {BULAN_ID[bulan - 1]} {tahun} — {len(jadwal)} hari "
                 f"— metode: {metode_label}")
        self.btn_simpan_csv_bulan.config(state="normal")
        self.notebook_hasil_sholat.select(self.frame_hasil_bulan)
        self._log(f"Jadwal sholat {BULAN_ID[bulan - 1]} {tahun} selesai dihitung "
                   f"({len(jadwal)} hari).")

    def _on_simpan_csv_jadwal_bulan(self):
        if not self._jadwal_bulan_terakhir:
            messagebox.showwarning("Belum ada jadwal", "Hitung jadwal sholat sebulan terlebih dahulu.")
            return
        tahun, bulan, mode_hisab, jadwal = self._jadwal_bulan_terakhir
        nama_default = f"jadwal_sholat_{BULAN_ID[bulan - 1]}_{tahun}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV (Comma-separated)", "*.csv"), ("Semua File", "*.*")],
            initialfile=nama_default,
            title="Simpan Jadwal Sholat Sebulan")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                penulis = csv.writer(f)
                penulis.writerow(["Tanggal", "Hari", "Imsak", "Subuh", "Terbit", "Dhuha",
                                   "Dzuhur", "Ashar", "Maghrib", "Isya", "Kiblat (Vincenty)", "Kiblat (Spherical)"])
                for tanggal, waktu in jadwal:
                    penulis.writerow([
                        tanggal.strftime("%d-%m-%Y"), tanggal.strftime("%A"),
                        format_jam_desimal(waktu["imsak"]), format_jam_desimal(waktu["subuh"]),
                        format_jam_desimal(waktu["terbit"]), format_jam_desimal(waktu["dhuha"]),
                        format_jam_desimal(waktu["dzuhur"]), format_jam_desimal(waktu["ashar"]),
                        format_jam_desimal(waktu["maghrib"]), format_jam_desimal(waktu["isya"]),
                        format_jam_desimal(waktu["kiblat_v"]), format_jam_desimal(waktu["kiblat_s"]),
                    ])
            messagebox.showinfo("Tersimpan", f"Jadwal sholat sebulan disimpan ke:\n{path}")
        except OSError as e:
            messagebox.showerror("Gagal menyimpan", f"Tidak bisa menulis file CSV:\n{e}")

    def _kumpulkan_data_lokasi(self):
        """Kumpulkan semua input lokasi/pengaturan saat ini jadi dict
        (dipakai untuk simpan ke file txt)."""
        lat, lon = self._ambil_koordinat_sholat()
        return {
            "mode_koordinat": self.mode_koordinat_sholat.get(),
            "mode_sholat": self.mode_sholat.get(),
            "lat": repr(lat),
            "lon": repr(lon),
            "elevasi": self.entry_elevasi.get().strip() or "0",
            "zona_label": self.var_zona_label.get(),
            "zona_offset": self.entry_zona_custom.get().strip(),
            "preset_sudut": self.var_preset_sudut.get(),
            "mazhab_ashar": self.var_mazhab_ashar.get(),
            "ihtiyat": self.entry_ihtiyat.get().strip(),
            "imsak_offset": self.entry_imsak_offset.get().strip(),
        }

    def _on_simpan_lokasi(self):
        try:
            data = self._kumpulkan_data_lokasi()
        except ValueError as e:
            messagebox.showerror("Input tidak valid", str(e))
            return
        path = self._path_file_lokasi()
        try:
            with open(path, "w", encoding="utf-8") as f:
                for k, v in data.items():
                    f.write(f"{k}={v}\n")
            messagebox.showinfo("Tersimpan", f"Lokasi & pengaturan disimpan ke:\n{path}\n\n"
                                              "Akan otomatis dimuat lagi saat aplikasi dibuka berikutnya.")
        except OSError as e:
            messagebox.showerror("Gagal menyimpan", f"Tidak bisa menulis file lokasi:\n{e}")

    def _muat_lokasi_awal(self):
        """Muat lokasi/pengaturan terakhir dari file txt lokal, kalau ada.
        Kalau file tidak ada, biarkan nilai default bawaan form (Jakarta,
        WIB) yang sudah diisi saat _bangun_tab_sholat."""
        path = self._path_file_lokasi()
        if not os.path.isfile(path):
            # Default awal yang masuk akal: Jakarta, WIB.
            self.entry_lat_desimal.insert(0, "-6.200000")
            self.entry_lon_desimal.insert(0, "106.816666")
            return

        data = {}
        try:
            with open(path, encoding="utf-8") as f:
                for baris in f:
                    baris = baris.strip()
                    if not baris or "=" not in baris:
                        continue
                    k, v = baris.split("=", 1)
                    data[k.strip()] = v.strip()
        except OSError:
            return

        try:
            mode = data.get("mode_koordinat", "desimal")
            self.mode_koordinat_sholat.set(mode if mode in ("desimal", "dms") else "desimal")

            mode_hisab_tersimpan = data.get("mode_sholat", "ringan")
            if mode_hisab_tersimpan in ("ringan", "jpl"):
                self.mode_sholat.set(mode_hisab_tersimpan)

            lat = float(data.get("lat", "-6.2"))
            lon = float(data.get("lon", "106.816666"))

            self.entry_lat_desimal.delete(0, "end")
            self.entry_lat_desimal.insert(0, f"{lat:.6f}")
            self.entry_lon_desimal.delete(0, "end")
            self.entry_lon_desimal.insert(0, f"{lon:.6f}")

            d, m, s, positif = desimal_ke_dms(lat)
            self.entry_lat_d.insert(0, str(d))
            self.entry_lat_m.insert(0, str(m))
            self.entry_lat_s.insert(0, f"{s:.2f}")
            self.var_lat_arah.set("LU" if positif else "LS")

            d, m, s, positif = desimal_ke_dms(lon)
            self.entry_lon_d.insert(0, str(d))
            self.entry_lon_m.insert(0, str(m))
            self.entry_lon_s.insert(0, f"{s:.2f}")
            self.var_lon_arah.set("BT" if positif else "BB")

            if "elevasi" in data:
                self.entry_elevasi.delete(0, "end")
                self.entry_elevasi.insert(0, data["elevasi"])

            if data.get("zona_label") in dict(ZONA_WAKTU_PILIHAN):
                self.var_zona_label.set(data["zona_label"])
            if "zona_offset" in data and data["zona_offset"]:
                self.entry_zona_custom.config(state="normal")
                self.entry_zona_custom.delete(0, "end")
                self.entry_zona_custom.insert(0, data["zona_offset"])
                if self.var_zona_label.get() != "Custom...":
                    self.entry_zona_custom.config(state="disabled")

            if data.get("preset_sudut") in PRESET_SUDUT:
                self.var_preset_sudut.set(data["preset_sudut"])
            if data.get("mazhab_ashar") in ("syafii", "hanafi"):
                self.var_mazhab_ashar.set(data["mazhab_ashar"])
            if "ihtiyat" in data:
                self.entry_ihtiyat.delete(0, "end")
                self.entry_ihtiyat.insert(0, data["ihtiyat"])
            if "imsak_offset" in data:
                self.entry_imsak_offset.delete(0, "end")
                self.entry_imsak_offset.insert(0, data["imsak_offset"])

            self._on_ganti_mode_koordinat_sholat()
            self._on_ganti_mode_sholat()
            self._log(f"Lokasi terakhir dimuat dari {os.path.basename(path)}.")
        except (ValueError, KeyError) as e:
            self._log(f"Gagal memuat file lokasi tersimpan ({e}), memakai default.")

    def _on_simpan_hasil_sholat(self):
        if not self._hasil_sholat_terakhir:
            messagebox.showwarning("Belum ada hasil", "Hitung waktu sholat & kiblat terlebih dahulu.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("File Teks", "*.txt"), ("Semua File", "*.*")],
            initialfile="waktu_sholat_kiblat.txt",
            title="Simpan Hasil Waktu Sholat & Kiblat")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._hasil_sholat_terakhir + "\n")
            messagebox.showinfo("Tersimpan", f"Hasil berhasil disimpan ke:\n{path}")
        except OSError as e:
            messagebox.showerror("Gagal menyimpan", f"Tidak bisa menulis file:\n{e}")

    # ---------------- Loading ephemeris ----------------

    def _muat_ephemeris(self):
        try:
            kernel_id = muat_kernel_aktif()
            if not status_kernel(kernel_id):
                # Kernel yang dipilih user ternyata sudah tidak ada di disk
                # (mis. dihapus manual di luar aplikasi) -- balik ke default
                # bawaan supaya aplikasi tetap bisa jalan, bukan macet error.
                simpan_kernel_aktif(KERNEL_DEFAULT_ID)
                kernel_id = KERNEL_DEFAULT_ID
            ts = load.timescale()
            path_bsp = path_utama_kernel(kernel_id)
            eph = load(path_bsp)
            self.antrian.put(("ephemeris_ok", (ts, eph)))
        except Exception as e:
            self.antrian.put(("error", f"Gagal memuat ephemeris: {e}"))

    # ---------------- Langkah 1: cari ijtimak ----------------

    def _on_cari_ijtimak(self):
        teks_tahun = self.entry_tahun.get().strip()
        if not (teks_tahun.isdigit() and len(teks_tahun) == 4):
            messagebox.showerror("Input tidak valid", "Masukkan angka tahun 4 digit, mis. 2026.")
            return

        tahun = int(teks_tahun)
        self.btn_cari.config(state="disabled")
        self.btn_proses.config(state="disabled")
        self.listbox_ijtimak.delete(0, "end")
        self._log(f"Mencari data ijtimak tahun {tahun}...")

        threading.Thread(target=self._cari_ijtimak_thread, args=(tahun,), daemon=True).start()

    def _cari_ijtimak_thread(self, tahun):
        try:
            ijtimak_times = cari_ijtimak_tahun(tahun, self.ts, self.eph, mode=self.mode.get())
            self.antrian.put(("ijtimak_ok", ijtimak_times))
        except Exception as e:
            self.antrian.put(("error", f"Gagal mencari ijtimak: {e}"))

    # ---------------- Langkah 3: proses peta ----------------

    def _on_proses(self):
        seleksi = self.listbox_ijtimak.curselection()
        if not seleksi:
            messagebox.showwarning("Belum dipilih", "Pilih salah satu ijtimak dari daftar terlebih dahulu.")
            return

        # Validasi setidaknya satu kriteria dipilih
        hitung_mabims_val = self.hitung_mabims.get()
        hitung_khgt_val = self.hitung_khgt.get()
        hitung_alt_val = self.hitung_alt.get()
        hitung_elong_val = self.hitung_elong.get()

        if not (hitung_mabims_val or hitung_khgt_val or hitung_alt_val or hitung_elong_val):
            messagebox.showwarning("Kriteria belum dipilih", "Pilih setidaknya satu kriteria peta yang ingin dihitung.")
            return

        idx = seleksi[0]
        waktu_ijtimak = ke_utc_datetime(self.ijtimak_times[idx])
        tanggal_ijtimak = datetime(waktu_ijtimak.year, waktu_ijtimak.month, waktu_ijtimak.day)
        tanggal_setelah = tanggal_ijtimak + timedelta(days=1)

        tanggal = tanggal_ijtimak if self.pilihan_hari.get() == "ijtimak" else tanggal_setelah
        self.tanggal_terpilih = tanggal
        self.waktu_ijtimak_terpilih = waktu_ijtimak

        # Tutup tab peta lama yang tidak terpilih untuk perhitungan baru ini,
        # agar tidak menampilkan data usang (outdated).
        tabs_to_close = []
        if not hitung_mabims_val and "mabims" in self._tab_peta:
            tabs_to_close.append(str(self._tab_peta["mabims"]["frame"]))
        if not hitung_khgt_val and "muhammadiyah" in self._tab_peta:
            tabs_to_close.append(str(self._tab_peta["muhammadiyah"]["frame"]))
        if not hitung_alt_val and "id_tinggi" in self._tab_peta:
            tabs_to_close.append(str(self._tab_peta["id_tinggi"]["frame"]))
        if not hitung_elong_val and "id_elongasi" in self._tab_peta:
            tabs_to_close.append(str(self._tab_peta["id_elongasi"]["frame"]))

        for tab_id in tabs_to_close:
            self._tutup_tab_by_id(tab_id)

        self.btn_proses.config(state="disabled")

        # Tentukan tugas tersisa berdasarkan kelompok kriteria yang diaktifkan
        hitung_global = hitung_mabims_val or hitung_khgt_val
        hitung_indonesia = hitung_alt_val or hitung_elong_val

        self._tugas_peta_tersisa = 0
        if hitung_global:
            self._tugas_peta_tersisa += 1
        if hitung_indonesia:
            self._tugas_peta_tersisa += 1

        self._log(f"\nIjtimak terpilih : {format_waktu_ijtimak(waktu_ijtimak)}")
        self._log(f"Tanggal diproses : {tanggal.strftime('%d %B %Y')}")
        self._log("Memulai perhitungan peta visibilitas hilal...")

        threading.Thread(target=self._hitung_grid_thread,
                         args=(tanggal, hitung_global, hitung_indonesia),
                         daemon=True).start()

    def _hitung_grid_thread(self, tanggal, hitung_global, hitung_indonesia):
        try:
            mode = self.mode.get()
            progress_cb = lambda msg: self.antrian.put(("progress", msg))
            waktu_ijtimak = self.waktu_ijtimak_terpilih

            # --- Grid Indonesia (Alt & Elongasi Lokal) ---
            if hitung_indonesia:
                def _hitung_id_paralel():
                    try:
                        grids_id = hitung_grid_indonesia(
                            tanggal, self.ts, self.eph, progress_cb=progress_cb, mode=mode)
                        self.antrian.put(("grid_id_ok", (tanggal, grids_id)))
                    except Exception as e:
                        self.antrian.put(("grid_id_error", f"Gagal menghitung grid Indonesia: {e}"))

                threading.Thread(target=_hitung_id_paralel, daemon=True).start()

            # --- Grid Global (MABIMS & Muhammadiyah) ---
            if hitung_global:
                hasil_pkg2_spekulatif = {}

                def _hitung_pkg2_paralel():
                    try:
                        hasil_pkg2_spekulatif["hasil_pkg2"] = cari_zona_pkg2_amerika(
                            tanggal, self.ts, self.eph, progress_cb=progress_cb, mode=mode)
                        bisa_hitung = (mode == "ringan") or (self.ts is not None and self.eph is not None)
                        if waktu_ijtimak is not None and bisa_hitung:
                            hasil_pkg2_spekulatif["waktu_fajar_nz"] = hitung_fajar_nz(
                                tanggal + timedelta(days=1), self.ts, self.eph, mode=mode)
                    except Exception as e:
                        hasil_pkg2_spekulatif["error"] = e

                thread_pkg2 = threading.Thread(target=_hitung_pkg2_paralel, daemon=True)
                thread_pkg2.start()

                grids = hitung_grid(tanggal, self.ts, self.eph, progress_cb=progress_cb, mode=mode)

                pkg2_precomputed = None
                if not _cek_pkg1_terpenuhi(grids):
                    progress_cb("PKG 1 tidak terpenuhi -- menunggu hasil PKG 2 Amerika "
                                "(sudah dihitung paralel sejak awal, tinggal menunggu selesai)...")
                    thread_pkg2.join()
                    if "error" in hasil_pkg2_spekulatif:
                        raise hasil_pkg2_spekulatif["error"]
                    pkg2_precomputed = hasil_pkg2_spekulatif

                evaluasi = evaluasi_pkg(grids, tanggal, waktu_ijtimak=waktu_ijtimak,
                                         ts=self.ts, eph=self.eph,
                                         progress_cb=progress_cb, mode=mode,
                                         pkg2_precomputed=pkg2_precomputed)

                self.antrian.put(("grid_global_ok", (tanggal, grids, evaluasi)))
        except Exception as e:
            self.antrian.put(("grid_global_error", f"Gagal menghitung grid: {e}"))

    # ---------------- Poll antrian (dijalankan di main thread) ----------------

    def _poll_antrian(self):
        try:
            while True:
                jenis, payload = self.antrian.get_nowait()

                if jenis == "ephemeris_ok":
                    self.ts, self.eph = payload
                    self._ephemeris_loading = False
                    if self.mode_sholat.get() == "jpl":
                        self.label_status_metode_sholat.config(
                            text="Mode Presisi aktif — ephemeris JPL DE421 sudah siap.")
                    self.btn_cari.config(state="normal")
                    if self._auto_cari_pending:
                        self._auto_cari_pending = False
                        self._log("Ephemeris siap. Mencari ulang ijtimak otomatis untuk Mode Presisi...")
                        self._on_cari_ijtimak()
                    else:
                        self._log("Ephemeris siap. Silakan masukkan tahun dan cari ijtimak.")

                elif jenis == "ijtimak_ok":
                    self.ijtimak_times = payload
                    if len(self.ijtimak_times) == 0:
                        self._log("Tidak ditemukan data ijtimak untuk tahun tersebut.")
                    else:
                        for t in self.ijtimak_times:
                            label = format_waktu_ijtimak(ke_utc_datetime(t))
                            self.listbox_ijtimak.insert("end", label)
                        self.listbox_ijtimak.selection_set(0)
                        self.btn_proses.config(state="normal")
                        self._log(f"Ditemukan {len(self.ijtimak_times)} kali ijtimak. Pilih salah satu di atas.")
                    self.btn_cari.config(state="normal")

                elif jenis == "gerhana_ok":
                    # Hasil mentah cari_gerhana_matahari_kandidat_ringan()/
                    # cari_gerhana_bulan_kandidat_ringan() berisi SEMUA waktu
                    # ijtimak/istiqbal setahun (~12-13 entri) -- kebanyakan
                    # BUKAN kandidat gerhana sama sekali. Saring dulu di sini
                    # supaya listbox cuma menampilkan entri yang benar2 kandidat
                    # gerhana (index self.kandidat_gerhana tetap sejajar dgn
                    # baris listbox_gerhana, sesuai catatan di __init__).
                    jenis_gerhana, kandidat_mentah, mode_gerhana = payload
                    self._jenis_gerhana_terakhir = jenis_gerhana
                    self._mode_gerhana_terakhir = mode_gerhana

                    if jenis_gerhana == "bulan":
                        self.kandidat_gerhana = [
                            k for k in kandidat_mentah if k["jenis"] != "tidak ada gerhana"
                        ]
                    else:
                        self.kandidat_gerhana = [
                            k for k in kandidat_mentah if k["waktu_greatest_eclipse"] is not None
                        ]

                    label_jenis = "matahari" if jenis_gerhana == "matahari" else "bulan"

                    if len(self.kandidat_gerhana) == 0:
                        self._log(f"Tidak ditemukan kandidat gerhana {label_jenis} pada tahun tersebut "
                                   "(tidak ada ijtimak/istiqbal dengan lintang ekliptika Bulan cukup kecil).")
                    else:
                        for k in self.kandidat_gerhana:
                            if jenis_gerhana == "bulan":
                                ikon = {"total": "🔴", "sebagian": "🟠",
                                        "penumbral": "⚪"}.get(k["jenis"], "⚪")
                                status = k["jenis"].capitalize()
                            else:
                                if k.get("kena_bumi"):
                                    ikon, status = "🔴", "Total/Cincin"
                                else:
                                    ikon, status = "🟡", "Parsial saja"
                            label = (f"{ikon} {format_waktu_ijtimak(k['waktu_greatest_eclipse'])} "
                                     f"— {status}")
                            self.listbox_gerhana.insert("end", label)
                        self.listbox_gerhana.selection_set(0)
                        self.btn_tampilkan_gerhana.config(state="normal")
                        self._log(f"Ditemukan {len(self.kandidat_gerhana)} kandidat gerhana {label_jenis}. "
                                   "Pilih salah satu di atas.")

                    self.btn_cari_gerhana.config(state="normal")

                elif jenis == "gerhana_error":
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self.btn_cari_gerhana.config(state="normal")

                elif jenis == "progress":
                    self._log(payload)

                elif jenis == "grid_global_ok":
                    # Peta global (MABIMS & Muhammadiyah / PKG 1-PKG 2) siap
                    # duluan -- langsung ditampilkan di sini, TIDAK menunggu
                    # peta Indonesia (yang dihitung paralel di thread lain
                    # dan tampil sendiri lewat "grid_id_ok" di bawah).
                    tanggal, grids, evaluasi = payload
                    self._log("Peta global (MABIMS/Muhammadiyah) selesai. Menampilkan...")
                    tgl_str = tanggal.strftime('%d %B %Y')
                    frame_to_select = None

                    if self.hitung_khgt.get():
                        fig_muh = buat_figure_muhammadiyah(grids, tanggal, evaluasi)
                        frame_muh = self._tampilkan_peta("muhammadiyah", f"🌙 Muhammadiyah — {tgl_str}", fig_muh)
                        frame_to_select = frame_muh

                    if self.hitung_mabims.get():
                        fig_mabims = buat_figure_mabims(grids, tanggal)
                        frame_mabims = self._tampilkan_peta("mabims", f"🌙 MABIMS — {tgl_str}", fig_mabims)
                        frame_to_select = frame_mabims

                    if frame_to_select is not None:
                        self.notebook.select(frame_to_select)

                    self._tugas_peta_tersisa = max(0, getattr(self, "_tugas_peta_tersisa", 1) - 1)
                    if self._tugas_peta_tersisa == 0:
                        self.btn_proses.config(state="normal")

                elif jenis == "grid_id_ok":
                    # Peta Indonesia siap -- ditampilkan begitu saja begitu
                    # selesai, tanpa mengganggu/memindahkan tab yang sedang
                    # dilihat user (mis. kalau user sudah ada di tab MABIMS).
                    tanggal, grids_id = payload
                    self._log("Peta Indonesia selesai. Menampilkan...")
                    tgl_str = tanggal.strftime('%d %B %Y')

                    if self.hitung_alt.get():
                        fig_id_tinggi = buat_figure_indonesia_tinggi_hilal(grids_id, tanggal)
                        self._tampilkan_peta("id_tinggi", f"🇮🇩 Tinggi Hilal — {tgl_str}", fig_id_tinggi)

                    if self.hitung_elong.get():
                        fig_id_elong = buat_figure_indonesia_elongasi(grids_id, tanggal)
                        self._tampilkan_peta("id_elongasi", f"🇮🇩 Elongasi — {tgl_str}", fig_id_elong)

                    self._tugas_peta_tersisa = max(0, getattr(self, "_tugas_peta_tersisa", 1) - 1)
                    if self._tugas_peta_tersisa == 0:
                        self.btn_proses.config(state="normal")

                elif jenis == "grid_id_error":
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self._tugas_peta_tersisa = max(0, getattr(self, "_tugas_peta_tersisa", 1) - 1)
                    if self._tugas_peta_tersisa == 0:
                        self.btn_proses.config(state="normal")

                elif jenis == "kalbanding_ok":
                    tahun_h, mode, hasil = payload
                    self._tampilkan_kalbanding(tahun_h, mode, hasil)

                elif jenis == "kalbanding_error":
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self.btn_bandingkan_kalender.config(state="normal")

                elif jenis == "efemeris_ok":
                    tanggal, lat, lon, zona_offset, mode, hasil, rts = payload
                    self._tampilkan_efemeris(tanggal, lat, lon, zona_offset, mode, hasil, rts)

                elif jenis == "efemeris_error":
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self.btn_buat_efemeris.config(state="normal")

                elif jenis == "konv_kriteria_ok":
                    self.label_hasil_konverter.config(text=payload)
                    self._log(f"Konverter Kalender: {payload.replace(chr(10), '  ')}")
                    self.btn_konversi.config(state="normal")

                elif jenis == "konv_kriteria_error":
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self.btn_konversi.config(state="normal")

                elif jenis == "jadwal_bulan_ok":
                    tahun, bulan, mode_hisab, jadwal = payload
                    self._tampilkan_jadwal_bulan(tahun, bulan, mode_hisab, jadwal)
                    self.btn_hitung_jadwal_bulan.config(state="normal")
                    self.btn_hitung_sholat.config(state="normal")

                elif jenis == "jadwal_bulan_error":
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self.btn_hitung_jadwal_bulan.config(state="normal")
                    self.btn_hitung_sholat.config(state="normal")

                elif jenis == "error":
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self.btn_cari.config(state="normal")
                    self.btn_proses.config(state="normal")

                elif jenis == "grid_global_error":
                    # Error khusus dari jalur grid global (MABIMS/Muhammadiyah).
                    # Jalur peta Indonesia berjalan independen dan tetap
                    # dilaporkan/ditampilkan sendiri lewat "grid_id_ok".
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Terjadi kesalahan", payload)
                    self._tugas_peta_tersisa = max(0, getattr(self, "_tugas_peta_tersisa", 1) - 1)
                    if self._tugas_peta_tersisa == 0:
                        self.btn_proses.config(state="normal")

        except queue.Empty:
            pass
        finally:
            self._poll_after_id = self.after(100, self._poll_antrian)


if __name__ == "__main__":
    app = HisabWinApp()
    app.mainloop()
