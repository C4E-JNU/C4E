"""
文件读写工具 — 在安全白名单目录内操作文件
+ 文件上传管理 + 笔记 CRUD
"""
import json
import os
import uuid
import shutil
import threading
from datetime import datetime

from tools import work_areas as _work_areas

# 单次读取/注入返回给模型的正文字符封顶：防止长行 JSON / 超大文档一次撑爆模型上下文
# 这是**主智能体默认值**；子智能体档案可独立覆盖（backend/agents/<name>/agent.json 的
# params.read_file_max_chars），由 contextvar 提供 —— 用户 2026-10-04 定：档案不与主智能体共用参数。
READ_FILE_MAX_CHARS = 20000


def read_file_max_chars() -> int:
    """当前生效的单次读取字符上限（子智能体档案优先，缺省 READ_FILE_MAX_CHARS）。"""
    try:
        from agent import profiles as _pf
        v = _pf.current_read_file_max_chars()
        if v:
            return int(v)
    except Exception:
        pass
    return READ_FILE_MAX_CHARS
# 历史拼装时，单条消息与总上下文的字符预算（1 token ≈ 2~3 汉字 / 4 英文，留足安全余量）
HISTORY_MSG_MAX_CHARS = 8000
HISTORY_TOTAL_MAX_CHARS = 300000

# ── 图像内联（image_read 通道）────────────────────────────
# 单张图片 dataURL 上限（字符）。超限不内联，只回 image_too_large。
# 可用环境变量 C4EAI_IMAGE_INLINE_MAX_CHARS 覆盖。
#
# 为什么是 1600000（2026-10-05 实测，本地 llama-server / Qwen3.6-35B-A3B，n_ctx=262144）：
#   · 视觉 token 只与**像素数**成正比（1 token ≈ 1024 px = 32×32 patch），与 JPEG 质量、
#     与字节数**无关**：1451×1283 分别用 q95 JPEG（442 KB）与 PNG 无损（1271 KB）内联，
#     实测都是 1820 prompt_tokens。→ 为省 token 而降质量/缩尺寸是白丢信息，保质量不花钱。
#   · 模型侧像素上限约 4 M px（4000×3000 被内部缩到 ≈4030 token）；本流水线最大图
#     ≈1.9 M px，全在其内 → 原图直通即可，重编码纯属损失。
#   · 请求体：956 KB dataURL（0.93 MB body）实测 0.9 s 通过；1271 KB PNG 亦通过。
# 旧值 200000（≈150 KB 原图）会把 188 KB / 314 KB 的整图挡在门外（子智能体看不到图）。
IMAGE_INLINE_MAX_CHARS = int(os.environ.get("C4EAI_IMAGE_INLINE_MAX_CHARS") or 1600000)
# 工具产出图片的形态（由 agent 每请求设置）：
#   "blocks" → 内容块 [{"type":"text",...},{"type":"image_url",...}]
#              · LangChain/LangGraph 原生就能吃：list[dict] 块经
#                langchain_core.tools.base._format_output → _normalize_message_content
#                装进 ToolMessage.content（type 须在 TOOL_MESSAGE_BLOCK_TYPES 内），
#                再由 langchain_openai._sanitize_chat_completions_content 原样发到 wire。
#              · custom 引擎没有这层框架管道，由 agent 自己把图提升成独立的 user 图像块。
#   "hoist"  → 返回含 data_url 的 JSON 文本，由引擎嗅探后提升（兜底路径）
#   "off"    → 不内联，只回一句提示（引擎/模型不支持视觉时）
_IMG_EMIT = "off"


def set_image_emit(mode: str):
    """设置工具产出图片的形态："blocks" / "hoist" / "off"。"""
    global _IMG_EMIT
    _IMG_EMIT = mode if mode in ("blocks", "hoist", "off") else "off"


def set_image_inline(on: bool):
    """兼容旧调用：True = 结构化内容块（blocks），False = 关闭（off）。"""
    set_image_emit("blocks" if on else "off")


def image_emit_mode() -> str:
    return _IMG_EMIT


_IMG_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


def _image_payload(data_url: str, meta: dict):
    """按当前产出形态包装一张图片。

    blocks → [text块(元数据JSON), image_url块]（LangChain 原生通道 / custom 由 agent 提升）
    hoist  → 含 data_url 的 JSON 文本（旧行为，兜底）
    """
    m = dict(meta or {})
    if _IMG_EMIT == "blocks":
        m["mode"] = "image"
        m["note"] = "图片已作为图像块附加，可直接查看像素"
        return [{"type": "text", "text": json.dumps(m, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": data_url}}]
    m["mode"] = "base64"
    m["data_url"] = data_url
    return json.dumps(m, ensure_ascii=False)


def _get_workspace_dir():
    """获取 workspace 目录的绝对路径（项目根目录下的 workspace/）"""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "workspace")
    )


# 当前请求的文件访问模式：safe=仅workspace；ask=读任意+写workspace/skills；full=任意读写
_PERMISSION_MODE = "safe"
# 云端共享库：普通成员(未授权/approved)的文件基座锁到本人目录+pool；None=不额外限制
_USER_SCOPE = None

def set_permission_mode(mode: str):
    """设置当前请求的文件访问模式。由 agent 每次运行前调用。"""
    global _PERMISSION_MODE
    if mode in ("safe", "ask", "full"):
        _PERMISSION_MODE = mode

def set_user_scope(scope):
    """云端按身份注入沙盒：{'user': 名} → 仅 users/<名>/ 与 pool/ 可读；None 解除。"""
    global _USER_SCOPE
    _USER_SCOPE = scope if isinstance(scope, dict) else None

def get_permission_mode() -> str:
    """当前生效的权限模式（供系统提示词如实交代运行环境）。"""
    return _PERMISSION_MODE

def get_user_scope():
    """当前生效的账号级文件作用域；None = 无额外限制。"""
    return _USER_SCOPE

# 当前请求的登录身份（产出区/附件登记要判断"这个文件属于谁"）。
# 与 _PERMISSION_MODE 同机制：每个请求入口设置一次。
_CURRENT_USER = ""

def set_current_user(username: str):
    global _CURRENT_USER
    _CURRENT_USER = (username or "").strip()

def _get_project_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def _get_skills_dir():
    return os.path.join(_get_project_root(), "skills")

def _within(base: str, p: str) -> bool:
    p = os.path.abspath(p)
    return p.startswith(base + os.sep) or p == base


def _resolve_safe_path(relative_path: str, write: bool = False) -> str:
    """将路径解析为绝对路径，并按当前权限模式校验访问范围。

    路径路由（用户 2026-10-03 定）：相对路径以**当前工作区域**为基准——默认落
    项目目录；以 output/ 开头 → 产物目录（中间产物专用）。绝对路径直接使用。
    模式范围：safe=项目目录+产物目录；ask=读+应用项目根 / 写+skills；full=任意。
    """
    mode = _PERMISSION_MODE
    area = _work_areas.current_area(_CURRENT_USER)
    project_abs = os.path.abspath(area.get("project_dir") or _get_workspace_dir())
    output_abs = os.path.abspath(area.get("output_dir") or os.path.join(project_abs, "output"))
    os.makedirs(project_abs, exist_ok=True)
    os.makedirs(output_abs, exist_ok=True)

    # 规范化路径：相对路径走工作区域路由，绝对路径直接用
    if os.path.isabs(relative_path):
        full_path = os.path.abspath(relative_path)
    else:
        full_path = _work_areas.route(_CURRENT_USER, relative_path)

    if mode == "full":
        return full_path  # 完全访问：任意路径读写

    if mode == "safe":
        # 仅当前工作区域（项目目录 ∪ 产物目录）
        if not (_within(project_abs, full_path) or _within(output_abs, full_path)):
            raise PermissionError(f"安全模式下仅允许访问当前工作区域（项目/产物目录）: {relative_path}")
        # 云端共享库：普通成员进一步锁到本人目录 users/<u>/ 与共享 pool/
        if _USER_SCOPE:
            u = (_USER_SCOPE.get("user") or "guest").replace("..", "_")
            me = os.path.abspath(os.path.join(_get_workspace_dir(), "users", u))
            pool = os.path.abspath(os.path.join(_get_workspace_dir(), "pool"))
            if not (_within(me, full_path) or _within(pool, full_path) or full_path == me):
                raise PermissionError(f"云端实例仅可访问自己的文件与共享库: {relative_path}")
        return full_path

    # ask 询问模式
    app_root = os.path.abspath(_get_project_root())
    if not write:
        # 读：允许工作区域 + 应用项目根（含 skills/）
        if _within(project_abs, full_path) or _within(output_abs, full_path) or _within(app_root, full_path):
            return full_path
        raise PermissionError(f"询问模式下仅可读取工作区域与应用项目目录: {relative_path}")
    # 写：允许工作区域 + skills 目录（写技能作为例外放行）
    skills_abs = os.path.abspath(_get_skills_dir())
    if _within(project_abs, full_path) or _within(output_abs, full_path) or _within(skills_abs, full_path):
        return full_path
    raise PermissionError("__NEED_FULL__ 该写操作超出工作区域与 skills 目录，需要切换到完全访问模式才能执行")


def _page_lines(lines, offset: int, limit: int, subject: str = "内容", extra_note: str = "",
                max_chars: int = 0):
    """按行取一段 + 字符封顶，并生成【与实现一致】的续读提示。返回 dict。

    read_file 的 path 分支与 file_id 分支共用此函数，保证 `offset` 在两个分支里
    始终是同一个含义（1-indexed 行号）。历史上出过两类"提示与实现不一致"的坑：
    ① file_id 分支根本不分页，却提示模型「改用 offset/limit 分页读取」，模型照做
       会永远拿回同样前 20000 字符，陷入无效重试；
    ② 只判字符封顶，漏判「行数窗口没读到文件末尾」——此时返回 truncated=false 且
       不给续读位置，模型误以为整个文件读完了（静默读到半截）。
    提示语统一在这里生成，避免三处各写一份、各说各话。
    """
    _cap = int(max_chars or 0) or read_file_max_chars()
    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)
    chunk = lines[start:end]
    content = "".join(chunk)
    total_chars = len("".join(lines))
    truncated = len(content) > _cap
    next_offset = None
    warning = ""
    if truncated:
        # 定位截断发生在哪一行，据此给出续读行号
        acc = 0
        consumed = 0
        for ln in chunk:
            if acc + len(ln) > _cap:
                break
            acc += len(ln)
            consumed += 1
        content = content[:_cap]
        if consumed > 0:
            next_offset = start + consumed + 1
        else:
            # 第一行本身就把额度占满：按行号续读会拿回同一段，必须明说，
            # 否则模型按 next_offset 重试就是死循环（超长单行的 JSON 正是这种形状）。
            warning = (f"第 {start + 1} 行单行就有 {len(lines[start])} 字符，超过"
                       f"{_cap} 上限，无法按行续读。"
                       f"别反复调本工具，改用 run_code/terminal 对该行做字符切片读取，"
                       f"或改读已清洗的正文文件。")
    elif end < total:
        # 字符没封顶，但窗口没读到文件末尾
        next_offset = end + 1
    has_more = next_offset is not None

    note = ""
    if truncated or warning:
        note = (f"{subject}共约 {total_chars} 字符 / {total} 行，"
                f"本段取自第 {start + 1} 行起、封顶 {_cap} 字符。")
    if warning:
        note += " " + warning
    elif has_more:
        note += f"未读完，请用 offset={next_offset} 继续调用本工具。"
    elif not truncated and offset > 1:
        note += "已读到末尾。"
    if extra_note:
        note = (note + " " + extra_note).strip()
    return {"content": content, "truncated": truncated, "has_more": has_more,
            "next_offset": next_offset, "total_chars": total_chars,
            "total_lines": total, "note": note}


async def read_file(path: str = "", file_id: str = "", as_base64: bool = False, offset: int = 1,
                    limit: int = 500, max_chars: int = 0):
    """读取文件内容。统一入口：
    - 提供 file_id（上传附件 id）时，按附件读取：文档返回解析全文；图片在图像通道开启时返回【图像内容块】
      （[{"type":"text"...},{"type":"image_url"...}]），可直接看到像素；通道关闭时只回提示，不回吐 base64 文本
    - 否则按 path 读取磁盘文件（行号分页）
    Args:
        path: 相对于 workspace/ 的文件路径
        file_id: 上传附件的 file_id（优先于 path）
        as_base64: 当按 file_id 且是文档时，仍尝试返回原始字节 base64（图片默认返回 base64）
        offset: 起始行号 (1-indexed)。path 与 file_id 两个分支含义相同，均可分页续读
        limit: 最多返回多少行
        max_chars: 本次单次返回的字符上限（0 = 用当前生效上限）。子智能体档案可为自己
                   设更大的上限（如 50000），主智能体默认 20000 —— 互不影响。
    注意：单次返回【按字符数封顶】，防止长行 JSON/大文档一次撑爆模型上下文。
    被截断时返回体带 next_offset，按其值续读即可读完整个文件（附件与磁盘文件同样适用）。
    """
    # 附件模式：按 file_id 读上传的文件
    if (file_id or "").strip():
        fid = file_id.strip()
        # 图片 → 返回 base64(dataURL)；文档 → 返回解析全文
        data_url = await image_to_base64(fid)
        if data_url:
            if _IMG_EMIT == "off":
                return json.dumps({"ok": True, "source": "attachment", "file_id": fid,
                                   "mode": "image_no_inline",
                                   "hint": "当前引擎不支持把工具结果内联成图像块。请让用户把图作为当轮附件发送，"\
                                           "或改用 custom 引擎；不要靠读 base64 文本猜图内容。"}, ensure_ascii=False)
            if len(data_url) > IMAGE_INLINE_MAX_CHARS:
                return json.dumps({"ok": True, "source": "attachment", "file_id": fid, "mode": "image_too_large",
                                   "hint": f"图片 dataURL 过大（{len(data_url)} 字符 > 上限 {IMAGE_INLINE_MAX_CHARS}）；"
                                           f"请先用 run_code(PIL) 裁 panel / 降质量 / 缩宽度，或切成多张小图分别读取。"},
                                  ensure_ascii=False)
            return _image_payload(data_url, {"ok": True, "source": "attachment", "file_id": fid,
                                             "data_url_chars": len(data_url)})
        text = await get_upload_text(fid)
        if text:
            p = _page_lines(text.splitlines(keepends=True), offset, limit,
                            subject="附件正文", extra_note="续读时 file_id 保持不变。",
                            max_chars=max_chars)
            return json.dumps({"ok": True, "source": "attachment", "file_id": fid, "mode": "text",
                               "offset": offset, "limit": limit,
                               "total_chars": p["total_chars"],
                               "truncated": p["truncated"], "has_more": p["has_more"],
                               "next_offset": p["next_offset"],
                               "note": p["note"],
                               "content": p["content"]}, ensure_ascii=False)
        # 无 text.txt：该附件是按「笔记附件」上传的（parse=False，只存原件不进索引）。
        # 用户 2026-09-25 定的分层设计：RAG 先命中笔记正文，需要细节时智能体按 file_id
        # 现读原件 —— 这里就在做「现读」：读原文即时提取，不落盘、不入索引。
        _d, _meta, _orig = _resolve_upload(fid)
        if _orig and os.path.isfile(_orig):
            try:
                with open(_orig, "rb") as _f:
                    _bytes = _f.read()
                _ext = os.path.splitext(_orig)[1].lower()
                if _ext == ".pdf":
                    import fitz
                    with fitz.open(stream=_bytes, filetype="pdf") as _doc:
                        _txt = "".join(_pg.get_text() for _pg in _doc)
                elif _ext == ".docx":
                    import io as _io, docx as _docx
                    _dcm = _docx.Document(_io.BytesIO(_bytes))
                    _txt = "\n".join(_p.text for _p in _dcm.paragraphs)
                elif _ext in (".pptx", ".ppt"):
                    import io as _io, zipfile as _zip
                    from xml.etree import ElementTree as _ET
                    _ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
                    _txt = ""
                    with _zip.ZipFile(_io.BytesIO(_bytes)) as _z:
                        _slides = sorted(n for n in _z.namelist()
                                         if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
                        for _sn in _slides:
                            _root = _ET.fromstring(_z.read(_sn))
                            _txt += "".join(t.text or "" for t in _root.iter(_ns + "t")) + "\n"
                else:
                    _txt = _bytes.decode("utf-8", errors="replace")
                _txt = (_txt or "").strip()
                if not _txt:
                    return json.dumps({"ok": False, "file_id": fid,
                                       "error": f"附件 {fid} 未提取到文本（可能是扫描件；"
                                                f"图片请用 read_file，扫描 PDF 可试 mineru_parse）"},
                                      ensure_ascii=False)
                p = _page_lines(_txt.splitlines(keepends=True), offset, limit,
                                subject="原件正文", extra_note="续读时 file_id 保持不变。",
                                max_chars=max_chars)
                _note = "该附件未预先解析（笔记附件），以上为本次即时提取的原文；未写入索引。"
                if p["note"]:
                    _note += " " + p["note"]
                return json.dumps({"ok": True, "source": "attachment", "file_id": fid, "mode": "text",
                                   "on_demand": True, "offset": offset, "limit": limit,
                                   "total_chars": p["total_chars"],
                                   "truncated": p["truncated"], "has_more": p["has_more"],
                                   "next_offset": p["next_offset"],
                                   "note": _note,
                                   "content": p["content"]}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"ok": False, "file_id": fid,
                                   "error": f"即时提取失败: {type(e).__name__}: {str(e)[:120]}"},
                                  ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"附件 {fid} 无内容"})

    path = (path or "").strip()
    if not path:
        return json.dumps({"error": "文件路径不能为空"})

    try:
        full_path = _resolve_safe_path(path)

        if not os.path.isfile(full_path):
            return json.dumps({"error": f"文件不存在: {path}"})

        # ── 图片：按 path 直接转 dataURL（image_read 通道）──────────
        _ext = os.path.splitext(full_path)[1].lower()
        if _ext in _IMG_MIME:
            if _IMG_EMIT == "off":
                return json.dumps({"ok": False, "mode": "image_no_inline", "path": path,
                                   "hint": "当前引擎不支持把工具结果内联成图像块（改用 custom 引擎，"
                                           "或让用户把图作为当轮附件发送）。"}, ensure_ascii=False)
            try:
                import base64 as _b64
                with open(full_path, "rb") as _f:
                    _b = _f.read()
                _du = f"data:{_IMG_MIME[_ext]};base64," + _b64.b64encode(_b).decode()
                if len(_du) > IMAGE_INLINE_MAX_CHARS:
                    return json.dumps({"ok": True, "source": "path", "mode": "image_too_large",
                                       "path": path, "bytes": len(_b),
                                       "hint": f"dataURL {len(_du)} 字符 > 上限 {IMAGE_INLINE_MAX_CHARS}；"
                                               f"请先用 run_code(PIL) 裁 panel / 降质量 / 缩宽度，"
                                               f"或切成多张小图分别读取。"}, ensure_ascii=False)
                return _image_payload(_du, {"ok": True, "source": "path", "path": path,
                                            "bytes": len(_b)})
            except Exception as e:
                return json.dumps({"error": f"图片读取失败: {type(e).__name__}: {str(e)[:120]}"},
                                  ensure_ascii=False)

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # 字符封顶：即便命中行数少、但单行超长（如整份 JSON 一行），也不会一次吐出超大文本
        p = _page_lines(lines, offset, limit, subject="该文件", extra_note=(
            "注意：勿整读巨型 JSON（layout.json / *_model.json / *_content_list_v2.json），"
            "正文用 00_full_cleaned.md。"), max_chars=max_chars)
        return json.dumps({
            "path": path,
            "total_lines": p["total_lines"],
            "total_chars": p["total_chars"],
            "offset": offset,
            "limit": limit,
            "truncated": p["truncated"],
            "has_more": p["has_more"],
            "next_offset": p["next_offset"],
            "note": p["note"],
            "content": p["content"],
        }, ensure_ascii=False)

    except PermissionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"读取文件失败: {str(e)}"})


async def write_file(path: str, content: str):
    """写入文件到 workspace 目录

    Args:
        path: 相对于 workspace/ 的文件路径
        content: 文件内容（完整覆盖）
    """
    path = path.strip()
    if not path:
        return json.dumps({"error": "文件路径不能为空"})

    norm = path.replace("\\", "/")
    # `outputs/xxx` = 写入**本人的产出区**，并自动登记成正式附件（只记指针、不复制文件）。
    # 拿到 file_id 后就能用 {{file:<file_id>:<文件名>}} 占位符引用 —— 与笔记附件完全同款：
    # 后端只有一份文件，「后端打开」直接打开它，「前端打开」从后端下载/浏览器临时预览。
    if norm == "outputs" or norm.startswith("outputs/"):
        name = os.path.basename(norm.rstrip("/")) or "output.txt"
        try:
            d = _user_outputs_dir(_CURRENT_USER)
            full_path = os.path.join(d, name)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            reg = json.loads(register_output("outputs/" + name, _CURRENT_USER))
            if reg.get("status") != "ok":
                return json.dumps(reg, ensure_ascii=False)
            return json.dumps({
                "status": "ok",
                "path": "outputs/" + name,
                "bytes_written": len(content.encode("utf-8")),
                "file_id": reg["file_id"],
                "name": name,
                "note": "产出已登记为附件；在回答里用 {{file:<file_id>:<name>}} 引用它",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"写产出失败: {e}"}, ensure_ascii=False)

    try:
        full_path = _resolve_safe_path(path, write=True)

        # 确保父目录存在
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        return json.dumps({
            "status": "ok",
            "path": path,
            "bytes_written": len(content.encode("utf-8")),
        }, ensure_ascii=False)

    except PermissionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"写入文件失败: {str(e)}"})


async def list_files(subdir: str = ""):
    """列出 workspace 目录下的文件"""
    try:
        full_path = _resolve_safe_path(subdir) if subdir else _get_workspace_dir()
        if not os.path.isdir(full_path):
            return json.dumps({"error": f"目录不存在: {subdir}"})

        items = []
        for name in sorted(os.listdir(full_path)):
            item_path = os.path.join(full_path, name)
            items.append({
                "name": name,
                "type": "dir" if os.path.isdir(item_path) else "file",
                "size": os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
                "modified": os.path.getmtime(item_path),
            })

        return json.dumps({
            "path": subdir or "/",
            "items": items,
        }, ensure_ascii=False)

    except PermissionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"列出目录失败: {str(e)}"})


# ═══════════════════════════════════════════════
# 文件上传管理 —— v2：物理分目录
#   users/<用户>/uploads/   私人聊天附件（不入索引）
#   pool/uploads/           共享数据库（入向量索引）
# ═══════════════════════════════════════════════

_WORKSPACE_ABS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspace"))

# 旧版单一目录（兼容解析存量 file_id）
_UPLOADS_DIR = os.path.join(_WORKSPACE_ABS, "uploads")


def _user_key(username: str) -> str:
    """用户名→安全目录名。游客/空 → _guest。"""
    u = (username or "").strip() or "guest"
    if u == "guest":
        return "_guest"
    k = "".join(c for c in u if (c.isalnum() or c in "-_@.·"))
    return k or "_guest"


def _users_root() -> str:
    d = os.path.join(_WORKSPACE_ABS, "users")
    os.makedirs(d, exist_ok=True)
    return d


def _user_dir(username: str) -> str:
    d = os.path.join(_users_root(), _user_key(username))
    os.makedirs(d, exist_ok=True)
    return d


def _user_uploads_dir(username: str) -> str:
    d = os.path.join(_user_dir(username), "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def _user_outputs_dir(username: str = "") -> str:
    """本人的**产出区**：agent / 技能生成的文件的唯一落点。

    用户 2026-09-26 定：「和我笔记里的一样，就一个占位符，链接后端真实文件位置」——
    所以产出文件**留在产出区不复制**，附件登记只记一条相对指针（见 register_output）。
    """
    d = os.path.join(_user_dir(username or _CURRENT_USER), "outputs")
    os.makedirs(d, exist_ok=True)
    return d


def _user_notes_path(username: str) -> str:
    return os.path.join(_user_dir(username), "notes.json")


def _pool_dir() -> str:
    d = os.path.join(_WORKSPACE_ABS, "pool")
    os.makedirs(d, exist_ok=True)
    return d


def _pool_uploads_dir() -> str:
    d = os.path.join(_pool_dir(), "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def _upload_roots() -> list:
    """全部可能的上传根目录（解析 file_id 用）：pool → 各用户 → 旧版单一目录"""
    roots = [_pool_uploads_dir()]
    uroot = _users_root()
    try:
        for name in sorted(os.listdir(uroot)):
            upd = os.path.join(uroot, name, "uploads")
            if os.path.isdir(upd):
                roots.append(upd)
    except Exception:
        pass
    if os.path.isdir(_UPLOADS_DIR):
        roots.append(_UPLOADS_DIR)
    return roots


def _resolve_upload(file_id: str):
    """在 pool / users/* / 旧目录中定位 file_id，返回 (dir, meta, orig_path) 或 (None,None,None)。"""
    fid = (file_id or "").strip()
    if not fid:
        return None, None, None
    for root in _upload_roots():
        d = os.path.join(root, fid)
        mp = os.path.join(d, "meta.json")
        if not os.path.isfile(mp):
            continue
        try:
            with open(mp, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        rel = (meta.get("rel") or "").strip().replace("\\", "/")
        if rel:
            # 产出指针：文件**留在产出区、不复制**（用户 2026-09-26 定：
            # 「链接后端真实文件位置」→ 后端只有一份，点前端打开才下载到本地）。
            # 只接受相对路径，且解析后必须仍在该基座（users/<u>/ 或 pool/）之内 ——
            # 这一处就是唯一的边界校验，不需要在每个调用端点重复。
            if os.path.isabs(rel) or ".." in rel.split("/"):
                continue
            base = os.path.dirname(os.path.abspath(root))
            orig = os.path.abspath(os.path.join(base, rel))
            if not _within(base, orig) or not os.path.isfile(orig):
                continue
            return d, meta, orig
        orig = os.path.join(d, meta.get("name", "file"))
        return d, meta, orig
    return None, None, None


def _save_upload_meta(file_id: str, meta: dict):
    # 直接按 source 定位目录（不能先 _resolve_upload：meta.json 尚不存在时解析不到，
    # 会错误回退到用户目录——曾导致 pool 文件 meta 写丢）
    if meta.get("source") == "database":
        d = os.path.join(_pool_uploads_dir(), file_id)
    else:
        d = os.path.join(_user_uploads_dir(meta.get("owner", "guest")), file_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _find_output_file_id(rel_path: str, owner: str) -> str:
    """该产出文件是否已登记过（避免每次列举都造一个新 file_id）。"""
    upd = _user_uploads_dir(owner)
    try:
        fids = sorted(os.listdir(upd))
    except Exception:
        return ""
    for fid in fids:
        mp = os.path.join(upd, fid, "meta.json")
        if not os.path.isfile(mp):
            continue
        try:
            with open(mp, "r", encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        if m.get("source") == "output" and (m.get("rel") or "").replace("\\", "/") == rel_path:
            return fid
    return ""


def register_output(rel_path: str, owner: str = "", name: str = "") -> str:
    """把一个**已存在**的产出文件登记成正式附件 —— **不复制文件**，只记一条相对指针。

    用户 2026-09-26 定：「和我笔记里的一样，就一个占位符，链接后端真实文件位置，
    后端打开就自动打开文件，前端打开就从后端下载/在浏览器里临时打开」。
    所以后端只有一份文件；`_resolve_upload` 认得 `rel` 指针后，
    /api/file/{id}、/raw、/text、/open 这些端点全部自动支持它（无需各自改动）。

    rel_path 相对 users/<owner>/（如 outputs/report.pptx）。
    """
    owner = owner or _CURRENT_USER
    rel_path = (rel_path or "").strip().replace("\\", "/")
    if os.path.isabs(rel_path) or ".." in rel_path.split("/"):
        return json.dumps({"error": f"产出路径非法: {rel_path}"}, ensure_ascii=False)
    udir = _user_dir(owner)
    full = os.path.abspath(os.path.join(udir, rel_path))
    if not _within(udir, full) or not os.path.isfile(full):
        return json.dumps({"error": f"产出文件不存在或越界: {rel_path}"}, ensure_ascii=False)

    existed = _find_output_file_id(rel_path, owner)
    if existed:
        fid = existed
    else:
        # ★ 必须是纯十六进制（与 upload_file 的 uuid4().hex 一致）——
        # 前端把 {{file:...}} 渲染成附件卡片的正则是 /\{\{file:([0-9a-fA-F]+):(.*)\}\}/，
        # 带 "file-" 前缀或连字符会匹配不上、显示成字面文本。
        fid = uuid.uuid4().hex
    meta = {
        "name": name or os.path.basename(rel_path),
        "rel": rel_path,                       # ★ 指针：文件在产出区原地不动
        "owner": owner,
        "source": "output",
        "size": os.path.getsize(full),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _save_upload_meta(fid, meta)
    return json.dumps({"status": "ok", "file_id": fid, "name": meta["name"],
                       "size": meta["size"], "rel": rel_path}, ensure_ascii=False)


def list_outputs(owner: str = "") -> str:
    """列出本人产出区的文件，**未登记的自动补登记** → 返回 file_id。

    产出源不固定（write_file / run_python / terminal / 以后装的 PPT 等各种技能），
    所以这里只认"目录"，不认"哪个工具写的" —— 谁写进来都会被登记。
    """
    owner = owner or _CURRENT_USER
    d = _user_outputs_dir(owner)
    files = []
    try:
        names = sorted(os.listdir(d))
    except Exception:
        names = []
    for fn in names:
        p = os.path.join(d, fn)
        if not os.path.isfile(p):
            continue
        rel = "outputs/" + fn
        fid = _find_output_file_id(rel, owner)
        if not fid:
            fid = (json.loads(register_output(rel, owner)) or {}).get("file_id", "")
        files.append({
            "file_id": fid,
            "name": fn,
            "size": os.path.getsize(p),
            "mtime": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M"),
        })
    return json.dumps({"status": "ok", "dir": "outputs", "count": len(files), "files": files},
                      ensure_ascii=False)


async def upload_file(filename: str, content_bytes: bytes, owner: str = "",
                      source: str = "chat", parse: bool = True) -> str:
    """上传文件，返回 JSON 字符串 { file_id, name, text, size }。

    owner=归属用户；source=chat→users/<owner>/uploads（私人，不入索引）；
    source=database→pool/uploads（共享，入向量索引，仅云端共享库用）。

    parse 的含义（用户 2026-09-25 定）：
      · parse=True  → 解析附件成文字，写 text.txt，进向量索引（供 RAG）
      · parse=False → **只存原件**，不解析、不写 text.txt、不入索引
    调用方（app.py）按「新内容首次进入共享库时落在哪个文件夹」决定：
      落在「文档」夹（parse=true）→ True；落在「笔记」夹或库内操作 → False。
    """
    owner = owner or "guest"
    file_id = uuid.uuid4().hex  # 全量 uuid，防止被枚举他人文件
    base_dir = _pool_uploads_dir() if source == "database" else _user_uploads_dir(owner)
    file_dir = os.path.join(base_dir, file_id)
    os.makedirs(file_dir, exist_ok=True)

    # 保存原始文件（带扩展名，方便系统识别类型）
    orig_path = os.path.join(file_dir, filename)
    with open(orig_path, "wb") as f:
        f.write(content_bytes)

    # 提取文本（parse=False 则跳过，只留原件）
    text_content = ""
    ext = os.path.splitext(filename)[1].lower()

    if not parse:
        meta = {
            "file_id": file_id,
            "name": filename,
            "size": len(content_bytes),
            "mime": ext,
            "text_length": 0,
            "created": str(datetime.now()),
            "owner": owner,
            "source": source,
            "parsed": False,        # 记录「未解析」这一事实，便于排查
        }
        _save_upload_meta(file_id, meta)
        return json.dumps({
            "file_id": file_id,
            "name": filename,
            "text": "",
            "text_full_length": 0,
            "size": len(content_bytes),
            "parsed": False,
        }, ensure_ascii=False)

    try:
        if ext == ".txt":
            text_content = content_bytes.decode("utf-8", errors="replace")
        elif ext == ".pdf":
            import fitz
            doc = fitz.open(stream=content_bytes, filetype="pdf")
            for page in doc:
                text_content += page.get_text()
            doc.close()
        elif ext in (".md", ".csv", ".json", ".xml", ".yaml", ".yml"):
            text_content = content_bytes.decode("utf-8", errors="replace")
        elif ext in (".docx", ".doc"):
            try:
                import io, docx
                doc = docx.Document(io.BytesIO(content_bytes))
                text_content = "\n".join(p.text for p in doc.paragraphs)
            except Exception as e:
                text_content = f"[Word 文档解析失败: {str(e)}]"
        elif ext == ".pptx":
            try:
                import io
                from pptx import Presentation
                prs = Presentation(io.BytesIO(content_bytes))
                texts = []
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text.strip():
                            texts.append(shape.text)
                text_content = "\n".join(texts)
            except Exception as e:
                text_content = f"[PPT 解析失败: {str(e)}]"
        elif ext == ".odt":
            try:
                import zipfile, io
                with zipfile.ZipFile(io.BytesIO(content_bytes)) as z:
                    import xml.etree.ElementTree as ET
                    ns = {"text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
                    tree = ET.parse(io.BytesIO(z.read("content.xml")))
                    paras = tree.findall(".//text:p", ns)
                    text_content = "\n".join("".join(p.itertext()) for p in paras)
            except Exception as e:
                text_content = f"[ODT 解析失败: {str(e)}]"
        elif ext == ".svg":
            text_content = content_bytes.decode("utf-8", errors="replace")
        elif ext == ".xlsx":
            try:
                import io, openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(content_bytes), read_only=True, data_only=True)
                rows = []
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        rows.append("\t".join(str(c or "") for c in row))
                text_content = "\n".join(rows)
                wb.close()
            except Exception as e:
                text_content = f"[Excel 解析失败: {str(e)}]"
        elif ext == ".xls":
            # 旧版 Excel(.xls) 用 xlrd 解析（openpyxl 不支持 .xls）
            try:
                import io, xlrd
                wb = xlrd.open_workbook(file_contents=content_bytes)
                rows = []
                for ws in wb.sheets():
                    for r in range(ws.nrows):
                        rows.append("\t".join(str(ws.cell_value(r, c)) for c in range(ws.ncols)))
                text_content = "\n".join(rows) if rows else ""
            except Exception as e:
                text_content = f"[Excel(.xls) 解析失败: {str(e)}；建议将文件另存为 .xlsx 后重传]"
        elif ext == ".doc":
            # 旧版 Word(.doc) 无成熟解析库；给出清晰指引
            text_content = "[Word(.doc) 旧格式暂不支持后端解析；建议将文件另存为 .docx 后重传]"
        elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"):
            text_content = f"[图片文件: {filename}]"
        elif ext == ".svg":
            text_content = content_bytes.decode("utf-8", errors="replace")
        else:
            # 其他格式尽量以 UTF-8 读
            try:
                text_content = content_bytes.decode("utf-8", errors="replace")
            except:
                text_content = f"[二进制文件，无法提取文本: {filename}]"
    except Exception as e:
        text_content = f"[文本提取失败: {str(e)}]"

    # 保存提取文本
    with open(os.path.join(file_dir, "text.txt"), "w", encoding="utf-8") as f:
        f.write(text_content)

    # 保存 metadata
    meta = {
        "file_id": file_id,
        "name": filename,
        "size": len(content_bytes),
        "mime": ext,
        "text_length": len(text_content),
        "created": str(datetime.now()),
        "owner": owner,
        "source": source,          # chat=私人附件 / database=共享数据库
        "parsed": True,            # 已解析出正文
    }
    _save_upload_meta(file_id, meta)

    # 知识库入库：仅 database 来源（共享池）。聊天附件永不入索引。
    # 图片占位/解析失败提示/过短文本不入库。
    # 不变量：走到这里必然 parse=True（parse=False 已在上面 early-return，
    # 只存原件不入索引）——这正是「笔记附件不参与 RAG 检索」的实现点。
    _t = (text_content or "").strip()
    if source == "database" and len(_t) >= 50 and not (_t.startswith("[") and _t.rstrip().endswith("]")):
        def _ingest(_fid=file_id, _name=filename, _text=_t):
            try:
                from tools.vector_search import ingest_text_to_knowledge
                ingest_text_to_knowledge(_text, _name, _fid)
            except Exception:
                pass  # 入库失败不影响上传本身；重传同内容会重复入库（可接受，搜索按相似度排序）
        threading.Thread(target=_ingest, daemon=True).start()

    return json.dumps({
        "file_id": file_id,
        "name": filename,
        "text": text_content,  # 完整文本（模型上下文足够，不做截断）
        "text_full_length": len(text_content),
        "size": len(content_bytes),
    }, ensure_ascii=False)


async def delete_upload(file_id: str) -> str:
    """删除上传的文件（并同步清理知识索引中该文件的块；私人附件不入索引无此步骤）"""
    d, meta, _ = _resolve_upload(file_id)
    if d and os.path.isdir(d):
        shutil.rmtree(d)
        if (meta or {}).get("source") == "database":
            try:
                from tools.vector_search import remove_from_knowledge_index
                remove_from_knowledge_index(file_id)
            except Exception:
                pass
        return json.dumps({"status": "ok", "file_id": file_id})
    return json.dumps({"error": f"文件 {file_id} 不存在"})


# 无 text.txt 时可回退「直读原文件本体」的文本类扩展名。
# 背景：产出文件（outputs/*.md 等）由 register_output 只登记指针、不复制、不生成 text.txt，
# 导致 /api/file/text 返回 200+空串 → 前端小窗显示「该文件没有可用文本」（且 200 不触发回退）。
_TEXT_READABLE_EXTS = {
    "md", "markdown", "txt", "py", "js", "mjs", "ts", "jsx", "tsx", "json", "jsonl",
    "csv", "tsv", "log", "yaml", "yml", "toml", "ini", "cfg", "conf", "env",
    "html", "htm", "css", "xml", "sql", "sh", "bat", "ps1", "tex", "bib", "rst",
}
_TEXT_READABLE_MAX = 1024 * 1024  # 1 MB 上限：超此不回退，避免超大文件灌爆小窗与模型上下文


def _parse_office_file(path: str) -> str:
    """产出文件（未经上传解析）按需抽正文：xlsx/xls/docx/pptx。
    2026-10-22：用户生成 Excel 点预览只看到「读取解析文本」→ 其实 text.txt 从未生成。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                rows.append("[%s]" % ws.title)
                for row in ws.iter_rows(values_only=True):
                    if any(c not in (None, "") for c in row):
                        rows.append("\t".join(str(c if c is not None else "") for c in row))
            wb.close()
            return "\n".join(rows)
        if ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(path)
            rows = []
            for ws in wb.sheets():
                rows.append("[%s]" % ws.name)
                for r in range(ws.nrows):
                    rows.append("\t".join(str(ws.cell_value(r, c)) for c in range(ws.ncols)))
            return "\n".join(rows)
        if ext == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(path).paragraphs)
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(path)
            out = []
            for i, slide in enumerate(prs.slides, 1):
                out.append("[slide %d]" % i)
                for sh in slide.shapes:
                    if getattr(sh, "has_text_frame", False) and sh.text.strip():
                        out.append(sh.text)
            return "\n".join(out)
    except Exception as e:
        print("[office-parse] %s: %s" % (path, e))
    return ""


async def get_upload_text(file_id: str) -> str:
    """获取上传文件的完整文本内容（跨 pool/私人区/旧目录解析）

    优先读解析产物 text.txt；没有 text.txt 时（产出指针文件、未解析的纯文本附件）
    回退直读原文件本体，仅限 _TEXT_READABLE_EXTS 且体积不超 _TEXT_READABLE_MAX；
    Office 产出（xlsx/xls/docx/pptx）则按需解析并回写 text.txt 缓存。
    """
    d, _, orig = _resolve_upload(file_id)
    if not d:
        return ""
    text_path = os.path.join(d, "text.txt")
    if os.path.isfile(text_path):
        with open(text_path, "r", encoding="utf-8") as f:
            return f.read()
    if orig and os.path.isfile(orig):
        ext = os.path.splitext(orig)[1].lower().lstrip(".")
        if ext in _TEXT_READABLE_EXTS:
            try:
                if os.path.getsize(orig) > _TEXT_READABLE_MAX:
                    return ""
                with open(orig, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except Exception:
                return ""
        if ext in ("xlsx", "xls", "docx", "pptx"):
            try:
                if os.path.getsize(orig) > 20 * 1024 * 1024:
                    return ""
                import asyncio as _aio
                txt = await _aio.to_thread(_parse_office_file, orig)
                if txt:
                    try:
                        with open(text_path, "w", encoding="utf-8") as f:
                            f.write(txt)          # 缓存：下次直接读
                    except Exception:
                        pass
                return txt
            except Exception:
                return ""
    return ""


# ═══════════════════════════════════════════════
# 笔记 CRUD —— v2：按用户物理分文件 users/<用户>/notes.json
# ═══════════════════════════════════════════════

_NOTES_PATH_LEGACY = os.path.join(_WORKSPACE_ABS, "_notes.json")


def _read_notes(owner: str = "guest") -> list:
    p = _user_notes_path(owner) if owner else _NOTES_PATH_LEGACY
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _write_notes(owner: str, notes: list):
    p = _user_notes_path(owner)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)


def list_all_users_notes() -> list:
    """admin 用：汇总 users/*/notes.json，每条补 owner 字段。旧版 _notes.json 视为 admin。"""
    out = []
    uroot = _users_root()
    try:
        for name in sorted(os.listdir(uroot)):
            np_ = os.path.join(uroot, name, "notes.json")
            if not os.path.isfile(np_):
                continue
            try:
                with open(np_, "r", encoding="utf-8") as f:
                    for n in json.load(f):
                        n = dict(n)
                        n.setdefault("owner", "_guest" if name == "_guest" else name)
                        out.append(n)
            except Exception:
                pass
    except Exception:
        pass
    if os.path.isfile(_NOTES_PATH_LEGACY):
        try:
            with open(_NOTES_PATH_LEGACY, "r", encoding="utf-8") as f:
                for n in json.load(f):
                    n = dict(n)
                    n.setdefault("owner", "admin")
                    out.append(n)
        except Exception:
            pass
    return out


def get_note(note_id: str, owner: str = "guest") -> dict | None:
    """取**单条**条目（含正文）。

    为什么需要它（用户 2026-09-26 实测反馈「共享库比较卡顿，每次都要加载」）：
    共享库 65 条条目的正文合计 413KB，而列表只需要标题（中位数 17 字）。
    正文原来是跟着列表一起下发的 → 每次打开面板都要下载 435KB。
    现在列表只给「目录」（title/kind/folder_id/owner/时间），正文由这里按需取
    —— 只有你**展开某一条**或**点编辑**时才取那一条。

    查找顺序与 get_note_owner 一致：先自己，再全库（共享库条目属于别人但可读）。
    """
    for n in _read_notes(owner):
        if n.get("id") == note_id:
            return n
    for n in list_all_users_notes():
        if n.get("id") == note_id:
            return n
    return None


def get_note_owner(note_id: str, owner: str = "guest") -> str:
    """笔记归属用户；先查自己，再全库兜底（app.py 越权校验用）"""
    for n in _read_notes(owner):
        if n.get("id") == note_id:
            return owner
    for n in list_all_users_notes():
        if n.get("id") == note_id:
            return n.get("owner", "admin")
    return owner


async def list_notes(branch_id: str = "", owner: str = "guest", kind: str = "",
                     folder_id: str = "") -> str:
    """列条目。kind="" 全部；"note" 笔记；"document" 文档条目。

    用户 2026-09-22 定：条目分两类，数据结构完全一致（都是 文本+附件）：
      · kind="note"     人写的笔记，适合直接阅读，共享库默认展示
      · kind="document" 机器解析的文档正文，文本乱但用于检索/向量索引
    老数据无 kind 字段 → 一律视为 "note"。

    folder_id（用户 2026-09-25 定）：按分组过滤。
      不传 = 不过滤（全部）；传 "__root__" = 只列未分组条目；传具体 id = 该分组下。
    """
    notes = _read_notes(owner)
    if branch_id:
        notes = [n for n in notes if n.get("branch_id", "") == branch_id]
    if kind:
        notes = [n for n in notes if (n.get("kind") or "note") == kind]
    if folder_id:
        if folder_id == "__root__":
            notes = [n for n in notes if not (n.get("folder_id") or "")]
        else:
            notes = [n for n in notes if (n.get("folder_id") or "") == folder_id]
    return json.dumps({"status": "ok", "total": len(notes), "notes": notes}, ensure_ascii=False)


async def add_note(title: str, content: str, branch_id: str = "", owner: str = "guest",
                   kind: str = "note", folder_id: str = "") -> str:
    owner = owner or "guest"
    notes = _read_notes(owner)
    note = {
        "id": "note-" + str(int(__import__("time").time() * 1000)),
        "title": title or "新笔记",
        "content": content or "",
        "branch_id": branch_id,
        "owner": owner,
        "kind": kind or "note",     # note=笔记 / document=文档条目（解析结果另立）
        "folder_id": folder_id or "",  # 归属分组（分组树 id；空=未分组）用户 2026-09-25 定
        "createdAt": str(datetime.now()),
        "updatedAt": str(datetime.now()),
    }
    notes.append(note)
    _write_notes(owner, notes)
    return json.dumps({"status": "ok", "note_id": note["id"]}, ensure_ascii=False)


def _find_note(owner: str, note_id: str):
    """先在自己笔记里找；找不到全库兜底。返回 (note, owner_key) 或 (None, None)。"""
    for n in _read_notes(owner):
        if n.get("id") == note_id:
            return n, owner
    for n in list_all_users_notes():
        if n.get("id") == note_id:
            return n, n.get("owner", "admin")
    return None, None


async def update_note(note_id: str, title: str = None, content: str = None,
                      owner: str = "guest", kind: str = None,
                     folder_id: str = None) -> str:
    """更新条目。kind 传入时一并更新（迁移/改分类用）；folder_id 传入时改归属分组。"""
    n, okey = _find_note(owner, note_id)
    if n is None:
        return json.dumps({"error": "笔记不存在"})
    notes = _read_notes(okey)
    for x in notes:
        if x.get("id") == note_id:
            if title is not None:
                x["title"] = title
            if content is not None:
                x["content"] = content
            if kind is not None:
                x["kind"] = kind or "note"
            if folder_id is not None:
                x["folder_id"] = folder_id
            x["updatedAt"] = str(datetime.now())
            _write_notes(okey, notes)
            return json.dumps({"status": "ok"}, ensure_ascii=False)
    return json.dumps({"error": "笔记不存在"})


async def delete_note(note_id: str, owner: str = "guest") -> str:
    n, okey = _find_note(owner, note_id)
    if n is None:
        return json.dumps({"error": "笔记不存在"})
    notes = [x for x in _read_notes(okey) if x.get("id") != note_id]
    _write_notes(okey, notes)
    return json.dumps({"status": "ok"}, ensure_ascii=False)


async def move_note(note_id: str, direction: int, owner: str = "guest") -> str:
    """在所属对话(branch)内移动笔记顺序。direction<0 上移, >0 下移。"""
    n, okey = _find_note(owner, note_id)
    if n is None:
        return json.dumps({"error": "笔记不存在"})
    notes = _read_notes(okey)
    idx = next((i for i, x in enumerate(notes) if x.get("id") == note_id), -1)
    if idx == -1:
        return json.dumps({"error": "笔记不存在"})
    branch_id = notes[idx].get("branch_id", "")
    rng = range(idx - 1, -1, -1) if direction < 0 else range(idx + 1, len(notes))
    for j in rng:
        if notes[j].get("branch_id", "") == branch_id:
            notes[idx], notes[j] = notes[j], notes[idx]
            _write_notes(okey, notes)
            return json.dumps({"status": "ok"}, ensure_ascii=False)
    msg = "已经是第一条" if direction < 0 else "已经是最后一条"
    return json.dumps({"status": "ok", "message": msg}, ensure_ascii=False)


# ═══════════════ 后端化：按 file_id 读取（_resolve_upload 已在上方统一定义，跨 pool/用户区/旧目录） ═══════════════


def split_image_result(tool_result):
    """把工具产出拆成 (data_url, text)。两种形态都吃：

    - 结构化内容块 list[dict]（blocks 形态）：取 type=image_url 的 url，text 块拼成 meta
    - JSON 文本（hoist 形态）：mode=="base64" 时取 data_url，其余字段作为 meta
    无图返回 ("", "")。data_url 超限（IMAGE_INLINE_MAX_CHARS）视为无图，交由上游提示裁剪。
    """
    if isinstance(tool_result, (list, tuple)):
        url, texts = "", []
        for b in tool_result:
            if not isinstance(b, dict):
                texts.append(str(b))
                continue
            if b.get("type") == "image_url":
                inner = b.get("image_url")
                u = inner.get("url") if isinstance(inner, dict) else ""
                if isinstance(u, str) and u.startswith("data:image/") and len(u) <= IMAGE_INLINE_MAX_CHARS:
                    url = url or u
            elif b.get("type") == "text":
                texts.append(b.get("text") or "")
            else:
                texts.append(json.dumps(b, ensure_ascii=False, default=str))
        return url, "\n".join(t for t in texts if t).strip()
    s = tool_result if isinstance(tool_result, str) else str(tool_result or "")
    if "data:image/" not in s:
        return "", ""
    try:
        d = json.loads(s)
    except Exception:
        return "", ""
    if not isinstance(d, dict) or d.get("mode") != "base64":
        return "", ""
    u = d.get("data_url") or ""
    if isinstance(u, str) and u.startswith("data:image/") and len(u) <= IMAGE_INLINE_MAX_CHARS:
        # meta 里剔除 data_url：工具消息里绝不能留 base64 文本
        meta = {k: v for k, v in d.items() if k != "data_url"}
        return u, json.dumps(meta, ensure_ascii=False)
    return "", ""


def extract_image_data_url(tool_result) -> str:
    """从工具产出里嗅探图片 dataURL（image_read 通道用）。无则返回空串。"""
    return split_image_result(tool_result)[0]


async def image_to_base64(file_id: str) -> str:
    """按 file_id 读取原始图片并转为 dataURL(base64)。非图片返回空串。"""
    import base64
    _, meta, orig = _resolve_upload(file_id)
    if not orig or not os.path.isfile(orig):
        return ""
    ext = (meta.get("name", "").split(".")[-1] or "").lower()
    if ext not in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
        return ""
    with open(orig, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}.get(ext, "application/octet-stream")
    return f"data:{mime};base64,{b64}"

