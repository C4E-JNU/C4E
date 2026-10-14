# -*- coding: utf-8 -*-
"""pdf_extract_figures.py <batch_dir> — 用 content_list 的 bbox+caption 从 origin PDF 裁图归档。
场景：MinerU 解析缺 images/ 或归档 FigureN 与正文图号对不上时，按 content_list 的
type=image/table 条目逐个裁剪，按 caption 编号命名 FigureN/TableN，与 full.md 引用解耦。
"""
import fitz  # PyMuPDF
import json
import os
import re
import sys


def cap_token(caption):
    if isinstance(caption, list):
        caption = caption[0] if caption else ""
    caption = caption or ""
    m = re.search(r"(?i)\b(fig(?:ure)?\.?|table|scheme|slika)\s*(\d+)", caption)
    if not m:
        return None
    kind = "Table" if m.group(1).lower().startswith("tab") else "Figure"
    return f"{kind}{m.group(2)}"


def main(root):
    n_total = 0
    for p in sorted(os.listdir(root)):
        paper = os.path.join(root, p)
        if not os.path.isdir(paper):
            continue
        clp = [f for f in os.listdir(paper) if f.endswith("_content_list.json")]
        pdfs = [f for f in os.listdir(paper) if f.endswith("_origin.pdf")]
        if not clp or not pdfs:
            continue
        cl = json.load(open(os.path.join(paper, clp[0]), encoding="utf-8"))
        if not isinstance(cl, list):
            continue
        # 图注兜底：content_list 无 caption 时，从 full.md 按文档序抽 Fig/Table/Slika N 序列
        md = ""
        for cand in ("00_full_cleaned.md", "full_cleaned.md", "full.md"):
            mp = os.path.join(paper, cand)
            if os.path.exists(mp):
                md = open(mp, encoding="utf-8", errors="ignore").read()
                break
        md_toks = [f"Figure{m.group(1)}" if not m.group(2) else f"Table{m.group(1)}"
                   for m in re.finditer(r"(?im)^\s*(?:fig(?:ure)?\.?|slika)\s*(\d+)|^\s*table\s*(\d+)", md)]
        doc = fitz.open(os.path.join(paper, pdfs[0]))
        img_dir = os.path.join(paper, "images")
        made = 0
        for it in cl:
            if not isinstance(it, dict) or it.get("type") != "image":
                continue
            pages = it.get("page_idx")
            bbox = it.get("bbox")
            try:
                pages = int(pages)
            except (TypeError, ValueError):
                continue
            if isinstance(bbox, str):
                try:
                    bbox = json.loads(bbox)
                except Exception:
                    continue
            if pages is None or not bbox:
                continue
            if not (isinstance(pages, int) and 0 <= pages < len(doc)):
                continue
            page = doc[pages]
            x0, y0, x1, y1 = [float(v) for v in bbox]
            # content_list 旧版 bbox 为 1000 归一化坐标
            if x1 <= 1.0 or y1 <= 1.0 or x1 > page.rect.width * 1.5:
                x0, x1 = x0 / 1000 * page.rect.width, x1 / 1000 * page.rect.width
                y0, y1 = y0 / 1000 * page.rect.height, y1 / 1000 * page.rect.height
            clip = fitz.Rect(max(0, x0 - 3), max(0, y0 - 3),
                             min(page.rect.width, x1 + 3), min(page.rect.height, y1 + 3))
            tok = cap_token(it.get("img_caption"))
            if not tok:
                if md_toks:
                    tok = md_toks.pop(0)
                else:
                    continue
            folder = os.path.join(img_dir, tok)
            os.makedirs(folder, exist_ok=True)
            jpgs = [f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".png"))]
            fn = f"{tok}_{pages:02d}_{int(x0)}_{int(y0)}.jpg" if jpgs else "figure.jpg"
            if not jpgs:
                fn = "figure.jpg"
            dst = os.path.join(folder, fn)
            if not os.path.exists(dst):
                try:
                    pix = page.get_pixmap(clip=clip, dpi=200)
                    pix.save(dst)
                    made += 1
                except Exception:
                    # 部分 JPEG 流（DNL）MuPDF 存不了 → 回退 PNG
                    try:
                        dst_png = dst[:-4] + ".png"
                        pix = page.get_pixmap(clip=clip, dpi=200)
                        pix.save(dst_png)
                        made += 1
                    except Exception:
                        pass
        # 补写/更新 FigureN.txt 图注
        for it in cl:
            if not isinstance(it, dict) or it.get("type") != "image":
                continue
            tok = cap_token(it.get("img_caption"))
            if not tok:
                continue
            folder = os.path.join(img_dir, tok)
            if os.path.isdir(folder) and not os.path.exists(os.path.join(folder, tok + ".txt")):
                cap = it.get("img_caption")
                if isinstance(cap, list):
                    cap = " ".join(str(c) for c in cap)
                with open(os.path.join(folder, tok + ".txt"), "w", encoding="utf-8") as tf:
                    tf.write(str(cap or tok))
        if made:
            print(f"{p[:40]:42s} PDF补提 {made} 图")
            n_total += made
    print("合计补提", n_total)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\B17")
