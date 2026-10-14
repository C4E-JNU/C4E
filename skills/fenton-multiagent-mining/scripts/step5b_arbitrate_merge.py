# -*- coding: utf-8 -*-
"""step5b_arbitrate_merge.py — 步骤 5B（图文互证补充）收口：把 fenton-arbiter 的裁决回写篇级台账

背景：步骤五（fenton-verifier）只产**候选矛盾**（severity 留空）；严重程度 A/B/C/D/E 由批次级裁决员
fenton-arbiter 在**步骤 5B**（图文互证的补充步骤，紧随步骤五）判定（跨文献矛盾篇内判不出、定级需统一口径）。
本脚本是**纯确定性 IO**：把裁决结果按 (paper, figure, field) 合并回
`<论文夹>/datamining/07_contradiction_ledger.json`，并导出报告输入。

用法：
  python step5b_arbitrate_merge.py <批次根或论文夹> [--arbitration <裁决 JSON 路径>]
默认裁决文件：<批次>/_ledgers/矛盾裁决.json（单篇：<论文夹>/datamining/_ledgers/矛盾裁决.json）
兼容旧名：同目录下的 09_arbitration.json 会被自动发现（旧批次无需改名）。

产出：
  ① 回写各篇 07_contradiction_ledger.json（severity / cross_paper / caliber_variance / 定案 / 复核标记）
  ② <批次>/_ledgers/全部矛盾条目.json   —— build_report.py 的矛盾清单输入（新字段名）
  ③ <批次>/_ledgers/跨文献矛盾对.json   —— 跨文献矛盾对 + 口径差备注
"""
import argparse
import json
import os
import sys

SEV_OK = ("A", "B", "C", "D", "E")     # E = 其他（无法归类），兼「判不了/待裁决」兜底；unknown 已退场
ARB_NAMES = ("矛盾裁决.json", "09_arbitration.json")   # 前者为准，后者兼容旧批次


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def _is_paper_dir(d):
    return (os.path.isdir(os.path.join(d, "datamining"))
            or os.path.exists(os.path.join(d, "00_full_cleaned.md")))


def papers_of(root):
    """三态自适应：① root 自身是论文夹 ② root 下直接是论文夹 ③ root/<Bxx>/论文夹。"""
    root = os.path.abspath(root)
    if _is_paper_dir(root):
        return [root]
    subs = [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p)) and not p.startswith("_")]
    out = [d for d in subs if _is_paper_dir(d)]
    if out:
        return out
    for bd in subs:
        for q in sorted(os.listdir(bd)):
            d = os.path.join(bd, q)
            if os.path.isdir(d) and not q.startswith("_") and _is_paper_dir(d):
                out.append(d)
    return out


def _key(paper, e):
    return (str(paper or "").strip(), str(e.get("figure") or "").strip(),
            str(e.get("field") or "").strip())


def _load(fp, default=None):
    if not os.path.exists(fp):
        return default
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception:
        return default


def _clean_sev(v):
    """裁决给的值：合法值 A–E 原样；`unknown` / 空 / 非法 → E（其他·无法归类，兜底）。"""
    s = str(v or "").strip().upper()
    return s if s in SEV_OK else "E"


def merge_paper(led, entries, stats):
    """把裁决 entries 合并进一篇台账（原地改 led['entries']）。"""
    idx = {}
    for e in led.get("entries", []):
        idx.setdefault(_key(led.get("paper"), e), e)
    byfig = {}
    for e in led.get("entries", []):
        byfig.setdefault((str(led.get("paper") or ""), str(e.get("figure") or "").strip()), []).append(e)

    for a in entries:
        k = _key(a.get("paper"), a)
        e = idx.get(k)
        if e is None:                                    # 宽松：同篇同图且台账字段为空时对齐
            cand = [x for x in byfig.get((k[0], k[1]), []) if not str(x.get("field") or "").strip()]
            e = cand[0] if len(cand) == 1 else None
        if e is None:                                    # 裁决里有、台账没有 → 追加
            e = {"figure": a.get("figure", "-"), "field": a.get("field", ""),
                 "text_value": a.get("text_value", ""), "image_value": a.get("image_value", ""),
                 "source": "arbiter", "added_by": "step5b_arbitrate_merge.py"}
            led.setdefault("entries", []).append(e)
            stats["added"] += 1
        else:
            stats["applied"] += 1
        e["severity"] = _clean_sev(a.get("severity"))
        e["cross_paper"] = bool(a.get("cross_paper"))
        e["caliber_variance"] = bool(a.get("caliber_variance"))
        e["evidence_source"] = e.get("evidence_source") or a.get("evidence_source", "")
        if a.get("adopted_value") not in (None, ""):
            e["adopted_value"] = a["adopted_value"]
        if a.get("adopt_reason"):
            e["adopt_reason"] = a["adopt_reason"]
        if a.get("confidence"):
            e["confidence"] = a["confidence"]
        e["needs_human_review"] = (_clean_sev(a.get("severity")) == "E"
                                   or bool(a.get("needs_human_review")))
        e["arbitrated"] = True
        refs = a.get("source_refs")
        if refs:
            e["source_refs"] = refs


def rebuild_stats(led):
    by_sev = {}
    for e in led.get("entries", []):
        if str(e.get("severity") or "").strip().upper() in ("", "UNKNOWN"):
            # 未被裁决 / 旧口径 unknown → 回填 E（其他·无法归类，兜底）；台账不得留空值
            e["severity"] = "E"
            e.setdefault("needs_human_review", True)
        s = e["severity"]
        by_sev[s] = by_sev.get(s, 0) + 1
    led["total"] = len(led.get("entries", []))
    led["by_severity"] = by_sev
    led["cross_paper_count"] = sum(1 for e in led.get("entries", []) if e.get("cross_paper"))
    led["needs_human_review_count"] = sum(1 for e in led.get("entries", [])
                                          if e.get("needs_human_review"))
    led["arbitrated_by"] = "fenton-arbiter（步骤 5B）+ step5b_arbitrate_merge.py"


def _find_arb(_ld, explicit):
    if explicit:
        return explicit
    for n in ARB_NAMES:
        fp = os.path.join(_ld, n)
        if os.path.exists(fp):
            return fp
    return os.path.join(_ld, ARB_NAMES[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--arbitration", default=None)
    a = ap.parse_args()
    ps = papers_of(a.root)
    if not ps:
        print(f"未找到论文夹：{a.root}")
        return 1
    single = len(ps) == 1
    bname = os.path.basename(os.path.abspath(ps[0])) if single else os.path.basename(os.path.normpath(a.root))
    _ld = os.path.join(wf_of(ps[0]), "_ledgers") if single else os.path.join(os.path.normpath(a.root), "_ledgers")
    arb_fp = _find_arb(_ld, a.arbitration)
    arb = _load(arb_fp)
    if arb is None:
        print(f"[error] 缺裁决文件：{arb_fp}\n（需先派 fenton-arbiter 并把它的 JSON 原样存为此文件，名：{ARB_NAMES[0]}）")
        return 1
    entries = arb.get("entries") if isinstance(arb, dict) else []
    entries = entries if isinstance(entries, list) else []

    by_paper = {}
    for e in entries:
        by_paper.setdefault(str(e.get("paper") or "").strip(), []).append(e)

    stats = {"applied": 0, "added": 0, "papers": 0, "entries_in": len(entries)}
    all_rows = []
    for paper in ps:
        p = os.path.basename(paper)
        fp = os.path.join(wf_of(paper), "07_contradiction_ledger.json")
        led = _load(fp)
        if led is None:
            print(f"[skip] 无台账：{fp}")
            continue
        merge_paper(led, by_paper.get(p, []), stats)
        rebuild_stats(led)
        json.dump(led, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        stats["papers"] += 1
        print(f"merged {p[:42]:44s} 台账 {led['total']} 条 {led['by_severity']} "
              f"| 跨文献 {led['cross_paper_count']} | 待复核 {led['needs_human_review_count']}")
        for e in led.get("entries", []):
            all_rows.append({
                "批次": bname, "文献": p,
                "图号/位置": e.get("figure", ""), "字段": e.get("field", ""),
                "严重度": e.get("severity") or "E",
                "载体来源": e.get("evidence_source", ""),
                "跨文献": bool(e.get("cross_paper")),
                "口径差": bool(e.get("caliber_variance")),
                "正文值": e.get("text_value", ""), "图值/冲突值": e.get("image_value", ""),
                "裁决/采纳值": e.get("adopted_value", ""),
                "置信度": e.get("confidence", ""),
                "正文引文": e.get("text_quote", ""), "理由": e.get("adopt_reason", ""),
                "待人工复核": bool(e.get("needs_human_review")),
            })

    unmatched = [e for e in entries if str(e.get("paper") or "").strip() not in
                 {os.path.basename(x) for x in ps}]
    if unmatched:
        print(f"[warn] {len(unmatched)} 条裁决的 paper 与批次内篇名对不上，已忽略："
              f"{sorted({e.get('paper') for e in unmatched})[:5]}")

    os.makedirs(_ld, exist_ok=True)
    cts = os.path.join(_ld, "全部矛盾条目.json")
    json.dump(all_rows, open(cts, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cross = {"cross_paper_pairs": (arb.get("cross_paper_pairs") if isinstance(arb, dict) else []) or [],
             "caliber_only": (arb.get("caliber_only") if isinstance(arb, dict) else []) or []}
    cross_fp = os.path.join(_ld, "跨文献矛盾对.json")
    json.dump(cross, open(cross_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"裁决 {stats['entries_in']} 条 → 命中 {stats['applied']} / 新增 {stats['added']}"
          f" | 篇 {stats['papers']}")
    print(f"报告输入 → {cts}（build_report.py 的矛盾清单；同目录需放 全局成绩汇总.json）")
    print(f"跨文献对 {len(cross['cross_paper_pairs'])} | 口径差备注 {len(cross['caliber_only'])} → {cross_fp}")
    print("下一步：步骤六 节点搭建与实体对齐（07 台账 severity 已回填）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
