# -*- coding: utf-8 -*-
"""迁移：把「笔记/文档」从 kind 标签变成两个真实根文件夹，并归位现有条目。

用户 2026-09-25 定的模型：
  整个共享库 = 一个文件夹树；文件夹有名字+简介；里面可装文件也可装子文件夹。
  「笔记」「文档」是两个**真实根文件夹**（不是类型标签）：
    · 「文档」夹 parse=True  → 新内容首次从外部进来时解析附件成文字
    · 「笔记」夹 parse=False → 不解析
  条目靠 folder_id 归属；kind 保留（记录来源），但**不再决定显示位置**。

本脚本做三件事（幂等，可重复跑）：
  ① ensure_roots()：建「root-note 笔记」「root-doc 文档」两个根文件夹
  ② 现有「根级用户自建分组」按原样保留（不动它们的父子关系）
  ③ 把 folder_id 为空的条目，按 kind 归到对应根文件夹

用法：
  python tools/migrate_to_folder_tree.py            # dry-run，只看会改什么
  python tools/migrate_to_folder_tree.py --apply    # 真改（带逐步核对）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from tools.folder_tree import (  # noqa: E402
    ROOT_NOTE, ROOT_DOC, ensure_roots, root_id_for_kind, _load as load_folders,
)
from tools.file_tools import (  # noqa: E402
    list_all_users_notes, _write_notes, _users_root,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = ap.parse_args()

    print("=" * 68)
    print("迁移到「文件夹树」模型" + ("（APPLY）" if args.apply else "（DRY-RUN）"))
    print("=" * 68)

    # ── ① 两个根文件夹 ──
    folders = load_folders()
    print(f"\n① 现有分组 {len(folders)} 个")
    for f in folders:
        print(f"   · [{f.get('id')}] {f.get('name')}  parent={f.get('parent_id') or '(根)'}"
              f"  parse={f.get('parse')}")
    if args.apply:
        folders, created = ensure_roots()
        print(f"   → ensure_roots(): {'新建了根夹' if created else '已存在，无需新建'}")
        folders = load_folders()
    else:
        print(f"   → 将确保存在: [{ROOT_NOTE}] 笔记(parse=False), [{ROOT_DOC}] 文档(parse=True)")

    # ── ② 条目归位 ──
    notes = list_all_users_notes() or []
    db = [n for n in notes if (n.get("branch_id") or "") == "__db__"]
    print(f"\n② 共享库条目 {len(db)} 条（users/*/notes.json 共 {len(notes)} 条）")

    need, keep = [], []
    for n in db:
        fid = n.get("folder_id") or ""
        if fid:
            keep.append(n)
        else:
            need.append(n)
    print(f"   已有归属 {len(keep)} 条 / 需归位 {len(need)} 条")

    by_kind = {}
    for n in need:
        k = n.get("kind") or "note"
        by_kind.setdefault(k, []).append(n)
    for k, arr in by_kind.items():
        print(f"      {k}: {len(arr)} 条 → 目标夹 {root_id_for_kind(k)}")

    if not need:
        print("   → 无需归位")
    elif not args.apply:
        print("   → 将按 kind 写入对应根文件夹的 folder_id")
    else:
        changed = 0
        # 按 owner 分组写回（每个用户的 notes.json 单独写）
        uroot = _users_root()
        owner_map = {}
        for n in need:
            owner_map.setdefault(n.get("owner") or "", []).append(n)

        for owner_name in sorted(os.listdir(uroot)):
            np_ = os.path.join(uroot, owner_name, "notes.json")
            if not os.path.isfile(np_):
                continue
            try:
                with open(np_, "r", encoding="utf-8") as f:
                    arr = json.load(f)
            except Exception as e:
                print(f"   ⚠️ 读失败 {np_}: {e}")
                continue
            touched = 0
            for n in arr:
                if (n.get("branch_id") or "") != "__db__":
                    continue
                if n.get("folder_id"):
                    continue
                n["folder_id"] = root_id_for_kind(n.get("kind"))
                touched += 1
            if touched:
                _write_notes(owner_name, arr)
                changed += touched
                print(f"   ✅ {owner_name}: 归位 {touched} 条")
        print(f"   → 共归位 {changed} 条")

        # ★ 落盘核对（不轻信写入回执）
        print("\n   落盘核对（重新读取）：")
        re_notes = [n for n in (list_all_users_notes() or [])
                    if (n.get("branch_id") or "") == "__db__"]
        n_note = len([n for n in re_notes if n.get("folder_id") == ROOT_NOTE])
        n_doc = len([n for n in re_notes if n.get("folder_id") == ROOT_DOC])
        n_none = len([n for n in re_notes if not n.get("folder_id")])
        print(f"     笔记夹: {n_note} 条")
        print(f"     文档夹: {n_doc} 条")
        print(f"     仍未归属: {n_none} 条")
        if n_none:
            print("     ⚠️ 仍有未归属条目，请检查")

    # ── ③ 最终状态 ──
    print("\n③ 最终文件夹树：")
    if args.apply:
        from tools.folder_tree import list_groups
        d = json.loads(list_groups())
        for line in (d.get("tree_text") or "").split("\n"):
            print("   " + line)
    else:
        print("   （dry-run 不展示；apply 后可见）")

    print("\n完成。" + ("" if args.apply else " 加 --apply 才真正写入。"))


if __name__ == "__main__":
    main()
