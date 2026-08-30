@echo off
cd /d "%~dp0"
title C4EAI

echo ========================================
echo   C4EAI - Single Server Launcher
echo ========================================
echo.

:: Clean old processes on port 8080
echo [1/3] Clean old processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080') do (
    taskkill /F /PID %%a 2>nul
)
timeout /t 2 /nobreak >nul

:: Start unified server (port 8080, serves both frontend + API)
echo [2/3] Start Server (port 8080)...
start "C4EAI" cmd /c "python backend\app.py"
echo   First startup takes ~20s (compiling imports)
echo   Watch the new window for progress
timeout /t 25 /nobreak >nul

:: Open browser
echo [3/3] Opening browser...
start http://127.0.0.1:8080/

echo.
echo ========================================
echo   All ready!
echo   Open: http://127.0.0.1:8080
echo   Close this window to stop.
echo ========================================
echo.
pause
