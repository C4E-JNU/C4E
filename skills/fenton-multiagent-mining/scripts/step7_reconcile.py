# -*- coding: utf-8 -*-
"""step7_reconcile.py — the Step 7 reconciliation gate (inventory count ↔ filled count / field names / fill_stats / curve homes)

Usage:
  python step7_reconcile.py <paper folder or corpus root>
Products:
  single paper → <paper folder>/datamining/07_reconciliation.json
  multiple papers → <corpus root>/_ledgers/07_reconciliation.json

Six checks (per paper):
  ① counts      05_node_inventory inventory count == 06_schema filled count (catalysts / experiments / variable sweep groups)
  ② field_names property key names must be in the field list (catalysts 45 / experiments 41 + stability 6)
  ③ fill_stats  from_text + from_image + from_errata + blank == total field count
  ④ pollutant   pollutant_name must be a single value (co-listed pollutants = split failure, must be refilled)
  ⑤ curves      every curve in 04_verified_image_data has a home in the schema (extra reverse ones are reported too)
  ⑥ levels      every variable levels[] item must carry both level_value and metric_name/metric_value (a level without
                its indicator = FAIL); the curve's level_value must match the level it sits in; the number of levels items must be >=
                the declared_levels count in 05_node_inventory (fewer = missing level, FAIL; more = WARN).
                The legacy structure (variables without levels) skips this item, so historical data still reconciles.
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

# co-listed-pollutant markers (a hit means "one pollutant per experiment" was violated; legacy Chinese separators kept for old data)
MULTI_POLLUTANT = [",", " and ", " + ", "+", " respectively", "respectively", "mixture"]
# template-native container keys: allowed inside properties and not counted toward the 45/41 fields
EXTRA_ALLOWED = {"other"}
# curve container keys (the same caliber as the schema_fill template curve binding table)
CURVE_KEY_RE = re.compile(r"(profiles|curves)$", re.I)
REF_RE = re.compile(r"^(fig(?:ure)?\.?\s*s?\d+)([a-z])?$", re.I)


# ────────────────────────── basic helpers ──────────────────────────
def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def papers_of(root):
    """Auto-adapt: one paper (root itself is a paper folder) or several (paper folders under root)."""
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


# ────────────────────────── checks ──────────────────────────
def schema_curve_index(schema):
    """Recursively collect the (fig, sub, label) of every curve container in the schema; also count entries missing points.

    Covers `degradation_profiles` plus custom keys (`quenching_degradation_profiles`,
    `other.control_curves`, `system_control_profiles` …) — the same caliber as the schema_fill template curve containers.
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
    """Collect the (fig, sub, label) of every curve in 04_verified_image_data."""
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


def collect_inv_vars(inv):
    """Tolerate both variable-registration formats in the 05 node inventory:
    new — variables nested inside the experiment node's variables[] (preferred; ownership is locked by the nesting);
    legacy — top-level flat variables[] carrying exp_id (fallback; all 767 historical papers use this form).
    Flattened uniformly to [{exp_id, variable_parameter, declared_levels, ...}] for counting and declared_levels reconciliation."""
    nested = []
    for ex in (inv.get("experiments", []) or []):
        if not isinstance(ex, dict):
            continue
        for iv in (ex.get("variables", []) or []):
            if isinstance(iv, dict):
                iv = dict(iv)
                # tolerate both keys: experiment_id (new) / exp_id (legacy)
                iv.setdefault("exp_id", ex.get("experiment_id") or ex.get("exp_id"))
                nested.append(iv)
    if nested:
        return nested
    return [iv for iv in (inv.get("variables", []) or []) if isinstance(iv, dict)]


def iter_sch_exps(sch):
    """Tolerate three formats of 06 experiment nodes (top-level experiments finalized 2026-10-23):
    new — top-level experiments[] (experiments do not nest catalysts; ownership comes from each node's catalyst_id);
    intermediate — top-level variable_experiments[] (the 2026-10-23 daytime key name, fallback);
    legacy — catalysts[].experiments[] (historical fallback). Files that coexist are unioned."""
    for e in (sch.get("experiments") or []):
        if isinstance(e, dict):
            yield e
    for e in (sch.get("variable_experiments") or []):
        if isinstance(e, dict):
            yield e
    for c in (sch.get("catalysts") or []):
        if isinstance(c, dict):
            for e in (c.get("experiments") or []):
                if isinstance(e, dict):
                    yield e


def check(paper):
    wf = wf_of(paper)
    inv = load_json(os.path.join(wf, "05_node_inventory.json"))
    sch = load_json(os.path.join(wf, "06_schema.json"))
    if inv is None or sch is None:
        return None
    name = os.path.basename(os.path.normpath(paper))
    inv_vars = collect_inv_vars(inv)
    inv_c, inv_e, inv_v = (len(inv.get("catalysts", []) or []),
                           len(inv.get("experiments", []) or []),
                           len(inv_vars))
    sch_c = len(sch.get("catalysts", []) or [])
    sch_exps = list(iter_sch_exps(sch))
    sch_e = len(sch_exps)
    sch_v = sum(len(e.get("variable_experiments", []) or []) for e in sch_exps)

    fails = []
    warns = []

    # ① counts (fewer = fill gap, FAIL; more = Step 6 inventory may have missed one, WARN pending review)
    for label, a, b in (("catalysts", inv_c, sch_c), ("experiments", inv_e, sch_e), ("variables", inv_v, sch_v)):
        if b < a:
            fails.append(f"count:{label} short by {a - b} (inventory {a} → filled {b})")
        elif b > a:
            warns.append(f"{label} over by {b - a} (inventory {a} → filled {b}; Step 6 may have missed a registration, review 05)")

    # ② field names (including the keys allowed at top level)
    bad_keys = []
    for c in sch.get("catalysts", []) or []:
        props = c.get("properties") or {}
        for k in props:
            # curve container keys (*_profiles / *_curves, e.g. degradation_profiles) are template-native and
            # do not count toward the 45/41 fields; they are checked separately by item ⑤, so only allow them here and never flag them
            if k not in CATALYST_FIELDS and not CURVE_KEY_RE.search(k):
                bad_keys.append(f"catalyst.{k}")
    for e in sch_exps:
        for k in (e.get("properties") or {}):
            if k not in EXPERIMENT_FIELDS and k not in EXTRA_ALLOWED and not CURVE_KEY_RE.search(k):
                bad_keys.append(f"experiment.{k}")
        for k in (e.get("stability") or {}):
            if k not in STABILITY_FIELDS:
                bad_keys.append(f"stability.{k}")
    if bad_keys:
        fails.append("illegal field names: " + ", ".join(sorted(set(bad_keys))[:12]))

    # ③ fill_stats (sum only the four caliber keys; extras such as total_fields supplied by subagents are informational)
    FS_KEYS = ("from_text", "from_image", "from_errata", "blank")
    total_fields = len(CATALYST_FIELDS) * sch_c + (len(EXPERIMENT_FIELDS) + len(STABILITY_FIELDS)) * sch_e
    fs = sch.get("fill_stats") or {}
    fs_sum = None
    if fs:
        lack = [k for k in FS_KEYS if k not in fs]
        if lack:
            fails.append("fill_stats missing keys: " + ", ".join(lack))
        fs_sum = sum(fs.get(k, 0) for k in FS_KEYS if isinstance(fs.get(k), (int, float)))
        if fs_sum != total_fields:
            fails.append(f"fill_stats sums to {fs_sum} ≠ total field count {total_fields}")
    else:
        fails.append("fill_stats absent")

    # ④ single pollutant value
    multi = []
    for e in sch_exps:
        pn = e.get("pollutant_name") or (e.get("properties") or {}).get("pollutant_name") or ""
        if pn and any(t in str(pn) for t in MULTI_POLLUTANT):
            multi.append(str(pn)[:40])
    if multi:
        fails.append("one pollutant per experiment violated: " + " | ".join(multi[:5]))

    # ⑤ curve homes
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
            fails.append(f"{len(lost)} curves have no home: " + "; ".join("/".join(x) for x in lost[:6]))
        if empty_pts:
            fails.append(f"{empty_pts} curves are missing points (the subagent failed to copy them; re-dispatch to read 04)")

    # ⑥ variable levels (v2): every levels[] item must carry level_value and an indicator; the curve level_value must match its item;
    #    the number of levels items >= the declared_levels count registered in Step 6 (fewer = missing level)
    lvl_stat = {"groups": 0, "with_levels": 0, "levels": 0, "declared": 0,
                "missing_level_value": [], "missing_metric": [], "curve_mismatch": [],
                "declared_gap": [], "beyond_declared": []}
    inv_decl = {}
    for iv in inv_vars:
        if isinstance(iv, dict) and iv.get("declared_levels"):
            inv_decl.setdefault((str(iv.get("exp_id") or "").lower(),
                                 str(iv.get("variable_parameter") or "").lower()), []).append(iv)
    for ei, e in enumerate(sch_exps):
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
                # legacy structure (no levels): require at least metric_value / new_value, otherwise flag it
                if not (v.get("metric_value") or v.get("new_value")):
                    lvl_stat["missing_metric"].append(
                        f"V{vi + 1}:{vpar or '-'} (no levels and no indicator value)")
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
                    lvl_stat["missing_metric"].append(f"{lp} (level {lv.get('level_value')})")
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
                            f"{lp}: curve level_value={ql} ≠ item level_value={lv.get('level_value')}")
            # declared_levels reconciliation (key = experiment index + parameter name; a mismatch is not reported, to avoid false alarms)
            for iv in inv_decl.get((ekey, vpar.lower()), []) or []:
                dl = iv.get("declared_levels")
                if not isinstance(dl, list):
                    continue
                lvl_stat["declared"] += len(dl)
                if len(levels) < len(dl):
                    lvl_stat["declared_gap"].append(
                        f"{vpar}: declared {len(dl)} levels {dl} but the schema has only {len(levels)} items")
                elif len(levels) > len(dl):
                    lvl_stat["beyond_declared"].append(
                        f"{vpar}: schema has {len(levels)} items > the {len(dl)} declared")
    if lvl_stat["missing_level_value"]:
        fails.append("variable levels missing level_value in %d places: %s" % (
            len(lvl_stat["missing_level_value"]), ", ".join(lvl_stat["missing_level_value"][:5])))
    if lvl_stat["missing_metric"]:
        fails.append("variable levels missing the corresponding indicator in %d places (a level without its indicator): %s" % (
            len(lvl_stat["missing_metric"]), ", ".join(lvl_stat["missing_metric"][:5])))
    if lvl_stat["curve_mismatch"]:
        fails.append("curve level_value disagrees with its level in %d places: %s" % (
            len(lvl_stat["curve_mismatch"]), "; ".join(lvl_stat["curve_mismatch"][:3])))
    if lvl_stat["declared_gap"]:
        fails.append("declared level count > schema level count (missing levels): " + "; ".join(lvl_stat["declared_gap"][:3]))
    if lvl_stat["beyond_declared"]:
        warns.append("schema level count > declared level count (pending review): " + "; ".join(lvl_stat["beyond_declared"][:3]))

    return {"paper": name, "counts": {"catalysts": [inv_c, sch_c], "experiments": [inv_e, sch_e],
                                      "variables": [inv_v, sch_v]},
            "bad_keys": sorted(set(bad_keys)), "fill_stats": fs, "fill_stats_sum": fs_sum,
            "total_fields": total_fields, "curves": curve_stat, "levels": lvl_stat,
            "missing": fails, "warnings": warns, "pass": not fails}


def main(root):
    papers = papers_of(root)
    if not papers:
        print(f"[warn] no paper folder found under {root} (missing datamining/ or 00_full_cleaned.md)")
        return 1
    recs = []
    for p in papers:
        r = check(p)
        if r is None:
            print(f"{os.path.basename(os.path.normpath(p))[:42]:44s} skipped (missing 05/06 products)")
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
    print(f"=== reconciliation {sum(1 for r in recs if r['pass'])}/{len(recs)} PASS → {out}")
    if any(not r["pass"] for r in recs):
        print("FAILED papers: fix each item under missing (missing fields → fill; curves missing points/axis units → re-dispatch the subagent to read 04) and rerun this script")
    return 0 if all(r["pass"] for r in recs) else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
