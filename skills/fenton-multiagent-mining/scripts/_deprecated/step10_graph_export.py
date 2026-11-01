# -*- coding: utf-8 -*-
"""step10_graph_export.py — graph node/edge export (Step 10①)

Convert the whole corpus into node-shaped graph data (pure JSON export; Neo4j is untouched — see step11 for import):
  nodes:   Paper / Catalyst / Experiment / Variable / Curve / Contradiction / Entity
           【2026-10-07 v2】Variable nodes expand per variable level: node_id = E1::V1::L2 (the level value, that level's indicator
           and that level's curve share one node, so "removal at pH=3" is directly queryable); legacy papers without levels stay E1::V1.
  edges:   HAS_CATALYST, USES_CATALYST, DEGRADES, HAS_VARIABLE, HAS_CURVE,
           FROM_FIGURE, DISPUTES (contradiction → disputed object, falling back to Paper with zero orphans)

【2026-10-05 merge】Entity information is completed once at export time; no closing backfill is needed:
  · Pollutant entity nodes join <corpus>_pollutant_alignment.json directly
    → pubchem_cid / pubchem_title / formula / mw / inchikey / cas
  · Catalyst nodes compute material_family / loading_pct directly (cross-paper aggregation keys; node_id unchanged)
  so `_deprecated/step12_graph_enrich.py` degrades to a "legacy data patch tool" that new versions never run.

Data sources: each paper's datamining/06b_numeric_schema.json (preferred) or 06_schema.json
        + datamining/07_contradiction_ledger.json (per-paper contradiction ledger)
        + <corpus>_pollutant_alignment.json in this skill directory (optional; without it a Pollutant carries only its name)
Output: <corpus root>/graph_export/
  nodes_paper.json / nodes_catalyst.json / nodes_experiment.json /
  nodes_variable.json / nodes_curve.json / nodes_contradiction.json /
  nodes_extra.json(Pollutant/Figure) / edges.json / manifest.json
Usage:
  python scripts/step10_graph_export.py "D:\\fenton_papers" --groups <group> ...
  python scripts/step10_graph_export.py "D:\\fenton_papers"            # all groups
"""
import json, re, sys, os, hashlib, argparse, collections
from pathlib import Path

# 【2026-10-06 caliber】five severity values + full labels: `severity_label` is read directly from the graph;
#   E = other (unclassifiable), also the fallback for "cannot grade / pending"; `unknown` is retired (normalized to E on sight).
SEV_LABEL = {"A": "numerical deviation", "B": "attribution or condition mismatch", "C": "naming, numbering or counting error",
             "D": "conclusion refuted by its own data", "E": "other (unclassifiable)"}

_MINERAL = {"goethite": "FeOOH", "针铁矿": "FeOOH", "hematite": "Fe2O3", "赤铁矿": "Fe2O3",
            "magnetite": "Fe3O4", "磁铁矿": "Fe3O4", "ferrihydrite": "FeOOH", "水铁矿": "FeOOH"}


def material_family(name):
    """Catalyst name → material family skeleton (the cross-paper aggregation key; node_id unchanged).

    Example: "20% Fe2O3 + 10% CeO2/ZSM-5 (Fe:Ce=2:1, total loading 30 wt%)" → "Fe2O3+CeO2/ZSM-5"
            "goethite (α-FeOOH)" → "FeOOH"
    """
    s = str(name or "").strip()
    if not s:
        return ""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)          # drop parenthetical notes (ratios, loading)
    s = re.sub(r"^\s*\d+(?:\.\d+)?\s*%\s*", "", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*%", "", s)
    for k, v in _MINERAL.items():                    # mineral / legacy name → chemical formula skeleton
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
    """Merge each group's <corpus>_pollutant_alignment.json → {normalized name: alignment row} (status=ok only).
    ledger_dirs: {group name: ledger directory}; since 2026-10-05 the ledger lives in <multi-paper directory>/_ledgers/."""
    tbl = {}
    for b in batch_names:
        d = ledger_dirs.get(b)
        if not d:
            continue
        p = Path(d) / ("%s_pollutant_alignment.json" % b)
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
    """Pollutant name → alignment row. Exact match first, then bidirectional substring (≥4 characters, blocking fragments like 'B'/'G'/'NO')."""
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
    """Alignment row → Pollutant node properties (empty values dropped)."""
    if not r:
        return {}
    d = {"pubchem_cid": r.get("cid"), "pubchem_title": r.get("pubchem_title"),
         "formula": r.get("formula"), "mw": r.get("mw"),
         "inchikey": r.get("inchikey"), "cas": r.get("cas"),
         "align_source": r.get("source") or "PubChem"}
    return {k: v for k, v in d.items() if v not in (None, "")}


NUM = re.compile(r'^[-+]?\d+(?:\.\d+)?$')
# figure number: a digit may be followed directly by a subfigure letter ('Fig.4B' / 'Fig. 4b'); a space-separated 'Fig. 4 a' is not a subfigure
FIG = re.compile(r'(?i)fig(?:ure)?\.?\s*-?\s*(S?\d+)([a-z])?')
FIG_SUB = re.compile(r'(?i)(?:\(|\[|\bsub(?:fig(?:ure)?)?\.?\s*)([a-z])(?:\)|\]|\b)')

def fig_norm(s):
    """Normalize a figure number (**without subfigure**): 'Fig. 4B' → 'Figure4'. Granularity = figure, used for Figure node ids."""
    if not isinstance(s, str): return None
    m = FIG.search(s)
    return 'Figure' + m.group(1) if m else None


def fig_full(s):
    """Normalize a figure number (**with subfigure letter**): 'Fig. 4B' → 'Figure4B'.

    【2026-10-10 fix / P1 real data loss】A curve identity must carry the subfigure. The old fig_norm dropped the letter, so
    'Fig.4B' and 'Fig.4C' both normalized to 'Figure4' → same-legend curves were deduplicated and silently swallowed
    (measured: all three Fig.4C curves of cual_kit6 variable V2 lost, 8 points each).
    """
    if not isinstance(s, str): return None
    m = FIG.search(s)
    if not m: return None
    sub = (m.group(2) or '').lower()
    if not sub:
        ms = FIG_SUB.search(s)          # tolerate 'subfigure b' / 'Fig.4(a)'
        if ms: sub = ms.group(1).lower()
    return 'Figure' + m.group(1) + sub


def curve_key(fig_raw, label):
    """Curve identity key = (figure number including subfigure, normalized label). Export/validation/tables all use it; two normalizations are forbidden."""
    lab = re.sub(r'\s+', '', str(label or '')).lower()
    return (fig_full(str(fig_raw or '')), lab)

def md5(s): return hashlib.md5(s.encode('utf-8')).hexdigest()[:12]


def _experiments_of(schema, cat):
    """Tolerate three formats of 06 experiment nodes (top-level experiments finalized 2026-10-23):
    new — top-level experiments[] grouped by catalyst_id (E indices identical to the legacy nested caliber);
    intermediate — top-level variable_experiments[] (the daytime key name, fallback);
    legacy — catalysts[].experiments[] nested fallback."""
    top = schema.get('experiments') or schema.get('variable_experiments')
    if top:
        cid = cat.get('catalyst_id')
        return [e for e in top if isinstance(e, dict) and e.get('catalyst_id') == cid]
    return cat.get('experiments', []) or []

def numeric(v, field=''):
    """Extract a pure number from a standardized structure or a raw value"""
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
    """Numeric triple {value,unit,raw} → flatten into multiple keys in dst (value / unit / raw / range / qualifier…).

    【2026-10-07 fix】The old version `continue`d on any dict, and Step 8 had just standardized numerics into dicts
    → numeric fields of Catalyst / Experiment were **lost wholesale** (measured: C1 lost 8 numeric keys, E1 lost 13).
    Now: {field} = value, {field}_unit = unit, {field}_raw = source text (unit and source text are never lost).
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
        return False                      # empty dict / nested structure: Neo4j cannot store it, drop it
    nv = numeric(v, key)
    dst[key] = nv if nv is not None else v
    return True


def _brief(props, pairs=(('removal_efficiency', 'removal'), ('rate_constant_k', 'k'),
                         ('initial_pH', 'pH'), ('reaction_time', 'time'),
                         ('temperature', 'temperature'))):
    """Experiment summary string (for search_text vector recall)."""
    bits = []
    for k, lab in pairs:
        if k in props:
            u = props.get(k + '_unit') or ''
            bits.append(f'{lab} {props[k]}{u}')
    return ' '.join(bits)


def _join(*xs):
    return ' | '.join(str(x) for x in xs if x not in (None, '', [])) or None


def build_curve(kid, pid, eid, q, vid=None):
    """Curve node: the point array is carried as-is plus n_points (serialized to *_json on import so points are never lost)."""
    xs = q.get('xs') or q.get('points') or q.get('xs_values')
    ys = q.get('ys') or q.get('ys_values')
    # 【2026-10-13 fix】when the profile-level x/y_unit is missing but curves[] carry the unit, inherit it by label
    # (measured B082 AJC-25-6-83: backfill wrote units on curves[].y_unit while the profile level was empty → 16 curves lost y_unit)
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
    # 【2026-10-10 fix】figure-number fallback chain: fall back to the `ref` field when source_figure is missing
    # (the level level often writes the figure number as ref='Fig.1'; the old version read only source_figure → fell back to the variable-level figure, misattributing it)
    fig_raw = q.get('source_figure') or q.get('ref') or q.get('figure')
    node = dict(node_id=kid, paper_id=pid, experiment_id=eid, metric=q.get('metric'),
                source_figure=fig_raw, x_unit=q.get('x_unit'),
                y_unit=q.get('y_unit'), curve_label=q.get('curve_label') or q.get('label'))
    if vid:
        node['variable_id'] = vid
    # 【2026-10-09 anti-loss】the needs_review flag from a failed backfill is passed into the graph (the review clue is not lost)
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
    """Paper-folder test: it contains datamining/ with 06b_numeric_schema.json or 06_schema.json."""
    dm = Path(p) / 'datamining'
    return (dm / '06b_numeric_schema.json').exists() or (dm / '06_schema.json').exists()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('--groups', '--batches', dest='batches', nargs='*', default=None,
                    help='export only the named paper groups (alias: --batches)')
    ap.add_argument('--out', default=None)
    ap.add_argument('--ledger-dir', default=None,
                    help='ledger directory (default: group <corpus>/_ledgers; single paper <paper folder>/datamining/_ledgers')
    a = ap.parse_args()
    root = Path(a.root)
    # 【2026-10-05 fix】single-paper auto-detection: when root itself is a paper folder, treat it as one paper.
    # The old version accepted only ^B\d+$ multi-paper directories, so passing a paper folder silently exported 0 papers, exited 0 and reported nothing.
    _single = _is_paper_dir(root)
    batches = [root] if _single else [Path(root) / b for b in (a.batches or sorted(
        d.name for d in root.iterdir() if re.match(r'^B\d+$', d.name)))]
    outd = Path(a.out) if a.out else root / 'graph_export'
    outd.mkdir(exist_ok=True)
    ledger_dirs = {b.name: (Path(a.ledger_dir) if a.ledger_dir
                            else (b / 'datamining' / '_ledgers' if _single else b / '_ledgers'))
                   for b in batches}
    ptbl = load_pollutant_props(ledger_dirs, [b.name for b in batches])
    print('pollutant alignment table: %d canonical entries (%s)' % (len(ptbl), a.ledger_dir or '<corpus>/_ledgers'))
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
            # 【2026-10-10 fix】in single-paper mode batch must not be the paper name: the old version set batch=bd.name=folder name,
            # and validate_sop_export built <corpus root>/<batch>/<dir_name> which always failed → single-paper exports were always SKIPped.
            batch_name = bd.name
            if _single:
                par = bd.parent
                if par and par.name and par.parent != par:      # the parent directory = the multi-paper directory under the corpus root
                    batch_name = par.name
            N['paper'].append(dict(
                paper_id=pid, batch=batch_name, dir_name=pd.name,
                title=meta.get('title'), journal=meta.get('journal'),
                doi=meta.get('doi'), publication_year=meta.get('publication_year'),
                authors=meta.get('authors'),
                # 【2026-10-17】duplicate-archive marker (the folder name of another copy with the same DOI), see _duplicate_papers_report.md
                duplicate_of=meta.get('duplicate_of'),
                search_text=' | '.join(str(meta.get(k)) for k in ('title', 'journal', 'doi') if meta.get(k)) or None,
                contradiction_count=led.get('total', 0),
                # 【2026-10-06】severity summary (a string, because a Neo4j property cannot be a dict)
                contradiction_severity=' '.join(f'{k}={v}' for k, v in sorted(
                    (led.get('by_severity') or {}).items())) or None))
            npapers += 1
            for ci, cat in enumerate(schema.get('catalysts', [])):
                cid = f'{pid}::C{ci+1}'
                # 【2026-10-04 fix】the 45 fields live in the cat['properties'] sub-object.
                # The old version iterated cat.items() directly (the top level holds only catalyst_id/catalyst_name/properties/
                # other/experiments), so properties was skipped as a dict → the Catalyst node kept only 5 keys and all 45 fields were lost.
                # Now properties is taken first and top-level scalar keys are added afterwards.
                src = dict(cat.get('properties') or {})
                for k, v in cat.items():
                    if k not in ('properties', 'experiments', 'other'):
                        src.setdefault(k, v)
                cprops = {}
                for k, v in src.items():
                    # 【2026-10-10 anti-loss】list values (characterization_methods/metal_elements etc.) → semicolon string, same policy as the experiment layer
                    if isinstance(v, list):
                        strs = [str(x) for x in v if x is not None]
                        if strs:
                            cprops[k] = '; '.join(strs)
                        continue
                    flatten_num(cprops, k, v)
                # 【2026-10-09】other fallback fields are no longer dropped wholesale: scalars/strings enter the node with an other_ prefix
                cat_other = cat.get('other')
                # 【2026-10-10】other as a string (legacy free-text note) → stored as other_note, content preserved
                if isinstance(cat_other, str):
                    if cat_other.strip():
                        flatten_num(cprops, 'other_note', cat_other)
                elif isinstance(cat_other, dict):
                    for k, v in cat_other.items():
                        # 【2026-10-12 fix】the old guard `k in cprops` skipped non-empty values under other because of a same-named empty placeholder key in properties
                        # (measured d2ew00019a C4 lost dominant_ROS etc.)
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
                # 【2026-10-07】Catalyst also gets search_text — otherwise the vec_catalyst index stays empty forever
                cnode['search_text'] = _join(
                    cat.get('catalyst_name'), fam, cprops.get('chemical_formula'),
                    cprops.get('metal_elements'), cprops.get('support_material'),
                    cprops.get('synthesis_method'), cprops.get('crystal_phase'))
                N['catalyst'].append({k: v for k, v in cnode.items() if v is not None})
                E.append({'type': 'HAS_CATALYST', 'from': pid, 'to': cid})
                for ei, e in enumerate(_experiments_of(schema, cat)):
                    eid = f'{cid}::E{ei+1}'
                    # 【2026-10-04 fix】the 41 fields live in the e['properties'] sub-object (the same bug as catalyst)
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
                        # 【2026-10-09 anti-loss】stability dict embedded in properties (legacy variant) → stability_ prefix
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
                        # 【2026-10-09 anti-loss】list values (e.g. [value, source] pairs written by subagents) → semicolon string instead of silently dropping
                        if isinstance(v, list):
                            strs = [str(x) for x in v if x is not None]
                            if strs:
                                ep[k] = '; '.join(strs)
                            continue
                        flatten_num(ep, k, v)
                    # 【2026-10-07】the 6 stability keys used to be excluded from the node entirely (data loss) → flattened into the node with a stability_ prefix
                    stab = e.get('stability')
                    if isinstance(stab, dict):
                        # 【2026-10-13 fix】same rule as the stability branch embedded in properties:
                        # the old version called flatten_num directly and list values were silently dropped (measured: one paper's tfe_leaching list,
                        # B30 legacy pairs, B100 recycle_curves — 10 keys lost in total, caught by the zero-loss audit)
                        for k, v in stab.items():
                            if isinstance(v, list):
                                strs = [str(x) for x in v if x is not None]
                                if strs:
                                    ep['stability_' + k] = '; '.join(strs)
                            else:
                                flatten_num(ep, 'stability_' + k, v)
                    # 【2026-10-09】other fallback fields are no longer dropped wholesale
                    other_field = e.get('other')
                    # 【2026-10-10】other as a string (legacy free-text note) → stored as other_note, content preserved
                    if isinstance(other_field, str):
                        if other_field.strip():
                            flatten_num(ep, 'other_note', other_field)
                    elif isinstance(other_field, dict):
                        for k, v in other_field.items():
                            # 【2026-10-12 fix】the old guard `k in ep` skipped non-empty values under other because of a same-named empty placeholder key in properties
                            # (measured d2ew00019a C4E1 lost 2 keys, caught by the zero-loss audit)
                            if isinstance(v, (list, dict)) or ('other_' + k) in ep:
                                continue
                            flatten_num(ep, 'other_' + k, v)
                    for bad in ('catalyst_id', 'experiment_id'):
                        ep.pop(bad, None)
                    ep['catalyst_name'] = cat.get('catalyst_name')
                    enode = dict(node_id=eid, paper_id=pid, catalyst_id=cid, **ep)
                    # 【2026-10-07】Experiment gets search_text (vec_experiment no longer idles)
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
                    emitted_sigs = set()          # 【2026-10-09】in-experiment curve signature dedup (figure number + label)
                    emitted_nodes = {}            # 【2026-10-13】sig→emitted curve node; on a collision the later curve's points merge into the earlier node (so a point-less descriptor cannot swallow a curve with points; measured B111 feal_mmt V4)
                    extra_var_curves = []         # variable-level curves not covered by any level → emitted as a fallback
                    for vi, v in enumerate(e.get('variable_experiments', []) or []):
                        # 【2026-10-07 v2】a variable = a sweep group; each level expands into its own node:
                        #   new structure node_id = E1::V1::L2 (level value / that level's indicator / that level's curve share one node),
                        #   so "TOC removal at pH=3" is a one-line Cypher query;
                        #   the legacy structure (no levels) degrades to E1::V1, and historical data still exports.
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
                            # 【2026-10-09 anti-loss】scalar keys outside the whitelist (legacy custom keys / note / scan_metric etc.) also enter the node
                            _vskip = ('degradation_profiles', 'levels', 'variable_experiments', 'qc', 'other')
                            for k, val in (lv if has_levels else v).items():
                                if k in _vskip or val is None or k in vp:
                                    continue
                                # 【2026-10-10 anti-loss】triple dicts and list values are no longer skipped: triples go through flatten_num, lists become semicolon strings
                                if isinstance(val, dict) and 'value' not in val:
                                    continue
                                if isinstance(val, list):
                                    strs = [str(x) for x in val if x is not None]
                                    if strs:
                                        vp[k] = '; '.join(strs)
                                    continue
                                flatten_num(vp, k, val)
                            # 【2026-10-10】other as a string (legacy free-text note) → stored as other_note, content preserved
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
                                lv_txt = ('level %g' % lvv) if isinstance(lvv, (int, float)) \
                                    and not isinstance(lvv, bool) else ('level %s' % lvv)
                            # 【2026-10-14 fix】a 06 variable node may carry legacy metadata experiment_id (e.g. 'E1' in cl.130313),
                            # colliding with the explicit kwarg; eid follows the iteration context (cid::E{i}), so the three explicit keys are stripped to prevent overwrite.
                            _vp = {k: v_ for k, v_ in vp.items() if k not in ('node_id', 'paper_id', 'experiment_id')}
                            vnode = dict(node_id=vid, paper_id=pid, experiment_id=eid, **_vp)
                            vnode['search_text'] = _join(
                                ep.get('pollutant_name'), vp.get('variable_parameter'), lv_txt,
                                vp.get('metric_name'), vp.get('metric_value'))
                            N['variable'].append({k: v_ for k, v_ in vnode.items() if v_ is not None})
                            E.append({'type': 'HAS_VARIABLE', 'from': eid, 'to': vid})
                            # 【2026-10-09 fix】level-level/variable-level curve fields merge field by field: in 06 the same curve often has half stored at each layer
                            # (variable level carries source_figure, level level carries x_unit) — aligned by curve_label, and
                            # variable-level curves not covered by a level go to extra_var_curves as a fallback (deduplicated by signature across variables).
                            def _lbl(s):
                                return re.sub(r'\s+', '', str(s or '')).lower()

                            var_prof_by_lbl = {}
                            var_curve_by_lbl = {}
                            _vdps_var = v.get('degradation_profiles')
                            for d in (_vdps_var if isinstance(_vdps_var, list)
                                      else [_vdps_var] if isinstance(_vdps_var, dict) else []):
                                if isinstance(d, dict):
                                    var_prof_by_lbl[_lbl(d.get('curve_label'))] = d
                                    # 【2026-10-13 fix】when a profile has no label of its own, index it by its curves[].label
                                    # so the level-level merge can drill down to points/units (measured B084/B107: variable level holds only {curves,metric})
                                    for _c in (d.get('curves') or []):
                                        if isinstance(_c, dict):
                                            _k = _lbl(_c.get('curve_label') or _c.get('label'))
                                            if _k and _k not in var_curve_by_lbl:
                                                var_curve_by_lbl[_k] = (d, _c)
                            vdps = (lv.get('degradation_profiles') if has_levels
                                    else v.get('degradation_profiles')) or []
                            if isinstance(vdps, dict): vdps = [vdps]
                            # 【2026-10-13 fix】when a profile has no points of its own and the points are in curves[], split it into per-curve descriptors
                            # (same rule as the QX fallback; measured B107 org v1 variable lost points and labels on 12 curves)
                            _vdps2 = []
                            for _q in vdps:
                                if isinstance(_q, dict) and not (_q.get('points') or _q.get('xs')) and _q.get('curves'):
                                    _n = 0
                                    for _c in (_q.get('curves') or []):
                                        if isinstance(_c, dict):
                                            _cl = _c.get('curve_label') or _c.get('label')
                                            if not (_cl or not _q.get('curve_label')):
                                                continue  # do not split unlabeled sub-curves under a labeled profile (avoid over-deduplication)
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
                                    # 【2026-10-13 fix】when variable-level points live in curves[].points, drill down by label to take them,
                                    # otherwise a flat level-level profile overwrites the label and the points are lost (measured B084/B107/B109/B111, 44 curves)
                                    if not q.get('points') and not q.get('xs'):
                                        for _c in (src_v.get('curves') or []):
                                            if isinstance(_c, dict) and _lbl(_c.get('curve_label') or _c.get('label')) == lbl:
                                                if _c.get('points') or _c.get('xs'):
                                                    q['points'] = _c.get('points') or _c.get('xs')
                                                    q['xs'] = _c.get('xs') or _c.get('points')
                                                break
                                if not q.get('source_figure'):
                                    # 【2026-10-10 fix】figure-number fallback chain: variable-level source_figure → variable-level ref
                                    # → level-level ref (eaf_slag_bpa writes the level figure as ref='Fig.1'; the old version missed it)
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
                                # 【2026-10-13 fix】the signature uses the same ref/figure fallback chain as build_curve:
                                # the old version read only source_figure, so same-label curves from different figures (Fig-2/Fig-4) were wrongly deduplicated
                                sig = curve_key(q.get('source_figure') or q.get('ref') or q.get('figure'), lbl_n)
                                if lbl_n and sig in emitted_sigs:
                                    # 【2026-10-13 fix】on a same-signature collision, if the earlier node has no points while the later curve has them,
                                    # merge the points/units into the earlier node (measured B111 feal_mmt V4: a curve with points was swallowed by a point-less descriptor)
                                    _prev = emitted_nodes.get(sig)
                                    if _prev is not None and not _prev.get('xs') and (q.get('xs') or q.get('points')):
                                        _prev['xs'] = q.get('xs') or q.get('points')
                                        _prev['n_points'] = len(_prev['xs'])
                                        for f in ('x_unit', 'y_unit'):
                                            if not _prev.get(f) and q.get(f):
                                                _prev[f] = q[f]
                                    continue   # curves with an empty label do not take part in dedup (avoid false kills)
                                emitted_sigs.add(sig)
                                kid = f'{vid}::Q{qi+1}'
                                _cnode = build_curve(kid, pid, eid, q, vid=vid)
                                emitted_nodes[sig] = _cnode
                                N['curve'].append(_cnode)
                                E.append({'type': 'HAS_CURVE', 'from': vid, 'to': kid})
                                fig = fig_norm(str(q.get('source_figure') or ''))
                                if fig: E.append({'type': 'FROM_FIGURE', 'from': kid, 'to': 'FIG::%s::%s' % (pid, fig)})
                    # 【2026-10-09】variable-level curves not covered by a level → attached to the experiment node as a fallback (no curve lost)
                    def _explode_var_curve(d):
                        # 【2026-10-13 fix】when a variable-level profile has no points of its own and the points are in curves[], split into per-curve
                        # descriptors; otherwise the QX fallback node loses all points (measured B107 IJRET: all 48 QX curves had no xs)
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
                                continue   # empty labels take no part in dedup
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
                    # 【2026-10-15 fix】when an experiment-level profile has no points of its own and the points are in curves[] sub-curves, split into per-curve
                    # descriptors (the same rule as the variable-level/QX fallback; measured B070 early schema lost 6 curves, B073 two papers lost the points of 8 curves)
                    _dps2 = []
                    for _q in dps:
                        if isinstance(_q, dict) and not (_q.get('points') or _q.get('xs')) and _q.get('curves'):
                            _n = 0
                            for _c in (_q.get('curves') or []):
                                if isinstance(_c, dict):
                                    _cl = _c.get('curve_label') or _c.get('label')
                                    if not (_cl or not _q.get('curve_label')):
                                        continue  # do not split unlabeled sub-curves under a labeled profile (avoid over-deduplication)
                                    _qq = dict(_q)
                                    _qq.pop('curves', None)
                                    if _cl:
                                        _qq['curve_label'] = _cl
                                    for f in ('points', 'xs', 'x_unit', 'y_unit', 'metric', 'source_figure'):
                                        if _c.get(f) and not _qq.get(f):
                                            _qq[f] = _c[f]
                                    _dps2.append(_qq)
                                    _n += 1
                            if not _n:
                                _dps2.append(_q)
                        else:
                            _dps2.append(_q)
                    dps = _dps2
                    for qi, q in enumerate(dps):
                        if not isinstance(q, dict): continue
                        kid = f'{eid}::Q{qi+1}'
                        N['curve'].append(build_curve(kid, pid, eid, q))
                        E.append({'type': 'HAS_CURVE', 'from': eid, 'to': kid})
                        fig = fig_norm(str(q.get('source_figure') or ''))
                        if fig: E.append({'type': 'FROM_FIGURE', 'from': kid, 'to': 'FIG::%s::%s' % (pid, fig)})
            # contradiction ledger: mounted at paper level
            paper_exps = [n_['node_id'] for n_ in N['experiment'] if n_['paper_id'] == pid]
            EXP_FIELD_HINTS = ('removal', 'dosage', 'pH', 'time', 'temperature', 'conc',
                               'k =', 'k=', 'toc', 'cod', 'oxidant')
            for ki, it in enumerate(led.get('entries', [])):
                kid = f'{pid}::K{ki+1}'
                # Contradiction node = review marker: tells the reader this paper's extracted data contains entries needing manual review
                # 【2026-10-06 caliber change】category deprecated → severity(A/B/C/D/E) + severity_label + evidence_source
                #   + the two booleans cross_paper / caliber_variance (see references/contradiction_taxonomy.md)
                #   E = other (unclassifiable), also the fallback for "cannot grade / pending"; `unknown` is retired (normalized to E on sight)
                adopted=it.get('裁决/采纳值', it.get('adopted_value'))
                severity = str(it.get('severity') or it.get('严重度') or 'E').strip().upper()
                if severity not in SEV_LABEL:
                    severity = 'E'
                cross_paper = bool(it.get('cross_paper') or it.get('跨文献'))
                # 【2026-10-06 traceability】fld/fig_raw/trace: any contradiction must show at a glance "which paper · which field · which figure"
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
                    review_scope='data_quality',   # the review scope of this paper's extracted data
                    search_text=_join(fld, severity, SEV_LABEL[severity],
                                      it.get('evidence_source') or it.get('载体来源'),
                                      it.get('理由', it.get('adopt_reason')))))
                # 【2026-10-06 anchor normalization】three-layer contradiction anchoring, paper level 100% zero-orphan:
                #   DISPUTES        → Paper      mandatory (one per paper, zero orphans)
                #   FROM_EXPERIMENT → Experiment when the paper has a unique experiment node and the field is decidable (best effort)
                #   DISPUTED_IN     → Figure     when the figure number parses; figure nodes are scoped per paper
                #                                (the legacy 'FIG::Figure5' was globally shared → figure numbers leaked across papers)
                E.append({'type': 'DISPUTES', 'from': kid, 'to': pid})
                if fld and any(w in fld for w in EXP_FIELD_HINTS) and len(paper_exps) == 1:
                    E.append({'type': 'FROM_EXPERIMENT', 'from': kid, 'to': paper_exps[0]})
                fig = fig_norm(str(fig_raw or ''))
                if fig: E.append({'type': 'DISPUTED_IN', 'from': kid, 'to': 'FIG::%s::%s' % (pid, fig)})
    # deduplicate pollutant/figure sentinel nodes
    pols = {e['to']: e['pollutant'] for e in E if e['type'] == 'DEGRADES'}
    figs = {e['to'] for e in E if e['type'] == 'FROM_FIGURE'}
    pol_nodes, n_pol_hit = [], 0
    for t, n_ in pols.items():
        node = {'node_id': t, 'label': 'Pollutant', 'name': n_}
        r = match_pollutant(n_, ptbl)
        if r:
            node.update(to_entity_props(r))
            n_pol_hit += 1
        # 【2026-10-10 fix】Pollutant gets search_text — otherwise the Entity vector index idles forever
        # (measured: 27 pollutant nodes with search_text 0/27, so pollutant semantic search had nothing to recall)
        node['search_text'] = _join(n_, node.get('formula'), node.get('pubchem_title'), node.get('cas'))
        pol_nodes.append(node)
    # 【2026-10-06】figure nodes are scoped per paper (node_id = 'FIG::<paper_id>::FigureN') and carry paper_id/name:
    # so "which paper's which figure did this contradiction/curve come from" is directly readable. The legacy 'FIG::Figure5' was a cross-paper shared node (leaking across papers).
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
    print('Pollutant entities %d, of which %d carry alignment properties (CID/formula/molecular weight)' % (len(pols), n_pol_hit))
    print('next, import: python scripts/step11_neo4j_import.py "%s"' % outd)

if __name__ == '__main__':
    main()
