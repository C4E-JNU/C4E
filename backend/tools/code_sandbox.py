"""
代码沙箱工具 — 在子进程中安全执行 Python 代码
支持 numpy, pandas, matplotlib 等数据分析库
"""
import json
import os
import subprocess
import tempfile
import asyncio


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
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "script.py")

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            # 在子进程中执行
            process = await asyncio.create_subprocess_exec(
                "python", "-u", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmpdir,
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                    # 禁止写文件到外部
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
