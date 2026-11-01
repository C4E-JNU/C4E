# -*- coding: utf-8 -*-
"""step0_preprocess.py — unified entry point for Step 2: complete MinerU products then archive (text cleaning + figures).

Input 【accepts whatever the user gives】: a paper PDF file / a paper folder / a multi-paper directory.
Output: per paper, under <paper folder>/datamining/
      - 00_full_cleaned.md   body text (cut before References)
      - images/FigureN/...   figures: when a PDF exists, prefer cropping from the PDF using content_list bboxes,
                             and only fall back to archiving MinerU loose images when no PDF exists
      - images/FigureN/FigureN.txt  English caption + page number / bbox
Usage: python step0_preprocess.py <pdf|paper folder|multi-paper directory>
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_preprocessor as pp  # noqa: E402
import step0b_mineru_reparse as s0b  # noqa: E402
import step0c_clip_figures as s0c  # noqa: E402

_orig_find = pp.find_json_file


def find_json_list(folder):
    """Fix a defect in the original: prefer *_content_list.json (the MinerU index)
    so that other JSON files in the folder (e.g. mining_schema) are not picked by mistake
    and everything archived as unknown"""
    cands = sorted(f for f in os.listdir(folder) if f.endswith("_content_list.json"))
    if cands:
        return os.path.join(folder, cands[0])
    return _orig_find(folder)


pp.find_json_file = find_json_list
from paper_preprocessor import clean_full_md, process_image_match  # noqa: E402


def papers_in(root):
    """A single paper folder (containing full.md + images) or a multi-paper directory (walk subfolders)"""
    if os.path.exists(os.path.join(root, "full.md")) and os.path.isdir(os.path.join(root, "images")):
        return [root]
    return [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p))
            and os.path.exists(os.path.join(root, p, "full.md"))
            and os.path.isdir(os.path.join(root, p, "images"))]


def main(root):
    root = s0b.normalize_input(root)
    if root is None:
        return
    s0b.main(root)  # acts only when MinerU products are missing (including calling MinerU on demand); near-instant when complete
    papers = papers_in(root)
    if not papers:
        print("No paper folder containing full.md + images/ was found")
        return
    for paper in papers:
        wf = os.path.join(paper, "datamining")
        os.makedirs(wf, exist_ok=True)
        # 1) Figures: prefer PDF cropping (complete blocks, independent of loose images); on failure fall back to MinerU loose-image archiving
        clip = s0c.clip_paper(paper) if s0c._origin_pdf(paper) else None
        if not (clip and clip.get("figures")):
            img = process_image_match(paper, output_dir=Path(wf))
            st = img.get("stats", {})
            print(f'  fell back to loose-image archiving {"OK" if img.get("ok") else "FAIL"} '
                  f'{img.get("processed", 0)}/{img.get("total", 0)} '
                  f'(Fig {st.get("figure", 0)} / Table {st.get("table", 0)} / unknown {st.get("unknown", 0)})')
        # 2) Clean the full text → 00_full_cleaned.md (overwrite)
        cln = clean_full_md(paper, output_dir=Path(wf))
        old_md = os.path.join(wf, "full_cleaned.md")
        if os.path.exists(old_md):
            shutil.move(old_md, os.path.join(wf, "00_full_cleaned.md"))
        nsub = len(os.listdir(os.path.join(wf, "images"))) if os.path.isdir(os.path.join(wf, "images")) else 0
        print(f'  00_full_cleaned.md {"OK" if cln.get("ok") else "FAIL"} {cln.get("chars", 0)} chars | figure folders {nsub}')


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not arg:
        print("Usage: python step0_preprocess.py <pdf|paper folder|multi-paper directory>")
        sys.exit(1)
    main(arg)
