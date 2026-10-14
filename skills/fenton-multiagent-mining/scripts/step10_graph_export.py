# -*- coding: utf-8 -*-
"""step10_graph_export.py — 图谱节点/边导出（步骤十①）

把整个语料库转换为节点化图谱数据(JSON 纯导出, 不碰 Neo4j；入库见 step11):
  nodes:   Paper / Catalyst / Experiment / Variable / Curve / Contradiction / Entity
           【2026-10-07 v2】Variable 按变量水平展开：node_id = E1::V1::L2（水平值 + 该水平指标
           + 该水平曲线三者同节点，「pH=3 时去除率」可直接查）；旧篇（无 levels）仍为 E1::V1。
  edges:   HAS_CATALYST, USES_CATALYST, DEGRADES, HAS_VARIABLE, HAS_CURVE,
           FROM_FIGURE, DISPUTES(矛盾→被质疑对象, 兜底挂 Paper, 零孤儿)

【2026-10-05 并线】实体信息在导出时一次做完，不再需要收尾回填：
  · Pollutant 实体节点直接 join <批次>_污染物对齐表.json
    → pubchem_cid / pubchem_title / formula / mw / inchikey / cas
  · Catalyst 节点直接算 material_family / loading_pct（跨文献聚合键，不改 node_id）
  因此 `_deprecated/step12_graph_enrich.py` 退化为「历史批次补丁工具」，新批次不必跑它。

数据源: 每篇 datamining/06b_numeric_schema.json(优先) 或 06_schema.json
        + datamining/07_contradiction_ledger.json(篇级矛盾账目)
        + <本技能目录>/<批次>_污染物对齐表.json（可缺，缺则 Pollutant 只有 name）
输出: <语料根>/graph_export/
  nodes_paper.json / nodes_catalyst.json / nodes_experiment.json /
  nodes_variable.json / nodes_curve.json / nodes_contradiction.json /
  nodes_extra.json(Pollutant/Figure) / edges.json / manifest.json
用法:
  python scripts/step10_graph_export.py "D:\\fenton_papers" --batches B01 B02
  python scripts/step10_graph_export.py "D:\\fenton_papers"            # 全部 Bxx 批次
"""
import json, re, sys, os, hashlib, argparse, collections
from pathlib import Path

# 【2026-10-06 口径】严重度五值 + 中文全称：图谱 `severity_label` 直读；
#   E = 其他（无法归类），兼「判不了/待裁决」兜底；`unknown` 已退场（读到即归一为 E）。
SEV_LABEL = {"A": "数值偏差", "B": "归属·条件错位", "C": "命名·编号·计数错",
             "D": "结论被自身数据否定", "E": "其他（无法归类）"}

_MINERAL = {"goethite": "FeOOH", "针铁矿": "FeOOH", "hematite": "Fe2O3", "赤铁矿": "Fe2O3",
            "magnetite": "Fe3O4", "磁铁矿": "Fe3O4", "ferrihydrite": "FeOOH", "水铁矿": "FeOOH"}


def material_family(name):
    """催化剂名 → 材料族骨架（跨文献聚合键；不改 node_id）。

    例： "20% Fe2O3 + 10% CeO2/ZSM-5（Fe:Ce=2:1，总负载 30 wt%）" → "Fe2O3+CeO2/ZSM-5"
         "针铁矿 goethite (α-FeOOH)" → "FeOOH"
    """
    s = str(name or "").strip()
    if not s:
        return ""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)          # 去括号注释（配比/负载说明）
    s = re.sub(r"^\s*\d+(?:\.\d+)?\s*%\s*", "", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*%", "", s)
    for k, v in _MINERAL.items():                    # 矿物名/中文名 → 化学式骨架
        if re.search(k, s, re.I):
            s = v
            break
    s = re.sub(r"\s*\+\s*", "+", s)
    s = re.sub(r"\s+", " ", s).strip(" +/")
    s = s.replace("α-FeOOH", "FeOOH").replace("γ-Fe2O3", "Fe2O3")
    return s


def loading_pct(name):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:Fe|Cu|Co|Mn|Ce|Zn|Ni|Fe2O3|CeO2)", str(name or ""))
    return float(m.group(1)) if m else None


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def load_pollutant_props(ledger_dirs, batch_names):
    """合并各批 <批次>_污染物对齐表.json → {归一化名: 对齐行}（只收 status=ok）。
    ledger_dirs: {批次名: 台账目录}；2026-10-05 起台账位于 <批次目录>/_ledgers/。"""
    tbl = {}
    for b in batch_names:
        d = ledger_dirs.get(b)
        if not d:
            continue
        p = Path(d) / ("%s_污染物对齐表.json" % b)
        if not p.exists():
            continue
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rows, dict):
            continue
        for k, r in rows.items():
            if not isinstance(r, dict) or r.get("status") != "ok":
                continue
            for alias in (k, r.get("query")):
                if alias:
                    tbl.setdefault(_norm(alias), r)
    return tbl


def match_pollutant(name, tbl):
    """污染物名 → 对齐行。先全等，再双向子串（≥4 字符，挡 'B'/'G'/'NO' 这类拆分残片）。"""
    n = _norm(name)
    if not n or not tbl:
        return None
    if n in tbl:
        return tbl[n]
    best = None
    for k, r in tbl.items():
        if len(k) < 4:
            continue
        if k in n or (len(n) >= 4 and n in k):
            if best is None or len(k) > len(best[0]):
                best = (k, r)
    return best[1] if best else None


def to_entity_props(r):
    """对齐行 → Pollutant 节点属性（空值丢弃）。"""
    if not r:
        return {}
    d = {"pubchem_cid": r.get("cid"), "pubchem_title": r.get("pubchem_title"),
         "formula": r.get("formula"), "mw": r.get("mw"),
         "inchikey": r.get("inchikey"), "cas": r.get("cas"),
         "align_source": r.get("source") or "PubChem"}
    return {k: v for k, v in d.items() if v not in (None, "")}


NUM = re.compile(r'^[-+]?\d+(?:\.\d+)?$')
# 图号：数字后可紧跟一个子图字母（'Fig.4B' / 'Fig. 4b'）；空格分隔的 'Fig. 4 a' 不算子图
FIG = re.compile(r'(?i)fig(?:ure)?\.?\s*-?\s*(S?\d+)([a-z])?')
FIG_SUB = re.compile(r'(?i)(?:\(|\[|\bsub(?:fig(?:ure)?)?\.?\s*)([a-z])(?:\)|\]|\b)')

def fig_norm(s):
    """图号归一（**不含子图**）：'Fig. 4B' → 'Figure4'。粒度=图，用于 Figure 节点 id。"""
    if not isinstance(s, str): return None
    m = FIG.search(s)
    return 'Figure' + m.group(1) if m else None


def fig_full(s):
    """图号归一（**含子图字母**）：'Fig. 4B' → 'Figure4B'。

    【2026-10-10 修 / P1 真丢数据】曲线身份必须带子图。旧版 fig_norm 丢掉字母，
    'Fig.4B' 与 'Fig.4C' 都归一成 'Figure4' → 与同图例同名曲线判重而静默吞掉
    （实测 cual_kit6 变量 V2 的 Fig.4C 三条曲线 8 个点位整条丢失）。
    """
    if not isinstance(s, str): return None
    m = FIG.search(s)
    if not m: return None
    sub = (m.group(2) or '').lower()
    if not sub:
        ms = FIG_SUB.search(s)          # 兼容 'subfigure b' / 'Fig.4(a)' 写法
        if ms: sub = ms.group(1).lower()
    return 'Figure' + m.group(1) + sub


def curve_key(fig_raw, label):
    """曲线身份键 =（图号含子图，归一标签）。导出/校验/表格全链路统一用它，禁止两套归一化。"""
    lab = re.sub(r'\s+', '', str(label or '')).lower()
    return (fig_full(str(fig_raw or '')), lab)

def md5(s): return hashlib.md5(s.encode('utf-8')).hexdigest()[:12]

def numeric(v, field=''):
    """从标准化结构或原始值中提取纯数值"""
    if isinstance(v, dict):
        val = v.get('value')
        if isinstance(val, list):
            return [x for x in val if isinstance(x, (int, float))]
        return val if isinstance(val, (int, float)) else None
    if isinstance(v, bool): return None
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, str):
        m = re.match(NUM, v.strip())
        return float(m.group()) if m else None
    return None

def flatten_num(dst, key, v):
    """数值三元组 {value,unit,raw} → 扁平多键写进 dst（值 / 单位 / 原文 / 范围 / 限定符…）。

    【2026-10-07 修】旧版对 dict 一律 `continue`，而步骤八刚把数值标准化成 dict
    → Catalyst / Experiment 的数值字段**整段丢失**（实测 C1 丢 8 个数值键、E1 丢 13 个）。
    现在改为：{field} = 数值，{field}_unit = 单位，{field}_raw = 原文（单位与原文永不丢）。
    """
    if isinstance(v, dict) and 'value' in v:
        val = v.get('value')
        if val is not None:
            dst[key] = val
        if v.get('unit'):
            dst[key + '_unit'] = v['unit']
        if v.get('raw') not in (None, ''):
            dst[key + '_raw'] = str(v['raw'])
        rng = v.get('range')
        if isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng):
            dst[key + '_range'] = [float(rng[0]), float(rng[1])]
        for flag, suf in (('qualifier', '_qualifier'), ('uncertainty', '_uncertainty')):
            if v.get(flag) not in (None, '', []):
                dst[key + suf] = v[flag]
        if v.get('unparsed'):
            dst[key + '_unparsed'] = True
        if v.get('converted'):
            dst[key + '_converted_from'] = str(v.get('raw_unit') or '')
        return True
    if isinstance(v, (list, dict)):
        return False                      # 空 dict / 嵌套结构：Neo4j 存不了，丢弃
    nv = numeric(v, key)
    dst[key] = nv if nv is not None else v
    return True


def _brief(props, pairs=(('removal_efficiency', '去除率'), ('rate_constant_k', 'k'),
                         ('initial_pH', 'pH'), ('reaction_time', '时间'),
                         ('temperature', '温度'))):
    """实验摘要串（供 search_text 向量召回）。"""
    bits = []
    for k, lab in pairs:
        if k in props:
            u = props.get(k + '_unit') or ''
            bits.append(f'{lab} {props[k]}{u}')
    return ' '.join(bits)


def _join(*xs):
    return ' | '.join(str(x) for x in xs if x not in (None, '', [])) or None


def build_curve(kid, pid, eid, q, vid=None):
    """Curve 节点：点位数组原样带上，另记 n_points（入库时序列化成 *_json，点位不再丢）。"""
    xs = q.get('xs') or q.get('points') or q.get('xs_values')
    ys = q.get('ys') or q.get('ys_values')
    # 【2026-10-13 修】profile 级 x/y_unit 缺失而 curves[] 里带单位时按标签继承
    # （实测 B082 AJC-25-6-83 回填把单位写在 curves[].y_unit，profile 层留空 → 16 曲线 y_unit 丢）
    if isinstance(q.get('curves'), list) and ((not q.get('x_unit')) or (not q.get('y_unit'))):
        _lblq = str(q.get('curve_label') or q.get('label') or '')
        _pick = None
        for _c in q['curves']:
            if isinstance(_c, dict) and str(_c.get('label') or _c.get('curve_label') or '') == _lblq and _lblq:
                _pick = _c
                break
        if _pick is None:
            for _c in q['curves']:
                if isinstance(_c, dict) and (_c.get('x_unit') or _c.get('y_unit')):
                    _pick = _c
                    break
        if isinstance(_pick, dict):
            if not q.get('x_unit') and _pick.get('x_unit'):
                q['x_unit'] = _pick['x_unit']
            if not q.get('y_unit') and _pick.get('y_unit') is not None:
                q['y_unit'] = _pick['y_unit']
    # 【2026-10-10 修】图号回退链：source_figure 缺失时认 `ref` 字段
    # （水平级常把图号写成 ref='Fig.1'，旧版只认 source_figure → 回退到变量级图号，图号归属错）
    fig_raw = q.get('source_figure') or q.get('ref') or q.get('figure')
    node = dict(node_id=kid, paper_id=pid, experiment_id=eid, metric=q.get('metric'),
                source_figure=fig_raw, x_unit=q.get('x_unit'),
                y_unit=q.get('y_unit'), curve_label=q.get('curve_label') or q.get('label'))
    if vid:
        node['variable_id'] = vid
    # 【2026-10-09 防丢】回填失败的 needs_review 标记透传进图谱（复核线索不丢）
    if isinstance(q.get('qc'), dict) and q['qc'].get('needs_review'):
        node['needs_review'] = True
    if isinstance(xs, list) and xs:
        node['xs'] = xs
        node['n_points'] = len(xs)
    if isinstance(ys, list) and ys:
        node['ys'] = ys
        node['n_points'] = max(node.get('n_points', 0), len(ys))
    return {k: v for k, v in node.items() if v is not None}


def _is_paper_dir(p):
    """论文夹判定：自身含 datamining/ 且其中有 06b_numeric_schema.json 或 06_schema.json。"""
    dm = Path(p) / 'datamining'
    return (dm / '06b_numeric_schema.json').exists() or (dm / '06_schema.json').exists()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('--batches', nargs='*', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--ledger-dir', default=None,
                    help='台账目录（默认：批次 <批次>/_ledgers；单篇 <论文夹>/datamining/_ledgers）')
    a = ap.parse_args()
    root = Path(a.root)
    # 【2026-10-05 修】单篇自适应：root 自身即论文夹时按单篇处理。
    # 旧版只认 ^B\d+$ 批次目录，传论文夹会静默导出 0 篇、exit 0 且不报错。
    _single = _is_paper_dir(root)
    batches = [root] if _single else [Path(root) / b for b in (a.batches or sorted(
        d.name for d in root.iterdir() if re.match(r'^B\d+$', d.name)))]
    outd = Path(a.out) if a.out else root / 'graph_export'
    outd.mkdir(exist_ok=True)
    ledger_dirs = {b.name: (Path(a.ledger_dir) if a.ledger_dir
                            else (b / 'datamining' / '_ledgers' if _single else b / '_ledgers'))
                   for b in batches}
    ptbl = load_pollutant_props(ledger_dirs, [b.name for b in batches])
    print('污染物对齐表: %d 条规范条目（%s）' % (len(ptbl), a.ledger_dir or '<批次>/_ledgers'))
    N = {k: [] for k in ('paper', 'catalyst', 'experiment', 'variable', 'curve', 'contradiction')}
    E = []
    npapers = 0
    for bd in batches:
        if not bd.is_dir(): continue
        for pd in ([bd] if _single else sorted(bd.iterdir())):
            dm = pd / 'datamining'
            sch_p = dm / '06b_numeric_schema.json'
            if not sch_p.exists(): sch_p = dm / '06_schema.json'
            if not sch_p.exists(): continue
            try: schema = json.loads(sch_p.read_text(encoding='utf-8'))
            except Exception: continue
            led_p = dm / '07_contradiction_ledger.json'
            led = json.loads(led_p.read_text(encoding='utf-8')) if led_p.exists() else {'entries': [], 'total': 0}
            pid = 'P_' + md5(str(pd))
            meta = schema.get('metadata', {}) or {}
            # 【2026-10-10 修】单篇模式 batch 不能写成篇名：旧版 batch=bd.name=论文夹名，
            # validate_sop_export 按 <语料根>/<batch>/<dir_name> 拼路径必失败 → 单篇导出必 SKIP。
            batch_name = bd.name
            if _single:
                par = bd.parent
                if par and par.name and par.parent != par:      # 父目录 = 语料根下的批次目录
                    batch_name = par.name
            N['paper'].append(dict(
                paper_id=pid, batch=batch_name, dir_name=pd.name,
                title=meta.get('title'), journal=meta.get('journal'),
                doi=meta.get('doi'), publication_year=meta.get('publication_year'),
                authors=meta.get('authors'),
                search_text=' | '.join(str(meta.get(k)) for k in ('title', 'journal', 'doi') if meta.get(k)) or None,
                contradiction_count=led.get('total', 0),
                # 【2026-10-06】严重度摘要（字符串，Neo4j 属性不能是 dict）
                contradiction_severity=' '.join(f'{k}={v}' for k, v in sorted(
                    (led.get('by_severity') or {}).items())) or None))
            npapers += 1
            for ci, cat in enumerate(schema.get('catalysts', [])):
                cid = f'{pid}::C{ci+1}'
                # 【2026-10-04 修】45 字段在 cat['properties'] 子对象里。
                # 旧版直接遍历 cat.items()（顶层只有 catalyst_id/catalyst_name/properties/
                # other/experiments），properties 作为 dict 被整体跳过 → 图谱 Catalyst 节点
                # 只剩 5 个键、45 字段全丢。改为先取 properties 再补顶层标量键。
                src = dict(cat.get('properties') or {})
                for k, v in cat.items():
                    if k not in ('properties', 'experiments', 'other'):
                        src.setdefault(k, v)
                cprops = {}
                for k, v in src.items():
                    # 【2026-10-10 防丢】列表值（characterization_methods/metal_elements 等）→ 分号串，与实验层同策略
                    if isinstance(v, list):
                        strs = [str(x) for x in v if x is not None]
                        if strs:
                            cprops[k] = '; '.join(strs)
                        continue
                    flatten_num(cprops, k, v)
                # 【2026-10-09】other 兜底字段不再整段丢弃：标量/字符串以 other_ 前缀入节点
                cat_other = cat.get('other')
                # 【2026-10-10】other 为字符串（旧格式文字备注）→ 存 other_note，不丢内容
                if isinstance(cat_other, str):
                    if cat_other.strip():
                        flatten_num(cprops, 'other_note', cat_other)
                elif isinstance(cat_other, dict):
                    for k, v in cat_other.items():
                        # 【2026-10-12 修】旧守卫 `k in cprops` 会因 properties 同名空占位键
                        # 跳过 other 下的非空值（实测 d2ew00019a C4 丢 dominant_ROS 等）
                        if isinstance(v, (list, dict)) or ('other_' + k) in cprops:
                            continue
                        flatten_num(cprops, 'other_' + k, v)
                cprops.pop('name', None)
                fam = material_family(cat.get('catalyst_name'))
                cnode = dict(node_id=cid, paper_id=pid, name=cat.get('catalyst_name'), **cprops)
                if fam:
                    cnode['material_family'] = fam
                lp = loading_pct(cat.get('catalyst_name'))
                if lp is not None:
                    cnode['loading_pct'] = lp
                # 【2026-10-07】Catalyst 也补 search_text —— 否则 vec_catalyst 索引永远是空的
                cnode['search_text'] = _join(
                    cat.get('catalyst_name'), fam, cprops.get('chemical_formula'),
                    cprops.get('metal_elements'), cprops.get('support_material'),
                    cprops.get('synthesis_method'), cprops.get('crystal_phase'))
                N['catalyst'].append({k: v for k, v in cnode.items() if v is not None})
                E.append({'type': 'HAS_CATALYST', 'from': pid, 'to': cid})
                for ei, e in enumerate(cat.get('experiments', [])):
                    eid = f'{cid}::E{ei+1}'
                    # 【2026-10-04 修】41 字段在 e['properties'] 子对象里（同 catalyst 的 bug）
                    esrc = dict(e.get('properties') or {})
                    for k, v in e.items():
                        if k not in ('properties', 'variable_experiments', 'stability', 'other'):
                            esrc.setdefault(k, v)
                    _keep_txt = ('pollutant_name', 'pollutant_class', 'oxidant_type',
                                 'reaction_system', 'dominant_ROS', 'other_condition')
                    ep = {}
                    for k, v in esrc.items():
                        if k in _keep_txt:
                            if not isinstance(v, (list, dict)):
                                ep[k] = v
                            continue
                        # 【2026-10-09 防丢】properties 内嵌的 stability 字典（旧格式变体）→ stability_ 前缀
                        if k == 'stability' and isinstance(v, dict):
                            for kk, vv in v.items():
                                if isinstance(vv, dict):
                                    flatten_num(ep, 'stability_' + kk, vv)
                                elif isinstance(vv, list):
                                    strs = [str(x) for x in vv if x is not None]
                                    if strs:
                                        ep['stability_' + kk] = '; '.join(strs)
                                else:
                                    flatten_num(ep, 'stability_' + kk, vv)
                            continue
                        # 【2026-10-09 防丢】列表值（子智能体写的 [值, 来源] 对等）→ 分号串，不再静默丢弃
                        if isinstance(v, list):
                            strs = [str(x) for x in v if x is not None]
                            if strs:
                                ep[k] = '; '.join(strs)
                            continue
                        flatten_num(ep, k, v)
                    # 【2026-10-07】stability 6 键原本被整段排除在节点外（丢数据）→ 以 stability_ 前缀扁平入节点
                    stab = e.get('stability')
                    if isinstance(stab, dict):
                        # 【2026-10-13 修】与 properties 内嵌 stability 分支同规则：
                        # 旧版直接 flatten_num，list 值被静默丢弃（实测 B02 tfe_leaching 列表、
                        # B30 旧式二元组、B100 recycle_curves 共丢 10 键，零丢失审计捕获）
                        for k, v in stab.items():
                            if isinstance(v, list):
                                strs = [str(x) for x in v if x is not None]
                                if strs:
                                    ep['stability_' + k] = '; '.join(strs)
                            else:
                                flatten_num(ep, 'stability_' + k, v)
                    # 【2026-10-09】other 兜底字段不再整段丢弃
                    other_field = e.get('other')
                    # 【2026-10-10】other 为字符串（旧格式文字备注）→ 存 other_note，不丢内容
                    if isinstance(other_field, str):
                        if other_field.strip():
                            flatten_num(ep, 'other_note', other_field)
                    elif isinstance(other_field, dict):
                        for k, v in other_field.items():
                            # 【2026-10-12 修】旧守卫 `k in ep` 会因 properties 同名空占位键
                            # 跳过 other 下的非空值（实测 d2ew00019a C4E1 丢 2 键，零丢失审计捕获）
                            if isinstance(v, (list, dict)) or ('other_' + k) in ep:
                                continue
                            flatten_num(ep, 'other_' + k, v)
                    for bad in ('catalyst_id', 'experiment_id'):
                        ep.pop(bad, None)
                    ep['catalyst_name'] = cat.get('catalyst_name')
                    enode = dict(node_id=eid, paper_id=pid, catalyst_id=cid, **ep)
                    # 【2026-10-07】Experiment 补 search_text（vec_experiment 不再空转）
                    enode['search_text'] = _join(
                        ep.get('pollutant_name'), cat.get('catalyst_name'), ep.get('oxidant_type'),
                        ep.get('reaction_system'), ep.get('process_mode'), ep.get('medium'),
                        ep.get('dominant_ROS'), _brief(ep))
                    N['experiment'].append({k: v for k, v in enode.items() if v is not None})
                    E.append({'type': 'USES_CATALYST', 'from': eid, 'to': cid})
                    if e.get('pollutant_name'):
                        E.append({'type': 'DEGRADES', 'from': eid,
                                  'to': 'POL::' + md5(str(e['pollutant_name'])),
                                  'pollutant': e['pollutant_name']})
                    emitted_sigs = set()          # 【2026-10-09】实验内曲线签名去重（图号+标签）
                    emitted_nodes = {}            # 【2026-10-13】sig→已发射曲线节点，去重碰撞时把后到曲线的点位并进先到节点（防无点位描述符吞掉带点曲线，实测 B111 feal_mmt V4 曲线）
                    extra_var_curves = []         # 变量级未被水平覆盖的曲线 → 兜底补发
                    for vi, v in enumerate(e.get('variable_experiments', []) or []):
                        # 【2026-10-07 v2】变量 = 扫描组，水平逐项展开成独立节点：
                        #   新结构 node_id = E1::V1::L2（水平值 / 该水平指标 / 该水平曲线三者同节点），
                        #   「pH=3 时 TOC 去除率」一句 Cypher 查得动；
                        #   旧结构（无 levels）退化为 E1::V1，历史数据仍可导。
                        levels = v.get('levels')
                        has_levels = isinstance(levels, list) and len(levels) > 0
                        for li, lv in enumerate(levels if has_levels else [None]):
                            lv = lv if isinstance(lv, dict) else {}
                            vid = f'{eid}::V{vi+1}::L{li+1}' if has_levels else f'{eid}::V{vi+1}'
                            vp = {}
                            for k in ('variable_parameter', 'fixed_conditions', 'metric_name',
                                      'metric_source', 'source_figure', 'optimum_flag'):
                                val = lv.get(k, v.get(k)) if has_levels else v.get(k)
                                if val is not None and not isinstance(val, (list, dict)):
                                    vp[k] = val
                            # 【2026-10-09 防丢】白名单外的标量键（旧结构自定义键 / note / scan_metric 等）也入节点
                            _vskip = ('degradation_profiles', 'levels', 'variable_experiments', 'qc', 'other')
                            for k, val in (lv if has_levels else v).items():
                                if k in _vskip or val is None or k in vp:
                                    continue
                                # 【2026-10-10 防丢】三元组 dict 与列表值不再跳过：三元组走 flatten_num，列表转分号串
                                if isinstance(val, dict) and 'value' not in val:
                                    continue
                                if isinstance(val, list):
                                    strs = [str(x) for x in val if x is not None]
                                    if strs:
                                        vp[k] = '; '.join(strs)
                                    continue
                                flatten_num(vp, k, val)
                            # 【2026-10-10】other 为字符串（旧格式文字备注）→ 存 other_note，不丢内容
                            v_other = v.get('other')
                            if isinstance(v_other, str):
                                if v_other.strip():
                                    flatten_num(vp, 'other_note', v_other)
                            elif isinstance(v_other, dict):
                                for k, val in v_other.items():
                                    if isinstance(val, (list, dict)) or ('other_' + k) in vp:
                                        continue
                                    flatten_num(vp, 'other_' + k, val)
                            if has_levels:
                                vp['level_index'] = li + 1
                                if lv.get('level_value') is not None:
                                    flatten_num(vp, 'level_value', lv.get('level_value'))
                                flatten_num(vp, 'metric_value', lv.get('metric_value'))
                            else:
                                flatten_num(vp, 'metric_value', v.get('metric_value'))
                                if 'new_value' in v:
                                    flatten_num(vp, 'new_value', v.get('new_value'))
                            lvv = vp.get('level_value')
                            lv_txt = None
                            if lvv is not None:
                                lv_txt = ('水平 %g' % lvv) if isinstance(lvv, (int, float)) \
                                    and not isinstance(lvv, bool) else ('水平 %s' % lvv)
                            vnode = dict(node_id=vid, paper_id=pid, experiment_id=eid, **vp)
                            vnode['search_text'] = _join(
                                ep.get('pollutant_name'), vp.get('variable_parameter'), lv_txt,
                                vp.get('metric_name'), vp.get('metric_value'))
                            N['variable'].append({k: v_ for k, v_ in vnode.items() if v_ is not None})
                            E.append({'type': 'HAS_VARIABLE', 'from': eid, 'to': vid})
                            # 【2026-10-09 修】水平级/变量级曲线字段级 merge：06 里同一曲线常两层各存一半
                            # （变量级带 source_figure、水平级带 x_unit）——按 curve_label 对齐互补，
                            # 变量级未被水平覆盖的曲线进 extra_var_curves 兜底补发（跨变量按签名去重）。
                            def _lbl(s):
                                return re.sub(r'\s+', '', str(s or '')).lower()

                            var_prof_by_lbl = {}
                            var_curve_by_lbl = {}
                            _vdps_var = v.get('degradation_profiles')
                            for d in (_vdps_var if isinstance(_vdps_var, list)
                                      else [_vdps_var] if isinstance(_vdps_var, dict) else []):
                                if isinstance(d, dict):
                                    var_prof_by_lbl[_lbl(d.get('curve_label'))] = d
                                    # 【2026-10-13 修】profile 无自身标签时按其 curves[].label 索引，
                                    # 供水平级 merge 下探点位/单位（实测 B084/B107 变量级仅 {curves,metric}）
                                    for _c in (d.get('curves') or []):
                                        if isinstance(_c, dict):
                                            _k = _lbl(_c.get('curve_label') or _c.get('label'))
                                            if _k and _k not in var_curve_by_lbl:
                                                var_curve_by_lbl[_k] = (d, _c)
                            vdps = (lv.get('degradation_profiles') if has_levels
                                    else v.get('degradation_profiles')) or []
                            if isinstance(vdps, dict): vdps = [vdps]
                            # 【2026-10-13 修】profile 自身无点位而点位在 curves[] 时拆为逐曲线 descriptor
                            # （与 QX 兜底同规则；实测 B107 org v1 变量 12 条曲线丢点位丢标签）
                            _vdps2 = []
                            for _q in vdps:
                                if isinstance(_q, dict) and not (_q.get('points') or _q.get('xs')) and _q.get('curves'):
                                    _n = 0
                                    for _c in (_q.get('curves') or []):
                                        if isinstance(_c, dict):
                                            _cl = _c.get('curve_label') or _c.get('label')
                                            if not (_cl or not _q.get('curve_label')):
                                                continue  # 有标签 profile 下的无标签子曲线不拆（防误去重）
                                            _qq = dict(_q)
                                            _qq.pop('curves', None)
                                            if _cl:
                                                _qq['curve_label'] = _cl
                                            for f in ('points', 'xs', 'x_unit', 'y_unit', 'metric', 'source_figure'):
                                                if _c.get(f) and not _qq.get(f):
                                                    _qq[f] = _c[f]
                                            _vdps2.append(_qq)
                                            _n += 1
                                    if _n == 0:
                                        _vdps2.append(_q)
                                else:
                                    _vdps2.append(_q)
                            vdps = _vdps2
                            merged, covered = [], set()
                            for q in vdps:
                                if not isinstance(q, dict): continue
                                q = dict(q)
                                lbl = _lbl(q.get('curve_label'))
                                covered.add(lbl)
                                src_v = var_prof_by_lbl.get(lbl)
                                src_pair = var_curve_by_lbl.get(lbl)
                                if src_pair and (not q.get('points') and not q.get('xs')):
                                    _sd, _sc = src_pair
                                    for f in ('points', 'xs', 'x_unit', 'y_unit', 'metric'):
                                        if not q.get(f) and _sc.get(f):
                                            q[f] = _sc[f]
                                    if not q.get('source_figure'):
                                        q['source_figure'] = (_sc.get('source_figure')
                                                              or _sd.get('source_figure') or _sd.get('ref'))
                                if src_v:
                                    for f in ('source_figure', 'metric', 'x_unit', 'y_unit', 'points', 'xs'):
                                        if not q.get(f) and src_v.get(f):
                                            q[f] = src_v[f]
                                    # 【2026-10-13 修】变量级点位在 curves[].points 里时按标签下探取点，
                                    # 否则水平级扁形 profile 覆盖标签后点位丢失（实测 B084/B107/B109/B111 44 曲线）
                                    if not q.get('points') and not q.get('xs'):
                                        for _c in (src_v.get('curves') or []):
                                            if isinstance(_c, dict) and _lbl(_c.get('curve_label') or _c.get('label')) == lbl:
                                                if _c.get('points') or _c.get('xs'):
                                                    q['points'] = _c.get('points') or _c.get('xs')
                                                    q['xs'] = _c.get('xs') or _c.get('points')
                                                break
                                if not q.get('source_figure'):
                                    # 【2026-10-10 修】图号回退链：变量级 source_figure → 变量级 ref
                                    # → 水平级 ref（eaf_slag_bpa 水平级图号写在 ref='Fig.1'，旧版漏认）
                                    q['source_figure'] = ((src_v.get('source_figure') or src_v.get('ref'))
                                                          if src_v else None) \
                                        or v.get('source_figure') or v.get('ref') or lv.get('ref')
                                merged.append(q)
                            if has_levels:
                                vid_base = f'{eid}::V{vi+1}'
                                for lbl, d in var_prof_by_lbl.items():
                                    if lbl in covered:
                                        continue
                                    dd = dict(d)
                                    dd.setdefault('source_figure', d.get('source_figure') or d.get('ref')
                                                  or v.get('source_figure') or v.get('ref'))
                                    extra_var_curves.append((vid_base, dd, curve_key(dd.get('source_figure'), lbl)))
                            vdps = merged
                            for qi, q in enumerate(vdps):
                                if not isinstance(q, dict): continue
                                lbl_n = _lbl(q.get('curve_label'))
                                # 【2026-10-13 修】签名与 build_curve 同用 ref/figure 回退链：
                                # 旧版只认 source_figure，不同图(Fig-2/Fig-4)的同名标签曲线被误去重
                                sig = curve_key(q.get('source_figure') or q.get('ref') or q.get('figure'), lbl_n)
                                if lbl_n and sig in emitted_sigs:
                                    # 【2026-10-13 修】同签名碰撞时若先到节点无点位而后到曲线带点位，
                                    # 把点位/单位并进先到节点（实测 B111 feal_mmt V4 带点曲线被 V3 无点描述符吞掉）
                                    _prev = emitted_nodes.get(sig)
                                    if _prev is not None and not _prev.get('xs') and (q.get('xs') or q.get('points')):
                                        _prev['xs'] = q.get('xs') or q.get('points')
                                        _prev['n_points'] = len(_prev['xs'])
                                        for f in ('x_unit', 'y_unit'):
                                            if not _prev.get(f) and q.get(f):
                                                _prev[f] = q[f]
                                    continue   # 空标签曲线不参与去重（防误杀）
                                emitted_sigs.add(sig)
                                kid = f'{vid}::Q{qi+1}'
                                _cnode = build_curve(kid, pid, eid, q, vid=vid)
                                emitted_nodes[sig] = _cnode
                                N['curve'].append(_cnode)
                                E.append({'type': 'HAS_CURVE', 'from': vid, 'to': kid})
                                fig = fig_norm(str(q.get('source_figure') or ''))
                                if fig: E.append({'type': 'FROM_FIGURE', 'from': kid, 'to': 'FIG::%s::%s' % (pid, fig)})
                    # 【2026-10-09】变量级未被水平覆盖的曲线 → 挂到实验节点兜底补发（防丢曲线）
                    def _explode_var_curve(d):
                        # 【2026-10-13 修】变量级 profile 自身无点位而点位在 curves[] 里时拆成逐曲线
                        # descriptor，否则 QX 兜底节点丢全部点位（实测 B107 IJRET 48 条 QX 均无 xs）
                        if isinstance(d, dict) and not (d.get('points') or d.get('xs')) and d.get('curves'):
                            out = []
                            for c in d['curves']:
                                if isinstance(c, dict):
                                    q = dict(d)
                                    q.pop('curves', None)
                                    q['curve_label'] = c.get('curve_label') or c.get('label')
                                    for f in ('points', 'xs', 'x_unit', 'y_unit', 'metric', 'source_figure'):
                                        if c.get(f) and not q.get(f):
                                            q[f] = c[f]
                                    out.append(q)
                            return out or [d]
                        return [d]
                    _qx = 0
                    for vid_base, dd, sig in extra_var_curves:
                        for dd1 in _explode_var_curve(dd):
                            lbl_n1 = _lbl(dd1.get('curve_label'))
                            sig1 = curve_key(dd1.get('source_figure') or dd1.get('ref') or dd1.get('figure'), lbl_n1)
                            if lbl_n1 and sig1 in emitted_sigs:
                                _prev = emitted_nodes.get(sig1)
                                if _prev is not None and not _prev.get('xs') and (dd1.get('xs') or dd1.get('points')):
                                    _prev['xs'] = dd1.get('xs') or dd1.get('points')
                                    _prev['n_points'] = len(_prev['xs'])
                                    for f in ('x_unit', 'y_unit'):
                                        if not _prev.get(f) and dd1.get(f):
                                            _prev[f] = dd1[f]
                                continue   # 空标签不去重
                            emitted_sigs.add(sig1)
                            _qx += 1
                            kid = f'{eid}::QX{_qx}'
                            _cnode = build_curve(kid, pid, eid, dd1, vid=vid_base)
                            emitted_nodes[sig1] = _cnode
                            N['curve'].append(_cnode)
                            E.append({'type': 'HAS_CURVE', 'from': eid, 'to': kid})
                            fig = fig_norm(str(dd1.get('source_figure') or dd1.get('ref') or ''))
                            if fig: E.append({'type': 'FROM_FIGURE', 'from': kid, 'to': 'FIG::%s::%s' % (pid, fig)})
                    dps = e.get('degradation_profiles') or []
                    if isinstance(dps, dict): dps = [dps]
                    for qi, q in enumerate(dps):
                        if not isinstance(q, dict): continue
                        kid = f'{eid}::Q{qi+1}'
                        N['curve'].append(build_curve(kid, pid, eid, q))
                        E.append({'type': 'HAS_CURVE', 'from': eid, 'to': kid})
                        fig = fig_norm(str(q.get('source_figure') or ''))
                        if fig: E.append({'type': 'FROM_FIGURE', 'from': kid, 'to': 'FIG::%s::%s' % (pid, fig)})
            # 矛盾账目: 篇级挂载
            paper_exps = [n_['node_id'] for n_ in N['experiment'] if n_['paper_id'] == pid]
            EXP_FIELD_HINTS = ('removal', 'dosage', 'pH', 'time', 'temperature', 'conc',
                               'k =', 'k=', 'toc', 'cod', 'oxidant')
            for ki, it in enumerate(led.get('entries', [])):
                kid = f'{pid}::K{ki+1}'
                # Contradiction 节点 = 审核标记: 告知该篇提取数据存在需人工复核的矛盾条目
                # 【2026-10-06 口径改】category 废弃 → severity(A/B/C/D/E) + severity_label + evidence_source
                #   + cross_paper / caliber_variance 两个布尔标记（见 references/矛盾分类规范.md）
                #   E = 其他（无法归类），兼「判不了/待裁决」兜底；`unknown` 已退场（读到即归一为 E）
                adopted=it.get('裁决/采纳值', it.get('adopted_value'))
                severity = str(it.get('severity') or it.get('严重度') or 'E').strip().upper()
                if severity not in SEV_LABEL:
                    severity = 'E'
                cross_paper = bool(it.get('cross_paper') or it.get('跨文献'))
                # 【2026-10-06 溯源】fld/fig_raw/trace：任何一条矛盾都要能一眼看出「哪篇文献·哪个字段·哪张图」
                fld = str(it.get('字段') or it.get('field') or '')
                fig_raw = it.get('图号/位置') or it.get('figure')
                title_txt = str(meta.get('title') or '?')[:90]
                N['contradiction'].append(dict(
                    node_id=kid, paper_id=pid,
                    trace=f"{pid} / {title_txt} / {fld or '-'} / {fig_raw or '-'}",
                    figure=fig_raw,
                    field=it.get('字段') or it.get('field'),
                    severity=severity,
                    severity_label=SEV_LABEL[severity],
                    evidence_source=it.get('evidence_source') or it.get('载体来源'),
                    cross_paper=cross_paper,
                    caliber_variance=bool(it.get('caliber_variance') or it.get('口径差')),
                    text_value=it.get('正文值', it.get('text_value')),
                    image_value=it.get('图值/冲突值', it.get('image_value')),
                    adopted_value=adopted,
                    reason=it.get('理由', it.get('adopt_reason', it.get('adopted_reason'))),
                    confidence=it.get('置信度', it.get('confidence')),
                    needs_human_review=(severity == 'E' or cross_paper),
                    review_status='pending',
                    review_scope='data_quality',   # 指向本篇提取数据的复核范围
                    search_text=_join(fld, severity, SEV_LABEL[severity],
                                      it.get('evidence_source') or it.get('载体来源'),
                                      it.get('理由', it.get('adopt_reason')))))
                # 【2026-10-06 锚点规范化】矛盾三层锚定，篇级 100% 零孤儿：
                #   DISPUTES        → Paper      必连（篇级唯一，零孤儿）
                #   FROM_EXPERIMENT → Experiment 该篇实验节点唯一且字段可判定时（尽力而为）
                #   DISPUTED_IN     → Figure     图号可解析时；图节点按论文限定
                #                                 （旧版 'FIG::Figure5' 全局共享 → 跨论文图号串台）
                E.append({'type': 'DISPUTES', 'from': kid, 'to': pid})
                if fld and any(w in fld for w in EXP_FIELD_HINTS) and len(paper_exps) == 1:
                    E.append({'type': 'FROM_EXPERIMENT', 'from': kid, 'to': paper_exps[0]})
                fig = fig_norm(str(fig_raw or ''))
                if fig: E.append({'type': 'DISPUTED_IN', 'from': kid, 'to': 'FIG::%s::%s' % (pid, fig)})
    # 去重 pollutant/figure 哨兵节点
    pols = {e['to']: e['pollutant'] for e in E if e['type'] == 'DEGRADES'}
    figs = {e['to'] for e in E if e['type'] == 'FROM_FIGURE'}
    pol_nodes, n_pol_hit = [], 0
    for t, n_ in pols.items():
        node = {'node_id': t, 'label': 'Pollutant', 'name': n_}
        r = match_pollutant(n_, ptbl)
        if r:
            node.update(to_entity_props(r))
            n_pol_hit += 1
        # 【2026-10-10 修】Pollutant 补 search_text —— 否则 Entity 向量索引永远空转
        # （实测 27 个污染物节点 search_text 0/27，污染物语义检索无数据可召回）
        node['search_text'] = _join(n_, node.get('formula'), node.get('pubchem_title'), node.get('cas'))
        pol_nodes.append(node)
    # 【2026-10-06】图节点按论文限定（node_id = 'FIG::<paper_id>::FigureN'），带 paper_id/name：
    # 让"这条矛盾/这条曲线出自哪篇的哪张图"可直读。旧版 'FIG::Figure5' 是跨论文共享节点（会串台）。
    fig_nodes = []
    for f in sorted(figs):
        parts = f.split('::')
        fig_nodes.append({'node_id': f, 'label': 'Figure',
                          'paper_id': parts[1] if len(parts) > 2 else None,
                          'name': parts[-1]})
    extra_nodes = pol_nodes + fig_nodes
    for k, v in N.items():
        (outd / f'nodes_{k}.json').write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding='utf-8')
    (outd / 'edges.json').write_text(json.dumps(E, ensure_ascii=False, indent=1), encoding='utf-8')
    (outd / 'nodes_extra.json').write_text(json.dumps(extra_nodes, ensure_ascii=False, indent=1), encoding='utf-8')
    (outd / 'manifest.json').write_text(json.dumps({
        'papers': npapers,
        'nodes': {k: len(v) for k, v in N.items()}, 'extra_nodes': len(extra_nodes),
        'pollutant_align': {'total': len(pols), 'matched': n_pol_hit},
        'edges': collections.Counter(e['type'] for e in E)}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('exported to', outd)
    print('papers:', npapers, '| nodes:', {k: len(v) for k, v in N.items()}, '| edges:', len(E))
    print('Pollutant 实体 %d 个，其中 %d 个已带对齐属性（CID/分子式/分子量）' % (len(pols), n_pol_hit))
    print('下一步入库：python scripts/step11_neo4j_import.py "%s"' % outd)

if __name__ == '__main__':
    main()
