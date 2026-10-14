# -*- coding: utf-8 -*-
"""fenton-image 档案的私有工具：pack_scan —— 一次拿全任务包上下文（只读）。

为什么需要它（本档案参数被刻意压紧：单次读入 8000 字符 / 仅 6 轮）：
  「读 task.json + 列包内文件 + 找归档 FigureN 文件夹 + 读图注 txt + 核对面板数」
  原本要 4~5 次 read_file / list_files，轮次直接烧光，留给"读图估读"的轮次所剩无几。
  本工具把这几步合成**一次**调用，并把**面板字母核对所需的文字材料**一次给全。
  它**不读像素、不写文件**（图像数据仍由 read_file 直读 image.jpg 完成）。

约定（agent.profiles 的加载器识别）：模块级 TOOLS = [tool_obj, ...]
"""
from __future__ import annotations

import json
import os
import re

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class PackScanParams(BaseModel):
    pack_dir: str = Field(description="任务包目录（内含 image.jpg 与 task.json）")
    max_txt_chars: int = Field(default=3500, description="每个 FigureN.txt 图注最多返回多少字符")


def _read_text(path: str, cap: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            t = f.read()
    except Exception as e:                                    # noqa: BLE001
        return f"<<读取失败 {type(e).__name__}: {e}>>"
    if len(t) <= cap:
        return t
    return t[:cap] + f"\n…（已截断，原文 {len(t)} 字符）"


def _panel_lines(txt: str):
    """抓 FigureN.txt 的有序面板清单：形如 `[-] xxx.jpg page=3` / 含 `(a)` 字母的行。"""
    rows = []
    for ln in txt.splitlines():
        s = ln.strip()
        if not s:
            continue
        m = re.search(r"([\w\-\.]+\.(?:jpg|jpeg|png))", s, re.I)
        if not m:
            continue
        ml = re.search(r"\(([a-hA-H])\)", s)
        rows.append({"line": s[:160], "file": m.group(1),
                     "panel_letter": ml.group(1).lower() if ml else ""})
    return rows


async def _pack_scan(pack_dir: str = "", max_txt_chars: int = 3500):
    from tools import file_tools as _ft

    d = (pack_dir or "").strip()
    if not d:
        return json.dumps({"error": "pack_dir 不能为空"}, ensure_ascii=False)
    try:
        base = _ft._resolve_safe_path(d, write=False)
    except Exception as e:                                    # noqa: BLE001
        return json.dumps({"error": f"路径不可访问：{e}"}, ensure_ascii=False)
    if not os.path.isdir(base):
        return json.dumps({"error": f"不是目录：{d}"}, ensure_ascii=False)

    out = {"ok": True, "pack_dir": base, "hint": "第一步就调本工具拿全上下文；随后用 read_file 直读 image.jpg 估读。"}

    # ① 包内文件
    files = []
    for fn in sorted(os.listdir(base)):
        fp = os.path.join(base, fn)
        if os.path.isfile(fp):
            files.append({"name": fn, "bytes": os.path.getsize(fp)})
    out["files"] = files

    # ② task.json
    task = None
    tsk = os.path.join(base, "task.json")
    if os.path.isfile(tsk):
        try:
            task = json.load(open(tsk, encoding="utf-8"))
        except Exception as e:                                # noqa: BLE001
            out["task_error"] = f"task.json 解析失败：{e}"
    if isinstance(task, dict):
        out["task"] = task
        subs = task.get("subfigure_ids") or task.get("subfigures") or []
        if isinstance(subs, str):
            subs = [x for x in re.split(r"[,\s]+", subs) if x]
        out["subfigure_ids"] = subs

    # ③ 归档面板清单（用于图面字母核对）
    arc = ""
    if isinstance(task, dict):
        arc = task.get("images_archive") or ""
        if not arc:
            m = re.search(r"(.*?[\\/])images[\\/]", str(task.get("original_image") or ""))
            if m:
                arc = m.group(0)
    info: dict = {}
    if arc:
        try:
            arc_abs = _ft._resolve_safe_path(arc, write=False)
        except Exception:                                     # noqa: BLE001
            arc_abs = arc
        info["images_archive"] = arc_abs
        fig = ""
        if isinstance(task, dict):
            mm = re.search(r"(Fig(?:ure)?\.?\s*\d+)", str(task.get("figure_id") or ""), re.I)
            if mm:
                fig = re.sub(r"[^A-Za-z0-9]", "", mm.group(1))
        folders = []
        if os.path.isdir(arc_abs):
            names = sorted(os.listdir(arc_abs))
            cands = [os.path.join(arc_abs, x) for x in names
                     if fig and x.lower().startswith(fig.lower())] or \
                    [os.path.join(arc_abs, x) for x in names]
            for c in cands[:6]:
                if not os.path.isdir(c):
                    continue
                jpgs = [f for f in sorted(os.listdir(c)) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
                panel_jpgs = [f for f in jpgs if not re.search(r"(orig|whole|full|原图)", f, re.I)]
                txts = [f for f in sorted(os.listdir(c)) if f.lower().endswith(".txt")]
                row = {"dir": c, "images": jpgs, "panel_image_count": len(panel_jpgs)}
                if txts:
                    t = _read_text(os.path.join(c, txts[0]), int(max_txt_chars or 3500))
                    row["caption_txt_file"] = txts[0]
                    row["caption"] = t
                    row["panel_lines"] = _panel_lines(t)
                folders.append(row)
        info["figure_folders"] = folders

        # ④ 计数核对（纯计数，不读像素）
        n_sub = len(out.get("subfigure_ids") or [])
        n_img = sum(int(r.get("panel_image_count") or 0) for r in folders) or \
            len([f for f in files if f["name"].lower().endswith((".jpg", ".jpeg", ".png"))])
        if n_sub and n_img and n_sub != n_img:
            info["count_hint"] = (
                f"工单子图数 {n_sub} ≠ 归档图数 {n_img}：可能是归档面板顺序 ≠ 图注字母顺序，"
                "或一张整图含多分幅（目录只 1 张 jpg ≠ 缺图）。"
                "**必须读 image.jpg 图面角落的面板字母裁决**，不得凭文件数判定『归档缺子图』。")
        else:
            info["count_hint"] = f"工单子图数 {n_sub}，归档图数 {n_img}（数量一致，仍要看图面字母确认对应关系）"
    out["archive_info"] = info or {
        "note": "task.json 未给 images_archive / original_image，无法定位归档目录；只按包内图与 caption 提取。"}

    return json.dumps(out, ensure_ascii=False)


TOOLS = [
    StructuredTool.from_function(
        name="pack_scan",
        description=(
            "一次拿全任务包上下文（只读）：task.json 全字段 + 包内文件清单 + 该 Figure 归档文件夹的面板清单"
            "与图注文字 + 子图数/图数核对提示。\n\n"
            "WHEN to use: 处理一个任务包时**第一步就调它**——代替 read_file(task.json) + list_files + "
            "翻归档目录 的多次调用。本档案只有 6 轮，省下的轮次留给读图估读。\n\n"
            "WHEN NOT: 要看图面像素 → 用 read_file 直读 image.jpg；本工具不读图、不写文件。\n\n"
            "HOW: 传 pack_dir（任务包目录）。拿返回的 panel_lines / caption 核对"
            "『图面字母 vs subfigure_ids』是否一致；不一致就走归档修正流程。"
        ),
        coroutine=_pack_scan,
        args_schema=PackScanParams,
    ),
]
