#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MinerU 归档一条龙：解压 zip + 图片按【逻辑图号】归档 + 清洗全文 + 生成图文映射清单。

为什么以 full.md 为准来分图（重要）：
  MinerU 的 *_content_list.json 里，一张多子图（Fig.2 的 a/b/c…）常被拆成多个 image 项，
  每项 caption 只是单字母 "a"/"b" 或为空 —— 只靠 caption 抽数字会把一个图的子图打散成
  Figure2/Figure3/Figure4…。而 full.md 里，图注块 "Fig. N. 完整说明" 紧跟在该图的各子图
  引用之后，天然是"一组子图 → 一个图号 + 完整图注"的可靠依据。故本脚本：
    1) 解析 full.md：把连续的 images/<hash> 归到其后最近的 "Fig./Table N" 图注 = 该图的子图；
    2) 用 content_list 补每张子图的字母标号 (a/b/c) 与页码/脚注/表格正文；
    3) 表 (type=table) 单独按 "Table N" 归档；
    4) 没被任何图注收留的散图 → unknown_ 前缀，绝不与正图混号。

产出（幂等，重跑覆盖；源 zip/论文目录不改动）：
  <out>/<论文名>/
     images/FigureN/<hash>.jpg + FigureN.txt(完整英文图注 + 各子图字母/页码)
     images/TableN/...
     00_full_cleaned.md      # 截 References 前正文
     00_images_map.json      # 逐图 + 逐"逻辑图(含子图列表)"映射清单，供翻译技能一行多图
"""
import os
import re
import sys
import json
import shutil
import zipfile
from pathlib import Path

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp")
_MD_IMG = re.compile(r"images/([0-9A-Za-z._\-]+?\.(?:jpg|jpeg|png|gif|bmp))", re.I)
# 图注行：Fig.2 / Figure 2 / Fig 2a / Fig. S1 / Table 1 ...
_FIGCAP = re.compile(r"^\s*(?:fig\.?|figure)\s*((?:[Ss]?)\d+[a-z]?)\b", re.I)
_TABCAP = re.compile(r"^\s*table\s*((?:[Ss]?)\d+[a-z]?)\b", re.I)


def _lp(s):
    p = str(s)
    if os.name == "nt" and len(p) > 160 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def _to_web_url(abs_path):
    norm = str(abs_path).replace("\\", "/")
    i = norm.find("workspace/")
    return "/" + norm[i:] if i >= 0 else norm


def _safe_extract(zip_path, dest):
    os.makedirs(_lp(dest), exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        dest_abs = os.path.abspath(dest)
        for member in z.namelist():
            target = os.path.abspath(os.path.normpath(os.path.join(dest, member)))
            if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
                continue
        z.extractall(_lp(dest))
    return dest


def _locate_paper_root(folder):
    folder = Path(folder)
    def is_paper(d):
        return (d / "images").is_dir() and ((d / "full.md").exists() or bool(list(d.glob("*.md"))))
    if is_paper(folder):
        return str(folder)
    for child in folder.rglob("images"):
        if child.is_dir() and is_paper(child.parent):
            return str(child.parent)
    return str(folder)


def _find_content_list(folder):
    cands = [p for p in Path(folder).glob("*.json")]
    def score(p):
        n = p.name.lower()
        if n == "layout.json" or "model" in n or "v2" in n:
            return 99
        if n.endswith("_content_list.json"):
            return 0
        return 1 if "content_list" in n else 50
    good = [p for p in cands if score(p) < 50] or [p for p in cands if p.name.lower() != "layout.json"]
    return str(sorted(good, key=lambda p: (score(p), p.name))[0]) if good else None


def _index_content_list(json_path):
    idx, ordered = {}, []
    try:
        data = json.load(open(_lp(json_path), "r", encoding="utf-8"))
    except Exception:
        return idx, ordered
    if not isinstance(data, list):
        return idx, ordered
    for it in data:
        if isinstance(it, dict):
            ordered.append(it)
            ip = it.get("img_path")
            if ip:
                idx[os.path.basename(ip)] = it
    return idx, ordered


def _cap(item, key):
    v = item.get(key, "")
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v) if v else ""


def _html_table_to_text(html):
    if not html:
        return ""
    if not _HAS_BS4:
        return html
    try:
        rows = []
        table = BeautifulSoup(html, "html.parser").find("table")
        for tr in (table.find_all("tr") if table else []):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append("\t".join(cells))
        return "\n".join(rows) or html
    except Exception:
        return html


def _panel_letter(caption):
    """子图 caption 是否为单字母标号 a/b/C/d)。"""
    if not caption:
        return None
    m = re.fullmatch(r"\s*[\(\[]?\s*([A-Za-z])\s*[\)\].:：]?\s*", caption)
    return m.group(1).lower() if m else None


def _figure_layout(n):
    """按子图张数定排版：常规图 3:2 / 1:1，容器约 920px。
    1→1列(520px)  2→2列(380)  3→3列(290)  4→2×2(380)  5-9→3列(290)  ≥10→4列(215)"""
    if n <= 1:
        return 1, 520
    if n == 2:
        return 2, 380
    if n == 4:
        return 2, 380
    if n <= 9:
        return 3, 290
    return 4, 215


def _figure_layout_html(folder_name, token, panels, wpx):
    """生成整段 flex 排版 HTML（多子图同行排开、自动换行），写笔记时整段照抄、只改 alt。"""
    label = "图" if folder_name.startswith("Figure") else "表"
    imgs = []
    for p in panels:
        sub = p.get("sub")
        alt = f"{label}{token}{sub}" if sub else f"{label}{token}"
        imgs.append(f'<img src="{p["web_url"]}" alt="{alt}" '
                    f'style="width:{wpx}px;height:auto;border-radius:6px;cursor:zoom-in;" />')
    return ('<div style="display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-start;gap:8px;">\n'
            + "\n".join(imgs) + "\n</div>")


def _parse_md_groups(md_path, exclude_files):
    """扫 full.md：连续的 images/<hash> 归到其后最近的 Fig./Table N 图注。
    返回 groups=[{kind:'Figure'/'Table', token, caption, files:[fname...]}] 和 consumed 集合。"""
    try:
        lines = open(_lp(md_path), "r", encoding="utf-8").read().splitlines()
    except Exception:
        return [], set()
    groups, buffer, consumed = [], [], set()
    n_fig = n_tab = 0
    i, N = 0, len(lines)
    while i < N:
        line = lines[i]
        imgs = [f for f in _MD_IMG.findall(line) if f.lower().endswith(IMAGE_EXTS) and f not in exclude_files]
        buffer.extend(imgs)
        fc, tc = _FIGCAP.match(line), _TABCAP.match(line)
        if fc or tc:
            kind = "Figure" if fc else "Table"
            token = (fc or tc).group(1)
            # 抓完整图注：本行 + 之后直到空行的续行
            cap_lines = [line.strip()]
            j = i + 1
            while j < N and lines[j].strip() and not _MD_IMG.search(lines[j]) and not (_FIGCAP.match(lines[j]) or _TABCAP.match(lines[j])):
                cap_lines.append(lines[j].strip()); j += 1
            caption = " ".join(cap_lines)
            # 若图注行本身含图（少见），buffer 已含
            if buffer:
                if kind == "Figure":
                    n_fig += 1
                else:
                    n_tab += 1
                groups.append({"kind": kind, "token": token, "caption": caption, "files": buffer})
                consumed.update(buffer)
                buffer = []
            i = j
            continue
        i += 1
    return groups, consumed


def archive_paper(paper_dir, output_dir=None):
    folder = Path(paper_dir)
    images_dir = folder / "images"
    json_path = _find_content_list(str(folder))
    if not images_dir.is_dir():
        return {"ok": False, "reason": f"缺 images/ 目录：{paper_dir}"}
    idx, _ordered = _index_content_list(json_path) if json_path else ({}, [])

    target_images = (Path(output_dir) / "images") if output_dir else images_dir
    os.makedirs(_lp(str(target_images)), exist_ok=True)

    # 表类文件先排除出 md 子图缓冲
    table_files = {os.path.basename(it["img_path"]) for it in _ordered
                   if isinstance(it, dict) and it.get("type") == "table" and it.get("img_path")}
    md_path = folder / "full.md"
    if not md_path.exists():
        mds = sorted(folder.glob("*.md")); md_path = mds[0] if mds else None
    groups, consumed = _parse_md_groups(str(md_path), table_files) if md_path else ([], set())

    def copy_panel(fname, folder_name, sub=None):
        src = images_dir / fname
        if not src.exists():
            return None
        fdir = os.path.join(str(target_images), folder_name)
        os.makedirs(_lp(fdir), exist_ok=True)
        dst = os.path.join(fdir, fname)
        if os.path.abspath(_lp(src)) != os.path.abspath(_lp(dst)):
            shutil.copy2(_lp(src), _lp(dst))
        it = idx.get(fname, {})
        return {"file": fname, "sub": sub, "page": it.get("page_idx"),
                "web_url": _to_web_url(dst)}

    manifest_imgs, figures, used_folders = [], [], set()

    # 1) 由 full.md 得到的逻辑图（含子图）
    for g in groups:
        base_fn = g["kind"] + g["token"]
        folder_name = base_fn
        kk = 1
        while folder_name in used_folders:
            kk += 1; folder_name = f"{base_fn}_{kk}"
        used_folders.add(folder_name)
        panels = []
        for f in g["files"]:
            it = idx.get(f, {})
            letter = _panel_letter(_cap(it, "image_caption")) if it.get("type") == "image" else None
            info = copy_panel(f, folder_name, sub=letter)
            if info:
                panels.append(info)
                manifest_imgs.append({"orig": it.get("img_path", f"images/{f}"), "filename": f,
                                      "folder": folder_name, "group": folder_name, "sub": letter,
                                      "type": it.get("type", "image"), "figure": g["token"],
                                      "caption_en": g["caption"], "page": info["page"],
                                      "web_url": info["web_url"]})
        if panels:
            txt = [g["caption"], "", f"{g['kind']} {g['token']} 面板数：{len(panels)}", "-" * 40]
            txt += [f"  [{p['sub'] or '-'}] {p['file']}  page={p['page']}" for p in panels]
            with open(_lp(os.path.join(str(target_images), folder_name, folder_name + ".txt")), "w", encoding="utf-8") as f:
                f.write("\n".join(txt) + "\n")
            cols, wpx = _figure_layout(len(panels))
            figures.append({"folder": folder_name, "kind": g["kind"], "token": g["token"],
                            "caption_en": g["caption"], "panel_count": len(panels),
                            "panels": panels,
                            "layout": {"cols": cols, "width_px": wpx},
                            "layout_html": _figure_layout_html(folder_name, g["token"], panels, wpx),
                            "recommended_width_px": wpx})

    # 2) 表（content_list type=table，md 未收留的）
    tseq = 0
    for it in _ordered:
        if not isinstance(it, dict) or it.get("type") != "table" or not it.get("img_path"):
            continue
        fname = os.path.basename(it["img_path"])
        if fname in consumed or not (images_dir / fname).exists():
            continue
        tc = _cap(it, "table_caption")
        m = re.search(r"table\s*(\d+)", tc, re.I)
        token = m.group(1) if m else str((tseq + 1))
        tseq += 1
        folder_name = "Table" + token
        if folder_name in used_folders:
            folder_name = f"Table{token}_{tseq}"
        used_folders.add(folder_name)
        info = copy_panel(fname, folder_name)
        if not info:
            continue
        content = [f"Type: table", f"Number: {folder_name}", ""]
        if tc:
            content += [f"Caption: {tc}", ""]
        body = it.get("table_body", "")
        if body:
            content += ["Table Content:", "-" * 40, _html_table_to_text(body), "-" * 40]
        with open(_lp(os.path.join(str(target_images), folder_name, folder_name + ".txt")), "w", encoding="utf-8") as f:
            f.write("\n".join(content) + "\n")
        manifest_imgs.append({"orig": it.get("img_path"), "filename": fname, "folder": folder_name,
                              "group": folder_name, "sub": None, "type": "table", "figure": token,
                              "caption_en": tc, "page": it.get("page_idx"), "web_url": info["web_url"]})
        figures.append({"folder": folder_name, "kind": "Table", "token": token, "caption_en": tc,
                        "panel_count": 1, "panels": [info],
                        "layout": {"cols": 1, "width_px": 520},
                        "layout_html": _figure_layout_html(folder_name, token, [info], 520),
                        "recommended_width_px": 520})

    # 3) 剩余未归档散图（无 md 图注、也非表）→ unknown_，避免污染正图号
    assigned = {m["filename"] for m in manifest_imgs}
    useq = 0
    for f in sorted(os.listdir(images_dir)):
        if not f.lower().endswith(IMAGE_EXTS) or f in assigned:
            continue
        useq += 1
        folder_name = f"unknown_{useq:02d}"
        info = copy_panel(f, folder_name)
        if not info:
            continue
        with open(_lp(os.path.join(str(target_images), folder_name, folder_name + ".txt")), "w", encoding="utf-8") as fh:
            fh.write(f"Original image: {f}\nNote: 未被任何 Fig./Table 图注归并，疑似未识别子图/装饰图。\n")
        manifest_imgs.append({"orig": f"images/{f}", "filename": f, "folder": folder_name,
                              "group": folder_name, "sub": None, "type": idx.get(f, {}).get("type", "unknown"),
                              "figure": None, "caption_en": "", "page": idx.get(f, {}).get("page_idx"),
                              "web_url": info["web_url"]})

    # 4) 清洗全文
    cleaned_info = {"ok": False}
    if md_path and md_path.exists():
        content = open(_lp(md_path), "r", encoding="utf-8").read()
        m = re.search(r"^#+\s+references?.*$", content, re.IGNORECASE | re.MULTILINE)
        cleaned = content[:m.start()].strip() if m else content.strip()
        tdir = Path(output_dir) if output_dir else folder
        os.makedirs(_lp(str(tdir)), exist_ok=True)
        with open(_lp(tdir / "00_full_cleaned.md"), "w", encoding="utf-8") as f:
            f.write(cleaned)
        cleaned_info = {"ok": True, "found_ref": bool(m), "chars": len(cleaned)}

    tdir = Path(output_dir) if output_dir else folder
    nfig = sum(1 for x in figures if x["kind"] == "Figure")
    ntab = sum(1 for x in figures if x["kind"] == "Table")
    stats = {"figures": nfig, "tables": ntab, "unknown": useq,
             "image_files": len(manifest_imgs),
             "multi_panel_figures": [x["folder"] for x in figures if x["panel_count"] > 1]}
    with open(_lp(tdir / "00_images_map.json"), "w", encoding="utf-8") as f:
        json.dump({"paper": folder.name, "out_dir": str(tdir.resolve()),
                   "content_list": (os.path.basename(json_path) if json_path else None),
                   "counts": stats, "figures": figures, "images": manifest_imgs},
                  f, ensure_ascii=False, indent=2)
    return {"ok": True, "stats": stats, "cleaned": cleaned_info,
            "manifest": str((tdir / "00_images_map.json").resolve()), "out_dir": str(tdir.resolve())}


def _iter_inputs(input_path, out_root):
    p = Path(input_path)
    if p.is_file() and p.suffix.lower() == ".zip":
        yield ("zip", str(p), p.stem, out_root)
    elif p.is_dir():
        zips = sorted(p.glob("*.zip"))
        if zips:
            for z in zips:
                yield ("zip", str(z), z.stem, out_root)
        else:
            yield ("dir", str(p), p.name, out_root)


def main(argv):
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "用法: mineru_archive.py <zip|论文目录|含zip目录> [out_root]"}, ensure_ascii=False))
        return 1
    input_path = argv[1]
    out_root = argv[2] if len(argv) > 2 else (str(Path(input_path).parent) if Path(input_path).is_file() else input_path)
    os.makedirs(_lp(out_root), exist_ok=True)
    results = []
    for kind, src, name, oroot in _iter_inputs(input_path, out_root):
        if kind == "zip":
            _safe_extract(src, os.path.join(oroot, name))
            paper_dir = _locate_paper_root(os.path.join(oroot, name))
        else:
            paper_dir = src
        r = archive_paper(paper_dir); r["name"] = name; results.append(r)
    print(json.dumps({"ok": all(r.get("ok") for r in results), "results": results}, ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
