"""
GitHub 只读工具 — 仓库信息 / 目录列举 / 读文件 / 仓库搜索 / 代码搜索 / 下载压缩包

全部走公开接口，不需要任何 token。若环境变量里恰好有 GITHUB_TOKEN 或 GH_TOKEN，
就自动带上 Authorization 头（只是可选凭据，没有也照常工作）。

各接口的实测分工（2026-09 实测）：
- 仓库信息 / 目录列举 / 搜索仓库 → api.github.com（未认证：core 60 次/小时，search 10 次/分钟）
- 读文件正文 → raw.githubusercontent.com（无限流）
- 代码搜索 → 官方 api.github.com/search/code 未认证直接 401「Requires authentication」，
  所以改走 grep.app（公开、无需 key）。实测：必须用浏览器 UA（否则立刻 429 Vercel 质询），
  且**短暂连发会被 Vercel 质询锁住数十分钟**（同机浏览器带 JS/cookie 却能过）——
  所以失败时要如实报「没搜到」并改走 gh_download_tarball + 本地 search_files。
- 下载压缩包 → codeload.github.com（ref 用 HEAD 即默认分支，无需先查仓库信息）

设计约定：不做兜底/猜测。请求失败就把 HTTP 状态与响应正文原样报出，让调用方知道真相。
"""
import json
import os
import re
import html
import base64
import zipfile
import httpx

# grep.app 走 Vercel 网关，非浏览器 UA 会被直接打回 429 challenge，这里统一用浏览器 UA
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_TIMEOUT = 30.0


def _get_workspace_dir() -> str:
    """workspace 目录（项目根下的 workspace/），与 file_tools 的解析方式保持一致。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspace"))


def _github_headers(accept: str = "application/vnd.github+json") -> dict:
    """GitHub API 请求头；有 token 就带上，没有就走未认证。"""
    h = {"User-Agent": _BROWSER_UA, "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _norm_repo(repo: str):
    """把 'owner/name' 规范化；格式不对返回 None。"""
    repo = (repo or "").strip().strip("/")
    repo = re.sub(r"^https?://github\.com/", "", repo)
    repo = re.sub(r"\.git$", "", repo)
    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return f"{parts[0]}/{parts[1]}"


def _http_error(status: int, body: str, url: str, resp: httpx.Response = None) -> str:
    """把非 2xx 响应如实报出；限流额外给出重置时间与原因说明。"""
    out = {
        "error": f"HTTP {status}",
        "url": url,
        "body": (body or "")[:600],
    }
    if status == 403 and resp is not None and "rate limit" in (body or "").lower():
        out["reason"] = "GitHub 未认证请求限流（core 60 次/小时；search 10 次/分钟）"
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            out["reset_unix"] = reset
            out["suggestion"] = "等限流窗口重置后再试；或读文件改用 gh_read_file（走 raw.githubusercontent.com，不限流）。"
        else:
            out["suggestion"] = "稍后再试。"
    return json.dumps(out, ensure_ascii=False, indent=2)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _net_error(err: str, url: str) -> str:
    """网络层异常（超时/DNS/TLS 中断）如实报出——这是「工具没跑成」，不是「没结果」。"""
    return _dump({
        "error": f"网络层失败: {err}",
        "url": url,
        "note": "请求根本没到达/没读完（超时或连接中断），绝不等于没有结果。稍后重试；持续如此说明本机出网不稳。",
    })


async def _fetch(client: httpx.AsyncClient, url: str, **kwargs):
    """统一的 GET 包装：把 httpx 网络层异常转成 (None, 错误文本)，让各工具如实返回。

    返回 (resp, None) 或 (None, err)。
    """
    try:
        return await client.get(url, **kwargs), None
    except httpx.HTTPError as e:
        return None, f"{type(e).__name__}: {e}"



# ── 1. 仓库信息 ──────────────────────────────────────────

async def gh_repo_info(repo: str) -> str:
    """查仓库基本信息：stars / 主语言 / 描述 / 默认分支 / 最近推送 等。

    repo: 'owner/name'，例 'NousResearch/hermes-agent'。
    """
    full = _norm_repo(repo)
    if not full:
        return _dump({"error": f"仓库名格式应为 'owner/name'，收到: {repo!r}"})

    url = f"https://api.github.com/repos/{full}"
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp, err = await _fetch(client, url, headers=_github_headers())
    if err:
        return _net_error(err, url)
    if resp.status_code != 200:
        return _http_error(resp.status_code, resp.text, url, resp)

    d = resp.json()
    return _dump({
        "repo": d.get("full_name"),
        "description": d.get("description"),
        "stars": d.get("stargazers_count"),
        "forks": d.get("forks_count"),
        "watchers": d.get("subscribers_count"),
        "open_issues": d.get("open_issues_count"),
        "language": d.get("language"),
        "license": (d.get("license") or {}).get("spdx_id"),
        "default_branch": d.get("default_branch"),
        "topics": d.get("topics"),
        "size_kb": d.get("size"),
        "created_at": d.get("created_at"),
        "pushed_at": d.get("pushed_at"),
        "archived": d.get("archived"),
        "html_url": d.get("html_url"),
    })


# ── 2. 目录列举 ──────────────────────────────────────────

async def gh_list_dir(repo: str, path: str = "", ref: str = None) -> str:
    """列仓库某个目录（api.github.com contents 端点）。

    repo: 'owner/name'；path: 目录路径，留空为根目录；ref: 分支/tag/commit，留空为默认分支。
    """
    full = _norm_repo(repo)
    if not full:
        return _dump({"error": f"仓库名格式应为 'owner/name'，收到: {repo!r}"})

    p = (path or "").strip().strip("/")
    url = f"https://api.github.com/repos/{full}/contents/{p}"
    params = {"ref": ref} if ref else None
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp, err = await _fetch(client, url, headers=_github_headers(), params=params)
    if err:
        return _net_error(err, url)
    if resp.status_code != 200:
        return _http_error(resp.status_code, resp.text, url, resp)

    data = resp.json()
    # contents 端点在目录条目过多时会返回一个 dict（而非 list）并带 message
    if isinstance(data, dict):
        return _dump({"repo": full, "path": p, "ref": ref,
                      "error": data.get("message", "contents 端点未返回目录列表"),
                      "hint": "条目过多时该端点会拒绝；可改用 gh_download_tarball 取整包再本地列。"})
    if not isinstance(data, list):
        return _dump({"error": "contents 端点返回了意外结构", "raw": str(data)[:300]})

    entries = []
    for it in data:
        e = {"name": it.get("name"), "type": it.get("type"), "size": it.get("size")}
        if it.get("type") == "symlink":
            e["target"] = it.get("target")
        entries.append(e)
    return _dump({"repo": full, "path": p, "ref": ref or "(默认分支)",
                  "count": len(entries), "entries": entries})


# ── 3. 读文件 ────────────────────────────────────────────

async def gh_read_file(repo: str, path: str, ref: str = None, max_chars: int = 40000) -> str:
    """读仓库内单个文件的正文。优先 raw.githubusercontent.com（不限流）；
    本机实测 raw 会整段连不上时，自动改用官方 contents 端点（base64，未认证 60 次/小时），
    并在结果里用 source / raw_fail 如实标出用的是哪个来源。

    repo: 'owner/name'；path: 文件路径；ref: 分支/tag/commit，留空用 HEAD（默认分支）；
    max_chars: 返回字符上限，超出会截断并在结果里显式说明总字符数。
    """
    full = _norm_repo(repo)
    if not full:
        return _dump({"error": f"仓库名格式应为 'owner/name'，收到: {repo!r}"})
    if not path or not path.strip():
        return _dump({"error": "path 不能为空"})
    p = path.strip().lstrip("/")
    use_ref = ref.strip() if isinstance(ref, str) and ref.strip() else "HEAD"
    url = f"https://raw.githubusercontent.com/{full}/{use_ref}/{p}"

    raw_fail = None
    text = None
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp, err = await _fetch(client, url, headers=_github_headers("text/plain"))
    if err:
        raw_fail = err
    elif resp.status_code == 200:
        text = resp.text
    else:
        raw_fail = f"HTTP {resp.status_code}"

    # 次选来源：官方 contents 端点（同一份文件，base64 返回）。
    # 实测本机 raw.githubusercontent.com 会整段连不上（curl HTTP 000 / ConnectError，约 21s 超时），
    # 而 api.github.com 仍通；此时若不换源，读文件在这个网络下等于完全不可用。
    # 输出里会明确写出 source 与 raw_fail，不掩盖降级事实（contents 未认证 60 次/小时）。
    source = "raw.githubusercontent.com"
    if text is None:
        curl_ = f"https://api.github.com/repos/{full}/contents/{p}"
        c_params = {"ref": use_ref} if use_ref != "HEAD" else None
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            c_resp, c_err = await _fetch(client, curl_, headers=_github_headers(), params=c_params)
        if c_err:
            return _dump({
                "error": f"两个来源都失败 — raw: {raw_fail}；contents: {c_err}",
                "urls": [url, curl_],
                "hint": "本机取 GitHub 内容失败（网络层）。稍后重试；持续如此说明出网被拦。",
            })
        if c_resp.status_code != 200:
            return _dump({
                "error": f"raw 失败（{raw_fail}）且 contents HTTP {c_resp.status_code}",
                "urls": [url, curl_],
                "body": c_resp.text[:300],
                "hint": ("404 可能是该路径确实不存在，也可能是它其实是目录（读文件接口不列目录，"
                         "请改用 gh_list_dir）或 ref/分支名写错。"),
            })
        data = c_resp.json()
        if isinstance(data, list):
            return _dump({"error": "该 path 是目录，不是文件", "path": p, "raw_fail": raw_fail,
                          "hint": "请改用 gh_list_dir 列举该目录。"})
        text = base64.b64decode(data.get("content") or "").decode("utf-8", "ignore")
        source = "api.github.com/contents（未认证 60 次/小时）"

    total = len(text)
    truncated = total > max_chars
    body = text[:max_chars] if truncated else text
    out = {
        "repo": full, "path": p, "ref": use_ref,
        "url": url,
        "source": source,
        "raw_fail": raw_fail,
        "total_chars": total,
        "returned_chars": len(body),
        "truncated": truncated,
        "content": body,
    }
    if truncated:
        out["note"] = (f"文件共 {total} 字符，本次只返回前 {max_chars} 字符（已截断）。"
                       "如需后续内容，请用 gh_download_tarball 取整包后本地读，"
                       "或再用更大的 max_chars 请求。")
    return _dump(out)


# ── 4. 仓库搜索 ──────────────────────────────────────────

async def gh_search_repos(query: str, limit: int = 5, sort: str = "stars") -> str:
    """按关键词搜仓库（api.github.com/search/repositories）。

    query: 关键词；limit: 条数 1-30；sort: stars | forks | updated | 空串(最佳匹配)。
    """
    q = (query or "").strip()
    if not q:
        return _dump({"error": "query 不能为空"})
    limit = max(1, min(int(limit or 5), 30))
    params = {"q": q, "per_page": limit}
    if sort:
        params["sort"] = sort
        params["order"] = "desc"
    url = "https://api.github.com/search/repositories"

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp, err = await _fetch(client, url, headers=_github_headers(), params=params)
    if err:
        return _net_error(err, url)
    if resp.status_code != 200:
        return _http_error(resp.status_code, resp.text, url, resp)

    d = resp.json()
    items = [{
        "repo": it.get("full_name"),
        "stars": it.get("stargazers_count"),
        "language": it.get("language"),
        "description": (it.get("description") or "")[:300],
        "updated_at": it.get("updated_at"),
        "html_url": it.get("html_url"),
    } for it in d.get("items", [])[:limit]]
    return _dump({"query": q, "sort": sort or "(最佳匹配)",
                  "total_count": d.get("total_count"), "returned": len(items), "results": items})


# ── 5. 代码搜索 ──────────────────────────────────────────

def _clean_snippet(snippet_html: str) -> str:
    """把 grep.app 返回的高亮 HTML 片段还原成 '行号: 代码' 文本。"""
    lines = []
    for m in re.finditer(r'<tr data-line="(\d+)".*?<pre>(.*?)</pre>', snippet_html, re.DOTALL):
        lineno, code = m.group(1), m.group(2)
        code = html.unescape(re.sub(r"<[^>]+>", "", code))
        lines.append(f"{lineno}: {code.rstrip()}")
    if not lines:  # 结构变了也不猜，退化为去标签的整段文本
        lines = [html.unescape(re.sub(r"<[^>]+>", "", snippet_html)).strip()]
    return "\n".join(lines)


async def gh_search_code(query: str, limit: int = 10, lang: str = None) -> str:
    """在全网公开仓库里搜代码片（走 grep.app，无需 key）。

    query: 搜索词（代码字面量/标识符最准）；limit: 返回条数（上限 30）；
    lang: 可选语言过滤，如 'Python'、'Go'（按 grep.app 的 filter[lang] 参数下发）。
    """
    q = (query or "").strip()
    if not q:
        return _dump({"error": "query 不能为空"})
    limit = max(1, min(int(limit or 10), 30))

    # ① 环境里有 GITHUB_TOKEN / GH_TOKEN → 走官方代码搜索（唯一"真·全网代码搜索"，但需认证）
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        qq = q + ((" language:%s" % lang) if lang else "")
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp, err = await _fetch(client, "https://api.github.com/search/code",
                                     headers=_github_headers(), params={"q": qq, "per_page": limit})
        if err:
            return _net_error(err, "https://api.github.com/search/code")
        if resp.status_code == 200:
            d = resp.json()
            return _dump({"query": q, "source": "api.github.com/search/code",
                          "total_hits": d.get("total_count"), "returned": len(d.get("items", [])),
                          "results": [{"repo": (it.get("repository") or {}).get("full_name"),
                                       "path": it.get("path"), "url": it.get("html_url")}
                                      for it in d.get("items", [])[:limit]]})
        if resp.status_code not in (401, 403):
            return _http_error(resp.status_code, resp.text[:400], str(resp.url), resp)
        # 401/403（token 无效或无 code search 权限）→ 落到下面的公开搜索

    url = "https://grep.app/api/search"
    params = {"q": q}
    if lang:
        params["filter[lang][0]"] = lang
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp, err = await _fetch(client, url, headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json",
            "Referer": "https://grep.app/",
        }, params=params)
    if err:
        return _net_error(err, url)

    if resp.status_code != 200:
        body = resp.text[:600]
        note = None
        if resp.status_code in (429, 503):
            note = ("公开代码搜索服务（grep.app）当前处于限流/人机质询下，本次**没有搜到结果**。"
                    "官方 GitHub 代码搜索接口需要认证（环境里没有 GITHUB_TOKEN/GH_TOKEN）。"
                    "请改走这两条路，不要原地重试：① 已知仓库 → gh_download_tarball 把整包拉到本地，"
                    "再用 search_files 在本地搜（最省调用）；② 不知道仓库 → gh_search_repos 先找候选仓库。")
        return _dump({"error": f"HTTP {resp.status_code}", "url": str(resp.url),
                      "body": body, "note": note})

    d = resp.json()
    if "hits" not in d:
        return _dump({"error": "公开代码搜索返回了非预期结构（本次没有结果）",
                      "note": "改用 gh_download_tarball + 本地 search_files，或 gh_search_repos 找候选仓库。",
                      "raw": str(d)[:400]})

    hits = d.get("hits", {}).get("hits", [])[:limit]
    results = [{
        "repo": h.get("repo"),
        "path": h.get("path"),
        "branch": h.get("branch"),
        "snippet": _clean_snippet((h.get("content") or {}).get("snippet", "")),
    } for h in hits]
    return _dump({
        "query": q, "lang": lang or "(不限)",
        "total_hits": d.get("hits", {}).get("total"),
        "returned": len(results),
        "repo_facets": d.get("facets", {}).get("repo", {}).get("buckets", [])[:10],
        "results": results,
    })


# ── 6. 下载仓库压缩包 ────────────────────────────────────

async def gh_download_tarball(repo: str, ref: str = None, dest_dir: str = None) -> str:
    """下载整仓 zip 到本地并解压，返回**解压后目录**的绝对路径（让 agent 拿到一份可读的完整代码）。

    repo: 'owner/name'；ref: 分支/tag/commit，留空用 HEAD（= 默认分支，codeload 支持）；
    dest_dir: 落地目录，留空则用 workspace/_github_tmp/。
    """
    full = _norm_repo(repo)
    if not full:
        return _dump({"error": f"仓库名格式应为 'owner/name'，收到: {repo!r}"})
    use_ref = ref.strip() if isinstance(ref, str) and ref.strip() else "HEAD"
    owner, name = full.split("/")

    base = os.path.abspath(dest_dir) if dest_dir else os.path.join(_get_workspace_dir(), "_github_tmp")
    os.makedirs(base, exist_ok=True)
    safe_ref = re.sub(r"[^A-Za-z0-9._-]", "_", use_ref)
    dest = os.path.join(base, f"{owner}__{name}__{safe_ref}.zip")

    url = f"https://codeload.github.com/{full}/zip/{safe_ref}"
    try:
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=_github_headers("application/zip")) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    return _dump({"error": f"HTTP {resp.status_code}", "url": url, "body": body[:300],
                                  "hint": "404 通常是 repo 或 ref 写错（ref=HEAD 表示默认分支）。"})
                size = 0
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)
                        size += len(chunk)
    except httpx.HTTPError as e:
        # 下载中断：磁盘上可能留了半个 zip，清掉再如实报错（半包解压会得到残缺代码）
        try:
            os.remove(dest)
        except OSError:
            pass
        return _net_error(f"{type(e).__name__}: {e}", url)

    top = []
    try:
        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
            root = names[0].split("/")[0] if names else ""
            for n in names:
                first = n.split("/")[0] if "/" in n else n
                if first not in top:
                    top.append(first)
            file_count = sum(1 for n in names if not n.endswith("/"))
            # **解压**：只丢一个 zip 给 agent 是没法用 search_files/read_file 读的
            # （工具说明里承诺"落地后本地读"，那就必须真解出目录）。
            # 解出来的顶层目录名 = zip 自己的根目录（GitHub 的 zip 根是 "<name>-<ref>/"）。
            src_dir = os.path.join(base, root)
            if not os.path.isdir(src_dir):
                z.extractall(base)
    except Exception as e:
        return _dump({"error": f"下载完成但解压失败: {e}", "path": dest, "bytes": size})
    try:
        os.remove(dest)          # zip 已完成使命，留目录就够（避免工作区里堆压缩包）
    except OSError:
        pass

    return _dump({
        "repo": full, "ref": use_ref,
        "path": os.path.join(base, root),
        "zip_removed": True,
        "bytes": size, "zip_file_count": file_count,
        "zip_root_dir": root,
        "top_entries": top[:30],
        "note": ("已解压为目录（zip 已删除）；目录内所有路径都带 zip_root_dir 前缀。"
                 "可直接用本地文件工具与 search_files 读取、检索。"),
    })
