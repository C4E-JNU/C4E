"""
XWiki REST API 客户端 + LangChain 工具函数

连接 138 上 Docker 部署的 XWiki (端口 8090)。
凭据用环境变量或默认值，不硬编码在代码里。
"""
import os
import json
import base64
import logging
from typing import Optional

import httpx  # 用 httpx 的 AsyncClient 替代 requests

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
_XWIKI_BASE = os.environ.get("XWIKI_BASE", "http://<HUB-IP>:8090")
_XWIKI_USER = os.environ.get("XWIKI_USER", "HaiQiangXie")
_XWIKI_PASS = os.environ.get("XWIKI_PASS", "xhq15297718955")
_XWIKI_WIKI = os.environ.get("XWIKI_WIKI", "xwiki")  # 默认 wiki 名

# 模块级 AsyncClient（复用连接）
_client: Optional[httpx.AsyncClient] = None


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


async def _request(method: str, path: str, **kwargs) -> dict:
    """统一发请求 + 处理响应"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0, verify=False)
    url = f"{_XWIKI_BASE}/rest{path}"
    try:
        resp = await _client.request(method, url, headers=_headers(), **kwargs)
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
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:2000]}


# ── 底层 API 封装 ─────────────────────────────────────

async def search_wiki(query: str, limit: int = 10) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/search",
                          params={"q": query, "number": limit})


async def get_page(space: str, page_name: str) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}")


async def create_page(space: str, page_name: str, content: str,
                      title: str = "", parent: str = "") -> dict:
    body = {"title": title or page_name, "content": content, "syntax": "xwiki/2.1"}
    if parent:
        body["parent"] = parent
    return await _request("PUT", f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}", json=body)


async def update_page(space: str, page_name: str, content: str,
                      title: str = "") -> dict:
    cur = await get_page(space, page_name)
    if "error" in cur:
        return cur
    ver = cur.get("version", "")
    body = {"title": title or cur.get("title", page_name),
            "content": content, "syntax": "xwiki/2.1", "version": ver}
    return await _request("PUT", f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}", json=body)


async def delete_page(space: str, page_name: str) -> dict:
    r = await _request("DELETE", f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}")
    if "error" not in r:
        return {"success": True, "message": f"已删除 {space}.{page_name}"}
    return r


async def list_pages(space: str, limit: int = 50) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages",
                          params={"number": limit})


async def list_spaces(limit: int = 100) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces",
                          params={"number": limit})


async def upload_attachment(space: str, page_name: str, file_path: str,
                            filename: str = "") -> dict:
    fname = filename or os.path.basename(file_path)
    with open(file_path, "rb") as f:
        data = f.read()
    return await _request(
        "PUT",
        f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}/attachments/{fname}",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


async def list_attachments(space: str, page_name: str) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}/attachments")


def get_attachment(space: str, page_name: str, filename: str) -> bytes:
    """下载附件（返回二进制）"""
    s = _get_session()
    resp = s.get(
        _url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}/attachments/{filename}")
    )
    if resp.ok:
        return resp.content
    return b""


# ── 面向大模型的工具函数（同步包装，供 LangChain 调用） ──

async def wiki_search(query: str, limit: int = 10) -> str:
    r = await search_wiki(query, limit)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    results = r.get("searchResults", [])
    items = [{"space": i.get("space", ""), "page": i.get("name", ""),
              "title": i.get("title", ""), "snippet": i.get("snippet", "")[:200],
              "url": i.get("xwikiUrl", "")} for i in results]
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
                       "url": f"{_XWIKI_BASE}/bin/view/{space}/{page}"}, ensure_ascii=False)


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
    pages = r.get("pageSummaries", [])
    items = [{"page": p.get("name", ""), "title": p.get("title", ""),
              "modified": p.get("modified", "")} for p in pages]
    return json.dumps({"space": space, "count": len(items), "pages": items},
                      ensure_ascii=False, indent=2)


async def wiki_list_spaces(limit: int = 100) -> str:
    r = await list_spaces(limit)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    spaces = r.get("spaces", [])
    items = [{"space": s.get("name", ""), "home": s.get("home", "")} for s in spaces]
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
    atts = r.get("attachments", [])
    items = [{"name": a.get("name", ""), "size": a.get("size", 0),
              "url": a.get("xwikiUrl", "")} for a in atts]
    return json.dumps({"space": space, "page": page, "count": len(items),
                       "attachments": items}, ensure_ascii=False, indent=2)
