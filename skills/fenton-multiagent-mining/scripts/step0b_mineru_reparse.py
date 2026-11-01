# -*- coding: utf-8 -*-
"""step0b_mineru_reparse.py — Step 1 prerequisite completion: re-parse missing MinerU products

Scenario: when a paper folder lacks MinerU products (images/ loose figures, full.md, *_content_list.json),
automatically call the MinerU v4 batch interface to re-parse that paper's *_origin.pdf and complete the missing content in the paper folder.

Completion policy (fidelity first, never overwrite existing files):
  - full.md + *_content_list.json exist but images/ is missing:
      re-parse to obtain a new content_list, build an old hash→new hash mapping by (page_idx, in-page order),
      rename the new images to the old file names and copy them into images/ (so references in the old full.md stay valid).
      If the new parse hashes are identical to the old ones (parsing is highly deterministic), copy directly.
      When an old hash has no matching new image → print WARN (later steps fall back to whole-figure handling for it).
  - full.md or *_content_list.json missing: adopt the whole new parse (full.md/content_list/layout/images),
      leaving the existing _origin.pdf, ground_truth_tree.json and similar untouched.

Token sources (tried in order):
  1. environment variables MINERU_TOKEN / MINERU_API_KEY
  2. the C4EAI project configuration backend/workspace/mineru_config.json (common install locations are probed automatically)

Usage: python step0b_mineru_reparse.py <paper folder or multi-paper directory>
"""
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

import requests

BASE_URL = "https://mineru.net"
FILE_URLS_ENDPOINT = BASE_URL + "/api/v4/file-urls/batch"
RESULTS_ENDPOINT = BASE_URL + "/api/v4/extract-results/batch"
MAX_POLL = 90
POLL_INTERVAL = 10

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# This script lives at <project root>/skills/<skill name>/scripts/; three levels up is the project root
_PROJ_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))

_C4EAI_CANDIDATES = [
    os.path.join(_PROJ_ROOT, "workspace", "mineru_config.json"),
    os.path.join(_PROJ_ROOT, "backend", "workspace", "mineru_config.json"),
]


def load_token():
    for env in ("MINERU_TOKEN", "MINERU_API_KEY"):
        t = os.environ.get(env)
        if t:
            return t.strip()
    for p in _C4EAI_CANDIDATES:
        if os.path.exists(p):
            try:
                cfg = json.load(open(p, encoding="utf-8"))
                if cfg.get("token"):
                    return cfg["token"].strip()
            except Exception:
                pass
    return None


def _pdfs_in(d):
    """A PDF inside the directory: prefer *_origin.pdf, falling back to any .pdf (V7 fix: the original accepted only *_origin.pdf,
    whereas much of the corpus uses a bare origin.pdf, so everything was reported as "no paper folder found")"""
    files = sorted(os.listdir(d))
    exact = [f for f in files if f.endswith("_origin.pdf")]
    if exact:
        return exact
    return [f for f in files if f.lower().endswith(".pdf")]


def normalize_input(root):
    """When the user supplies a bare PDF: create a same-named sibling folder and 【copy】 the file into it (the original stays untouched); return that folder.
    Folders and multi-paper directories are returned unchanged."""
    if not (os.path.isfile(root) and root.lower().endswith(".pdf")):
        return root
    stem = os.path.splitext(os.path.basename(root))[0]
    if stem.lower().endswith("_origin"):
        stem = stem[:-7]
    d = os.path.join(os.path.dirname(os.path.abspath(root)), stem)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, os.path.basename(root))
    if not os.path.exists(dst):
        shutil.copy2(root, dst)
    print(f"Input is a PDF → working folder {d} (the original was not moved)")
    return d


def papers_in(root):
    """A single paper folder (containing a PDF) or a multi-paper directory (walk subfolders)"""
    def is_paper(d):
        return any(os.path.isfile(os.path.join(d, f)) for f in _pdfs_in(d))
    if is_paper(root):
        return [root]
    return [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p)) and is_paper(os.path.join(root, p))]


def check_missing(paper):
    """Return the missing-product list"""
    missing = []
    if not os.path.exists(os.path.join(paper, "full.md")):
        missing.append("full.md")
    cl = [f for f in os.listdir(paper) if f.endswith("_content_list.json")]
    if not cl:
        missing.append("content_list")
    imgs = os.path.join(paper, "images")
    if not (os.path.isdir(imgs) and any(os.scandir(imgs))):
        missing.append("images")
    return missing


def find_origin_pdf(paper):
    cands = _pdfs_in(paper)
    return os.path.join(paper, cands[0]) if cands else None


def mineru_parse_pdf(pdf_path, token, workdir):
    """Upload one PDF → poll → download the zip → extract into workdir; return the extraction directory or raise"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    name = os.path.basename(pdf_path)
    data = {"enable_formula": True, "language": "ch", "enable_table": True,
            "files": [{"name": name, "is_ocr": True, "data_id": name[:120]}]}
    res = requests.post(FILE_URLS_ENDPOINT, headers=headers, json=data, timeout=30)
    rj = res.json()
    if res.status_code != 200 or rj.get("code") != 0:
        raise RuntimeError(f"failed to request an upload URL: {rj}")
    batch_id = rj["data"]["batch_id"]
    url = rj["data"]["file_urls"][0]
    with open(pdf_path, "rb") as f:
        r = requests.put(url, data=f, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"upload failed PUT {r.status_code}")

    result_url = f"{RESULTS_ENDPOINT}/{batch_id}"
    fr = None
    for _ in range(MAX_POLL):
        time.sleep(POLL_INTERVAL)
        try:
            er = requests.get(result_url, headers={"Authorization": f"Bearer {token}"},
                              timeout=30).json().get("data", {}).get("extract_result") or []
            if er and er[0].get("state") in ("done", "failed"):
                fr = er[0]
                break
        except Exception:
            pass
    if fr is None:
        raise RuntimeError("polling timed out")
    if fr.get("state") != "done" or not fr.get("full_zip_url"):
        raise RuntimeError(f"parsing failed: {fr.get('err_msg')}")
    zpath = os.path.join(workdir, "result.zip")
    r = requests.get(fr["full_zip_url"], timeout=300)
    with open(zpath, "wb") as fz:
        fz.write(r.content)
    out = os.path.join(workdir, "unzipped")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out)
    return out


def load_image_blocks(cl_path):
    d = json.load(open(cl_path, encoding="utf-8"))
    return [x for x in d if x.get("type") == "image" and x.get("img_path")]


def resolve_bbox_scale(paper, cl_path):
    """Determine the coordinate system of content_list bboxes.
    Legacy MinerU content_list bboxes are 1000x1000 normalized coordinates (matching the layout.json image blocks exactly
    after converting with (x/1000*W, y/1000*H)); the new version uses PDF point coordinates.
    Decide automatically using the layout.json image-block bboxes as the reference, falling back to the coordinate range when no layout exists."""
    import fitz
    cl_path = os.path.join(paper, cl_path) if not os.path.isabs(cl_path) else cl_path
    blocks = load_image_blocks(cl_path)
    if not blocks:
        return 1.0, (1.0, 1.0)
    # layout page size
    page_size = {}
    lay = os.path.join(paper, "layout.json")
    if os.path.exists(lay):
        try:
            lj = json.load(open(lay, encoding="utf-8"))
            for pg in lj.get("pdf_info", []):
                page_size[pg.get("page_idx")] = pg.get("page_size") or None
        except Exception:
            pass
    pdf_path = find_origin_pdf(paper)
    if pdf_path:
        try:
            pdf = fitz.open(pdf_path)
            for i, pg in enumerate(pdf):
                page_size.setdefault(i, [pg.rect.width, pg.rect.height])
        except Exception:
            pass
    # hypothesis H1: bbox is already in page points → every block should fall inside the page
    def in_page(scale_x, scale_y):
        ok = 0
        for b in blocks:
            ps = page_size.get(b.get("page_idx"))
            if not ps:
                continue
            x0, y0, x1, y1 = b["bbox"]
            if x1 <= ps[0] * 1.05 and y1 <= ps[1] * 1.05:
                ok += 1
        return ok / max(1, len([b for b in blocks if page_size.get(b.get("page_idx"))]))
    if in_page(1.0, 1.0) > 0.9:
        return 1.0, (1.0, 1.0)  # point coordinates
    return 1000.0, (1000.0, 1000.0)  # normalized coordinates


def merge_images_from_pdf(paper):
    """Rebuild images/ by cropping from the origin PDF using the old content_list bboxes (zero misassignment, deterministic)"""
    import fitz
    cl = [f for f in os.listdir(paper) if f.endswith("_content_list.json")][0]
    blocks = load_image_blocks(os.path.join(paper, cl))
    norm, (sx, sy) = resolve_bbox_scale(paper, cl)
    pdf_path = find_origin_pdf(paper)
    pdf = fitz.open(pdf_path)
    # actual page size (the basis for converting bboxes)
    page_dims = {i: (pg.rect.width, pg.rect.height) for i, pg in enumerate(pdf)}
    # if the layout page_size differs from the PDF size, prefer layout (some PDFs have CropBox differences)
    lay = os.path.join(paper, "layout.json")
    if os.path.exists(lay):
        try:
            lj = json.load(open(lay, encoding="utf-8"))
            for pg in lj.get("pdf_info", []):
                ps = pg.get("page_size")
                if ps and pg.get("page_idx") in page_dims:
                    page_dims[pg["page_idx"]] = (ps[0], ps[1])
        except Exception:
            pass
    imgs = os.path.join(paper, "images")
    os.makedirs(imgs, exist_ok=True)
    rebuilt, skipped = 0, []
    for b in blocks:
        h = os.path.basename(b["img_path"])
        dst = os.path.join(imgs, h)
        if os.path.exists(dst):
            rebuilt += 1
            continue
        pidx = b.get("page_idx", 0)
        if pidx >= len(pdf):
            skipped.append((h, f"page index {pidx} out of range"))
            continue
        W, H = page_dims.get(pidx, (pdf[pidx].rect.width, pdf[pidx].rect.height))
        x0, y0, x1, y1 = b["bbox"]
        if norm != 1.0:
            x0, x1 = x0 / sx * W, x1 / sx * W
            y0, y1 = y0 / sy * H, y1 / sy * H
        clip = fitz.Rect(x0, y0, x1, y1)
        if clip.is_empty or clip.width < 5 or clip.height < 5:
            skipped.append((h, "invalid bbox"))
            continue
        pix = pdf[pidx].get_pixmap(clip=clip, dpi=200)
        pix.save(dst)
        rebuilt += 1
    return rebuilt, skipped, len(blocks)


def main(root):
    root = normalize_input(root)
    papers = papers_in(root)
    if not papers:
        print("No paper folder containing *_origin.pdf was found")
        return
    token = None          # lazily loaded: a token is needed only when MinerU must actually be called
    any_missing = False
    for paper in papers:
        name = os.path.basename(os.path.normpath(paper))[:44]
        missing = check_missing(paper)
        if not missing:
            print(f"{name:46s} products complete, skipped")
            continue
        any_missing = True
        # With full.md + content_list present, no MinerU re-parse is needed: rebuild images/ by cropping from the
        # origin PDF using the old content_list bboxes (deterministic, zero misassignment; legacy bboxes are 1000-normalized)
        if "images" in missing and "full.md" not in missing and "content_list" not in missing:
            rebuilt, skipped, total = merge_images_from_pdf(paper)
            print(f"  rebuilt images/ by cropping from the PDF {rebuilt}/{total}, skipped {len(skipped)} {skipped[:3] if skipped else ''}")
        else:
            if token is None:
                token = load_token()
            if not token:
                print(f"{name:46s} missing {missing}; MinerU re-parse required but no token was found"
                      f" (environment variable MINERU_TOKEN / C4EAI config workspace/mineru_config.json) → skipping this paper")
                continue
            pdf = find_origin_pdf(paper)
            print(f"{name:46s} missing {missing}; re-parsing {os.path.basename(pdf)} ...")
            with tempfile.TemporaryDirectory(prefix="mineru_reparse_") as td:
                new_dir = mineru_parse_pdf(pdf, token, td)
                need_full = "full.md" in missing
                need_cl = "content_list" in missing
                # a whole-set gap → adopt the new products directly
                if need_full:
                    shutil.copy2(os.path.join(new_dir, "full.md"), os.path.join(paper, "full.md"))
                if need_cl:
                    for f in os.listdir(new_dir):
                        if f.endswith("_content_list.json") and not f.endswith("_v2.json"):
                            shutil.copy2(os.path.join(new_dir, f), os.path.join(paper, f))
                            break
                if need_full:
                    # when adopting the whole set, complete layout as well
                    lf = [f for f in os.listdir(new_dir) if f == "layout.json"]
                    if lf and not os.path.exists(os.path.join(paper, "layout.json")):
                        shutil.copy2(os.path.join(new_dir, "layout.json"), os.path.join(paper, "layout.json"))
                    # the new whole images set is copied directly
                    nimg = os.path.join(new_dir, "images")
                    if os.path.isdir(nimg):
                        dimg = os.path.join(paper, "images")
                        os.makedirs(dimg, exist_ok=True)
                        for f in os.listdir(nimg):
                            dst = os.path.join(dimg, f)
                            if not os.path.exists(dst):
                                shutil.copy2(os.path.join(nimg, f), dst)
        # re-check
        still = check_missing(paper)
        print(f"  re-check: {'still missing ' + str(still) if still else 'complete OK'}")
    if not any_missing:
        print("All papers have complete products; no re-parsing needed")


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else None
    if not root:
        print("Usage: python step0b_mineru_reparse.py <paper folder or multi-paper directory>")
        sys.exit(1)
    main(root)
