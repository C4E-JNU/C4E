# -*- coding: utf-8 -*-
"""reader 档案的私有工具：fulltext_read —— 一次通读整篇正文。

为什么需要它：read_file 的单次上限是主智能体的全局参数（20000 字符），
分页续读对「通读一篇文献」这种任务很别扭（10 页要调 10 次）。
本工具按**当前档案的上限**一次读入（reader 档案 = 50000），并附标题提纲。

约定（agent.profiles 的加载器识别）：
  · 模块级 `TOOLS = [tool_obj, ...]`   —— 也可以改用 `def register(registry): ...`
  · 工具只做只读操作；写类工具必须由档案里的 allow_write 显式开启
"""
from __future__ import annotations

import json
import os
import re

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class FulltextReadParams(BaseModel):
    path: str = Field(description="文件路径；相对路径按当前工作区域解析（与 read_file 同一套路由与权限校验）")
    max_chars: int = Field(default=0, description="本次返回的字符上限；0 = 用当前档案的上限（reader 档案为 50000）")
    with_outline: bool = Field(default=True, description="是否附上 Markdown 标题（#/##/###）提纲，便于定位章节")


def _read_sync(full_path: str) -> str:
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


async def _fulltext_read(path: str = "", max_chars: int = 0, with_outline: bool = True):
    from tools import file_tools as _ft

    path = (path or "").strip()
    if not path:
        return json.dumps({"error": "path 不能为空"}, ensure_ascii=False)
    try:
        full = _ft._resolve_safe_path(path, write=False)
    except Exception as e:
        return json.dumps({"error": f"路径不可访问：{e}"}, ensure_ascii=False)
    if not os.path.isfile(full):
        return json.dumps({"error": f"文件不存在：{path}"}, ensure_ascii=False)

    try:
        text = _read_sync(full)
    except Exception as e:
        return json.dumps({"error": f"读取失败：{type(e).__name__}: {e}"}, ensure_ascii=False)

    cap = int(max_chars or 0) or _ft.read_file_max_chars()
    body = text[:cap]
    out = {
        "ok": True,
        "path": path,
        "total_chars": len(text),
        "returned_chars": len(body),
        "truncated": len(text) > len(body),
        "note": ("已整篇返回。" if len(text) <= len(body)
                 else f"超过本次上限 {cap} 字符，只返回了开头；"
                      f"请让主控按章节分段派发，或改读更小的分片文件。"),
    }
    if with_outline:
        heads = [ln.strip() for ln in text.splitlines()
                 if re.match(r"^#{1,4}\s+\S", ln.strip())][:80]
        if heads:
            out["outline"] = heads
    out["content"] = body
    return json.dumps(out, ensure_ascii=False)


TOOLS = [
    StructuredTool.from_function(
        name="fulltext_read",
        description=(
            "一次读入整篇正文（含标题提纲），单次上限由当前子智能体档案决定（reader 档案 = 5 万字符）。\n\n"
            "WHEN to use: 精读一篇已归档文献的正文（如 datamining/00_full_cleaned.md、翻译稿 markdown），"
            "需要通读而不是分页续读时。\n\n"
            "WHEN NOT: 只有一处细节要看 → 用 read_file 精确分页更省；读巨型 JSON → 别用本工具。\n\n"
            "HOW: 传 path 即可；返回体带 total_chars / returned_chars / truncated，据此判断是否整篇读完。"
        ),
        coroutine=_fulltext_read,
        args_schema=FulltextReadParams,
    ),
]
