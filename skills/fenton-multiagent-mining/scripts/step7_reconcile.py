# -*- coding: utf-8 -*-
"""step7_reconcile.py — 步骤七 对账门（清点数 ↔ 填充数 / 字段名 / fill_stats / 曲线归宿）

用法：
  python step7_reconcile.py <论文夹 或 批次根>
产物：
  单篇 → <论文夹>/datamining/07_reconciliation.json
  批次 → <批次根>/_ledgers/07_reconciliation.json

六项校验（逐篇）：
  ① counts      05_node_inventory 清点数 == 06_schema 填充数（催化剂 / 实验 / 变量扫描组）
  ② field_names properties 内键名必须在字段清单内（催化剂 45 / 实验 41 + stability 6）
  ③ fill_stats  from_text + from_image + from_errata + blank == total_fields
  ④ pollutant   pollutant_name 必须单值（并列多污染物 = 拆分失败，必须重填）
  ⑤ curves      04_verified_image_data 里每条 curve 在 schema 中有归宿（反向多出的也报）
  ⑥ levels      变量 levels[] 每项须同时有 level_value 与 metric_name/metric_value（塞了水平却
                不给对应指标 = FAIL）；曲线 level_value 须与所在水平一致；levels 项数须 >=
                05_node_inventory 的 declared_levels 数（少 = 漏水平 FAIL，多 = WARN）。
                旧结构（变量无 levels）跳过本项，保证历史数据仍可对账。
"""
import json
import os
import re
import sys

CATALYST_FIELDS = [
    "catalyst_name", "chemical_formula", "metal_elements", "metal_type", "metal_interaction",
    "metal_content", "support_material", "catalyst_form", "morphology", "synthesis_method",
    "precursors", "calcination_temp", "calcination_time", "calcination_heating_rate",
    "calcination_atmosphere", "post_treatment", "crystal_phase", "crystal_system", "space_group",
    "lattice_a", "lattice_c", "crystallite_size", "lattice_spacing_nm", "particle_size",
    "impurity_phase", "specific_surface_area", "pore_volume", "pore_size", "adsorption_capacity",
    "pH_pzc", "zeta_potential", "oxygen_vacancy", "surface_functional_groups",
    "element_oxidation_states", "raman_ID_IG", "band_gap", "redox_activity",
    "valence_ratio_description", "eis_Rct", "saturation_magnetization", "char_PL_intensity",
    "characterization_methods", "purity", "supplier", "other",
]
EXPERIMENT_FIELDS = [
    "pollutant_name", "pollutant_class", "chemical_formula", "oxidant_type",
    "oxidant_concentration", "oxidant_dosage", "initial_pollutant_conc", "catalyst_dosage",
    "solution_volume", "initial_pH", "temperature", "reaction_time", "coexisting_substances",
    "light_condition", "reaction_system", "process_mode", "agitation", "kinetics_model",
    "medium", "replicates", "oxidant_pollutant_molar_ratio", "catalyst_pollutant_mass_ratio",
    "other_condition", "removal_efficiency", "decolorization_efficiency", "rate_constant_k",
    "rate_constant_R2", "toc_removal", "cod_removal", "adsorption_contribution",
    "degradation_products", "dominant_ROS", "ros_detection_method", "epr_signal_desc",
    "xps_after_reaction", "degradation_pathway", "toxicity_change", "pms_alone_control",
    "h2o2_generation_consumption", "chloride_release", "catalytic_activity",
]
STABILITY_FIELDS = ["cycle_count", "initial_efficiency", "final_efficiency",
                    "efficiency_retention", "metal_leaching_conc", "structure_after_rxn"]

# 并列污染物特征符（命中即视为「一实验一污染物」被违反）
MULTI_POLLUTANT = ["、", "和", " + ", "+", " respectively", "respectively", "混合", " and "]
# 模板自带的收纳键：允许出现在 properties 内，不计入 45/41 字段数
EXTRA_ALLOWED = {"other"}
# 曲线容器键（与 step7b_curve_backfill.py 同一口径）
CURVE_KEY_RE = re.compile(r"(profiles|curves)$", re.I)
REF_RE = re.compile(r"^(fig(?:ure)?\.?\s*s?\d+)([a-z])?$", re.I)


# ────────────────────────── 基础工具 ──────────────────────────
def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def papers_of(root):
    """单篇（root 自身是论文夹）或批次（root 下若干论文夹）自适应。"""
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
    s = str(s or "").strip().lower()
    return s or "main"


def norm_label(s):
    return re.sub(r"[\s_]+", "", str(s or "").strip().lower())


def split_ref(ref, sub=""):
    """'Fig.7a' → ('Fig.7','a')；'Fig.S1' → ('Fig.S1','main')。"""
    s = str(ref or "").strip().replace(" ", "")
    m = REF_RE.match(s)
    if m:
        return m.group(1), (m.group(2) or norm_sub(sub))
    return str(ref or "").strip(), norm_sub(sub)


# ────────────────────────── 校验 ──────────────────────────
def schema_curve_index(schema):
    """递归收集 schema 中所有曲线容器的 (fig, sub, label)；另统计缺 points 的条数。

    覆盖 `degradation_profiles` 及自定义键（`quenching_degradation_profiles`、
    `other.control_curves`、`system_control_profiles` …）——与回填脚本同一口径。
    """
    seen, empty, total = set(), 0, 0

    def handle(dp):
        nonlocal empty, total
        items = dp if isinstance(dp, list) else ([dp] if isinstance(dp, dict) else [])
        for q in items:
            if not isinstance(q, dict):
                continue
            total += 1
            fig, sub = split_ref(q.get("source_figure") or q.get("ref") or q.get("figure") or "",
                                 q.get("subfigure") or "")
            lab = q.get("curve_label") or q.get("label") or ""
            pts = q.get("points") or q.get("xs")
            if not pts:
                empty += 1
            if fig:
                seen.add((norm_fig(fig), norm_sub(sub), norm_label(lab)))

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if v is None:
                    continue
                if CURVE_KEY_RE.search(str(k)) and isinstance(v, (dict, list)):
                    handle(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return seen, empty, total


def verified_curve_index(verified):
    """收集 04_verified_image_data 里所有 curve 的 (fig, sub, label)。"""
    seen = set()
    for rec in verified or []:
        if not isinstance(rec, dict):
            continue
        fig, sub = rec.get("figure", ""), rec.get("subfigure", "main")
        for cu in rec.get("curves", []) or []:
            if not isinstance(cu, dict):
                continue
            seen.add((norm_fig(fig), norm_sub(sub), norm_label(cu.get("label") or cu.get("curve_label"))))
    return seen


def check(paper):
    wf = wf_of(paper)
    inv = load_json(os.path.join(wf, "05_node_inventory.json"))
    sch = load_json(os.path.join(wf, "06_schema.json"))
    if inv is None or sch is None:
        return None
    name = os.path.basename(os.path.normpath(paper))
    inv_c, inv_e, inv_v = (len(inv.get("catalysts", []) or []),
                           len(inv.get("experiments", []) or []),
                           len(inv.get("variables", []) or []))
    sch_c = len(sch.get("catalysts", []) or [])
    sch_e = sum(len(c.get("experiments", []) or []) for c in sch.get("catalysts", []) or [])
    sch_v = sum(len(e.get("variable_experiments", []) or [])
                for c in sch.get("catalysts", []) or [] for e in c.get("experiments", []) or [])

    fails = []
    warns = []

    # ① 计数（少 = 填充缺口，FAIL；多 = 步骤六清点可能漏登，WARN 待复核）
    for label, a, b in (("催化剂", inv_c, sch_c), ("实验", inv_e, sch_e), ("变量", inv_v, sch_v)):
        if b < a:
            fails.append(f"count:{label}缺 {a - b}（清单 {a} → 填充 {b}）")
        elif b > a:
            warns.append(f"{label}多 {b - a}（清点 {a} → 填充 {b}，步骤六可能漏登记，请复核 05）")

    # ② 字段名（含顶层允许键）
    bad_keys = []
    for c in sch.get("catalysts", []) or []:
        props = c.get("properties") or {}
        for k in props:
            if k not in CATALYST_FIELDS:
                bad_keys.append(f"催化剂.{k}")
        for e in c.get("experiments", []) or []:
            for k in (e.get("properties") or {}):
                if k not in EXPERIMENT_FIELDS and k not in EXTRA_ALLOWED:
                    bad_keys.append(f"实验.{k}")
            for k in (e.get("stability") or {}):
                if k not in STABILITY_FIELDS:
                    bad_keys.append(f"stability.{k}")
    if bad_keys:
        fails.append("字段名非法: " + ", ".join(sorted(set(bad_keys))[:12]))

    # ③ fill_stats（只累加四项口径键；子智能体附带的 total_fields 等仅作说明，不计入求和）
    FS_KEYS = ("from_text", "from_image", "from_errata", "blank")
    total_fields = len(CATALYST_FIELDS) * sch_c + (len(EXPERIMENT_FIELDS) + len(STABILITY_FIELDS)) * sch_e
    fs = sch.get("fill_stats") or {}
    fs_sum = None
    if fs:
        lack = [k for k in FS_KEYS if k not in fs]
        if lack:
            fails.append("fill_stats 缺项: " + ", ".join(lack))
        fs_sum = sum(fs.get(k, 0) for k in FS_KEYS if isinstance(fs.get(k), (int, float)))
        if fs_sum != total_fields:
            fails.append(f"fill_stats 四项和 {fs_sum} ≠ 总字段数 {total_fields}")
    else:
        fails.append("fill_stats 缺失")

    # ④ 污染物单值
    multi = []
    for c in sch.get("catalysts", []) or []:
        for e in c.get("experiments", []) or []:
            pn = e.get("pollutant_name") or (e.get("properties") or {}).get("pollutant_name") or ""
            if pn and any(t in str(pn) for t in MULTI_POLLUTANT):
                multi.append(str(pn)[:40])
    if multi:
        fails.append("一实验一污染物被违反: " + " | ".join(multi[:5]))

    # ⑤ 曲线归宿
    curve_stat = None
    verified = load_json(os.path.join(wf, "04_verified_image_data.json"), default=None)
    if verified is not None:
        v_set = verified_curve_index(verified)
        s_set, empty_pts, n_curve = schema_curve_index(sch)
        lost = sorted(v_set - s_set)
        extra = sorted(s_set - v_set)
        curve_stat = {"verified": len(v_set), "schema": n_curve,
                      "lost": ["/".join(x) for x in lost], "extra": ["/".join(x) for x in extra],
                      "no_points": empty_pts}
        if lost:
            fails.append(f"曲线未落位 {len(lost)} 条: " + "; ".join("/".join(x) for x in lost[:6]))
        if empty_pts:
            fails.append(f"曲线缺 points {empty_pts} 条（曲线回填未跑？）")

    # ⑥ 变量水平（v2）：levels[] 每项须同时有 level_value 与指标；曲线 level_value 与所在项一致；
    #    levels 项数 >= 步骤六登记的 declared_levels 数（少 = 漏水平）
    lvl_stat = {"groups": 0, "with_levels": 0, "levels": 0, "declared": 0,
                "missing_level_value": [], "missing_metric": [], "curve_mismatch": [],
                "declared_gap": [], "beyond_declared": []}
    inv_decl = {}
    for iv in (inv.get("variables", []) or []):
        if isinstance(iv, dict) and iv.get("declared_levels"):
            inv_decl.setdefault((str(iv.get("exp_id") or "").lower(),
                                 str(iv.get("variable_parameter") or "").lower()), []).append(iv)
    for c in sch.get("catalysts", []) or []:
        for ei, e in enumerate(c.get("experiments", []) or []):
            if not isinstance(e, dict):
                continue
            ekey = f"E{ei + 1}".lower()
            for vi, v in enumerate(e.get("variable_experiments", []) or []):
                if not isinstance(v, dict):
                    continue
                lvl_stat["groups"] += 1
                vpar = str(v.get("variable_parameter") or "")
                levels = v.get("levels")
                if not isinstance(levels, list) or not levels:
                    # 旧结构（无 levels）：至少要有 metric_value / new_value，否则提示
                    if not (v.get("metric_value") or v.get("new_value")):
                        lvl_stat["missing_metric"].append(
                            f"V{vi + 1}:{vpar or '-'}（无 levels 且无指标值）")
                    continue
                lvl_stat["with_levels"] += 1
                for li, lv in enumerate(levels):
                    if not isinstance(lv, dict):
                        lvl_stat["missing_level_value"].append(f"V{vi + 1}.L{li + 1}")
                        continue
                    lvl_stat["levels"] += 1
                    lp = f"V{vi + 1}.L{li + 1}"
                    if lv.get("level_value") in (None, ""):
                        lvl_stat["missing_level_value"].append(lp)
                    if lv.get("metric_value") in (None, "") and lv.get("metric_name") in (None, ""):
                        lvl_stat["missing_metric"].append(f"{lp}（水平 {lv.get('level_value')}）")
                    dps = lv.get("degradation_profiles")
                    dps = dps if isinstance(dps, list) else ([dps] if isinstance(dps, dict) else [])
                    for q in dps:
                        if not isinstance(q, dict):
                            continue
                        ql = q.get("level_value")
                        if ql in (None, ""):
                            continue
                        if str(ql).strip() != str(lv.get("level_value")).strip():
                            lvl_stat["curve_mismatch"].append(
                                f"{lp}: 曲线 level_value={ql} ≠ 项 level_value={lv.get('level_value')}")
                # declared_levels 对账（key = 实验序号 + 参数名，匹配不上则不报警，避免误伤）
                for iv in inv_decl.get((ekey, vpar.lower()), []) or []:
                    dl = iv.get("declared_levels")
                    if not isinstance(dl, list):
                        continue
                    lvl_stat["declared"] += len(dl)
                    if len(levels) < len(dl):
                        lvl_stat["declared_gap"].append(
                            f"{vpar}: 声明 {len(dl)} 个水平 {dl}，schema 只有 {len(levels)} 项")
                    elif len(levels) > len(dl):
                        lvl_stat["beyond_declared"].append(
                            f"{vpar}: schema {len(levels)} 项 > 声明 {len(dl)} 个")
    if lvl_stat["missing_level_value"]:
        fails.append("变量水平缺 level_value %d 处: %s" % (
            len(lvl_stat["missing_level_value"]), ", ".join(lvl_stat["missing_level_value"][:5])))
    if lvl_stat["missing_metric"]:
        fails.append("变量水平缺对应指标 %d 处（塞了水平却没给指标）: %s" % (
            len(lvl_stat["missing_metric"]), ", ".join(lvl_stat["missing_metric"][:5])))
    if lvl_stat["curve_mismatch"]:
        fails.append("曲线 level_value 与所在水平不一致 %d 处: %s" % (
            len(lvl_stat["curve_mismatch"]), "; ".join(lvl_stat["curve_mismatch"][:3])))
    if lvl_stat["declared_gap"]:
        fails.append("声明水平数 > schema 水平数（漏水平）: " + "; ".join(lvl_stat["declared_gap"][:3]))
    if lvl_stat["beyond_declared"]:
        warns.append("schema 水平数 > 声明水平数（待复核）: " + "; ".join(lvl_stat["beyond_declared"][:3]))

    return {"paper": name, "counts": {"catalysts": [inv_c, sch_c], "experiments": [inv_e, sch_e],
                                      "variables": [inv_v, sch_v]},
            "bad_keys": sorted(set(bad_keys)), "fill_stats": fs, "fill_stats_sum": fs_sum,
            "total_fields": total_fields, "curves": curve_stat, "levels": lvl_stat,
            "missing": fails, "warnings": warns, "pass": not fails}


def main(root):
    papers = papers_of(root)
    if not papers:
        print(f"[warn] 未在 {root} 下找到论文夹（缺 datamining/ 或 00_full_cleaned.md）")
        return 1
    recs = []
    for p in papers:
        r = check(p)
        if r is None:
            print(f"{os.path.basename(os.path.normpath(p))[:42]:44s} 跳过（缺 05/06 产物）")
            continue
        recs.append(r)
        c, e, v = (r["counts"]["catalysts"], r["counts"]["experiments"], r["counts"]["variables"])
        lv = r.get("levels") or {}
        mark = "PASS" if r["pass"] else "FAIL " + "; ".join(r["missing"])
        if r["warnings"]:
            mark += "  ⚠ " + "; ".join(r["warnings"])
        print(f"{r['paper'][:42]:44s} C{c[0]}→{c[1]} E{e[0]}→{e[1]} V{v[0]}→{v[1]} "
              f"L{lv.get('levels', 0)}  {mark}")
    single = len(papers) == 1
    out_dir = wf_of(papers[0]) if single else os.path.join(os.path.abspath(root), "_ledgers")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "07_reconciliation.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"root": os.path.abspath(root), "papers": len(recs), "records": recs}, f,
                  ensure_ascii=False, indent=1)
    print(f"=== 对账 {sum(1 for r in recs if r['pass'])}/{len(recs)} PASS → {out}")
    if any(not r["pass"] for r in recs):
        print("FAIL 篇目：按 missing 逐条补齐（缺字段→补填；曲线缺 points→先跑 step7b_curve_backfill.py）后重跑本脚本")
    return 0 if all(r["pass"] for r in recs) else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
