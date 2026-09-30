"""C4EAI 桌面版 —— 同一个后端，装进 Windows 原生窗口（WebView2），不依赖浏览器。

双击桌面图标即启动：
  1. 后台线程起 uvicorn（与 start.py / 启动.bat 同一套配置，含 Windows 必需的 Selector 循环）
  2. 前台开一个原生窗口指向本机后端
  3. 关掉窗口即退出（后端是守护线程，随之结束）

本地实例仍然只监听 127.0.0.1；手机/其他电脑走隧道（见 tunnel.py），互不影响。
"""

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backend"))

PORT = int(os.environ.get("C4EAI_PORT", "8080"))
HOST = os.environ.get("C4EAI_HOST", "127.0.0.1")
URL = f"http://127.0.0.1:{PORT}/"
ICO = os.path.join(HERE, "assets", "icons", "c4eai.ico")
if not os.path.isfile(ICO):
    ICO = os.path.join(HERE, "c4eai.ico")
# WebView2 的存储目录必须持久化：模型服务商的密钥、界面偏好都存在浏览器本地存储里，
# 用临时目录的话每次启动都会变回空白（要重新填 key）。
PROFILE_DIR = os.path.join(HERE, "workspace", "desktop_profile")


def _win_size() -> "tuple[int, int]":
    """窗口尺寸跟着屏幕走：1080p 用 1380×900，小屏按比例缩，最小 980×640。"""
    import ctypes

    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    return (max(980, min(1380, int(screen_w * 0.86))),
            max(640, min(900, int(screen_h * 0.88))))


def backend_alive() -> bool:
    """本机后端是否已在服务（已开着就直接开窗口，不再起第二个进程抢端口）。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def serve() -> None:
    import asyncio

    import uvicorn

    from app import app  # backend/app.py 里的 FastAPI 应用

    # ★ Windows 必须跑 Selector 事件循环：uvicorn 硬编码 Proactor，而 Proactor 的
    #   accept 出错处理有 CPython bug（gh-156365），会把监听 socket 关掉且不再重挂，
    #   服务从此静默不再接受连接。loop="none" 才交给事件循环策略决定。
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning",
                loop="none" if sys.platform == "win32" else "auto")


def main() -> None:
    started_here = False
    if not backend_alive():
        threading.Thread(target=serve, daemon=True, name="c4eai-backend").start()
        started_here = True
        for _ in range(240):          # 最多等 60 秒（首次运行要编译依赖）
            if backend_alive():
                break
            time.sleep(0.25)

    import tunnel                     # 远程访问隧道：本机连出到服务器，给手机一个固定地址
    if tunnel.start(PORT):
        print("远程访问：", tunnel.public_url())

    import webview

    _w, _h = _win_size()
    webview.create_window("C4EAI", URL, width=_w, height=_h, min_size=(980, 640))
    kw = {"private_mode": False, "storage_path": PROFILE_DIR}
    if os.path.isfile(ICO):
        kw["icon"] = ICO
    try:
        webview.start(**kw)
    except TypeError:
        # 老版本 pywebview 不认某些参数时，退回最小参数集（存储目录仍是默认值）
        webview.start()

    if started_here:
        tunnel.stop()                  # 关窗口 = 退出：隧道一起收掉
        os._exit(0)                    # 守护线程里的 uvicorn 不阻塞退出


if __name__ == "__main__":
    main()
