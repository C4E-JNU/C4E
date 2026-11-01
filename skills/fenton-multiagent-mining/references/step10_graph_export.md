# Step 10: Graph Export, Neo4j Import, and Table Export (v2, 2026-11-02)

> Prerequisite: Steps 6-9 are finalized and the per-batch standardization passes
> (reports 46-61 under `审查脚本和报告/键分析报告/`) are all green — every node type in the
> corpus is 100% English, every entity library is locked, and all three checkers
> (`check_node_metadata.py`, `check_node_catalyst.py`, `check_node_experiment.py`) report FAIL 0.
> Executor: **main-agent scripts only; no subagent is dispatched** — deterministic IO and
> idempotent MERGE; handing this to an LLM would only add uncertainty.
> The three v2 scripts replace the 2026-10-25 v1 pipeline wholesale (v1 archived under
> `scripts/_deprecated/` and `references/*.bak_20261102_v1`).

## What changed versus v1 (why the rewrite was required)

The corpus finalization (Nov 2026) moved the schema from "one flat experiment/catalyst
record with a few entities" to a full property graph:

| v1 (2026-10) | v2 (2026-11, current) |
|---|---|
| Paper / Catalyst / Experiment / Variable / Curve / Contradiction / Entity (single label) | **29 node labels**: the six above (Contradiction unchanged) plus Journal, Element, InteractionType, Chemical, Support, SynthesisFlow, TreatmentFlow, Phase, Form, Morphology, FunctionalGroup, Defect, CharacterizationMethod, Pollutant, PollutantClass, Medium, ProcessMode, CoexistingSubstance, Oxidant, LightCondition, Agitation, KineticsModel, ROSType |
| catalyst/experiment numeric triples flattened on the node only | unchanged, **plus** relationship-carried measurements (element content on `HAS_ELEMENT`, calcination conditions on `USES_FLOW`, lattice/size on `HAS_PHASE`, particle size on `HAS_MORPHOLOGY`, dosage/ratio on `USES_CATALYST`/`USES_OXIDANT`/`TARGETS`) |
| provenance scattered in files | **raw travels inside the graph**: every relationship has a `raw` property; narrative `raw` also remains on the owning node (`<field>_raw`) |
| pollutant alignment per batch | consolidated **Pollutant alignment library**: PubChem-CID anchor (186 chemical entities) + typed non-chemical entities (real/mixed matrices, commercial dyes without CID, biological, metal complexes, reference stubs) — 961 distinct names, 100% mapped |
| variable levels nested in one node | **one VariableExperiment node per level** (`level_value` numeric scalar; dual-basis parsing "10 mg（0.5 g/L）" → main + alt), level curves attached to the level node |

## Commands

```
python step10_graph_export.py                                   # corpus + entity libraries -> graph_export/
python step11_neo4j_import.py [--dry-run] [--purge-all-papers | --purge-paper <dir_name>]
python step12_table_export.py                                   # graph_export + libraries -> CSV tables
```

`FENTON_DATA_ROOT` (env) or auto-discovery locates the corpus root that contains
`审查脚本和报告/实体对齐库`. Connection settings are read from `scripts/neo4j.env`.

## 10① Export (step10 → `graph_export/`)

Assembles, from the finalized corpus and the entity alignment libraries, a uniform set of
`nodes_<Label>.json` (`{"label","key_field","nodes":[...]}`) and `edges_<TYPE>.json`
(`{"type","from_label","from_key","to_label","to_key","edges":[...]}`) + `manifest.json`.

Node keys (stable across exports — `paper_id` is the corpus directory name, not a hash):

| Label | Key | Notes |
|---|---|---|
| Paper | dir name | bibliographic keys + journal_key |
| Catalyst | `<paper_id>::<catalyst_id>` | 43 keys; triples flattened `<field>`/`_unit`/`_raw` |
| Experiment | `<paper_id>::<experiment_id>` | 40 keys; triples flattened likewise |
| VariableExperiment | `...::V<i>::L<j>` | **one node per level**; `level_value` numeric (main + alt basis) |
| StabilityRecord | `...::STAB` | cycle/initial/final/retention/leaching triples + narrative |
| Curve | `...::Q<i>` / `...::Q` | points as `points_json` (Neo4j has no 2-D arrays) |
| 23 entity labels | natural key (symbol / tag / chemical_key / phase / flow_key / journal_key / pollutant_key / …) | from the locked entity libraries |

Edges (27 types), with the measurement/provenance they carry:

| Edge | From → To | Carries |
|---|---|---|
| HAS_ELEMENT | Catalyst → Element | **value, unit** (content), basis, method, role, source, raw |
| USES_OXIDANT | Experiment → Oxidant | concentration / dosage / molar-ratio triples, raw |
| USES_CATALYST | Experiment → Catalyst | dosage, mass ratio, raw |
| TARGETS | Experiment → Pollutant | initial conc, removal efficiency, k, raw |
| USES_FLOW | Catalyst → SynthesisFlow | calcination temp/time/rate/atmosphere, thermal_steps, condition_attribution, raw |
| HAS_PHASE | Catalyst → Phase | role (main/impurity), lattice_a/c, crystallite_size, lattice_spacing, attribution, raw |
| HAS_MORPHOLOGY | Catalyst → Morphology | particle_size, attribution, raw |
| HAS_VARIABLE | Experiment → VariableExperiment | — |
| HAS_CURVE | Experiment / VariableExperiment → Curve | — |
| HAS_STABILITY | Experiment → StabilityRecord | — |
| IDENTIFIES_ROS | Experiment → ROSType | method (EPR/quenching/probe/…), raw |
| BELONGS_TO | Pollutant → PollutantClass | votes |
| PUBLISHED_IN | Paper → Journal | — |
| HAS_INTERACTION / PREPARED_FROM / SUPPORTED_ON / POST_TREATED_BY / HAS_FORM / HAS_FUNCTIONAL_GROUP / HAS_DEFECT / CHARACTERIZED_BY / COEXISTS_WITH / IN_MEDIUM / OPERATED_AS / AGITATED_BY / FOLLOWS_KINETICS | Catalyst / Experiment → entities | raw |

## 10② Import (step11 v2)

- uniqueness constraints per label; **MERGE by key field** (idempotent);
- edges: MATCH both endpoints by key, MERGE relationship, SET all properties (raw included);
  unmatched endpoints are counted per type (must be 0);
- `--dry-run` writes nothing; `--purge-all-papers` / `--purge-paper <dir>` DETACH-DELETE stale
  nodes first (paper_id is now the stable directory name, so purging is only needed after
  corpus-wide deletions, not after schema revisions);
- reconciliation after import: per-type `db count >= manifest count` must hold.

## 10③ Table export (step12 v2)

CSV tables (UTF-8-SIG), identical to `审查脚本和报告/表格导出_20261102/`:
papers / catalysts / experiments / variable_experiments / stability / curves +
23 entity tables + 25 edge tables. Tables and graph share keys, so they cannot disagree.
Numeric triples are three columns (`<field>`, `<field>_unit`, `<field>_raw`); entity keys are
two columns (`<key>_value`, `<key>_raw`).

## Archive `other_*` node properties

The archive layers `cat_other` / `exp_other` are flattened onto the owning Catalyst /
Experiment node with an `other_` prefix (`other_notes`, `other_role`, `other_particle_size_range`,
`other_<key>_original`, ...). Strings and lists of strings stay native; nested objects are stored
as JSON strings. The mirror keys removed during cleanup (synthesis_steps, duplicated catalyst_name /
pollutant_name) were deleted from the corpus before export, so they never appear here.

## Vector search (semantic retrieval)

`step11_neo4j_import.py --embed` composes an English `search_text` per node
(all scalar properties except `points_json`, plus every `*_raw` provenance text), embeds it with
**BAAI/bge-small-en-v1.5 (384 dims, via fastembed)**, stores the vector in the `embedding` node
property, and creates cosine vector indexes `vec_paper / vec_catalyst / vec_experiment /
vec_variableexperiment`. Requirements: `pip install fastembed`; the first run downloads the 67 MB ONNX model
(qdrant/bge-small-en-v1.5-onnx-q). On networks where huggingface.co is unreachable or the
Xet storage returns 401, step11 automatically retries through the mirror
(`HF_ENDPOINT=https://hf-mirror.com` with `HF_HUB_DISABLE_XET=1`) - verified working.
If embedding is still unavailable the import completes without vectors; rerun with `--embed` to fill them.

```cypher
// semantic query: top-10 catalysts similar to a natural-language question
CALL db.index.vector.queryNodes('vec_catalyst', 10, $question_embedding) YIELD node, score
RETURN node.catalyst_name, score;
```

Embedding model note: the corpus is 100% English, so the v1 Chinese model (bge-small-zh-v1.5,
512 dims) is replaced by bge-small-en-v1.5 (384 dims). Changing the model requires dropping and
recreating the vector indexes with the new dimensionality and re-running `--embed`.

## Reference query examples

```cypher
-- catalysts with Cu content > 50 wt% and their evidence
MATCH (c:Catalyst)-[r:HAS_ELEMENT]->(:Element {symbol:"Cu"})
WHERE r.unit IN ["wt%","at%"] AND r.value > 50
RETURN c.catalyst_name, r.value, r.method, r.raw;

-- all pH=3 variable levels of one experiment, ordered
MATCH (e {experiment_id:"E1"})-[:HAS_VARIABLE]->(v {variable_parameter:"initial_ph"})
RETURN v.level_value, v.metric_value ORDER BY v.level_value;

-- all experiments identifying ·OH as the dominant ROS, with detection method
MATCH (e)-[:IDENTIFIES_ROS]->(r:ROSType {ros_type:"·OH"})
RETURN e.experiment_key, r.method;
```

## Verification checklist

1. `manifest.json` counts equal the row counts of every `nodes_*.json`;
2. step11 `--dry-run` node/edge totals equal the manifest before any write;
3. after import, per-type `db count >= manifest count` and no unmatched endpoints;
4. spot-check provenance: `HAS_ELEMENT.raw` non-empty where `source="raw-per-element"`;
   `USES_FLOW.raw` carries the synthesis narrative; Curve `points_json` parses and
   `n_points` agrees;
5. three corpus checkers remain FAIL 0 after any re-export.
