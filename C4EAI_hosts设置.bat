@echo off
rem ================= C4EAI 内网域名一键配置 =================
rem 作用：把 c4eai / c4eai.lab 指向实验室服务器，供全组访问 C4EAI 与本地千问。
rem 用法：右键本文件 ->“以管理员身份运行”，跑完即可关闭。
set SERVER_IP=<HUB-IP>

net session >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo [!] 请以管理员身份运行本文件（右键 -> 以管理员身份运行）
    pause
    exit /b 1
)

set HOSTS=%SystemRoot%\System32\drivers\etc\hosts
findstr /i /c:"c4eai.lab" "%HOSTS%" >/dev/null 2>&1
if %errorlevel% equ 0 (
    echo [OK] hosts 中已有 c4eai 条目，跳过写入
) else (
    echo.>>"%HOSTS%"
    echo # C4EAI 实验室服务器>>"%HOSTS%"
    echo %SERVER_IP%  c4eai c4eai.lab>>"%HOSTS%"
    echo [OK] 已写入 hosts
)
ipconfig /flushdns >/dev/null 2>&1
echo.
echo 验证解析：
ping -n 1 c4eai.lab
echo.
echo 完成。浏览器访问 http://c4eai.lab:8088 测试即可。
pause
