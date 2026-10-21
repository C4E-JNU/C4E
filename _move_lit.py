# -*- coding: utf-8 -*-
"""把 xhq1 的 30 条文献对话条目 + 链接笔记 + 附件移动到 xhq2；并删除 6 条测试笔记。
用法: python _move_lit.py            # dry-run 预演
      python _move_lit.py --apply   # 正式执行
"""
import json, os, sys, shutil, time

WS = r"<PROJECT-DIR>\workspace"
U = os.path.join(WS, "users")
X1, X2 = os.path.join(U, "xhq1"), os.path.join(U, "xhq2")
APPLY = "--apply" in sys.argv
TS = time.strftime("%Y%m%d_%H%M%S")

LIT_IDS = ["branch-56"] + ["branch-%d" % i for i in range(60, 89)]
DEL_NOTE_IDS = {
    "note-17878299590649364", "note-17878568093388052", "note-1787861725284",
    "note-1789715338623", "note-1790574560417", "2e3b86141e2b440e922b5a1e0cc42bb9",
}

def load(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def dump(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)

chats = load(os.path.join(X1, "chats.json"))
notes = load(os.path.join(X1, "notes.json"))

# ---- 1) 待删测试笔记 ----
del_notes = [n for n in notes if n.get("id") in DEL_NOTE_IDS]
missing = DEL_NOTE_IDS - {n.get("id") for n in del_notes}
print("[1] test notes to delete: %d (missing %d)" % (len(del_notes), len(missing)))
for n in del_notes:
    print("    -", n["id"], n.get("branch_id"), (n.get("title") or "")[:50])

# ---- 2) 待移动的文献条目 ----
mv_branches = {}
for b in LIT_IDS:
    if b in chats["branches"]:
        mv_branches[b] = chats["branches"][b]
    else:
        print("    !! branch missing:", b)
print("[2] branches to move: %d" % len(mv_branches))

mv_notes = [n for n in notes if n.get("branch_id") in LIT_IDS]
print("[2] linked notes: %d" % len(mv_notes))
missing_note = [b for b in mv_branches if b not in {n.get("branch_id") for n in mv_notes}]
if missing_note:
    print("    !! branches without note:", missing_note)

# ---- 3) 附件 fileId 集合 ----
fids = {}
for bid, b in mv_branches.items():
    for bub in b.get("_bubbles") or []:
        fid = bub.get("fileId")
        if fid:
            fids.setdefault(fid, []).append(bid)
print("[3] attached fileIds: %d" % len(fids))
up1, up2 = os.path.join(X1, "uploads"), os.path.join(X2, "uploads")
plan_files, absent = [], []
for fid in fids:
    src = os.path.join(up1, fid)
    if os.path.isdir(src):
        plan_files.append(fid)
    else:
        absent.append(fid)
print("    upload dirs to move: %d ; not found in xhq1/uploads: %d %s"
      % (len(plan_files), len(absent), absent[:6]))

# 未被条目引用、但近 2 天上传的 PDF（仅报告）
print("[3b] other recent uploads in xhq1/uploads (info only):")

if not APPLY:
    print("\nDRY-RUN done. run with --apply to execute.")
    sys.exit(0)

# ================= 正式执行 =================
shutil.copy2(os.path.join(X1, "chats.json"), os.path.join(X1, "chats.json.bak_move_%s" % TS))
shutil.copy2(os.path.join(X1, "notes.json"), os.path.join(X1, "notes.json.bak_move_%s" % TS))
if os.path.isfile(os.path.join(X2, "chats.json")):
    shutil.copy2(os.path.join(X2, "chats.json"), os.path.join(X2, "chats.json.bak_premove_%s" % TS))
if os.path.isfile(os.path.join(X2, "notes.json")):
    shutil.copy2(os.path.join(X2, "notes.json"), os.path.join(X2, "notes.json.bak_premove_%s" % TS))
print("[backup] done, ts=%s" % TS)

now = time.strftime("%Y-%m-%d %H:%M:%S")

# 1) 删测试笔记
notes_new = [n for n in notes if n.get("id") not in DEL_NOTE_IDS]

# 2) 笔记搬去 xhq2（owner 改为 xhq2），xhq1 里移除
mv_notes_ids = {n["id"] for n in mv_notes}
xhq1_notes = [n for n in notes_new if n["id"] not in mv_notes_ids]
for n in mv_notes:
    n["owner"] = "xhq2"
xhq2_notes = load(os.path.join(X2, "notes.json")) if os.path.isfile(os.path.join(X2, "notes.json")) else []
exist_ids = {n.get("id") for n in xhq2_notes}
xhq2_notes += [n for n in mv_notes if n["id"] not in exist_ids]

# 3) 条目搬去 xhq2
xhq2_chats = load(os.path.join(X2, "chats.json")) if os.path.isfile(os.path.join(X2, "chats.json")) else {
    "branches": {}, "deleted": {}, "branchOrder": []}
xhq2_chats.setdefault("branches", {})
xhq2_chats.setdefault("deleted", {})
xhq2_chats.setdefault("branchOrder", [])
xhq2_chats["branches"].update(mv_branches)
order = [b for b in xhq2_chats["branchOrder"] if b in xhq2_chats["branches"]]
order += [b for b in LIT_IDS if b in mv_branches and b not in order]
xhq2_chats["branchOrder"] = order
xhq2_chats["_updated"] = now

for b in mv_branches:
    chats["branches"].pop(b, None)
    chats["deleted"][b] = {"id": b, "name": mv_branches[b].get("name"),
                           "deletedAt": now, "movedTo": "xhq2"}
    if b in chats.get("branchOrder", []):
        chats["branchOrder"].remove(b)
chats["_updated"] = now

# 4) 附件目录移动 + meta.owner
os.makedirs(up2, exist_ok=True)
moved_dirs = 0
for fid in plan_files:
    src, dst = os.path.join(up1, fid), os.path.join(up2, fid)
    if os.path.isdir(dst):
        continue
    shutil.move(src, dst)
    moved_dirs += 1
    mp = os.path.join(dst, "meta.json")
    if os.path.isfile(mp):
        try:
            m = load(mp)
            m["owner"] = "xhq2"
            dump(mp, m)
        except Exception as e:
            print("    !! meta fix failed", fid, e)

# 5) 落盘
dump(os.path.join(X1, "chats.json"), chats)
dump(os.path.join(X1, "notes.json"), xhq1_notes)
dump(os.path.join(X2, "chats.json"), xhq2_chats)
dump(os.path.join(X2, "notes.json"), xhq2_notes)

print("[APPLY] branches moved=%d ; notes moved=%d ; upload dirs moved=%d"
      % (len(mv_branches), len(mv_notes), moved_dirs))
print("[APPLY] xhq1 branches now=%d notes now=%d" % (len(chats["branches"]), len(xhq1_notes)))
print("[APPLY] xhq2 branches now=%d notes now=%d" % (len(xhq2_chats["branches"]), len(xhq2_notes)))
