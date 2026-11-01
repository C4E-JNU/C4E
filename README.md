# C4EAI · Shared Wiki — a research assistant agent for a lab group

A multi-user, multi-tool agent for **research assistance and a shared knowledge base**. It combines a static single-page frontend with a FastAPI backend and ships with a shared wiki (notes/documents), knowledge-graph (Neo4j) retrieval, batch literature parsing (MinerU), web search, file handling, code execution and a skill system.

> The design follows [Hermes Agent](https://github.com/NousResearch/hermes-agent): tool capabilities come from OpenAI function-calling schemas, skills are listed as `<available_skills>` with name + description, and full skill text is read on demand with `skill_view`.

---

## Documentation

| Document | When to read it |
|---|---|
| **[HANDOFF.md](开发中间产物/HANDOFF.md)** | **Read first when taking over** — current state, permissions, deployment shapes, dev commands, known pitfalls |
| **[CHANGELOG.md](CHANGELOG.md)** | Why a piece of code looks the way it does — dated entries with reasons and affected files |
| [ARCHITECTURE.md](项目架构说明.md) | Changing code — frontend/backend responsibilities, full API table, core flows |

> For server deployment see [SHARED_LIBRARY_DEPLOYMENT.md](共享库部署指南.md).

---

## Features

| Module | Description |
|---|---|
| 🧠 **Agent engines** | A hand-written streaming engine (custom) and a LangChain engine |
| 🌐 **Knowledge graph** | Neo4j queries via graph_query (schema / cypher / search); the schema is configurable and the current graph stores research entity relations |
| 📄 **Literature parsing** | MinerU v4 batch PDF→Markdown (single file or folder) |
| 🔍 **Web search** | web_search / web_extract; free Bing scraping without a key |
| 📎 **Backend file parsing** | pdf / docx / xlsx / xls / pptx / odt / txt (uploads store a file_id; history attachments are references, Hermes style) |
| 🖼️ **Multimodal images** | The backend converts images to base64 for vision models; OCR remains frontend-only |
| 💻 **Code execution** | run_code (restricted) plus **terminal** (the real project environment; uv/pip installs, venvs) |
| 🗂️ **File tools** | read_file / write_file / list_files (also reads uploaded attachments by file_id) |
| 💾 **Skill system** | SKILL.md skills plus skill_view / skill_write for self-authoring |
| 🎛️ **Permission modes** | Safe / Ask / Full access, bounding file and terminal operations |
| 🔤 **Tool and skill selection** | Grouped checkboxes in the settings panel, populated from the backend registry |

---

## Quick start

### Requirements
- Python 3.10+ (3.11/3.12 recommended)
- Optional: `uv` (faster environments) or `pip`
- Graph: a Neo4j instance (Aura or local)
- MinerU: an API token

### Install

```bash
# Option 1: uv (recommended)
cd c4eai
uv sync            # creates the environment from backend/pyproject.toml + uv.lock

# Option 2: pip
cd c4eai/backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### Start the backend (port 8080, serves frontend and API)

```bash
# Option 1: start.bat (Windows)
start.bat

# Option 2: command line
cd backend
python app.py

# Then open http://127.0.0.1:8080
```

The first start takes about 20 seconds (imports/compilation). In **Agent settings**:
1. Configure a model API key (DeepSeek / Qwen / any OpenAI-compatible endpoint)
2. Connect the Neo4j graph
3. Configure the MinerU token (literature parsing)
4. Tick the tools and skills you need

---

## Architecture

```
Frontend (static SPA)                 Backend (FastAPI, :8080)
┌─────────────────────┐     ┌─────────────────────────────┐
│ index.html / js/*   │     │ app.py  (all /api/* endpoints)│
│  messages.js render │     │  agent/agent.py              │
│  graph_rag.js agent │ ←→  │    stream_agent (custom)     │
│  agent_config.js    │ SSE │    stream_agent_lc (LangChain)│
│  dynamic tool/skill │     │  agent/tools.py tools_registry│
└─────────────────────┘     │  tools/  (16 backend tools)   │
                            └─────────────────────────────┘
```

**Data flow (one turn)**: `messages.js sendMessage → reactAgentLoop → POST /api/agent/stream (SSE) → stream_agent → LLM + tool loop → event stream → frontend rendering`

See [ARCHITECTURE.md](项目架构说明.md) for details, including the mermaid sources of every diagram.

---

## Tool list (backend `tools_registry`)

| Group | Tools |
|---|---|
| graph | graph_query (schema / cypher / search) |
| web | web_search · web_extract |
| skills | skill_list · skill_view · skill_write |
| file | read_file · write_file · list_files |
| code/terminal | run_code · terminal |
| memory | search_history · search_knowledge · conversation_notes |
| task | todo |
| documents | mineru_parse |

---

## License

MIT License (see LICENSE).

---

## Acknowledgements

- Architecture inspired by [Hermes Agent](https://github.com/NousResearch/hermes-agent) (schema-first tools, compact skill listing, project venv + uv)
- MinerU for PDF parsing
- LangChain for the optional agent engine
