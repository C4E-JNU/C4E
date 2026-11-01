# -*- coding: utf-8 -*-
"""step9_numeric_normalize.py — Step 8: numeric standardization and unit unification

Division of labour (fixed 2026-10-07; see references/step8_unit_normalization.md):
  · mechanical first pass: this script parses the numeric fields of 06_schema into {value, unit, raw} drafts (deterministic, reproducible);
  · semantic finalization: hard strings (conditional clauses stealing the value, digits in formulas, several calibers, missing units, conversions) go to the
    `fenton-normalizer` subagent for a per-entry decision (value/unit/source text/basis);
  · validation and persistence: this script merges the returns and validates each item (key name, unit whitelist, structural consistency, no bare strings) before writing 06b.

Three usage modes:
  python step9_numeric_normalize.py <paper folder|corpus root> --prepare
        → produces <dm>/06b_numeric_tasks.json (pending list + mechanical draft) for fenton-normalizer
  python step9_numeric_normalize.py <paper folder|corpus root> --from-agent <return.json>
        → validate + merge + write 06b_numeric_schema.json (+ stats + review)
  python step9_numeric_normalize.py <paper folder|corpus root>
        → pure mechanical persistence (fallback / fast path; also produces tasks for review)

Target fields **must carry a unit**: all three keys `{value, unit, raw}` present; raw is preserved verbatim (including units, ±, >, ranges),
downstream step10 flattens it into `<field>` / `<field>_unit` / `<field>_raw` for the graph.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ────────────────────────── unit normalization (the single authoritative table) ──────────────────────────
# (unit regex, normalized unit, regex flags); flags default to re.I, and a third value of 0 means case-sensitive
UNIT_RULES = [
    (r'at\s*%|atom\s*%', 'At%'),
    (r'mg\s*/?\s*L|mgl-1|mg·L-1', 'mg/L'), (r'g\s*/?\s*L|gl-1|g·L-1', 'g/L'),
    (r'ug\s*/?\s*L|μg\s*/?\s*L|μgl-1', 'μg/L'),
    (r'\bm\s*M\b|mmol\s*/?L|mmol·l-1', 'mM'),
    (r'\bµm\b|\bum\b|μmol\s*/?L', 'μM'),
    (r'\bmol\s*/?L\b', 'M'),
    (r'(?<![A-Za-z0-9])M(?![A-Za-z0-9])', 'M', 0),
    (r'wt\s*%', 'wt%'), (r'vol\s*%', 'vol%'),
    (r'mg\s*/?\s*kg', 'mg/kg'), (r'g\s*/?\s*kg', 'g/kg'),
    (r'mg\s*g-1|mg\s*/?\s*g(?![a-z])', 'mg/g'), (r'm2\s*/?\s*g|m2g-1', 'm²/g'),
    (r'cm3\s*/?\s*g|cc/g|cm3g-1', 'cm³/g'),
    (r'\bppm\b', 'mg/L'),                                  # ppm treated as mg/L (no density check; annotated)
    # "degree sign + C" or "C after a digit/space and not followed by a letter" (the old `°?\s*c\b` read the 'c' in 'cm-1' as °C)
    (r'°C|(?<=[\d\s])[cC](?![A-Za-z])|摄氏度', '°C'),
    (r'开尔文|\bK\b', 'K'),
    (r'\bmin\b|分钟', 'min'), (r'(?<![A-Za-z0-9])h(?![A-Za-z0-9])|hour', 'h', 0),
    (r'(?<![A-Za-z0-9])s(?![A-Za-z0-9])|秒', 's', 0),
    (r'min-1|min⁻¹|/min', 'min⁻¹'), (r'h-1|h⁻¹|/h', 'h⁻¹'), (r's-1|s⁻¹|/s', 's⁻¹'),
    (r'°C\s*/\s*min|℃/min', '°C/min'),
    (r'cm-1|cm⁻¹', 'cm⁻¹'), (r'\brpm\b|转\s*/?\s*分', 'rpm'),
    (r'Ω|ohm|欧', 'Ω'), (r'\bmV\b', 'mV'), (r'\bkV\b', 'kV'), (r'\bV\b', 'V'),
    (r'%|百分比', '%'), (r'\bmg\b(?!/)', 'mg'), (r'\bg\b(?!/)', 'g', 0),
    (r'\bmL\b(?!/)|毫升', 'mL'), (r'\bL\b(?!/)', 'L'), (r'ul\b|μL\b|微升', 'μL'),
    (r'nm\b', 'nm'), (r'μm\b|µm\b', 'μm'),
    (r'ev\b', 'eV'), (r'emu\s*/?\s*g', 'emu/g'),
]
# character normalization before matching (matching only; raw is preserved): superscripts and full-width symbols
_SUPMAP = str.maketrans({'²': '2', '³': '3', '¹': '1', '⁻': '-', '·': '', 'μ': 'u', 'µ': 'u', '℃': '°C'})
# unit whitelist (hard validation before persistence: unit must be in this table or None)
UNIT_CANON = {
    'At%', 'wt%', 'vol%', 'mg/L', 'g/L', 'μg/L', 'mg/kg', 'g/kg', 'mM', 'M', 'μM',
    'mg/g', 'm²/g', 'cm³/g', 'mg', 'g', 'mL', 'L', 'μL', 'kg',
    '°C', 'K', 'min', 'h', 's', 'd', 'min⁻¹', 'h⁻¹', 's⁻¹', 'cm⁻¹', 'eV',
    'emu/g', 'nm', 'μm', 'rpm', 'Ω', 'mV', 'kV', 'V', 'mA', 'W', 'Hz', '%', '°C/min',
}
# field → canonical unit (the schema layer must use it; mismatches are converted via SCALE, and flagged with a note when no conversion exists)
CANON_UNIT = {
    'initial_pollutant_conc': 'mg/L', 'catalyst_dosage': 'g/L', 'oxidant_concentration': 'mM',
    'temperature': '°C', 'calcination_temp': '°C',
    'reaction_time': 'min', 'calcination_time': 'min', 'calcination_heating_rate': '°C/min',
    'rate_constant_k': 'min⁻¹',
    'specific_surface_area': 'm²/g', 'pore_volume': 'cm³/g', 'pore_size': 'nm',
    'crystallite_size': 'nm', 'particle_size': 'nm', 'lattice_spacing_nm': 'nm',
    'band_gap': 'eV', 'saturation_magnetization': 'emu/g', 'eis_Rct': 'Ω',
    'zeta_potential': 'mV', 'adsorption_capacity': 'mg/g', 'metal_content': 'wt%',
}
# known conversions (from, to) → factor; K→°C is handled specially (subtract 273.15)
SCALE = {
    ('h', 'min'): 60.0, ('s', 'min'): 1 / 60.0, ('d', 'min'): 1440.0,
    ('min', 'h'): 1 / 60.0, ('h', 's'): 3600.0, ('s', 'h'): 1 / 3600.0,
    ('s⁻¹', 'min⁻¹'): 60.0, ('h⁻¹', 'min⁻¹'): 1 / 60.0,
    ('μg/L', 'mg/L'): 1e-3, ('g/L', 'mg/L'): 1e3, ('mg/L', 'g/L'): 1e-3,
    ('μM', 'mM'): 1e-3, ('M', 'mM'): 1e3,
    ('wt%', 'At%'): None,
}

NUM = r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?'
NUM_G = r'([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)'
# multi-value: the label must start with a letter or a CJK character (the old version allowed numeric labels and split "99.5% (60 min)" wrongly)
MULTI = re.compile(r'([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff0-9\-]*)\s*' + NUM_G + r'\s*%')
UNC = re.compile(r'[±±]\s*' + NUM_G)
QUALIFIER = re.compile(r'([><≥≤])\s*(?=' + NUM + r')')
# range: both sides must be unsigned numbers (the old signed NUM read the "-20" in "17-20" as negative)
UINT_RANGE = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)\s*[-–~]\s*(\d+(?:\.\d+)?)(?![\d.])')

# field registry (key names identical, character for character, to the Step 7 45 / 41 + stability 6 lists)
CATALYST_NUM_KEYS = [
    'metal_content', 'calcination_temp', 'calcination_time', 'calcination_heating_rate',
    'lattice_a', 'lattice_c', 'crystallite_size', 'lattice_spacing_nm', 'particle_size',
    'specific_surface_area', 'pore_volume', 'pore_size', 'adsorption_capacity',
    'pH_pzc', 'zeta_potential', 'raman_ID_IG', 'band_gap', 'eis_Rct',
    'saturation_magnetization', 'char_PL_intensity',
    'bet_surface_area', 'surface_area', 'metal_loading', 'dosage', 'mass', 'pore_diameter',
]
EXPERIMENT_NUM_KEYS = [
    'oxidant_concentration', 'oxidant_dosage', 'initial_pollutant_conc', 'catalyst_dosage',
    'solution_volume', 'initial_pH', 'temperature', 'reaction_time',
    'oxidant_pollutant_molar_ratio', 'catalyst_pollutant_mass_ratio',
    'removal_efficiency', 'decolorization_efficiency', 'rate_constant_k', 'rate_constant_R2',
    'toc_removal', 'cod_removal', 'adsorption_contribution',
    'flow_rate', 'light_intensity', 'mineralization_rate', 'mineralization_efficiency',
]
STABILITY_NUM_KEYS = [
    'cycle_count', 'initial_efficiency', 'final_efficiency', 'efficiency_retention',
    'metal_leaching_conc',
]
VARIABLE_NUM_KEYS = ['metric_value', 'new_value', 'old_value', 'optimum_value']
# 【2026-10-07 v2】numeric keys inside variable level items (variable_experiments[].levels[]):
#   one level = one id (C1.E1.V1.L2); level_value and that level's metric_value sit at the same level and correspond one-to-one
LEVEL_NUM_KEYS = ['level_value', 'metric_value']
# note: x_unit / y_unit are **the unit itself** and never enter the numeric registry (the old version registered them and parsed "min" as a number)

UNITLESS = {'initial_pH', 'rate_constant_R2', 'ph', 'raman_ID_IG', 'cycle_count',
            'oxidant_pollutant_molar_ratio', 'catalyst_pollutant_mass_ratio'}
PERCENT = {'removal_efficiency', 'decolorization_efficiency', 'toc_removal', 'cod_removal',
           'mineralization_rate', 'metal_content', 'metal_loading', 'wt',
           'adsorption_contribution', 'efficiency_retention', 'initial_efficiency',
           'final_efficiency'}
# field → preferred unit (when a string mixes a conditional clause, pick the semantically matching value first)
PREFER_UNITS = {
    'metal_content': ['wt%', 'At%', '%'], 'metal_loading': ['wt%', '%'],
    'specific_surface_area': ['m²/g'], 'pore_volume': ['cm³/g'], 'pore_size': ['nm'],
    'crystallite_size': ['nm'], 'lattice_spacing_nm': ['nm'], 'particle_size': ['nm'],
    'band_gap': ['eV'], 'saturation_magnetization': ['emu/g'], 'eis_Rct': ['Ω'],
    'zeta_potential': ['mV'], 'calcination_temp': ['°C'], 'calcination_time': ['min', 'h'],
    'adsorption_capacity': ['mg/g', '%'],
    'oxidant_concentration': ['mg/L', 'mM', 'g/L', 'μM'],
    'oxidant_dosage': ['μL', 'mL', 'mg', 'mg/L', 'g/L', 'mM'],
    'initial_pollutant_conc': ['mg/L', 'mM', 'μM'], 'catalyst_dosage': ['mg', 'mg/L', 'g/L'],
    'solution_volume': ['mL', 'L'], 'reaction_time': ['min', 'h'], 'temperature': ['°C'],
    'removal_efficiency': ['%'], 'decolorization_efficiency': ['%'],
    'toc_removal': ['%'], 'cod_removal': ['%'], 'adsorption_contribution': ['%'],
    'rate_constant_k': ['min⁻¹'], 'light_intensity': ['W', 'mW'],
}
COND_UNITS = {'°C', 'K', 'min', 'h', 's', 'd'}
COND_FIELDS = {'temperature', 'calcination_temp', 'reaction_time', 'calcination_time'}
_MULTI_STOP = ('图读', '对照', '估算', '或', 'vs', '另')

KEY_REGISTRY = {
    'catalyst': CATALYST_NUM_KEYS, 'experiment': EXPERIMENT_NUM_KEYS,
    'stability': STABILITY_NUM_KEYS, 'variable': VARIABLE_NUM_KEYS,
    'level': LEVEL_NUM_KEYS,
}


def _ut(s):
    return str(s).translate(_SUPMAP)


def _can_convert(u, target):
    """Whether u can be converted to target (used for preferred-unit matching: K→°C, h→min, s⁻¹→min⁻¹)."""
    if u == target:
        return True
    if u == 'K' and target == '°C':
        return True
    return (u, target) in SCALE and SCALE[(u, target)] is not None


def _near_unit(t, end, field=''):
    """Take the nearest unit after the number: within a 24 / 60 character window, prefer the earliest hit and, at the same position, the longest match;
    when nothing matches, fall back to the field semantics (flagged inferred)."""
    for span in (24, 60):
        seg = t[end:end + span]
        best = None
        for rule in UNIT_RULES:
            pat, u = rule[0], rule[1]
            fl = rule[2] if len(rule) > 2 else re.I
            mm = re.search(pat, seg, fl)
            if not mm:
                continue
            score = (mm.start(), -len(mm.group()))       # longer match wins at the same start ("min-1" beats "min")
            if best is None or score < best[0]:
                best = (score, u)
        if best:
            return best[1], False
    p = PREFER_UNITS.get(field) or []
    return (p[0], True) if p else (None, False)


def parse_value(raw, field):
    """Mechanical first pass: raw → {value, unit, raw, ...flags}; when unparsable → unparsed (evidence is never lost)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        s = str(raw)
        u = '%' if field in PERCENT else (None if field in UNITLESS else
                                          CANON_UNIT.get(field))
        return {'value': float(raw), 'unit': u, 'raw': s, 'multi': False,
                'unit_inferred': bool(u and field not in PERCENT)}
    s = str(raw).strip()
    if not s:
        return None
    # multi-value: short strings only, and exclude second items such as "figure-read/control" that are not the primary value
    ms = MULTI.findall(s)
    if len(ms) >= 2 and len(s) <= 40 and not any(w in s for w in _MULTI_STOP):
        vals = []
        for m in ms:
            try:
                vals.append(float(str(m[1])))
            except Exception:
                return {'value': None, 'unit': None, 'raw': s, 'multi': False, 'unparsed': True}
        return {'value': vals, 'unit': '%', 'labels': [m[0] for m in ms], 'raw': s, 'multi': True}
    t = _ut(s)
    cands = []
    for mm in re.finditer(NUM, t):
        st = mm.start()
        g = mm.group()
        prev = t[st - 1] if st > 0 else ''
        if g[0] in '+-':
            if prev.isdigit() or prev in '-–~.':          # range upper bound ("17-20") → treat as unsigned
                g, st = g.lstrip('+-'), st + 1
            elif prev in ('±', '±'):
                continue                                  # after ± comes an uncertainty, captured separately by UNC
            elif prev.isalpha():
                continue
        elif prev.isalpha():
            continue                                  # digits inside formulas/identifiers: N2 / Fe3O4 / R2 / H2O2
        elif prev in ('±', '±'):
            continue
        pre = t[max(0, st - 10):st].lower()
        if re.search(r'fig|图|scheme|tab|ref|eq|no\.', pre):
            continue
        try:
            v = float(g)
        except ValueError:
            continue
        u, inferred = _near_unit(t, mm.end(), field)
        cands.append((st, v, u, inferred))
    if not cands:
        return {'value': None, 'unit': None, 'raw': s, 'multi': False, 'unparsed': True}
    prefer = PREFER_UNITS.get(field)
    chosen = None
    if prefer:
        for _, v, u, inf in cands:
            if u and any(_can_convert(u, p) for p in prefer):   # a convertible unit is present (K→°C / h→min)
                chosen = (v, u, inf)
                break
    if chosen is None and prefer and cands[0][2] not in COND_UNITS:
        chosen = (cands[0][1], prefer[0], True)
    if chosen is None and prefer is None:
        for _, v, u, inf in cands:
            if u not in COND_UNITS or field in COND_FIELDS:
                chosen = (v, u, inf)
                break
    if chosen is None:
        # the field should be %/nm etc. while the string holds only condition-type values → do not force a fill
        return {'value': None, 'unit': None, 'raw': s, 'multi': False, 'unparsed': True}
    val, unit, inferred = chosen
    pick_val = val                                        # the selected value before conversion (for conflict detection)
    extra = {}
    # convert the unit to the field's canonical unit (convert when possible; otherwise keep the original unit and add a note)
    canon = CANON_UNIT.get(field)
    if unit and canon and unit != canon:
        if unit == 'K' and canon == '°C':
            val, extra['raw_unit'], extra['converted'] = val - 273.15, unit, True
            unit = canon
        elif (unit, canon) in SCALE and SCALE[(unit, canon)]:
            val, extra['raw_unit'], extra['converted'] = val * SCALE[(unit, canon)], unit, True
            unit = canon
        else:
            extra['note'] = 'unit_not_canonical:%s→%s' % (unit, canon)
    if field in UNITLESS:
        unit = None
    if unit is None and field in PERCENT:
        unit, inferred = '%', True
    if inferred and unit:
        extra['unit_inferred'] = True
    conflicts = [(v_, u_) for _, v_, u_, _ in cands
                 if u_ == unit and u_ is not None
                 and abs(v_ - pick_val) > 0.05 * max(abs(pick_val), 1e-9)]
    if (cands and cands[0][1] != pick_val) or conflicts:
        extra['ambiguous'] = True
        extra['candidates'] = [{'value': v_, 'unit': u_} for _, v_, u_, _ in cands]
    m_u = UNC.search(t)
    if m_u:
        try:
            extra['uncertainty'] = abs(float(m_u.group(1)))
        except ValueError:
            pass
    m_q = QUALIFIER.search(t)
    if m_q:
        extra['qualifier'] = m_q.group(1)
    m_r = UINT_RANGE.search(t)
    if m_r:
        try:
            extra['range'] = [float(m_r.group(1)), float(m_r.group(2))]
        except ValueError:
            pass
    out = {'value': val, 'unit': unit, 'raw': s, 'multi': False}
    out.update(extra)
    return out


def _experiments_of(schema, cat):
    """Tolerate three formats of 06 experiment nodes (top-level experiments finalized 2026-10-23):
    new — top-level experiments[] grouped by catalyst_id (tid kept identical to the legacy nested caliber);
    intermediate — top-level variable_experiments[] (the daytime key name, fallback);
    legacy — catalysts[].experiments[] nested fallback."""
    top = schema.get('experiments') or schema.get('variable_experiments')
    if top:
        cid = cat.get('catalyst_id')
        return [e for e in top if isinstance(e, dict) and e.get('catalyst_id') == cid]
    return cat.get('experiments', []) or []


def _iter_targets(schema):
    """(tid, container dict, key name, node type) — tid looks like C1.pore_size / C1.E1.removal_efficiency."""
    seen = set()
    for ci, cat in enumerate(schema.get('catalysts', []) or []):
        if not isinstance(cat, dict):
            continue
        cn = f'C{ci + 1}'
        for key in CATALYST_NUM_KEYS:
            for cont in (cat.get('properties'), cat):
                if isinstance(cont, dict) and key in cont:
                    tid = f'{cn}.{key}'
                    if tid not in seen:
                        seen.add(tid)
                        yield tid, cont, key, 'catalyst'
                    break
        for ei, e in enumerate(_experiments_of(schema, cat)):
            if not isinstance(e, dict):
                continue
            en = f'{cn}.E{ei + 1}'
            for key in EXPERIMENT_NUM_KEYS:
                for cont in (e.get('properties'), e):
                    if isinstance(cont, dict) and key in cont:
                        tid = f'{en}.{key}'
                        if tid not in seen:
                            seen.add(tid)
                            yield tid, cont, key, 'experiment'
                        break
            stab = e.get('stability')
            if isinstance(stab, dict):
                for key in STABILITY_NUM_KEYS:
                    if key in stab:
                        tid = f'{en}.stability.{key}'
                        if tid not in seen:
                            seen.add(tid)
                            yield tid, stab, key, 'stability'
            for vi, v in enumerate(e.get('variable_experiments', []) or []):
                if not isinstance(v, dict):
                    continue
                vn = f'{en}.V{vi + 1}'
                for key in VARIABLE_NUM_KEYS:
                    if key in v:
                        tid = f'{vn}.{key}'
                        if tid not in seen:
                            seen.add(tid)
                            yield tid, v, key, 'variable'
                # 【2026-10-07 v2】level items: one id per level ({vn}.L{k}); level_value and metric_value sit at the same level
                for li, lv in enumerate(v.get('levels', []) or []):
                    if not isinstance(lv, dict):
                        continue
                    lvn = f'{vn}.L{li + 1}'
                    for key in LEVEL_NUM_KEYS:
                        if key in lv:
                            tid = f'{lvn}.{key}'
                            if tid not in seen:
                                seen.add(tid)
                                yield tid, lv, key, 'level'


def build_draft(schema):
    """Mechanical first pass over all numeric fields → (draft_schema, tasks). The input is never modified."""
    draft = json.loads(json.dumps(schema, ensure_ascii=False))
    tasks = []
    idx = 0
    tids = [t for t in _iter_targets(schema)]
    for tid, cont, key, typ in tids:
        raw = cont.get(key)
        if isinstance(raw, dict):
            continue                                # already standardized
        idx += 1
        d = parse_value(raw, key)
        if d is None:
            continue
        tasks.append({'id': tid, 'field': key, 'node_type': typ, 'raw': d.get('raw'),
                      'draft': {k: v for k, v in d.items() if k != 'raw'},
                      'flags': sorted(k for k in ('unparsed', 'ambiguous', 'unit_inferred',
                                                  'converted', 'multi') if d.get(k))})
    # write the draft back by tid (reusing the same traversal order guarantees one-to-one correspondence)
    draft_iter = list(_iter_targets(draft))
    src_iter = list(_iter_targets(schema))
    for (tid_d, cont_d, key_d, _), (tid_s, cont_s, key_s, _) in zip(draft_iter, src_iter):
        if tid_d != tid_s:
            continue
        d = parse_value(cont_s.get(key_s), key_s)
        if d is not None:
            cont_d[key_d] = d
    order = {'unparsed': 0, 'ambiguous': 1, 'unit_inferred': 2}
    tasks.sort(key=lambda x: (min([order.get(f, 9) for f in x['flags']] or [9]), x['id']))
    return draft, tasks


def _load_agent_fields(path):
    """Subagent return → [{id, value, unit, raw, basis}]; tolerates fields / items / a bare list."""
    obj = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(obj, dict):
        rows = obj.get('fields') or obj.get('items') or obj.get('data') or []
        if not rows and isinstance(obj.get('papers'), list):
            out = []
            for p in obj['papers']:
                out += (p.get('fields') or p.get('items') or [])
            return out
        return rows
    return obj if isinstance(obj, list) else []


def apply_agent(schema, tasks, items):
    """Merge the subagent return by tid → (schema, report). Validates: id exists / key legal / unit whitelisted / value type legal."""
    tmap = {t['id']: t for t in tasks}
    idx = {tid: (cont, key, typ) for tid, cont, key, typ in _iter_targets(schema)}
    rep = {'accepted': 0, 'by_agent': 0, 'unknown_id': [], 'bad_field': [], 'bad_unit': [],
           'bad_value': [], 'missing_value': [], 'not_canonical': []}
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = str(it.get('id') or '').strip()
        t = tmap.get(tid)
        if t is None:
            rep['unknown_id'].append(tid)
            continue
        cont, key, typ = idx[tid]
        fld = str(it.get('field') or t['field'])
        if fld != key or fld not in KEY_REGISTRY.get(typ, []):
            rep['bad_field'].append({'id': tid, 'field': fld})
            continue
        unit = it.get('unit')
        if isinstance(unit, str):
            unit = unit.strip() or None
        if unit is not None and unit not in UNIT_CANON:
            rep['bad_unit'].append({'id': tid, 'unit': unit})
            continue
        val = it.get('value')
        if isinstance(val, bool):
            ok_type = False
        elif val is None or isinstance(val, (int, float)):
            ok_type = True
        elif isinstance(val, list):
            ok_type = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val)
        else:
            ok_type = False
        if not ok_type:
            rep['bad_value'].append({'id': tid, 'value': val})
            continue
        if val is None and not it.get('unparsed'):
            rep['missing_value'].append(tid)
        src_raw = cont.get(key)                    # 【2026-10-07】raw follows the source string (the LLM may not rewrite the source text)
        if isinstance(src_raw, dict):
            src_raw = src_raw.get('raw')
        raw = src_raw if src_raw is not None else it.get('raw')
        triple = {'value': val, 'unit': unit, 'raw': str(raw) if raw is not None else None,
                  'multi': bool(isinstance(val, list))}
        for k in ('range', 'uncertainty', 'qualifier', 'labels', 'note', 'basis',
                  'raw_unit', 'converted', 'unit_inferred'):
            if it.get(k) not in (None, '', []):
                triple[k] = it[k]
        if val is None:
            triple['unparsed'] = True
        if unit is None and fld in PERCENT:
            triple['unit'] = '%'
        triple['source'] = 'agent'
        canon = CANON_UNIT.get(fld)
        if unit and canon and unit != canon:
            rep['not_canonical'].append({'id': tid, 'unit': unit, 'canon': canon})
        cont[key] = triple
        rep['accepted'] += 1
        rep['by_agent'] += 1
    return schema, rep


def _structure_sig(schema):
    cats = schema.get('catalysts', []) or []
    exp_containers = [e for c in cats if isinstance(c, dict)
                      for e in _experiments_of(schema, c) if isinstance(e, dict)]
    exps = len(exp_containers)
    vars_ = sum(len(e.get('variable_experiments', []) or []) for e in exp_containers)
    # 【2026-10-07 v2】the number of level items also enters the structural signature (a lost level = structural mismatch, which must be blocked)
    lvls = sum(len(v.get('levels') or []) for e in exp_containers
               for v in (e.get('variable_experiments', []) or []) if isinstance(v, dict))
    return {'catalysts': len(cats), 'experiments': exps, 'variables': vars_, 'levels': lvls}


def verify_out(out_schema, src_schema, unit_check=True):
    """Hard validation before persistence: structural consistency + no bare strings + unit whitelist + all three keys present."""
    bad = {'structure': None, 'bare': [], 'bad_unit': [], 'missing_raw': []}
    if _structure_sig(out_schema) != _structure_sig(src_schema):
        bad['structure'] = {'src': _structure_sig(src_schema), 'out': _structure_sig(out_schema)}
    for tid, cont, key, _ in _iter_targets(out_schema):
        v = cont.get(key)
        if isinstance(v, dict):
            if 'value' not in v:
                bad['bare'].append(tid)
            if v.get('raw') is None and v.get('value') is not None:
                bad['missing_raw'].append(tid)
            u = v.get('unit')
            if unit_check and u is not None and u not in UNIT_CANON:
                bad['bad_unit'].append({'id': tid, 'unit': u})
        elif v not in (None, ''):
            bad['bare'].append(tid)
    return bad


def collect_papers(target):
    tp = Path(target)
    if (tp / 'datamining' / '06_schema.json').exists():
        return [tp]
    papers = []
    if tp.is_dir():
        for d in sorted(tp.iterdir()):
            if (d / 'datamining' / '06_schema.json').exists():
                papers.append(d)
            elif d.is_dir():
                papers += [dd for dd in sorted(d.iterdir())
                           if (dd / 'datamining' / '06_schema.json').exists()]
    return papers


def do_prepare(pdir):
    dm = Path(pdir) / 'datamining'
    schema = json.loads((dm / '06_schema.json').read_text(encoding='utf-8'))
    draft, tasks = build_draft(schema)
    (dm / '06b_numeric_draft.json').write_text(
        json.dumps(draft, ensure_ascii=False, indent=1), encoding='utf-8')
    payload = {'paper': Path(pdir).name, 'generated': datetime.now().isoformat(timespec='seconds'),
               'schema_file': str(dm / '06_schema.json'),
               'unit_canon': sorted(UNIT_CANON), 'canon_unit': CANON_UNIT,
               'stats': {'fields': len(tasks),
                         'flagged': sum(1 for t in tasks if t['flags'])},
               'tasks': tasks}
    (dm / '06b_numeric_tasks.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'paper': Path(pdir).name, 'fields': len(tasks), 'flagged': payload['stats']['flagged']}


def do_finalize(pdir, agent_file=None):
    dm = Path(pdir) / 'datamining'
    src = json.loads((dm / '06_schema.json').read_text(encoding='utf-8'))
    tasks_p = dm / '06b_numeric_tasks.json'
    tasks = []
    if tasks_p.exists():
        tasks = json.loads(tasks_p.read_text(encoding='utf-8')).get('tasks', [])
    if agent_file:
        draft, tasks_mech = build_draft(src)
        if not tasks:
            tasks = tasks_mech
        out, rep = apply_agent(draft, tasks, _load_agent_fields(agent_file))
    else:
        out, tasks_mech = build_draft(src)
        tasks = tasks_mech
        rep = {'accepted': len(tasks), 'by_agent': 0, 'unknown_id': [], 'bad_field': [],
               'bad_unit': [], 'bad_value': [], 'missing_value': [], 'not_canonical': []}
        # the mechanical path also records source
        for tid, cont, key, _ in _iter_targets(out):
            if isinstance(cont.get(key), dict):
                cont[key].setdefault('source', 'mechanical')
    bad = verify_out(out, src)
    n = {'cat': 0, 'exp': 0, 'var': 0}
    flags = {}
    for tid, cont, key, typ in _iter_targets(out):
        v = cont.get(key)
        if not isinstance(v, dict):
            continue
        n[{'catalyst': 'cat', 'experiment': 'exp', 'stability': 'exp',
           'variable': 'var', 'level': 'var'}[typ]] += 1
        for f in ('unparsed', 'ambiguous', 'unit_inferred', 'converted', 'multi'):
            if v.get(f):
                flags[f] = flags.get(f, 0) + 1
    out_p = dm / '06b_numeric_schema.json'
    out_p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    stat = {'paper': Path(pdir).name, 'normalized': n,
            'source_counts': {'agent': rep.get('by_agent', 0),
                              'mechanical': max(0, sum(n.values()) - rep.get('by_agent', 0))},
            'flags': flags,
            'issues': {k: v for k, v in rep.items()
                       if k not in ('accepted', 'by_agent') and v},
            'verify': bad, 'output': str(out_p)}
    (dm / '06b_numeric_stats.json').write_text(
        json.dumps(stat, ensure_ascii=False, indent=1), encoding='utf-8')
    # review list handed to the final inspection
    review = {'unparsed': [], 'ambiguous': [], 'unit_not_canonical': [], 'unit_inferred': []}
    for tid, cont, key, _ in _iter_targets(out):
        v = cont.get(key)
        if not isinstance(v, dict):
            continue
        if v.get('unparsed'):
            review['unparsed'].append({'id': tid, 'raw': v.get('raw')})
        if v.get('ambiguous'):
            review['ambiguous'].append({'id': tid, 'raw': v.get('raw'),
                                        'chosen': v.get('value'), 'candidates': v.get('candidates')})
        if isinstance(v.get('note'), str) and v['note'].startswith('unit_not_canonical'):
            review['unit_not_canonical'].append({'id': tid, 'note': v['note']})
        if v.get('unit_inferred'):
            review['unit_inferred'].append({'id': tid, 'unit': v.get('unit'),
                                            'raw': v.get('raw')})
    (dm / '06b_numeric_review.json').write_text(
        json.dumps(review, ensure_ascii=False, indent=1), encoding='utf-8')
    ok = (not bad['structure']) and (not bad['bare']) and (not bad['bad_unit']) \
        and not rep.get('bad_unit') and not rep.get('bad_field') and not rep.get('unknown_id')
    return {'paper': Path(pdir).name, 'counts': n, 'flags': flags,
            'issues': {k: (v if not isinstance(v, list) else len(v))
                       for k, v in stat['issues'].items()},
            'gate': 'PASS' if ok else 'FAIL', 'output': str(out_p)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('--prepare', action='store_true', help='only produce the pending list 06b_numeric_tasks.json')
    ap.add_argument('--from-agent', default=None, metavar='JSON',
                    help='merge the fenton-normalizer return and write 06b_numeric_schema.json')
    a = ap.parse_args()
    papers = collect_papers(a.target)
    if not papers:
        print('FAIL no paper directory containing datamining/06_schema.json: %s' % a.target)
        return 1
    if a.prepare:
        for p in papers:
            print('[prepare]', do_prepare(p))
        return 0
    failed = 0
    for p in papers:
        r = do_finalize(p, a.from_agent)
        print('[%s]' % r['gate'], r['paper'], r['counts'], 'flags=%s' % r['flags'],
              'issues=%s' % r['issues'] if r['issues'] else '')
        if r['gate'] != 'PASS':
            failed += 1
            print('      FAIL validation: ', json.dumps(r, ensure_ascii=False)[:600])
    print('done: %d papers, %d FAIL' % (len(papers), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
