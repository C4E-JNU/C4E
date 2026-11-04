"""
LangChain tool wrappers - expose backend tool functions as LangChain @tools.
Each tool has: name, description, args_schema, func.
"""
import json
import os
from langchain.tools import tool, BaseTool
from langchain_core.tools import StructuredTool
from typing import Optional, Type
from pydantic import BaseModel, Field

# -- Import backend tool functions --
from tools.neo4j_tools import (
    execute_graph_schema as _exec_graph_schema,
    execute_cypher as _exec_cypher,
    execute_graph_vector_search as _exec_graph_vector_search,
    Neo4jClient,
)
from tools.web_tools import web_search as _web_search, web_extract as _web_extract
from tools.skill_tools import skill_list as _skill_list, skill_view as _skill_view, skill_write as _skill_write
from tools.code_sandbox import run_python as _run_python
from tools.terminal_tool import terminal as _terminal
from tools.file_tools import read_file as _read_file, write_file as _write_file, list_files as _list_files
from tools.vector_search import search_history as _search_history
from tools.shared_hub import shared_upload as _shared_upload, shared_search as _shared_search, knowledge as _knowledge
from tools.mineru_tools import parse_pdfs, load_token
from tools.xwiki_tools import (
    wiki_read as _wiki_read,
    wiki_write as _wiki_write,
)


# -- Pydantic parameter models (JSON Schema given to the LLM) --

# 用户定名（2026-11-02）：三个库的大概叫法 —— 个人知识库 / 共享笔记库 / Wiki。
# 这段话会拼进每个库工具的描述开头：模型分不清库，根本原因是三个库在工具里
# 各自有一堆别名（notes / database / shared library / knowledge base / wiki），
# 且共享库历史上还有两个入口。统一成同一张路由表，别名只出现一次。
LIBRARY_ROUTING = (
    "THREE STORES — pick by what the user actually asked for; never substitute one for another.\n"
    "  1) My Knowledge Base (个人知识库) — the user's OWN private notes, saved per conversation; only this user can see them. Tool: conversation_notes.\n"
    "  2) Shared Notes (共享笔记库) — the whole group's library on the server: a folder tree of notes and uploaded documents, visible to everyone. Tool: shared_search (upload a file first with shared_upload).\n"
    "  3) Wiki (团队 Wiki / XWiki) — the team wiki site, organized into spaces and pages. Tools: wiki_read / wiki_write.\n"
    "Intent map: 'my notes / I noted earlier / my knowledge base' -> 1; "
    "'shared / the group / what others uploaded / the server library' -> 2; "
    "'wiki / space / page / wiki.<domain>' -> 3.\n"
    "If the user does not say which store and the answer would change, ask — do not guess.\n\n"
)

class GraphQueryParams(BaseModel):
    """Single entry point for graph queries (schema / cypher / search)."""
    action: str = Field(description="Action: schema=inspect graph structure (call first) | cypher=run Cypher | search=natural-language semantic search")
    cypher: str = Field(default="", description="For action=cypher: the Cypher statement. Example: MATCH (p:Pollutant {name:$name})-[:DEGRADES]->(c) RETURN c.name LIMIT 10")
    params: dict = Field(default={}, description="For action=cypher: optional query parameters. Example: {'name': 'Ciprofloxacin'}")
    query: str = Field(default="", description="For action=search: natural-language query. Example: 'nickel-doped magnetite degrading DDT'")
    scope: str = Field(default="catalyst", description="For action=search: paper / catalyst / pollutant / experiment / contradiction")
    top_k: int = Field(default=8, description="For action=search: number of results, 1-25")


class SearchParams(BaseModel):
    query: str = Field(description="Search keywords, e.g. 'vector database Qdrant usage'")
    limit: int = Field(default=5, description="Number of results, 1-10")

class FetchParams(BaseModel):
    url: str = Field(description="Full URL to fetch; must start with http:// or https://")

class SkillViewParams(BaseModel):
    name: str = Field(description="Skill name, e.g. 'literature-translate'")
    file_path: str = Field(default="SKILL.md", description="Path inside the skill; defaults to SKILL.md")

class CodeParams(BaseModel):
    code: str = Field(description="Python code to execute")
    timeout: int = Field(default=30, description="Timeout in seconds (1-60). Split longer jobs or run a script via terminal")

class ReadFileParams(BaseModel):
    path: str = Field(default="", description="File path; relative paths resolve against workspace/. In ask/full modes absolute paths inside the project or on disk may be read. May be omitted when file_id is given")
    file_id: str = Field(default="", description="file_id of an uploaded attachment: parsed documents return full text; unparsed attachments (note attachments) are extracted on the fly; images return base64 (dataURL). Takes precedence over path")
    as_base64: bool = Field(default=False, description="When reading by file_id, force base64 output (images are base64 by default)")
    offset: int = Field(default=1, description="Start line (1-based). Same meaning for path and file_id; use it to continue reading")
    limit: int = Field(default=500, description=(
        "Maximum number of lines to return. Output is also capped by characters (about 20,000 by default for the main agent;"
        "subagent profiles may set a higher cap), so a response can be truncated before the line limit."
        "has_more=true means the file was not fully read; call again with the returned next_offset."
        "Only has_more=false means the whole text was read."))
    max_chars: int = Field(default=0, description=(
        "Optional character cap for this single call (0 = current effective cap). Raise it to read a large block,"
        "e.g. 50000; it affects only this call and does not change the system default."))

class WriteFileParams(BaseModel):
    path: str = Field(description="File path; safe mode restricts to workspace/, ask mode also allows skills/, full access allows any absolute path")
    content: str = Field(description="File content (full overwrite)")

class TerminalParams(BaseModel):
    command: str = Field(description="Shell command to run (e.g. `uv pip install pandas`, `uv sync`, `python script.py`, `git status`). Any command is allowed, subject to the permission mode.")
    workdir: str = Field(default="", description="Working directory. Omit to use your own output area (for temporary scripts and outputs, keeping the project clean). Relative paths resolve against the project root; pass workdir='.' explicitly to run git/uv pip/project scripts in the project. Safe mode forces the project directory.")

class TodoParams(BaseModel):
    action: str = Field(description="Action: list / add / complete / remove")
    task: Optional[str] = Field(default=None, description="Task description (required for add)")
    task_id: Optional[int] = Field(default=None, description="Task id (required for complete/remove)")

class NotesParams(BaseModel):
    """My Knowledge Base (personal notes) — see LIBRARY_ROUTING."""
    action: str = Field(description="Action: list / read one / add / append to an existing entry / update / delete — all in the user's PERSONAL knowledge base")
    note_id: Optional[str] = Field(default=None, description="Entry id (required for read/append/update/delete; database entry ids look like note-1234567890)")
    branch_id: str = Field(
        default="",
        description="Only for scope='notes' with action='list':"
                    "empty (default) = list notes from ALL your conversations (with branch_id to locate them);"
                    "a branch id (e.g. 'branch-48') = only that conversation;"
                    "'__current__' = only the current conversation."
                    "read/append/update/delete work globally by note_id and ignore this parameter."
    )
    title: Optional[str] = Field(default=None, description="Note title (required for add)")
    content: Optional[str] = Field(
        default=None,
        description="Note content (required for add/append/update). Use full Markdown rich text:"
                    "(1) organize with #/##/### headings; (2) use lists and emphasis (-, 1., **bold**, *italic*);"
                    "(3) use Markdown tables for comparisons; (4) use ```mermaid blocks for flowcharts, sequence diagrams and Gantt charts;"
                    "(5) use $$...$$ for block and $...$ for inline LaTeX; (6) use fenced code blocks with a language tag."
                    "Make notes well structured and richly visual."
                    "LENGTH LIMIT: keep a single call under ~6,000 characters: add the first part, then append; writes over 20,000 characters are rejected."
    )

class ListFilesParams(BaseModel):
    subdir: Optional[str] = Field(default="", description="Directory path; omit to list the workspace root; outside safe mode absolute paths may list project/disk directories")

class SkillWriteParams(BaseModel):
    name: str = Field(description="Skill name (used as the skills/<name>/ directory name; must not contain / or \\\\\\\\）")
    content: str = Field(description="Full file content; SKILL.md needs YAML frontmatter (name/description/version)")
    file_path: str = Field(default="SKILL.md", description="Sub-file path (e.g. references/x.md, templates/x, scripts/x.py); defaults to SKILL.md")

class SearchMemoryParams(BaseModel):
    query: str = Field(description="Search keywords, e.g. 'experiment record instrument calibration'")
    scope: str = Field(default="auto", description="Scope: auto / history (past conversations) / knowledge (knowledge base)")

class SharedUploadParams(BaseModel):
    file_id: str = Field(default="", description="file_id of a locally uploaded file (returned on upload)")
    path: str = Field(default="", description="Or a workspace-relative or absolute path. One of file_id / path is required")


class SharedSearchParams(BaseModel):
    """Shared knowledge base tool - unified entry for search and group management (decision 2026-09-25:
    keep knowledge-base tools unified; do not add many new tools).

    Search: action="search" (default), semantic retrieval.
    Groups: action=list_groups / create_group / update_group / move_group / delete_group.
    Do one thing per call, selected by action.
    """
    action: str = Field(
        default="search",
        description=("Operation to perform:\n"
                     "  search        -- semantic search in the shared notes library (default)\n"
                     "  list_groups   -- view the group tree (with each group's intro, to locate material)\n"
                     "  create_group  -- create a group (needs name; optional parent_id, intro)\n"
                     "  update_group  -- rename or re-describe a group (needs group_id; optional name, intro)\n"
                     "  move_group    -- move a group (needs group_id; parent_id = new parent, empty = root)\n"
                     "  delete_group  -- delete a group (needs group_id; mode=move_up moves children up (default) / cascade deletes them)\n"
                     "  list_entries  -- list entries (supports owner/year/kind/sort/summary_only filters, see below)\n"
                     "  read_entry    -- read one entry's full body (needs entry_id)\n"
                     "  add_entry     -- create an entry in the shared library (needs title and/or content; optional kind, group_id)\n"
                     "  append_entry  -- append text to an existing entry (needs entry_id, content)\n"
                     "  update_entry  -- overwrite an existing entry (needs entry_id; optional title, content)\n"
                     "  delete_entry  -- delete an entry (needs entry_id; irreversible — confirm with the user first)\n"
                     "  move_entry    -- move an entry into a group (needs entry_id, to_group_id; empty = ungroup)"),
    )
    title: str = Field(default="", description="For add_entry/update_entry: entry title")
    content: str = Field(default="", description="For add_entry/append_entry/update_entry: Markdown body (keep each call under ~6,000 characters; build long text with add_entry + append_entry)")
    query: str = Field(default="", description="For search: keywords or a natural-language question, e.g. 'in-house instrument usage rules'")
    top_k: int = Field(default=5, description="For search: number of most relevant results, default 5")
    group_id: str = Field(default="", description="For list_entries: restrict to a group (empty = all; \"__root__\" = ungrouped); for update/move/delete_group: target group id (8-char short ids accepted)")
    name: str = Field(default="", description="For create/update_group: group name")
    parent_id: str = Field(default="", description="For create/move_group: parent group id; empty string = root")
    intro: str = Field(default="", description="For create/update_group: one-line group description (written by the user or by you) used for navigation and search")
    mode: str = Field(default="move_up", description="For delete_group: move_up = move child groups and entries to the parent (default); cascade = delete children too")
    entry_id: str = Field(default="", description="For move_entry: id of the entry to move")
    to_group_id: str = Field(default="", description="For move_entry: target group id; empty string = remove from the group")
    owner: str = Field(default="", description="For list_entries: only uploads by this user (e.g. \"xhq1\"; empty = all users)")
    year: str = Field(default="", description="For list_entries: only uploads in this year (e.g. \"2026\", by createdAt; empty = all years)")
    kind: str = Field(default="", description="For list_entries: \"note\" / \"document\"; empty = both")
    sort: str = Field(default="time", description="For list_entries: \"time\" = newest first (default) / \"time_asc\" / \"title\"")
    summary_only: bool = Field(default=False, description=("For list_entries: True returns ONLY the entry count per group.\n"
                                                           "**When you do not know which group holds the material, call this first with summary_only=True,"
                                                           "then pull details for the relevant group_id - this saves the most tokens. Do not fetch everything and filter locally.**"))


# -- XWiki parameter models (two unified entries: read / write) --

class WikiReadParams(BaseModel):
    """XWiki read entry - one tool dispatched by action."""
    action: str = Field(
        default="search",
        description=("Read-only operation:\n"
                     "  search          -- keyword search in titles/body (default; needs query)\n"
                     "  get             -- read the full body of a page (needs space, page)\n"
                     "  list_spaces     -- list all spaces (optional name filter)\n"
                     "  list_pages      -- list all pages in a space (needs space)\n"
                     "  list_attachments-- list attachments of a page (needs space, page)"))
    query: str = Field(default="", description="For action=search: keywords; Chinese is fine, e.g. 'molecular docking'")
    space: str = Field(default="", description="Space name, e.g. 'Main', '02. New member guide'")
    page: str = Field(default="", description="Page name, e.g. 'WebHome', 'AutodockVina steps'")
    limit: int = Field(default=10, description="Number of results: 10 by default for search; 10 for list_pages (internal cap 200)")
    scope: str = Field(default="all", description="For action=search: all = title+body (default) / title / content")
    name: str = Field(default="", description="For action=list_spaces: filter by name fragment, e.g. '06' or 'Software'")


class WikiWriteParams(BaseModel):
    """XWiki write entry - one tool dispatched by action."""
    action: str = Field(
        description=("Write operation:\n"
                     "  create            -- create a page (needs space, page, content)\n"
                     "  update            -- overwrite an existing page body (needs space, page, content)\n"
                     "  delete            -- delete a page (needs space, page; irreversible)\n"
                     "  upload_attachment -- upload a local file as an attachment (needs space, page, file_path)"))
    space: str = Field(default="", description="Space name, e.g. 'Main'")
    page: str = Field(default="", description="Page name (English, no spaces), e.g. 'FentonProtocol'")
    content: str = Field(default="", description="Page body in XWiki 2.1 syntax (not Markdown)")
    title: str = Field(default="", description="Display title (optional; defaults to page)")
    parent: str = Field(default="", description="For action=create: full name of the parent page (optional)")
    file_path: str = Field(default="", description="For action=upload_attachment: local file path")
    filename: str = Field(default="", description="For action=upload_attachment: file name shown in the wiki (optional)")


# -- Tool factory helpers --

def _make_async_tool(name: str, description: str, args_schema: Type[BaseModel], func):
    """Create an async LangChain StructuredTool"""
    return StructuredTool.from_function(
        name=name,
        description=description,
        args_schema=args_schema,
        coroutine=func,
    )


# -- Tool instances --

tools_registry = {}

def get_tools(enabled_names: list[str]) -> list[BaseTool]:
    """Return LangChain tool instances for the enabled list"""
    return [t for name, t in tools_registry.items() if name in enabled_names]


# -- Register tools --

# -- Graph query: one entry, three actions --
# Why merged (decision 2026-09-27): graph_schema / execute_cypher / graph_vector_search used three slots
# and the settings panel showed only the first two, so the third could never be enabled. Now the UI shows one
# "graph" tool while the LLM still gets the same three paths via action (implementations unchanged).
async def _exec_graph_query(action: str = "", cypher: str = "", params: dict = None,
                            query: str = "", scope: str = "catalyst", top_k: int = 8) -> str:
    """Single graph entry: dispatch by action to the existing implementations."""
    a = (action or "").strip().lower()
    if a == "schema":
        return await _exec_graph_schema()
    if a == "cypher":
        if not (cypher or "").strip():
            return json.dumps({"error": "action=cypher requires a cypher statement"}, ensure_ascii=False)
        return await _exec_cypher(cypher, params or {})
    if a == "search":
        if not (query or "").strip():
            return json.dumps({"error": "action=search requires a natural-language query"}, ensure_ascii=False)
        return await _exec_graph_vector_search(query, scope, top_k)
    return json.dumps({"error": "unknown action: %r (use schema / cypher / search)" % action}, ensure_ascii=False)


tools_registry["graph_query"] = _make_async_tool(
    name="graph_query",
    description=(
        "Unified entry to the catalytic degradation knowledge graph (Neo4j). **All graph data is read through this tool**; choose an action:\n"
        "- schema -- graph structure (node labels / relationship types / properties / vector indexes). Call it first on a new graph.\n"
        "- cypher -- run any Cypher (MATCH/RETURN/CALL). Writes (CREATE/DELETE/SET) need admin rights.\n"
        "- search -- natural-language/synonym semantic search (local bge embeddings + graph vector index). Use it to get candidates when the exact name is unknown.\n"
        "\n"
        "WHEN: the user asks about catalysts, pollutants, degradation efficiency, experimental conditions or literature contradictions.\n"
        "HOW (step by step; avoid one huge query): 1) first call action=schema; 2) with an exact name use action=cypher;\n"
        "when unsure of the name, use action=search for candidates, then action=cypher by paper_id.\n"
        "cypher in three steps: fuzzy MATCH (n) WHERE n.name CONTAINS $q, then exact MATCH (p:Pollutant {name:$name}),"
        "then expand MATCH (p)-[r]-(c) RETURN p,r,c.\n"
        "Empty-result fallback: fuzzy match -> search the node aliases property (alternate names and abbreviations) -> switch label -> only then use your own knowledge (mark it 💡).\n"
        "search results carry a similarity score; cite only score>0.6.\n"
        "\n"
        "Answer format: 📊 from the graph / 💡 from your own knowledge / 📊+💡 mixed. Prefer tables (catalyst | efficiency | time | pH) and keep technical terms in English.\n"
        "CONNECTION NOTE: the graph runs on remote Neo4j Aura, which drops idle connections,"
        "so the **first or second query may fail** (defunct connection / not connected)."
        "It reconnects and retries once; if you see a single failure, call it again to recover."
        "This does not mean the graph is broken; never claim it has no data."
        "Report a problem only after repeated failures with an explicit error; do not retry endlessly."
    ),
    args_schema=GraphQueryParams,
    func=_exec_graph_query,
)


tools_registry["web_search"] = _make_async_tool(
    name="web_search",
    description=(
        "Search the web for real-time information and return several results (title, URL, summary).\n\n"
        "WHEN to use: the user asks about recent research, news, papers or field progress, or you are unsure of a fact or your knowledge may be outdated (new results, performance data, market information): search first. Do not answer from possibly stale knowledge.\n\n"
        "WHEN NOT: when the graph, uploaded documents or user-provided material answer reliably, use those instead.\n\n"
        "HOW: build an objective, focused query (keyword combinations work, e.g. 'Qwen3 release features benchmark 2026'). If the first result is poor, change keywords or add qualifiers once; do not repeat the same query endlessly.\n\n"
        "After searching: cross-check several results, prefer authoritative sources, note uncertainty, and use web_extract for key results that need full text."
    ),
    args_schema=SearchParams,
    func=_web_search,
)

tools_registry["web_extract"] = _make_async_tool(
    name="web_extract",
    description=(
        "Fetch a URL, convert the page to plain text and return the key points.\n\n"
        "WHEN to use: reading a full paper, blog post, news article or official documentation - web_search gives only titles/snippets, so details need this tool.\n\n"
        "HOW: pass a concrete URL. PDFs and anti-bot sites may be restricted; if the result is incomplete, tell the user or use another source.\n\n"
        "Note: extract only the key information for the answer; do not paste the whole page."
    ),
    args_schema=FetchParams,
    func=_web_extract,
)

tools_registry["skill_list"] = StructuredTool.from_function(
    name="skill_list",
    description="List all available skills and their descriptions. Skills carry domain knowledge, operating instructions and constraints. When unsure how to proceed or needing domain expertise, check this first.",
    coroutine=_skill_list,
)


async def _list_outputs_tool():
    """List your own output area (owner is derived from the login identity; **the model cannot pass an identity**)."""
    from tools.file_tools import list_outputs
    return list_outputs()


tools_registry["list_outputs"] = StructuredTool.from_function(
    name="list_outputs",
    description=(
        "List the files you generated (csv/png/pptx/xlsx/md etc. in the output area) with their file_id.\n\n"
        "WHEN to use: after write_file('outputs/xxx'), run_code or a skill script produced a file -"
        "you must obtain the file_id before handing the output to the user.\n\n"
        "HOW: once you have the file_id, write {{file:<file_id>:<name>}} in the answer; the frontend renders an attachment card,"
        "and the user can open it on the backend or download/preview it in the browser (same as note attachments).\n"
        "Note: write output files under outputs/ (relative path), otherwise they are not indexed."
    ),
    coroutine=_list_outputs_tool,
)

tools_registry["run_code"] = _make_async_tool(
    name="run_code",
    description=(
        "Run Python code in a separate subprocess and return stdout/stderr/exit_code.\n\n"
        "WHEN to use: the user asks for computation, data analysis, statistics, visualization or bulk processing, or a piece of logic needs verification.\n\n"
        "HOW: write complete, runnable code (with imports and print output). The working directory is your own output area;"
        "files written by the script persist. The default timeout is 30 s (1-60).\n\n"
        "LIMITS (state them accurately, do not guess): AST blocks subprocess / os.system / os.remove and similar"
        "process/filesystem calls, plus dynamic eval / exec / compile / __import__; normal calls such as re.compile or"
        "model.compile are unaffected. Network requests are NOT blocked and file paths are NOT restricted,"
        "so this is a convenience environment, not a security sandbox. Third-party imports depend on the backend"
        "interpreter (numpy/pandas/scipy usually work; matplotlib/sklearn may not). If a package is missing, use terminal"
        "with `uv pip install` into the project venv, or run the script in terminal."
    ),
    args_schema=CodeParams,
    func=_run_python,
)

tools_registry["skill_view"] = _make_async_tool(
    name="skill_view",
    description=(
        "Read the full content of a skill (SKILL.md or a sub-file).\n\n"
        "WHEN to use: skills are listed (<available_skills> or skill_list) and you need to actually follow one (it contains steps, commands and quality criteria):\n"
        "always call skill_view(name) for the full text - a one-line summary cannot guide execution. Read it whenever the skill is relevant.\n\n"
        "HOW: pass the skill name, then follow its steps and conventions exactly."
    ),
    args_schema=SkillViewParams,
    func=_skill_view,
)

tools_registry["terminal"] = _make_async_tool(
    name="terminal",
    description=(
        "Run shell commands in the project (git, build, test, uv/pip install, scripts) and return stdout/stderr/exit_code.\n\n"
        "WHEN to use: running real commands, installing/upgrading packages (uv pip install / uv sync), running tests or scripts, checking git status. run_code can import most installed packages (numpy/pandas/scipy verified) but its timeout is 60 s and it is not process-isolated; use terminal for long jobs, installs or script files.\n\n"
        "HOW: write the full shell command. The working directory **defaults to the current work area's project directory**; relative workdir values resolve against it. For scripts that emit many intermediate files, cd to the output directory first (see the Work Area section of the system prompt).\n\n"
        "Safety: constrained by the permission mode - safe allows only the project and blocks risky commands; ask returns need_confirm for external actions; full allows anything. Install packages with `uv pip install <package>` into the project venv."
    ),
    args_schema=TerminalParams,
    func=_terminal,
)

tools_registry["read_file"] = _make_async_tool(
    name="read_file",
    description="Read file content, optionally starting at a line and limiting the number of lines. Relative paths resolve against the current work area's project directory; paths starting with output/ read the output directory; ask/full modes allow absolute paths.",
    args_schema=ReadFileParams,
    func=_read_file,
)

tools_registry["write_file"] = _make_async_tool(
    name="write_file",
    description="Write content to a file (overwrites). Relative paths write into the current work area's project directory; paths starting with output/ write the output directory (for intermediate files such as output/tmp.log). Safe mode restricts to the work area; ask mode also allows skills/; full mode allows any path.",
    args_schema=WriteFileParams,
    func=_write_file,
)

tools_registry["skill_write"] = _make_async_tool(
    name="skill_write",
    description=(
        "Create or update a skill: write files under skills/<name>/ (SKILL.md by default; references/templates/scripts sub-files allowed) and update skills/index.json automatically.\n\n"
        "WHEN to use: the user asks to save a procedure as a reusable skill or to fix an existing one; proactively offer to create a skill when a reusable workflow is worth keeping.\n\n"
        "HOW - SKILL.md must contain YAML frontmatter:\n"
        "  name: skill name (directory name; no / or whitespace)\n"
        "  description: one self-contained sentence stating when to use it ('Use when X. Briefly what it does.'); state the trigger within the first 57 characters.\n"
        "  version: version number\n"
        "Body in Markdown: sections (## When to use / ## Steps / ## Pitfalls / ## Verification) with reproducible commands, parameters and pitfalls so a future run succeeds first time.\n\n"
        "index.json is synced automatically afterwards."
    ),
    args_schema=SkillWriteParams,
    func=_skill_write,
)

# -- todo implementation --

_WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspace"))

# Hard cap on a single note write: reject and ask to split/append (prevents output-limit truncation)
MAX_NOTE_CHARS = 20000


def _note_too_long(content: str) -> str:
    if content and len(content) > MAX_NOTE_CHARS:
        return (f"Refused: {len(content)} characters exceeds the limit of {MAX_NOTE_CHARS} (forced writes are truncated by the generation limit)."
                f"Split it into several notes, or write the first part and append the rest with the append action.")
    return ""

def _get_todo_path():
    """Todos are stored per **identity + conversation branch**.

    The old implementation used a global workspace/_todos.json, so everyone and every conversation
    shared the same list (exposing private items in multi-user setups) and could not restore context per conversation.
    """
    from agent.agent import _current_user, _current_branch_id
    owner = _current_user or "guest"
    d = os.path.join(_WORKSPACE, "users", owner, "todos")
    os.makedirs(d, exist_ok=True)
    branch = str(_current_branch_id or "default")
    for ch in '/\\:*?"<>|':
        branch = branch.replace(ch, "_")
    return os.path.join(d, branch + ".json")


def todo_context_block() -> str:
    """Feed the current identity+branch todos back into the system prompt.

    The old version only had add/complete/list, and todos **never appeared in the context** -
    the model could not see its own list, defeating the purpose.
    Returns an empty string when nothing is pending (no context cost).
    """
    try:
        todos = _read_todos()
    except Exception:
        return ""
    pending = [x for x in todos if isinstance(x, dict) and not x.get("done")]
    if not pending:
        return ""
    lines = ["[Current task list] (recorded with the todo tool; %d pending; mark each one complete when done)" % len(pending)]
    for x in pending[:20]:
        lines.append("- #%s %s" % (x.get("id"), str(x.get("content", ""))[:160]))
    if len(pending) > 20:
        lines.append("...and %d more" % (len(pending) - 20))
    return "\n".join(lines)

def _read_todos():
    path = _get_todo_path()
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_todos(todos):
    path = _get_todo_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)

async def _exec_todo(action: str, task: str = None, task_id: int = None) -> str:
    todos = _read_todos()
    if action == "add":
        if not task:
            return json.dumps({"error": "Task description cannot be empty"})
        new_id = max([t.get("id", 0) for t in todos], default=0) + 1
        todos.append({"id": new_id, "content": task, "done": False, "created": str(__import__("datetime").datetime.now())})
        _write_todos(todos)
        return json.dumps({"status": "ok", "message": f"Task added: {task}", "total": len(todos)}, ensure_ascii=False)
    elif action == "complete":
        if task_id is None:
            return json.dumps({"error": "Task id is required"})
        for t in todos:
            if t["id"] == task_id:
                t["done"] = True
                _write_todos(todos)
                return json.dumps({"status": "ok", "message": f"✅ Completed: {t['content']}"}, ensure_ascii=False)
        return json.dumps({"error": f"Task #{task_id} does not exist"})
    elif action == "remove":
        if task_id is None:
            return json.dumps({"error": "Task id is required"})
        new_todos = [t for t in todos if t["id"] != task_id]
        if len(new_todos) == len(todos):
            return json.dumps({"error": f"Task #{task_id} does not exist"})
        _write_todos(new_todos)
        return json.dumps({"status": "ok", "message": f"Deleted task #{task_id}"})
    else:  # list
        done = sum(1 for t in todos if t.get("done"))
        return json.dumps({
            "status": "ok", "total": len(todos), "completed": done,
            "pending": len(todos) - done,
            "tasks": [{"id": t["id"], "content": t["content"], "done": t.get("done", False)} for t in todos]
        }, ensure_ascii=False, indent=2)

# -- conversation_notes implementation --

def _get_notes_path():
    os.makedirs(_WORKSPACE, exist_ok=True)
    return os.path.join(_WORKSPACE, "_notes.json")

def _read_notes():
    path = _get_notes_path()
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_notes(notes):
    path = _get_notes_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)

async def _exec_notes(action: str, note_id: str = None, title: str = None, content: str = None,
                      scope: str = "notes", branch_id: str = "") -> str:
    """Unified entry for notes and database entries.

    scope='notes'    -> local conversation notes in users/<identity>/notes.json (list covers all conversations by default; branch_id narrows it)
    scope='database' -> cloud shared database entries (branch=__db__; local instances forward to the cloud)
    """
    from agent.agent import _current_branch_id, _current_user
    from tools import file_tools as _ft
    owner = _current_user or "guest"
    now = str(__import__("datetime").datetime.now())

    # -- Database entries: go through the cloud (cloud instances use the local store; local instances forward to the hub) --
    if (scope or "notes").strip().lower() in ("database", "db", "__db__", "cloud", "shared"):
        from tools.shared_hub import db_notes
        act = action
        r = await db_notes(act, note_id or "", title or "", content or "", owner)
        if "error" in r:
            return json.dumps(r, ensure_ascii=False)
        if act == "list":
            entries = r.get("entries", [])
            # Database list is already a catalog (id/title/owner/content_len/preview, no body): pass it through
            slim = [{"id": e.get("id"), "title": e.get("title"), "owner": e.get("owner", ""),
                     "content_len": e.get("content_len", 0), "preview": e.get("preview", "")}
                    for e in entries]
            return json.dumps({"status": "ok", "scope": "database", "total": len(slim),
                               "note": "The above is only a catalog (no body, to protect the context). Read the body with action=read by id;"
                                       "create/update/delete with add/update/delete",
                               "entries": slim}, ensure_ascii=False, indent=2)
        if act == "read":
            return json.dumps({"status": "ok", "scope": "database", "entry": r.get("entry")},
                              ensure_ascii=False, indent=2)
        msg = {"add": "Created a database entry", "append": "Appended to the database entry",
               "update": "Updated the database entry", "delete": "Deleted the database entry"}.get(act, "Done")
        return json.dumps({"status": "ok", "scope": "database", "id": r.get("id", ""),
                           "message": f"{msg}（id={r.get('id','')}）"}, ensure_ascii=False)

    # -- Local conversation notes --
    if action == "add":
        if not title and not content:
            return json.dumps({"error": "Title and content cannot both be empty"})
        too_long = _note_too_long(content)
        if too_long:
            return json.dumps({"error": too_long}, ensure_ascii=False)
        r = json.loads(await _ft.add_note(title, content, _current_branch_id, owner))
        return json.dumps({"status": "ok", "message": f"Note added: {title} ({len(content or '')} characters)",
                           "note_id": r.get("note_id")}, ensure_ascii=False)

    if action == "append":
        if not note_id:
            return json.dumps({"error": "append requires a note_id"})
        if not content:
            return json.dumps({"error": "append content cannot be empty"})
        too_long = _note_too_long(content)
        if too_long:
            return json.dumps({"error": too_long}, ensure_ascii=False)
        notes = _ft._read_notes(owner)
        for n in notes:
            if n.get("id") == note_id:
                n["content"] = (n.get("content", "") + "\n\n" + content) if n.get("content") else content
                n["updatedAt"] = now
                _ft._write_notes(owner, notes)
                return json.dumps({"status": "ok", "message": f"Appended to note: {n['title']} (now {len(n['content'])} characters)"}, ensure_ascii=False)
        return json.dumps({"error": "Note not found"})

    if action == "update":
        too_long = _note_too_long(content) if content is not None else None
        if too_long:
            return json.dumps({"error": too_long}, ensure_ascii=False)
        r = json.loads(await _ft.update_note(note_id, title, content, owner))
        if "error" in r:
            return json.dumps(r, ensure_ascii=False)
        notes = _ft._read_notes(owner)
        t = next((n for n in notes if n.get("id") == note_id), {})
        return json.dumps({"status": "ok", "message": f"Note updated: {t.get('title','')} ({len(t.get('content',''))} characters)"}, ensure_ascii=False)

    if action == "delete":
        r = json.loads(await _ft.delete_note(note_id, owner))
        if "error" in r:
            return json.dumps(r, ensure_ascii=False)
        return json.dumps({"status": "ok", "message": "Note deleted"}, ensure_ascii=False)

    # read: read one local note by id
    if action == "read":
        n, _ = _ft._find_note(owner, note_id)
        if n is None:
            return json.dumps({"error": "Note not found"}, ensure_ascii=False)
        return json.dumps({"status": "ok", "scope": "notes",
                           "entry": {"id": n["id"], "title": n.get("title", ""),
                                     "content": (n.get("content") or "")[:20000],
                                     "content_len": len(n.get("content") or ""),
                                     "truncated": len(n.get("content") or "") > 20000,
                                     "branch_id": n.get("branch_id", ""),
                                     "owner": n.get("owner", owner)}}, ensure_ascii=False, indent=2)

    # list: catalog of local notes.
    # Fix (found in real use): list used to hard-filter to the current branch,
    #   so the agent could not see notes created in **other conversations** (document entries live in their own branches),
    #   producing "I have many notes but the AI says one". It now lists all conversations by default with branch_id / branch_title,
    #   and branch_id still narrows it (specific id or '__current__'). Catalog only; bodies may be huge.
    bid = (branch_id or "").strip()
    if bid.lower() in ("__current__", "current", "this chat", "current chat"):
        bid = _current_branch_id or ""
    all_notes = _ft._read_notes(owner)
    notes = [n for n in all_notes if (n.get("branch_id") or "") == bid] if bid else list(all_notes)

    # Conversation title map (chats.json branches[].title) so the agent can locate notes by conversation
    titles, groups = {}, {}
    try:
        from tools import user_chats as _uc
        for b_id, b in ((_uc._read_raw(owner) or {}).get("branches") or {}).items():
            if isinstance(b, dict):
                titles[b_id] = (b.get("title") or b.get("name") or "").strip()[:60]
    except Exception:
        pass

    def _slim_local(n):
        c = n.get("content") or ""
        b_id = n.get("branch_id") or ""
        return {"id": n["id"], "title": n.get("title", ""), "branch_id": b_id,
                "branch_title": titles.get(b_id, ""),
                "content_len": len(c), "updatedAt": n.get("updatedAt", ""),
                "preview": c[:120].replace("\n", " ") + ("…" if len(c) > 120 else "")}

    for n in notes:
        b_id = n.get("branch_id") or "(unassigned)"
        g = groups.setdefault(b_id, {"branch_id": b_id, "branch_title": titles.get(b_id, ""), "count": 0})
        g["count"] += 1

    return json.dumps({
        "status": "ok", "scope": "notes", "owner": owner,
        "filter": bid or "(all conversations)",
        "current_branch_id": _current_branch_id,
        "total": len(notes),
        "by_branch": list(groups.values()),
        "note": "Catalog of your notes (all conversations by default; no bodies). For a body, action=read by note_id;"
                "To list one conversation, pass branch_id='branch-12' or '__current__'.",
        "notes": [_slim_local(n) for n in notes]
    }, ensure_ascii=False, indent=2)

# -- Subagents (delegate_task) --

class DelegateTaskParams(BaseModel):
    tasks: list[dict] = Field(
        description="List of subtasks to dispatch (1-4). **One profile may run several instances at once**"
                    "(e.g. three fenton-screener instances each screening one paper), each with its own context, skill directory,"
                    "tool set and parameters/model, running in parallel. Each element looks like "
                    "{\"goal\": \"Goal of this subtask (required, concrete and actionable)\", "
                    "\"context\": \"Necessary background (optional; include key facts from the main conversation, which the subagent cannot see)\", "
                    "\"profile\": \"Optional: subagent profile name (backend/agents/<name>/agent.json),"
                    "with its own skill/tool directories, context parameters and model. Omit for the generic subagent. Available profiles: /api/agents/profiles\", "
                    "\"tools\": [\"Optional: restrict the tools this subagent may use\"], "
                    "\"role\": \"Optional (legacy): contradiction / extraction / verify\"}"
    )


def delegate_prepare(tasks) -> tuple:
    """Validate and trim the subtask list. Returns (valid_tasks, error_json_str or None).

    Shared by both paths: (1) _exec_delegate (non-streaming / LangChain tool path);
    (2) the custom streaming engine in agent.stream_agent.
    """
    from agent.agent import get_subagent_options, get_current_model_config
    from agent.subagent import SUBAGENT_MAX_TASKS

    opts = get_subagent_options()
    if not opts.get("enabled", True):
        return [], json.dumps({"error": "Subagents are disabled in settings"}, ensure_ascii=False)
    if not get_current_model_config().get("model"):
        return [], json.dumps({"error": "Subagents cannot start: no model configured (choose a model first)"}, ensure_ascii=False)
    if not isinstance(tasks, list) or not tasks:
        return [], json.dumps({"error": "tasks cannot be empty"}, ensure_ascii=False)
    limit = int(opts.get("max_tasks", SUBAGENT_MAX_TASKS) or SUBAGENT_MAX_TASKS)
    valid = [t for t in tasks if isinstance(t, dict) and (t.get("goal") or "").strip()][:limit]
    if not valid:
        return [], json.dumps({"error": "Every subtask needs a non-empty goal"}, ensure_ascii=False)
    # Profile check: profile/role must exist (fail early so the main agent can correct or re-dispatch)
    try:
        from agent import profiles as _pf
        _known = set(_pf.load_profiles().keys())
        for t in valid:
            pn = str(t.get("profile") or t.get("role") or "").strip()
            if pn and pn not in _known:
                return [], json.dumps({"error": f"Unknown subagent profile: {pn}",
                                       "available": sorted(_known)}, ensure_ascii=False)
    except Exception:
        pass
    return valid, None


def slim_subagent_results(results: list) -> list:
    """Trim subagent results to summaries before feeding them back (prevents context blow-up).

    Root cause: steps returned by run_subagent contain the **full text the subagent read**
    (tool_result up to 2,000 characters each, full thinking), which was fed back into the main context.
    Two papers in one dispatch consumed about 20,000 characters; a 10-step pipeline overflowed.

    Fix: **separate the UI stream from the main agent** -
      - the event slot set_delegate_results() still holds the **full originals** for the subagent_end stream;
      - here only the copy fed to the model is trimmed: drop step bodies, keep verifiable metadata
        (ok / goal / summary / error / rounds / seconds / tool names / step count).
    To restore the old behavior, set subagent.feed_steps=True.
    """
    try:
        from agent.agent import get_subagent_options
        if get_subagent_options().get("feed_steps"):
            return results or []
    except Exception:
        pass
    out = []
    for r in (results or []):
        if not isinstance(r, dict):
            continue
        steps = r.get("steps") or []
        tools_used: list[str] = []
        for s in steps:
            if isinstance(s, dict) and s.get("type") == "tool_call":
                n = (s.get("name") or "").strip()
                if n and n not in tools_used:
                    tools_used.append(n)
        item = {
            "index": r.get("index", 0),
            "ok": bool(r.get("ok")),
            "goal": r.get("goal", ""),
            "summary": (r.get("summary") or "")[:10000],
            "rounds": r.get("rounds", 0),
            "seconds": r.get("seconds", 0),
            # Full steps already went to the UI via subagent_end; report only counts, never bodies
            "steps_omitted": len(steps),
            "tools_used": tools_used,
        }
        if r.get("error"):
            item["error"] = r["error"]
        # Structured status so the main agent can decide whether to re-dispatch or switch profiles
        for _k in ("profile", "status", "missing", "confidence"):
            if r.get(_k) not in (None, ""):
                item[_k] = r[_k]
        out.append(item)
    return out


async def _exec_delegate(tasks: list[dict], max_rounds: int = None, timeout: int = None) -> str:
    """Dispatch subagents (concurrency and timeouts come from settings; failures are not retried).

    Non-streaming path (LangChain engine / /api/agent/run): run first, return once.
    The custom streaming engine calls run_subagents_parallel(emit=...) directly in agent.stream_agent.
    """
    from agent.agent import (get_subagent_options,
                             set_delegate_tasks, set_delegate_results)
    from agent.subagent import (run_subagents_parallel, SUBAGENT_MAX_ROUNDS_DEFAULT,
                                SUBAGENT_TIMEOUT_DEFAULT)

    valid, err = delegate_prepare(tasks)
    if err is not None:
        return err
    opts = get_subagent_options()
    mr = max_rounds if max_rounds else opts.get("max_rounds", SUBAGENT_MAX_ROUNDS_DEFAULT)
    to = timeout if timeout else opts.get("timeout", SUBAGENT_TIMEOUT_DEFAULT)
    sub_timeouts = {"nudge": opts.get("nudge"), "warn": opts.get("warn"), "abort": opts.get("abort")}
    # Put the task list in the event slot so the main loop can stream subagent_start (engine-agnostic)
    set_delegate_tasks(valid)
    results = await run_subagents_parallel(valid, max_rounds=mr, timeout=to,
                                           max_parallel=int(opts.get("max_parallel", 2) or 2),
                                           timeouts=sub_timeouts)
    # Put results in the slot so the main loop streams subagent_end (the UI wants full steps, so originals go here)
    set_delegate_results(results)
    # The copy fed back to the main agent must be trimmed (see slim_subagent_results)
    return json.dumps({"status": "ok", "count": len(results),
                       "results": slim_subagent_results(results)},
                      ensure_ascii=False)


tools_registry["delegate_task"] = _make_async_tool(
    name="delegate_task",
    description=(
        "Dispatch subagents (independent-context helpers) to work on **mutually independent subtasks** in parallel; you synthesize their conclusions."
        "WHEN to use: the task splits into 2-4 independent parts, each needing multi-step retrieval/reading (e.g."
        "'check the literature progress of topics A, B and C' or 'one paper for structure, one for data');"
        "or when parallel collection saves time."
        "WHEN NOT to use: (1) one question can be answered directly (calling tools yourself is faster); (2) subtasks depend on each other (the next needs the previous result) -"
        "do them yourself in order; (3) you only need to write notes or edit the database (use conversation_notes); (4) never dispatch just to look thorough."
        "PROFILES - each subagent has its own skills, tools, parameters and model."
        "Each subtask may name a profile (backend/agents/<name>/agent.json) with its own skill directory,"
        "tool directory, context parameters (e.g. characters per read) and model, **not shared with the main agent**."
        "**One profile may run several instances** (e.g. three fenton-screener instances on three papers), each with its own context."
        "Omit profile for the generic subagent (read-only tools + main model); see /api/agents/profiles."
        "HARD LIMITS: subagents cannot dispatch subagents (depth=1). Task count, concurrency, rounds and timeouts"
        "follow the user's Subagents settings (default 4 tasks / concurrency 2 / 4 rounds / 300 s timeout),"
        "but rounds/limits set explicitly in a profile take precedence."
        "Subagents may use only read-only/retrieval tools (including private tools under agents/<name>/tools/);"
        "they cannot write files or run terminal commands."
        "HOW: each goal must state what to do and what to produce; context must carry the background it needs"
        "(subagents cannot see our chat history; without context they guess)."
        "EVALUATE AND RE-DISPATCH: each result carries status(ok/partial/failed), missing, and confidence -"
        "check whether it meets the requirement; **re-dispatch if not**, and always put the previous failure in context"
        "(e.g. 'last extraction of XXX failed: <copy missing verbatim>. Tried <path>; use another path to finish YYY'),"
        "otherwise the new instance is a blank slate and repeats the same actions. Switching profiles is allowed"
        "(e.g. an incomplete fenton-verifier result -> re-dispatch fenton-arbiter). Re-dispatch the same subtask **at most twice**;"
        "if it still fails, report the gap honestly; do not retry endlessly."
        "After receiving results you **must** synthesize them into the final answer;"
        "do not just paste the subagents' text together."
    ),
    args_schema=DelegateTaskParams,
    func=_exec_delegate,
)


# -- Register new tools --

tools_registry["list_files"] = _make_async_tool(
    name="list_files",
    description="List files in a directory. Relative paths resolve against workspace/; outside safe mode absolute paths may list project or disk directories.",
    args_schema=ListFilesParams,
    func=_list_files,
)

tools_registry["todo"] = _make_async_tool(
    name="todo",
    description="Manage the task list: list, add, complete and remove tasks. Use it to track multi-step work or remember user to-dos.",
    args_schema=TodoParams,
    func=_exec_todo,
)

tools_registry["conversation_notes"] = _make_async_tool(
    name="conversation_notes",
    description=LIBRARY_ROUTING +
                "THIS TOOL = store 1 only: the user's PERSONAL knowledge base (private, per conversation, invisible to the group)."
                "It is NOT the shared library: group-shared notes and documents are read/written with shared_search, and the Wiki with wiki_read / wiki_write."
                "Actions: list / read by id / add / append / update / delete."
                "SCOPE RULE (2026): action='list' **covers all conversations by default** and returns the catalog from **every** conversation,"
                "each with branch_id / branch_title and a by_branch summary -"
                "**never claim the user has no notes or only one just because the current conversation shows none** (when the user says 'I have many notes', list all conversations first)."
                "To list one conversation, pass branch_id='branch-12' or '__current__'."
                "read/append/update/delete work **globally by note_id** and never take a branch."
                "READING RULE: list returns only the **catalog** (id/title/branch/body length/120-character preview), **no body** -"
                "large entries (some hundreds of thousands of characters) would overflow the context. Read the body with read by id;"
                "read returns at most 20,000 characters per call (longer text sets truncated=true and reports the total length);"
                "do not re-read the same huge entry; ask the user to narrow it down or read it in sections."
                "LENGTH RULE: keep content under ~6,000 characters per write; anything beyond the single-response limit is truncated."
                "Build long content in steps: add the first part, then append sections (about 2,000-5,000 characters each);"
                "or split into several notes titled Part 1/2/3. Never try to write the whole text at once."
                "FORMATTING: use Markdown rich text for clear structure and rich visuals:"
                "(1) organize long text with #/##/### headings;"
                "(2) use ordered/unordered lists, **bold**, *italic* and > quotes for readability;"
                "(3) present comparisons, parameters and summaries in Markdown tables;"
                "(4) show flows, architecture, sequences or Gantt charts with ```mermaid blocks (flowchart / sequenceDiagram / gantt / pie);"
                "(5) use $$...$$ block and $...$ inline LaTeX for math;"
                "(6) use fenced code blocks with a language tag."
                "Make every entry a well-typeset illustrated report, not plain text.",
    args_schema=NotesParams,
    func=_exec_notes,
)

# -- Search tools --

async def _search_history_wrapped(query: str, top_k: int = 3, scope: str = None) -> str:
    """Search your own chat history under the current login identity (guests use the guest space)."""
    from agent.agent import _current_user
    return await _search_history(query, top_k, scope, user=_current_user)


tools_registry["search_history"] = _make_async_tool(
    name="search_history",
    description="Search the current user's past conversations (isolated by identity). Use it to recall earlier discussions, data or conclusions. Returns the most relevant snippets with similarity scores.",
    args_schema=SearchMemoryParams,
    func=_search_history_wrapped,
)

# search_knowledge was removed: local instances have no database; use shared_search for the shared library.

tools_registry["shared_upload"] = _make_async_tool(
    name="shared_upload",
    description="Upload a local file to the group shared library (a C4EAI instance on the LAN server). The server parses it and indexes the text,"
                "so the whole group can find it with shared_search. Use it when the user says 'share', 'upload to the server' or 'for the group'."
                "Pass either file_id (a locally uploaded file) or path (a workspace path).",
    args_schema=SharedUploadParams,
    func=_shared_upload,
)

tools_registry["shared_search"] = _make_async_tool(
    name="shared_search",
    description=(
        LIBRARY_ROUTING +
        "THIS TOOL = store 2 only: the Shared Notes library (共享笔记库) — the group's shared folder tree of notes and documents on the server."
        "It is NOT the user's personal notes (that is conversation_notes) and NOT the Wiki (that is wiki_read / wiki_write).\n"
        "Unified entry to the group shared library: semantic search + entry read/write + folder-tree management.\n"
        "\n"
        "HOW THE SHARED LIBRARY IS ORGANIZED\n"
        "  Top level by entry type: notes (human-written summaries; attachments not parsed) and documents (parsed on upload).\n"
        "  Below is a group tree; each group has an intro describing what it holds - read intros first.\n"
        "  Entries belong to a group and record owner and upload time.\n"
        "\n"
        "CORRECT ORDER FOR FINDING MATERIAL (saves many tokens)\n"
        "  1) action=list_groups      view the tree and intros\n"
        "  2) action=list_entries + summary_only=True\n"
        "     see how many entries each group has. **Do this first when unsure; do not pull all details.**\n"
        "  3) action=list_entries for a specific group_id (title/id/owner/time only, no body)\n"
        "  4) use action=search or read a specific entry when you need the body\n"
        "\n"
        "PUSH FILTERS DOWN - filter with parameters; do not fetch everything and filter locally\n"
        "  Example: the user asks for XX group content uploaded by xhq1 in 2026\n"
        "     -> action=list_entries, owner=\"xhq1\", year=\"2026\", group_id=\"<that group>\"\n"
        "  Example: unknown group -> summary_only=True + owner=\"xhq1\" + year=\"2026\"\n"
        "     count per group, then pull details from the group that has material.\n"
        "\n"
        "SEMANTIC SEARCH: action=search (default) returns relevant snippets and source documents.\n"
        "  Use it when you do not know where something is and want to search by meaning.\n"
        "\n"
        "MANAGEMENT: for 'create a group / move this group under xx / update the intro / move an entry',\n"
        "  call create_group / move_group / update_group / move_entry / delete_group directly.\n"
        "  Note: groups are only categories; deleting one does not delete entries (children move up by default).\n"
        "WRITING ENTRIES: add_entry (title + content) / append_entry / update_entry / delete_entry / read_entry,\n"
        "  identified by entry_id. Long text: add_entry first, then append_entry in ~2,000-5,000 character sections.\n"
        "  For a file, upload it with shared_upload first, then it becomes an entry."
    ),
    args_schema=SharedSearchParams,
    func=_knowledge,
)

# -- MinerU PDF parsing tool --

class MinerUParams(BaseModel):
    path: str = Field(description="PDF file path or a folder containing PDFs (all .pdf files are scanned recursively). Relative paths resolve against workspace/; absolute paths are allowed.")
    out_dir: str = Field(default="mineru_output", description="Markdown output directory (relative to workspace/). A single file outputs a same-named .md there.")
    single_pdf: bool = Field(default=False, description="True treats path as a single PDF; False auto-detects: a file is one document, a folder is processed in batches. Usually no need to set.")

async def _mineru_parse(path: str, out_dir: str = "mineru_output", single_pdf: bool = False):
    """MinerU backend: parse one PDF or every PDF in a folder (MinerU v4 batch interface)."""
    import asyncio
    from pathlib import Path
    ws = Path(__file__).parent.parent.parent / "workspace"
    p = Path(path)
    if not p.is_absolute():
        p = ws / path
    _out = Path(out_dir)
    out_root = _out if _out.is_absolute() else (ws / out_dir)

    token = load_token()
    if not token:
        return {"ok": False, "error": "MinerU API token is not configured. Fill in the API key under MinerU in the Agent settings panel."}

    def _run():
        return parse_pdfs(str(p), str(out_root), token)

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return {"ok": False, "error": str(e)}

tools_registry["mineru_parse"] = _make_async_tool(
    name="mineru_parse",
    description="Parse PDF papers into Markdown with MinerU. Use it when the user uploads a PDF or gives a PDF path/folder and needs readable Markdown. Supports one PDF or a folder (recursive scan) through the MinerU v4 batch interface (single file, or folder batches of at most 50). Each PDF yields a same-named .zip containing the parse output, and the file paths are returned. Requires a MinerU token.",
    args_schema=MinerUParams,
    func=_mineru_parse,
)


# -- Search / edit / literature / GitHub --
# Implemented in backend/tools/{search_tools,lit_tools,github_tools}.py; this file only registers them and holds wording.
# Three rules are baked into the descriptions:
#   1) locate first, then read: do not read whole files immediately;
#   2) over-limit results say how much remains and how to continue;
#   3) do not retry the same call; read the error and change approach.

from tools.search_tools import (search_files as _search_files, find_files as _find_files,
                                edit_file as _edit_file)
from tools.lit_tools import (crossref_lookup as _crossref_lookup, arxiv_search as _arxiv_search,
                             semantic_scholar_search as _s2_search)
from tools.github_tools import (gh_repo_info as _gh_repo_info, gh_list_dir as _gh_list_dir,
                                gh_read_file as _gh_read_file, gh_search_repos as _gh_search_repos,
                                gh_search_code as _gh_search_code, gh_download_tarball as _gh_tarball)


class SearchFilesParams(BaseModel):
    pattern: str = Field(description="Regular expression (Python re syntax)")
    path: str = Field(default=".", description="Search root (file or directory). Relative paths resolve against workspace/ (same as read_file); outside safe mode local absolute paths are allowed (on Windows e.g. C:/Users/<name>/Documents, using this machine's real path)")
    file_glob: str = Field(default=None, description="Only search files matching this glob, e.g. *.py / *.md")
    output_mode: str = Field(default="files_with_matches",
                             description="files_with_matches = file names only (locate first) / content = line numbers and context / count = hits per file")
    context: int = Field(default=0, description="Context lines in content mode (like grep -C)")
    limit: int = Field(default=50, description="Maximum results (the response reports how many remain; continue with offset)")
    offset: int = Field(default=0, description="Skip the first N results (paging)")
    order: str = Field(default="modified", description="modified = most recently modified first / discovery = directory walk order")


class FindFilesParams(BaseModel):
    pattern: str = Field(description="File-name glob, e.g. *.py, **/test_*.py")
    path: str = Field(default=".", description="Search root directory. Relative paths resolve against workspace/; outside safe mode absolute paths are allowed")
    limit: int = Field(default=50, description="Maximum results")
    offset: int = Field(default=0, description="Skip the first N results")


class EditFileParams(BaseModel):
    file_path: str = Field(description="File path to edit")
    old_string: str = Field(description="Original text to replace (must match exactly once; otherwise an error lists the occurrence count)")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False, description="true = replace every occurrence (default false: only a unique old_string is replaced)")


class CrossrefParams(BaseModel):
    doi: str = Field(default=None, description="DOI, e.g. 10.1038/s41467-026-70180-1 (most accurate when available)")
    query: str = Field(default=None, description="Title/keywords (used when there is no DOI)")
    rows: int = Field(default=5, description="Number of results in query mode")


class ArxivParams(BaseModel):
    query: str = Field(description="Search terms (English works best)")
    max_results: int = Field(default=5, description="Number of results")
    sort_by: str = Field(default="relevance", description="relevance / lastUpdatedDate / submittedDate")


class S2Params(BaseModel):
    query: str = Field(description="Search terms")
    limit: int = Field(default=5, description="Number of results")
    year: str = Field(default=None, description="Year filter, e.g. 2020-2026 or 2024")


class GhRepoParams(BaseModel):
    repo: str = Field(description="owner/name, e.g. NousResearch/hermes-agent")


class GhListDirParams(BaseModel):
    repo: str = Field(description="owner/name")
    path: str = Field(default="", description="Directory path; empty = repository root")
    ref: str = Field(default=None, description="Branch/tag/commit; defaults to the repository default branch")


class GhReadFileParams(BaseModel):
    repo: str = Field(description="owner/name")
    path: str = Field(description="File path, e.g. src/main.py")
    ref: str = Field(default=None, description="Branch/tag/commit")
    max_chars: int = Field(default=40000, description="Maximum characters (long output reports truncation and the total length)")


class GhSearchReposParams(BaseModel):
    query: str = Field(description="Repository search terms")
    limit: int = Field(default=5, description="Number of results")
    sort: str = Field(default="stars", description="stars / forks / updated")


class GhSearchCodeParams(BaseModel):
    query: str = Field(description="Code signature (function name, unique string; if nothing is found use a more distinctive string, do not retry the same one)")
    limit: int = Field(default=10, description="Number of results")
    lang: str = Field(default=None, description="Language filter, e.g. python")


class GhTarballParams(BaseModel):
    repo: str = Field(description="owner/name")
    ref: str = Field(default=None, description="Branch/tag/commit")
    dest_dir: str = Field(default=None, description="Destination directory (defaults to your output area)")


tools_registry["search_files"] = _make_async_tool(
    name="search_files",
    description=(
        "Search file **content** locally with a regex: a grep for LLMs with three output modes and paging.\n\n"
        "WHEN: the user asks where X is implemented or which file has X, you must locate code before editing, or you need a function name or string.\n"
        "PATH BASE: relative paths resolve against workspace/ (same as read_file) - to search project code pass this machine absolute path (Windows e.g. C:/Users/<name>/project/backend).\\n"
        "HOW (in order; 1-2 calls should converge): 1) start with output_mode=files_with_matches to see which files;"
        "2) when few files remain, use output_mode=content + context=3 on one file;"
        "3) too many hits: add file_glob (e.g. *.py) or tighten the regex - **do not switch to whole-file read_file**;"
        "4) use output_mode=count to check whether an approach is promising.\n"
        "Over-limit results report how many remain; continue with offset (do not repeat the search)."
    ),
    args_schema=SearchFilesParams, func=_search_files)

tools_registry["find_files"] = _make_async_tool(
    name="find_files",
    description=(
        "Find files by **file name** glob (e.g. *.pdf, **/test_*.py), newest modified first.\n\n"
        "WHEN: you only remember a file name/suffix and need its location, or need to list a directory.\n"
        "HOW: pass a glob; broaden it when nothing is found (first *fragment*, then *). Do not guess with terminal ls/find."
    ),
    args_schema=FindFilesParams, func=_find_files)

tools_registry["edit_file"] = _make_async_tool(
    name="edit_file",
    description=(
        "Replace one passage in a file (no full rewrite).\n\n"
        "WHEN: the user asks to change a specific place and you have **already read** that passage.\n"
        "HOW: include enough context in old_string to be unique; if the error says it occurs N times, add more context and retry -"
        "do **not** gamble with replace_all (it changes unintended places). Re-read with read_file to confirm.\n"
        "Never touch a file you have not read."
    ),
    args_schema=EditFileParams, func=_edit_file)

tools_registry["crossref_lookup"] = _make_async_tool(
    name="crossref_lookup",
    description=(
        "Look up literature metadata (Crossref, no key): title/journal/year/authors/DOI/citations.\n\n"
        "WHEN: a DOI is known and metadata is needed, or only a title is known and its DOI is needed.\n"
        "HOW: with a DOI pass doi= (most accurate); otherwise query=title. To read the paper use web_extract(url='https://doi.org/<DOI>').\n"
        "Do not use web_search for metadata (too noisy)."
    ),
    args_schema=CrossrefParams, func=_crossref_lookup)

tools_registry["arxiv_search"] = _make_async_tool(
    name="arxiv_search",
    description=(
        "Search arXiv preprints (no key): id/title/abstract/authors/date/primary category.\n\n"
        "WHEN: looking for literature in arXiv-covered fields (physics/CS/materials/engineering) or verifying a known arXiv paper.\n"
        "HOW: query in English; try synonyms **once** for broader coverage; if both are empty, tell the user nothing was found."
    ),
    args_schema=ArxivParams, func=_arxiv_search)

tools_registry["semantic_scholar_search"] = _make_async_tool(
    name="semantic_scholar_search",
    description=(
        "Search Semantic Scholar (no key, rate-limited): title/year/journal/citations/abstract/DOI.\n\n"
        "WHEN: judging the **influence** of a topic (citationCount) or needing abstracts for a quick read.\n"
        "HOW: on 429 do **not** retry; use crossref_lookup and mention it in the answer."
    ),
    args_schema=S2Params, func=_s2_search)

tools_registry["gh_repo_info"] = _make_async_tool(
    name="gh_repo_info",
    description=("GitHub repository overview (stars/language/description/default branch), no token.\n\n"
                 "WHEN: the user mentions an open-source repository; confirm what it is and its default branch."),
    args_schema=GhRepoParams, func=_gh_repo_info)

tools_registry["gh_list_dir"] = _make_async_tool(
    name="gh_list_dir",
    description=("List files in a GitHub repository directory (no token).\n\n"
                 "WHEN: the file location is unknown. With a known path use gh_read_file; do not crawl level by level."),
    args_schema=GhListDirParams, func=_gh_list_dir)

tools_registry["gh_read_file"] = _make_async_tool(
    name="gh_read_file",
    description=("Read one file from a GitHub repository (no token, raw). Long files report truncation and total length.\n\n"
                 "WHEN: inspecting how someone implemented something. To read a whole project, use gh_download_tarball then search_files/read_file locally (fewer calls)."),
    args_schema=GhReadFileParams, func=_gh_read_file)

tools_registry["gh_search_repos"] = _make_async_tool(
    name="gh_search_repos",
    description=("Search GitHub repositories by keyword (no token), sortable by stars.\n\n"
                 "WHEN: the exact repository is unknown; find candidates (e.g. Python implementation of XX)."),
    args_schema=GhSearchReposParams, func=_gh_search_repos)

tools_registry["gh_search_code"] = _make_async_tool(
    name="gh_search_code",
    description=("Search **code snippets** across public GitHub repositories (public search service, no token).\n\n"
                 "WHEN: finding which projects use a function/class, or seeing real API usage.\n"
                 "HOW: query with a distinctive string (full function name, exact error text). The official code search API needs auth; this uses a public service,"
                 "switch to a more distinctive string when nothing is found - **do not repeat the same search**."),
    args_schema=GhSearchCodeParams, func=_gh_search_code)

tools_registry["gh_download_tarball"] = _make_async_tool(
    name="gh_download_tarball",
    description=("Download a GitHub repository tarball locally (no token, codeload).\n\n"
                 "WHEN: reading an entire open-source project (far fewer calls than gh_read_file per file).\n"
                 "HOW: the local path is returned; then use find_files/search_files/read_file."),
    args_schema=GhTarballParams, func=_gh_tarball)


# -- XWiki tools (one read, one write, dispatched by action) --

tools_registry["wiki_read"] = _make_async_tool(
    name="wiki_read",
    description=(LIBRARY_ROUTING +
                 "THIS TOOL = store 3 only: the team Wiki (XWiki) — read-only, one tool dispatched by action."
                 "It is NOT the shared notes library (shared_search) and NOT the user's personal notes (conversation_notes).\n"
                 "\n"
                 "ACTIONS\n"
                 "  search (default) keyword search in titles+body, needs query; returns space/page/title/snippet/url\n"
                 "  get            read a full page body, needs space+page\n"
                 "  list_spaces    list all spaces (optional name filter, e.g. name='06')\n"
                 "  list_pages     list pages in a space, needs space\n"
                 "  list_attachments list page attachments, needs space+page\n"
                 "\n"
                 "HOW: is there XX in the wiki -> search; a known page -> get;\n"
                 "unknown space name -> list_spaces first (or filter by name).\n"
                 "Space names may be non-English (e.g. '02. New member guide'); copy the returned space field.\n"
                 "Search is full-text database matching (not semantic): use specific keywords and change them when nothing is found."),
    args_schema=WikiReadParams, func=_wiki_read)

tools_registry["wiki_write"] = _make_async_tool(
    name="wiki_write",
    description=(LIBRARY_ROUTING +
                 "THIS TOOL = store 3 only: the team Wiki (XWiki) — one tool dispatched by action."
                 "It is NOT the shared notes library (shared_search) and NOT the user's personal notes (conversation_notes).\n"
                 "\n"
                 "ACTIONS\n"
                 "  create            create a page, needs space+page+content\n"
                 "  update            overwrite a page body, needs space+page+content (version read to prevent conflicts)\n"
                 "  delete            delete a page, needs space+page (irreversible)\n"
                 "  upload_attachment upload a local file, needs space+page+file_path\n"
                 "\n"
                 "RULES\n"
                 "  1) content must be XWiki 2.1 syntax (headings, bold, lists, code blocks), not Markdown;\n"
                 "  2) before editing a page, wiki_read(action=get) to see the current state; never overwrite blindly;\n"
                 "  3) always have the user confirm the page name before deleting."),
    args_schema=WikiWriteParams, func=_wiki_write)
