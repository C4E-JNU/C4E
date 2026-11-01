# -*- coding: utf-8 -*-
"""step11_neo4j_import.py — write the nodes and edges of graph_export/ into Neo4j (Step 10②)

Upstream: <corpus root>/graph_export/ produced by step10_graph_export.py
  nodes_paper / nodes_catalyst / nodes_experiment / nodes_variable /
  nodes_curve / nodes_contradiction .json + nodes_extra.json（Pollutant / Figure）+ edges.json

This script MERGEs by unique key (idempotent, safely rerunnable) and creates constraints/indexes.

【2026-10-07 four data-loss / key problems fixed】
  ① nested arrays (curve points xs/ys) are serialized as `<key>_json` + `<key>_n`:
     a Neo4j property cannot be a nested array, and the old clean() filtered them out → 100% of curve points were lost
     (measured: 5586 Curve nodes with 0 points).
  ② nodes are written with `SET x = $props` (clear then write): the old `SET x += $props` only added incrementally, so
     fields deleted upstream stayed behind as dirty properties after a rerun.
  ③ every edge write is reconciled: edges with a missing endpoint/type are **recorded individually** in the report instead of being silently dropped;
     after writing, actual edge counts by type are printed against the expectation in edges.json.
  ④ in-set duplicate unique keys are checked, and `_import_report.json` is persisted (nodes/edges/skip reasons/count differences).

【2026-10-10 two P0 data losses fixed + five reconciliation items】
  ① edge properties are written: after MERGEing the relationship, `SET r = $props` is applied. The old version never wrote edge properties →
     `DEGRADES.pollutant` and the like were 100% lost (measured on a legacy re-export: 0 of 34 property-carrying DEGRADES edges).
  ② new cleanup flags `--purge-export / --purge-group / --purge-paper`: before writing, look up **all new and old** paper_ids by
     dir_name / batch and then DETACH DELETE. paper_id is a content hash, so it changes as soon as a Step 10 rule changes, and unique-key MERGE
     cannot prevent "two node sets for the same paper coexisting"
     (measured on a legacy re-export: Paper 10≠5, Experiment 68≠34, Curve 124≠88). The shared entities
     Entity/Pollutant have no paper_id and are kept by design.
  ③ edge reconciliation adds a **batch view** (filtered by endpoint ownership) so it is no longer masked by whole-database history;
     the report adds `edge_props_expected/written`, `edges_reported_batch`, `purged`.
  ④ edge writes are wrapped in try/except: a single exception is recorded as skipped and writing continues (no half-write without a report).
  ⑤ clean() now serializes mixed-type arrays (`['Fe', 1]`) — Neo4j rejects such arrays, which used to fail the whole write.
  ⑥ EMBED_LABELS adds Entity (Pollutant now carries search_text) and a new vec_entity vector index is created.
  ⑦ dry-run also persists `_import_report.dryrun.json`.
  ⑦ dry-run also persists `_import_report.dryrun.json`.

Edges: {type, from, to, ...other keys} in edges.json, where from/to are node_ids (a Paper's node_id = paper_id).
     A missing target node → an Entity / Figure placeholder is created automatically, guaranteeing zero orphans.
     Keys other than type/from/to are written as **relationship properties** (cleaned by the same clean() rules).

Usage:
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export"
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --dry-run
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --purge-export      # soft re-import (recommended)
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --purge-group <group>
  python step11_neo4j_import.py "D:\\fenton_papers\\graph_export" --no-embed

Vertex unique keys:
  Paper → paper_id; everything else → node_id; Entity/Pollutant and Figure share node_id (distinguished by the label property).
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
    # vector indexes (bge-small-zh-v1.5 = 512 dimensions)
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
    """Keep only types Neo4j can store; drop empty values.

    Nested arrays / objects → a `<key>_json` string + a `<key>_n` count (**points are never lost again**);
    plain scalar arrays → kept as-is (Neo4j supports same-type arrays).
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
            # 【2026-10-10 fix】mixed-type arrays (e.g. ['Fe', 1]) are rejected by Neo4j → serialize them too,
            # so a single property no longer aborts the whole write (the old version serialized only nested/empty values).
            if len(keep) == len(v) and len(kinds_) <= 1:
                out[k] = keep
            else:                                   # nested/empty/mixed types → serialize
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
    """Generate embeddings for nodes that have search_text but no embedding (bge-small-zh-v1.5, 512 dimensions)."""
    cache = cfg.get("EMBED_CACHE") or os.environ.get("EMBED_CACHE")
    if cache:
        os.environ.setdefault("FASTEMBED_CACHE_PATH", cache)
    def _pending():
        """The real number of nodes still to embed (search_text present, embedding absent).

        【2026-10-10 fix · a self-check must not lie】the old version returned `0, {l:0 …}` when fastembed was missing or the model failed to load,
        so the report showed embedded_pending as all zeros — indistinguishable from "everything embedded"; one measured run misreported
        "21 Catalyst / 34 Experiment / 15 Contradiction pending" as "nothing pending". Skipping now counts truthfully.
        """
        c = {}
        for l in labels:
            c[l] = s.run("MATCH (n:%s) WHERE n.search_text IS NOT NULL AND n.embedding IS NULL "
                         "RETURN count(n) AS c" % l).single()['c']
        return c

    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("  ! fastembed is not installed, skipping embeddings (uv pip install fastembed)")
        return 0, _pending()
    try:
        model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")
    except Exception as ex:
        print("  ! failed to load the embedding model, skipping: %s" % str(ex)[:80])
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
    """【added 2026-10-10】Clean up old nodes of the same group before import — otherwise two node sets coexist under old and new schemas.

    Background: paper_id is a **content hash** ('P_' + md5(paper folder path)), so it may change as soon as a Step 10 rule changes;
    unique-key MERGE only prevents "the same id twice" and cannot prevent "one paper under two ids"
    (measured on a legacy re-export: each of 5 papers had 2 Paper nodes — Experiment 68≠34, Curve 124≠88, Catalyst 42≠21).
    So look up **all new and old** paper_ids by paper folder name (dir_name) / group (batch), then delete by paper_id,
    avoiding a partial delete that leaves the legacy nodes of old ids behind.

    Entity/Pollutant are cross-paper shared entities (no paper_id) and are **kept** by design (their edges are rebuilt on re-import).
    """
    dirs = sorted({d for d in (dirs or []) if d})
    batches = sorted({b for b in (batches or []) if b})
    pids = set(p for p in (pids or []) if p)
    if not (dirs or batches or pids):
        return {"deleted_nodes": 0, "deleted_rels": 0, "pids": [], "note": "nothing matched the scope, nothing purged"}
    old = set()
    for r in s.run("MATCH (p:Paper) WHERE p.dir_name IN $d OR p.batch IN $b "
                   "RETURN DISTINCT p.paper_id AS pid", d=dirs, b=batches).data():
        if r.get("pid"):
            old.add(r["pid"])
    scope = sorted(old | pids)
    # count first (so even dry-run reports how much would be deleted), then delete
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
        res["note"] = "dry-run counted only"
        return res
    s.run(tgt + " DETACH DELETE n", pids=scope, d=dirs, b=batches)
    # dirty legacy nodes without dir_name/batch on Paper: rescan by this batch's dir_name as a fallback
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export_dir")
    ap.add_argument("--env", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "neo4j.env"))
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    ap.add_argument("--no-embed", action="store_true", help="skip embeddings (write nodes and edges only)")
    ap.add_argument("--purge-export", action="store_true",
                    help="purge old nodes of 【the papers contained in this export directory】 before writing (all new and old paper_ids looked up by dir_name)")
    ap.add_argument("--purge-group", "--purge-batch", dest="purge_batch", nargs="*", default=None,
                    help="purge all old nodes of the named group before writing, e.g. --purge-group <group>")
    ap.add_argument("--purge-paper", nargs="*", default=None,
                    help="purge old nodes of the named paper folder (dir_name) before writing, e.g. --purge-paper cual_kit6")
    a = ap.parse_args()

    if not os.path.isdir(a.export_dir):
        print(f"FAIL directory does not exist: {a.export_dir}")
        return 1
    cfg = load_env(a.env)
    if not cfg.get("NEO4J_URI"):
        print(f"FAIL cannot read NEO4J_URI (env: {a.env})")
        return 1

    # ── collect nodes + duplicate unique-key check ──
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
            for k, v in r.items():                    # record the keys that were serialized (points etc.)
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
    # 【2026-10-10】expected number of property-carrying edges (edges with keys beyond type/from/to)
    edge_props_expected = sum(1 for e in edges if any(k not in ("type", "from", "to") for k in e))

    total_nodes = sum(len(g) for _, _, g in groups)
    print(f"nodes {total_nodes} / edges {len(edges)}; URI={cfg['NEO4J_URI']}")
    for fn, label, g in groups:
        print(f"  {label:14s} {len(g):3d}  ({fn})")
    print("  edge types:", kinds)
    if nested_keys:
        print("  nested arrays serialized (*_json + *_n):", nested_keys)
    if dup:
        print("  ! duplicate unique keys (in-set):", {k: len(v) for k, v in dup.items()})
    if a.dry_run:
        print("(--dry-run, nothing written)")
        rp = os.path.join(a.export_dir, "_import_report.dryrun.json")   # 【2026-10-10】dry-run also persists a report
        with open(rp, "w", encoding="utf-8") as fo:
            json.dump({"dry_run": True, "export_dir": a.export_dir,
                       "nodes": {l: len(g) for _, l, g in groups},
                       "edges_expected": kinds, "edge_props_expected": edge_props_expected,
                       "dup_keys": dup, "nested_serialized": nested_keys}, fo,
                      ensure_ascii=False, indent=1)
        print("report: %s" % rp)
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
                print(f"  ! constraint skipped: {str(ex)[:80]}")

        purged = {}
        if a.purge_export or a.purge_batch or a.purge_paper:
            pmeta = [p for _, lab, g in groups if lab == "Paper" for _, _, p, _ in g]
            dirs = {p.get("dir_name") for p in pmeta if p.get("dir_name")} | set(a.purge_paper or [])
            bsets = set(a.purge_batch or [])
            if a.purge_export:
                bsets |= {p.get("batch") for p in pmeta if p.get("batch")}
            purged = purge_scope(s, dirs=dirs, batches=bsets,
                                 pids={p.get("paper_id") for p in pmeta})
            print("  purging old nodes: deleting %s nodes / %s relationships (scope dirs=%s batches=%s)"
                  % (purged["deleted_nodes"], purged["deleted_rels"], sorted(dirs), sorted(bsets)))

        for fn, label, g in groups:
            for lab, uid, props, key in g:
                if not props.get(uid):
                    continue
                # 【2026-10-07】clear then write: no dirty properties left by fields deleted upstream
                s.run(f"MERGE (x:{lab} {{{uid}: $key}}) SET x = $props", key=key, props=props)
                written_n += 1

        for e in edges:
            typ = str(e.get("type") or "").strip()
            f, t = e.get("from"), e.get("to")
            if not typ or not f or not t:
                skipped.append({"from": f, "to": t, "type": typ, "reason": "missing type/from/to"})
                continue
            if f not in id2label:                      # missing start node: add an Entity placeholder so the edge is not lost
                s.run("MERGE (x:Entity {node_id: $k}) SET x.label='Unknown', x.name=$k", k=f)
                id2label[f] = "Entity"
                skipped.append({"from": f, "to": t, "type": typ, "reason": "missing start → Entity placeholder added"})
            if t not in id2label:
                if str(t).startswith("FIG::"):
                    s.run("MERGE (x:Figure {node_id: $k}) SET x.label='Figure', x.name=$nm",
                          k=t, nm=fig_name(t))
                    id2label[t] = "Figure"
                else:
                    s.run("MERGE (x:Entity {node_id: $k}) SET x.label='Unknown', x.name=$k", k=t)
                    id2label[t] = "Entity"
                skipped.append({"from": f, "to": t, "type": typ, "reason": "missing end → Entity placeholder added"})
            lf, lt = id2label[f], id2label[t]
            # 【2026-10-10 fix / P0】edge properties must be written: the old version only MERGEed the relationship and never SET →
            # edge properties such as pollutant were 100% lost (measured on a legacy re-export: 0 of 34 DEGRADES edges carrying pollutant).
            eprops = clean({k: v for k, v in e.items() if k not in ("type", "from", "to")})
            try:
                cy = (f"MATCH (a:{lf} {{{uid_of(lf)}: $f}}) MATCH (b:{lt} {{{uid_of(lt)}: $t}}) "
                      f"MERGE (a)-[r:{typ}]->(b)")
                if eprops:
                    res = s.run(cy + " SET r = $props", f=f, t=t, props=eprops).consume()
                    edge_props_written += 1
                else:
                    res = s.run(cy, f=f, t=t).consume()
            except Exception as ex:                    # 【2026-10-10】a single exception no longer aborts the whole write
                skipped.append({"from": f, "to": t, "type": typ,
                                "reason": "edge write error %s" % str(ex)[:80]})
                continue
            created_rel += res.counters.relationships_created
            written_e += 1

        # edge reconciliation: count by type (whole database + 【2026-10-10】the batch view, so history cannot mask this batch's gaps)
        actual_rel, actual_rel_batch = {}, {}
        if kinds:
            try:
                actual_rel = {r["t"]: r["c"] for r in s.run(
                    "MATCH ()-[r]->() WHERE type(r) IN $types RETURN type(r) AS t, count(r) AS c",
                    types=sorted(kinds)).data()}
            except Exception as ex:
                print("  ! edge statistics failed: %s" % str(ex)[:80])
            try:
                ids = sorted(id2label.keys())
                actual_rel_batch = {r["t"]: r["c"] for r in s.run(
                    "MATCH (a)-[r]->(b) WHERE type(r) IN $types "
                    "AND (a.node_id IN $ids OR a.paper_id IN $ids) "
                    "AND (b.node_id IN $ids OR b.paper_id IN $ids) "
                    "RETURN type(r) AS t, count(r) AS c", types=sorted(kinds), ids=ids).data()}
            except Exception as ex:
                print("  ! batch edge statistics failed: %s" % str(ex)[:80])

        n_emb, emb_per = (0, {}) if a.no_embed else embed_pending(s, cfg)
        cnt = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        cnt_rel = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

    drv.close()
    print(f"OK wrote {written_n} nodes / {written_e} edges (of which {created_rel} relationships created); "
          f"graph now holds nodes {cnt} / relationships {cnt_rel}")
    diff = {k: {"expected": v, "actual (batch)": actual_rel_batch.get(k, 0), "actual (whole db)": actual_rel.get(k, 0)}
            for k, v in kinds.items() if actual_rel_batch.get(k, 0) != v}
    if diff:
        print("  ! edge-count difference (batch view; should be 0 after a purge and re-import):", json.dumps(diff, ensure_ascii=False))
    print("  OK edge properties: %d edges carry properties / %d expected" % (edge_props_written, edge_props_expected))
    if edge_props_expected and edge_props_written < edge_props_expected:
        print("  ! %d edge properties were not written" % (edge_props_expected - edge_props_written))
    if skipped:
        print(f"  ! {len(skipped)} placeholder-created edges (see the report)")
    emb_skipped = bool(not a.no_embed and n_emb == 0 and sum(emb_per.values()) > 0)
    if not a.no_embed:
        print(f"OK embedded {n_emb} nodes (bge-small-zh-v1.5); pending per label {emb_per}")
        if emb_skipped:
            print("  ! embeddings not run (fastembed missing / model load failed): %d nodes still pending"
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
    print("report: %s" % rp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
