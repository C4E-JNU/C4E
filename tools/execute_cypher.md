# 工具：execute_cypher

> **⚠️ 说明文档：** 工具的实际代码在 `js/agent_tools.js` 中。修改此文件不影响智能体行为。

执行任意 Cypher 查询语句。这是核心工具，所有图谱数据都通过它获取。

## 触发条件

需要从 Neo4j 图数据库查询任何数据时自动调用。支持任何 MATCH / RETURN / CALL 语句。

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| cypher | string | 是 | 要执行的 Cypher 查询语句 |
| params | object | 否 | 查询参数（可选），用于安全传参 |

## 用法示例

```cypher
-- 模糊搜索实体
MATCH (n) WHERE n.name CONTAINS $q RETURN n.name, labels(n)[0] LIMIT 10

-- 精确查询 + 展开关系
MATCH (p:Pollutant {name:$name})-[r:DEGRADES]-(c:Catalyst)
RETURN p.name, c.name, r.efficiency, r.time, r.ph

-- 聚合统计
MATCH (c:Catalyst)-[r:DEGRADES]-(p)
RETURN c.name, avg(r.efficiency) ORDER BY avg(r.efficiency) DESC
```

## 返回格式

```json
{
  "count": 5,
  "truncated": false,
  "columns": ["name", "efficiency"],
  "data": [{"name": "示例节点", "efficiency": 95}, ...]
}
```

返回结果最多 20 条，超出会被截断。
