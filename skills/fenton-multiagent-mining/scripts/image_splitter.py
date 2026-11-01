"""image_splitter.py — subfigure splitting (a fixed Python script, not an LLM)
For figures marked needs_subfigure_split=true in the work order, split them into subfigures using white-gap line detection.

Algorithm source: subfigure_split.ipynb (recursive cutting along horizontal then vertical white lines, named A/B/C)
Input: 01_work_order.json + content_list.json in each paper directory
Output: subfigure files for figures that need splitting, saved under paper/images/split/<FigureX>/
"""
import os
import re
import json
import cv2
import numpy as np
from pathlib import Path
from PIL import Image


# ───────────────── image loading (tolerates Windows long paths / non-ASCII paths) ─────────────────

def _long_path(path):
    path = str(path)
    if path.startswith('\\\\?\\'):
        return path
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    return '\\\\?\\' + path


def safe_imread(img_path):
    """Safely read an image (tolerates non-ASCII and long paths)"""
    try:
        pil_img = Image.open(img_path)
        if pil_img.mode != 'RGB':
            pil_img = pil_img.convert('RGB')
        return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception:
        pass
    try:
        img_array = np.fromfile(_long_path(img_path), np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    except Exception:
        return None


# ───────────────── white-line detection + splitting algorithm (reused from the ipynb) ─────────────────

def find_white_horizontal_lines(img_gray, threshold=250, min_white_ratio=0.99):
    """Find rows that are entirely white across the full width"""
    h, w = img_gray.shape
    white_lines = []
    for y in range(h):
        if np.mean(img_gray[y, :] > threshold) >= min_white_ratio:
            white_lines.append(y)
    return white_lines


def group_consecutive_lines(lines, min_gap=3):
    """Group consecutive white rows/columns"""
    if not lines:
        return []
    groups = []
    start = lines[0]
    prev = lines[0]
    for curr in lines[1:]:
        if curr - prev > min_gap:
            groups.append((start, prev))
            start = curr
        prev = curr
    groups.append((start, prev))
    return groups


def should_split_region(region_size, total_size, min_ratio=0.25):
    return region_size >= total_size * min_ratio


def merge_small_regions(regions, total_size, min_size_ratio=0.25):
    if not regions or len(regions) <= 1:
        return regions
    min_size = total_size * min_size_ratio
    merged = []
    i = 0
    while i < len(regions):
        start, end = regions[i]
        size = end - start
        if size < min_size:
            if i > 0 and i < len(regions) - 1:
                prev_size = merged[-1][1] - merged[-1][0] if merged else 0
                next_start, next_end = regions[i + 1]
                next_size = next_end - next_start
                if prev_size >= next_size:
                    if merged:
                        prev_start, prev_end = merged.pop()
                        merged.append((prev_start, end))
                else:
                    merged.append((start, next_end))
                    i += 1
            elif i > 0:
                if merged:
                    prev_start, prev_end = merged.pop()
                    merged.append((prev_start, end))
            elif i < len(regions) - 1:
                next_start, next_end = regions[i + 1]
                merged.append((start, next_end))
                i += 1
            else:
                merged.append((start, end))
        else:
            merged.append((start, end))
        i += 1
    return merged


def split_horizontal_with_merge(img, img_gray, y_start, y_end, min_width_ratio=0.25):
    """Cut a horizontal segment vertically, merging regions that are too narrow"""
    w = img_gray.shape[1]
    vertical_white = []
    for x in range(w):
        column_slice = img_gray[y_start:y_end, x]
        if np.mean(column_slice > 250) >= 0.99:
            vertical_white.append(x)
    if not vertical_white:
        return [img[y_start:y_end, :]]
    v_groups = group_consecutive_lines(vertical_white, min_gap=5)
    cut_points = []
    for v_start, v_end in v_groups:
        if v_end - v_start >= 3:
            cut_points.append((v_start + v_end) // 2)
    if not cut_points:
        return [img[y_start:y_end, :]]
    regions = []
    x_start = 0
    for cut_x in cut_points:
        if cut_x - x_start > 5:
            region_width = cut_x - x_start
            if should_split_region(region_width, w, min_width_ratio):
                regions.append((x_start, cut_x))
        x_start = cut_x
    if w - x_start > 5:
        region_width = w - x_start
        if should_split_region(region_width, w, min_width_ratio):
            regions.append((x_start, w))
    if not regions:
        return [img[y_start:y_end, :]]
    regions = merge_small_regions(regions, w, min_width_ratio)
    if len(regions) <= 1:
        return [img[y_start:y_end, :]]
    subplots = []
    for x1, x2 in regions:
        sub_img = img[y_start:y_end, x1:x2]
        if sub_img.size > 0:
            subplots.append(sub_img)
    return subplots


def split_figure(img_path, output_dir, figure_name,
                 min_height_ratio=0.25, min_width_ratio=0.25):
    """Cut horizontally first, then vertically, naming pieces A/B/C in order; return the saved subfigure file names"""
    img = safe_imread(img_path)
    if img is None:
        return []
    img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = img_gray.shape

    # 1. horizontal cut
    h_white = find_white_horizontal_lines(img_gray)
    if not h_white:
        h_cut_points = []
    else:
        h_groups = group_consecutive_lines(h_white, min_gap=3)
        h_cut_points = []
        for gap_start, gap_end in h_groups:
            if gap_end - gap_start >= 3:
                h_cut_points.append((gap_start + gap_end) // 2)

    if not h_cut_points:
        h_regions = [(0, h)]
    else:
        h_regions = []
        y_start = 0
        for cut_y in h_cut_points:
            if cut_y - y_start > 5:
                region_height = cut_y - y_start
                if should_split_region(region_height, h, min_height_ratio):
                    h_regions.append((y_start, cut_y))
            y_start = cut_y
        if h - y_start > 5:
            region_height = h - y_start
            if should_split_region(region_height, h, min_height_ratio):
                h_regions.append((y_start, h))

    if not h_regions:
        h_regions = [(0, h)]
    h_regions = merge_small_regions(h_regions, h, min_height_ratio)

    # 2. vertical cut
    all_subplots = []
    for y_start, y_end in h_regions:
        subplots = split_horizontal_with_merge(img, img_gray, y_start, y_end, min_width_ratio)
        all_subplots.extend(subplots)

    if len(all_subplots) <= 1:
        return []

    # 3. save subfigures
    os.makedirs(output_dir, exist_ok=True)
    saved = []
    for idx, sub_img in enumerate(all_subplots):
        sub_h, sub_w = sub_img.shape[:2]
        if sub_h < h * 0.08 or sub_w < w * 0.08:
            continue
        label = chr(65 + idx)  # A, B, C...
        output_filename = f"{figure_name}{label}.jpg"
        output_path = os.path.join(output_dir, output_filename)
        # Labels (A/B/C...) are unique within one round, so names never collide; a rerun overwrites the old subfigures
        # (do not switch to _1/_2 suffixes: downstream matches the subfigure letter from the last character of the file name, which a suffix would break)
        try:
            cv2.imencode('.jpg', sub_img)[1].tofile(output_path)
            saved.append(os.path.basename(output_path))
        except Exception:
            pass
    return saved


# ───────────────── work-order lookup: figure_id → actual image ─────────────────

def normalize_figure_id(fid: str) -> str:
    """'Fig. 1' / 'Figure 1' / 'Fig.1' → 'Figure 1'"""
    m = re.search(r'(?:Fig(?:ure)?\.?)\s*(\d+)', str(fid), re.IGNORECASE)
    if m:
        return f"Figure {m.group(1)}"
    return str(fid).strip()


def load_content_list(paper_dir: Path):
    """Read content_list.json and build a {figure number: img_path} mapping"""
    for f in paper_dir.glob('*_content_list.json'):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            mapping = {}
            for item in data:
                if item.get('type') == 'image' and item.get('image_caption'):
                    cap = item['image_caption'][0] if isinstance(item['image_caption'], list) else item['image_caption']
                    fid = normalize_figure_id(cap)
                    mapping[fid] = item['img_path']
            return mapping
        except Exception:
            continue
    return {}


def find_image_for_figure(paper_dir: Path, figure_id: str, content_map: dict):
    """Locate the actual image file path from the work order figure_id
    (content_list img_path / the images/FigureN subdirectory, preferring the workflow copy and falling back to the paper directory)"""
    norm = normalize_figure_id(figure_id)
    if norm in content_map:
        p = paper_dir / content_map[norm]
        if p.exists():
            return p
        # content_list's img_path may be stale (images/xxx.jpg) while the actual image lives in the FigureN subdirectory
        basename = Path(content_map[norm]).name
        for base in [paper_dir / 'C4EAI AGENTICWORKFLOW', paper_dir]:
            for fig_dir in [base / 'images' / norm.replace(' ', ''), base / 'images' / norm]:
                cand = fig_dir / basename
                if cand.exists():
                    return cand
    # fallback: under images/, find a file name containing FigureN (preferring the workflow copy, falling back to the paper directory)
    for base in [paper_dir / 'C4EAI AGENTICWORKFLOW', paper_dir]:
        for ext in ['*.jpg', '*.png', '*.jpeg']:
            for p in (base / 'images').glob(ext):
                if norm.lower().replace(' ', '') in p.stem.lower().replace(' ', ''):
                    return p
        # look inside the images/FigureN subdirectory
        for sub in (base / 'images').glob('*'):
            if sub.is_dir() and norm.lower().replace(' ', '') in sub.name.lower().replace(' ', ''):
                for ext in ['*.jpg', '*.png', '*.jpeg']:
                    for p in sub.glob(ext):
                        return p
    return None


# ───────────────── main entry ─────────────────

def split_work_order(paper_dir: Path, work_order=None, log=print):
    """Split every figure with needs_subfigure_split=true according to the work order.
    The split count should match the work order's total_subfigures; otherwise the split is marked as failed (the caller falls back to whole-figure processing).
    Products (02_image_match.json is written by the caller; split subfigures go to the workflow's images/split/)."""
    wf_dir = paper_dir / "C4EAI AGENTICWORKFLOW"
    if work_order is None:
        wo_path = wf_dir / '01_work_order.json'
        if not wo_path.exists():
            wo_path = paper_dir / '01_work_order.json'
        if not wo_path.exists():
            log("! no work-order file")
            return []
        work_order = json.loads(wo_path.read_text(encoding='utf-8'))

    content_map = load_content_list(paper_dir)
    results = []
    for task in work_order:
        fid = task.get('figure_id')
        subs = task.get('subfigures', [])
        need_split = any(s.get('needs_subfigure_split') for s in subs)
        if not need_split:
            continue
        img_path = find_image_for_figure(paper_dir, fid, content_map)
        if img_path is None:
            log(f"! {fid}: image not found")
            continue
        out_dir = wf_dir / 'images' / 'split' / normalize_figure_id(fid).replace(' ', '')
        saved = split_figure(str(img_path), str(out_dir), normalize_figure_id(fid).replace(' ', ''))
        # Check: the number of split subfigures should match the total recorded in the work order
        expected = task.get('total_subfigures') or len(subs)
        split_ok = (len(saved) == expected)
        results.append({
            'figure_id': fid,
            'img_path': str(img_path),
            'subfigures': saved,
            'expected_total': expected,
            'split_success': split_ok,
            'needs_xy_count': len([s for s in subs if s.get('needs_xy_extraction')]),
        })
        if split_ok:
            log(f"OK {fid}: split into {len(saved)} subfigures (matches the recorded {expected})")
        else:
            log(f"! {fid}: split into {len(saved)} but the work order records {expected}; treating the split as failed and falling back to whole-figure processing")
    return results


def split_single_paper(paper_dir: Path, log=print):
    """Run subfigure splitting for a single paper directory"""
    return split_work_order(Path(paper_dir), log=log)


if __name__ == '__main__':
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target:
        split_single_paper(Path(target))
    else:
        print("Usage: python image_splitter.py <paper directory path>")
