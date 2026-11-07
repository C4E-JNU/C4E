# -*- coding: utf-8 -*-
"""Read-only check: does the local embedding model match the Neo4j vector indexes?

Usage:
    python scripts/check_vector_compat.py --env path/to/neo4j.env
    python scripts/check_vector_compat.py --env ... --model BAAI/bge-small-zh-v1.5 --cache model_cache

Exit code 0 = all indexes/nodes match the model dimension; 1 = mismatch.
This script never writes to the database.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / "skills" / "fenton-multiagent-mining" / "scripts" / "neo4j.env"


def load_env(path: Path) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip()
    return cfg


def model_dim(model_name: str, cache_dir: str | None) -> int:
    try:
        from fastembed import TextEmbedding
        kwargs = {"model_name": model_name}
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        model = TextEmbedding(**kwargs)
        return len(next(model.embed(["dimension probe"])))
    except Exception as exc:
        print(f"[warn] could not load {model_name}: {exc}", file=sys.stderr)
        if "bge-small-zh" in model_name:
            return 512
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--cache", default=str(ROOT / "model_cache"))
    args = parser.parse_args()

    cfg = load_env(Path(args.env))
    uri = cfg.get("NEO4J_URI", "")
    user = cfg.get("NEO4J_USERNAME", "")
    password = cfg.get("NEO4J_PASSWORD", "")
    database = cfg.get("NEO4J_DATABASE") or "neo4j"
    if not (uri and user and password):
        print(f"[error] missing NEO4J_URI/USERNAME/PASSWORD in {args.env}", file=sys.stderr)
        return 2

    expected = model_dim(args.model, args.cache)
    print(f"model={args.model} dim={expected}")
    print(f"database={database}")

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=20)
    mismatches = 0
    try:
        with driver.session(database=database) as session:
            indexes = session.run(
                "SHOW VECTOR INDEXES YIELD name, labelsOrTypes, properties, options"
            ).data()
            print(f"vector_indexes={len(indexes)}")
            for row in indexes:
                conf = ((row.get("options") or {}).get("indexConfig") or {})
                dim = conf.get("vector.dimensions") or conf.get("dimensions")
                status = "MATCH" if expected and dim and int(dim) == expected else "MISMATCH"
                if status == "MISMATCH":
                    mismatches += 1
                print(f"  [{status}] {row.get('name')} labels={row.get('labelsOrTypes')} "
                      f"index_dim={dim}")

            labels = [r["label"] for r in session.run(
                "CALL db.labels() YIELD label RETURN label ORDER BY label"
            ).data()]
            for label in labels:
                try:
                    row = session.run(
                        f"MATCH (n:`{label}`) WHERE n.embedding IS NOT NULL "
                        "RETURN count(n) AS c, collect(DISTINCT size(n.embedding))[..5] AS dims"
                    ).single()
                except Exception:
                    continue
                if row and row["c"]:
                    dims = row["dims"]
                    ok = bool(dims) and all(int(d) == expected for d in dims if d is not None)
                    status = "MATCH" if ok else "MISMATCH"
                    if not ok:
                        mismatches += 1
                    print(f"  [{status}] {label} nodes={row['c']} embedding_dims={dims}")
        print("RESULT:", "MATCH" if mismatches == 0 else f"MISMATCH ({mismatches} issues)")
        return 0 if mismatches == 0 else 1
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
