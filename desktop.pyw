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

import ctypes
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
URL = f"http://127.0.0.1:{PORT}/" + "?shell=desktop"   # 让前端知道自己跑在桌面窗口里（CSS 据此不做浏览器式响应式堆叠）
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
    """窗口尺寸跟着「屏幕可用区」走 —— 必须减掉任务栏，否则窗口会高过可用区。

    ★ 曾经用 GetSystemMetrics 拿整屏高度（720p 机器 = 720），按 0.88 算出来 633，
    再被 min_size 抬到 640：可用区其实只有约 672，窗口一旦被往下拖，底部的对话输入区
    就落到屏幕外面 —— 用户看到的就是"缩小窗口后对话区域消失"。这里改用
    SystemParametersInfo(SPI_GETWORKAREA) 取真正的可用区，四边都保证在屏幕里。
    """
    import ctypes

    left, top, right, bottom = _work_area()
    return (min(1380, int((right - left) * 0.92)), min(900, int((bottom - top) * 0.94)))


def backend_alive() -> bool:
    """本机后端是否已在服务（已开着就直接开窗口，不再起第二个进程抢端口）。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


# ★ CREATE_NO_WINDOW：不加的话每调一次 netstat/tasklist 就闪一个黑窗。启动轮询里
#   port_owner() 是每 0.3 秒一次的 → 用户看到的就是"cmd 一直闪"（实测就是这个原因）。
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def port_owner(port: int) -> int:
    """占着这个端口的进程 PID（0 = 没人占）。用来分清"端口被别的程序占了"和"后端自己没起来"。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, errors="replace", creationflags=_NO_WINDOW).stdout
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


def watch_backend() -> None:
    """后端掉线时在日志里留一行。

    ★ 为什么要有它：窗口里的页面是 WebView2 自己渲染的，后端要是中途不响应，用户看到的
    就是浏览器那句"无法访问"，而日志里空白一片 —— 事后完全无从判断（这次就是这样）。
    每 15 秒探一次健康接口，只在"从有到无"那一次记一行，避免刷屏。
    """
    while True:
        time.sleep(15)
        if backend_alive():
            continue
        log("后端失去响应（页面会显示「无法访问」，可直接关掉窗口重开）")
        while not backend_alive():
            time.sleep(15)
        log("后端恢复响应")


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

    def start_drag(self):
        """顶栏按下：记下「光标 - 窗口左上角」的偏移。

        ★ 为什么不用 pywebview 的 drag-region，也不用 WM_NCLBUTTONDOWN：前者在 Windows
        后端对"后加类名的容器"不生效（顶栏拖不动）；后者会进入系统的模态移动循环，
        拖动期间的鼠标消息不再回给页面（测试和自绘都不可控）。这里由页面报告"鼠标动了"，
        后端用 GetCursorPos 自己算窗口位置 —— 完全可控，也不吃消息循环。
        """
        h = _win_holder.get("h")
        if not h:
            return
        u = ctypes.windll.user32
        pt, r = _POINT(), _RECT()
        u.GetCursorPos(ctypes.byref(pt))
        u.GetWindowRect(h, ctypes.byref(r))      # argtypes 绑的是模块级 _RECT，别用局部类
        _win_holder["drag_offset"] = (pt.x - r.left, pt.y - r.top)

    def drag_move(self):
        """拖动中：把窗口摆到「光标 - 偏移」的位置（尺寸不变、不抢焦点）。"""
        h, off = _win_holder.get("h"), _win_holder.get("drag_offset")
        if not (h and off):
            return
        u = ctypes.windll.user32
        pt = _POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetWindowPos(h, 0, pt.x - off[0], pt.y - off[1], 0, 0, 0x0001 | 0x0004 | 0x0010)

    def end_drag(self):
        """松开鼠标：光标贴在屏幕左/右边缘 → 分成左/右半屏；贴在顶边 → 最大化。

        ★ 为什么得自己做：拖动是自研的（页面报 mousemove、后端 SetWindowPos），
          没有进系统的模态移动循环，所以 Windows 的 Aero Snap 不会自己触发。
        """
        h, off = _win_holder.get("h"), _win_holder.pop("drag_offset", None)
        if not h or not off:
            return
        import ctypes

        u = ctypes.windll.user32
        pt = _POINT()
        u.GetCursorPos(ctypes.byref(pt))
        wa = _work_area(h)
        if not wa:
            return
        wl, wt, wr, wb = wa                 # 可用区 (left, top, right, bottom)
        EDGE_HIT = 12                       # 离边缘多少像素算"贴边"
        w, hh = wr - wl, wb - wt
        if pt.y - wt <= 4 and wl <= pt.x <= wr:
            u.ShowWindow(h, 3)              # SW_MAXIMIZE
            return
        if pt.x - wl <= EDGE_HIT:
            u.SetWindowPos(h, 0, wl, wt, w // 2, hh, 0x0004 | 0x0010)            # 左半屏
        elif wr - pt.x <= EDGE_HIT:
            u.SetWindowPos(h, 0, wl + w // 2, wt, w // 2, hh, 0x0004 | 0x0010)   # 右半屏


class _RECT(ctypes.Structure):                       # noqa: N801 —— 与 Win32 RECT 同名对齐
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def _work_area(h=None) -> "tuple[int, int, int, int]":
    """可用区 (left, top, right, bottom) —— 窗口所在显示器的 rcWork（任务栏已扣掉）。

    ★ 别用 SPI_GETWORKAREA：多显示器下它给的是整个虚拟桌面（本机实测 1895 宽，
    而真实主屏只有 1280），拿它算窗口尺寸会把窗口开得比屏幕还大。
    """
    import ctypes

    u = ctypes.windll.user32
    u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
    u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.MonitorFromWindow.restype = ctypes.c_void_p
    # h=None 时 MonitorFromWindow(NULL, MONITOR_DEFAULTTOPRIMARY) 就是主显示器
    mon = u.MonitorFromWindow(h or 0, 2 if h else 1)
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    u.GetMonitorInfoW(mon, ctypes.byref(mi))
    r = mi.rcWork
    return (r.left, r.top, r.right, r.bottom)


_win_holders: "list" = []      # ★ 必须留着回调引用：WNDPROC 被 GC 回收会直接崩进程


def _install_frame_proc(h) -> None:
    """接管窗口过程：客户区铺满整个窗口，四边/四角自己给拖拽热区。

    ★ 为什么必须这么干（上一版只加 WS_THICKFRAME 是不够的）：
      只把样式位加回去，Windows 会把顶部当成"活动标题栏区域"整条上色 —— 顶带比左右下
      明显粗，四边不等厚。Hermes / Codex / VS Code 的做法是接管 WM_NCCALCSIZE：返回时
      客户区 = 整个窗口（不留标题栏高度），顶部那条粗带就没有了；DWM 依然按 WS_THICKFRAME
      画细边、圆角、阴影。非客户区没了，拖拽热区就得自己在 WM_NCHITTEST 里给。
    """
    import ctypes
    from ctypes import wintypes

    GWLP_WNDPROC = -4
    WM_NCCALCSIZE, WM_NCHITTEST = 0x0083, 0x0084
    (HTCLIENT, HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT,
     HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT) = 1, 10, 11, 12, 13, 14, 15, 16, 17
    # ★ 用户报「四个角的可调整区域太小，很难点出双箭头」→ 边 8px、角 18px。
    #   角必须比边明显大：斜向改尺寸只在角的方形区域里才认得出。
    BORDER, CORNER = 8, 18
    LRESULT, WPARAM, LPARAM = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t

    class NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", _RECT * 3), ("lppos", ctypes.c_void_p)]

    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    u.SetWindowLongPtrW.restype = ctypes.c_void_p
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    u.CallWindowProcW.restype = LRESULT
    u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM]

    _hit_debug = []

    def proc(hwnd, msg, wparam, lparam):
        if msg == WM_NCCALCSIZE and wparam:
            # ★ 顶部那条粗带的根源就在这里：默认算法会把"标题栏高度"算进顶部非客户区。
            #   这里自己给客户区：四边各留 1px 做 DWM 的细边，其余全给内容 —— 四边就等厚了。
            if u.IsZoomed(hwnd):
                return 0                 # 最大化时不留边（否则四周露出 1px 缝）
            params = ctypes.cast(lparam, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
            params.rgrc[0].left += 1
            params.rgrc[0].top += 1
            params.rgrc[0].right -= 1
            params.rgrc[0].bottom -= 1
            return 0
        if msg == WM_NCHITTEST:
            if len(_hit_debug) < 10:
                _hit_debug.append(1)
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            r = _RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            dl, dr, dt, db = x - r.left, r.right - x, y - r.top, r.bottom - y
            # 先按「角」（半径 CORNER 的方形区）判，再按「边」（BORDER 宽）判
            side_c = HTLEFT if dl < CORNER else (HTRIGHT if dr < CORNER else None)
            edge_c = HTTOP if dt < CORNER else (HTBOTTOM if db < CORNER else None)
            if side_c and edge_c:
                res = {(HTLEFT, HTTOP): HTTOPLEFT, (HTLEFT, HTBOTTOM): HTBOTTOMLEFT,
                       (HTRIGHT, HTTOP): HTTOPRIGHT, (HTRIGHT, HTBOTTOM): HTBOTTOMRIGHT}[(side_c, edge_c)]
                if len(_hit_debug) < 10:
                    _hit_debug.append(1)
                    log("角命中 dl=%d dt=%d → 返回 %d" % (dl, dt, res))
                return res
            if dl < BORDER or dr < BORDER or dt < BORDER or db < BORDER:
                res = (HTLEFT if dl < BORDER else HTRIGHT if dr < BORDER else None) or \
                      (HTTOP if dt < BORDER else HTBOTTOM if db < BORDER else None) or HTCLIENT
                if len(_hit_debug) < 10:
                    _hit_debug.append(1)
                    log("边命中 dl=%d dr=%d dt=%d db=%d → 返回 %d" % (dl, dr, dt, db, res))
                return res
        return u.CallWindowProcW(old_proc, hwnd, msg, wparam, lparam)

    cb = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM)(proc)
    old_proc = u.SetWindowLongPtrW(h, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
    _win_holders.append(cb)
    _win_holder["h"] = int(h)
    log("已接管窗口过程（四边等厚；边 %d px / 角 %d px）" % (BORDER, CORNER))


def _fit_on_screen(h) -> None:
    """把窗口收进屏幕可用区 —— Windows 会记住上次的窗口位置，可能开在屏幕外面。

    ★ 桌面端最要命的"看不见"就是这个：窗口底边落到屏幕外，底部的对话输入区就再也够不着
    （720p 的屏 + 记住的位置，实测少掉 70 多像素）。这里只做一件事：四边都不许出可用区。
    """
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    cur = _RECT()
    u.GetWindowRect(h, ctypes.byref(cur))
    wl, wt, wr, wb = _work_area(h)
    avail_w, avail_h = wr - wl, wb - wt
    w, hgt = min(cur.right - cur.left, avail_w), min(cur.bottom - cur.top, avail_h)
    x = min(max(cur.left, wl), max(wl, wr - w))
    y = min(max(cur.top, wt), max(wt, wb - hgt))
    if (x, y, w, hgt) == (cur.left, cur.top, cur.right - cur.left, cur.bottom - cur.top):
        return
    u.SetWindowPos(h, 0, x, y, w, hgt, 0x0004 | 0x0010)           # SWP_NOZORDER|SWP_NOACTIVATE
    log("窗口原先超出屏幕可用区，已收回 %d,%d %dx%d" % (x, y, w, hgt))


def _set_window_icon(h) -> None:
    """把窗口图标设成 C4EAI 的图标（任务栏 / Alt+Tab 都用它）。

    ★ 为什么不用 pywebview 的 start(icon=...)：它在 Windows 后端靠 Pillow 读 .ico，
    本机没装 Pillow 时**静默跳过**（实测窗口图标句柄是 0 → 任务栏显示 pythonw 的图标）。
    这里直接调 Win32，绕开这个依赖。
    ★ 必须声明 argtypes：不声明时 ctypes 把 HWND 按 32 位传，句柄一截断就静默失效。
    """
    import ctypes
    from ctypes import wintypes

    try:
        u = ctypes.windll.user32
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE, WM_SETICON = 1, 0x0010, 0x0040, 0x0080
        big = u.LoadImageW(None, ICO, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        small = u.LoadImageW(None, ICO, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if not (big or small):
            log("加载图标失败（文件在不在？）：%s" % ICO)
            return
        u.SendMessageW(h, WM_SETICON, 1, big or small)      # ICON_BIG
        u.SendMessageW(h, WM_SETICON, 0, small or big)      # ICON_SMALL
        log("窗口图标已设置")
    except Exception:
        log("设置窗口图标异常：\n" + traceback.format_exc())


def _set_frame_color(h) -> None:
    """把窗口边框/标题栏的颜色改成和界面一致的浅色。

    ★ 为什么：加了可调整边框之后，Windows 会把自己那套「强调色」（用户机器上是青蓝）
    画上去 —— 顶部那条特别显眼，因为顶部被当成活动标题栏区域按整条上色，左右下只有
    很细一条。这个厚度改不掉（除非自己画非客户区），但颜色可以指定：设成和界面同色，
    那条蓝条就看不见了。

    ★ 必须声明 argtypes：不声明的话 ctypes 把 HWND 按 32 位传，DwmSetWindowAttribute
    收到截断的句柄会直接失败（返回非 0，什么都不做，且不抛异常）。
    """
    import ctypes
    from ctypes import wintypes

    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_uint,
                                              ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR = 34, 35
        light = ctypes.c_uint(0x00F2F2F2)          # COLORREF = 0x00BBGGRR → 浅灰（贴合浅色界面）
        for attr in (DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR):
            hr = dwm.DwmSetWindowAttribute(h, attr, ctypes.byref(light), 4)
            if hr != 0:
                log("设置窗口边框色失败：属性 %d 返回 0x%08X" % (attr, hr & 0xFFFFFFFF))
    except Exception:
        log("设置窗口边框色异常：\n" + traceback.format_exc())


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
                # ★ 不能要求窗口"可见"：窗口是 hidden=True 建的，chrome 上完才 show()。
                #   以前这里是 pid and IsWindowVisible → 隐藏窗永远找不到 → 边框上不去、
                #   show() 也不会被调用，结果窗口根本不显示。
                if p.value == pid:
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

        _install_frame_proc(h)
        _set_window_icon(h)
        _set_frame_color(h)
        _fit_on_screen(h)
        # 强制按新样式重算边框并立即重绘，然后才让窗口显示 —— 首帧就是最终样子
        u.SetWindowPos(h, 0, 0, 0, 0, 0, SWP | 0x0010)          # + SWP_NOACTIVATE
        u.RedrawWindow(h, None, None, 0x0001 | 0x0080 | 0x0100 | 0x0400)   # FRAME|UPDATENOW|ALLCHILDREN
        w = _win_holder.get("w")
        if w:
            w.show()
            # ★ pywebview 只在"窗口可见"时注入 JS 桥（window.pywebview）—— 隐藏建窗的话
            #   桥永远不注入，窗口按钮/拖动全哑。所以 show() 之后重载一次页面，
            #   让页面在可见状态下重新加载 → 桥注入。代价是启动时多一次页面加载。
            w.load_url(URL)
        log("已加回可调整边框（四边一样细 + 圆角，四边/四角可拖拽改大小）")
    except Exception:
        log("加窗口边框失败：\n" + traceback.format_exc())


APP_ID = "C4EAI.Researchers.JNU.C4EAI"     # 任务栏身份：不设的话窗口会被归到 pythonw.exe 名下


def _set_app_id() -> None:
    """告诉 Windows 这是独立应用（任务栏图标/分组才不会用 python 的）。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def main() -> None:
    _ensure_streams()
    _set_app_id()                 # pythonw 无控制台 → 先接好 stdout/stderr，否则 uvicorn 必崩
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
    threading.Thread(target=watch_backend, daemon=True, name="c4eai-watch").start()

    import webview

    # 顶栏（header）当窗口拖拽区：只有点在顶栏本身才拖动，里面的按钮/主题圆点照常可点
    webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True
    # ★ 最小尺寸必须明显小于"屏幕一半"，否则 Windows 分屏（半屏 640px）会被挡住 ——
    #   用户报「无法使用 windows 分屏功能」就是这个原因。前端配套：窗口变窄到放不下
    #   "侧栏最小 + 对话区最小"时自动收起侧栏（js/sidebar.js）。
    _w, _h = _win_size()
    # frameless：去掉系统那条白色标题栏 —— 顶栏就是应用自己的 header（logo/主题/数据库/窗口按钮同一条）
    # ★ hidden=True：窗口先不显示 —— 边框/图标/颜色这些 chrome 是窗口出现之后才改的，
    #   先亮出来的话首帧带着那条"标题栏高度的粗条"，要等用户操作触发重绘才消失。
    #   现在流程改成：建隐藏窗 → 上 chrome → 强制重算边框 → show()。
    win = webview.create_window("C4EAI", URL, width=_w, height=_h, min_size=(420, 340),
                                frameless=True, hidden=True, js_api=_WinAPI())
    _win_holder["w"] = win
    kw = {"private_mode": False, "storage_path": PROFILE_DIR}
    # 图标不走 pywebview 的 icon= —— Windows 后端靠 Pillow 读 ico，没装 Pillow 就静默跳过。
    # 统一由 _set_window_icon() 用 Win32 设（见那里的说明）。
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
