# ==== starmap.py ====
# Modul tambahan HisabWin: "Peta Langit" (rasi bintang + planet), untuk 1.0.2.
#
# TIDAK mengubah logika hisab hilal yang sudah ada di hisabwin.py -- modul ini
# murni fitur baru, ditulis terpisah supaya gampang direview/dibuang kalau
# ternyata tidak jadi dipakai.
#
# PENTING soal arsitektur: modul ini SENGAJA TIDAK melakukan
# "from hisabwin import ..." atau "import hisabwin" sama sekali, supaya
# tidak muncul circular import (hisabwin.py dijalankan sebagai skrip utama
# "__main__", jadi "import hisabwin" dari sini justru akan mengeksekusi
# ULANG seluruh hisabwin.py sebagai modul terpisah -- boros & rawan bug).
# Sebagai gantinya, fungsi murni astronomi yang dipakai ulang (julian_day,
# delta_t_detik, gast_derajat, nutasi_singkat, posisi_matahari, posisi_bulan)
# di-INJECT dari hisabwin.py lewat parameter `astro` (dict) tiap dipanggil,
# dan folder aset + gaya warna/font di-set sekali lewat inisialisasi().
#
# CARA PAKAI (lihat juga PATCH_INTEGRASI.md / file .patch):
#   import starmap
#   starmap.inisialisasi(folder_aset=_resource_base_dir(), WARNA_BG=WARNA_BG, ...)
#   ...
#   starmap.tampilkan_peta_langit(root, tanggal, jam_utc, lat, lon, ASTRO_FUNCS,
#                                  mode="jpl", eph=eph, ts=ts)
#
# ---------------------------------------------------------------------------
# SUMBER DATA & ATRIBUSI (aset CSV yang menyertai modul ini):
#   - bintang_terang.csv : disaring (magnitudo <= 5.0) dari HYG Database v41
#     (astronexus/HYG-Database, https://github.com/astronexus/HYG-Database),
#     lisensi CC BY-SA 4.0. Kolom RA/Dec dikonversi dari jam -> derajat,
#     epoch J2000 (TANPA koreksi presesi -- lihat catatan di _radec_ke_altaz).
#   - rasi_garis.csv : disederhanakan dari d3-celestial (ofrohn/d3-celestial,
#     https://github.com/ofrohn/d3-celestial), lisensi BSD-3-Clause.
#   Kalau HisabWin mau dirilis publik, tambahkan atribusi ini di
#   README/dialog "Tentang" -- CC BY-SA mensyaratkan penyebutan sumber.
# ---------------------------------------------------------------------------

import csv
import os
import tkinter as tk

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# =========================================================
# GAYA TAMPILAN (default mandiri -- dioverride oleh inisialisasi() supaya
# ikut tema hisabwin.py; kalau modul ini dipakai berdiri sendiri tanpa
# inisialisasi(), nilai default di bawah ini yang dipakai)
# =========================================================
WARNA_BG = "#F4F6F8"
WARNA_PANEL = "#FFFFFF"
WARNA_AKSEN = "#0F6E5B"
WARNA_TEKS = "#1F2937"
WARNA_TEKS_MUTED = "#6B7280"
WARNA_BORDER = "#E1E5EA"
FONT_UTAMA = ("Segoe UI", 10)
FONT_UTAMA_BOLD = ("Segoe UI", 10, "bold")
FONT_JUDUL = ("Segoe UI", 18, "bold")
FONT_KECIL = ("Segoe UI", 8)

# =========================================================
# ASET DATA
# =========================================================

MAG_LIMIT_DEFAULT = 4.5  # ambang magnitudo default (katalog sendiri s/d 5.0)

_FOLDER_ASET = os.path.dirname(os.path.abspath(__file__))
ASET_BINTANG = os.path.join(_FOLDER_ASET, "bintang_terang.csv")
ASET_RASI_GARIS = os.path.join(_FOLDER_ASET, "rasi_garis.csv")

_cache_bintang = None
_cache_rasi = None


def inisialisasi(folder_aset=None, **gaya):
    """Dipanggil SEKALI oleh hisabwin.py, tepat setelah `import starmap`.

    folder_aset : folder tempat 'bintang_terang.csv' & 'rasi_garis.csv'
                   berada -- kirim hasil _resource_base_dir() dari
                   hisabwin.py supaya konsisten dengan lokasi de421.bsp dkk,
                   termasuk setelah dibundel PyInstaller.
    **gaya      : optional, kirim WARNA_BG=..., FONT_UTAMA=..., dst (nama
                  variabel harus persis sama dengan konstanta modul ini di
                  atas) untuk menyamakan tampilan dengan tema hisabwin.py.
    """
    global _FOLDER_ASET, ASET_BINTANG, ASET_RASI_GARIS, _cache_bintang, _cache_rasi
    if folder_aset:
        _FOLDER_ASET = folder_aset
        ASET_BINTANG = os.path.join(_FOLDER_ASET, "bintang_terang.csv")
        ASET_RASI_GARIS = os.path.join(_FOLDER_ASET, "rasi_garis.csv")
        _cache_bintang = None  # paksa dimuat ulang dari lokasi baru
        _cache_rasi = None
    for kunci, nilai in gaya.items():
        if kunci in globals():
            globals()[kunci] = nilai


def _muat_katalog_bintang():
    """Memuat & meng-cache katalog bintang terang (RA/Dec J2000, magnitudo,
    nama) dari ASET_BINTANG. Return dict of numpy arrays, atau None kalau
    file aset tidak ditemukan (fitur tetap jalan, cuma tanpa bintang)."""
    global _cache_bintang
    if _cache_bintang is not None:
        return _cache_bintang
    if not os.path.exists(ASET_BINTANG):
        _cache_bintang = None
        return None
    ra, dec, mag, nama = [], [], [], []
    with open(ASET_BINTANG, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ra.append(float(row["ra_deg"]))
                dec.append(float(row["dec_deg"]))
                mag.append(float(row["mag"]))
            except (ValueError, KeyError):
                continue
            label = row.get("nama", "").strip()
            if not label:
                bayer = row.get("bayer", "").strip()
                kon = row.get("konstelasi", "").strip()
                label = f"{bayer} {kon}".strip() if bayer else ""
            nama.append(label)
    _cache_bintang = {
        "ra_deg": np.array(ra),
        "dec_deg": np.array(dec),
        "mag": np.array(mag),
        "nama": np.array(nama, dtype=object),
    }
    return _cache_bintang


def _muat_garis_rasi():
    """Memuat & meng-cache segmen garis rasi bintang dari ASET_RASI_GARIS.
    Return array (N,4) berisi (ra1,dec1,ra2,dec2) derajat, atau None."""
    global _cache_rasi
    if _cache_rasi is not None:
        return _cache_rasi
    if not os.path.exists(ASET_RASI_GARIS):
        _cache_rasi = None
        return None
    segmen = []
    with open(ASET_RASI_GARIS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                segmen.append((
                    float(row["ra1_deg"]), float(row["dec1_deg"]),
                    float(row["ra2_deg"]), float(row["dec2_deg"]),
                ))
            except (ValueError, KeyError):
                continue
    _cache_rasi = np.array(segmen) if segmen else None
    return _cache_rasi


# =========================================================
# KONVERSI RA/DEC -> ALTITUDE/AZIMUTH
# =========================================================

def _radec_ke_altaz(ra_deg, dec_deg, jd_ut, T, lat_deg, lon_deg, astro):
    """Konversi RA/Dec (derajat, katalog J2000) ke Altitude/Azimuth toposentris
    di satu titik & waktu, memakai GAST -- rumus & fungsi pendukung
    (astro['gast_derajat'], astro['nutasi_singkat']) SAMA PERSIS dengan yang
    dipakai _altaz_matahari_bulan() di hisabwin.py untuk Matahari/Bulan
    (di-inject lewat parameter `astro`, lihat inisialisasi modul ini),
    supaya konsisten satu kode dasar & tidak ada rumus dobel.

    CATATAN AKURASI: RA/Dec katalog di sini TIDAK dikoreksi presesi dari
    epoch J2000 ke tanggal pengamatan. Presesi Bumi ~0.014 derajat/tahun --
    untuk peta langit visual (bukan untuk kriteria hilal), efeknya jauh di
    bawah yang terlihat mata, jadi sengaja disederhanakan sama seperti
    pendekatan 'Ringan' lain di proyek ini.

    az_deg: azimuth diukur dari Utara, searah jarum jam (konvensi kompas).
    """
    dpsi, deps = astro["nutasi_singkat"](T)
    eps0 = 23 + 26 / 60 + 21.448 / 3600 - (46.8150 * T + 0.00059 * T ** 2
                                            - 0.001813 * T ** 3) / 3600
    eps = eps0 + deps
    gast = astro["gast_derajat"](jd_ut, T, dpsi, eps)
    lst = (gast + lon_deg) % 360
    H = ((lst - ra_deg + 180) % 360) - 180

    lat_r = np.radians(lat_deg)
    dec_r = np.radians(dec_deg)
    H_r = np.radians(H)

    alt = np.degrees(np.arcsin(
        np.sin(lat_r) * np.sin(dec_r) + np.cos(lat_r) * np.cos(dec_r) * np.cos(H_r)))
    az_selatan = np.degrees(np.arctan2(
        np.sin(H_r), np.cos(H_r) * np.sin(lat_r) - np.tan(dec_r) * np.cos(lat_r)))
    az = (az_selatan + 180) % 360  # dari acuan Selatan (Meeus) -> acuan Utara (kompas)
    return az, alt


def _waktu_ke_jd_T(tanggal, jam_utc, astro):
    """Helper: (tanggal, jam_utc desimal) -> (jd_ut, T abad Julian TT),
    dengan Delta-T sama seperti dipakai fungsi hisab lain di proyek ini."""
    dt = astro["delta_t_detik"](tanggal.year, tanggal.month)
    jd_ut = astro["julian_day"](tanggal.year, tanggal.month, tanggal.day + jam_utc / 24.0)
    T = (jd_ut + dt / 86400.0 - 2451545.0) / 36525.0
    return jd_ut, T


# =========================================================
# PERHITUNGAN ISI PETA LANGIT
# =========================================================

# Planet yang ditampilkan di mode JPL, dengan label kunci ephemeris DE421 dan
# nama tampilan. Bumi & Bulan/Matahari ditangani terpisah (lihat hitung_langit).
_DAFTAR_PLANET_JPL = [
    ("mercury barycenter", "Merkurius"),
    ("venus barycenter", "Venus"),
    ("mars barycenter", "Mars"),
    ("jupiter barycenter", "Jupiter"),
    ("saturn barycenter", "Saturnus"),
]


def hitung_langit(tanggal, jam_utc, lat, lon, astro, mode="jpl", eph=None, ts=None,
                   mag_limit=MAG_LIMIT_DEFAULT):
    """Menghitung posisi alt-az semua objek yang mau ditampilkan di peta
    langit (bintang, garis rasi, Matahari, Bulan, planet) untuk satu
    titik & waktu pengamatan.

    astro : dict berisi 6 fungsi murni dari hisabwin.py --
            {"julian_day", "delta_t_detik", "gast_derajat", "nutasi_singkat",
             "posisi_matahari", "posisi_bulan"} -- WAJIB diisi, lihat
            ASTRO_FUNCS di PATCH_INTEGRASI.

    mode='jpl'    -> planet dihitung presisi tinggi lewat eph (skyfield/DE421),
                      butuh parameter eph & ts terisi.
    mode='ringan' -> planet TIDAK dihitung (belum ada model VSOP87 planet di
                      proyek ini, cuma Matahari & Bulan), tapi bintang & garis
                      rasi tetap tampil normal (posisinya tidak butuh eph).

    Return dict:
      {
        "bintang": {"az":..,"alt":..,"mag":..,"nama":..}   (hanya alt>0)
        "garis_rasi": array (M,4) berisi (az1,alt1,az2,alt2) (kedua ujung alt>0)
        "objek": [ (nama, az, alt, warna), ... ]   # Matahari, Bulan, planet
      }
    """
    jd_ut, T = _waktu_ke_jd_T(tanggal, jam_utc, astro)

    hasil = {"bintang": None, "garis_rasi": None, "objek": []}

    # --- Bintang ---
    katalog = _muat_katalog_bintang()
    if katalog is not None:
        m = katalog["mag"] <= mag_limit
        az, alt = _radec_ke_altaz(katalog["ra_deg"][m], katalog["dec_deg"][m],
                                   jd_ut, T, lat, lon, astro)
        tampak = alt > 0
        hasil["bintang"] = {
            "az": az[tampak], "alt": alt[tampak],
            "mag": katalog["mag"][m][tampak],
            "nama": katalog["nama"][m][tampak],
        }

    # --- Garis rasi bintang ---
    garis = _muat_garis_rasi()
    if garis is not None:
        az1, alt1 = _radec_ke_altaz(garis[:, 0], garis[:, 1], jd_ut, T, lat, lon, astro)
        az2, alt2 = _radec_ke_altaz(garis[:, 2], garis[:, 3], jd_ut, T, lat, lon, astro)
        tampak = (alt1 > 0) & (alt2 > 0)
        hasil["garis_rasi"] = np.column_stack([az1, alt1, az2, alt2])[tampak]

    # --- Matahari & Bulan (selalu tersedia, JPL maupun Ringan) ---
    _, dec_s, lam_s, _ = astro["posisi_matahari"](np.array([T]))
    # RA Matahari dari lambda ekliptika (dipakai jg internal di
    # posisi_matahari()); dihitung ulang di sini via arctan2 supaya dapat
    # RA, bukan cuma dec, TANPA mengubah fungsi asli di hisabwin.py.
    eps0 = 23 + 26 / 60 + 21.448 / 3600
    eps_r = np.radians(eps0)
    lam_r = np.radians(lam_s)
    ra_s = np.degrees(np.arctan2(np.cos(eps_r) * np.sin(lam_r), np.cos(lam_r))) % 360
    az_s, alt_s = _radec_ke_altaz(ra_s, dec_s, jd_ut, T, lat, lon, astro)
    if alt_s[0] > 0:
        hasil["objek"].append(("Matahari", float(az_s[0]), float(alt_s[0]), "#F2A900"))

    ra_m, dec_m, _, _, _, _ = astro["posisi_bulan"](T)
    az_m, alt_m = _radec_ke_altaz(ra_m, dec_m, jd_ut, T, lat, lon, astro)
    if alt_m[0] > 0:
        hasil["objek"].append(("Bulan", float(az_m[0]), float(alt_m[0]), "#C7CBD1"))

    # --- Planet (mode JPL saja) ---
    if mode == "jpl" and eph is not None and ts is not None:
        from skyfield.api import wgs84
        t = ts.utc(tanggal.year, tanggal.month, tanggal.day, jam_utc)
        earth = eph["earth"]
        observer = earth + wgs84.latlon(lat, lon)
        for kunci, label in _DAFTAR_PLANET_JPL:
            if kunci not in eph:
                continue
            alt_ap, az_ap, _ = observer.at(t).observe(eph[kunci]).apparent().altaz()
            if alt_ap.degrees > 0:
                hasil["objek"].append((label, az_ap.degrees, alt_ap.degrees, "#5B8DEF"))

    return hasil


# =========================================================
# JENDELA GUI (Tkinter + Matplotlib, pola sama dengan jendela peta
# MABIMS/Muhammadiyah yang sudah ada di hisabwin.py)
# =========================================================

def gambar_jendela_peta_langit(parent, tanggal, jam_utc, lat, lon, data, mode="jpl",
                                mag_limit=MAG_LIMIT_DEFAULT):
    """Membuka jendela Toplevel berisi peta langit dari `data` yang SUDAH
    dihitung sebelumnya lewat hitung_langit() -- dipisah dari perhitungan
    supaya perhitungan bisa dijalankan di background thread (pola queue
    self.antrian yang sama seperti tab lain di hisabwin.py: hitung_langit()
    di thread pekerja, gambar_jendela_peta_langit() di main thread lewat
    _poll_antrian), sementara pembuatan widget Tkinter WAJIB di main thread.

    Kalau tidak butuh threading (mis. dipanggil langsung/skrip berdiri
    sendiri), pakai tampilkan_peta_langit() di bawah -- itu membungkus
    hitung_langit() + gambar_jendela_peta_langit() jadi satu panggilan.
    """
    win = tk.Toplevel(parent)
    win.title(f"Peta Langit — {tanggal:%d %B %Y} {jam_utc:05.2f} UTC "
              f"(Lat {lat:.2f}, Lon {lon:.2f})")
    win.configure(bg=WARNA_BG)
    win.geometry("900x760")

    header = tk.Frame(win, bg=WARNA_BG)
    header.pack(fill="x", padx=16, pady=(14, 4))
    tk.Label(header, text="Peta Langit", font=FONT_JUDUL,
              bg=WARNA_BG, fg=WARNA_TEKS).pack(anchor="w")
    sub = (f"{tanggal:%d %B %Y}, {jam_utc:05.2f} UTC — Lintang {lat:.3f}°, "
           f"Bujur {lon:.3f}°" + ("" if mode == "jpl" else "  (mode Ringan: tanpa planet)"))
    tk.Label(header, text=sub, font=FONT_UTAMA, bg=WARNA_BG,
              fg=WARNA_TEKS_MUTED).pack(anchor="w")

    fig = plt.Figure(figsize=(7.5, 7.5), dpi=100, facecolor=WARNA_BG)
    ax = fig.add_subplot(111, projection="polar", facecolor="#0B1220")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)  # searah jarum jam: U -> T -> S -> B
    ax.set_ylim(0, 90)
    ax.set_yticks([0, 30, 60, 90])
    ax.set_yticklabels(["90°", "60°", "30°", "Ufuk"], color=WARNA_TEKS_MUTED, fontsize=8)
    ax.set_xticks(np.radians([0, 90, 180, 270]))
    ax.set_xticklabels(["U", "T", "S", "B"], color=WARNA_TEKS, fontweight="bold")
    ax.grid(color="#2A3446", linewidth=0.6)

    # Garis rasi bintang (digambar duluan, di lapisan paling bawah)
    if data["garis_rasi"] is not None and len(data["garis_rasi"]) > 0:
        for az1, alt1, az2, alt2 in data["garis_rasi"]:
            ax.plot([np.radians(az1), np.radians(az2)], [90 - alt1, 90 - alt2],
                     color="#3D4A63", linewidth=0.8, zorder=1)

    # Bintang -- ukuran titik proporsional ke terang (magnitudo makin kecil
    # makin terang), memakai skala kuadratik sederhana yang umum dipakai
    # untuk sky chart.
    if data["bintang"] is not None and len(data["bintang"]["az"]) > 0:
        b = data["bintang"]
        ukuran = np.clip((mag_limit - b["mag"] + 1.0), 0.5, None) ** 2 * 2.0
        ax.scatter(np.radians(b["az"]), 90 - b["alt"], s=ukuran,
                    c="white", edgecolors="none", zorder=2)

    # Matahari, Bulan, planet -- marker & label khusus
    for nama, az, alt, warna in data["objek"]:
        ax.scatter([np.radians(az)], [90 - alt], s=160, c=warna,
                    edgecolors="#1F2937", linewidths=0.8, zorder=3)
        ax.annotate(nama, (np.radians(az), 90 - alt), color=warna,
                     fontsize=9, fontweight="bold", zorder=4,
                     xytext=(6, 6), textcoords="offset points")

    canvas = FigureCanvasTkAgg(fig, master=win)
    canvas.draw()
    canvas.get_tk_widget().pack(fill="both", expand=True, padx=16, pady=8)

    toolbar_frame = tk.Frame(win, bg=WARNA_BG)
    toolbar_frame.pack(fill="x", padx=16, pady=(0, 12))
    toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
    toolbar.update()

    if data["bintang"] is None or data["garis_rasi"] is None:
        tk.Label(win, text=("Catatan: aset katalog bintang/rasi tidak ditemukan "
                             "di folder aplikasi -- peta tetap tampil tanpa itu."),
                  font=FONT_KECIL, bg=WARNA_BG, fg=WARNA_TEKS_MUTED).pack(pady=(0, 8))

    return win


def tampilkan_peta_langit(parent, tanggal, jam_utc, lat, lon, astro,
                           mode="jpl", eph=None, ts=None, mag_limit=MAG_LIMIT_DEFAULT):
    """Cara pakai SINKRON (hitung + gambar sekaligus, di thread manapun ini
    dipanggil) -- kalau dipanggil dari main thread Tkinter, ini paling
    simpel. Untuk pola threading+antrian seperti tab lain di hisabwin.py,
    panggil hitung_langit() di thread pekerja lalu gambar_jendela_peta_langit()
    di main thread (lihat PATCH_INTEGRASI)."""
    data = hitung_langit(tanggal, jam_utc, lat, lon, astro, mode=mode, eph=eph, ts=ts,
                          mag_limit=mag_limit)
    return gambar_jendela_peta_langit(parent, tanggal, jam_utc, lat, lon, data,
                                       mode=mode, mag_limit=mag_limit)
