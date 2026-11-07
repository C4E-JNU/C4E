@echo off
chcp 65001 >nul
cd /d "%~dp0"
title C4EAI

if exist "%~dp0python\python.exe" goto run
if exist "%~dp0.venv\Scripts\python.exe" goto run

echo.
echo C4EAI first run: downloading the portable runtime (about 200 MB)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Runtime setup failed. See the messages above.
  pause
  exit /b 1
)

:run
call "%~dp0start.bat"
