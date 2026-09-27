@echo off
chcp 936 >nul
cd /d "%~dp0"
title C4EAI 一键打包

echo ========================================================
echo    C4EAI 一键打包 —— 把当前代码打成最新安装包 exe
echo ========================================================
echo.
echo 用的 Python:
where python
echo.
echo  [1/3] 从当前项目目录取最新代码（不用先 git commit）
echo  [2/3] 注入共享库地址 + 包内容自检
echo  [3/3] 编译安装程序（约 3~6 分钟，进度会一行行刷出来）
echo.
echo --------------------------------------------------------
python _private\c4eai_release.py team --bump patch
if errorlevel 1 (
    echo.
    echo ========================================================
    echo  [失败] 打包没成功 —— 往上翻，找 "!!" 或 Traceback 的地方。
    echo  常见原因：<BUILD-DIR>\payload 被删了（要重跑 payload）
    echo           或 <BUILD-DIR>\inno\ISCC.exe 不在（要重跑 inno）
    echo ========================================================
    pause
    exit /b 1
)
echo.
echo ========================================================
echo  完成！以下文件可直接发给别人（双击安装、桌面出图标）：
echo --------------------------------------------------------
dir /b "dist\C4EAI-Setup-*.exe"
echo ========================================================
start "" explorer "%~dp0dist"
pause
