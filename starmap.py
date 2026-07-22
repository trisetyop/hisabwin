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
import time
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

import numpy as np
import matplotlib.pyplot as plt
# FigureCanvasTkAgg/NavigationToolbar2Tk hanya dipakai di gambar_jendela_peta_
# langit() & buka_planetarium() (jendela GUI Tkinter), tidak pernah dipanggil
# dari hitung_langit()/lengkapi_garis_rasi_altaz() (fungsi kalkulasi murni
# yang dipakai server.py). Dibungkus try/except supaya starmap.py tetap bisa
# di-import di server tanpa Tcl/Tk sama sekali (mis. lingkungan serverless).
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
except ImportError:
    FigureCanvasTkAgg = None
    NavigationToolbar2Tk = None

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
    nama, sebutan Bayer/Flamsteed, rasi, jarak) dari ASET_BINTANG. Return
    dict of numpy arrays, atau None kalau file aset tidak ditemukan (fitur
    tetap jalan, cuma tanpa bintang)."""
    global _cache_bintang
    if _cache_bintang is not None:
        return _cache_bintang
    if not os.path.exists(ASET_BINTANG):
        _cache_bintang = None
        return None
    ra, dec, mag, nama = [], [], [], []
    bayer, flam, konstelasi, hip, dist_pc = [], [], [], [], []
    with open(ASET_BINTANG, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ra.append(float(row["ra_deg"]))
                dec.append(float(row["dec_deg"]))
                mag.append(float(row["mag"]))
            except (ValueError, KeyError):
                continue
            b = row.get("bayer", "").strip()
            fl = row.get("flam", "").strip()
            kon = row.get("konstelasi", "").strip()
            label = row.get("nama", "").strip()
            if not label:
                label = f"{b} {kon}".strip() if b else (f"{fl} {kon}".strip() if fl else "")
            nama.append(label)
            bayer.append(b)
            flam.append(fl)
            konstelasi.append(kon)
            try:
                hip.append(int(float(row.get("hip", "") or -1)))
            except ValueError:
                hip.append(-1)
            try:
                dist_pc.append(float(row.get("dist_pc", "") or -1))
            except ValueError:
                dist_pc.append(-1.0)
    _cache_bintang = {
        "ra_deg": np.array(ra),
        "dec_deg": np.array(dec),
        "mag": np.array(mag),
        "nama": np.array(nama, dtype=object),
        "bayer": np.array(bayer, dtype=object),
        "flam": np.array(flam, dtype=object),
        "konstelasi": np.array(konstelasi, dtype=object),
        "hip": np.array(hip),
        "dist_pc": np.array(dist_pc),
    }
    return _cache_bintang


# Sebutan Bayer (huruf Yunani) singkatan -> simbol, dipakai buat tampilan
# info objek (mis. "Alp" -> "\u03b1"), sama seperti dipakai Stellarium/Cartes du Ciel.
_HURUF_BAYER = {
    "Alp": "\u03b1", "Bet": "\u03b2", "Gam": "\u03b3", "Del": "\u03b4", "Eps": "\u03b5",
    "Zet": "\u03b6", "Eta": "\u03b7", "The": "\u03b8", "Iot": "\u03b9", "Kap": "\u03ba",
    "Lam": "\u03bb", "Mu": "\u03bc", "Nu": "\u03bd", "Xi": "\u03be", "Omi": "\u03bf",
    "Pi": "\u03c0", "Rho": "\u03c1", "Sig": "\u03c3", "Tau": "\u03c4", "Ups": "\u03c5",
    "Phi": "\u03c6", "Chi": "\u03c7", "Psi": "\u03c8", "Ome": "\u03c9",
}

# Kode IAU 3-huruf -> nama lengkap rasi bintang (Latin), 88 rasi standar --
# dipakai biar info objek menampilkan "Canis Major" bukan cuma "CMa".
_NAMA_RASI = {
    "And": "Andromeda", "Ant": "Antlia", "Aps": "Apus", "Aqr": "Aquarius",
    "Aql": "Aquila", "Ara": "Ara", "Ari": "Aries", "Aur": "Auriga",
    "Boo": "Bo\u00f6tes", "Cae": "Caelum", "Cam": "Camelopardalis", "Cnc": "Cancer",
    "CVn": "Canes Venatici", "CMa": "Canis Major", "CMi": "Canis Minor",
    "Cap": "Capricornus", "Car": "Carina", "Cas": "Cassiopeia", "Cen": "Centaurus",
    "Cep": "Cepheus", "Cet": "Cetus", "Cha": "Chamaeleon", "Cir": "Circinus",
    "Col": "Columba", "Com": "Coma Berenices", "CrA": "Corona Australis",
    "CrB": "Corona Borealis", "Crv": "Corvus", "Crt": "Crater", "Cru": "Crux",
    "Cyg": "Cygnus", "Del": "Delphinus", "Dor": "Dorado", "Dra": "Draco",
    "Equ": "Equuleus", "Eri": "Eridanus", "For": "Fornax", "Gem": "Gemini",
    "Gru": "Grus", "Her": "Hercules", "Hor": "Horologium", "Hya": "Hydra",
    "Hyi": "Hydrus", "Ind": "Indus", "Lac": "Lacerta", "Leo": "Leo",
    "LMi": "Leo Minor", "Lep": "Lepus", "Lib": "Libra", "Lup": "Lupus",
    "Lyn": "Lynx", "Lyr": "Lyra", "Men": "Mensa", "Mic": "Microscopium",
    "Mon": "Monoceros", "Mus": "Musca", "Nor": "Norma", "Oct": "Octans",
    "Oph": "Ophiuchus", "Ori": "Orion", "Pav": "Pavo", "Peg": "Pegasus",
    "Per": "Perseus", "Phe": "Phoenix", "Pic": "Pictor", "Psc": "Pisces",
    "PsA": "Piscis Austrinus", "Pup": "Puppis", "Pyx": "Pyxis", "Ret": "Reticulum",
    "Sge": "Sagitta", "Sgr": "Sagittarius", "Sco": "Scorpius", "Scl": "Sculptor",
    "Sct": "Scutum", "Ser": "Serpens", "Sex": "Sextans", "Tau": "Taurus",
    "Tel": "Telescopium", "Tri": "Triangulum", "TrA": "Triangulum Australe",
    "Tuc": "Tucana", "UMa": "Ursa Major", "UMi": "Ursa Minor", "Vel": "Vela",
    "Vir": "Virgo", "Vol": "Volans", "Vul": "Vulpecula",
}


def _format_ra(ra_deg):
    """Derajat -> string jam:menit:detik ('06j 45m 08.6d'), format baku RA
    di aplikasi astronomi (Stellarium, Cartes du Ciel, dll)."""
    jam_desimal = (ra_deg % 360) / 15.0
    j = int(jam_desimal)
    sisa_menit = (jam_desimal - j) * 60
    m = int(sisa_menit)
    d = (sisa_menit - m) * 60
    return f"{j:02d}\u02b0 {m:02d}\u1d50 {d:04.1f}\u02e2"


def _format_dec(dec_deg):
    """Derajat -> string derajat:menit:detik ('-16\u00b0 42\u2032 58\u2033')."""
    tanda = "-" if dec_deg < 0 else "+"
    d_abs = abs(dec_deg)
    d = int(d_abs)
    sisa_menit = (d_abs - d) * 60
    m = int(sisa_menit)
    s = (sisa_menit - m) * 60
    return f"{tanda}{d:02d}\u00b0 {m:02d}\u2032 {s:04.1f}\u2033"


def _nama_tampilan_bintang(nama, bayer, flam, konstelasi):
    """Bangun nama tampilan ala katalog bintang (mis. 'Sirius (\u03b1 CMa)'
    atau '61 Cyg' kalau tidak ada nama diri)."""
    rasi_lengkap = _NAMA_RASI.get(konstelasi, konstelasi)
    sebutan = ""
    if bayer:
        huruf = _HURUF_BAYER.get(bayer.split("-")[0], bayer)
        sebutan = f"{huruf} {konstelasi}"
    elif flam:
        sebutan = f"{flam} {konstelasi}"
    if nama and sebutan:
        return f"{nama} ({sebutan})", rasi_lengkap
    elif nama:
        return nama, rasi_lengkap
    elif sebutan:
        return sebutan, rasi_lengkap
    else:
        return f"Bintang {konstelasi}", rasi_lengkap


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


def lengkapi_garis_rasi_altaz(data, tanggal, jam_utc, lat, lon, astro):
    """Hitung alt-az segmen garis rasi bintang & simpan ke data["garis_rasi_altaz"].

    Garis rasi disimpan di katalog sebagai RA/Dec, jadi HARUS dikonversi ke
    Alt/Az yang sama seperti bintang sebelum diproyeksikan -- kalau tidak
    garis-garisnya tidak akan menyatu dengan posisi bintang yang sebenarnya.

    Dipanggil baik oleh JendelaPlanetarium sendiri (_hitung_posisi_mag_penuh)
    maupun oleh pemanggil dari luar (mis. thread background di hisabwin.py
    yang menyiapkan `data_awal` SEBELUM jendela dibuka) -- supaya kunci
    "garis_rasi_altaz" selalu terisi dan garis rasi selalu tampil, tidak
    peduli dari jalur mana `data` itu berasal."""
    garis = _muat_garis_rasi()
    if garis is not None:
        jd_ut, T = _waktu_ke_jd_T(tanggal, jam_utc, astro)
        az1, alt1 = _radec_ke_altaz(garis[:, 0], garis[:, 1], jd_ut, T, lat, lon, astro)
        az2, alt2 = _radec_ke_altaz(garis[:, 2], garis[:, 3], jd_ut, T, lat, lon, astro)
        data["garis_rasi_altaz"] = np.column_stack([az1, alt1, az2, alt2])
    else:
        data["garis_rasi_altaz"] = None
    return data


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
        "bintang": {"az":..,"alt":..,"mag":..,"nama":..,"bayer":..,"flam":..,
                     "konstelasi":..,"hip":..,"dist_pc":..}   (hanya alt>0)
        "garis_rasi": array (M,4) berisi (az1,alt1,az2,alt2) (kedua ujung alt>0)
        "objek": [ (nama, az, alt, warna, jenis, ra_deg, dec_deg), ... ]
                 # Matahari/Bulan/planet -- jenis dipakai buat info panel
                 # ("Matahari"/"Bulan"/"Planet"), ra/dec buat ditampilkan di
                 # kartu info kalau objek diklik (lihat _pilih_objek).
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
            "ra_deg": katalog["ra_deg"][m][tampak], "dec_deg": katalog["dec_deg"][m][tampak],
            "mag": katalog["mag"][m][tampak],
            "nama": katalog["nama"][m][tampak],
            "bayer": katalog["bayer"][m][tampak], "flam": katalog["flam"][m][tampak],
            "konstelasi": katalog["konstelasi"][m][tampak],
            "hip": katalog["hip"][m][tampak], "dist_pc": katalog["dist_pc"][m][tampak],
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
        hasil["objek"].append(("Matahari", float(az_s[0]), float(alt_s[0]), "#F2A900",
                                "Matahari", float(ra_s[0]), float(dec_s[0])))

    ra_m, dec_m, _, _, _, _ = astro["posisi_bulan"](T)
    az_m, alt_m = _radec_ke_altaz(ra_m, dec_m, jd_ut, T, lat, lon, astro)
    if alt_m[0] > 0:
        hasil["objek"].append(("Bulan", float(az_m[0]), float(alt_m[0]), "#C7CBD1",
                                "Bulan", float(ra_m[0]), float(dec_m[0])))

    # --- Planet (mode JPL saja) ---
    if mode == "jpl" and eph is not None and ts is not None:
        from skyfield.api import wgs84
        t = ts.utc(tanggal.year, tanggal.month, tanggal.day, jam_utc)
        earth = eph["earth"]
        observer = earth + wgs84.latlon(lat, lon)
        for kunci, label in _DAFTAR_PLANET_JPL:
            if kunci not in eph:
                continue
            posisi_tampak = observer.at(t).observe(eph[kunci]).apparent()
            alt_ap, az_ap, _ = posisi_tampak.altaz()
            if alt_ap.degrees > 0:
                ra_ap, dec_ap, _ = posisi_tampak.radec()
                hasil["objek"].append((label, az_ap.degrees, alt_ap.degrees, "#5B8DEF",
                                        "Planet", ra_ap.hours * 15.0, dec_ap.degrees))

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
    for nama, az, alt, warna, _jenis, _ra, _dec in data["objek"]:
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

# =========================================================
# MODE PLANETARIUM -- jendela interaktif ala Cartes du Ciel/Stellarium mini:
# pan (klik = geser pusat pandang), zoom (scroll mouse), kontrol waktu
# (play/pause + kecepatan), pencarian objek, toggle layer, info panel.
#
# Proyeksi: stereografik (konformal, bentuk rasi tidak melenceng) dipusatkan
# di titik pandang (az0, alt0) yang BISA digeser -- beda dari
# gambar_jendela_peta_langit() di atas yang selalu dome penuh dipusatkan di
# zenith (proyeksi polar bawaan matplotlib, tidak bisa di-pan).
# =========================================================

def _proyeksi_stereografik(az_deg, alt_deg, az0_deg, alt0_deg):
    """(az,alt) objek + (az0,alt0) pusat pandang -> (x, y, jarak_sudut_deg).
    Rumus bearing/jarak sudut standar trigonometri bola (sama polanya
    dengan rumus navigasi great-circle), lalu jari-jari stereografik
    r = 2*tan(c/2) -- konformal, jadi bentuk rasi bintang di dekat pusat
    pandang tidak melenceng walau di-zoom/pan."""
    az = np.radians(az_deg); alt = np.radians(alt_deg)
    az0 = np.radians(az0_deg); alt0 = np.radians(alt0_deg)
    daz = az - az0
    cos_c = np.sin(alt0) * np.sin(alt) + np.cos(alt0) * np.cos(alt) * np.cos(daz)
    cos_c = np.clip(cos_c, -1.0, 0.999999)
    c = np.arccos(cos_c)
    theta = np.arctan2(np.sin(daz) * np.cos(alt),
                        np.cos(alt0) * np.sin(alt) - np.sin(alt0) * np.cos(alt) * np.cos(daz))
    r = 2.0 * np.tan(c / 2.0)
    x = r * np.sin(theta)
    y = r * np.cos(theta)
    return x, y, np.degrees(c)


def _proyeksi_stereografik_balik(x, y, az0_deg, alt0_deg):
    """Kebalikan _proyeksi_stereografik(): (x,y) di kanvas -> (az,alt) asli.
    Dipakai buat tahu koordinat langit dari titik yang diklik user (rumus
    "destination point" navigasi bola, kebalikan matematis dari rumus
    bearing/jarak di atas)."""
    az0 = np.radians(az0_deg); alt0 = np.radians(alt0_deg)
    r = np.hypot(x, y)
    theta = np.arctan2(x, y)
    c = 2.0 * np.arctan2(r, 2.0)
    alt = np.arcsin(np.sin(alt0) * np.cos(c) + np.cos(alt0) * np.sin(c) * np.cos(theta))
    az = az0 + np.arctan2(np.sin(theta) * np.sin(c) * np.cos(alt0),
                           np.cos(c) - np.sin(alt0) * np.sin(alt))
    return (np.degrees(az) % 360), np.degrees(alt)


# Kecepatan animasi waktu: (label tampil, kali lipat kecepatan riil)
KECEPATAN_PILIHAN = [
    ("Nyata (1x)", 1),
    ("60x (1 menit/detik)", 60),
    ("600x", 600),
    ("3.600x (1 jam/detik)", 3600),
    ("86.400x (1 hari/detik)", 86400),
]
_INTERVAL_TICK_MS = 300  # jeda antar frame animasi (ms), cukup halus tanpa berat


class JendelaPlanetarium(tk.Toplevel):
    """Jendela "Mode Planetarium": versi interaktif dari peta langit --
    bisa di-pan/zoom, waktunya bisa dijalankan (play/pause), bisa cari
    objek, toggle layer, dan klik bintang/planet buat lihat info singkat.

    Beda arsitektur dari gambar_jendela_peta_langit(): di situ posisi
    dihitung SEKALI lalu digambar statis. Di sini posisi dihitung ULANG
    tiap kali waktu berubah (langkah manual / animasi berjalan), tapi
    TIDAK dihitung ulang untuk pan/zoom semata (itu murni ganti proyeksi
    dari data alt-az yang sama, jadi ringan & responsif).
    """

    def __init__(self, parent, tanggal, jam_utc, lat, lon, astro, mode="jpl",
                 eph=None, ts=None, mag_limit_awal=4.0, data_awal=None):
        super().__init__(parent)
        self.astro = astro
        self.eph = eph
        self.ts = ts
        self.mode = mode
        self.lat = lat
        self.lon = lon
        self.tanggal = tanggal
        self.jam_utc = jam_utc

        self.az_center = 180.0   # hadap Selatan secara default
        self.alt_center = 45.0
        self.fov = 90.0           # "field of view" -- makin kecil makin zoom
        self.mag_limit = mag_limit_awal

        self._drag_terakhir_xy = None   # (xdata, ydata) posisi mouse terakhir selama drag
        self._drag_bergerak = False     # True kalau mouse sempat digeser selama tombol ditekan
        self._drag_waktu_gambar_terakhir = 0.0  # throttle redraw saat drag (biar tidak berat)

        self.tampil_garis_rasi = tk.BooleanVar(value=True)
        self.tampil_nama_rasi = tk.BooleanVar(value=True)
        self.tampil_nama_bintang = tk.BooleanVar(value=True)
        self.tampil_grid = tk.BooleanVar(value=True)

        self.sedang_main = False
        self.var_kecepatan = tk.StringVar(value=KECEPATAN_PILIHAN[2][0])  # 600x default

        self.title("Mode Planetarium — HisabWin")
        self.configure(bg=WARNA_BG)
        self.geometry("1300x860")
        self.minsize(900, 620)

        self._data = data_awal if data_awal is not None else self._hitung_posisi_mag_penuh()
        if "garis_rasi_altaz" not in self._data:
            lengkapi_garis_rasi_altaz(self._data, self.tanggal, self.jam_utc, self.lat, self.lon, self.astro)
        self._objek_terpilih = None  # (nama, az, alt, mag_atau_None)

        self._bangun_ui()
        self._gambar()

    # ---------------- perhitungan posisi ----------------

    def _hitung_posisi_mag_penuh(self):
        """Selalu hitung sampai batas magnitudo TERGELAP katalog (5.0),
        supaya slider magnitudo di panel kanan bisa naik/turun tanpa perlu
        hitung ulang alt-az -- cuma re-filter array yang sudah ada.

        Sekalian hitung alt-az segmen garis rasi bintang DI SINI (bukan di
        _gambar()) -- garis rasi disimpan di katalog sebagai RA/Dec, jadi
        HARUS dikonversi ke Alt/Az yang sama seperti bintang sebelum
        diproyeksikan, kalau tidak garis-garisnya tidak akan menyatu dengan
        posisi bintang yang sebenarnya (bug versi sebelumnya)."""
        data = hitung_langit(self.tanggal, self.jam_utc, self.lat, self.lon, self.astro,
                              mode=self.mode, eph=self.eph, ts=self.ts, mag_limit=5.0)
        lengkapi_garis_rasi_altaz(data, self.tanggal, self.jam_utc, self.lat, self.lon, self.astro)
        return data

    def _waktu_berubah(self):
        """Panggil tiap kali tanggal/jam_utc berganti (langkah manual,
        animasi, atau "Sekarang") -- hitung ulang posisi lalu gambar ulang."""
        self._data = self._hitung_posisi_mag_penuh()
        self._perbarui_label_waktu()
        self._gambar()

    # ---------------- UI ----------------

    def _bangun_ui(self):
        # --- bar waktu di atas ---
        bar_waktu = tk.Frame(self, bg=WARNA_PANEL, highlightbackground=WARNA_BORDER,
                              highlightthickness=1)
        bar_waktu.pack(fill="x", padx=8, pady=(8, 4))

        self.btn_mundur = tk.Button(bar_waktu, text="\u25c0\u25c0 10m", command=lambda: self._langkah(-10),
                                     bg=WARNA_PANEL, relief="flat")
        self.btn_mundur.pack(side="left", padx=4, pady=4)

        self.btn_main = tk.Button(bar_waktu, text="\u25b6 Main", command=self._toggle_main,
                                   bg=WARNA_AKSEN, fg="white", relief="flat", width=8)
        self.btn_main.pack(side="left", padx=4, pady=4)

        self.btn_maju = tk.Button(bar_waktu, text="10m \u25b6\u25b6", command=lambda: self._langkah(10),
                                   bg=WARNA_PANEL, relief="flat")
        self.btn_maju.pack(side="left", padx=4, pady=4)

        ttk.Combobox(bar_waktu, textvariable=self.var_kecepatan, state="readonly", width=20,
                     values=[k[0] for k in KECEPATAN_PILIHAN]).pack(side="left", padx=(10, 4))

        tk.Button(bar_waktu, text="Sekarang", command=self._ke_sekarang,
                  bg=WARNA_PANEL, relief="flat").pack(side="left", padx=(10, 4))
        tk.Button(bar_waktu, text="Reset Pandangan", command=self._reset_pandangan,
                  bg=WARNA_PANEL, relief="flat").pack(side="left", padx=4)

        self.label_waktu = tk.Label(bar_waktu, text="", font=FONT_UTAMA_BOLD,
                                     bg=WARNA_PANEL, fg=WARNA_TEKS)
        self.label_waktu.pack(side="right", padx=10)
        self._perbarui_label_waktu()

        # --- badan: kanvas kiri, panel kanan ---
        badan = tk.Frame(self, bg=WARNA_BG)
        badan.pack(fill="both", expand=True, padx=8, pady=4)

        panel_kanan = tk.Frame(badan, bg=WARNA_PANEL, width=240,
                                highlightbackground=WARNA_BORDER, highlightthickness=1)
        panel_kanan.pack(side="right", fill="y", padx=(6, 0))
        panel_kanan.pack_propagate(False)
        self._bangun_panel_kanan(panel_kanan)

        frame_kanvas = tk.Frame(badan, bg=WARNA_BG)
        frame_kanvas.pack(side="left", fill="both", expand=True)

        # Set warna latar figure sama dengan langit gelap (#0B1220)
        self.fig = plt.Figure(figsize=(8, 7), dpi=100, facecolor="#0B1220")
        self.ax = self.fig.add_axes([0, 0, 1, 1], facecolor="#0B1220")
        self.ax.set_aspect("equal")
        self.ax.set_xticks([]); self.ax.set_yticks([])
        for spine in self.ax.spines.values():
            spine.set_visible(False)

        self.canvas = FigureCanvasTkAgg(self.fig, master=frame_kanvas)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_tekan_kanvas)
        self.canvas.mpl_connect("motion_notify_event", self._on_gerak_kanvas)
        self.canvas.mpl_connect("button_release_event", self._on_lepas_kanvas)
        self.canvas.mpl_connect("scroll_event", self._on_scroll_kanvas)

        # Resize figure dinamis mengikuti ukuran frame_kanvas
        self._resize_after = None
        frame_kanvas.bind("<Configure>", self._on_kanvas_resize)

        bar_bawah = tk.Frame(self, bg=WARNA_BG)
        bar_bawah.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(bar_bawah,
                 text="Klik & geser = pan pandangan. Klik saja (tanpa geser) di dekat "
                      "bintang/planet = pilih & lihat info. Scroll = zoom.",
                 font=FONT_KECIL, bg=WARNA_BG, fg=WARNA_TEKS_MUTED).pack(side="left")
        self.label_status = tk.Label(bar_bawah, text="", font=FONT_KECIL,
                                      bg=WARNA_BG, fg=WARNA_TEKS_MUTED)
        self.label_status.pack(side="right")

    def _on_kanvas_resize(self, event):
        """Resize figure matplotlib mengikuti lebar/tinggi frame_kanvas.
        Debounce 150ms supaya tidak terlalu sering redraw saat user
        menyeret tepi window."""
        if self._resize_after is not None:
            self.after_cancel(self._resize_after)
        self._resize_after = self.after(150, lambda w=event.width, h=event.height:
                                        self._terapkan_resize(w, h))

    def _terapkan_resize(self, w, h):
        self._resize_after = None
        if w < 100 or h < 100:
            return
        dpi = self.fig.get_dpi()
        self.fig.set_size_inches(w / dpi, h / dpi)
        self._gambar()

    def _bangun_panel_kanan(self, panel):
        pad = {"padx": 10, "pady": 6}

        ttk.Label(panel, text="Cari Objek", font=FONT_UTAMA_BOLD,
                  background=WARNA_PANEL).pack(anchor="w", **pad)
        frame_cari = tk.Frame(panel, bg=WARNA_PANEL)
        frame_cari.pack(fill="x", padx=10)
        self.entry_cari = ttk.Entry(frame_cari)
        self.entry_cari.pack(side="left", fill="x", expand=True)
        self.entry_cari.bind("<Return>", lambda e: self._cari_objek())
        ttk.Button(frame_cari, text="Cari", command=self._cari_objek, width=6).pack(side="left", padx=(4, 0))

        ttk.Separator(panel).pack(fill="x", padx=10, pady=10)

        ttk.Label(panel, text="Tampilan", font=FONT_UTAMA_BOLD,
                  background=WARNA_PANEL).pack(anchor="w", **pad)
        ttk.Checkbutton(panel, text="Garis rasi bintang", variable=self.tampil_garis_rasi,
                         command=self._gambar).pack(anchor="w", padx=10)
        ttk.Checkbutton(panel, text="Nama rasi bintang", variable=self.tampil_nama_rasi,
                         command=self._gambar).pack(anchor="w", padx=10)
        ttk.Checkbutton(panel, text="Nama bintang terang", variable=self.tampil_nama_bintang,
                         command=self._gambar).pack(anchor="w", padx=10)
        ttk.Checkbutton(panel, text="Grid horizon & arah mata angin", variable=self.tampil_grid,
                         command=self._gambar).pack(anchor="w", padx=10)

        ttk.Label(panel, text="Magnitudo bintang terlemah:", font=FONT_UTAMA,
                  background=WARNA_PANEL).pack(anchor="w", padx=10, pady=(10, 0))
        self.slider_mag = tk.Scale(panel, from_=1.0, to=5.0, resolution=0.1, orient="horizontal",
                                    bg=WARNA_PANEL, highlightthickness=0, command=self._on_ubah_mag)
        self.slider_mag.set(self.mag_limit)
        self.slider_mag.pack(fill="x", padx=10)

        ttk.Label(panel, text="Zoom (medan pandang \u00b0):", font=FONT_UTAMA,
                  background=WARNA_PANEL).pack(anchor="w", padx=10, pady=(10, 0))
        self.slider_zoom = tk.Scale(panel, from_=5, to=170, resolution=1, orient="horizontal",
                                     bg=WARNA_PANEL, highlightthickness=0, command=self._on_ubah_zoom)
        self.slider_zoom.set(self.fov)
        self.slider_zoom.pack(fill="x", padx=10)

        ttk.Separator(panel).pack(fill="x", padx=10, pady=10)
        ttk.Label(panel, text="Info Objek", font=FONT_UTAMA_BOLD,
                  background=WARNA_PANEL).pack(anchor="w", **pad)
        self.label_info_nama = tk.Label(panel, text="Klik / cari objek untuk lihat detail.",
                                         font=FONT_UTAMA_BOLD, bg=WARNA_PANEL, fg=WARNA_TEKS,
                                         justify="left", wraplength=230, anchor="nw")
        self.label_info_nama.pack(fill="x", padx=10, pady=(0, 2))
        self.label_info_detail = tk.Label(panel, text="",
                                           font=FONT_KECIL, bg=WARNA_PANEL, fg=WARNA_TEKS_MUTED,
                                           justify="left", wraplength=230, anchor="nw")
        self.label_info_detail.pack(fill="x", padx=10, pady=(0, 10))

    # ---------------- kontrol waktu ----------------

    def _perbarui_label_waktu(self):
        self.label_waktu.config(
            text=f"{self.tanggal:%d %b %Y}  {self.jam_utc:05.2f} UTC  "
                 f"({self.lat:.2f}, {self.lon:.2f})")

    def _langkah(self, menit):
        self._geser_waktu_menit(menit)
        self._waktu_berubah()

    def _geser_waktu_menit(self, menit):
        total_jam = self.jam_utc + menit / 60.0
        hari_geser = 0
        while total_jam >= 24.0:
            total_jam -= 24.0
            hari_geser += 1
        while total_jam < 0.0:
            total_jam += 24.0
            hari_geser -= 1
        if hari_geser:
            self.tanggal = self.tanggal + timedelta(days=hari_geser)
        self.jam_utc = total_jam

    def _ke_sekarang(self):
        now = datetime.utcnow()
        self.tanggal = now.date() if hasattr(now, "date") else now
        self.jam_utc = now.hour + now.minute / 60.0 + now.second / 3600.0
        self._waktu_berubah()

    def _toggle_main(self):
        self.sedang_main = not self.sedang_main
        self.btn_main.config(text="\u23f8 Jeda" if self.sedang_main else "\u25b6 Main")
        if self.sedang_main:
            self._tick_animasi()

    def _tick_animasi(self):
        if not self.sedang_main or not self.winfo_exists():
            return
        label_kecepatan = self.var_kecepatan.get()
        kali = dict(KECEPATAN_PILIHAN)[label_kecepatan]
        detik_simulasi = kali * (_INTERVAL_TICK_MS / 1000.0)
        self._geser_waktu_menit(detik_simulasi / 60.0)
        self._waktu_berubah()
        self.after(_INTERVAL_TICK_MS, self._tick_animasi)

    # ---------------- pan & zoom ----------------

    def _reset_pandangan(self):
        self.az_center, self.alt_center, self.fov = 180.0, 45.0, 90.0
        self.slider_zoom.set(self.fov)
        self._gambar()

    def _on_ubah_zoom(self, _val):
        self.fov = float(self.slider_zoom.get())
        self._gambar()

    def _on_ubah_mag(self, _val):
        self.mag_limit = float(self.slider_mag.get())
        self._gambar()

    def _on_scroll_kanvas(self, event):
        faktor = 0.85 if event.button == "up" else (1 / 0.85)
        self.fov = float(np.clip(self.fov * faktor, 5, 170))
        self.slider_zoom.set(self.fov)
        self._gambar()

    def _on_tekan_kanvas(self, event):
        if event.xdata is None or event.ydata is None or event.button != 1:
            return
        self._drag_terakhir_xy = (event.xdata, event.ydata)
        self._drag_bergerak = False

    def _on_gerak_kanvas(self, event):
        """Selama tombol kiri ditekan & mouse digeser: pusat pandang ikut
        geser real-time (drag-pan halus), bukan lompat sekali klik.

        Dihitung pakai aproksimasi bidang-singgung lokal di sekitar pusat
        pandang saat ini (x \u2248 (az-az0)*cos(alt0), y \u2248 (alt-alt0) untuk
        pergeseran kecil, radian) -- akurat untuk tiap langkah kecil antar
        event mouse (yang memang kecil-kecil & sering selama drag asli),
        dan JAUH lebih sederhana/stabil daripada rumus proyeksi-balik penuh
        untuk drag menerus."""
        if self._drag_terakhir_xy is None or event.xdata is None or event.ydata is None:
            return
        x0, y0 = self._drag_terakhir_xy
        dx, dy = event.xdata - x0, event.ydata - y0
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            self._drag_bergerak = True
        cos_alt0 = max(np.cos(np.radians(self.alt_center)), 0.02)  # cegah pembagian nyaris nol dekat zenith
        self.az_center = (self.az_center - np.degrees(dx) / cos_alt0) % 360
        self.alt_center = float(np.clip(self.alt_center - np.degrees(dy), -89, 89))
        self._drag_terakhir_xy = (event.xdata, event.ydata)

        sekarang = time.monotonic()
        if sekarang - self._drag_waktu_gambar_terakhir > 0.04:  # ~25 fps, cukup halus tanpa berat
            self._drag_waktu_gambar_terakhir = sekarang
            self._gambar()

    def _on_lepas_kanvas(self, event):
        if self._drag_terakhir_xy is None:
            return
        bergerak = self._drag_bergerak
        self._drag_terakhir_xy = None
        self._drag_bergerak = False
        if bergerak:
            self._gambar()  # gambar ulang final (kalau throttle di atas sempat melewatkan frame terakhir)
        elif event.xdata is not None and event.ydata is not None:
            # tidak digeser sama sekali -> perlakukan sebagai klik biasa:
            # pilih objek terdekat, atau pindah pusat pandang persis ke titik itu.
            terdekat = self._cari_objek_terdekat_dari_proyeksi(event.xdata, event.ydata)
            if terdekat is not None:
                self._pilih_objek(terdekat)
                self.az_center, self.alt_center = terdekat["az"], terdekat["alt"]
            else:
                az, alt = _proyeksi_stereografik_balik(event.xdata, event.ydata,
                                                         self.az_center, self.alt_center)
                self.az_center, self.alt_center = float(az), float(np.clip(alt, -89, 89))
                self._objek_terpilih = None
                self.label_info_nama.config(text="(tidak ada objek katalog di titik ini)")
                self.label_info_detail.config(text=(
                    f"Titik langit -- Azimut/Altitud: {self.az_center:.2f}\u00b0 / "
                    f"{self.alt_center:.2f}\u00b0"))
            self._gambar()

    def _cari_objek_terdekat_dari_proyeksi(self, x, y, ambang=0.05):
        kandidat = self._semua_kandidat_objek()
        if not kandidat:
            return None
        terbaik, jarak_min = None, ambang
        for info in kandidat:
            xo, yo, _c = _proyeksi_stereografik(np.array([info["az"]]), np.array([info["alt"]]),
                                                 self.az_center, self.alt_center)
            jarak = float(np.hypot(xo[0] - x, yo[0] - y))
            if jarak < jarak_min:
                jarak_min, terbaik = jarak, info
        return terbaik

    def _semua_kandidat_objek(self, kunci_cari=None):
        """Kumpulkan semua objek (bintang + Matahari/Bulan/planet) jadi satu
        list dict info seragam -- dipakai bareng oleh klik-pilih & pencarian,
        supaya kartu info (_pilih_objek) selalu dapat data lengkap yang sama
        (RA/Dec, rasi, jarak, dst), bukan cuma nama/az/alt/mag seperti versi
        sebelumnya."""
        kandidat = []
        if self._data["bintang"] is not None:
            b = self._data["bintang"]
            m = b["mag"] <= self.mag_limit
            for i in np.where(m)[0]:
                nama = b["nama"][i]
                if kunci_cari and not (nama and kunci_cari in nama.lower()):
                    continue
                nama_tampil, rasi_lengkap = _nama_tampilan_bintang(
                    nama, b["bayer"][i], b["flam"][i], b["konstelasi"][i])
                kandidat.append({
                    "nama": nama_tampil, "az": float(b["az"][i]), "alt": float(b["alt"][i]),
                    "ra": float(b["ra_deg"][i]), "dec": float(b["dec_deg"][i]),
                    "mag": float(b["mag"][i]), "jenis": "Bintang",
                    "konstelasi": rasi_lengkap, "dist_pc": float(b["dist_pc"][i]),
                    "hip": int(b["hip"][i]),
                })
        for nama, az, alt, _warna, jenis, ra, dec in self._data["objek"]:
            if kunci_cari and kunci_cari not in nama.lower():
                continue
            kandidat.append({
                "nama": nama, "az": float(az), "alt": float(alt), "ra": float(ra), "dec": float(dec),
                "mag": None, "jenis": jenis, "konstelasi": None, "dist_pc": -1, "hip": -1,
            })
        return kandidat

    def _pilih_objek(self, info):
        """Tampilkan kartu info ala Stellarium/Cartes du Ciel: nama+sebutan,
        jenis & rasi, magnitudo, jarak (bintang), RA/Dec, Alt/Az."""
        self._objek_terpilih = info
        self.label_info_nama.config(text=info["nama"])

        baris = [info["jenis"] + (f" \u2014 rasi {info['konstelasi']}" if info.get("konstelasi") else "")]
        if info["mag"] is not None:
            baris.append(f"Magnitudo: {info['mag']:.2f}")
        if info.get("dist_pc", -1) and info["dist_pc"] > 0:
            if info["dist_pc"] >= 10000:
                baris.append("Jarak: tidak diketahui (paralaks terlalu kecil)")
            else:
                tahun_cahaya = info["dist_pc"] * 3.26156
                baris.append(f"Jarak: {tahun_cahaya:,.1f} tahun cahaya ({info['dist_pc']:.1f} pc)")
        label_epoch = "J2000" if info["jenis"] == "Bintang" else "saat ini"
        baris.append(f"RA/Dec ({label_epoch}): {_format_ra(info['ra'])}  {_format_dec(info['dec'])}")
        baris.append(f"Azimut/Altitud: {info['az']:.2f}\u00b0 / {info['alt']:.2f}\u00b0")
        if info.get("hip", -1) and info["hip"] > 0:
            baris.append(f"Katalog Hipparcos: HIP {info['hip']}")
        self.label_info_detail.config(text="\n".join(baris))

    def _cari_objek(self):
        kunci = self.entry_cari.get().strip().lower()
        if not kunci:
            return
        kandidat = self._semua_kandidat_objek(kunci_cari=kunci)
        if not kandidat:
            self.label_status.config(text=f'"{self.entry_cari.get()}" tidak ditemukan (mungkin di bawah ufuk).')
            return
        # kalau ada beberapa hasil, pilih yang paling terang (planet/Matahari/Bulan diprioritaskan di atas bintang)
        kandidat.sort(key=lambda o: (o["mag"] is not None, o["mag"] if o["mag"] is not None else 0))
        info = kandidat[0]
        self.az_center, self.alt_center = info["az"], info["alt"]
        self._pilih_objek(info)
        self.label_status.config(text=f"Ditemukan: {info['nama']}")
        self._gambar()

    # ---------------- gambar ----------------

    def _gambar(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor("#0B1220")
        ax.set_position([0, 0, 1, 1])
        ax.set_facecolor("#0B1220")
        self.fig.set_facecolor("#0B1220")
        ax.set_aspect("equal")
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

        # Dapatkan aspect ratio aktual dari kanvas
        bbox = ax.get_window_extent()
        w_px, h_px = bbox.width, bbox.height
        aspect_ratio = (w_px / h_px) if (h_px > 0 and w_px > 0) else 1.2

        batas_y = 2.0 * np.tan(np.radians(self.fov) / 2.0)
        batas_x = batas_y * aspect_ratio

        ax.set_xlim(-batas_x, batas_x)
        ax.set_ylim(-batas_y, batas_y)

        # Sudut angular maksimum dari pusat ke pojok kanvas
        r_max = np.hypot(batas_x, batas_y)
        c_max_deg = np.degrees(2.0 * np.arctan(r_max / 2.0)) * 1.05

        # --- tanah (bawah horizon) diwarnai, biar bisa dibedakan dari langit ---
        # Sampling raster (bukan cuma polyline horizon) supaya tetap benar di
        # segala arah pandang -- termasuk saat menghadap dekat zenith/nadir,
        # di mana bentuk area "tanah" pada kanvas bukan lagi kurva sederhana.
        n_raster = 140
        xs = np.linspace(-batas_x, batas_x, n_raster)
        ys = np.linspace(-batas_y, batas_y, n_raster)
        Xg, Yg = np.meshgrid(xs, ys)
        _, alt_g = _proyeksi_stereografik_balik(Xg, Yg, self.az_center, self.alt_center)
        WARNA_TANAH_RGB = (0x35 / 255.0, 0x2A / 255.0, 0x18 / 255.0)  # cokelat tanah gelap
        tanah_rgba = np.zeros((n_raster, n_raster, 4))
        tanah_rgba[..., 0] = WARNA_TANAH_RGB[0]
        tanah_rgba[..., 1] = WARNA_TANAH_RGB[1]
        tanah_rgba[..., 2] = WARNA_TANAH_RGB[2]
        tanah_rgba[..., 3] = np.where(alt_g < 0, 0.9, 0.0)
        ax.imshow(tanah_rgba, extent=(-batas_x, batas_x, -batas_y, batas_y), origin="lower",
                  interpolation="bilinear", zorder=0.5)

        # --- grid horizon (lingkaran alt=0, 30, 60) + label mata angin ---
        if self.tampil_grid.get():
            for alt_lingkar, gaya in [(0, "-"), (30, ":"), (60, ":")]:
                az_s = np.linspace(0, 360, 361)
                x, y, c = _proyeksi_stereografik(az_s, np.full_like(az_s, alt_lingkar),
                                                  self.az_center, self.alt_center)
                terlihat = c < 179
                if terlihat.any():
                    warna = "#4A5A78" if alt_lingkar == 0 else "#26314A"
                    ax.plot(x[terlihat], y[terlihat], gaya, color=warna, linewidth=1.0, zorder=1)
            for az_label, teks in [(0, "U"), (45, "TL"), (90, "T"), (135, "TG"),
                                    (180, "S"), (225, "BD"), (270, "B"), (315, "BL")]:
                x, y, c = _proyeksi_stereografik(np.array([az_label]), np.array([0.0]),
                                                  self.az_center, self.alt_center)
                if c[0] < 179 and abs(x[0]) < batas_x and abs(y[0]) < batas_y:
                    ax.annotate(teks, (x[0], y[0]), color=WARNA_TEKS_MUTED, fontsize=9,
                                fontweight="bold", ha="center", va="center", zorder=2)

        # --- garis & nama rasi bintang ---
        garis_altaz = self._data.get("garis_rasi_altaz")
        if garis_altaz is not None and self.tampil_garis_rasi.get():
            x1, y1, c1 = _proyeksi_stereografik(garis_altaz[:, 0], garis_altaz[:, 1],
                                                 self.az_center, self.alt_center)
            x2, y2, c2 = _proyeksi_stereografik(garis_altaz[:, 2], garis_altaz[:, 3],
                                                 self.az_center, self.alt_center)
            terlihat = (c1 < c_max_deg) & (c2 < c_max_deg)
            for i in np.where(terlihat)[0]:
                ax.plot([x1[i], x2[i]], [y1[i], y2[i]], color="#3D4A63", linewidth=0.8, zorder=1)

        # --- bintang ---
        if self._data["bintang"] is not None:
            b = self._data["bintang"]
            m = b["mag"] <= self.mag_limit
            x, y, c = _proyeksi_stereografik(b["az"][m], b["alt"][m], self.az_center, self.alt_center)
            terlihat = c < c_max_deg
            if terlihat.any():
                mag_t = b["mag"][m][terlihat]
                ukuran = np.clip((self.mag_limit - mag_t + 1.0), 0.5, None) ** 2 * 3.0
                ax.scatter(x[terlihat], y[terlihat], s=ukuran, c="white",
                           edgecolors="none", zorder=3)
                if self.tampil_nama_bintang.get():
                    nama_t = b["nama"][m][terlihat]
                    mag_terang = mag_t < 2.0  # cuma label bintang yang benar-benar terang, biar tidak penuh
                    for xi, yi, nm in zip(x[terlihat][mag_terang], y[terlihat][mag_terang],
                                           nama_t[mag_terang]):
                        if nm:
                            ax.annotate(nm, (xi, yi), color="#C7CBD1", fontsize=7,
                                        xytext=(4, 4), textcoords="offset points", zorder=4)

        # --- Matahari, Bulan, planet ---
        for nama, az, alt, warna, _jenis, _ra, _dec in self._data["objek"]:
            x, y, c = _proyeksi_stereografik(np.array([az]), np.array([alt]),
                                              self.az_center, self.alt_center)
            if c[0] < c_max_deg:
                ax.scatter(x, y, s=170, c=warna, edgecolors="#1F2937", linewidths=0.9, zorder=5)
                ax.annotate(nama, (x[0], y[0]), color=warna, fontsize=9, fontweight="bold",
                            xytext=(6, 6), textcoords="offset points", zorder=6)

        # --- nama rasi bintang (dekat centroid garis-garisnya) ---
        garis = _muat_garis_rasi()  # sudah di-cache oleh _muat_garis_rasi(), murah dipanggil lagi
        if garis is not None and self.tampil_nama_rasi.get():
            self._gambar_label_rasi(ax, garis)

        # --- lingkaran penanda objek terpilih (ala reticle Stellarium) ---
        if self._objek_terpilih is not None:
            self._gambar_penanda_seleksi(ax)

        self.canvas.draw_idle()
        self.label_status.config(
            text=f"Pusat: Az {self.az_center:.1f}\u00b0 Alt {self.alt_center:.1f}\u00b0  |  "
                 f"FOV {self.fov:.0f}\u00b0  |  Mag \u2264 {self.mag_limit:.1f}")

    def _gambar_penanda_seleksi(self, ax):
        """Gambar lingkaran penanda di sekitar objek yang sedang dipilih --
        posisinya SELALU dihitung ulang di sini (bukan pakai az/alt lama
        yang tersimpan saat diklik), supaya kalau waktu berjalan (Bulan/
        planet bergerak), penandanya ikut mengikuti objeknya, bukan diam di
        posisi lama."""
        info = self._objek_terpilih
        if info["jenis"] == "Bintang":
            jd_ut, T = _waktu_ke_jd_T(self.tanggal, self.jam_utc, self.astro)
            az_h, alt_h = _radec_ke_altaz(np.array([info["ra"]]), np.array([info["dec"]]),
                                           jd_ut, T, self.lat, self.lon, self.astro)
            az_h, alt_h = az_h[0], alt_h[0]
        else:
            az_h = alt_h = None
            for nama, az, alt, _warna, _jenis, _ra, _dec in self._data["objek"]:
                if nama == info["nama"]:
                    az_h, alt_h = az, alt
                    break
        if az_h is None or alt_h <= 0:
            return  # objek terpilih sudah tenggelam di bawah ufuk, tidak digambar
        x, y, c = _proyeksi_stereografik(np.array([az_h]), np.array([alt_h]),
                                          self.az_center, self.alt_center)
        if c[0] < self.fov * 0.95:
            ax.scatter(x, y, s=420, facecolors="none", edgecolors="#FFD54A",
                       linewidths=1.6, zorder=7)
            ax.scatter(x, y, s=900, facecolors="none", edgecolors="#FFD54A",
                       linewidths=0.8, alpha=0.5, zorder=7)

    def _gambar_label_rasi(self, ax, garis):
        # centroid kasar tiap rasi dari titik-titik garisnya (cukup buat label, tidak perlu presisi)
        if not hasattr(self, "_centroid_rasi_cache"):
            konstelasi = {}
            with open(ASET_RASI_GARIS, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    k = row["konstelasi"]
                    konstelasi.setdefault(k, []).append(
                        (float(row["ra1_deg"]), float(row["dec1_deg"])))
                    konstelasi.setdefault(k, []).append(
                        (float(row["ra2_deg"]), float(row["dec2_deg"])))
            self._centroid_rasi_cache = {
                k: (float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts])))
                for k, pts in konstelasi.items()
            }
        jd_ut, T = _waktu_ke_jd_T(self.tanggal, self.jam_utc, self.astro)
        for kode, (ra_c, dec_c) in self._centroid_rasi_cache.items():
            az_c, alt_c = _radec_ke_altaz(np.array([ra_c]), np.array([dec_c]), jd_ut, T,
                                           self.lat, self.lon, self.astro)
            if alt_c[0] <= 0:
                continue
            x, y, c = _proyeksi_stereografik(az_c, alt_c, self.az_center, self.alt_center)
            if c[0] < self.fov * 0.85:
                ax.annotate(kode, (x[0], y[0]), color="#5B6B8C", fontsize=8, style="italic",
                            ha="center", va="center", zorder=2)


def buka_planetarium(parent, tanggal, jam_utc, lat, lon, astro, mode="jpl",
                      eph=None, ts=None, mag_limit_awal=4.0, data_awal=None):
    """Titik masuk yang dipanggil hisabwin.py untuk membuka Mode Planetarium."""
    return JendelaPlanetarium(parent, tanggal, jam_utc, lat, lon, astro, mode=mode,
                               eph=eph, ts=ts, mag_limit_awal=mag_limit_awal,
                               data_awal=data_awal)
