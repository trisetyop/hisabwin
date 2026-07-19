# 🌙 HisabWin

**HisabWin** adalah aplikasi desktop untuk visualisasi dan pemetaan peta visibilitas hilal awal bulan kamariah di seluruh dunia secara interaktif.

Aplikasi ini didedikasikan sebagai sebuah **TRIBUTE** (penghormatan) terhadap **WinHisab (WinHisab 2)**, perangkat lunak hisab rukyat legendaris buatan Kementerian Agama RI yang telah berjasa mendidik dan menemani para astronom muslim, praktisi falak, dan akademisi di Indonesia selama bertahun-tahun.

Di era modern ini, HisabWin hadir membawa semangat WinHisab ke dalam arsitektur teknologi yang lebih baru (Python, Skyfield, Matplotlib, Cartopy) serta mendukung kriteria-kriteria kontemporer:

1. **Kriteria MABIMS Baru** (tinggi 3°, elongasi 6,4°)
2. **Kriteria KHGT** (Kalender Hijriah Global Tunggal) Muhammadiyah

> **Versi saat ini:** v1.0.1

---

## ✨ Fitur Utama

### Perhitungan & Peta Hilal
- Pencarian waktu ijtimak (konjungsi) otomatis sepanjang tahun.
- Peta visibilitas hilal global dan khusus Indonesia, dengan kontur resolusi tinggi.
- Dua mode perhitungan: **Presisi** (Skyfield + JPL DE421, dapat ditingkatkan ke DE440/DE441) dan **Ringan** (VSOP87 + ELP2000, cepat & offline).
- Pilihan kriteria yang bisa dihitung secara independen: MABIMS, KHGT, ketinggian lokal, dan elongasi lokal.

### Gerhana
- Deteksi dan visualisasi **gerhana matahari** (perhitungan umbra/penumbra, jalur gerhana, waktu kontak) di peta dunia.
- Deteksi dan visualisasi **gerhana bulan** (P1/U1/U2/U3/U4/P4) beserta peta wilayah yang bisa menyaksikannya.
- Pencarian gerhana berbasis tahun dengan panel akordeon di GUI.

### Kalender & Konversi
- Konverter kalender **Masehi ↔ Hijriah**, mendukung metode urfi (tabular), MABIMS, dan KHGT.
- Perbandingan awal bulan Hijriah antara kriteria **MABIMS vs KHGT** di titik-titik sampel pesisir Indonesia, lengkap dengan ekspor CSV.

### Efemeris & Objek Langit
- Tabel efemeris (azimuth/altitude/deklinasi) untuk Matahari, Bulan, dan planet Merkurius–Pluto.
- Dukungan objek kustom JPL Horizons (asteroid, wahana antariksa, dll.) via pencarian daring.
- Manajemen kernel JPL (DE421 dibundel, DE440/DE441 dapat diunduh sesuai kebutuhan presisi).

### Waktu Sholat & Kiblat
- Kalkulator waktu sholat beserta arah kiblat.

### Antarmuka & Kemudahan Pakai
- GUI modern berbasis Tkinter/ttkbootstrap dengan tab membulat, bayangan kartu lembut, dan overlay splash saat startup.
- Bekerja **sepenuhnya offline** (data Natural Earth 110m dan ephemeris DE421 dibundel ke dalam installer).
- Instalasi Windows mandiri (installer/uninstaller) serta build portabel Linux.
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
