@echo off
chcp 936 >nul
cd /d "%~dp0"
title C4EAI

:: 清理占用 8080 的旧进程（重复启动时避免端口冲突）
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080') do (
    taskkill /F /PID %%a 2>nul
)
timeout /t 2 /nobreak >nul

:: ===== 共享库（组内服务器）=====
:: C4EAI_HUB：共享库地址，供 shared_upload / shared_search 两个工具使用。
:: 已部署：YOUR-SERVER:8088（那台机器的 8080 被 Qwen llama-server 占用，故用 8088）
:: 共享库启用账号鉴权：在网页右上角点「登录」用共享库账号登录一次即可，
:: shared_upload / shared_search 会自动带身份，无需手动配 token。
rem set C4EAI_HUB=http://YOUR-SERVER:8088
:: set C4EAI_HUB_TOKEN=（备用：手动指定身份才填，一般用不到）

:: ===== 监听地址（2026-09-26 用户要求：与云端一样，局域网可访问）=====
:: C4EAI_HOST=0.0.0.0 → 监听所有网卡，同一个局域网内的手机/平板/别的电脑也能打开。
:: 程序默认是 127.0.0.1（只有本机能连）。只想本机用就删掉下面这行。
:: 注意：本地实例未启用登录（没设 C4EAI_AUTH），同网段的人可直接访问，
::       因此仅建议在可信网络（自家 WiFi）下这样用。
set C4EAI_HOST=0.0.0.0

:: 单窗口启动：服务日志就在本窗口；浏览器在服务就绪后自动打开（逻辑见 start.py）
python start.py

echo.
echo 服务已停止。若上方是报错信息，请整段截图反馈。
pause
