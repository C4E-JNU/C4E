# -*- coding: utf-8 -*-
"""step3_merge_results.py — end of Step 4 (enhanced):
1. Merge each task pack's 03_result.json → 03_image_data.json
2. Normalize structure: dict→list, backfill figure/mode from task.json, strip None
3. Pack-count check: ready packs in 02b_extraction_tasks vs actual result count; list the missing ones for re-dispatch
4. Collect the archive-fix list
Usage: python step3_merge_results.py <corpus root directory>
"""
import glob
import json
import os
import sys


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def normalize(records, task):
    """Normalize structure: ensure every record has figure/subfigure/mode with the right types"""
    if isinstance(records, dict):
        # a records list wrapped in a dict
        records = records.get("records", [records])
    if not isinstance(records, list):
        records = [records]
    fig_id = task.get("figure_id", "")
    mode = task.get("mode", "")
    for r in records:
        if not isinstance(r, dict):
            continue
        # backfill figure (when missing or None)
        if not r.get("figure"):
            r["figure"] = fig_id
        # backfill subfigure (use the first subfigure id when missing)
        if not r.get("subfigure"):
            sids = task.get("subfigure_ids", ["main"])
            r["subfigure"] = sids[0] if sids else "main"
        # fix mode
        if not r.get("mode"):
            r["mode"] = mode
        # tolerate series→curves (the extractor occasionally carries curves under the series key)
        if not r.get("curves") and isinstance(r.get("series"), list):
            r["curves"] = [
                {"label": c.get("label") or c.get("name", ""), "points": c.get("points", []),
                 **{k: v for k, v in c.items() if k not in ("label", "name", "points")}}
                for c in r["series"] if isinstance(c, dict)]
        # strip None values
        for k in list(r.keys()):
            if r[k] is None and k not in ("degradation_profiles",):
                del r[k]
    return records


def merge(paper):
    wf = wf_of(paper)
    idx_path = os.path.join(wf, "02b_extraction_tasks.json")
    if not os.path.exists(idx_path):
        return 0

    idx = json.load(open(idx_path, encoding="utf-8"))
    ready_pkgs = [t for t in idx.get("tasks", []) if t.get("status") == "ready"]

    # 1) collect existing results and normalize them
    records, errors, got_tasks = [], [], set()
    for cat_dir in sorted(glob.glob(os.path.join(wf, "02b_tasks", "*"))):
        if not os.path.isdir(cat_dir):
            continue
        for rj in sorted(glob.glob(os.path.join(cat_dir, "T*", "0*result.json"))):
            tid = os.path.basename(os.path.dirname(rj))
            got_tasks.add(tid)
            try:
                data = json.load(open(rj, encoding="utf-8"))
                task = next((t for t in ready_pkgs if t["task_id"] == tid), {})
                recs = normalize(data, task)
                records.extend(recs)
            except Exception as e:
                errors.append(f"{os.path.relpath(rj, wf)}: {str(e)[:60]}")

    # 2) check: pack count vs result count; list the missing ones
    missing = [t["task_id"] for t in ready_pkgs if t["task_id"] not in got_tasks]

    json.dump(records, open(os.path.join(wf, "03_image_data.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    n_xy = sum(1 for r in records if r.get("mode") == "xy")
    npts = sum(len(c.get("points", [])) for r in records if r.get("mode") == "xy"
               for c in r.get("curves", []))
    name = os.path.basename(os.path.normpath(paper))
    print(f"{name[:44]:46s} {len(records):3d} records (xy {n_xy:2d} + basic {len(records)-n_xy:2d})"
          f" points {npts:4d} bad packs {len(errors)} missing results {len(missing)}")
    for e in errors:
        print("  ", e)
    if missing:
        print(f"  ! missing-result packs: {missing} (re-dispatch required)")
    return len(records)


def collect_archive_fixes(root, fixes):
    for rj in glob.glob(os.path.join(root, "*", "datamining", "02b_tasks", "*", "T*", "0*result.json"),
                        recursive=True):
        try:
            d = json.load(open(rj, encoding="utf-8"))
        except Exception:
            continue
        for r in (d if isinstance(d, list) else [d]):
            if isinstance(r, dict) and r.get("archive_fix"):
                fixes.append({"paper": os.path.basename(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.dirname(rj))))),
                    "task": os.path.basename(os.path.dirname(rj)),
                    "figure": r.get("figure"), "subfigure": r.get("subfigure"),
                    **r["archive_fix"]})


def main(root):
    total = 0
    fixes = []
    for p in sorted(os.listdir(root)):
        paper = os.path.join(root, p)
        if not os.path.isdir(paper):
            continue
        wf = wf_of(paper)
        if os.path.isdir(os.path.join(wf, "02b_tasks")):
            total += merge(paper)
    collect_archive_fixes(root, fixes)
    if fixes:
        # 2026-10-05 fix: write into <multi-paper directory>/_ledgers/, no longer into the skill directory
        _ld = os.path.join(os.path.normpath(root), "_ledgers")
        os.makedirs(_ld, exist_ok=True)
        fp = os.path.join(_ld, f"{os.path.basename(os.path.normpath(root))}_archive_fix_list.json")
        json.dump(fixes, open(fp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"=== archive-fix list: {len(fixes)} entries ===")
    print(f"=== total: {total} records ===")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test10b")
