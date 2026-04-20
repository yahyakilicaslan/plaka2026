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
    pause
    exit /b
)

echo [1/3] Backend (API) baslatiliyor...
start "LPR_BACKEND" cmd /k "chcp 65001 && venv\Scripts\activate && python backend\main.py"

timeout /t 3 >nul

echo [2/3] Yapay Zeka Motoru (LPR) baslatiliyor...
start "LPR_ENGINE" cmd /k "chcp 65001 && venv\Scripts\activate && python lpr_engine\detector.py"

timeout /t 4 >nul

echo [3/3] Tarayici (Dashboard) aciliyor...
start http://localhost:8000

echo.
echo ======================================================
echo [BASARILI] Sistem bilesenleri acildi ve tarayici baslatildi.
echo Bu ana pencereyi kapatabilirsiniz.
echo ======================================================
pause