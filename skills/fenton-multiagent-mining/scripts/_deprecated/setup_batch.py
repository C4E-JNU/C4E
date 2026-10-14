# -*- coding: utf-8 -*-
"""setup_batch.py <batch_id> — 从 pool211 复制批次工作副本（支持超长路径）。"""
import json, os, shutil, sys

bid = sys.argv[1] if len(sys.argv) > 1 else "B17"
PRE = "\\\\?\\"


def LP(p):
    return p if p.startswith(PRE) else PRE + os.path.abspath(p)


d = json.load(open(r"D:/fenton_papers/batch_plan.json", encoding="utf-8"))
b = [x for x in d["batches"] if x["batch_id"] == bid][0]
print(bid, b["status"])
os.makedirs(r"D:/fenton_papers/" + bid, exist_ok=True)
n = 0
for name in b["papers"]:
    src = os.path.join(b["pool_root"], name)
    dst = os.path.join(r"D:/fenton_papers/" + bid, name)
    if os.path.isdir(dst):
        print("skip", name[:40])
        continue
    shutil.copytree(LP(src), LP(dst))
    n += 1
    print("copied", name[:44])
print("copied", n)
