#!/usr/bin/env python3
"""文件检索/编辑工具 — search_files / find_files / edit_file（纯实现，不注册）

给 LLM 的三个工具，返回**已排好版的文本**（不是 JSON），模型可直接读：
  - search_files  正则搜文件内容：files_with_matches / content / count 三态，
                  支持 file_glob 过滤、上下文行（context）、limit/offset 分页
  - find_files    按文件名 glob 检索，按 mtime 新→旧排序
  - edit_file     old_string → new_string 替换，带唯一匹配校验（不唯一就报错，绝不猜）

路径与权限沿用 file_tools._resolve_safe_path：相对路径以 workspace/ 为基准，
当前权限模式决定能看/能改哪些目录。检索固定跳过 .git / node_modules / __pycache__ / dist / _backup。
"""
import os
import re
import fnmatch
import difflib
from datetime import datetime

from tools import file_tools

# 检索时固定跳过的目录（依赖/构建产物/版本库，搜进去只有噪声）
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "_backup"}
# 单文件超过此大小不搜（多为数据/日志，正则全扫代价过高）
MAX_FILE_BYTES = 2_000_000


def _resolve(path: str, write: bool = False) -> str:
    """按项目权限规则把路径解析成绝对路径（相对路径以 workspace/ 为基准）。"""
    return file_tools._resolve_safe_path(path or ".", write=write)


def _rel(path: str, base: str) -> str:
    """显示用相对路径，统一正斜杠。"""
    try:
        r = os.path.relpath(path, base)
    except ValueError:  # 跨盘符
        r = path
    return r.replace("\\", "/")


def _glob_hit(pattern: str, name: str, dirpath: str, root: str) -> bool:
    """file_glob 过滤：不含分隔符按文件名匹配（*.py），含分隔符按相对路径匹配（src/*.py）。"""
    if "/" not in pattern and "\\" not in pattern:
        return fnmatch.fnmatch(name, pattern)
    return fnmatch.fnmatch(_rel(os.path.join(dirpath, name), root), pattern)


def _read_text(path: str):
    """读文本文件；含 NUL 视为二进制，返回 None（不搜）。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return None if "\x00" in text else text


def _ctx_block(rel: str, lines: list, i: int, context: int) -> str:
    """第 i 行（0-indexed）命中的上下文块，grep -C 风格：`行号:内容` 命中、`行号-内容` 上下文。"""
    lo = max(0, i - context)
    hi = min(len(lines), i + context + 1)
    out = [f"{rel}:{i + 1}"]
    for j in range(lo, hi):
        sep = ":" if j == i else "-"
        out.append(f"{j + 1}{sep}{lines[j]}")
    return "\n".join(out)


def _slice_page(items: list, limit: int, offset: int):
    """分页切片：返回 (本页, 未显示数, 下一页 offset)。"""
    shown = items[offset:offset + limit]
    return shown, max(0, len(items) - offset - len(shown)), offset + len(shown)


async def search_files(pattern: str, path: str = ".", file_glob: str = None,
                       output_mode: str = "files_with_matches", context: int = 0,
                       limit: int = 50, offset: int = 0, order: str = "modified") -> str:
    """用正则搜索文件内容。

    Args:
        pattern: 正则表达式（Python re 语法）
        path: 搜索起点，文件或目录；相对路径以 workspace/ 为基准
        file_glob: 只搜匹配该 glob 的文件（如 "*.py"、"src/*.py"）；None 表示全部
        output_mode: 结果形态
            files_with_matches → 每个命中文件一行（路径）
            content            → 逐条命中行，带行号与上下文
            count              → 每个文件一行 `路径: 命中行数`
        context: content 模式下每条命中前后附带的上下文行数
        limit: 本页最多返回多少条（文件数 / 命中行数）
        offset: 跳过的条数，用于翻页
        order: 文件遍历顺序，modified=按 mtime 新→旧；discovery=目录遍历顺序
    """
    if output_mode not in ("files_with_matches", "content", "count"):
        return f"output_mode 只能是 files_with_matches / content / count，收到 {output_mode!r}"
    if limit < 1:
        return "limit 必须 >= 1"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"正则表达式非法：{e}（pattern={pattern!r}）"

    root = _resolve(path)
    if not os.path.exists(root):
        return f"路径不存在：{path}"

    if os.path.isfile(root):
        files, base = [root], os.path.dirname(root)
    else:
        files, base = _iter_files(root, file_glob, order), root

    units = []  # 分页单位：files_with_matches/count → 每文件一条；content → 每命中行一条
    for fp in files:
        text = _read_text(fp)
        if text is None:
            continue
        lines = text.splitlines()
        hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
        if not hits:
            continue
        rp = _rel(fp, base)
        if output_mode == "files_with_matches":
            units.append(rp)
        elif output_mode == "count":
            units.append(f"{rp}: {len(hits)}")
        else:
            units.extend(_ctx_block(rp, lines, i, context) for i in hits)

    head = (f"search_files {pattern!r} @ {root.replace(os.sep, '/')} "
            f"[mode={output_mode}, file_glob={file_glob}, context={context}, order={order}]")
    if not units:
        return f"{head}\n共 0 条命中（已扫描 {len(files)} 个文件）。"

    shown, remaining, next_offset = _slice_page(units, limit, offset)
    if not shown:
        return f"{head}\n共 {len(units)} 条，offset={offset} 已越界。"
    body = "\n".join(shown)
    foot = (f"还有 {remaining} 条未显示，用 offset={next_offset} 继续。"
            if remaining else "已显示全部。")
    return (f"{head}\n共 {len(units)} 条命中，显示第 {offset + 1}-{offset + len(shown)} 条：\n"
            f"{body}\n{foot}")


async def find_files(pattern: str, path: str = ".", limit: int = 50, offset: int = 0) -> str:
    """按文件名 glob 检索文件，结果按 mtime 新→旧排序。

    Args:
        pattern: 文件名 glob（如 "*.py"、"*config*"）；含 "/" 时按相对路径匹配
        path: 检索起点目录；相对路径以 workspace/ 为基准
        limit: 本页最多返回多少条
        offset: 跳过的条数，用于翻页
    """
    if limit < 1:
        return "limit 必须 >= 1"
    root = _resolve(path)
    if not os.path.isdir(root):
        return f"目录不存在：{path}"

    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not _glob_hit(pattern, name, dirpath, root):
                continue
            p = os.path.join(dirpath, name)
            try:
                mtime, size = os.path.getmtime(p), os.path.getsize(p)
            except OSError:
                continue
            rows.append((mtime, _rel(p, root), size))
    rows.sort(key=lambda r: -r[0])

    head = f"find_files {pattern!r} @ {root.replace(os.sep, '/')}"
    if not rows:
        return f"{head}\n共 0 个文件。"
    shown, remaining, next_offset = _slice_page(rows, limit, offset)
    if not shown:
        return f"{head}\n共 {len(rows)} 个文件，offset={offset} 已越界。"
    body = "\n".join(
        f"{datetime.fromtimestamp(m).strftime('%Y-%m-%d %H:%M')}  {s:>10}  {r}"
        for m, r, s in shown)
    foot = (f"还有 {remaining} 个未显示，用 offset={next_offset} 继续。"
            if remaining else "已显示全部。")
    return (f"{head}\n共 {len(rows)} 个文件，显示第 {offset + 1}-{offset + len(shown)} 个"
            f"（按修改时间新→旧）：\n{body}\n{foot}")


async def edit_file(file_path: str, old_string: str, new_string: str,
                    replace_all: bool = False) -> str:
    """把文件里的 old_string 替换成 new_string。

    唯一匹配校验：命中 0 次报「未找到」并给出最相近的片段；命中 >1 次且
    replace_all=False 时报错并列出出现次数与行号 —— 绝不猜替换哪一处。
    换行风格（CRLF/LF）按文件现有风格保持。

    Args:
        file_path: 目标文件；相对路径以 workspace/ 为基准
        old_string: 要被替换的原文，必须是文件里的精确文本
        new_string: 替换后的新文本
        replace_all: True 时替换所有命中，False（默认）要求唯一匹配
    """
    if not old_string:
        return "old_string 为空，无法定位要替换的内容。"
    if old_string == new_string:
        return "old_string 与 new_string 相同，无需修改。"
    full = _resolve(file_path, write=True)
    if not os.path.isfile(full):
        return f"文件不存在：{file_path}"

    with open(full, "r", encoding="utf-8", errors="replace", newline="") as f:
        content = f.read()
    nl = "\r\n" if "\r\n" in content else "\n"
    old = _to_nl(old_string, nl)
    new = _to_nl(new_string, nl)

    spans = [m.start() for m in re.finditer(re.escape(old), content)]
    n = len(spans)
    if n == 0:
        msg = f"未找到 old_string（{file_path}）。"
        hint = _closest_snippet(content, old_string)
        return msg + ("\n最相近的片段：\n" + hint if hint else " 文件中没有相似内容。")
    if n > 1 and not replace_all:
        line_nos = [content.count("\n", 0, s) + 1 for s in spans]
        cap = 5
        listed = "、".join(f"L{x}" for x in line_nos[:cap])
        more = f" 等（还有 {n - cap} 处未列出）" if n > cap else ""
        return (f"old_string 在 {file_path} 中出现 {n} 次（{listed}{more}），无法确定替换哪一处。"
                f"请带上更多上下文让匹配唯一，或传 replace_all=True 全部替换。")

    new_content = content.replace(old, new) if replace_all else \
        content[:spans[0]] + new + content[spans[0] + len(old):]
    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(new_content)

    line_nos = [content.count(nl, 0, s) + 1 for s in spans]
    return (f"已替换 {n} 处（{file_path}），涉及行：{', '.join(map(str, line_nos))}，"
            f"换行风格 {nl.replace(chr(13), 'CR').replace(chr(10), 'LF')}。")


def _iter_files(root: str, file_glob: str, order: str) -> list:
    """遍历 root 下的可搜文件：跳过 SKIP_DIRS 与超大文件，file_glob 过滤。"""
    cands = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if file_glob and not _glob_hit(file_glob, name, dirpath, root):
                continue
            p = os.path.join(dirpath, name)
            try:
                size, mtime = os.path.getsize(p), os.path.getmtime(p)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            cands.append((p, mtime))
    if order == "modified":
        cands.sort(key=lambda x: -x[1])
    return [p for p, _ in cands]


def _to_nl(s: str, nl: str) -> str:
    """换行归一后转成文件使用的换行风格（LLM 给的通常是 \\n，文件可能是 CRLF）。"""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s if nl == "\n" else s.replace("\n", nl)


def _vis_ws(line: str) -> str:
    """把行首空白可见化（→ = tab，· = 空格）。"""
    body = line.lstrip(" \t")
    return line[:len(line) - len(body)].replace("\t", "→").replace(" ", "·") + body


def _closest_snippet(content: str, old_string: str, max_hits: int = 3, context: int = 2) -> str:
    """与 old_string 首行最相近的若干片段（带行号），供模型改正后重试。"""
    old_lines = old_string.splitlines()
    lines = content.splitlines()
    if not old_lines or not lines:
        return ""
    anchor = old_lines[0].strip() or next((l.strip() for l in old_lines if l.strip()), "")
    if not anchor:
        return ""
    scored = sorted(((difflib.SequenceMatcher(None, anchor, l.strip()).ratio(), i)
                     for i, l in enumerate(lines) if l.strip()), key=lambda x: -x[0])
    top = [s for s in scored if s[0] > 0.3][:max_hits]
    if not top:
        return ""

    parts, seen = [], set()
    for _, idx in top:
        lo = max(0, idx - context)
        hi = min(len(lines), idx + context + 1)
        if (lo, hi) in seen:
            continue
        seen.add((lo, hi))
        parts.append("\n".join(f"{lo + j + 1:4d}| {lines[lo + j]}" for j in range(hi - lo)))
    out = "\n---\n".join(parts)

    best = lines[top[0][1]]
    if best.strip() == anchor and best != old_lines[0]:
        out += ("\n\n疑似空白差异（→ = tab，· = 空格）：\n"
                f"  文件里: {_vis_ws(best)}\n"
                f"  你给的: {_vis_ws(old_lines[0])}")
    return out
