# -*- coding: utf-8 -*-
"""部署校验 —— 比对本地与线上的前端文件，防止漏传。

用户痛点（2026-09-24）：一次部署改了 7 个前端文件，实际只传了 5 个
（漏了 index.html 和 js/database.js），结果：
  · index.html 缺 → 数据库面板看不到「笔记/文档」Tab
  · database.js 缺 → Tab 点击无效（_switchDbKind 未定义）
线上"看起来部署了"，实际关键文件是旧版 —— 这类漏传靠肉眼查不出来。

判据（客观事实）：逐个前端文件算 md5，与线上拉到的内容比对。
不做"应该没问题"的推断。

用法：
  python tools/check_deploy.py                      # 校验（列出不一致）
  python tools/check_deploy.py --files a.js b.js    # 只校验指定文件
退出码：0 = 全部一致；1 = 有不一致（或线上拉不到）
"""
import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request

HUB = os.environ.get("C4EAI_HUB", "http://<HUB-IP>:8088")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要校验的前端产物（后端 .py 由重启流程管，不在此列）
DEFAULT_FILES = [
    "index.html",
    "css/layout.css", "css/components.css", "css/messages.css",
    "css/mobile.css", "css/sidebar.css", "css/xiaoe.css", "css/agent.css",
    "css/input.css", "css/modals.css",
    "js/api.js", "js/auth.js", "js/branching.js", "js/bubble_actions.js",
    "js/bubbles.js", "js/database.js", "js/file_preview.js", "js/graph_rag.js",
    "js/image_tools.js", "js/main.js", "js/messages.js", "js/sidebar.js",
    "js/state.js", "js/subagent_view.js", "js/xiaoe.js",
    # 以下原先漏在校验外（用户曾因「漏部署」出过 3 次问题）—— 补齐
    "js/agent_config.js", "js/dom.js", "js/editing.js", "js/formatting.js",
    "js/mobile.js", "js/note_refs.js", "js/provider.js", "js/settings.js",
    "js/skill_system.js", "js/tools.js", "js/ui.js", "js/user_settings.js",
    "js/user_chats.js", "js/events.js",
    "js/utils.js",
    "backend/app.py", "backend/tools/user_settings.py", "backend/tools/user_chats.py",
    "backend/auth.py", "backend/tools/file_tools.py",
]


def local_md5(rel):
    p = os.path.join(PROJ, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def remote_md5(rel):
    url = f"{HUB}/{rel}?deploycheck=1"
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return hashlib.md5(r.read()).hexdigest()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:
        return f"ERR {str(e)[:40]}"


def main():
    global HUB
    ap = argparse.ArgumentParser(description="部署校验（本地 vs 线上 md5）")
    ap.add_argument("--files", nargs="*", default=None, help="只校验指定文件")
    ap.add_argument("--hub", default=None, help="共享库地址（默认 C4EAI_HUB 或内置）")
    args = ap.parse_args()

    if args.hub:
        HUB = args.hub
    files = args.files or DEFAULT_FILES

    same, diff, error = [], [], []
    for rel in files:
        lm = local_md5(rel)
        if lm is None:
            continue  # 本地都没有的文件（如未引入的模块）不算问题
        rm = remote_md5(rel)
        if rm == lm:
            same.append(rel)
        elif rm.startswith(("HTTP", "ERR")):
            error.append((rel, rm))
        else:
            diff.append(rel)

    print(f"部署校验  {HUB}")
    print(f"  本地存在 {len(same) + len(diff) + len(error)} 个文件")
    print(f"  ✅ 一致   {len(same)}")
    print(f"  ❌ 不一致 {len(diff)}")
    print(f"  ⚠️ 拉取失败 {len(error)}")

    if diff:
        print("\n❌ 以下文件本地与线上不一致（说明没部署或部署不完整）:")
        for rel in diff:
            print(f"     {rel}")
    if error:
        print("\n⚠️ 以下文件无法从线上拉取:")
        for rel, why in error:
            print(f"     {rel}  ({why})")

    if not diff and not error:
        print("\n✅ 全部一致，部署完整")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
