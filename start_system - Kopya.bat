@echo off
title EVO TEKNOLOJI - LPR SISTEM BASLATICI
color 0B
echo [1/3] Kutuphaneler Kontrol Ediliyor...
pip install -r requirements.txt >nul 2>&1
echo [2/3] Klasorler Kontrol Ediliyor...
if not exist captured_images mkdir captured_images
echo [3/3] Sistem Baslatiliyor...
start "LPR BACKEND (API)" cmd /k "python backend/main.py"
timeout /t 3 >nul
start "LPR ENGINE (AI)" cmd /k "python lpr_engine/detector.py"
timeout /t 5 >nul
start http://localhost:8000
echo [BILGI] Sistem aktif.