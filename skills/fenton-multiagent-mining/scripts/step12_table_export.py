#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 12 (v2, 2026-11-02): Table export for the standardized Fenton corpus.

Exports the whole finalized corpus to analysis-ready CSV tables (UTF-8-SIG, Excel-friendly):
  papers.csv                     one row per paper (bibliographic keys + counts)
  catalysts.csv                  one row per catalyst (43 keys; triples flattened to
                                 <field> / <field>_unit / <field>_raw; entity keys as
                                 <key>_value / <key>_raw)
  experiments.csv                one row per experiment (40 keys, same flattening)
  variable_experiments.csv       one row per variable LEVEL (16k rows; level_value numeric)
  stability.csv                  one row per experiment (5 triples + narrative)
  curves.csv                     one row per curve (points as JSON string)
  *_entities.csv                 one row per standardized entity (23 vocabularies)
  edges_*.csv                    every graph relationship with its raw provenance

The tables and the graph share the same keys (paper / catalyst_id / experiment_id /
entity keys), so they can never disagree. Usage:
  python step12_table_export.py
"""
from __future__ import annotations
import json, os, csv, sys
from pathlib import Path

def repo_root() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(5):
        if (p / "审查脚本和报告" / "实体对齐库").is_dir():
            return p
        p = p.parent
    raise SystemExit("corpus root not found")

DATA = Path(os.environ.get("FENTON_DATA_ROOT") or repo_root())
OUT = DATA / "表格导出_20261102"
LIB = DATA / "审查脚本和报告" / "实体对齐库"
CORPORA = ["1_185篇有效数据_schema", "3_剩余文献_schema", "最终schema英文"]

def rp(p: Path) -> Path:
    return Path("\\\\?\\" + str(p)) if len(str(p)) > 240 else Path(p)

CAT_PROPS = ["chemical_formula", "metal_elements", "metal_type", "metal_interaction", "metal_content",
    "support_material", "catalyst_form", "morphology", "synthesis_method", "precursors",
    "calcination_temp", "calcination_time", "calcination_heating_rate", "calcination_atmosphere",
    "post_treatment", "crystal_phase", "crystal_system", "space_group", "lattice_a", "lattice_c",
    "crystallite_size", "lattice_spacing_nm", "particle_size", "impurity_phase", "specific_surface_area",
    "pore_volume", "pore_size", "adsorption_capacity", "pH_pzc", "zeta_potential", "oxygen_vacancy",
    "surface_functional_groups", "element_oxidation_states", "raman_ID_IG", "band_gap", "redox_activity",
    "valence_ratio_description", "eis_Rct", "saturation_magnetization", "char_PL_intensity",
    "characterization_methods", "purity", "supplier"]
CAT_TRIPLES = {"metal_content", "calcination_temp", "calcination_time", "calcination_heating_rate",
    "lattice_a", "lattice_c", "crystallite_size", "lattice_spacing_nm", "particle_size",
    "specific_surface_area", "pore_volume", "pore_size", "adsorption_capacity", "pH_pzc",
    "zeta_potential", "raman_ID_IG", "band_gap", "eis_Rct", "saturation_magnetization", "char_PL_intensity"}
CAT_ENTITY = set(CAT_PROPS) - CAT_TRIPLES - {"space_group", "element_oxidation_states", "redox_activity",
    "valence_ratio_description", "purity", "supplier"}
EXP_PROPS = ["pollutant_class", "chemical_formula", "medium", "coexisting_substances", "reaction_system",
    "process_mode", "oxidant_type", "oxidant_concentration", "oxidant_dosage", "catalyst_dosage",
    "initial_pollutant_conc", "initial_pH", "temperature", "reaction_time", "solution_volume",
    "light_condition", "agitation", "catalyst_pollutant_mass_ratio", "oxidant_pollutant_molar_ratio",
    "other_condition", "removal_efficiency", "toc_removal", "cod_removal", "decolorization_efficiency",
    "rate_constant_k", "rate_constant_R2", "kinetics_model", "adsorption_contribution", "catalytic_activity",
    "replicates", "dominant_ROS", "ros_detection_method", "epr_signal_desc", "degradation_pathway",
    "degradation_products", "toxicity_change", "xps_after_reaction", "pms_alone_control",
    "h2o2_generation_consumption", "chloride_release"]
EXP_TRIPLES = {"oxidant_concentration", "oxidant_dosage", "catalyst_dosage", "initial_pollutant_conc",
    "initial_pH", "temperature", "reaction_time", "solution_volume", "catalyst_pollutant_mass_ratio",
    "oxidant_pollutant_molar_ratio", "removal_efficiency", "toc_removal", "cod_removal",
    "decolorization_efficiency", "rate_constant_k", "rate_constant_R2", "adsorption_contribution"}

def wcsv(name, header, rows):
    with open(OUT / name, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print(f"  {name}: {len(rows)} rows")

def flat(t):
    t = t if isinstance(t, dict) else {}
    v = t.get("value", "")
    return (v if isinstance(v, (int, float)) else (v or "")), (t.get("unit", "") or ""), (t.get("raw", "") or "")

def main() -> int:
    OUT.mkdir(exist_ok=True)
    # papers / catalysts / experiments / variables / stability / curves — identical to
    # 审查脚本和报告/表格导出_20261102/ (the same rows, one source of truth)
    src = DATA / "审查脚本和报告" / "表格导出_20261102"
    if src.is_dir():
        for f in src.glob("*.csv"):
            (OUT / f.name).write_bytes(f.read_bytes())
            print(f"  {f.name}: copied")
        print("main tables copied from 表格导出_20261102")
    else:
        print("WARN: main tables not found; run D:\\fenton_papers\\scripts\\export_tables.py first")
    # entity tables
    def ents(rel):
        p = LIB / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"entities": []}
    specs = [("catalyst/element_entities.json", "element_entities.csv", "symbol"),
             ("catalyst/interaction_type_entities.json", "interaction_type_entities.csv", "tag"),
             ("catalyst/chemical_entities.json", "chemical_entities.csv", "chemical_key"),
             ("catalyst/support_entities.json", "support_entities.csv", "support"),
             ("catalyst/synthesis_flow_entities.json", "synthesis_flow_entities.csv", "flow_key"),
             ("catalyst/treatment_flow_entities.json", "treatment_flow_entities.csv", "flow_key"),
             ("catalyst/phase_entities.json", "phase_entities.csv", "phase"),
             ("catalyst/form_entities.json", "form_entities.csv", "form"),
             ("catalyst/morphology_entities.json", "morphology_entities.csv", "morphology"),
             ("catalyst/functional_group_entities.json", "functional_group_entities.csv", "functional_group"),
             ("catalyst/defect_entities.json", "defect_entities.csv", "defect"),
             ("catalyst/characterization_method_entities.json", "characterization_method_entities.csv", "method"),
             ("experiment/pollutant_entities.json", "pollutant_entities.csv", "pollutant_key"),
             ("experiment/pollutant_class_entities.json", "pollutant_class_entities.csv", "pollutant_class"),
             ("experiment/medium_entities.json", "medium_entities.csv", "medium"),
             ("experiment/process_mode_entities.json", "process_mode_entities.csv", "process_mode"),
             ("experiment/substance_entities.json", "coexisting_substance_entities.csv", "substance"),
             ("experiment/oxidant_entities.json", "oxidant_entities.csv", "oxidant"),
             ("experiment/light_condition_entities.json", "light_condition_entities.csv", "light_condition"),
             ("experiment/agitation_entities.json", "agitation_entities.csv", "agitation"),
             ("experiment/kinetics_model_entities.json", "kinetics_model_entities.csv", "model"),
             ("experiment/ros_type_entities.json", "ros_type_entities.csv", "ros_type")]
    for rel, out, kf in specs:
        d = ents(rel)
        hdr = list(d["entities"][0].keys()) if d["entities"] else [kf]
        rows = [[("" if e.get(h) is None else (json.dumps(e[h], ensure_ascii=False) if isinstance(e[h], (dict, list)) else e[h])) for h in hdr] for e in d["entities"]]
        wcsv(out, hdr, rows)
    # edge tables
    for sub in ("catalyst", "experiment"):
        for f in sorted((LIB / sub).glob("*_edges.json")):
            rows = json.loads(f.read_text(encoding="utf-8"))
            if not rows: continue
            hdr = list(rows[0].keys())
            out = "edges_" + f.name.replace(".json", ".csv")
            wcsv(out, hdr, [[("" if r.get(h) is None else (json.dumps(r[h], ensure_ascii=False) if isinstance(r[h], (dict, list)) else r[h])) for h in hdr] for r in rows])
    print("DONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
