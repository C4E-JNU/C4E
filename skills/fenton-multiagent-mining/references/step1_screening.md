# Step 1: Literature Screening (system fit + duplicate check)

> Prerequisite: papers supplied by the user, either as PDFs or as pre-extracted paper folders.
> Executed by: dispatch `fenton-screener` (read-only; the orchestrator persists results).

**First locate the path the user supplied.** An extracted paper folder looks like:

```text
<paper_folder>/
├── full.md                  Full text parsed by MinerU
├── images/                  Individual figures cropped by MinerU (hash-named jpg/png)
├── *_content_list.json      MinerU index (caption ↔ image mapping)
└── *_origin.pdf             Original PDF (used by later steps; untouched here)
```

Hand the path to the PDF or to `full.md` to the dedicated `fenton-screener` subagent. It decides two things: whether the paper belongs to the heterogeneous Fenton system, and whether the paper already exists in the knowledge graph.

**Supply only the full text of that single paper.** Do not supply design reports, step documents, other papers, the global ledger, or earlier conclusions — collateral context leaks priors into the decision.

For several papers, dispatch several `fenton-screener` instances (the same subagent profile can run in parallel). Do not exceed 5 papers per subagent instance, or the context will overflow.

## Dispatch

```text
delegate_task(tasks=[
  {"profile": "fenton-screener",
   "goal": "Screen this paper: decide whether it fits the heterogeneous Fenton system and whether its data already exist in the neo4j knowledge graph; return pass/skip/manual with evidence",
   "context": "<path to this paper's PDF or full.md>"}
])
```

## Subagent return format

The **field definitions and examples are defined in** `backend/agents/fenton-screener/system.md` (single source of truth; not repeated here).
The orchestrator checks three things only: `decision` (pass/skip/manual), a non-empty `system.evidence`, and that all three routes of `duplicate.checked` carry values.

## Orchestrator close-out

1. Persist `00_screening.json` per paper, adding `screened_at` and `screened_by`.
2. Act on `decision`: `pass` → proceed to Step 2; anything else ends this paper here.

## Verification

1. Every paper has a parseable `00_screening.json`.
2. `system.evidence` is non-empty; for `skip`, `reasons` is non-empty and quotes the source text.
3. All three routes of `duplicate.checked` carry values (use `"unavailable"` when a route cannot be reached; never leave blank and never default to false).
4. The aggregated `by_decision` counts match the actual files.

## Next

Verification passed → Step 2: parsing and archiving (`references/step2_parsing_archiving.md`).
