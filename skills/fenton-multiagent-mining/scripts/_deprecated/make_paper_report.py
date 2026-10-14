# -*- coding: utf-8 -*-
"""【已废弃 · 2026-10-05】原步骤⑨：单篇格式化报告 08_paper_report.json

废弃理由：产出内容全是既有产物的镜像（entity_graph 重遍历 06、crossval 数 04、
reconciliation 抄 07、fill_stats 抄 06、work_order 抄 01），**没有任何脚本读取 08**，
零新增信息；批次级统计该由 build_report.py 一次汇总，不必每篇吐一份。
文件保留仅供取用其字段拼装逻辑；新批次不要再跑。

原说明：汇总一篇的全部产物为固定 schema，供 211 篇跑完后批次统计聚合。
输入（datamining/ 下）：gt_evaluation.json、04c_errata.json、04_internal_corrections.json、
04_verified_image_data.json、04_discrepancy_ledger.json、05_node_inventory.json、
06_schema.json、01_work_order.json、07_reconciliation.json
批次级输入（skill 目录）：<batch>_污染物对齐表.json
用法：python make_paper_report.py <文献根目录或单篇目录> [--align 污染物对齐表.json]
"""
import glob
import json
import os
import sys


def wf(paper):
    return os.path.join(paper, "datamining") if os.path.isdir(os.path.join(paper, "datamining")) else paper


def jload(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def count_points(verified):
    """统计验证后数据：记录数、曲线点、image_only/dual_source 点。"""
    recs = verified if isinstance(verified, list) else []
    n_rec = len(recs)
    n_curve_pts = 0
    img_only_pts = 0
    dual_pts = 0
    dual_checked = 0
    for r in recs:
        src = ""
        v = r.get("verify") or r.get("verification") or {}
        if isinstance(v, dict):
            src = v.get("source", "")
        pts = 0
        if r.get("mode") == "xy":
            for c in r.get("curves", []) or []:
                pts += len(c.get("points", []) or [])
        n_curve_pts += pts
        if src == "image_only":
            img_only_pts += max(pts, 1)
        elif src == "dual_source":
            dual_pts += max(pts, 1)
            dual_checked += 1
    return {"records": n_rec, "curve_points": n_curve_pts,
            "image_only_points": img_only_pts, "dual_source_points": dual_pts,
            "dual_source_records": dual_checked}


def build(paper, align_table):
    z = wf(paper)
    name = os.path.basename(os.path.normpath(paper))
    rep = {"paper": name, "generated_at": "auto",
           "gt_scores": None, "extraction_failures": [],
           "literature_contradictions": [], "crossval_benefit": {},
           "entity_graph": {}, "fill_stats": None, "reconciliation": None}

    # ── GT 评分 ──
    gt = jload(os.path.join(z, "gt_evaluation.json"))
    if gt:
        prf = gt.get("prf") or gt.get("prf_metrics") or gt.get("precision_recall_f1") or {}
        rep["gt_scores"] = {"prf_raw": prf}

    # ── 计分失败清单（从 gt_evaluation 的 field_comparison/prf breakdown 提取）──
    fails, seen = [], set()

    def walk_fail(o, parent=""):
        if isinstance(o, dict):
            cnt = str(o.get("counts", "") or o.get("count", "") or o.get("type", "") or o.get("verdict", "") or o.get("result", ""))
            if not cnt and ("fp_list" in parent or "fn_list" in parent):
                cnt = "FP" if "fp_list" in parent else "FN"
            if ("gt_value" in o or "gt" in o) and (cnt.startswith("FP") or cnt.startswith("FN")):
                key = json.dumps({k: o.get(k) for k in ("node", "field", "gt", "schema")}, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    fails.append({"node": o.get("node") or o.get("node_pair") or o.get("path", ""),
                                  "field": o.get("field", ""), "type": cnt[:2],
                                  "gt": o.get("gt_value", o.get("gt")),
                                  "schema": o.get("schema_value", o.get("schema")),
                                  "root_cause": o.get("root_cause", o.get("root_cause_tally", "")),
                                  "note": (o.get("note") or o.get("item") or "")[:120]})
            for k, v in o.items():
                walk_fail(v, parent + "/" + str(k))
        elif isinstance(o, list):
            for v in o:
                walk_fail(v, parent)

    walk_fail(gt)
    rep["extraction_failures"] = fails

    # ── 文章真矛盾（4c 勘误 = text-text/E3/E5 定案）──
    err = jload(os.path.join(z, "04c_errata.json"), [])
    if isinstance(err, dict):
        err = err.get("errata") or [v for v in err.values() if isinstance(v, dict)] or []
    rep["literature_contradictions"] = [
        {"id": e.get("id", i + 1), "category": e.get("category", e.get("type", "")),
         "location": (e.get("location", "") or "")[:80],
         "conflict": (e.get("conflict", e.get("description", "")) or "")[:150],
         "adopted_value": e.get("adopted_value", "")}
        for i, e in enumerate(err) if isinstance(e, dict)]

    # ── 图文互证收益 ──
    corr = jload(os.path.join(z, "04_internal_corrections.json"), [])
    led = jload(os.path.join(z, "04_discrepancy_ledger.json"), [])
    ver = jload(os.path.join(z, "04_verified_image_data.json"), [])
    cnt = count_points(ver if isinstance(ver, list) else [])
    e1 = sum(1 for c in corr if c.get("category") == "E1") if isinstance(corr, list) else 0
    e2 = sum(1 for c in corr if c.get("category") == "E2") if isinstance(corr, list) else 0
    rep["crossval_benefit"] = {
        "e1_reading_fixed": e1, "e2_text_fixed": e2,
        "e3_adjudicated": len(led) if isinstance(led, list) else 0, **cnt}

    # ── 入库图谱 ──
    sch = jload(os.path.join(z, "06_schema.json")) or {}
    inv = jload(os.path.join(z, "05_node_inventory.json")) or {}
    cats, exps, links, variables = [], [], [], []
    align = jload(align_table, {}) if align_table else {}
    for c in sch.get("catalysts", []):
        cid = c.get("catalyst_id", "")
        props = c.get("properties", {}) or {}
        filled = sum(1 for v in props.values() if v not in (None, ""))
        cats.append({"id": cid, "name": c.get("catalyst_name") or props.get("catalyst_name", ""),
                     "properties_filled": filled})
        for e in c.get("experiments", []) or []:
            eid = e.get("experiment_id", "")
            pname = e.get("pollutant_name") or (e.get("properties", {}) or {}).get("pollutant_name", "")
            exps.append({"id": eid, "catalyst": cid, "pollutant_name": pname})
            links.append([cid, eid])
            for v in e.get("variable_experiments", []) or []:
                vid = f"{eid}:{v.get('variable_parameter','')}={v.get('new_value','')}"
                variables.append({"id": vid, "exp": eid,
                                  "parameter": v.get("variable_parameter", ""),
                                  "e3_flag": bool((v.get("qc") or {}).get("e3_flag"))})
                links.append([eid, vid])
    # 污染物 CID 对齐
    for pnode in inv.get("pollutants", []) or []:
        en = ""
        for k, vv in align.items():
            if vv.get("status") == "ok" and k.lower() in str(pnode.get("name", "")).lower():
                en = k
                break
        if en:
            links.append([align[en]["cid"], exps[0]["id"] if exps else ""]) if False else None
    rep["entity_graph"] = {
        "catalysts": cats, "experiments": exps, "variables": variables,
        "n_nodes": len(cats) + len(exps) + len(variables) + len(inv.get("pollutants", []) or []),
        "n_links": len(links), "links": links,
        "pollutants": [{"id": p.get("id"), "name": p.get("name"),
                        "pubchem_cid": next((align[k]["cid"] for k, vv in align.items()
                                             if vv.get("status") == "ok" and k.lower() in str(p.get("name", "")).lower()), None)}
                       for p in inv.get("pollutants", []) or []]}

    # ── 其它 ──
    sch_all = sch if "fill_stats" in sch else {}
    rep["fill_stats"] = sch_all.get("fill_stats")
    rec = jload(os.path.join(wf(os.path.dirname(z)), "07_reconciliation.json"))
    if isinstance(rec, list):
        for r in rec:
            if r.get("paper") == name:
                rep["reconciliation"] = "PASS" if r.get("pass") else "FAIL: " + ";".join(r.get("missing", []))
    wo = jload(os.path.join(z, "01_work_order.json")) or {}
    rep["work_order_summary"] = wo.get("summary")

    out = os.path.join(z, "08_paper_report.json")
    json.dump(rep, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out, rep


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    align = None
    if "--align" in sys.argv:
        align = sys.argv[sys.argv.index("--align") + 1]
    target = args[0] if args else r"D:\fenton_papers\test3f"
    papers = [target] if os.path.isfile(os.path.join(target, "full.md")) or \
        os.path.isdir(os.path.join(target, "datamining")) else \
        sorted(glob.glob(os.path.join(target, "*")))
    for p in papers:
        if not os.path.isdir(p):
            continue
        try:
            out, rep = build(p, align)
            g = rep["gt_scores"] or {}
            print(f"{rep['paper'][:36]:38s} 报告OK 失败{len(rep['extraction_failures'])}条 "
                  f"矛盾{len(rep['literature_contradictions'])}条 互证E1/E2="
                  f"{rep['crossval_benefit'].get('e1_reading_fixed')}/{rep['crossval_benefit'].get('e2_text_fixed')} "
                  f"图节点{rep['entity_graph'].get('n_nodes')} → {os.path.basename(out)}")
        except Exception as e:
            print(f"{os.path.basename(p)[:36]:38s} FAIL {str(e)[:60]}")


if __name__ == "__main__":
    main()
