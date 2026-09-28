#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_figure_block.py —— 从 MinerU 归档目录生成「成品 Markdown 图块」（替代人工手抄 layout_html）。

为什么有这个脚本：
  老流程要求模型把 ~1.2KB 的 layout_html 一字不差地抄进笔记，抄错一个字符就破图。
  本脚本直接读 <归档目录>/00_images_map.json 的 figures[]（含 panels / layout / caption_en），
  生成排版正确的成品块：一个逻辑图 = 一个 flex 网格 div（panel_count 张 <img>）+ 一条图注。
  模型只需把脚本输出（或 --out 落盘的片段）粘进笔记，不再手抄任何 HTML。

用法（纯标准库，任意 python3 可跑）：
  python render_figure_block.py <归档目录> --list
  python render_figure_block.py <归档目录> --figure Figure2
  python render_figure_block.py <归档目录> --figure Figure2 --caption "三个数据集的特征与验证。a–c …" --out block.md
  python render_figure_block.py <归档目录> --figure Figure2 --alts "a=膜分离示意;b=装置示意" --out block.md
  python render_figure_block.py <归档目录> --all --out figures.md
  python render_figure_block.py <归档目录> --figure Table1 --path-mode relative

参数：
  <归档目录>            含 00_images_map.json 的目录，如 workspace/mineru_output/<论文名>
  --figure NAME         只渲染该逻辑图（folder 名，如 Figure2 / Table1）；可重复多次
  --all                 渲染全部逻辑图（按 images_map 顺序，块间空行分隔）
  --caption TEXT        该图的中文图注正文（仅当只选中一个图时可用；编号"图N/表N"由脚本按 images_map 的 token 自动加）
  --alts "a=膜分离;b=装置"  子图 alt 的中文短说明；键=子图字母 sub（无字母子图用 "1"/"2"… 序号）。
                        未给某子图则 alt 仅为"图Na"。例："a=膜分离示意;b=装置示意"
  --out FILE            写入该文件（UTF-8，自动创建上级目录）；省略则打印到 stdout
  --path-mode MODE      web       (默认) 原样使用 images_map 里的 web_url（/workspace/... 绝对路径，笔记渲染用）
                        relative   相对归档目录：images/<folder>/<file>.jpg
                        absolute   归档目录绝对路径 + 相对部分（正斜杠）
  --list                仅列出可渲染的逻辑图（folder / kind / token / panel_count），不生成块
  --report              额外把机器可核验的 JSON 摘要打到 stderr（src 列表、panel_count、cols/width）

输出格式（每个逻辑图一块）：
  <div data-layout-cols="3" data-panel-count="8" style="display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-start;gap:8px;">
  <img src="/workspace/mineru_output/<论文名>/images/Figure2/<hash>.jpg" alt="图2a 膜分离示意" style="width:290px;height:auto;border-radius:6px;cursor:zoom-in;" />
  ...（共 panel_count 张）
  </div>

  **图 2　<中文图注>**
  （未给 --caption 时，图注用 caption_en 占位，并在其上方加 <!-- TODO 译成中文 --> 注释）

要点：
  - 网格行列完全来自 images_map 的 layout.cols / layout.width_px（1→1列520 2→2列380 3→3列290 4→2×2 5–9→3列290 ≥10→4列215）；
  - 图片路径默认与 images_map 的 web_url 逐字一致（同一 panel 同一字符串，不会抄错）；
  - 图注编号用 images_map 的 token（与 caption_en 的 "Fig. N/Table N" 对应）。
"""
import os
import sys
import json
import argparse

try:  # Windows 终端默认可能是 cp936，强制 UTF-8 以免中文报错
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _load_map(archive_dir):
    path = os.path.join(archive_dir, "00_images_map.json")
    if not os.path.isfile(path):
        sys.exit(f"[ERROR] 找不到 00_images_map.json：{path}\n"
                 f"        请确认 <归档目录> 是 mineru-agent-parse 的产物目录。")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _figures_of(data):
    figs = data.get("figures")
    if not isinstance(figs, list) or not figs:
        sys.exit("[ERROR] 00_images_map.json 里没有 figures[]，无法生成图块。")
    return figs


def _label_of(fig):
    kind = str(fig.get("kind", "Figure"))
    return "表" if kind.lower().startswith("tab") else "图"


def _resolve_src(panel, fig, archive_dir, mode):
    folder = fig.get("folder", "")
    fname = panel.get("file", "")
    if mode == "web":
        return panel.get("web_url") or f"/workspace/mineru_output/{os.path.basename(archive_dir.rstrip(os.sep))}/images/{folder}/{fname}"
    if mode == "relative":
        return f"images/{folder}/{fname}"
    if mode == "absolute":
        return os.path.join(os.path.abspath(archive_dir), "images", folder, fname).replace("\\", "/")
    sys.exit(f"[ERROR] 未知 --path-mode：{mode}")


def _parse_alts(spec):
    """'a=膜分离;b=装置' -> {'a':'膜分离','b':'装置'}"""
    alts = {}
    if not spec:
        return alts
    for chunk in spec.replace("；", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            sys.exit(f"[ERROR] --alts 片段缺少 '='：{chunk!r}（应形如 a=膜分离示意;b=装置示意）")
        k, v = chunk.split("=", 1)
        alts[k.strip()] = v.strip()
    return alts


def render_figure(fig, archive_dir, caption=None, alts=None, mode="web"):
    """生成单个逻辑图的成品 Markdown 块。返回 (markdown_text, report_dict)。"""
    panels = fig.get("panels") or []
    if not panels:
        return "", {"folder": fig.get("folder"), "panel_count": 0, "srcs": [], "error": "no panels"}
    layout = fig.get("layout") or {}
    cols = int(layout.get("cols", 1) or 1)
    wpx = int(layout.get("width_px", 290) or 290)
    label = _label_of(fig)
    token = fig.get("token", "")
    alts = alts or {}

    imgs, srcs, alt_labels = [], [], []
    for i, panel in enumerate(panels):
        src = _resolve_src(panel, fig, archive_dir, mode)
        sub = panel.get("sub")
        base = f"{label}{token}{sub if sub else ''}"
        suffix = alts.get(sub if sub else str(i + 1), "")
        alt = f"{base} {suffix}".strip()
        imgs.append(f'<img src="{src}" alt="{alt}" '
                    f'style="width:{wpx}px;height:auto;border-radius:6px;cursor:zoom-in;" />')
        srcs.append(src)
        alt_labels.append(alt)

    div = (f'<div data-layout-cols="{cols}" data-panel-count="{len(panels)}" '
           f'style="display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-start;gap:8px;">\n'
           + "\n".join(imgs) + "\n</div>")

    if caption:
        cap_line = f"**{label} {token}　{caption}**"
        todo = ""
    else:
        en = (fig.get("caption_en") or "").strip() or "（原图注为空，请据正文/图片补写中文图注）"
        todo = "<!-- TODO 把上面的英文图注译成中文并替换本行（保留图片与编号） -->\n"
        cap_line = f"**{label} {token}　{en}**"

    block = div + "\n\n" + todo + cap_line
    report = {"folder": fig.get("folder"), "kind": fig.get("kind"), "token": token,
              "panel_count": len(panels), "cols": cols, "width_px": wpx,
              "img_count": len(imgs), "alt_count": len(alt_labels),
              "alts": alt_labels, "srcs": srcs, "caption_line": cap_line,
              "has_todo": bool(todo)}
    return block, report


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="render_figure_block.py",
        description="从 MinerU 归档目录生成成品 Markdown 图块（替代手抄 layout_html）。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive_dir", help="含 00_images_map.json 的归档目录")
    ap.add_argument("--figure", action="append", default=[], metavar="NAME",
                    help="只渲染该逻辑图（folder 名，可重复）")
    ap.add_argument("--all", action="store_true", help="渲染全部逻辑图")
    ap.add_argument("--caption", default=None, help="中文图注正文（仅单图时可用）")
    ap.add_argument("--alts", default=None, help='子图 alt 中文短说明，如 "a=膜分离示意;b=装置示意"')
    ap.add_argument("--out", default=None, help="输出文件（省略则打印到 stdout）")
    ap.add_argument("--path-mode", default="web", choices=["web", "relative", "absolute"])
    ap.add_argument("--list", action="store_true", dest="do_list", help="仅列出逻辑图")
    ap.add_argument("--report", action="store_true", help="把 JSON 摘要打到 stderr")
    args = ap.parse_args(argv)

    data = _load_map(args.archive_dir)
    figs = _figures_of(data)

    if args.do_list:
        listing = [{"folder": f.get("folder"), "kind": f.get("kind"), "token": f.get("token"),
                    "panel_count": f.get("panel_count", len(f.get("panels") or [])),
                    "cols": (f.get("layout") or {}).get("cols"),
                    "width_px": (f.get("layout") or {}).get("width_px")} for f in figs]
        print(json.dumps({"paper": data.get("paper"), "figures": listing},
                         ensure_ascii=False, indent=2))
        return 0

    if not args.all and not args.figure:
        sys.exit("[ERROR] 请指定 --figure <名> 或 --all（先跑 --list 看看有哪些图）。")

    wanted = {n.strip() for n in args.figure}
    if args.all:
        selected = figs
    else:
        selected = [f for f in figs if f.get("folder") in wanted]
        missing = wanted - {f.get("folder") for f in selected}
        if missing:
            sys.exit(f"[ERROR] 归档里没有这些逻辑图：{sorted(missing)}；可用："
                     f"{[f.get('folder') for f in figs]}")

    if (args.caption or args.alts) and len(selected) != 1:
        sys.exit("[ERROR] --caption / --alts 只能配合单个 --figure 使用（当前选中 "
                 f"{len(selected)} 个图）。")

    alts = _parse_alts(args.alts)
    blocks, reports = [], []
    for fig in selected:
        block, rep = render_figure(fig, args.archive_dir,
                                   caption=args.caption, alts=alts, mode=args.path_mode)
        if block:
            blocks.append(block)
            reports.append(rep)

    out_text = "\n\n".join(blocks) + ("\n" if blocks else "")

    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"[OK] 已写 {len(blocks)} 个图块 -> {os.path.abspath(args.out)}"
              f"（共 {sum(r['img_count'] for r in reports)} 张图）", file=sys.stderr)
    else:
        sys.stdout.write(out_text)

    if args.report:
        print(json.dumps({"archive_dir": os.path.abspath(args.archive_dir),
                          "path_mode": args.path_mode, "blocks": len(blocks),
                          "total_images": sum(r["img_count"] for r in reports),
                          "reports": reports}, ensure_ascii=False, indent=2),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
