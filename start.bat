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
:: C4EAI_HUB：组内共�服务�（局域网 C4EAI 实例）地�，供 shared_upload / shared_search 两个工具使用�
:: 已部署：<HUB-IP>:8088（�机� 8080 � Qwen llama-server 占用，故� 8088�
:: 共享库已�用账号鉴权：在本地网页右上��点击登录�用共享库账号登录一次即��
:: shared_upload / shared_search �动带�份，无需手动配置 token�
set C4EAI_HUB=http://<HUB-IP>:8088
:: set C4EAI_HUB_TOKEN=（�用：手动指定身份才�，一�用不到）
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
