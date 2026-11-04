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
import re
import json
import time
import base64
import asyncio
import logging
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
_XWIKI_BASE = os.environ.get("XWIKI_BASE", "http://172.24.108.138:8090")
_XWIKI_USER = os.environ.get("XWIKI_USER", "HaiqiangXie")
_XWIKI_PASS = os.environ.get("XWIKI_PASS", "xhq15297718955")
_XWIKI_WIKI = os.environ.get("XWIKI_WIKI", "xwiki")

# 模块级 AsyncClient（复用连接）
_client: Optional[httpx.AsyncClient] = None


def _seg(s: str) -> str:
    """路径段编码（中文空间名/页面名必需）。

    XWiki 返回的空间名带转义反斜杠（``06\\. 实验仪器``），直接拼进路径会 404，
    所以统一在这里去掉反斜杠。
    """
    return quote(str(s).replace("\\", ""), safe="")


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


# ── 空间层级解析 ──────────────────────────────────────
# XWiki 的空间名是层级的：``06\. 实验仪器与化学药品.01\.南区实验室仪器清单``
# 里反斜杠转义的是**字面点**，没转义的点是**层级分隔**。以前直接
# ``/spaces/{整串}/pages/{页}`` 拼路径，凡是子空间页面一律 404 ——
# 表现为「搜到了但读不出内容」。这里按已存在的空间名解析成层级路径。
_spaces_cache = {"t": 0.0, "names": set()}


async def _space_names() -> set:
    if _spaces_cache["names"] and (time.time() - _spaces_cache["t"] < 300):
        return _spaces_cache["names"]
    r = await _request("GET", f"/wikis/{_XWIKI_WIKI}/spaces", params={"number": 500})
    names = set()
    for s in (r.get("spaces") or []):
        nm = str(s.get("name", ""))
        names.add(nm)
        names.add(nm.replace("\\", ""))
    if names:
        _spaces_cache["t"] = time.time()
        _spaces_cache["names"] = names
    return names


def _greedy_space_split(plain: str, names: set):
    """把 ``06. 实验仪器与化学药品.01.南区实验室仪器清单`` 拆回已存在的层级空间。

    空间名自己就带点（``02. 新成员须知``），所以不能按点硬切；从长到短
    匹配已知空间名，命中就切走，再看剩下的部分。
    """
    segs, rest = [], plain
    while rest:
        best = ""
        for i in range(len(rest), 0, -1):
            cand = rest[:i]
            if cand in names and (i == len(rest) or rest[i] == "."):
                best = cand
                break
        if not best:
            return None
        segs.append(best)
        rest = rest[len(best):].lstrip(".")
    return segs or None


async def _space_segments(space: str) -> list:
    raw = str(space)
    plain = raw.replace("\\", "")
    names = await _space_names()
    if raw in names or plain in names:
        return [plain]
    parts = re.split(r"(?<!\\)\.", raw) if "\\." in raw else raw.split(".")
    parts = [p.replace("\\", "").strip() for p in parts if p.strip()]
    if len(parts) > 1 and all(p in names for p in parts):
        return parts
    greedy = _greedy_space_split(plain, names)
    if greedy:
        return greedy
    return [plain]


async def _space_path(space: str) -> str:
    segs = await _space_segments(space)
    return "".join(f"/spaces/{_seg(s)}" for s in segs)


async def _page_path(space: str, page_name: str) -> str:
    return f"/wikis/{_XWIKI_WIKI}{await _space_path(space)}/pages/{_seg(page_name)}"


def _xwql_like(term: str) -> str:
    """XWQL 字符串字面量：转义单引号与反斜杠"""
    return term.replace("\\", "\\\\").replace("'", "''")


# ── 检索（走 XWQL，Solr 索引为空时的唯一可用路径） ──
# XWQL 只有 LIKE 子串匹配，没有分词：整句「怎么申请仪器」当子串查 = 0 命中
# （2026-11-05 实测）。所以把查询拆成词 / 相邻 2-gram 做 OR 召回，
# 再抓候选页正文做本地打分 + 片段；否则模型只拿到一堆无序标题，看起来就是"查不到"。
_SYS_SPACES = ("XWiki", "WikiManager", "PanelsCode", "ColorThemes", "Sandbox")

_SPLIT_RE = re.compile(r"[\s,，。．、;；:：!！?？\"'“”‘’()（）\[\]【】《》<>]+")

# 疑问/口语填充词：不剔除的话，"怎么申请仪器" 会被拆出 "怎么/么申" 这类
# 与知识无关的 2-gram，既召回噪声又拉低命中覆盖率。
_FILLERS = {"怎么", "如何", "什么", "哪里", "在哪", "请问", "帮我", "我想",
            "一下", "可以", "怎样", "为什么", "是不是", "多少", "有没有", "的", "吗", "呢", "了"}


def _query_terms(query: str) -> list:
    """把用户/模型的一句话拆成可 OR 的词（中文长串用 2-gram）。"""
    q = (query or "").strip()
    if not q:
        return []
    parts = [p for p in _SPLIT_RE.split(q) if p and p not in _FILLERS]
    if not parts:
        return []
    if len(parts) > 1:
        return parts[:6]
    s = parts[0]
    if len(s) <= 2:
        return [s]
    grams = [s[i:i + 2] for i in range(len(s) - 1)]
    grams = [g for g in grams if g not in _FILLERS]
    return ([s] + grams)[:9]


def _where(terms: list, scope: str = "all") -> str:
    parts = []
    for t in terms:
        lit = _xwql_like(t)
        if scope == "title":
            parts.append(f"doc.title like '%{lit}%'")
        elif scope == "content":
            parts.append(f"doc.content like '%{lit}%'")
        else:
            parts.append(f"(doc.title like '%{lit}%' or doc.content like '%{lit}%')")
    return " or ".join(parts)


def _plain(s: str) -> str:
    """粗略去掉 XWiki 语法，只留可读正文（片段用）。"""
    s = re.sub(r"\{\{/?[^{}]*\}\}", " ", s)
    s = re.sub(r"\(\%.*?\%\)", " ", s)
    s = s.replace("[[", " ").replace("]]", " ")
    s = re.sub(r">>https?://\S+", " ", s)
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"[=*#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _score(content: str, title: str, terms: list) -> int:
    """正文/标题命中词数打分：标题权重高，正文按出现次数计。"""
    c = (content or "").lower()
    tl = (title or "").lower()
    score = 0
    for t in terms:
        t = t.lower()
        if not t:
            continue
        if t in tl:
            score += 6
        cnt = c.count(t)
        if cnt:
            score += min(cnt, 10)
    return score


def _snippet(content: str, terms: list, width: int = 160) -> str:
    plain = _plain(content or "")
    low = plain.lower()
    pos = -1
    for t in terms:
        p = low.find(t.lower())
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return plain[:width]
    start = max(0, pos - width // 3)
    return plain[start:start + width]


async def search_wiki(query: str, limit: int = 10, scope: str = "all",
                      space: str = "") -> dict:
    """按关键词检索标题/正文。scope: all | title | content；space 可按空间过滤。"""
    terms = _query_terms(query)
    if not terms:
        return {"error": "query 不能为空"}
    where = _where(terms, scope)
    if space:
        where = f"({where}) and doc.space like '%{_xwql_like(space)}%'"
    r = await _request("GET", f"/wikis/{_XWIKI_WIKI}/query",
                       params={"q": f"where {where}", "type": "xwql",
                               "number": min(max(limit * 4, 20), 60)})
    if "error" in r or "searchResults" not in r:
        return r
    cands = []
    for x in r.get("searchResults") or []:
        sp = str(x.get("space", "")).replace("\\", "")
        if sp.split(".")[0].strip() in _SYS_SPACES:   # 系统空间（翻译/面板）不是知识
            continue
        cands.append({"space": sp, "page": x.get("pageName", ""),
                      "title": x.get("title", ""), "version": x.get("version", ""),
                      "modified": x.get("modified", 0),
                      "full": str(x.get("pageFullName", "")).replace("\\", "")})
    if not cands:
        return {"terms": terms, "candidates": 0, "results": []}

    # 抓候选正文：既用于打分排序，也用于给出命中片段（模型据此判断"是不是我要的"）
    probe = cands[:max(limit * 2, 8)]
    pages = await asyncio.gather(*[get_page(c["space"], c["page"]) for c in probe],
                                 return_exceptions=True)
    for c, p in zip(probe, pages):
        if isinstance(p, dict) and "error" not in p:
            c["_content"] = p.get("content", "") or ""
            c["_title"] = p.get("title", "") or c["title"]
        else:
            c["_content"] = ""
            c["_title"] = c["title"]
        c["_score"] = _score(c["_content"], c["_title"], terms)
        body = (c["_content"] + " " + c["_title"]).lower()
        c["_cover"] = sum(1 for t in terms if t and t.lower() in body)

    # 覆盖率只用于排序与"结果可信度"标注，不做硬过滤：
    # 中文长句拆 2-gram 后，正确页面往往只命中其中一两个词
    # （"怎么申请仪器" → 仪器清单只命中 仪器/申请），硬砍会直接查不到。
    if len(terms) >= 3:
        need = max(2, int(len(terms) * 0.5))
    else:
        need = 0

    ranked = sorted(probe, key=lambda c: (c["_cover"], c["_score"], c.get("modified") or 0), reverse=True)
    results = []
    for c in ranked[:limit]:
        results.append({
            "space": c["space"],
            "page": c["page"],
            "title": c["_title"],
            "snippet": _snippet(c["_content"], terms),
            "url": f"{_XWIKI_BASE}/bin/view/" + c["full"].replace(".", "/"),
            "version": c["version"],
            "match": "strong" if (need and c["_cover"] >= need) else ("weak" if need else "direct"),
        })
    return {"terms": terms, "candidates": len(cands), "results": results}



async def get_page(space: str, page_name: str) -> dict:
    return await _request("GET", await _page_path(space, page_name))


async def create_page(space: str, page_name: str, content: str,
                      title: str = "", parent: str = "") -> dict:
    body = {"title": title or page_name, "content": content, "syntax": "xwiki/2.1"}
    if parent:
        body["parent"] = parent
    return await _request("PUT", await _page_path(space, page_name),
                          json=body)


async def update_page(space: str, page_name: str, content: str,
                      title: str = "") -> dict:
    cur = await get_page(space, page_name)
    if "error" in cur:
        return cur
    body = {"title": title or cur.get("title", page_name),
            "content": content, "syntax": "xwiki/2.1",
            "version": cur.get("version", "")}
    return await _request("PUT", await _page_path(space, page_name),
                          json=body)


async def delete_page(space: str, page_name: str) -> dict:
    r = await _request("DELETE", await _page_path(space, page_name))
    if "error" not in r:
        return {"success": True, "message": f"已删除 {space}.{page_name}"}
    return r


async def list_pages(space: str, limit: int = 50) -> dict:
    return await _request("GET", f"/wikis/{_XWIKI_WIKI}{await _space_path(space)}/pages",
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
        f"{await _page_path(space, page_name)}/attachments/{_seg(fname)}",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


async def list_attachments(space: str, page_name: str) -> dict:
    return await _request(
        "GET", f"{await _page_path(space, page_name)}/attachments")


# ── 面向大模型的工具函数（async，供 LangChain await） ──

async def wiki_search(query: str, limit: int = 10, scope: str = "all",
                      space: str = "") -> str:
    r = await search_wiki(query, limit, scope, space)
    if "error" in r:
        return json.dumps(r, ensure_ascii=False)
    results = r.get("results", [])
    out = {"count": len(results), "terms": r.get("terms", []),
           "results": results}
    if not results:
        out["hint"] = ("No match. Try shorter keywords (1-2 words), a synonym, "
                       "or list_spaces / list_pages to browse by space.")
    elif all(x.get("match") == "weak" for x in results):
        out["hint"] = ("These are weak matches (only a few query words occur in these pages). "
                       "Re-search with different, more specific keywords before concluding "
                       "the wiki does not cover it.")
    return json.dumps(out, ensure_ascii=False, indent=2)


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
    # 这个 wiki 几乎全是子空间结构：只列本空间会只有一个 WebHome，
    # 所以顺带给出直接子空间，模型可以据此继续 list_pages 往下走。
    plain = space.replace("\\", "").rstrip(".")
    children = sorted(n for n in await _space_names()
                      if n.startswith(plain + ".") and "." not in n[len(plain) + 1:])
    return json.dumps({"space": space, "count": len(items), "pages": items,
                       "child_spaces": [c.replace("\\", "") for c in children]},
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
        return await wiki_search(query, limit, scope, space)
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

