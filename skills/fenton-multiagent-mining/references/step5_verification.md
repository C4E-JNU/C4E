# Step 5: Figure/Table vs. Text Verification (intra-paper)

> Prerequisite: Step 4 complete — `03_image_data.json` merged.
> Executed by: one `fenton-verifier` per paper (stages one and two combined) → orchestrator assembly + scripts.
> Output: **verification findings Q1–Q4 + candidate contradictions**. **This step does not assign severity** — A/B/C/D/E is decided by the immediately following **Step 5B** (`fenton-arbiter`) across papers.
> Reference: `references/contradiction_taxonomy.md` (single source of truth).

## Dispatch

```
delegate_task(tasks=[
  {"profile": "fenton-verifier",
   "goal": "Figure/table vs. text verification + candidate-contradiction sweep: decide Q1/Q2/Q3/Q4 (our reading error / our text error / caliber difference / consistent),"
           "and for surviving candidates supply evidence only, leave severity empty, and give an adoption recommendation",
   "context": "<path to the paper folder's datamining directory>; reference skills/fenton-multiagent-mining/references/contradiction_taxonomy.md"},
  # one entry per paper
])
```

## Subagent return format (four blocks; **do not paste a full copy of 04**)

1. A per-record **annotation table** (`figure / subfigure / source / checked / value_source / finding`, covering every record without omission)
2. `04_internal_corrections.json` (Q1/Q2 — our own extraction errors; never enter the contradiction store)
3. `04_discrepancy_ledger.json` (candidate contradictions, **severity left empty**)
4. `04b_text_table_contradictions.json` (text/table candidate contradictions, **severity left empty**)

The **field definitions and examples for all four blocks are defined in** `backend/agents/fenton-verifier/system.md` (single source of truth; not repeated here);
`severity` is always an empty string, and `evidence_source` takes only the four legal values.

> `04_verified_image_data.json` is a **full copy** of `03_image_data.json` and far exceeds the single-return limit.
> The orchestrator script copies 03 into 04 using the annotation table (match key `(figure, subfigure)`) and **must retain every `curves[].points[]`**
> — the downstream schema-filling subagent reads points from there by `(figure, subfigure) + curve_label` and writes curve fragment files (no chat round-trip, no truncation).

## Orchestrator close-out

1. Persist: the annotation table as `datamining/04_annotation_table.json`, and the other three JSON files verbatim as
   `04_internal_corrections.json` (Q1/Q2), `04_discrepancy_ledger.json` (candidates),
   `04b_text_table_contradictions.json` (text/table candidates);
2. Assemble: copy `03` into `04` using the annotation table (retaining every `curves[].points[]`)

```
python step4_assemble_verified.py <paper folder or corpus root>
```

3. Final review: check the direction of each candidate's `adopted_value`; for OCR-suspect entries compare against the origin PDF **text layer**;
4. Issue the ledgers (**normalize the legacy caliber + emit verification gain**):

```
python step4c_errata.py <paper folder or corpus root>
```

→ `07_contradiction_ledger.json` (per-paper candidate ledger, `severity` empty/E pending)
→ `04c_errata.json` (Q1/Q2, our own extraction errors)
→ `05b_verification_gain.json` (per paper) + `<corpus>_verification_gain_summary.json` (cross-paper C/e/R/Δ)

> **Severity is not decided here**: `severity` in `07` is backfilled by **Step 5B** (`fenton-arbiter`),
> then merged with `step5b_arbitrate_merge.py` (see `step5b_adjudication.md`).
> All three scripts live in this skill package under `scripts/`; pass either a single paper folder or a corpus root (auto-detected).

## Verification

1. `04_verified_image_data.json` is a full list copy (not a dict), and the `curves[].points[]` counts match 03 point for point
   (the assembly script prints `curve points N/N OK`; any mismatch is a failure);
2. The assembly script reports an empty "unannotated" set (the annotation table covers every 03 record) and an empty "extra annotation" set (no unmatched keys);
3. The three JSON files do not overlap: **Q1/Q2 only in `04_internal_corrections.json`; Q3 in `04_discrepancy_ledger.json` with `caliber_variance=true` (the orchestrator extracts `caliber_only`, which does not count as a contradiction); Q4 only in the annotation table**;
4. Every candidate has **an empty `severity`** (any A–E value here is out of scope — grading belongs to Step 5B);
5. All `evidence_source` values fall within the four legal values (`text-vs-figure` / `text-vs-table` / `text-vs-text` / `figure-vs-table`);
6. OCR artifacts have been removed (suspect entries checked against the PDF text layer);
7. Spot-check several candidates: `adopted_value` agrees with the evidence chain, and `confidence` matches the actual certainty;
8. Verification gain has been emitted: in `05b_verification_gain.json`, `N` > 0, `checked ≤ N`, and `Δ = fixed / N` is self-consistent with `fixed`/`e`;
9. **Endpoint backfill** (`finding.endpoint_backfill`) appears only in the annotation table (the canonical Q4 home), each entry carries `points_after`, and the point count
   matches that curve in the figure ledger (only the endpoint value is replaced by the text value); `04_internal_corrections.json` contains no Q4 entries (otherwise `step4c` would misclassify them as Q1/Q2).

## Next

→ **Step 5B** (`fenton-arbiter` grades all candidate contradictions uniformly) → then Step 6 (node construction and entity alignment).
