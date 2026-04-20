@echo off
title EVO SMART - Minimal Kurulum

echo ================================
echo     EVO SMART MINIMAL INSTALLER
echo  Python 3.10.11 + VC++ 2015-2022
echo ================================
echo.

set DL=%cd%\downloads
if not exist "%DL%" mkdir "%DL%"
cd %DL%

echo [1/2] Python 3.10.11 indiriliyor...
bitsadmin /transfer pythonJob /download /priority normal https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe "%DL%\python.exe"

echo [2/2] VC++ 2015-2022 indiriliyor...
bitsadmin /transfer vc2022Job /download /priority normal https://aka.ms/vs/17/release/vc_redist.x64.exe "%DL%\vc2022.exe"

echo.
echo ======= KURULUM BASLIYOR =======
echo.

echo Python kuruluyor...
start /wait python.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0

echo VC++ 2015-2022 kuruluyor...
start /wait vc2022.exe /install /quiet /norestart

echo.
echo =================================
echo       KURULUM TAMAMLANDI!
echo =================================
echo Python versiyon:
python --version

pause
