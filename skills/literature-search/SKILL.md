---
name: literature-search
description: 当要查文献、补全题名/期刊/年份/DOI/引用数，或拿到 DOI 抓原文时用。含三接口分工、S2 限流的实战退路与「换词最多两次」的收敛策略。
version: 1.1
---

# 查文献（检索 → 定位 DOI → 抓原文）

## 何时用

- 用户给关键词/主题，要「找几篇相关文献」；
- 用户给一个题名或半截引用，要补全期刊/年份/DOI/作者；
- 要做「先查清楚再写笔记/建条目」的活（配合 `c4eai-document-entry` 那一套）。

## 三个工具的定位（别混用）

| 工具 | 什么时候用 | 得到什么 | 实测状态 |
|---|---|---|---|
| `crossref_lookup` | **已有 DOI** → 权威元数据；或只有题名 → 查 DOI | 题名/期刊/年份/作者/DOI/被引 | ✅ 稳定，无严格限流 |
| `arxiv_search` | 预印本、物理/CS/材料等 arXiv 覆盖领域 | arXiv id/题名/摘要/作者/主分类 | ✅ 可用 |
| `semantic_scholar_search` | 要**引用数**判断影响力 | title/year/venue/citationCount/abstract | ❌ 见下，**关键词搜索基本别指望** |

## 标准流程

1. **先取元数据**：有 DOI 直接 `crossref_lookup(doi=...)`；没有就 `crossref_lookup(query="题名")`
   或 `arxiv_search(query=...)`。
2. **拿到 DOI 再抓正文**：`web_extract(url="https://doi.org/<DOI>")`（DOI 解析跳到出版社页）。
   — ⚪ 此路径本次**未单独验证**，别当成已验证结论；抓不动就退回元数据回答。
   — 不要拿 doi.org 链接去 `web_search`，那是浪费一次调用。
3. **要 PDF 全文**：本地有 PDF 就 `read_file`；扫描版走 `mineru_parse` 技能。
4. **落库**：一篇文献要立条目时按 `c4eai-document-entry` 流程走。

## S2 限流：唯一的可靠退路（实测）

- `semantic_scholar_search` 匿名 key 下 **search 端点在本网络里是持续 429**（等 30~90 秒同样），
  **不要重试**，直接改用 `crossref_lookup` 并在回答里说明限流。
- **但 S2 的详情端点是通的**：已知 DOI、只要引用数时，走
  `web_extract(url="https://api.semanticscholar.org/graph/v1/paper/DOI:<DOI>?fields=title,year,venue,citationCount,externalIds")`
  → 实测返回 200 + JSON。
- ⚠️ **引用数口径不同**：同一篇论文 Crossref 与 S2 给的数不一样（实测 NumPy 那篇 S2 22589 / Crossref 24698）。
  报引用数时**注明来源**，不要混着用或当成绝对真值。

## 收敛策略（重点，避免瞎搜）

- **换词最多两次**：第一次用用户原话，第二次换同义词或英文术语（「光催化降解抗生素」→
  `photocatalytic degradation of antibiotics`）。两次都空 → 告诉用户「没检索到，建议给 DOI 或题名」，
  **不要继续换词**。
- **认「够了」**：拿到 3~5 条题名+年份+期刊+DOI 就够回答「有哪些相关文献」，别为凑数继续搜。
- **认「失败」**：429 → 换 crossref（见上节），**不原路重试**。
- **不许编造**：任何 DOI、年份、期刊名都必须来自工具返回；工具没返回就不写。
  （✅ 实测兜底：随便编的 DOI 会被 crossref 明确返回「未找到」——这条红线是可被工具拦住的。）

## 常见错误

- ❌ 用 `web_search` 找文献元数据（噪声大、易过期）→ ✅ crossref/arxiv/S2 结构化接口。
- ❌ 同一关键词搜三遍以上 → ✅ 换词最多两次。
- ❌ S2 429 后反复重试 → ✅ 换 crossref，或已知 DOI 时走 S2 详情端点（经 web_extract）。
- ❌ 把 arXiv id 当 DOI 用 → arXiv id 形如 `2509.12345`，DOI 形如 `10.1038/s41467-...`，不能互查。
