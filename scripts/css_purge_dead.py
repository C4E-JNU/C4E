# -*- coding: utf-8 -*-
"""C4EAI 前端体检 ②：按 css_class_audit.py 的结论删掉死样式。
用法:
    python scripts/css_purge_dead.py [项目根]            # 预演（只打印）
    python scripts/css_purge_dead.py [项目根] --apply     # 真改（原子写 + .bak_deadpurge）

做法：真解析（递归下降，逐字符跳过注释与字符串），把整份 CSS 解析成节点树后
**重新序列化**，不做「按字符区间删除」——区间算术在规则相邻/嵌套时会切错位置
（曾把 `.db-tcount { margin-left` 切成 `.db-tcoumargin-left`），所以这里改成
每个节点独立决定：留着就原样输出（必要时只摘选择器段），整条死了就整块不输出。

规则：
  · 规则选择器列表里摘掉含死类的段；全死 → 整条不输出（连同它前面的空行/注释）；
  · @media/@supports 递归处理，处理完里面空了就连外壳一起丢；
  · @keyframes 整体原样保留（里面的 0%/from/to 不是类选择器）；
  · @import / @charset 等顶层语句原样保留。
"""
import os, re, sys, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from css_class_audit import css_classes, LIB_PREFIX, STATE_WORDS

PROJ = next((a for a in sys.argv[1:] if not a.startswith("--")), ".")
APPLY = "--apply" in sys.argv


# ── 词法：跳过注释与字符串 ──────────────────────────────────────────
def skip_ws(text, i, out_pre):
    """收集前导空白与注释原文，返回新位置。"""
    n = len(text)
    while i < n:
        if text[i] in " \t\r\n":
            out_pre.append(text[i]); i += 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out_pre.append(text[i:j]); i = j
        else:
            break
    return i


def find_struct(text, i):
    """返回下一个结构性字符 { } ; 的位置（跳过注释与字符串），没有则 len(text)。"""
    n = len(text)
    while i < n:
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        c = text[i]
        if c in "\"'":
            q = c
            j = i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c in "{};":
            return i
        i += 1
    return n


def match_brace(text, open_idx):
    """返回与 open_idx 处 { 匹配的 } 下标（跳过注释与字符串）。"""
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        c = text[i]
        if c in "\"'":
            q = c
            j = i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("括号不匹配：%d" % open_idx)


# ── 解析 ────────────────────────────────────────────────────────────
def parse_list(text, i, end):
    """把 [i,end) 解析成一串节点。"""
    nodes = []
    while i < end:
        pre = []
        i = skip_ws(text, i, pre)
        if i >= end:
            nodes.append({"kind": "tail", "raw": "".join(pre)})
            break
        j = find_struct(text, i)
        if j >= end or text[j] == "}":
            nodes.append({"kind": "tail", "raw": "".join(pre) + text[i:end]})
            break
        prelude = text[i:j]
        if text[j] == ";":                                   # @import / @charset 等
            nodes.append({"kind": "raw", "pre": "".join(pre), "raw": text[i:j + 1]})
            i = j + 1
            continue
        close = match_brace(text, j)
        if close >= end:
            raise ValueError("块越过容器边界：%d" % j)
        head = prelude.strip()
        if head.startswith("@") and re.match(r"@(media|supports|layer|container|scope|document)\b", head):
            nodes.append({"kind": "at", "pre": "".join(pre), "prelude": prelude,
                          "children": parse_list(text, j + 1, close)})
        elif head.startswith("@keyframes") or head.startswith("@-webkit-keyframes"):
            nodes.append({"kind": "raw", "pre": "".join(pre), "raw": prelude + text[j:close + 1]})
        else:
            nodes.append({"kind": "rule", "pre": "".join(pre), "prelude": prelude,
                          "body": text[j + 1:close]})
        i = close + 1
    return nodes


# ── 序列化 + 摘死类 ─────────────────────────────────────────────────
def has_dead(sel, dead):
    return any(re.search(r"\.%s(?![\w-])" % re.escape(d), sel) for d in dead)


def split_sel(sel):
    """按逗号切选择器段（段内保留原有换行缩进；逗号由调用方重新连接）。"""
    return [p for p in sel.split(",") if p.strip() != ""]


def prune_sel(sel, dead):
    """摘掉含死类的段，**用逗号重新连接**——绝不能丢掉逗号，
    否则 `A, B {` 会变成 `A B {`（后代选择器），静默改样式。"""
    parts = split_sel(sel)
    keep = [p for p in parts if not has_dead(p, dead)]
    return ",".join(keep), len(parts) - len(keep)


def serialize(nodes, dead, stats):
    out = []
    for nd in nodes:
        if nd["kind"] == "tail":
            out.append(nd["raw"])
        elif nd["kind"] == "raw":
            out.append(nd.get("pre", "") + nd["raw"])
        elif nd["kind"] == "rule":
            new_sel, dropped = prune_sel(nd["prelude"], dead)
            if not new_sel.strip():
                stats["规则"] += 1
                continue                                     # 整条丢（连前导空行/注释）
            assert new_sel.count(",") == len(split_sel(new_sel)) - 1, "选择器逗号数不对"
            if dropped:
                stats["摘段"] += 1
            else:
                stats["保留"] += 1
            out.append(nd["pre"] + new_sel.strip() + " {" + nd["body"] + "}")
        else:                                                # at 块（媒体查询等）
            inner_stats = dict(stats)
            inner = serialize(nd["children"], dead, inner_stats)
            if inner.strip() == "" and re.match(r"@(media|supports)\b", nd["prelude"].strip()):
                stats["空外壳"] += 1
                continue
            for k in inner_stats:
                stats[k] = inner_stats[k]
            out.append(nd["pre"] + nd["prelude"].strip() + " {" + inner + "}")
    return "".join(out)


def main():
    idx = open(os.path.join(PROJ, "index.html"), encoding="utf-8", newline="").read()
    css = [x.split("?")[0] for x in re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', idx) if not x.startswith("http")]
    js = [x.split("?")[0] for x in re.findall(r'<script src="([^"]+)"', idx) if not x.startswith("http")]
    code = "\n".join(open(os.path.join(PROJ, f), encoding="utf-8", newline="").read()
                     for f in ["index.html"] + js if os.path.exists(os.path.join(PROJ, f)))
    literal, dyn, toks = set(), set(), set()
    for pat in [r'class="([^"]*)"', r"class='([^']*)'", r'className\s*=\s*[`\'"]([^`\'"]*)[`\'"]',
                r'classList\.(?:add|remove|toggle)\(([^)]*)\)', r'setAttribute\(\s*[\'"]class[\'"]\s*,\s*([^)]*)\)']:
        for m in re.finditer(pat, code):
            literal |= {p for p in re.split(r"[^A-Za-z0-9_-]+", m.group(1)) if p}
    for m in re.finditer(r"['\"`]([A-Za-z][\w-]*-)['\"`]", code):
        dyn.add(m.group(1))
    for m in re.finditer(r"['\"`]([A-Za-z][\w-]*)['\"`]", code):
        toks.add(m.group(1))
    dead = set()
    for f in css:
        for name in css_classes(os.path.join(PROJ, f)):
            if (name in literal or name in toks or name.startswith("theme-") or name in STATE_WORDS
                    or name.startswith(LIB_PREFIX) or any(name.startswith(d) for d in dyn)):
                continue
            dead.add(name)
    if not dead:
        print("没有死样式，无需清理 ✅")
        return 0
    print("死类 %d 个" % len(dead))
    total = 0
    for f in css:
        p = os.path.join(PROJ, f)
        raw = open(p, encoding="utf-8", newline="").read()
        stats = {"规则": 0, "摘段": 0, "空外壳": 0, "保留": 0}
        t = serialize(parse_list(raw, 0, len(raw)), dead, stats)
        if t == raw:
            continue
        print("  %-20s 删规则 %d 条、摘选择器段 %d 处、丢空外壳 %d 个；%d → %d 行"
              % (f, stats["规则"], stats["摘段"], stats["空外壳"], raw.count("\n"), t.count("\n")))
        total += stats["规则"]
        if APPLY:
            shutil.copy(p, p + ".bak_deadpurge")
            tmp = p + ".tmpw"
            open(tmp, "w", encoding="utf-8", newline="").write(t)
            os.replace(tmp, p)
    print("合计删 %d 条规则" % total + ("" if APPLY else "（预演，未写盘；加 --apply 才生效）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
