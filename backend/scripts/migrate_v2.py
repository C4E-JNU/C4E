# -*- coding: utf-8 -*-
"""C4EAI v2 存储迁移（物理分目录 + pool）。幂等，可在本地/云端各跑一次。
本地: uploads/* → users/_guest/uploads/（meta owner→guest, source→chat）
云端: uploads/* → pool/uploads/（meta source→database）；_notes.json → users/admin/notes.json；
      _vector_knowledge.json → pool/_vector_knowledge.json；清理 testauth9。
"""
import json, os, shutil, sys

WS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspace"))
IS_HUB = os.environ.get("C4EAI_AUTH", "") == "1"

def _ensure(p):
    os.makedirs(p, exist_ok=True)
    return p

def _migrate_uploads(src_root, dst_root, owner=None, source=None):
    n = 0
    if not os.path.isdir(src_root):
        return 0
    for fid in os.listdir(src_root):
        sd = os.path.join(src_root, fid)
        mp = os.path.join(sd, "meta.json")
        if not os.path.isfile(mp):
            continue
        dd = os.path.join(dst_root, fid)
        if os.path.exists(dd):
            continue
        shutil.move(sd, dd)
        try:
            meta = json.load(open(mp, encoding="utf-8"))
        except Exception:
            meta = {}
        if owner is not None:
            meta["owner"] = owner
        if source is not None:
            meta["source"] = source
        meta.setdefault("file_id", fid)
        json.dump(meta, open(os.path.join(dd, "meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        n += 1
    # 清空残留空目录
    try:
        if not os.listdir(src_root):
            os.rmdir(src_root)
    except Exception:
        pass
    return n

def main():
    print(f"[migrate] workspace={WS} hub={IS_HUB}")
    if IS_HUB:
        # 云端：旧共享 uploads → pool（全员可见、入索引）
        n1 = _migrate_uploads(os.path.join(WS, "uploads"),
                              _ensure(os.path.join(WS, "pool", "uploads")),
                              source="database")
        print(f"[migrate] uploads→pool: {n1}")
        # 旧笔记 → admin
        src_notes = os.path.join(WS, "_notes.json")
        dst_dir = _ensure(os.path.join(WS, "users", "admin"))
        dst_notes = os.path.join(dst_dir, "notes.json")
        if os.path.isfile(src_notes) and not os.path.exists(dst_notes):
            shutil.copy2(src_notes, dst_notes)
            os.remove(src_notes)
            print("[migrate] _notes.json → users/admin/notes.json")
        # 旧知识索引 → pool
        src_idx = os.path.join(WS, "_vector_knowledge.json")
        dst_idx = os.path.join(WS, "pool", "_vector_knowledge.json")
        if os.path.isfile(src_idx) and not os.path.exists(dst_idx):
            _ensure(os.path.dirname(dst_idx))
            shutil.move(src_idx, dst_idx)
            print("[migrate] _vector_knowledge.json → pool/")
        # 清理测试账号 testauth9（账号+目录）
        uf = os.path.join(WS, "_auth", "users.json")
        if os.path.isfile(uf):
            try:
                users = json.load(open(uf, encoding="utf-8"))
            except Exception:
                users = {}
            if "testauth9" in users:
                users.pop("testauth9")
                json.dump(users, open(uf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print("[migrate] 已移除 testauth9 账号")
        td = os.path.join(WS, "users", "testauth9")
        if os.path.isdir(td):
            shutil.rmtree(td, ignore_errors=True)
            print("[migrate] 已删除 users/testauth9")
    else:
        # 本地：旧 uploads → users/_guest/uploads（私人）
        n1 = _migrate_uploads(os.path.join(WS, "uploads"),
                              _ensure(os.path.join(WS, "users", "_guest", "uploads")),
                              owner="guest", source="chat")
        print(f"[migrate] uploads→users/_guest/uploads: {n1}")
        # 旧笔记 → users/_guest/notes.json
        src_notes = os.path.join(WS, "_notes.json")
        dst_dir = _ensure(os.path.join(WS, "users", "_guest"))
        dst_notes = os.path.join(dst_dir, "notes.json")
        if os.path.isfile(src_notes) and not os.path.exists(dst_notes):
            try:
                notes = json.load(open(src_notes, encoding="utf-8"))
                for x in notes:
                    x["owner"] = "guest"
            except Exception:
                notes = []
            json.dump(notes, open(dst_notes, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            os.remove(src_notes)
            print("[migrate] _notes.json → users/_guest/notes.json")
        # 本地不再使用知识索引/聊天向量（保留旧文件不删，避免误伤）
        print("[migrate] 本地向量索引停用（旧文件保留未删）")
    print("[migrate] done")

if __name__ == "__main__":
    main()
