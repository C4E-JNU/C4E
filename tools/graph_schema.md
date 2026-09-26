# 工具：graph_schema

> **⚠️ 说明文档：** 工具的实际代码在 `js/agent_tools.js` 中。修改此文件不影响智能体行为。

获取 Neo4j 知识图谱的完整结构，包括所有节点类型（如 Pollutant, Catalyst）、关系类型（如 DEGRADES）、各类型的属性列表、向量索引信息。

## 触发条件

首次查询时、或需要了解图结构时自动调用。

## 参数

无

## 返回示例

```json
{
  "nodeTypes": [
    {"label": "Pollutant", "count": 50, "properties": ["name: String", "formula: String", ...]},
    {"label": "Catalyst", "count": 30, "properties": ["name: String", "band_gap: Float", ...]}
  ],
  "relationships": ["DEGRADES", "PRODUCES", "HAS_PROPERTY"],
  "vectorIndexes": [...]
}
```
