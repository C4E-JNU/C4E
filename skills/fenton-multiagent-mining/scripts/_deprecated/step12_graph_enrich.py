# -*- coding: utf-8 -*-
"""step12_graph_enrich.py — 图谱实体信息回填【历史批次补丁，新批次不必跑】

【2026-10-05 定位变更】实体信息已前移到导出阶段：
  step10_graph_export.py 现在**导出时**就 join <批次>_污染物对齐表.json
  （Pollutant 节点直接带 CID/分子式/分子量/InChIKey/CAS），并算好 Catalyst 的
  material_family / loading_pct；step11 一次入库即带全属性。
  所以本脚本只留给**已入库的历史批次**（当时导出没带这些属性）——好处是不必重跑
  step10+step11（重跑会重算 node_id，反而更麻烦）。

【背景】step5b1 的对齐表此前只落在技能目录里、从未写回 Neo4j —— 图谱里 862 个
  Entity{label:'Pollutant'} 节点实测只有 name/node_id/label 三个属性，CID/分子式/分子量全缺。
  本脚本补这条断链：把对齐表 SET 回对应的 Pollutant 实体节点。

  同时给 Catalyst 节点补两个**归并辅助属性**（不改 node_id，不动既有 2437 个节点）：
    - material_family : 材料族（剥离负载量/掺杂比后的骨架，如 "Fe2O3/ZSM-5"）
    - loading_pct     : 负载量数字（如 10）
  这样跨文献聚合可用
    MATCH (c:Catalyst) WHERE c.material_family='Fe2O3/ZSM-5' RETURN c.paper_id, c.catalyst_name
  而无需重建 node_id（Catalyst 的唯一键是 pid::C序号，跨文献天然不合并）。

对齐匹配策略：Pollutant 实体名常是「中文 (English, ABBR)」混合形式，故按
  toLower(e.name) CONTAINS toLower(查询词) 匹配；零命中时回退到「name 里含该英文词」的宽松匹配。

用法（仅历史批次）：
  python step12_graph_enrich.py B202                       # 用 skills/<技能>/scripts/neo4j.env
  python step12_graph_enrich.py B202 --dry-run             # 只报命中数，不写
  python step12_graph_enrich.py B202 --table <对齐表路径>   # 显式指定对齐表
  python step12_graph_enrich.py B202 --skip-catalyst       # 只回填污染物
"""
import argparse
import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))


def load_env(path):
    cfg = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


_MINERAL = {"goethite": "FeOOH", "针铁矿": "FeOOH", "hematite": "Fe2O3", "赤铁矿": "Fe2O3",
            "magnetite": "Fe3O4", "磁铁矿": "Fe3O4", "ferrihydrite": "FeOOH", "水铁矿": "FeOOH"}


def material_family(name):
    """催化剂名 → 材料族骨架（与 step10 保持一致）。

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", help="批次名（如 B202）")
    ap.add_argument("--table", default="", help="污染物对齐表路径（默认 <语料根>/<批次>/_ledgers/<批次>_污染物对齐表.json）")
    ap.add_argument("--corpus", default=r"D:/fenton_papers", help="语料根（批次目录的父目录）")
    ap.add_argument("--env", default=os.path.join(_SCRIPT_DIR, "neo4j.env"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-catalyst", action="store_true")
    a = ap.parse_args()

    table = a.table or os.path.join(a.corpus, a.batch, "_ledgers", f"{a.batch}_污染物对齐表.json")
    if not os.path.exists(table):
        print(f"✗ 找不到对齐表：{table}")
        return 1
    rows = json.load(open(table, encoding="utf-8"))
    ok_rows = [r for r in rows.values() if isinstance(r, dict) and r.get("status") == "ok"]

    cfg = load_env(a.env)
    if not cfg.get("NEO4J_URI"):
        print(f"✗ 读不到 NEO4J_URI（env: {a.env}）")
        return 1

    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(cfg["NEO4J_URI"],
                               auth=(cfg.get("NEO4J_USERNAME", ""), cfg.get("NEO4J_PASSWORD", "")))
    db = cfg.get("NEO4J_DATABASE") or None
    n_pol = n_cat = miss = 0

    with drv.session(database=db) as s:
        # ── 1) 污染物：对齐表 → Entity{label:'Pollutant'} ──
        for r in ok_rows:
            q = str(r.get("query") or "").strip()
            if not q:
                continue
            props = {"pubchem_cid": r.get("cid"), "pubchem_title": r.get("pubchem_title"),
                     "formula": r.get("formula"), "mw": r.get("mw"),
                     "inchikey": r.get("inchikey"), "cas": r.get("cas"),
                     "align_source": r.get("source") or "PubChem"}
            props = {k: v for k, v in props.items() if v not in (None, "")}
            if a.dry_run:
                c = s.run("MATCH (e:Entity {label:'Pollutant'}) "
                          "WHERE toLower(e.name) CONTAINS toLower($q) RETURN count(e) AS c",
                          q=q).single()["c"]
                print(f"  [dry] {q:34s} → 命中 {c} 个 Pollutant 实体")
                n_pol += min(c, 1)
                continue
            res = s.run("MATCH (e:Entity {label:'Pollutant'}) "
                        "WHERE toLower(e.name) CONTAINS toLower($q) "
                        "SET e += $props RETURN count(e) AS c", q=q, props=props).single()
            c = res["c"]
            if c:
                n_pol += 1
            else:
                miss += 1
                print(f"  ⚠ 图谱无匹配 Pollutant：{q}（对齐表有条目但图谱未收录）")
            print(f"  ✓ {q:34s} → 回填 {c} 个节点 CID={r.get('cid')}")

        # ── 2) 催化剂：材料族 / 负载量（归并辅助，不改 node_id）──
        if not a.skip_catalyst:
            cids = s.run("MATCH (c:Catalyst) RETURN c.node_id AS nid, c.catalyst_name AS nm").data()
            upd = []
            for it in cids:
                fam = material_family(it["nm"])
                if not fam:
                    continue
                props = {"material_family": fam}
                lp = loading_pct(it["nm"])
                if lp is not None:
                    props["loading_pct"] = lp
                upd.append({"nid": it["nid"], "props": props})
            print(f"  催化剂待标注 material_family：{len(upd)} 个")
            if not a.dry_run:
                for i in range(0, len(upd), 500):
                    s.run("UNWIND $rows AS r MATCH (c:Catalyst {node_id: r.nid}) "
                          "SET c += r.props", rows=upd[i:i + 500])
                n_cat = len(upd)
                try:
                    s.run("CREATE INDEX IF NOT EXISTS FOR (n:Catalyst) ON (n.material_family)")
                except Exception as ex:
                    print(f"  ⚠ 索引跳过：{str(ex)[:70]}")

    drv.close()
    tag = "（--dry-run 未写入）" if a.dry_run else ""
    print(f"✓ 污染物回填 {n_pol} 条 / 未匹配 {miss} 条；催化剂材料族标注 {n_cat} 个 {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
