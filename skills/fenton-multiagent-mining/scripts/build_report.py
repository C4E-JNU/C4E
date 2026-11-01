# -*- coding: utf-8 -*-
"""build_report.py — build the data-mining summary workbook (overview / per-paper scores / all contradictions / loss list / verification gain)

Usage:
  python build_report.py <input directory> [output.xlsx]
The input directory must contain two files (missing → error exit):
  global_score_summary.json  [{batch, paper, P, R, F1, TP, FP, FN, universe, gt, exempt}, …]
  all_contradiction_entries.json  [{group, paper, figure_location, field, severity, evidence_source, cross_paper,
                                    text_value, conflict_value, adopted_value, …}, …]
    — produced by step5b_arbitrate_merge.py (after the Step 5B adjudication)
Optional (adds a "Verification gain" sheet):
  *_verification_gain_summary.json  cross-paper verification gain (C / e / R / Δ) produced by step4c_errata.py
Output: by default <input directory>/corpus_datamining_summary.xlsx (override with the second argument).

Contradiction caliber (since 2026-10-06, single source references/contradiction_taxonomy.md):
  severity = A numerical deviation / B condition or attribution mismatch / C naming or numbering / D conclusion refuted by own data / E other (unclassifiable, also the pending fallback)
  + two booleans: cross_paper (cross-paper exclusion) / caliber_variance (caliber difference, not a contradiction);
  the legacy category (E1/E2/E3/E5/figure-text/table-text/text-text/letter levels) is deprecated.
Legacy filenames and Chinese keys are still accepted so historical reports keep working.
"""
import json
import os
import sys
from collections import Counter

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:                                    # pragma: no cover
    print("[error] openpyxl is missing: uv pip install openpyxl")
    sys.exit(2)

# ── inline styles (professional look) ────────────────────────
FONT_CJK = "Microsoft YaHei"
PRIMARY = "1F4E79"
HEADER_TEXT = "FFFFFF"
NEUTRAL_100 = "F2F2F2"
NEUTRAL_600 = "808080"
NEUTRAL_900 = "262626"


def write_table(ws, title, headers, rows, widths=None, note=None):
    ws.sheet_view.showGridLines = False
    ws.cell(row=2, column=2, value=title).font = Font(name=FONT_CJK, size=14, bold=True, color=PRIMARY)
    hr = 4
    for j, h in enumerate(headers, 2):
        c = ws.cell(row=hr, column=j, value=h)
        c.font = Font(name=FONT_CJK, bold=True, color=HEADER_TEXT)
        c.fill = PatternFill("solid", fgColor=PRIMARY)
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for i, row in enumerate(rows, hr + 1):
        fill = PatternFill("solid", fgColor=NEUTRAL_100) if i % 2 else None
        for j, v in enumerate(row, 2):
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)[:300]
            elif v is not None and not isinstance(v, (str, int, float, bool)):
                v = str(v)
            c = ws.cell(row=i, column=j, value=v)
            c.font = Font(name=FONT_CJK, size=10, color=NEUTRAL_900)
            c.alignment = Alignment(vertical="top", wrap_text=True)
            if fill:
                c.fill = fill
    if widths:
        for j, w in enumerate(widths, 2):
            ws.column_dimensions[get_column_letter(j)].width = w
    if note:
        ws.cell(row=hr + len(rows) + 2, column=2,
                value=note).font = Font(name=FONT_CJK, size=9, color=NEUTRAL_600)
    ws.freeze_panes = ws.cell(row=hr + 1, column=2).coordinate


def _first(d, *keys, default=""):
    """Read the first present key (tolerates the legacy Chinese keys)."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _find(src, canonical, legacy):
    for name in (canonical, legacy):
        p = os.path.join(src, name)
        if os.path.exists(p):
            return p
    return os.path.join(src, canonical)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = os.path.abspath(sys.argv[1])
    score_p = _find(src, "global_score_summary.json", "全局成绩汇总.json")
    cts_p = _find(src, "all_contradiction_entries.json", "全部矛盾条目.json")
    for p in (score_p, cts_p):
        if not os.path.exists(p):
            print(f"[error] missing input file: {p}")
            return 1
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(src, "corpus_datamining_summary.xlsx")
    score = json.load(open(score_p, encoding="utf-8"))
    cts = json.load(open(cts_p, encoding="utf-8"))

    wb = Workbook()
    wb.properties.creator = "C4EAI"

    # ── Sheet 1: overview ────────────────────────
    ws = wb.active
    ws.title = "Overview"
    ok = [r for r in score
          if r.get("universe") and abs((r.get("TP") or 0) + (r.get("FP") or 0)
                                       + (r.get("FN") or 0) - r["universe"]) <= 2]
    tp = sum(r.get("TP") or 0 for r in ok)
    fp = sum(r.get("FP") or 0 for r in ok)
    fn = sum(r.get("FN") or 0 for r in ok)
    P = tp / (tp + fp) if tp + fp else 0
    R = tp / (tp + fn) if tp + fn else 0
    F1 = 2 * P * R / (P + R) if P + R else 0
    f1s = sorted(r["F1"] for r in score if r.get("F1") is not None)
    batches = sorted({r.get("batch") for r in score if r.get("batch")})
    sev = Counter(_first(c, "severity", "严重度", default="E") or "E" for c in cts)   # unknown retired → fallback E (other/unclassifiable)
    cross_n = sum(1 for c in cts if _first(c, "cross_paper", "跨文献", default=False))
    cal_n = sum(1 for c in cts if _first(c, "caliber_variance", "口径差", default=False))
    # verification gain (Step 5 step4c summary, optional)
    gains = []
    for fname in sorted(os.listdir(src)):
        if fname.endswith("verification_gain_summary.json") or fname.endswith("互证收益汇总.json"):
            g = json.load(open(os.path.join(src, fname), encoding="utf-8"))
            if isinstance(g, dict) and "N" in g:
                g["_file"] = fname.replace("_verification_gain_summary.json", "").replace("_互证收益汇总.json", "")
                gains.append(g)
    gN = sum(g.get("N") or 0 for g in gains)
    gchk = sum(g.get("checked") or 0 for g in gains)
    ge = sum(g.get("e") or 0 for g in gains)
    gf = sum(g.get("fixed") or 0 for g in gains)
    gC = round(gchk / gN, 4) if gN else None
    gR = round(gf / ge, 4) if ge else None
    gD = round(gf / gN, 4) if gN else None
    overview = [
        ("Pipeline", "Heterogeneous Fenton literature data mining (MinerU products → 08_paper_report)", ""),
        ("Groups / papers", f"{len(batches)} groups / {len(score)} papers", "from global_score_summary.json"),
        ("Scalar-field precision P", round(P, 4), f"wrong extractions (FP) total {fp}"),
        ("Scalar-field recall R", round(R, 4), f"missed extractions (FN) total {fn}"),
        ("Scalar-field F1", round(F1, 4), "computed over the subset that passes the identity check"),
        ("F1 distribution", (f"min {f1s[0]:.3f} / median {f1s[len(f1s) // 2]:.3f} / max {f1s[-1]:.3f}"
                             if f1s else "none"),
         "papers with a ground-truth evaluation"),
        ("Papers with F1<0.95", sum(1 for f in f1s if f < 0.95), "mainly coverage gaps (missing figures in the archive / unscanned tables)"),
        ("Contradiction entries", len(cts), "candidate contradictions (severity A/B/C/D/E; E = other · unclassifiable, including pending), deduplicated and merged"),
        ("Severity distribution", " / ".join(f"{k} {v}" for k, v in sorted(sev.items())) or "none",
         "A numerical deviation / B condition mismatch / C naming and numbering / D conclusion refuted by own data / E other (unclassifiable)"),
        ("Cross-paper / caliber difference", f"{cross_n} / {cal_n}",
         "cross-paper = two published sources disagree; caliber differences are not contradictions, only annotated"),
        ("Verification coverage C", gC, "= checked / N, the share of records actually compared by verification"),
        ("Verification-found own errors e", ge, "Q1 our figure misreads + Q2 our text/table misreads"),
        ("Verification correction rate R", gR, "= fixed / e, the share of found errors corrected and written back"),
        ("Verification accuracy gain Δ", gD, "= fixed / N, the accuracy lift contributed by verification"),
    ]
    write_table(ws, "Heterogeneous Fenton literature data mining — corpus summary",
                ["Item", "Value", "Note"], overview, [30, 70, 66],
                note="Caliber: the per-paper scoring universe in gt_evaluation.json (GT labels − exemptions); identity: universe = TP+FP+FN.")

    # ── Sheet 2: per-paper scores ────────────────
    ws2 = wb.create_sheet("Per-paper scores")
    rows2 = [[r.get("batch"), str(r.get("paper", ""))[:60], r.get("P"), r.get("R"), r.get("F1"),
              r.get("TP"), r.get("FP"), r.get("FN"), r.get("universe"), r.get("gt"), r.get("exempt")]
             for r in sorted(score, key=lambda x: (x.get("F1") is None, x.get("F1") or 0))]
    write_table(ws2, "Per-paper ground-truth scores (ascending F1)",
                ["Group", "Paper", "P", "R", "F1", "TP", "FP", "FN", "Score universe", "GT labels", "Exempt"],
                rows2, [9, 55, 8, 8, 8, 7, 7, 7, 11, 10, 8],
                note=f"{len(rows2)} papers carry a quantitative score.")

    # ── Sheet 3: all contradictions ──────────────
    ws3 = wb.create_sheet("All contradictions")
    rows3 = [[_first(c, "group", "分组", "批次"), _first(c, "paper", "文献"),
              _first(c, "figure_location", "图号/位置"), _first(c, "field", "字段"),
              _first(c, "severity", "严重度"), _first(c, "evidence_source", "载体来源"),
              "yes" if _first(c, "cross_paper", "跨文献", default=False) else "",
              _first(c, "text_value", "正文值"), _first(c, "conflict_value", "图值/冲突值"),
              _first(c, "adopted_value", "裁决/采纳值"), _first(c, "confidence", "置信度"),
              _first(c, "text_quote", "正文引文"), _first(c, "reason", "理由")] for c in cts]
    write_table(ws3, f"All contradiction entries ({len(cts)}, listed individually)",
                ["Group", "Paper", "Figure/location", "Field", "Severity", "Evidence source", "Cross-paper",
                 "Text value", "Conflicting value", "Adopted value", "Confidence", "Text quote", "Reason"],
                rows3, [7, 30, 16, 26, 8, 18, 9, 12, 14, 14, 10, 55, 55],
                note="Severity: A numerical deviation, B condition/attribution mismatch, C naming/numbering/count error, "
                     "D conclusion refuted by own data, E other (unclassifiable, including pending, needs a human). "
                     "The evidence source (text-vs-figure / text-vs-table / text-vs-text) is used only for traceability, never as a class. "
                     "Caliber differences (mineralization vs. removal, etc.) are not contradictions; see cross_paper_contradiction_pairs.json.")

    # ── Sheet 4: losses and low scores ───────────
    ws4 = wb.create_sheet("Loss list")
    low = [r for r in score if r.get("F1") is not None and r["F1"] < 0.95]
    rows4 = [[r.get("batch"), str(r.get("paper", ""))[:60], r.get("F1"), r.get("FP") or 0,
              r.get("FN") or 0, r.get("universe")] for r in sorted(low, key=lambda x: x["F1"])]
    write_table(ws4, f"Papers with F1 < 0.95 ({len(low)})",
                ["Group", "Paper", "F1", "FP", "FN", "Score universe"], rows4, [9, 55, 8, 7, 7, 11],
                note="Loss patterns: (1) a non-optimal level of the design matrix is unread; (2) a missing figure removes a whole block; (3) the control group is not recorded; (4) the paper's own contradiction costs points.")

    # ── Sheet 5: verification gain (the step4c summary from Step 5) ──
    if gains:
        ws5 = wb.create_sheet("Verification gain")
        rows5 = [[g.get("_file"), g.get("papers"), g.get("N"), g.get("checked"),
                  g.get("Q1"), g.get("Q2"), g.get("Q3"), g.get("Q4"),
                  g.get("e"), g.get("fixed"), g.get("C"), g.get("R"), g.get("delta")]
                 for g in gains]
        write_table(ws5, f"Figure/table vs. text verification gain ({len(gains)} groups)",
                    ["Group", "Papers", "Records N", "Compared checked", "Q1 figure misreads", "Q2 text misreads",
                     "Q3 caliber differences", "Q4 consistent", "e=Q1+Q2", "fixed",
                     "Coverage C", "Correction rate R", "Accuracy gain Δ"],
                    rows5, [10, 7, 10, 14, 14, 14, 15, 12, 10, 8, 10, 13, 13],
                    note="C=checked/N (verification coverage); e=Q1+Q2 (our own extraction errors found by verification); "
                         "R=fixed/e (correction rate); Δ=fixed/N (the accuracy lift contributed by verification, i.e. the value of this step). "
                         "Q3 caliber differences and Q4 agreement never enter the contradiction store.")

    wb.save(out)
    print("saved", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
