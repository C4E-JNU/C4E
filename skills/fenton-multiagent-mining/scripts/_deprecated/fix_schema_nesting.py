# -*- coding: utf-8 -*-
"""fix_schema_nesting.py — 步骤⑦容错：把填充智能体产出的非标准 schema 结构归一到标准树
处理两类常见偏差：
1. 'tree' 包装层（tree.catalysts/...）→ 提升到顶层
2. 实验/变量放在顶层数组（experiments[]、variable_experiments[]）→ 按 catalyst_id/exp_id 嵌回
   catalysts[].experiments[].variable_experiments[]
催化剂键名归一：id→catalyst_id、name→catalyst_name；实验键名归一：exp_id→experiment_id
用法：python fix_schema_nesting.py <批次根目录>
"""
import json
import os
import sys


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def fix_schema(s):
    changed = []
    if isinstance(s.get("tree"), dict):
        tree = s.pop("tree")
        fs = s.get("fill_stats") or tree.get("fill_stats")
        s = {**tree, **{k: v for k, v in s.items() if k != "fill_stats"}}
        if fs:
            s["fill_stats"] = fs
        changed.append("unwrap tree")

    cats = s.get("catalysts", [])
    for c in cats:
        if "id" in c and "catalyst_id" not in c:
            c["catalyst_id"] = c.pop("id")
            changed.append(f"id→catalyst_id:{c.get('catalyst_id')}")
        if "node_id" in c and "catalyst_id" not in c:
            c["catalyst_id"] = c.pop("node_id")
            changed.append(f"node_id→catalyst_id:{c.get('catalyst_id')}")
        if "name" in c and "catalyst_name" not in c:
            c["catalyst_name"] = c.pop("name")
        c.setdefault("experiments", [])
        c.setdefault("other", {})
    idx = {str(c.get("catalyst_id")).strip(): c for c in cats if c.get("catalyst_id")}

    top_exps = s.pop("experiments", None) or []
    for e in top_exps:
        eid = e.pop("exp_id", None) or e.pop("node_id", None) or e.get("experiment_id")
        if eid:
            e["experiment_id"] = eid
        props = e.get("properties", {}) or {}
        if "pollutant_name" not in e and props.get("pollutant_name"):
            e["pollutant_name"] = props["pollutant_name"]
        e.setdefault("degradation_profiles", None)
        e.setdefault("variable_experiments", [])
        e.setdefault("stability", {})
        if not isinstance(e.get("other"), dict):
            e["other"] = {}
        e.setdefault("qc", {})
        host = idx.get(str(e.get("catalyst_id", "")).strip())
        if host is None and len(cats) == 1:
            host = cats[0]
        if host is not None:
            host["experiments"].append(e)
            changed.append(f"嵌回实验 {eid}→{host.get('catalyst_id')}")
        else:
            print(f"  WARN 孤儿实验 {eid} (catalyst_id={e.get('catalyst_id')}) 挂到首个催化剂")
            e["other"]["nesting_note"] = f"原 catalyst_id={e.get('catalyst_id')} 未能匹配催化剂节点"
            if cats:
                cats[0]["experiments"].append(e)
                changed.append(f"孤儿实验 {eid}→{cats[0].get('catalyst_id')}(兜底)")
            else:
                s.setdefault("experiments", []).append(e)
    if top_exps:
        changed.append(f"处理顶层实验 {len(top_exps)} 个")

    # experiments_by_catalyst 包装层 → 解包嵌回（list[{catalyst_id, experiments}] 或 {cid: [exps]}）
    ebc = s.pop("experiments_by_catalyst", None)
    groups = []
    if isinstance(ebc, dict):
        groups = [{"catalyst_id": k, "experiments": v} for k, v in ebc.items()]
    elif isinstance(ebc, list):
        groups = ebc
    n_ebc = 0
    for grp in groups:
        if not isinstance(grp, dict):
            continue
        gcid = str(grp.get("catalyst_id", "")).strip()
        for e in grp.get("experiments", []) or []:
            if gcid and not e.get("catalyst_id"):
                e["catalyst_id"] = gcid
            host = idx.get(str(e.get("catalyst_id", "")).strip())
            if host is None and len(cats) == 1:
                host = cats[0]
            if host is not None:
                host.setdefault("experiments", []).append(e)
                n_ebc += 1
            elif cats:
                cats[0]["experiments"].append(e)
                e.setdefault("other", {})["nesting_note"] = f"原 catalyst_id={e.get('catalyst_id')} 未能匹配，兜底挂到首个催化剂"
                n_ebc += 1
            else:
                s.setdefault("experiments", []).append(e)
    if n_ebc:
        changed.append(f"experiments_by_catalyst 解包嵌回 {n_ebc} 个实验")

    exp_idx = {str(e.get("experiment_id")).strip(): e for c in cats for e in c.get("experiments", [])}
    # 实验内部 'variables' 键 → 统一 'variable_experiments'
    for c in cats:
        for e in c.get("experiments", []):
            if "variables" in e and "variable_experiments" not in e:
                e["variable_experiments"] = e.pop("variables")
                changed.append(f"variables→variable_experiments:{e.get('experiment_id')}")
    # 顶层 variables 数组 → 按 exp_id 嵌回
    top_vars = s.pop("variables", None) or []
    n_var = 0
    for v in top_vars:
        vid = v.pop("var_id", None) or v.pop("variable_id", None) or v.pop("node_id", None)
        eid = v.pop("exp_id", None) or v.pop("experiment_id", None)
        v.setdefault("degradation_profiles", None)
        v.setdefault("qc", {"e3_flag": False, "needs_review": False, "errata_ref": "", "note": ""})
        if not isinstance(v.get("other"), dict):
            v["other"] = {}
        host = exp_idx.get(str(eid).strip()) if eid else None
        if host is None and exp_idx:
            host = list(exp_idx.values())[0]
            v["other"]["nesting_note"] = f"原 exp_id={eid} 未能匹配实验节点，兜底挂到首个实验"
        if host is not None:
            host.setdefault("variable_experiments", []).append(v)
            n_var += 1
        else:
            print(f"  WARN 孤儿变量 {vid} 无处挂载，保留顶层")
            s.setdefault("variable_experiments", []).append(v)
    if n_var:
        changed.append(f"嵌回变量 {n_var} 个")
    top_var2 = s.pop("variable_experiments", None) or []
    for v in top_vars and []:  # 顶层残留变量极少见，保留
        pass
    if top_var2:
        s["variable_experiments"] = top_var2
    return s, changed


def auto_add_vars(wf, s, changed):
    """对账补丁：05_node_inventory 有而 schema 缺的变量节点→补占位（防悬空引用与 FAIL）"""
    import json, os
    inv_p = os.path.join(wf, "05_node_inventory.json")
    if not os.path.isfile(inv_p):
        return
    try:
        inv = json.load(open(inv_p, encoding="utf-8"))
    except Exception:
        return
    inv_vars = inv.get("variables") or []
    if not inv_vars:
        return
    have = set()
    for c in s.get("catalysts", []):
        for e in c.get("experiments", []):
            for v in e.get("variable_experiments", []) or []:
                have.add((str(v.get("variable_parameter", "")).split("(")[0].strip().lower(),
                          str(e.get("experiment_id", ""))))
    by_exp = {}
    for c in s.get("catalysts", []):
        for e in c.get("experiments", []):
            by_exp.setdefault(str(e.get("experiment_id", "")), []).append(e)
    added = 0
    for iv in inv_vars:
        key = str(iv.get("variable_parameter", "")).split("(")[0].strip().lower()
        exp_id = str(iv.get("exp_id", ""))
        if (key, exp_id) in have:
            continue
        for e in by_exp.get(exp_id, []):
            e.setdefault("variable_experiments", []).append({
                "variable_parameter": iv.get("variable_parameter", ""),
                "new_value": "", "varied_conditions": "占位节点（fix_schema_nesting 自动补齐；水平值待核）",
                "metric_name": "", "metric_value": "", "source_figure": "",
                "degradation_profiles": None,
                "qc": {"e3_flag": False, "needs_review": True,
                        "errata_ref": "", "note": "auto_added_from_inventory"},
                "other": {}})
            have.add((key, exp_id)); added += 1
            changed.append(f"auto_var:{exp_id}.{iv.get('variable_parameter','')[:20]}")
    if added:
        print(f"    自动补变量节点 {added} 个")


def main(root):
    for p in sorted(os.listdir(root)):
        wf = wf_of(os.path.join(root, p))
        sch_p = os.path.join(wf, "06_schema.json")
        if not os.path.isfile(sch_p):
            continue
        s = json.load(open(sch_p, encoding="utf-8"))
        s2, changed = fix_schema(s)
        auto_add_vars(wf, s2, changed)
        if changed:
            json.dump(s2, open(sch_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{p[:40]:42s} 已归一: {'; '.join(changed)}")
        else:
            print(f"{p[:40]:42s} 结构标准，无需修改")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test3f")
