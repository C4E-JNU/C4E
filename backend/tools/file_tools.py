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

# 单次读取/注入返回给模型的正文字符封顶：防止长行 JSON / 超大文档一次撑爆模型上下文
READ_FILE_MAX_CHARS = 20000
# 历史拼装时，单条消息与总上下文的字符预算（1 token ≈ 2~3 汉字 / 4 英文，留足安全余量）
HISTORY_MSG_MAX_CHARS = 8000
HISTORY_TOTAL_MAX_CHARS = 300000


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

    write=False 表示读/列目录；write=True 表示写文件。
    相对路径以 workspace/ 为基准；绝对路径直接使用（非安全模式下可访问项目/任意路径）。
    """
    mode = _PERMISSION_MODE
    workspace = _get_workspace_dir()
    # 确保 workspace 目录存在
    os.makedirs(workspace, exist_ok=True)

    # 规范化路径：相对路径以 workspace 为基准，绝对路径直接用
    if os.path.isabs(relative_path):
        full_path = os.path.abspath(relative_path)
    else:
        full_path = os.path.abspath(os.path.join(workspace, relative_path))

    if mode == "full":
        return full_path  # 完全访问：任意路径读写

    workspace_abs = os.path.abspath(workspace)
    project_abs = os.path.abspath(_get_project_root())

    if mode == "safe":
        # 仅 workspace
        if not _within(workspace_abs, full_path):
            raise PermissionError(f"安全模式下仅允许访问 workspace 目录: {relative_path}")
        # 云端共享库：普通成员进一步锁到本人目录 users/<u>/ 与共享 pool/
        if _USER_SCOPE:
            u = (_USER_SCOPE.get("user") or "guest").replace("..", "_")
            me = os.path.abspath(os.path.join(workspace_abs, "users", u))
            pool = os.path.abspath(os.path.join(workspace_abs, "pool"))
            if not (_within(me, full_path) or _within(pool, full_path) or full_path == me):
                raise PermissionError(f"云端实例仅可访问自己的文件与共享库: {relative_path}")
        return full_path

    # ask 询问模式
    if not write:
        # 读：允许 workspace + 项目根（含 skills/）
        if _within(workspace_abs, full_path) or _within(project_abs, full_path):
            return full_path
        raise PermissionError(f"询问模式下仅可读取项目与 workspace 目录: {relative_path}")
    # 写：允许 workspace + skills 目录（写技能作为例外放行）
    skills_abs = os.path.abspath(_get_skills_dir())
    if _within(workspace_abs, full_path) or _within(skills_abs, full_path):
        return full_path
    raise PermissionError("__NEED_FULL__ 该写操作超出 workspace 与 skills 目录，需要切换到完全访问模式才能执行")


async def read_file(path: str = "", file_id: str = "", as_base64: bool = False, offset: int = 1, limit: int = 500):
    """读取文件内容。统一入口：
    - 提供 file_id（上传附件 id）时，按附件读取：文档返回解析全文，图片(或 as_base64=True)返回 base64(dataURL)
    - 否则按 path 读取磁盘文件（原逻辑，行号分页）
    Args:
        path: 相对于 workspace/ 的文件路径
        file_id: 上传附件的 file_id（优先于 path）
        as_base64: 当按 file_id 且是文档时，仍尝试返回原始字节 base64（图片默认返回 base64）
        offset: 起始行号 (1-indexed)
        limit: 最多返回多少行
    注意：单次返回【按字符数封顶】(READ_FILE_MAX_CHARS)，防止长行 JSON/大文档一次撑爆模型上下文。
    """
    # 附件模式：按 file_id 读上传的文件
    if (file_id or "").strip():
        fid = file_id.strip()
        # 图片 → 返回 base64(dataURL)；文档 → 返回解析全文
        data_url = await image_to_base64(fid)
        if data_url:
            if len(data_url) > READ_FILE_MAX_CHARS:
                return json.dumps({"ok": True, "source": "attachment", "file_id": fid, "mode": "image_too_large",
                                   "hint": f"图片 base64 过大（{len(data_url)} 字符），未内联返回以免撑爆上下文；"
                                           f"如需读取内容请用 vision/OCR 或指定更小图片。"}, ensure_ascii=False)
            return json.dumps({"ok": True, "source": "attachment", "file_id": fid, "mode": "base64", "data_url": data_url}, ensure_ascii=False)
        text = await get_upload_text(fid)
        if text:
            clipped = text[:READ_FILE_MAX_CHARS]
            truncated = len(text) > READ_FILE_MAX_CHARS
            return json.dumps({"ok": True, "source": "attachment", "file_id": fid, "mode": "text",
                               "truncated": truncated,
                               "note": (f"文件正文 {len(text)} 字符，已截断至 {READ_FILE_MAX_CHARS} 字符；"
                                        "如需后续内容请改用 offset/limit 分页读取，或告诉我需要的具体段落。") if truncated else "",
                               "content": clipped}, ensure_ascii=False)
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
                                                f"图片请用 read_image，扫描 PDF 可试 mineru_parse）"},
                                      ensure_ascii=False)
                _clip = _txt[:READ_FILE_MAX_CHARS]
                return json.dumps({"ok": True, "source": "attachment", "file_id": fid, "mode": "text",
                                   "on_demand": True, "truncated": len(_txt) > READ_FILE_MAX_CHARS,
                                   "note": "该附件未预先解析（笔记附件），以上为本次即时提取的原文；未写入索引。",
                                   "content": _clip}, ensure_ascii=False)
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

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        start = max(0, offset - 1)
        end = min(total_lines, start + limit)

        content = "".join(lines[start:end])
        # 字符封顶：即便命中行数少、但单行超长（如整份 JSON 一行），也不会一次吐出超大文本
        total_chars = len("".join(lines))
        clipped = content[:READ_FILE_MAX_CHARS]
        truncated = len(content) > READ_FILE_MAX_CHARS
        next_offset = None
        note = ""
        if truncated:
            # 找到截断点所在行，告诉模型下次从哪行继续（分页续读）
            acc = 0
            consumed = 0
            for ln in lines[start:end]:
                if acc + len(ln) > READ_FILE_MAX_CHARS:
                    break
                acc += len(ln)
                consumed += 1
            next_offset = start + consumed + 1
            note = (f"本段 {len(content)} 字符已截断至 {READ_FILE_MAX_CHARS}。"
                    f"该文件共 {total_lines} 行 / {total_chars} 字符。"
                    f"要继续读，请用 offset={next_offset} 再次调用（limit 建议 ≤500）。"
                    "注意：勿整读巨型 JSON（layout.json / *_model.json / *_content_list_v2.json），正文用 00_full_cleaned.md。")

        return json.dumps({
            "path": path,
            "total_lines": total_lines,
            "offset": offset,
            "limit": limit,
            "truncated": truncated,
            "next_offset": next_offset,
            "note": note,
            "content": clipped,
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


async def get_upload_text(file_id: str) -> str:
    """获取上传文件的完整文本内容（跨 pool/私人区/旧目录解析）"""
    d, _, _ = _resolve_upload(file_id)
    if not d:
        return ""
    text_path = os.path.join(d, "text.txt")
    if not os.path.isfile(text_path):
        return ""
    with open(text_path, "r", encoding="utf-8") as f:
        return f.read()


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

