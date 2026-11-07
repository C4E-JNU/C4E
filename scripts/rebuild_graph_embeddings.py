# -*- coding: utf-8 -*-
"""Rebuild Neo4j graph embeddings with BAAI/bge-small-zh-v1.5 (512-dim).

Why: older graph imports created 384-dim indexes (Catalyst / Experiment / Paper /
Contradiction), while the application uses a 512-dim bge-small-zh-v1.5 model.
This script re-embeds every selected node and recreates the vector indexes at
the model dimension.

Dry-run (default, read-only):
    python scripts/rebuild_graph_embeddings.py --env path/to/neo4j.env

Apply (DROPS and recreates the vector indexes, then writes embeddings):
    python scripts/rebuild_graph_embeddings.py --env path/to/neo4j.env --apply

Use the bundled Python if fastembed is not installed in your system Python:
    portable\\python\\python.exe scripts\\rebuild_graph_embeddings.py --env ... --apply
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / "skills" / "fenton-multiagent-mining" / "scripts" / "neo4j.env"
INDEXES = {
    "Paper": "vec_paper",
    "Catalyst": "vec_catalyst",
    "Experiment": "vec_experiment",
    "Contradiction": "vec_contradiction",
    "Pollutant": "vec_pollutant",
}
DEFAULT_LABELS = ["Paper", "Catalyst", "Experiment", "Contradiction", "Pollutant", "VariableExperiment"]


def load_env(path: Path) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip()
    return cfg


def text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(text_value(v) for v in value)
    return str(value).strip()


def build_text(label: str, props: dict) -> str:
    def parts(*keys: str) -> str:
        return " | ".join(v for v in (text_value(props.get(k)) for k in keys) if v)

    if label == "Paper":
        return parts("title", "doi", "journal", "authors", "publication_year")
    if label == "Catalyst":
        return parts("catalyst_name", "chemical_formula_value", "chemical_formula_raw",
                     "metal_elements_value", "support_material_value", "morphology_value",
                     "crystal_phase_value", "synthesis_method_value", "calcination_temp",
                     "specific_surface_area", "band_gap")
    if label == "Experiment":
        return parts("catalyst_id", "pollutant_name", "oxidant_type", "process_mode", "medium",
                     "initial_pH", "temperature", "reaction_time", "catalyst_dosage",
                     "oxidant_dosage", "removal_efficiency", "rate_constant_k",
                     "dominant_ROS", "degradation_pathway")
    if label == "Contradiction":
        return parts("text_quote", "reason", "category", "severity_label", "resolution",
                     "conflicting_value", "adopted_value")
    if label == "Pollutant":
        return parts("name", "aliases", "formula_reported", "pollutant_class", "type")
    if label == "VariableExperiment":
        return parts("variable_parameter", "level_raw", "metric_name", "metric_value",
                     "metric_unit", "fixed_conditions")
    return " ".join(text_value(v) for v in props.values() if not isinstance(v, (list, dict)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--cache", default=str(ROOT / "model_cache"))
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    parser.add_argument("--apply", action="store_true", help="actually drop indexes and write embeddings")
    args = parser.parse_args()

    cfg = load_env(Path(args.env))
    uri = cfg.get("NEO4J_URI", "")
    user = cfg.get("NEO4J_USERNAME", "")
    password = cfg.get("NEO4J_PASSWORD", "")
    database = cfg.get("NEO4J_DATABASE") or "neo4j"
    if not (uri and user and password):
        print(f"[error] missing NEO4J_URI/USERNAME/PASSWORD in {args.env}", file=sys.stderr)
        return 2

    from fastembed import TextEmbedding
    model_kwargs = {"model_name": args.model}
    if args.cache:
        model_kwargs["cache_dir"] = args.cache
    model = TextEmbedding(**model_kwargs)
    dim = len(next(model.embed(["dimension probe"])))
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    print(f"model={args.model} dim={dim} labels={labels} apply={args.apply}")

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=30)

    try:
        with driver.session(database=database) as session:
            for label in labels:
                row = session.run(
                    f"MATCH (n:`{label}`) RETURN count(n) AS total, count(n.embedding) AS embedded"
                ).single()
                print(f"{label}: total={row['total']} embedded={row['embedded']}")

            if not args.apply:
                print("\nDRY-RUN only. No indexes or embeddings were changed.")
                print("Re-run with --apply to drop indexes, re-embed, and recreate indexes at "
                      f"{dim} dims.")
                return 0

            print("\nDropping vector indexes ...")
            for label, index_name in INDEXES.items():
                if label not in labels:
                    continue
                try:
                    session.run(f"DROP INDEX {index_name} IF EXISTS")
                    print(f"  dropped {index_name}")
                except Exception as exc:
                    print(f"  [warn] drop {index_name}: {exc}")

            total_done = 0
            started = time.time()
            for label in labels:
                skip = 0
                label_done = 0
                while True:
                    rows = session.run(
                        f"MATCH (n:`{label}`) RETURN elementId(n) AS eid, properties(n) AS p "
                        "ORDER BY eid SKIP $skip LIMIT $limit",
                        skip=skip, limit=args.batch,
                    ).data()
                    if not rows:
                        break
                    payload = []
                    for row in rows:
                        props = dict(row["p"] or {})
                        props.pop("embedding", None)
                        text = build_text(label, props)
                        if text:
                            payload.append((row["eid"], text))
                    if payload:
                        vectors = list(model.embed([text for _, text in payload]))
                        update_rows = [
                            {"eid": eid, "vec": [float(x) for x in vector.tolist()]}
                            for (eid, _), vector in zip(payload, vectors)
                        ]
                        session.run(
                            "UNWIND $rows AS row MATCH (n) WHERE elementId(n) = row.eid "
                            "SET n.embedding = row.vec, n.embedding_model = $model",
                            rows=update_rows, model=args.model,
                        )
                        label_done += len(update_rows)
                        total_done += len(update_rows)
                    skip += args.batch
                    if skip % (args.batch * 10) == 0:
                        print(f"  {label}: {label_done} nodes, {time.time() - started:.0f}s")
                print(f"  {label}: done {label_done} nodes")

            print("\nCreating vector indexes ...")
            for label, index_name in INDEXES.items():
                if label not in labels:
                    continue
                session.run(
                    f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS FOR (n:`{label}`) "
                    f"ON (n.embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {int(dim)}, "
                    "`vector.similarity_function`: 'cosine'}}"
                )
                print(f"  created {index_name} ({dim} dims)")
            try:
                session.run("CALL db.awaitIndexes(300)")
            except Exception:
                pass

            print(f"\nDONE: re-embedded {total_done} nodes in {time.time() - started:.0f}s.")
            return 0
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
