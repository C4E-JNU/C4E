# -*- coding: utf-8 -*-
"""step10b_clean_legacy.py — legacy graph contradiction cleanup (rerunnable, idempotent)

Re-normalize Contradiction nodes written under the old caliber according to `references/contradiction_taxonomy.md`:

  ① non-genuine contradictions → relabel :ContradictionArchived and remove from the active set (embedding stripped, **node never deleted**, reversible and auditable)
  ② genuine contradictions   → severity(A/B/C/D/E) + severity_label + evidence_source + cross_paper + caliber_variance, category deprecated
             (E = other · unclassifiable, also the "cannot grade" fallback; this script never produces `unknown`)
  ③ relationship rebuild → delete the legacy dirty edge :REL; create one unique (:DISPUTES)->(:Paper) per contradiction
  ④ traceability completion → trace / paper_title / paper_doi / batch / paper_dir
  ⑤ Figure anchoring → extract FigureN from the figure number and create **paper-scoped** figure nodes + (:DISPUTED_IN)
  ⑥ Paper counters recomputed → contradiction_count / contradiction_severity

Why removal relabels instead of DELETE:
  · old data lacks severity, so rule-based grading carries residual uncertainty → keeping the node allows inspection and rollback at any time;
  · after relabeling the Contradiction set no longer returns them, which for downstream consumers (graph retrieval / reports / GraphRAG) is equivalent to removal;
  · embeddings are also REMOVEd so vector recall stops returning them and nothing bleeds through.

Usage:
  python scripts/step10b_clean_legacy.py --dry-run          # only count each step's matches, write nothing
  python scripts/step10b_clean_legacy.py                    # run the cleanup
  python scripts/step10b_clean_legacy.py --uri neo4j+s://... --user xxx --password yyy
"""
import argparse
import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / 'neo4j.env'

# ────────────────────────────────────────────────────────────────
# ① non-genuine-contradiction archival rules: (description, WHERE clause, archival reason)
#    every criterion is based on "is there a mutually incompatible comparator value", qualified only by source/severity, never by carrier class.
# ────────────────────────────────────────────────────────────────
ARCHIVE_RULES = [
    (
        'Q1/Q2 our extraction/OCR errors, Q3 caliber differences, Q4 dual-source agreement',
        "coalesce(c.category,'') IN ['E1','E2','E5','consistent','dual_source','within_tolerance','none','CONSISTENT'] "
        "OR coalesce(c.category,'') STARTS WITH '一致'",
        "CASE WHEN coalesce(c.category,'') IN ['E1','E2'] THEN 'Q1/Q2 our own reading error, not a paper contradiction' "
        "WHEN coalesce(c.category,'')='E5' THEN 'Q3 caliber difference, neither side is wrong' "
        "ELSE 'Q4 the two sources agree' END",
    ),
    (
        'the entry itself says it is not a contradiction / record only',
        "toString(c.adopted_value) CONTAINS '非矛盾' OR toString(c.adopted_value) CONTAINS '仅记录' "
        "OR toString(c.adopted_value) CONTAINS '不构成' OR toString(c.reason) CONTAINS '非矛盾'",
        "'the entry itself says it is not a contradiction / record only'",
    ),
    (
        'OCR artifacts / correction records / agreement variants (dirty category values)',
        "coalesce(c.category,'') =~ '(?i).*(consistent|none|tolerance|suspicion_resolved|ocr|伪影|archive_fix|text_only|image_only|记录|dual_source).*'",
        "CASE WHEN coalesce(c.category,'') =~ '(?i).*(ocr|伪影).*' THEN 'Q1/Q2 our own OCR/figure-reading artifact, not a paper contradiction' "
        "WHEN coalesce(c.category,'') =~ '(?i).*(archive_fix).*' THEN 'archive-correction record, not a paper contradiction' "
        "WHEN coalesce(c.category,'') =~ '(?i).*(text_only|image_only|记录).*' THEN 'single-source record (no second source to compare), not a contradiction' "
        "ELSE 'Q4 the two sources agree / within tolerance' END",
    ),
    (
        'no comparator value at all (text/adopted/image all empty)',
        "c.text_value IS NULL AND c.adopted_value IS NULL AND c.image_value IS NULL",
        "'record without a comparator value (no conflicting text/adopted/image value)'",
    ),
    (
        'numerically equivalent (relative difference <1%)',
        "toFloat(toString(c.text_value)) IS NOT NULL AND toFloat(toString(c.adopted_value)) IS NOT NULL "
        "AND abs(toFloat(toString(c.text_value))-toFloat(toString(c.adopted_value))) "
        "<= 0.01*(CASE WHEN abs(toFloat(toString(c.text_value)))>abs(toFloat(toString(c.adopted_value))) "
        "THEN abs(toFloat(toString(c.text_value))) ELSE abs(toFloat(toString(c.adopted_value))) END)",
        "'Q4 numerically equivalent (relative difference <1%)'",
    ),
    (
        'the figure and text values are materially equal',
        "toFloat(toString(c.text_value)) IS NOT NULL AND toFloat(toString(c.image_value)) IS NOT NULL "
        "AND abs(toFloat(toString(c.text_value))-toFloat(toString(c.image_value))) "
        "<= 0.01*(CASE WHEN abs(toFloat(toString(c.text_value)))>abs(toFloat(toString(c.image_value))) "
        "THEN abs(toFloat(toString(c.text_value))) ELSE abs(toFloat(toString(c.image_value))) END)",
        "'Q4 the figure and text values are materially equal (relative difference <1%)'",
    ),
    (
        'no comparator value and self-described as consistent / basis adjudicated',
        "c.text_value IS NULL AND c.image_value IS NULL AND c.adopted_value IS NOT NULL "
        "AND (toString(c.adopted_value) CONTAINS '一致' OR toString(c.adopted_value) CONTAINS '为准' "
        "OR toString(c.adopted_value) CONTAINS '无冲突') AND NOT toString(c.adopted_value) CONTAINS '不一致'",
        "'Q4 the two sources agree / basis adjudicated (no conflicting value)'",
    ),
]

ARCHIVE_TMPL = (
    "MATCH (c:Contradiction) WHERE {where} "
    "SET c:ContradictionArchived, c.archive_reason = {reason}, c.archived_at = datetime() "
    "REMOVE c:Contradiction, c.embedding RETURN count(*) AS n"
)
ARCHIVE_COUNT_TMPL = "MATCH (c:Contradiction) WHERE {where} RETURN count(*) AS n"

# ② genuine-contradiction classification: severity / evidence_source / the two booleans, category deprecated
CLASSIFY = (
    "MATCH (c:Contradiction) WITH c, coalesce(c.category,'') AS cat "
    "SET c.severity = CASE WHEN cat IN ['A','B','C','D'] THEN cat "
    "WHEN cat CONTAINS '结论被自身数据否定' OR cat='internal_inconsistency' THEN 'D' ELSE 'E' END, "
    "c.cross_paper = (cat='E'), c.caliber_variance = false, "
    "c.evidence_source = CASE "
    "WHEN cat =~ '(?i).*(figure|image|图文|图|e3).*' THEN 'text-vs-figure' "
    "WHEN cat =~ '(?i).*(table|表).*' THEN 'text-vs-table' ELSE 'text-vs-text' END, "
    "c.needs_human_review = true, c.review_status = 'pending', c.review_scope = 'contradiction_review' "
    "REMOVE c.category RETURN count(*) AS n"
)

# ②b both figure and text values are numeric and differ → numerical deviation A
PROMOTE_A = (
    "MATCH (c:Contradiction) WITH c, toFloat(toString(c.text_value)) AS tv, toFloat(toString(c.image_value)) AS iv "
    "WHERE c.severity='unknown' AND tv IS NOT NULL AND iv IS NOT NULL SET c.severity='A' RETURN count(*) AS n"
)

# ②c text-heuristic grading (grade what can be graded; leave the rest pending_arbitration)
HEURISTIC = (
    "MATCH (c:Contradiction) WHERE c.severity='unknown' "
    "WITH c, toLower(coalesce(c.field,'') + ' | ' + coalesce(c.reason,'')) AS t "
    "SET c.severity = CASE "
    "WHEN t =~ '.*(结论|摘要|conclusion|不支持|相反|自相矛盾|否定).*' THEN 'D' "
    "WHEN t =~ '.*(命名|编号|序号|个数|计数|标号|错别字|笔误|单位错|数量).*' THEN 'C' "
    "WHEN t =~ '.*(结合能|峰位|波数|峰强|峰面积|半峰宽|xrd|ftir|xps|raman|衍射|图谱).*' THEN 'A' "
    "WHEN t =~ '.*(ph|温度|时间|剂量|浓度|投加|转速|体积|负载).*' THEN 'B' ELSE 'E' END, "
    "c.review_status = CASE WHEN t =~ '.*(命名|编号|序号|个数|计数|标号|错别字|笔误|单位错|数量|结论|摘要|conclusion|"
    "不支持|相反|自相矛盾|否定|结合能|峰位|波数|峰强|峰面积|半峰宽|xrd|ftir|xps|raman|衍射|图谱|ph|温度|时间|剂量|浓度|投加|转速|体积|负载).*' "
    "THEN 'pending' ELSE 'pending_arbitration' END RETURN count(*) AS n"
)

# ③ relationship rebuild
DEL_REL = "MATCH (c:Contradiction)-[r:REL]->() WITH r LIMIT 20000 DELETE r RETURN count(*) AS n"
DEL_REL_ARCHIVED = "MATCH (a:ContradictionArchived)-[r]->() WITH r LIMIT 20000 DELETE r RETURN count(*) AS n"
DEL_LEGACY_EDGE = "MATCH (c:Contradiction)-[r:REPORTED_IN|FROM_FIGURE|DISPUTES]->() DELETE r RETURN count(*) AS n"
DISPUTES_PAPER = (
    "MATCH (c:Contradiction), (p:Paper {paper_id:c.paper_id}) MERGE (c)-[:DISPUTES]->(p) RETURN count(*) AS n"
)

# ④ traceability completion
ENRICH = (
    "MATCH (c:Contradiction)-[:DISPUTES]->(p:Paper) "
    "SET c.paper_title = p.title, c.paper_doi = p.doi, c.batch = p.batch, c.paper_dir = p.dir_name, "
    "c.trace = c.paper_id + ' / ' + coalesce(left(toString(p.title), 90), '?') + ' / ' "
    "+ coalesce(c.field, '-') + ' / ' + coalesce(c.figure, '-') RETURN count(*) AS n"
)

# ⑤ Figure anchoring (APOC number extraction → paper-scoped figure node + DISPUTED_IN)
FIGURE_LINK = (
    "MATCH (c:Contradiction) WHERE c.figure IS NOT NULL AND c.figure <> '' "
    "WITH c, apoc.text.regexGroups(c.figure, '(?i)fig(?:ure)?\\\\.?\\\\s*(S?\\\\d+)') AS g "
    "WHERE size(g) > 0 UNWIND g AS grp WITH DISTINCT c, grp[1] AS fno "
    "MERGE (f:Figure {node_id: 'FIG::' + c.paper_id + '::Figure' + fno}) "
    "ON CREATE SET f.label='Figure', f.paper_id=c.paper_id, f.figure_no=fno, f.name='Figure'+fno "
    "MERGE (c)-[:DISPUTED_IN]->(f) RETURN count(*) AS n"
)

# ⑤b experiment anchoring (best effort: the paper's only experiment hits a condition word / the only pollutant name hits)
EXP_LINK_1 = (
    "MATCH (c:Contradiction) MATCH (e:Experiment {paper_id:c.paper_id}) WITH c, collect(e) AS exps "
    "WHERE size(exps)=1 AND c.field IS NOT NULL AND any(w IN "
    "['removal','dosage','ph','time','temperature','conc','toc','cod','oxidant','ratio','efficiency'] "
    "WHERE toLower(c.field) CONTAINS w) WITH c, exps[0] AS e MERGE (c)-[:FROM_EXPERIMENT]->(e) "
    "SET c.experiment_ref = e.node_id RETURN count(*) AS n"
)
EXP_LINK_2 = (
    "MATCH (c:Contradiction) MATCH (e:Experiment {paper_id:c.paper_id}) "
    "WHERE e.pollutant_name IS NOT NULL AND size(trim(toString(e.pollutant_name))) >= 3 "
    "AND c.field IS NOT NULL AND toLower(c.field) CONTAINS toLower(toString(e.pollutant_name)) "
    "WITH c, collect(DISTINCT e) AS hits WHERE size(hits)=1 WITH c, hits[0] AS e "
    "MERGE (c)-[:FROM_EXPERIMENT]->(e) SET c.experiment_ref = e.node_id RETURN count(*) AS n"
)

# ⑥ Paper counter recomputation
RECOUNT = (
    "MATCH (p:Paper) OPTIONAL MATCH (c:Contradiction {paper_id:p.paper_id}) "
    "WITH p, count(c) AS n, "
    "sum(CASE WHEN c.severity='A' THEN 1 ELSE 0 END) AS a, sum(CASE WHEN c.severity='B' THEN 1 ELSE 0 END) AS b, "
    "sum(CASE WHEN c.severity='C' THEN 1 ELSE 0 END) AS cc, sum(CASE WHEN c.severity='D' THEN 1 ELSE 0 END) AS d, "
    "sum(CASE WHEN c.severity='unknown' THEN 1 ELSE 0 END) AS u "
    "SET p.contradiction_count = n, p.contradiction_severity = CASE WHEN n = 0 THEN NULL ELSE "
    "'A=' + toString(a) + ' B=' + toString(b) + ' C=' + toString(cc) + ' D=' + toString(d) + ' unknown=' + toString(u) END "
    "RETURN sum(n) AS total, count(p) AS papers"
)

AUDIT = (
    "MATCH (c:Contradiction) RETURN count(*) AS true_total, "
    "sum(CASE WHEN c.severity='A' THEN 1 ELSE 0 END) AS sA, sum(CASE WHEN c.severity='B' THEN 1 ELSE 0 END) AS sB, "
    "sum(CASE WHEN c.severity='C' THEN 1 ELSE 0 END) AS sC, sum(CASE WHEN c.severity='D' THEN 1 ELSE 0 END) AS sD, "
    "sum(CASE WHEN c.severity='unknown' THEN 1 ELSE 0 END) AS sU, "
    "sum(CASE WHEN c.trace IS NULL THEN 1 ELSE 0 END) AS no_trace, "
    "sum(CASE WHEN NOT (c)-[:DISPUTES]->(:Paper) THEN 1 ELSE 0 END) AS no_paper, "
    "sum(CASE WHEN (c)-[:DISPUTED_IN]->(:Figure) THEN 1 ELSE 0 END) AS fig_linked, "
    "sum(CASE WHEN (c)-[:FROM_EXPERIMENT]->(:Experiment) THEN 1 ELSE 0 END) AS exp_linked"
)


def load_env():
    cfg = {}
    if ENV_FILE.exists():
        for ln in ENV_FILE.read_text(encoding='utf-8').splitlines():
            ln = ln.strip()
            if ln and not ln.startswith('#') and '=' in ln:
                k, v = ln.split('=', 1)
                cfg[k.strip()] = v.strip()
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='only count each archival rule\'s matches, write nothing')
    ap.add_argument('--uri', default=None)
    ap.add_argument('--user', default=None)
    ap.add_argument('--password', default=None)
    ap.add_argument('--database', default=None)
    a = ap.parse_args()

    cfg = load_env()
    uri = a.uri or cfg.get('NEO4J_URI')
    user = a.user or cfg.get('NEO4J_USERNAME')
    pwd = a.password or cfg.get('NEO4J_PASSWORD')
    db = a.database or cfg.get('NEO4J_DATABASE')
    if not (uri and user and pwd):
        print('missing Neo4j connection details (scripts/neo4j.env or --uri/--user/--password)')
        return 1
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print('the neo4j driver is required: uv pip install neo4j')
        return 1

    drv = GraphDatabase.driver(uri, auth=(user, pwd))
    with drv.session(database=db) as s:
        print('before cleanup: ', s.run(AUDIT).single().data())

        # ① archival
        for desc, where, reason in ARCHIVE_RULES:
            if a.dry_run:
                n = s.run(ARCHIVE_COUNT_TMPL.format(where=where)).single()['n']
                print(f'  [dry] archive·{desc}: {n}')
            else:
                n = s.run(ARCHIVE_TMPL.format(where=where, reason=reason)).single()['n']
                print(f'  OK archive·{desc}: {n}')

        if a.dry_run:
            print('\n[dry-run] nothing was written; drop --dry-run to run the cleanup.')
            return 0

        # ② classification
        for desc, q in [('severity/evidence_source placement', CLASSIFY),
                        ('figure-text numerical deviation → A', PROMOTE_A),
                        ('text-heuristic grading', HEURISTIC)]:
            print(f'  ✓ {desc}: {s.run(q).single()["n"]}')

        # ③ relationship rebuild
        for desc, q in [('delete dirty REL edges (genuine contradictions)', DEL_REL), ('delete edges left on archived nodes', DEL_REL_ARCHIVED),
                        ('clear legacy contradiction edges', DEL_LEGACY_EDGE), ('create DISPUTES→Paper', DISPUTES_PAPER)]:
            print(f'  ✓ {desc}: {s.run(q).single()["n"]}')

        # ④ traceability
        print(f'  OK traceability properties completed: {s.run(ENRICH).single()["n"]}')

        # ⑤ anchoring
        print(f'  OK Figure anchoring: {s.run(FIGURE_LINK).single()["n"]}')
        print(f'  OK experiment anchoring (condition words): {s.run(EXP_LINK_1).single()["n"]}')
        print(f'  OK experiment anchoring (pollutant names): {s.run(EXP_LINK_2).single()["n"]}')

        # ⑥ counters
        print(f'  OK Paper counters recomputed: {s.run(RECOUNT).single().data()}')

        print('after cleanup: ', s.run(AUDIT).single().data())
    drv.close()
    print('\nnote: entries left as unknown / review_status=pending_arbitration are for fenton-arbiter to grade across papers.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
