# -*- coding: utf-8 -*-
"""共享库体检（Lint）—— 纯只读，不改任何数据，0 次 LLM 调用。

用户 2026-09-24 定：借鉴 WeKnora Wiki 的 lint 机制。
最痛的那个问题是 stale_ref —— 条目引用 {{file:xxx}} 但附件其实不存在
（之前 pptx 云端 404 就是这么来的，修了一整轮 + 回填 14 个文件）。

检查项（判据全部是客观事实，不是猜测）：
  stale_ref      条目正文引用了 file_id，但该 file_id 的可读目录不存在
  cloud_missing  云端条目引用的附件文件在云端磁盘上找不到（落地缺失）
  local_only     附件在本机有、云端没有（共享库跨版本同步缺口）
  orphan         磁盘上有附件，但没有任何条目引用它（可能是泄漏）
  dup_file       同一个 file_id 被多个条目引用（可能重复上传同一文件）
  empty_entry    条目正文为空（既无正文也无附件引用）
  note_with_file note 条目里出现 {{file:...}}（按新设计，上传产物应在 document）

用法：
  # 只查云端（默认）
  python tools/lint_shared_hub.py --token <token>

  # 同时对比本机
  python tools/lint_shared_hub.py --token <token> --local-root <workspace 路径>

  # 仅机器可读输出
  python tools/lint_shared_hub.py --token <token> --json

退出码：0 = 无问题；1 = 有 warning；2 = 有 error
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict

HUB = os.environ.get("C4EAI_HUB", "http://<HUB-IP>:8088")
DB_BRANCH = "__db__"

# 与 backend/tools/shared_hub.py 的解析口径保持一致（客观引用格式）
RE_FILE_REF = re.compile(r"\{\{file:([0-9a-fA-F]{8,}):")
RE_IMG_REF = re.compile(r"/api/file/([0-9a-fA-F]{8,})")

SEV_INFO = "info"
SEV_WARN = "warning"
SEV_ERROR = "error"

_ORDER = {SEV_INFO: 0, SEV_WARN: 1, SEV_ERROR: 2}


def _api(method, path, token, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(HUB + path, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return 0, str(e)[:200]


def _list_files(token):
    """列出可见附件（含 pool 与我的私人附件）。返回 [(file_id, name, size, source)]。"""
    st, d = _api("GET", "/api/file/list", token)
    if st != 200 or not isinstance(d, dict):
        return []
    out = []
    for it in (d.get("files") or d.get("items") or []):
        if isinstance(it, dict):
            out.append((it.get("file_id") or it.get("id") or "",
                        it.get("name") or "",
                        it.get("size") or 0,
                        it.get("source") or ""))
    return [x for x in out if x[0]]


def refs_of(content):
    """从条目正文提取所有 file_id 引用（{{file:..}} 与 /api/file/.. 两种口径）。"""
    c = content or ""
    return set(RE_FILE_REF.findall(c)) | set(RE_IMG_REF.findall(c))


def lint(token, local_root=None, cloud_workspace=None):
    issues = []

    def add(itype, sev, target, desc, fixable=False):
        issues.append({"type": itype, "severity": sev, "target": target,
                       "description": desc, "auto_fixable": fixable})

    # ── 拉云端条目 ──
    st, d = _api("GET", f"/api/notes?branch_id={DB_BRANCH}", token)
    if st != 200 or not isinstance(d, dict):
        print(f"❌ 拉取条目失败: HTTP {st} {str(d)[:200]}", file=sys.stderr)
        return None, None
    notes = d.get("notes") or []

    # ── 云端可见附件集合 ──
    cloud_files = {fid for fid, _, _, _ in _list_files(token)}

    # ── 云端磁盘上的 pool/uploads 目录（若能直接访问 workspace）──
    cloud_disk = None
    if cloud_workspace and os.path.isdir(cloud_workspace):
        pool = os.path.join(cloud_workspace, "pool", "uploads")
        if os.path.isdir(pool):
            cloud_disk = {n for n in os.listdir(pool)
                          if os.path.isdir(os.path.join(pool, n))}

    # ── 本机 pool/uploads ──
    local_disk = None
    if local_root and os.path.isdir(local_root):
        pool = os.path.join(local_root, "pool", "uploads")
        if os.path.isdir(pool):
            local_disk = {n for n in os.listdir(pool)
                          if os.path.isdir(os.path.join(pool, n))}

    referenced = defaultdict(list)   # file_id -> [条目标题]
    for n in notes:
        title = n.get("title") or "(无标题)"
        kind = n.get("kind") or "note"
        content = n.get("content") or ""
        ids = refs_of(content)

        # ① 空条目
        if not content.strip() and not ids:
            add("empty_entry", SEV_INFO, title, "条目正文为空且无附件引用")

        for fid in ids:
            referenced[fid].append(title)

            # ② 云端条目引用的附件是否可见
            known_cloud = (fid in cloud_files)
            if not known_cloud and cloud_disk is not None:
                known_cloud = fid in cloud_disk
            if not known_cloud:
                add("cloud_missing", SEV_ERROR, title,
                    f"引用附件 {fid[:12]}… 在云端找不到（打开会 404）", True)

            # ③ 本机有、云端无 → 同步缺口
            if local_disk is not None and cloud_disk is not None:
                if fid in local_disk and fid not in cloud_disk:
                    add("local_only", SEV_WARN, title,
                        f"附件 {fid[:12]}… 本机有、云端无（共享库同步缺口）", True)

        # ④ kind 与内容形态不符
        if kind == "note" and ids:
            add("note_with_file", SEV_INFO, title,
                f"note 条目含 {len(ids)} 个附件引用（按设计上传产物应在 document）")

    # ⑤ 重复引用
    for fid, titles in referenced.items():
        if len(titles) > 1:
            add("dup_file", SEV_INFO, fid[:12] + "…",
                f"同一附件被 {len(titles)} 个条目引用：" + "、".join(titles[:3]))

    # ⑥ 孤儿附件（磁盘有、无人引用）
    disk_sets = []
    if cloud_disk is not None:
        disk_sets.append(("云端", cloud_disk))
    if local_disk is not None:
        disk_sets.append(("本机", local_disk))
    for label, ds in disk_sets:
        for fid in sorted(ds):
            if fid not in referenced:
                add("orphan", SEV_INFO, f"{fid[:12]}…",
                    f"{label}磁盘上的附件没有任何条目引用")

    stats = {"entries": len(notes),
             "note": sum(1 for n in notes if (n.get("kind") or "note") == "note"),
             "document": sum(1 for n in notes if n.get("kind") == "document"),
             "refs": len(referenced),
             "cloud_files": len(cloud_files)}
    return issues, stats


def health_score(issues):
    """0-100 健康分（借鉴 WeKnora 的 HealthScore）。error 扣 10，warning 扣 3，info 扣 0.5。"""
    deduct = 0.0
    for i in issues:
        deduct += {SEV_ERROR: 10.0, SEV_WARN: 3.0, SEV_INFO: 0.5}[i["severity"]]
    return max(0, int(round(100 - deduct)))


def main():
    global HUB
    ap = argparse.ArgumentParser(description="共享库体检（只读）")
    ap.add_argument("--token", default=os.environ.get("C4EAI_TOKEN", ""),
                    help="登录 token（或设 C4EAI_TOKEN）")
    ap.add_argument("--hub", default=None, help="共享库地址（默认 C4EAI_HUB 或内置）")
    ap.add_argument("--local-root", default="",
                    help="本机 workspace 路径（对比本地/云端同步）")
    ap.add_argument("--cloud-workspace", default="",
                    help="云端 workspace 路径（在服务器上直接跑时用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.hub:
        HUB = args.hub

    if not args.token:
        print("❌ 需要 --token 或 C4EAI_TOKEN 环境变量", file=sys.stderr)
        return 2

    issues, stats = lint(args.token, args.local_root or None,
                         args.cloud_workspace or None)
    if issues is None:
        return 2

    issues.sort(key=lambda x: -_ORDER[x["severity"]])
    score = health_score(issues)

    if args.json:
        print(json.dumps({"health_score": score, "stats": stats, "issues": issues},
                         ensure_ascii=False, indent=2))
    else:
        print(f"共享库体检报告   健康分 {score}/100")
        print(f"条目 {stats['entries']}（笔记 {stats['note']} / 文档 {stats['document']}"
              f"）  被引用附件 {stats['refs']}  可见附件 {stats['cloud_files']}")
        print("=" * 66)
        if not issues:
            print("✅ 未发现问题")
        else:
            counts = defaultdict(int)
            for i in issues:
                counts[(i["severity"], i["type"])] += 1
            for sev in (SEV_ERROR, SEV_WARN, SEV_INFO):
                group = [i for i in issues if i["severity"] == sev]
                if not group:
                    continue
                icon = {SEV_ERROR: "❌", SEV_WARN: "⚠️ ", SEV_INFO: "ℹ️ "}[sev]
                print(f"\n{icon} {sev.upper()}（{len(group)} 项）")
                for i in group:
                    print(f"   [{i['type']}] {i['target'][:38]}")
                    print(f"       {i['description']}")

    worst = max((_ORDER[i["severity"]] for i in issues), default=-1)
    return 2 if worst >= _ORDER[SEV_ERROR] else (1 if worst >= _ORDER[SEV_WARN] else 0)


if __name__ == "__main__":
    sys.exit(main())
