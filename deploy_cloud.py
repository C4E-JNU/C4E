# -*- coding: utf-8 -*-
"""C4EAI 一键部署到云端共享库（c4eai.lab）。

用法：  python deploy_cloud.py            # 全量同步代码并重启云端服务
        python deploy_cloud.py --no-restart

行为：本地打 tar（排除数据/缓存）→ 上传 → 云端自动备份旧代码到 ~/c4eai-hub_backup_<ts>.tar.gz
     → 解包覆盖（绝不动云端 workspace/ 与 model_cache/）→ 重启服务 → 验证。
需要本机环境：paramiko（pip install paramiko）。密码可用环境变量 C4EAI_SSH_PASSWORD 覆盖。
"""
import os, sys, time
import tarfile, io, paramiko

HOST = os.environ.get("C4EAI_SSH_HOST", "<HUB-IP>")
USER = os.environ.get("C4EAI_SSH_USER", "user")
# 密码必须由环境变量提供（避免硬编码泄漏）；本地开发可放在 .env 里，.env 已被 .gitignore 忽略
PASSWORD = os.environ.get("C4EAI_SSH_PASSWORD", "")
if not PASSWORD:
    raise SystemExit(
        "缺少环境变量 C4EAI_SSH_PASSWORD。\n"
        "请先设置后重试，例如：\n"
        "  export C4EAI_SSH_PASSWORD='<你的服务器密码>'   # bash\n"
        "  set C4EAI_SSH_PASSWORD=<你的服务器密码>        # cmd\n"
        "（旧版本曾硬编码密码作为兜底，已移除以防泄漏。）"
    )
REMOTE_DIR = "/home/user/c4eai-hub"
START_CMD = ("cd ~/c4eai-hub && setsid nohup bash -c 'C4EAI_HOST=0.0.0.0 C4EAI_PORT=8088 "
             "C4EAI_AUTH=1 C4EAI_ADMIN_PASSWORD=<ADMIN-PASSWORD> ~/c4eai-venv/bin/python backend/app.py' "
             "> ~/c4eai-hub.log 2>&1 < /dev/null &")
# 永不同步的目录/文件（云端数据与环境，绝对保护）
EXCLUDE_DIRS = {"workspace", "model_cache", "node_modules", ".git", "__pycache__", ".trash", "_syn",
                "开发中间产物", "dist", "_backup", "outputs", "parse_out"}   # 研发/备份/agent 中间产物不同步
                                           # （云端本来就有旧副本，不同步 = 只留不再更新，不影响运行）
EXCLUDE_EXT = {".zip", ".gguf", ".log", ".pyc", ".jsonl", ".json.bak", ".out", ".exe", ".msi"}  # 安装包是交付物，云端不需要（云端已有旧版本份）
EXCLUDE_FILES = {"deploy_cloud.py", "run_translate.py", "run_translate_log.txt", "start.bat",
                 "test_sse.py", ".DS_Store", "plan.json"}
EXCLUDE_PREFIX = ("plan_batch", "HANDOFF-")   # 一次性计划/交接文档，不进云端


def make_tar():
    """本地代码打包到内存 tar.gz，返回 bytes。"""
    buf = io.BytesIO()
    n = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        root = os.path.dirname(os.path.abspath(__file__))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith("_bak")]
            for fn in filenames:
                if fn in EXCLUDE_FILES or os.path.splitext(fn)[1] in EXCLUDE_EXT or fn.startswith(EXCLUDE_PREFIX):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                tf.add(full, arcname=rel)
                n += 1
    print(f"[1/4] 本地打包完成：{n} 个文件，{len(buf.getvalue())//1024} KB")
    return buf.getvalue()


def main():
    restart = "--no-restart" not in sys.argv
    data = make_tar()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=10)

    def run(cmd):
        _, o, e = c.exec_command(cmd)
        return (o.read().decode() + e.read().decode()).strip()

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = f"/home/user/c4eai-hub_backup_{ts}.tar.gz"
    print("[2/4] 上传中…")
    with c.open_sftp().file("/tmp/c4eai_sync.tar.gz", "wb") as f:
        f.write(data)
    print(f"[3/4] 云端备份旧代码 → {backup}")
    print("  ", run(f"cd {REMOTE_DIR} && tar czf {backup} --exclude=workspace --exclude=model_cache --exclude=__pycache__ . && echo 备份OK"))
    print("  ", run(f"cd {REMOTE_DIR} && tar xzf /tmp/c4eai_sync.tar.gz && rm /tmp/c4eai_sync.tar.gz && echo 解包OK"))
    if restart:
        print("[4/4] 重启云端服务…")
        run("pkill -f 'backend/app.py'")
        time.sleep(2)
        run(START_CMD)
        time.sleep(6)
        print("  进程:", run("pgrep -f 'backend/app.py' | head -1"))
        print("  日志:", run("tail -2 ~/c4eai-hub.log"))
        print("  接口:", run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/ || echo DOWN"))
    else:
        print("[4/4] 跳过重启（--no-restart）")
    c.close()
    print("部署完成 ✅  云端: http://c4eai.lab:8088")


if __name__ == "__main__":
    main()
