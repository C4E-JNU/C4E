#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 11 (v2, 2026-11-02): Neo4j import for the standardized Fenton corpus.

Generic, schema-driven importer for the `graph_export/` directory produced by
step10_graph_export.py (v2). Every node file is {"label","key_field","nodes":[{"_key":...}]};
every edge file is {"type","from_label","from_key","to_label","to_key","edges":[{"from_key":..,"to_key":..,...}]}.

Behavior:
  - creates uniqueness constraints per node label;
  - MERGEs nodes by their key field (idempotent), sets all properties;
  - MATCHes both endpoints per edge and MERGEs the relationship (type + endpoints),
    setting every remaining edge property (raw provenance included);
  - `--dry-run` prints counts and writes nothing;
  - `--purge-paper <dir_name>` / `--purge-all-papers` remove old node sets first
    (paper_id is the corpus directory name, stable across schema revisions);
  - connection settings come from neo4j.env next to this script.

Verification: run with --dry-run first; after a real import reconcile
`MATCH ()-[r]->() RETURN type(r), count(*)` against manifest.json `edges`.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load_env():
    env = HERE / "neo4j.env"
    cfg = {}
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg

def build_search_text(label, props):
    """Compose the semantic search text for a node (English-only corpus)."""
    parts = [str(v) for k, v in props.items()
             if isinstance(v, (str, int, float)) and v not in ("", None)
             and k not in ("points_json",) and not k.endswith("_raw")]
    raws = [v for k, v in props.items() if k.endswith("_raw") and isinstance(v, str)]
    return ". ".join(parts + raws)[:4000]

def main() -> int:
    root = Path(os.environ.get("FENTON_DATA_ROOT") or repo_root())
    gdir = root / "graph_export"
    if not gdir.is_dir():
        raise SystemExit(f"graph_export directory not found: {gdir}")
    dry = "--dry-run" in sys.argv
    purge_all = "--purge-all-papers" in sys.argv
    do_embed = "--embed" in sys.argv
    purge_papers = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--purge-paper"]

    manifest = json.loads((gdir / "manifest.json").read_text(encoding="utf-8"))
    node_files = sorted(gdir.glob("nodes_*.json"))
    edge_files = sorted(gdir.glob("edges_*.json"))
    total_nodes = sum(len(json.loads(f.read_text(encoding="utf-8"))["nodes"]) for f in node_files)
    total_edges = sum(len(json.loads(f.read_text(encoding="utf-8"))["edges"]) for f in edge_files)
    print(f"graph_export: {len(node_files)} node files ({total_nodes} nodes), "
          f"{len(edge_files)} edge files ({total_edges} edges)")
    if dry:
        print("DRY RUN - nothing written")
        return 0

    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise SystemExit("neo4j driver missing: pip install neo4j")

    cfg = load_env()
    uri = cfg.get("NEO4J_URI", "bolt://localhost:7687")
    auth = (cfg.get("NEO4J_USER", "neo4j"), cfg.get("NEO4J_PASSWORD", ""))
    driver = GraphDatabase.driver(uri, auth=auth)

    def run_tx(session, cypher, **params):
        session.run(cypher, **params).consume()

    with driver.session() as s:
        if purge_all or purge_papers:
            names = purge_papers
            if purge_all:
                names = [n["props"]["paper_id"] for f in node_files
                         if json.loads(f.read_text(encoding="utf-8"))["label"] == "Paper"
                         for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]]
            for nm in names:
                run_tx(s, "MATCH (n) WHERE n.paper_id = $p DETACH DELETE n", p=nm)
            print(f"purged nodes for papers: {names}")

        # constraints
        for f in node_files:
            d = json.loads(f.read_text(encoding="utf-8"))
            lbl, kf = d["label"], d["key_field"]
            run_tx(s, f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:`{lbl}`) REQUIRE n.{kf} IS UNIQUE")

        # nodes (properties live under the "props" sub-dict)
        embedder = None
        if do_embed:
            try:
                from fastembed import TextEmbedding
                try:
                    embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
                except Exception:
                    # huggingface.co unreachable / Xet-blocked: retry via mirror without Xet
                    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                    os.environ["HF_HUB_DISABLE_XET"] = "1"
                    embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
            except Exception as ex:
                print(f"embedding unavailable ({ex}); import continues without vectors - "
                      f"rerun with --embed once fastembed is installed: pip install fastembed")
        import numpy as np
        def embed(texts):
            return [list(map(float, v)) for v in embedder.embed(texts)] if embedder is not None else None
        for f in node_files:
            d = json.loads(f.read_text(encoding="utf-8"))
            lbl, kf = d["label"], d["key_field"]
            batch_texts, batch_nodes = [], []
            def flush():
                if embedder is None or not batch_texts:
                    return
                vecs = embed(batch_texts)
                for nd, vec in zip(batch_nodes, vecs):
                    nd["embedding"] = vec
                batch_texts.clear(); batch_nodes.clear()
            for wrapper in d["nodes"]:
                nprops = wrapper.get("props", {})
                key = nprops.get(kf)
                if key is None:
                    continue
                if embedder is not None:
                    st = build_search_text(lbl, nprops)
                    if st:
                        batch_texts.append(st); batch_nodes.append(nprops)
                        if len(batch_texts) >= 256:
                            flush()
                run_tx(s, f"MERGE (n:`{lbl}` {{{kf}: $key}}) SET n = $props",
                       key=key, props=nprops)
            flush()
            print(f"  nodes {lbl}: {len(d['nodes'])}")
        # vector indexes (Neo4j 5.13+); dims must match the embedding model (bge-small-en-v1.5 = 384)
        if do_embed and embedder is not None:
            for lbl in ("Paper", "Catalyst", "Experiment", "VariableExperiment"):
                run_tx(s, f"CREATE VECTOR INDEX IF NOT EXISTS vec_{lbl.lower()} FOR (n:`{lbl}`) "
                          f"ON n.embedding OPTIONS {{indexConfig: {{`vector.dimensions`: 384, "
                          f"`vector.similarity_function`: 'cosine'}}}}")
            print("  vector indexes created (384 dims, cosine)")

        # edges
        for f in edge_files:
            d = json.loads(f.read_text(encoding="utf-8"))
            fl, fk, tl, tk, et = d["from_label"], d["from_key"], d["to_label"], d["to_key"], d["type"]
            missing = 0
            for e in d["edges"]:
                props = {k: v for k, v in e.items() if k not in ("from_key", "to_key")}
                rec = s.run(
                    f"MATCH (a:`{fl}` {{{fk}: $fk}}), (b:`{tl}` {{{tk}: $tk}}) "
                    f"MERGE (a)-[r:`{et}`]->(b) SET r += $props RETURN count(r) AS c",
                    fk=e["from_key"], tk=e["to_key"], props=props).single()
                if not rec or rec["c"] == 0:
                    missing += 1
            print(f"  edges {et}: {len(d['edges'])} (unmatched endpoints: {missing})")

        # reconcile
        got = {r["t"]: r["c"] for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")}
    driver.close()

    ok = all(got.get(t, 0) >= n for t, n in manifest["edges"].items())
    print("\nreconciliation (db count >= manifest expectation):")
    for t, n in manifest["edges"].items():
        flag = "OK" if got.get(t, 0) >= n else "SHORT"
        print(f"  {t}: db {got.get(t, 0)} vs expected {n} [{flag}]")
    print("IMPORT", "OK" if ok else "CHECK REQUIRED")
    return 0

def repo_root():
    p = HERE
    for _ in range(5):
        if (p / "审查脚本和报告" / "实体对齐库").is_dir():
            return p
        p = p.parent
    raise SystemExit("corpus root not found")

if __name__ == "__main__":
    sys.exit(main())
