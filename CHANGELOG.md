# Changelog

Semua perubahan penting pada proyek ini didokumentasikan di berkas ini.
Format mengacu pada [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), dan proyek ini mengikuti [Semantic Versioning](https://semver.org/lang/id/).

## [1.0.1] - 2026-07-19

Rilis ini merangkum seluruh pekerjaan sejak `v1.0.0` (rilis perdana) hingga commit terakhir di `main`. Dibandingkan versi pertama, HisabWin kini jauh lebih lengkap: dari sekadar peta visibilitas hilal, aplikasi ini bertambah dukungan gerhana matahari/bulan, konverter & pembanding kalender, manajemen kernel JPL, objek langit tambahan, serta banyak perbaikan performa dan tampilan.

### Ditambahkan
- **Gerhana matahari**: deteksi kandidat, perhitungan umbra/penumbra, waktu kontak, dan visualisasi jalur gerhana di peta dunia, lengkap dengan panel akordeon pencarian berbasis tahun.
- **Gerhana bulan**: deteksi P1/U1/U2/U3/U4/P4, peta visibilitas, serta radio button untuk memilih jenis gerhana (matahari/bulan) di GUI.
- **Perbandingan kalender MABIMS vs KHGT**: mesin perbandingan awal bulan Hijriah di titik-titik sampel pesisir Indonesia, tab hasil baru, dan ekspor ke CSV.
- **Konverter kalender Masehi ↔ Hijriah**: mendukung metode urfi (tabular), MABIMS, dan KHGT, dengan panel akordeon baru dan perhitungan berbasis thread.
- **Manajemen kernel JPL** (DE421/DE440/DE441): katalog kernel, preferensi tersimpan, unduh/hapus kernel opsional (DE440 ±114MB, DE441 ±1.5GB) via server NASA/NAIF, serta dialog modal status kernel.
- **Dukungan objek langit tambahan**: planet Merkurius–Pluto serta objek kustom JPL Horizons (asteroid, wahana antariksa, dll.) lewat dialog pencarian, dengan parser hasil pencarian berbasis regex.
- **Mode ringan** (`_alt_matahari_saja`) untuk perhitungan altitude Matahari yang lebih cepat tanpa perhitungan Bulan yang mahal.
- Build **Linux** (tarball portabel) di GitHub Actions, selain build Windows yang sudah ada.
- Overlay `bg.png` saat aplikasi baru dibuka, sebelum tab peta/hasil apa pun muncul, agar tampilan awal lebih rapi.
- Tab membulat (rounded tabs) dan bayangan kartu lembut (soft card shadow) tanpa dependensi eksternal.
- Menu klik-kanan pada tab (tutup / tutup yang lain / tutup semua).
- Checkbox pemilihan kriteria peta yang dihitung (MABIMS, KHGT, altitude lokal, elongasi lokal), dengan validasi minimal satu kriteria dipilih.
- Bundel data **Natural Earth 110m** ke dalam build agar aplikasi berjalan sepenuhnya offline (tidak perlu mengunduh shapefile Cartopy saat runtime).
- Dependensi `requests` beserta penanganan pesan error yang ramah pengguna saat modul untuk JPL Horizons daring belum terpasang.
- `requirements.txt` untuk mendukung caching dependensi di CI.

### Diubah
- Model nutasi Skyfield dipindah dari IAU2000A ke IAU2000B (selisih maks. ~0,4 mas), mempercepat perhitungan mode Muhammadiyah dari ~2,75 detik menjadi ~0,49 detik.
- Perhitungan geometri gerhana (jarak, titik bayangan, radius bayangan, dll.) divektorkan dengan NumPy, menggantikan loop Python per-menit agar jauh lebih cepat.
- Skala fitur peta Cartopy (LAND/OCEAN/BORDERS/coastlines) dikunci ke resolusi 110m agar konsisten dan tidak memicu unduhan shapefile campuran saat runtime.
- Logika pemilihan data historis Delta-T dirapikan (dari `np.where` bertingkat menjadi `np.select`) dan ditambah data historis untuk akurasi lebih baik.
- Resolusi kontur peta dinaikkan menjadi 0,2° (peta global) dan 0,05° (peta Indonesia), dengan label kontur-saja dan `constrained_layout`.
- Thread perhitungan peta global/Indonesia kini berjalan kondisional dan paralel, serta menutup tab-tab lama secara otomatis.
- Tata letak input tanggal (hari/bulan/tahun) diubah dari satu baris 6-kolom menjadi tersusun vertikal agar tidak meluber di panel kiri yang lebarnya tetap.
- Render overlay `bg.png` awal dibuat lebih andal lewat mekanisme retry (hingga 30 percobaan, ±1,5 detik) untuk mengatasi ukuran widget yang belum valid saat window baru dibuka.

### Diperbaiki
- Selisih satu hari (off-by-one) pada penentuan awal bulan Hijriah: jika kriteria visibilitas terpenuhi pada malam tanggal yang diperiksa, tanggal Masehi yang diumumkan kini digeser ke hari berikutnya.
- Bug perbandingan zona waktu (naive vs aware) pada fitur perbandingan kalender, diperbaiki dengan helper `_ke_naif`.
- Bug proyeksi titik pada rutin bayangan gerhana batch, memastikan skalar proyeksi berpasangan dengan arah vektor satuan yang benar (menghindari kesalahan skala).
- Masalah caching workflow CI dengan menambahkan `requirements.txt`.

## [1.0.0] - 2026-07-14

Rilis perdana 🌙 — versi awal HisabWin sebagai aplikasi desktop visualisasi peta visibilitas hilal.

### Ditambahkan
- Aplikasi desktop dengan GUI Tkinter bergaya flat modern.
- Perhitungan astronomi menggunakan Skyfield (JPL DE421) dan metode ringan VSOP87 + ELP2000.
- Peta visibilitas hilal global dan khusus Indonesia.
- Kalkulator waktu sholat dan arah kiblat.
- Dua mode perhitungan: Presisi (Skyfield) dan Ringan.
- Dukungan kriteria MABIMS dan Muhammadiyah (KHGT).
- Installer dan uninstaller Windows dengan pembuatan shortcut otomatis.
- Pipeline CI/CD GitHub Actions untuk build executable.
- Berkas `readme.txt` berisi penghormatan (tribute) kepada WinHisab 2.


