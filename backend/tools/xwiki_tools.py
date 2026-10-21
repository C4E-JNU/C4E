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

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
_XWIKI_BASE = os.environ.get("XWIKI_BASE", "http://<HUB-IP>:8090")
_XWIKI_USER = os.environ.get("XWIKI_USER", "HaiQiangXie")
_XWIKI_PASS = os.environ.get("XWIKI_PASS", "xhq15297718955")
_XWIKI_WIKI = os.environ.get("XWIKI_WIKI", "xwiki")  # 默认 wiki 名

# 模块级 session（复用连接）
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.auth = HTTPBasicAuth(_XWIKI_USER, _XWIKI_PASS)
        _session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "C4EAI-XWiki-Client/1.0",
        })
        # 关闭 SSL 验证（内网 HTTP）
        _session.verify = False
    return _session


def _url(path: str) -> str:
    return f"{_XWIKI_BASE}/rest{path}"


def _handle(resp: requests.Response) -> dict:
    """统一处理响应：成功返回 JSON，失败返回 error 结构"""
    if resp.status_code == 401:
        return {"error": "XWiki 认证失败：请检查 XWIKI_USER / XWIKI_PASS"}
    if resp.status_code == 403:
        return {"error": "XWiki 权限不足：该账号无权执行此操作"}
    if resp.status_code == 404:
        return {"error": f"XWiki 资源不存在: {resp.url}"}
    if not resp.ok:
        return {"error": f"XWiki HTTP {resp.status_code}: {resp.text[:300]}"}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:2000]}


# ── 底层 API 封装 ─────────────────────────────────────

def search_wiki(query: str, limit: int = 10) -> dict:
    """搜索 wiki 页面"""
    s = _get_session()
    resp = s.get(_url(f"/wikis/{_XWIKI_WIKI}/search"),
                 params={"q": query, "number": limit})
    return _handle(resp)


def get_page(space: str, page_name: str) -> dict:
    """获取页面内容（XWiki 2.1 语法）"""
    s = _get_session()
    resp = s.get(_url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}"))
    return _handle(resp)


def create_page(space: str, page_name: str, content: str,
                title: str = "", parent: str = "") -> dict:
    """新建页面"""
    s = _get_session()
    body = {
        "title": title or page_name,
        "content": content,
        "syntax": "xwiki/2.1",
    }
    if parent:
        body["parent"] = parent
    resp = s.put(_url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}"),
                 json=body)
    return _handle(resp)


def update_page(space: str, page_name: str, content: str,
                title: str = "", version: str = "") -> dict:
    """更新页面（需先 GET 拿 version，防止冲突）"""
    s = _get_session()
    # 先读当前版本
    cur = get_page(space, page_name)
    if "error" in cur:
        return cur
    ver = cur.get("version", "")
    body = {
        "title": title or cur.get("title", page_name),
        "content": content,
        "syntax": "xwiki/2.1",
        "version": ver,
    }
    resp = s.put(_url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}"),
                 json=body)
    return _handle(resp)


def delete_page(space: str, page_name: str) -> dict:
    """删除页面"""
    s = _get_session()
    resp = s.delete(_url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}"))
    if resp.status_code == 204:
        return {"success": True, "message": f"已删除 {space}.{page_name}"}
    return _handle(resp)


def list_pages(space: str, limit: int = 50) -> dict:
    """列出某空间下的页面"""
    s = _get_session()
    resp = s.get(_url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages"),
                 params={"number": limit})
    return _handle(resp)


def list_spaces(limit: int = 100) -> dict:
    """列出所有空间"""
    s = _get_session()
    resp = s.get(_url(f"/wikis/{_XWIKI_WIKI}/spaces"),
                 params={"number": limit})
    return _handle(resp)


def upload_attachment(space: str, page_name: str, file_path: str,
                      filename: str = "") -> dict:
    """上传附件到页面"""
    s = _get_session()
    fname = filename or os.path.basename(file_path)
    with open(file_path, "rb") as f:
        resp = s.put(
            _url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}/attachments/{fname}"),
            data=f,
            headers={"Content-Type": "application/octet-stream"},
        )
    return _handle(resp)


def list_attachments(space: str, page_name: str) -> dict:
    """列出页面附件"""
    s = _get_session()
    resp = s.get(_url(f"/wikis/{_XWIKI_WIKI}/spaces/{space}/pages/{page_name}/attachments"))
    return _handle(resp)


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

def wiki_search(query: str, limit: int = 10) -> str:
    """搜索 wiki 页面，返回标题和摘要列表"""
    r = search_wiki(query, limit)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    results = r.get("searchResults", [])
    items = []
    for item in results:
        items.append({
            "space": item.get("space", ""),
            "page": item.get("name", ""),
            "title": item.get("title", ""),
            "snippet": item.get("snippet", "")[:200],
            "url": item.get("xwikiUrl", ""),
        })
    return json.dumps({"count": len(items), "results": items}, ensure_ascii=False, indent=2)


def wiki_get(space: str, page: str) -> str:
    """获取 wiki 页面完整内容"""
    r = get_page(space, page)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({
        "space": space,
        "page": page,
        "title": r.get("title", ""),
        "content": r.get("content", ""),
        "version": r.get("version", ""),
        "author": r.get("author", ""),
        "modified": r.get("modified", ""),
    }, ensure_ascii=False, indent=2)


def wiki_create(space: str, page: str, content: str,
                title: str = "", parent: str = "") -> str:
    """新建 wiki 页面"""
    r = create_page(space, page, content, title, parent)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({
        "success": True,
        "space": space,
        "page": page,
        "url": f"{_XWIKI_BASE}/bin/view/{space}/{page}",
    }, ensure_ascii=False)


def wiki_update(space: str, page: str, content: str,
                title: str = "") -> str:
    """更新 wiki 页面（自动处理版本冲突）"""
    r = update_page(space, page, content, title)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({
        "success": True,
        "space": space,
        "page": page,
        "version": r.get("version", ""),
    }, ensure_ascii=False)


def wiki_delete(space: str, page: str) -> str:
    """删除 wiki 页面"""
    r = delete_page(space, page)
    return json.dumps(r, ensure_ascii=False)


def wiki_list_pages(space: str, limit: int = 50) -> str:
    """列出某空间下所有页面"""
    r = list_pages(space, limit)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    pages = r.get("pageSummaries", [])
    items = [{"page": p.get("name", ""), "title": p.get("title", ""),
              "modified": p.get("modified", "")} for p in pages]
    return json.dumps({"space": space, "count": len(items), "pages": items},
                      ensure_ascii=False, indent=2)


def wiki_list_spaces(limit: int = 100) -> str:
    """列出所有 wiki 空间"""
    r = list_spaces(limit)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    spaces = r.get("spaces", [])
    items = [{"space": s.get("name", ""), "home": s.get("home", "")} for s in spaces]
    return json.dumps({"count": len(items), "spaces": items},
                      ensure_ascii=False, indent=2)


def wiki_upload_attachment(space: str, page: str, file_path: str,
                           filename: str = "") -> str:
    """上传附件到 wiki 页面"""
    r = upload_attachment(space, page, file_path, filename)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    return json.dumps({"success": True, "file": filename or os.path.basename(file_path)},
                      ensure_ascii=False)


def wiki_list_attachments(space: str, page: str) -> str:
    """列出 wiki 页面的附件"""
    r = list_attachments(space, page)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    atts = r.get("attachments", [])
    items = [{"name": a.get("name", ""), "size": a.get("size", 0),
              "url": a.get("xwikiUrl", "")} for a in atts]
    return json.dumps({"space": space, "page": page, "count": len(items),
                       "attachments": items}, ensure_ascii=False, indent=2)
