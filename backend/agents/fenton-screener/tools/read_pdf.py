# -*- coding: utf-8 -*-
"""fenton-screener 档案的私有工具：read_pdf —— 读 PDF 的**文本层**（按页）。

为什么需要它：筛选员经常遇到「只有 PDF、还没有 MinerU 解析产物」的文献，
而 read_file 读 PDF 二进制只会得到乱码/报错。本工具用 PyMuPDF(fitz)
只提文本层、按页范围读，并给出总页数 / 每页字符数 / 是否截断 / 接着读哪几页，
让「先读 PDF 判体系」这条路走得通。

约定（agent.profiles 的加载器识别）：
  · 模块级 `TOOLS = [tool_obj, ...]`（也可以改用 `def register(registry): ...`）
  · 只读操作；不做任何写盘
"""
from __future__ import annotations

import json
import os
import re

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# 默认一次读多少页（不给 pages 时）；页数只是「默认窗口」，真正的封顶是字符数
DEFAULT_MAX_PAGES = 12
# 一页少于此字符数，基本可判定为「图页/扫描页」（无文本层）
THIN_PAGE_CHARS = 100
# 全篇少于这么多字符 → 判定为扫描件（无可用文本层）
SCANNED_TOTAL_CHARS = 300


class ReadPdfParams(BaseModel):
    path: str = Field(description="PDF 文件路径；相对路径按当前工作区域解析（与 read_file 同一套路由与权限校验）")
    pages: str = Field(default="", description="页范围，如 '1-12' / '3' / '1,4,7'；留空 = 从第 1 页起读 max_pages 页（默认 12 页）")
    max_pages: int = Field(default=0, description="pages 留空时读多少页；0 = 默认 12 页")
    max_chars: int = Field(default=0, description="本次返回的字符上限；0 = 用当前档案的上限（本档案为 10 万）")


def _parse_pages(spec: str, total: int, max_pages: int) -> list[int]:
    """把 '1-3,7' 解析成页码列表（1 基，去重、越界丢弃、保持出现顺序）。"""
    spec = (spec or "").strip()
    if not spec:
        n = int(max_pages or 0) or DEFAULT_MAX_PAGES
        return list(range(1, min(total, max(1, n)) + 1))
    raw: list[int] = []
    for part in re.split(r"[,\s;]+", spec):
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–~至]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            raw.extend(range(a, b + 1))
        elif part.isdigit():
            raw.append(int(part))
    seen: set[int] = set()
    out: list[int] = []
    for p in raw:
        if 1 <= p <= total and p not in seen:
            seen.add(p)
            out.append(p)
    return out


async def _read_pdf(path: str = "", pages: str = "", max_pages: int = 0, max_chars: int = 0):
    from tools import file_tools as _ft

    path = (path or "").strip()
    if not path:
        return json.dumps({"error": "path 不能为空"}, ensure_ascii=False)
    if not path.lower().endswith(".pdf"):
        return json.dumps({"error": f"这不是 PDF：{path}（读文本文件请用 read_file）"}, ensure_ascii=False)
    try:
        full = _ft._resolve_safe_path(path, write=False)
    except Exception as e:
        return json.dumps({"error": f"路径不可访问：{e}"}, ensure_ascii=False)
    if not os.path.isfile(full):
        return json.dumps({"error": f"文件不存在：{path}"}, ensure_ascii=False)
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        return json.dumps({"error": f"PyMuPDF 不可用：{type(e).__name__}: {e}；请在项目 venv 里装 pymupdf"},
                          ensure_ascii=False)

    try:
        doc = fitz.open(full)
    except Exception as e:
        return json.dumps({"error": f"PDF 打不开：{type(e).__name__}: {str(e)[:150]}"}, ensure_ascii=False)

    try:
        total = doc.page_count
        cap = int(max_chars or 0) or _ft.read_file_max_chars()
        want = _parse_pages(pages, total, max_pages)
        if not want:
            return json.dumps({"error": f"页码解析为空或全部越界（总页数 {total}）；pages 例：'1-12'"},
                              ensure_ascii=False)

        chunks: list[str] = []
        chars_by_page: dict[str, int] = {}
        done: list[int] = []
        used = 0
        for pno in want:
            try:
                txt = doc.load_page(pno - 1).get_text("text") or ""
            except Exception as e:
                txt = f"(本页提取失败：{type(e).__name__})"
            txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
            chars_by_page[str(pno)] = len(txt)
            seg = f"\n\n===== [p.{pno}] =====\n{txt}"
            if used + len(seg) > cap:
                if not chunks:                      # 首页就超限：截断后返回，并声明截断
                    seg = seg[:cap]
                    chunks.append(seg)
                    used += len(seg)
                    done.append(pno)
                break
            chunks.append(seg)
            used += len(seg)
            done.append(pno)

        body = "".join(chunks)
        left = [p for p in want if p not in done]
        thin = [p for p, c in chars_by_page.items() if c < THIN_PAGE_CHARS and p in {str(d) for d in done}]
        total_chars = sum(chars_by_page.values())

        if total_chars < SCANNED_TOTAL_CHARS:
            note = ("本 PDF 几乎提取不到文本层（可能是扫描件或纯图 PDF）——"
                    "不要据此判体系，报 partial + MISSING 说明需 OCR / MinerU。")
        elif thin:
            note = (f"第 {', '.join(thin)} 页几乎无文字（<{THIN_PAGE_CHARS} 字符），"
                    f"应为图页；图注文字通常也能在正文找到，不要靠读图立论。")
        else:
            note = "文本层正常。"
        if left:
            note += f" 本次因字符上限停在 p.{done[-1] if done else 0}；续读 pages='{left[0]}-{left[-1]}'。"

        out = {
            "ok": True,
            "path": path,
            "total_pages": total,
            "pages_read": done,
            "chars_by_page": chars_by_page,
            "total_chars": total_chars,
            "returned_chars": len(body),
            "truncated": bool(left),
            "next_pages": f"{left[0]}-{left[-1]}" if left else "",
            "note": note,
            "content": body,
        }
        return json.dumps(out, ensure_ascii=False)
    finally:
        try:
            doc.close()
        except Exception:
            pass


TOOLS = [
    StructuredTool.from_function(
        name="read_pdf",
        description=(
            "读 PDF 的**文本层**（按页），用于「只有 PDF、没有解析产物」时读正文。\n\n"
            "WHEN to use: 论文夹里没有 full.md / datamining/00_full_cleaned.md，只有 *_origin.pdf 或原始 PDF 时；"
            "或需要核对 markdown 与 PDF 原文的差异时。\n\n"
            "WHEN NOT: 有 markdown 产物时优先 read_file（更快更干净）；要看图/表图像 → 本工具读不到（只出文字）。\n\n"
            "HOW: 传 path，必要时给 pages（'1-12'）；返回体带 total_pages / chars_by_page / truncated / "
            "next_pages，据此按页续读。全篇 total_chars 极小 = 扫描件，需 OCR。"
        ),
        coroutine=_read_pdf,
        args_schema=ReadPdfParams,
    ),
]
