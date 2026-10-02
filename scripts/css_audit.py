# -*- coding: utf-8 -*-
"""CSS 残渣校验：括号平衡 / 游离闭合 / 空规则块 / 空媒体查询 / 连续空行堆积。
只报事实，不改文件。用法: css_audit.py <项目根>"""
import os, re, sys, json

PROJ = sys.argv[1]
def strip_comments(t):
    """去注释/字符串内容（字符串整体保留），同时给出 映射：去注释后第 k 个字符 → 原文件下标。"""
    out, idx, i, n = [], [], 0, len(t)
    while i < n:
        if t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif t[i] in "\"'":
            q = t[i]; j = i + 1
            while j < n and t[j] != q:
                j += 2 if t[j] == "\\" else 1
            for k in range(i, min(j + 1, n)):
                out.append(t[k]); idx.append(k)
            i = j + 1
        else:
            out.append(t[i]); idx.append(i); i += 1
    return "".join(out), idx

report = {}
for f in sorted(os.listdir(os.path.join(PROJ, "css"))):
    if not f.endswith(".css"):
        continue
    path = os.path.join(PROJ, "css", f)
    raw = open(path, encoding="utf-8", newline="").read()
    t, idx = strip_comments(raw)
    depth, stray, maxdepth = 0, [], 0
    for m in re.finditer(r"[{}]", t):
        depth += 1 if m.group() == "{" else -1
        if depth < 0:
            stray.append(raw[:idx[m.start()]].count("\n") + 1)
            depth = 0
        maxdepth = max(maxdepth, depth)
    empty = []
    for m in re.finditer(r"\{([^{}]*)\}", t):
        body = re.sub(r"\s+", "", m.group(1))
        if body == "":
            empty.append(raw[:idx[m.start()]].count("\n") + 1)
    blanks = []
    run = 0
    for i, line in enumerate(raw.split("\n"), 1):
        if line.strip() == "":
            run += 1
            if run == 4:
                blanks.append(i)
        else:
            run = 0
    if stray or depth != 0 or empty or blanks:
        report[f] = {
            "游离的_右括号_行号": stray,
            "文件结束时未闭合的_左括号数": depth,
            "空规则块_行号": empty,
            "连续4行以上空行_起": blanks,
        }
print(json.dumps(report, ensure_ascii=False, indent=1) if report else "（全部 CSS 括号平衡、无空块、无空行堆积）")
