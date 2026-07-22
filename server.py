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
try:
    import hisabwin as hw
finally:
    matplotlib.use = _matplotlib_use_asli

import numpy as np
import matplotlib.pyplot as plt
from shapely.geometry import Polygon as ShapelyPolygon, Point as ShapelyPoint
from skyfield.api import load
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

_state_lock = threading.Lock()
_ts = None
_eph = None

# Cache sederhana di memori: {tahun: [datetime UTC, ...]}
_cache_ijtimak = {}


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
                coords.append(np.round(seg, 3).tolist())
    return {"type": "MultiLineString", "coordinates": coords} if coords else None


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


# ---------------------------------------------------------------------------
# Endpoint: cari semua ijtimak (konjungsi) sepanjang tahun tertentu.
# ---------------------------------------------------------------------------
@app.route("/api/ijtimak")
def api_ijtimak():
    tahun_str = request.args.get("tahun", "").strip()
    if not (tahun_str.isdigit() and len(tahun_str) == 4):
        return _error_response("Masukkan tahun 4 digit, misalnya 2026.")
    tahun = int(tahun_str)

    try:
        with _state_lock:
            _pastikan_ephemeris()
            waktu_list = hw.cari_ijtimak_tahun(tahun, _ts, _eph, mode="jpl")
            waktu_utc = [hw.ke_utc_datetime(t) for t in waktu_list]
            _cache_ijtimak[tahun] = waktu_utc
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal mencari ijtimak: {e}", 500)

    hasil = [
        {"index": i, "iso": dt.isoformat(), "label": hw.format_waktu_ijtimak(dt)}
        for i, dt in enumerate(waktu_utc)
    ]
    return jsonify({"ok": True, "tahun": tahun, "ijtimak": hasil})


# ---------------------------------------------------------------------------
# Endpoint: hitung grid + evaluasi, lalu kembalikan GeoJSON peta MABIMS &
# Muhammadiyah untuk satu ijtimak terpilih (tanpa gambar sama sekali).
# ---------------------------------------------------------------------------
@app.route("/api/peta", methods=["POST"])
def api_peta():
    data = request.get_json(force=True, silent=True) or {}
    tahun = data.get("tahun")
    index = data.get("index")
    hari = data.get("hari", "ijtimak")  # "ijtimak" atau "setelah"

    if tahun is None or index is None:
        return _error_response("Parameter 'tahun' dan 'index' wajib diisi.")
    try:
        tahun = int(tahun)
        index = int(index)
    except (TypeError, ValueError):
        return _error_response("Parameter 'tahun'/'index' tidak valid.")

    if tahun not in _cache_ijtimak:
        return _error_response("Cari ijtimak untuk tahun ini dahulu (panggil /api/ijtimak).")
    waktu_list = _cache_ijtimak[tahun]
    if not (0 <= index < len(waktu_list)):
        return _error_response("Index ijtimak di luar jangkauan.")

    waktu_ijtimak = waktu_list[index]
    tanggal_ijtimak = datetime(waktu_ijtimak.year, waktu_ijtimak.month, waktu_ijtimak.day)
    tanggal = tanggal_ijtimak if hari == "ijtimak" else tanggal_ijtimak + timedelta(days=1)

    fig_tmp = None
    try:
        with _state_lock:
            _pastikan_ephemeris()

            grids = hw.hitung_grid(tanggal, _ts, _eph, mode="jpl")
            evaluasi = hw.evaluasi_pkg(
                grids, tanggal, waktu_ijtimak=waktu_ijtimak, ts=_ts, eph=_eph, mode="jpl"
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
    except Exception as e:
        traceback.print_exc()
        return _error_response(f"Gagal menghitung peta: {e}", 500)
    finally:
        if fig_tmp is not None:
            plt.close(fig_tmp)

    return jsonify({
        "ok": True,
        "tanggal": tanggal.strftime("%d %B %Y"),
        "waktu_ijtimak": hw.format_waktu_ijtimak(waktu_ijtimak),
        "evaluasi": {
            "pkg1_terpenuhi": bool(evaluasi.get("pkg1_terpenuhi")),
            "pkg2_terpenuhi": bool(evaluasi.get("pkg2_terpenuhi")),
            "pkg2_ijtimak_ok": bool(evaluasi.get("pkg2_ijtimak_ok")) if evaluasi.get("pkg2_ijtimak_ok") is not None else None,
        },
        "mabims": mabims,
        "muhammadiyah": muhammadiyah,
    })


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


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
