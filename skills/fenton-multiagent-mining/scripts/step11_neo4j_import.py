# -*- coding: utf-8 -*-
"""step11_neo4j_import.py — 把 graph_export/ 的节点与边写入 Neo4j（步骤十②）

上游：step10_graph_export.py 产出的 <语料根>/graph_export/
  nodes_paper / nodes_catalyst / nodes_experiment / nodes_variable /
  nodes_curve / nodes_contradiction .json + nodes_extra.json（Pollutant / Figure）+ edges.json

本脚本按唯一键 MERGE（幂等，可反复重跑），并建立约束/索引。

【2026-10-07 修四处丢数据 / 键混乱】
  ① 嵌套数组（曲线点位 xs/ys）序列化为 `<key>_json` + `<key>_n`：
     Neo4j 属性不能是嵌套数组，旧版 clean() 直接过滤 → 曲线点位 100% 丢失
     （实测 5586 个 Curve 节点 0 点位）。
  ② 写入改 `SET x = $props`（先清后写）：旧版 `SET x += $props` 只增量覆盖，
     重跑时上游已删的字段会以脏属性形式留在节点上。
  ③ 边写入全程对账：缺端点/缺类型的边**逐条记录**并写入报告，不再静默丢边；
     写完后按类型统计实际边数，与 edges.json 期望值对照打印。
  ④ 唯一键重复检查（批内）+ 落盘 `_import_report.json`（节点/边/跳过原因/计数差异）。

【2026-10-10 修两处 P0 丢数据 + 五项对账】
  ① 边属性写入：MERGE 关系后补 `SET r = $props`。旧版从不写边属性 →
     `DEGRADES.pollutant` 等 100% 丢失（实测 B101：34 条 DEGRADES 带属性者 0 条）。
  ② 新增清理参数 `--purge-export / --purge-batch / --purge-paper`：写前按
     dir_name / batch 反查**新旧全部** paper_id 再 DETACH DELETE。paper_id 是内容哈希，
     步骤十规则一变即换 id，唯一键 MERGE 挡不住「同篇两套节点并存」
     （实测 B101：Paper 10≠5、Experiment 68≠34、Curve 124≠88）。共享实体
     Entity/Pollutant 无 paper_id，按设计保留。
  ③ 边对账加**本批视角**（按端点归属过滤），不再被全库历史数据掩盖；
     报告新增 `edge_props_expected/written`、`edges_reported_batch`、`purged`。
  ④ 边写入 try/except：单条异常记 skipped 后继续，不再中断整批（避免半写入无报告）。
  ⑤ clean() 遇混合类型数组（`['Fe', 1]`）改序列化 —— Neo4j 拒收这类数组，
     旧版会让整批写入失败。
  ⑥ EMBED_LABELS 加 Entity（Pollutant 现已带 search_text）+ 新建 vec_entity 向量索引。
  ⑦ dry-run 也落 `_import_report.dryrun.json`。

边：edges.json 的 {type, from, to, ...其他键}，from/to 都是 node_id（Paper 的 node_id = paper_id）。
    目标节点缺失 → 自动补 Entity / Figure 兜底节点，保证零孤儿。
    除 type/from/to 外的键写入**关系属性**（clean() 同规则净化）。

用法：
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export"
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --dry-run
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --purge-export      # 软性重导（推荐）
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --purge-batch B101
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --no-embed

顶点唯一键：
  Paper → paper_id；其余 → node_id；Entity/Pollutant 与 Figure 共用 node_id（属性 label 区分）。
"""
import argparse
import json
import os
import re
import sys

NODE_FILES = [
    ("nodes_paper.json", "Paper", "paper_id"),
    ("nodes_catalyst.json", "Catalyst", "node_id"),
    ("nodes_experiment.json", "Experiment", "node_id"),
    ("nodes_variable.json", "Variable", "node_id"),
    ("nodes_curve.json", "Curve", "node_id"),
    ("nodes_contradiction.json", "Contradiction", "node_id"),
    ("nodes_extra.json", "Entity", "node_id"),
]

CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Paper) REQUIRE n.paper_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Catalyst) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Experiment) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Variable) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Curve) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Contradiction) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Figure) REQUIRE n.node_id IS UNIQUE",
    "CREATE INDEX IF NOT EXISTS FOR (n:Figure) ON (n.paper_id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Paper) ON (n.doi)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Paper) ON (n.batch)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Catalyst) ON (n.chemical_formula)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Catalyst) ON (n.material_family)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Entity) ON (n.label)",
    # 向量索引（bge-small-zh-v1.5 = 512 维）
    "CREATE VECTOR INDEX vec_paper IF NOT EXISTS FOR (p:Paper) ON (p.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 512, `vector.similarity_function`: 'cosine'}}",
    "CREATE VECTOR INDEX vec_catalyst IF NOT EXISTS FOR (c:Catalyst) ON (c.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 512, `vector.similarity_function`: 'cosine'}}",
    "CREATE VECTOR INDEX vec_experiment IF NOT EXISTS FOR (e:Experiment) ON (e.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 512, `vector.similarity_function`: 'cosine'}}",
    "CREATE VECTOR INDEX vec_contradiction IF NOT EXISTS FOR (c:Contradiction) ON (c.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 512, `vector.similarity_function`: 'cosine'}}",
    "CREATE VECTOR INDEX vec_entity IF NOT EXISTS FOR (e:Entity) ON (e.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 512, `vector.similarity_function`: 'cosine'}}",
]

EMBED_LABELS = ("Paper", "Catalyst", "Experiment", "Contradiction", "Entity")


def load_env(path):
    cfg = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def clean(props):
    """只保留 Neo4j 可存类型；空值丢弃。

    嵌套数组 / 对象 → `<key>_json` 字符串 + `<key>_n` 计数（**不再丢点位**）；
    纯标量数组 → 原样保留（Neo4j 支持同类型数组）。
    """
    out = {}
    for k, v in props.items():
        if v is None or v == "":
            continue
        if isinstance(v, (bool, int, float, str)):
            out[k] = v
        elif isinstance(v, list):
            if not v:
                continue
            keep = [x for x in v if isinstance(x, (bool, int, float, str))]
            kinds_ = {('num' if (isinstance(x, (int, float)) and not isinstance(x, bool))
                       else 'bool' if isinstance(x, bool) else 'str') for x in keep}
            # 【2026-10-10 修】混合类型数组（如 ['Fe', 1]）Neo4j 拒绝写入 → 一并序列化，
            # 不再让单条属性异常把整批写入打断（旧版只在有嵌套/空值时才序列化）。
            if len(keep) == len(v) and len(kinds_) <= 1:
                out[k] = keep
            else:                                   # 含嵌套/空值/混合类型 → 序列化
                out[k + "_json"] = json.dumps(v, ensure_ascii=False)
                out[k + "_n"] = len(v)
        elif isinstance(v, dict):
            if v:
                out[k + "_json"] = json.dumps(v, ensure_ascii=False)
    return out


def fig_name(node_id):
    return re.sub(r"^FIG::", "", str(node_id))


KEY_BY_LABEL = {"Paper": "paper_id"}


def uid_of(label):
    return KEY_BY_LABEL.get(label, "node_id")


def embed_pending(s, cfg, labels=EMBED_LABELS):
    """给有 search_text 但无 embedding 的节点生成向量（bge-small-zh-v1.5, 512 维）。"""
    cache = cfg.get("EMBED_CACHE") or os.environ.get("EMBED_CACHE")
    if cache:
        os.environ.setdefault("FASTEMBED_CACHE_PATH", cache)
    def _pending():
        """真实待嵌入数（有 search_text、无 embedding）。

        【2026-10-10 修·自检不说谎】旧版在 fastembed 缺失/模型加载失败时 `return 0, {l:0 …}`，
        报告里 embedded_pending 全 0 —— 与「已经全部嵌入」无法区分，实测把「21 Catalyst /
        34 Experiment / 15 Contradiction 待嵌入」误报成「无待嵌入」。现跳过时也如实统计。
        """
        c = {}
        for l in labels:
            c[l] = s.run("MATCH (n:%s) WHERE n.search_text IS NOT NULL AND n.embedding IS NULL "
                         "RETURN count(n) AS c" % l).single()['c']
        return c

    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("  ⚠ fastembed 未安装，跳过向量化（uv pip install fastembed）")
        return 0, _pending()
    try:
        model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")
    except Exception as ex:
        print("  ⚠ 向量模型加载失败，跳过：%s" % str(ex)[:80])
        return 0, _pending()
    emb, per = 0, {}
    for label in labels:
        rows = s.run("MATCH (n:%s) WHERE n.search_text IS NOT NULL AND n.embedding IS NULL "
                     "RETURN coalesce(n.node_id, n.paper_id) AS id, n.search_text AS t" % label).data()
        items = [(r["id"], r["t"]) for r in rows if r.get("t")]
        for i in range(0, len(items), 64):
            part = items[i:i + 64]
            vecs = list(model.embed([t for _, t in part]))
            for (nid, _), v in zip(part, vecs):
                s.run("MATCH (n:%s) WHERE (n.node_id = $id OR n.paper_id = $id) "
                      "SET n.embedding = $v, n.embedding_model = 'bge-small-zh-v1.5'" % label,
                      id=nid, v=[float(x) for x in v])
                emb += 1
        per[label] = len(items)
    return emb, per


def purge_scope(s, dirs=None, batches=None, pids=None, dry=False):
    """【2026-10-10 新增】入库前清理同批旧节点 —— 否则新旧 schema 两套节点并存。

    背景：paper_id 是**内容哈希**（'P_' + md5(论文夹路径)），步骤十规则一变就可能换 id；
    唯一键 MERGE 只能防「同一 id 重复」，完全挡不住「同一篇论文两个 id」
    （实测 B101 同 5 篇论文各有 2 个 Paper 节点：Experiment 68≠34、Curve 124≠88、Catalyst 42≠21）。
    故按论文夹名(dir_name) / 批次(batch) 反查**新旧全部** paper_id，再按 paper_id 连带删除，
    避免只删新 id 而把旧 id 的遗留节点留在库里。

    Entity/Pollutant 是跨论文共享实体（无 paper_id），按设计**保留**（其边重导时重建）。
    """
    dirs = sorted({d for d in (dirs or []) if d})
    batches = sorted({b for b in (batches or []) if b})
    pids = set(p for p in (pids or []) if p)
    if not (dirs or batches or pids):
        return {"deleted_nodes": 0, "deleted_rels": 0, "pids": [], "note": "无匹配范围，未清理"}
    old = set()
    for r in s.run("MATCH (p:Paper) WHERE p.dir_name IN $d OR p.batch IN $b "
                   "RETURN DISTINCT p.paper_id AS pid", d=dirs, b=batches).data():
        if r.get("pid"):
            old.add(r["pid"])
    scope = sorted(old | pids)
    # 先统计（dry-run 也能给出「会删多少」），再删
    tgt = ("MATCH (n) WHERE n.paper_id IN $pids "
           "OR (n:Paper AND (n.dir_name IN $d OR n.batch IN $b)) "
           "OR (n:Figure AND (n.dir_name IN $d OR n.batch IN $b))")
    cnt_n = cnt_r = 0
    for r in s.run(tgt + " RETURN count(n) AS c", pids=scope, d=dirs, b=batches).data():
        cnt_n = r["c"]
    for r in s.run(tgt + " OPTIONAL MATCH (n)-[x]-() RETURN count(DISTINCT x) AS c",
                   pids=scope, d=dirs, b=batches).data():
        cnt_r = r["c"]
    res = {"deleted_nodes": cnt_n, "deleted_rels": cnt_r, "pids": scope}
    if dry:
        res["note"] = "dry-run 仅统计"
        return res
    s.run(tgt + " DETACH DELETE n", pids=scope, d=dirs, b=batches)
    # Paper 上无 dir_name/batch 的历史脏节点：按本批 dir_name 兜底再扫一遍
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export_dir")
    ap.add_argument("--env", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "neo4j.env"))
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    ap.add_argument("--no-embed", action="store_true", help="跳过向量化（只写节点与边）")
    ap.add_argument("--purge-export", action="store_true",
                    help="写入前清理【本导出目录所含论文】的旧节点（按 dir_name 反查新旧全部 paper_id）")
    ap.add_argument("--purge-batch", nargs="*", default=None,
                    help="写入前清理指定批次的全部论文旧节点，例：--purge-batch B101")
    ap.add_argument("--purge-paper", nargs="*", default=None,
                    help="写入前清理指定论文夹(dir_name)的旧节点，例：--purge-paper cual_kit6")
    a = ap.parse_args()

    if not os.path.isdir(a.export_dir):
        print(f"✗ 目录不存在：{a.export_dir}")
        return 1
    cfg = load_env(a.env)
    if not cfg.get("NEO4J_URI"):
        print(f"✗ 读不到 NEO4J_URI（env: {a.env}）")
        return 1

    # ── 收集节点 + 唯一键重复检查 ──
    groups, id2label, dup = [], {}, {}
    nested_keys = {}
    for fn, label, uid in NODE_FILES:
        p = os.path.join(a.export_dir, fn)
        if not os.path.exists(p):
            continue
        rows = json.load(open(p, encoding="utf-8"))
        if not isinstance(rows, list):
            rows = [rows]
        g, seen = [], set()
        for r in rows:
            if not isinstance(r, dict) or not r.get(uid):
                continue
            lab = "Figure" if str(r.get("label") or "") == "Figure" else label
            props = clean(r)
            for k, v in r.items():                    # 记录被序列化的键（点位等）
                if isinstance(v, list) and len(v) != len([x for x in v if isinstance(x, (bool, int, float, str))]):
                    nested_keys[k] = nested_keys.get(k, 0) + 1
            if r.get(uid) in seen:
                dup.setdefault(lab, []).append(r.get(uid))
            seen.add(r.get(uid))
            g.append((lab, uid, props, r.get(uid)))
        groups.append((fn, label, g))
        for lab, _, _, key in g:
            id2label[key] = lab

    edges = []
    ep = os.path.join(a.export_dir, "edges.json")
    if os.path.exists(ep):
        edges = json.load(open(ep, encoding="utf-8")) or []

    kinds = {}
    for e in edges:
        kinds[e.get("type")] = kinds.get(e.get("type"), 0) + 1
    # 【2026-10-10】期望带属性边数（除 type/from/to 外还有键的边）
    edge_props_expected = sum(1 for e in edges if any(k not in ("type", "from", "to") for k in e))

    total_nodes = sum(len(g) for _, _, g in groups)
    print(f"节点 {total_nodes} 条 / 边 {len(edges)} 条；URI={cfg['NEO4J_URI']}")
    for fn, label, g in groups:
        print(f"  {label:14s} {len(g):3d}  ({fn})")
    print("  边类型:", kinds)
    if nested_keys:
        print("  嵌套数组已序列化（*_json + *_n）:", nested_keys)
    if dup:
        print("  ⚠ 唯一键重复（批内）：", {k: len(v) for k, v in dup.items()})
    if a.dry_run:
        print("（--dry-run，未写入）")
        rp = os.path.join(a.export_dir, "_import_report.dryrun.json")   # 【2026-10-10】dry-run 也落报告
        with open(rp, "w", encoding="utf-8") as fo:
            json.dump({"dry_run": True, "export_dir": a.export_dir,
                       "nodes": {l: len(g) for _, l, g in groups},
                       "edges_expected": kinds, "edge_props_expected": edge_props_expected,
                       "dup_keys": dup, "nested_serialized": nested_keys}, fo,
                      ensure_ascii=False, indent=1)
        print("报告：%s" % rp)
        return 0

    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(cfg["NEO4J_URI"], auth=(cfg.get("NEO4J_USERNAME", ""), cfg.get("NEO4J_PASSWORD", "")))
    db = cfg.get("NEO4J_DATABASE") or None
    written_n = written_e = created_rel = edge_props_written = 0
    skipped = []
    purged = {}

    with drv.session(database=db) as s:
        for c in CONSTRAINTS:
            try:
                s.run(c)
            except Exception as ex:
                print(f"  ⚠ 约束跳过：{str(ex)[:80]}")

        purged = {}
        if a.purge_export or a.purge_batch or a.purge_paper:
            pmeta = [p for _, lab, g in groups if lab == "Paper" for _, _, p, _ in g]
            dirs = {p.get("dir_name") for p in pmeta if p.get("dir_name")} | set(a.purge_paper or [])
            bsets = set(a.purge_batch or [])
            if a.purge_export:
                bsets |= {p.get("batch") for p in pmeta if p.get("batch")}
            purged = purge_scope(s, dirs=dirs, batches=bsets,
                                 pids={p.get("paper_id") for p in pmeta})
            print("  清理旧节点：删除 %s 节点 / %s 关系（范围 dirs=%s batches=%s）"
                  % (purged["deleted_nodes"], purged["deleted_rels"], sorted(dirs), sorted(bsets)))

        for fn, label, g in groups:
            for lab, uid, props, key in g:
                if not props.get(uid):
                    continue
                # 【2026-10-07】先清后写：杜绝上游已删字段留下的脏属性
                s.run(f"MERGE (x:{lab} {{{uid}: $key}}) SET x = $props", key=key, props=props)
                written_n += 1

        for e in edges:
            typ = str(e.get("type") or "").strip()
            f, t = e.get("from"), e.get("to")
            if not typ or not f or not t:
                skipped.append({"from": f, "to": t, "type": typ, "reason": "缺 type/from/to"})
                continue
            if f not in id2label:                      # 起点缺失：补 Entity 占位，避免丢边
                s.run("MERGE (x:Entity {node_id: $k}) SET x.label='Unknown', x.name=$k", k=f)
                id2label[f] = "Entity"
                skipped.append({"from": f, "to": t, "type": typ, "reason": "起点缺失→补 Entity 占位"})
            if t not in id2label:
                if str(t).startswith("FIG::"):
                    s.run("MERGE (x:Figure {node_id: $k}) SET x.label='Figure', x.name=$nm",
                          k=t, nm=fig_name(t))
                    id2label[t] = "Figure"
                else:
                    s.run("MERGE (x:Entity {node_id: $k}) SET x.label='Unknown', x.name=$k", k=t)
                    id2label[t] = "Entity"
                skipped.append({"from": f, "to": t, "type": typ, "reason": "终点缺失→补 Entity 占位"})
            lf, lt = id2label[f], id2label[t]
            # 【2026-10-10 修 / P0】边属性必须写：旧版只 MERGE 关系、从不 SET →
            # 边的 pollutant 等属性 100% 丢失（实测 B101 DEGRADES 34 条带 pollutant 者 0 条）。
            eprops = clean({k: v for k, v in e.items() if k not in ("type", "from", "to")})
            try:
                cy = (f"MATCH (a:{lf} {{{uid_of(lf)}: $f}}) MATCH (b:{lt} {{{uid_of(lt)}: $t}}) "
                      f"MERGE (a)-[r:{typ}]->(b)")
                if eprops:
                    res = s.run(cy + " SET r = $props", f=f, t=t, props=eprops).consume()
                    edge_props_written += 1
                else:
                    res = s.run(cy, f=f, t=t).consume()
            except Exception as ex:                    # 【2026-10-10】单条异常不再中断整批
                skipped.append({"from": f, "to": t, "type": typ,
                                "reason": "写边异常 %s" % str(ex)[:80]})
                continue
            created_rel += res.counters.relationships_created
            written_e += 1

        # 边对账：按类型统计（全库 + 【2026-10-10】本批视角，防全库历史数据掩盖本批缺口）
        actual_rel, actual_rel_batch = {}, {}
        if kinds:
            try:
                actual_rel = {r["t"]: r["c"] for r in s.run(
                    "MATCH ()-[r]->() WHERE type(r) IN $types RETURN type(r) AS t, count(r) AS c",
                    types=sorted(kinds)).data()}
            except Exception as ex:
                print("  ⚠ 边统计失败：%s" % str(ex)[:80])
            try:
                ids = sorted(id2label.keys())
                actual_rel_batch = {r["t"]: r["c"] for r in s.run(
                    "MATCH (a)-[r]->(b) WHERE type(r) IN $types "
                    "AND (a.node_id IN $ids OR a.paper_id IN $ids) "
                    "AND (b.node_id IN $ids OR b.paper_id IN $ids) "
                    "RETURN type(r) AS t, count(r) AS c", types=sorted(kinds), ids=ids).data()}
            except Exception as ex:
                print("  ⚠ 本批边统计失败：%s" % str(ex)[:80])

        n_emb, emb_per = (0, {}) if a.no_embed else embed_pending(s, cfg)
        cnt = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        cnt_rel = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

    drv.close()
    print(f"✓ 写入 {written_n} 节点 / {written_e} 边（其中新建关系 {created_rel} 条）；"
          f"图谱现有 节点 {cnt} / 关系 {cnt_rel}")
    diff = {k: {"期望": v, "本批实际": actual_rel_batch.get(k, 0), "全库实际": actual_rel.get(k, 0)}
            for k, v in kinds.items() if actual_rel_batch.get(k, 0) != v}
    if diff:
        print("  ⚠ 边计数差异（本批视角，清理后重导应为 0）：", json.dumps(diff, ensure_ascii=False))
    print("  ✓ 边属性：%d 条带属性边 / 应带 %d 条" % (edge_props_written, edge_props_expected))
    if edge_props_expected and edge_props_written < edge_props_expected:
        print("  ⚠ 有 %d 条边属性未写入" % (edge_props_expected - edge_props_written))
    if skipped:
        print(f"  ⚠ 兜底补建的边 {len(skipped)} 条（详见报告）")
    emb_skipped = bool(not a.no_embed and n_emb == 0 and sum(emb_per.values()) > 0)
    if not a.no_embed:
        print(f"✓ 向量化 {n_emb} 个节点（bge-small-zh-v1.5）；各标签待嵌入数 {emb_per}")
        if emb_skipped:
            print("  ⚠ 向量化未执行（fastembed 缺失 / 模型加载失败）：仍有 %d 个节点待嵌入"
                  % sum(emb_per.values()))

    report = {"export_dir": a.export_dir, "nodes": {l: len(g) for _, l, g in groups},
              "edges_expected": kinds, "edges_reported": actual_rel,
              "edges_reported_batch": actual_rel_batch,
              "edges_written": written_e, "relationships_created": created_rel,
              "edge_props_expected": edge_props_expected, "edge_props_written": edge_props_written,
              "purged": purged,
              "skipped_edges": skipped, "dup_keys": dup, "nested_serialized": nested_keys,
              "embedded": n_emb, "embedded_pending": emb_per, "embed_skipped": emb_skipped,
              "totals": {"nodes": cnt, "relationships": cnt_rel}}
    rp = os.path.join(a.export_dir, "_import_report.json")
    with open(rp, "w", encoding="utf-8") as fo:
        json.dump(report, fo, ensure_ascii=False, indent=1)
    print("报告：%s" % rp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
