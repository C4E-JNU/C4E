# -*- coding: utf-8 -*-
"""step2_split.py — Step 4, subfigure splitting (a plain script, no LLM)
Reads datamining/01_work_order.json and splits figures that are multi-panel and contain at least one subfigure with needs_xy_extraction=true
(decided at subfigure level; a single-panel xy figure is not split and is extracted directly),
writing datamining/split/<figure id>/ and datamining/02_image_match.json (with a split-count check).
Figures that fail to split (count mismatch / original image missing) are listed in the summary → re-run this step after agent-side cropping or PDF re-extraction.
Usage: python step2_split.py <corpus root directory>
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from image_splitter import split_figure  # noqa: E402


def wf_dir(paper_dir: Path) -> Path:
    z = paper_dir / "datamining"
    return z if z.is_dir() else paper_dir


def find_image(wf: Path, figure_id: str):
    img_dir = wf / "images"
    if not img_dir.is_dir():
        return None
    m = re.search(r"(?:Fig(?:ure)?\.?)\s*(\d+)", str(figure_id), re.IGNORECASE)
    norm = f"Figure{m.group(1)}" if m else str(figure_id).strip()
    for cand in {str(figure_id).strip(), norm}:
        d = img_dir / cand
        if d.is_dir():
            full = d / (cand + "_full.jpg")   # new archiving: prefer the whole figure stitched vertically from several subfigures
            if full.exists():
                return full
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                files = [f for f in d.glob(ext) if "_full" not in f.name]
                if files:
                    return files[0]
    return None


def read_panel_list(wf: Path, figure_id: str):
    """Read the ordered panel list '[-] <file> page=N' from images/FigureN/FigureN.txt.

    【fixed 2026-10-05】During archiving MinerU has already rendered each panel of a multi-panel figure as its own image,
    so FigureN.txt is the authoritative and ordered panel list (order = caption a/b/c) and no white-line cutting is needed.
    The old version unconditionally called split_figure to cut a "single-panel image",
    which always produced 0 pieces → a false "split failed" (measured on Fig.5/6/10 of B202 wen-et-al-2021).
    """
    img_dir = wf / "images"
    if not img_dir.is_dir():
        return []
    m = re.search(r"(?:Fig(?:ure)?\.?)\s*(\d+)", str(figure_id), re.IGNORECASE)
    if not m:
        return []
    d = img_dir / f"Figure{m.group(1)}"
    if not d.is_dir():
        return []
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".txt"):
            continue
        try:
            txt = (d / fn).read_text(encoding="utf-8")
        except Exception:
            continue
        if not txt.strip():
            continue
        # 【fixed 2026-10-05】tolerate two panel-line formats: the new step0c archive uses "  [1] <file> page=N bbox=…"
        # and the old MinerU archive uses "  [-] <file>". The old version accepted only [-] and required the literal word "subfigure" in the line,
        # so an [n] format always read as empty → the whole figure degraded to a single pack and no panel matched (measured: Fig.7 read 4 blocks as 0).
        names = re.findall(r"^\s*\[[-\d]+\]\s+(\S+\.(?:jpg|jpeg|png))", txt, re.M)
        out = [d / n for n in names if (d / n).exists()]
        if out:
            return out
    return []


def xy_subs_of(task):
    """The list of XY subfigures of this figure (subfigure-level decision; the single source of truth = subfigures[].needs_xy_extraction).

    Tolerates old work orders: when no subfigure carries the field, fall back to the figure-level boolean (old schema figure-level true = all subfigures are XY),
    which applies only when no subfigure-level information exists at all; new work orders never reach this branch.
    """
    subs = task.get("subfigures", [])
    if any("needs_xy_extraction" in s for s in subs):
        return [s for s in subs if s.get("needs_xy_extraction")]
    return list(subs) if task.get("needs_xy_extraction") else []


def split_paper(root: Path, paper: str, log=print):
    wf = wf_dir(root / paper)
    wo_path = wf / "01_work_order.json"
    if not wo_path.exists():
        return None
    data = json.loads(wo_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None  # skip legacy list-format work orders (e.g. leftovers from C4EAI)
    out_root = wf / "02_split"
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(exist_ok=True)
    results = []
    for task in data.get("figures", []):
        subs = task.get("subfigures", [])
        # 【changed 2026-10-05】the XY decision moved down to subfigure level: subfigures[].needs_xy_extraction is authoritative.
        # A multi-panel figure must be split if even one subfigure is XY (one figure may mix xy and basic panels).
        xy_subs = xy_subs_of(task)
        if not (xy_subs and len(subs) > 1):
            continue  # a single-panel xy figure is extracted directly and needs no split
        fid = task["figure_id"]
        expected = task.get("total_subfigures") or len(subs)
        # The archive is already split into panels (ordered FigureN.txt list) → accept it directly, no white-line cutting.
        # Criterion: panel count > 1 and ≥ the number of subfigures included by the work order (extra panels are excluded subfigures and do not affect picking by letter).
        panels = read_panel_list(wf, fid)
        if len(panels) > 1 and len(panels) >= len(subs):
            expected = len(panels)
            out_dir = out_root / fid
            out_dir.mkdir(exist_ok=True)
            saved = []
            for i, p in enumerate(panels):
                dst = out_dir / f"{fid}{chr(ord('A') + i)}.jpg"
                shutil.copyfile(p, dst)
                saved.append(dst.name)
            results.append({"figure_id": fid, "img_path": "", "split_dir": str(out_dir),
                            "subfigures": saved, "expected_total": expected,
                            "split_count": len(saved), "split_success": True,
                            "source": "archive_panel",
                            "xy_subfigure_ids": [s["subfigure_id"] for s in xy_subs]})
            log(f"OK {paper[:30]} {fid}: archive already split {len(saved)}/{expected} (accepted directly)")
            continue
        img = find_image(wf, fid)
        if img is None:
            results.append({"figure_id": fid, "error": "original image not found",
                            "expected_total": expected, "split_success": False})
            log(f"FAIL {paper[:30]} {fid}: original image not found (switch to PDF re-extraction)")
            continue
        out_dir = out_root / fid
        saved = split_figure(str(img), str(out_dir), fid)
        ok = len(saved) == expected
        results.append({"figure_id": fid, "img_path": str(img), "split_dir": str(out_dir),
                        "subfigures": saved, "expected_total": expected,
                        "split_count": len(saved), "split_success": ok,
                        "xy_subfigure_ids": [s["subfigure_id"] for s in xy_subs]})
        log(f"{'✓' if ok else '✗'} {paper[:30]} {fid}: {len(saved)}/{expected}")
    (wf / "02_image_match.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


def main(root):
    root = Path(root)
    ok = bad = 0
    for paper in sorted(os.listdir(root)):
        if not (root / paper).is_dir():
            continue
        r = split_paper(root, paper, log=print) or []
        for it in r:
            ok += 1 if it.get("split_success") else 0
            bad += 0 if it.get("split_success") else 1
    print(f"=== script split: {ok} succeeded, {bad} pending (rerun after agent-side cropping / PDF re-extraction) ===")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test10b")
