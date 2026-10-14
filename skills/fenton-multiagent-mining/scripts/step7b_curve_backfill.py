# -*- coding: utf-8 -*-
"""step7b_curve_backfill.py — 步骤七 曲线回填（子智能体的绑定表 → 逐点数据）

子智能体只给绑定（点位不回贴，避免输出被截断），支持两种写法：
  ① 单条：  {"metric":"C/C0","ref":"Fig.7a","curve_label":"H2O2","level_value":"3"}
  ② 成组：  {"metric":"C/C0","bindings":[{"ref":"Fig.7a","curve_label":"H2O2"}, …]}
绑定表可带 `level_value`（该曲线对应的变量水平值）。变量水平 v2 结构为
`variable_experiments[].levels[].degradation_profiles`：条目缺失 `level_value` 时，
本脚本按**所在 level 项**的 `level_value` 自动补上，使曲线与水平机械对齐
（对账门第 ⑥ 项据此校验「曲线是否挂到了对应水平」）。
本脚本扫描 schema 里所有以 `*_profiles` / `*_curves` 结尾的键（含
`degradation_profiles` / `quenching_degradation_profiles` / `other.control_curves` 等自定义键），
按 `(figure, subfigure) + curve_label` 从
  <篇>/datamining/04_verified_image_data.json   ← 主来源（已互证裁决）
  <篇>/datamining/03_image_data.json            ← 兜底（verified 里找不到时）
取 points 回填，就地写回 `06_schema.json`。

用法：
  python step7b_curve_backfill.py <论文夹 或 批次根> [--dry-run]
产物：
  改写 <篇>/datamining/06_schema.json（首次运行前备份为 06_schema.preraw.json）
  <篇>/datamining/06_curve_bindings.json   回填报告（filled / already / unmatched / partial）

匹配不上**不静默丢**：原条目保留、标 qc.needs_review，并在报告与 stdout 列出原因。
"""
import argparse
import json
import os
import re
import shutil
import sys

NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
REF_RE = re.compile(r"^(fig(?:ure)?\.?\s*s?\d+)([a-z])?$", re.I)
CURVE_KEY_RE = re.compile(r"(profiles|curves)$", re.I)
DROP_KEYS = {"note", "n_curves", "n_points_each", "bindings"}


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def papers_of(root):
    root = os.path.abspath(root)
    if os.path.isdir(os.path.join(root, "datamining")) or os.path.exists(
            os.path.join(root, "00_full_cleaned.md")):
        return [root]
    out = []
    for p in sorted(os.listdir(root)):
        d = os.path.join(root, p)
        if not os.path.isdir(d) or p.startswith("_"):
            continue
        if os.path.isdir(os.path.join(d, "datamining")) or os.path.exists(
                os.path.join(d, "00_full_cleaned.md")):
            out.append(d)
    return out


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def norm_fig(s):
    s = str(s or "").strip().lower().replace(" ", "").replace(".", "")
    return "fig" + s[6:] if s.startswith("figure") else s


def norm_sub(s):
    return str(s or "").strip().lower() or "main"


def norm_label(s):
    return re.sub(r"[\s_]+", "", str(s or "").strip().lower())


def split_ref(ref, sub=""):
    s = str(ref or "").strip().replace(" ", "")
    m = REF_RE.match(s)
    if m:
        return m.group(1), (m.group(2) or norm_sub(sub))
    return str(ref or "").strip(), norm_sub(sub)


def parse_compact(s):
    pts = []
    for seg in re.split(r"[;；]", str(s)):
        nums = re.findall(NUM, seg)
        if len(nums) >= 2:
            try:
                pts.append([float(nums[0]), float(nums[1])])
            except ValueError:
                pass
    return pts


def index_sources(paper_dir):
    """(fig, sub) → 记录；verified 覆盖 03，故后写。"""
    wf = wf_of(paper_dir)
    idx = {}
    for fname in ("03_image_data.json", "04_verified_image_data.json"):
        for rec in load_json(os.path.join(wf, fname)) or []:
            if not isinstance(rec, dict) or not rec.get("curves"):
                continue
            r = dict(rec)
            r["_src"] = fname
            idx[(norm_fig(rec.get("figure")), norm_sub(rec.get("subfigure")))] = r
    return idx


def pick(rec, labels):
    curves = [c for c in (rec.get("curves") or []) if isinstance(c, dict)]
    if not labels:
        return curves, []
    got, miss = [], []
    for lb in labels:
        hit = next((c for c in curves if norm_label(c.get("label")) == norm_label(lb)), None)
        if hit is None:
            hit = next((c for c in curves
                        if norm_label(lb) and norm_label(lb) in norm_label(c.get("label"))), None)
        (got if hit else miss).append(hit or lb)
    return [g for g in got if g], miss


def backfill_entry(entry, idx, report, where, lvl=None):
    if not isinstance(entry, dict):
        return [entry]
    if entry.get("points") or entry.get("xs"):
        report["already"] += 1
        return [entry]
    if entry.get("points_compact"):
        pts = parse_compact(entry["points_compact"])
        if pts:
            e = dict(entry)
            e["points"] = pts
            e["n_points"] = len(pts)
            e["backfill_source"] = "points_compact"
            e.pop("points_compact", None)
            report["filled"] += 1
            return [e]

    ref = entry.get("ref") or entry.get("source_figure") or entry.get("figure") or ""
    fig, sub = split_ref(ref, entry.get("subfigure") or "")
    labels = entry.get("curve_labels") or entry.get("curve_label") or entry.get("label") or []
    if isinstance(labels, str):
        labels = [labels]
    if not ref:
        report["no_ref"].append({"where": where, "entry": str(entry)[:80]})
        return [entry]

    rec = idx.get((norm_fig(fig), norm_sub(sub)))
    if rec is None:
        report["unmatched"].append({"where": where, "ref": ref,
                                    "reason": "verified/03 中无此 (figure, subfigure)"})
        return [mark_review(entry)]

    curves, miss = pick(rec, labels)
    if not curves:
        report["unmatched"].append({"where": where, "ref": ref, "reason": f"标签未命中 {miss}"})
        return [mark_review(entry)]

    axes = rec.get("axes") or {}
    out = []
    for c in curves:
        e = {k: v for k, v in entry.items() if k != "ref"}
        e["points"] = c.get("points") or []
        e["n_points"] = len(e["points"])
        e["curve_label"] = c.get("label") or (entry.get("curve_label") or "")
        e["source_figure"] = f"{fig}{'' if norm_sub(sub) == 'main' else sub}"
        e.setdefault("metric", axes.get("label") or "C/C0")
        if axes.get("unit"):
            e.setdefault("y_unit", axes["unit"])
        e["backfill_source"] = rec["_src"]
        if e.get("level_value") in (None, "") and lvl not in (None, ""):
            e["level_value"] = lvl           # 按所在 level 补水平值，使曲线与水平机械对齐
            report["levels_bound"] += 1
        out.append(e)
    report["filled"] += max(1, len(out))
    if miss:
        report["partial"].append({"where": where, "ref": ref, "missed_labels": miss})
    return out


def mark_review(entry):
    e = dict(entry)
    qc = e.get("qc")
    if not isinstance(qc, dict):
        qc = {} if qc is None else {"note": str(qc)}
    qc["needs_review"] = True
    e["qc"] = qc
    return e


def handle_value(val, where, idx, report, lvl=None):
    items = val if isinstance(val, list) else [val]
    out = []
    for it in items:
        if not isinstance(it, dict):
            out.append(it)
            continue
        if isinstance(it.get("bindings"), list):
            base = {k: v for k, v in it.items() if k not in DROP_KEYS}
            for b in it["bindings"]:
                if isinstance(b, dict):
                    e = dict(base)
                    e.update({k: v for k, v in b.items() if k not in DROP_KEYS})
                    out += backfill_entry(e, idx, report, where, lvl)
        else:
            out += backfill_entry(it, idx, report, where, lvl)
    return out


def walk(node, path, idx, report, lvl=None):
    if isinstance(node, dict):
        # 变量水平项（含 level_value）→ 其内所有曲线继承该水平值（v2 水平对齐）
        if node.get("level_value") not in (None, ""):
            lvl = node.get("level_value")
        for k, v in list(node.items()):
            if v is None:
                continue
            if CURVE_KEY_RE.search(str(k)) and isinstance(v, (dict, list)):
                node[k] = handle_value(v, f"{path}{k}", idx, report, lvl)
            else:
                walk(v, f"{path}{k}.", idx, report, lvl)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}].", idx, report, lvl)


def process(paper_dir, dry=False):
    wf = wf_of(paper_dir)
    sch_p = os.path.join(wf, "06_schema.json")
    sch = load_json(sch_p)
    if sch is None:
        return None
    idx = index_sources(paper_dir)
    report = {"paper": os.path.basename(os.path.normpath(paper_dir)),
              "sources": sorted(f"{k[0]}/{k[1]}" for k in idx),
              "filled": 0, "already": 0, "levels_bound": 0, "unmatched": [], "partial": [],
              "no_ref": [], "verified_curves": sum(len(r.get("curves") or []) for r in idx.values())}
    walk(sch, "", idx, report)
    if not dry:
        if not os.path.exists(os.path.join(wf, "06_schema.preraw.json")):
            shutil.copy2(sch_p, os.path.join(wf, "06_schema.preraw.json"))
        with open(sch_p, "w", encoding="utf-8") as f:
            json.dump(sch, f, ensure_ascii=False, indent=1)
        with open(os.path.join(wf, "06_curve_bindings.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    papers = papers_of(a.root)
    if not papers:
        print(f"[warn] 未在 {a.root} 下找到论文夹")
        return 1
    bad = 0
    for p in papers:
        r = process(p, dry=a.dry_run)
        if r is None:
            print(f"{os.path.basename(os.path.normpath(p))[:42]:44s} 跳过（无 06_schema.json）")
            continue
        mark = "OK" if not (r["unmatched"] or r["no_ref"]) else "CHECK"
        bad += 0 if mark == "OK" else 1
        print(f"{r['paper'][:42]:44s} 回填 {r['filled']} / 已有 {r['already']} / "
              f"水平绑定 {r.get('levels_bound', 0)} / 未匹配 {len(r['unmatched'])} / "
              f"无 ref {len(r['no_ref'])} / 源曲线 {r['verified_curves']}  {mark}")
        for u in r["unmatched"][:5]:
            print(f"    ! {u['where']} ref={u['ref']} :: {u['reason']}")
        for u in r["partial"][:5]:
            print(f"    ~ {u['where']} ref={u['ref']} 部分标签未命中 {u['missed_labels']}")
        for u in r["no_ref"][:3]:
            print(f"    ? {u['where']} 无 ref：{u['entry']}")
    if a.dry_run:
        print("（--dry-run：未写盘）")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
