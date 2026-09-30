"""C4EAI 桌面版 —— 同一个后端，装进 Windows 原生窗口（WebView2），不依赖浏览器。

双击 C4EAI.exe 即启动：
  1. 后台线程起 uvicorn（与 start.py / 启动.bat 同一套配置，含 Windows 必需的 Selector 循环）
  2. 后端就绪后开原生窗口；远程访问隧道在后台连，不拖慢窗口出现
  3. 关掉窗口即退出（后端是守护线程，随之结束）

★ 起不来的时候不许"开个空白窗口糊弄"：真实原因写进 workspace/desktop.log 并弹窗说明
  （端口被占 / 依赖报错 / 超时）。曾经是静默等 60 秒后照开窗口，用户看到的是
  "127.0.0.1 拒绝连接"，完全不知道发生了什么 —— 这正是要避免的。

本地实例仍然只监听 127.0.0.1；手机/其他电脑走隧道（见 tunnel.py），互不影响。
"""

import os
import subprocess
import sys
import threading
import time
import traceback

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
LOG = os.path.join(HERE, "workspace", "desktop.log")

_backend_error = []          # 后端线程崩了就把 traceback 记这里，供失败提示用


def _ensure_streams() -> None:
    """★ pythonw 启动的进程没有控制台，sys.stdout/stderr 是 None。

    uvicorn 配置日志时会调 sys.stdout.isatty() → 直接 AttributeError 崩掉
    （"Unable to configure formatter 'default'"），后端根本起不来，而窗口只显示
    "127.0.0.1 拒绝连接"。所以这里把两个流接到日志文件上，既治好崩溃，也留住运行日志。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        sink = open(os.path.join(HERE, "workspace", "desktop.out.log"), "a",
                    encoding="utf-8", errors="replace")
    except Exception:
        sink = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


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


def port_owner(port: int) -> int:
    """占着这个端口的进程 PID（0 = 没人占）。用来分清"端口被别的程序占了"和"后端自己没起来"。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, errors="replace").stdout
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 5 and p[3] == "LISTENING" and p[1].endswith(":%d" % port):
                return int(p[4])
    except Exception:
        pass
    return 0


def serve() -> None:
    try:
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
    except BaseException:
        _backend_error.append(traceback.format_exc())
        log("后端启动失败：\n" + _backend_error[-1])
        raise


def _fatal(msg: str) -> None:
    """启动不了就把原因摆到人面前，而不是开个空白窗口。"""
    log("启动失败：" + msg)
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, f"{msg}\n\n详情见日志：\n{LOG}", "C4EAI 启动失败", 0x10)


def start_tunnel() -> None:
    """远程访问隧道（放后台：它连不连得上都不该拖慢窗口出现）。"""
    try:
        import tunnel
        if tunnel.start(PORT):
            log("远程访问隧道已连接：" + tunnel.public_url())
        else:
            log("远程访问隧道未启动（分发包默认关闭，或本目录缺 frpc）")
    except Exception:
        log("隧道启动异常：\n" + traceback.format_exc())


_win_holder = {}             # 窗口对象（_WinAPI 要用；窗口要先创建、API 再引用它）


class _WinAPI:
    """暴露给网页的窗口控制 —— js/window_controls.js 里的三个圆点调这三个方法。

    走我们自己的 js_api，而不是 pywebview 未公开的内置方法，行为可预期。
    """

    def minimize(self):
        w = _win_holder.get("w")
        if w:
            w.minimize()

    def maximize(self):
        w = _win_holder.get("w")
        if w:
            w.maximize()

    def restore(self):
        w = _win_holder.get("w")
        if w:
            w.restore()

    def close(self):
        """关窗口 = 退出整个应用（隧道在 webview.start 返回后收掉）。"""
        w = _win_holder.get("w")
        if w:
            w.destroy()


def _add_resize_frame() -> None:
    """★ 给无边框窗口加回「可调整边框」（WS_THICKFRAME）。

    pywebview 的 frameless 是把整个边框（FormBorderStyle=None）拿掉，代价是拖边缘改大小
    和 DWM 画的细边框/圆角一起没了 —— 看起来就像个没有边的方块。Codex / VS Code 这类
    Electron 应用并不是真无边框：它们去掉的是标题栏，保留 thickFrame（Electron 默认
    thickFrame=true，即 WS_THICKFRAME 还在）。所以这里照同样做法补回样式位：
    加 WS_THICKFRAME|WS_BORDER，但绝不加 WS_CAPTION（加了白色标题栏就回来了）。

    窗口是启动后几秒才出现的，所以这里按「本进程 PID + 标题 C4EAI」等它出现。
    """
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        pid = os.getpid()
        GWL_STYLE = -16
        WS_THICKFRAME, WS_BORDER, WS_CAPTION = 0x00040000, 0x00800000, 0x00C00000
        SWP = 0x0002 | 0x0001 | 0x0004 | 0x0020          # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED

        def my_window():
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def cb(h, _):
                p = wintypes.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(p))
                if p.value == pid and u.IsWindowVisible(h):
                    n = ctypes.create_unicode_buffer(256)
                    u.GetWindowTextW(h, n, 256)
                    if n.value == "C4EAI":
                        found.append(h)
                return True

            u.EnumWindows(cb, 0)
            return found[0] if found else 0

        h = 0
        for _ in range(60):                              # 最多等 30 秒
            h = my_window()
            if h:
                break
            time.sleep(0.5)
        if not h:
            log("加窗口边框：没等到窗口，跳过")
            return
        style = u.GetWindowLongW(h, GWL_STYLE)
        u.SetWindowLongW(h, GWL_STYLE, (style | WS_THICKFRAME | WS_BORDER) & ~WS_CAPTION)
        u.SetWindowPos(h, 0, 0, 0, 0, 0, SWP)
        log("已加回可调整边框（四边/四角可拖拽改大小）")
    except Exception:
        log("加窗口边框失败：\n" + traceback.format_exc())


def main() -> None:
    _ensure_streams()                 # pythonw 无控制台 → 先接好 stdout/stderr，否则 uvicorn 必崩
    started_here = False
    if backend_alive():
        log("已有后端在 %d 端口服务，直接开窗口" % PORT)
    else:
        own = port_owner(PORT)
        if own and own != os.getpid():
            _fatal("端口 %d 已被别的进程占用（PID %d），C4EAI 的后端起不来。\n"
                   "先关掉那个程序（或重启电脑），再双击 C4EAI。" % (PORT, own))
            return

        t0 = time.time()
        log("启动后端（端口 %d）" % PORT)
        threading.Thread(target=serve, daemon=True, name="c4eai-backend").start()
        started_here = True
        while time.time() - t0 < 180:
            if backend_alive():
                log("后端就绪，用时 %.1f 秒" % (time.time() - t0))
                break
            if _backend_error:
                break
            busy = port_owner(PORT)              # 起不来又冒出个占用者 = 端口被抢
            if busy and busy != os.getpid():
                break
            time.sleep(0.3)

    if not backend_alive():
        if _backend_error:
            _fatal("后端启动时报错：\n" + _backend_error[-1].strip().splitlines()[-1])
        else:
            own = port_owner(PORT)
            if own and own != os.getpid():
                _fatal("端口 %d 已被别的进程占用（PID %d），C4EAI 的后端起不来。\n"
                       "先关掉那个程序（或重启电脑），再双击 C4EAI。" % (PORT, own))
            else:
                _fatal("后端 180 秒内没有就绪。首次启动较慢（杀毒软件扫描、依赖首次加载），"
                       "也可能是依赖没装全。")
        return

    threading.Thread(target=start_tunnel, daemon=True, name="c4eai-tunnel").start()

    import webview

    # 顶栏（header）当窗口拖拽区：只有点在顶栏本身才拖动，里面的按钮/主题圆点照常可点
    webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True
    _w, _h = _win_size()
    # frameless：去掉系统那条白色标题栏 —— 顶栏就是应用自己的 header（logo/主题/数据库/窗口按钮同一条）
    win = webview.create_window("C4EAI", URL, width=_w, height=_h, min_size=(980, 640),
                                frameless=True, js_api=_WinAPI())
    _win_holder["w"] = win
    kw = {"private_mode": False, "storage_path": PROFILE_DIR}
    if os.path.isfile(ICO):
        kw["icon"] = ICO
    try:
        # 第一个参数是 GUI 起来后运行的函数（独立线程）→ 用它把可调整边框加回来
        webview.start(_add_resize_frame, **kw)
    except TypeError:
        # 老版本 pywebview 不认某些参数时，退回最小参数集（存储目录仍是默认值）
        webview.start()

    if started_here:
        try:
            import tunnel
            tunnel.stop()              # 关窗口 = 退出：隧道一起收掉
        except Exception:
            pass
        os._exit(0)                    # 守护线程里的 uvicorn 不阻塞退出


if __name__ == "__main__":
    main()
