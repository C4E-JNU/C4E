# Step 7: Schema Filling and Reconciliation

> Prerequisite: Step 5 (`04_verified_image_data.json` + `04c_errata.json` + `04_annotation_table.json`) and Step 6 (`05_node_inventory.json`) complete.
> Executed by: one `fenton-schema-catalyst` per paper + one `fenton-schema-experiment` per experiment node (**dispatched in a single parallel round**)
>       + orchestrator (assembly → curve-fragment merge → reconciliation gate → numeric normalization).

## Dispatch (two subagent classes, one parallel round)

A single summary returned to the orchestrator has a length cap, and the full schema (many experiments + all fields) far exceeds it — one call per paper would always be truncated.
**Split by node type into two dedicated subagents and issue one parallel `delegate_task` round**:

- `fenton-schema-catalyst` (**one instance per catalyst node**; the node count follows the 05 skeleton; dispatched in parallel):
  fills exactly one catalyst per call — all 45 fixed slots, always emitted (unreported slots stay empty; never omit a key); **the C1 fragment carries the five metadata fields**
  (one per paper; other fragments do not) — never touches experiments, variables or curves;
- `fenton-schema-experiment` (**one instance per experiment node**): fills only its own E: the 41 fields + 6 stability keys +
  variable `levels[]` + the curve binding table.

```
delegate_task(tasks=[
  {"profile": "fenton-schema-catalyst",
   "goal": "Fill the 45 fixed slots + five metadata fields for C1 of this paper; do not touch experiments, variables or curves",
   "context": "<paper folder>/datamining directory path"},
  {"profile": "fenton-schema-catalyst",
   "goal": "Fill the 45 fixed slots for C2 (control) of this paper; no metadata",
   "context": "<paper folder>/datamining directory path"},
  {"profile": "fenton-schema-experiment",
   "goal": "Fill the 41 fields + stability + variable nodes for E1 (belongs to C1); output this experiment only",
   "context": "<paper folder>/datamining directory path"},
  # one entry each for C3…, E2, E3…, dispatched in parallel
])
```

The context is the **`datamining` directory** (the subagent's inputs and products live at that level; never pass the parent paper folder).
For a short paper with only 1 catalyst and 1 experiment, you may instead dispatch the fallback subagent `fenton-schema` to fill everything in one call, avoiding the split.

**Curve points are never pasted back**: the subagent supplies only the binding table `{ref: "Fig.7a", curve_label: "…", metric: "C/C0", x_unit: "min", y_unit: "", level_value: "3"}`
(`x_unit`/`y_unit` are copied from `xy_axes` in the 04 figure ledger and carried into the graph with the curve; the binding-table field definitions have a single source of truth in the `fenton-schema-experiment` subagent).
The orchestrator script reads points from `04_verified_image_data.json` by `(figure, subfigure) + curve_label`
and attaches them to the main `degradation_profiles`, to **each level's own `levels[].degradation_profiles`**, and to the experiment's `other.control_curves`;
`level_value` lands on the curve entry together with the binding table (written by the filling subagent according to the level it belongs to; never left empty).

**A variable = a sweep group; levels go into `levels[]` (v2, since 2026-10-07)**: one variable sweep group = one `variable_experiments` entry;
each level the group swept occupies one item in `levels[]` — **the level value + that level's indicator + that level's curve correspond one-to-one inside the same item**
(pH 3/5/8 → three items, each with its own removal efficiency and curve). **Never** flatten several levels into one string and then give a single indicator value elsewhere
("3, 5, 8" with only one number); and **never** split one sweep group into three variable nodes. "If a curve exists it must be attached to the corresponding level";
when the text gives no per-level removal efficiency, **read the missing `metric_value` from the curve endpoint** (`metric_source: "curve_endpoint@<time>"`).
Field definitions and the iron rule on level alignment are in `backend/agents/fenton-schema/system.md` (single source of truth; not repeated here).

## Subagent return format

The returned JSON's **field definitions are in** `backend/agents/fenton-schema-catalyst/system.md` (metadata + the 45-slot list) and
`backend/agents/fenton-schema-experiment/system.md` (the 41+6 list + binding table + the single source of truth for the variable iron rule; not repeated here).
The orchestrator checks only: the four `fill_stats` values of each fragment sum to their own caliber (catalysts 45×C, experiments 47), every key name is in the list, every curve has a binding-table entry
`{ref, curve_label, x_unit, y_unit, level_value}` (level curves),
every variable `levels[]` item carries its own indicator (see below), and the `qc` flags are complete.

## Canonical 06 shape (assembly target, fixed 2026-10-23)

Four top-level keys `metadata / catalysts / experiments / fill_stats`: experiments are flat at top level (ownership is carried by each node's own
`catalyst_id`; catalysts are no longer nested inside them); variable sweep groups live **inside** the experiment node under `variable_experiments[]`
(one group per parameter, one item per level in `levels[]`); the catalyst and experiment counts and the variable-group and level counts all follow the 05 skeleton,
and assembly checks them id by id (fragment count = catalyst count + experiment count). Multi-catalyst (including control) + multi-experiment + multi-variable example
(`properties` shows representative slots only; the real delivery is complete slot by slot, with unreported slots as empty strings `""`; see the few-shot examples in the two filling subagents):

```json
{
  "metadata": {"doi": "10.xxxx/xxxxx", "title": "…", "journal": "…",
               "publication_year": "2023", "authors": "…", "other": {}},
  "catalysts": [
    {"catalyst_id": "C1", "catalyst_name": "Fe3O4/biochar",
     "properties": {"chemical_formula": "Fe3O4", "metal_elements": "Fe",
                    "metal_type": "non-noble transition metal (Fe-based)",
                    "synthesis_method": "two-step co-precipitation then pyrolysis…",
                    "specific_surface_area": {"value": 86.3, "unit": "m²/g", "raw": "BET 86.3 m²/g"},
                    "(…all 43 remaining slots…)": "", "other": {}},
     "qc": {"q_flag": false, "needs_review": false, "errata_ref": "", "note": ""}},
    {"catalyst_id": "C2", "catalyst_name": "bare Fe3O4 nanoparticles (control)",
     "properties": {"chemical_formula": "Fe3O4", "metal_elements": "Fe",
                    "metal_type": "non-noble transition metal (Fe-based)", "metal_interaction": "",
                    "metal_content": {"value": "", "unit": "", "raw": ""},
                    "synthesis_method": "co-precipitation (no biochar support)",
                    "crystal_phase": "magnetite Fe3O4 (JCPDS 19-0629)",
                    "crystallite_size": {"value": 24, "unit": "nm", "raw": "Scherrer 24 nm"},
                    "specific_surface_area": {"value": 51.2, "unit": "m²/g", "raw": "BET 51.2 m²/g"},
                    "(…all 45 slots; 30 unreported slots empty…)": "", "other": {}},
     "qc": {"q_flag": false, "needs_review": false, "errata_ref": "", "note": "control catalyst"}}
  ],
  "experiments": [
    {"experiment_id": "E1", "catalyst_id": "C1", "pollutant_name": "SMX (sulfamethoxazole)",
     "properties": {"pollutant_class": "antibiotic (sulfonamide)", "chemical_formula": "C10H11N3O3S",
                    "oxidant_type": "H2O2",
                    "oxidant_concentration": {"value": 5, "unit": "mmol/L", "raw": "5 mmol/L H2O2"},
                    "initial_pollutant_conc": {"value": 10, "unit": "mg/L", "raw": "10 mg/L SMX"},
                    "catalyst_dosage": {"value": 0.2, "unit": "g/L", "raw": "0.2 g/L"},
                    "initial_pH": {"value": 5, "unit": "", "raw": "5 (optimal)"},
                    "removal_efficiency": {"value": 95, "unit": "%", "raw": "95% (120 min)"},
                    "toc_removal": {"value": 61, "unit": "%", "raw": "61%"},
                    "kinetics_model": "pseudo-first-order kinetics (R² = 0.98)",
                    "(…all 40 remaining slots…)": "",
                    "degradation_profiles": {"desc": "Main degradation curve under standard conditions: C/C0 decay of 10 mg/L SMX with 0.2 g/L catalyst + 5 mmol/L H2O2",
                                             "ref": "Fig.4a", "curve_label": "SMX+cat+H2O2", "metric": "C/C0",
                                             "x_unit": "min", "y_unit": "",
                                             "points": [[0, 1.0], [10, 0.9], [20, 0.7], [40, 0.69], [60, 0.49], [90, 0.34], [120, 0.26], [180, 0.15]]},
                    "other": {}},
     "stability": {"cycle_count": {"value": 5, "unit": "", "raw": "5 cycles"},
                   "initial_efficiency": {"value": 95, "unit": "%", "raw": "95% in cycle 1"},
                   "final_efficiency": {"value": 87, "unit": "%", "raw": "87% in cycle 5"},
                   "efficiency_retention": {"value": 92, "unit": "%", "raw": "92%"},
                   "metal_leaching_conc": {"value": 0.8, "unit": "mg/L", "raw": "Fe 0.8 mg/L (ICP)"},
                   "structure_after_rxn": "XRD phase unchanged"},
     "variable_experiments": [
       {"variable_parameter": "initial_pH",
        "fixed_conditions": "H2O2 5 mmol/L, catalyst 0.2 g/L, SMX 10 mg/L, 25 °C, 100 mL",
        "levels": [
          {"level_value": "3", "metric_name": "removal_efficiency",
           "metric_value": {"value": 68, "unit": "%", "raw": "68% (120 min)"},
           "metric_source": "text", "source_figure": "Fig.6a",
           "degradation_profiles": {"desc": "C/C0 degradation curve at pH=3 (mild acid suppresses ·OH)",
                                    "ref": "Fig.6a", "curve_label": "pH=3", "metric": "C/C0",
                                    "x_unit": "min", "y_unit": "", "level_value": "3",
                                    "points": [[0, 1.0], [60, 0.59], [120, 0.34], [180, 0.25]]}},
          {"level_value": "5", "metric_name": "removal_efficiency",
           "metric_value": {"value": 95, "unit": "%", "raw": "95% (main experimental condition)"},
           "metric_source": "text", "source_figure": "Fig.6a",
           "degradation_profiles": {"desc": "C/C0 degradation curve at pH=5 (optimal; identical to the main curve)",
                                    "ref": "Fig.4a", "curve_label": "SMX+cat+H2O2", "metric": "C/C0",
                                    "x_unit": "min", "y_unit": "", "level_value": "5",
                                    "points": [[0, 1.0], [10, 0.9], [20, 0.7], [40, 0.69], [60, 0.49], [90, 0.34], [120, 0.26], [180, 0.15]]}},
          {"level_value": "9", "metric_name": "removal_efficiency",
           "metric_value": {"value": 41, "unit": "%", "raw": "41% (not given in the text; read from the curve endpoint)"},
           "metric_source": "curve_endpoint@120min", "source_figure": "Fig.6a",
           "degradation_profiles": {"desc": "C/C0 degradation curve at pH=9 (alkaline deactivation)",
                                    "ref": "Fig.6a", "curve_label": "pH=9", "metric": "C/C0",
                                    "x_unit": "min", "y_unit": "", "level_value": "9",
                                    "points": [[0, 1.0], [60, 0.75], [120, 0.59], [180, 0.49]]}}
        ],
        "source_figure": "Fig.6a",
        "qc": {"q_flag": false, "needs_review": false, "errata_ref": "", "note": ""},
        "other": {}},
       {"variable_parameter": "catalyst_dosage", "fixed_conditions": "H2O2 5 mmol/L, SMX 10 mg/L, pH 5, 25 °C",
        "levels": [
          {"level_value": "0.1", "metric_name": "removal_efficiency",
           "metric_value": {"value": 74, "unit": "%", "raw": "74%"}, "metric_source": "text",
           "source_figure": "Fig.6b",
           "degradation_profiles": {"desc": "C/C0 degradation curve at a dosage of 0.1 g/L", "ref": "Fig.6b",
                                    "curve_label": "0.1 g/L", "metric": "C/C0", "x_unit": "min",
                                    "y_unit": "", "level_value": "0.1", "points": [[0, 1.0], [60, 0.42], [120, 0.26]]}},
          {"level_value": "0.2", "metric_name": "removal_efficiency",
           "metric_value": {"value": 95, "unit": "%", "raw": "95% (main experimental condition)"}, "metric_source": "text",
           "source_figure": "Fig.6b",
           "degradation_profiles": {"desc": "Dosage 0.2 g/L (same as the main experiment; see the main curve)", "ref": "Fig.4a",
                                    "curve_label": "SMX+cat+H2O2", "metric": "C/C0", "x_unit": "min",
                                    "y_unit": "", "level_value": "0.2",
                                    "points": [[0, 1.0], [10, 0.9], [20, 0.7], [40, 0.69], [60, 0.49], [90, 0.34], [120, 0.26], [180, 0.15]]}},
          {"level_value": "0.5", "metric_name": "removal_efficiency",
           "metric_value": {"value": 97, "unit": "%", "raw": "97%"}, "metric_source": "text",
           "source_figure": "Fig.6b",
           "degradation_profiles": {"desc": "C/C0 degradation curve at a dosage of 0.5 g/L", "ref": "Fig.6b",
                                    "curve_label": "0.5 g/L", "metric": "C/C0", "x_unit": "min",
                                    "y_unit": "", "level_value": "0.5", "points": [[0, 1.0], [60, 0.5], [120, 0.31]]}}
        ],
        "source_figure": "Fig.6b",
        "qc": {"q_flag": false, "needs_review": false, "errata_ref": "", "note": ""},
        "other": {}}
     ],
     "qc": {"q_flag": false, "needs_review": false, "errata_ref": "", "note": ""}},
    {"experiment_id": "E2", "catalyst_id": "C1", "pollutant_name": "CIP (ciprofloxacin)",
     "properties": {"pollutant_class": "antibiotic (fluoroquinolone)", "chemical_formula": "C17H18FN3O3",
                    "oxidant_type": "H2O2",
                    "oxidant_concentration": {"value": 5, "unit": "mmol/L", "raw": "5 mmol/L"},
                    "initial_pollutant_conc": {"value": 10, "unit": "mg/L", "raw": "10 mg/L CIP"},
                    "initial_pH": {"value": 7, "unit": "", "raw": "7 (degradable at neutral pH)"},
                    "removal_efficiency": {"value": 88, "unit": "%", "raw": "88% (120 min)"},
                    "(…all 40 remaining slots…)": "",
                    "degradation_profiles": {"desc": "Main degradation curve under standard conditions: C/C0 decay of CIP in the same system as E1",
                                             "ref": "Fig.7a", "curve_label": "CIP+cat+H2O2", "metric": "C/C0",
                                             "x_unit": "min", "y_unit": "",
                                             "points": [[0, 1.0], [10, 0.87], [20, 0.74], [40, 0.55], [60, 0.38], [90, 0.22], [120, 0.12]]},
                    "other": {}},
     "stability": {"cycle_count": {"value": "", "unit": "", "raw": ""},
                   "initial_efficiency": {"value": "", "unit": "", "raw": ""},
                   "final_efficiency": {"value": "", "unit": "", "raw": ""},
                   "efficiency_retention": {"value": "", "unit": "", "raw": ""},
                   "metal_leaching_conc": {"value": "", "unit": "", "raw": ""},
                   "structure_after_rxn": ""},
     "variable_experiments": [],
     "qc": {"q_flag": false, "needs_review": false, "errata_ref": "",
            "note": "No cycling stability and no TOC/COD: the corresponding slots are empty and counted as blank"}},
    {"experiment_id": "E3", "catalyst_id": "C2", "pollutant_name": "SMX (sulfamethoxazole)",
     "properties": {"pollutant_class": "antibiotic (sulfonamide)", "chemical_formula": "C10H11N3O3S",
                    "oxidant_type": "H2O2",
                    "oxidant_concentration": {"value": 5, "unit": "mmol/L", "raw": "5 mmol/L"},
                    "initial_pollutant_conc": {"value": 10, "unit": "mg/L", "raw": "10 mg/L SMX"},
                    "catalyst_dosage": {"value": 0.2, "unit": "g/L", "raw": "0.2 g/L bare Fe3O4 (control)"},
                    "removal_efficiency": {"value": 34, "unit": "%", "raw": "34% (120 min, control)"},
                    "(…all 40 remaining slots…)": "",
                    "degradation_profiles": {"desc": "Main degradation curve of the control: C/C0 decay of SMX over bare Fe3O4 without biochar",
                                             "ref": "Fig.4a", "curve_label": "bare Fe3O4", "metric": "C/C0",
                                             "x_unit": "min", "y_unit": "",
                                             "points": [[0, 1.0], [60, 0.81], [120, 0.66]]},
                    "other": {}},
     "stability": {"cycle_count": {"value": "", "unit": "", "raw": ""},
                   "initial_efficiency": {"value": "", "unit": "", "raw": ""},
                   "final_efficiency": {"value": "", "unit": "", "raw": ""},
                   "efficiency_retention": {"value": "", "unit": "", "raw": ""},
                   "metal_leaching_conc": {"value": "", "unit": "", "raw": ""},
                   "structure_after_rxn": ""},
     "variable_experiments": [],
     "qc": {"q_flag": false, "needs_review": false, "errata_ref": "",
            "note": "Control experiment; still counted as 47 fields (45 × catalysts + 47 × experiments = 231 for the reconciliation gate, item ③)"}}
  ],
  "fill_stats": {"from_text": 231, "from_image": 96, "from_errata": 2, "blank": 41}
}
```

> Slot arithmetic: the per-paper `fill_stats` sum equals 45 × (number of catalysts) + 47 × (number of experiments); the orchestrator sums all fragments and passes the total to the reconciliation gate.

## Orchestrator close-out

1. **Assembly** (assembly is the main agent's job; the canonical 06 shape is shown above): collect all fragments (fragment count = dispatch count;
   if any is missing, check against the 05 skeleton and re-dispatch per the retry policy) → take the top-level `metadata` from the C1 fragment (assembled once); insert each catalyst fragment
   into the corresponding node of the 05 skeleton's `catalysts[]` by `catalyst_id` (`role`/`evidence` copied from the skeleton;
   anchor on IDs, never pair by name); insert each experiment fragment into the corresponding node of the 05 skeleton's top-level `experiments[]`
   by `experiment_id` — assembly only inserts structurally and never changes a field value → persist `06_schema.json` per paper;
2. **Curve-fragment merge**: collect the `datamining/06_curves_<ID>.json` files written by each subagent (points copied point by point from 04 plus the copied
   `x_unit`/`y_unit`) and merge them into the corresponding positions of `06_schema.json` — **merging LLM products is deterministic IO that scripts are allowed to do;
   no script may take content from 04/03 and write 06**. After merging, check: every verified curve has a home in 06,
   has `points`, and has non-null `x_unit`/`y_unit`; if missing → **re-dispatch the subagent to read 04/03 and complete it** (revision loop,
   same pattern as the Step 8 normalizer); never let a script fill it in.
3. **Reconciliation gate**: `python step7_reconcile.py <paper folder or corpus root>`
   - **Six checks**: ① inventory count ↔ filled count (catalysts / experiments / variable **sweep groups**); ② property key names are in the list (45 / 41 + 6 stability); ③ the four `fill_stats` values sum to the total field count; ④ `pollutant_name` is a single value; ⑤ every verified curve has a home in the schema and no `points` are missing; ⑥ **variable-level alignment**: every `levels[]` item must carry both `level_value` and `metric_name`/`metric_value` (**a level without its indicator = FAIL**), the curve's `level_value` must match the item it sits in, and the number of `levels` items must be ≥ the number of `declared_levels` registered in Step 6 (fewer = missing level, FAIL; more = WARN, pending review);
   - Product `07_reconciliation.json` (single paper → `<paper>/datamining/`; multi-paper → `<corpus root>/_ledgers/`);
   - FAIL → fix according to `missing` (fill missing fields / add missing `levels` items / **re-dispatch the subagent to read 04 and complete missing `points` or axis units**) and rerun until PASS.
4. **Unit conversion is already done (by the LLM during filling) → hand to Step 8 for a pure-LLM review**: `fenton-normalizer` re-checks as a read-only reviewer
   the [NUM] field triples in `06_schema.json` (unit whitelist / conversion / missing-value completion), and the revision loop returns to the filler —
   no script is involved at all; see `step8_unit_normalization.md`.

## fill_stats caliber (identical for subagents and the reconciliation gate)

`from_text + from_image + from_errata + blank = total_fields`; **accounted separately per subagent**: a catalyst instance reports
`total_fields = 45` (each instance reports only its own C; the orchestrator **sums** the `fill_stats` of all fragments and passes the total to the gate,
where the total = 45×C + 47×E).
**Unreported fixed slots stay empty ([TXT] `""`; [NUM] an empty triple) and count as `blank`** — hence the four values always sum to
the total field count, which is exactly gate item ③.

## Verification

1. `06_schema.json` parses; assembly is complete — the `catalyst_id` of every experiment fragment matches a `catalysts[].catalyst_id`
   one-to-one with no dangling reference, the number of received fragments equals the dispatch count; the summed `fill_stats` = 45×C + 47×E;
   the four top-level keys are `metadata / catalysts / experiments / fill_stats` (no paper node may reappear;
   experiments are flat at top level, not nested; shape as in the example above);
2. Field names match the list verbatim (gate `bad_keys` empty); list carriers: catalysts 45 → `fenton-schema-catalyst/system.md`,
   experiments 41+6 → `fenton-schema-experiment/system.md` (combined fallback: `fenton-schema/system.md`);
3. **[NUM] fields are always `{value, unit, raw}` triples** (since 2026-10-09; the field lists mark [NUM]/[TXT]/[NUM|TXT] verbatim, aligned with
   the Step 8 NUM_KEYS): value is a pure number, unit a canonical unit, raw a faithful copy of the source (value carrying a unit or words, a missing raw,
   or a fabricated midpoint for a range are all failures); **[TXT] fields are plain strings written directly — wrapping them in an empty-shell triple is a failure**; samples in each filling subagent's few-shot;
4. Fields hit by a Q1/Q2 correction, or adopting a candidate contradiction's `adopted_value`, carry `qc.q_flag` (the legacy `qc.e3_flag` is equivalent); uncertain ones carry `qc.needs_review`;
5. Every curve has a home and no `points` are missing (gate item ⑤); `curve_label` matches the legend verbatim;
6. **Variable levels correspond one-to-one** (gate item ⑥): the number of `levels[]` items in each sweep group equals the number of levels the authors swept, and each item carries its own indicator value and curve;
   when the text gives no per-level removal efficiency, check whether `metric_value` was read from the curve endpoint (`metric_source` marked);
   an item with only a level value and no corresponding indicator is a failure;
7. The reconciliation gate passes.

## Next

Graph export and import: `step10_graph_export.py` → `step11_neo4j_import.py` (prefers `06b_numeric_schema.json`,
falls back to `06_schema.json` if absent — **new versions have no 06b and use 06 directly**; the triples are flattened natively by the export script into the graph's three keys).
