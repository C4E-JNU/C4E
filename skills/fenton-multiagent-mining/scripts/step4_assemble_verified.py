# -*- coding: utf-8 -*-
"""step4_assemble_verified.py — 步骤五收口：用子智能体的标注表把 03 组装成 04（全量副本）

为什么需要它：`04_verified_image_data.json` 是 `03_image_data.json` 的**全量副本**（含全部
`curves[].points[]`），体积远超单次回喂上限 —— 子智能体回贴必被截断，只能回「标注表」。
本脚本按 `(figure, subfigure)` 把标注并回 03，其余字段原样保留，**曲线点位一字不动**。

输入：
  <论文夹或批次根>/datamining/03_image_data.json      图账（list）
  <论文夹或批次根>/datamining/04_annotation_table.json 标注表（list，子智能体回报后由主控落盘）
输出：
  <论文夹或批次根>/datamining/04_verified_image_data.json

用法：
  python step4_assemble_verified.py <论文夹或批次根>
说明：目录内含 `datamining/` 即按单篇处理，否则按其下子目录逐篇处理。
"""
import json
import os
import re
import sys


def papers_of(root):
    """单篇（root 自身是论文夹）或批次（root 下若干论文夹）自适应。"""
    root = os.path.abspath(root)
    if os.path.isdir(os.path.join(root, "datamining")) or os.path.exists(
            os.path.join(root, "00_full_cleaned.md")):
        return [root]
    out = []
    for p in sorted(os.listdir(root)):
        d = os.path.join(root, p)
        if not os.path.isdir(d) or p.startswith("_"):
            continue
        if os.path.isdir(os.path.join(d, "datamining")) or os.path.exists(
                os.path.join(d, "00_full_cleaned.md")):
            out.append(d)
    return out


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def key_of(fig, sub):
    """归一匹配键：'Fig.7'/'Figure7' → 'fig7'，子图小写去空格。"""
    f = re.sub(r"[^0-9a-z]", "", str(fig or "").lower())
    f = f.replace("figure", "fig")
    s = re.sub(r"\s+", "", str(sub or "").lower())
    return f, s


def load(fp, default=None):
    if not os.path.exists(fp):
        return default
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception as e:
        print(f"  ! 读失败 {os.path.basename(fp)}: {e}")
        return default


def assemble(paper):
    wf = wf_of(paper)
    src = load(os.path.join(wf, "03_image_data.json"))
    if src is None:
        print(f"  ! 缺 03_image_data.json，跳过")
        return None
    if isinstance(src, dict):                       # 兼容 {"records": [...]} 形态
        src = src.get("records") or src.get("items") or []
    ann_fp = os.path.join(wf, "04_annotation_table.json")
    ann = load(ann_fp, []) or []
    if isinstance(ann, dict):
        ann = ann.get("records") or ann.get("items") or ann.get("annotations") or []

    table = {}
    for a in ann:
        if not isinstance(a, dict):
            continue
        k = key_of(a.get("figure"), a.get("subfigure"))
        if not k[0]:
            continue
        table[k] = a

    out, used, missing = [], set(), []
    for rec in src:
        rec = json.loads(json.dumps(rec, ensure_ascii=False))   # 深拷贝，绝不改原对象
        k = key_of(rec.get("figure"), rec.get("subfigure"))
        a = table.get(k)
        if a is None:
            missing.append(f"{rec.get('figure')}/{rec.get('subfigure')}")
            rec["value_source"] = rec.get("value_source") or ""
            rec["checked"] = False
            rec["discrepancy"] = None
            rec["annotation"] = "missing"
        else:
            used.add(k)
            rec["value_source"] = a.get("value_source") or ""
            rec["checked"] = bool(a.get("checked", True))
            rec["discrepancy"] = a.get("discrepancy")
            rec["annotation"] = "matched"
        out.append(rec)

    extra = [f"{k[0]}/{k[1]}" for k in table if k not in used]
    out_fp = os.path.join(wf, "04_verified_image_data.json")
    json.dump(out, open(out_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 曲线点位完整性：04 必须与 03 逐点一致（这是「全量副本」的硬指标）
    def pts(records):
        return sum(len(c.get("points") or []) for r in records for c in (r.get("curves") or []))
    p3, p4 = pts(src), pts(out)
    flag = "OK" if p3 == p4 else "!! 点位丢失"
    print(f"  04 {len(out)}/{len(src)} 条  曲线点 {p4}/{p3} {flag}"
          + (f"  未标注 {len(missing)}: {','.join(missing[:5])}" if missing else "")
          + (f"  标注表多余 {len(extra)}: {','.join(extra[:5])}" if extra else ""))
    return {"records": len(out), "points": p4, "missing": missing, "extra": extra,
            "points_ok": p3 == p4}


def main(root):
    ps = papers_of(root)
    if not ps:
        print(f"未找到论文夹：{root}\n（目录下需有 datamining/ 或 00_full_cleaned.md）")
        return 1
    print(f"论文 {len(ps)} 篇 ← {root}")
    bad = 0
    for p in ps:
        print(f"- {os.path.basename(p)[:46]}")
        r = assemble(p)
        if r is None or not r["points_ok"]:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
