# -*- coding: utf-8 -*-
"""一次性迁移：共享池已有文件 → 数据库笔记条目（branch_id='__db__'）。

每个池文件生成一条标准条目：标题=文件名，正文={{file:file_id:文件名}}。
幂等：已存在引用同一 file_id 的 __db__ 条目则跳过。
用法（服务器）：env -u PYTHONPATH ~/c4eai-venv/bin/python backend/scripts/migrate_pool_to_notes.py
"""
import os, sys, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.file_tools import _pool_uploads_dir, list_all_users_notes, add_note  # noqa: E402

DB_BRANCH = "__db__"


def main():
    root = _pool_uploads_dir()
    # 已有 __db__ 条目里出现过的 file_id
    existing = list_all_users_notes() or []
    have = set()
    for n in existing or []:
        if n.get("branch_id") != DB_BRANCH:
            continue
        content = n.get("content") or ""
        import re
        for m in re.finditer(r"\{\{file:([0-9a-fA-F]+):", content):
            have.add(m.group(1))

    created, skipped = 0, 0
    for fid in sorted(os.listdir(root)):
        meta_p = os.path.join(root, fid, "meta.json")
        if not os.path.isfile(meta_p):
            continue
        import json
        with open(meta_p, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if fid in have:
            skipped += 1
            continue
        name = meta.get("name") or fid
        owner = meta.get("owner") or "admin"
        r = asyncio.run(add_note(name, "\n{{file:%s:%s}}\n" % (fid, name), DB_BRANCH, owner))
        try:
            r = json.loads(r) if isinstance(r, str) else r
        except Exception:
            pass
        st = r.get("status") if isinstance(r, dict) else None
        if st == "ok":
            created += 1
            print("✓ %s → 条目（owner=%s）" % (name, owner))
        else:
            print("✗ %s 失败: %s" % (name, r))
    print("== 迁移完成：新建 %d 条，跳过已有 %d 条 ==" % (created, skipped))


if __name__ == "__main__":
    main()
