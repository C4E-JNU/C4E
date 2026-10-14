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
    ('dir_name', '论文文件夹'), ('batch', '批次'), ('title', '标题'), ('journal', '期刊'),
    ('doi', 'DOI'), ('publication_year', '年份'), ('authors', '作者'),
    ('contradiction_count', '矛盾数'), ('contradiction_severity', '严重度摘要'),
]
CATALYST_COLS = [
    ('chemical_formula', '化学式'), ('metal_elements', '金属元素'), ('metal_type', '金属类型'),
    ('metal_interaction', '金属相互作用'), ('metal_content', '金属含量'),
    ('support_material', '载体'), ('catalyst_form', '催化剂形态'), ('morphology', '形貌'),
    ('synthesis_method', '合成方法'), ('precursors', '前驱体'),
    ('calcination_temp', '煅烧温度'), ('calcination_time', '煅烧时间'),
    ('calcination_heating_rate', '升温速率'), ('calcination_atmosphere', '煅烧气氛'),
    ('post_treatment', '后处理'), ('crystal_phase', '晶相'), ('crystal_system', '晶系'),
    ('space_group', '空间群'), ('lattice_a', '晶格常数a'), ('lattice_c', '晶格常数c'),
    ('crystallite_size', '晶粒尺寸'), ('lattice_spacing_nm', '晶面间距nm'),
    ('particle_size', '粒径'), ('impurity_phase', '杂相'),
    ('specific_surface_area', '比表面积'), ('pore_volume', '孔容'), ('pore_size', '孔径'),
    ('adsorption_capacity', '吸附容量'), ('pH_pzc', 'pHpzc'), ('zeta_potential', 'Zeta电位'),
    ('oxygen_vacancy', '氧空位'), ('surface_functional_groups', '表面官能团'),
    ('element_oxidation_states', '元素价态'), ('raman_ID_IG', 'Raman ID/IG'),
    ('band_gap', '带隙'), ('redox_activity', '氧化还原活性'),
    ('valence_ratio_description', '价态比描述'), ('eis_Rct', '阻抗Rct'),
    ('saturation_magnetization', '饱和磁化强度'), ('char_PL_intensity', 'PL强度'),
    ('characterization_methods', '表征方法'), ('purity', '纯度'), ('supplier', '供应商'),
]
EXPERIMENT_COLS = [
    ('pollutant_class', '污染物类别'), ('chemical_formula', '污染物分子式'),
    ('oxidant_type', '氧化剂类型'), ('oxidant_concentration', '氧化剂浓度'),
    ('oxidant_dosage', '氧化剂投加量'), ('initial_pollutant_conc', '初始污染物浓度'),
    ('catalyst_dosage', '催化剂投加量'), ('solution_volume', '溶液体积'),
    ('initial_pH', '初始pH'), ('temperature', '温度'), ('reaction_time', '反应时间'),
    ('coexisting_substances', '共存物质'), ('light_condition', '光照条件'),
    ('reaction_system', '反应体系'), ('process_mode', '运行方式'), ('agitation', '搅拌'),
    ('medium', '介质'), ('replicates', '平行样'),
    ('oxidant_pollutant_molar_ratio', '氧化剂/污染物摩尔比'),
    ('catalyst_pollutant_mass_ratio', '催化剂/污染物质量比'), ('other_condition', '其他条件'),
    ('removal_efficiency', '去除率'), ('decolorization_efficiency', '脱色率'),
    ('rate_constant_k', '速率常数k'), ('rate_constant_R2', 'R²'),
    ('toc_removal', 'TOC去除'), ('cod_removal', 'COD去除'),
    ('adsorption_contribution', '吸附贡献'), ('degradation_products', '降解产物'),
    ('dominant_ROS', '主导活性物种'), ('ros_detection_method', 'ROS检测方法'),
    ('epr_signal_desc', 'EPR信号'), ('xps_after_reaction', '反应后XPS'),
    ('degradation_pathway', '降解途径'), ('toxicity_change', '毒性变化'),
    ('pms_alone_control', '氧化剂单独对照'), ('h2o2_generation_consumption', 'H2O2生成/消耗'),
    ('chloride_release', '氯释放'), ('catalytic_activity', '催化活性'),
    ('stability_cycle_count', '循环次数'), ('stability_initial_efficiency', '首次循环效率'),
    ('stability_final_efficiency', '末次循环效率'), ('stability_efficiency_retention', '循环保持率'),
    ('stability_metal_leaching_conc', '金属浸出'), ('stability_structure_after_rxn', '反应后结构'),
]
LEVEL_COLS = [
    ('variable_parameter', '扫描参数'), ('level_value', '水平值'), ('level_index', '水平序'),
    ('metric_name', '指标名'), ('metric_value', '指标值'), ('metric_source', '指标来源'),
    ('source_figure', '来源图'), ('fixed_conditions', '固定基准'),
]
CURVE_COLS = [
    ('metric', '指标'), ('curve_label', '曲线图例'), ('source_figure', '来源图'),
    ('x_unit', 'x单位'), ('y_unit', 'y单位'), ('n_points', '点数'),
]
CONTRA_COLS = [
    ('field', '字段'), ('figure', '图号'), ('severity', '严重度'), ('severity_label', '严重度全称'),
    ('evidence_source', '载体来源'), ('text_value', '正文值'), ('image_value', '图值'),
    ('adopted_value', '采纳值'), ('reason', '裁决理由'), ('confidence', '置信度'),
    ('cross_paper', '跨文献'), ('caliber_variance', '口径差'),
    ('needs_human_review', '待人工复核'),
    ('review_status', '复核状态'), ('review_scope', '复核范围'),   # 【2026-10-10】补列（旧版有值无列）
    ('trace', '溯源'),
]

# 【2026-10-10】结构键/生成物键：不进兜底列（node_id/paper_id 等已由首列承载；search_text 是向量召回用的生成物）
SKIP_EXTRA = {'node_id', 'paper_id', 'experiment_id', 'variable_id', 'catalyst_id',
              'search_text', 'qc', 'label', 'embedding', 'embedding_model'}
EXTRA_SUFFIX = {'变量水平': '(var)', '实验': '(其他)', '催化剂': '(其他)',
                '论文': '(其他)', '曲线': '(其他)', '矛盾': '(其他)'}


def load(p):
    """读 JSON；缺文件/解析失败**显式告警**（旧版静默返回 [] → 整张表空且无人知）。"""
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except FileNotFoundError:
        print(f'[!] 缺文件：{p}')
        return None
    except Exception as ex:
        print(f'[!] 解析失败：{p} :: {ex}')
        return None


def cell(v):
    """单元格值净化：list/dict → 字符串；数字保持数字。"""
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return '是' if v else '否'
    if isinstance(v, float) and v == int(v):
        return int(v)
    if isinstance(v, (list, tuple)):
        return '、'.join(str(x) for x in v)
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
            out.append((key, head + '·单位', 'unit'))
            out.append((key, head + '·原文', 'raw'))
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
    return s + (f'…({len(pts)}点)' if len(pts) > cap else '')


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    outd = root if (root / 'manifest.json').exists() else root / 'graph_export'
    if not (outd / 'manifest.json').exists():
        print(f'[!] 未找到导出目录：{outd}（先跑 step10_graph_export.py）')
        return 1
    papers = load(outd / 'nodes_paper.json') or []
    cats = load(outd / 'nodes_catalyst.json') or []
    exps = load(outd / 'nodes_experiment.json') or []
    vars_ = load(outd / 'nodes_variable.json') or []
    curves = load(outd / 'nodes_curve.json') or []
    cons = load(outd / 'nodes_contradiction.json') or []
    if not papers:
        print(f'[!] nodes_paper.json 为空或缺失，导出中止（目录：{outd}）')
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
        return {'曲线图例': labels or None, '曲线单位(x/y)': '/'.join(units) or None,
                '曲线点数': npts or None, '曲线点位': pts or None}

    rows, audit = {}, {}
    rows['论文'] = [{'论文': pname.get(p.get('paper_id'), '?'), '节点ID': p.get('paper_id'),
                    **{h: cell(p.get(k)) for k, h in PAPER_COLS}} for p in papers]
    audit['论文'] = {'nodes': len(papers), 'extra': add_extra_cols(
        papers, rows['论文'], {'论文', '节点ID'} | {k for k, _ in PAPER_COLS}, EXTRA_SUFFIX['论文'])}

    cat_cols = emit_cols(cats, CATALYST_COLS)
    rows['催化剂'] = []
    for n in cats:
        r = {'论文': pname.get(n.get('paper_id'), '?'), '节点ID': n.get('node_id'),
             '催化剂': cell(n.get('name')), '材料族': cell(n.get('material_family')),
             '负载%': cell(n.get('loading_pct'))}
        for key, head, kind in cat_cols:
            r[head] = col_value(n, key, kind)
        rows['催化剂'].append(r)
    audit['催化剂'] = {'nodes': len(cats), 'extra': add_extra_cols(
        cats, rows['催化剂'],
        {'论文', '节点ID', 'name', 'material_family', 'loading_pct', 'catalyst_name'}
        | {k for k, _ in CATALYST_COLS}, EXTRA_SUFFIX['催化剂'])}

    exp_cols = emit_cols(exps, EXPERIMENT_COLS)
    rows['实验'] = []
    for n in exps:
        r = {'论文': pname.get(n.get('paper_id'), '?'), '节点ID': n.get('node_id'),
             '污染物': cell(n.get('pollutant_name')), '归属催化剂': owner_name(pname, n.get('catalyst_id')),
             '催化剂名': cell(n.get('catalyst_name')),           # 【2026-10-10】旧版有值无列
             '动力学模型': cell(n.get('kinetics_model'))}
        for key, head, kind in exp_cols:
            r[head] = col_value(n, key, kind)
        r.update(curve_cells(exp_cur.get(n.get('node_id'))))
        rows['实验'].append(r)
    audit['实验'] = {'nodes': len(exps), 'extra': add_extra_cols(
        exps, rows['实验'],
        {'论文', '节点ID', 'pollutant_name', 'catalyst_id', 'catalyst_name', 'kinetics_model',
         '曲线图例', '曲线单位(x/y)', '曲线点数', '曲线点位'} | {k for k, _ in EXPERIMENT_COLS},
        EXTRA_SUFFIX['实验'])}

    lvl_cols = emit_cols(vars_, LEVEL_COLS)
    rows['变量水平'] = []
    for n in vars_:
        r = {'论文': pname.get(n.get('paper_id'), '?'), '节点ID': n.get('node_id'),
             '归属实验': owner_name(pname, n.get('experiment_id'))}
        for key, head, kind in lvl_cols:
            r[head] = col_value(n, key, kind)
        r.update(curve_cells(lvl_cur.get(n.get('node_id'))))
        rows['变量水平'].append(r)
    # 【防丢】变量/水平节点白名单外的标量键（new_value / note / other_* / 旧结构自定义键）动态补列
    audit['变量水平'] = {'nodes': len(vars_), 'extra': add_extra_cols(
        vars_, rows['变量水平'],
        {'论文', '节点ID', 'experiment_id', '曲线图例', '曲线单位(x/y)', '曲线点数', '曲线点位'}
        | {k for k, _ in LEVEL_COLS}, EXTRA_SUFFIX['变量水平'])}

    rows['曲线'] = [{'论文': pname.get(q.get('paper_id'), '?'), '节点ID': q.get('node_id'),
                    '归属': owner_name(pname, q.get('variable_id') or q.get('experiment_id')),
                    **{h: cell(q.get(k)) for k, h in CURVE_COLS},
                    'needs_review': '是' if q.get('needs_review') else None,
                    '点位': fmt_points(q.get('xs'))} for q in curves]
    audit['曲线'] = {'nodes': len(curves), 'extra': add_extra_cols(
        curves, rows['曲线'],
        {'论文', '节点ID', 'variable_id', 'experiment_id', 'xs', 'ys', 'n_points', 'needs_review',
         '曲线图例', '曲线单位(x/y)', '曲线点数', '曲线点位'} | {k for k, _ in CURVE_COLS},
        EXTRA_SUFFIX['曲线'])}

    rows['矛盾'] = [{'论文': pname.get(c.get('paper_id'), '?'), '节点ID': c.get('node_id'),
                    **{h: cell(c.get(k)) for k, h in CONTRA_COLS}} for c in cons]
    audit['矛盾'] = {'nodes': len(cons), 'extra': add_extra_cols(
        cons, rows['矛盾'], {'论文', '节点ID'} | {k for k, _ in CONTRA_COLS}, EXTRA_SUFFIX['矛盾'])}

    wb = Workbook()
    wb.remove(wb.active)
    for name in ('论文', '催化剂', '实验', '变量水平', '曲线', '矛盾'):
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
    for name in ('论文', '催化剂', '实验', '变量水平', '曲线', '矛盾'):
        ncol = len(rows[name][0]) if rows[name] else 0
        ex = audit[name]['extra']
        tail = f"  [兜底列 {len(ex)}: {', '.join(ex[:6])}{'…' if len(ex) > 6 else ''}]" if ex else ''
        print(f'  {name}: {len(rows[name])} 行 × {ncol} 列{tail}')
    print(f'列覆盖账 → {outd / "columns_audit.json"}')
    return 0


def owner_name(pname, node_id):
    """归属列：catalyst_id/experiment_id/variable_id → 论文短名 + 节点尾段。"""
    if not node_id:
        return None
    short = pname.get(node_id.split('::')[0], '?')
    return f'{short} :: {node_id.split("::", 1)[1]}'


if __name__ == '__main__':
    sys.exit(main())
