# -*- coding: utf-8 -*-
"""step5b1_pollutant_align.py — 步骤六② 实体对齐（污染物三源 + 催化剂 Neo4j）

【2026-10-05 改造】对齐分两类，口径不同：

  污染物（种类有限、跨文献同一物质）——**以合并节点为目标**：
    ① 先查 Neo4j 已有 Pollutant 节点（名字/英文名/缩写展开后归一化匹配）
       → 命中即复用其 CID/CAS/InChIKey（跨文献一致、省联网；Aura 空闲会断连，本脚本自动重试）
    ② 未命中 → PubChem 两步法（name→cids→cid属性）；PubChem 连续故障 → NCI CACTUS 兜底
    `graph_hits` > 1 表示库内该物质已裂成多个节点（待合并的债，不是错误）。

  催化剂（变体极多：负载量/掺杂比/载体各不同）——**不合并节点**：
    只按 `material_family`（材料族骨架，算法与 step10_graph_export.py 严格一致）算族键，
    记录同族已入库节点，供跨文献「按族聚合、按变体区分」查询。

用法：
  python step5b1_pollutant_align.py <论文夹 或 批次根>
  python step5b1_pollutant_align.py <路径> --no-net     # 只用 Neo4j，不联网
  python step5b1_pollutant_align.py <路径> --dry-run    # 只匹配、不写产物
台账目录：单篇 → <篇>/datamining/_ledgers；批次 → <批次根>/_ledgers
产物：<批次>_污染物对齐表.json（step10 join 用）、<批次>_催化剂对齐表.json、<批次>_实体对齐表.json
"""
import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request

H = {"User-Agent": "Mozilla/5.0 (research script)"}
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 缩写 → 全名候选（依次尝试；首查词放最前）
SYNONYMS = {
    "ao7": ["Acid Orange 7", "Orange II", "4-[(2-hydroxynaphthyl)azo]benzenesulfonic acid"],
    "acid orange 7": ["Orange II", "4-[(2-hydroxynaphthyl)azo]benzenesulfonic acid"],
    "orange ii": ["Acid Orange 7"],
    "orange g": ["Orange G"],
    "cip": ["Ciprofloxacin"], "ofx": ["Ofloxacin"], "ofl": ["Ofloxacin"],
    "nor": ["Norfloxacin"], "lev": ["Levofloxacin"], "enr": ["Enrofloxacin"],
    "smx": ["Sulfamethoxazole"], "smz": ["Sulfamethazine"], "sdz": ["Sulfadiazine"],
    "tc": ["Tetracycline"], "otc": ["Oxytetracycline"], "amx": ["Amoxicillin"],
    "mb": ["Methylene blue"], "rhb": ["Rhodamine B"], "mo": ["Methyl orange"],
    "cr": ["Congo red"], "mg": ["Malachite green"], "cv": ["Crystal violet"],
    "bpa": ["Bisphenol A"], "pnp": ["4-Nitrophenol"], "4-np": ["4-Nitrophenol"],
    "2,4-d": ["2,4-Dichlorophenoxyacetic acid"], "2,4-dcp": ["2,4-Dichlorophenol"],
    "4-cp": ["4-Chlorophenol"], "rb5": ["Reactive Black 5"],
    "rb19": ["Reactive Blue 19"], "atz": ["Atrazine"], "dbp": ["Dibutyl phthalate"],
    "cfx": ["Cefalexin"],
}

_MINERAL = {"goethite": "FeOOH", "针铁矿": "FeOOH", "hematite": "Fe2O3", "赤铁矿": "Fe2O3",
            "magnetite": "Fe3O4", "磁铁矿": "Fe3O4", "ferrihydrite": "FeOOH", "水铁矿": "FeOOH"}


# ────────────────────────── 基础工具 ──────────────────────────
def load_env(path):
    cfg = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def papers_of(root):
    """单篇（root 自身是论文夹）或批次（root 下若干论文夹）自适应。"""
    root = os.path.abspath(root)
    if os.path.isdir(os.path.join(root, "datamining")) or os.path.exists(
            os.path.join(root, "00_full_cleaned.md")):
        return [root]
    out = []
    for p in sorted(os.listdir(root)):
        d = os.path.join(root, p)
        if not os.path.isdir(d) or p.startswith("_"):
            continue
        if os.path.isdir(os.path.join(d, "datamining")) or os.path.exists(
                os.path.join(d, "00_full_cleaned.md")):
            out.append(d)
    return out


def norm(s):
    """实体名归一化：小写 → 去括号注释 → 去浓度/剂量 → 去标点 → 压空白。"""
    s = str(s or "").strip().lower()
    s = re.sub(r"[（(][^）)]*[）)]", " ", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*(?:mg|ml|l|g|μg|ug|nm|μm|um|mm|mol|mM|%)\b", " ", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def english_of(raw):
    """节点名（常为「中文 (English, ABBR)」）→ 英文查询词；剥掉结尾逗号+缩写后缀。"""
    m = re.search(r"[A-Za-z][A-Za-z0-9 ,\.\-']+", raw)
    en = m.group(0).strip(" ,.-") if m else raw
    return re.sub(r",\s*[A-Z][A-Z0-9\-]{0,5}\s*$", "", en).strip(" ,.-")


def material_family(name):
    """催化剂名 → 材料族骨架。**必须与 step10_graph_export.py 的算法一致**。"""
    s = str(name or "").strip()
    if not s:
        return ""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"^\s*\d+(?:\.\d+)?\s*%\s*", "", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*%", "", s)
    for k, v in _MINERAL.items():
        if re.search(k, s, re.I):
            s = v
            break
    s = re.sub(r"\s*\+\s*", "+", s)
    s = re.sub(r"\s+", " ", s).strip(" +/")
    return s.replace("α-FeOOH", "FeOOH").replace("γ-Fe2O3", "Fe2O3")


# ────────────────────────── 联网查询（PubChem → CACTUS） ──────────────────────────
def get(url, timeout=20):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_text(url, timeout=25):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace").strip()


pubchem_fail = 0  # 连续故障计数，>=2 熔断


def lookup_pubchem(name):
    global pubchem_fail
    if pubchem_fail >= 2:
        raise RuntimeError("PubChem 熔断中（连续故障）")
    try:
        cids = get("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                   f"{urllib.parse.quote(name)}/cids/JSON")["IdentifierList"]["CID"]
        cid = cids[0]
        time.sleep(0.3)
        p = get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/"
                "property/Title,MolecularFormula,MolecularWeight/JSON"
                )["PropertyTable"]["Properties"][0]
        pubchem_fail = 0
        return {"query": name, "cid": cid, "pubchem_title": p.get("Title"),
                "formula": p.get("MolecularFormula"), "mw": p.get("MolecularWeight"),
                "source": "PubChem", "status": "ok"}
    except Exception:
        pubchem_fail += 1
        raise


def cactus_props(query_name, cand):
    formula = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                       f"{urllib.parse.quote(cand)}/formula")
    mw = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                  f"{urllib.parse.quote(cand)}/mw")
    ik = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                  f"{urllib.parse.quote(cand)}/stdinchikey").replace("InChIKey=", "")
    try:
        cas = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                       f"{urllib.parse.quote(cand)}/cas")
    except Exception:
        cas = ""
    return {"query": query_name, "cid": None, "pubchem_title": cand,
            "formula": formula, "mw": mw, "inchikey": ik, "cas": cas,
            "source": "NCI_CACTUS", "synonym_used": cand if cand != query_name else "",
            "status": "ok",
            "note": "PubChem 不可达；formula/mw/InChIKey 来自 NCI CACTUS，CID 留空待补"}


def lookup(name):
    """PubChem 优先（缩写先换全名）→ CACTUS → 抛异常。"""
    cands = [name] + SYNONYMS.get(name.lower(), [])
    pub_err = None
    for q in dict.fromkeys(cands):
        try:
            r = lookup_pubchem(q)
            if q != name:
                r["synonym_used"] = q
            return r
        except Exception as e:
            pub_err = str(e)[:60]
            if pubchem_fail >= 2:
                break
    for q in dict.fromkeys(cands):
        try:
            r = cactus_props(name, q)
            if pub_err:
                r["pubchem_error"] = pub_err
            return r
        except Exception:
            time.sleep(0.5)
            continue
    raise RuntimeError(f"PubChem+CACTUS 均失败：{pub_err}")


# ────────────────────────── 图谱读取与索引 ──────────────────────────
def fetch_graph(env_path, tries=3):
    """拉全量 Pollutant / Catalyst（一次读完，避免逐条往返）；Aura 空闲断连自动重试。"""
    cfg = load_env(env_path)
    if not cfg.get("NEO4J_URI"):
        return None, f"读不到 NEO4J_URI（--env 指向 neo4j.env，默认 {env_path}）"
    try:
        from neo4j import GraphDatabase
    except Exception as e:
        return None, f"未安装 neo4j 驱动：{e}"
    last = ""
    for i in range(tries):
        try:
            drv = GraphDatabase.driver(cfg["NEO4J_URI"],
                                       auth=(cfg.get("NEO4J_USERNAME", ""),
                                             cfg.get("NEO4J_PASSWORD", "")),
                                       connection_timeout=30)
            with drv.session(database=cfg.get("NEO4J_DATABASE") or None) as s:
                # 污染物节点是 :Entity {label:'Pollutant'}（不是独立的 :Pollutant 标签），
                # 用 properties(n) 一次取全，避免引用图谱里尚不存在的属性键（cas/inchikey）。
                raw = s.run("MATCH (n:Entity {label:'Pollutant'}) "
                            "RETURN properties(n) AS props").data()
                pol = []
                for x in raw:
                    r = dict(x.get("props") or {})
                    r.setdefault("nid", r.get("node_id"))
                    r["cid"] = r.get("pubchem_cid")
                    r["ik"] = r.get("inchikey")
                    pol.append(r)
                cat = s.run("MATCH (c:Catalyst) "
                            "RETURN c.node_id AS nid, coalesce(c.name, c.catalyst_name) AS name, "
                            "c.material_family AS fam, c.paper_id AS pid").data()
            drv.close()
            return {"pollutant": pol, "catalyst": cat, "n_retry": i}, None
        except Exception as e:
            last = str(e)[:120]
            time.sleep(1.5)
    return None, f"Neo4j 连接失败（已重试 {tries} 次）：{last}"


def index_graph(g):
    pol_by, cat_by_name, cat_by_fam = {}, {}, {}
    for r in g["pollutant"]:
        pol_by.setdefault(norm(r.get("name")), []).append(r)
    for r in g["catalyst"]:
        cat_by_name.setdefault(norm(r.get("name")), []).append(r)
        f = norm(r.get("fam"))
        if f:
            cat_by_fam.setdefault(f, []).append(r)
    return pol_by, cat_by_name, cat_by_fam


def match_pollutant(entry, pol_by):
    """① 归一化精确 → ② 双向子串（长度≥4）。返回 (match_type, rows)。"""
    cands = [norm(entry["query"])]
    cands += [norm(x) for x in SYNONYMS.get(str(entry["query"]).lower(), [])]
    cands += [norm(x) for x in entry.get("raw_names", [])]
    cands = [c for c in dict.fromkeys(cands) if c]
    for c in cands:
        if c in pol_by:
            return "exact", pol_by[c]
    for c in cands:
        if len(c) < 4:
            continue
        rows = []
        for k, v in pol_by.items():
            if k and (c in k or k in c):
                rows += v
        if rows:
            return "substr", rows
    return "", []


def match_catalyst(name, cat_by_name, cat_by_fam):
    """① 全名精确 → ② material_family 命中。返回 (match_type, family, exact_rows, fam_rows)。"""
    n = norm(name)
    fam = material_family(name)
    rows = cat_by_name.get(n, [])
    if rows:
        return "exact", fam, rows, cat_by_fam.get(norm(fam), [])
    frows = cat_by_fam.get(norm(fam), []) if fam else []
    return ("family" if frows else "none"), fam, [], frows


# ────────────────────────── 主流程 ──────────────────────────
def collect(papers):
    pol, cat = {}, {}
    for paper in papers:
        inv = os.path.join(wf_of(paper), "05_node_inventory.json")
        if not os.path.exists(inv):
            print(f"⚠ 无 05_node_inventory.json：{paper}")
            continue
        try:
            d = json.load(open(inv, encoding="utf-8"))
        except Exception as e:
            print(f"✗ 解析失败 {inv}: {str(e)[:60]}")
            continue
        pname = os.path.basename(os.path.normpath(paper))[:25]
        for e in d.get("pollutants", []) or []:
            raw = str(e.get("name", "")).strip()
            en = english_of(raw)
            if not raw or not en:
                continue
            it = pol.setdefault(en, {"query": en, "raw_names": [], "papers": []})
            if raw not in it["raw_names"]:
                it["raw_names"].append(raw)
            if pname not in it["papers"]:
                it["papers"].append(pname)
        for e in d.get("catalysts", []) or []:
            name = str(e.get("name", "")).strip()
            if not name:
                continue
            it = cat.setdefault(name, {"name": name, "papers": [], "role": e.get("role", "")})
            if pname not in it["papers"]:
                it["papers"].append(pname)
    return pol, cat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="论文夹 或 批次根（自适应）")
    ap.add_argument("--env", default=os.path.join(_SCRIPT_DIR, "neo4j.env"))
    ap.add_argument("--no-net", action="store_true", help="只用 Neo4j，不联网")
    ap.add_argument("--dry-run", action="store_true", help="只匹配，不写产物")
    a = ap.parse_args()

    root = os.path.abspath(a.path)
    if not os.path.isdir(root):
        print(f"✗ 不是目录：{root}")
        return 1
    papers = papers_of(root)
    if not papers:
        print(f"✗ 未找到论文夹（需含 datamining/05_node_inventory.json）：{root}")
        return 1
    single = os.path.isdir(os.path.join(root, "datamining"))
    batch = os.path.basename(os.path.normpath(root))
    ledger = os.path.join(root, "datamining", "_ledgers") if single else os.path.join(root, "_ledgers")

    print(f"论文 {len(papers)} 篇（{'单篇' if single else '批次'}）｜台账 → {ledger}")
    pol, cat = collect(papers)
    print(f"待对齐：污染物 {len(pol)} 个 / 催化剂 {len(cat)} 个")

    g, err = fetch_graph(a.env)
    if err:
        print(f"✗ {err}")
        print("  提示：图谱跑在远端 Neo4j Aura，空闲会被回收连接——本脚本已自动重试 3 次；"
              "仍失败请稍后再跑，**不要据此断言图谱没数据**")
        return 1
    n_retry = g.pop("n_retry")
    print(f"图谱读取：Pollutant {len(g['pollutant'])} / Catalyst {len(g['catalyst'])}"
          f"（连接重试 {n_retry} 次）")
    pol_by, cat_by_name, cat_by_fam = index_graph(g)

    # ── 污染物 ──
    pout = {}
    for en, entry in sorted(pol.items()):
        mt, rows = match_pollutant(entry, pol_by)
        if rows:
            r0 = rows[0]
            rec = {"query": en, "status": "ok", "source": "neo4j", "match_type": mt,
                   "cid": r0.get("cid"), "cas": r0.get("cas"), "inchikey": r0.get("ik"),
                   "pubchem_title": r0.get("name"), "matched_node_id": r0.get("nid"),
                   "graph_hits": len(rows),
                   "graph_node_ids": [x.get("nid") for x in rows][:8],
                   "raw_names": entry["raw_names"], "papers": entry["papers"]}
            print(f"OK  {en:30s} [图谱 {mt}] {r0.get('name','')[:34]}"
                  f"{'  ⚠ 库内' + str(len(rows)) + '个同名节点(待合并)' if len(rows) > 1 else ''}")
        elif a.no_net:
            rec = {"query": en, "status": "unresolved", "source": "graph_only",
                   "error": "未命中 Neo4j，且 --no-net 跳过联网",
                   "raw_names": entry["raw_names"], "papers": entry["papers"]}
            print(f"MISS {en:30s} 图谱未命中（--no-net，未联网）")
        else:
            try:
                rec = lookup(en)
                rec.update({"raw_names": entry["raw_names"], "papers": entry["papers"]})
                tag = f"CID {rec['cid']}" if rec.get("cid") else f"CACTUS {rec.get('formula','')}"
                print(f"OK  {en:30s} [{rec.get('source')}] {tag}")
            except Exception as e:
                rec = {"query": en, "status": "unresolved", "error": str(e)[:80],
                       "raw_names": entry["raw_names"], "papers": entry["papers"]}
                print(f"ERR {en:30s} {str(e)[:50]}")
        pout[en] = rec
        if rec.get("source") != "neo4j":
            time.sleep(0.4)

    # ── 催化剂（不合并节点，只算族键 + 找同族） ──
    cout = {}
    for name, entry in sorted(cat.items()):
        mt, fam, exact_rows, fam_rows = match_catalyst(name, cat_by_name, cat_by_fam)
        hit = exact_rows or fam_rows
        cout[name] = {"name": name, "family": fam, "role": entry.get("role", ""),
                      "match_type": mt, "action": "reuse_family" if hit else "new_family",
                      "matched_node_ids": [x.get("nid") for x in hit][:10],
                      "graph_same_name": len(exact_rows), "graph_same_family": len(fam_rows),
                      "matched_papers": len({x.get("pid") for x in hit if x.get("pid")}),
                      "papers": entry["papers"],
                      "note": "催化剂不合并节点；跨文献聚合用 family（=material_family）"}
        print(f"{'CAT ' if hit else 'NEW '} {name[:46]:46s} 族={fam[:22]:22s} {mt}"
              f"{'  同族 ' + str(len(hit)) + ' 节点/' + str(cout[name]['matched_papers']) + ' 篇' if hit else ''}")

    n_pol_ok = sum(1 for v in pout.values() if v.get("status") == "ok")
    n_pol_graph = sum(1 for v in pout.values() if v.get("source") == "neo4j")
    n_pol_dup = sum(1 for v in pout.values() if v.get("graph_hits", 0) > 1)
    n_cat_hit = sum(1 for v in cout.values() if v["action"] == "reuse_family")
    print(f"=== 污染物对齐 {n_pol_ok}/{len(pout)}（其中图谱命中 {n_pol_graph}，"
          f"库内多节点待合并 {n_pol_dup}）｜催化剂 {n_cat_hit}/{len(cout)} 命中已有族")

    if a.dry_run:
        print("（--dry-run，未写产物）")
        return 0
    os.makedirs(ledger, exist_ok=True)
    f_pol = os.path.join(ledger, f"{batch}_污染物对齐表.json")
    f_cat = os.path.join(ledger, f"{batch}_催化剂对齐表.json")
    f_all = os.path.join(ledger, f"{batch}_实体对齐表.json")
    json.dump(pout, open(f_pol, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(cout, open(f_cat, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"batch": batch, "papers": len(papers), "ledger": ledger,
               "pollutants": pout, "catalysts": cout,
               "summary": {"pollutant_total": len(pout), "pollutant_ok": n_pol_ok,
                           "pollutant_by_graph": n_pol_graph, "pollutant_dup_in_graph": n_pol_dup,
                           "catalyst_total": len(cout), "catalyst_family_hit": n_cat_hit}},
              open(f_all, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"→ {f_pol}\n→ {f_cat}\n→ {f_all}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
