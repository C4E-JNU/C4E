# -*- coding: utf-8 -*-
"""gen_package_prompts.py — 步骤③收尾：为每个任务包生成 prompt.txt（填充 XY/BASIC 模板）

skill 自包含原则：模板从本文件所在目录的 ../prompts/ 导入，不依赖 workflow_v5 主目录。
用法：python gen_package_prompts.py <批次根目录>
"""
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "prompts"))
from image_extraction import XY_TASK_TEMPLATE, BASIC_TASK_TEMPLATE

root = sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test3e"

n = 0
for tj in sorted(glob.glob(os.path.join(root, "*", "datamining", "02b_tasks", "*", "T*", "task.json"))):
    pkg = os.path.dirname(tj)
    task = json.load(open(tj, encoding="utf-8"))
    paper_zcm = os.path.dirname(os.path.dirname(pkg))  # .../datamining
    pdfs = [f for f in os.listdir(os.path.dirname(paper_zcm)) if f.endswith("_origin.pdf")]
    tpl = XY_TASK_TEMPLATE if task["mode"] == "xy" else BASIC_TASK_TEMPLATE
    txt = tpl.format(
        image_file=os.path.join(pkg, "image.jpg"),
        task_json=json.dumps(task, ensure_ascii=False, indent=1),
        images_archive=os.path.join(paper_zcm, "images"),
        pdf_path=os.path.join(os.path.dirname(paper_zcm), pdfs[0]) if pdfs else "(PDF 缺失，跳过 PDF 兜底)",
        output_path=os.path.join(pkg, "03_result.json"),
    )
    with open(os.path.join(pkg, "prompt.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    n += 1
print(f"generated {n} prompt.txt")
