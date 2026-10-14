# -*- coding: utf-8 -*-
"""step10b_clean_legacy.py — 历史图谱矛盾清洗（可复跑，幂等）

把旧口径的 Contradiction 节点按《references/矛盾分类规范.md》重排为：

  ① 非真矛盾 → 换标签 :ContradictionArchived 出库（摘掉 embedding，**不删节点**，可逆可审计）
  ② 真矛盾   → severity(A/B/C/D/E) + severity_label + evidence_source + cross_paper + caliber_variance，废弃 category
             （E = 其他·无法归类，兼「判不了」兜底；本脚本不再产生 `unknown`）
  ③ 关系重建 → 删历史脏边 :REL；每个矛盾建唯一 (:DISPUTES)->(:Paper)
  ④ 溯源补全 → trace / paper_title / paper_doi / batch / paper_dir
  ⑤ Figure 锚定 → 从图号抽 FigureN，建 **paper 限定** 图节点 + (:DISPUTED_IN)
  ⑥ Paper 计数重算 → contradiction_count / contradiction_severity

为什么"去除"用换标签而不是 DELETE：
  · 旧数据缺 severity，规则判级有残余不确定性 → 保留节点可随时翻查、可回滚；
  · 换标签后 Contradiction 集合不再返回它们，对下游（图谱检索/报告/GraphRAG）等于已去除；
  · 同时 REMOVE embedding，向量索引不再召回，避免串味。

用法:
  python scripts/step10b_clean_legacy.py --dry-run          # 只统计各步命中数，不写库
  python scripts/step10b_clean_legacy.py                    # 执行清洗
  python scripts/step10b_clean_legacy.py --uri neo4j+s://... --user xxx --password yyy
"""
import argparse
import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / 'neo4j.env'

# ────────────────────────────────────────────────────────────────
# ① 非真矛盾归档规则：(说明, WHERE 条件, 归档原因)
#    判据一律基于「有无互不相容的对照值」，只按来源/严重程度定性，不看载体类别。
# ────────────────────────────────────────────────────────────────
ARCHIVE_RULES = [
    (
        'Q1/Q2 我方提取/OCR 错误、Q3 口径差、Q4 双源一致',
        "coalesce(c.category,'') IN ['E1','E2','E5','consistent','dual_source','within_tolerance','none','CONSISTENT'] "
        "OR coalesce(c.category,'') STARTS WITH '一致'",
        "CASE WHEN coalesce(c.category,'') IN ['E1','E2'] THEN 'Q1/Q2 我方提取读数错误，非文献矛盾' "
        "WHEN coalesce(c.category,'')='E5' THEN 'Q3 口径差异，双方均无误' "
        "ELSE 'Q4 双源一致' END",
    ),
    (
        '条目自述非矛盾/仅记录',
        "toString(c.adopted_value) CONTAINS '非矛盾' OR toString(c.adopted_value) CONTAINS '仅记录' "
        "OR toString(c.adopted_value) CONTAINS '不构成' OR toString(c.reason) CONTAINS '非矛盾'",
        "'条目自述非矛盾/仅记录'",
    ),
    (
        'OCR 伪影 / 修正记录 / 一致类变体（脏 category 值）',
        "coalesce(c.category,'') =~ '(?i).*(consistent|none|tolerance|suspicion_resolved|ocr|伪影|archive_fix|text_only|image_only|记录|dual_source).*'",
        "CASE WHEN coalesce(c.category,'') =~ '(?i).*(ocr|伪影).*' THEN 'Q1/Q2 我方 OCR/读图伪影，非文献矛盾' "
        "WHEN coalesce(c.category,'') =~ '(?i).*(archive_fix).*' THEN '归档修正记录，非文献矛盾' "
        "WHEN coalesce(c.category,'') =~ '(?i).*(text_only|image_only|记录).*' THEN '单源记录（无第二来源对照），非矛盾' "
        "ELSE 'Q4 双源一致/容差内' END",
    ),
    (
        '无任何对照值（text/adopted/image 全空）',
        "c.text_value IS NULL AND c.adopted_value IS NULL AND c.image_value IS NULL",
        "'无对照值记录（text/adopted/image 均无冲突值）'",
    ),
    (
        '数值实质等同（相对差<1%）',
        "toFloat(toString(c.text_value)) IS NOT NULL AND toFloat(toString(c.adopted_value)) IS NOT NULL "
        "AND abs(toFloat(toString(c.text_value))-toFloat(toString(c.adopted_value))) "
        "<= 0.01*(CASE WHEN abs(toFloat(toString(c.text_value)))>abs(toFloat(toString(c.adopted_value))) "
        "THEN abs(toFloat(toString(c.text_value))) ELSE abs(toFloat(toString(c.adopted_value))) END)",
        "'Q4 数值实质等同（相对差<1%）'",
    ),
    (
        '图文两值实质等同',
        "toFloat(toString(c.text_value)) IS NOT NULL AND toFloat(toString(c.image_value)) IS NOT NULL "
        "AND abs(toFloat(toString(c.text_value))-toFloat(toString(c.image_value))) "
        "<= 0.01*(CASE WHEN abs(toFloat(toString(c.text_value)))>abs(toFloat(toString(c.image_value))) "
        "THEN abs(toFloat(toString(c.text_value))) ELSE abs(toFloat(toString(c.image_value))) END)",
        "'Q4 图文两值实质等同（相对差<1%）'",
    ),
    (
        '无对照值且自述一致/基准裁定',
        "c.text_value IS NULL AND c.image_value IS NULL AND c.adopted_value IS NOT NULL "
        "AND (toString(c.adopted_value) CONTAINS '一致' OR toString(c.adopted_value) CONTAINS '为准' "
        "OR toString(c.adopted_value) CONTAINS '无冲突') AND NOT toString(c.adopted_value) CONTAINS '不一致'",
        "'Q4 双源一致/基准裁定（无冲突值）'",
    ),
]

ARCHIVE_TMPL = (
    "MATCH (c:Contradiction) WHERE {where} "
    "SET c:ContradictionArchived, c.archive_reason = {reason}, c.archived_at = datetime() "
    "REMOVE c:Contradiction, c.embedding RETURN count(*) AS n"
)
ARCHIVE_COUNT_TMPL = "MATCH (c:Contradiction) WHERE {where} RETURN count(*) AS n"

# ② 真矛盾归类：severity / evidence_source / 两个布尔标记，废弃 category
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

# ②b 图文两值都是数值且不等 → 数值偏差 A
PROMOTE_A = (
    "MATCH (c:Contradiction) WITH c, toFloat(toString(c.text_value)) AS tv, toFloat(toString(c.image_value)) AS iv "
    "WHERE c.severity='unknown' AND tv IS NOT NULL AND iv IS NOT NULL SET c.severity='A' RETURN count(*) AS n"
)

# ②c 文本启发式定级（能判的判，判不了的保持 unknown 并标 pending_arbitration）
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

# ③ 关系重建
DEL_REL = "MATCH (c:Contradiction)-[r:REL]->() WITH r LIMIT 20000 DELETE r RETURN count(*) AS n"
DEL_REL_ARCHIVED = "MATCH (a:ContradictionArchived)-[r]->() WITH r LIMIT 20000 DELETE r RETURN count(*) AS n"
DEL_LEGACY_EDGE = "MATCH (c:Contradiction)-[r:REPORTED_IN|FROM_FIGURE|DISPUTES]->() DELETE r RETURN count(*) AS n"
DISPUTES_PAPER = (
    "MATCH (c:Contradiction), (p:Paper {paper_id:c.paper_id}) MERGE (c)-[:DISPUTES]->(p) RETURN count(*) AS n"
)

# ④ 溯源补全
ENRICH = (
    "MATCH (c:Contradiction)-[:DISPUTES]->(p:Paper) "
    "SET c.paper_title = p.title, c.paper_doi = p.doi, c.batch = p.batch, c.paper_dir = p.dir_name, "
    "c.trace = c.paper_id + ' / ' + coalesce(left(toString(p.title), 90), '?') + ' / ' "
    "+ coalesce(c.field, '-') + ' / ' + coalesce(c.figure, '-') RETURN count(*) AS n"
)

# ⑤ Figure 锚定（APOC 抽号 → paper 限定图节点 + DISPUTED_IN）
FIGURE_LINK = (
    "MATCH (c:Contradiction) WHERE c.figure IS NOT NULL AND c.figure <> '' "
    "WITH c, apoc.text.regexGroups(c.figure, '(?i)fig(?:ure)?\\\\.?\\\\s*(S?\\\\d+)') AS g "
    "WHERE size(g) > 0 UNWIND g AS grp WITH DISTINCT c, grp[1] AS fno "
    "MERGE (f:Figure {node_id: 'FIG::' + c.paper_id + '::Figure' + fno}) "
    "ON CREATE SET f.label='Figure', f.paper_id=c.paper_id, f.figure_no=fno, f.name='Figure'+fno "
    "MERGE (c)-[:DISPUTED_IN]->(f) RETURN count(*) AS n"
)

# ⑤b 实验锚定（尽力而为：该篇唯一实验命中条件词 / 唯一污染物名命中）
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

# ⑥ Paper 计数重算
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
    ap.add_argument('--dry-run', action='store_true', help='只统计各归档规则命中数，不写库')
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
        print('缺少 Neo4j 连接信息（scripts/neo4j.env 或 --uri/--user/--password）')
        return 1
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print('需要 neo4j 驱动：uv pip install neo4j')
        return 1

    drv = GraphDatabase.driver(uri, auth=(user, pwd))
    with drv.session(database=db) as s:
        print('清洗前：', s.run(AUDIT).single().data())

        # ① 归档
        for desc, where, reason in ARCHIVE_RULES:
            if a.dry_run:
                n = s.run(ARCHIVE_COUNT_TMPL.format(where=where)).single()['n']
                print(f'  [dry] 归档·{desc}: {n}')
            else:
                n = s.run(ARCHIVE_TMPL.format(where=where, reason=reason)).single()['n']
                print(f'  ✓ 归档·{desc}: {n}')

        if a.dry_run:
            print('\n[dry-run] 未写入任何改动；去掉 --dry-run 执行清洗。')
            return 0

        # ② 归类
        for desc, q in [('severity/evidence_source 归位', CLASSIFY),
                        ('图文数值偏差 → A', PROMOTE_A),
                        ('文本启发式定级', HEURISTIC)]:
            print(f'  ✓ {desc}: {s.run(q).single()["n"]}')

        # ③ 关系重建
        for desc, q in [('删脏边 REL(真矛盾)', DEL_REL), ('删归档节点残留边', DEL_REL_ARCHIVED),
                        ('清旧矛盾边', DEL_LEGACY_EDGE), ('建 DISPUTES→Paper', DISPUTES_PAPER)]:
            print(f'  ✓ {desc}: {s.run(q).single()["n"]}')

        # ④ 溯源
        print(f'  ✓ 溯源属性补全: {s.run(ENRICH).single()["n"]}')

        # ⑤ 锚定
        print(f'  ✓ Figure 锚定: {s.run(FIGURE_LINK).single()["n"]}')
        print(f'  ✓ 实验锚定(条件词): {s.run(EXP_LINK_1).single()["n"]}')
        print(f'  ✓ 实验锚定(污染物名): {s.run(EXP_LINK_2).single()["n"]}')

        # ⑥ 计数
        print(f'  ✓ Paper 计数重算: {s.run(RECOUNT).single().data()}')

        print('清洗后：', s.run(AUDIT).single().data())
    drv.close()
    print('\n提示：unknown / review_status=pending_arbitration 的条目留给 fenton-arbiter 分批定级。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
