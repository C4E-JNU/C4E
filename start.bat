@echo off
chcp 936 >nul
cd /d "%~dp0"
title C4EAI

echo ========================================
echo   C4EAI - Launch Pad(本机 + 局域网)
echo ========================================
echo.

:: 清理占用 8080 的旧进程
echo [1/3] 清理旧进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080') do (
    taskkill /F /PID %%a 2>nul
)
timeout /t 2 /nobreak >nul

:: ===== 共享库（组内服务器）=====
:: C4EAI_HUB：共享库地址，供 shared_upload / shared_search 两个工具使用。
:: 已部署：<HUB-IP>:8088（那台机器的 8080 被 Qwen llama-server 占用，故用 8088）
:: 共享库启用账号鉴权：在网页右上角点「登录」用共享库账号登录一次即可，
:: shared_upload / shared_search 会自动带身份，无需手动配 token。
set C4EAI_HUB=http://<HUB-IP>:8088
:: set C4EAI_HUB_TOKEN=（备用：手动指定身份才填，一般用不到）

:: ===== 监听地址（2026-09-26 用户要求：与云端一样，局域网可访问）=====
:: C4EAI_HOST=0.0.0.0 → 监听所有网卡，同一局域网内的手机/平板/别的电脑也能打开。
:: 程序默认是 127.0.0.1（只有本机能连）。只想本机用就删掉下面这行。
:: 注意：本地实例未启用登录（没设 C4EAI_AUTH），同网段的人可直接访问，
::       因此仅建议在可信网络（自家 WiFi）下这样用。
set C4EAI_HOST=0.0.0.0

echo [2/3] 启动服务（端口 8080）...
start "C4EAI" cmd /c "python backend\app.py"
echo   首次启动约 20 秒（编译依赖，请等待）
timeout /t 25 /nobreak >nul

:: 取第一个 IPv4 作为局域网地址
set LANIP=
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /c:"IPv4"') do (
    if not defined LANIP set LANIP=%%i
)

echo [3/3] 打开浏览器...
start http://127.0.0.1:8080/

echo.
echo ========================================
echo   就绪！
echo   本机：    http://127.0.0.1:8080
echo   局域网：  http://%LANIP%:8080
echo             （上面为空或连不上时，用 ipconfig 查本机 IP；本机名：%COMPUTERNAME%）
echo   关闭本窗口即停止。
echo ========================================
echo.
pause
