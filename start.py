# -*- coding: utf-8 -*-
"""C4EAI 本地启动器（单窗口）。

为什么需要它：以前 start.bat 用 `start "C4EAI" cmd /c "python backend\\app.py"`
另开一个窗口跑服务，于是每次启动弹两个 CMD，很繁琐（用户 2026-09-26 反馈）。

现在由本脚本启动后端本体，服务日志就在**同一个**窗口里，并且：
  · 浏览器在服务**真正就绪后**才打开 —— 不再靠 sleep 猜 20 秒
    （首次启动要编译依赖，实测导入 app.py 约 18 秒）
  · 自动探测局域网地址并打印，手机/别的电脑照着敲即可
  · 关掉这个窗口（或 Ctrl+C）= 停止服务

用法：python start.py        （由 start.bat 调用，也可单独运行）
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("C4EAI_PORT", "8080"))          # 与 backend/app.py 的默认值一致
HOST = os.environ.get("C4EAI_HOST", "127.0.0.1")


def lan_ip() -> str:
    """本机在局域网里的地址。

    用一个 UDP connect 让系统按默认路由选出出网网卡（不发送任何数据）。
    比解析 ipconfig 可靠：不会挑到 VMware/VirtualBox 的虚拟网卡。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def wait_port(port: int = PORT, timeout: float = 120.0) -> bool:
    """等端口可连上 —— 即服务真正就绪。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def _open_when_ready(url: str, port: int = PORT) -> None:
    if wait_port(port):
        webbrowser.open(url)


def main() -> int:
    os.chdir(ROOT)
    local = f"http://127.0.0.1:{PORT}/"
    print("=" * 56)
    print("  C4EAI 启动中（首次启动需编译依赖，约 20 秒）...")
    if HOST == "0.0.0.0":
        ip = lan_ip()
        print(f"  本机：   {local}")
        print(f"  局域网： {('http://' + ip + f':{PORT}/') if ip else '(未探测到，请用 ipconfig 查本机 IP)'}")
    else:
        print(f"  {local}")
    print("  关闭本窗口即停止服务")
    print("=" * 56)
    threading.Thread(target=_open_when_ready, args=(local,), daemon=True).start()
    # 同一个控制台里跑后端本体：日志直接显示在这里，全程只有一个窗口
    return subprocess.call([sys.executable, os.path.join("backend", "app.py")])


if __name__ == "__main__":
    sys.exit(main())
