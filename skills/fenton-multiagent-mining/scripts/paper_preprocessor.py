"""Paper Preprocessor — literature preprocessing (fixed Python code, no LLM calls).

Functions (corresponding to the two notebooks S1.4 image matching + S1.5 redundancy cleaning, but 【excluding extraction】):
1. Image matching: read the MinerU JSON index inside the paper folder (excluding layout.json),
   match the loose images under images/ to their figure/table numbers (Figure1/Figure2/Table1/unknown_xx),
   create a subfolder per figure/table, copy the images and generate a .txt description file.
2. Full-text cleaning: read full.md, take everything before the '# References' heading, and write full_cleaned.md.
   (If no References heading is found, copy the whole text.)

Conventions:
- Existing files are 【overwritten】.
- Input: the path of one paper folder (already extracted by the user).
- Output: images/<FigureN>/..., full_cleaned.md.
"""
import os
import re
import json
import shutil
from pathlib import Path

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp")


# ─────────────────────────────────────────────
# image matching (from decompress_and_match.ipynb cell 1)
# ─────────────────────────────────────────────
def find_json_file(folder):
    """Find the JSON file in the folder (excluding layout.json)"""
    for file in os.listdir(folder):
        if file.endswith(".json") and file != "layout.json":
            return os.path.join(folder, file)
    return None


def load_json_data(json_path):
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def create_image_index(json_data):
    """Build a file-name → entry index"""
    image_index = {}
    if not isinstance(json_data, list):
        return image_index
    for item in json_data:
        if not isinstance(item, dict):
            continue
        img_path = item.get("img_path")
        if not img_path:
            continue
        img_filename = os.path.basename(img_path)
        image_index[img_filename] = item
    return image_index


def extract_number(caption_text, elem_type):
    """Extract the figure/table number from the caption"""
    if isinstance(caption_text, list):
        caption = caption_text[0] if caption_text else ""
    else:
        caption = caption_text or ""
    if not caption:
        return None
    if elem_type == "table":
        match = re.search(r"\b(?:[Tt]able|[Tt]abela|[Tt]ablica|[Tt]ab\.?)\s*(\d+)", caption)
        prefix = "Table"
    else:
        match = re.search(r"\b(?:[Ff]ig\.?|[Ff]igure|[Ss]lika|[Ss]l\.?)\s*(\d+)", caption)
        prefix = "Figure"
    if match:
        return f"{prefix}{match.group(1)}"
    return None


def html_table_to_text(html_str):
    """Convert an HTML table into a text table"""
    if not html_str:
        return ""
    if not _HAS_BS4:
        return html_str
    try:
        soup = BeautifulSoup(html_str, "html.parser")
        table = soup.find("table")
        if not table:
            return html_str
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                text = td.get_text(strip=True)
                cells.append(text if text else "")
            if cells:
                rows.append(cells)
        return "\n".join(["\t".join(row) for row in rows])
    except Exception:
        return html_str


def safe_get_caption(item, elem_type):
    if elem_type == "unknown":
        return ""
    caption_value = item.get(f"{elem_type}_caption", "")
    if isinstance(caption_value, list):
        return caption_value[0] if caption_value else ""
    return caption_value or ""


def safe_get_footnote(item, elem_type):
    if elem_type == "unknown":
        return []
    footnote_value = item.get(f"{elem_type}_footnote", [])
    if isinstance(footnote_value, list):
        return footnote_value
    elif footnote_value:
        return [str(footnote_value)]
    return []


# ── full.md caption anchors (the reliable basis for grouping subfigures) ──────────────────
_MD_IMG = re.compile(r"images/([0-9A-Za-z._\-]+?\.(?:jpg|jpeg|png|gif|bmp))", re.I)
_FIGCAP = re.compile(r"^\s*(?:fig\.?|figure|slika|sl\.?)\s*((?:[Ss]?)\d+[a-z]?)\b", re.I)
_TABCAP = re.compile(r"^\s*(?:table|tabela|tablica|tab\.?)\s*((?:[Ss]?)\d+[a-z]?)\b", re.I)


def _panel_letter(caption):
    """Whether a subfigure caption is a single-letter label such as (a)/a/a."""
    if not caption:
        return None
    m = re.fullmatch(r"\s*[\(\[]?\s*([A-Za-z])\s*[\)\].:：]?\s*", str(caption))
    return m.group(1).lower() if m else None


def _find_content_list(folder):
    """Robustly choose *_content_list.json (excluding _v2/_model/layout, so the wrong file is not taken by directory order)."""
    cands = sorted(Path(folder).glob("*.json"))

    def score(p):
        n = p.name.lower()
        if n == "layout.json" or "model" in n or "v2" in n:
            return 99
        if n.endswith("_content_list.json"):
            return 0
        return 1 if "content_list" in n else 50
    good = [p for p in cands if score(p) < 50] or [p for p in cands if p.name.lower() != "layout.json"]
    return str(sorted(good, key=lambda p: (score(p), p.name))[0]) if good else None


def _parse_md_groups(md_path, exclude_files):
    """Scan full.md: consecutive images/<hash> entries are assigned to the nearest following Fig./Table N caption → the subfigure set of that figure.
    Returns groups=[{kind, token, caption, files:[fname...]}] and the consumed set."""
    try:
        lines = open(_lp(md_path), "r", encoding="utf-8").read().splitlines()
    except Exception:
        return [], set()
    groups, buffer, consumed = [], [], set()
    i, N = 0, len(lines)
    while i < N:
        line = lines[i]
        for f in _MD_IMG.findall(line):
            if f.lower().endswith(IMAGE_EXTS) and f not in exclude_files:
                buffer.append(f)
        fc, tc = _FIGCAP.match(line), _TABCAP.match(line)
        if fc or tc:
            kind = "Figure" if fc else "Table"
            token = (fc or tc).group(1)
            cap_lines = [line.strip()]
            j = i + 1
            while (j < N and lines[j].strip() and not _MD_IMG.search(lines[j])
                   and not (_FIGCAP.match(lines[j]) or _TABCAP.match(lines[j]))):
                cap_lines.append(lines[j].strip()); j += 1
            if buffer:
                groups.append({"kind": kind, "token": token, "caption": " ".join(cap_lines), "files": buffer})
                consumed.update(buffer)
                buffer = []
            i = j
            continue
        i += 1
    return groups, consumed


def _merge_panels(folder, files, out_path):
    """Stitch the subfigures of one logical figure vertically in md order into a single image (PIL); return None on failure."""
    try:
        from PIL import Image
        imgs = [Image.open(_lp(os.path.join(str(folder), f))) for f in files]
        w = max(im.width for im in imgs)
        gap = 12
        h = sum(im.height for im in imgs) + gap * (len(imgs) - 1)
        canvas = Image.new("RGB", (w, h), "white")
        y = 0
        for im in imgs:
            canvas.paste(im, (0, y)); y += im.height + gap
        canvas.save(_lp(out_path))
        return out_path
    except Exception:
        return None


def process_image_match(folder_path, output_dir=None):
    """Archive loose images by 【logical figure number】: a multi-panel figure (Fig.2 a/b/c…) goes into one Figure2/,
    each FigureN/ holding all its subfigure jpgs plus one FigureN.txt (full English caption + each subfigure's letter/page).
    Basis = "subfigure blocks → the immediately following Fig./Table N caption" in full.md; content_list only supplies letter labels/page numbers/table bodies.
    When output_dir is given, everything is organized into output_dir/images (e.g. the workflow's datamining/images)."""
    folder = Path(folder_path)
    images_dir = folder / "images"
    if not images_dir.is_dir():
        return {"ok": False, "reason": "images folder does not exist", "processed": 0, "total": 0,
                "indexed": 0, "stats": {"table": 0, "figure": 0, "other": 0, "unknown": 0}}

    image_files = [f for f in os.listdir(images_dir) if f.lower().endswith(IMAGE_EXTS)]

    # content_list index (supplies letter labels/page numbers/table bodies/types)
    json_path = _find_content_list(folder_path)
    json_data = load_json_data(json_path) if json_path else None
    image_index = create_image_index(json_data) if json_data else {}
    table_files = set()
    if isinstance(json_data, list):
        for it in json_data:
            if isinstance(it, dict) and it.get("type") == "table" and it.get("img_path"):
                table_files.add(os.path.basename(it["img_path"]))

    target_images = (Path(output_dir) / "images") if output_dir else images_dir
    os.makedirs(_lp(str(target_images)), exist_ok=True)

    # primary: group subfigures by the full.md captions
    md_path = folder / "full.md"
    if not md_path.exists():
        mds = sorted(folder.glob("*.md")); md_path = mds[0] if mds else None
    groups, consumed = _parse_md_groups(str(md_path), table_files) if md_path else ([], set())

    used = set()
    n_fig = n_tab = n_unk = processed = 0

    def new_folder(kind, token):
        base = f"{kind}{token}"; name, k = base, 1
        while name in used:
            k += 1; name = f"{base}_{k}"
        used.add(name); return name

    def copy_into(folder_name, img_file):
        nonlocal processed
        if not os.path.exists(os.path.join(str(images_dir), img_file)):
            return  # an image referenced by md is missing under images/ (name drift after MinerU re-parsing): skip and let the work order mark [figure pending]
        _make_image_folder(str(images_dir), img_file, target_images, folder_name, txt_content=None)
        processed += 1

    # 1) logical figures (including multi-panel ones)
    for g in groups:
        fname = new_folder("Figure" if g["kind"] == "Figure" else "Table", g["token"])
        if g["kind"] == "Figure":
            n_fig += 1
        else:
            n_tab += 1
        panel_lines = []
        for f in g["files"]:
            it = image_index.get(f, {})
            letter = _panel_letter(it.get("image_caption", "")) if it.get("type") == "image" else None
            copied_before = processed
            copy_into(fname, f)
            if processed == copied_before:
                continue  # image missing and not copied: no panel line left behind
            panel_lines.append(f"  [{letter or '-'}] {f}  page={it.get('page_idx', '')}")
        if not panel_lines:
            used.discard(fname)
            continue  # every image in the group is missing: do not create an empty directory
        txt = [g["caption"], "", f"{g['kind']} {g['token']}: {len(g['files'])} subfigures", "-" * 40] + panel_lines
        with open(_lp(os.path.join(str(target_images), fname, fname + ".txt")), "w", encoding="utf-8") as tf:
            tf.write("\n".join(txt) + "\n")
        # multiple subfigures → generate a vertically stitched whole figure FigureN_full.jpg (for splitting / whole-figure fallback)
        if len(g["files"]) > 1:
            if _merge_panels(target_images, g["files"], os.path.join(str(target_images), fname, fname + "_full.jpg")):
                panel_lines.append(f"  [merged] {fname}_full.jpg")

    # 2) tables: type=table entries in content_list that md did not claim (the table body is converted to text into TableN.txt)
    if isinstance(json_data, list):
        tseq = 0
        for it in json_data:
            if not isinstance(it, dict) or it.get("type") != "table" or not it.get("img_path"):
                continue
            f = os.path.basename(it["img_path"])
            if f in consumed or f not in image_files or not (images_dir / f).exists():
                continue
            tc = safe_get_caption(it, "table")
            m = re.search(r"table\s*(\d+)", tc, re.I)
            tseq += 1
            fname = new_folder("Table", m.group(1) if m else str(tseq))
            n_tab += 1
            copy_into(fname, f)
            lines = ["Type: table", f"Number: {fname}", ""]
            if tc:
                lines += [f"Caption: {tc}", ""]
            body = it.get("table_body", "")
            if body:
                lines += ["Table Content:", "-" * 40, html_table_to_text(body), "-" * 40]
            fn = safe_get_footnote(it, "table")
            if fn:
                lines += ["\nFootnote: " + " ".join(str(x) for x in fn)]
            with open(_lp(os.path.join(str(target_images), fname, fname + ".txt")), "w", encoding="utf-8") as tf:
                tf.write("\n".join(lines) + "\n")

    # 3) remaining unarchived loose images → unknown_, so real figure numbers stay clean
    assigned = set()
    for g in groups:
        assigned.update(g["files"])
    for f in image_files:
        if f in assigned or f in consumed:
            continue
        n_unk += 1
        fname = new_folder("unknown", f"_{n_unk:02d}")
        copy_into(fname, f)
        it = image_index.get(f, {})
        cap = safe_get_caption(it, it.get("type", "unknown")) if it else ""
        with open(_lp(os.path.join(str(target_images), fname, fname + ".txt")), "w", encoding="utf-8") as tf:
            tf.write(f"Original image: {f}\nType: {it.get('type','unknown') if it else 'unknown'}\n"
                     f"Note: not grouped under any Fig./Table caption (probably an unrecognized subfigure or decoration)."
                     + (f"\nCaption: {cap}" if cap else "") + "\n")

    stats = {"table": n_tab, "figure": n_fig, "other": 0, "unknown": n_unk}
    return {"ok": True, "processed": processed, "total": len(image_files),
            "indexed": len(image_index), "stats": stats}


def _lp(s):
    """Windows long-path prefix (add the extended-length prefix when the path exceeds 160 characters or is deeply nested, avoiding WinError 3/Errno 2).
    shutil.copy2/open/makedirs raise No such file for ordinary str long/non-ASCII paths, so the extended-length prefix is required."""
    p = str(s)
    if os.name == "nt" and len(p) > 160 and not p.startswith("\\\\?\\"):
        # the extended-length prefix requires an all-backslash absolute path; forward slashes cause WinError 2/3
        p = p.replace("/", "\\")
        return "\\\\?\\" + p
    return p


def _make_image_folder(images_dir, img_file, target_images, folder_name, txt_content=None, note=None):
    """Create the subfolder, copy the images and write the txt description (overwriting existing files).
    Source images are copied from images_dir, and the target subfolder is created under target_images."""
    target_dir = os.path.join(str(target_images), folder_name)
    os.makedirs(_lp(target_dir), exist_ok=True)
    img_src = os.path.join(images_dir, img_file)
    img_dst = os.path.join(target_dir, img_file)
    shutil.copy2(_lp(img_src), _lp(img_dst))  # overwrite
    txt_path = os.path.join(target_dir, f"{folder_name}.txt")
    content = txt_content if txt_content is not None else (note or "")
    with open(_lp(txt_path), "w", encoding="utf-8") as txt:
        txt.write(content)


# ─────────────────────────────────────────────
# full-text cleaning (from redundancy_cleaning.ipynb)
# ─────────────────────────────────────────────
_REF_PATTERN = re.compile(r"^#+\s+references?.*$", re.IGNORECASE | re.MULTILINE)


def clean_full_md(folder_path, output_dir=None):
    """Read full.md, cut before References and write full_cleaned.md (overwrite).
    When output_dir is omitted it is written to the paper directory; when given (e.g. a workflow directory) it is written there."""
    source_path = os.path.join(folder_path, "full.md")
    if not os.path.exists(source_path):
        return {"ok": False, "reason": "no full.md"}
    try:
        with open(_lp(source_path), "r", encoding="utf-8") as f:
            content = f.read()
        match = _REF_PATTERN.search(content)
        if match:
            cleaned = content[:match.start()].strip()
        else:
            cleaned = content.strip()
        target_dir = output_dir or folder_path
        os.makedirs(_lp(str(target_dir)), exist_ok=True)
        target_path = os.path.join(str(target_dir), "full_cleaned.md")
        with open(_lp(target_path), "w", encoding="utf-8") as f:
            f.write(cleaned)
        return {"ok": True, "found_ref": bool(match), "chars": len(cleaned)}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


# ─────────────────────────────────────────────
# unified entry
# ─────────────────────────────────────────────
def preprocess_paper(folder_path, output_dir=None):
    """Run the complete preprocessing for one paper folder (image matching + full-text cleaning).
    output_dir defaults to the paper directory; when given (e.g. a workflow directory) products are organized into output_dir."""
    img = process_image_match(folder_path, output_dir)
    clean = clean_full_md(folder_path, output_dir)
    return {
        "image_match": img,
        "full_clean": clean,
    }
