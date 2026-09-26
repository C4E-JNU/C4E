"""
Neo4j 图谱工具 — graph_schema + execute_cypher
直接从前端 agent_tools.js 的 JS 逻辑翻译为 Python
"""
import json
from neo4j import GraphDatabase

class Neo4jClient:
    """Neo4j 连接管理器（单例模式）"""
    _driver = None

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
            connection_timeout=10000,
        )
        cls._driver.verify_connectivity()
        return cls._driver

    @classmethod
    def is_connected(cls):
        return cls._driver is not None

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
        """执行 Cypher 查询，返回结果列表"""
        if not cls._driver:
            raise Exception("Neo4j 未连接")
        params = params or {}
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


# ── 工具函数 ──────────────────────────────────────────

async def execute_graph_schema():
    """获取 Neo4j 图谱的节点类型、关系类型、属性结构"""
    if not Neo4jClient.is_connected():
        return json.dumps({"error": "Neo4j 未连接"})

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
        return json.dumps({"error": "Neo4j 未连接"})
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
