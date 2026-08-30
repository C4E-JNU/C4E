"""
联网工具 — web_search + web_extract
用 duckduckgo_search 库替代前端爬 HTML 的方案
"""
import json
import re
import html as _html
import httpx
import urllib.request
import urllib.parse
import concurrent.futures as _cf

from duckduckgo_search import DDGS  # 备用回退；主搜索走 Bing HTML


async def web_search(query: str, limit: int = 5):
    """搜索互联网，返回标题+URL+摘要（主走 Bing HTML 抓取，免费无 key；失败回退 DDG）。"""
    query = query.strip()
    if not query:
        return json.dumps({"error": "搜索词不能为空"})

    limit = min(max(1, limit), 10)

    # Bing 抓取（免费、无 key、服务器稳定）——带超时保护，避免卡住 agent 循环
    try:
        import asyncio
        results = await asyncio.to_thread(_bing_search, query, limit)
        if results:
            return json.dumps({
                "source": "bing",
                "count": len(results),
                "results": results,
            }, ensure_ascii=False, indent=2)
    except Exception as e:
        # Bing 失败（网络/超时/被限），记录后走 ddgs
        _last_err = f"bing:{e}"

    # 回退：ddgs（Hermes 同款包，走 Yahoo/DDG）
    try:
        import asyncio
        results = await asyncio.to_thread(_ddgs_search, query, limit)
        if results:
            return json.dumps({"source": "ddgs", "count": len(results), "results": results}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"source": "none", "error": f"搜索失败: bing + ddgs 均不可用", "query": query})


def _bing_search(query: str, limit: int) -> list:
    """抓取 Bing 搜索结果 HTML，解析 title/href/摘要。免费无 key。带总体超时保护。"""
    def _do():
        url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
               + "&setlang=zh-hans&count=" + str(limit))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        r = urllib.request.urlopen(req, timeout=12)
        return r.read().decode("utf-8", "ignore")

    # 超时保护：整个抓取最多 15 秒，超时不阻塞
    with _cf.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_do)
        try:
            t = fut.result(timeout=15)
        except Exception:
            return []

    # 解析 b_algo 结果块
    blocks = re.findall(r'<li class="b_algo".*?</li>', t, re.DOTALL)
    out = []
    for blk in blocks:
        m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.DOTALL)
        if not m:
            continue
        url = _html.unescape(m.group(1))
        title = _html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        # 摘要：b_caption / b_lineclamp
        sm = re.search(r'<p[^>]*>(.*?)</p>', blk, re.DOTALL)
        snippet = ""
        if sm:
            snippet = _html.unescape(re.sub(r"<[^>]+>", "", sm.group(1))).strip()
        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def _ddgs_search(query: str, limit: int):
    """同步执行 DuckDuckGo 搜索"""
    try:
        with DDGS() as ddgs:
            results = []
            for i, r in enumerate(ddgs.text(query, max_results=limit)):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", "")
                })
                if len(results) >= limit:
                    break
            return results
    except Exception:
        # 降级：如果 duckduckgo_search 库失败，尝试 httpx 直接请求
        return _ddgs_fallback(query, limit)


def _ddgs_fallback(query: str, limit: int):
    """降级方案：直接用 httpx 请求 DuckDuckGo Lite"""
    try:
        url = "https://lite.duckduckgo.com/lite/"
        resp = httpx.post(url, data={"q": query}, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text
        results = []
        rows = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL)
        current = None
        for row in rows:
            link = re.search(
                r'<a[^>]*href="([^"]*)"[^>]*>([\s\S]*?)</a>', row
            )
            snippet = re.search(
                r'class="result-snippet"[^>]*>([\s\S]*?)</td>', row
            )
            if link and 'result-snippet' not in row:
                current = {
                    "title": re.sub(r'<[^>]+>', "", link.group(2)).strip(),
                    "url": link.group(1)
                }
            if snippet and current:
                results.append({
                    "title": current["title"],
                    "url": current["url"],
                    "snippet": re.sub(r'<[^>]+>', "", snippet.group(1)).strip()
                })
                current = None
        return results[:limit]
    except Exception:
        return []


async def web_extract(url: str):
    """抓取指定 URL 的网页内容并转为纯文本"""
    url = url.strip()
    if not url:
        return json.dumps({"error": "URL 不能为空"})
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "URL 必须以 http:// 或 https:// 开头"})

    # 安全限制：禁止内网
    blocked = ["localhost", "127.0.0.1", "0.0.0.0", "10.", "192.168.", "172.16."]
    for b in blocked:
        if b in url:
            return json.dumps({"error": "不允许访问内网地址"})

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            resp.raise_for_status()
            html = resp.text

        # 提取标题
        title = ""
        m = re.search(r'<title[^>]*>([\s\S]*?)</title>', html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()

        # 提取纯文本
        text = html
        text = re.sub(r'<script[^>]*>.*?</script>', "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', " ", text)
        text = re.sub(r'&nbsp;', " ", text)
        text = re.sub(r'&amp;', "&", text)
        text = re.sub(r'&lt;', "<", text)
        text = re.sub(r'&gt;', ">", text)
        text = re.sub(r'\s+', " ", text).strip()

        max_len = 15000
        if len(text) > max_len:
            text = text[:max_len] + f"\n\n...（截断至 {max_len} 字符）"

        return json.dumps({
            "url": url,
            "title": title,
            "content_length": len(text),
            "content": text
        }, ensure_ascii=False, indent=2)

    except httpx.TimeoutException:
        return json.dumps({"error": "请求超时，该网站可能响应较慢"})
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}"})
    except Exception as e:
        return json.dumps({"error": f"抓取失败: {str(e)}"})
