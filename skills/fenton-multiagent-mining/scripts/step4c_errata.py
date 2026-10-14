# -*- coding: utf-8 -*-
"""step4c_errata.py — 步骤五收口：按《矛盾分类规范》v1 归一 → 篇级候选台账 + errata + 互证收益

口径（2026-10-06 起，唯一来源 skills/fenton-multiagent-mining/references/矛盾分类规范.md）：
  轴一 严重程度 severity = A/B/C/D/E（E = 其他·无法归类，兼「待裁决」兜底；unknown 已退场，读到即归一为 E）；两个布尔标记 cross_paper / caliber_variance
  轴二 互证发现项 q = Q1(我方读图错) / Q2(我方读文错) / Q3(口径差) / Q4(一致)
  字段 evidence_source = text-vs-figure / text-vs-table / text-vs-text / figure-vs-table
  旧 category（E1/E2/E3/E5/图文/表文/text-text/旧单字母 E）在本脚本一次性迁移；**旧 E 级（交叉矛盾）废弃** → 改 cross_paper 布尔。
  定级（A–E）由批次级裁决员 fenton-arbiter 在步骤 5B（图文互证补充）完成，本脚本不改已给 severity，只把空值兜底为 E。

产出：
  <论文夹>/datamining/07_contradiction_ledger.json   篇级候选矛盾账（severity 空/E 待回填）
  <论文夹>/datamining/05b_verification_gain.json      篇级互证收益 C/e/R/Δ
  <论文夹>/datamining/04c_errata.json                 真错误表（Q1/Q2）
  <批次>/_ledgers/<批次>_矛盾汇总台账.json              全批候选汇总（归一后）
  <批次>/_ledgers/<批次>_互证收益汇总.json              批次级收益汇总

用法：python step4c_errata.py <论文夹或批次根>
"""
import json
import os
import re
import sys

# ── 口径常量 ─────────────────────────────────────────────
SEV_OK = ("A", "B", "C", "D", "E")     # 严重度合法值（E = 其他/无法归类，兼「待裁决」兜底；unknown 已退场）
Q_OK = ("Q1", "Q2", "Q3", "Q4")
EV_OK = ("text-vs-figure", "text-vs-table", "text-vs-text", "figure-vs-table")
CONSISTENT_KEYS = ("consistent", "dual_source", "dual-source", "within_tolerance", "一致", "相符")


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def _is_paper_dir(d):
    return (os.path.isdir(os.path.join(d, "datamining"))
            or os.path.exists(os.path.join(d, "00_full_cleaned.md")))


def papers_of(root):
    """三态自适应：① root 自身是论文夹 → 单篇；② root 下直接是论文夹 → 批次；
    ③ root/<Bxx>/论文夹（批次根）→ 退一层再扫。"""
    root = os.path.abspath(root)
    if _is_paper_dir(root):
        return [root]
    subs = [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p)) and not p.startswith("_")]
    out = [d for d in subs if _is_paper_dir(d)]
    if out:
        return out
    for bd in subs:                                   # ③ 批次根：root/Bxx/论文夹
        for q in sorted(os.listdir(bd)):
            d = os.path.join(bd, q)
            if os.path.isdir(d) and not q.startswith("_") and _is_paper_dir(d):
                out.append(d)
    return out


def _first(it, *keys):
    for k in keys:
        v = it.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _norm_ev(raw):
    """载体来源归一（模板归位，禁止再用作文分类）。"""
    u = str(raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not u:
        return ""
    if "figure" in u or "image" in u or "图" in u:
        return "figure-vs-table" if "table" in u or "表" in u else "text-vs-figure"
    if "table" in u or "表" in u:
        return "figure-vs-table" if "figure" in u or "图" in u else "text-vs-table"
    if "text" in u or "文" in u:
        return "text-vs-text"
    return ""


def _guess_ev(raw, ev=""):
    if ev:
        return _norm_ev(ev)
    if str(raw or "").strip().upper() in ("TT", "T-T"):
        return "text-vs-text"
    return _norm_ev(raw)


def _is_consistent(raw, u):
    if any(k in str(raw) for k in CONSISTENT_KEYS):
        return True
    return u.startswith("CONSISTENT") or u.startswith("DUAL") or u.startswith("WITHIN")


def _guess_sev(raw, u):
    """旧字母级别 → severity。旧单字母 E 是跨文献标记（走 cross_paper），不占严重度字母；
    判不出来的一律兜底 E（其他·无法归类）——`unknown` 已退场。"""
    if str(raw).strip() and "一致" in str(raw):
        return "E"
    if not u:
        return "E"
    if u[0] in "ABCD" and (len(u) == 1 or not u[1].isalnum()):
        return u[0]
    return "E"


def normalize(it):
    """一条记录 → 归位结果。

    返回 dict：
      bucket = corrections（Q1/Q2，进 errata 不入矛盾库）| consistent（Q4，只记覆盖率）
             | caliber（口径差，标 caliber_variance 不入矛盾库）| drop（OCR 伪影，剔除）
             | ledger（候选矛盾，进 07 台账）
      其余键：q / ev / sev / cross / note
    """
    raw = _first(it, "category", "类别", "type")
    u = raw.upper()
    ev = _first(it, "evidence_source", "evidence")
    q = _first(it, "q", "finding_q")
    sev = _first(it, "severity", "严重度")
    cal = bool(it.get("caliber_variance")) or ("口径" in raw)
    cross = bool(it.get("cross_paper"))
    note = ""

    q = q.upper() if re.match(r"^Q[1-4]$", q.upper()) else ""

    if not q:                                   # 旧字段迁移（仅当没给新字段）
        if u.startswith("OCR") or "伪影" in raw:
            return {"bucket": "drop", "note": "OCR 伪影，非文献问题"}
        if u.startswith("E1"):
            q = "Q1"
        elif u.startswith("E2"):
            q = "Q2"
        elif u.startswith("E3"):
            # 原 E3 = 图文各执一词；带括号注明 text-text 的变体按其注明归位
            _r = raw.lower()
            ev = ev or ("text-vs-text" if ("text-text" in _r or "text_text" in _r or "文文" in raw)
                        else "text-vs-figure")
        elif u.startswith("E5"):
            cal, note = True, "原 E5 口径差异 → caliber_variance"
        elif _is_consistent(raw, u):
            q = "Q4"
        elif u.startswith("E") and not re.match(r"^E[1-9]", u):
            # 单字母 E / E(跨文献…)：原「交叉矛盾」→ 跨文献标记（不占严重度字母）
            cross, note = True, "原 E 级交叉矛盾 → cross_paper"

    if q in ("Q1", "Q2"):
        return {"bucket": "corrections", "q": q, "ev": _guess_ev(raw, ev)}
    if q == "Q4":
        return {"bucket": "consistent", "q": "Q4"}
    if cal:
        return {"bucket": "caliber", "ev": _guess_ev(raw, ev), "note": note or "口径差异"}

    if not (len(sev) == 1 and sev.upper() in SEV_OK):
        sev = _guess_sev(raw, u)
    else:
        sev = sev.upper()
    if sev not in SEV_OK:
        sev = "E"                                    # 兜底：其他·无法归类（兼待裁决）；unknown 已退场
    return {"bucket": "ledger", "sev": sev, "ev": _guess_ev(raw, ev), "cross": cross, "note": note}


def collect(root):
    """读各篇 4a/4b 台账 → master（不改原 dict，归一结果另存 _norm）。"""
    master = []
    for paper in papers_of(root):
        p = os.path.basename(paper)
        wf = wf_of(paper)
        for src, f in (("4a", os.path.join(wf, "04_discrepancy_ledger.json")),
                       ("4b", os.path.join(wf, "04b_text_table_contradictions.json"))):
            if not os.path.exists(f):
                continue
            d = json.load(open(f, encoding="utf-8"))
            if isinstance(d, dict):
                if isinstance(d.get("adopted_values_E3"), list):
                    d = [dict(x) for x in d["adopted_values_E3"]]
                else:
                    d = d.get("entries") or d.get("items") or [d]
            for it in d:
                if isinstance(it, str):
                    it = {"finding": it, "category": "text-text"}
                it = dict(it)
                it["source"] = src
                it["paper"] = p
                it["_norm"] = normalize(it)
                master.append(it)
    return master


def finalize(master):
    """自动清洗：剔除 OCR 伪影。severity 的最终定级留待步骤 5B 裁决员回填。"""
    return [it for it in master if it["_norm"]["bucket"] != "drop"]


def gen_ledger(root, master):
    """篇级 07_contradiction_ledger.json —— 候选矛盾账（口径：severity + evidence_source + 两标记）。"""
    by_paper = {}
    for it in master:
        n = it["_norm"]
        if n["bucket"] == "caliber":
            by_paper.setdefault(it.get("paper"), []).append(it)   # 口径差单列，不入 total
            continue
        if n["bucket"] != "ledger":
            continue
        by_paper.setdefault(it.get("paper"), []).append(it)
    for paper in papers_of(root):
        p = os.path.basename(paper)
        items, caliber = [], []
        for it in by_paper.get(p, []):
            n = it["_norm"]
            sev = n.get("sev") or ("E" if n["bucket"] != "caliber" else "")   # 台账不留空：兜底 E（其他·无法归类）
            rec = {
                "figure": it.get("figure", "-"),
                "field": it.get("field", it.get("finding", "")),
                "severity": sev,
                "evidence_source": n.get("ev", ""),
                "cross_paper": bool(n.get("cross") or it.get("cross_paper")),
                "text_value": it.get("text_value", ""),
                "image_value": it.get("image_value", it.get("conflict", "")),
                "adopted_value": it.get("adopted_value", ""),
                "adopt_reason": it.get("adopt_reason", it.get("adopted_reason", "")),
                "confidence": it.get("confidence", ""),
                "text_quote": it.get("text_quote", it.get("location", "")),
                "needs_human_review": sev == "E",
                "source": it.get("source", ""),
            }
            if n["bucket"] == "caliber":
                rec["caliber_variance"] = True
                rec["needs_human_review"] = False
                caliber.append(rec)
            else:
                rec["caliber_variance"] = False
                items.append(rec)
        for i, it in enumerate(items, 1):
            it["id"] = i
        by_sev = {}
        for it in items:
            by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
        out = os.path.join(wf_of(paper), "07_contradiction_ledger.json")
        json.dump({
            "paper": p, "total": len(items), "by_severity": by_sev,
            "cross_paper_count": sum(1 for it in items if it["cross_paper"]),
            "caliber_variance_count": len(caliber),
            "caliber": "矛盾分类规范 v1：severity=A/B/C/D/E（E=其他·无法归类，兼待裁决兜底）；cross_paper / caliber_variance 布尔标记；"
                       "evidence_source 只作载体回溯；E1/E2/OCR 不入账；定级由 fenton-arbiter（步骤 5B）回填",
            "generated_by": "step4c_errata.py",
            "entries": items,
            "caliber_annotations": caliber,
        }, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"ledger {p[:42]:44s} 候选 {len(items)} 条 {by_sev}"
              f" | 跨文献 {sum(1 for it in items if it['cross_paper'])}"
              f" | 口径差 {len(caliber)}")


def gen_errata(root, master):
    """篇级 04c_errata.json —— 互证查出的**我方提取错误**（Q1/Q2），不入矛盾库。"""
    by_paper = {}
    for it in master:
        n = it["_norm"]
        if n["bucket"] == "corrections" and it.get("paper"):
            by_paper.setdefault(it["paper"], []).append((it, n))
    # 04_internal_corrections.json 里可能还有标注表之外补记的（用 q/before/after 字段）
    for paper in papers_of(root):
        p = os.path.basename(paper)
        wf = wf_of(paper)
        items = []
        for it, n in by_paper.get(p, []):
            items.append({
                "figure": it.get("figure", "-"),
                "field": it.get("field", it.get("finding", "")),
                "q": n.get("q", "Q1"),
                "before": it.get("before", it.get("text_value", "")),
                "after": it.get("after", it.get("adopted_value", "")),
                "evidence": it.get("basis", it.get("adopt_reason", it.get("text_quote", ""))),
                "confidence": it.get("confidence", ""),
                "source": it.get("source", ""),
            })
        extra = _load(os.path.join(wf, "04_internal_corrections.json")) or []
        seen = {(x.get("figure"), x.get("field")) for x in items}
        for it in extra if isinstance(extra, list) else []:
            if not isinstance(it, dict):
                continue
            q = str(it.get("q") or "").upper()
            if not re.match(r"^Q[1-2]$", q):
                q = "Q1" if str(it.get("category", "")).upper().startswith("E1") else "Q2"
            rec = {"figure": it.get("figure", "-"), "field": it.get("field", ""), "q": q,
                   "before": it.get("before", ""), "after": it.get("after", ""),
                   "evidence": it.get("basis", ""), "confidence": it.get("confidence", ""),
                   "source": "04_internal_corrections"}
            if (rec["figure"], rec["field"]) in seen:
                continue
            items.append(rec)
        if items:
            out = os.path.join(wf, "04c_errata.json")
            json.dump({"paper": p, "errata_count": len(items),
                       "caliber": "Q1/Q2 我方提取错误（互证修正），非文献矛盾",
                       "errata": items},
                      open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"errata {p[:42]:44s} {len(items)} 条（Q1/Q2）")


def _load(fp, default=None):
    if not os.path.exists(fp):
        return default
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception:
        return default


def compute_gain(paper):
    """篇级互证收益：N / checked / Q1-Q4 / fixed / C / e / R / Δ。

    权威划分：**Q1/Q2 以 `04_internal_corrections.json` 为准**（我方提取错误的权威清单）；
    标注表只用于覆盖率（checked）与 Q3/Q4；corrections 缺失时才退回用标注表的 Q1/Q2 线索。
    """
    wf = wf_of(paper)
    ann = _load(os.path.join(wf, "04_annotation_table.json")) or []
    img = _load(os.path.join(wf, "03_image_data.json")) or []
    cor = _load(os.path.join(wf, "04_internal_corrections.json")) or []
    N = len(img) if isinstance(img, list) else 0
    checked = 0
    qc = {k: 0 for k in Q_OK}
    ann_q12 = 0
    for r in ann if isinstance(ann, list) else []:
        if not isinstance(r, dict):
            continue
        if r.get("checked"):
            checked += 1
        f = r.get("finding") if isinstance(r.get("finding"), dict) else {}
        q = str(f.get("q") or (r.get("discrepancy") or {}).get("q") or r.get("q") or "").upper()
        if q in ("Q3", "Q4"):
            qc[q] += 1
        elif q in ("Q1", "Q2"):
            ann_q12 += 1

    seen = set()
    for it in cor if isinstance(cor, list) else []:
        if not isinstance(it, dict):
            continue
        q = str(it.get("q") or "").upper()
        if not re.match(r"^Q[1-2]$", q):
            q = "Q1" if str(it.get("category", "")).upper().startswith("E1") else "Q2"
        kk = (str(it.get("figure") or ""), str(it.get("field") or ""), str(it.get("before") or ""))
        if kk in seen:
            continue
        seen.add(kk)
        qc[q] += 1
    if not seen and ann_q12:                      # 无修正清单 → 退回标注表线索
        qc["Q1"] = ann_q12

    fixed = sum(1 for it in (cor if isinstance(cor, list) else [])
                if isinstance(it, dict) and it.get("after") not in (None, ""))
    e = qc["Q1"] + qc["Q2"]
    r_ = round(fixed / e, 4) if e else None
    return {
        "paper": os.path.basename(paper), "N": N, "checked": checked,
        "Q1": qc["Q1"], "Q2": qc["Q2"], "Q3": qc["Q3"], "Q4": qc["Q4"],
        "e": e, "fixed": fixed,
        "C": round(checked / N, 4) if N else None,
        "R": r_, "delta": round(fixed / N, 4) if N else None,
        "caliber": "C=checked/N 覆盖率；e=Q1+Q2 查出的自身错误；R=fixed/e 纠错率；delta=fixed/N 互证提升的准确率",
    }


def gen_gain(root):
    """篇级 05b_verification_gain.json + 批次级汇总。"""
    rows = []
    for paper in papers_of(root):
        g = compute_gain(paper)
        rows.append(g)
        json.dump(g, open(os.path.join(wf_of(paper), "05b_verification_gain.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"gain   {g['paper'][:42]:44s} N={g['N']} checked={g['checked']} "
              f"Q1={g['Q1']} Q2={g['Q2']} Q3={g['Q3']} Q4={g['Q4']} Δ={g['delta']}")
    N = sum(r["N"] for r in rows)
    checked = sum(r["checked"] for r in rows)
    e = sum(r["e"] for r in rows)
    fixed = sum(r["fixed"] for r in rows)
    agg = {
        "papers": len(rows), "N": N, "checked": checked,
        "Q1": sum(r["Q1"] for r in rows), "Q2": sum(r["Q2"] for r in rows),
        "Q3": sum(r["Q3"] for r in rows), "Q4": sum(r["Q4"] for r in rows),
        "e": e, "fixed": fixed,
        "C": round(checked / N, 4) if N else None,
        "R": round(fixed / e, 4) if e else None,
        "delta": round(fixed / N, 4) if N else None,
        "per_paper": rows,
        "caliber": "批次级互证收益（各篇并集）：delta = 互证把提取准确率抬高的比例",
    }
    return agg, rows


def main(root):
    ps = papers_of(root)
    if not ps:
        print(f"未找到论文夹：{root}\n（目录下需有 datamining/ 或 00_full_cleaned.md）")
        return 1
    master = collect(root)
    _ld = (os.path.join(wf_of(ps[0]), "_ledgers")
           if len(ps) == 1 else os.path.join(os.path.normpath(root), "_ledgers"))
    os.makedirs(_ld, exist_ok=True)
    kept = finalize(master)
    bname = os.path.basename(os.path.normpath(root))
    master_fp = os.path.join(_ld, f"{bname}_矛盾汇总台账.json")
    json.dump(kept, open(master_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cats = {}
    for it in kept:
        c = it["_norm"]["bucket"]
        cats[c] = cats.get(c, 0) + 1
    print(f"master 台账 {len(kept)} 条 → {master_fp}")
    print("归位分布:", cats, "（ledger=候选矛盾 / caliber=口径差 / corrections=我方错误 / consistent=一致）")

    gen_ledger(root, kept)
    gen_errata(root, kept)
    agg, _ = gen_gain(root)
    if len(ps) > 1:
        json.dump(agg, open(os.path.join(_ld, f"{bname}_互证收益汇总.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"批次收益汇总 → {os.path.join(_ld, bname + '_互证收益汇总.json')} "
              f"C={agg['C']} e={agg['e']} R={agg['R']} Δ={agg['delta']}")
    print("下一步：步骤 5B 派 fenton-arbiter 定级 → step5b_arbitrate_merge.py 回写 severity")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
