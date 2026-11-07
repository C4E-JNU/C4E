@echo off
chcp 65001 >nul
cd /d "%~dp0"
title C4EAI

rem Prefer the bundled portable Python, then a local .venv, then system Python.
if exist "%~dp0python\python.exe" (
  set "PATH=%~dp0python;%~dp0python\Scripts;%PATH%"
) else if exist "%~dp0.venv\Scripts\python.exe" (
  set "PATH=%~dp0.venv\Scripts;%PATH%"
)

rem Stop an old instance on port 8080.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080') do taskkill /F /PID %%a >nul 2>nul
timeout /t 1 /nobreak >nul

rem Local-only by default. Set C4EAI_HOST=0.0.0.0 to allow LAN access.
if "%C4EAI_HOST%"=="" set "C4EAI_HOST=127.0.0.1"

python start.py
if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start. Run the launcher first to install the runtime.
  pause
)