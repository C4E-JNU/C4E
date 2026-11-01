#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 10 (v2, 2026-11-02): Graph export for the finalized heterogeneous-Fenton corpus.

Assembles a uniform `graph_export/` directory from the finalized corpus
(06_schema.json per paper) and the entity alignment libraries
(审查脚本和报告/实体对齐库/{catalyst,experiment,metadata}). The export is the single input
consumed by step11_neo4j_import.py (v2). All provenance (raw source text) lives inside
the graph: relationship properties carry `raw`, numeric facts are flattened to
`<field>` / `<field>_unit` / `<field>_raw`.

Graph model (final; see references/step10_graph_export.md):
  Nodes : Paper, Catalyst, Experiment, VariableExperiment, StabilityRecord, Curve,
          Journal, Element, InteractionType, Chemical, Support, SynthesisFlow,
          TreatmentFlow, Phase, Form, Morphology, FunctionalGroup, Defect,
          CharacterizationMethod, Pollutant, PollutantClass, Medium, ProcessMode,
          CoexistingSubstance, Oxidant, LightCondition, Agitation, KineticsModel, ROSType
  Edges : HAS_CATALYST, HAS_EXPERIMENT, USES_CATALYST, TARGETS, USES_OXIDANT, HAS_ELEMENT,
          HAS_INTERACTION, PREPARED_FROM, SUPPORTED_ON, USES_FLOW, POST_TREATED_BY,
          HAS_PHASE, HAS_FORM, HAS_MORPHOLOGY, HAS_FUNCTIONAL_GROUP, HAS_DEFECT,
          CHARACTERIZED_BY, BELONGS_TO, IN_MEDIUM, OPERATED_AS, COEXISTS_WITH,
          IDENTIFIES_ROS, FOLLOWS_KINETICS, PUBLISHED_IN, HAS_VARIABLE, HAS_STABILITY, HAS_CURVE

Usage:  python step10_graph_export.py            # assemble graph_export/ from corpus + libraries
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path

def repo_root() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(5):
        if (p / "审查脚本和报告" / "实体对齐库").is_dir():
            return p
        p = p.parent
    raise SystemExit("corpus root (containing 审查脚本和报告/实体对齐库) not found")

DATA = Path(os.environ.get("FENTON_DATA_ROOT") or repo_root())
CORPORA = ["1_185篇有效数据_schema", "3_剩余文献_schema", "最终schema英文"]
LIB = DATA / "审查脚本和报告" / "实体对齐库"
OUT = DATA / "graph_export"

def rp(p: Path) -> Path:
    return Path("\\\\?\\" + str(p)) if len(str(p)) > 240 else Path(p)

def load(rel: str):
    p = LIB / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

CAT_TRIPLES = {"metal_content","calcination_temp","calcination_time","calcination_heating_rate","lattice_a",
    "lattice_c","crystallite_size","lattice_spacing_nm","particle_size","specific_surface_area","pore_volume",
    "pore_size","adsorption_capacity","pH_pzc","zeta_potential","raman_ID_IG","band_gap","eis_Rct",
    "saturation_magnetization","char_PL_intensity"}
EXP_TRIPLES = {"oxidant_concentration","oxidant_dosage","catalyst_dosage","initial_pollutant_conc","initial_pH",
    "temperature","reaction_time","solution_volume","catalyst_pollutant_mass_ratio","oxidant_pollutant_molar_ratio",
    "removal_efficiency","toc_removal","cod_removal","decolorization_efficiency","rate_constant_k",
    "rate_constant_R2","adsorption_contribution"}

def flat(t):
    t = t if isinstance(t, dict) else {}
    v = t.get("value", "")
    return {"value": v if isinstance(v, (int, float)) else (v or ""),
            "unit": t.get("unit", "") or "", "raw": t.get("raw", "") or ""}

def coex_labels(cs: str):
    LAB = [(r"Cl[-⁻−]", "Cl-"), (r"NO3[-⁻−]|nitrate", "NO3-"), (r"SO4", "SO4^2-"), (r"HCO3", "HCO3-"),
           (r"CO3", "CO3^2-"), (r"phosphate|H2PO4|HPO4|PO4", "phosphate"), (r"Br[-⁻−]", "Br-"),
           (r"humic|\bHA\b", "humic acid"), (r"fulvic|\bFA\b", "fulvic acid"), (r"\bNOM\b", "NOM"),
           (r"\bEDTA\b", "EDTA"), (r"\bTBA\b|tert-butanol|t-BuOH", "TBA (scavenger)"),
           (r"\bMeOH\b|methanol", "methanol (scavenger)"), (r"\bIPA\b|isopropanol", "isopropanol (scavenger)"),
           (r"p-BQ|benzoquinone", "p-BQ (scavenger)"), (r"\bEtOH\b|ethanol", "ethanol (scavenger)"),
           (r"NaCl", "NaCl"), (r"Na2SO4", "Na2SO4"), (r"NH4\+", "NH4+"), (r"\bCa\b", "Ca2+"), (r"\bMg\b", "Mg2+")]
    out = []
    for rx, lab in LAB:
        if re.search(rx, cs, flags=re.I) and lab not in out:
            out.append(lab)
    return out

def main() -> int:
    OUT.mkdir(exist_ok=True)
    nodes: dict = {}
    edges: list = []

    def put(label, key_field, props):
        nodes[f"{label}::{props[key_field]}"] = {"label": label, "key_field": key_field, "props": props}

    def rel(etype, fl, fk, tl, tk, **props):
        edges.append({"type": etype, "from_label": fl, "from_key": fk, "to_label": tl, "to_key": tk,
                      "props": {k: v for k, v in props.items() if v not in ("", None)}})

    # ---------- entity libraries ----------
    for e in (load("metadata/journal_alignment_library.json") or {"entities": []})["entities"]:
        put("Journal", "journal_key", {"journal_key": e["journal_key"], "canonical_name": e["canonical_name"],
                                       "paper_count": e["paper_count"]})
    for label, kf, fname in [
            ("Element", "symbol", "element_entities.json"),
            ("InteractionType", "tag", "interaction_type_entities.json"),
            ("Chemical", "chemical_key", "chemical_entities.json"),
            ("Support", "support", "support_entities.json"),
            ("SynthesisFlow", "flow_key", "synthesis_flow_entities.json"),
            ("TreatmentFlow", "flow_key", "treatment_flow_entities.json"),
            ("Phase", "phase", "phase_entities.json"),
            ("Form", "form", "form_entities.json"),
            ("Morphology", "morphology", "morphology_entities.json"),
            ("FunctionalGroup", "functional_group", "functional_group_entities.json"),
            ("Defect", "defect", "defect_entities.json"),
            ("CharacterizationMethod", "method", "characterization_method_entities.json")]:
        d = load(f"catalyst/{fname}")
        for e in (d or {}).get("entities", []):
            put(label, kf, dict(e))
    for label, kf, fname in [
            ("Pollutant", "pollutant_key", "pollutant_entities.json"),
            ("PollutantClass", "pollutant_class", "pollutant_class_entities.json"),
            ("Medium", "medium", "medium_entities.json"),
            ("ProcessMode", "process_mode", "process_mode_entities.json"),
            ("CoexistingSubstance", "substance", "substance_entities.json"),
            ("Oxidant", "oxidant", "oxidant_entities.json"),
            ("LightCondition", "light_condition", "light_condition_entities.json"),
            ("Agitation", "agitation", "agitation_entities.json"),
            ("KineticsModel", "model", "kinetics_model_entities.json"),
            ("ROSType", "ros_type", "ros_type_entities.json")]:
        d = load(f"experiment/{fname}")
        for e in (d or {}).get("entities", []):
            put(label, kf, dict(e))

    # ---------- catalyst relationship libraries ----------
    def rows(rel):
        return json.loads((LIB / rel).read_text(encoding="utf-8")) if (LIB / rel).is_file() else []
    for e in rows("catalyst/catalyst_element_edges.json"):
        rel("HAS_ELEMENT", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "Element", e["element"],
            role=e.get("role"), value=e.get("value"), unit=e.get("unit"), basis=e.get("basis"),
            method=e.get("method"), source=e.get("source"), raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_interaction_edges.json"):
        rel("HAS_INTERACTION", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "InteractionType",
            e["interaction"], raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_precursor_edges.json"):
        rel("PREPARED_FROM", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "Chemical", e["chemical_key"],
            chemical_raw=e.get("chemical_raw"), order=e.get("order"), raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_support_edges.json"):
        rel("SUPPORTED_ON", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "Support", e["support"], raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_uses_flow_edges.json"):
        cid = f"{e['paper']}::{e['catalyst_id']}"
        ct, ctim, cr = e.get("calcination_temp"), e.get("calcination_time"), e.get("calcination_heating_rate")
        rel("USES_FLOW", "Catalyst", cid, "SynthesisFlow", e["flow_key"], flow=e.get("flow"),
            thermal_steps=e.get("thermal_steps"), condition_attribution=e.get("condition_attribution"),
            calcination_temp_value=ct and ct.get("value"), calcination_temp_unit=ct and ct.get("unit"),
            calcination_time_value=ctim and ctim.get("value"), calcination_time_unit=ctim and ctim.get("unit"),
            calcination_rate_value=cr and cr.get("value"), calcination_rate_unit=cr and cr.get("unit"),
            calcination_atmosphere=e.get("calcination_atmosphere"), raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_treatment_edges.json"):
        rel("POST_TREATED_BY", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "TreatmentFlow", e["flow_key"], raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_phase_edges.json"):
        rel("HAS_PHASE", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "Phase", e["phase"], role=e.get("role"),
            lattice_a=e.get("lattice_a"), lattice_c=e.get("lattice_c"),
            crystallite_size=e.get("crystallite_size"), lattice_spacing_nm=e.get("lattice_spacing_nm"),
            measurement_attribution=e.get("measurement_attribution"), raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_form_edges.json"):
        rel("HAS_FORM", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "Form", e["form"], raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_morphology_edges.json"):
        ps = e.get("particle_size")
        rel("HAS_MORPHOLOGY", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "Morphology", e["morphology"],
            particle_size_value=ps and ps.get("value"), particle_size_unit=ps and ps.get("unit"),
            measurement_attribution=e.get("measurement_attribution"), raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_functional_group_edges.json"):
        rel("HAS_FUNCTIONAL_GROUP", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "FunctionalGroup",
            e["functional_group"], raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_defect_edges.json"):
        rel("HAS_DEFECT", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "Defect", e["defect"], raw=e.get("raw", ""))
    for e in rows("catalyst/catalyst_characterization_edges.json"):
        rel("CHARACTERIZED_BY", "Catalyst", f"{e['paper']}::{e['catalyst_id']}", "CharacterizationMethod",
            e["method"], raw=e.get("raw", ""))

    # ---------- papers / catalysts / experiments ----------
    n_cat = n_exp = n_var = n_stab = n_curve = 0
    pl_map = (load("experiment/pollutant_alignment_library.json") or {}).get("name_to_entity", {})
    for corpus in CORPORA:
        cdir = DATA / corpus
        if not cdir.is_dir(): continue
        for d in os.scandir(cdir):
            fp = cdir / d.name / "06_schema.json"
            if not rp(fp).is_file(): continue
            s = json.loads(rp(fp).read_text(encoding="utf-8"))
            pid = d.name
            m = s.get("metadata", {}) or {}
            mo = m.get("meta_other", {}) or {}
            put("Paper", "paper_id", {"paper_id": pid, "corpus": corpus, "doi": m.get("doi", ""),
                "title": m.get("title", ""), "journal": m.get("journal", ""),
                "journal_key": mo.get("journal_key", ""), "publication_year": m.get("publication_year", ""),
                "authors": m.get("authors", "")})
            if mo.get("journal_key"):
                rel("PUBLISHED_IN", "Paper", pid, "Journal", mo["journal_key"])
            for c in s.get("catalysts", []):
                cid = f"{pid}::{c.get('catalyst_id','')}"
                props = c.get("properties", {}) or {}
                node = {"catalyst_key": cid, "paper_id": pid, "catalyst_id": c.get("catalyst_id", ""),
                        "catalyst_name": c.get("catalyst_name", "")}
                for k, v in props.items():
                    if k in CAT_TRIPLES:
                        f = flat(v)
                        node[k], node[k+"_unit"], node[k+"_raw"] = f["value"], f["unit"], f["raw"]
                    elif isinstance(v, dict):
                        node[k+"_value"] = v.get("value", ""); node[k+"_raw"] = v.get("raw", "")
                    else:
                        node[k] = v
                qc = c.get("qc", {}) or {}
                node.update({"q_flag": bool(qc.get("q_flag")), "needs_review": bool(qc.get("needs_review")),
                             "errata_ref": qc.get("errata_ref", ""), "qc_note": qc.get("note", "")})
                for ok, ov in (c.get("cat_other") or {}).items():
                    pk2 = "other_" + ok
                    if isinstance(ov, str):
                        node[pk2] = ov
                    elif isinstance(ov, list) and all(isinstance(x, str) for x in ov):
                        node[pk2] = ov
                    else:
                        node[pk2] = json.dumps(ov, ensure_ascii=False)
                put("Catalyst", "catalyst_key", node); n_cat += 1
                rel("HAS_CATALYST", "Paper", pid, "Catalyst", cid)
            for e in s.get("experiments", []):
                eid = f"{pid}::{e.get('experiment_id','')}"
                p = e.get("properties", {}) or {}
                node = {"experiment_key": eid, "paper_id": pid,
                        "experiment_id": e.get("experiment_id", ""), "catalyst_id": e.get("catalyst_id", ""),
                        "pollutant_name": e.get("pollutant_name", "")}
                for k, v in p.items():
                    if k in EXP_TRIPLES:
                        f = flat(v)
                        node[k], node[k+"_unit"], node[k+"_raw"] = f["value"], f["unit"], f["raw"]
                    else:
                        node[k] = v
                qc = e.get("qc", {}) or {}
                node.update({"q_flag": bool(qc.get("q_flag")), "needs_review": bool(qc.get("needs_review")),
                             "errata_ref": qc.get("errata_ref", ""), "qc_note": qc.get("note", "")})
                for ok, ov in (e.get("exp_other") or {}).items():
                    pk2 = "other_" + ok
                    if isinstance(ov, str):
                        node[pk2] = ov
                    elif isinstance(ov, list) and all(isinstance(x, str) for x in ov):
                        node[pk2] = ov
                    else:
                        node[pk2] = json.dumps(ov, ensure_ascii=False)
                put("Experiment", "experiment_key", node); n_exp += 1
                rel("HAS_EXPERIMENT", "Paper", pid, "Experiment", eid)
                ckey = f"{pid}::{e.get('catalyst_id','')}"
                cd = flat(p.get("catalyst_dosage") or {})
                mr = flat(p.get("catalyst_pollutant_mass_ratio") or {})
                rel("USES_CATALYST", "Experiment", eid, "Catalyst", ckey,
                    dosage_value=cd["value"], dosage_unit=cd["unit"], dosage_raw=cd["raw"],
                    mass_ratio_value=mr["value"], mass_ratio_unit=mr["unit"], mass_ratio_raw=mr["raw"])
                nm = str(e.get("pollutant_name") or "").strip()
                pk = pl_map.get(nm)
                if pk:
                    ic = flat(p.get("initial_pollutant_conc") or {})
                    re_ = flat(p.get("removal_efficiency") or {})
                    rk = flat(p.get("rate_constant_k") or {})
                    rel("TARGETS", "Experiment", eid, "Pollutant", pk, raw=nm,
                        initial_conc_value=ic["value"], initial_conc_unit=ic["unit"],
                        removal_efficiency_value=re_["value"], removal_efficiency_unit=re_["unit"],
                        rate_constant_k_value=rk["value"], rate_constant_k_unit=rk["unit"])
                ot = str(p.get("oxidant_type") or "").strip()
                if ot and ot.lower() not in ("not-reported", "unknown", "none"):
                    oc, od = flat(p.get("oxidant_concentration") or {}), flat(p.get("oxidant_dosage") or {})
                    omr = flat(p.get("oxidant_pollutant_molar_ratio") or {})
                    rel("USES_OXIDANT", "Experiment", eid, "Oxidant", ot,
                        conc_value=oc["value"], conc_unit=oc["unit"], conc_raw=oc["raw"],
                        dosage_value=od["value"], dosage_unit=od["unit"], dosage_raw=od["raw"],
                        molar_ratio_value=omr["value"], molar_ratio_unit=omr["unit"])
                pcs = str(p.get("process_mode") or "").strip()
                if pcs and pcs.lower() not in ("not-reported", "unknown", "none"):
                    rel("OPERATED_AS", "Experiment", eid, "ProcessMode", pcs)
                md = str(p.get("medium") or "").strip().lower()
                if md and md.lower() not in ("not-reported", "unknown", "none"):
                    rel("IN_MEDIUM", "Experiment", eid, "Medium", md)
                ag = str(p.get("agitation") or "").strip()
                if ag and ag.lower() not in ("not-reported", "unknown", "none"):
                    rel("AGITATED_BY", "Experiment", eid, "Agitation", ag)
                km = str(p.get("kinetics_model") or "").strip()
                if km and km.lower() not in ("not-reported", "unknown", "none"):
                    rel("FOLLOWS_KINETICS", "Experiment", eid, "KineticsModel", km)
                dr = str(p.get("dominant_ROS") or "").strip()
                if dr and dr.lower() not in ("not-reported", "unknown", "none"):
                    rm = str(p.get("ros_detection_method") or "").strip()
                    eprd = str(p.get("epr_signal_desc") or "").strip()
                    rel("IDENTIFIES_ROS", "Experiment", eid, "ROSType", dr, method=rm or None,
                        raw=" | ".join(x for x in (dr, eprd) if x))
                cs = str(p.get("coexisting_substances") or "").strip()
                if cs and cs.lower() not in ("not-reported", "unknown", "none"):
                    for lab in coex_labels(cs):
                        rel("COEXISTS_WITH", "Experiment", eid, "CoexistingSubstance", lab, raw=cs)
                # variable levels
                for ii, it in enumerate(e.get("variable_experiments") or []):
                    if not isinstance(it, dict): continue
                    param = str(it.get("variable_parameter") or "unspecified")
                    fcond = it.get("fixed_conditions", "")
                    for li, lv in enumerate(it.get("levels") or []):
                        if not isinstance(lv, dict): continue
                        vkey = f"{eid}::V{ii}::L{li}"
                        mv = lv.get("metric_value") or {}
                        put("VariableExperiment", "variable_key", {
                            "variable_key": vkey, "paper_id": pid, "experiment_key": eid,
                            "variable_parameter": param, "level_raw": lv.get("level_value", ""),
                            "metric_name": lv.get("metric_name", ""),
                            "metric_value": mv.get("value") if isinstance(mv, dict) else None,
                            "metric_unit": mv.get("unit", "") if isinstance(mv, dict) else "",
                            "metric_raw": mv.get("raw", "") if isinstance(mv, dict) else "",
                            "metric_source": lv.get("metric_source", ""),
                            "source_figure": lv.get("source_figure", ""),
                            "fixed_conditions": fcond})
                        n_var += 1
                        rel("HAS_VARIABLE", "Experiment", eid, "VariableExperiment", vkey)
                        dp = lv.get("degradation_profiles")
                        if isinstance(dp, dict) and (dp.get("points") or dp.get("ref")):
                            qkey = vkey + "::Q"
                            put("Curve", "curve_key", {
                                "curve_key": qkey, "paper_id": pid, "experiment_key": eid,
                                "ref": dp.get("ref", ""), "curve_label": dp.get("curve_label", ""),
                                "metric": dp.get("metric", ""), "x_unit": dp.get("x_unit", ""),
                                "y_unit": dp.get("y_unit", ""),
                                "n_points": dp.get("n_points", len(dp.get("points", []) or [])),
                                "points_json": json.dumps(dp.get("points", []), ensure_ascii=False)})
                            rel("HAS_CURVE", "VariableExperiment", vkey, "Curve", qkey)
                            n_curve += 1
                # stability
                st = e.get("stability")
                if isinstance(st, dict) and st:
                    skey = eid + "::STAB"
                    node = {"stability_key": skey, "paper_id": pid, "experiment_key": eid}
                    for k, v in st.items():
                        if isinstance(v, dict) and set(v.keys()) <= {"value", "unit", "raw"}:
                            f = flat(v)
                            node[k], node[k+"_unit"], node[k+"_raw"] = f["value"], f["unit"], f["raw"]
                        else:
                            node[k] = v
                    put("StabilityRecord", "stability_key", node); n_stab += 1
                    rel("HAS_STABILITY", "Experiment", eid, "StabilityRecord", skey)
                # experiment-level curves
                dps = e.get("degradation_profiles")
                if dps:
                    for i, c in enumerate(dps if isinstance(dps, list) else [dps]):
                        if not isinstance(c, dict): continue
                        qkey = eid + f"::Q{i}"
                        put("Curve", "curve_key", {
                            "curve_key": qkey, "paper_id": pid, "experiment_key": eid,
                            "ref": c.get("ref", ""), "curve_label": c.get("curve_label", ""),
                            "metric": c.get("metric", ""), "x_unit": c.get("x_unit", ""),
                            "y_unit": c.get("y_unit", ""),
                            "n_points": c.get("n_points", len(c.get("points", []) or [])),
                            "points_json": json.dumps(c.get("points", []), ensure_ascii=False)})
                        rel("HAS_CURVE", "Experiment", eid, "Curve", qkey)
                        n_curve += 1
    # pollutant -> class
    for e in rows("experiment/pollutant_belongs_to_class_edges.json"):
        rel("BELONGS_TO", "Pollutant", e["pollutant_key"], "PollutantClass", e["pollutant_class"])

    # ---------- write ----------
    by_label = {}
    for v in nodes.values():
        by_label.setdefault(v["label"], []).append(v)
    for label, items in by_label.items():
        (OUT / f"nodes_{label}.json").write_text(json.dumps(
            {"label": label, "key_field": items[0]["key_field"], "nodes": items},
            ensure_ascii=False, indent=1), encoding="utf-8")
    by_type = {}
    for e in edges:
        by_type.setdefault(e["type"], []).append(e)
    for t, items in by_type.items():
        (OUT / f"edges_{t}.json").write_text(json.dumps(
            {"type": t, "from_label": items[0]["from_label"], "from_key": items[0]["from_key"],
             "to_label": items[0]["to_label"], "to_key": items[0]["to_key"], "edges": items},
            ensure_ascii=False, indent=1), encoding="utf-8")
    manifest = {"papers": len(by_label.get("Paper", [])), "catalysts": n_cat, "experiments": n_exp,
                "variable_levels": n_var, "stability_records": n_stab, "curves": n_curve,
                "entity_labels": {k: len(v) for k, v in by_label.items()
                                  if k not in ("Paper", "Catalyst", "Experiment", "VariableExperiment",
                                               "StabilityRecord", "Curve")},
                "edges": {k: len(v) for k, v in sorted(by_type.items())}}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    return 0

if __name__ == "__main__":
    sys.exit(main())
