"""
共享库客户端工具 — 访问局域网服务器上的 C4EAI 共享实例
    shared_upload: 把本地文件上传到共享库（服务器自动解析+入知识索引，全组可搜）
    shared_search: 在共享库上做语义搜索（服务器上的知识索引）

服务器地址由环境变量 C4EAI_HUB 指定，例如：
    C4EAI_HUB=http://<你的服务器>:8088
未配置时工具返回明确错误，不静默失败。
"""
import json
import os
from pathlib import Path

import httpx

_HUB_ENV = "C4EAI_HUB"


def _hub_base() -> str:
    """共享库地址（环境变量 C4EAI_HUB）。

    已配置 → 库在服务器（本地实例，start.bat 里设了）
    未配置 → 库就在本机（云端实例，start_c4eai.sh 不设此变量）

    调用方据此分流：有地址走 HTTP，没地址直读本地索引。
    """
    return os.environ.get(_HUB_ENV, "").rstrip("/")


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

    base = _hub_base()
    if not base:
        # 库就在本机（云端实例）：直接写 pool 并解析入索引
        from tools.file_tools import upload_file as _upload_file
        res = await _upload_file(local.name, data, "system", "database")
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)

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
        # 没地址 = 库在本机（云端实例），却调了这个「把本地附件推到服务器」的函数
        return {"error": "云端实例无需推送：共享库就在本机，请直接写 pool"} 
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
                # ★ 解析的唯一触发点是「新内容第一次从共享库外部进入共享库」。
                # 个人库发布属于「已有内容搬进来」→ **不解析**（用户 2026-09-25 明确要求）。
                # 故这里固定 no_entry=1&parse=0：不另建条目、不解析、不入索引。
                r = await client.post(f"{base}/api/file/upload?source=database&no_entry=1&parse=0",
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
                   owner: str = "guest", kind: str = "") -> dict:
    """数据库条目（云端 branch=__db__）统一入口 —— 给智能体工具用。

    - 云端实例（C4EAI_AUTH=1）：数据库就在本机 users/*/notes.json 的 __db__ 分支 → 直接读写
    - 本地实例：数据库唯一在云端 → 走 hub HTTP（带本地已存的登录会话）
    kind: 新建条目时的分类（note=笔记/document=文档条目）；留空则用默认 note。
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
            r = json.loads(await _ft.add_note(title or "新条目", content or "", db_branch, owner,
                                              kind=(kind or "note")))
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
            # kind 由调用方指定：个人笔记上传→note；文档解析→document（用户 2026-09-22 定）
            r = await client.post(f"{base}/api/notes",
                                  json={"title": title or "新条目", "content": content or "",
                                        "branch_id": db_branch, "kind": (kind or "note")},
                                  headers={**headers, "Content-Type": "application/json"})
        elif action == "update":
            # kind 必须一并转发：迁移/改分类靠它（漏传则 kind 永远写不进去）
            _upd = {"note_id": note_id, "title": title or None, "content": content,
                    "branch_id": db_branch}
            if kind:
                _upd["kind"] = kind
            r = await client.put(f"{base}/api/notes", json=_upd,
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
    """在组内共享库（服务器上所有人上传的文献/文档）中做语义搜索。

    有 C4EAI_HUB 地址 → 库在服务器 → HTTP 转发（本地实例）
    没地址           → 库就在本机 → 直读本地索引（云端实例）
    """
    base = _hub_base()
    if not base:
        from tools.vector_search import search_knowledge as _local_search
        return await _local_search(query, top_k)

    timeout = httpx.Timeout(connect=30, read=120, write=120, pool=30)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/api/search/knowledge",
                                  json={"query": query, "top_k": top_k},
                                  headers=_hub_headers())
        return _friendly(r.text)
    except Exception as e:
        return json.dumps({"error": f"共享服务器搜索失败: {e}"}, ensure_ascii=False)


# ═══════════════════════════════════════════════
# 共享知识库统一工具 —— 检索 + 分组管理（用户 2026-09-25 定）
# ═══════════════════════════════════════════════

def _resolve_group_id(gid: str) -> str:
    """支持用 8 位短 id（工具描述里显示的就是短 id）——按前缀匹配补全。

    唯一匹配才补全；歧义/找不到则原样返回（让下游报「分组不存在」）。
    """
    gid = (gid or "").strip()
    if not gid:
        return ""
    from tools.folder_tree import _load
    folders = _load()
    if any(f.get("id") == gid for f in folders):
        return gid
    hits = [f["id"] for f in folders if f.get("id", "").startswith(gid)]
    return hits[0] if len(hits) == 1 else gid


async def knowledge(action: str = "search", query: str = "", top_k: int = 5,
                    group_id: str = "", name: str = "", parent_id: str = "",
                    intro: str = "", mode: str = "move_up",
                    entry_id: str = "", to_group_id: str = "",
                    owner: str = "", year: str = "", sort: str = "",
                    kind: str = "", summary_only: bool = False) -> str:
    """共享知识库统一入口：语义检索 + 分组树管理 + 条目归类。

    分组数据在共享库（云端 pool/_folders.json）：
      云端实例（无 C4EAI_HUB）→ 直接操作本地文件
      本地实例（有 C4EAI_HUB）→ HTTP 转发到云端
    """
    action = (action or "search").strip().lower()

    if action == "search":
        if not (query or "").strip():
            return json.dumps({"error": "search 需要提供 query"}, ensure_ascii=False)
        return await shared_search(query, top_k)

    # ── 分组类操作 ──
    _ACTIONS = {"list_groups", "create_group", "update_group", "move_group", "delete_group",
                "list_entries", "move_entry"}
    if action not in _ACTIONS:
        return json.dumps({"error": f"未知 action: {action}；可用: search / " + " / ".join(sorted(_ACTIONS))},
                          ensure_ascii=False)

    gid = _resolve_group_id(group_id)
    pid = _resolve_group_id(parent_id) if parent_id else ""
    tgid = _resolve_group_id(to_group_id) if to_group_id else ""

    base = _hub_base()
    if base:
        # 本地实例：转发到云端（云端的 /api/groups 与 /api/notes 都能处理）
        if action in ("list_entries", "move_entry"):
            return await _entries_via_hub(action, base, gid, entry_id, tgid,
                                          owner=owner, year=year, sort=sort,
                                          kind=kind, summary_only=summary_only)
        payload = {"action": action, "group_id": gid, "name": name,
                   "parent_id": pid, "intro": intro, "mode": mode}
        timeout = httpx.Timeout(connect=30, read=120, write=120, pool=30)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{base}/api/groups", json=payload,
                                      headers=_hub_headers())
            return _friendly(r.text)
        except Exception as e:
            return json.dumps({"error": f"共享服务器分组操作失败: {e}"}, ensure_ascii=False)

    # 云端实例：直接操作本地
    import tools.folder_tree as FT
    if action == "list_groups":
        return FT.list_groups()
    if action == "create_group":
        return FT.create_group(name, pid, intro)
    if action == "update_group":
        return FT.update_group(gid, name or None, intro if intro else None)
    if action == "move_group":
        return FT.move_group(gid, pid)
    if action == "delete_group":
        return FT.delete_group(gid, mode)
    if action == "list_entries":
        return await _list_entries_local(gid, owner, year, sort, kind, summary_only)
    if action == "move_entry":
        return await _move_entry_local(entry_id, tgid)
    return json.dumps({"error": f"未实现: {action}"}, ensure_ascii=False)


async def _list_entries_local(group_id: str = "", owner: str = "",
                              year: str = "", sort: str = "",
                              kind: str = "", summary_only: bool = False) -> str:
    """列出共享库条目（默认**不含正文** —— 目录用途，避免撑爆上下文）。

    过滤条件（全部**下推到后端**，不要先 list 全部再让模型自己筛）：
      group_id     — 只列某分组（""=全部；"__root__"=未分组）
      owner        — 只列某用户上传的（""=全部）
      year         — 只列某年上传的（"2026"；按 createdAt，""=全部）
      kind         — "note" / "document"（""=全部）
      sort         — "time"（默认，新→旧）/ "time_asc" / "title"
      summary_only — True 时**只返回各分组的条目计数**，不返回明细
                     （智能体先看「哪个组有货」，再决定按组拉明细 —— 省 token）
    """
    from tools.folder_tree import _load as _load_folders, _recompute_paths
    from tools.file_tools import list_all_users_notes

    notes = list_all_users_notes() or []
    notes = [n for n in notes if (n.get("branch_id") or "") == "__db__"]
    if group_id == "__root__":
        notes = [n for n in notes if not (n.get("folder_id") or "")]
    elif group_id:
        notes = [n for n in notes if (n.get("folder_id") or "") == group_id]
    if owner:
        notes = [n for n in notes if (n.get("owner") or "") == owner]
    if kind:
        notes = [n for n in notes if (n.get("kind") or "note") == kind]
    if year:
        notes = [n for n in notes
                 if str(n.get("createdAt") or n.get("updatedAt") or "").startswith(str(year))]

    if sort == "title":
        notes.sort(key=lambda n: (n.get("title") or ""))
    elif sort == "time_asc":
        notes.sort(key=lambda n: str(n.get("createdAt") or ""))
    else:
        notes.sort(key=lambda n: str(n.get("createdAt") or ""), reverse=True)

    if summary_only:
        folders = _load_folders()
        _recompute_paths(folders)
        fname = {f["id"]: f for f in folders}
        buckets = {}
        for n in notes:
            fid = n.get("folder_id") or ""
            buckets[fid] = buckets.get(fid, 0) + 1
        groups = []
        for fid, cnt in buckets.items():
            f = fname.get(fid)
            groups.append({
                "group_id": fid,
                "name": (f or {}).get("name") or "（未分组）",
                "path": (f or {}).get("path") or "（未分组）",
                "intro": (f or {}).get("intro") or "",
                "count": cnt,
            })
        groups.sort(key=lambda g: -g["count"])
        return json.dumps({
            "status": "ok", "mode": "summary_only",
            "total_entries": len(notes), "groups": groups,
            "note": "以上是各分组的条目数（不含明细）。要看某组明细再用 "
                    "action=list_entries 并传 group_id。",
        }, ensure_ascii=False)

    items = [{"id": n.get("id"), "title": n.get("title"),
              "kind": n.get("kind") or "note",
              "folder_id": n.get("folder_id") or "",
              "owner": n.get("owner") or "",
              "created": str(n.get("createdAt") or "")} for n in notes]
    return json.dumps({"status": "ok", "total": len(items), "entries": items},
                      ensure_ascii=False)


async def _move_entry_local(entry_id: str, to_group_id: str) -> str:
    """把共享库条目移到指定分组（空 = 移出分组）。"""
    if not (entry_id or "").strip():
        return json.dumps({"error": "move_entry 需要 entry_id"}, ensure_ascii=False)
    from tools.file_tools import update_note, _find_note
    n, okey = _find_note("xhq1", entry_id)
    if n is None:
        return json.dumps({"error": f"条目不存在: {entry_id}"}, ensure_ascii=False)
    await update_note(entry_id, None, None, okey, folder_id=to_group_id or "")
    return json.dumps({"status": "ok", "entry_id": entry_id,
                       "folder_id": to_group_id or ""}, ensure_ascii=False)


async def _entries_via_hub(action: str, base: str, group_id: str,
                           entry_id: str, to_group_id: str,
                           owner: str = "", year: str = "", sort: str = "",
                           kind: str = "", summary_only: bool = False) -> str:
    """本地实例：条目类操作转发到云端。"""
    timeout = httpx.Timeout(connect=30, read=120, write=120, pool=30)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if action == "list_entries":
                q = "/api/notes?branch_id=__db__"
                if group_id:
                    q += f"&folder_id={group_id}"
                if kind:
                    q += f"&kind={kind}"
                r = await client.get(base + q, headers=_hub_headers())
                d = json.loads(_friendly(r.text))
                notes = d.get("notes") or []
                # owner / year / sort 本地侧再过滤
                # （云端 /api/notes 只支持 folder_id / kind；但结果默认不含正文，token 可接受）
                if owner:
                    notes = [n for n in notes if (n.get("owner") or "") == owner]
                if year:
                    notes = [n for n in notes if str(n.get("createdAt") or n.get("updatedAt")
                                                     or "").startswith(str(year))]
                if sort == "title":
                    notes.sort(key=lambda n: (n.get("title") or ""))
                elif sort == "time_asc":
                    notes.sort(key=lambda n: str(n.get("createdAt") or ""))
                else:
                    notes.sort(key=lambda n: str(n.get("createdAt") or ""), reverse=True)
                if summary_only:
                    buckets = {}
                    for n in notes:
                        fid = n.get("folder_id") or ""
                        buckets[fid] = buckets.get(fid, 0) + 1
                    return json.dumps({"status": "ok", "mode": "summary_only",
                                       "total_entries": len(notes),
                                       "groups": [{"group_id": k, "count": v}
                                                  for k, v in sorted(buckets.items(),
                                                                     key=lambda x: -x[1])]},
                                      ensure_ascii=False)
                items = [{"id": n.get("id"), "title": n.get("title"),
                          "kind": n.get("kind") or "note",
                          "folder_id": n.get("folder_id") or "",
                          "owner": n.get("owner") or "",
                          "created": str(n.get("createdAt") or "")} for n in notes]
                return json.dumps({"status": "ok", "total": len(items), "entries": items},
                                  ensure_ascii=False)
            r = await client.put(base + "/api/notes",
                                 json={"note_id": entry_id, "branch_id": "__db__",
                                       "folder_id": to_group_id or ""},
                                 headers=_hub_headers())
            return _friendly(r.text)
    except Exception as e:
        return json.dumps({"error": f"共享服务器条目操作失败: {e}"}, ensure_ascii=False)
