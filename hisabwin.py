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
import csv
import os
import queue
import sys
import threading
import tkinter as tk
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

matplotlib.use("TkAgg")

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.shapereader as shpreader
import matplotlib.pyplot as plt
import numpy as np
import shapely  # dipakai untuk shapely.contains_xy (vectorized, no Python loop)
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

        alt_sun_flat, _, _, _ = _altaz_matahari_bulan(tanggal, t_flat, lat_rep, lon_rep)
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
    sun = eph['sun']
    topo = wgs84.latlon(NZ_REF_LAT, NZ_REF_LON)

    # Jendela pencarian: dari jam -14 s/d +14 UTC pada tanggal tsb (relatif
    # ke tengah malam UTC), cukup lebar untuk mencakup dini hari lokal NZ
    # (UTC+12/+13) tanpa memotong kejadian fajarnya.
    t0 = ts.utc(tanggal_lokal.year, tanggal_lokal.month, tanggal_lokal.day, -14)
    t1 = ts.utc(tanggal_lokal.year, tanggal_lokal.month, tanggal_lokal.day, 14)

    f = almanac.risings_and_settings(eph, sun, topo, horizon_degrees=sudut_fajar)
    t, y = almanac.find_discrete(t0, t1, f)

    waktu_fajar = [tt.utc_datetime() for tt, yy in zip(t, y) if yy]
    if not waktu_fajar:
        return None
    return min(waktu_fajar)


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
        alt_sun_flat, _, _, _ = _altaz_matahari_bulan(tanggal, t_flat, lat_rep, lon_rep)
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
INDONESIA_RESOLUSI_HALUS = 0.15

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
    ax.add_feature(cfeature.LAND, facecolor="lightgray")
    ax.add_feature(cfeature.OCEAN, facecolor="lightblue")
    ax.add_feature(cfeature.BORDERS, linewidth=0.5, edgecolor="dimgray")
    ax.coastlines(linewidth=0.6)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False


def buat_figure_indonesia_tinggi_hilal(grids_id, tanggal):
    """Peta tinggi hilal toposentris khusus wilayah Indonesia, dengan garis
    kontur di SETIAP derajat bulat (mis. 0°, 1°, 2°, ...) — gaya peta yang
    biasa dipublikasikan BMKG — plus garis tebal untuk ambang MABIMS (3°)."""
    lon_mesh, lat_mesh = grids_id["lon_mesh"], grids_id["lat_mesh"]
    alt_grid = grids_id["alt_grid"]

    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    _gambar_peta_dasar_indonesia(ax)

    valid = ~np.isnan(alt_grid)
    if np.any(valid):
        lo = int(np.floor(np.nanmin(alt_grid)))
        hi = int(np.ceil(np.nanmax(alt_grid)))
    else:
        lo, hi = -2, 10
    levels_int = np.arange(lo, hi + 1, 1)

    cf = ax.contourf(lon_mesh, lat_mesh, alt_grid, levels=levels_int,
                      cmap="RdYlGn", extend="both", alpha=0.75,
                      transform=ccrs.PlateCarree())
    cs = ax.contour(lon_mesh, lat_mesh, alt_grid, levels=levels_int,
                     colors="black", linewidths=0.6, transform=ccrs.PlateCarree())
    ax.clabel(cs, fmt="%d°", fontsize=7, inline=True)

    # Ambang kriteria MABIMS (tinggi hilal >=3°) ditebalkan supaya menonjol
    # di antara garis-garis integer biasa.
    ax.contour(lon_mesh, lat_mesh, alt_grid, levels=[3], colors="blue",
               linewidths=2.2, transform=ccrs.PlateCarree())

    cbar = fig.colorbar(cf, ax=ax, orientation="vertical", pad=0.02, shrink=0.85)
    cbar.set_label("Tinggi hilal toposentris (°)")

    ax.set_title("Peta Tinggi Hilal Toposentris — Wilayah Indonesia\n"
                 f"{tanggal.strftime('%d %B %Y')}  (garis hitam: tiap 1°, garis biru tebal: 3° / MABIMS)",
                 fontsize=11, pad=12)
    fig.tight_layout()
    return fig


def buat_figure_indonesia_elongasi(grids_id, tanggal):
    """Peta elongasi khusus wilayah Indonesia, dengan garis kontur di SETIAP
    derajat bulat — gaya peta yang biasa dipublikasikan BMKG — plus garis
    tebal untuk ambang MABIMS (6.4°)."""
    lon_mesh, lat_mesh = grids_id["lon_mesh"], grids_id["lat_mesh"]
    elong_grid = grids_id["elong_grid"]

    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    _gambar_peta_dasar_indonesia(ax)

    valid = ~np.isnan(elong_grid)
    if np.any(valid):
        lo = int(np.floor(np.nanmin(elong_grid)))
        hi = int(np.ceil(np.nanmax(elong_grid)))
    else:
        lo, hi = 0, 12
    levels_int = np.arange(lo, hi + 1, 1)

    cf = ax.contourf(lon_mesh, lat_mesh, elong_grid, levels=levels_int,
                      cmap="RdYlGn", extend="both", alpha=0.75,
                      transform=ccrs.PlateCarree())
    cs = ax.contour(lon_mesh, lat_mesh, elong_grid, levels=levels_int,
                     colors="black", linewidths=0.6, transform=ccrs.PlateCarree())
    ax.clabel(cs, fmt="%d°", fontsize=7, inline=True)

    # Ambang kriteria MABIMS (elongasi >=6.4°) ditebalkan.
    ax.contour(lon_mesh, lat_mesh, elong_grid, levels=[6.4], colors="red",
               linewidths=2.2, transform=ccrs.PlateCarree())

    cbar = fig.colorbar(cf, ax=ax, orientation="vertical", pad=0.02, shrink=0.85)
    cbar.set_label("Elongasi (°)")

    ax.set_title("Peta Elongasi — Wilayah Indonesia\n"
                 f"{tanggal.strftime('%d %B %Y')}  (garis hitam: tiap 1°, garis merah tebal: 6.4° / MABIMS)",
                 fontsize=11, pad=12)
    fig.tight_layout()
    return fig


# =========================================================
#  PEMBUATAN FIGURE (dijalankan di thread utama / main thread)
# =========================================================

def buat_figure_mabims(grids, tanggal):
    lon_mesh, lat_mesh = grids["lon_mesh"], grids["lat_mesh"]
    elong_grid, alt_grid = grids["elong_grid"], grids["alt_grid"]

    fig = plt.figure(figsize=(13, 7.2))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="lightgray")
    ax.add_feature(cfeature.OCEAN, facecolor="lightblue")
    ax.coastlines(linewidth=0.5)

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
    fig.tight_layout()
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
                pkg2_ijtimak_ok = waktu_ijtimak < waktu_fajar_nz
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
                pkg2_ijtimak_ok = waktu_ijtimak < waktu_fajar_nz

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

    fig = plt.figure(figsize=(13, 7.2))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="lightgray")
    ax.add_feature(cfeature.OCEAN, facecolor="lightblue")
    ax.coastlines(linewidth=0.5)

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

    fig.tight_layout()
    return fig


# =========================================================
#  WAKTU SHOLAT & ARAH KIBLAT
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
        style.layout("Closable.TNotebook.Tab", [
            ("Notebook.tab", {"sticky": "nswe", "children": [
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

        # Menyimpan tab-peta yang sudah pernah dibuat: nama_tab -> {"frame":..., "fig":...}
        # supaya saat "Tampilkan Peta" ditekan lagi, kanvas & figure LAMA diganti
        # (bukan menumpuk tab baru terus-menerus).
        self._tab_peta = {}

        # Flag: apakah perlu otomatis mencari ulang ijtimak setelah ephemeris JPL
        # selesai dimuat (dipicu saat user ganti mode padahal sudah pernah mencari).
        self._auto_cari_pending = False

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

        def _toggle(event=None):
            if state["buka"]:
                body.pack_forget()
                label_panah.config(text="▸")
            else:
                body.pack(fill="x")
                label_panah.config(text="▾")
                if on_open is not None:
                    on_open()
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
        self.notebook = ClosableNotebook(self.paned)
        self.paned.add(self.notebook, weight=1)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_ganti_tab_notebook)
        self.notebook.bind("<<NotebookTabClosed>>", self._on_tab_notebook_ditutup)

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
            self._buat_bagian_akordeon(tab_kontrol, "🌙 Hilal — Cari Ijtimak & Peta",
                                        buka_awal=False, on_open=lambda: self._tutup_akordeon_sholat())

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

        self.btn_proses = ttk.Button(body_hilal, text="Tampilkan Peta", command=self._on_proses,
                                      state="disabled", style="Aksen.TButton")
        self.btn_proses.pack(pady=8)

        # --- Bagian akordeon ke-2: input Waktu Sholat & Kiblat (terlipat
        #     di awal -- baru terbuka otomatis begitu tab "Waktu Sholat &
        #     Kiblat" dipilih, lihat _on_ganti_tab_notebook) ---
        self._body_akordeon_sholat, self._buka_akordeon_sholat, self._tutup_akordeon_sholat = \
            self._buat_bagian_akordeon(tab_kontrol, "🕌 Input — Waktu Sholat & Kiblat",
                                        buka_awal=False, on_open=lambda: self._tutup_akordeon_hilal())

        # --- Tab tambahan: Waktu Sholat & Arah Kiblat (permanen, selalu ada) ---
        self._bangun_tab_sholat()

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
        else:
            self._buka_akordeon_hilal()
            self._tutup_akordeon_sholat()

    def _on_tab_notebook_ditutup(self, event=None):
        """Dipanggil begitu user klik tombol × di salah satu tab notebook
        kanan (lihat ClosableNotebook). Membersihkan state yang terkait
        dengan tab tsb, lalu benar-benar melepasnya dari notebook."""
        tab_id = self.notebook.tab_ditutup_terakhir
        if not tab_id:
            return
        self.notebook.tab_ditutup_terakhir = None

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
            self.notebook.add(self._frame_sholat, text="🕌 Waktu Sholat & Kiblat")
            self._tab_sholat_ditambahkan = True
        self.notebook.select(self._frame_sholat)

    def _log(self, pesan):
        self.text_log.configure(state="normal")
        self.text_log.insert("end", pesan + "\n")
        self.text_log.see("end")
        self.text_log.configure(state="disabled")

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
        self.entry_zona_custom = ttk.Entry(frame_zona, width=6, state="disabled")
        self.entry_zona_custom.insert(0, "7")
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
            ts = load.timescale()
            path_bsp = os.path.join(_resource_base_dir(), 'de421.bsp')
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

        idx = seleksi[0]
        waktu_ijtimak = ke_utc_datetime(self.ijtimak_times[idx])
        tanggal_ijtimak = datetime(waktu_ijtimak.year, waktu_ijtimak.month, waktu_ijtimak.day)
        tanggal_setelah = tanggal_ijtimak + timedelta(days=1)

        tanggal = tanggal_ijtimak if self.pilihan_hari.get() == "ijtimak" else tanggal_setelah
        self.tanggal_terpilih = tanggal
        self.waktu_ijtimak_terpilih = waktu_ijtimak

        self.btn_proses.config(state="disabled")
        # Ada 2 tugas independen yang berjalan paralel & tampil begitu masing-
        # masing siap (peta global MABIMS/Muhammadiyah, dan peta Indonesia).
        # Tombol "Tampilkan Peta" baru diaktifkan lagi setelah KEDUANYA
        # selesai (bisa dalam urutan apa saja).
        self._tugas_peta_tersisa = 2
        self._log(f"\nIjtimak terpilih : {format_waktu_ijtimak(waktu_ijtimak)}")
        self._log(f"Tanggal diproses : {tanggal.strftime('%d %B %Y')}")
        self._log("Memulai perhitungan peta visibilitas hilal...")

        threading.Thread(target=self._hitung_grid_thread, args=(tanggal,), daemon=True).start()

    def _hitung_grid_thread(self, tanggal):
        try:
            mode = self.mode.get()
            progress_cb = lambda msg: self.antrian.put(("progress", msg))
            waktu_ijtimak = self.waktu_ijtimak_terpilih

            # --- Grid Indonesia TIDAK bergantung sama sekali pada grid
            #     global atau hasil evaluasi PKG (cuma butuh tanggal/ts/eph),
            #     jadi dihitung di thread terpisah SECARA PARALEL dengan
            #     grid global + evaluasi PKG. Skyfield/VSOP87 di sini cuma
            #     melakukan operasi numpy baca-saja atas ephemeris yang
            #     sudah dimuat, jadi aman dipanggil paralel dari beberapa
            #     thread.
            #     PENTING: thread ini TIDAK di-join oleh thread utama --
            #     begitu selesai ia langsung kirim hasilnya sendiri ke
            #     antrian ("grid_id_ok"). Jadi peta global (MABIMS/
            #     Muhammadiyah, lihat "grid_global_ok" di bawah) maupun
            #     peta Indonesia sama-sama tampil begitu masing-masing
            #     siap -- yang lebih dulu selesai TIDAK menunggu yang
            #     lain. ---
            def _hitung_id_paralel():
                try:
                    grids_id = hitung_grid_indonesia(
                        tanggal, self.ts, self.eph, progress_cb=progress_cb, mode=mode)
                    self.antrian.put(("grid_id_ok", (tanggal, grids_id)))
                except Exception as e:
                    self.antrian.put(("grid_id_error", f"Gagal menghitung grid Indonesia: {e}"))

            threading.Thread(target=_hitung_id_paralel, daemon=True).start()

            # --- PKG 2 Amerika (pemindaian daratan benua + cek fajar NZ)
            #     juga TIDAK bergantung pada grid global -- satu-satunya
            #     alasan ia biasanya baru dihitung SETELAH grid global
            #     selesai adalah karena ia cuma DIBUTUHKAN kalau PKG 1
            #     ternyata gagal. Supaya tidak menunggu bergiliran, hasilnya
            #     dihitung SPEKULATIF di thread lain paralel dengan grid
            #     global sejak awal. Kalau nanti ternyata PKG 1 lolos (kasus
            #     paling umum), hasil ini tinggal diabaikan -- tidak
            #     menambah waktu tunggu sama sekali karena berjalan paralel,
            #     cuma memakai sedikit CPU ekstra di belakang layar. Kalau
            #     PKG 1 gagal, hasilnya sudah siap/hampir siap duluan,
            #     bukan baru mulai dihitung dari nol. ---
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
            # Kalau PKG 1 lolos, thread_pkg2 SENGAJA tidak di-join -- biarkan
            # selesai sendiri di belakang layar (daemon thread), hasilnya
            # tidak dipakai sama sekali.

            evaluasi = evaluasi_pkg(grids, tanggal, waktu_ijtimak=waktu_ijtimak,
                                     ts=self.ts, eph=self.eph,
                                     progress_cb=progress_cb, mode=mode,
                                     pkg2_precomputed=pkg2_precomputed)

            # --- Begitu evaluasi PKG (PKG 1, dan PKG 2 kalau perlu) selesai,
            #     LANGSUNG kirim ke antrian supaya peta MABIMS & Muhammadiyah
            #     bisa ditampilkan saat itu juga -- TIDAK menunggu thread
            #     grid Indonesia di atas selesai dulu. Kalau grid Indonesia
            #     belakangan selesai, ia akan tampil menyusul lewat pesan
            #     "grid_id_ok"-nya sendiri. ---
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

                elif jenis == "progress":
                    self._log(payload)

                elif jenis == "grid_global_ok":
                    # Peta global (MABIMS & Muhammadiyah / PKG 1-PKG 2) siap
                    # duluan -- langsung ditampilkan di sini, TIDAK menunggu
                    # peta Indonesia (yang dihitung paralel di thread lain
                    # dan tampil sendiri lewat "grid_id_ok" di bawah).
                    tanggal, grids, evaluasi = payload
                    self._log("Peta global (MABIMS/Muhammadiyah) selesai. Menampilkan...")
                    fig_mabims = buat_figure_mabims(grids, tanggal)
                    fig_muh = buat_figure_muhammadiyah(grids, tanggal, evaluasi)
                    tgl_str = tanggal.strftime('%d %B %Y')
                    self._tampilkan_peta("muhammadiyah", f"🌙 Muhammadiyah — {tgl_str}", fig_muh)
                    frame_mabims = self._tampilkan_peta("mabims", f"🌙 MABIMS — {tgl_str}", fig_mabims)
                    self.notebook.select(frame_mabims)  # tampilkan tab MABIMS terlebih dahulu
                    self._tugas_peta_tersisa = max(0, getattr(self, "_tugas_peta_tersisa", 1) - 1)
                    if self._tugas_peta_tersisa == 0:
                        self.btn_proses.config(state="normal")

                elif jenis == "grid_id_ok":
                    # Peta Indonesia siap -- ditampilkan begitu saja begitu
                    # selesai, tanpa mengganggu/memindahkan tab yang sedang
                    # dilihat user (mis. kalau user sudah ada di tab MABIMS).
                    tanggal, grids_id = payload
                    self._log("Peta Indonesia selesai. Menampilkan...")
                    fig_id_tinggi = buat_figure_indonesia_tinggi_hilal(grids_id, tanggal)
                    fig_id_elong = buat_figure_indonesia_elongasi(grids_id, tanggal)
                    tgl_str = tanggal.strftime('%d %B %Y')
                    self._tampilkan_peta("id_tinggi", f"🇮🇩 Tinggi Hilal — {tgl_str}", fig_id_tinggi)
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
