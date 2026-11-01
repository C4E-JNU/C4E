# Step 5B: Verification Supplement · Cross-Paper Contradiction Adjudication and Grading

> **Position**: **immediately after Step 5** (run once figure/table vs. text verification has finished for all papers and `07_contradiction_ledger.json` exists everywhere) — it is a **supplementary step** of cross-modal verification, not a separate stage.
> Prerequisite: Step 5 has produced candidate contradictions (per-paper `07_contradiction_ledger.json`, all `severity` empty).
> Executed by: **dispatch `fenton-arbiter`, one instance per run** (shard by pollutant/topic when the corpus is large) → orchestrator persists + script writes back.
> Why it is separate: Step 5 can only decide **intra-paper** findings Q; **cross-paper contradictions need corpus-wide visibility and consistent grading** — deciding them paper by paper inevitably drifts. Verification therefore only emits candidates, and adjudication is done once in this supplementary step.

## Dispatch

```
delegate_task(tasks=[
  {"profile": "fenton-arbiter",
   "goal": "Assign severity A/B/C/D/E to the candidate contradictions of the supplied papers (write E when undecidable), decide adopted values, judge cross-paper exclusion and caliber differences, and produce the adjudication JSON",
   "context": "<corpus directory path>; per-paper candidate ledgers at each <paper folder>/datamining/07_contradiction_ledger.json;"
              "taxonomy at skills/fenton-multiagent-mining/references/contradiction_taxonomy.md; corpus contains N papers — cover every paper without omission"},
  # one entry per run
])
```

**One instance per run** (cross-paper judgement needs corpus-wide visibility). When the corpus exceeds 15 papers, shard by pollutant class,
but **the same topic must stay in the same instance**, otherwise cross-paper pairs cannot be found — state "judge only class XX, ignore the rest" in the context when sharding.

## What the arbiter does (see its `system.md` for detail)

| Action | Output field |
|---|---|
| Assign severity | `severity` ∈ A (numerical deviation) / B (misattributed condition) / C (naming, numbering, counting) / D (conclusion refuted by its own data) |
| Decide | `adopted_value` + `adopt_reason` (evidence chain: in-figure annotation consistency > multi-panel convergence > mass balance > multiple consistent statements in the text) |
| Judge cross-paper | `cross_paper=true` + `cross_paper_pairs[]{a,b,topic,conflict,severity,basis}` |
| Judge caliber difference | `caliber_variance=true` → goes to `caliber_only`, **not counted as a contradiction** |
| Undecidable | `severity="E"` (other · unclassifiable) + `needs_human_review=true` (**never force a grade, never leave blank, never write `unknown`**) |

Prerequisites for grading (all three must hold): (1) not our own extraction error (Q1/Q2 already excluded); (2) not an OCR artifact; (3) numerical difference ≥5% of the axis range (or 5% relative, whichever is larger).

The arbiter is **read-only**: it reads `07` / `04` / `04b` / `00_full_cleaned.md` / panel images / prior graph conclusions.
It does not need `06b_numeric_schema.json` (which is why this step can immediately follow Step 5 instead of waiting until the end).

## Orchestrator close-out

1. Persist the subagent's returned JSON **verbatim** as `<corpus>/_ledgers/contradiction_adjudication.json`
   (single paper: `<paper folder>/datamining/_ledgers/contradiction_adjudication.json`; the script auto-detects the legacy `09_arbitration.json` if present);
2. Write back to the ledgers (pure script):

```
python step5b_arbitrate_merge.py <corpus directory> [--arbitration <adjudication JSON path>]
```

→ Writes back `severity` / `cross_paper` / `caliber_variance` / the decision / the review flag into each paper's `07_contradiction_ledger.json`
→ Exports `<corpus>/_ledgers/all_contradiction_entries.json` (the contradiction list consumed by `build_report.py`)
→ Exports `<corpus>/_ledgers/cross_paper_contradiction_pairs.json` (cross-paper pairs + caliber-difference notes)
3. Go through every undecided entry (`severity=E`) **one by one**: supply missing evidence yourself where possible (back to the source text or the figure); otherwise mark it "manual review recommended" in the report.

## Verification

1. The adjudication JSON parses, and the per-level counts in `summary` match the actual number of `entries`;
2. **No empty `severity` remains in the ledgers** (backfill `E`), and **every value ∈ A/B/C/D/E** (any `unknown` is a taxonomy violation);
3. Every entry with `severity ∈ A/B/C/D` carries `adopt_reason`; every `E` carries `needs_human_review=true`;
4. Every entry with `cross_paper=true` has a matching pair in `cross_paper_pairs` (including `basis`);
5. Match keys agree: `(paper, figure, field)` maps one-to-one between ledger and adjudication; of the script's "matched N / added M", M should be 0 or explained;
6. After write-back, `by_severity` counts in `07` equal the per-severity counts in `all_contradiction_entries.json`.

> **Re-runnable**: if Step 5 or an earlier step is rerun and `07_contradiction_ledger.json` is regenerated, this step must be rerun (idempotent; it only overwrites the `severity` family of fields).

## Next

→ `step6_nodes_alignment.md` (Steps 7/8/9 read the backfilled `severity`);
finally `step10_graph_export.md` writes it into the graph's Contradiction nodes.
