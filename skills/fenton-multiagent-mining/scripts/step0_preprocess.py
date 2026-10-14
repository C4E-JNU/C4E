# -*- coding: utf-8 -*-
"""step0_preprocess.py — 步骤二统一入口：补齐 MinerU 产物 → 归档（全文清洗 + 图）。

输入【用户给什么都能接】：论文 PDF 文件 / 论文文件夹 / 批次目录。
输出：每篇 <论文夹>/datamining/ 下
      - 00_full_cleaned.md   正文（截去 References）
      - images/FigureN/...   图：有 PDF 时优先用 content_list 的 bbox 从 PDF 裁剪，
                             无 PDF 时才退回 MinerU 散图归档
      - images/FigureN/FigureN.txt  英文图注 + 页码/bbox
用法：python step0_preprocess.py <pdf|论文夹|批次目录>
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
    """修复原版缺陷：优先取 *_content_list.json（MinerU 索引），
    避免误取文件夹里其他 json（如 mining_schema）导致全部归档为 unknown"""
    cands = sorted(f for f in os.listdir(folder) if f.endswith("_content_list.json"))
    if cands:
        return os.path.join(folder, cands[0])
    return _orig_find(folder)


pp.find_json_file = find_json_list
from paper_preprocessor import clean_full_md, process_image_match  # noqa: E402


def papers_in(root):
    """单篇文件夹（自身含 full.md+images）或批次目录（遍历子文件夹）"""
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
    s0b.main(root)  # 缺 MinerU 产物才动作（含按需调 MinerU）；产物齐全时秒过
    papers = papers_in(root)
    if not papers:
        print("未找到含 full.md + images/ 的文献文件夹")
        return
    for paper in papers:
        wf = os.path.join(paper, "datamining")
        os.makedirs(wf, exist_ok=True)
        # 1) 图：优先 PDF 裁剪（块完整、不依赖散图）；失败则退回 MinerU 散图归档
        clip = s0c.clip_paper(paper) if s0c._origin_pdf(paper) else None
        if not (clip and clip.get("figures")):
            img = process_image_match(paper, output_dir=Path(wf))
            st = img.get("stats", {})
            print(f'  退回散图归档 {"OK" if img.get("ok") else "FAIL"} '
                  f'{img.get("processed", 0)}/{img.get("total", 0)} '
                  f'(Fig {st.get("figure", 0)} / Table {st.get("table", 0)} / unknown {st.get("unknown", 0)})')
        # 2) 全文清洗 → 00_full_cleaned.md（覆盖）
        cln = clean_full_md(paper, output_dir=Path(wf))
        old_md = os.path.join(wf, "full_cleaned.md")
        if os.path.exists(old_md):
            shutil.move(old_md, os.path.join(wf, "00_full_cleaned.md"))
        nsub = len(os.listdir(os.path.join(wf, "images"))) if os.path.isdir(os.path.join(wf, "images")) else 0
        print(f'  00_full_cleaned.md {"OK" if cln.get("ok") else "FAIL"} {cln.get("chars", 0)} 字符 | 图目录 {nsub}')


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not arg:
        print("用法：python step0_preprocess.py <pdf|论文夹|批次目录>")
        sys.exit(1)
    main(arg)
