---
name: mineru-agent-parse
description: "Parse and archive PDFs into a 'full text + figures/tables filed by caption number' layout, producing 00_full_cleaned.md and the 00_images_map.json manifest for cross-modal verification and data mining. Requires a MinerU token."
version: 4.2.0
tags: [mineru, pdf, parse, archive, literature]
---

# MinerU Parsing + Archiving (PDF → full text + figures filed by caption number + manifest)

Turn one or many PDFs into standard intermediate products reusable downstream: **cleaned full text `00_full_cleaned.md` + images filed by caption number into `images/FigureN/` + mapping manifest `00_images_map.json`**. This is the data source for later skills such as literature translation and figure-text mining; archive first.

## When to use

- The user uploads a PDF, gives a PDF path or a folder containing PDFs, and wants the content parsed or extracted;
- A paper must be turned into a tidy "text + archived figures and tables" directory for translation, cross-modal verification or data mining;
- If only the Markdown body is needed, run step 1 alone; for image filing and the manifest, run 1→2→3.

## Prerequisites

1. **MinerU token configured**: Agent Configuration panel → MinerU parsing configuration, enter the token and save (it expires).
2. The archiving script uses the Python standard library; run it with the **terminal** tool (needs "ask/full access" permission to read and write the workspace). Pure computation can also be inlined in `run_code`, but `terminal` is more stable.
3. Output goes to `workspace/mineru_output/` (images are statically mounted and reachable at `/workspace/...` URLs).

**🔒 File-reading whitelist for this skill (iron rule)**: all archiving is done by scripts, and **you never need to read any JSON**. The only permitted reads are:
- the JSON printed by the script to stdout (for verification);
- `00_images_map.json` (spot-checking the figure/table list, about 20 KB, safe);
- `images/FigureN/FigureN.txt` (spot-checking one figure's caption).
**❌ Never read**: `layout.json`, `*_model.json`, `*_content_list_v2.json`, `*_content_list.json` — they are multi-MB coordinate/model dumps or internal script indexes, and reading them once blew the context up to 8.9 million tokens and triggered a 400 error. Which content_list is used is the script's business, not yours.

## Three-step flow

### Step 1: Parse (mineru_parse)

```
mineru_parse(path="workspace/uploads/xxx/some_paper.pdf", out_dir="mineru_output")
# or in bulk: pass a directory containing several PDFs → recursively scan *.pdf, ≤50 per batch, one same-named .zip each
```
Product: `workspace/mineru_output/<paper name>.zip`. The zip is **flat**: `full.md` / `images/<hash>.jpg` / `<uuid>_content_list.json` / `layout.json` / `..._origin.pdf`.

The `zip` path in the returned `downloaded` list is the input to the next step.

### Step 2: Extract + file images + clean + manifest (bundled script)

Run with **terminal** at the project root (script: `skills/mineru-agent-parse/scripts/mineru_archive.py`):

```bash
# a single zip
python skills/mineru-agent-parse/scripts/mineru_archive.py "workspace/mineru_output/<paper name>.zip"
# a directory of zips → bulk
python skills/mineru-agent-parse/scripts/mineru_archive.py "workspace/mineru_output"
# or specify the output root
python skills/mineru-agent-parse/scripts/mineru_archive.py "<zip>" "workspace/mineru_output"
```
The script will: (1) extract safely (no path traversal) into `<out>/<name>/`; (2) read the correct `*_content_list.json` (automatically excluding `_v2`/`_model`/`layout`); (3) **group subfigures by the caption block in full.md**: the consecutive `images/<hash>` entries before a `Fig./Table N` caption are treated as subfigures of the same figure and copied together into `images/FigureN/` (one multi-panel figure → several jpgs in one folder), and writes `FigureN.txt` (**full English caption** + each subfigure's a/b/c letter and page number); tables are archived separately by `Table N`; content_list is used only to fill in letters, page numbers and table bodies; (4) write `00_full_cleaned.md` from the body up to References; (5) write the manifest `00_images_map.json` (including the `figures[]` logical grouping, see below). Fully idempotent (reruns overwrite); the source zip/paper directory is untouched.

> Why not extract numbers from single captions: MinerU often splits one multi-panel figure into several image entries whose caption carries only a single letter "a/b/C" or nothing at all — relying on that alone scatters Fig.2's a–h into Figure2…Figure9. Sequential clustering on full.md is used instead, **guaranteeing that all subfigures of one figure number land in the same `FigureN/`**.

### Step 3: Verify (**run the checker first; do not rely on your eyes**)

Run with **terminal** at the project root (script: `skills/mineru-agent-parse/scripts/verify_note.py`, standard library only):

```bash
# verify one archive directory (recommended; 00_images_map.json or a Markdown note also accepted)
python skills/mineru-agent-parse/scripts/verify_note.py "workspace/mineru_output/<paper name>"
# machine-readable:
python skills/mineru-agent-parse/scripts/verify_note.py "workspace/mineru_output/<paper name>" --json
```

The script checks item by item and reports **line numbers / file names**: (1) broken images (whether the images referenced by the manifest and the Markdown exist); (2) caption/panel counts (`panel_count` vs. `panels[]` vs. the `<img>`/`<div>` in `layout_html` vs. the actual images in `images/FigureN/` vs. the panel count recorded in `FigureN.txt`); (3) truncation of the text (unclosed `$$`/code fences/`div`, an unfinished table on the last line, the length ratio of `00_full_cleaned.md` to `full.md`); (4) duplicate image references and skipped or duplicated caption numbers.
**Exit code: 0 = pass (possibly with warnings); 1 = errors present; 2 = usage/file-read failure.** Any error must be fixed before continuing (rerunning the archiving script repairs it idempotently).

Once the script passes, inspect by hand the items **the script cannot judge** (script stdout JSON plus a manifest sample):
- `ok:true`; `figure/table/other` in `stats` are plausible and `unknown` is small (if almost everything is `unknown` → the zip's content_list was taken wrongly or the image names do not line up; inspect the JSON);
- `cleaned.ok:true` and `chars>0`;
- `workspace/mineru_output/<name>/` contains `images/FigureN/…`, `00_full_cleaned.md` and `00_images_map.json`;
- every warning from the script (e.g. "caption letter count differs from panel count", "subfigure missing its sub label") needs a human decision: is it a MinerU splitting problem, or does the caption need to be completed?

## Product structure

```
workspace/mineru_output/<paper name>/
  00_full_cleaned.md      # cleaned full text (up to References) — the text source for translation/mining
  00_images_map.json      # figure-text mapping manifest (figures[] logical grouping + per-subfigure, see below)
  full.md                 # MinerU's original full text (with inline ![](images/<hash>.jpg))
  <uuid>_content_list.json
  images/
    <hash>.jpg            # original loose images (kept, untouched)
    Figure2/              # ★ a multi-panel figure: all its subfigure jpgs live here
      <hashA>.jpg ...     #   one jpg per subfigure
      Figure2.txt         #   full English caption + each subfigure's a/b/c letter and page number
    Figure4/<hash>.jpg + Figure4.txt   # a single-panel figure (1 image)
    TableN/<hash>.jpg + TableN.txt     # a table (including the table body converted to text)
    unknown_NN/                          # loose images not claimed by any Fig./Table caption
```

## Manifest `00_images_map.json` fields (the translation skill places images with it, including several images per row)

```json
{ "paper":"…", "out_dir":"…", "content_list":"…_content_list.json",
  "counts":{"figures":6,"tables":1,"unknown":4,"image_files":40,
            "multi_panel_figures":["Figure1","Figure2","Figure3","Figure5","Figure6"]},
  "figures":[
    {"folder":"Figure2","kind":"Figure","token":"2",
     "caption_en":"Fig. 2. Characteristics and validation of three water-related datasets. a-c …",
     "panel_count":8,
     "layout":{"cols":3,"width_px":290},
     "layout_html":"<div style=\"display:flex;flex-wrap:wrap;…\"><img src=\"/workspace/…/Figure2/<h1>.jpg\" alt=\"Fig.2a\" style=\"width:290px;…\" /> ×8</div>",
     "panels":[{"file":"<hash>.jpg","sub":"a","web_url":"/workspace/…/images/Figure2/<hash>.jpg","page":4}]}
  ],
  "images":[
    {"orig":"images/<hash>.jpg","filename":"<hash>.jpg","folder":"Figure2","group":"Figure2",
     "sub":"a","type":"image","figure":"2","caption_en":"Fig. 2. …","page":4,
     "web_url":"/workspace/mineru_output/<paper name>/images/Figure2/<hash>.jpg"}
  ]
}
```
- `figures[]`: **grouped by logical figure** — a multi-panel figure is **one** entry, and `panels` lists all its subfigures (with letter `sub`, `web_url`, `page`). The translation skill uses this to place several subfigures on **one row** sharing a single translated caption.
- `layout` / `layout_html`: **a grid pre-arranged by the script according to the number of panels** (1→1 column 520 px; 2→2 columns 380; 3→3 columns 290; 4→2×2; 5–9→3 columns 290; ≥10→4 columns 215); `layout_html` is flex HTML you can copy verbatim (for note figures use the whole block and only change `alt`).
- `caption_en`: the figure's **full English caption** (including the a/b/c… items), used as the caption after translation.
- `web_url`: an absolute path usable directly in note/body `![](…)` (rendered via static mounting).

## Pitfalls

- `mineru_parse` **only downloads the zip and does not extract it**; without extraction there is no `full.md/images/`, so step 2 is mandatory.
- There are two content_list files (`_content_list.json` and `_v2.json`) plus `_model.json`: the script locks onto the standard `_content_list.json`; if you read JSON by hand, never use `_v2`/`_model`.
- **Subfigure grouping relies on caption blocks in full.md**: if a paper writes captions unusually (e.g. `Fig. 2:` with a colon, or the caption before the subfigures), that figure may fail to group and land in `unknown_`. Watch the `unknown` count during verification and check by hand when needed. Some figures with empty captions are still filed into the correct `FigureN` (thanks to full.md order), but the `sub` label is missing and the caption must be completed from the text.
- Token expired → step 1 reports an authentication error; ask the user to re-enter it in the configuration panel.
- **Never `read_file` a huge JSON in full**: `layout.json`, `*_model.json` and `*_content_list_v2.json` in an archive directory are often "single-line multi-MB" coordinate/model dumps that instantly blow up the model context (one incident produced a 400 error at 8.9 million tokens). For the body read only `00_full_cleaned.md`; for figure/table information read `00_images_map.json` (small). The tool now truncates at 20k characters as a guard rail, but you should still avoid `list_files` followed by reading each of these JSON files.
- Large PDFs / deep Chinese paths: the script already handles Windows long paths (`\\?\`), so no manual change is needed.

## Related

After archiving: first run `skills/mineru-agent-parse/scripts/verify_note.py "<archive directory>"` (broken images / caption-panel counts / text truncation / numbering gaps; a non-zero exit code means errors); for cross-modal verification, entity mining or Fenton data mining → use this manifest and `00_full_cleaned.md` as inputs (the Fenton flow is the `fenton-multiagent-mining` skill).
