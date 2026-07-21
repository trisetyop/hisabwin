# PowerShell script to build HisabWin Installer
# Usage: Run this script in PowerShell
#
# Catatan migrasi (dari installer.py/uninstaller.py + 7z self-extracting
# ke NSIS): dulu script ini membundel 7z.exe/7z.dll + HisabWin.7z ke dalam
# sebuah exe PyInstaller --onefile (installer.py), yang saat dijalankan user
# harus mengekstrak dirinya sendiri dulu (bootloader PyInstaller) SEBELUM
# baru mengekstrak HisabWin.7z lewat 7z.exe. Dua lapis ekstraksi ini yang
# jadi sumber error "Failed to extract 7z.dll: decompression resulted in
# return code -1!" saat exe-nya korup/ke-lock antivirus/download terputus.
#
# Sekarang installer.nsi (dikompilasi oleh NSIS/makensis) langsung mem-
# bundel & mengekstrak folder dist\HisabWin dengan kompresi LZMA bawaannya
# sendiri -- tidak ada lagi 7z.exe/7z.dll eksternal maupun exe yang meng-
# ekstrak dirinya sendiri, dan uninstaller dibuat otomatis oleh NSIS
# (WriteUninstaller), jadi uninstaller.py juga tidak diperlukan lagi.

Write-Host "=== Memulai Proses Kompilasi Installer HisabWin (NSIS) ===" -ForegroundColor Green

$WorkspaceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $WorkspaceDir) {
    $WorkspaceDir = (Get-Location).Path
}
$DistDir = "$WorkspaceDir\dist"
$AppDir = "$DistDir\HisabWin"
$NsiScript = "$WorkspaceDir\installer.nsi"

# 1. Pastikan hasil build PyInstaller (--onedir) untuk aplikasi utama ada.
#    Folder ini dihasilkan oleh langkah terpisah:
#      pyinstaller --onedir --name HisabWin ... hisabwin.py
#    (lihat .github/workflows/build.yml, step "Build HisabWin Executable")
if (-not (Test-Path $AppDir)) {
    Write-Error "Folder $AppDir tidak ditemukan! Jalankan dulu build PyInstaller --onedir untuk hisabwin.py sebelum script ini."
    exit 1
}

$AppExe = "$AppDir\HisabWin.exe"
if (-not (Test-Path $AppExe)) {
    Write-Error "HisabWin.exe tidak ditemukan di $AppDir! Build PyInstaller sepertinya belum lengkap/gagal."
    exit 1
}

if (-not (Test-Path $NsiScript)) {
    Write-Error "installer.nsi tidak ditemukan di $WorkspaceDir!"
    exit 1
}

# 2. Cari makensis.exe (compiler NSIS)
$makensisCheck = Get-Command makensis -ErrorAction SilentlyContinue
if ($makensisCheck) {
    $MakensisExe = $makensisCheck.Source
} else {
    $CandidatePaths = @(
        "$Env:ProgramFiles(x86)\NSIS\makensis.exe",
        "$Env:ProgramFiles\NSIS\makensis.exe",
        "C:\Program Files (x86)\NSIS\makensis.exe",
        "C:\Program Files\NSIS\makensis.exe"
    )
    $MakensisExe = $CandidatePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $MakensisExe -or -not (Test-Path $MakensisExe)) {
    Write-Error "makensis.exe (NSIS) tidak ditemukan! Instal NSIS dari https://nsis.sourceforge.io/Download atau 'choco install nsis', lalu jalankan ulang script ini."
    exit 1
}

Write-Host "Menggunakan NSIS dari: $MakensisExe" -ForegroundColor Cyan

# 3. Tentukan versi dari CHANGELOG.md (baris pertama "## [x.y.z]"), fallback ke 0.0.0
$ProductVersion = "0.0.0"
$ChangelogPath = "$WorkspaceDir\CHANGELOG.md"
if (Test-Path $ChangelogPath) {
    $VersionLine = Select-String -Path $ChangelogPath -Pattern '^## \[(\d+\.\d+\.\d+)\]' | Select-Object -First 1
    if ($VersionLine) {
        $ProductVersion = $VersionLine.Matches[0].Groups[1].Value
    }
}
Write-Host "Versi produk terdeteksi: $ProductVersion" -ForegroundColor Cyan

# 4. Kompilasi installer.nsi -> dist\HisabWin_Installer.exe
Write-Host "`nMengompilasi installer.nsi..." -ForegroundColor Cyan

$NsisArgs = @(
    "/DSRC_DIR=$AppDir",
    "/DPRODUCT_VERSION=$ProductVersion",
    "$NsiScript"
)

Write-Host "Perintah yang dijalankan: $MakensisExe $NsisArgs" -ForegroundColor DarkGray
& $MakensisExe $NsisArgs

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n=== KOMPILASI BERHASIL! ===" -ForegroundColor Green
    Write-Host "File installer dapat ditemukan di: $DistDir\HisabWin_Installer.exe" -ForegroundColor Green
    Write-Host "Uninstaller (Uninstall.exe) dibuat otomatis oleh NSIS dan" -ForegroundColor Green
    Write-Host "ditempatkan di dalam folder instalasi saat user menjalankan installer." -ForegroundColor Green
} else {
    Write-Error "Proses kompilasi NSIS gagal!"
    exit 1
}
