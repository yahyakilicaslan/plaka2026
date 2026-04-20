@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title EVO TEKNOLOJI - LPR SISTEM BASLATICI (VENV MODU)
color 0B
cls

echo ======================================================
echo   EVO SMART TEKNOLOJI - SISTEM KURULUM VE BASLATMA
echo ======================================================
echo.

:: ADIM 1: Python Kontrolü
echo [1/6] Python surumu kontrol ediliyor...
py --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [HATA] Python yuklu degil veya PATH'e eklenmemis!
    echo https://www.python.org adresinden Python 3.10+ kurun.
    pause
    exit /b
)
echo Python bulundu.
echo.

:: ADIM 2: Sanal Ortam (venv) Kurulumu ve Aktivasyonu
echo [2/6] Sanal ortam (venv) kontrol ediliyor...
if not exist venv\ (
    echo [BILGI] Sanal ortam bulunamadi. Sifirdan olusturuluyor...
    py -m venv venv
    if errorlevel 1 (
        color 0C
        echo [HATA] Sanal ortam olusturulamadi!
        pause
        exit /b
    )
    echo [BASARILI] Sanal ortam olusturuldu.
) else (
    echo [BILGI] Sanal ortam zaten mevcut.
)

:: Ana pencere icin sanal ortami aktif et (Kurulumlar icin gerekli)
echo [BILGI] Sanal ortam aktif ediliyor...
call venv\Scripts\activate
echo.

:: ADIM 3: requirements.txt Kontrolü ve Kutuphane Kurulumu
echo [3/6] Gerekli kutuphaneler kontrol ediliyor (requirements.txt)...
echo ------------------------------------------------------
if not exist requirements.txt (
    color 0C
    echo [HATA] requirements.txt bulunamadi!
    echo Bat dosyasi ile ayni klasorde olmali.
    pause
    exit /b
)

:: Venv icindeki pip'i guncelle ve kutuphaneleri kur
py -m pip install --upgrade pip >nul 2>&1
py -m pip install -r requirements.txt

if errorlevel 1 (
    color 0C
    echo.
    echo ======================================================
    echo [KRITIK HATA] Kutuphaneler yuklenemedi!
    echo ======================================================
    echo GERCEKCI Sebepler:
    echo - Internet yok
    echo - Python surumu uyumsuz
    echo - requirements.txt icinde gecersiz paket var
    pause
    exit /b
)

echo.
echo [BASARILI] Tum kutuphaneler hazir (Sanal Ortam icinde).
echo ------------------------------------------------------
echo.

:: ADIM 4: Klasor Kontrolu
echo [4/6] Klasorler kontrol ediliyor...
if not exist captured_images (
    mkdir captured_images
    echo [BILGI] captured_images olusturuldu.
) else (
    echo [BILGI] captured_images zaten mevcut.
)
echo.

:: ADIM 5: Sistem Baslatma
echo [5/6] Sistem baslatiliyor...
echo.

:: NOT: Yeni acilan pencerelerde sanal ortamin aktif olmasi icin 
:: "venv\Scripts\activate && python ..." seklinde baslatiyoruz.

if exist backend\main.py (
    echo - Backend baslatiliyor...
    start "LPR BACKEND (API)" cmd /k "chcp 65001 >nul && venv\Scripts\activate && python backend\main.py"
) else (
    color 0E
    echo [UYARI] backend\main.py bulunamadi!
)

timeout /t 3 >nul

if exist lpr_engine\detector.py (
    echo - Yapay Zeka Motoru baslatiliyor...
    start "LPR ENGINE (AI)" cmd /k "chcp 65001 >nul && venv\Scripts\activate && python lpr_engine\detector.py"
) else (
    color 0E
    echo [UYARI] lpr_engine\detector.py bulunamadi!
)

timeout /t 4 >nul

if exist desktop_camera.py (
    echo - Masaustu Kamera Monitoru baslatiliyor...
    start "LPR DESKTOP CAMERA" cmd /k "chcp 65001 >nul && venv\Scripts\activate && python desktop_camera.py"
) else (
    color 0E
    echo [UYARI] desktop_camera.py bulunamadi!
)

timeout /t 3 >nul

echo [6/6] Tarayici aciliyor...
start http://localhost:8000

echo.
echo ======================================================
echo [BILGI] Sistem calisiyor. Bu pencereyi kapatmayin.
echo   - Dashboard  : http://localhost:8000
echo   - LPR Engine : http://localhost:5001
echo   - Kamera     : Masaustu uygulama (2x2 canli grid)
echo ======================================================
pause
