"""
Neo4j 图谱工具 — graph_schema + execute_cypher
直接从前端 agent_tools.js 的 JS 逻辑翻译为 Python
"""
import json
from pathlib import Path

from neo4j import GraphDatabase

# ── 服务端连接配置（硬编码优先，不读环境变量）──────────
# 连接信息直接写在 backend/tools/neo4j_local.py。该文件被 .gitignore 排除，
# 不会进仓库，也不会被打进公开发布的便携包 —— 既能开箱即用，又不会泄露密码。
# 没有该文件时（别人 clone 后用自己的实例），退回仓库内 neo4j.env；
# 两者都没有，仍可由前端「智能体配置」面板填写。

_ENV_FILES = (
    ('neo4j.env',),
    ('skills', 'fenton-multiagent-mining', 'scripts', 'neo4j.env'),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _hardcoded_config() -> dict:
    """读取硬编码在 backend/tools/neo4j_local.py 里的连接信息。"""
    try:
        from . import neo4j_local as local       # 以 tools 包导入
    except Exception:
        try:
            import neo4j_local as local          # 直接在 backend 目录运行
        except Exception:
            return {}
    uri = (getattr(local, 'NEO4J_URI', '') or '').strip()
    password = getattr(local, 'NEO4J_PASSWORD', '') or ''
    if not uri or not password:
        return {}
    return {
        'uri': uri,
        'user': (getattr(local, 'NEO4J_USERNAME', '') or 'neo4j').strip(),
        'password': password,
        'database': (getattr(local, 'NEO4J_DATABASE', '') or '').strip(),
        'source': 'hardcoded',
    }


def _env_file_config() -> dict:
    for parts in _ENV_FILES:
        path = _repo_root().joinpath(*parts)
        if not path.is_file():
            continue
        values = {}
        try:
            text = path.read_text(encoding='utf-8')
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.strip().upper()] = value.strip()
        if values.get('NEO4J_URI'):
            cfg = {
                'uri': values['NEO4J_URI'],
                'user': values.get('NEO4J_USERNAME') or values.get('NEO4J_USER') or 'neo4j',
                'password': values.get('NEO4J_PASSWORD', ''),
                'database': values.get('NEO4J_DATABASE', ''),
                'source': str(path),
            }
            return cfg if cfg['password'] else {}
    return {}


def load_neo4j_config() -> dict:
    """服务端连接信息：硬编码 > neo4j.env 文件 > 空。绝不回传 password。"""
    return _hardcoded_config() or _env_file_config()


class Neo4jClient:
    """Neo4j 连接管理器（单例模式）"""
    _driver = None
    # 【2026-10-05】保存凭证，供 run() 在断连时自愈重连。
    # 背景：Neo4j Aura 空闲会回收连接，表现为「首次/第二次查询 defunct connection
    # 或未连接，第三次就好」——不是图谱坏了，而是驱动持有死连接（实测复现）。
    _uri = None
    _user = None
    _password = None

    @classmethod
    def get_driver(cls, uri=None, user=None, password=None):
        if cls._driver is not None:
            return cls._driver
        if not uri or not user or not password:
            return None
        cls._driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_pool_size=2,
            connection_timeout=10,  # 秒（driver 单位是秒，此前误写 10000 相当于 2.8 小时）
        )
        cls._uri, cls._user, cls._password = uri, user, password   # 供 run() 自愈重连
        cls._driver.verify_connectivity()
        return cls._driver

    @classmethod
    def is_connected(cls):
        return cls._driver is not None

    _env_cfg = None

    @classmethod
    def auto_connect(cls):
        """用服务端配置自动连接一次（幂等）。没配置就返回 {}，不影响普通对话。"""
        if cls._driver is not None:
            return {"success": True, "source": "existing"}
        if cls._env_cfg is None:
            cls._env_cfg = load_neo4j_config()
        cfg = cls._env_cfg
        if not cfg:
            return {}
        result = cls.connect(cfg["uri"], cfg["user"], cfg["password"])
        result["source"] = cfg.get("source", "")
        return result

    @classmethod
    def connect(cls, uri, user, password):
        try:
            if cls._driver:
                cls._driver.close()
                cls._driver = None
            cls.get_driver(uri, user, password)
            return {"success": True}
        except Exception as e:
            cls._driver = None
            return {"success": False, "error": str(e)}

    @classmethod
    def disconnect(cls):
        if cls._driver:
            cls._driver.close()
            cls._driver = None

    @classmethod
    def run(cls, cypher, params=None):
        """执行 Cypher 查询，返回结果列表。

        【2026-10-05 自愈】Aura 空闲断连 → 用保存的凭证重连一次再试，
        不再让上层（以及 LLM）看到「图谱连不上」的假象。
        """
        if not cls._driver:
            if cls._uri:
                cls.get_driver(cls._uri, cls._user, cls._password)
            if not cls._driver:
                raise Exception("Neo4j 未连接")
        params = params or {}
        try:
            return cls._execute(cypher, params)
        except Exception:
            if not cls._uri:                      # 未保存凭证（老路径）→ 原样抛
                raise
            try:
                cls._driver.close()
            except Exception:
                pass
            cls._driver = None
            cls.get_driver(cls._uri, cls._user, cls._password)
            if not cls._driver:
                raise
            return cls._execute(cypher, params)   # 重连后重试一次

    @classmethod
    def _execute(cls, cypher, params):
        """实际执行（抽出来供 run 重试复用）"""
        with cls._driver.session() as session:
            result = session.run(cypher, params)
            records = []
            for record in result:
                row = {}
                for key in record.keys():
                    row[key] = _serialize_value(record.get(key))
                records.append(row)
            return records


def _serialize_value(v):
    """递归序列化 Neo4j 返回的各种类型"""
    if v is None:
        return None
    # Neo4j Node
    if hasattr(v, 'labels') and hasattr(v, 'items'):  # Node
        return {
            "_type": "Node",
            "labels": list(v.labels),
            "properties": dict(v.items())
        }
    # Neo4j Relationship
    if hasattr(v, 'type') and hasattr(v, 'items') and not hasattr(v, 'labels'):
        return {
            "_type": "Relationship",
            "type": v.type,
            "properties": dict(v.items())
        }
    # Neo4j Path
    if hasattr(v, 'segments'):
        return {
            "_type": "Path",
            "segments": [
                {
                    "start": _serialize_value(s.start),
                    "relationship": _serialize_value(s.relationship),
                    "end": _serialize_value(s.end)
                }
                for s in v.segments
            ]
        }
    # Integer 处理
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return round(v, 4)
    # 其它类型（string, list, dict）
    if isinstance(v, (list, tuple)):
        return [_serialize_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _serialize_value(val) for k, val in v.items()}
    try:
        # 尝试转字符串后转数字
        s = str(v)
        n = float(s)
        return int(n) if n == int(n) else n
    except (ValueError, TypeError):
        return str(v)


# ── 向量检索（图谱语义查询）──────────────────────────

# 向量索引名 → 节点标签；索引维度由 step10_graph_sync.py 按本地 bge-small-zh-v1.5 (512) 创建
VECTOR_INDEX_LABELS = {
    "vec_paper": "Paper",
    "vec_catalyst": "Catalyst",
    "vec_category": "CatalystCategory",
    "vec_pollutant": "Pollutant",
    "vec_experiment": "Experiment",
    "vec_contradiction": "Contradiction",
}


async def execute_graph_vector_search(query: str, scope: str = "catalyst", top_k: int = 8):
    """图谱语义检索：本地 bge 模型向量化 query → Neo4j 原生向量索引召回 → 沿边扩展上下文。

    scope: catalyst / category / pollutant / experiment / contradiction（对应 5 个向量索引）
    返回节点属性 + score + 一跳图上下文，供 LLM 直接引用。
    """
    if not Neo4jClient.is_connected():
        Neo4jClient.auto_connect()          # 服务端有 neo4j.env 时免手填
    if not Neo4jClient.is_connected():
        return json.dumps({"error": "Neo4j 未连接（服务端未配置 neo4j.env，前端也未填写连接信息）"})
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "查询文本不能为空"})
    index_name = f"vec_{scope}"
    label = VECTOR_INDEX_LABELS.get(index_name)
    if not label:
        return json.dumps({"error": f"scope 必须是 {list(VECTOR_INDEX_LABELS.keys())}"})
    top_k = max(1, min(int(top_k or 8), 25))
    try:
        from tools.vector_search import embed_texts
        # bge 官方建议：查询侧加检索指令前缀（文档侧/图谱 search_text 侧不加）
        q_inst = "为这个句子生成表示以用于检索相关文章："
        q_vec = embed_texts([q_inst + query])[0].tolist()
    except Exception as e:
        return json.dumps({"error": f"本地向量化模型加载失败: {e}"})

    expand = {
        "Paper": """
            OPTIONAL MATCH (n)-[:HAS_CONTRADICTION]->(k:Contradiction)
            RETURN n, score, count(DISTINCT k) AS contradictions""",
        "Catalyst": """
            OPTIONAL MATCH (n)-[:IN_CATEGORY]->(cat:CatalystCategory)
            OPTIONAL MATCH (n)<-[:USES_CATALYST]-(e:Experiment)-[:DEGRADES]->(pl:Pollutant)
            OPTIONAL MATCH (k:Contradiction)-[:DISPUTES]->(e)
            RETURN n, score, cat.display_name AS category,
                   collect(DISTINCT pl.name)[..6] AS pollutants,
                   count(DISTINCT k) AS contradictions""",
        "CatalystCategory": """
            OPTIONAL MATCH (n)<-[:IN_CATEGORY]-(c:Catalyst)<-[:USES_CATALYST]-(e:Experiment)
            RETURN n, score, count(DISTINCT e) AS experiments, count(DISTINCT c) AS members""",
        "Pollutant": """
            OPTIONAL MATCH (n)<-[:DEGRADES]-(e:Experiment)-[:USES_CATALYST]->(c:Catalyst)
            OPTIONAL MATCH (k:Contradiction)-[:DISPUTES]->(e)
            RETURN n, score, count(DISTINCT e) AS experiments,
                   collect(DISTINCT c.catalyst_name)[..6] AS catalysts,
                   count(DISTINCT k) AS contradictions""",
        "Experiment": """
            OPTIONAL MATCH (n)-[:USES_CATALYST]->(c:Catalyst)
            OPTIONAL MATCH (n)-[:DEGRADES]->(pl:Pollutant)
            OPTIONAL MATCH (n)-[:WITH_OXIDANT]->(o:Oxidant)
            OPTIONAL MATCH (k:Contradiction)-[:DISPUTES]->(n)
            RETURN n, score, c.catalyst_name AS catalyst, pl.name AS pollutant,
                   o.oxidant_key AS oxidant, count(DISTINCT k) AS contradictions""",
        "Contradiction": """
            OPTIONAL MATCH (p:Paper)-[:HAS_CONTRADICTION]->(n)
            RETURN n, score, p.title AS paper_title, p.doi AS doi, p.paper_id AS paper_id""",
    }[label]

    try:
        # 先核对索引维度：旧库可能存在 384 维索引，而本地 bge-small-zh-v1.5 是 512 维。
        # 直接查询会得到难懂的 "dimension mismatch"，这里提前给出可操作的错误。
        try:
            for _r in Neo4jClient.run("SHOW VECTOR INDEXES YIELD name, options"):
                if _r.get("name") != index_name:
                    continue
                _conf = ((_r.get("options") or {}).get("indexConfig") or {})
                _dim = _conf.get("vector.dimensions") or _conf.get("dimensions")
                if _dim and int(_dim) != len(q_vec):
                    return json.dumps({
                        "error": f"向量维度不匹配：本地模型 bge-small-zh-v1.5 输出 {len(q_vec)} 维，"
                                 f"索引 {index_name} 是 {int(_dim)} 维。",
                        "model": "BAAI/bge-small-zh-v1.5",
                        "model_dim": len(q_vec),
                        "index": index_name,
                        "index_dim": int(_dim),
                        "fix": "运行 scripts/rebuild_graph_embeddings.py --apply 重建该图谱的向量与索引。"
                    }, ensure_ascii=False)
        except Exception:
            pass

        rows = Neo4jClient.run(
            f"""
            CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score
            WITH node AS n, score
            {expand}
            """,
            {"index": index_name, "k": top_k, "vec": q_vec},
        )
        out = []
        for r in rows:
            node = r.get("n") or {}
            props = node.get("properties", {}) if isinstance(node, dict) else {}
            props.pop("embedding", None)  # 向量不回传给 LLM
            out.append({"score": r.get("score"), **{k2: v for k2, v in r.items()
                                                     if k2 not in ("n", "score") and v is not None},
                        "node": props})
        return json.dumps({
            "query": query, "scope": scope, "count": len(out), "results": out,
            "hint": "score 为余弦相似度；>0.6 高相关。数据细节可再对 node.node_id/paper_id 用 execute_cypher 深查。",
        }, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"向量检索失败: {str(e)}", "cypher_hint":
                           "若报 'no such vector index'，先在 skill 目录跑 step10_graph_sync.py --embed 重建索引并回填向量。"})

async def execute_graph_schema():
    """获取 Neo4j 图谱的节点类型、关系类型、属性结构"""
    if not Neo4jClient.is_connected():
        Neo4jClient.auto_connect()          # 服务端有 neo4j.env 时免手填
    if not Neo4jClient.is_connected():
        return json.dumps({"error": "Neo4j 未连接（服务端未配置 neo4j.env，前端也未填写连接信息）"})

    try:
        # 节点属性
        node_props = Neo4jClient.run("""
            CALL db.schema.nodeTypeProperties()
            YIELD nodeLabels, propertyName, propertyTypes, mandatory
            RETURN nodeLabels, propertyName, propertyTypes, mandatory
            ORDER BY nodeLabels, propertyName
        """)
        # 关系类型
        rels = Neo4jClient.run("""
            CALL db.relationshipTypes() YIELD relationshipType
            RETURN relationshipType ORDER BY relationshipType
        """)
        # 节点标签
        labels = Neo4jClient.run("""
            CALL db.labels() YIELD label RETURN label ORDER BY label
        """)
        # 各节点计数
        counts = {}
        for r in labels:
            cnt = Neo4jClient.run(
                f"MATCH (n:`{r['label']}`) RETURN count(n) AS cnt"
            )
            counts[r['label']] = cnt[0]['cnt'] if cnt else 0

        # 索引
        indexes = Neo4jClient.run("SHOW INDEXES")
        vector_idx = [
            {"name": i.get("name"), "labelsOrTypes": i.get("labelsOrTypes"),
             "properties": i.get("properties")}
            for i in indexes if i.get("type") == "VECTOR"
        ]

        # 组装节点类型信息
        node_types = {}
        for r in node_props:
            label = r.get("nodeLabels")
            if isinstance(label, list):
                label = label[0] if label else "Unknown"
            if label not in node_types:
                node_types[label] = {
                    "properties": [],
                    "count": counts.get(label, 0)
                }
            if r.get("propertyName"):
                node_types[label]["properties"].append(
                    f"{r['propertyName']}: {r.get('propertyTypes')}"
                )

        return json.dumps({
            "nodeTypes": [
                {"label": label, "count": info["count"],
                 "properties": info["properties"]}
                for label, info in node_types.items()
            ],
            "relationships": [r.get("relationshipType") for r in rels],
            "vectorIndexes": vector_idx,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": f"查询 schema 失败: {str(e)}"})


async def execute_cypher(cypher: str, params: dict = None):
    """执行任意 Cypher 查询"""
    if not Neo4jClient.is_connected():
        Neo4jClient.auto_connect()          # 服务端有 neo4j.env 时免手填
    if not Neo4jClient.is_connected():
        return json.dumps({"error": "Neo4j 未连接（服务端未配置 neo4j.env，前端也未填写连接信息）"})
    if not cypher or not cypher.strip():
        return json.dumps({"error": "Cypher 查询不能为空"})
    try:
        import role_policy as _rp
        if _rp.agent_cypher_denied(cypher):
            return json.dumps({"error": "图谱删除类语句（DELETE/DETACH/DROP）需管理员权限。"})
    except Exception:
        pass

    params = params or {}
    try:
        results = Neo4jClient.run(cypher, params)
        truncated = results[:20] if len(results) > 20 else results
        return json.dumps({
            "count": len(results),
            "truncated": len(results) > 20,
            "columns": list(results[0].keys()) if results else [],
            "data": truncated,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "error": f"Cypher 查询失败: {str(e)}",
            "cypher": cypher
        })
