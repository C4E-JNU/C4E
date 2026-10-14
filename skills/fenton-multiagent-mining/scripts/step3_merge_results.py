# -*- coding: utf-8 -*-
"""step3_merge_results.py — V5 步骤③收尾（增强版）：
1. 汇总各任务包 03_result.json → 03_image_data.json
2. 结构规范化：dict→list、figure/mode 从 task.json 回填、None 清洗
3. 包数校验：02b_extraction_tasks 的 ready 包数 vs 实际 result 数，缺者列出供重派
4. 归档修正清单收集
用法：python step3_merge_results.py <批次根目录>
"""
import glob
import json
import os
import sys


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def normalize(records, task):
    """结构规范化：确保每条记录有 figure/subfigure/mode，类型正确"""
    if isinstance(records, dict):
        # dict 包裹的 records 列表
        records = records.get("records", [records])
    if not isinstance(records, list):
        records = [records]
    fig_id = task.get("figure_id", "")
    mode = task.get("mode", "")
    for r in records:
        if not isinstance(r, dict):
            continue
        # 回填 figure（缺失或 None 时）
        if not r.get("figure"):
            r["figure"] = fig_id
        # 回填 subfigure（缺失时用第一个子图 id）
        if not r.get("subfigure"):
            sids = task.get("subfigure_ids", ["main"])
            r["subfigure"] = sids[0] if sids else "main"
        # 修正 mode
        if not r.get("mode"):
            r["mode"] = mode
        # series→curves 容错（提取方偶发用 series 键承载曲线）
        if not r.get("curves") and isinstance(r.get("series"), list):
            r["curves"] = [
                {"label": c.get("label") or c.get("name", ""), "points": c.get("points", []),
                 **{k: v for k, v in c.items() if k not in ("label", "name", "points")}}
                for c in r["series"] if isinstance(c, dict)]
        # 剥离 None 值
        for k in list(r.keys()):
            if r[k] is None and k not in ("degradation_profiles",):
                del r[k]
    return records


def merge(paper):
    wf = wf_of(paper)
    idx_path = os.path.join(wf, "02b_extraction_tasks.json")
    if not os.path.exists(idx_path):
        return 0

    idx = json.load(open(idx_path, encoding="utf-8"))
    ready_pkgs = [t for t in idx.get("tasks", []) if t.get("status") == "ready"]

    # 1) 收集已有结果并规范化
    records, errors, got_tasks = [], [], set()
    for cat_dir in sorted(glob.glob(os.path.join(wf, "02b_tasks", "*"))):
        if not os.path.isdir(cat_dir):
            continue
        for rj in sorted(glob.glob(os.path.join(cat_dir, "T*", "0*result.json"))):
            tid = os.path.basename(os.path.dirname(rj))
            got_tasks.add(tid)
            try:
                data = json.load(open(rj, encoding="utf-8"))
                task = next((t for t in ready_pkgs if t["task_id"] == tid), {})
                recs = normalize(data, task)
                records.extend(recs)
            except Exception as e:
                errors.append(f"{os.path.relpath(rj, wf)}: {str(e)[:60]}")

    # 2) 校验：包数 vs 结果数，缺者列出
    missing = [t["task_id"] for t in ready_pkgs if t["task_id"] not in got_tasks]

    json.dump(records, open(os.path.join(wf, "03_image_data.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    n_xy = sum(1 for r in records if r.get("mode") == "xy")
    npts = sum(len(c.get("points", [])) for r in records if r.get("mode") == "xy"
               for c in r.get("curves", []))
    name = os.path.basename(os.path.normpath(paper))
    print(f"{name[:44]:46s} {len(records):3d} 条（xy {n_xy:2d} + basic {len(records)-n_xy:2d}）"
          f" 数据点 {npts:4d} 坏包 {len(errors)} 缺结果 {len(missing)}")
    for e in errors:
        print("  ", e)
    if missing:
        print(f"  ⚠ 缺结果包: {missing}（需重派）")
    return len(records)


def collect_archive_fixes(root, fixes):
    for rj in glob.glob(os.path.join(root, "*", "datamining", "02b_tasks", "*", "T*", "0*result.json"),
                        recursive=True):
        try:
            d = json.load(open(rj, encoding="utf-8"))
        except Exception:
            continue
        for r in (d if isinstance(d, list) else [d]):
            if isinstance(r, dict) and r.get("archive_fix"):
                fixes.append({"paper": os.path.basename(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.dirname(rj))))),
                    "task": os.path.basename(os.path.dirname(rj)),
                    "figure": r.get("figure"), "subfigure": r.get("subfigure"),
                    **r["archive_fix"]})


def main(root):
    total = 0
    fixes = []
    for p in sorted(os.listdir(root)):
        paper = os.path.join(root, p)
        if not os.path.isdir(paper):
            continue
        wf = wf_of(paper)
        if os.path.isdir(os.path.join(wf, "02b_tasks")):
            total += merge(paper)
    collect_archive_fixes(root, fixes)
    if fixes:
        # 2026-10-05 修正：写 <批次目录>/_ledgers/，不再写进技能目录
        _ld = os.path.join(os.path.normpath(root), "_ledgers")
        os.makedirs(_ld, exist_ok=True)
        fp = os.path.join(_ld, f"{os.path.basename(os.path.normpath(root))}_归档修正清单.json")
        json.dump(fixes, open(fp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"=== 归档修正清单 {len(fixes)} 条 ===")
    print(f"=== 全批合计 {total} 条记录 ===")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test10b")
