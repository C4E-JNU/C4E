"""
XWiki REST API 客户端 + LangChain 工具函数

连接 138 上 Docker 部署的 XWiki (端口 8090)。

实测要点（勿改回）：
  1. 用户名大小写敏感，真实账号是 HaiqiangXie（小写 q），不是 HaiQiangXie。
  2. /rest/wikis/xwiki/search 走 Solr 索引；本实例从老服务器迁移后索引为空，
     该端点恒返回空，所以检索一律走 /query（XWQL，直查 MySQL）。
  3. 中文空间名必须做 URL 编码（用 _seg() 处理路径段）。
"""
import os
import json
import base64
import logging
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
_XWIKI_BASE = os.environ.get("XWIKI_BASE", "")
_XWIKI_USER = os.environ.get("XWIKI_USER", "HaiqiangXie")
_XWIKI_PASS = os.environ.get("XWIKI_PASS", "xhq15297718955")
_XWIKI_WIKI = os.environ.get("XWIKI_WIKI", "xwiki")

# 模块级 AsyncClient（复用连接）
_client: Optional[httpx.AsyncClient] = None


def _seg(s: str) -> str:
    """路径段编码（中文空间名/页面名必需）"""
    return quote(str(s), safe="")


def _auth_header() -> str:
    cred = f"{_XWIKI_USER}:{_XWIKI_PASS}"
    return "Basic " + base64.b64encode(cred.encode()).decode()


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _auth_header(),
        "User-Agent": "C4EAI-XWiki-Client/1.0",
    }


async def _request(method: str, path: str, params: dict = None, **kwargs) -> dict:
    """统一发请求 + 处理响应"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    url = f"{_XWIKI_BASE}/rest{path}"
    try:
        resp = await _client.request(method, url, headers=_headers(),
                                     params=params, **kwargs)
    except Exception as e:
        return {"error": f"XWiki 连接失败: {type(e).__name__}: {e}"}

    if resp.status_code == 401:
        return {"error": "XWiki 认证失败：请检查 XWIKI_USER / XWIKI_PASS"}
    if resp.status_code == 403:
        return {"error": "XWiki 权限不足：该账号无权执行此操作"}
    if resp.status_code == 404:
        return {"error": f"XWiki 资源不存在: {path}"}
    if not resp.is_success:
        return {"error": f"XWiki HTTP {resp.status_code}: {resp.text[:300]}"}
    if resp.status_code == 204 or not resp.content:
        return {"success": True}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:2000]}


def _xwql_like(term: str) -> str:
    """XWQL 字符串字面量：转义单引号与反斜杠"""
    return term.replace("\\", "\\\\").replace("'", "''")


# ── 检索（走 XWQL，Solr 索引为空时的唯一可用路径） ──

async def search_wiki(query: str, limit: int = 10, scope: str = "all") -> dict:
    """按关键词检索标题/正文。scope: all | title | content"""
    t = _xwql_like(query)
    if scope == "title":
        where = f"doc.title like '%{t}%'"
    elif scope == "content":
        where = f"doc.content like '%{t}%'"
    else:
        where = f"doc.title like '%{t}%' or doc.content like '%{t}%'"
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/query",
                          params={"q": f"where {where}", "type": "xwql",
                                  "number": limit})


async def get_page(space: str, page_name: str) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces/{_seg(space)}/pages/{_seg(page_name)}")


async def create_page(space: str, page_name: str, content: str,
                      title: str = "", parent: str = "") -> dict:
    body = {"title": title or page_name, "content": content, "syntax": "xwiki/2.1"}
    if parent:
        body["parent"] = parent
    return await _request("PUT", f"/wikis/{_XWIKI_WIKI}/spaces/{_seg(space)}/pages/{_seg(page_name)}",
                          json=body)


async def update_page(space: str, page_name: str, content: str,
                      title: str = "") -> dict:
    cur = await get_page(space, page_name)
    if "error" in cur:
        return cur
    body = {"title": title or cur.get("title", page_name),
            "content": content, "syntax": "xwiki/2.1",
            "version": cur.get("version", "")}
    return await _request("PUT", f"/wikis/{_XWIKI_WIKI}/spaces/{_seg(space)}/pages/{_seg(page_name)}",
                          json=body)


async def delete_page(space: str, page_name: str) -> dict:
    r = await _request("DELETE", f"/wikis/{_XWIKI_WIKI}/spaces/{_seg(space)}/pages/{_seg(page_name)}")
    if "error" not in r:
        return {"success": True, "message": f"已删除 {space}.{page_name}"}
    return r


async def list_pages(space: str, limit: int = 50) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces/{_seg(space)}/pages",
                          params={"number": limit})


async def list_spaces(limit: int = 200, name: str = "") -> dict:
    """列出空间；name 给定时按前缀过滤（XWiki 端不支持，本地过滤）。"""
    r = await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces", params={"number": limit})
    if name and "error" not in r:
        kw = name.lower()
        r["spaces"] = [s for s in r.get("spaces", [])
                       if kw in str(s.get("name", "")).lower()]
    return r


async def upload_attachment(space: str, page_name: str, file_path: str,
                            filename: str = "") -> dict:
    fname = filename or os.path.basename(file_path)
    with open(file_path, "rb") as f:
        data = f.read()
    return await _request(
        "PUT",
        f"/wikis/{_XWIKI_WIKI}/spaces/{_seg(space)}/pages/{_seg(page_name)}/attachments/{_seg(fname)}",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


async def list_attachments(space: str, page_name: str) -> dict:
    return await _request(
        "GET", f"/wikis/{_XWIKI_WIKI}/spaces/{_seg(space)}/pages/{_seg(page_name)}/attachments")


# ── 面向大模型的工具函数（async，供 LangChain await） ──

async def wiki_search(query: str, limit: int = 10, scope: str = "all") -> str:
    r = await search_wiki(query, limit, scope)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    items = []
    for i in r.get("searchResults", []):
        content = i.get("content", "") or ""
        snip = ""
        pos = content.lower().find(query.lower())
        if pos >= 0:
            snip = content[max(0, pos - 60): pos + 140].replace("\n", " ")
        full = str(i.get("pageFullName", "")).replace("\\", "")
        items.append({
            "space": str(i.get("space", "")).replace("\\", ""),
            "page": i.get("pageName", ""),
            "title": i.get("title", ""),
            "snippet": snip[:200],
            "url": f"{_XWIKI_BASE}/bin/view/" + full.replace(".", "/"),
        })
    return json.dumps({"count": len(items), "results": items}, ensure_ascii=False, indent=2)


async def wiki_get(space: str, page: str) -> str:
    r = await get_page(space, page)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({"space": space, "page": page, "title": r.get("title", ""),
                       "content": r.get("content", ""), "version": r.get("version", ""),
                       "author": r.get("author", ""), "modified": r.get("modified", "")},
                      ensure_ascii=False, indent=2)


async def wiki_create(space: str, page: str, content: str,
                      title: str = "", parent: str = "") -> str:
    r = await create_page(space, page, content, title, parent)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({"success": True, "space": space, "page": page,
                       "url": f"{_XWIKI_BASE}/bin/view/{_seg(space)}/{_seg(page)}"},
                      ensure_ascii=False)


async def wiki_update(space: str, page: str, content: str, title: str = "") -> str:
    r = await update_page(space, page, content, title)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({"success": True, "space": space, "page": page,
                       "version": r.get("version", "")}, ensure_ascii=False)


async def wiki_delete(space: str, page: str) -> str:
    r = await delete_page(space, page)
    return json.dumps(r, ensure_ascii=False)


async def wiki_list_pages(space: str, limit: int = 50) -> str:
    r = await list_pages(space, limit)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    items = [{"page": p.get("name", ""), "title": p.get("title", ""),
              "modified": p.get("modified", "")} for p in r.get("pageSummaries", [])]
    return json.dumps({"space": space, "count": len(items), "pages": items},
                      ensure_ascii=False, indent=2)


async def wiki_list_spaces(limit: int = 200, name: str = "") -> str:
    r = await list_spaces(limit, name)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    items = [{"space": s.get("name", ""), "home": str(s.get("home", "")).replace("\\", "")}
             for s in r.get("spaces", [])]
    return json.dumps({"count": len(items), "spaces": items}, ensure_ascii=False, indent=2)


async def wiki_upload_attachment(space: str, page: str, file_path: str,
                                 filename: str = "") -> str:
    r = await upload_attachment(space, page, file_path, filename)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({"success": True, "file": filename or os.path.basename(file_path)},
                      ensure_ascii=False)


async def wiki_list_attachments(space: str, page: str) -> str:
    r = await list_attachments(space, page)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    items = [{"name": a.get("name", ""), "size": a.get("size", 0),
              "url": a.get("xwikiUrl", "")} for a in r.get("attachments", [])]
    return json.dumps({"space": space, "page": page, "count": len(items),
                       "attachments": items}, ensure_ascii=False, indent=2)


# ── 统一入口（工具只暴露 read / write 两个，action 分发） ──

_READ_ACTIONS = ("search", "get", "list_spaces", "list_pages", "list_attachments")
_WRITE_ACTIONS = ("create", "update", "delete", "upload_attachment")


async def wiki_read(action: str = "search", query: str = "", space: str = "",
                    page: str = "", limit: int = 10, scope: str = "all",
                    name: str = "") -> str:
    """只读入口：search / get / list_spaces / list_pages / list_attachments"""
    a = (action or "search").strip()
    if a == "search":
        if not query:
            return json.dumps({"error": "action=search 需要 query"}, ensure_ascii=False)
        return await wiki_search(query, limit, scope)
    if a == "get":
        if not space or not page:
            return json.dumps({"error": "action=get 需要 space 和 page"}, ensure_ascii=False)
        return await wiki_get(space, page)
    if a == "list_spaces":
        return await wiki_list_spaces(limit if limit and limit > 0 else 200, name)
    if a == "list_pages":
        if not space:
            return json.dumps({"error": "action=list_pages 需要 space"}, ensure_ascii=False)
        return await wiki_list_pages(space, limit if limit and limit > 0 else 50)
    if a == "list_attachments":
        if not space or not page:
            return json.dumps({"error": "action=list_attachments 需要 space 和 page"}, ensure_ascii=False)
        return await wiki_list_attachments(space, page)
    return json.dumps({"error": f"未知 action: {a}；可用: {', '.join(_READ_ACTIONS)}"},
                      ensure_ascii=False)


async def wiki_write(action: str = "", space: str = "", page: str = "",
                     content: str = "", title: str = "", parent: str = "",
                     file_path: str = "", filename: str = "") -> str:
    """写入入口：create / update / delete / upload_attachment"""
    a = (action or "").strip()
    if a == "create":
        if not space or not page or not content:
            return json.dumps({"error": "action=create 需要 space、page、content"}, ensure_ascii=False)
        return await wiki_create(space, page, content, title, parent)
    if a == "update":
        if not space or not page or not content:
            return json.dumps({"error": "action=update 需要 space、page、content"}, ensure_ascii=False)
        return await wiki_update(space, page, content, title)
    if a == "delete":
        if not space or not page:
            return json.dumps({"error": "action=delete 需要 space 和 page"}, ensure_ascii=False)
        return await wiki_delete(space, page)
    if a == "upload_attachment":
        if not space or not page or not file_path:
            return json.dumps({"error": "action=upload_attachment 需要 space、page、file_path"}, ensure_ascii=False)
        return await wiki_upload_attachment(space, page, file_path, filename)
    return json.dumps({"error": f"未知 action: {a}；可用: {', '.join(_WRITE_ACTIONS)}"},
                      ensure_ascii=False)

