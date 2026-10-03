#!/usr/bin/env python3
"""终端执行工具 — 让 agent 能运行 shell 命令（对齐 Hermes 的 terminal 工具）。

安全边界（复用 file_tools 的权限模式 set_permission_mode / _PERMISSION_MODE）：
  - safe: 只能在项目根目录内执行；拦截高危破坏性命令（rm -rf /、format、系统盘删除等）。
  - ask : 可在项目根外执行，但含高危/外部写特征的命令返回 __NEED_ASK__，由前端确认后才执行。
  - full: 任意执行，不拦截。

用途：agent 用它 uv pip install 装包、建 venv、跑测试/build 等；就是 Hermes 里 agent
自主管理项目环境的那个工具。
"""
import json
import os
import re
import subprocess
import asyncio
from pathlib import Path

from tools import file_tools  # 引用模块，实时读 _PERMISSION_MODE（避免值拷贝不更新）

# 项目根（terminal 的默认工作目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# ⚠️ 高危破坏性命令特征（safe/ask 模式拦截）
DANGEROUS_PATTERNS = [
    r"\brm\s+(-[a-z]*r[a-z]*f?|[a-z]*f[a-z]*r?)\s+/\b",   # rm -rf /
    r"\brm\s+(-[a-z]*r[a-z]*f?|[a-z]*f[a-z]*r?)\s+~\b",    # rm -rf ~
    r"\bformat\s+[a-zA-Z]:",       # format c:
    r"\bdel\s+[a-zA-Z]:\\\\",      # del c:\
    r"\bformat\b.*/q",             # format /q
    r"\brd\s+/[sq]\s+[a-zA-Z]:\\\\", # rd /s c:\
    r"\brm\s+-rf\s+/c/",           # git-bash 删 c: 根
    r"\bpoweroff\b", r"\bshutdown\b", r"\breboot\b",       # 关机/重启
    r"\b:\(\)\s*\{\s*:\|:&\s*\};:",  # fork bomb
    r"\bmkfs\.",                   # 格式化文件系统
    r"\bdd\s+if=.*of=/dev/",        # dd 写设备
]

def _is_dangerous(command: str) -> bool:
    """判断命令是否含高危破坏性操作。"""
    lowered = command.strip().lower()
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, lowered):
            return True
    return False


async def terminal(command: str, workdir: str = ""):
    """执行 shell 命令并返回 stdout/stderr/exit_code。

    Args:
        command: 要执行的 shell 命令
        workdir: 工作目录。**省略 = 当前工作区域的项目目录**；
                 相对路径以项目目录为基准。会产出大量中间文件的脚本，先 cd 到产物目录再跑。
    """
    command = (command or "").strip()
    if not command:
        return json.dumps({"error": "命令不能为空"})

    mode = file_tools._PERMISSION_MODE or "safe"

    # 1. 权限拦截
    if mode == "safe" and _is_dangerous(command):
        return json.dumps({"error": "安全模式下禁止高危破坏性命令，如需此操作请切换到完全访问模式", "blocked": True})

    # 2. workdir 解析（相对=项目根；safe 强制项目根内）
    # 默认 cwd = **本人产出区**（用户 2026-09-26 定：产出源不固定，不能只认 write_file）——
    # 这样用终端跑技能脚本产出的文件会被产出区收走、自动登记成附件、有同款打开方式。
    # 需要操作项目本身时，显式传 workdir（相对项目根）。
    # 工作区域（用户 2026-10-03 定）：默认 cwd = 项目目录；safe 允许范围 = 应用项目根 ∪ 项目目录 ∪ 产物目录
    _area = file_tools._work_areas.current_area(file_tools._CURRENT_USER)
    _proj_dir = os.path.abspath(_area.get("project_dir") or PROJECT_ROOT)
    _out_dir = os.path.abspath(_area.get("output_dir") or os.path.join(_proj_dir, "output"))

    if workdir and workdir.strip():
        if os.path.isabs(workdir):
            wd = workdir
        else:
            wd = os.path.join(_proj_dir, workdir)
    else:
        wd = _proj_dir
    wd = os.path.abspath(wd)

    _app_roots = (os.path.abspath(PROJECT_ROOT), _proj_dir, _out_dir)

    if mode == "safe":
        if not any(wd == r or wd.startswith(r + os.sep) for r in _app_roots):
            return json.dumps({"error": f"安全模式仅允许在工作区域内执行: {workdir}", "blocked": True})
        if _is_dangerous(command):
            return json.dumps({"error": "安全模式下禁止高危命令", "blocked": True})
        need_ask = False
    elif mode == "ask":
        # ask：允许外部，但高危/外部写特征要询问
        need_ask = _is_dangerous(command) or (not any(os.path.abspath(wd).startswith(r + os.sep) for r in _app_roots))
        if need_ask:
            return json.dumps({"need_confirm": True, "command": command, "workdir": wd,
                                "message": "该命令可能影响项目外部或操作敏感资源，请确认是否执行"})
    else:
        # full：任意
        need_ask = False

    # 3. 执行（Windows：用 cmd /c 经 bash 不行，直接 subprocess.run with shell）
    # ⚠️ 必须丢进线程（to_thread），不能在事件循环里直接 subprocess.run：
    #   本函数是 async，直接调用阻塞 API 会把**整个事件循环**钉死 —— 期间服务器不处理任何请求
    #   （/api/health 也不响应，表现为"服务卡死"）。2026-09-28 实测就是这么自锁的：
    #   agent 用 terminal 起了个子进程，子进程又回头调本服务器 → 服务器等子进程、子进程等服务器，永久死锁。
    #   丢进线程后循环照常服务，这类回调链自然解开。
    #   （不用 asyncio.create_subprocess_exec：Windows 本地实例跑 Selector 循环，它不支持子进程。）
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=wd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        out = (proc.stdout or "")[-12000:]
        err = (proc.stderr or "")[-4000:]
        return json.dumps({
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
            "workdir": wd,
        }, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "命令执行超时（120 秒）"})
    except Exception as e:
        return json.dumps({"error": f"执行失败: {str(e)}"})
