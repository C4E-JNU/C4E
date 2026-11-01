---
name: fenton-multiagent-mining
description: Use when the user supplies papers or a directory of papers and asks to run heterogeneous Fenton literature data mining with figure and table extraction. Workflow: screening, archiving, work orders, figure mining, cross-modal verification, 5B adjudication, node alignment, schema filling, numeric standardization and unit normalization, final inspection, graph ingestion. Semantic judgment is dispatched to subagents; deterministic rules and IO run in main-agent scripts.
version: 3.5.0
---

# Multi-Agent Fenton Data Mining (entry point and main-agent handbook)

You are the main agent. Follow the steps below **in order**: dispatch, wait for the result, then verify and accept it against the checklist.

- "How to judge, read, and compute" plus the **report schema** for each step live in the subagent profile `backend/agents/<name>/system.md` (the single carrier; **never copy them into these notes** - two copies always drift).
- The notes the main agent needs (dispatch templates, checklists) live in this skill package under `references/`.
- Paths and numbering: **this skill package** = `skills/fenton-multiagent-mining/` (so "the package `scripts/`" below means that directory). Experiment ids are written `E1/E2...` and are unrelated to the **retired** contradiction class letter E.

## Step index

| # | Step | Executor | Notes file |
|---|---|---|---|
| 1 | Literature screening | subagent `fenton-screener` | `references/step1_screening.md` |
| 2 | Parsing and archiving | main-agent scripts | `references/step2_parsing_archiving.md` |
| 3 | Work-order generation | subagent `fenton-workorder` | `references/step3_work_order.md` |
| 4 | Figure mining | subagent `fenton-image` | `references/step4_figure_mining.md` |
| 5 | Figure/table vs text verification (findings Q and candidate contradictions only) | subagent `fenton-verifier` | `references/step5_verification.md` |
| **5B** | **Verification supplement: cross-paper contradiction adjudication and grading** | subagent `fenton-arbiter` plus main-agent scripts | `references/step5b_adjudication.md` |
| 6 | Node construction and entity alignment | subagent `fenton-node` plus main-agent scripts | `references/step6_nodes_alignment.md` |
| 7 | Schema filling and reconciliation | subagent `fenton-schema-catalyst` (one per catalyst) plus `fenton-schema-experiment` (one per experiment, parallel) plus main-agent assembly and scripts | `references/step7_schema_filling.md` |
| 8 | Unit review (units are already produced in step 7; this step is a **pure-LLM review by a different body, no scripts**) | subagent `fenton-normalizer` (read-only review) plus the main-agent correction loop | `references/step8_unit_normalization.md` |
| 9 | Final inspection of the product | subagent `fenton-auditor` | `references/step9_final_inspection.md` |
| 10 | Graph export and Neo4j ingestion plus table export (**v2 graph model, 2026-11-02**: 29 node labels incl. 23 entity types, 27 edge types, raw provenance inside the graph; per-level VariableExperiment nodes) | main-agent scripts (step10 export / step11 MERGE import / step12 CSV tables, all rewritten for the finalized schema) | `references/step10_graph_export.md` |
| **10b** | **Legacy contradiction cleanup** (optional, one-off, idempotent) | main-agent script `step10b_clean_legacy.py` | `references/step10_graph_export.md` |

**5B is the supplement to verification**: it runs immediately after step 5, once every paper's `07_contradiction_ledger.json` is complete. It is the **only** stage in the workflow with authority to grade contradiction severity, because a cross-paper contradiction cannot be judged inside one paper and grading requires one uniform standard; that is why it is not folded into step 5.

**Division of labor**: semantic judgment (should this be collected, is the value right, do the definitions agree, which unit belongs here) goes to a subagent, because a different body is what catches its own bias; deterministic rules and IO (parsing, matching, MERGE) go to main-agent scripts, which are reproducible and verifiable.
Unit normalization is folded into step 7: the LLM filler embeds the conversion table and produces triples as it fills. Step 8 is a **pure-LLM review by a different body** (no scripts): `fenton-normalizer` checks triple legality, verifies conversions, and supplies missing units, and the correction loop returns to the filler, because scripts are error-prone on semantic strings while a context-bearing LLM reviews them more reliably. The legacy three-stage scripts now serve only the old string-based format.

**Numeric and unit rule (v2, since 2026-10-09)**: during schema filling, **numeric fields are produced directly as `{value, unit, raw}` triples** (value numeric, unit canonical, raw faithful), so structuring is done once, by the LLM. The field list marks every field **[num] / [text] / [num|text]** (aligned with the step-8 gate constant `NUM_KEYS`): only [num] fields carry triples; [text] fields are plain strings, and wrapping one in an empty triple is a failure. On ingestion the triple is flattened into the three keys `<field>` / `<field>_unit` / `<field>_raw`, so **the unit and the source text are never lost** and cross-paper aggregation never compares 1.9 nm with 19 A. **Step 8 is a pure-LLM review** (no scripts): `fenton-normalizer` checks units and conversions and supplies missing ones, the correction loop returns to the filler, and `06_schema.json` is then final. The legacy string format may still use the old three-stage scripts (documented for reference).

**Figure axis units and reading discipline (since 2026-10-09)**: figure-mining output must itself carry complete values and units. In xy records all four axis keys `label/unit/range_min/range_max` are mandatory per axis and `unit` copies the figure without conversion; in basic records every quantitative item pairs `value` with `unit` and uses canonical item names (both in `fenton-image/system.md`). The schema curve-binding table carries `x_unit` / `y_unit` copied from the 04 ledger `xy_axes`, so Curve nodes in the graph carry axis units and "60 min vs 1 h" aligns across papers.

**Variable-level rule (since 2026-10-07, updated 2026-10-23)**: a sweep group inside an experiment node is one `variable_experiments` entry and each level goes into `levels[]`, so **the level value, that level's metric, and that level's curve sit at the same level and correspond one to one** (pH 3/5/8 gives three entries, each with its own removal and curve). Do **not** flatten several levels into one string and report a single metric elsewhere, and do **not** split one sweep into three nodes. Graph export expands each level into `E1::V1::L2`, so "removal at pH 3" is directly queryable. Field definitions are in `fenton-schema-experiment/system.md` (group and level counts are locked by the 05 skeleton).

**Contradiction rules (since 2026-10-06, single source)**: `references/contradiction_taxonomy.md` defines two independent axes: **severity A/B/C/D/E** (**E = other, unclassifiable**, also the fallback for undecidable cases; graded across papers by `fenton-arbiter` in step 5B) and **findings Q1-Q4** (judged inside a paper by `fenton-verifier` in step 5). The carrier (figure-vs-text, table-vs-text, text-vs-text) is only the `evidence_source` field and **is no longer a category**. The legacy `category` and E1/E2/E3/E5 are retired.

**Graph placement (since 2026-10-06)**: only genuine contradictions enter the graph. Every contradiction carries a **three-level anchor**: `DISPUTES -> Paper` (always present, no orphans, answers "which paper"), `FROM_EXPERIMENT -> Experiment` (answers "which experiment"), and `DISPUTED_IN -> Figure` (figure nodes scoped by paper as `FIG::<paper_id>::FigureN`, answering "which figure"). Nodes carry a one-line `trace` (`paper_id / title / field / figure number`) for provenance. Non-contradictions are relabeled `:ContradictionArchived` and leave the graph (the node is **not deleted**; its `embedding` is detached, so the change is auditable and reversible).
Legacy graph re-layout: run `python scripts/step10b_clean_legacy.py --dry-run` to see the hit count, then rerun without `--dry-run`.

**Always run the regression after changing the taxonomy**: `python scripts/test_contradiction_v1.py` (35 legacy samples; it must be all green before you stop).

**Input paths always come from the user** (a PDF, a paper folder, or a directory of papers). This skill assumes no fixed path and no fixed paper count.

**Start now**: read `references/step1_screening.md` and execute step 1.
