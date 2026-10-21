# -*- coding: utf-8 -*-
"""生成 runtime\\C4EAI.exe —— 本机 Python 的「换了身份」的副本（公开版，随包发布）。

为什么需要它：Windows 任务栏（右键菜单/固定项/分组标题）显示的名字取自 **exe 的版本资源**，
而 pythonw.exe 里写的是 FileDescription = "Python" —— 直接用它启动，任务栏永远显示 "Python"。
办法：复制 pythonw.exe 并删掉版本资源，Windows 退回用文件名显示 → 显示 C4EAI。

用法：
    python scripts/make_runtime.py [python目录] [输出目录]
        默认  D:\\installed\\anaconda  →  <项目根>\\runtime

launcher_dev（开发版启动器）在 runtime\\C4EAI.exe 缺失时会自动调用本脚本自愈。
"""
import os
import shutil
import sys
import ctypes
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

k32 = ctypes.windll.kernel32
k32.LoadLibraryExW.restype = ctypes.c_void_p
k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD]
k32.BeginUpdateResourceW.restype = ctypes.c_void_p
k32.BeginUpdateResourceW.argtypes = [ctypes.c_void_p, wintypes.BOOL]
k32.UpdateResourceW.restype = wintypes.BOOL
k32.UpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                wintypes.WORD, ctypes.c_void_p, ctypes.c_uint]
k32.EndUpdateResourceW.restype = wintypes.BOOL
k32.EndUpdateResourceW.argtypes = [ctypes.c_void_p, wintypes.BOOL]
_CB4 = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_CB5 = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.WORD, ctypes.c_void_p)
k32.EnumResourceNamesW.restype = wintypes.BOOL
k32.EnumResourceNamesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, _CB4, ctypes.c_void_p]
k32.EnumResourceLanguagesW.restype = wintypes.BOOL
k32.EnumResourceLanguagesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, _CB5, ctypes.c_void_p]
RT_VERSION = 16


def strip_version_resource(path: str) -> int:
    """删掉 exe 里的 VS_VERSIONINFO（RT_VERSION）。回调参数个数必须和 API 一致，
    句柄参数也必须声明 argtypes —— 少一个就会被静默截断/报 TypeError，什么都删不掉。"""
    mod = k32.LoadLibraryExW(path, None, 0x00000002)          # LOAD_LIBRARY_AS_DATAFILE
    assert mod, "读取 exe 失败：%s" % path
    found = []

    def _lang(h, t, n, lang, p):
        found.append((int(ctypes.cast(n, ctypes.c_void_p).value), int(lang)))
        return True

    def _name(h, t, n, p):
        k32.EnumResourceLanguagesW(h, t, n, _CB5(_lang), None)
        return True

    k32.EnumResourceNamesW(ctypes.c_void_p(mod), ctypes.c_void_p(RT_VERSION), _CB4(_name), None)
    k32.FreeLibrary(ctypes.c_void_p(mod))
    if not found:
        return 0
    h = k32.BeginUpdateResourceW(path, False)
    assert h, "无法写入 exe：%s" % path
    for nid, lid in found:
        k32.UpdateResourceW(ctypes.c_void_p(h), ctypes.c_void_p(RT_VERSION),
                            ctypes.c_void_p(nid), ctypes.c_ushort(lid), None, 0)
    assert k32.EndUpdateResourceW(ctypes.c_void_p(h), False), "写回 exe 失败"
    return len(found)


def make_runtime(py_dir: str, out_dir: str) -> str:
    """从 py_dir 复制 pythonw.exe → out_dir\\C4EAI.exe 并剥掉版本资源。返回产物路径。"""
    src = os.path.join(py_dir, "pythonw.exe")
    if not os.path.exists(src):
        raise FileNotFoundError("找不到 %s" % src)
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, "C4EAI.exe")
    shutil.copy2(src, dst)
    strip_version_resource(dst)
    return dst


def main() -> None:
    py_dir = sys.argv[1] if len(sys.argv) > 1 else r"<ANACONDA-DIR>"
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PROJ, "runtime")
    try:
        dst = make_runtime(py_dir, out_dir)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    print("已生成 %s（%.0f KB）" % (dst, os.path.getsize(dst) / 1024))


if __name__ == "__main__":
    main()
