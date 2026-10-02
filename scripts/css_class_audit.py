# -*- coding: utf-8 -*-
"""C4EAI 前端体检 ①：CSS 里定义、但代码里从不生成的类（死样式）。
用法: python scripts/css_class_audit.py [项目根]
退出码: 0 = 没有死样式；1 = 有（--warn-only 时始终 0）

判定用的是"保守"规则，宁可漏报不可误删 —— 一个类只要满足任一条就算活着：
  1) 出现在 class="..." / className = '...' / classList.xxx('...') / setAttribute('class', '...') 里；
  2) 是动态拼接前缀：代码里存在字面量 '前缀-' （≥2 段，如 db-group-），则该前缀下所有类都算活；
  3) 属于外部库/框架类：katex / hljs / language- / mermaid / cm- / no- 等；
  4) 属于主题/角色/状态类：theme-* 及一组状态词（动态加的，代码里未必有字面量）；
  5) 名字在 JS 里作为字符串出现过（可能被拼进 classList）。
"""
import os, re, sys

PROJ = sys.argv[1] if len(sys.argv) > 1 else "."
WARN_ONLY = "--warn-only" in sys.argv

LIB_PREFIX = ("katex", "hljs", "language-", "mermaid", "cm-", "no-", "php", "astro")
STATE_WORDS = {
    "active", "activated", "done", "running", "error", "success", "primary", "small",
    "connected", "disconnected", "user", "assistant", "system", "tool", "database",
    "document", "note", "open", "collapsed", "expanded", "selected", "show", "hide",
    "loading", "visible", "hidden", "has-file", "drag-over", "disabled", "empty", "new",
}
CLASS_ATTR = [
    r'class="([^"]*)"', r"class='([^']*)'",
    r'className\s*=\s*[`\'"]([^`\'"]*)[`\'"]',
    r'classList\.(?:add|remove|toggle)\(([^)]*)\)',
    r'setAttribute\(\s*[\'"]class[\'"]\s*,\s*([^)]*)\)',
]

def strip_noise(t):
    """去掉不该被当成类名的东西：注释、引号字符串、url(...) 里的 data-URI（
    http://www.w3.org/... 会被误扫成 .org / .w3）。"""
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    t = re.sub(r"url\([^)]*\)", "url()", t, flags=re.S)
    t = re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", '""', t)
    return t

def css_classes(path):
    t = strip_noise(open(path, encoding="utf-8", newline="").read())
    out = {}
    for m in re.finditer(r"\.([A-Za-z][\w-]*)", t):
        out.setdefault(m.group(1), 0)
    return out

def main():
    idx = open(os.path.join(PROJ, "index.html"), encoding="utf-8", newline="").read()
    js = [x.split("?")[0] for x in re.findall(r'<script src="([^"]+)"', idx) if not x.startswith("http")]
    css = [x.split("?")[0] for x in re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', idx) if not x.startswith("http")]
    code = "\n".join(open(os.path.join(PROJ, f), encoding="utf-8", newline="").read()
                     for f in ["index.html"] + js if os.path.exists(os.path.join(PROJ, f)))

    literal, dyn_prefix, js_tokens = set(), set(), set()
    for pat in CLASS_ATTR:
        for m in re.finditer(pat, code):
            for part in re.split(r"[^A-Za-z0-9_-]+", m.group(1)):
                if part:
                    literal.add(part)
    for m in re.finditer(r"['\"`]([A-Za-z][\w-]*-)['\"`]", code):        # '前缀-'
        seg = m.group(1).count("-")
        if seg >= 1:
            dyn_prefix.add(m.group(1))
    for m in re.finditer(r"['\"`]([A-Za-z][\w-]*)['\"`]", code):
        js_tokens.add(m.group(1))

    dead = {}
    for f in css:
        p = os.path.join(PROJ, f)
        if not os.path.exists(p):
            continue
        for name in css_classes(p):
            if name in literal or name in js_tokens:
                continue
            if name.startswith("theme-") or name in STATE_WORDS:
                continue
            if name.startswith(LIB_PREFIX):
                continue
            if any(name.startswith(dp) for dp in dyn_prefix):
                continue
            dead.setdefault(name, []).append(f)

    if not dead:
        print("CSS 类审计：无死样式 ✅")
        return 0
    print("CSS 类审计：发现 %d 个「CSS 定义但代码从不生成」的类 ——" % len(dead))
    for name in sorted(dead):
        print("   .%-30s %s" % (name, ", ".join(sorted(dead[name]))))
    print("（脚本偏保守，宁可漏报；确认无用再删，注意动态拼串的类不要删）")
    return 0 if WARN_ONLY else 1

if __name__ == "__main__":
    sys.exit(main())
