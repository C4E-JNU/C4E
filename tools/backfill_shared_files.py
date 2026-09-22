#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""共享库附件回填工具（存量修复）

背景（用户架构 2026-09-22 确认）：
  · 个人数据库 → 存在「当前前端对应的后端所在电脑」
  · 共享数据库 → 统一存在「服务器」；本地版本上传时，本地+服务器各一份
  早期版本的本地上传没有把附件同步到服务器，导致云端条目里的 file_id 是本机 id，
  云端 pool/uploads 里没有文件 → 云端打开 404（本地能开）。

本脚本做的事（只在本地项目目录运行）：
  1. 遍历云端所有 branch_id='__db__' 条目的正文，收集 {{file:<id>:<名>}} 引用
  2. 找出「云端 pool/uploads 里不存在」的 id
  3. 在本机 workspace/users/*/uploads/<id>/ 里找到文件本体
  4. 上传到云端（source=database&no_entry=1，不另立条目）
  5. 用返回的云端新 id 改写云端条目正文（本地文件/条目保持不动）

用法：
  python tools/backfill_shared_files.py            # 预演（dry-run，只报告）
  python tools/backfill_shared_files.py --apply    # 真正执行
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
WS = os.path.join(PROJ, "workspace")

HUB = os.environ.get("C4EAI_HUB", "").strip() or "http://<HUB-IP>:8088"
APPLY = "--apply" in sys.argv


def _session():
    """本地实例保存的共享库登录会话。"""
    p = os.path.join(WS, "_auth", "hub_session.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _api(path, method="GET", body=None, tok="", raw=None, filename=None, mime=None, timeout=600):
    url = HUB.rstrip("/") + path
    headers = {}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    data = None
    if raw is not None:
        bd = "----c4eaiBackfill"
        head = (f"--{bd}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n").encode("utf-8")
        tail = f"\r\n--{bd}--\r\n".encode("utf-8")
        data = head + raw + tail
        headers["Content-Type"] = f"multipart/form-data; boundary={bd}"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)[:200]


def _local_file(fid):
    """在本机 workspace/users/*/uploads/<fid>/ 里找原件（返回 (路径, 文件名, mime)）。"""
    users = os.path.join(WS, "users")
    if not os.path.isdir(users):
        return None
    for owner in os.listdir(users):
        d = os.path.join(users, owner, "uploads", fid)
        if not os.path.isdir(d):
            continue
        meta = {}
        mp = os.path.join(d, "meta.json")
        if os.path.isfile(mp):
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        name = meta.get("name") or ""
        mime = meta.get("mime") or ""
        for fn in os.listdir(d):
            if fn in ("meta.json", "text.txt"):
                continue
            if name and fn != name:
                continue
            return os.path.join(d, fn), (name or fn), mime
        # 没有匹配的：取第一个非元数据文件
        for fn in os.listdir(d):
            if fn not in ("meta.json", "text.txt"):
                return os.path.join(d, fn), (name or fn), mime
    return None


def main():
    sess = _session()
    tok = sess.get("token") or ""
    print(f"共享服务器: {HUB}")
    print(f"登录身份  : {sess.get('username') or '(无会话，将尝试匿名)'}")
    print(f"模式      : {'APPLY（真正执行）' if APPLY else 'DRY-RUN（只报告）'}")
    print()

    # 1) 云端所有 __db__ 条目
    st, b = _api("/api/notes?branch_id=__db__", tok=tok)
    if st != 200:
        print(f"✗ 取云端条目失败 HTTP {st}: {b[:200]}")
        print("  提示：先在本地网页右上角「登录」一次（共享库账号），再运行本脚本。")
        return 1
    try:
        raw = json.loads(b)
    except Exception:
        print("✗ 云端条目不是 JSON")
        return 1
    notes = raw if isinstance(raw, list) else (raw.get("notes") or raw.get("items") or [])
    print(f"云端共享库条目: {len(notes)} 条")

    # 2) 云端已有的 file_id 目录
    st, b = _api("/api/file/list", tok=tok)
    have = set()
    if st == 200:
        try:
            j = json.loads(b)
            for it in (j if isinstance(j, list) else j.get("files") or j.get("items") or []):
                fm = str(it.get("file_id") or it.get("id") or "")
                if fm:
                    have.add(fm)
        except Exception:
            pass

    pat = re.compile(r"\{\{file:([0-9a-fA-F]{8,}):([^}]+)\}\}")
    todo = []      # (note_id, title, owner, fid, fname)
    for n in notes:
        c = n.get("content", "") or ""
        for fid, fn in pat.findall(c):
            if fid in have:
                continue
            todo.append((n.get("id") or n.get("note_id") or "", n.get("title") or "", fid, fn))

    if not todo:
        print("✓ 没有缺失附件，无需回填")
        return 0

    print(f"发现 {len(todo)} 个缺失引用：\n")
    plan = {}
    for nid, title, fid, fn in todo:
        lf = _local_file(fid)
        mark = "✓ 本机有" if lf else "✗ 本机也没有"
        print(f"  [{mark}] {fn[:38]:<38} {fid[:16]}")
        if lf:
            plan[nid] = plan.get(nid, [])
            plan[nid].append((fid, fn, lf))

    print(f"\n可回填: {sum(len(v) for v in plan.values())} 个（涉及 {len(plan)} 条条目）")
    if not APPLY:
        print("\n（这是 DRY-RUN，未做任何改动；加 --apply 真正执行）")
        return 0

    # 3) 逐个上传 + 改写条目
    id_map = {}
    for nid, items in plan.items():
        for fid, fn, (path, name, mime) in items:
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except Exception as e:
                print(f"  ✗ 读本地文件失败 {fn}: {e}")
                continue
            st, b = _api("/api/file/upload?source=database&no_entry=1", "POST",
                         raw=data, filename=name, mime=mime, tok=tok)
            new_id = ""
            try:
                new_id = json.loads(b).get("file_id", "")
            except Exception:
                pass
            if st == 200 and new_id:
                id_map[fid] = new_id
                print(f"  ✓ {fn[:34]:<34} → {new_id[:16]}  ({len(data)} B)")
            else:
                print(f"  ✗ {fn[:34]:<34} HTTP {st} {str(b)[:100]}")

    if not id_map:
        print("\n没有任何文件上传成功，条目未改动。")
        return 1

    # 4) 改写云端条目（只替换成功映射的 id）
    print("\n改写云端条目正文 …")
    for nid, items in plan.items():
        note = next((x for x in notes if (x.get("id") or x.get("note_id")) == nid), None)
        if not note:
            continue
        content = note.get("content", "") or ""
        newc = content
        for fid, fn, _ in items:
            if fid in id_map:
                newc = newc.replace(fid, id_map[fid])
        if newc == content:
            continue
        st, b = _api("/api/notes", "PUT",
                     {"note_id": nid, "title": note.get("title") or "",
                      "content": newc, "branch_id": "__db__"}, tok=tok)
        ok = st == 200 and '"status"' in b and "ok" in b
        print(f"  {'✓' if ok else '✗'} {note.get('title','')[:30]:<30} HTTP {st} {'' if ok else str(b)[:90]}")

    print(f"\n完成：上传 {len(id_map)} 个文件、改写 {len(plan)} 条条目。")
    print("提醒：云端实例可能需要重新解析/入索引，智能体才能召回新附件正文。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
