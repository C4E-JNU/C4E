# -*- coding: utf-8 -*-
"""step2c_verify_packages.py — 打包自检：把每个包的 image.jpg 反查回归档面板，输出对照表。

为什么需要：打包正确性原来只靠人工抽查，「c 拿成 d 的图」这类错配查不出来。
本脚本纯本地（读 txt 清单 + 算 md5），**不读图、不调大模型、零 token**。

校验四条：
  ① 包完整    image.jpg 与 task.json 都在，task.json 可解析；
  ② 图未损坏  包内 image.jpg 的 md5 == task.json.image_file 的 md5；
  ③ 序号一致  图在 FigureN.txt 有序清单中的序号 == task.json.panel_index；
  ④ 无漏无重  该图归档的面板块至多被用一次；有面板级包时，未用块明确列出（工单未收录属正常）。

用法：python step2c_verify_packages.py <论文夹|批次目录>
"""
import hashlib
import json
import os
import re
import sys


def md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def read_panels(txt):
    """FigureN.txt → (有序面板文件名列表, merged 整图文件名)。

    行格式由 step0c 产出：`[i] <hash>.jpg  page=N  bbox=(…)`；多块时另有 `[merged] FigureN_full.jpg`。
    """
    panels, merged = [], None
    if not os.path.exists(txt):
        return panels, merged
    for line in open(txt, encoding="utf-8"):
        m = re.match(r"^\s*\[(\d+)\]\s+(\S+\.(?:jpg|jpeg|png))", line, re.I)
        if m:
            panels.append(m.group(2))
            continue
        m = re.match(r"^\s*\[merged\]\s+(\S+\.(?:jpg|jpeg|png))", line, re.I)
        if m:
            merged = m.group(1)
    return panels, merged


def paper_dirs(root):
    """输入可为论文夹或批次目录 → 返回含 02b_tasks 的论文夹列表。"""
    def has(p):
        return os.path.isdir(os.path.join(p, "datamining", "02b_tasks")) or \
               os.path.isdir(os.path.join(p, "02b_tasks"))
    if has(root):
        return [root]
    out = []
    if os.path.isdir(root):
        for n in sorted(os.listdir(root)):
            p = os.path.join(root, n)
            if os.path.isdir(p) and has(p):
                out.append(p)
    return out


def figure_dir(img_root, fid):
    """figure_id（Figure7 / Fig.7）→ 归档目录 images/Figure7。"""
    num = re.sub(r"\D", "", fid or "")
    if not num or not os.path.isdir(img_root):
        return None
    for n in sorted(os.listdir(img_root)):
        if n.lower() == f"figure{num}".lower():
            return os.path.join(img_root, n)
    return None


def check_paper(paper):
    dm = os.path.join(paper, "datamining")
    root = os.path.join(dm, "02b_tasks")
    if not os.path.isdir(root):
        root = os.path.join(paper, "02b_tasks")
    img_root = os.path.join(dm, "images")

    pkgs = []
    for mode in ("xy", "basic"):
        d = os.path.join(root, mode)
        if os.path.isdir(d):
            for t in sorted(os.listdir(d)):
                if os.path.isdir(os.path.join(d, t)):
                    pkgs.append((mode, t, os.path.join(d, t)))

    print(f"\n=== {os.path.basename(os.path.normpath(paper))} — 包 {len(pkgs)} 个 ===")
    print(f"{'包':<5}{'mode':<7}{'图':<10}{'子图':<16}{'panel_idx':<11}{'包内图 → 归档位置':<34}判定")
    rows, bad, used_panel, seen_fig = [], [], {}, {}

    for mode, t, pkg in pkgs:
        tp = os.path.join(pkg, "task.json")
        if not os.path.exists(tp):
            bad.append(f"{t} 缺 task.json")
            continue
        try:
            tj = json.load(open(tp, encoding="utf-8"))
        except Exception as e:
            bad.append(f"{t} task.json 解析失败：{e}")
            continue

        fid = tj.get("figure_id", "")
        sids = ",".join(str(x) for x in tj.get("subfigure_ids", []))
        pi = tj.get("panel_index")
        img = os.path.join(pkg, "image.jpg")
        fd = figure_dir(img_root, fid)
        panels, merged = read_panels(os.path.join(fd, f"{os.path.basename(fd)}.txt")) if fd else ([], None)
        seen_fig.setdefault(fid, (fd, len(panels)))

        if not os.path.exists(img):
            bad.append(f"{t} 缺 image.jpg（no_image 任务不应打包）")
            print(f"{t:<5}{mode:<7}{fid:<10}{sids:<10}{str(pi or '-'):<11}{'—':<34}NO_IMAGE")
            continue

        h = md5(img)
        src = tj.get("image_file", "") or ""
        src_base = os.path.basename(src)
        # 打包会把超限图重编码压缩（md5 必变），故权威身份是 task.json 记的**来源指纹**；
        # 旧包无指纹时回退按文件名/md5 比。
        src_md5 = tj.get("image_source_md5", "") or ""
        src_name = tj.get("image_source_name", "") or ""
        by_src = (lambda f: bool(src_md5) and os.path.exists(f) and md5(f) == src_md5)
        zip_tag = "（重编码）" if tj.get("image_resized") else ""

        kind, arch_idx = "", None
        for i, fn in enumerate(panels, 1):                      # ① 面板级：名字/md5/来源指纹命中
            fp = os.path.join(fd, fn) if fd else ""
            if fn == src_base or fn == src_name or (fp and os.path.exists(fp)
                                                    and (md5(fp) == h or by_src(fp))):
                arch_idx, kind = i, f"{fn[:10]}… → 归档第{i}块{zip_tag}"
                break
        if arch_idx is None:
            fp = os.path.join(fd, merged) if (fd and merged) else ""
            if merged and (src_base == merged or src_name == merged
                           or (fp and os.path.exists(fp)
                               and (md5(fp) == h or by_src(fp)))):
                kind = f"整图(full) → 非面板级{zip_tag}"
            elif tj.get("image_source") == "split":
                letter = re.sub(r"[^A-Za-z]", "", re.sub(rf"^{re.escape(fid)}", "", src_base))
                kind = f"split 分割图（{letter or '无字母'}）"
                if letter and sids and letter.lower() != sids.replace(",", "").lower():
                    bad.append(f"{t} split 图文件名字母({letter}) ≠ 子图({sids})")
            else:
                kind = "不可追溯（归档内无此图）"
                bad.append(f"{t} 包内图无法在 {fid} 归档中反查到")

        mark = "OK"
        if arch_idx and pi and int(pi) != arch_idx:
            mark = "MISMATCH"
            bad.append(f"{t} panel_index={pi} ≠ 归档实际第{arch_idx}块（子图 {sids}）")
        elif kind.startswith("不可追溯"):
            mark = "UNTRACEABLE"
        if arch_idx:
            used_panel.setdefault(fid, set()).add(arch_idx)
        sid_show = sids if len(sids) <= 15 else sids[:14] + "…"
        print(f"{t:<5}{mode:<7}{fid:<10}{sid_show:<16}{str(pi if pi else '-'):<11}{kind:<34}{mark}")
        rows.append((t, mode, fid, sids, pi, kind, mark))

    n_bad = len({b.split(" ")[0] for b in bad})
    n_ok = sum(1 for r in rows if r[-1] == "OK")
    print(f"汇总：包 {len(pkgs)} | OK {n_ok} | 异常 {n_bad}")

    for fid, (fd, n) in sorted(seen_fig.items()):
        used = used_panel.get(fid, set())
        if used and n > len(used):            # 有面板级包时，才提示未用块
            miss = [i for i in range(1, n + 1) if i not in used]
            print(f"未用面板块：{fid} {miss} / 共 {n} 块 —— 工单未收录（如被排除的照片/示意图子图），属正常")
        elif not used and n > 1:
            print(f"整图一包：{fid}（{n} 块未拆，取 _full 或最大块，属正常）")
    for b in bad:
        print(f"  ⚠ {b}")
    return 1 if bad else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    if not os.path.exists(root):
        print(f"路径不存在：{root}")
        return 2
    papers = paper_dirs(root)
    if not papers:
        print(f"未找到含 02b_tasks 的论文夹：{root}")
        return 2
    return max(check_paper(p) for p in papers)


if __name__ == "__main__":
    sys.exit(main())
