---
name: mineru-agent-parse
description: 使用 mineru_parse 工具将 PDF 文献解析为 Markdown，支持单个 PDF 文件或文件夹批量（递归扫 .pdf，MinerU v4 批量接口），需配置 MinerU API Token
version: 3.0.0
---

# MinerU PDF 解析（PDF → Markdown）

## 概述

将 PDF 文献解析为 Markdown。当用户上传了 PDF，或给出一个 PDF 文件/文件夹路径，需要把 PDF 转成可读 Markdown 时调用 `mineru_parse` 工具。

## 触发场景

- 用户上传 PDF，要求解析/提取内容
- 用户给出 PDF 文件路径或一个含多个 PDF 的文件夹路径
- 后续文本挖掘需要先把 PDF 清洗成 Markdown

## 使用 mineru_parse 工具

| 参数 | 说明 |
|------|------|
| `path` | PDF 文件路径，或含 PDF 的文件夹路径（递归扫描所有 `.pdf`）。可为相对 `workspace/` 的相对路径或绝对路径 |
| `out_dir` | zip 输出目录（默认 `mineru_output`） |
| `single_pdf` | 一般不需设置；工具自动判断是文件还是目录 |

返回：`ok`、`pdf_count`、`downloaded`（每个 pdf 的 zip 路径）、`failed`、`batch_errors`。

### 单文件
```
mineru_parse(path="workspace/uploads/xxx/某论文.pdf", out_dir="mineru_output")
```

### 文件夹批量
```
mineru_parse(path="workspace/pdf_folder", out_dir="mineru_output")
# 递归扫 *.pdf，按每批 50 个自动分批，每 pdf 输出同名 .zip
```

## 底层实现（MinerU v4 批量接口）

工具调用 `backend/tools/mineru_tools.py`，采用官方 v4 批量流程：
1. `POST /api/v4/file-urls/batch` 申请预签名上传 URL（参数 enable_formula, language=ch, enable_table）
2. `PUT` 逐个上传 PDF
3. `GET /api/v4/extract-results/batch/{batch_id}` 轮询直到全部 done/failed
4. 下载每个 done 的 `full_zip_url` → 同名 `.zip`（内含 parse 产物，通常含 .md/images）

批量上限 50 个/批，轮询最多 60 次×10s，失败自动记录。

## 前置条件

1. **MinerU API Token 必须已配置**：在『智能体配置』面板的 **MinerU 解析配置** 填写 Token 并保存（`Authorization: Bearer`）。
2. Token 会过期，过期后返回鉴权错误，需用户重新更新。
3. 单文件 ≤ 200MB（MinerU v4 支持大文件）。

## 注意事项

- 解析异步，工具内部轮询；文件夹批量自动分批串行处理
- Token 未配置时返回引导用户到配置面板
- 结果下载为 zip（不是直接 .md），需要时用 `read_file`/解压工具读取内容

## 实现脚本

核心在 `backend/tools/mineru_tools.py`（v4 批量 API），由 `mineru_parse` 工具调用。
