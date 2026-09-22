"""
共享库客户端工具 — 访问局域网服务器上的 C4EAI 共享实例
    shared_upload: 把本地文件上传到共享库（服务器自动解析+入知识索引，全组可搜）
    shared_search: 在共享库上做语义搜索（服务器上的知识索引）

服务器地址由环境变量 C4EAI_HUB 指定，例如：
    C4EAI_HUB=http://<HUB-IP>:8088
未配置时工具返回明确错误，不静默失败。
"""
import json
import os
from pathlib import Path

import httpx

_HUB_ENV = "C4EAI_HUB"
_DEFAULT_HUB = "http://<HUB-IP>:8088"   # 组内共享库默认地址（服务器已更名：c4eai / c4eai.lab；start.bat 环境变量传递不可靠，此处兜底）


def _hub_base() -> str:
    return os.environ.get(_HUB_ENV, "").rstrip("/") or _DEFAULT_HUB


def _token() -> str:
    """身份来源：1) 环境变量 C4EAI_HUB_TOKEN；2) 本地实例网页登录后保存的共享库会话（推荐，零配置）"""
    tok = os.environ.get("C4EAI_HUB_TOKEN", "").strip()
    if tok:
        return tok
    try:
        p = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "workspace", "_auth", "hub_session.json",
        )
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return str(d.get("token", "") or "")
    except Exception:
        return ""


def _hub_headers() -> dict:
    """共享库若启用了账号鉴权，本地实例用 C4EAI_HUB_TOKEN 提供身份"""
    tok = _token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _no_hub() -> str:
    return json.dumps(
        {"error": f"未配置共享服务器：请设置环境变量 {_HUB_ENV}，例如 http://<HUB-IP>:8088"},
        ensure_ascii=False)


def _workspace_dir() -> Path:
    return Path(__file__).parent.parent.parent / "workspace"


def _friendly(resp_text: str) -> str:
    """把服务器 401/403 翻成人话：401=需要登录；403=已注册但数据库权限未开通。"""
    try:
        d = json.loads(resp_text)
        err = str(d.get("error", ""))
        if "未登录" in err or "会话" in err:
            return json.dumps({
                "error": "共享服务器需要登录身份：请在网页右上角点击「登录」一次即可，无需手动配置 token。"
            }, ensure_ascii=False)
        if "授权" in err or "approved" in err.lower():
            return json.dumps({
                "error": err or "已注册，等待管理员开通数据库权限后即可使用。"
            }, ensure_ascii=False)
    except Exception:
        pass
    return resp_text


async def shared_upload(file_id: str = "", path: str = "") -> str:
    """把本地文件上传到组内共享库（局域网服务器 C4EAI 实例）。

    file_id: 本地上传文件的 id（uploads 目录里的 8 位 id）；
    path:    workspace 内相对路径或绝对路径。
    二选一必填。上传成功后服务器会解析文本并入知识索引，全组都能 shared_search 到。
    """
    base = _hub_base()
    if not base:
        return _no_hub()

    local = None
    if file_id:
        from tools.file_tools import _resolve_upload
        d, meta, orig = _resolve_upload(file_id)
        if orig and os.path.isfile(orig):
            local = Path(orig)
        if local is None:
            return json.dumps({"error": f"本地找不到 file_id={file_id} 的上传文件"}, ensure_ascii=False)
    elif path:
        p = Path(path)
        if not p.is_absolute():
            p = _workspace_dir() / path
        if not p.is_file():
            return json.dumps({"error": f"本地找不到文件: {path}"}, ensure_ascii=False)
        local = p
    else:
        return json.dumps({"error": "必须提供 file_id 或 path 之一"}, ensure_ascii=False)

    try:
        data = local.read_bytes()
    except Exception as e:
        return json.dumps({"error": f"读取本地文件失败: {e}"}, ensure_ascii=False)

    timeout = httpx.Timeout(connect=30, read=600, write=600, pool=30)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/api/file/upload?source=database",
                                  files={"file": (local.name, data)},
                                  headers=_hub_headers())
        return _friendly(r.text)
    except Exception as e:
        return json.dumps({"error": f"上传到共享服务器失败: {e}"}, ensure_ascii=False)


async def publish_note_to_db(note_id: str, owner: str, base: str = "", token: str = "",
                             entry_id: str = "") -> dict:
    """把一条本地笔记「原样复制」到共享数据库（云端）。

    - 文本原样复制
    - 附件 {{file:id:名}} 与图片 ![](/api/file/id) 引用的文件逐个复制到云端池，
      正文里的 file_id 换成云端新 id（本地笔记保持不动）
    - 云端新建同结构条目（branch_id=__db__），返回云端条目 id

    base/token 不传则用共享库配置（本地实例场景）；云端实例由调用方传入自身地址+当前用户 token。
    """
    import re
    from tools.file_tools import _resolve_upload, _read_notes, _find_note

    base = (base or _hub_base()).rstrip("/")
    if not base:
        return {"error": _no_hub()}
    tok = token or _token()
    if not tok:
        return {"error": "共享服务器需要登录身份：请先在网页右上角点击「登录」一次"}
    headers = {"Authorization": f"Bearer {tok}"}

    note, _ = _find_note(owner, note_id)
    if note is None:
        return {"error": "笔记不存在"}
    content = note.get("content", "") or ""
    title = note.get("title") or "未命名笔记"

    # 1) 收集正文里引用的所有本地 file_id（附件占位符 + 图片/文件链接）
    ids = set(re.findall(r"\{\{file:([0-9a-fA-F]{8,}):", content))
    ids |= set(re.findall(r"/api/file/([0-9a-fA-F]{8,})", content))

    timeout = httpx.Timeout(connect=30, read=600, write=600, pool=30)
    id_map, failures = {}, []
    entry_id_in = entry_id or ""
    updated = False
    async with httpx.AsyncClient(timeout=timeout) as client:
        for fid in sorted(ids):
            d, meta, orig = _resolve_upload(fid)
            if not orig or not os.path.isfile(orig):
                failures.append(f"{fid}(本地文件丢失)")
                continue
            try:
                data = Path(orig).read_bytes()
            except Exception as e:
                failures.append(f"{fid}(读取失败:{e})")
                continue
            try:
                r = await client.post(f"{base}/api/file/upload?source=database&no_entry=1",
                                      files={"file": (meta.get("name", Path(orig).name), data,
                                                      meta.get("mime") or "application/octet-stream")},
                                      headers=headers)
                j = r.json()
                new_id = j.get("file_id", "")
                if r.status_code == 200 and new_id:
                    id_map[fid] = new_id
                else:
                    failures.append(f"{fid}(云端返回:{j.get('error') or r.status_code})")
            except Exception as e:
                failures.append(f"{fid}(上传异常:{e})")

        # 2) 正文里的旧 id 全部替换为云端新 id（原样保留其余文本/格式）
        new_content = content
        for old, new in id_map.items():
            new_content = new_content.replace(old, new)

        # 3) 云端建同结构条目（已上传过 → 更新同一条，避免堆重复）
        try:
            if entry_id_in:
                r = await client.put(f"{base}/api/notes",
                                     json={"note_id": entry_id_in, "title": title,
                                           "content": new_content, "branch_id": "__db__"},
                                     headers={**headers, "Content-Type": "application/json"})
            else:
                r = await client.post(f"{base}/api/notes",
                                      json={"title": title, "content": new_content, "branch_id": "__db__"},
                                      headers={**headers, "Content-Type": "application/json"})
            j = r.json()
            if r.status_code != 200 or j.get("status") != "ok":
                return {"error": f"云端写条目失败：{j.get('error') or r.status_code}"}
            entry_id = entry_id_in or j.get("note_id") or j.get("id") or ""
            updated = bool(entry_id_in)
        except Exception as e:
            return {"error": f"云端写条目异常：{e}"}

    return {"status": "ok", "entry_id": entry_id, "title": title, "updated": updated,
            "files_copied": len(id_map), "files_failed": failures,
            "content_len": len(new_content)}

async def db_notes(action: str, note_id: str = "", title: str = "", content: str = "",
                   owner: str = "guest") -> dict:
    """数据库条目（云端 branch=__db__）统一入口 —— 给智能体工具用。

    - 云端实例（C4EAI_AUTH=1）：数据库就在本机 users/*/notes.json 的 __db__ 分支 → 直接读写
    - 本地实例：数据库唯一在云端 → 走 hub HTTP（带本地已存的登录会话）
    返回 {status: 'ok', ...} 或 {error: ...}
    """
    import os as _os
    db_branch = "__db__"
    if _os.environ.get("C4EAI_AUTH", "") == "1":
        from tools import file_tools as _ft
        if action == "list":
            # 权限：L3(橙) 起可查询**所有人**的条目；L0-L2 只能看自己的
            try:
                import role_policy as _rp
                _can_all = _rp.can_query_all({"username": owner})
            except Exception:
                _can_all = False
            if _can_all:
                # 用现成的跨用户汇总函数（admin 面板同款），每条已带 owner
                try:
                    notes = [n for n in _ft.list_all_users_notes()
                             if n.get("branch_id", "") == db_branch]
                except Exception:
                    notes = [n for n in _ft._read_notes(owner) if n.get("branch_id", "") == db_branch]
            else:
                notes = [n for n in _ft._read_notes(owner) if n.get("branch_id", "") == db_branch]
            # ⚠️ 只返回「目录」：不含正文（正文可能几十万字，全量返回会撑爆上下文）。
            # 智能体要正文必须再调 read（按 id），由 read 决定给多少。
            def _slim(n):
                c = n.get("content") or ""
                return {"id": n["id"], "title": n.get("title", ""),
                        "owner": n.get("owner", owner), "content_len": len(c),
                        "preview": c[:120].replace("\n", " ") + ("…" if len(c) > 120 else "")}
            return {"status": "ok", "total": len(notes), "entries": [_slim(n) for n in notes],
                    "note": "以上仅为条目目录（不含正文）。需要某条正文时用 read 动作按 id 读取。"}
        if action == "read":
            n, _ = _ft._find_note(owner, note_id)
            if n is None:
                return {"error": "条目不存在"}
            c = n.get("content") or ""
            _cap = 20000
            _trunc = len(c) > _cap
            return {"status": "ok", "entry": {"id": n["id"], "title": n.get("title", ""),
                                             "owner": n.get("owner", owner),
                                             "content": c[:_cap], "content_len": len(c),
                                             "truncated": _trunc,
                                             "hint": (f"原文共 {len(c)} 字符，此处仅返回前 {_cap} 字符；"
                                                      f"如需后续内容请用 append/update 前先确认，或让用户分段查看。"
                                                      if _trunc else "")}}
        if action in ("add", "append"):
            if action == "append":
                n, _ = _ft._find_note(owner, note_id)
                if n is None:
                    return {"error": "条目不存在"}
                new_content = ((n.get("content") or "") + "\n\n" + content) if (n.get("content") or "") else content
                r = json.loads(await _ft.update_note(note_id, None, new_content, owner))
                return {"status": "ok", "id": note_id} if "error" not in r else r
            r = json.loads(await _ft.add_note(title or "新条目", content or "", db_branch, owner))
            return r
        if action == "update":
            r = json.loads(await _ft.update_note(note_id, title or None, content, owner))
            return {"status": "ok", "id": note_id} if "error" not in r else r
        if action == "delete":
            r = json.loads(await _ft.delete_note(note_id, owner))
            return {"status": "ok", "id": note_id} if "error" not in r else r
        return {"error": f"未知操作: {action}"}

    # ── 本地实例：转发云端 ──
    base = _hub_base()
    tok = _token()
    if not tok:
        return {"error": "数据库在共享服务器上：请先在网页右上角点击「登录」一次"}
    timeout = httpx.Timeout(connect=30, read=300, write=300, pool=30)
    headers = {"Authorization": f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        if action in ("list", "read"):
            r = await client.get(f"{base}/api/notes?branch_id={db_branch}", headers=headers)
            j = r.json()
            if r.status_code != 200:
                return {"error": j.get("error") or r.status_code}
            notes = j.get("notes", [])
            if action == "read":
                n = next((x for x in notes if x.get("id") == note_id), None)
                return {"status": "ok", "entry": n} if n else {"error": "条目不存在"}
            return {"status": "ok", "total": len(notes),
                    "entries": [{"id": x.get("id"), "title": x.get("title", ""),
                                 "content": x.get("content", ""), "owner": x.get("owner", "")} for x in notes]}
        if action == "append":
            r = await client.get(f"{base}/api/notes?branch_id={db_branch}", headers=headers)
            notes = r.json().get("notes", [])
            n = next((x for x in notes if x.get("id") == note_id), None)
            if n is None:
                return {"error": "条目不存在"}
            content = ((n.get("content") or "") + "\n\n" + content) if (n.get("content") or "") else content
            action = "update"
        if action == "add":
            r = await client.post(f"{base}/api/notes",
                                  json={"title": title or "新条目", "content": content or "", "branch_id": db_branch},
                                  headers={**headers, "Content-Type": "application/json"})
        elif action == "update":
            r = await client.put(f"{base}/api/notes",
                                 json={"note_id": note_id, "title": title or None, "content": content,
                                       "branch_id": db_branch},
                                 headers={**headers, "Content-Type": "application/json"})
        else:  # delete
            r = await client.delete(f"{base}/api/notes/{note_id}?source=cloud", headers=headers)
        j = r.json()
        if r.status_code != 200 or (isinstance(j, dict) and j.get("error")):
            return {"error": j.get("error") if isinstance(j, dict) else r.status_code}
        if action == "add":
            j.setdefault("id", j.get("note_id") or "")
        else:
            j["id"] = note_id
        return j


async def shared_search(query: str, top_k: int = 5) -> str:
    """在组内共享库（服务器上所有人上传的文献/文档）中做语义搜索。"""
    base = _hub_base()
    if not base:
        return _no_hub()
    timeout = httpx.Timeout(connect=30, read=120, write=120, pool=30)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/api/search/knowledge",
                                  json={"query": query, "top_k": top_k},
                                  headers=_hub_headers())
        return _friendly(r.text)
    except Exception as e:
        return json.dumps({"error": f"共享服务器搜索失败: {e}"}, ensure_ascii=False)
