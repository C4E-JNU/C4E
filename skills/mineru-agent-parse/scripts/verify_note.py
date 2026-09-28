#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产出校验：把 MinerU 归档目录 / 中文译本笔记的「肉眼检查」变成可执行的机器校验。

替代的肉眼检查项：
  1) 破图        —— Markdown / HTML 里引用的图片文件在归档目录里是否存在
  2) 图注/子图数 —— images_map 的子图数 与 正文图注编号数 / layout_html 的 img·div 数对不上
  3) 正文截断    —— 00_full_cleaned.md 与笔记正文长度差异过大 / 结尾异常（未闭合的 $$、代码块、表格、div）
  4) 重复与跳号  —— 图片重复引用、图注编号跳号/重号

用法（纯标准库，可命令行直接跑）：
  python verify_note.py <归档目录>
  python verify_note.py <归档目录>/00_images_map.json
  python verify_note.py <中文笔记.md> [--archive <归档目录>]
  python verify_note.py <归档目录> --json          # 机器可读
  python verify_note.py <笔记.md> --archive <归档目录> --min-ratio 0.15

退出码：0 = 通过（可能带 warning）；1 = 发现 error；2 = 用法 / 读文件失败。
"""
import argparse
import json
import os
import re
import sys

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg")

RE_MD_IMG = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
RE_HTML_IMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
# Fig 2 / Figure 2 / Fig. 2a / Table 1 / Fig. S1
RE_CAP_TOKEN = re.compile(r"\b(fig(?:ure)?|tab(?:le)?)\.?\s*([Ss]?\d+[a-z]?)\b", re.I)
# 中文图注编号：图2 / 表 1
RE_CN_TOKEN = re.compile(r"([图表])\s*([Ss]?\d+[a-z]?)")

LEVELS = {"PASS": 0, "WARN": 1, "ERROR": 2}


class Report(object):
    def __init__(self):
        self.items = []  # (level, check, message)

    def add(self, level, check, msg):
        self.items.append((level, check, msg))

    def error(self, check, msg):
        self.add("ERROR", check, msg)

    def warn(self, check, msg):
        self.add("WARN", check, msg)

    def ok(self, check, msg):
        self.add("PASS", check, msg)

    def info(self, check, msg):
        self.add("INFO", check, msg)

    def count(self, level):
        return sum(1 for i in self.items if i[0] == level)

    def worst(self):
        order = {"PASS": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
        return max((order[i[0]] for i in self.items), default=0)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _exists(path):
    try:
        return os.path.isfile(path)
    except OSError:
        return False


def _isdir(path):
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def _ancestor_roots(*paths):
    """收集可用来解析 /workspace/... 绝对路径的候选根目录（各路径的全部祖先 + cwd）。"""
    roots = []

    def add(p):
        p = os.path.abspath(p)
        while True:
            if p not in roots:
                roots.append(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent

    for p in list(paths) + [os.getcwd()]:
        if p:
            add(p)
    return roots


def _resolve_image(src, roots, base_dirs):
    """把图片引用解析成真实文件路径。返回 (status, path)
    status ∈ {ok, missing, remote, skip}"""
    s = (src or "").strip().strip("'\"")
    if not s:
        return "skip", None
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://") or low.startswith("//"):
        return "remote", None
    if low.startswith("data:"):
        return "skip", None
    if not low.endswith(IMAGE_EXTS) and "?" not in s:
        return "skip", None

    candidates = []
    if "workspace/" in s.replace("\\", "/"):
        rel = s.replace("\\", "/")
        rel = rel[rel.index("workspace/"):]
        for r in roots:
            candidates.append(os.path.join(r, *rel.split("/")))
    else:
        clean = s.replace("\\", "/")
        for b in base_dirs:
            if not b:
                continue
            candidates.append(os.path.join(b, *clean.split("/")))

    for c in candidates:
        if _exists(c):
            return "ok", c
    return "missing", candidates[0] if candidates else s


def _iter_image_refs(text):
    """产出 (lineno, src, kind) —— 遍历 markdown 图片与 html <img>。"""
    for i, line in enumerate(text.splitlines(), 1):
        for m in RE_MD_IMG.finditer(line):
            yield i, m.group(1), "md"
        for m in RE_HTML_IMG.finditer(line):
            yield i, m.group(1), "html"


# ---------------------------------------------------------------- 归档目录检查

def _figure_expected_cols(n):
    """与 mineru_archive._figure_layout 保持一致，用于核对 cols/width_px。"""
    if n <= 1:
        return 1, 520
    if n == 2:
        return 2, 380
    if n == 4:
        return 2, 380
    if n <= 9:
        return 3, 290
    return 4, 215


def _caption_letters(caption):
    """从图注里抽出子图字母标号（a-c → a,b,c；'a Workflow' → a；'(b)' → b）。保序去重。
    注意：只认「单字母 - 单字母」的区间（两侧都不是单词字符），否则 'high-quality' /
    'state-of-the-art' 这类连字符词会被误当成区间，产生一堆假字母。"""
    out = []
    if not caption:
        return out
    for m in re.finditer(r"(?<![A-Za-z0-9])([a-z])\s*[-\u2013\u2014]\s*([a-z])(?![A-Za-z0-9])", caption):
        a, b = ord(m.group(1)), ord(m.group(2))
        if 0 < b - a <= 12:
            for c in range(a, b + 1):
                out.append(chr(c))
    for m in re.finditer(r"[\(\[]\s*([a-z])\s*[\)\]]", caption):
        out.append(m.group(1))
    for m in re.finditer(r"(?:^|[\s.;:,])([a-z])(?=\s+[A-Z][a-z])", caption):
        out.append(m.group(1))
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def check_archive(archive_dir, manifest, rep):
    """archive_dir 可为 None（仅给了 manifest 文件）。"""
    figures = manifest.get("figures") or []
    images = manifest.get("images") or []
    counts = manifest.get("counts") or {}
    images_dir = os.path.join(archive_dir, "images") if archive_dir else None

    # ---------- 1) 破图：manifest 里每个子图文件是否真实存在 ----------
    missing = []
    for fig in figures:
        folder = fig.get("folder") or ""
        for p in fig.get("panels") or []:
            fn = p.get("file")
            if not fn:
                missing.append((folder, "<空文件名>"))
                continue
            if images_dir is None:
                continue
            fp = os.path.join(images_dir, folder, fn)
            if not _exists(fp):
                missing.append((folder, fn))
    if images_dir is None:
        rep.info("破图", "未提供归档目录，跳过磁盘存在性检查（仅校验清单内部一致性）")
    elif missing:
        rep.error("破图", "清单引用的 %d 个图片文件在归档目录中不存在：" % len(missing))
        for folder, fn in missing[:30]:
            rep.error("破图", "  images/%s/%s" % (folder, fn))
        if len(missing) > 30:
            rep.error("破图", "  …另有 %d 个" % (len(missing) - 30))
    else:
        rep.ok("破图", "清单引用的全部 %d 个子图文件均存在" % sum(len(f.get("panels") or []) for f in figures))

    # ---------- 4a) 图片重复引用（manifest 内同一文件被登记多次）----------
    seen = {}
    for img in images:
        fn = img.get("filename")
        if fn:
            seen.setdefault(fn, []).append(img.get("folder"))
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    if dups:
        rep.error("图片重复引用", "%d 个图片文件在清单中被重复登记：" % len(dups))
        for fn, folders in list(dups.items())[:20]:
            rep.error("图片重复引用", "  %s → %s" % (fn, ", ".join(str(x) for x in folders)))
    else:
        rep.ok("图片重复引用", "清单内 %d 个图片文件无重复登记" % len(images))

    # ---------- 2) 图注/子图数 vs panel 数 vs layout_html ----------
    problems = []
    for fig in figures:
        folder = fig.get("folder")
        token = fig.get("token")
        pc = fig.get("panel_count")
        panels = fig.get("panels") or []
        html = fig.get("layout_html") or ""
        n_img = len(re.findall(r"<img\b", html, re.I))
        n_div = len(re.findall(r"<div\b", html, re.I))
        n_div_close = len(re.findall(r"</div>", html, re.I))
        cols = (fig.get("layout") or {}).get("cols")
        wpx = (fig.get("layout") or {}).get("width_px")

        if pc != len(panels):
            problems.append("ERROR|%s: panel_count=%s 但 panels[] 有 %d 项" % (folder, pc, len(panels)))
        if n_img != pc:
            problems.append("ERROR|%s: layout_html 里 <img> 有 %d 个，与 panel_count=%s 不符" % (folder, n_img, pc))
        if n_div != 1 or n_div_close != 1:
            problems.append("ERROR|%s: layout_html 的 div 不配对（<div>=%d, </div>=%d），预期 1/1" % (folder, n_div, n_div_close))
        exp_cols, exp_w = _figure_expected_cols(len(panels) or (pc or 0))
        if cols != exp_cols or wpx != exp_w:
            problems.append("WARN|%s: layout.cols/width_px=%s/%s，按 %d 个子图应为 %s/%s" %
                            (folder, cols, wpx, len(panels), exp_cols, exp_w))
        # 图注提到的子图字母数 vs panel 数（仅对图；表没有子图字母）
        if fig.get("kind") == "Figure":
            letters = _caption_letters(fig.get("caption_en") or "")
            if letters and abs(len(letters) - len(panels)) >= 1:
                problems.append("WARN|%s: 图注提到的子图字母 %d 个（%s），与 panel 数 %d 不符" %
                                (folder, len(letters), ",".join(letters), len(panels)))
        # 磁盘上该图目录里的实际图片数 vs panel_count（真正"网格数对不上"的硬指标）
        if images_dir and _isdir(os.path.join(images_dir, folder)):
            files = [x for x in os.listdir(os.path.join(images_dir, folder))
                     if x.lower().endswith(IMAGE_EXTS)]
            if len(files) != pc:
                problems.append("ERROR|%s: images/%s/ 下实际有 %d 张图，与 panel_count=%s 不符" %
                                (folder, folder, len(files), pc))
            txf = os.path.join(images_dir, folder, folder + ".txt")
            if _exists(txf):
                m = re.search(r"面板数[：:]\s*(\d+)", _read(txf))
                if m and int(m.group(1)) != pc:
                    problems.append("ERROR|%s: %s.txt 记的面板数=%s，与清单 panel_count=%s 不符" %
                                    (folder, folder, m.group(1), pc))
        # 子图字母标号缺失
        subs = [p.get("sub") for p in panels]
        if len(panels) > 1 and all(s is None for s in subs):
            problems.append("WARN|%s: %d 个子图全部缺 sub 字母标号（MinerU caption 为空，需人工补）" %
                            (folder, len(panels)))
        elif len(panels) > 1:
            known = sorted([s for s in subs if s])
            if known and (len(set(known)) != len(known)):
                problems.append("WARN|%s: 子图 sub 字母有重复：%s" % (folder, known))
    hard = [p for p in problems if p.startswith("ERROR|")]
    soft = [p for p in problems if p.startswith("WARN|")]
    if hard:
        rep.error("图注/子图数", "%d 处 panel 数 与 清单/layout_html 不一致：" % len(hard))
        for p in hard:
            rep.error("图注/子图数", "  " + p.split("|", 1)[1])
    else:
        rep.ok("图注/子图数", "%d 个逻辑图的 panel_count / panels[] / layout_html 一致" % len(figures))
    for p in soft:
        rep.warn("图注/子图数", "  " + p.split("|", 1)[1])

    # ---------- 4b) 图注编号跳号 / 重号（按数字 token）----------
    def tokens_of(kind):
        out = []
        for fig in figures:
            if fig.get("kind") != kind:
                continue
            t = str(fig.get("token") or "")
            if re.fullmatch(r"\d+", t):
                out.append(int(t))
        return out

    for kind in ("Figure", "Table"):
        toks = tokens_of(kind)
        if not toks:
            continue
        uniq = sorted(set(toks))
        dup = sorted({t for t in toks if toks.count(t) > 1})
        gaps = [n for n in range(uniq[0], uniq[-1] + 1) if n not in set(toks)]
        if dup:
            rep.error("图注编号", "%s 编号重复：%s" % (kind, ", ".join(str(x) for x in dup)))
        if gaps:
            rep.error("图注编号", "%s 编号跳号：缺 %s（现有 %s）" %
                      (kind, ", ".join(str(x) for x in gaps), ", ".join(str(x) for x in uniq)))
        if not dup and not gaps:
            rep.ok("图注编号", "%s 编号连续无重复：%s" % (kind, ", ".join(str(x) for x in uniq)))

    # ---------- 计数自洽 ----------
    n_fig = sum(1 for f in figures if f.get("kind") == "Figure")
    n_tab = sum(1 for f in figures if f.get("kind") == "Table")
    if counts.get("figures") not in (None, n_fig) or counts.get("tables") not in (None, n_tab):
        rep.warn("计数自洽", "counts 与 figures[] 实际不符：counts={figures:%s,tables:%s} vs 实际 %d/%d" %
                 (counts.get("figures"), counts.get("tables"), n_fig, n_tab))
    else:
        rep.ok("计数自洽", "counts.figures/tables 与 figures[] 一致（%d/%d）" % (n_fig, n_tab))


# ---------------------------------------------------------------- 正文/笔记检查

def check_text(text, label, rep, ref_map=None, source_text=None, min_ratio=0.15):
    body = text.splitlines()

    # ---------- 3a) 结尾异常 ----------
    fence = len(re.findall(r"^\s*```", text, re.M))
    if fence % 2:
        rep.error("正文截断", "%s: 代码块 ``` 未闭合（出现 %d 次，为奇数）" % (label, fence))
    dd = len(re.findall(r"\$\$", text))
    if dd % 2:
        rep.error("正文截断", "%s: 数学块 $$ 未闭合（出现 %d 次，为奇数）" % (label, dd))
    nd_open = len(re.findall(r"<div\b", text, re.I))
    nd_close = len(re.findall(r"</div>", text, re.I))
    if nd_open != nd_close:
        rep.error("正文截断", "%s: HTML <div> 未配对（<div>=%d, </div>=%d），图片网格可能被截断" %
                  (label, nd_open, nd_close))

    # ---------- 2b) 成品图块：data-panel-count / data-layout-cols vs 块内 <img> 数 vs 清单 ----------
    figs_by_folder = {}
    for f in (ref_map or {}).get("figures") or []:
        figs_by_folder[(f.get("folder") or "").lower()] = f
    blocks = list(re.finditer(r'<div\b[^>]*data-panel-count\s*=\s*"(\d+)"[^>]*>(.*?)</div>',
                              text, re.I | re.S))
    for m in blocks:
        declared = int(m.group(1))
        inner = m.group(2)
        n_img = len(re.findall(r"<img\b", inner, re.I))
        ln = text.count("\n", 0, m.start()) + 1
        if n_img != declared:
            rep.error("图注/子图数", "%s: 第 %d 行图块 data-panel-count=%d，但块内只有 %d 个 <img>（网格数与声明不符）" %
                      (label, ln, declared, n_img))
        cm = re.search(r'data-layout-cols\s*=\s*"(\d+)"', m.group(0))
        if cm:
            exp_cols, _ = _figure_expected_cols(declared)
            if int(cm.group(1)) != exp_cols:
                rep.warn("图注/子图数", "%s: 第 %d 行图块 data-layout-cols=%s，按 %d 张子图应为 %d" %
                         (label, ln, cm.group(1), declared, exp_cols))
        fm = re.search(r"\bimages/([^/\"]+)/", inner)
        if fm and figs_by_folder:
            folder = fm.group(1)
            fig = figs_by_folder.get(folder.lower())
            if fig is None:
                rep.warn("图注/子图数", "%s: 第 %d 行图块引用的 %s 不在清单里" % (label, ln, folder))
            elif fig.get("panel_count") != declared:
                rep.error("图注/子图数", "%s: 第 %d 行图块 %s 声明 %d 张，清单 panel_count=%s" %
                          (label, ln, folder, declared, fig.get("panel_count")))
    if blocks:
        rep.ok("图注/子图数", "%s: %d 个成品图块的 data-panel-count 与块内 <img> 数、清单一致" % (label, len(blocks)))

    tail = [ln for ln in body if ln.strip()]
    if tail:
        last = tail[-1].rstrip()
        ln_no = len(body)
        if last.startswith("|") and not last.endswith("|"):
            rep.error("正文截断", "%s: 最后一行是未闭合的 Markdown 表格（第 %d 行）：%s" %
                      (label, ln_no, last[:80]))
        elif last.endswith("|") and tail.count(last) and len([l for l in tail[-4:] if l.strip().startswith("|")]) < 2:
            rep.warn("正文截断", "%s: 结尾像单个表格行，表格可能被截断（第 %d 行）" % (label, ln_no))
        else:
            ok_end = ("。", "！", "？", ".", "!", "?", ")", "）", "\"", "'", "」", "』", ":", "：", ">", "=", "-")
            if not last.endswith(ok_end) and len(last) > 0:
                if re.search(r"[\w\u4e00-\u9fff]$", last):
                    rep.warn("正文截断", "%s: 结尾无收束标点、疑似半句（第 %d 行）：%s" %
                             (label, ln_no, last[-60:]))
    if not any(i[0] == "ERROR" and i[1] == "正文截断" for i in rep.items):
        rep.ok("正文截断", "%s: 结尾标点/代码块/$$/div 闭合正常" % label)

    # ---------- 3b) 长度差异 ----------
    if source_text is not None:
        src_len, note_len = len(source_text), len(text)
        ratio = (note_len / src_len) if src_len else 1.0
        if ratio < min_ratio:
            rep.error("正文截断", "笔记正文 %d 字符，仅为源文 00_full_cleaned.md（%d 字符）的 %.1f%%（阈值 %.0f%%）——疑似只译了一小部分" %
                      (note_len, src_len, ratio * 100, min_ratio * 100))
        else:
            rep.ok("正文截断", "笔记正文/源文长度比 %.1f%%（源 %d → 笔记 %d 字符）" %
                   (ratio * 100, src_len, note_len))

    # ---------- 1) 破图（正文里引用的图片）----------
    roots = _ancestor_roots(os.path.dirname(os.path.abspath(ref_map["_doc_dir"])) if ref_map and ref_map.get("_doc_dir") else os.getcwd(),
                            ref_map.get("_archive_dir") if ref_map else None,
                            os.getcwd())
    base_dirs = [ref_map.get("_archive_dir") if ref_map else None,
                 ref_map.get("_doc_dir") if ref_map else None]
    refs = list(_iter_image_refs(text))
    broken, remote = [], []
    seen_line = {}
    for ln_no, src, kind in refs:
        status, path = _resolve_image(src, roots, base_dirs)
        if status == "remote":
            remote.append((ln_no, src))
            continue
        if status == "missing":
            broken.append((ln_no, src))
        # 去重键用「解析后的真实路径」，避免同一张图两种写法（images/x.jpg 与
        # /workspace/…/Figure2/x.jpg）被误判成重复；真实重复插入才会同路径。
        key = path if status == "ok" else src
        seen_line.setdefault(key, []).append(ln_no)
    if broken:
        rep.error("破图", "%s: 引用到 %d 个不存在的图片文件：" % (label, len(broken)))
        for ln_no, src in broken[:30]:
            rep.error("破图", "  第 %d 行 → %s" % (ln_no, src))
        if len(broken) > 30:
            rep.error("破图", "  …另有 %d 个" % (len(broken) - 30))
    elif refs:
        rep.ok("破图", "%s: %d 处图片引用均解析到真实文件" % (label, len([r for r in refs if _resolve_image(r[1], roots, base_dirs)[0] == "ok"])))
    if remote:
        rep.info("破图", "%s: %d 处为远程 http(s) 图片，跳过存在性检查" % (label, len(remote)))
    if not refs:
        rep.warn("破图", "%s: 正文里没有任何图片引用（译本笔记应图文并茂）" % label)

    # ---------- 4a) 图片重复引用 ----------
    dup_lines = {k: v for k, v in seen_line.items() if len(v) > 1}
    if dup_lines:
        rep.error("图片重复引用", "%s: %d 张图片被重复引用：" % (label, len(dup_lines)))
        for k, lines in list(dup_lines.items())[:20]:
            rep.error("图片重复引用", "  %s → 行 %s" % (k, ", ".join(str(x) for x in lines)))
    elif refs:
        rep.ok("图片重复引用", "%s: 无重复引用的图片" % label)

    # ---------- 2) 正文图注编号 vs 清单 ----------
    if ref_map and ref_map.get("figures"):
        in_body_fig, in_body_tab = set(), set()
        for m in RE_CAP_TOKEN.finditer(text):
            if re.match(r"[Ss]\d", m.group(2)):
                continue  # Supplementary（Fig. S1 / Table S7）另行编号，不参与正图号比对
            kind = "Figure" if m.group(1).lower().startswith("fig") else "Table"
            (in_body_fig if kind == "Figure" else in_body_tab).add(_norm_token(m.group(2)))
        for m in RE_CN_TOKEN.finditer(text):
            if re.match(r"[Ss]\d", m.group(2)):
                continue
            (in_body_fig if m.group(1) == "图" else in_body_tab).add(_norm_token(m.group(2)))
        map_fig = {_norm_token(f.get("token")) for f in ref_map["figures"]
                   if f.get("kind") == "Figure" and not re.match(r"[Ss]\d", str(f.get("token") or ""))}
        map_tab = {_norm_token(f.get("token")) for f in ref_map["figures"]
                   if f.get("kind") == "Table" and not re.match(r"[Ss]\d", str(f.get("token") or ""))}
        extra_f = sorted(x for x in in_body_fig if x and x not in map_fig)
        extra_t = sorted(x for x in in_body_tab if x and x not in map_tab)
        miss_f = sorted(x for x in map_fig if x and x not in in_body_fig)
        if extra_f or extra_t:
            rep.warn("图注编号", "%s: 正文引用了清单里没有的编号 —— 图:%s 表:%s（疑似编号写错或漏归档）" %
                     (label, ",".join(extra_f) or "-", ",".join(extra_t) or "-"))
        if miss_f:
            rep.warn("图注编号", "%s: 清单里的图 %s 未在正文/图注中出现" % (label, ",".join(miss_f)))
        if not (extra_f or extra_t or miss_f):
            rep.ok("图注编号", "%s: 正文图注编号与清单完全对应（图 %d / 表 %d）" %
                   (label, len(map_fig), len(map_tab)))


def _norm_token(t):
    t = str(t or "").strip()
    m = re.match(r"[Ss]?(\d+)[a-z]?$", t)
    return m.group(1) if m else t


# ---------------------------------------------------------------- 装载

def load_manifest(path):
    data = json.loads(_read(path))
    if isinstance(data, dict) and "figures" in data:
        return data
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="MinerU 归档产物 / 中文译本笔记的产出校验（破图 / 图注子图数 / 正文截断 / 重复与跳号）")
    ap.add_argument("target", help="归档目录、00_images_map.json 或 中文笔记 .md/.txt")
    ap.add_argument("--archive", default=None, help="笔记模式下指定对应归档目录（用于校验图片是否存在）")
    ap.add_argument("--min-ratio", type=float, default=0.15,
                    help="笔记正文长度 / 源文长度 的最小比值，低于此值报截断（默认 0.15）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    tgt = args.target
    if not os.path.exists(tgt):
        print("错误：路径不存在：%s" % tgt, file=sys.stderr)
        return 2

    rep = Report()
    is_dir = os.path.isdir(tgt)
    manifest = None
    archive_dir = None
    doc_text = None
    doc_label = None
    doc_dir = None

    if is_dir:
        archive_dir = os.path.abspath(tgt)
        mp = os.path.join(archive_dir, "00_images_map.json")
        if not _exists(mp):
            print("错误：归档目录缺少 00_images_map.json：%s" % archive_dir, file=sys.stderr)
            return 2
        try:
            manifest = load_manifest(mp)
        except Exception as e:
            print("错误：解析 %s 失败：%s" % (mp, e), file=sys.stderr)
            return 2
        doc_dir = archive_dir
        rep.info("目标", "归档目录：%s" % archive_dir)
    else:
        tgt = os.path.abspath(tgt)
        doc_dir = os.path.dirname(tgt)
        ext = os.path.splitext(tgt)[1].lower()
        if ext == ".json":
            manifest = load_manifest(tgt)
            if manifest is None:
                print("错误：%s 不是有效的 00_images_map.json" % tgt, file=sys.stderr)
                return 2
            archive_dir = os.path.dirname(tgt)
            rep.info("目标", "清单元数据文件：%s" % tgt)
        else:
            doc_text = _read(tgt)
            doc_label = os.path.basename(tgt)
            rep.info("目标", "笔记正文：%s" % tgt)
            if args.archive:
                archive_dir = os.path.abspath(args.archive)
            else:
                # 尝试从笔记同目录向上找 00_images_map.json
                d, hops = doc_dir, 0
                while hops < 4:
                    cand = os.path.join(d, "00_images_map.json")
                    if _exists(cand):
                        archive_dir = d
                        break
                    nd = os.path.dirname(d)
                    if nd == d:
                        break
                    d, hops = nd, hops + 1
            if archive_dir:
                mp = os.path.join(archive_dir, "00_images_map.json")
                if _exists(mp):
                    try:
                        manifest = load_manifest(mp)
                        rep.info("目标", "关联归档目录：%s" % archive_dir)
                    except Exception as e:
                        rep.warn("破图", "读取关联 00_images_map.json 失败：%s" % e)
                else:
                    rep.warn("破图", "关联路径下没有 00_images_map.json：%s" % archive_dir)
            else:
                rep.info("目标", "未找到关联归档目录（未给 --archive），跳过清单类检查")

    if manifest is not None:
        ad = archive_dir if archive_dir else doc_dir
        if ad and not _isdir(os.path.join(ad, "images")):
            # manifest 的 out_dir 兜底
            od = manifest.get("out_dir")
            if od and _isdir(os.path.join(od, "images")):
                ad = od
            else:
                rep.warn("破图", "归档目录下没有 images/ 子目录，磁盘存在性检查可能误报")
        check_archive(ad, manifest, rep)

    if doc_text is not None:
        source_text = None
        if archive_dir:
            sc = os.path.join(archive_dir, "00_full_cleaned.md")
            if _exists(sc):
                source_text = _read(sc)
        check_text(doc_text, doc_label, rep,
                   ref_map={"_archive_dir": archive_dir, "_doc_dir": doc_dir,
                            "figures": (manifest or {}).get("figures")},
                   source_text=source_text, min_ratio=args.min_ratio)

    elif is_dir or (doc_dir and manifest is not None and not is_dir):
        # 归档模式：额外查 00_full_cleaned.md 本身
        ad = archive_dir or doc_dir
        cleaned = os.path.join(ad, "00_full_cleaned.md")
        if _exists(cleaned):
            ctext = _read(cleaned)
            if not ctext.strip():
                rep.error("正文截断", "00_full_cleaned.md 为空")
            else:
                full = os.path.join(ad, "full.md")
                src = _read(full) if _exists(full) else None
                ratio = (len(ctext) / len(src)) if src else None
                if ratio is not None and ratio < 0.25:
                    rep.error("正文截断", "00_full_cleaned.md（%d 字符）不足 full.md（%d 字符）的 25%%（%.1f%%），正文被严重截断" %
                              (len(ctext), len(src), ratio * 100))
                elif ratio is not None and ratio < 0.4:
                    rep.warn("正文截断", "00_full_cleaned.md（%d 字符）不足 full.md（%d 字符）的 40%%（%.1f%%），清洗可能误截 References" %
                             (len(ctext), len(src), ratio * 100))
                else:
                    rep.ok("正文截断", "00_full_cleaned.md %d 字符%s" %
                           (len(ctext), "（full.md 的 %.1f%%）" % (ratio * 100) if ratio else ""))
                check_text(ctext, "00_full_cleaned.md", rep,
                           ref_map={"_archive_dir": ad, "_doc_dir": ad, "figures": (manifest or {}).get("figures")})
        else:
            rep.warn("正文截断", "归档目录缺少 00_full_cleaned.md")

    # ---------------------------------------------------------------- 输出
    errors = rep.count("ERROR")
    warnings = rep.count("WARN")
    code = 1 if errors else 0

    if args.json:
        print(json.dumps({
            "target": tgt, "archive": archive_dir,
            "errors": errors, "warnings": warnings, "exit_code": code,
            "items": [{"level": l, "check": c, "message": m} for l, c, m in rep.items],
        }, ensure_ascii=False, indent=2))
        return code

    print("=" * 68)
    print("产出校验 verify_note.py：%s" % tgt)
    if archive_dir:
        print("归档目录：%s" % archive_dir)
    print("=" * 68)
    order = {"ERROR": 0, "WARN": 1, "PASS": 2, "INFO": 3}
    tag = {"ERROR": "[错误]", "WARN": "[警告]", "PASS": "[通过]", "INFO": "[信息]"}
    for l, c, m in sorted(rep.items, key=lambda i: order[i[0]]):
        if m.startswith("  "):
            print("        %s" % m)
        else:
            print("%s %-12s %s" % (tag[l], c, m))
    print("-" * 68)
    print("结果：%d 个错误，%d 个警告  →  退出码 %d（%s）" %
          (errors, warnings, code, "失败" if code else "通过"))
    return code


if __name__ == "__main__":
    sys.exit(main())
