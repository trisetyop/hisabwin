; ============================================================================
; HisabWin - NSIS Installer Script
; ----------------------------------------------------------------------------
; Menggantikan installer.py + uninstaller.py (wizard Tkinter kustom yang
; dibungkus PyInstaller --onefile lalu mengekstrak HisabWin.7z lewat 7z.exe
; saat runtime). Pendekatan lama itu rawan gagal dengan pesan
; "Failed to extract 7z.dll: decompression resulted in return code -1!"
; setiap kali bootloader PyInstaller onefile gagal mengekstrak dirinya
; sendiri (exe korup/download terputus/di-lock antivirus).
;
; NSIS membundel & mengekstrak file aplikasi sendiri (LZMA), jadi tidak ada
; lagi 7z.exe/7z.dll eksternal maupun proses ekstraksi dua tingkat.
;
; Build:
;   makensis /DSRC_DIR="dist\HisabWin" /DPRODUCT_VERSION="1.0.1" installer.nsi
;
; SRC_DIR harus menunjuk ke folder hasil `pyinstaller --onedir` untuk
; hisabwin.py (yang sudah berisi HisabWin.exe + seluruh data/dependensi).
; ============================================================================

!define PRODUCT_NAME "HisabWin"
!define PRODUCT_PUBLISHER "trisetyop"
!define PRODUCT_WEB_SITE "https://github.com/trisetyop/hisabwin"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"
!define APP_EXE "HisabWin.exe"

!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "1.0.1"
!endif

; Folder sumber hasil build PyInstaller --onedir. Bisa dioverride lewat
; /DSRC_DIR="path\ke\folder" saat memanggil makensis.
!ifndef SRC_DIR
  !define SRC_DIR "dist\HisabWin"
!endif

!include "MUI2.nsh"

; ----------------------------------------------------------------------------
; Info umum
; ----------------------------------------------------------------------------
Name "${PRODUCT_NAME}"
OutFile "dist\HisabWin_Installer.exe"
InstallDir "$LOCALAPPDATA\Programs\${PRODUCT_NAME}"
InstallDirRegKey HKCU "${PRODUCT_UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUnInstDetails show

VIProductVersion "${PRODUCT_VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "CompanyName" "${PRODUCT_PUBLISHER}"
VIAddVersionKey "FileDescription" "${PRODUCT_NAME} Installer"
VIAddVersionKey "FileVersion" "${PRODUCT_VERSION}"

; ----------------------------------------------------------------------------
; Tampilan (Modern UI 2) - meniru wizard installer.py: Welcome -> Directory
; + Shortcut options -> Progress -> Finish (dengan opsi "Jalankan Sekarang")
; ----------------------------------------------------------------------------
!define MUI_ICON "logo.ico"
!define MUI_UNICON "logo.ico"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Jalankan HisabWin Sekarang"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ----------------------------------------------------------------------------
; Deskripsi komponen (muncul di halaman Components)
; ----------------------------------------------------------------------------
LangString DESC_SecApp ${LANG_ENGLISH} "Berkas inti HisabWin (wajib)."
LangString DESC_SecDesktop ${LANG_ENGLISH} "Buat pintasan di Desktop."
LangString DESC_SecStartMenu ${LANG_ENGLISH} "Buat pintasan di Start Menu."

; ----------------------------------------------------------------------------
; Section: aplikasi utama (wajib)
; ----------------------------------------------------------------------------
Section "HisabWin (wajib)" SEC_APP
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "${SRC_DIR}\*.*"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegDWORD HKCU "${PRODUCT_UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${PRODUCT_UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Pintasan Desktop" SEC_DESKTOP
  CreateShortCut "$DESKTOP\HisabWin.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "Peta Visibilitas Hilal HisabWin"
SectionEnd

Section "Pintasan Start Menu" SEC_STARTMENU
  CreateShortCut "$SMPROGRAMS\HisabWin.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "Peta Visibilitas Hilal HisabWin"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_APP} $(DESC_SecApp)
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_DESKTOP} $(DESC_SecDesktop)
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTMENU} $(DESC_SecStartMenu)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ----------------------------------------------------------------------------
; Uninstall
; ----------------------------------------------------------------------------
Section "Uninstall"
  Delete "$DESKTOP\HisabWin.lnk"
  Delete "$SMPROGRAMS\HisabWin.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${PRODUCT_UNINST_KEY}"
SectionEnd
