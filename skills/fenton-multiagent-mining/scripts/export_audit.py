# -*- coding: utf-8 -*-
"""export_audit.py — 步骤十「导出后产物覆盖自检」（源 06 ↔ 图谱节点 ↔ tables.xlsx ↔ 实库）

【为什么要它】成品终检(fenton-auditor)只保证「06_schema 文件自身字段合规」，
**不保证「跨表示映射无损」**。而 step10/12 的搬运层各含硬编码假设（去重签名、字段 merge、
列白名单），schema 契约一演进就静默过期 —— 此前没有任何一道门在拦。实测两次真丢
（Fig.4B/4C 同名曲线被判重整条吞掉、边属性从未写入、三表有值无列）都出在这条缝上。

本脚本做**差集**，任何未映射的键/行/曲线直接 FAIL：
  A 行数对账：每类节点数 ↔ 对应 sheet 数据行数（一一对应）
  B 列覆盖：节点**每个键**必须落到某列（白名单字段 → 其表头列；白名单外 → 兜底列 {key}(其他|var)）
  C 曲线三方（需 --root）：06 源曲线（按「图号含子图 + 图例」去重）↔ nodes_curve.json ↔ 曲线 sheet 行数
  D 点位对账：带 xs 的 Curve 数 ↔ 曲线 sheet 点位非空行数；n_points 总和一致
  E 实库（可选 --neo4j）：本次（全类 node_id ∪ paper_id）回查 —— 节点存在性 / Paper 数 /
    关系数 / 带属性边数（与 edges.json 期望值对账）/ 属性 key 数。
    边按**任一端**归属本次判定（另一端允许是共享 Entity / Figure 占位）——
    旧版要求两端都命中，导致连向 Entity 的 DEGRADES 边被整片漏统计（假阴性）。

用法：
  python export_audit.py <graph_export_dir> --root <语料根>
  python export_audit.py <graph_export_dir> --root <语料根> --neo4j
输出：
  <graph_export_dir>/export_audit_report.json + 控制台 PASS / FAIL 汇总
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

SHEET_OF = {'paper': 'Papers', 'catalyst': 'Catalysts', 'experiment': 'Experiments',
            'variable': 'Variable levels', 'curve': 'Curves', 'contradiction': 'Contradictions'}
# 首列已承载的键（不作为「未成列」计）
LEAD_KEYS = {
    'paper': {'paper_id', 'dir_name', 'batch', 'title'},
    'catalyst': {'node_id', 'paper_id', 'name', 'material_family', 'loading_pct', 'catalyst_name'},
    'experiment': {'node_id', 'paper_id', 'catalyst_id', 'catalyst_name', 'pollutant_name',
                   'kinetics_model'},
    'variable': {'node_id', 'paper_id', 'experiment_id'},
    'curve': {'node_id', 'paper_id', 'variable_id', 'experiment_id', 'xs', 'ys', 'n_points',
              'needs_review'},
    'contradiction': {'node_id', 'paper_id'},
}
SUFFIXES = ('_unit', '_raw', '_range', '_qualifier', '_uncertainty', '_converted_from', '_unparsed')

# —— 曲线身份键：必须与 step10_graph_export.py 的 curve_key/fig_full 一致（图号含子图字母）——
FIG = re.compile(r'(?i)fig(?:ure)?\.?\s*(S?\d+)([a-z])?')
FIG_SUB = re.compile(r'(?i)(?:\(|\[|\bsub(?:fig(?:ure)?)?\.?\s*)([a-z])(?:\)|\]|\b)')


def fig_full(s):
    if not isinstance(s, str):
        return None
    m = FIG.search(s)
    if not m:
        return None
    sub = (m.group(2) or '').lower()
    if not sub:
        ms = FIG_SUB.search(s)
        if ms:
            sub = ms.group(1).lower()
    return 'Figure' + m.group(1) + sub


def curve_key(fig_raw, label):
    return (fig_full(str(fig_raw or '')), re.sub(r'\s+', '', str(label or '')).lower())


def base_of(k):
    for s in SUFFIXES:
        if k.endswith(s):
            return k[: -len(s)]
    return k


def load_step12():
    """复用 step12 的列白名单/兜底策略，避免自检与实际出表口径脱节。"""
    p = Path(__file__).with_name('step12_table_export.py')
    spec = importlib.util.spec_from_file_location('step12_tbl', str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sheet_shape(xlsx, sheet):
    """→ (headers, data_rows)；sheet 不存在返回 (None, 0)。"""
    from openpyxl import load_workbook
    wb = load_workbook(xlsx, read_only=True)
    if sheet not in wb.sheetnames:
        return None, 0
    ws = wb[sheet]
    headers, n = [], 0
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(x) for x in row if x is not None]
        elif any(v is not None for v in row):
            n += 1
    return headers, n


def sheet_rows(xlsx, sheet, col):
    """取某列全部非空值（用于点位/点数对账）。"""
    from openpyxl import load_workbook
    wb = load_workbook(xlsx, read_only=True)
    ws = wb[sheet]
    hdr = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    try:
        idx = [str(h) for h in hdr].index(col)
    except ValueError:
        return []
    return [r[idx] for r in ws.iter_rows(min_row=2, values_only=True) if r[idx] not in (None, '')]


def collect_sigs06(root, papers, issues):
    """06_schema → 曲线签名集合（实验级 / 变量级 / 水平级三层，图号回退链同 step10）。"""
    sigs = set()
    for p in papers:
        cands = [Path(root) / str(p.get('batch') or '') / str(p.get('dir_name') or '') / 'datamining' / '06_schema.json',
                 Path(root) / str(p.get('dir_name') or '') / 'datamining' / '06_schema.json']
        pth = next((c for c in cands if c.exists()), None)
        if pth is None:
            issues.append('C: 找不到 06_schema（%s）' % p.get('dir_name'))
            continue
        try:
            sch = json.loads(pth.read_text(encoding='utf-8'))
        except Exception as ex:
            issues.append('C: 06 解析失败 %s :: %s' % (p.get('dir_name'), str(ex)[:60]))
            continue

        def add(dps, fig):
            for d in (dps if isinstance(dps, list) else [dps] if isinstance(dps, dict) else []):
                if not isinstance(d, dict):
                    continue
                k = curve_key(d.get('source_figure') or d.get('ref') or fig, d.get('curve_label'))
                if k[1]:
                    sigs.add(k)

        for cat in sch.get('catalysts', []) or []:
            for e in cat.get('experiments', []) or []:
                add(e.get('degradation_profiles'), e.get('source_figure'))
                for v in e.get('variable_experiments', []) or []:
                    vfig = v.get('source_figure') or v.get('ref')
                    add(v.get('degradation_profiles'), vfig)
                    for lv in v.get('levels') or []:
                        add(lv.get('degradation_profiles'),
                            lv.get('source_figure') or lv.get('ref') or vfig)
    return sigs


def check_neo4j(env, g, papers, nodes, issues, out):
    """按「本次归属」回查实库：节点存在性 / 关系数 / 带属性边数 / 属性 key 数。

    【2026-10-10 修·自检假阴性】旧版有两处写窄：
      ① 本次 ids 只取 curve + variable 的 node_id；
      ② 边过滤写成「**两端**都要命中本次」(`(a…) AND (b…)`)。
    而 DEGRADES 的终点是 Entity（跨论文共享污染物实体：**无 paper_id**，node_id 也不在
    旧 ids 内）→ 所有连向 Entity 的边被整片排除。实测（旧版重导出）：导出 368 条边只报 331，
    34 条带 `pollutant` 的 DEGRADES 一条不剩 —— 等于「边属性是否写入」这道门形同虚设。
    现改为：本次集合 = 全部类别 node_id 全集 ∪ paper_id 全集；边按**任一端**归属本次计入
    （另一端允许是共享 Entity / Figure 占位）。并对账 edges.json 的期望边数/带属性边数。
    """
    cfg = {}
    if Path(env).exists():
        for line in open(env, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                cfg[k.strip()] = v.strip()
    if not cfg.get('NEO4J_URI'):
        issues.append('E: 读不到 NEO4J_URI（env=%s）' % env)
        return
    try:
        from neo4j import GraphDatabase
    except ImportError:
        issues.append('E: neo4j 驱动未安装，跳过实库核对')
        return
    drv = GraphDatabase.driver(cfg['NEO4J_URI'],
                               auth=(cfg.get('NEO4J_USERNAME', ''), cfg.get('NEO4J_PASSWORD', '')))
    # 本次集合：全部类别 node_id 全集（旧版只放 curve+variable）+ paper_id 全集
    ids = sorted({q.get('node_id') for arr in (nodes or {}).values() for q in (arr or [])
                  if isinstance(q, dict) and q.get('node_id')})
    pids = [p.get('paper_id') for p in papers if p.get('paper_id')]
    mine = ("((a.paper_id IN $pids OR a.node_id IN $ids) OR "
            "(b.paper_id IN $pids OR b.node_id IN $ids))")
    # edges.json 期望值（导出侧）
    exp_edges = exp_props = 0
    ep = Path(g) / 'edges.json'
    if ep.exists():
        try:
            for e in json.loads(ep.read_text(encoding='utf-8')) or []:
                exp_edges += 1
                if any(k not in ('type', 'from', 'to') for k in e):
                    exp_props += 1
        except Exception as ex:
            issues.append('E: edges.json 解析失败 :: %s' % str(ex)[:60])
    db = cfg.get('NEO4J_DATABASE') or None
    with drv.session(database=db) as s:
        rows = s.run(
            "MATCH (n) WHERE n.paper_id IN $pids RETURN labels(n)[0] AS l, count(n) AS c, "
            "sum(size(keys(n))) AS k", pids=pids).data()
        out['neo4j'] = {r['l']: {'nodes': r['c'], 'keys': r['k']} for r in rows}
        nb = out['neo4j']
        nb['_expected'] = {'node_ids': len(ids), 'papers': len(pids),
                           'edges': exp_edges, 'edges_with_props': exp_props}
        # 节点存在性：本次每个 node_id 至少命中 1 个节点
        nb['_nodes_found'] = s.run(
            "MATCH (n) WHERE n.node_id IN $ids RETURN count(DISTINCT n.node_id) AS c",
            ids=ids).single()['c']
        if nb['_nodes_found'] != len(ids):
            issues.append('E: 本次 node_id 实库命中 %d / %d（差 %d：未写入，或旧节点已清后的口径差）'
                          % (nb['_nodes_found'], len(ids), len(ids) - nb['_nodes_found']))
        nb['_papers_found'] = s.run(
            "MATCH (p:Paper) WHERE p.paper_id IN $pids RETURN count(p) AS c",
            pids=pids).single()['c']
        if nb['_papers_found'] != len(pids):
            issues.append('E: 本次 Paper 实库 %d / 导出 %d —— 有节点未写入'
                          % (nb['_papers_found'], len(pids)))
        # 「同篇两套并存」门：按 dir_name 反查库里该篇的**全部** paper_id（paper_id 是内容哈希，
        # 步骤十规则一变即换 id，唯一键 MERGE 挡不住 —— 只按本次 pids 查是查不出来的）
        dirs = sorted({p.get('dir_name') for p in papers if p.get('dir_name')})
        allp = sorted({r['pid'] for r in (s.run(
            "MATCH (p:Paper) WHERE p.dir_name IN $dirs RETURN DISTINCT p.paper_id AS pid",
            dirs=dirs).data() if dirs else []) if r.get('pid')})
        nb['_papers_all_pids'] = len(allp)
        if len(allp) > len(pids):
            issues.append('E: 同篇论文在库中有 %d 个 paper_id（本次导出 %d）—— 新旧两套并存，'
                          '入库前需 step11 --purge-export' % (len(allp), len(pids)))
        nb['_rels'] = s.run("MATCH (a)-[r]->(b) WHERE " + mine + " RETURN count(r) AS c",
                            pids=pids, ids=ids).single()['c']
        if exp_edges and nb['_rels'] != exp_edges:
            issues.append('E: 本次关系数 实库 %d ≠ 导出 %d（%+d）'
                          % (nb['_rels'], exp_edges, nb['_rels'] - exp_edges))
        ewp = s.run("MATCH (a)-[r]->(b) WHERE " + mine + " AND size(keys(r)) > 0 "
                    "RETURN type(r) AS t, count(r) AS c", pids=pids, ids=ids).data()
        nb['_edge_with_props'] = ewp
        got_props = sum(r['c'] for r in ewp)
        nb['_edge_with_props_n'] = got_props
        if exp_props and got_props != exp_props:
            issues.append('E: 带属性边 实库 %d ≠ 导出 %d —— 边属性未写入（查 step11 `SET r = $props`）'
                          % (got_props, exp_props))
    drv.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('export_dir')
    ap.add_argument('--root', default=None, help='语料根（给了才做 C 项 06 源曲线对账）')
    ap.add_argument('--neo4j', action='store_true', help='回查实库（节点/关系/属性/带属性边）')
    ap.add_argument('--env', default=str(Path(__file__).with_name('neo4j.env')))
    a = ap.parse_args()
    g = Path(a.export_dir)
    if not (g / 'tables.xlsx').exists():
        print('[!] 缺 tables.xlsx（先跑 step12_table_export.py）：%s' % g)
        return 1
    s12 = load_step12()
    nodes = {}
    for k in SHEET_OF:
        f = g / ('nodes_%s.json' % k)
        if f.exists():
            nodes[k] = json.loads(f.read_text(encoding='utf-8'))
        else:
            print('[!] 缺 nodes_%s.json' % k)

    cols_of = {'paper': s12.PAPER_COLS, 'catalyst': s12.CATALYST_COLS, 'experiment': s12.EXPERIMENT_COLS,
               'variable': s12.LEVEL_COLS, 'curve': s12.CURVE_COLS, 'contradiction': s12.CONTRA_COLS}
    issues, out = [], {'export_dir': str(g), 'sheets': {}, 'unmapped_cols': {}}

    # A 行数 + B 列覆盖
    for k, sheet in SHEET_OF.items():
        if k not in nodes:
            continue
        hdr, nrow = sheet_shape(g / 'tables.xlsx', sheet)
        if hdr is None:
            issues.append('A: tables.xlsx 缺 sheet「%s」' % sheet)
            continue
        out['sheets'][sheet] = {'rows': nrow, 'cols': len(hdr), 'nodes': len(nodes[k])}
        if nrow != len(nodes[k]):
            issues.append('A: %s 行数 %d ≠ 节点数 %d' % (sheet, nrow, len(nodes[k])))
        hdr_set, wl = set(hdr), {key for key, _ in cols_of[k]}
        head_of = dict(cols_of[k])
        miss = {}
        for n in nodes[k]:
            for key, v in n.items():
                if key in s12.SKIP_EXTRA or v is None or isinstance(v, (list, dict)):
                    continue
                if key.endswith('_json') or key.endswith('_n'):
                    continue
                if key in LEAD_KEYS[k]:
                    continue
                if key in wl:                      # 键本身即在白名单（如 x_unit/y_unit 是主键名）
                    if str(head_of.get(key)) not in hdr_set:
                        miss[key] = '白名单字段无表头列「%s」' % head_of.get(key)
                    continue
                b = base_of(key)
                if b in wl:                        # 三元组伴随键，由值/单位/原文三列承载
                    if str(head_of.get(b)) not in hdr_set:
                        miss[key] = '白名单字段无表头列「%s」' % head_of.get(b)
                    continue
                exp = '%s%s' % (key, s12.EXTRA_SUFFIX[sheet])
                if exp not in hdr_set:
                    miss[key] = '未成列（期望兜底列 %s）' % exp
        if miss:
            out['unmapped_cols'][sheet] = miss
            issues.append('B: %s 有 %d 个键未成列：%s' % (sheet, len(miss), list(miss)[:4]))

    # C 曲线三方 + D 点位
    cur = nodes.get('curve', [])
    sig_g = {curve_key(q.get('source_figure') or q.get('ref'), q.get('curve_label'))
             for q in cur if q.get('curve_label')}
    out['curves'] = {'nodes': len(cur), 'sigs_graph': len(sig_g)}
    if a.root:
        sig_06 = collect_sigs06(a.root, nodes.get('paper', []), issues)
        labels_g = {s[1] for s in sig_g}
        # 与 validate_sop_export 同口径：标签也不在图谱 = 真丢（FAIL）；
        # 标签在、仅图号不同 = 多图写法（'Fig.1, Fig.3'）等归一差异，单列提示不计 FAIL。
        lost, hints = [], []
        for s in sorted(sig_06):
            if s in sig_g:
                continue
            (hints if s[1] in labels_g else lost).append(s)
        out['curves'].update({'sigs_06': len(sig_06), 'lost': lost, 'fig_hints': hints})
        if lost:
            issues.append('C: 06 有 %d 条曲线签名未进图谱（疑似整条丢失）：%s'
                          % (len(lost), lost[:3]))
        if hints:
            print('  ~ 图号差异提示 %d 条（标签在图谱中、图号写法不同）：%s'
                  % (len(hints), hints[:3]))
    n_xs = sum(1 for q in cur if q.get('xs'))
    pts_rows = len(sheet_rows(g / 'tables.xlsx', 'Curves', 'Points'))
    out['curves']['with_xs'] = n_xs
    out['curves']['sheet_pts_rows'] = pts_rows
    if n_xs != pts_rows:
        issues.append('D: 带点位 Curve %d 条 ≠ 曲线表点位非空行 %d 条' % (n_xs, pts_rows))
    npt_graph = sum(q.get('n_points') or 0 for q in cur)
    npt_sheet = sum(v for v in sheet_rows(g / 'tables.xlsx', 'Curves', 'Point count') if isinstance(v, (int, float)))
    out['curves']['points_graph'] = npt_graph
    out['curves']['points_sheet'] = npt_sheet
    if npt_graph != npt_sheet:
        issues.append('D: 点数总和 图谱 %s ≠ 表 %s' % (npt_graph, npt_sheet))

    # E 实库
    if a.neo4j:
        check_neo4j(a.env, g, nodes.get('paper', []), nodes, issues, out)

    out['issues'] = issues
    out['status'] = 'PASS' if not issues else 'FAIL'
    rp = g / 'export_audit_report.json'
    rp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print('== export_audit：%s ==' % out['status'])
    for k, v in out['sheets'].items():
        print('  %-6s 行 %3d × 列 %3d  (节点 %3d)' % (k, v['rows'], v['cols'], v['nodes']))
    print('  曲线：图谱 %d 条 / 签名 %d %s' % (len(cur), len(sig_g),
          ('/ 06 源 %d' % out['curves']['sigs_06']) if 'sigs_06' in out['curves'] else ''))
    if a.neo4j and 'neo4j' in out:
        nb = out['neo4j']
        ex = nb.get('_expected', {})
        print('  实库回查（本次/期望）：节点 %s/%s · Paper %s/%s · 关系 %s/%s · 带属性边 %s/%s'
              % (nb.get('_nodes_found'), ex.get('node_ids'), nb.get('_papers_found'), ex.get('papers'),
                 nb.get('_rels'), ex.get('edges'), nb.get('_edge_with_props_n'), ex.get('edges_with_props')))
        print('    带属性边明细：', json.dumps(nb.get('_edge_with_props') or [], ensure_ascii=False))
    for it in issues:
        print('  ✗ %s' % it)
    print('报告：%s' % rp)
    return 0 if not issues else 1


if __name__ == '__main__':
    sys.exit(main())
