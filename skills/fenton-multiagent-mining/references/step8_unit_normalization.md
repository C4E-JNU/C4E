# Step 8: Unit Review (pure LLM, no scripts)

> **Revised 2026-10-09**: unit conversion is already performed by the LLM during Step 7 filling (the conversion table is embedded in the two filling subagents, which emit triples directly).
> This step is an **independent review by a different actor**: dispatch `fenton-normalizer` (review mode) to read-only review every [NUM] field in `06_schema.json` —
> verify triple legality, check each conversion against the canonical unit table, and supply missing units — **with no script involved**.
> (Scripts misjudge strings that contain conditional clauses; deterministic conversion is done by the LLM with context, and the review adds a second pair of eyes.)
> The "Appendix" below preserves the legacy three-stage script flow for the "value + unit" string caliber; the script's field lists
> (`CATALYST_NUM_KEYS`, etc.) remain the reference source for the [NUM] annotations used by the filling subagents.

> Prerequisite: the Step 7 reconciliation gate passed (`06_schema.json` ready; [NUM] fields are already triples).
> Executed by: **dispatch `fenton-normalizer` (review mode, grouped by node) → orchestrator persists the revision list → revision loop**.
> Output: `06c_unit_review.json` (review record + revision list) + the revised `06_schema.json`.

## Dispatch (grouped by node: one group per C or per E)

```
delegate_task(tasks=[
  {"profile": "fenton-normalizer",
   "goal": "Review all [NUM] field triples of C1: unit whitelist / conversion correctness / missing-unit completion; return the revision list only",
   "context": "<paper folder>/datamining directory path; field list backend/agents/fenton-schema-catalyst/system.md"},
  # one entry each for E1, E2…, with the field list switched to fenton-schema-experiment/system.md
])
```

## What the reviewer checks (five classes; details in its system.md)

(1) Triple legality (value is a pure number or null, unit is in the whitelist, raw is non-empty); (2) conversion correctness (checked one by one against the canonical unit table:
K→°C, h→min, mg/L↔g/L typos); (3) missing units supplied from field semantics (`unit_inferred`); (4) non-convertible units kept as-is
(At% + annotation); (5) [TXT] fields wrongly wrapped in triples, and `levels` metric_value consistency with stability.

## Orchestrator close-out

1. Merge the per-node returns and persist `<paper folder>/datamining/06c_unit_review.json`;
2. **Revision loop**: hand `revisions` back to the corresponding filling subagent instance → it returns only corrected fragments for the affected fields → the orchestrator re-assembles
   `06_schema.json` (the same assembly mechanism as Step 7); if `revisions = 0`, go straight to Step 9;
3. Record `uncertain` entries in the ledger for the Step 9 auditor.

## Verification

1. `06c_unit_review.json` parses, and the `checked` statistics are consistent with the node's [NUM] field count;
2. Every revision carries before/after/reason; `after` has a pure-number value, a whitelisted unit, and a raw identical to before;
3. Spot-check 2 revisions to confirm `06_schema.json` was actually updated;
4. `uncertain` may never be silently omitted.

---

## Appendix: legacy script flow (only for the "value + unit" string caliber)

## Why neither pure scripts nor pure LLM

A pure script suffices for simple strings (`"12.5 mg/L"`) but always mis-parses the hard ones: conditional clauses stealing the value (`"TGA 450-600 ℃"`),
digits inside chemical formulas (`N2` / `Fe3O4`), co-existing calibers (`">100 nm, plus two other spots of about 40 and about 70 nm"`),
non-convertible units (`At%` vs `wt%`) — these need context, which a script does not have.
A pure LLM, in turn, introduces "one dataset written two ways". Hence: **deterministic first pass by script, semantic finalization by subagent, validation and persistence back to script**.

## ① First pass (script)

```
python step9_numeric_normalize.py <paper folder or corpus root> --prepare
```

Produces `<dm>/06b_numeric_tasks.json`:

- `tasks[] = {id, field, node_type, raw, draft, flags}`; `draft` is the mechanical first pass (**may be wrong**, provided for reference);
- `flags ∈ {unparsed, ambiguous, unit_inferred, converted, multi}`; **flagged items are ordered first**;
- also produces `06b_numeric_draft.json` (a full draft with the same structure as 06_schema).

## ② Finalization (subagent `fenton-normalizer`)

```
delegate_task(tasks=[
  {"profile": "fenton-normalizer",
   "goal": "Finalize the numeric fields of C1 (return only the ids in this assignment): value / unit / source text / basis",
   "context": "<paper folder>/datamining directory path; pending list in 06b_numeric_tasks.json; return only entries whose id starts with C1."},
  # other nodes: C1.E1. (including variables/stability) / C1.E2. …
])
```

- **Group by node** (one C node, or one E node, per group) — a single instance output is capped at about 12,000 characters, so a whole paper in one call would always be truncated;
- **Variable level items (v2)**: ids look like `C1.E1.V1.L2.level_value` / `…L2.metric_value` (one pair per level — the level value and that level's indicator sit at the same level); **every item must be judged**;
  skipping any leaves that level at the mechanical draft, and the graph then cannot answer "the removal efficiency at pH=3";
- Criteria, unit whitelist, canonical unit table, id naming and return schema are in `backend/agents/fenton-normalizer/system.md` (**single source of truth**; not repeated here);
- The orchestrator only checks: full coverage of these ids, `unit` whitelisted or `null`, `value` of a legal type, and `unparsed` entries carrying `basis`.

## ③ Validation and persistence (script)

```
python step9_numeric_normalize.py <paper folder or corpus root> --from-agent <return.json>
```

- **Merge**: agent values win where given; uncovered ids keep the mechanical draft flagged `source: mechanical`;
- **raw always comes from the source string in `06_schema.json`** (the LLM may not rewrite the source text; the script enforces this);
- **Validation gate** (any failure → `gate=FAIL`, process exit 1):
  `unknown_id` (id not in the list) / `bad_field` (key name not in the list) / `bad_unit` (unit out of bounds) /
  `bad_value` (illegal value type) / `structure` (tree structure differs from 06_schema) / `bare` (a bare string remains un-standardized);
- **Warnings only, no FAIL**: `not_canonical` (unit is not the canonical unit for that field, e.g. a non-convertible At%),
  `unit_inferred` (the source string gave no unit and it was supplied from field semantics).

## Products

| File | Content |
|---|---|
| `06b_numeric_schema.json` | **Finished product**: same tree structure as 06_schema; numeric fields = `{value, unit, raw}` (may also carry `range` / `uncertainty` / `qualifier` / `converted` / `basis` / `source`) |
| `06b_numeric_stats.json` | Counts + `source_counts` (agent / mechanical) + `flags` + `issues` + `verify` |
| `06b_numeric_review.json` | Review list handed to Step 9 (unparsed / ambiguous / unit_inferred / non-convertible) |
| `06b_numeric_tasks.json` · `06b_numeric_draft.json` | Intermediate products (archived) |

**`06_schema.json` is never rewritten** (the source text stays on record and remains traceable).

**Fallback path**: running without arguments → pure mechanical persistence (`source=mechanical`), for quick verification when subagents are unavailable;
**a formal run must use --prepare / --from-agent**.

## Verification

1. `gate=PASS` (no unknown_id / bad_unit / bad_field / bad_value / structure / bare);
2. Tree structure identical to `06_schema.json` (same catalyst / experiment / variable node counts);
3. Every target field is a triple, with no bare strings left;
4. All units fall inside the whitelist (from the source string or supplied from field semantics, the latter recorded as `unit_inferred`);
5. Every `unparsed` / `ambiguous` item in `06b_numeric_review.json` is explainable (long sentence, qualitative, multiple calibers), and the list goes to Step 9;
6. `stats.source_counts.agent + stats.source_counts.mechanical` = the total number of standardized fields.

## Next

→ `step9_final_inspection.md`
