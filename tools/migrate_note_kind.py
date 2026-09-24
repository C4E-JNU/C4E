# -*- coding: utf-8 -*-
"""迁移：共享库既有条目补 kind 字段 + 把"上传自动解析"产生的条目划为文档条目。

用户 2026-09-22 定：
  · kind="note"     人写的笔记，适合直接阅读，共享库默认展示
  · kind="document" 机器解析的文档正文，供检索；由「📤 上传」按钮产生
  · 两者数据结构完全一致（都是 文本+附件）

迁移判据（客观事实，非猜测）：
  正文含 "{{file:<fid>:<name>}}" 附件占位符 → 上传解析产物 → document
  纯手写无占位符                            → note

⚠️ 教训：只信写回执「成功」是错的 —— 必须**重新拉取并按 id 核对** kind 真的落盘。
   本脚本每次写入后 re-GET 校验，报"成功"即有据可查。

用法：
  C4EAI_TOKEN=<token> python tools/migrate_note_kind.py            # dry-run
  C4EAI_TOKEN=<token> python tools/migrate_note_kind.py --apply    # 执行
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HUB = os.environ.get("C4EAI_HUB", "http://<HUB-IP>:8088")
TOKEN = os.environ.get("C4EAI_TOKEN", "")
DB_BRANCH = "__db__"
APPLY = "--apply" in sys.argv


def _req(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = "Bearer " + TOKEN
    r = urllib.request.Request(HUB + path, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)[:200]


def _fetch_kinds():
    """拉全部条目，返回 {id: kind}（kind 缺省为 None）。"""
    st, d = _req("GET", f"/api/notes?branch_id={DB_BRANCH}")
    if st != 200 or not isinstance(d, dict):
        print(f"❌ 拉取失败: HTTP {st} {str(d)[:200]}")
        return None
    return {n.get("id"): n.get("kind") for n in (d.get("notes") or [])}


def main():
    st, d = _req("GET", f"/api/notes?branch_id={DB_BRANCH}")
    if st != 200 or not isinstance(d, dict):
        print(f"❌ 拉取条目失败: HTTP {st} {str(d)[:200]}")
        return 1
    notes = d.get("notes") or []
    print(f"云端共享库条目: {len(notes)} 个   mode={'APPLY' if APPLY else 'DRY-RUN'}\n")

    plan = []
    for n in notes:
        if n.get("kind"):
            print(f"  [跳过] 已有 kind={n['kind']}: {n.get('title', '')[:42]}")
            continue
        content = n.get("content") or ""
        has_att = bool(re.search(r"\{\{file:[0-9a-fA-F]{8,}:", content))
        auto = "附件正文（上传时自动解析）" in content
        plan.append((n.get("id"), n.get("title", ""),
                     "document" if (has_att or auto) else "note", has_att, auto))

    print(f"\n{'=' * 64}")
    n_doc = sum(1 for p in plan if p[2] == "document")
    n_note = len(plan) - n_doc
    print(f"待迁移: {len(plan)} 个   document={n_doc}  note={n_note}\n")
    for _id, title, kind, has_att, auto in plan:
        why = "含附件占位符" if has_att else ("含自动解析标记" if auto else "纯手写")
        print(f"  → {kind:<8} | {title[:42]:<44} | {why}")

    if not plan:
        print("\n✓ 无需迁移")
        return 0
    if not APPLY:
        print(f"\n[DRY-RUN] 加 --apply 执行上述 {len(plan)} 项")
        return 0

    # ── 执行 + 真实校验 ──
    print(f"\n{'=' * 64}\n开始执行 {len(plan)} 项…\n")
    want = {p[0]: p[2] for p in plan}
    ok = fail = 0
    for _id, title, kind, _a, _b in plan:
        st2, r2 = _req("PUT", "/api/notes", {"note_id": _id, "kind": kind, "branch_id": DB_BRANCH})
        good = st2 == 200 and isinstance(r2, dict) and r2.get("status") == "ok"
        if not good:
            fail += 1
            print(f"  ❌ 写入失败 {title[:36]}: HTTP {st2} {str(r2)[:110]}")
        else:
            ok += 1

    # 关键：重新拉取，逐条核对 kind 真的落盘（不信回执，信数据）
    print("\n校验落盘（重新拉取逐条核对）…")
    actual = _fetch_kinds()
    if actual is None:
        print("❌ 校验拉取失败，无法确认真实结果")
        return 1
    verified = sum(1 for i, k in want.items() if actual.get(i) == k)
    wrong = [(i, k, actual.get(i)) for i, k in want.items() if actual.get(i) != k]
    print(f"  写入回执成功: {ok} / 失败 {fail}")
    print(f"  实际落盘正确: {verified} / {len(want)}")
    if wrong:
        print(f"\n  ⚠️ {len(wrong)} 条未落盘（前 5 个）:")
        for i, w, g in wrong[:5]:
            print(f"     id={i} 期望={w} 实际={g}")
        return 1
    print("\n✅ 全部落盘正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
