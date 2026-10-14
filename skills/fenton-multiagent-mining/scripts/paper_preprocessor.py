"""Paper Preprocessor — 文献预处理（固定 Python 代码，无 LLM 调用）。

功能（对应 S1.4 图像匹配 + S1.5 冗余清洗 两个 ipynb，但【不包含解压】）：
1. 图像匹配：读取文献文件夹内的 minerU JSON 索引（排除 layout.json），
   把 images/ 下零散的图片匹配到对应图表编号（Figure1/Figure2/Table1/unknown_xx），
   为每个图表建子文件夹并复制图片 + 生成 .txt 描述文件。
2. 全文清洗：读 full.md，截取 # References 之前的内容，写 full_cleaned.md。
   （若未找到 References 标题，则复制全文。）

约定：
- 已存在的文件【直接覆盖】。
- 输入：一篇文献文件夹路径（已由用户提前解压好）。
- 输出：images/<FigureN>/..., full_cleaned.md。
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
# 图像匹配（来自 解压缩与图像匹配工作.ipynb cell 1）
# ─────────────────────────────────────────────
def find_json_file(folder):
    """查找文件夹中的 JSON 文件（排除 layout.json）"""
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
    """创建 图片文件名 -> 对应字典 的索引"""
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
    """从 caption 中提取图表编号"""
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
    """将 HTML 表格转换为文本表格"""
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


# ── full.md 图注锚点（子图归并的可靠依据）──────────────────
_MD_IMG = re.compile(r"images/([0-9A-Za-z._\-]+?\.(?:jpg|jpeg|png|gif|bmp))", re.I)
_FIGCAP = re.compile(r"^\s*(?:fig\.?|figure|slika|sl\.?)\s*((?:[Ss]?)\d+[a-z]?)\b", re.I)
_TABCAP = re.compile(r"^\s*(?:table|tabela|tablica|tab\.?)\s*((?:[Ss]?)\d+[a-z]?)\b", re.I)


def _panel_letter(caption):
    """子图 caption 是否为单字母标号 (a)/a/a. 之类。"""
    if not caption:
        return None
    m = re.fullmatch(r"\s*[\(\[]?\s*([A-Za-z])\s*[\)\].:：]?\s*", str(caption))
    return m.group(1).lower() if m else None


def _find_content_list(folder):
    """稳健选 *_content_list.json（排除 _v2/_model/layout，避免按目录序取错）。"""
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
    """扫 full.md：连续的 images/<hash> 归到其后最近的 Fig./Table N 图注 → 该图的子图集合。
    返回 groups=[{kind, token, caption, files:[fname...]}] 与 consumed 集合。"""
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
    """把同一逻辑图的多张子图按 md 顺序纵向拼接为一张整图（PIL），失败返回 None。"""
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
    """按【逻辑图号】把散图归档：一张多子图(Fig.2 的 a/b/c…)归到同一个 Figure2/，
    每个 FigureN/ 里放该图全部子图 jpg + 一份 FigureN.txt(完整英文图注 + 各子图字母/页码)。
    依据 = full.md 里"子图块 → 紧随其后的 Fig./Table N 图注"，content_list 只补字母标号/页码/表格正文。
    output_dir 指定时编排到 output_dir/images（如 workflow 的 datamining/images）。"""
    folder = Path(folder_path)
    images_dir = folder / "images"
    if not images_dir.is_dir():
        return {"ok": False, "reason": "images 文件夹不存在", "processed": 0, "total": 0,
                "indexed": 0, "stats": {"table": 0, "figure": 0, "other": 0, "unknown": 0}}

    image_files = [f for f in os.listdir(images_dir) if f.lower().endswith(IMAGE_EXTS)]

    # content_list 索引（补字母标号/页码/表格正文/类型）
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

    # 主：按 full.md 图注归并子图
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
            return  # md 引用的图在 images/ 缺失（MinerU 二次解析名漂移）：跳过，留待工单标【待补图】
        _make_image_folder(str(images_dir), img_file, target_images, folder_name, txt_content=None)
        processed += 1

    # 1) 逻辑图（含多子图）
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
                continue  # 图缺失未复制：不留 panel 行
            panel_lines.append(f"  [{letter or '-'}] {f}  page={it.get('page_idx', '')}")
        if not panel_lines:
            used.discard(fname)
            continue  # 组内图全部缺失：不建空目录
        txt = [g["caption"], "", f"{g['kind']} {g['token']}：{len(g['files'])} 个子图", "-" * 40] + panel_lines
        with open(_lp(os.path.join(str(target_images), fname, fname + ".txt")), "w", encoding="utf-8") as tf:
            tf.write("\n".join(txt) + "\n")
        # 多子图 → 生成纵向拼接整图 FigureN_full.jpg（供分割/整图兜底使用）
        if len(g["files"]) > 1:
            if _merge_panels(target_images, g["files"], os.path.join(str(target_images), fname, fname + "_full.jpg")):
                panel_lines.append(f"  [merged] {fname}_full.jpg")

    # 2) 表：content_list 里 type=table 且 md 未收留的（表正文转文本写进 TableN.txt）
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

    # 3) 剩余未归档散图 → unknown_，不污染正图号
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
                     f"Note: 未被任何 Fig./Table 图注归并（疑似未识别子图/装饰图）。"
                     + (f"\nCaption: {cap}" if cap else "") + "\n")

    stats = {"table": n_tab, "figure": n_fig, "other": 0, "unknown": n_unk}
    return {"ok": True, "processed": processed, "total": len(image_files),
            "indexed": len(image_index), "stats": stats}


def _lp(s):
    """Windows 长路径前缀（>160 字符或中文深路径时加 \\\\?\\，避免 WinError 3/Errno 2）。
    shutil.copy2/open/makedirs 对普通 str 的长路径/中文路径会报 No such file，需 \\\\?\\ 前缀。"""
    p = str(s)
    if os.name == "nt" and len(p) > 160 and not p.startswith("\\\\?\\"):
        # \\?\ 扩展长度前缀要求全反斜杠的绝对路径；正斜杠会导致 WinError 2/3
        p = p.replace("/", "\\")
        return "\\\\?\\" + p
    return p


def _make_image_folder(images_dir, img_file, target_images, folder_name, txt_content=None, note=None):
    """建子文件夹并复制图片 + 写 txt 描述（覆盖已存在）。
    输入图从 images_dir 复制，目标子文件夹建在 target_images 下。"""
    target_dir = os.path.join(str(target_images), folder_name)
    os.makedirs(_lp(target_dir), exist_ok=True)
    img_src = os.path.join(images_dir, img_file)
    img_dst = os.path.join(target_dir, img_file)
    shutil.copy2(_lp(img_src), _lp(img_dst))  # 覆盖
    txt_path = os.path.join(target_dir, f"{folder_name}.txt")
    content = txt_content if txt_content is not None else (note or "")
    with open(_lp(txt_path), "w", encoding="utf-8") as txt:
        txt.write(content)


# ─────────────────────────────────────────────
# 全文清洗（来自 冗余清洗.ipynb）
# ─────────────────────────────────────────────
_REF_PATTERN = re.compile(r"^#+\s+references?.*$", re.IGNORECASE | re.MULTILINE)


def clean_full_md(folder_path, output_dir=None):
    """读 full.md，截取 References 之前写 full_cleaned.md（覆盖）。
    output_dir 缺省时写到文献目录；指定时（如 workflow 目录）写到该目录。"""
    source_path = os.path.join(folder_path, "full.md")
    if not os.path.exists(source_path):
        return {"ok": False, "reason": "无 full.md"}
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
# 统一入口
# ─────────────────────────────────────────────
def preprocess_paper(folder_path, output_dir=None):
    """对单篇文献文件夹执行完整预处理（图像匹配 + 全文清洗）。
    output_dir 缺省 = 文献目录；指定时（如 workflow 目录）产出编排到 output_dir。"""
    img = process_image_match(folder_path, output_dir)
    clean = clean_full_md(folder_path, output_dir)
    return {
        "image_match": img,
        "full_clean": clean,
    }
