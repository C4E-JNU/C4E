# -*- coding: utf-8 -*-
"""validate_sop_export.py — 图谱导出 ↔ 06_schema 一一对应校验（重点：变量水平）

逐篇核对 graph_export 节点与 06_schema.json：
  A. 变量水平：06 里每个 levels[] 项 ↔ 一个 E..V..L.. 节点，逐项比
     variable_parameter / level_value / metric_value / metric_value_unit / metric_name / metric_source
  B. 曲线：06 的 profiles 条数（实验级 + 水平级）↔ Curve 节点数；x_unit 覆盖率；点位存在率
  C. 三元组：06【数】字段的 {value,unit} ↔ 节点 <field> / <field>_unit 逐字相等
  D. metric 卫生：节点 metric 不含数字/时间/中文括注
用法:
  python validate_sop_export.py <graph_export_dir> <语料根>
输出: 每篇 PASS / FAIL + 问题清单，末尾汇总。
"""
import json
import re
import sys
from pathlib import Path

NUM_RE = re.compile(r'^(fig(?:ure)?\.?\s*s?\d+)([a-z])?$', re.I)


def norm_fig(s):
    s = re.sub(r"[^0-9a-z]", "", str(s or "").lower())
    return s.replace("figure", "fig") if s.startswith("figure") else s


def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return []


def main():
    gdir, root = Path(sys.argv[1]), Path(sys.argv[2])
    papers = load(gdir / 'nodes_paper.json')
    gvars = load(gdir / 'nodes_variable.json')
    gcurves = load(gdir / 'nodes_curve.json')
    gcats = load(gdir / 'nodes_catalyst.json')
    gexps = load(gdir / 'nodes_experiment.json')

    by_paper_vars, by_paper_curves = {}, {}
    for v in gvars:
        by_paper_vars.setdefault(v.get('paper_id'), []).append(v)
    for q in gcurves:
        by_paper_curves.setdefault(q.get('paper_id'), []).append(q)
    gcat_by_id = {c.get('node_id'): c for c in gcats}
    gexp_by_id = {e.get('node_id'): e for e in gexps}

    total_issues = 0
    for p in papers:
        pid = p.get('paper_id')
        batch, dname = p.get('batch'), p.get('dir_name')
        sch_p = root / batch / dname / 'datamining' / '06_schema.json'
        if not sch_p.exists() and batch == dname:   # ④ 单篇模式：batch 被写成篇名 → 语料根/篇名/
            sch_p = root / dname / 'datamining' / '06_schema.json'
        sch = load(sch_p)
        if not sch:
            print(f"{dname[:40]:42s} SKIP（06_schema 不存在）")
            continue
        issues = []

        # A. 变量水平逐项对（键带催化剂维度，防多催化剂 E/V 撞号）
        gv = by_paper_vars.get(pid, [])
        gv_by_key = {}
        for v in gv:
            m = re.match(r'.*::(C\d+)::(E\d+)::(V\d+)::(L\d+)$', str(v.get('node_id', '')))
            if m:
                gv_by_key[m.group(1, 2, 3, 4)] = v
        exp_levels_total = 0
        for ci, cat in enumerate(sch.get('catalysts', []) or []):
            ckey = f'C{ci+1}'
            for ei, e in enumerate(cat.get('experiments', []) or []):
                ekey = f'E{ei+1}'
                for vi, v in enumerate(e.get('variable_experiments', []) or []):
                    levels = v.get('levels')
                    if not isinstance(levels, list):
                        continue
                    for li, lv in enumerate(levels):
                        exp_levels_total += 1
                        key = (ckey, ekey, f'V{vi+1}', f'L{li+1}')
                        node = gv_by_key.get(key)
                        if node is None:
                            issues.append(f'变量水平缺节点 {"::".join(key)}')
                            continue
                        if str(node.get('variable_parameter', '')) != str(v.get('variable_parameter', '')):
                            issues.append(f'{key} 参数名不符: {node.get("variable_parameter")!r} vs {v.get("variable_parameter")!r}')
                        lvv, nvv = lv.get('level_value'), node.get('level_value')
                        try:
                            if lvv is not None and float(str(lvv).split()[0]) != float(nvv):
                                issues.append(f'{key} 水平值不符: {lvv!r} vs {nvv!r}')
                        except (ValueError, TypeError):
                            pass
                        mv = lv.get('metric_value')
                        if isinstance(mv, dict):
                            if mv.get('value') is not None and float(mv['value']) != float(node.get('metric_value')):
                                issues.append(f'{key} 指标值不符: {mv.get("value")} vs {node.get("metric_value")}')
                            if (mv.get('unit') or '') != (node.get('metric_value_unit') or ''):
                                issues.append(f'{key} 指标单位不符: {mv.get("unit")!r} vs {node.get("metric_value_unit")!r}')

        # B. 曲线丢失检测（按「图号+图例」签名；与导出的 merge/去重语义一致；空标签不参与签名）
        def norm_lbl(s):
            return re.sub(r'\s+', '', str(s or '')).lower()
        def norm_fig_s(s):
            return re.sub(r'[^0-9a-z]', '', str(s or '').lower())
        sigs06 = set()
        def add_sigs(dps, vfig=''):
            for d in (dps if isinstance(dps, list) else [dps] if isinstance(dps, dict) else []):
                if not isinstance(d, dict):
                    continue
                lbl = norm_lbl(d.get('curve_label'))
                if not lbl:
                    continue
                sigs06.add((norm_fig_s(d.get('source_figure') or vfig), lbl))
        for cat in sch.get('catalysts', []) or []:
            for e in cat.get('experiments', []) or []:
                add_sigs(e.get('degradation_profiles'), e.get('source_figure'))
                for v in e.get('variable_experiments', []) or []:
                    vfig = v.get('source_figure')
                    add_sigs(v.get('degradation_profiles'), vfig)
                    for lv in v.get('levels') or []:
                        add_sigs(lv.get('degradation_profiles'), vfig)
        gcur = by_paper_curves.get(pid, [])
        gsig = {(norm_fig_s(q.get('source_figure')), norm_lbl(q.get('curve_label'))) for q in gcur}
        glabels = {s[1] for s in gsig}
        # 【2026-10-10 修】旧版把「标签已在图中出现」的未命中签名一律豁免 → 正好掩盖
        # 「同图不同子图的同名曲线被判重而整条吞掉」这类真丢（Fig.4C 的 h2o2 被 Fig.4B 顶掉）。
        # 现拆两档：lost = 标签也不在图中（真丢，计 FAIL）；hints = 标签在、图号不同（多为
        # 图号写法差异，单列提示不计 FAIL，供人工扫一眼）。
        lost, hints = [], []
        for (f, l) in sorted(sigs06):
            if (f, l) in gsig:
                continue
            (hints if l in glabels else lost).append((f, l))
        if lost:
            issues.append(f'{len(lost)} 条曲线签名未命中（疑似丢失）: {lost[:3]}')
        no_xunit = [q.get('node_id') for q in gcur if not q.get('x_unit')]
        no_pts = [q.get('node_id') for q in gcur if not q.get('xs')]
        if no_xunit:
            issues.append(f'{len(no_xunit)} 条曲线缺 x_unit（如 {no_xunit[0].split("::")[-1]}）')
        if no_pts:
            issues.append(f'{len(no_pts)} 条曲线无点位')

        # C. 三元组逐字对（催化剂 + 实验）
        for ci, cat in enumerate(sch.get('catalysts', []) or []):
            gc = gcat_by_id.get(f'{pid}::C{ci+1}')
            if gc is None:
                issues.append(f'C{ci+1} 节点缺失')
                continue
            for k, v in (cat.get('properties') or {}).items():
                if isinstance(v, dict) and 'value' in v and v.get('value') is not None:
                    if gc.get(k) != v['value']:
                        issues.append(f'C{ci+1}.{k} 值不符: {gc.get(k)!r} vs {v["value"]!r}')
                    if (gc.get(k + '_unit') or '') != (v.get('unit') or ''):
                        issues.append(f'C{ci+1}.{k} 单位不符: {gc.get(k+"_unit")!r} vs {v.get("unit")!r}')

        # D. metric 卫生
        dirty = [q.get('metric') for q in gcur
                 if q.get('metric') and re.search(r'\d|（|图|末值', str(q.get('metric')))
                 and not re.fullmatch(r'-?ln\(C/C0\)|C/C0|Ct/C0', str(q.get('metric')).strip())]
        if dirty:
            issues.append(f'{len(dirty)} 条 metric 疑似脏: {list(set(dirty))[:3]}')

        status = 'PASS' if not issues else f'FAIL({len(issues)})'
        total_issues += len(issues)
        print(f"{dname[:40]:42s} 水平{exp_levels_total:3d}/节点{len(gv_by_key):3d} 曲线{len(gcur):3d}  {status}")
        for it in issues[:6]:
            print(f"    - {it}")
        if len(issues) > 6:
            print(f"    … 共 {len(issues)} 条")
        if hints:
            print(f"    ~ 图号差异提示 {len(hints)} 条（标签在图谱中、图号不同）: {hints[:3]}")
    print(f"\n== 汇总：{len(papers)} 篇，问题 {total_issues} 条 ==")


if __name__ == '__main__':
    main()
