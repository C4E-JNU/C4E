"""
文献检索工具 — crossref_lookup + arxiv_search + semantic_scholar_search
三个源都免费、无需 API key；全部走 httpx 异步请求，超时 15s（与 web_tools.py 一致）。
返回 JSON 字符串（ensure_ascii=False），错误如实返回，不做任何伪造/兜底数据。
"""
import json
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

# 与 web_tools.py 保持一致的超时
_TIMEOUT = 15

# Crossref 建议带上联系方式（polite pool），无 key 也能用
_UA = "C4EAI/1.0 (literature tools; mailto:noreply@example.com)"


def _clamp(n, lo, hi):
    """把请求条数夹到合理区间，避免上游拒绝或返回过多。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return lo
    return min(max(lo, n), hi)


async def crossref_lookup(doi: str = None, query: str = None, rows: int = 5) -> str:
    """Crossref 检索（无 key）：按 DOI 精确查，或按关键词模糊搜。

    :param doi:   DOI，如 "10.1038/s41467-026-70180-1"（可带 https://doi.org/ 前缀）。给了 doi 就忽略 query。
    :param query: 关键词，如 "tetracycline photocatalytic degradation"。
    :param rows:  关键词检索时返回条数，默认 5（区间 1-20）。
    返回：题名 / 期刊 / 年份 / 前几位作者 / DOI / 被引数。
    """
    doi = (doi or "").strip()
    query = (query or "").strip()
    if not doi and not query:
        return json.dumps({"error": "必须提供 doi 或 query 之一"}, ensure_ascii=False)

    headers = {"User-Agent": _UA}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            if doi:
                # 去掉常见前缀，只留裸 DOI
                for pre in ("https://doi.org/", "http://doi.org/", "doi:", "https://dx.doi.org/"):
                    if doi.lower().startswith(pre):
                        doi = doi[len(pre):]
                url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
                resp = await client.get(url, headers=headers)
            else:
                rows = _clamp(rows, 1, 20)
                url = ("https://api.crossref.org/works?query="
                       + urllib.parse.quote(query) + f"&rows={rows}")
                resp = await client.get(url, headers=headers)

            if resp.status_code == 404:
                return json.dumps({"source": "crossref", "error": f"未找到该 DOI：{doi}"},
                                  ensure_ascii=False)
            if resp.status_code != 200:
                return json.dumps({
                    "source": "crossref",
                    "error": f"Crossref 返回 HTTP {resp.status_code}",
                    "body": resp.text[:300],
                }, ensure_ascii=False, indent=2)

            data = resp.json()

        if doi:
            items = [data.get("message", {})]
        else:
            items = data.get("message", {}).get("items", [])

        out = []
        for it in items:
            authors = []
            for a in it.get("author", []) or []:
                name = ((a.get("given", "") or "") + " " + (a.get("family", "") or "")).strip() \
                    or a.get("name", "")
                if name:
                    authors.append(name)
            # 年份：优先 issued，其次 published-print/online
            year = None
            for k in ("issued", "published-print", "published-online", "created"):
                parts = (it.get(k) or {}).get("date-parts") or []
                if parts and parts[0] and parts[0][0]:
                    year = parts[0][0]
                    break
            title = (it.get("title") or [""])[0]
            journal = (it.get("container-title") or [""])[0]
            out.append({
                "title": title,
                "journal": journal,
                "year": year,
                "authors": authors[:6],
                "author_count": len(authors),
                "doi": it.get("DOI"),
                "cited_by": it.get("is-referenced-by-count"),
                "type": it.get("type"),
            })

        if not out:
            return json.dumps({"source": "crossref", "query": query or doi,
                               "count": 0, "results": [],
                               "note": "Crossref 正常响应但没有命中，换个检索词试试。"},
                              ensure_ascii=False, indent=2)

        return json.dumps({
            "source": "crossref",
            "mode": "doi" if doi else "query",
            "count": len(out),
            "results": out,
        }, ensure_ascii=False, indent=2)

    except httpx.TimeoutException:
        return json.dumps({"source": "crossref", "error": f"请求超时（>{_TIMEOUT}s）"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"source": "crossref", "error": f"请求失败: {type(e).__name__}: {e}"},
                          ensure_ascii=False)


async def arxiv_search(query: str, max_results: int = 5, sort_by: str = "relevance") -> str:
    """arXiv 预印本检索（Atom XML，无 key）。

    :param query:       检索词，如 "photocatalytic degradation"。
    :param max_results: 返回条数，默认 5（区间 1-20）。
    :param sort_by:     排序，"relevance"（默认）/ "lastUpdatedDate" / "submittedDate"。
    返回：arXiv id / 题名 / 摘要 / 作者 / 发布时间 / 主分类。
    """
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "检索词不能为空"}, ensure_ascii=False)

    max_results = _clamp(max_results, 1, 20)
    if sort_by not in ("relevance", "lastUpdatedDate", "submittedDate"):
        sort_by = "relevance"

    # all: 覆盖题名/摘要/作者/全文关键词
    params = {
        "search_query": "all:" + query,
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": "descending",
    }
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            return json.dumps({
                "source": "arxiv",
                "error": f"arXiv 返回 HTTP {resp.status_code}",
                "body": resp.text[:300],
            }, ensure_ascii=False, indent=2)

        root = ET.fromstring(resp.text)
    except httpx.TimeoutException:
        return json.dumps({"source": "arxiv", "error": f"请求超时（>{_TIMEOUT}s）"},
                          ensure_ascii=False)
    except ET.ParseError as e:
        return json.dumps({"source": "arxiv", "error": f"返回内容不是合法 XML: {e}"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"source": "arxiv", "error": f"请求失败: {type(e).__name__}: {e}"},
                          ensure_ascii=False)

    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        raw_id = (entry.findtext("a:id", "", ns) or "").strip()
        # http://arxiv.org/abs/2401.01234v1 → 2401.01234
        short_id = raw_id.rsplit("/", 1)[-1]
        short_id = short_id.split("v")[0] if "v" in short_id else short_id
        authors = [(a.findtext("a:name", "", ns) or "").strip()
                   for a in entry.findall("a:author", ns)]
        primary = entry.find("arxiv:primary_category", ns)
        out.append({
            "id": short_id,
            "title": " ".join((entry.findtext("a:title", "", ns) or "").split()),
            "authors": authors[:6],
            "author_count": len(authors),
            "published": (entry.findtext("a:published", "", ns) or "").strip(),
            "updated": (entry.findtext("a:updated", "", ns) or "").strip(),
            "primary_category": primary.get("term") if primary is not None else None,
            "url": raw_id,
            "summary": " ".join((entry.findtext("a:summary", "", ns) or "").split()),
        })

    if not out:
        return json.dumps({"source": "arxiv", "query": query, "count": 0, "results": [],
                           "note": "arXiv 正常响应但无命中。"},
                          ensure_ascii=False, indent=2)

    return json.dumps({"source": "arxiv", "query": query, "sort_by": sort_by,
                       "count": len(out), "results": out},
                      ensure_ascii=False, indent=2)


async def semantic_scholar_search(query: str, limit: int = 5, year: str = None) -> str:
    """Semantic Scholar 检索（无 key，有限流）。

    :param query: 检索词，如 "tetracycline photocatalytic"。
    :param limit: 返回条数，默认 5（区间 1-20）。
    :param year:  年份过滤，如 "2020"（单年）或 "2018-2024"（区间），None 表示不限。
    返回：title / year / venue / citationCount / abstract（首 300 字）/ externalIds（含 DOI）。
    注意：匿名调用共享配额，被限流时返回 429 提示，不做静默重试。
    """
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "检索词不能为空"}, ensure_ascii=False)

    limit = _clamp(limit, 1, 20)
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,year,venue,citationCount,abstract,externalIds,authors",
    }
    if year:
        params["year"] = str(year).strip()

    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})

        if resp.status_code == 429:
            return json.dumps({
                "source": "semantic_scholar",
                "error": "429 限流，请稍后再试",
                "suggestion": ("Semantic Scholar 匿名接口有共享配额限制（约每秒 1 次）。"
                               "请等待 30-60 秒后重试；本次检索可先用 crossref_lookup "
                               "或 arxiv_search 替代。"),
            }, ensure_ascii=False, indent=2)
        if resp.status_code != 200:
            return json.dumps({
                "source": "semantic_scholar",
                "error": f"Semantic Scholar 返回 HTTP {resp.status_code}",
                "body": resp.text[:300],
            }, ensure_ascii=False, indent=2)

        data = resp.json()

        out = []
        for p in data.get("data", []) or []:
            abstract = p.get("abstract") or ""
            if len(abstract) > 300:
                abstract = abstract[:300] + "…"
            authors = [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")]
            out.append({
                "title": p.get("title"),
                "year": p.get("year"),
                "venue": p.get("venue"),
                "citationCount": p.get("citationCount"),
                "authors": authors[:6],
                "abstract": abstract,
                "externalIds": p.get("externalIds") or {},
                "paperId": p.get("paperId"),
            })

        if not out:
            return json.dumps({"source": "semantic_scholar", "query": query,
                               "count": 0, "results": [],
                               "note": "Semantic Scholar 正常响应但无命中。"},
                              ensure_ascii=False, indent=2)

        return json.dumps({
            "source": "semantic_scholar",
            "query": query,
            "year": year,
            "count": len(out),
            "total": data.get("total"),
            "results": out,
        }, ensure_ascii=False, indent=2)

    except httpx.TimeoutException:
        return json.dumps({"source": "semantic_scholar", "error": f"请求超时（>{_TIMEOUT}s）"},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"source": "semantic_scholar",
                           "error": f"请求失败: {type(e).__name__}: {e}"},
                          ensure_ascii=False)
