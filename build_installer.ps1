# PowerShell script to build HisabWin Installer
# Usage: Run this script in PowerShell

Write-Host "=== Memulai Proses Kompilasi Installer HisabWin ===" -ForegroundColor Green

$WorkspaceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $WorkspaceDir) {
    $WorkspaceDir = (Get-Location).Path
}
$DistDir = "$WorkspaceDir\dist"
$ZipFile = "$DistDir\HisabWin.7z"

# 1. Pastikan file HisabWin.7z ada
if (-not (Test-Path $ZipFile)) {
    Write-Error "File HisabWin.7z tidak ditemukan di $DistDir! Pastikan Anda sudah membuat arsip 7z dari aplikasi utama."
    exit 1
}

# 2. Lokasi 7-Zip di system
$7zExe = "C:\Program Files\7-Zip\7z.exe"
$7zDll = "C:\Program Files\7-Zip\7z.dll"

if (-not (Test-Path $7zExe) -or -not (Test-Path $7zDll)) {
    Write-Error "7-Zip tidak ditemukan di $7zExe! Pustaka ini dibutuhkan untuk membundel extractor."
    exit 1
}

Write-Host "Menggunakan 7z dari: $7zExe" -ForegroundColor Cyan

# 3. Pastikan PyInstaller terinstal
$pyinstallerCheck = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstallerCheck) {
    Write-Host "PyInstaller tidak ditemukan di PATH, mencoba menjalankan lewat modul python..." -ForegroundColor Yellow
    python -m PyInstaller --version
    if ($LASTEXITCODE -ne 0) {
         Write-Error "PyInstaller tidak terinstal! Silakan instal terlebih dahulu dengan: pip install pyinstaller"
         exit 1
    }
    $pyinstallerCmd = "python"
    $pyinstallerArgs = @("-m", "PyInstaller")
} else {
    $pyinstallerCmd = "pyinstaller"
    $pyinstallerArgs = @()
}

# 4. Build uninstaller.py terlebih dahulu
$UninstallerScript = "$WorkspaceDir\uninstaller.py"
if (-not (Test-Path $UninstallerScript)) {
    Write-Error "File uninstaller.py tidak ditemukan di $WorkspaceDir!"
    exit 1
}

# Modul berat yang tidak dipakai HisabWin, dikecualikan supaya ukuran & waktu build
# lebih kecil/cepat. Dipakai bareng untuk build uninstaller maupun installer utama.
$ExcludeModules = @(
    "--exclude-module", "scipy",
    "--exclude-module", "pandas",
    "--exclude-module", "sqlalchemy",
    "--exclude-module", "psycopg2",
    "--exclude-module", "greenlet",
    "--exclude-module", "numba",
    "--exclude-module", "llvmlite",
    "--exclude-module", "astropy",
    "--exclude-module", "astropy_iers_data",
    "--exclude-module", "erfa",
    "--exclude-module", "Cython",
    "--exclude-module", "yaml",
    "--exclude-module", "IPython",
    "--exclude-module", "jupyter",
    "--exclude-module", "notebook"
)

Write-Host "`nMenjalankan PyInstaller untuk uninstaller..." -ForegroundColor Cyan

$UninstallerPyArgs = $pyinstallerArgs + @(
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "HisabWin_Uninstaller",
    "--icon", "$WorkspaceDir\logo.ico",
    "--add-data", "$WorkspaceDir\logo.png;.",
    "--add-data", "$WorkspaceDir\logo.ico;."
) + $ExcludeModules + @(
    "$UninstallerScript"
)

Write-Host "Perintah yang dijalankan: $pyinstallerCmd $UninstallerPyArgs" -ForegroundColor DarkGray
& $pyinstallerCmd $UninstallerPyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Error "Proses PyInstaller untuk uninstaller gagal!"
    exit 1
}

$UninstallerExe = "$DistDir\HisabWin_Uninstaller.exe"
if (-not (Test-Path $UninstallerExe)) {
    Write-Error "HisabWin_Uninstaller.exe tidak ditemukan di $DistDir setelah build!"
    exit 1
}

Write-Host "Uninstaller berhasil dibuat: $UninstallerExe" -ForegroundColor Green

# 5. Bundel uninstaller.exe ke dalam HisabWin.7z supaya ikut terekstrak
#    ke folder instalasi bersama HisabWin.exe (diberi nama tetap "Uninstall.exe"
#    di dalam arsip agar konsisten dengan yang dicari user).
Write-Host "Menambahkan uninstaller ke dalam HisabWin.7z..." -ForegroundColor Cyan

$UninstallerStaging = "$DistDir\_uninstaller_staging"
if (Test-Path $UninstallerStaging) {
    Remove-Item $UninstallerStaging -Recurse -Force
}
$AppFolderNameInArchive = "HisabWin"

# Cek dulu bahwa folder aplikasi itu memang ada di dalam arsip, sebelum kita
# asumsikan Uninstall.exe harus masuk ke dalamnya.
$PreCheckListing = & $7zExe l -slt $ZipFile
$AppFolderExists = $PreCheckListing | Select-String -Pattern "^Path = $([regex]::Escape($AppFolderNameInArchive))$"
if (-not $AppFolderExists) {
    Write-Error "Folder '$AppFolderNameInArchive' tidak ditemukan di root $ZipFile. Cek ulang nama folder aplikasi di dalam arsip (mis. lewat 7-Zip File Manager) dan sesuaikan variabel `$AppFolderNameInArchive di script ini."
    exit 1
}

$StagingAppFolder = "$UninstallerStaging\$AppFolderNameInArchive"
New-Item -ItemType Directory -Path $StagingAppFolder | Out-Null
Copy-Item $UninstallerExe "$StagingAppFolder\Uninstall.exe"

Push-Location $UninstallerStaging
& $7zExe a -y $ZipFile "$AppFolderNameInArchive\Uninstall.exe"
$7zExitCode = $LASTEXITCODE
Pop-Location

if ($7zExitCode -ne 0) {
    Write-Error "Gagal menambahkan Uninstall.exe ke dalam $ZipFile!"
    exit 1
}

Remove-Item $UninstallerStaging -Recurse -Force
Write-Host "Uninstall.exe berhasil ditambahkan ke dalam $ZipFile" -ForegroundColor Green

# Verifikasi: pastikan Uninstall.exe tersimpan tepat di dalam folder $AppFolderNameInArchive
# (bukan di root arsip / folder lain). Pakai mode -slt (technical listing) supaya
# path tiap entry gampang & aman di-parse.
$ListingOutput = & $7zExe l -slt $ZipFile
$PathLines = $ListingOutput | Select-String -Pattern '^Path = (.*Uninstall\.exe)$'

if ($PathLines.Count -eq 0) {
    Write-Error "Uninstall.exe tidak ditemukan di dalam $ZipFile setelah verifikasi!"
    exit 1
}

$UninstallPathInArchive = $PathLines[0].Matches[0].Groups[1].Value.Trim()
$ExpectedPathInArchive = "$AppFolderNameInArchive\Uninstall.exe"

if ($UninstallPathInArchive -ne $ExpectedPathInArchive) {
    Write-Error "Uninstall.exe tersimpan dengan path salah di dalam arsip: '$UninstallPathInArchive' (seharusnya '$ExpectedPathInArchive')."
    Write-Error "Ini akan menyebabkan Uninstall.exe terekstrak di LUAR folder $AppFolderNameInArchive, sejajar dengannya, bukan di dalamnya."
    exit 1
}

Write-Host "Verifikasi OK: Uninstall.exe berada di dalam '$AppFolderNameInArchive\' pada arsip $ZipFile" -ForegroundColor Green

# 6. Jalankan PyInstaller untuk installer utama
Write-Host "`nMenjalankan PyInstaller untuk installer..." -ForegroundColor Cyan

# Menyusun argumen PyInstaller
$PyArgs = $pyinstallerArgs + @(
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "HisabWin_Installer",
    "--icon", "$WorkspaceDir\logo.ico",
    "--add-data", "$7zExe;.",
    "--add-data", "$7zDll;.",
    "--add-data", "$ZipFile;.",
    "--add-data", "$WorkspaceDir\logo.png;.",
    "--add-data", "$WorkspaceDir\logo.ico;."
) + $ExcludeModules + @(
    "$WorkspaceDir\installer.py"
)

Write-Host "Perintah yang dijalankan: $pyinstallerCmd $PyArgs" -ForegroundColor DarkGray

# Jalankan perintah
& $pyinstallerCmd $PyArgs

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n=== KOMPILASI BERHASIL! ===" -ForegroundColor Green
    Write-Host "File installer dapat ditemukan di: $DistDir\HisabWin_Installer.exe" -ForegroundColor Green
    Write-Host "File uninstaller (standalone) dapat ditemukan di: $UninstallerExe" -ForegroundColor Green
    Write-Host "Uninstall.exe juga sudah dibundel di dalam HisabWin.7z, sehingga akan otomatis" -ForegroundColor Green
    Write-Host "ikut terekstrak ke folder instalasi saat pengguna menjalankan installer." -ForegroundColor Green
} else {
    Write-Error "Proses PyInstaller gagal!"
    exit 1
}