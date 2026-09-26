---
name: graph-guide
description: 催化降解图谱查询指引 — 包含图谱结构、别名映射、查询策略、回答格式
version: 1.0.0
author: C4EAI
license: MIT
metadata:
  hermes:
    tags: [catalysis, degradation, neo4j, graph-rag, cypher]
    related_skills: []
---

# 催化降解图谱查询指引

## 图谱结构

首次使用请先调用 `graph_schema()` 获取最新结构。

### 主要节点类型

- **(Pollutant)** — 污染物
  - 属性: name, formula, category, toxicity, structure, aliases
- **(Catalyst)** — 催化剂
  - 属性: name, band_gap, category, aliases, preparation, characterization
- **(DegradationMethod)** — 降解方法
  - 属性: name, condition, efficiency, time, ph, byproducts
- 其他: Experiment, ResearchPaper, Element, Property

### 主要关系类型

- `[:DEGRADES]` — Pollutant → Catalyst（被降解）
  - 属性: efficiency, time, ph, condition
- `[:PRODUCES]` — 产生副产物
- `[:HAS_PROPERTY]` — 具有某种性质

## 别名映射

详细映射表见 `references/alias.md`（调用 skill_view("graph-guide", "references/alias.md") 读取）。常用：

| 中文/简称 | 数据库名 |
|-----------|---------|
| 环丙沙星 / CIP | Ciprofloxacin |
| 四环素 | Tetracycline |
| g-C3N4/Bi2WO6 / CNBWO | g-C3N4/Bi2WO6 |
| 石墨相氮化碳 | g-C3N4 |
| 二氧化钛 | TiO2 |
| 氧化锌 | ZnO |

## 查询策略

### 分步推理

每次只调一个工具，每步先思考再行动：

1. **Step 1** — graph_schema() 了解图结构（仅首次）
2. **Step 2** — execute_cypher 写具体查询
   - 先模糊搜: `MATCH (n) WHERE n.name CONTAINS $q`
   - 再精准查: `MATCH (p:Pollutant {name:$name})`
   - 再展开: `MATCH (p)-[r]-(c) RETURN p, r, c`
3. **Step 3** — 如果结果为空，换方式重试：
   - 小写/模糊匹配
   - 搜 aliases 属性
   - 换标签类型
   - 最后用自身知识（标注💡）

### 结果为空时的降级链

```
空结果 → 模糊匹配 → 别名搜索 → 换标签 → 自身知识
```

## 回答格式

- 📊 基于图谱数据
- 💡 基于自身知识
- 📊+💡 混合来源
- 数据优先用表格（催化剂 | 效率 | 时间 | pH）
- 保留专业术语英文原文
