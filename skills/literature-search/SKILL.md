---
name: literature-search
description: "查文献的正确顺序：先用 crossref_lookup/arxiv_search/semantic_scholar_search 拿到题名/期刊/年份/DOI，再用 web_extract 抓原文。含收敛策略：什么时候换词、什么时候停。"
version: 1.0.0
tags: [literature, search, doi, arxiv, crossref]
---

# 查文献（检索 → 定位 DOI → 抓原文）

## 何时用

- 用户给关键词/主题，要「找几篇相关文献」；
- 用户给一个题名或半截引用，要补全期刊/年份/DOI/作者；
- 要做「先查清楚再写笔记/建条目」的活（配合 `c4eai-document-entry` 那一套）。

## 三个工具的定位（别混用）

| 工具 | 什么时候用 | 得到什么 |
|---|---|---|
| `crossref_lookup` | **已有 DOI** → 拿权威元数据；或只有题名 → 按题名查 DOI | 题名/期刊/年份/作者/DOI/被引 |
| `arxiv_search` | 预印本、物理/CS/材料等 arXiv 覆盖的领域 | arXiv id/题名/摘要/作者/主分类 |
| `semantic_scholar_search` | 要**引用数**判断影响力，或要摘要与 externalIds | title/year/venue/citationCount/abstract/DOI |

## 标准流程

1. **先取元数据**：有 DOI 直接 `crossref_lookup(doi=...)`；没有就 `crossref_lookup(query="题名")` 或 `arxiv_search(query=...)`。
2. **拿到 DOI 再抓正文**：`web_extract(url="https://doi.org/<DOI>")`（DOI 解析会跳到出版社页，能拿到摘要/正文）。
   — 不要拿 doi.org 的链接去 `web_search`，那是浪费一次调用。
3. **要 PDF 全文**：若用户本地有 PDF，直接 `read_file(path=...)`；扫描版走 `mineru_parse` 技能。
4. **落库**：一篇文献要立条目时，按 `c4eai-document-entry` 的流程走（气泡放 `DOI …`、`2025.11`、`ACS Catal.` 这类信息）。

## 收敛策略（这一节是重点，避免瞎搜）

- **换词最多两次**：第一次用用户原话，第二次换同义词或英文术语（如「光催化降解抗生素」→ `photocatalytic degradation of antibiotics`）。两次都空 → 直接告诉用户「没检索到，建议给出 DOI 或题名」，**不要继续换词**。
- **认「够了」**：拿到 3~5 条题名+年份+期刊+DOI，就够回答「有哪些相关文献」；不要为了凑数继续搜。
- **认「失败」**：`semantic_scholar_search` 报 429 限流时**不要重试**，改用 `crossref_lookup`（无严格限流），并在回答里说明限流。
  ⚠️ 实测：**匿名 key 下 S2 的 search 端点在本网络里是持续 429**（等 30~90 秒也一样），
  而 `https://api.semanticscholar.org/graph/v1/paper/DOI:<doi>` 是 200 —— 也就是说
  「已知道 DOI、要引用数」时可走详情端点，但「按关键词搜」这条路基本别指望，直接用 crossref。
- **不许编造**：任何一个 DOI、年份、期刊名都必须来自工具返回。工具没返回就不写。

## 常见错误

- ❌ 用 `web_search` 找文献元数据（噪声大、易过期）→ ✅ 用 crossref/arxiv/S2 的结构化接口。
- ❌ 同一关键词反复搜三遍以上 → 上面「换词最多两次」。
- ❌ 把 arXiv 的 id 当成 DOI 用 → arXiv id 形如 `2509.12345`，DOI 形如 `10.1038/s41467-...`，两者不能互查。
