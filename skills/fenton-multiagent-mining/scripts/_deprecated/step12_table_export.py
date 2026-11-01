# -*- coding: utf-8 -*-
"""step12_table_export.py — 步骤十③ 表格导出（图谱同源，保三元组结构）

把 graph_export/ 的节点 JSON 转成多 sheet Excel：
  论文 / 催化剂 / 实验 / 变量水平 / 曲线 / 矛盾。
 【2026-10-09 v2】按数据本来的混合结构出表：
  · 【数】字段 → 三列：{字段}·值（纯数字）/ {字段}·单位 / {字段}·原文（raw 保真，不合并）；
  · 【文】字段 → 单列文本；
  · 曲线除独立目录表外，**内嵌回实验行与变量水平行**（图例/单位/点数/点位），一一对应可见。
【2026-10-10 v3 修「有值无列」】：
  · 六张表**统一动态兜底列**（旧版只有变量表有）→ 白名单外的键（other_*/qc_*/catalyst_name/
    review_status/review_scope）不再静默丢列；伴随键 _unit/_raw/_range 视为已承载不重复出列；
  · 矛盾表补 review_status / review_scope 列；实验表补 catalyst_name 列；点位 cap 40→200；
  · load() 缺文件/解析失败**显式告警**（旧版静默 [] → 整表空且无人知）；
  · 出表后写 columns_audit.json（各表行数 / 兜底列清单），兜底列非空即提示 schema 契约已演进。
用法:
  python scripts/step12_table_export.py "D:\\fenton_papers"
  python scripts/step12_table_export.py "D:\\fenton_papers\\graph_export"
输出: <graph_export>/tables.xlsx
"""
import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

PAPER_COLS = [
    ('dir_name', 'Paper folder'), ('batch', 'Batch'), ('title', 'Title'), ('journal', 'Journal'),
    ('doi', 'DOI'), ('publication_year', 'Year'), ('authors', 'Authors'),
    ('contradiction_count', 'Contradictions'), ('contradiction_severity', 'Severity summary'),
]
CATALYST_COLS = [
    ('chemical_formula', 'Chemical formula'), ('metal_elements', 'Metal elements'), ('metal_type', 'Metal type'),
    ('metal_interaction', 'Metal interaction'), ('metal_content', 'Metal content'),
    ('support_material', 'Support'), ('catalyst_form', 'Catalyst form'), ('morphology', 'Morphology'),
    ('synthesis_method', 'Synthesis method'), ('precursors', 'Precursors'),
    ('calcination_temp', 'Calcination temperature'), ('calcination_time', 'Calcination time'),
    ('calcination_heating_rate', 'Heating rate'), ('calcination_atmosphere', 'Calcination atmosphere'),
    ('post_treatment', 'Post-treatment'), ('crystal_phase', 'Crystal phase'), ('crystal_system', 'Crystal system'),
    ('space_group', 'Space group'), ('lattice_a', 'Lattice constant a'), ('lattice_c', 'Lattice constant c'),
    ('crystallite_size', 'Crystallite size'), ('lattice_spacing_nm', 'd-spacing (nm)'),
    ('particle_size', 'Particle size'), ('impurity_phase', 'Impurity phase'),
    ('specific_surface_area', 'Specific surface area'), ('pore_volume', 'Pore volume'), ('pore_size', 'Pore size'),
    ('adsorption_capacity', 'Adsorption capacity'), ('pH_pzc', 'pHpzc'), ('zeta_potential', 'Zeta potential'),
    ('oxygen_vacancy', 'Oxygen vacancy'), ('surface_functional_groups', 'Surface functional groups'),
    ('element_oxidation_states', 'Element oxidation states'), ('raman_ID_IG', 'Raman ID/IG'),
    ('band_gap', 'Band gap'), ('redox_activity', 'Redox activity'),
    ('valence_ratio_description', 'Valence ratio description'), ('eis_Rct', 'EIS Rct'),
    ('saturation_magnetization', 'Saturation magnetization'), ('char_PL_intensity', 'PL intensity'),
    ('characterization_methods', 'Characterization methods'), ('purity', 'Purity'), ('supplier', 'Supplier'),
]
EXPERIMENT_COLS = [
    ('pollutant_class', 'Pollutant class'), ('chemical_formula', 'Pollutant formula'),
    ('oxidant_type', 'Oxidant type'), ('oxidant_concentration', 'Oxidant concentration'),
    ('oxidant_dosage', 'Oxidant dosage'), ('initial_pollutant_conc', 'Initial pollutant concentration'),
    ('catalyst_dosage', 'Catalyst dosage'), ('solution_volume', 'Solution volume'),
    ('initial_pH', 'Initial pH'), ('temperature', 'Temperature'), ('reaction_time', 'Reaction time'),
    ('coexisting_substances', 'Coexisting substances'), ('light_condition', 'Light condition'),
    ('reaction_system', 'Reaction system'), ('process_mode', 'Process mode'), ('agitation', 'Agitation'),
    ('medium', 'Medium'), ('replicates', 'Replicates'),
    ('oxidant_pollutant_molar_ratio', 'Oxidant/pollutant molar ratio'),
    ('catalyst_pollutant_mass_ratio', 'Catalyst/pollutant mass ratio'), ('other_condition', 'Other conditions'),
    ('removal_efficiency', 'Removal efficiency'), ('decolorization_efficiency', 'Decolorization efficiency'),
    ('rate_constant_k', 'Rate constant k'), ('rate_constant_R2', 'R²'),
    ('toc_removal', 'TOC removal'), ('cod_removal', 'COD removal'),
    ('adsorption_contribution', 'Adsorption contribution'), ('degradation_products', 'Degradation products'),
    ('dominant_ROS', 'Dominant ROS'), ('ros_detection_method', 'ROS detection method'),
    ('epr_signal_desc', 'EPR signal'), ('xps_after_reaction', 'XPS after reaction'),
    ('degradation_pathway', 'Degradation pathway'), ('toxicity_change', 'Toxicity change'),
    ('pms_alone_control', 'Oxidant-only control'), ('h2o2_generation_consumption', 'H2O2 generation/consumption'),
    ('chloride_release', 'Chloride release'), ('catalytic_activity', 'Catalytic activity'),
    ('stability_cycle_count', 'Cycle count'), ('stability_initial_efficiency', 'Initial-cycle efficiency'),
    ('stability_final_efficiency', 'Final-cycle efficiency'), ('stability_efficiency_retention', 'Efficiency retention'),
    ('stability_metal_leaching_conc', 'Metal leaching'), ('stability_structure_after_rxn', 'Structure after reaction'),
]
LEVEL_COLS = [
    ('variable_parameter', 'Variable'), ('level_value', 'Level value'), ('level_index', 'Level index'),
    ('metric_name', 'Metric'), ('metric_value', 'Metric value'), ('metric_source', 'Metric source'),
    ('source_figure', 'Source figure'), ('fixed_conditions', 'Fixed baseline'),
]
CURVE_COLS = [
    ('metric', 'Metric'), ('curve_label', 'Curve label'), ('source_figure', 'Source figure'),
    ('x_unit', 'x unit'), ('y_unit', 'y unit'), ('n_points', 'Point count'),
]
CONTRA_COLS = [
    ('field', 'Field'), ('figure', 'Figure'), ('severity', 'Severity'), ('severity_label', 'Severity label'),
    ('evidence_source', 'Evidence source'), ('text_value', 'Text value'), ('image_value', 'Figure value'),
    ('adopted_value', 'Adopted value'), ('reason', 'Reason'), ('confidence', 'Confidence'),
    ('cross_paper', 'Cross-paper'), ('caliber_variance', 'Definition gap'),
    ('needs_human_review', 'Needs human review'),
    ('review_status', 'Review status'), ('review_scope', 'Review scope'),   # 【2026-10-10】补列（旧版有值无列）
    ('trace', 'Trace'),
]

# 【2026-10-10】结构键/生成物键：不进兜底列（node_id/paper_id 等已由首列承载；search_text 是向量召回用的生成物）
SKIP_EXTRA = {'node_id', 'paper_id', 'experiment_id', 'variable_id', 'catalyst_id',
              'search_text', 'qc', 'label', 'embedding', 'embedding_model'}
EXTRA_SUFFIX = {'Variable levels': '(var)', 'Experiments': '(other)', 'Catalysts': '(other)',
                'Papers': '(other)', 'Curves': '(other)', 'Contradictions': '(other)'}


def load(p):
    """读 JSON；缺文件/解析失败**显式告警**（旧版静默返回 [] → 整张表空且无人知）。"""
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except FileNotFoundError:
        print(f'[!] Missing file: {p}')
        return None
    except Exception as ex:
        print(f'[!] Parse failed: {p} :: {ex}')
        return None


def cell(v):
    """单元格值净化：list/dict → 字符串；数字保持数字。"""
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    if isinstance(v, float) and v == int(v):
        return int(v)
    if isinstance(v, (list, tuple)):
        return ', '.join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return v


def emit_cols(nodes, cols):
    """每个 curated 字段 → 三元组三列（值/单位/原文）或单列（纯文本）。"""
    out = []
    for key, head in cols:
        triple = any((n.get(key + '_raw') not in (None, '')) or (n.get(key + '_unit') not in (None, ''))
                     for n in nodes)
        if triple:
            out.append((key, head, 'value'))
            out.append((key, head + ' (unit)', 'unit'))
            out.append((key, head + ' (raw)', 'raw'))
        else:
            out.append((key, head, 'plain'))
    return out


SUFFIXES = ('_unit', '_raw', '_range', '_qualifier', '_uncertainty', '_converted_from', '_unparsed')


def extra_scalar_keys(nodes, used):
    """表内**未映射**的标量键 —— 兜底列来源。

    【2026-10-10 修 / P1】旧版只有「变量水平」表有动态兜底列，论文/催化剂/实验/曲线/矛盾
    四张表遇到白名单外的键（other_* / qc_* / catalyst_name / review_* …）就静默丢列。
    现统一：所有表都补兜底列，白名单过期不再无声丢数据。
    三元组的伴随键（_unit/_raw/_range/…）若其主字段已在白名单，视为**已承载**，不重复出列。
    """
    ks = []
    for n in nodes:
        for k, v in n.items():
            if k in used or k in SKIP_EXTRA or k in ks:
                continue
            if v is None or isinstance(v, (list, dict)):
                continue
            if k.endswith('_json') or k.endswith('_n'):     # 嵌套数组序列化副本，源头另有列
                continue
            base = k
            for s in SUFFIXES:
                if k.endswith(s):
                    base = k[: -len(s)]
                    break
            if base in used:
                continue
            ks.append(k)
    return ks


def add_extra_cols(nodes, rows, used, suffix=''):
    """给整表补兜底列，返回补进去的键清单（写入 columns_audit.json）。"""
    ks = extra_scalar_keys(nodes, used)
    for k in ks:
        for n, r in zip(nodes, rows):
            r[f'{k}{suffix}'] = cell(n.get(k))
    return ks


def col_value(n, key, kind):
    if kind == 'value':
        v = n.get(key)
        if v is None:
            rng = n.get(key + '_range')          # 区间字段：value 为 null 时出 a-b
            if isinstance(rng, list) and len(rng) == 2:
                try:
                    return f'{float(rng[0]):g}-{float(rng[1]):g}'
                except (TypeError, ValueError):
                    return str(rng)
        if isinstance(v, float) and v == int(v):
            v = int(v)
        qua = n.get(key + '_qualifier')          # >/</≈ 限定符、± 不确定度不丢
        unc = n.get(key + '_uncertainty')
        if qua not in (None, ''):
            v = f'{qua}{v}' if v is not None else str(qua)
        if unc not in (None, ''):
            v = f'{v}±{unc}' if v is not None else f'±{unc}'
        return cell(v)
    if kind == 'unit':
        return cell(n.get(key + '_unit'))
    if kind == 'raw':
        return cell(n.get(key + '_raw'))
    return cell(n.get(key))


def fmt_points(pts, cap=200):
    """点位数组 → 紧凑串（x,y 分号分隔；>cap 才截断并标注总点数）。cap 由 40 提到 200，防长曲线丢点。"""
    if not isinstance(pts, list) or not pts:
        return None
    out = []
    for p in pts[:cap]:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                out.append(f'{float(p[0]):g},{float(p[1]):g}')
            except (TypeError, ValueError):
                out.append(f'{p[0]},{p[1]}')
        else:
            out.append(str(p))
    s = ';'.join(out)
    return s + (f'...({len(pts)} points)' if len(pts) > cap else '')


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    outd = root if (root / 'manifest.json').exists() else root / 'graph_export'
    if not (outd / 'manifest.json').exists():
        print(f'[!] Export directory not found: {outd} (run step10_graph_export.py first)')
        return 1
    papers = load(outd / 'nodes_paper.json') or []
    cats = load(outd / 'nodes_catalyst.json') or []
    exps = load(outd / 'nodes_experiment.json') or []
    vars_ = load(outd / 'nodes_variable.json') or []
    curves = load(outd / 'nodes_curve.json') or []
    cons = load(outd / 'nodes_contradiction.json') or []
    if not papers:
        print(f'[!] nodes_paper.json is empty or missing; export aborted (directory: {outd})')
        return 1
    pname = {p.get('paper_id'): (p.get('dir_name') or p.get('title') or p.get('paper_id'))
             for p in papers}

    # 曲线按归属分组：水平级（variable_id）与实验级（无 variable_id）
    lvl_cur, exp_cur = {}, {}
    for q in curves:
        if q.get('variable_id'):
            lvl_cur.setdefault(q['variable_id'], []).append(q)
        elif q.get('experiment_id'):
            exp_cur.setdefault(q['experiment_id'], []).append(q)

    def curve_cells(qlist):
        if not qlist:
            return {}
        labels = ' || '.join(str(q.get('curve_label')) for q in qlist if q.get('curve_label'))
        units = sorted({f"{q.get('x_unit') or ''}/{q.get('y_unit') or ''}" for q in qlist})
        npts = sum(q.get('n_points') or 0 for q in qlist)
        pts = ' || '.join(filter(None, (fmt_points(q.get('xs')) for q in qlist)))
        return {'Curve label': labels or None, 'Curve units (x/y)': '/'.join(units) or None,
                'Curve point count': npts or None, 'Curve points': pts or None}

    rows, audit = {}, {}
    rows['Papers'] = [{'Paper': pname.get(p.get('paper_id'), '?'), 'Node ID': p.get('paper_id'),
                    **{h: cell(p.get(k)) for k, h in PAPER_COLS}} for p in papers]
    audit['Papers'] = {'nodes': len(papers), 'extra': add_extra_cols(
        papers, rows['Papers'], {'Paper', 'Node ID'} | {k for k, _ in PAPER_COLS}, EXTRA_SUFFIX['Papers'])}

    cat_cols = emit_cols(cats, CATALYST_COLS)
    rows['Catalysts'] = []
    for n in cats:
        r = {'Paper': pname.get(n.get('paper_id'), '?'), 'Node ID': n.get('node_id'),
             'Catalyst': cell(n.get('name')), 'Material family': cell(n.get('material_family')),
             'Loading %': cell(n.get('loading_pct'))}
        for key, head, kind in cat_cols:
            r[head] = col_value(n, key, kind)
        rows['Catalysts'].append(r)
    audit['Catalysts'] = {'nodes': len(cats), 'extra': add_extra_cols(
        cats, rows['Catalysts'],
        {'Paper', 'Node ID', 'name', 'material_family', 'loading_pct', 'catalyst_name'}
        | {k for k, _ in CATALYST_COLS}, EXTRA_SUFFIX['Catalysts'])}

    exp_cols = emit_cols(exps, EXPERIMENT_COLS)
    rows['Experiments'] = []
    for n in exps:
        r = {'Paper': pname.get(n.get('paper_id'), '?'), 'Node ID': n.get('node_id'),
             'Pollutant': cell(n.get('pollutant_name')), 'Owner catalyst': owner_name(pname, n.get('catalyst_id')),
             'Catalyst name': cell(n.get('catalyst_name')),           # 【2026-10-10】旧版有值无列
             'Kinetics model': cell(n.get('kinetics_model'))}
        for key, head, kind in exp_cols:
            r[head] = col_value(n, key, kind)
        r.update(curve_cells(exp_cur.get(n.get('node_id'))))
        rows['Experiments'].append(r)
    audit['Experiments'] = {'nodes': len(exps), 'extra': add_extra_cols(
        exps, rows['Experiments'],
        {'Paper', 'Node ID', 'pollutant_name', 'catalyst_id', 'catalyst_name', 'kinetics_model',
         'Curve label', 'Curve units (x/y)', 'Curve point count', 'Curve points'} | {k for k, _ in EXPERIMENT_COLS},
        EXTRA_SUFFIX['Experiments'])}

    lvl_cols = emit_cols(vars_, LEVEL_COLS)
    rows['Variable levels'] = []
    for n in vars_:
        r = {'Paper': pname.get(n.get('paper_id'), '?'), 'Node ID': n.get('node_id'),
             'Owner experiment': owner_name(pname, n.get('experiment_id'))}
        for key, head, kind in lvl_cols:
            r[head] = col_value(n, key, kind)
        r.update(curve_cells(lvl_cur.get(n.get('node_id'))))
        rows['Variable levels'].append(r)
    # 【防丢】变量/水平节点白名单外的标量键（new_value / note / other_* / 旧结构自定义键）动态补列
    audit['Variable levels'] = {'nodes': len(vars_), 'extra': add_extra_cols(
        vars_, rows['Variable levels'],
        {'Paper', 'Node ID', 'experiment_id', 'Curve label', 'Curve units (x/y)', 'Curve point count', 'Curve points'}
        | {k for k, _ in LEVEL_COLS}, EXTRA_SUFFIX['Variable levels'])}

    rows['Curves'] = [{'Paper': pname.get(q.get('paper_id'), '?'), 'Node ID': q.get('node_id'),
                    'Owner': owner_name(pname, q.get('variable_id') or q.get('experiment_id')),
                    **{h: cell(q.get(k)) for k, h in CURVE_COLS},
                    'needs_review': 'Yes' if q.get('needs_review') else None,
                    'Points': fmt_points(q.get('xs'))} for q in curves]
    audit['Curves'] = {'nodes': len(curves), 'extra': add_extra_cols(
        curves, rows['Curves'],
        {'Paper', 'Node ID', 'variable_id', 'experiment_id', 'xs', 'ys', 'n_points', 'needs_review',
         'Curve label', 'Curve units (x/y)', 'Curve point count', 'Curve points'} | {k for k, _ in CURVE_COLS},
        EXTRA_SUFFIX['Curves'])}

    rows['Contradictions'] = [{'Paper': pname.get(c.get('paper_id'), '?'), 'Node ID': c.get('node_id'),
                    **{h: cell(c.get(k)) for k, h in CONTRA_COLS}} for c in cons]
    audit['Contradictions'] = {'nodes': len(cons), 'extra': add_extra_cols(
        cons, rows['Contradictions'], {'Paper', 'Node ID'} | {k for k, _ in CONTRA_COLS}, EXTRA_SUFFIX['Contradictions'])}

    wb = Workbook()
    wb.remove(wb.active)
    for name in ('Papers', 'Catalysts', 'Experiments', 'Variable levels', 'Curves', 'Contradictions'):
        data = rows[name]
        ws = wb.create_sheet(name)
        headers = list(data[0].keys()) if data else []
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in data:
            ws.append([r.get(h) for h in headers])
        for i, h in enumerate(headers, 1):
            width = max([len(str(r.get(h) or '')) for r in data] + [len(h)]) + 2
            ws.column_dimensions[get_column_letter(i)].width = min(max(width * 1.4, 8), 55)
    out = outd / 'tables.xlsx'
    wb.save(out)
    # 【2026-10-10】列覆盖账：各表行数 / 兜底列（白名单外的键）—— 兜底列非空即提示契约已演进
    (outd / 'columns_audit.json').write_text(
        json.dumps(audit, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'tables.xlsx → {out}')
    for name in ('Papers', 'Catalysts', 'Experiments', 'Variable levels', 'Curves', 'Contradictions'):
        ncol = len(rows[name][0]) if rows[name] else 0
        ex = audit[name]['extra']
        tail = f"  [extra columns {len(ex)}: {', '.join(ex[:6])}{'...' if len(ex) > 6 else ''}]" if ex else ''
        print(f'  {name}: {len(rows[name])} rows x {ncol} cols{tail}')
    print(f'Column coverage report -> {outd / "columns_audit.json"}')
    return 0


def owner_name(pname, node_id):
    """归属列：catalyst_id/experiment_id/variable_id → 论文短名 + 节点尾段。"""
    if not node_id:
        return None
    short = pname.get(node_id.split('::')[0], '?')
    return f'{short} :: {node_id.split("::", 1)[1]}'


if __name__ == '__main__':
    sys.exit(main())
