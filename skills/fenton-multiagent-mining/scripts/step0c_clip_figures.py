# -*- coding: utf-8 -*-
"""step0c_clip_figures.py — crop figure blocks from the origin PDF using content_list bboxes, and archive them by caption number.

Why this exists: MinerU's loose images under images/ are a "figure content stream", so multi-panel figures get chopped up, lose panels or fall out of order;
the bboxes in content_list are "where the figure block sits on the page" — cropping from the PDF by bbox yields complete panels
and does not depend on the loose image files being complete, so neither the orchestrator nor a subagent has to "look at an image and guess which figure it is".

Join key = image file name (hash): content_list gives hash→(page_idx,bbox), full.md gives hash→figure number.

Input: a paper folder / a multi-paper directory / a bare PDF (must already have *_content_list.json + *_origin.pdf)
Output: <paper folder>/datamining/images/FigureN/<hash>.jpg (the same-named directory is rebuilt entirely)
      + FigureN_full.jpg (stitched in the original layout when there are several blocks)
      + FigureN.txt (English caption + page number/bbox of each block)
      blocks not claimed by any caption → unknown_NN/
Usage: python step0c_clip_figures.py <paper folder|multi-paper directory|pdf>
"""
import io
import json
import os
import re
import shutil
import sys

import fitz  # PyMuPDF
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_preprocessor import _lp, _parse_md_groups  # noqa: E402

DPI = 200
PAD = 2.0
GAP = 16


def _content_list(paper):
    cands = sorted(f for f in os.listdir(paper)
                   if f.endswith("_content_list.json") and "_v2" not in f)
    return os.path.join(paper, cands[0]) if cands else None


def _origin_pdf(paper):
    cands = [f for f in sorted(os.listdir(paper)) if f.endswith("_origin.pdf")]
    if not cands:
        cands = [f for f in sorted(os.listdir(paper)) if f.lower().endswith(".pdf")]
    return os.path.join(paper, cands[0]) if cands else None


def load_blocks(cl_path):
    """{hash file name: (page_idx, [x0,y0,x1,y1])}, taken from type=chart/image/table entries"""
    out, tables = {}, set()
    data = json.load(open(_lp(cl_path), encoding="utf-8"))
    if not isinstance(data, list):
        return out, tables
    for it in data:
        if not isinstance(it, dict):
            continue
        ip, bbox = it.get("img_path"), it.get("bbox")
        if not ip or not bbox or it.get("type") not in ("chart", "image", "table"):
            continue
        name = os.path.basename(str(ip))
        if isinstance(bbox, str):
            try:
                bbox = json.loads(bbox)
            except Exception:
                continue
        try:
            pg = int(it.get("page_idx"))
            box = [float(v) for v in bbox]
        except (TypeError, ValueError):
            continue
        out[name] = (pg, box)
        if it.get("type") == "table":
            tables.add(name)
    return out, tables


def resolve_scale(doc, blocks):
    """Whether bbox uses page point units or 1000×1000 normalized coordinates → return (sx, sy)"""
    ok = tot = 0
    for pg, bbox in blocks.values():
        if pg is None or not (0 <= pg < len(doc)):
            continue
        tot += 1
        w, h = doc[pg].rect.width, doc[pg].rect.height
        if bbox[2] <= w * 1.05 and bbox[3] <= h * 1.05:
            ok += 1
    return (1.0, 1.0) if tot and ok / tot > 0.9 else (1000.0, 1000.0)


def _clip(doc, pg, bbox, scale):
    """Crop one block → (PIL.Image, scaled x0, y0); None on failure"""
    if pg is None or not (0 <= pg < len(doc)):
        return None
    page = doc[pg]
    W, H = page.rect.width, page.rect.height
    sx, sy = scale
    x0, y0, x1, y1 = bbox
    if sx != 1.0:
        x0, x1 = x0 / sx * W, x1 / sx * W
        y0, y1 = y0 / sy * H, y1 / sy * H
    rect = fitz.Rect(max(0, x0 - PAD), max(0, y0 - PAD),
                     min(W, x1 + PAD), min(H, y1 + PAD))
    if rect.is_empty or rect.width < 5 or rect.height < 5:
        return None
    try:
        pix = page.get_pixmap(clip=rect, dpi=DPI)
    except Exception:
        return None
    return Image.open(io.BytesIO(pix.tobytes("png"))), x0, y0


def _merge_layout(items, out_path):
    """items=[(PIL.Image, x0, y0)] → stitch in the original layout (horizontal within a row, vertical across rows)"""
    if len(items) < 2:
        return None
    med_h = sorted(im.height for im, _, _ in items)[len(items) // 2]
    rows, cur = [], []
    for im, x0, y0 in sorted(items, key=lambda t: (t[2], t[1])):
        if cur and abs(y0 - cur[0][2]) > 0.6 * med_h:
            rows.append(cur)
            cur = []
        cur.append((im, x0, y0))
    if cur:
        rows.append(cur)
    row_imgs = []
    for row in rows:
        cells = [t[0] for t in sorted(row, key=lambda t: t[1])]
        h = max(im.height for im in cells)
        scaled = [im.resize((max(1, round(im.width * h / im.height)), h)) for im in cells]
        w = sum(im.width for im in scaled) + GAP * (len(scaled) - 1)
        canvas = Image.new("RGB", (w, h), "white")
        x = 0
        for im in scaled:
            canvas.paste(im, (x, 0))
            x += im.width + GAP
        row_imgs.append(canvas)
    W = max(im.width for im in row_imgs)
    H = sum(im.height for im in row_imgs) + GAP * (len(row_imgs) - 1)
    out = Image.new("RGB", (W, H), "white")
    y = 0
    for im in row_imgs:
        out.paste(im, (0, y))
        y += im.height + GAP
    try:
        out.save(_lp(str(out_path)))
        return out_path
    except Exception:
        return None


def _write_folder(target, name, entries, caption, kind):
    """entries=[(hash, page, bbox, PIL.Image)]; build FigureN/ and write the images + txt"""
    folder = os.path.join(str(target), name)
    if os.path.isdir(_lp(folder)):
        shutil.rmtree(_lp(folder))
    os.makedirs(_lp(folder), exist_ok=True)
    lines = [caption or name, "", f"{kind} {name}: {len(entries)} blocks", "-" * 40]
    crops = []
    for i, (fname, page, bbox, im) in enumerate(entries, 1):
        dst = os.path.join(folder, fname)
        try:
            im.save(_lp(dst))
        except Exception:
            continue
        bb = ",".join(str(int(v)) for v in bbox)
        lines.append(f"  [{i}] {fname}  page={page}  bbox=({bb})")
        crops.append((im, bbox[0], bbox[1]))
    if len(crops) > 1 and _merge_layout(crops, os.path.join(folder, f"{name}_full.jpg")):
        lines.append(f"  [merged] {name}_full.jpg")
    with open(_lp(os.path.join(folder, f"{name}.txt")), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(entries)


def clip_paper(paper):
    cl_path, pdf_path = _content_list(paper), _origin_pdf(paper)
    if not cl_path or not pdf_path:
        print(f"{os.path.basename(os.path.normpath(paper))[:44]:46s} missing content_list or PDF, skipped")
        return None
    blocks, table_files = load_blocks(cl_path)
    if not blocks:
        print(f"{os.path.basename(os.path.normpath(paper))[:44]:46s} content_list has no figure block, skipped")
        return None
    doc = fitz.open(pdf_path)
    scale = resolve_scale(doc, blocks)

    md = None
    for cand in ("full.md", "full_cleaned.md", "00_full_cleaned.md"):
        p = os.path.join(paper, cand)
        if os.path.exists(p):
            md = p
            break
    groups, consumed = _parse_md_groups(md, table_files) if md else ([], set())

    target = os.path.join(paper, "datamining", "images")
    os.makedirs(_lp(target), exist_ok=True)

    n_fig = n_unk = 0
    used = set()
    for g in groups:
        kind = "Table" if g["kind"] == "Table" else "Figure"
        name = f"{kind}{g['token']}"
        if name in used:
            continue
        entries = []
        for f in g["files"]:
            if f in used or f not in blocks:
                continue
            pg, bbox = blocks[f]
            r = _clip(doc, pg, bbox, scale)
            if r:
                entries.append((f, pg, bbox, r[0]))
                used.add(f)
        if not entries:
            continue
        used.add(name)
        _write_folder(target, name, entries, g["caption"], kind)
        n_fig += 1

    for f, (pg, bbox) in blocks.items():
        if f in used or f in consumed or f in table_files:
            continue
        r = _clip(doc, pg, bbox, scale)
        if not r:
            continue
        n_unk += 1
        _write_folder(target, f"unknown_{n_unk:02d}", [(f, pg, bbox, r[0])],
                      f"Original: {f}\nNote: not claimed by any caption (decorative or unrecognized block).", "unknown")

    name = os.path.basename(os.path.normpath(paper))
    print(f"{name[:44]:46s} PDF crop OK figures {n_fig} / blocks {len(used & set(blocks))} / unknown {n_unk}"
          f" (coordinate system {'points' if scale[0] == 1.0 else '1000-normalized'})")
    return {"figures": n_fig, "blocks": len(blocks), "unknown": n_unk}


def papers_in(root):
    if os.path.isfile(root):
        return [root]  # a single PDF: the caller first completes MinerU products; this is only a hint
    if os.path.isdir(os.path.join(root, "datamining")) or _content_list(root):
        return [root]
    return [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p)) and _content_list(os.path.join(root, p))]


def main(root):
    if os.path.isfile(root):
        print("This path is a file: run step0b_mineru_reparse.py first to produce the paper folder and MinerU products, then run this script")
        return
    papers = papers_in(root)
    if not papers:
        print("No paper folder containing *_content_list.json was found")
        return
    for p in papers:
        clip_paper(p)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python step0c_clip_figures.py <paper folder|multi-paper directory|pdf>")
        sys.exit(1)
    main(sys.argv[1])
