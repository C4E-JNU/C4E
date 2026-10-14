# -*- coding: utf-8 -*-
"""step2_split.py — V5 步骤② 子图分割（白线脚本）
读取 datamining/01_work_order.json，对「多面板且含 needs_xy_extraction=true 子图」的图分割
（子图级判定；单面板 xy 图不分割，直接提取），
写 datamining/split/<图号>/ 与 datamining/02_image_match.json（分割数校验）。
分割失败（数量不符/缺原图）的图会在汇总中列出 → 转智能体裁切或 PDF 补提后重跑本步。
用法：python step2_split.py <批次根目录>
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from image_splitter import split_figure  # noqa: E402


def wf_dir(paper_dir: Path) -> Path:
    z = paper_dir / "datamining"
    return z if z.is_dir() else paper_dir


def find_image(wf: Path, figure_id: str):
    img_dir = wf / "images"
    if not img_dir.is_dir():
        return None
    m = re.search(r"(?:Fig(?:ure)?\.?)\s*(\d+)", str(figure_id), re.IGNORECASE)
    norm = f"Figure{m.group(1)}" if m else str(figure_id).strip()
    for cand in {str(figure_id).strip(), norm}:
        d = img_dir / cand
        if d.is_dir():
            full = d / (cand + "_full.jpg")   # 新归档：多子图纵向拼接整图优先
            if full.exists():
                return full
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                files = [f for f in d.glob(ext) if "_full" not in f.name]
                if files:
                    return files[0]
    return None


def read_panel_list(wf: Path, figure_id: str):
    """读 images/FigureN/FigureN.txt 的「[-] <file> page=N」有序面板清单。

    【2026-10-05 修】归档阶段 MinerU 已把多面板图各自独立成图，
    此时 FigureN.txt 是权威且有序的面板清单（顺序 = 图注 a/b/c），
    无需再做白线切割。旧版无条件调 split_figure 去切「单张面板图」，
    必然 0 张 → 误报「分割失败」（B202 wen-et-al-2021 的 Fig.5/6/10 实测）。
    """
    img_dir = wf / "images"
    if not img_dir.is_dir():
        return []
    m = re.search(r"(?:Fig(?:ure)?\.?)\s*(\d+)", str(figure_id), re.IGNORECASE)
    if not m:
        return []
    d = img_dir / f"Figure{m.group(1)}"
    if not d.is_dir():
        return []
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".txt"):
            continue
        try:
            txt = (d / fn).read_text(encoding="utf-8")
        except Exception:
            continue
        if not txt.strip():
            continue
        # 【2026-10-05 修】兼容两种面板行格式：新 step0c 归档「  [1] <file> page=N bbox=…」
        # 与旧 MinerU 归档「  [-] <file>」。旧版只认 [-] 且要求正文含「子图」二字，
        # 对 [n] 格式必然读空 → 整图降级为单包、面板全部对不上（实测 Fig.7 4 块读成 0 块）。
        names = re.findall(r"^\s*\[[-\d]+\]\s+(\S+\.(?:jpg|jpeg|png))", txt, re.M)
        out = [d / n for n in names if (d / n).exists()]
        if out:
            return out
    return []


def xy_subs_of(task):
    """该图的 XY 子图清单（子图级判定，唯一真源 = subfigures[].needs_xy_extraction）。

    兼容旧工单：子图内一个该字段都没有时，回退读图级布尔（旧 schema 图级 true = 全部子图当 XY），
    只在完全没有子图级信息时生效，新工单不会走到这里。
    """
    subs = task.get("subfigures", [])
    if any("needs_xy_extraction" in s for s in subs):
        return [s for s in subs if s.get("needs_xy_extraction")]
    return list(subs) if task.get("needs_xy_extraction") else []


def split_paper(root: Path, paper: str, log=print):
    wf = wf_dir(root / paper)
    wo_path = wf / "01_work_order.json"
    if not wo_path.exists():
        return None
    data = json.loads(wo_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None  # 旧版 list 格式工单(如 C4EAI 残留)跳过
    out_root = wf / "02_split"
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(exist_ok=True)
    results = []
    for task in data.get("figures", []):
        subs = task.get("subfigures", [])
        # 【2026-10-05 改】XY 判定下沉到子图级：以 subfigures[].needs_xy_extraction 为准。
        # 多面板图里只要有 1 个 XY 子图就要分割（同一图可能混装 xy/basic 面板）。
        xy_subs = xy_subs_of(task)
        if not (xy_subs and len(subs) > 1):
            continue  # 单面板 xy 图直接提取，无需分割
        fid = task["figure_id"]
        expected = task.get("total_subfigures") or len(subs)
        # 归档已分幅（FigureN.txt 有序面板清单）→ 直接采纳，不做白线切割。
        # 判据：面板数 > 1 且 ≥ 工单收录子图数（多出的面板属 excluded 子图，按字母取用时不受影响）。
        panels = read_panel_list(wf, fid)
        if len(panels) > 1 and len(panels) >= len(subs):
            expected = len(panels)
            out_dir = out_root / fid
            out_dir.mkdir(exist_ok=True)
            saved = []
            for i, p in enumerate(panels):
                dst = out_dir / f"{fid}{chr(ord('A') + i)}.jpg"
                shutil.copyfile(p, dst)
                saved.append(dst.name)
            results.append({"figure_id": fid, "img_path": "", "split_dir": str(out_dir),
                            "subfigures": saved, "expected_total": expected,
                            "split_count": len(saved), "split_success": True,
                            "source": "archive_panel",
                            "xy_subfigure_ids": [s["subfigure_id"] for s in xy_subs]})
            log(f"✓ {paper[:30]} {fid}: 归档已分幅 {len(saved)}/{expected}（直接采纳）")
            continue
        img = find_image(wf, fid)
        if img is None:
            results.append({"figure_id": fid, "error": "找不到原图",
                            "expected_total": expected, "split_success": False})
            log(f"✗ {paper[:30]} {fid}: 找不到原图（转 PDF 补提）")
            continue
        out_dir = out_root / fid
        saved = split_figure(str(img), str(out_dir), fid)
        ok = len(saved) == expected
        results.append({"figure_id": fid, "img_path": str(img), "split_dir": str(out_dir),
                        "subfigures": saved, "expected_total": expected,
                        "split_count": len(saved), "split_success": ok,
                        "xy_subfigure_ids": [s["subfigure_id"] for s in xy_subs]})
        log(f"{'✓' if ok else '✗'} {paper[:30]} {fid}: {len(saved)}/{expected}")
    (wf / "02_image_match.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


def main(root):
    root = Path(root)
    ok = bad = 0
    for paper in sorted(os.listdir(root)):
        if not (root / paper).is_dir():
            continue
        r = split_paper(root, paper, log=print) or []
        for it in r:
            ok += 1 if it.get("split_success") else 0
            bad += 0 if it.get("split_success") else 1
    print(f"=== 脚本分割 {ok} 张成功，{bad} 张待处理（智能体裁切/PDF补提后重跑） ===")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test10b")
