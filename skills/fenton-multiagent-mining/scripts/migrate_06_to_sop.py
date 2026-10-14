# -*- coding: utf-8 -*-
"""migrate_06_to_sop.py — 存量 06_schema 机械预迁移（SOP 补充环节，语义补齐交给子智能体）

只做「照抄类」工作，不做语义判断：
  1. 【数】字段（step9 的 NUM_KEYS 清单）旧字符串 → {value, unit, raw} 三元组
     （复用 step9 的 parse_value；解析失败的原串保留，记入 unparsed 留给子智能体）；
  2. 曲线绑定（degradation_profiles / levels 内）从 03/04 图账按 (figure, subfigure)
     join xy_axes → 补 x_unit / y_unit；
  3. levels[].metric_value 字符串 → 三元组（同 1）。
不做：变量 levels v2 重构、单位换算核对、metric 清理、定性判读 —— 全部留给子智能体。

每篇先备份 06_schema.pre_sop.json（已存在则不覆盖），写 datamining/06_migration_report.json。

用法:
  python migrate_06_to_sop.py B50 B110 B200 B202 C01     # 批次目录（相对当前目录）或论文夹
"""
import importlib.util
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NUM_RE = re.compile(r'^(fig(?:ure)?\.?\s*s?\d+)([a-z])?$', re.I)

_spec = importlib.util.spec_from_file_location(
    "step9mod", os.path.join(HERE, "step9_numeric_normalize.py"))
step9 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(step9)

NUM_KEYS = set(step9.CATALYST_NUM_KEYS) | set(step9.EXPERIMENT_NUM_KEYS) | set(step9.STABILITY_NUM_KEYS)
# 白名单外键名里的数值容器也升级：变量 metric_value / new_value
VALUE_KEYS = NUM_KEYS | {'metric_value', 'new_value', 'level_metric'}


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
        if os.path.isdir(d) and not p.startswith("_") and (
                os.path.isdir(os.path.join(d, "datamining")) or
                os.path.exists(os.path.join(d, "00_full_cleaned.md"))):
            out.append(d)
    return out


def norm_fig(s):
    s = re.sub(r"[^0-9a-z]", "", str(s or "").lower())
    return s.replace("figure", "fig") if s.startswith("figure") else s


def norm_sub(s):
    return re.sub(r"\s+", "", str(s or "").lower()) or "main"


def split_ref(ref):
    s = re.sub(r"\s+", "", str(ref or ""))
    m = NUM_RE.match(s)
    if m:
        return norm_fig(m.group(1)), (m.group(2) or "main")
    return norm_fig(ref), "main"


def index_axes(wf):
    """(fig, sub) → xy_axes；03 在前、04 覆盖（更权威）。"""
    idx = {}
    for name in ("03_image_data.json", "04_verified_image_data.json"):
        p = os.path.join(wf, name)
        try:
            recs = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for rec in recs if isinstance(recs, list) else []:
            ax = rec.get("xy_axes") if isinstance(rec, dict) else None
            if isinstance(ax, dict):
                idx[(norm_fig(rec.get("figure")), norm_sub(rec.get("subfigure")))] = ax
    return idx


def axes_for(idx, ref):
    fig, sub = split_ref(ref)
    ax = idx.get((fig, sub)) or idx.get((fig, "main"))
    if not isinstance(ax, dict):
        return None, None
    # 键位两代形态：xy_axes.x_axis/y_axis（新）与 axes.x/y（部分旧批次），都要吃
    x = ((ax.get("x_axis") or {}).get("unit") or (ax.get("x") or {}).get("unit"))
    y = ((ax.get("y_axis") or {}).get("unit") or (ax.get("y") or {}).get("unit"))
    return x, y


def to_triple(raw, field, unparsed_list, path):
    """旧字符串 → 三元组；失败返回 None（原串保留）。"""
    if isinstance(raw, dict) or not isinstance(raw, str) or not raw.strip():
        return None
    d = step9.parse_value(raw, field)
    if not d or d.get("unparsed") or d.get("value") is None:
        unparsed_list.append({"path": path, "raw": raw.strip()[:80]})
        return None
    t = {"value": d.get("value"), "unit": d.get("unit"), "raw": raw}
    for k in ("range", "uncertainty", "qualifier"):
        if d.get(k) not in (None, "", []):
            t[k] = d[k]
    return t


def upgrade_props(props, unparsed_list, prefix):
    """仅升级 NUM_KEYS 内的字符串字段；其余（【文】）不动。"""
    n = 0
    for k, v in list(props.items()):
        if k in VALUE_KEYS and isinstance(v, str):
            t = to_triple(v, k, unparsed_list, f"{prefix}{k}")
            if t:
                props[k] = t
                n += 1
    return n


def join_curve_axes(dps, idx, stat):
    """一条 profiles 记录 → 补 x_unit/y_unit。"""
    if not isinstance(dps, dict):
        return
    stat["curves"] += 1
    ref = dps.get("ref") or dps.get("source_figure") or ""
    x, y = axes_for(idx, ref)
    if x and not dps.get("x_unit"):
        dps["x_unit"] = x
        stat["joined"] += 1
    if y is not None and not dps.get("y_unit"):
        dps["y_unit"] = y
    if not dps.get("x_unit"):
        stat["no_axes"].append(ref)


def migrate(paper):
    wf = wf_of(paper)
    sch_p = os.path.join(wf, "06_schema.json")
    if not os.path.exists(sch_p):
        return None
    bak = os.path.join(wf, "06_schema.pre_sop.json")
    if not os.path.exists(bak):
        shutil.copy2(sch_p, bak)
    # 旧 06b（步骤八时代的字符串口径成品）已作废：改名退役，防止 step10/11 优先读到旧数据
    old_06b = os.path.join(wf, "06b_numeric_schema.json")
    if os.path.exists(old_06b):
        shutil.move(old_06b, os.path.join(wf, "06b_numeric_schema.pre_sop.json"))
    sch = json.load(open(sch_p, encoding="utf-8"))
    idx = index_axes(wf)
    rep = {"paper": os.path.basename(os.path.normpath(paper)),
           "tripled": 0, "unparsed": [], "curves": 0, "joined": 0, "no_axes": [],
           "vars_old_structure": 0}
    for ci, cat in enumerate(sch.get("catalysts", []) or []):
        rep["tripled"] += upgrade_props(cat.get("properties") or {}, rep["unparsed"], f"C{ci+1}.")
        for ei, e in enumerate(cat.get("experiments", []) or []):
            pre = f"C{ci+1}.E{ei+1}."
            rep["tripled"] += upgrade_props(e.get("properties") or {}, rep["unparsed"], pre)
            stab = e.get("stability")
            if isinstance(stab, dict):
                rep["tripled"] += upgrade_props(stab, rep["unparsed"], pre + "stability.")
            join_curve_axes(e.get("degradation_profiles"), idx, rep) \
                if isinstance(e.get("degradation_profiles"), dict) else None
            for dps in e.get("degradation_profiles", []) if isinstance(e.get("degradation_profiles"), list) else []:
                join_curve_axes(dps, idx, rep)
            for vi, v in enumerate(e.get("variable_experiments", []) or []):
                if not isinstance(v.get("levels"), list):
                    rep["vars_old_structure"] += 1
                for dps in v.get("degradation_profiles", []) if isinstance(v.get("degradation_profiles"), list) else []:
                    join_curve_axes(dps, idx, rep)
                for li, lv in enumerate(v.get("levels") or []):
                    if isinstance(lv, dict):
                        t = to_triple(lv.get("metric_value"), "metric_value",
                                      rep["unparsed"], f"{pre}V{vi+1}.L{li+1}.metric_value")
                        if t:
                            lv["metric_value"] = t
                            rep["tripled"] += 1
                        join_curve_axes(lv.get("degradation_profiles"), idx, rep) \
                            if isinstance(lv.get("degradation_profiles"), dict) else None
    json.dump(sch, open(sch_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(rep, open(os.path.join(wf, "06_migration_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return rep


def main():
    roots = sys.argv[1:] or ["."]
    total = {"papers": 0, "tripled": 0, "unparsed": 0, "curves": 0, "joined": 0, "vars_old": 0}
    for root in roots:
        for paper in papers_of(root):
            r = migrate(paper)
            if r is None:
                continue
            total["papers"] += 1
            for k, mk in (("tripled", "tripled"), ("curves", "curves"), ("joined", "joined"),
                          ("vars_old", "vars_old_structure")):
                total[k] += r.get(mk, 0)
            total["unparsed"] += len(r.get("unparsed", []))
            print(f"{r['paper'][:40]:42s} 三元组+{r['tripled']:3d} 未解析{len(r['unparsed']):3d} "
                  f"曲线{r['curves']:3d}(join {r['joined']:3d}) 旧结构变量{r['vars_old_structure']}")
    print(f"== 合计 {total['papers']} 篇 | 三元组 +{total['tripled']} | 未解析(留子智能体) {total['unparsed']} "
          f"| 曲线 {total['curves']}(join {total['joined']}) | 旧结构变量 {total['vars_old']} ==")


if __name__ == "__main__":
    main()
