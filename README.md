# C4EAI · TruthCross — 组内科研辅助智能体

一个面向科研辅助与共享知识库的多用户、多工具智能体。前端是纯静态 SPA，后端是 FastAPI；内置共享 Wiki、Neo4j 知识图谱检索、MinerU 文献批量解析、联网搜索、文件处理、代码执行与技能系统。

> 设计对齐 [Hermes Agent](https://github.com/NousResearch/hermes-agent)：工具能力由 OpenAI function-calling schema 提供，技能以 `<available_skills>` 简列注入系统提示词，详情按需 `skill_view` 读取。

---

## 🧬 TruthCross · 端到端文献挖掘工作流

本仓库同时内置 **TruthCross** —— 面向催化文献的端到端多模态数据挖掘与知识图谱工作流（[`skills/fenton-multiagent-mining/`](skills/fenton-multiagent-mining/SKILL.md)，v3.4.8）。它把「文献筛选 → 解析归档 → 图像挖掘 → 图文互证 → 节点对齐 → schema 填充 → 图谱入库」串成一条可复现的流水线，支持跨文献推理与文献持续入库。

完整说明与使用声明见 **[TRUTHCross.md](TRUTHCross.md)**。

---

## ⚡ 快速启动（Windows）

### 方式一：clone 后一键启动（推荐）

```bash
git clone https://github.com/C4E-JNU/C4E.git
cd C4E
启动.bat
```

首次运行 `启动.bat` 会自动做三件事：

1. 从本仓库的 [Releases](https://github.com/C4E-JNU/C4E/releases) 下载约 200 MB 的便携运行包（自带 Python 3.11、全部依赖和嵌入模型）；
2. 解压到当前目录；
3. 启动后端，并自动打开 <http://127.0.0.1:8080>。

第二次以后直接双击 `启动.bat` 即可，不再下载。若 Release 尚未上传，脚本会退回到使用系统 Python 创建 `.venv` 并安装依赖。

### 方式二：直接下载便携包

不想用 Git 的话，也可以从 [Releases](https://github.com/C4E-JNU/C4E/releases) 下载 `C4EAI-Portable.zip`，解压后双击 `启动.bat`。便携包不需要安装 Python。

### 方式三：源码运行（开发者）

```bash
git clone https://github.com/C4E-JNU/C4E.git
cd C4E/backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cd ..
python start.py
```

启动后访问 <http://127.0.0.1:8080>。默认只监听本机；如需让同一局域网的手机/平板访问，设置 `C4EAI_HOST=0.0.0.0` 后再启动。

---

## 🔧 首次配置

打开页面右上角的「配置」面板：

1. **大模型（必填）**：DeepSeek、通义千问、OpenAI 或任意兼容 OpenAI 接口的服务，填写 Base URL、API Key、模型名。
2. **Neo4j（可选）**：使用图谱功能时填写自己的实例；也可以复制 `skills/fenton-multiagent-mining/scripts/neo4j.env.example` 为 `neo4j.env` 后填写。仓库不内置任何共享实例凭据。
3. **MinerU Token（可选）**：批量解析 PDF 时需要，需自备账号。
4. **工具/技能**：按需钩选；启用 `fenton-multiagent-mining` 后可对指定文献目录发起 TruthCross 流水线。

> 如果升级后仍有工具没默认打开，点「工具」标题旁的 **全选 / Select all**，或在浏览器控制台执行
> `localStorage.removeItem('ragagent-agent-config')` 后刷新页面。新版本会把旧存档迁移到
> `settingsRev=3`，一次性启用 `graph_query`、`delegate_task`、文件与终端工具。

### 图谱向量维度

应用默认使用 `BAAI/bge-small-zh-v1.5`（**512 维**）。如果你导入的旧图谱里向量索引是 384 维，
`graph_query` 的向量检索会报维度不匹配。先运行只读检查：

```bash
python scripts/check_vector_compat.py --env skills/fenton-multiagent-mining/scripts/neo4j.env
```

确认需要重建后，再执行（会删除并重建 5 个向量索引，然后重新嵌入节点，耗时取决于节点数）：

```bash
python scripts/rebuild_graph_embeddings.py --env skills/fenton-multiagent-mining/scripts/neo4j.env --apply
```

---

## 📚 文档

| 文档 | 什么时候读 |
|---|---|
| [TRUTHCross.md](TRUTHCross.md) | TruthCross 的能力、部署、上手示例与使用声明 |
| [CHANGELOG.md](CHANGELOG.md) | 想知道"某处为什么这么写" —— 按日期记录改动原因与影响文件 |
| [项目架构说明.md](项目架构说明.md) | 要改代码 —— 前后端文件职责、API 全表、核心链路 |

---

## ✨ 功能

| 模块 | 说明 |
|---|---|
| 🧠 **Agent 引擎** | 手搓流式（custom）与 LangChain 两套引擎 |
| 🌐 **知识图谱** | Neo4j 查询（graph_query：schema / cypher / search） |
| 📄 **文献解析** | MinerU v4 批量 PDF→Markdown |
| 🔍 **联网搜索** | web_search / web_extract，免费 Bing 抓取无 key |
| 📎 **文件解析** | pdf / docx / xlsx / xls / pptx / odt / txt |
| 🖼️ **图片多模态** | 后端自动转 base64 交给视觉模型 |
| 💻 **代码执行** | run_code（沙箱） + terminal（项目真实环境） |
| 🗂️ **文件工具** | read_file / write_file / list_files |
| 💾 **技能系统** | SKILL.md + skill_view / skill_write |
| 🎛️ **权限模式** | 安全 / 询问 / 完全访问 |
| 🔤 **工具/技能钩选** | 配置面板中按分组动态钩选 |

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

详见 [项目架构说明.md](项目架构说明.md)。

---

## 📦 维护者：生成便携包

便携包由 `scripts/build_portable.py` 从当前仓库源码 + 一个已安装依赖的 Python 运行时生成：

```bash
python scripts/build_portable.py --runtime D:\C4EAI-build\payload --out dist\C4EAI-Portable.zip
```

然后把 `dist\C4EAI-Portable.zip` 上传到 GitHub Releases，资产名保持为 `C4EAI-Portable.zip`。`启动.bat` 会通过 `releases/latest/download/C4EAI-Portable.zip` 自动获取它。

---

## 🔐 安全说明

- 仓库不包含任何真实 API Key、Neo4j 密码或内网地址；请在本地配置。
- `.env`、`neo4j.env`、`workspace/`、`model_cache/`、`python/` 均已被 `.gitignore` 排除。
- 智能体具备文件读写与终端执行能力，建议在独立目录或容器中运行，并提前备份重要数据。

---

## 📄 LICENSE

本项目采用 [MIT License](LICENSE)。

## 🙏 致谢

- 架构与设计借鉴 [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- MinerU：PDF 解析
- LangChain：Agent 框架（可选引擎）
