"""
联网工具 — web_search + web_extract
用 duckduckgo_search 库替代前端爬 HTML 的方案
"""
import ipaddress
import json
import re
import html as _html
import httpx
import socket
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
    errors = []

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
        errors.append("bing: 返回 0 条（可能被限流或页面结构变更）")
    except Exception as e:
        # Bing 失败（网络/超时/被限），记录后走 ddgs
        errors.append(f"bing: {e}")

    # 回退：ddgs（Hermes 同款包，走 Yahoo/DDG）
    try:
        import asyncio
        results = await asyncio.to_thread(_ddgs_search, query, limit)
        if results:
            return json.dumps({"source": "ddgs", "count": len(results), "results": results}, ensure_ascii=False, indent=2)
        errors.append("ddgs: 返回 0 条")
    except Exception as e:
        errors.append(f"ddgs: {e}")

    # 关键：绝不能隐式返回 None。模型收到 None 时分不清「没搜到」和「工具挂了」，
    # 会开始无意义重试。这里显式区分「两个源都通了但都没结果」和「两个源都失败」。
    if len(errors) == 2 and all("返回 0 条" in x for x in errors):
        return json.dumps({
            "source": "none", "query": query, "count": 0, "results": [],
            "note": ("两个搜索引擎都正常响应但都没有命中，说明这个检索词确实搜不到东西，"
                     "不是工具故障。请换检索词（减少限定词/换同义词/改用英文术语）重试，"
                     "或改用 web_extract 直接访问已知的站点。"),
            "diagnostics": errors,
        }, ensure_ascii=False, indent=2)
    return json.dumps({
        "source": "none", "query": query, "count": 0, "results": [],
        "error": "搜索工具本次不可用（不是没有结果）。不要按同一检索词重试。",
        "diagnostics": errors,
        "suggestion": "换用 web_extract 抓取已知 URL；或稍后再试。图谱/文献类问题优先用 graph_query 与本地已归档文件。",
    }, ensure_ascii=False, indent=2)


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


_INTERNAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")


def _ip_is_internal(host: str):
    """IP 字面量是否属于内网/保留地址。是则返回说明文字，否则返回 None。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None  # 不是 IP 字面量，交给 DNS 解析那一步判断
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return f"地址 {host} 属于内网/保留段"
    return None


async def _internal_host_reason(host: str):
    """判断目标主机是否内网。放行返回 None，拒绝返回原因字符串。

    重要背景：旧实现是 `for b in ["10.", "172.16.", ...]: if b in url`，
    即对**整个 URL 字符串**做子串匹配。这会把所有 DOI 链接
    （https://doi.org/10.1021/xxx）以及任何路径里带 "10." 的正常网址
    一律误判成内网地址，导致文献检索系统性瘫痪。
    现在只对 host 部分判断，并且先按 IP 字面量、再按 DNS 解析结果判断。
    """
    if not host:
        return "URL 无法解析出主机名"
    host = host.strip().rstrip(".").lower()
    if host == "localhost" or host.endswith(_INTERNAL_HOST_SUFFIXES):
        return f"主机 {host} 是内部名称"
    reason = _ip_is_internal(host)
    if reason:
        return reason
    if ":" in host or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        return reason  # IP 字面量已在上一步判过，不是内网即放行
    # 域名：解析后逐个 IP 检查，防「公网域名解析到内网」这型 SSRF
    try:
        import asyncio
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except Exception:
        return None  # 解析失败交给 httpx 报真实错误，不在这里误伤
    for info in infos:
        reason = _ip_is_internal(info[4][0])
        if reason:
            return f"主机 {host} 解析到内网地址（{reason}）"
    return None


async def web_extract(url: str):
    """抓取指定 URL 的网页内容并转为纯文本"""
    url = url.strip()
    if not url:
        return json.dumps({"error": "URL 不能为空"})
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "URL 必须以 http:// 或 https:// 开头"})

    try:
        # 手动跟随重定向并逐跳做 SSRF 检查：若用 follow_redirects=True，
        # 一个公网 URL 可以靠 302 把请求带进内网，绕过入口检查。
        current = url
        resp = None
        too_many_hops = False
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            for _hop in range(6):
                reason = await _internal_host_reason(
                    urllib.parse.urlparse(current).hostname or "")
                if reason:
                    return json.dumps({"error": f"目标被安全策略拒绝：{reason}",
                                       "url": current}, ensure_ascii=False)
                resp = await client.get(
                    current,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    current = urllib.parse.urljoin(current, loc)
                    continue
                break
            else:
                too_many_hops = True
        if too_many_hops:
            return json.dumps({"error": "重定向次数过多（>5 跳），已停止跟随",
                               "url": url}, ensure_ascii=False)
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
