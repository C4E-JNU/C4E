# C4EAI — 催化降解研究智能体

一个面向**催化降解 / 光催化**科研场景的多工具智能体。前端纯静态 SPA + FastAPI 后端，内置知识图谱（Neo4j）检索、文献批量解析（MinerU）、联网搜索、文件处理、代码执行与技能系统。

> 设计对齐 [Hermes Agent](https://github.com/NousResearch/hermes-agent)：工具能力由 OpenAI function-calling schema 提供，技能以 `<available_skills>` 简列注入系统提示词、详情按需 `skill_view` 读取。

---

## ✨ 功能

| 模块 | 说明 |
|---|---|
| 🧠 **Agent 引擎** | 手搓流式（custom）与 LangChain 两套引擎，保留候选 |
| 🌐 **知识图谱** | Neo4j 查询（graph_schema / execute_cypher），面向污染物-催化剂-降解方法 |
| 📄 **文献解析** | MinerU v4 批量 PDF→Markdown（支持文件/文件夹） |
| 🔍 **联网搜索** | web_search / web_extract，免费 Bing 抓取无 key |
| 📎 **全后端文件解析** | pdf / docx / xlsx / xls / pptx / odt / txt（上传只存 file_id，历史附件即引用，Hermes 式） |
| 🖼️ **图片多模态** | 后端自动转 base64 交给千问等视觉模型；仅 OCR 保留前端 |
| 💻 **代码执行** | run_code（沙箱） + **terminal**（项目真实环境，可 uv/pip 装包、建 venv） |
| 🗂️ **文件工具** | read_file / write_file / list_files（支持按 file_id 读上传附件） |
| 💾 **技能系统** | SKILL.md 技能 + skill_view / skill_write 自我生成 |
| 🎛️ **权限模式** | 安全 / 询问 / 完全访问，控制 agent 的文件与终端操作边界 |
| 🔤 **工具/技能钩选** | 配置面板中按分组动态钩选，数据来自后端注册表 |

---

## 🚀 快速启动

### 依赖
- Python 3.10+（推荐 3.11/3.12）
- 可选：`uv`（更快管理环境），或 `pip`
- 图谱：Neo4j 实例（Aura 或本地）
- MinerU：配置 API Token

### 安装

```bash
# 方式一：uv（推荐）
cd c4eai
uv sync            # 根据 backend/pyproject.toml + uv.lock 创建并装好环境

# 方式二：pip
cd c4eai/backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 启动后端（端口 8080，同时服务前端 + API）

```bash
# 方式一：start.bat（Windows）
start.bat

# 方式二：命令行
cd backend
python app.py

# 启动后访问 http://127.0.0.1:8080
```

首次启动约需 20 秒（编译导入）。启动后打开页面，在**智能体配置**中：
1. 配置模型（DeepSeek / 千问 / OpenAI 兼容）API Key
2. 连接 Neo4j 图谱
3. 配置 MinerU Token（文献解析）
4. 按需钩选工具/技能

---

## 🏗️ 架构

```
前端（静态 SPA）                    后端（FastAPI, :8080）
┌─────────────────────┐     ┌─────────────────────────────┐
│ index.html / js/*   │     │ app.py  (全部 /api/* 端点)    │
│  messages.js 渲染   │     │  agent/agent.py              │
│  graph_rag.js Agent │ ←→  │    stream_agent (手搓)       │
│  agent_config.js 配置│ SSE  │    stream_agent_lc (LangChain)│
│  工具/技能动态钩选   │     │  agent/tools.py tools_registry│
└─────────────────────┘     │  tools/  (16 个后端工具)      │
                            └─────────────────────────────┘
```

**数据流（一次对话）**：`messages.js sendMessage → reactAgentLoop → POST /api/agent/stream(SSE) → stream_agent → LLM + 工具循环 → 逐事件回流 → 前端渲染`

详见 [`项目架构说明.md`](项目架构说明.md)（含全部流程图的 mermaid 源码）。

---

## 🛠️ 工具清单（后端 `tools_registry`）

| 分组 | 工具 |
|---|---|
| 图谱 (graph) | graph_schema · execute_cypher |
| 联网 (web) | web_search · web_extract |
| 技能 (skills) | skill_list · skill_view · skill_write |
| 文件 (file) | read_file · write_file · list_files |
| 代码/终端 (code) | run_code · terminal |
| 记忆 (memory) | search_history · search_knowledge · conversation_notes |
| 任务 (task) | todo |
| 文档解析 (documents) | mineru_parse |

---

## 📄 LICENSE

本项目采用 [MIT License](LICENSE)。

---

## 🙏 致谢

- 架构与设计借鉴 [Hermes Agent](https://github.com/NousResearch/hermes-agent)（工具 schema 优先、技能简列注入、项目 venv + uv 环境管理）
- MinerU：PDF 解析
- LangChain：Agent 框架（可选引擎）
