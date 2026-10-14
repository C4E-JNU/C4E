# -*- coding: utf-8 -*-
"""build_report.py — 生成数据挖掘总结报告工作簿（总览 / 分篇成绩 / 全部矛盾条目 / 失分清单 / 互证收益）

用法：
  python build_report.py <输入目录> [输出.xlsx]
输入目录下需要两个文件（缺则报错退出）：
  全局成绩汇总.json  [{batch, paper, P, R, F1, TP, FP, FN, universe, gt, exempt}, …]
  全部矛盾条目.json  [{批次, 文献, 图号/位置, 字段, 严重度, 载体来源, 跨文献, 正文值, 图值/冲突值, 裁决/采纳值, …}, …]
    —— 由 step5b_arbitrate_merge.py 产出（步骤 5B 裁决后）
可选（有则多一张「互证收益」表）：
  *_互证收益汇总.json  step4c_errata.py 产出的批次级互证收益（C / e / R / Δ）
输出：默认写到 <输入目录>/全库数据挖掘总结报告.xlsx（可用第二个参数覆盖）。

矛盾口径（2026-10-06 起，唯一来源 references/矛盾分类规范.md）：
  严重度 = A 数值偏差 / B 条件错位 / C 命名编号 / D 结论被自身数据否定 / E 其他（无法归类，兼待裁决兜底）
  + cross_paper（跨文献互斥） / caliber_variance（口径差，不算矛盾）两个布尔标记；
  旧的 category（E1/E2/E3/E5/图文/表文/text-text/字母级别）已废弃。
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
    print("[error] 缺 openpyxl：uv pip install openpyxl")
    sys.exit(2)

# ── 内联样式（professional 风格）────────────────────────────
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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = os.path.abspath(sys.argv[1])
    score_p = os.path.join(src, "全局成绩汇总.json")
    cts_p = os.path.join(src, "全部矛盾条目.json")
    for p in (score_p, cts_p):
        if not os.path.exists(p):
            print(f"[error] 缺输入文件：{p}")
            return 1
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(src, "全库数据挖掘总结报告.xlsx")
    score = json.load(open(score_p, encoding="utf-8"))
    cts = json.load(open(cts_p, encoding="utf-8"))

    wb = Workbook()
    wb.properties.creator = "C4EAI"

    # ── Sheet1 总览 ──────────────────────────────
    ws = wb.active
    ws.title = "总览"
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
    sev = Counter(c.get("严重度") or "E" for c in cts)   # unknown 已退场 → 兜底 E（其他/无法归类）
    cross_n = sum(1 for c in cts if c.get("跨文献"))
    cal_n = sum(1 for c in cts if c.get("口径差"))
    # 互证收益（步骤五 step4c 的批次汇总，可选）
    gains = []
    for fn in sorted(os.listdir(src)):
        if fn.endswith("互证收益汇总.json"):
            g = json.load(open(os.path.join(src, fn), encoding="utf-8"))
            if isinstance(g, dict) and "N" in g:
                g["_file"] = fn.replace("_互证收益汇总.json", "")
                gains.append(g)
    gN = sum(g.get("N") or 0 for g in gains)
    gchk = sum(g.get("checked") or 0 for g in gains)
    ge = sum(g.get("e") or 0 for g in gains)
    gf = sum(g.get("fixed") or 0 for g in gains)
    gC = round(gchk / gN, 4) if gN else None
    gR = round(gf / ge, 4) if ge else None
    gD = round(gf / gN, 4) if gN else None
    overview = [
        ("流水线", "非均相 Fenton 文献数据挖掘（MinerU 产物 → 08_paper_report）", ""),
        ("批次 / 文献", f"{len(batches)} 批 / {len(score)} 篇", "来自 全局成绩汇总.json"),
        ("标量字段 精确率 P", round(P, 4), f"提错（FP）合计 {fp} 条"),
        ("标量字段 召回率 R", round(R, 4), f"漏提（FN）合计 {fn} 条"),
        ("标量字段 F1", round(F1, 4), "恒等式校验通过子集统计"),
        ("F1 分布", (f"min {f1s[0]:.3f} / 中位 {f1s[len(f1s) // 2]:.3f} / max {f1s[-1]:.3f}"
                     if f1s else "无"),
         "含 GT 评估的篇目"),
        ("F1<0.95 篇数", sum(1 for f in f1s if f < 0.95), "主要为覆盖缺口（归档缺图/表格漏扫）"),
        ("矛盾条目总数", len(cts), "候选矛盾（严重度 A/B/C/D/E；E=其他·无法归类，含待裁决），已去重合并"),
        ("矛盾严重度分布", " / ".join(f"{k} {v}" for k, v in sorted(sev.items())) or "无",
         "A 数值偏差 / B 条件错位 / C 命名编号 / D 结论被自身数据否定 / E 其他（无法归类）"),
        ("跨文献矛盾 / 口径差", f"{cross_n} 条 / {cal_n} 条",
         "跨文献=两已发表来源互斥；口径差不算矛盾，仅标注解释"),
        ("互证覆盖率 C", gC, "= checked / N，互证实际比对到的记录比例"),
        ("互证查出自身错误 e", ge, "Q1 我方读图错 + Q2 我方读文/读表错"),
        ("互证纠错率 R", gR, "= fixed / e，查出的错误中已修正写回的比例"),
        ("互证准确率提升 Δ", gD, "= fixed / N，互证把提取准确率抬高的比例"),
    ]
    write_table(ws, "非均相 Fenton 文献数据挖掘 — 全库总结总览",
                ["项目", "数值", "说明"], overview, [26, 70, 60],
                note="统计口径：gt_evaluation.json 各篇评分宇宙（GT 标注 − 豁免）；恒等式：宇宙 = TP+FP+FN。")

    # ── Sheet2 分篇成绩 ───────────────────────────
    ws2 = wb.create_sheet("分篇成绩")
    rows2 = [[r.get("batch"), str(r.get("paper", ""))[:60], r.get("P"), r.get("R"), r.get("F1"),
              r.get("TP"), r.get("FP"), r.get("FN"), r.get("universe"), r.get("gt"), r.get("exempt")]
             for r in sorted(score, key=lambda x: (x.get("F1") is None, x.get("F1") or 0))]
    write_table(ws2, "分篇 GT 评估成绩（按 F1 升序）",
                ["批次", "文献", "P", "R", "F1", "TP", "FP", "FN", "评分宇宙", "GT标注", "豁免"],
                rows2, [8, 55, 8, 8, 8, 7, 7, 7, 9, 9, 8],
                note=f"共 {len(rows2)} 篇有量化成绩。")

    # ── Sheet3 全部矛盾条目 ───────────────────────
    ws3 = wb.create_sheet("全部矛盾条目")
    rows3 = [[c.get("批次"), c.get("文献"), c.get("图号/位置"), c.get("字段"),
              c.get("严重度"), c.get("载体来源"), "是" if c.get("跨文献") else "",
              c.get("正文值"), c.get("图值/冲突值"), c.get("裁决/采纳值"), c.get("置信度"),
              c.get("正文引文"), c.get("理由")] for c in cts]
    write_table(ws3, f"全库矛盾条目清单（{len(cts)} 条，逐条列出）",
                ["批次", "文献", "图号/位置", "字段", "严重度", "载体来源", "跨文献",
                 "正文值", "图值/冲突值", "裁决/采纳值", "置信度", "正文引文", "裁决理由"],
                rows3, [7, 30, 16, 26, 8, 16, 7, 12, 12, 14, 10, 55, 55],
                note="严重度：A=数值偏差、B=条件/归属错位、C=命名编号计数错、D=结论被自身数据否定、"
                     "E=其他（无法归类，含待裁决，需人工）。载体来源（text-vs-figure / text-vs-table / text-vs-text）"
                     "只用于回溯定位，不作分类。口径差（矿化率 vs 去除率等）不算矛盾，见 跨文献矛盾对.json。")

    # ── Sheet4 失分与低分清单 ─────────────────────
    ws4 = wb.create_sheet("失分与低分清单")
    low = [r for r in score if r.get("F1") is not None and r["F1"] < 0.95]
    rows4 = [[r.get("batch"), str(r.get("paper", ""))[:60], r.get("F1"), r.get("FP") or 0,
              r.get("FN") or 0, r.get("universe")] for r in sorted(low, key=lambda x: x["F1"])]
    write_table(ws4, f"F1 < 0.95 的篇目（{len(low)} 篇）",
                ["批次", "文献", "F1", "FP", "FN", "评分宇宙"], rows4, [8, 55, 8, 7, 7, 9],
                note="失分模式：① 设计矩阵非最优水平漏读；② 归档缺图致整块缺失；③ 对照组漏记；④ 文献自身矛盾计分。")

    # ── Sheet5 互证收益（步骤五 step4c 的批次汇总）──
    if gains:
        ws5 = wb.create_sheet("互证收益")
        rows5 = [[g.get("_file"), g.get("papers"), g.get("N"), g.get("checked"),
                  g.get("Q1"), g.get("Q2"), g.get("Q3"), g.get("Q4"),
                  g.get("e"), g.get("fixed"), g.get("C"), g.get("R"), g.get("delta")]
                 for g in gains]
        write_table(ws5, f"图文/表文互证收益（{len(gains)} 批）",
                    ["批次", "篇数", "记录数 N", "已比对 checked", "Q1 读图错", "Q2 读文错",
                     "Q3 口径差", "Q4 一致", "e=Q1+Q2", "fixed 已修正",
                     "覆盖率 C", "纠错率 R", "准确率提升 Δ"],
                    rows5, [10, 7, 10, 13, 10, 10, 10, 10, 10, 11, 10, 10, 12],
                    note="C=checked/N（互证覆盖率）；e=Q1+Q2（互证查出的我方提取错误数）；"
                         "R=fixed/e（纠错率）；Δ=fixed/N（互证把提取准确率抬高的比例，即这一步的价值）。"
                         "Q3 口径差、Q4 一致都不计入矛盾库。")

    wb.save(out)
    print("saved", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
