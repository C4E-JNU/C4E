# -*- coding: utf-8 -*-
"""补齐共享库向量索引缺口 —— 把「有条目但没进索引」的文档重新入库。

背景（2026-09-24 实测）：共享库有 64 个 document 条目，但索引只有 52 个文档
→ 12 个文档搜不到。原因：早期上传／附件同步前后，索引未建立或被重置。

判据（客观事实）：文档条目正文里 {{file:<fid>:...}} 解析出的 file_id，
    · 在 pool/uploads/<fid>/text.txt 有可提取文本，且
    · 该 file_id 尚未出现在索引里（按 file_id 去重）
→ 需要补索引。

默认 dry-run；--apply 才写入。幂等：已索引的跳过。

⚠️ 本脚本**在服务器本机运行**（索引文件只存云端 pool/）。
   用法（服务器上）：
     ~/c4eai-venv/bin/python ~/c4eai-hub/tools/backfill_knowledge_index.py            # dry-run
     ~/c4eai-venv/bin/python ~/c4eai-hub/tools/backfill_knowledge_index.py --apply    # 执行
"""
import json
import os
import re
import sys

WORKSPACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workspace")
WORKSPACE = os.path.normpath(WORKSPACE)
POOL = os.path.join(WORKSPACE, "pool")
UPLOADS = os.path.join(POOL, "uploads")
INDEX_FILE = os.path.join(POOL, "_vector_knowledge.json")

RE_FILE = re.compile(r"\{\{file:([0-9a-fA-F]{8,}):")
# 与 upload_file 的入库门槛一致：>= 50 字符、且不是"[图片占位]"这类方括号占位
MIN_TEXT = 50
APPLY = "--apply" in sys.argv


def load_index():
    if not os.path.isfile(INDEX_FILE):
        return []
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_doc_entries():
    """遍历 users/*/notes.json，返回 [(title, file_id, text)] —— 共享库 document 条目。"""
    out = []
    uroot = os.path.join(WORKSPACE, "users")
    if not os.path.isdir(uroot):
        return out
    for uname in sorted(os.listdir(uroot)):
        np_ = os.path.join(uroot, uname, "notes.json")
        if not os.path.isfile(np_):
            continue
        try:
            with open(np_, encoding="utf-8") as f:
                notes = json.load(f)
        except Exception as e:
            print(f"  ⚠️ 读不了 {uname}/notes.json: {e}")
            continue
        for n in notes:
            if n.get("branch_id") != "__db__":
                continue
            if (n.get("kind") or "note") != "document":
                continue
            title = n.get("title") or ""
            for fid in RE_FILE.findall(n.get("content") or ""):
                out.append((title, fid))
    return out


def main():
    index = load_index()
    indexed_fids = {c.get("file_id") for c in index if c.get("file_id")}
    print(f"现有索引: {len(index)} 块, 覆盖 {len(indexed_fids)} 个 file_id")
    print(f"workspace: {WORKSPACE}\n")

    entries = collect_doc_entries()
    print(f"共享库 document 条目解析出 {len(entries)} 个附件引用\n")

    plan = []
    for title, fid in entries:
        if fid in indexed_fids:
            continue
        tpath = os.path.join(UPLOADS, fid, "text.txt")
        if not os.path.isfile(tpath):
            print(f"  [跳过] {title[:34]:<36} 无 text.txt")
            continue
        with open(tpath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
        reason = None
        if len(text) < MIN_TEXT:
            reason = f"文本过短({len(text)}字符)"
        elif text.startswith("[") and text.rstrip().endswith("]"):
            reason = "占位符文本（无实质内容）"
        if reason:
            print(f"  [跳过] {title[:34]:<36} {reason}")
            continue
        plan.append((title, fid, text))

    print(f"\n{'=' * 64}")
    print(f"待补索引: {len(plan)} 个文档\n")
    total_chunks = 0
    for title, fid, text in plan:
        # 复用生产切块参数（500/50）
        est = max(1, len(text) // 450)
        total_chunks += est
        print(f"  + {title[:40]:<42} {len(text):>7} 字符  ≈{est:>3} 块  {fid[:12]}…")

    if not plan:
        print("\n✓ 索引已完整，无需补齐")
        return 0
    if not APPLY:
        print(f"\n[DRY-RUN] 加 --apply 执行；预计新增约 {total_chunks} 块")
        return 0

    # ── 执行：复用生产入库函数（切块 + 向量化 + 落盘）──
    sys.path.insert(0, os.path.join(os.path.dirname(WORKSPACE), "backend"))
    try:
        from tools.vector_search import ingest_text_to_knowledge
    except Exception as e:
        print(f"❌ 无法导入 ingest_text_to_knowledge: {e}")
        return 1

    ok = fail = 0
    added_chunks = 0
    for title, fid, text in plan:
        try:
            n = ingest_text_to_knowledge(text, title, fid)
            added_chunks += n
            ok += 1
            print(f"  ✅ {title[:38]:<40} +{n} 块")
        except Exception as e:
            fail += 1
            print(f"  ❌ {title[:38]:<40} {type(e).__name__}: {str(e)[:80]}")

    # ── 真实校验：重新读索引核对 ──
    after = load_index()
    after_fids = {c.get("file_id") for c in after if c.get("file_id")}
    verified = sum(1 for _, fid, _ in plan if fid in after_fids)
    print(f"\n{'=' * 64}")
    print(f"入库回执: 成功 {ok} / 失败 {fail}")
    print(f"索引块数: {len(index)} → {len(after)}（+{len(after) - len(index)}）")
    print(f"实际落盘核对: {verified}/{len(plan)} 个 file_id 已进索引")
    if verified < len(plan):
        missing = [t for t, fid, _ in plan if fid not in after_fids]
        print(f"⚠️ 未落盘: {missing[:5]}")
        return 1
    print("✅ 全部落盘正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
