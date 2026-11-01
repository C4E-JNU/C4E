# -*- coding: utf-8 -*-
"""step4_assemble_verified.py — end of Step 5: assemble 03 into 04 (a full copy) using the subagent's annotation table

Why this exists: `04_verified_image_data.json` is a **full copy** of `03_image_data.json` (including every
`curves[].points[]`), far larger than the single-return limit — a subagent that pastes it back is always truncated, so it returns only the annotation table.
This script merges the annotations back into 03 by `(figure, subfigure)`, keeps every other field as-is, and **leaves the curve points untouched, point for point**.

Inputs:
  <paper folder or corpus root>/datamining/03_image_data.json      figure ledger (list)
  <paper folder or corpus root>/datamining/04_annotation_table.json annotation table (list, persisted by the orchestrator after the subagent returns)
Output:
  <paper folder or corpus root>/datamining/04_verified_image_data.json

Usage:
  python step4_assemble_verified.py <paper folder or corpus root>
Note: a directory containing `datamining/` is treated as a single paper; otherwise its subdirectories are processed one by one.
"""
import json
import os
import re
import sys


def papers_of(root):
    """Auto-adapt: one paper (root itself is a paper folder) or several (paper folders under root)."""
    root = os.path.abspath(root)
    if os.path.isdir(os.path.join(root, "datamining")) or os.path.exists(
            os.path.join(root, "00_full_cleaned.md")):
        return [root]
    out = []
    for p in sorted(os.listdir(root)):
        d = os.path.join(root, p)
        if not os.path.isdir(d) or p.startswith("_"):
            continue
        if os.path.isdir(os.path.join(d, "datamining")) or os.path.exists(
                os.path.join(d, "00_full_cleaned.md")):
            out.append(d)
    return out


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def key_of(fig, sub):
    """Normalize the match key: 'Fig.7'/'Figure7' → 'fig7', subfigure lowercased with spaces removed."""
    f = re.sub(r"[^0-9a-z]", "", str(fig or "").lower())
    f = f.replace("figure", "fig")
    s = re.sub(r"\s+", "", str(sub or "").lower())
    return f, s


def load(fp, default=None):
    if not os.path.exists(fp):
        return default
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception as e:
        print(f"  ! read failed {os.path.basename(fp)}: {e}")
        return default


def assemble(paper):
    wf = wf_of(paper)
    src = load(os.path.join(wf, "03_image_data.json"))
    if src is None:
        print("  ! missing 03_image_data.json, skipped")
        return None
    if isinstance(src, dict):                       # tolerate the {"records": [...]} form
        src = src.get("records") or src.get("items") or []
    ann_fp = os.path.join(wf, "04_annotation_table.json")
    ann = load(ann_fp, []) or []
    if isinstance(ann, dict):
        ann = ann.get("records") or ann.get("items") or ann.get("annotations") or []

    table = {}
    for a in ann:
        if not isinstance(a, dict):
            continue
        k = key_of(a.get("figure"), a.get("subfigure"))
        if not k[0]:
            continue
        table[k] = a

    out, used, missing = [], set(), []
    for rec in src:
        rec = json.loads(json.dumps(rec, ensure_ascii=False))   # deep copy; never mutate the original object
        k = key_of(rec.get("figure"), rec.get("subfigure"))
        a = table.get(k)
        if a is None:
            missing.append(f"{rec.get('figure')}/{rec.get('subfigure')}")
            rec["value_source"] = rec.get("value_source") or ""
            rec["checked"] = False
            rec["discrepancy"] = None
            rec["annotation"] = "missing"
        else:
            used.add(k)
            rec["value_source"] = a.get("value_source") or ""
            rec["checked"] = bool(a.get("checked", True))
            rec["discrepancy"] = a.get("discrepancy")
            rec["annotation"] = "matched"
        out.append(rec)

    extra = [f"{k[0]}/{k[1]}" for k in table if k not in used]
    out_fp = os.path.join(wf, "04_verified_image_data.json")
    json.dump(out, open(out_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # Curve point integrity: 04 must match 03 point for point (the hard requirement of a "full copy")
    def pts(records):
        return sum(len(c.get("points") or []) for r in records for c in (r.get("curves") or []))
    p3, p4 = pts(src), pts(out)
    flag = "OK" if p3 == p4 else "!! POINTS LOST"
    print(f"  04 {len(out)}/{len(src)} records  curve points {p4}/{p3} {flag}"
          + (f"  unannotated {len(missing)}: {','.join(missing[:5])}" if missing else "")
          + (f"  extra annotations {len(extra)}: {','.join(extra[:5])}" if extra else ""))
    return {"records": len(out), "points": p4, "missing": missing, "extra": extra,
            "points_ok": p3 == p4}


def main(root):
    ps = papers_of(root)
    if not ps:
        print(f"No paper folder found: {root}\n(a datamining/ directory or 00_full_cleaned.md is required)")
        return 1
    print(f"{len(ps)} papers ← {root}")
    bad = 0
    for p in ps:
        print(f"- {os.path.basename(p)[:46]}")
        r = assemble(p)
        if r is None or not r["points_ok"]:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
