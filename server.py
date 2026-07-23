"""
server.py - Backend web/webview untuk HisabWin
================================================

Membungkus fitur utama hisabwin.py (cari ijtimak + peta visibilitas hilal
kriteria MABIMS & Muhammadiyah) sebagai web app lokal, supaya bisa dibuka
lewat browser ATAU jendela webview (pywebview) alih-alih GUI Tkinter asli.

TIDAK mengubah logika astronomi sama sekali - hanya memanggil fungsi-fungsi
yang sudah ada di hisabwin.py (cari_ijtimak_tahun, hitung_grid, evaluasi_pkg)
lalu mengonversi grid hasilnya menjadi GeoJSON (garis kontur & zona terisi)
supaya bisa digambar interaktif di peta Leaflet pada index.html - TIDAK ada
render PNG/matplotlib figure sama sekali yang dikirim ke browser.

Cara pakai:
    pip install -r requirements.txt
    python server.py
    -> otomatis buka jendela webview (kalau pywebview terpasang),
       atau buka manual: http://127.0.0.1:5000

Catatan:
- hisabwin.py, starmap.py, de421.bsp, dan mainland_amerika_mask.npz HARUS
  ada di folder yang sama dengan server.py ini (file-file asli dari repo).
- cartopy/matplotlib TETAP dipakai di balik layar hanya untuk MENGHITUNG
  posisi garis kontur (algoritma marching-squares bawaan matplotlib) -
  tidak ada satu pun figure yang dirender ke gambar untuk dikirim ke klien.
"""

import os
import sys
import threading
import traceback
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)  # supaya hisabwin.py menemukan de421.bsp dkk di folder ini

# ---------------------------------------------------------------------------
# Import hisabwin.py sebagai modul, TANPA menjalankan GUI Tkinter-nya.
# hisabwin.py memaksa backend matplotlib ke "TkAgg" di baris tengah file
# (untuk GUI aslinya) - kita pura-purakan matplotlib.use() jadi no-op selama
# proses import supaya backend "Agg" (headless, aman untuk server) yang kita
# pasang di awal TIDAK ditimpa. Backend Agg di sini HANYA dipakai untuk
# menghitung geometri kontur, bukan untuk menggambar gambar.
# ---------------------------------------------------------------------------
import matplotlib
matplotlib.use("Agg")
_matplotlib_use_asli = matplotlib.use
matplotlib.use = lambda *a, **k: None

# ---------------------------------------------------------------------------
# hisabwin.py melakukan "import tkinter as tk" & "from tkinter import
# filedialog, messagebox, ttk" di baris paling atas, lalu mewarisi tk.Tk /
# tk.Toplevel / ttk.Notebook di 4 class GUI-nya (DialogKernelJPL,
# DialogCariObjekHorizons, ClosableNotebook, HisabWinApp). Di komputer
# desktop tkinter selalu tersedia, tapi lingkungan serverless (mis. Vercel)
# biasanya TIDAK punya library sistem Tcl/Tk terpasang -> "import tkinter"
# bisa gagal total dan bikin seluruh server.py ikut gagal, walau kita tidak
# pernah memakai GUI-nya sama sekali di jalur web ini.
#
# Sudah diverifikasi dengan AST parser: keempat class tk.* itu TIDAK punya
# satu pun statement yang benar-benar jalan saat class-nya didefinisikan
# (cuma docstring & satu atribut bool biasa) - semua kode tkinter asli ada
# di DALAM method, yang tidak pernah dipanggil oleh server.py. Jadi kalau
# tkinter asli tidak ada, aman untuk mengganti "tk.Tk"/"tk.Toplevel"/
# "ttk.Notebook" dengan class kosong biasa sekadar supaya "class Foo(tk.Tk):"
# tidak error saat import - tanpa mengubah satu baris pun logika astronomi.
try:
    import tkinter as _tk_asli_cek  # noqa: F401
except ImportError:
    import types

    class _DummyTkWidget:
        """Class dasar kosong, dipakai sebagai pengganti tk.Tk/tk.Toplevel
        HANYA kalau tkinter asli tidak ada di server. Method apa pun yang
        dipanggil di atasnya (tidak pernah terjadi di jalur web) akan
        diam-diam tidak melakukan apa-apa, bukan error, biar aman."""

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, nama):
            return lambda *a, **k: None

    tk_dummy = types.ModuleType("tkinter")
    tk_dummy.Tk = _DummyTkWidget
    tk_dummy.Toplevel = _DummyTkWidget

    ttk_dummy = types.ModuleType("tkinter.ttk")
    ttk_dummy.Notebook = _DummyTkWidget
    ttk_dummy.Style = _DummyTkWidget
    tk_dummy.ttk = ttk_dummy

    filedialog_dummy = types.ModuleType("tkinter.filedialog")
    messagebox_dummy = types.ModuleType("tkinter.messagebox")
    tk_dummy.filedialog = filedialog_dummy
    tk_dummy.messagebox = messagebox_dummy

    sys.modules["tkinter"] = tk_dummy
    sys.modules["tkinter.ttk"] = ttk_dummy
    sys.modules["tkinter.filedialog"] = filedialog_dummy
    sys.modules["tkinter.messagebox"] = messagebox_dummy

# hisabwin.py otomatis memanggil _tampilkan_splash_awal() di level modul
# (baris 151-152 di hisabwin.py) - ini untuk GUI Tkinter aslinya, supaya
# splash muncul selagi import berat (cartopy/matplotlib/skyfield) jalan.
# Splash itu HANYA ditutup lagi oleh _tutup_splash_awal() di dalam
# HisabWinApp.__init__ - yang tidak pernah dipanggil di server.py ini
# (server.py cuma memakai fungsi kalkulasinya, bukan GUI-nya sama sekali).
# Akibatnya jendela splash muncul lalu nyangkut selamanya tiap kali
# server.py dijalankan.
#
# CATATAN: jangan coba mem-patch tkinter.Tk itu sendiri - hisabwin.py juga
# punya "class HisabWinApp(tk.Tk):" di level modul, yang butuh tk.Tk tetap
# berupa class Tkinter asli (bukan fungsi/lambda) supaya class statement-nya
# tidak error saat import.
#
# Solusi yang aman: _tampilkan_splash_awal() mengecek dulu apakah file
# "splash.png" ada (os.path.isfile) SEBELUM membuat window tk.Tk() apapun,
# dan langsung return None kalau filenya tidak ditemukan - tanpa membuat
# window sama sekali. Jadi kita cukup membuat os.path.isfile() sementara
# menganggap "splash.png" tidak ada, khusus selama proses import hisabwin
# di bawah ini, supaya splash gagal dibuat dengan aman (fungsi itu memang
# didesain "aman gagal diam-diam") dan tidak pernah tampil di layar sama
# sekali pada mode webapp - tanpa mengubah perilaku os.path.isfile untuk
# file lain.
_isfile_asli = os.path.isfile


def _isfile_tanpa_splash(path):
    if os.path.basename(path) == "splash.png":
        return False
    return _isfile_asli(path)


os.path.isfile = _isfile_tanpa_splash
try:
    import hisabwin as hw
finally:
    matplotlib.use = _matplotlib_use_asli
    os.path.isfile = _isfile_asli

import numpy as np
import matplotlib.pyplot as plt
from shapely.geometry import Polygon as ShapelyPolygon, Point as ShapelyPoint
from skyfield.api import load
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

# _state_lock & _ts/_eph SENGAJA tetap disimpan di memori proses (bukan di
# database eksternal): kalau proses/instance-nya masih "hangat" (warm),
# ephemeris tidak perlu dimuat ulang tiap request -> lebih cepat. Tapi
# TIDAK ADA logika yang WAJIB mengandalkan ini tetap ada antar-request -
# kalau instance-nya baru/dingin (umum terjadi di lingkungan serverless
# seperti Vercel, tiap request bisa kena instance berbeda), _pastikan_
# ephemeris() otomatis memuat ulang dari awal - jadi tetap benar, cuma
# sedikit lebih lambat di request pertama tiap instance baru.
_state_lock = threading.Lock()
_ts = None
_eph = None


def _pastikan_ephemeris():
    """Muat skyfield timescale + kernel JPL de421.bsp bawaan (sekali saja)."""
    global _ts, _eph
    if _ts is not None and _eph is not None:
        return
    kernel_id = hw.muat_kernel_aktif()
    path_bsp = hw.path_utama_kernel(kernel_id)
    if not os.path.isfile(path_bsp):
        path_bsp = os.path.join(BASE_DIR, "de421.bsp")
    _ts = load.timescale()
    _eph = load(path_bsp)


def _error_response(pesan, kode=400):
    return jsonify({"ok": False, "error": pesan}), kode


# ---------------------------------------------------------------------------
# Konversi grid numpy -> GeoJSON, memakai matplotlib HANYA sebagai mesin
# hitung kontur (marching squares), tanpa pernah merender ke gambar.
# ---------------------------------------------------------------------------
def _dummy_axes():
    """Figure/Axes sekali-pakai, murni untuk menghitung path kontur."""
    fig = plt.figure()
    ax = fig.add_subplot(111)
    return fig, ax


def _split_antimeridian_seg(seg):
    """Membagi segmen garis [[lon, lat], ...] jika ada loncat garis di antimeridian (|lon2 - lon1| > 180)."""
    subsegs = []
    curr = [seg[0]]
    for i in range(1, len(seg)):
        if abs(seg[i][0] - seg[i-1][0]) > 180:
            if len(curr) >= 2:
                subsegs.append(curr)
            curr = [seg[i]]
        else:
            curr.append(seg[i])
    if len(curr) >= 2:
        subsegs.append(curr)
    return subsegs


def _garis_geojson(ax, lon_mesh, lat_mesh, grid, levels):
    """Kontur garis (mis. batas tinggi hilal = 3 derajat) -> MultiLineString."""
    if np.isscalar(levels):
        levels = [levels]
    grid = np.asarray(grid, dtype=float)
    if not np.any(~np.isnan(grid)):
        return None
    cs = ax.contour(lon_mesh, lat_mesh, grid, levels=levels)
    coords = []
    for seg_list in cs.allsegs:
        for seg in seg_list:
            if len(seg) >= 2:
                rounded = np.round(seg, 3).tolist()
                for sub in _split_antimeridian_seg(rounded):
                    coords.append(sub)
    return {"type": "MultiLineString", "coordinates": coords} if coords else None


def _kontur_berlabel_geojson(ax, lon_mesh, lat_mesh, grid, levels):
    """Sama seperti _garis_geojson, tapi utk BANYAK level kontur sekaligus
    (mis. tiap 0.2 derajat, gaya peta BMKG) - satu Feature LineString per
    segmen garis, dengan properti 'level' supaya nilainya bisa dilabeli di
    peta Leaflet sisi client (mirip ax.clabel di gambar matplotlib asli)."""
    grid = np.asarray(grid, dtype=float)
    if not np.any(~np.isnan(grid)):
        return None
    cs = ax.contour(lon_mesh, lat_mesh, grid, levels=levels)
    features = []
    for lvl, seg_list in zip(cs.levels, cs.allsegs):
        for seg in seg_list:
            if len(seg) >= 2:
                rounded = np.round(seg, 3).tolist()
                for sub in _split_antimeridian_seg(rounded):
                    features.append({
                        "type": "Feature",
                        "properties": {"level": round(float(lvl), 2)},
                        "geometry": {"type": "LineString", "coordinates": sub},
                    })
    return {"type": "FeatureCollection", "features": features} if features else None


def _levels_02(grid, lo_default, hi_default):
    """Level kontur tiap 0.2 derajat, dibulatkan ke rentang data (persis
    logika yang sama seperti buat_figure_indonesia_tinggi_hilal/elongasi
    di hisabwin.py) - dipakai berdua supaya garisnya identik dgn versi GUI."""
    grid = np.asarray(grid, dtype=float)
    valid = ~np.isnan(grid)
    if np.any(valid):
        lo = np.floor(np.nanmin(grid) / 0.2) * 0.2
        hi = np.ceil(np.nanmax(grid) / 0.2) * 0.2
    else:
        lo, hi = lo_default, hi_default
    return np.arange(lo, hi + 0.001, 0.2)


def _zona_geojson(ax, lon_mesh, lat_mesh, mask_bool):
    """Zona biner (mis. memenuhi kriteria MABIMS) -> MultiPolygon (dengan lubang bila ada)."""
    mask_bool = np.asarray(mask_bool)
    if mask_bool.dtype != bool:
        mask_bool = np.nan_to_num(mask_bool, nan=0).astype(bool)
    if not np.any(mask_bool):
        return None

    cs = ax.contourf(lon_mesh, lat_mesh, mask_bool.astype(int), levels=[0.5, 1.5])
    segs = cs.allsegs[0] if cs.allsegs else []

    exteriors, holes = [], []
    for loop in segs:
        if len(loop) < 4:
            continue
        xs, ys = loop[:, 0], loop[:, 1]
        # Tanda luas (shoelace) menentukan arah putaran: matplotlib selalu
        # mengembalikan loop luar (exterior) berlawanan-jarum-jam (area > 0)
        # dan lubang (hole) searah-jarum-jam (area < 0) - jadi ini cukup
        # untuk memisahkan keduanya tanpa perlu library GIS tambahan.
        area2 = np.sum(xs * np.roll(ys, -1) - np.roll(xs, -1) * ys)
        pts = np.round(loop, 3).tolist()
        (exteriors if area2 > 0 else holes).append(pts)

    if not exteriors:
        return None

    polys = [[ext] for ext in exteriors]
    shapes = [ShapelyPolygon(e) for e in exteriors]
    for hole in holes:
        titik = ShapelyPoint(hole[0])
        for i, shp in enumerate(shapes):
            valid_shp = shp if shp.is_valid else shp.buffer(0)
            if valid_shp.contains(titik):
                polys[i].append(hole)
                break

    return {"type": "MultiPolygon", "coordinates": polys}


def _lingkaran_geodesic_geojson(lat0, lon0, radius_derajat=89.9, n=120):
    """Lingkaran geodesic (radius SUDUT dari titik pusat, di permukaan bola)
    -> GeoJSON Polygon. Replikasi manual dari cartopy.geodesic.Geodesic()
    .circle() yang dipakai versi GUI (buat_figure_visibilitas_gerhana_bulan)
    - dipakai utk lingkaran horizon 90 derajat (radius 89.9 spy sedikit di
    dalam, sama seperti alasan RADIUS_HORIZON_M di hisabwin.py: menghindari
    titik genap 90 derajat yang bisa bikin geometri degenerate). TIDAK
    butuh cartopy sama sekali (lihat catatan cartopy opsional sebelumnya).

    np.unwrap() dipakai (BUKAN _split_antimeridian_seg seperti garis kontur
    biasa) supaya bujurnya tetap kontinu melewati antimeridian tanpa
    loncatan - penting krn ini POLIGON tertutup (lingkaran), bukan garis
    yang boleh dipotong jadi beberapa segmen terpisah. Leaflet (dengan
    worldCopyJump yang sudah dipakai index.html) merender koordinat di
    luar rentang -180..180 dengan benar."""
    lat0_r, lon0_r = np.radians(lat0), np.radians(lon0)
    d = np.radians(radius_derajat)
    bearing = np.linspace(0, 2 * np.pi, n)
    lat = np.arcsin(np.sin(lat0_r) * np.cos(d) + np.cos(lat0_r) * np.sin(d) * np.cos(bearing))
    lon = lon0_r + np.arctan2(
        np.sin(bearing) * np.sin(d) * np.cos(lat0_r), np.cos(d) - np.sin(lat0_r) * np.sin(lat)
    )
    lon_deg = np.degrees(np.unwrap(lon))
    lat_deg = np.degrees(lat)
    coords = list(zip(np.round(lon_deg, 3).tolist(), np.round(lat_deg, 3).tolist()))
    coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def _lintasan_gerhana_matahari_geojson(lintasan):
    """list of {'waktu','lat','lon','gamma'} (dari hitung_lintasan_gerhana_
    matahari) -> MultiLineString, antimeridian-safe (pakai _split_
    antimeridian_seg yang sama dgn kontur tinggi hilal/elongasi)."""
    if not lintasan:
        return None
    seg = [[p["lon"], p["lat"]] for p in lintasan]
    coords = _split_antimeridian_seg(seg)
    return {"type": "MultiLineString", "coordinates": coords} if coords else None


def _kontak_ke_iso(kontak):
    return {k: (v.isoformat() if v is not None else None) for k, v in kontak.items()}


# ---------------------------------------------------------------------------
# In-Memory Python Cache & Vercel Edge CDN Cache-Control Manager
# ---------------------------------------------------------------------------
import hashlib
import json

_API_RESPONSE_CACHE = {}
_MAX_CACHE_ENTRIES = 500


def _make_cached_response(endpoint, payload, compute_fn, s_maxage=31536000, immutable=False):
    """
    Eksekusi compute_fn dengan caching 2 tingkat:
    1. In-Memory Cache (sub-milidetik untuk request berulang di instance serverless yang sama)
    2. Vercel Edge CDN Cache-Control Header (s-maxage & immutable), hemat komputasi Vercel 100%!
    """
    key_raw = endpoint + ":" + json.dumps(payload, sort_keys=True, default=str)
    cache_key = hashlib.md5(key_raw.encode("utf-8")).hexdigest()

    cache_header = f"public, max-age=31536000, s-maxage={s_maxage}"
    if immutable:
        cache_header += ", immutable"
    else:
        cache_header += ", stale-while-revalidate=86400"

    if cache_key in _API_RESPONSE_CACHE:
        data = _API_RESPONSE_CACHE[cache_key]
        res = jsonify(data)
        res.headers["Cache-Control"] = cache_header
        res.headers["X-Cache-Status"] = "HIT-MEMORY"
        return res

    data = compute_fn()
    if isinstance(data, dict) and data.get("ok"):
        if len(_API_RESPONSE_CACHE) >= _MAX_CACHE_ENTRIES:
            _API_RESPONSE_CACHE.clear()
        _API_RESPONSE_CACHE[cache_key] = data

    res = jsonify(data)
    res.headers["Cache-Control"] = cache_header
    res.headers["X-Cache-Status"] = "MISS"
    return res


# ---------------------------------------------------------------------------
# Endpoint: cari semua ijtimak (konjungsi) sepanjang tahun tertentu.
# ---------------------------------------------------------------------------
@app.route("/api/ijtimak")
def api_ijtimak():
    tahun_str = request.args.get("tahun", "").strip()
    mode = request.args.get("mode", "jpl").strip().lower()
    if mode not in ("jpl", "ringan"):
        mode = "jpl"

    if not (tahun_str.isdigit() and len(tahun_str) == 4):
        return _error_response("Masukkan tahun 4 digit, misalnya 2026.")
    tahun = int(tahun_str)

    def _hitung():
        with _state_lock:
            if mode == "jpl":
                _pastikan_ephemeris()
                ts_arg, eph_arg = _ts, _eph
            else:
                ts_arg, eph_arg = None, None
            waktu_list = hw.cari_ijtimak_tahun(tahun, ts_arg, eph_arg, mode=mode)
            waktu_utc = [hw.ke_utc_datetime(t) for t in waktu_list]

        hasil = [
            {"index": i, "iso": dt.isoformat(), "label": hw.format_waktu_ijtimak(dt)}
            for i, dt in enumerate(waktu_utc)
        ]
        return {"ok": True, "tahun": tahun, "mode": mode, "ijtimak": hasil}

    try:
        return _make_cached_response("/api/ijtimak", {"tahun": tahun, "mode": mode}, _hitung, s_maxage=31536000, immutable=True)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal mencari ijtimak: {e}", 500)


# ---------------------------------------------------------------------------
# Endpoint: hitung grid + evaluasi, lalu kembalikan GeoJSON peta MABIMS &
# Muhammadiyah untuk satu ijtimak terpilih (tanpa gambar sama sekali).
# ---------------------------------------------------------------------------
@app.route("/api/peta", methods=["POST"])
def api_peta():
    data = request.get_json(force=True, silent=True) or {}
    iso = data.get("iso")
    hari = data.get("hari", "ijtimak")  # "ijtimak" atau "setelah"
    mode = str(data.get("mode", "jpl")).strip().lower()
    if mode not in ("jpl", "ringan"):
        mode = "jpl"

    # CATATAN: endpoint ini SENGAJA dibuat stateless - client mengirim balik
    # nilai "iso" (waktu ijtimak UTC) yang tadi didapat dari /api/ijtimak,
    # bukan cuma "tahun"+"index" yang dulu dipakai untuk mencari lagi ke
    # cache di memori server. Kenapa diubah: di lingkungan serverless
    # (mis. Vercel) tiap request bisa ditangani instance proses yang
    # berbeda-beda, jadi cache di memori dari request /api/ijtimak
    # sebelumnya bisa saja sudah tidak ada lagi saat /api/peta dipanggil.
    # Dengan client mengirim balik "iso" secara langsung, endpoint ini
    # tidak bergantung sama sekali pada state dari request lain.
    if not iso:
        return _error_response("Parameter 'iso' (waktu ijtimak dari hasil /api/ijtimak) wajib diisi.")
    try:
        waktu_ijtimak = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return _error_response("Parameter 'iso' tidak valid.")

    tanggal_ijtimak = datetime(waktu_ijtimak.year, waktu_ijtimak.month, waktu_ijtimak.day)
    tanggal = tanggal_ijtimak if hari == "ijtimak" else tanggal_ijtimak + timedelta(days=1)

    def _hitung():
        fig_tmp = None
        try:
            with _state_lock:
                if mode == "jpl":
                    _pastikan_ephemeris()
                    ts_arg, eph_arg = _ts, _eph
                else:
                    ts_arg, eph_arg = None, None

                grids = hw.hitung_grid(tanggal, ts_arg, eph_arg, mode=mode)
                evaluasi = hw.evaluasi_pkg(
                    grids, tanggal, waktu_ijtimak=waktu_ijtimak, ts=ts_arg, eph=eph_arg, mode=mode
                )

                lon_mesh, lat_mesh = grids["lon_mesh"], grids["lat_mesh"]
                elong_grid, alt_grid = grids["elong_grid"], grids["alt_grid"]
                geo_alt_grid, hours_utc_grid = grids["geo_alt_grid"], grids["hours_utc_grid"]

                fig_tmp, ax_tmp = _dummy_axes()

                # ---- Peta MABIMS: elongasi >= 6.4 derajat & tinggi hilal >= 3 derajat (toposentris) ----
                mabims_zone = (elong_grid >= 6.4) & (alt_grid >= 3)
                mabims = {
                    "zona_memenuhi": _zona_geojson(ax_tmp, lon_mesh, lat_mesh, mabims_zone),
                    "kontur_alt3": _garis_geojson(ax_tmp, lon_mesh, lat_mesh, alt_grid, 3),
                    "kontur_elong64": _garis_geojson(ax_tmp, lon_mesh, lat_mesh, elong_grid, 6.4),
                    "zona_no_sunset": _zona_geojson(ax_tmp, lon_mesh, lat_mesh, np.isnan(alt_grid)),
                }

                # ---- Peta Muhammadiyah: elongasi >= 8 derajat & tinggi hilal >= 5 derajat (geosentris) ----
                pkg1 = evaluasi["pkg1_terpenuhi"]
                pkg2 = evaluasi["pkg2_terpenuhi"]
                titik_pkg2 = None

                if pkg1:
                    warna, label = "#e08a2e", "Zona memenuhi kriteria (PKG 1 & PKG 2 terpenuhi)"
                    zona_kriteria = _zona_geojson(ax_tmp, lon_mesh, lat_mesh, evaluasi["zona_pkg1"])
                elif pkg2:
                    warna, label = "#e8c04a", "Zona memenuhi PKG 2 (fallback, daratan utama Amerika)"
                    hasil_pkg2 = evaluasi["hasil_pkg2"]
                    lon2, lat2, zona2 = hasil_pkg2["lon_mesh"], hasil_pkg2["lat_mesh"], hasil_pkg2["zona"]
                    zona_kriteria = _zona_geojson(ax_tmp, lon2, lat2, zona2)
                    if np.any(zona2):
                        titik_pkg2 = {"lat": round(float(lat2[zona2].mean()), 3),
                                      "lon": round(float(lon2[zona2].mean()), 3)}
                else:
                    warna, label = "#e08a2e", "Zona memenuhi kriteria Muhammadiyah"
                    zona_kriteria = None

                if pkg1:
                    status_teks = "PKG 1 & PKG 2 terpenuhi"
                elif pkg2:
                    status_teks = "PKG 1 tidak terpenuhi — fallback ke PKG 2: terpenuhi"
                else:
                    status_teks = "PKG 1 & PKG 2 tidak terpenuhi"

                catatan_pkg2 = None
                if not pkg1:
                    baris = []
                    wfnz = evaluasi["waktu_fajar_nz"]
                    if wfnz is not None:
                        cek = "OK" if evaluasi["pkg2_ijtimak_ok"] else "TIDAK terpenuhi"
                        baris.append(
                            f"Ijtimak {waktu_ijtimak.strftime('%d %b %Y %H:%M')} UTC vs "
                            f"fajar NZ {wfnz.strftime('%d %b %Y %H:%M')} UTC -> {cek}"
                        )
                    else:
                        baris.append("Waktu fajar NZ tidak dapat dihitung.")
                    hp2 = evaluasi["hasil_pkg2"]
                    if hp2 is not None:
                        ket = "terpenuhi" if hp2["ditemukan"] else "TIDAK terpenuhi"
                        baris.append(f"Kriteria 5°/8° di daratan utama Amerika: {ket} (pencarian tahap {hp2['tahap']})")
                    else:
                        baris.append("Kriteria 5°/8° di daratan utama Amerika: tidak dapat diperiksa.")
                    catatan_pkg2 = "\n".join(baris)

                muhammadiyah = {
                    "warna_zona": warna,
                    "label_zona": label,
                    "zona_kriteria": zona_kriteria,
                    "titik_pkg2": titik_pkg2,
                    "kontur_alt5": _garis_geojson(ax_tmp, lon_mesh, lat_mesh, geo_alt_grid, 5),
                    "kontur_elong8": _garis_geojson(ax_tmp, lon_mesh, lat_mesh, elong_grid, 8),
                    "zona_no_sunset": _zona_geojson(ax_tmp, lon_mesh, lat_mesh, evaluasi["no_sunset_masked"]),
                    "kontur_cutoff": _garis_geojson(ax_tmp, lon_mesh, lat_mesh, hours_utc_grid, [0, 24]),
                    "status_teks": status_teks,
                    "catatan_pkg2": catatan_pkg2,
                }
            return {
                "ok": True,
                "mode": mode,
                "tanggal": tanggal.strftime("%d %B %Y"),
                "waktu_ijtimak": hw.format_waktu_ijtimak(waktu_ijtimak),
                "evaluasi": {
                    "pkg1_terpenuhi": bool(evaluasi.get("pkg1_terpenuhi")),
                    "pkg2_terpenuhi": bool(evaluasi.get("pkg2_terpenuhi")),
                    "pkg2_ijtimak_ok": bool(evaluasi.get("pkg2_ijtimak_ok")) if evaluasi.get("pkg2_ijtimak_ok") is not None else None,
                },
                "mabims": mabims,
                "muhammadiyah": muhammadiyah,
            }
        finally:
            if fig_tmp is not None:
                plt.close(fig_tmp)

    try:
        return _make_cached_response("/api/peta", {"iso": iso, "hari": hari, "mode": mode}, _hitung, s_maxage=31536000, immutable=True)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal menghitung peta: {e}", 500)


# ---------------------------------------------------------------------------
# Endpoint: peta khusus wilayah Indonesia (tinggi hilal & elongasi, gaya
# kontur BMKG tiap 0.2 derajat)
# ---------------------------------------------------------------------------
@app.route("/api/peta_indonesia", methods=["POST"])
def api_peta_indonesia():
    data = request.get_json(force=True, silent=True) or {}
    iso = data.get("iso")
    hari = data.get("hari", "ijtimak")
    mode = str(data.get("mode", "jpl")).strip().lower()
    if mode not in ("jpl", "ringan"):
        mode = "jpl"

    if not iso:
        return _error_response("Parameter 'iso' (waktu ijtimak dari hasil /api/ijtimak) wajib diisi.")
    try:
        waktu_ijtimak = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return _error_response("Parameter 'iso' tidak valid.")

    tanggal_ijtimak = datetime(waktu_ijtimak.year, waktu_ijtimak.month, waktu_ijtimak.day)
    tanggal = tanggal_ijtimak if hari == "ijtimak" else tanggal_ijtimak + timedelta(days=1)

    def _hitung():
        fig_tmp = None
        try:
            with _state_lock:
                if mode == "jpl":
                    _pastikan_ephemeris()
                    ts_arg, eph_arg = _ts, _eph
                else:
                    ts_arg, eph_arg = None, None

                grids_id = hw.hitung_grid_indonesia(tanggal, ts_arg, eph_arg, mode=mode)
                lon_mesh, lat_mesh = grids_id["lon_mesh"], grids_id["lat_mesh"]
                alt_grid, elong_grid = grids_id["alt_grid"], grids_id["elong_grid"]

                fig_tmp, ax_tmp = _dummy_axes()

                # ---- Peta tinggi hilal toposentris (garis tiap 0.2°, ambang MABIMS 3°) ----
                tinggi_hilal = {
                    "kontur": _kontur_berlabel_geojson(
                        ax_tmp, lon_mesh, lat_mesh, alt_grid, _levels_02(alt_grid, -2.0, 10.0)
                    ),
                    "kontur_ambang": _garis_geojson(ax_tmp, lon_mesh, lat_mesh, alt_grid, 3.0),
                    "ambang": 3.0,
                    "ambang_label": "Ambang MABIMS: tinggi hilal 3°",
                }

                # ---- Peta elongasi (garis tiap 0.2°, ambang MABIMS 6.4°) ----
                elongasi = {
                    "kontur": _kontur_berlabel_geojson(
                        ax_tmp, lon_mesh, lat_mesh, elong_grid, _levels_02(elong_grid, 0.0, 12.0)
                    ),
                    "kontur_ambang": _garis_geojson(ax_tmp, lon_mesh, lat_mesh, elong_grid, 6.4),
                    "ambang": 6.4,
                    "ambang_label": "Ambang MABIMS: elongasi 6.4°",
                }
            return {
                "ok": True,
                "mode": mode,
                "tanggal": tanggal.strftime("%d %B %Y"),
                "waktu_ijtimak": hw.format_waktu_ijtimak(waktu_ijtimak),
                "bounds": {
                    "lat_min": hw.INDONESIA_LAT_RANGE[0], "lat_max": hw.INDONESIA_LAT_RANGE[1],
                    "lon_min": hw.INDONESIA_LON_RANGE[0], "lon_max": hw.INDONESIA_LON_RANGE[1],
                },
                "tinggi_hilal": tinggi_hilal,
                "elongasi": elongasi,
            }
        finally:
            if fig_tmp is not None:
                plt.close(fig_tmp)

    try:
        return _make_cached_response("/api/peta_indonesia", {"iso": iso, "hari": hari, "mode": mode}, _hitung, s_maxage=31536000, immutable=True)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal menghitung peta Indonesia: {e}", 500)


# ---------------------------------------------------------------------------
# Endpoint: hitung waktu sholat hari ini & arah kiblat
# ---------------------------------------------------------------------------
@app.route("/api/sholat", methods=["POST"])
def api_sholat():
    data = request.get_json(force=True, silent=True) or {}
    try:
        tgl_str = data.get("tanggal")
        if tgl_str:
            tanggal = datetime.fromisoformat(tgl_str)
        else:
            now = datetime.now()
            tanggal = datetime(now.year, now.month, now.day)

        lat = float(data.get("lat", -6.1751))
        lon = float(data.get("lon", 106.8272))
        zona = float(data.get("zona", 7.0))
        elevasi = float(data.get("elevasi", 0.0))
        sudut_fajar = float(data.get("sudut_fajar", -20.0))
        sudut_isya = float(data.get("sudut_isya", -18.0))
        ihtiyat = float(data.get("ihtiyat", 2.0))
        imsak_offset = float(data.get("imsak_offset", 10.0))
        mazhab = str(data.get("mazhab", "syafii")).lower()
        mode = str(data.get("mode", "jpl")).strip().lower()
        if mode not in ("jpl", "ringan"):
            mode = "jpl"
    except Exception as e:
        return _error_response(f"Parameter input tidak valid: {e}")

    def _hitung():
        with _state_lock:
            if mode == "jpl":
                _pastikan_ephemeris()
                ts_arg, eph_arg = _ts, _eph
            else:
                ts_arg, eph_arg = None, None

            waktu_dict = hw.hitung_waktu_sholat_otomatis(
                tanggal, lat, lon, zona, mode=mode, ts=ts_arg, eph=eph_arg,
                elevasi_m=elevasi, sudut_fajar=sudut_fajar, sudut_isya=sudut_isya,
                ihtiyat_menit=ihtiyat, imsak_sebelum_fajr_menit=imsak_offset, mazhab_ashar=mazhab
            )
            az_v, dist_v = hw.qibla_vincenty(lat, lon)
            az_s, dist_s = hw.qibla_spherical(lat, lon)

        waktu_formatted = {k: hw.format_jam_desimal(v) for k, v in waktu_dict.items()}

        dms_lat = hw.format_dms(lat, "lat")
        dms_lon = hw.format_dms(lon, "lon")

        selisih_u = (az_v - 270.0) if az_v >= 270 else (360.0 - az_v)
        arah_kiblat_str = f"U {selisih_u:.2f}° B ({az_v:.2f}° Azimuth)"

        return {
            "ok": True,
            "mode": mode,
            "tanggal_iso": tanggal.strftime("%Y-%m-%d"),
            "tanggal_formatted": f"{tanggal.day} {hw.BULAN_ID[tanggal.month-1]} {tanggal.year}",
            "koordinat": {
                "lat": lat, "lon": lon, "zona": zona, "elevasi": elevasi,
                "lat_dms": dms_lat, "lon_dms": dms_lon
            },
            "waktu": waktu_formatted,
            "waktu_desimal": {k: (round(v, 4) if v is not None else None) for k, v in waktu_dict.items()},
            "kiblat": {
                "az_vincenty": round(az_v, 2),
                "az_spherical": round(az_s, 2),
                "jarak_km": round(dist_v, 1),
                "arah_teks": arah_kiblat_str,
                "kiblat_v_jam": hw.format_jam_desimal(waktu_dict.get("kiblat_v")),
                "kiblat_s_jam": hw.format_jam_desimal(waktu_dict.get("kiblat_s"))
            }
        }

    try:
        return _make_cached_response("/api/sholat", data, _hitung)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal menghitung waktu sholat: {e}", 500)


# ---------------------------------------------------------------------------
# Endpoint: hitung jadwal sholat 1 bulan penuh
# ---------------------------------------------------------------------------
@app.route("/api/sholat_bulan", methods=["POST"])
def api_sholat_bulan():
    data = request.get_json(force=True, silent=True) or {}
    try:
        now = datetime.now()
        tahun = int(data.get("tahun", now.year))
        bulan = int(data.get("bulan", now.month))
        lat = float(data.get("lat", -6.1751))
        lon = float(data.get("lon", 106.8272))
        zona = float(data.get("zona", 7.0))
        elevasi = float(data.get("elevasi", 0.0))
        sudut_fajar = float(data.get("sudut_fajar", -20.0))
        sudut_isya = float(data.get("sudut_isya", -18.0))
        ihtiyat = float(data.get("ihtiyat", 2.0))
        imsak_offset = float(data.get("imsak_offset", 10.0))
        mazhab = str(data.get("mazhab", "syafii")).lower()
        mode = str(data.get("mode", "jpl")).strip().lower()
        if mode not in ("jpl", "ringan"):
            mode = "jpl"
    except Exception as e:
        return _error_response(f"Parameter input tidak valid: {e}")

    def _hitung():
        with _state_lock:
            if mode == "jpl":
                _pastikan_ephemeris()
                ts_arg, eph_arg = _ts, _eph
            else:
                ts_arg, eph_arg = None, None

            jadwal_raw = hw.hitung_jadwal_sholat_bulan(
                tahun, bulan, lat, lon, zona, mode=mode, ts=ts_arg, eph=eph_arg,
                elevasi_m=elevasi, sudut_fajar=sudut_fajar, sudut_isya=sudut_isya,
                ihtiyat_menit=ihtiyat, imsak_sebelum_fajr_menit=imsak_offset, mazhab_ashar=mazhab
            )

        jadwal = []
        for tgl, w_dict in jadwal_raw:
            idx_hari = (tgl.weekday() + 1) % 7
            hari_nama = hw.HARI_ID[idx_hari]
            jadwal.append({
                "tgl": tgl.day,
                "iso": tgl.strftime("%Y-%m-%d"),
                "tanggal_formatted": f"{tgl.day:02d} {hw.BULAN_ID[bulan-1]} {tahun}",
                "hari_nama": hari_nama,
                "imsak": hw.format_jam_desimal(w_dict.get("imsak")),
                "subuh": hw.format_jam_desimal(w_dict.get("subuh")),
                "terbit": hw.format_jam_desimal(w_dict.get("terbit")),
                "dhuha": hw.format_jam_desimal(w_dict.get("dhuha")),
                "dzuhur": hw.format_jam_desimal(w_dict.get("dzuhur")),
                "ashar": hw.format_jam_desimal(w_dict.get("ashar")),
                "maghrib": hw.format_jam_desimal(w_dict.get("maghrib")),
                "isya": hw.format_jam_desimal(w_dict.get("isya")),
                "kiblat_v": hw.format_jam_desimal(w_dict.get("kiblat_v"))
            })

        return {
            "ok": True,
            "tahun": tahun,
            "bulan": bulan,
            "bulan_nama": hw.BULAN_ID[bulan-1],
            "jumlah_hari": len(jadwal),
            "jadwal": jadwal
        }

    try:
        return _make_cached_response("/api/sholat_bulan", data, _hitung)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal menghitung jadwal sholat bulanan: {e}", 500)


from flask import make_response

# ---------------------------------------------------------------------------
# Endpoint: daftar gerhana matahari & bulan sepanjang tahun tertentu.
# Memakai cari_gerhana_matahari_kandidat_ringan/cari_gerhana_bulan_kandidat_
# ringan - SUDAH DIVERIFIKASI murni numpy/skyfield (tanpa cartopy) & hasil
# perhitungannya dicek manual cocok dgn gerhana asli 2026 (cincin 17 Feb,
# total 12 Agu utk matahari; total 3 Mar, sebagian 28 Agu utk bulan).
# ---------------------------------------------------------------------------
@app.route("/api/gerhana")
def api_gerhana():
    tahun_str = request.args.get("tahun", "").strip()
    mode = request.args.get("mode", "jpl").strip().lower()
    if mode not in ("jpl", "ringan"):
        mode = "jpl"
    if not (tahun_str.isdigit() and len(tahun_str) == 4):
        return _error_response("Masukkan tahun 4 digit, misalnya 2026.")
    tahun = int(tahun_str)

    def _hitung():
        with _state_lock:
            if mode == "jpl":
                _pastikan_ephemeris()
                ts_arg, eph_arg = _ts, _eph
            else:
                ts_arg, eph_arg = None, None

            kandidat_m = hw.cari_gerhana_matahari_kandidat_ringan(tahun, mode=mode, ts=ts_arg, eph=eph_arg)
            kandidat_b = hw.cari_gerhana_bulan_kandidat_ringan(tahun, mode=mode, ts=ts_arg, eph=eph_arg)

            matahari = []
            for k in kandidat_m:
                wg = k["waktu_greatest_eclipse"]
                if wg is None:
                    continue  # bukan kandidat sungguhan (beta terlalu besar)
                if k["kena_bumi"]:
                    _, r_umbra_km, _ = hw._radius_bayangan_km(wg, mode=mode, ts=ts_arg, eph=eph_arg)
                    jenis = "total" if r_umbra_km > 0 else "cincin"
                else:
                    jenis = "sebagian"
                judul = {"total": "Total", "cincin": "Cincin", "sebagian": "Sebagian"}[jenis]
                matahari.append({
                    "iso": wg.isoformat(),
                    "jenis": jenis,
                    "lat": round(k["lat_perkiraan"], 3) if k["lat_perkiraan"] is not None else None,
                    "lon": round(k["lon_perkiraan"], 3) if k["lon_perkiraan"] is not None else None,
                    "label": f"{hw.format_waktu_ijtimak(wg)} — Gerhana Matahari {judul}",
                })

            bulan = []
            for k in kandidat_b:
                if k["jenis"] == "tidak ada gerhana":
                    continue
                wg = k["waktu_greatest_eclipse"]
                judul = {"total": "Total", "sebagian": "Sebagian", "penumbral": "Penumbral"}[k["jenis"]]
                bulan.append({
                    "iso": wg.isoformat(),
                    "jenis": k["jenis"],
                    "magnitudo_umbral": round(k["magnitudo_umbral"], 3),
                    "magnitudo_penumbral": round(k["magnitudo_penumbral"], 3),
                    "label": f"{hw.format_waktu_ijtimak(wg)} — Gerhana Bulan {judul}",
                })

        return {"ok": True, "tahun": tahun, "mode": mode, "matahari": matahari, "bulan": bulan}

    try:
        return _make_cached_response("/api/gerhana", {"tahun": tahun, "mode": mode}, _hitung, s_maxage=31536000, immutable=True)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal mencari gerhana: {e}", 500)


# ---------------------------------------------------------------------------
# Endpoint: detail + peta satu gerhana MATAHARI (lintasan totalitas/cincin,
# jejak bayangan penumbra, 6 waktu kontak) - stateless, "iso" dikirim balik
# dari hasil /api/gerhana (waktu_greatest_eclipse), sama pola dgn /api/peta.
# ---------------------------------------------------------------------------
@app.route("/api/gerhana_matahari", methods=["POST"])
def api_gerhana_matahari():
    data = request.get_json(force=True, silent=True) or {}
    iso = data.get("iso")
    mode = str(data.get("mode", "jpl")).strip().lower()
    if mode not in ("jpl", "ringan"):
        mode = "jpl"
    if not iso:
        return _error_response("Parameter 'iso' (waktu greatest eclipse dari hasil /api/gerhana) wajib diisi.")
    try:
        waktu_greatest = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return _error_response("Parameter 'iso' tidak valid.")

    def _hitung():
        with _state_lock:
            if mode == "jpl":
                _pastikan_ephemeris()
                ts_arg, eph_arg = _ts, _eph
            else:
                ts_arg, eph_arg = None, None

            _, r_umbra_km, _ = hw._radius_bayangan_km(waktu_greatest, mode=mode, ts=ts_arg, eph=eph_arg)
            jenis = "total" if r_umbra_km > 0 else "cincin"

            lintasan = hw.hitung_lintasan_gerhana_matahari(waktu_greatest, mode=mode, ts=ts_arg, eph=eph_arg)
            penumbra = hw.hitung_bayangan_penumbra_gerhana_matahari(waktu_greatest, mode=mode, ts=ts_arg, eph=eph_arg)
            kontak = hw.cari_kontak_gerhana_matahari(waktu_greatest, mode=mode, ts=ts_arg, eph=eph_arg)

        titik_greatest = None
        if lintasan:
            tengah = min(lintasan, key=lambda p: abs((p["waktu"] - waktu_greatest).total_seconds()))
            titik_greatest = {"lat": round(tengah["lat"], 3), "lon": round(tengah["lon"], 3)}

        return {
            "ok": True,
            "mode": mode,
            "jenis": jenis,
            "waktu_greatest_eclipse": hw.format_waktu_ijtimak(waktu_greatest),
            "titik_greatest": titik_greatest,
            "lintasan_totalitas": _lintasan_gerhana_matahari_geojson(lintasan) if jenis != "sebagian" else None,
            # Jejak bayangan penumbra dikirim sbg titik pusat + radius (km),
            # BUKAN poligon jadi - lebih ringan & digambar via L.circle() di
            # Leaflet (radiusnya jauh lebih kecil dari lingkaran horizon
            # gerhana Bulan, jadi aproksimasi "lingkaran kecil" Leaflet
            # cukup akurat utk lapis arsiran ini - beda dgn lingkaran
            # horizon 90 derajat gerhana Bulan yang WAJIB poligon geodesic
            # presisi, lihat _lingkaran_geodesic_geojson).
            "bayangan_penumbra": [
                {"lat": round(p["lat"], 3), "lon": round(p["lon"], 3), "radius_km": round(p["r_penumbra_km"], 1)}
                for p in penumbra
            ],
            "kontak": _kontak_ke_iso(kontak),
        }

    try:
        return _make_cached_response("/api/gerhana_matahari", {"iso": iso, "mode": mode}, _hitung, s_maxage=31536000, immutable=True)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal menghitung peta gerhana matahari: {e}", 500)


# ---------------------------------------------------------------------------
# Endpoint: detail + peta satu gerhana BULAN (lingkaran visibilitas P1/P4 &
# greatest eclipse, 7 waktu kontak) - stateless sama seperti di atas.
# ---------------------------------------------------------------------------
@app.route("/api/gerhana_bulan", methods=["POST"])
def api_gerhana_bulan():
    data = request.get_json(force=True, silent=True) or {}
    iso = data.get("iso")
    mode = str(data.get("mode", "jpl")).strip().lower()
    if mode not in ("jpl", "ringan"):
        mode = "jpl"
    if not iso:
        return _error_response("Parameter 'iso' (waktu greatest eclipse dari hasil /api/gerhana) wajib diisi.")
    try:
        waktu_greatest = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return _error_response("Parameter 'iso' tidak valid.")

    def _hitung():
        with _state_lock:
            if mode == "jpl":
                _pastikan_ephemeris()
                ts_arg, eph_arg = _ts, _eph
            else:
                ts_arg, eph_arg = None, None

            P_sun, P_moon, _, _ = hw._vektor_matahari_bulan_gast_batch(
                waktu_greatest, np.array([0.0]), mode, ts_arg, eph_arg)
            jarak, r_umbra, r_penumbra = hw._jarak_bulan_ke_sumbu_bayangan_bumi_km_batch(P_sun, P_moon)
            jarak, r_umbra, r_penumbra = float(jarak[0]), float(r_umbra[0]), float(r_penumbra[0])
            mag_umbral = (r_umbra + hw.R_BULAN_KM - jarak) / (2 * hw.R_BULAN_KM)
            mag_penumbral = (r_penumbra + hw.R_BULAN_KM - jarak) / (2 * hw.R_BULAN_KM)
            if mag_umbral >= 1.0:
                jenis = "total"
            elif mag_umbral > 0.0:
                jenis = "sebagian"
            else:
                jenis = "penumbral"

            kontak = hw.cari_kontak_gerhana_bulan(waktu_greatest, mode=mode, ts=ts_arg, eph=eph_arg)
            lat_g, lon_g = hw._subtitik_bulan(waktu_greatest, mode=mode, ts=ts_arg, eph=eph_arg)

            lingkaran_p1 = lingkaran_p4 = None
            if kontak.get("P1") is not None:
                lat_p1, lon_p1 = hw._subtitik_bulan(kontak["P1"], mode=mode, ts=ts_arg, eph=eph_arg)
                lingkaran_p1 = _lingkaran_geodesic_geojson(lat_p1, lon_p1)
            if kontak.get("P4") is not None:
                lat_p4, lon_p4 = hw._subtitik_bulan(kontak["P4"], mode=mode, ts=ts_arg, eph=eph_arg)
                lingkaran_p4 = _lingkaran_geodesic_geojson(lat_p4, lon_p4)

        return {
            "ok": True,
            "mode": mode,
            "jenis": jenis,
            "waktu_greatest_eclipse": hw.format_waktu_ijtimak(waktu_greatest),
            "magnitudo_umbral": round(mag_umbral, 3),
            "magnitudo_penumbral": round(mag_penumbral, 3),
            "titik_greatest": {"lat": round(lat_g, 3), "lon": round(lon_g, 3)},
            "visibilitas": {
                "lingkaran_greatest": _lingkaran_geodesic_geojson(lat_g, lon_g),
                "lingkaran_p1": lingkaran_p1,
                "lingkaran_p4": lingkaran_p4,
            },
            "kontak": _kontak_ke_iso(kontak),
        }

    try:
        return _make_cached_response("/api/gerhana_bulan", {"iso": iso, "mode": mode}, _hitung, s_maxage=31536000, immutable=True)
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal menghitung peta gerhana bulan: {e}", 500)


@app.route("/")
def index():
    res = make_response(send_from_directory(BASE_DIR, "index.html"))
    res.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return res


@app.route("/<path:filename>")
def static_files(filename):
    res = make_response(send_from_directory(BASE_DIR, filename))
    res.headers["Cache-Control"] = "public, max-age=31536000, s-maxage=31536000, immutable"
    return res


def _jalankan_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    URL = "http://127.0.0.1:5000"
    try:
        import webview  # pywebview, opsional - untuk mode jendela desktop (webview)
        threading.Thread(target=_jalankan_flask, daemon=True).start()
        webview.create_window("HisabWin", URL, width=1200, height=820, min_size=(900, 650))
        webview.start()
    except ImportError:
        print("=" * 60)
        print(" pywebview belum terpasang -> jalan sebagai server web biasa.")
        print(" (opsional: 'pip install pywebview' untuk mode jendela desktop)")
        print(f" Buka di browser: {URL}")
        print("=" * 60)
        _jalankan_flask()
