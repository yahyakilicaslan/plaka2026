@echo off
cd /d "%~dp0"
chcp 65001 >nul
title EVO TEKNOLOJI - HIZLI BASLAT
color 0A
cls

echo ======================================================
echo   EVO SMART TEKNOLOJI - LPR HIZLI BASLATICI
echo ======================================================
echo.

if not exist venv\ (
    color 0C
    echo [HATA] Sanal ortam klasoru bulunamadi!
    echo Once debuglu_n.bat calistirin (ilk kurulum icin).
    pause
    exit /b
)

set LPR_API=http://127.0.0.1:8000

echo [1/4] Backend (API) baslatiliyor...
start "LPR_BACKEND" cmd /k "chcp 65001 >nul && venv\Scripts\activate && python backend\main.py"

timeout /t 4 >nul

echo [2/4] Yapay Zeka Motoru (LPR) baslatiliyor...
start "LPR_ENGINE" cmd /k "chcp 65001 >nul && venv\Scripts\activate && python lpr_engine\detector.py"

timeout /t 5 >nul

echo [3/4] Masaustu Kamera Monitoru baslatiliyor...
start "LPR_DESKTOP_CAMERA" cmd /k "chcp 65001 >nul && venv\Scripts\activate && set LPR_API=http://127.0.0.1:8000 && python desktop_camera.py"

timeout /t 2 >nul

echo [4/4] Dashboard (web) aciliyor...
start http://127.0.0.1:8000

echo.
echo ======================================================
echo [BASARILI] Tum sistem bilesenleri acildi.
echo - Backend API       : http://127.0.0.1:8000
echo - LPR Engine        : http://127.0.0.1:5001
echo - Dashboard (web)   : http://127.0.0.1:8000
echo - Kamera Monitoru   : Masaustu Uygulama
echo ======================================================
pause
