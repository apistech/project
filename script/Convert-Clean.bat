@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Convert ^& Clean IPTV - All in One

:: ========================================
:: CEK PowerShell
:: ========================================
where pwsh >nul 2>&1
if %errorlevel% neq 0 (
    echo PowerShell 7 tidak ditemukan.
    pause
    exit /b
)
set "PS=pwsh"

:: ========================================
:: CEK INPUT
:: ========================================
if "%~1"=="" (
    echo [ERROR] Tidak ada file untuk diproses.
    echo Drag-and-drop file .txt/.m3u/.m3u8
    pause
    exit /b
)

echo.
echo ======================================
echo    Convert ^& Clean - All in One
echo ======================================
echo.

:: ========================================
:: HITUNG JUMLAH FILE
:: ========================================
set "FILE_COUNT=0"
for %%a in (%*) do (
    set "ext=%%~xa"
    if /i "!ext!"==".m3u"  set /a FILE_COUNT+=1
    if /i "!ext!"==".m3u8" set /a FILE_COUNT+=1
)

:: ========================================
:: MENU INTERAKTIF
:: ========================================
set "DO_COMBINE=0"
if !FILE_COUNT! gtr 1 (
    echo [!FILE_COUNT! file M3U/M3U8 terdeteksi]
    set /p "DO_COMBINE=Gabungkan semua file menjadi satu playlist? (1=ya / 0=tidak) [default: 0]: "
    if "!DO_COMBINE!"=="" set "DO_COMBINE=0"
    echo.
)

set /p "DO_BACKUP=Backup file asli? (1=ya / 0=tidak) [default: 1]: "
if "!DO_BACKUP!"=="" set "DO_BACKUP=1"

set /p "DO_CHECK=Periksa URL live? (1=ya / 0=tidak) [default: 1]: "
if "!DO_CHECK!"=="" set "DO_CHECK=1"

:: ========================================
:: MENU SORTING
:: ========================================
:ask_sort
echo.
echo ==========================================
echo   Pilih mode sorting:
echo     [1] Group  - Urut berdasarkan group, lalu title (default)
echo     [2] None   - Tanpa sorting, urutan asli dipertahankan
echo ==========================================
echo.

set /p "SORT_MODE=Pilih angka (1-2) [default: 1]: "
if "!SORT_MODE!"=="" set "SORT_MODE=1"
if "!SORT_MODE!"=="1" goto :valid_sort
if "!SORT_MODE!"=="2" goto :valid_sort
echo [ERROR] Pilihan tidak valid. Gunakan angka 1-2.
goto :ask_sort

:valid_sort
set /p "TIMEOUT=Timeout (detik) [default: 8]: "
if "!TIMEOUT!"=="" set "TIMEOUT=8"

set /p "PARALLEL=Jumlah worker parallel [default: 32]: "
if "!PARALLEL!"=="" set "PARALLEL=32"

echo.
echo --------------------------------------
echo   Gabungkan file : !DO_COMBINE!
echo   Backup asli    : !DO_BACKUP!
echo   Periksa URL    : !DO_CHECK!
echo   Mode sorting   : !SORT_MODE! (1=Group, 2=None)
echo   Timeout        : !TIMEOUT!s
echo   Worker         : !PARALLEL!
echo --------------------------------------

:: ========================================
:: ROUTING
:: ========================================
if "!DO_COMBINE!"=="1" goto combine_mode
goto normal_mode

:: ========================================
:: MODE GABUNG FILE (HANYA .m3u/.m3u8)
:: ========================================
:combine_mode
echo.
echo [MODE GABUNG] Mengumpulkan file M3U/M3U8...

set "COMBINED_FILE=%~dp0playlist_combined.m3u"
set "FILE_LIST="
for %%a in (%*) do (
    set "ext=%%~xa"
    if /i "!ext!"==".m3u" (
        if exist "%%~fa" (
            echo   + %%~nxa
            if "!FILE_LIST!"=="" (
                set "FILE_LIST=%%~fa"
            ) else (
                set "FILE_LIST=!FILE_LIST!|%%~fa"
            )
        )
    ) else if /i "!ext!"==".m3u8" (
        if exist "%%~fa" (
            echo   + %%~nxa
            if "!FILE_LIST!"=="" (
                set "FILE_LIST=%%~fa"
            ) else (
                set "FILE_LIST=!FILE_LIST!|%%~fa"
            )
        )
    )
)

if "!FILE_LIST!"=="" (
    echo [ERROR] Tidak ada file .m3u/.m3u8 valid untuk digabung.
    goto done
)

echo.
echo [GABUNG] Menggabungkan dengan PowerShell...

%PS% -NoProfile -ExecutionPolicy Bypass ^
    -File "%~dp0Combine-M3U.ps1" ^
    -FileList "!FILE_LIST!" ^
    -OutFile "!COMBINED_FILE!"

if not exist "!COMBINED_FILE!" (
    echo [ERROR] Gagal membuat file gabungan.
    goto done
)

echo.
echo ==========================================
echo Memproses: playlist_combined.m3u
echo ==========================================

if "!DO_BACKUP!"=="1" (
    copy /y "!COMBINED_FILE!" "!COMBINED_FILE!.bak" >nul
    echo [BACKUP] playlist_combined.m3u.bak
)

%PS% -NoProfile -ExecutionPolicy Bypass ^
    -File "%~dp0Convert-Clean.ps1" ^
    -InputFile "!COMBINED_FILE!" ^
    -DoCheck !DO_CHECK! ^
    -SortMode !SORT_MODE! ^
    -TimeoutSec !TIMEOUT! ^
    -MaxParallel !PARALLEL!

if %errorlevel% neq 0 (
    echo [GAGAL] playlist_combined.m3u
) else (
    echo [BERHASIL] playlist_combined.m3u
)
goto done

:: ========================================
:: MODE NORMAL (PS1 akan deteksi sendiri jenis file)
:: ========================================
:normal_mode
if "%~1"=="" goto done

echo.
echo ==========================================
echo Memproses: %~nx1
echo ==========================================

if "!DO_BACKUP!"=="1" (
    copy /y "%~f1" "%~f1.bak" >nul
    echo [BACKUP] %~nx1.bak
)

%PS% -NoProfile -ExecutionPolicy Bypass ^
    -File "%~dp0Convert-Clean.ps1" ^
    -InputFile "%~f1" ^
    -DoCheck !DO_CHECK! ^
    -SortMode !SORT_MODE! ^
    -TimeoutSec !TIMEOUT! ^
    -MaxParallel !PARALLEL!

if %errorlevel% neq 0 (
    echo [GAGAL] %~nx1
) else (
    echo [BERHASIL] %~nx1
)

shift
goto normal_mode

:done
echo.
echo ==========================================
echo Semua file selesai diproses!
echo ==========================================
pause