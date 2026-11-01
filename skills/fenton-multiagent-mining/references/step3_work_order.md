# Step 3: Work-Order Generation

> Prerequisite: Step 2 complete — `datamining/00_full_cleaned.md` + `datamining/images/` ready.
> Executed by: one `fenton-workorder` per paper (the same subagent profile can run in parallel).
> The subagent is read-only; the orchestrator persists `datamining/01_work_order.json`.

Hand the archived paper folder to `fenton-workorder`. It reads the full text, judges data value **panel by panel**, packs the extraction work order, and decides which figures the downstream extractor must process.

## Dispatch

```
delegate_task(tasks=[
  {"profile": "fenton-workorder",
   "goal": "Generate the work order: read the full text, identify figures with extractable data, judge value and needs_xy_extraction per subfigure, and pack 01_work_order.json",
   "context": "<paper folder path>"},
  # one entry per paper
])
```

## Subagent return format

The **field definitions and examples are defined in** `backend/agents/fenton-workorder/system.md` (single source of truth; not repeated here).
The orchestrator checks two things only: every subfigure carries a boolean `needs_xy_extraction` (the field must never appear at figure level), and the four `summary` counters agree with `figures`.

## Orchestrator close-out

1. Persist `01_work_order.json` per paper;
2. Aggregate each paper's `summary` into `<corpus_root>/workorder_summary.json`;
3. Re-dispatch `failed / partial` items under the retry policy (at most 2 attempts per subtask).

## Verification

1. `01_work_order.json` exists and parses;
2. The four `summary` counters agree with `figures`: `figures_xy_total` = number of figures containing at least one XY subfigure, `figures_split_total` = number of included figures, `subfigures_xy_total` = number of XY subfigures, `extraction_calls_total` = total number of included subfigures;
3. Every subfigure has a boolean `needs_xy_extraction`, and the field does not appear at figure level;
4. Purely decorative schematics are not included, tables are not included, and 3D surfaces are not included.

## Next

Read `references/step4_figure_mining.md` and execute Step 4.
