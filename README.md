# C4EAI · Shared Wiki — 组内科研辅助智能体

一个面向**组内科研辅助与共享知识库**的多用户多工具智能体。前端纯静态 SPA + FastAPI 后端，内置共享 Wiki（笔记/文档共享库）、知识图谱（Neo4j）检索、文献批量解析（MinerU）、联网搜索、文件处理、代码执行与技能系统。

> 设计对齐 [Hermes Agent](https://github.com/NousResearch/hermes-agent)：工具能力由 OpenAI function-calling schema 提供，技能以 `<available_skills>` 简列注入系统提示词、详情按需 `skill_view` 读取。

---

## 🧩 部署形态（先看懂这一条）

**后端跑在"你启动它的那台机器"上，前端只是访问者。**

```
你的笔记本                          服务器
start.bat → 后端(8080)              start_c4eai.sh → 后端(8088)
   ↑ 浏览器访问                        ↑ 浏览器访问
   └ 工作区 = 你笔记本上的 workspace/   └ 工作区 = 服务器上的 workspace/
```

- 每台机器 = **独立的后端 + 独立的工作区**（各自的笔记、附件、对话、产出）。
- 想让全组共用同一份知识库：把后端部署到一台服务器（设 `C4EAI_AUTH=1` 开账号系统），其他人设 `C4EAI_HUB` 指向它即可**用远端共享库、留本地个人数据**。
- 不设 `C4EAI_HUB` 时，本机就是完全独立的单机版。

---

## ✨ 功能

| 模块 | 说明 |
|---|---|
| 🧠 **Agent 引擎** | 手搓流式（custom）与 LangChain 两套引擎，保留候选 |
| 🌐 **知识图谱** | Neo4j 查询（graph_schema / execute_cypher），图谱结构可配置 |
| 📄 **文献解析** | MinerU v4 批量 PDF→Markdown（支持文件/文件夹） |
| 🔍 **联网搜索** | web_search / web_extract，免费 Bing 抓取无 key |
| 📎 **全后端文件解析** | pdf / docx / xlsx / xls / pptx / odt / txt（上传只存 file_id，历史附件即引用） |
| 🖼️ **图片多模态** | 后端自动转 base64 交给视觉模型；仅 OCR 保留前端 |
| 💻 **代码执行** | run_code（沙箱） + **terminal**（项目真实环境，可装包、建 venv） |
| 🗂️ **文件工具** | read_file / write_file / list_outputs（按 file_id 读上传附件） |
| 💾 **技能系统** | SKILL.md 技能 + skill_view / skill_write 自我生成 |
| 🎛️ **权限模式** | 安全 / 询问 / 完全访问，控制 agent 的文件与终端操作边界 |
| 🔤 **工具/技能钩选** | 配置面板中按分组动态钩选，数据来自后端注册表 |
| 👥 **多用户** | 账号 + 等级（L1–L5），共享库与个人库物理隔离 |
| 🔄 **多设备同步** | 同一账号多设备同时登录，对话/笔记/共享库经 SSE 实时互见 |

---

## 🚀 快速开始

### 1. 环境要求

- **Python 3.10+**（推荐 3.11 / 3.12）
  > ⚠️ Windows 上必须是**真实安装的 Python**（python.org 或 Anaconda）。微软商店的 `python.exe` 是个占位桩，无法运行本项目。
- 可选：`uv`（更快的包管理）
- 可选：Neo4j 实例（用图谱功能才需要）、MinerU API Token（用文献解析才需要）

### 2. 安装依赖

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate           # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt
```

用 uv 的话：`uv pip install -r backend/requirements.txt`

### 3. 启动

```bash
python start.py                  # 任意系统
# 或 Windows 双击：start.bat
```

启动后会自动打开浏览器；默认同时监听 `0.0.0.0`，所以同一局域网的手机/平板也能访问 `http://<本机IP>:8080`。

> ⚠️ **安全提示**：本地实例默认**不启用登录**（未设 `C4EAI_AUTH`）。一旦 `C4EAI_HOST=0.0.0.0`，**同网段的任何人都能访问你的数据**。在校园网/公共网络中请设 `C4EAI_AUTH=1` 开启账号系统。

---

## ⚙️ 配置

日常配置全在网页**右上角「配置」面板**里，不用改代码：

| 要配什么 | 面板位置 | 说明 |
|---|---|---|
| 模型 | 配置 → 模型 | 任何兼容 OpenAI 接口的服务：本地 llama-server / DeepSeek / 通义 / OpenAI…，填 Base URL、Key、模型名 |
| 知识图谱 | 配置 → 图谱 | Neo4j 的 URI / 用户名 / 密码 |
| MinerU | 配置 → 文献解析 | 填 API Token |
| 联网搜索 | — | 免密钥，开箱可用 |

### 环境变量（可选，覆盖默认）

| 变量 | 默认 | 作用 |
|---|---|---|
| `C4EAI_HOST` | `127.0.0.1` | 监听地址。`0.0.0.0` = 局域网可访问 |
| `C4EAI_PORT` | `8080` | 服务端口 |
| `C4EAI_AUTH` | 未设 | `=1` 启用账号系统（多人共用/对外必须开） |
| `C4EAI_ADMIN_PASSWORD` | 随机 | 初始 admin 密码；不设会生成随机密码并打印在启动日志里 |
| `C4EAI_HUB` | 空 | 共享库后端地址。**设了它 → 本机通过 HTTP 使用远端共享库**；不设 → 本机独立运行 |
| `C4EAI_LLM_BASE` / `C4EAI_LLM_MODEL` | 空 / `qwen3` | 默认模型端点（一般在上面的面板里填即可） |

---

## 💾 数据放在哪

所有运行数据都在 `workspace/` 目录（已在 `.gitignore` 中排除，不会进版本库）：

```
workspace/
├── users/<账号>/            个人数据（按账号物理隔离）
│   ├── notes.json           个人笔记
│   ├── settings.json        个人配置
│   ├── chats.json           对话历史
│   ├── uploads/<file_id>/   上传的附件（meta.json + 原文件）
│   └── outputs/             智能体产出的文件（写文件落这里，才能被前端打开/下载）
├── pool/                    共享库（组内共用）
│   ├── _folders.json        分组树（「笔记」「文档」两个真实根文件夹）
│   ├── _vector_knowledge.json   向量索引
│   └── ...                  共享条目与附件
└── _auth/                   账号与会话
```

- **备份/迁移**：整个复制 `workspace/` 即可。
- **想给别人一份干净环境**：删掉 `workspace/` 重启，会重新初始化。

---

## 🔎 向量检索（可选）

共享库的知识检索依赖本地嵌入模型（`fastembed`，默认 bge-small-zh）：
**首次使用会联网下载约 90 MB 到 `model_cache/`**（该目录已 gitignore）。
离线环境请预先下载模型，或使用打包好的分发包（内含模型，解压即用）。

---

## 📚 文档

| 文档 | 什么时候读 |
|---|---|
| [项目架构说明.md](项目架构说明.md) | **要改代码** —— 前后端文件职责、API 全表、核心链路 |
| [CHANGELOG.md](CHANGELOG.md) | 想知道"某处为什么这么写" —— 按日期记录每次改动的原因与影响文件 |

---

## 🩺 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 启动报 `AttributeError: _ARRAY_API not found` | numpy 2.x 撞上旧版 numexpr/bottleneck → `pip install -U numexpr bottleneck` |
| 双击 `start.bat` 闪退，或弹出微软商店 | 系统里没有真实 Python（只有商店占位桩）→ 装 python.org 版并勾选 Add to PATH |
| 浏览器打不开 | 端口被占：`start.bat` 会先杀掉占用 8080 的旧进程；或改 `C4EAI_PORT` |
| 局域网其他设备访问不了 | 确认 `C4EAI_HOST=0.0.0.0`，并放行防火墙 8080 端口 |
| 共享库是空的 | 没设 `C4EAI_HUB`（=本机独立运行，共享库自然为空），或在账号系统下当前账号没有共享库权限 |
| 智能体写的文件点不开 | 文件必须落在 `workspace/users/<账号>/outputs/` 下才会被登记（工具会自动这么做） |

---

## 📄 License

MIT
