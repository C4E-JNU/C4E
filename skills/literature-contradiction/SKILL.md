---
name: literature-contradiction
description: "Use when auditing the internal consistency of a single archived paper. First classify our own errors / caliber differences / agreement as Q1–Q4 (the verification scorecard), then grade genuine contradictions A–E (E = other · unclassifiable). Read figure values from pixels first (image_read, both engines wired), then cross them three ways against the caption and the text; figure and table ledgers run in parallel via delegate_task(role=contradiction), and the paragraph ledger is done by the orchestrator."
version: 1.5.0
tags: [literature, contradiction-audit, figure-ledger, table-ledger, paragraph-ledger, pixel-reading]
---

# Single-Paper Reconciliation (figure ledger · table ledger · paragraph ledger → contradiction table)

Audit the internal consistency of **one** archived paper: figures against text, tables against text, and paragraphs against each other; find genuine contradictions, adjudicate them, and output a table.

## 🔑 Two axes (single source of truth; never mix them)

| | **Axis 2 · verification finding Q** | **Axis 1 · severity of a genuine contradiction** |
|---|---|---|
| Answers | How many of our own errors verification found, and of what value | How serious the paper's own contradiction is |
| Values | **Q1 / Q2 / Q3 / Q4** | **A / B / C / D / E** (E = other · unclassifiable; ungradable → E) |
| Assigned by | This skill throughout | This skill (decidable intra-paper) |
| Destination | **Never into the contradiction ledger** (Q3/Q4 not even into the verdict) | The ledger's `severity` |

- The evidence carrier (figure-text / table-text / text-text / table-table) **is not a classification**; it is downgraded to the field `evidence_source` (`text-vs-figure` / `text-vs-table` / `text-vs-text`), used only for traceability.
- `cross_paper` (cross-paper exclusion) is a boolean flag: **this skill runs in single-paper mode and never sets it** (it cannot be decided intra-paper and needs corpus-wide visibility).
- **`E` is a legal level** (other · unclassifiable, also the "cannot grade" fallback); **the old E as a class prefix (E1/E2/E3/E5) is deprecated**, and `unknown` is no longer used.
- Method: **classify Q first, then grade A–E**; report only genuine problems in the paper, and **prefer omission to over-reporting**.

## 🔒 File-reading whitelist (iron rule)

Read only: `00_full_cleaned.md` (paged; continue with the returned `next_offset`), `00_images_map.json`, `images/FigureN/FigureN.txt`, `images/TableN/TableN.txt`, and the image files under `images/` (**must** go through the "pixel reading" channel below).
**❌ Never read**: `layout.json`, `*_model.json`, `*_content_list*.json` (huge dumps).

## 🖼 Pixel reading (image_read channel) — the third source of the figure ledger

**Why it is mandatory**: pie sectors, bar heights, scatter points and spectral peaks **exist only in pixels**; neither the caption nor the text carries them. Reconciling "caption ↔ text" alone means the figure ledger is only half done.

**Channel status (both engines are wired; do not speculate)**: `read_file(path="x.jpg")` / `read_file(file_id=...)` return a **structured image block** `[{"type":"text",...},{"type":"image_url",...}]`.
- **langchain engine**: uses the framework's native path (`_format_output` → `ToolMessage.content` blocks → `role=tool` with `image_url` on the wire), zero middleware.
- **custom engine**: the loop promotes the image into a separate `user` image block, leaving only a text stub in the tool message.
Both engines let the orchestrator "see" pixels directly. See `references/image_read_channel.md`.

**Two iron rules**
1. **Only the orchestrator may read pixels**: subagents have no image channel (tool results are plain text truncated at 4,000 characters). The orchestrator reads, transcribes into a plain-text "pixel reading table", inserts it into the excerpt pack, and only then dispatches the subagent.
2. **If it cannot be read, write "needs figure check" — never invent a number** (historical incident: an unread source produced 0.88 → a false contradiction). Any "value" that comes from a figure rather than text must be marked `source: image_read` in the ledger.

**Preconditions (three gates; failing any one degrades the run)**
1. **Channel gate**: both engines enable it by default (`set_image_emit("blocks")`). A return of `mode=image_no_inline` means it was explicitly disabled (the langchain engine honours `C4EAI_LC_IMAGE_MODE=off`) → restore the default and restart, or ask the user to send the image as an attachment for that turn.
2. **Size gate**: a single dataURL must be ≤ `IMAGE_INLINE_MAX_CHARS` (**1,600,000 characters**, overridable with `C4EAI_IMAGE_INLINE_MAX_CHARS`); beyond that it returns `mode=image_too_large`. When over the limit, use `run_code` + PIL to **crop the panel / downscale / split in two and register each half**.
   ⚠️ **Never lower JPEG quality to "save tokens"**: visual tokens scale only with pixel count (about 1 token per 1,024 px), so lowering quality loses information for free and saves nothing.
3. **Registration gate**: to read by `file_id` the image must live in the **output area** (`users/<u>/outputs/`) → `list_outputs()` registers it automatically and returns the `file_id`.

**Operation sequence (run by the orchestrator itself)**
```python
from PIL import Image      # run_code's working directory is the output area; write the filename directly
im = Image.open(r"<archive>/images/Figure6/<hash>.jpg")
im.crop((0, 0, im.width, im.height//2)).convert("L").save("fig6_top.png", optimize=True)
```
→ `list_outputs()` to obtain the file_id → `read_file(file_id=..., as_base64=True)` → transcribe panel by panel into a **pixel reading table** `{panel, axis/range, legend series, reading, unit, uncertainty}`; **estimate uncertainty as half the smallest axis tick**, and leave it blank when it cannot be estimated — **a reading without an uncertainty may not enter the ledger**.

## Adjudication system (applies throughout)

### Step 1 · Classify Q — these **never enter the contradiction ledger**

| Code | Meaning | Handling |
|---|---|---|
| **Q1** | Our **figure-reading error**: crop/caption numbering misalignment, wrong value read from a figure | Correct the reading, record Q1 |
| **Q2** | Our **text/table-reading error**: transcription error, condition mismatch; **and OCR artifacts** (the cleaned text disagrees with the original `TableN.txt`/`FigureN.txt` → the original wins) | Correct, record Q2 |
| **Q3** | **Caliber difference**: different unit/basis/conversion (mineralization vs. removal, dry vs. wet weight); neither side is wrong | Keep both values + set `caliber_variance=true`, **not a contradiction** |
| **Q4** | **Consistent**: relative difference <1% (rounding), difference <5% of the range (take the text value within reading error), or image estimation error (≤ 1 axis tick × 2) | Counts toward coverage only |

Q1/Q2 are **our own errors** (this is exactly where verification earns its keep), Q3 is an explanation, Q4 is coverage — none of the three enters the contradiction ledger.
**"Image estimation error" gets no separate code**: it belongs to Q4; only a wrong value caused by our own crop/numbering error is Q1.

### Step 2 · Grade severity A–E (the only classification, written to `severity`)

**Grading prerequisites (all three must hold)**: (1) Q1/Q2 excluded (not our extraction error); (2) OCR artifacts excluded; (3) difference ≥5% of the range (or ≥5% relative, whichever is larger) — **anything within 5% is not a contradiction**.

| Level | Name | Criterion | Typical |
|---|---|---|---|
| **A** | Numerical deviation | Same order of magnitude and direction, only beyond tolerance | Text 92% vs. figure 88% |
| **B** | Attribution / condition mismatch | The value is right but attached to the wrong sample, or temperature/pH/dose/time/dosage is misplaced | The 95% at 90 min is written as the 120 min value |
| **C** | Naming / numbering / counting error | Material name, figure number, experiment number or count inconsistent | Text says Fig.7a while the caption says Fig.7b; "four groups" are really three |
| **D** | Conclusion refuted by its own data | A qualitative conclusion runs opposite to, or is unsupported by, the paper's own data | Claims "significant enhancement" while the data show no difference |
| **E** | Other (unclassifiable) | Has opposing values but **fits none of A–D**, or **evidence is insufficient to grade** | Text is qualitative while the figure shows a numerical trend; prerequisites incomplete |

- **Use only the five values A/B/C/D/E.** When two different sources inside the paper conflict (figure A vs. figure B, figure vs. table, pixel vs. caption/text), grade A–D by the closest fitting criterion; **anything outside A–D is E**. Cross-paper exclusion needs corpus-wide visibility → this skill does not judge it and says so honestly.
- **Cannot grade / outside A–D** → `severity="E"` + `needs_human_review=true`; **never force a grade, never leave it empty, never write `unknown`**.
- **Only two classes count as genuine contradictions**: (1) the same quantity with two values (same object, same indicator, different values in different places); (2) a conclusion refuted by its own data.

**Not reported**: vague wording (about/nearly), terminology drift, inconsistent abbreviations, reference numbering.
**Special case**: when the text contradicts itself, **do not default to "text wins"** — take the side with more corroboration and quote both sources in full.

## Flow (five stages)

### Stage 0 · Orchestrator structural scan (the prerequisite for dispatch)
1. `read_file` on `00_images_map.json` → obtain all logical figures (folder/token/caption_en/panels).
2. Read `00_full_cleaned.md` once, page by page, producing two things as you go:
   - **figure/table → text excerpt pack**: for every data figure and every table, extract the exact sentences where the text cites or describes it (with section location), ≤6,000 characters per unit;
   - **paragraph assertion table**: extract data assertions {entity, indicator, value, condition, location} (record every occurrence of the same quantity).
3. **Figure classification (domain-independent criteria)**:
   - **Enters the figure ledger (has readable content)**: the caption/text shows coordinates, units, numbers, curves, peak positions, bars/proportions/heat maps/spectra/microscopy measurements, i.e. **readable data**;
   - **Skipped (no data content)**: mechanism schematics, flow block diagrams, setup schematics, route maps, pure photographs and other figures with nothing to read;
   - **When unsure → skip**, and list it in the "unchecked figures" list at the end.
4. **Pixel reading**: for every figure entering the ledger, read it as described above, transcribe panel by panel, and incidentally check **whether the numbers cited in the caption agree with the pixels** (this is where B-level issues usually appear: subfigure letters swapped with conditions). If unreadable → mark that panel "needs figure check".

### Stage 1 · Figure ledger (parallel subagents)
One subtask per **data figure**, dispatched in groups (at most the maximum subtask count per round):
```
delegate_task(tasks=[
 {"role":"contradiction","tools":["read_file"],
  "goal":"Figure ledger: reconcile Figure2 against the text. Three-way cross-check: (1) caption/text numbers and trends vs. the pixel reading (entries with source:image_read in the excerpt pack); (2) whether subfigure letters (a/b/c…) are swapped with the text conditions (B level); (3) conclusion direction vs. the data shown. Classify Q first (Q1 figure misread / Q2 text misread / Q3 caliber difference / Q4 consistent), then grade genuine contradictions severity A|B|C|D|E (E when ungradable or outside A–D). Output a JSON array only: [{\"kind\":\"contradiction|q\",\"q\":\"Q1|Q2|Q3|Q4|\",\"severity\":\"A|B|C|D|E|\",\"evidence_source\":\"text-vs-figure\",\"figure\":\"Figure2\",\"field\":\"...\",\"text_quote\":\"...\",\"figure_quote\":\"...\",\"value_a\":\"...\",\"value_b\":\"...\",\"adopted_value\":\"...\",\"source\":\"image_read|text\",\"confidence\":\"high|medium|low\"}], output [] when nothing is found. Never restate the text.",
  "context":"<full caption of this figure + relevant text excerpts + this figure's pixel reading table (with uncertainties) + a one-line summary of the thresholds and the Q1–Q4 rules>"},
 ... ≤4 per round
])
```
The orchestrator **only records** the returned results and never re-reads that figure.

### Stage 2 · Table ledger (parallel subagents)
One subtask per table, with `context` = the full `TableN.txt` + the sentences in the text that cite that table. Rule: **`TableN.txt` is authoritative for table-text conflicts** (markdown tables are OCR-prone); compare table values against the citing sentences, and table-row conditions against the conditions described in the text; output format as above (`evidence_source="text-vs-table"`).

### Stage 3 · Paragraph reconciliation (done by the orchestrator, global view)
Cross-compare the assertion table from Stage 0: if the same entity + indicator + condition appears in ≥2 places with different values → candidate (including the "conclusion refuted by its own data" check); **classify Q first, and grade the remainder A–E only after passing the threshold**.

### Stage 4 · Merge, adjudicate, output
1. Deduplicate the three tracks by `figure+field+text_quote` (the figure/table ledgers and the paragraph ledger may report the same spot);
2. Give every entry a final `adopted_value` and a confidence;
3. **Output = a Markdown table** (directly in the reply, no notes written), sorted by severity (**D → A, with `E` (other/ungradable) last**):

| # | Location | Indicator | Value A (source) | Value B (source) | Adopted value | Severity | Confidence |
|---|---|---|---|---|---|---|---|

After the table, three summary lines: **contradictions N (A× · B× · C× · D× · E×) | verification findings Q1× Q2× Q3× Q4× | unchecked figures K (list them)**.

**Verification gain (the scorecard for this run, written outside the contradiction table)**: with $N$ = records checked, $checked$ = records actually compared, $e=|Q1|+|Q2|$, $fixed$ = number corrected:

$$C=\frac{checked}{N}\qquad R=\frac{fixed}{e}\qquad \Delta=\frac{fixed}{N}$$

Coverage $C$, correction rate $R$, and **$\Delta$ = how much verification raised extraction accuracy**; the error rate goes from $e/N$ before verification to $(e-fixed)/N$ after.

4. Persist the ledger JSON with `write_file` to `outputs/<paper name>_ledger.json` only when the user asks for a record.

## Completion checklist

1. Every contradiction has a traceable `text_quote` (the exact sentence can be found in the archive); entries from pixels must also carry `panel + reading + uncertainty`;
2. Table-text conflicts are adjudicated against `TableN.txt`, not the markdown table;
3. The thresholds and the "do not report" list were actually enforced (the ledger must contain no entry below 1% relative difference or below 5% of the range);
4. **Caliber compliance**: `severity` ∈ **A/B/C/D/E** (any `unknown` is a violation); "figure-text/table-text/text-text" may never be used as a class, only as `evidence_source`; `cross_paper` must never be set arbitrarily in this single-paper skill;
5. Q classes are kept separate from contradictions: Q1/Q2/Q3/Q4 go only into the summary and the gain, never into the contradiction table;
6. Unchecked-figure and Q counts are of a plausible order (in papers with many schematics, unchecked figures should outnumber contradictions);
7. Subagent output is pure JSON (entries polluted by restating the text are discarded and re-dispatched).

## Pitfalls

- **Never treat Q as a contradiction**: Q1/Q2 are **our own mistakes**; writing them into the contradiction table inflates the paper's problems. Q3 is a caliber difference where neither side is wrong, and Q4 is agreement. They belong only in the summary/gain.
- **Never write `unknown`**: severity has exactly **five values A–E**, and `E` (other · unclassifiable) is the fallback for "cannot grade / outside A–D". Cross-paper exclusion (`cross_paper`) cannot be judged by this skill and needs corpus-wide visibility.
- **Never send a schematic to reconciliation**: a figure with nothing readable only produces false contradictions; when unsure, skip it into "unchecked figures".
- **Never skip Stage 0 and dispatch directly**: subagents without an excerpt pack each re-read the full text page by page — slow and over the limit.
- **Never expect a subagent to see an image**: it only has text. Pixel reading is the orchestrator's job, transcribed and passed down as text.
- **Never write a number from "what the figure should look like"**: if it cannot be read, mark "needs figure check". Inventing readings is the most serious failure mode of this skill.
- **Never lower JPEG quality to save tokens**: visual tokens depend only on pixel count, so lowering quality loses information for free; to save, crop smaller or crop panels.
- **Never forget to restart the backend**: changes under `backend/` take effect only after a restart.
- Subtask returns are truncated when too long → the goal must state "output JSON only, never restate the text".
- Single-paper mode: no cross-paper reconciliation (conditions are incomparable and false contradictions abound); when the user asks for cross-paper work, state clearly that this skill does not apply and recommend `fenton-multiagent-mining` (Step 5B cross-paper adjudication).
- `role="contradiction"` is what gives the subagent its adjudication discipline; forgetting it means free improvisation.

## Dependencies

Upstream archiving: `mineru-agent-parse` (requires `00_full_cleaned.md` + `00_images_map.json` + FigureN/TableN txt + the original images under `images/`).
Taxonomy source: `skills/fenton-multiagent-mining/references/contradiction_taxonomy.md` (the single source of truth for A–E + Q1–Q4; this skill stays consistent with it).
Pixel reading channel: **both engines are wired** (three additive changes in `file_tools.py` / `agent.py` / `subagent.py`: tools return image content blocks); usage and constants in `references/image_read_channel.md`; requires a **vision model**, and the backend must be restarted after code changes.
Subagent role preset: `delegate_task` with `role=contradiction`. Back up any file before changing project code (`xxx.py.bak_<topic>`), and restart the backend afterwards for changes to take effect.
