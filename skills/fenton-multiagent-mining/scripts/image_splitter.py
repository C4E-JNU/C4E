"""image_splitter.py — 子图分割（固定 Python 脚本，非 LLM）
根据工单中 needs_subfigure_split=true 的图，用白色间隙线检测算法分割成子图。

算法来源：子图分割.ipynb（横向白线 + 纵向白线递归切割，按 A/B/C 命名）
输入：每篇文献目录下的 01_work_order.json + content_list.json
输出：对需分割的图生成子图文件，保存到文献/images/split/<FigureX>/ 下
"""
import os
import re
import json
import cv2
import numpy as np
from pathlib import Path
from PIL import Image


# ───────────────── 图像读取（兼容 Windows 长路径 / 中文路径） ─────────────────

def _long_path(path):
    path = str(path)
    if path.startswith('\\\\?\\'):
        return path
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    return '\\\\?\\' + path


def safe_imread(img_path):
    """安全读取图片（兼容中文/长路径）"""
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


# ───────────────── 白线检测 + 分割算法（复用 ipynb） ─────────────────

def find_white_horizontal_lines(img_gray, threshold=250, min_white_ratio=0.99):
    """找到完全横向贯穿的白色行"""
    h, w = img_gray.shape
    white_lines = []
    for y in range(h):
        if np.mean(img_gray[y, :] > threshold) >= min_white_ratio:
            white_lines.append(y)
    return white_lines


def group_consecutive_lines(lines, min_gap=3):
    """将连续的白色行/列分组"""
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
    """对横向片段纵向切割，合并太窄区域"""
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
    """先横向切割再纵向切割，按顺序命名 A/B/C... 返回保存的子图文件名列表"""
    img = safe_imread(img_path)
    if img is None:
        return []
    img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = img_gray.shape

    # 1. 横向切割
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

    # 2. 纵向切割
    all_subplots = []
    for y_start, y_end in h_regions:
        subplots = split_horizontal_with_merge(img, img_gray, y_start, y_end, min_width_ratio)
        all_subplots.extend(subplots)

    if len(all_subplots) <= 1:
        return []

    # 3. 保存子图
    os.makedirs(output_dir, exist_ok=True)
    saved = []
    for idx, sub_img in enumerate(all_subplots):
        sub_h, sub_w = sub_img.shape[:2]
        if sub_h < h * 0.08 or sub_w < w * 0.08:
            continue
        label = chr(65 + idx)  # A, B, C...
        output_filename = f"{figure_name}{label}.jpg"
        output_path = os.path.join(output_dir, output_filename)
        # 同轮内 label（A/B/C...）唯一不会重名；跨轮重跑直接覆盖旧子图
        # （不能改用 _1/_2 后缀：下游按文件名末字母 A/B/C 匹配子图号，后缀会破坏映射）
        try:
            cv2.imencode('.jpg', sub_img)[1].tofile(output_path)
            saved.append(os.path.basename(output_path))
        except Exception:
            pass
    return saved


# ───────────────── 工单定位：figure_id → 实际图片 ─────────────────

def normalize_figure_id(fid: str) -> str:
    """'Fig. 1' / 'Figure 1' / 'Fig.1' → 'Figure 1'"""
    m = re.search(r'(?:Fig(?:ure)?\.?)\s*(\d+)', str(fid), re.IGNORECASE)
    if m:
        return f"Figure {m.group(1)}"
    return str(fid).strip()


def load_content_list(paper_dir: Path):
    """读取 content_list.json，建立 {图号: img_path} 映射"""
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
    """根据工单 figure_id 定位实际图片文件路径
    （content_list img_path / images/FigureN 子目录，workflow 优先回退文献目录）"""
    norm = normalize_figure_id(figure_id)
    if norm in content_map:
        p = paper_dir / content_map[norm]
        if p.exists():
            return p
        # content_list 的 img_path 可能是旧路径（images/xxx.jpg），实际图在 FigureN 子目录
        basename = Path(content_map[norm]).name
        for base in [paper_dir / 'C4EAI AGENTICWORKFLOW', paper_dir]:
            for fig_dir in [base / 'images' / norm.replace(' ', ''), base / 'images' / norm]:
                cand = fig_dir / basename
                if cand.exists():
                    return cand
    # 兜底：在 images/ 目录找文件名含 FigureN 的（workflow 优先回退文献目录）
    for base in [paper_dir / 'C4EAI AGENTICWORKFLOW', paper_dir]:
        for ext in ['*.jpg', '*.png', '*.jpeg']:
            for p in (base / 'images').glob(ext):
                if norm.lower().replace(' ', '') in p.stem.lower().replace(' ', ''):
                    return p
        # 在 images/FigureN 子目录找
        for sub in (base / 'images').glob('*'):
            if sub.is_dir() and norm.lower().replace(' ', '') in sub.name.lower().replace(' ', ''):
                for ext in ['*.jpg', '*.png', '*.jpeg']:
                    for p in sub.glob(ext):
                        return p
    return None


# ───────────────── 主入口 ─────────────────

def split_work_order(paper_dir: Path, work_order=None, log=print):
    """按工单分割所有 needs_subfigure_split=true 的图。
    分割数应与工单 total_subfigures 一致，不符则标记分割失败（调用方降级为整图处理）。
    产出（02_image_match.json 由调用方写；分割子图写入 workflow 的 images/split/）。"""
    wf_dir = paper_dir / "C4EAI AGENTICWORKFLOW"
    if work_order is None:
        wo_path = wf_dir / '01_work_order.json'
        if not wo_path.exists():
            wo_path = paper_dir / '01_work_order.json'
        if not wo_path.exists():
            log("⚠️ 无工单文件")
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
            log(f"⚠️ {fid}: 找不到图片")
            continue
        out_dir = wf_dir / 'images' / 'split' / normalize_figure_id(fid).replace(' ', '')
        saved = split_figure(str(img_path), str(out_dir), normalize_figure_id(fid).replace(' ', ''))
        # 校验：分割出的子图数应与工单标注的总子图数一致
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
            log(f"✅ {fid}: 分割出 {len(saved)} 个子图（与标注的 {expected} 一致）")
        else:
            log(f"⚠️ {fid}: 分割出 {len(saved)} 个，但工单标注 {expected} 个，判定分割失败，降级为整图处理")
    return results


def split_single_paper(paper_dir: Path, log=print):
    """对单个文献目录执行子图分割"""
    return split_work_order(Path(paper_dir), log=log)


if __name__ == '__main__':
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target:
        split_single_paper(Path(target))
    else:
        print("用法: python image_splitter.py <文献目录路径>")
