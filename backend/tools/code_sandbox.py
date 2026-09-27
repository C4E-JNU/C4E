"""
代码沙箱工具 — 在子进程中安全执行 Python 代码
支持 numpy, pandas, matplotlib 等数据分析库
"""
import ast
import json
import os
import shutil
import subprocess
import tempfile
import asyncio


# 危险操作检查必须走 AST，不能靠字符串比对。
# 历史教训：旧实现把代码 replace(" ", "") 去空格后，再与【含空格】的字面量
# （"import subprocess"、"import os; os.system"）做 in 判断——去掉空格后永远
# 匹配不上带空格的串，所以 subprocess 实际是【放行】的；反过来无空格的
# "compile(" 会命中 re.compile(...) / model.compile(...)，把正常代码【误杀】，
# 模型只收到一句莫名其妙的"包含被禁用的操作: compile("。
_BANNED_BUILTINS = {"eval", "exec", "compile", "__import__"}
_BANNED_MODULES = {"subprocess", "pty", "ctypes"}
_BANNED_OS_CALLS = {"system", "popen", "fork", "kill", "remove", "rmdir",
                    "unlink", "rename", "replace", "truncate"}


def _danger_reason(tree):
    """扫描 AST，返回被禁用操作的说明文字；没有则返回 None。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _BANNED_MODULES:
                    return "import %s（会启动外部进程或绕过类型检查）" % alias.name
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BANNED_MODULES:
                return "from %s import ...（会启动外部进程或绕过类型检查）" % node.module
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _BANNED_BUILTINS:
                return "%s() 动态执行代码" % fn.id
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                base = fn.value.id
                attr = fn.attr
                if base == "os" and (attr in _BANNED_OS_CALLS
                                     or attr.startswith(("exec", "spawn"))):
                    return "os.%s() 会起进程或改动文件系统" % attr
    return None


def _python_exe() -> str:
    """跨平台探测 Python 可执行文件名：Linux 服务器通常只有 python3"""
    for cand in ("python", "python3"):
        p = shutil.which(cand)
        if p:
            return p
    return "python3"  # 兜底，让报错信息清晰


async def run_python(code: str, timeout: int = 30):
    """在隔离临时目录中执行 Python 代码，返回 stdout/stderr/exit_code"""
    code = code.strip()
    if not code:
        return json.dumps({"error": "代码不能为空"})

    # 安全检查：AST 扫描（见上方 _danger_reason 的说明）
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = None  # 语法错误让子进程去报真实行号，不在这里误判成"危险操作"
    if tree is not None:
        reason = _danger_reason(tree)
        if reason:
            return json.dumps({
                "error": "run_code 不接受这段代码：%s" % reason,
                "stdout": "",
                "stderr": "",
                "hint": ("run_code 定位为纯计算 / 数据分析 / 画图。需要跑系统命令、安装包、"
                         "执行脚本或做文件增删改名时，改用 terminal 工具（同样受权限模式约束）。"
                         "注意 re.compile()、model.compile() 这类是正常的，不在禁用范围。"),
            }, ensure_ascii=False)

    timeout = min(max(1, timeout), 60)  # 1-60 秒

    try:
        # 产出落点 = 本人产出区（持久）：脚本里 `open("report.png","w")` 写出来的文件
        # 才不会随临时目录一起蒸发 —— 原来 cwd 是临时目录，函数一返回文件就没了，
        # 所以 agent 过去只能把结果"贴在回答里"。
        from tools.file_tools import _user_outputs_dir
        outdir = _user_outputs_dir()
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "script.py")

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            # 在子进程中执行。
            # 为什么是「线程 + subprocess.run」而不是 asyncio.create_subprocess_exec：
            #   Windows 本地实例跑在 Selector 事件循环上（原因见 backend/app.py 主程序的注释），
            #   而 Selector 循环不支持 asyncio 子进程。subprocess.run 本身就是阻塞调用，
            #   丢进线程里语义与原来的 PIPE + communicate() 一致（超时同样会杀掉子进程）。
            def _exec():
                return subprocess.run(
                    [_python_exe(), "-u", script_path],
                    cwd=outdir,
                    capture_output=True,
                    timeout=timeout,
                    env={
                        **os.environ,
                        "PYTHONIOENCODING": "utf-8",
                        # ⚠️ PYTHONSAFEPATH 只影响 sys.path（不把脚本目录加进去），
                        # 【管不住 open() 写文件】，也不限制网络。
                        # 本工具的真实边界：AST 挡住起进程/删文件/动态执行这几类，
                        # 文件读写路径与网络请求都不拦 —— 所以它不是安全沙箱，
                        # 只是"少一手危险操作"的执行环境。role_policy 因此把
                        # run_code 限定给主管理员（见 backend/role_policy.py 顶部说明）。
                        "PYTHONSAFEPATH": "1",
                    },
                )

            try:
                proc = await asyncio.to_thread(_exec)
            except subprocess.TimeoutExpired:
                return json.dumps({
                    "error": f"执行超时 ({timeout} 秒)",
                    "stdout": "",
                    "stderr": f"脚本运行超过 {timeout} 秒，已终止"
                })

            stdout_text = proc.stdout.decode("utf-8", errors="replace")
            stderr_text = proc.stderr.decode("utf-8", errors="replace")

            return json.dumps({
                "stdout": stdout_text[-5000:] if len(stdout_text) > 5000 else stdout_text,
                "stderr": stderr_text[-2000:] if len(stderr_text) > 2000 else stderr_text,
                "exit_code": proc.returncode,
            }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"沙箱执行失败: {str(e)}",
            "stdout": "",
            "stderr": str(e),
        })
