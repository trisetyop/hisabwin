========================================================================
                               HISABWIN
========================================================================

HisabWin adalah aplikasi desktop untuk visualisasi dan pemetaan peta 
visibilitas hilal awal bulan kamariah di seluruh dunia secara interaktif.

Aplikasi ini didekasikan sebagai sebuah TRIBUTE (penghormatan) terhadap
WINHISAB (WinHisab 2), perangkat lunak hisab rukyat legendaris buatan
Kementerian Agama RI yang telah berjasa mendidik dan menemani para
astronom muslim, praktisi falak, dan akademisi di Indonesia selama 
bertahun-tahun.

Di era modern ini, HisabWin hadir membawa semangat WinHisab ke dalam 
arsitektur teknologi yang lebih baru (Python, Matplotlib, Cartopy) 
serta mendukung kriteria-kriteria kontemporer:
1. Kriteria MABIMS Baru (Tinggi 3 derajat, Elongasi 6.4 derajat).
2. Kriteria KHGT (Kalender Hijriah Global Tunggal) Muhammadiyah.

------------------------------------------------------------------------
FITUR UTAMA
------------------------------------------------------------------------
* Pencarian waktu ijtimak (konjungsi) otomatis sepanjang tahun.
* Plotting peta bumi interaktif yang kaya detail visual.
* Dukungan penuh metode penghitungan offline (VSOP87 + ELP2000) 
  maupun presisi tinggi JPL DE421.
* Tampilan GUI yang modern, bersih, dan nyaman dipandang mata.
* Proses instalasi Windows mandiri yang mudah dan cepat.

------------------------------------------------------------------------
Kebutuhan Sistem & Penggunaan
------------------------------------------------------------------------
Untuk pengguna akhir (end-user):
Cukup jalankan berkas 'HisabWin_Installer.exe' untuk memasang aplikasi 
ke komputer Anda secara otomatis.

Untuk pengembangan (development):
1. Pastikan Python 3.10+ sudah terpasang.
2. Pasang dependensi menggunakan perintah:
   pip install -r requirements.txt
3. Jalankan aplikasi utama:
   python hisabwin.py

------------------------------------------------------------------------
Terima kasih kepada para perintis falakiah Indonesia dan tim pengembang
WinHisab legendaris yang telah menjadi inspirasi utama proyek ini.
========================================================================
