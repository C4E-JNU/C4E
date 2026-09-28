#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献/文档 → C4EAI 条目（标题命名 + 原件气泡 + 信息气泡 + Markdown 总结笔记）。

两步走：
  ① python make_doc_entry.py --base http://127.0.0.1:8080 --token X --prepare "D:/papers/*.pdf" --plan plan.json
     逐个上传原件（source=chat），拿回 file_id 与解析全文；全文写到 parse_out/，
     并为每条生成 title 猜测 + 空的气泡/笔记占位。
  ② 填好 plan.json（title / bubbles / note_content）后：
     python make_doc_entry.py --base ... --token X --plan plan.json
     建条目 + 挂气泡（一次 PUT）+ 逐个建笔记 + 回读校验。

批量（用户偏好）：篇数多时先用 delegate_task 让子智能体**分别**读 parse_out/*.txt，
各自返回该条的 title/bubbles/note_content（纯 JSON），父 agent 合并后只跑一次 --plan。
子智能体不要自己发 HTTP 写 chats.json（单体文件，并发 PUT 会互相覆盖）。

实测（2026-09-28）：1.7MB 真实 PDF → 解析 57916 字全文 → 条目 + 4 气泡（1 文件型）+ 富文本笔记
（表格/公式/mermaid）→ 原件可 GET，全链 14/14 通过。
不带 --token 时写的是本机实例的默认身份（本项目实测 = xhq1）。

只发 HTTP，不启动/重启任何服务。纯标准库。
"""
import argparse, glob, json, mimetypes, os, re, sys, time, urllib.request, urllib.error

# ── HTTP ────────────────────────────────────────────────────────────────────
def _req(base, method, path, token=None, body=None, ctype="application/json", timeout=180):
    url = base.rstrip("/") + path
    data = None
    headers = {}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = ctype
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, raw[:400]


def upload_file(base, token, path, source="chat"):
    """multipart/form-data，字段名固定 file（见 backend/app.py:633）"""
    boundary = "----c4eai" + str(int(time.time() * 1000))
    fn = os.path.basename(path)
    ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    parts = []
    parts.append(("--%s\r\n" % boundary).encode())
    parts.append((
        'Content-Disposition: form-data; name="file"; filename="%s"\r\n' % fn.replace('"', '')
    ).encode("utf-8"))
    parts.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
    parts.append(content)
    parts.append(("\r\n--%s--\r\n" % boundary).encode())
    body = b"".join(parts)
    return _req(base, "POST", "/api/file/upload?source=" + source, token, body,
                ctype="multipart/form-data; boundary=" + boundary, timeout=600)


def get_chats(base, token):
    st, data = _req(base, "GET", "/api/user/chats", token)
    if st != 200 or not isinstance(data, dict):
        raise SystemExit("读条目失败 HTTP %s: %s" % (st, str(data)[:300]))
    return data.get("chats") or {}


def next_branch_seed(chats):
    mx = 0
    for bid in (chats.get("branches") or {}):
        m = re.match(r"^branch-(\d+)$", str(bid))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _join_wrapped(text):
    """PDF 解析出来的 DOI/URL 常被硬换行切断（"https://doi.org/10.1038/\ns41467-…"）。
    把"上一行以 10.xxxx/ 或 https://…/ 结尾"的软换行拼回去。"""
    return re.sub(r"(10\.\d{4,9}/|https?://[^\s]*?/)\s*\n\s*(?=[A-Za-z0-9])", r"\1", text)


def extract_meta(text):
    """从解析全文里抽 DOI / 期刊 / 年份（供气泡种子；AI 再判断取舍）。
    注意：这里只能做到"尽量准"，最终由 agent 读原文确认，找不到宁可不放。"""
    t = _join_wrapped(text or "")
    head = t[:12000]
    out = {}
    m = re.search(r"10\.\d{4,9}/[^\s\]\)\}>\"',;]+", head)
    if m:
        out["doi"] = "DOI " + m.group(0).rstrip(".")
    m = re.search(r"([A-Z][A-Za-z\.& ]{2,40}?)\s*[\(（]\s*(20\d\d)", head)          # 形如 “Nat Commun (2026)”
    if m:
        out["journal"] = re.sub(r"\s+", " ", m.group(1)).strip(" .,")
        out["year"] = m.group(2)
    if "year" not in out:                                                              # 出版年 → Accepted → © （Received 放最后）
        for pat in (r"Published[^\n]{0,40}\b(20\d\d)\b", r"Accepted[^\n]{0,40}\b(20\d\d)\b",
                    r"©[^\n]{0,60}\b(20\d\d)\b", r"\b(20\d\d)\b"):
            mm = re.search(pat, head, re.I)
            if mm:
                out["year"] = mm.group(1)
                break
    return out


def guess_title(text, fallback):
    """从解析全文里挑一个像标题的行。只做草猜：首行常是 ARTICLE IN PRESS / 期刊头 /
    “Cite this article as: Li, J., …” 这种夹着作者的引用串，**agent 必须自己读原文定名**。"""
    bad = re.compile(r"^(abstract|keywords?|introduction|received|accepted|published|available|cite this|contents lists|"
                     r"article in press|preprint|author|doi|https?|www\.|©|license|open access|\d+\s*$)", re.I)
    for ln in (text or "").splitlines()[:80]:
        s = re.sub(r"^[#>*\-\s]+", "", ln).strip()
        s = re.sub(r"\s+", " ", s)
        if 12 <= len(s) <= 200 and not bad.match(s) and re.search(r"[A-Za-z\u4e00-\u9fff]", s):
            return s[:160]
    return fallback


def bub_id():
    return "bub-%d-%06x" % (int(time.time() * 1000), int.from_bytes(os.urandom(3), "big"))


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


# ── ① prepare ───────────────────────────────────────────────────────────────
def do_prepare(base, token, patterns, plan_path, out_dir):
    files = []
    for pat in patterns:
        files.extend(sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat])
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        raise SystemExit("没匹配到文件：%s" % patterns)
    os.makedirs(out_dir, exist_ok=True)
    items = []
    for p in files:
        print("上传 %s …" % os.path.basename(p), flush=True)
        st, res = upload_file(base, token, p, source="chat")
        if st != 200 or not isinstance(res, dict) or not res.get("file_id"):
            print("  ✗ 失败 HTTP %s %s" % (st, str(res)[:200]))
            items.append({"file": p, "error": "upload failed HTTP %s" % st})
            continue
        text = res.get("text") or ""
        stem = os.path.splitext(os.path.basename(p))[0]
        tpath = os.path.join(out_dir, stem + ".txt")
        with open(tpath, "w", encoding="utf-8") as f:
            f.write(text)
        print("  ✓ file_id=%s  解析 %d 字 → %s" % (res["file_id"][:12], len(text), tpath))
        meta = extract_meta(text)
        print("    自动抽到：%s" % (json.dumps(meta, ensure_ascii=False) or "（无）"))
        items.append({
            "file": p,
            "file_id": res["file_id"],
            "orig_name": res.get("name") or os.path.basename(p),
            "file_size": res.get("size") or os.path.getsize(p),
            "text_path": tpath,
            "text_chars": len(text),
            "meta_auto": meta,                            # doi/journal/year 草稿（agent 确认/删减）
            "title": guess_title(text, stem),          # ← agent 必须覆盖（草猜不可信）
            "bubbles": [],                              # ← agent 填 [{"text":"2025.11","icon":"📌"}, …]
            "note_title": "",                           # ← agent 填
            "note_content": ""                          # ← agent 填 Markdown 富文本
        })
    plan = {"base": base, "items": items,
            "comment": "填 title / bubbles / note_title / note_content 后，用 --plan 执行第二步"}
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print("\n计划已写到 %s（%d 条，失败 %d 条）" % (plan_path, len(items),
          len([i for i in items if i.get("error")])))
    print("下一步：读 parse_out/*.txt 补全 title / bubbles / note_content，再跑 --plan")


# ── ② plan ──────────────────────────────────────────────────────────────────
def do_plan(base, token, plan_path, dry=False):
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    items = [i for i in (plan.get("items") or []) if i.get("file_id")]
    if not items:
        raise SystemExit("plan 里没有可用条目（缺 file_id？）")

    chats = get_chats(base, token)
    branches = chats.get("branches") or {}
    order = list(chats.get("branchOrder") or [])
    # 同名条目复用（重跑不重复建）
    by_name = {}
    for bid, b in branches.items():
        if isinstance(b, dict) and b.get("name"):
            by_name.setdefault(str(b["name"]).strip(), bid)
    seed = next_branch_seed(chats)
    now = iso_now()
    new_branches = {}
    created = []
    for it in items:
        title = (it.get("title") or it.get("orig_name") or "未命名文档").strip()
        bid = by_name.get(title)
        if bid is None:
            bid = "branch-%d" % seed
            seed += 1
        old = branches.get(bid) or {}
        bubbles = []
        # ① 原件气泡（fileId 非空 = 点击能打开）
        bubbles.append({
            "id": bub_id(), "text": "PDF", "filePath": it.get("orig_name") or "",
            "fileType": mimetypes.guess_type(it.get("orig_name") or "")[0] or "application/pdf",
            "fileSize": it.get("file_size") or 0, "icon": "📕",
            "fileId": it["file_id"], "hasFile": True,
        })
        # ② 信息气泡
        for b in (it.get("bubbles") or []):
            txt = (b.get("text") if isinstance(b, dict) else str(b)) or ""
            if not txt.strip():
                continue
            bubbles.append({"id": bub_id(), "text": txt.strip(), "filePath": "",
                            "icon": (b.get("icon") if isinstance(b, dict) else None) or "📌"})
        new_branches[bid] = {
            "id": bid, "name": title,
            "messages": old.get("messages") or [], "parentId": old.get("parentId"),
            "createdAt": old.get("createdAt") or now, "updatedAt": iso_now(),   # 必填且最新
            "totalTokens": old.get("totalTokens", 0), "sessionTokens": old.get("sessionTokens", 0),
            "hasContent": old.get("hasContent", False), "isTemporary": False,
            "notes": old.get("notes") or [], "_bubbles": bubbles,
        }
        created.append((bid, title, it))
    # 新条目排在最前
    order = [bid for bid, _, _ in created] + [b for b in order if b not in [c[0] for c in created]]

    if dry:
        print(json.dumps({"branches": list(new_branches), "order": order[:5]}, ensure_ascii=False, indent=2))
        return

    # 一次 PUT 建全部条目 + 气泡
    st, res = _req(base, "PUT", "/api/user/chats", token,
                   {"chats": {"branches": new_branches, "deleted": {}, "branchOrder": order}})
    print("PUT /api/user/chats → HTTP %s %s" % (st, json.dumps(res, ensure_ascii=False)[:200] if isinstance(res, dict) else str(res)[:200]))
    if st != 200:
        raise SystemExit("建条目失败")

    # 逐个建笔记
    for bid, title, it in created:
        content = (it.get("note_content") or "").strip()
        if not content:
            print("  - %s：无 note_content，跳过笔记" % bid)
            continue
        nt = (it.get("note_title") or ("总结：" + title)).strip()
        st, res = _req(base, "POST", "/api/notes", token,
                       {"title": nt, "content": content[:20000], "branch_id": bid,
                        "kind": "note", "folder_id": ""})
        print("  笔记 %s → HTTP %s %s" % (bid, st, json.dumps(res, ensure_ascii=False)[:120] if isinstance(res, dict) else str(res)[:120]))
        time.sleep(0.02)   # note_id 是毫秒时间戳，串行防撞

    # 回读校验（PUT 回执成功 ≠ 落盘）
    print("\n=== 回读校验 ===")
    back = get_chats(base, token).get("branches") or {}
    allok = True
    for bid, title, it in created:
        b = back.get(bid) or {}
        fbs = [x for x in (b.get("_bubbles") or []) if x.get("fileId")]
        st, _ = _req(base, "GET", "/api/notes?branch_id=" + bid + "&with_content=0", token)
        stf, _ = _req(base, "GET", "/api/file/" + it["file_id"], token)
        good = b.get("name") == title and len(b.get("_bubbles") or []) == (1 + len(it.get("bubbles") or [])) \
            and len(fbs) == 1 and st == 200 and stf == 200
        allok = allok and good
        print("  %s %s | 名称=%s | 气泡=%d(文件型%d) | 笔记HTTP=%s | 原件HTTP=%s" %
              ("✓" if good else "✗", bid, b.get("name"), len(b.get("_bubbles") or []), len(fbs), st, stf))
    print("\n%s" % ("全部校验通过 ✅" if allok else "有校验未通过 ✗（看上面每行）"))
    return allok


def main():
    ap = argparse.ArgumentParser(description="文献/文档 → C4EAI 条目")
    ap.add_argument("--base", default=os.environ.get("C4EAI_BASE", "http://127.0.0.1:8080"))
    ap.add_argument("--token", default=os.environ.get("C4EAI_TOKEN", ""),
                    help="c4eai_token（浏览器 localStorage 里那个）；不给就是 guest 身份")
    ap.add_argument("--prepare", nargs="+", metavar="PATH/GLOB", help="第一步：上传原件+解析全文")
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--parse-out", default="parse_out")
    ap.add_argument("--dry-run", action="store_true", help="只打印将写入的 id，不真写")
    a = ap.parse_args()
    if not a.token:
        print("⚠️  没给 --token：将写到本机实例的**默认身份**（本项目实测 = xhq1，即用户自己的账号；"
              "不是 guest）。要指定账号或云端必须带 c4eai_token。", file=sys.stderr)
    if a.prepare:
        do_prepare(a.base, a.token, a.prepare, a.plan, a.parse_out)
    else:
        do_plan(a.base, a.token, a.plan, dry=a.dry_run)


if __name__ == "__main__":
    main()
