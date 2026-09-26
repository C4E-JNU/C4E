"""
代码沙箱工具 — 在子进程中安全执行 Python 代码
支持 numpy, pandas, matplotlib 等数据分析库
"""
import json
import os
import shutil
import subprocess
import tempfile
import asyncio


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

    # 安全检查：防止明显的危险操作
    dangerous_patterns = [
        "import os; os.system", "import subprocess",
        "__import__('os')", "__import__('subprocess')",
        "eval(", "exec(", "compile(",
    ]
    for pattern in dangerous_patterns:
        if pattern in code.replace(" ", ""):
            return json.dumps({
                "error": f"代码包含被禁用的操作: {pattern}",
                "stdout": "",
                "stderr": "安全限制：不允许执行系统命令或动态代码执行"
            })

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

            # 在子进程中执行
            process = await asyncio.create_subprocess_exec(
                _python_exe(), "-u", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=outdir,
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                    # ⚠️ 原来这里注释写"禁止写文件到外部"，其实不准：PYTHONSAFEPATH 只影响
                    # sys.path（不把脚本目录加进去），**管不住 open() 写文件**。
                    # 真正拦住的只有上面 dangerous_patterns 那串字符串（挡 os.system/subprocess/
                    # eval/exec/compile），裸的绝对路径写入仍可能发生。
                    "PYTHONSAFEPATH": "1",
                }
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
                stdout_text = stdout.decode("utf-8", errors="replace")
                stderr_text = stderr.decode("utf-8", errors="replace")

                return json.dumps({
                    "stdout": stdout_text[-5000:] if len(stdout_text) > 5000 else stdout_text,
                    "stderr": stderr_text[-2000:] if len(stderr_text) > 2000 else stderr_text,
                    "exit_code": process.returncode,
                }, ensure_ascii=False)

            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return json.dumps({
                    "error": f"执行超时 ({timeout} 秒)",
                    "stdout": "",
                    "stderr": f"脚本运行超过 {timeout} 秒，已终止"
                })

    except Exception as e:
        return json.dumps({
            "error": f"沙箱执行失败: {str(e)}",
            "stdout": "",
            "stderr": str(e),
        })
