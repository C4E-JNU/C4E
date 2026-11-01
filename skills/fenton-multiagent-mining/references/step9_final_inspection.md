# Step 9: Final Inspection

> Prerequisite: the Step 7 reconciliation gate passed (new product = `06_schema.json` with inline triples; legacy = `06b_numeric_schema.json`).
> Executed by: **dispatch `fenton-auditor`, one instance per paper**; the orchestrator only aggregates and never takes part in the judgement.
> Purpose: the extraction workflow has finished; bring in an independent actor holding the end-to-end product to review it once, and only import into the graph after the result is confirmed trustworthy.

## Why a subagent instead of orchestrator self-check

Everything produced in the first eight steps was "made by ourselves" — the same actor checking itself is merely marking its own homework.
The final inspector receives only the finished product and **never sees the intermediate process**, so its context is independent: it catches caliber drift that the orchestrator cannot see.
It is **read-only**, and the decision to fix stays with the orchestrator.

## Dispatch

```
delegate_task(tasks=[
  {"profile": "fenton-auditor",
   "goal": "Run the final inspection on the extraction product of <paper folder name> and produce 08_audit.json",
   "context": "<paper folder>/datamining directory path + field-list paths backend/agents/fenton-schema-catalyst/system.md and backend/agents/fenton-schema-experiment/system.md; independent review, do not modify any file"},
])
```

**One instance per paper** (isolated contexts, no cross-contamination); for several papers dispatch several instances in parallel.

## What is checked (five classes; every class must return a verdict, none may be skipped)

| Class | How | Issue condition |
|---|---|---|
| ① Field compliance | Compare the product's key names (`06`, or `06b` for legacy) verbatim against the 45 / 41 lists (**list sources**: `backend/agents/fenton-schema-catalyst/system.md` (45) and `backend/agents/fenton-schema-experiment/system.md` (41+6), with the combined fallback `backend/agents/fenton-schema/system.md`; not duplicated inside the skill, so the dispatch context must give the paths); check that units are in the normalization table; check that **[NUM] fields are `{value, unit, raw}` triples** (the field lists mark [NUM]/[TXT]; a bare string is an issue only for [NUM] fields, since strings are legal for [TXT]); for legacy, also cross-check `unparsed` / `ambiguous` entries in `06b_numeric_review.json` (new versions have no such list — skip) | Key name mismatch / unit out of bounds / bare string in a [NUM] field / triple missing `unit` or `raw` |
| ② Value–source consistency | **Sample** values back against the full text `00_full_cleaned.md` and the figure ledger `04_verified_image_data.json` | Deviation above 5% of the axis range |
| ③ Curve plausibility | ≥3 points, monotonically increasing x, y within the dimensional range, `metric` consistent with the caption | Any violation |
| ④ Ledger completeness | Candidate entries in `07_contradiction_ledger` = `04_discrepancy_ledger` + `04b` (deduplicated, **excluding Q1/Q2** — those live in `04c_errata.json`) **+ entries appended by the Step 5B adjudication** (if any); every entry carries `evidence_source` (one of the four legal values) and `adopt_reason`; `severity` has been backfilled by **Step 5B** (**no empty value, and every value ∈ A/B/C/D/E**; `E` = other · unclassifiable, always carrying `needs_human_review=true`) | Missing item / empty reason / `evidence_source` out of bounds / legacy `category` present / `severity` empty or out of bounds (`unknown` also counts as out of bounds) |
| ⑤ Graph pre-check | (1) Reference integrity: an experiment's `catalyst_id` must exist, variables must attach to an existing experiment, `pollutant_name` must be non-empty, curves must have `ref`/`points`, ids must be unique; (2) use `graph_query` to look for **synonymous pollutant nodes** already in the graph (→ merge signal) and **same-family catalysts** (→ aggregation key) | Dangling reference / empty name / duplicate id |

**Sampling discipline**: ② at least 1 record per node class and ≥3 experiment records; every entry must quote the **source text** (never just "consistent").
**Report problems only, never change data**: the orchestrator decides from each issue whether to roll back to an upstream step.

## Return format

The returned JSON's **field definitions and examples are defined in** `backend/agents/fenton-auditor/system.md` (single source of truth; not repeated here).
The orchestrator only checks: all five classes have `checked=true` and `pass`, `verdict=pass` with `blocker=0`, and every issue carries a source quotation and a location.
`severity`: `blocker` (must roll back and fix) / `major` (caliber inconsistency) / `minor` (record and watch) — **this is the inspection caliber and has nothing to do with contradiction grades A–D**.

## Orchestrator close-out

1. Persist `<paper folder>/datamining/08_audit.json` per paper;
2. Aggregate issues sorted by severity: **blocker > 0 → that paper may not proceed to Step 10**; roll back to the corresponding upstream step, fix, and rerun the inspection;
3. Record major issues in the ledger and keep minor ones under watch.

## Verification

1. Every `08_audit.json` parses, and all five classes have `checked=true` and `pass`;
2. Only `verdict=pass` with `blocker=0` may proceed to Step 10;
3. Every issue in ② carries a **source quotation and a location** (figure number / section number);
4. No upstream file was modified (the md5 of `06b` / `07` / `04` is identical to before dispatch);
5. Legacy: every `unparsed` / `ambiguous` / `unit_inferred` entry in `06b_numeric_review.json` has **an individual** verdict
   (adopt / roll back / manual); new versions have no such list, so skip this item.

## Next

→ `step10_graph_export.md` (before importing, confirm that the **Step 5B** `severity` write-back has completed)
