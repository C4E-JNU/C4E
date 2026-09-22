# -*- coding: utf-8 -*-
"""把本地 users/_guest 数据迁到 users/xhq1（owner 字段改写 guest→xhq1）。幂等。"""
import json, os, shutil, sys

WS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspace"))
SRC = os.path.join(WS, "users", "_guest")
DST = os.path.join(WS, "users", "xhq1")
n = {"files": 0, "metas": 0, "notes": 0, "history": 0}

def rewrite_owner(path, old="guest", new="xhq1"):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return False
    if isinstance(d, list):
        for x in d:
            if isinstance(x, dict) and x.get("owner") == old:
                x["owner"] = new
    elif isinstance(d, dict) and d.get("owner") == old:
        d["owner"] = new
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return True

# 1) uploads 目录
s_up, d_up = os.path.join(SRC, "uploads"), os.path.join(DST, "uploads")
if os.path.isdir(s_up):
    os.makedirs(d_up, exist_ok=True)
    for fid in os.listdir(s_up):
        sd, dd = os.path.join(s_up, fid), os.path.join(d_up, fid)
        if os.path.isdir(sd) and not os.path.exists(dd):
            shutil.move(sd, dd)
            n["files"] += 1
        mp = os.path.join(dd, "meta.json")
        if os.path.isfile(mp) and rewrite_owner(mp):
            n["metas"] += 1

# 2) notes.json
s_no, d_no = os.path.join(SRC, "notes.json"), os.path.join(DST, "notes.json")
if os.path.isfile(s_no) and not os.path.exists(d_no):
    os.makedirs(DST, exist_ok=True)
    shutil.move(s_no, d_no)
if os.path.isfile(d_no) and rewrite_owner(d_no):
    n["notes"] = len(json.load(open(d_no, encoding="utf-8")))

# 3) 聊天历史 + 向量索引（guest→xhq1）
for old, new in (("_history_guest.jsonl", "_history_xhq1.jsonl"),
                 ("_vector_history__guest.json", "_vector_history__xhq1.json"),
                 ("_vector_meta__guest.json", "_vector_meta__xhq1.json")):
    sp, dp = os.path.join(WS, old), os.path.join(WS, new)
    if os.path.isfile(sp) and not os.path.exists(dp):
        shutil.copy2(sp, dp)
        n["history"] += 1

print(f"[guest→xhq1] files={n['files']} metas={n['metas']} notes={n['notes']} history={n['history']}")
