# 🌙 HisabWin

**HisabWin** adalah aplikasi desktop untuk visualisasi dan pemetaan peta visibilitas hilal awal bulan kamariah di seluruh dunia secara interaktif.

Aplikasi ini didedikasikan sebagai sebuah **TRIBUTE** (penghormatan) terhadap **WinHisab (WinHisab 2)**, perangkat lunak hisab rukyat legendaris buatan Kementerian Agama RI yang telah berjasa mendidik dan menemani para astronom muslim, praktisi falak, dan akademisi di Indonesia selama bertahun-tahun.

Di era modern ini, HisabWin hadir membawa semangat WinHisab ke dalam arsitektur teknologi yang lebih baru (Python, Skyfield, Matplotlib, Cartopy) serta mendukung kriteria-kriteria kontemporer:

1. **Kriteria MABIMS Baru** (tinggi 3°, elongasi 6,4°)
2. **Kriteria KHGT** (Kalender Hijriah Global Tunggal) Muhammadiyah

> **Versi saat ini:** v1.1.0

---

## ✨ Fitur Utama

### Perhitungan & Peta Hilal
- Pencarian waktu ijtimak (konjungsi) otomatis sepanjang tahun.
- Peta visibilitas hilal global dan khusus Indonesia, dengan kontur resolusi tinggi.
- Dua mode perhitungan: **Presisi** (Skyfield + JPL DE421, dapat ditingkatkan ke DE440/DE441) dan **Ringan** (VSOP87 + ELP2000, cepat & offline).
- Pilihan kriteria yang bisa dihitung secara independen: MABIMS, KHGT, ketinggian lokal, dan elongasi lokal.

### Simulasi Hilal & Profil Cakrawala (3D Ridgeline Horizon)
- Visualisasi animasi terbenamnya Hilal & Matahari terhadap profil horizon topografi.
- **Perbandingan 3 Horizon**: Ideotoped/Geometrik, Refraksi Standar, dan Profil Ridgeline Pegunungan.
- Pencarian nama puncak gunung & bukit lokal di Indonesia berbasis database offline CSV (`gunung_indonesia.csv`).
- Kalkulasi akurat memperhitungkan limb (tepi) & semi-diameter Matahari, jarak Matahari, serta refraksi atmosferik.

### Planetarium Interaktif & Teleskop
- Mode **Planetarium Interaktif** (`starmap.py`) dengan proyeksi langit malam, katalog bintang terang (`bintang_terang.csv`), dan rasi bintang (`rasi_garis.csv`).
- Integrasi kontrol teleskop via protokol **ASCOM / Alpaca** (`kontrol_teleskop.py`) beserta built-in mock server untuk simulasi.
- Modul kamera mock & tayangan **Live View** langsung di dalam aplikasi.
- Seleksi & pelacakan objek langit real-time untuk pengarahan teleskop.

### Almanak & Ekspor Dokumen
- Generator **Almanak Falakiah Bulanan (PDF)** lengkap dengan tampilan kalender grid, info ijtimak, awal bulan Hijriah, dan waktu astronomi/sholat.
- Konverter kalender **Masehi ↔ Hijriah**, mendukung metode urfi (tabular), MABIMS, dan KHGT (termasuk penyempurnaan kriteria PKG1 & PKG2).
- Perbandingan awal bulan Hijriah antara kriteria **MABIMS vs KHGT** di titik-titik sampel pesisir Indonesia, lengkap dengan ekspor CSV.

### Scripting & Web API
- Panel **Konsol Scripting Python** interaktif di GUI untuk eksekusi skrip falak mandiri.
- Modul `hisabwin_api.py` untuk penggunaan fungsi astronomi HisabWin secara programmatic.
- Web Application & backend Flask (`server.py`, `index.html`, `vercel.json`) dengan peta gerhana interaktif 3D Globe dan waktu sholat serverless.

### Gerhana & Efemeris
- Deteksi dan visualisasi **gerhana matahari** (umbra/penumbra, jalur gerhana, waktu kontak) dan **gerhana bulan** (P1/U1/U2/U3/U4/P4).
- Tabel efemeris Matahari, Bulan, dan planet Merkurius–Pluto serta objek kustom JPL Horizons.
- Manajemen kernel JPL (DE421 dibundel, DE440/DE441 opsional).

### Antarmuka & Kemudahan Pakai
- GUI modern berbasis Tkinter/ttkbootstrap dengan tab membulat, bayangan kartu lembut, dan splash screen kustom lintas platform.
- Bekerja **sepenuhnya offline** (data Natural Earth 110m, catalog bintang, database gunung, dan ephemeris DE421 dibundel).
- Installer Windows modern berbasis **NSIS** serta build portabel Linux.
- Continuous Integration (GitHub Actions) untuk build otomatis Windows & Linux.

---

## 🖥️ Kebutuhan Sistem & Penggunaan

### Untuk pengguna akhir (end-user)
Cukup jalankan berkas `HisabWin_Installer.exe` (hasil build CI) untuk memasang aplikasi ke komputer Anda secara otomatis.

### Untuk pengembangan (development)
1. Pastikan **Python 3.10+** sudah terpasang.
2. Pasang dependensi:
   ```bash
   pip install -r requirements.txt
   ```
3. Jalankan aplikasi utama:
   ```bash
   python hisabwin.py
   ```

### Dependensi utama
`skyfield`, `cartopy`, `numpy`, `matplotlib`, `shapely`, `ttkbootstrap`, `pillow`, `pyinstaller`, `requests`

---

## 📦 Build

Build executable Windows & tarball Linux portable dilakukan otomatis lewat GitHub Actions (`.github/workflows/build.yml`) menggunakan **PyInstaller**, dengan data Natural Earth dan ephemeris DE421 yang sudah dibundel agar hasil build bisa berjalan tanpa koneksi internet.

Untuk build manual di Windows, lihat `build_installer.ps1`.

---

## 📜 Lisensi

Proyek ini dirilis di bawah [Lisensi MIT](LICENSE).

---

## 🙏 Ucapan Terima Kasih

Terima kasih kepada para perintis falakiah Indonesia dan tim pengembang WinHisab legendaris yang telah menjadi inspirasi utama proyek ini.

---

## 📝 Changelog

Lihat riwayat perubahan lengkap di [CHANGELOG.md](CHANGELOG.md).
