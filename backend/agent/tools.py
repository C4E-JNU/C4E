"""
LangChain 工具包装 — 将后端的工具函数包装为 LangChain @tool
每个工具包含：name, description, args_schema, func
"""
import json
import os
from langchain.tools import tool, BaseTool
from langchain_core.tools import StructuredTool
from typing import Optional, Type
from pydantic import BaseModel, Field

# ── 导入后端工具函数 ──────────────────────────────
from tools.neo4j_tools import (
    execute_graph_schema as _exec_graph_schema,
    execute_cypher as _exec_cypher,
    Neo4jClient,
)
from tools.web_tools import web_search as _web_search, web_extract as _web_extract
from tools.skill_tools import skill_list as _skill_list, skill_view as _skill_view, skill_write as _skill_write
from tools.code_sandbox import run_python as _run_python
from tools.terminal_tool import terminal as _terminal
from tools.file_tools import read_file as _read_file, write_file as _write_file, list_files as _list_files
from tools.vector_search import search_history as _search_history, search_knowledge as _search_knowledge
from tools.mineru_tools import parse_pdfs, load_token


# ── Pydantic 参数模型（提供给 LLM 的 JSON Schema） ──

class CypherParams(BaseModel):
    cypher: str = Field(description="要执行的 Cypher 查询语句，例如: MATCH (n:Pollutant) WHERE n.name CONTAINS $q RETURN n.name LIMIT 10")
    params: dict = Field(default={}, description="查询参数（可选），例如: {'q': 'CIP'}")

class SearchParams(BaseModel):
    query: str = Field(description="搜索关键词，例如: 'CIP 光催化降解 TiO2'")
    limit: int = Field(default=5, description="返回结果数量 1-10")

class FetchParams(BaseModel):
    url: str = Field(description="要抓取的网页完整 URL，必须以 http:// 或 https:// 开头")

class SkillViewParams(BaseModel):
    name: str = Field(description="技能名称，例如: 'graph-guide'")
    file_path: str = Field(default="SKILL.md", description="技能内的文件路径，不传则读 SKILL.md")

class CodeParams(BaseModel):
    code: str = Field(description="要执行的 Python 代码")
    timeout: int = Field(default=30, description="超时秒数（1-60）")

class ReadFileParams(BaseModel):
    path: str = Field(default="", description="文件路径；相对路径以 workspace/ 为基准，询问/完全访问模式下可用绝对路径读取项目内或磁盘文件。若同时给 file_id 则可省略")
    file_id: str = Field(default="", description="上传附件的 file_id：文档返回解析全文，图片返回 base64(dataURL) 供多模态。给 file_id 时优先于 path")
    as_base64: bool = Field(default=False, description="按 file_id 读取时是否强制返回 base64（图片默认即 base64）")
    offset: int = Field(default=1, description="起始行号")
    limit: int = Field(default=1000000, description="最多返回行数（文件内容做大不截断）")

class WriteFileParams(BaseModel):
    path: str = Field(description="文件路径；安全模式仅限 workspace/，询问模式可写 workspace/ 与 skills/，完全访问模式可写任意绝对路径")
    content: str = Field(description="文件内容（完整覆盖）")

class TerminalParams(BaseModel):
    command: str = Field(description="要执行的 shell 命令（如 `uv pip install pandas`、`uv sync`、`python script.py`、`git status`）。可执行任意命令，受权限模式约束。")
    workdir: str = Field(default=".", description="工作目录（相对路径以项目根为基准；省略则用项目根）。安全模式强制在项目内。")

class TodoParams(BaseModel):
    action: str = Field(description="操作类型: list=列表, add=添加, complete=标记完成, remove=删除")
    task: Optional[str] = Field(default=None, description="任务描述（add 操作时需要）")
    task_id: Optional[int] = Field(default=None, description="任务编号（complete/remove 操作时需要）")

class NotesParams(BaseModel):
    action: str = Field(description="操作类型: list=列表, add=添加, update=更新, delete=删除")
    note_id: Optional[str] = Field(default=None, description="笔记ID（update/delete时需要）")
    title: Optional[str] = Field(default=None, description="笔记标题（add时需要）")
    content: Optional[str] = Field(
        default=None,
        description="笔记内容（add/update时需要）。必须使用完整 Markdown 富文本，充分利用排版与可视化能力："
                    "① 用 #/##/### 分级标题组织结构；② 用 - / 1. / **加粗** / *斜体* 等列表与强调；"
                    "③ 数据对比用 Markdown 表格；④ 流程图、时序图、甘特图等用 ```mermaid 代码块；"
                    "⑤ 数学公式用 $$...$$ 块级 / $...$ 行内 LaTeX；⑥ 代码用 ```语言 代码块。"
                    "让笔记图文并茂、结构清晰、可视化丰富。"
    )

class ListFilesParams(BaseModel):
    subdir: Optional[str] = Field(default="", description="目录路径；不传则列出 workspace 根目录，非安全模式可用绝对路径列出项目/磁盘目录")

class SkillWriteParams(BaseModel):
    name: str = Field(description="技能名（作为 skills/<name>/ 目录名，不能含 / 或 \\\\\\\\）")
    content: str = Field(description="文件完整内容；写 SKILL.md 时需含 YAML frontmatter（name/description/version）")
    file_path: str = Field(default="SKILL.md", description="子文件路径（如 references/x.md、templates/x、scripts/x.py），默认 SKILL.md")

class SearchMemoryParams(BaseModel):
    query: str = Field(description="搜索关键词，例如: 'CIP 光催化降解 TiO2'")
    scope: str = Field(default="auto", description="搜索范围: auto=自动判断, history=历史对话, knowledge=知识库")


# ── 工具工厂函数 ─────────────────────────────────

def _make_async_tool(name: str, description: str, args_schema: Type[BaseModel], func):
    """创建一个 LangChain StructuredTool（异步）"""
    return StructuredTool.from_function(
        name=name,
        description=description,
        args_schema=args_schema,
        coroutine=func,
    )


# ── 工具实例 ─────────────────────────────────────

tools_registry = {}

def get_tools(enabled_names: list[str]) -> list[BaseTool]:
    """根据启用列表返回 LangChain 工具实例"""
    return [t for name, t in tools_registry.items() if name in enabled_names]


# ── 注册工具 ─────────────────────────────────────

tools_registry["graph_schema"] = StructuredTool.from_function(
    name="graph_schema",
    description="获取 Neo4j 知识图谱的完整结构，包括所有节点类型（如 Pollutant, Catalyst）、关系类型（如 DEGRADES）、各类型的属性列表、向量索引信息。首次查询时调用此工具了解图谱结构。",
    coroutine=_exec_graph_schema,
)

tools_registry["execute_cypher"] = _make_async_tool(
    name="execute_cypher",
    description="执行任意 Cypher 查询语句，返回查询结果数组。所有图谱数据都通过此工具获取。支持任何 MATCH / RETURN / CALL 语句。如果是写操作（CREATE / DELETE / SET）需谨慎。",
    args_schema=CypherParams,
    func=_exec_cypher,
)

tools_registry["web_search"] = _make_async_tool(
    name="web_search",
    description=(
        "搜索互联网获取实时信息，返回多个结果（标题、URL、摘要）。\n\n"
        "WHEN to use: 用户问到最新研究、新闻、论文、领域进展，或你对事实不确定、知识可能过时（如催化降解最新成果、某材料性能数据、市场信息）时，先搜索再回答；不要用自己可能过时的知识直接下结论。\n\n"
        "WHEN NOT: 能直接从图谱/上传文档/用户给定资料可靠回答时，优先用那些，不必搜索。\n\n"
        "HOW: 构造一个客观、聚焦的查询（可用关键词组合，如 'Fe2O3 光催化 降解 CIP 机理 2023'）。若第一次结果不佳，换关键词或加限定词再搜一次；不要无限重试同一查询。\n\n"
        "After searching: 结合多条结果交叉判断，优先采信权威来源，注明不确定性；用 web_extract 读取需要全文的关键结果。"
    ),
    args_schema=SearchParams,
    func=_web_search,
)

tools_registry["web_extract"] = _make_async_tool(
    name="web_extract",
    description=(
        "抓取指定 URL 的网页内容并转为纯文本，返回正文要点。\n\n"
        "WHEN to use: 需要读论文全文、博客、新闻详情、官方文档时——web_search 只给了标题/摘要，正文细节要靠它。\n\n"
        "HOW: 传入具体 URL。若网页为 PDF 或某些反爬站点可能受限，返回不完整时告知用户或换源。\n\n"
        "Note: 抓取后只提取正文关键信息回答用户，不要原样贴出整篇内容。"
    ),
    args_schema=FetchParams,
    func=_web_extract,
)

tools_registry["skill_list"] = StructuredTool.from_function(
    name="skill_list",
    description="列出所有可用的技能及其描述。技能包含领域知识、操作指引、规则约束。当你不确定如何回答或需要领域专业知识时，先用此工具看有什么技能可用。",
    coroutine=_skill_list,
)

tools_registry["run_code"] = _make_async_tool(
    name="run_code",
    description=(
        "在隔离沙箱中执行 Python 代码，返回 stdout。\n\n"
        "WHEN to use: 用户要求计算、数据分析、统计、可视化、批量处理、脚本执行，或需要验证某段逻辑时。\n\n"
        "HOW: 写完整可运行的代码（含必要的 import 和 print 输出）。代码在临时目录运行，有 30 秒超时。\n\n"
        "Note: 沙箱禁止系统命令和网络副作用（os.system/subprocess 受控）；适合纯计算/数据分析。需要文件系统或系统操作时用 read_file/write_file。"
    ),
    args_schema=CodeParams,
    func=_run_python,
)

tools_registry["skill_view"] = _make_async_tool(
    name="skill_view",
    description=(
        "读取一个技能的完整内容（SKILL.md 或子文件）。\n\n"
        "WHEN to use: 系统已列出可用技能（<available_skills> 或 skill_list），当你需要真正按某技能执行（它含操作指引、命令、质量标准）时，\n"
        "必须先 skill_view(name) 读取全文——一行简介不足以指导执行。即使你自认会用基础工具，只要技能相关就应读取。\n\n"
        "HOW: 传入技能名。读了之后严格按技能所述步骤与约定执行该任务。"
    ),
    args_schema=SkillViewParams,
    func=_skill_view,
)

tools_registry["terminal"] = _make_async_tool(
    name="terminal",
    description=(
        "在项目环境中执行 shell 命令（git、build、test、uv/pip 装包、跑脚本等），返回 stdout/stderr/exit_code。\n\n"
        "WHEN to use: 需要运行真实命令、装/升级第三方库（uv pip install / uv sync）、跑测试或脚本、查看 git 状态时。run_code 沙箱只跑 stdlib，需要第三方包时必须用 terminal。\n\n"
        "HOW: 写完整 shell 命令。工作目录默认项目根；可用 workdir 指定（相对项目根）。\n\n"
        "安全: 受权限模式约束——safe 仅项目内且禁高危命令；ask 涉及外部会返回 need_confirm 需用户确认；full 任意执行。装包建议用 `uv pip install <包>` 装进项目 venv。"
    ),
    args_schema=TerminalParams,
    func=_terminal,
)

tools_registry["read_file"] = _make_async_tool(
    name="read_file",
    description="读取文件内容，可指定起始行和行数。相对路径以 workspace/ 为基准；询问/完全访问模式下可用绝对路径读取项目内或磁盘上的文件。",
    args_schema=ReadFileParams,
    func=_read_file,
)

tools_registry["write_file"] = _make_async_tool(
    name="write_file",
    description="将内容写入文件（存在则覆盖）。相对路径以 workspace/ 为基准。安全模式仅限 workspace/ 内；询问模式可写 workspace/ 与 skills/；完全访问模式可写任意路径（含项目代码、系统文件）。",
    args_schema=WriteFileParams,
    func=_write_file,
)

tools_registry["skill_write"] = _make_async_tool(
    name="skill_write",
    description=(
        "创建或更新一个技能：写入 skills/<name>/ 下的文件（默认 SKILL.md，可写 references/templates/scripts 子文件），并自动更新 skills/index.json。\n\n"
        "WHEN to use: 用户要求把某套做法保存成可复用技能、或修正现有技能；当用户提出可复用的工作流/操作指引且值得留存时，主动提出创建技能。\n\n"
        "HOW — SKILL.md 必须含 YAML frontmatter：\n"
        "  name: 技能名（作目录名，勿含 / 或空白）\n"
        "  description: 触发条件自包含的一句话（'当要X时用。简述行为。'），首 57 字符内写明何时触发。\n"
        "  version: 版本号\n"
        "正文用 Markdown：分节（## 何时用 / ## 步骤 / ## 坑 / ## 验证），把可复现的具体命令、参数、易错点写清楚，让未来一次成功。\n\n"
        "写完后 index.json 会自动同步。"
    ),
    args_schema=SkillWriteParams,
    func=_skill_write,
)

# ── todo 实现 ─────────────────────────────────────

_WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspace"))

def _get_todo_path():
    os.makedirs(_WORKSPACE, exist_ok=True)
    return os.path.join(_WORKSPACE, "_todos.json")

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
            return json.dumps({"error": "任务描述不能为空"})
        new_id = max([t.get("id", 0) for t in todos], default=0) + 1
        todos.append({"id": new_id, "content": task, "done": False, "created": str(__import__("datetime").datetime.now())})
        _write_todos(todos)
        return json.dumps({"status": "ok", "message": f"已添加任务: {task}", "total": len(todos)}, ensure_ascii=False)
    elif action == "complete":
        if task_id is None:
            return json.dumps({"error": "需要指定任务编号"})
        for t in todos:
            if t["id"] == task_id:
                t["done"] = True
                _write_todos(todos)
                return json.dumps({"status": "ok", "message": f"✅ 已完成: {t['content']}"}, ensure_ascii=False)
        return json.dumps({"error": f"任务 #{task_id} 不存在"})
    elif action == "remove":
        if task_id is None:
            return json.dumps({"error": "需要指定任务编号"})
        new_todos = [t for t in todos if t["id"] != task_id]
        if len(new_todos) == len(todos):
            return json.dumps({"error": f"任务 #{task_id} 不存在"})
        _write_todos(new_todos)
        return json.dumps({"status": "ok", "message": f"已删除任务 #{task_id}"})
    else:  # list
        done = sum(1 for t in todos if t.get("done"))
        return json.dumps({
            "status": "ok", "total": len(todos), "completed": done,
            "pending": len(todos) - done,
            "tasks": [{"id": t["id"], "content": t["content"], "done": t.get("done", False)} for t in todos]
        }, ensure_ascii=False, indent=2)

# ── conversation_notes 实现 ──────────────────────

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

async def _exec_notes(action: str, note_id: str = None, title: str = None, content: str = None) -> str:
    from agent.agent import _current_branch_id
    notes = _read_notes()
    if action == "add":
        if not title and not content:
            return json.dumps({"error": "标题和内容不能同时为空"})
        new_note = {
            "id": "note-" + str(__import__("time").time()).replace(".", ""),
            "title": title or "新笔记",
            "content": content or "",
            "branch_id": _current_branch_id,
            "createdAt": str(__import__("datetime").datetime.now()),
            "updatedAt": str(__import__("datetime").datetime.now()),
        }
        notes.append(new_note)
        _write_notes(notes)
        return json.dumps({"status": "ok", "message": f"已添加笔记: {title}", "note_id": new_note["id"]}, ensure_ascii=False)
    elif action == "update":
        for n in notes:
            if n["id"] == note_id:
                if title is not None:
                    n["title"] = title or "未命名笔记"
                if content is not None:
                    n["content"] = content
                n["updatedAt"] = str(__import__("datetime").datetime.now())
                _write_notes(notes)
                return json.dumps({"status": "ok", "message": f"已更新笔记: {n['title']}"}, ensure_ascii=False)
        return json.dumps({"error": "笔记不存在"})
    elif action == "delete":
        new_notes = [n for n in notes if n["id"] != note_id]
        if len(new_notes) == len(notes):
            return json.dumps({"error": "笔记不存在"})
        _write_notes(new_notes)
        return json.dumps({"status": "ok", "message": "已删除笔记"})
    else:  # list
        notes = [n for n in notes if n.get("branch_id", "") == _current_branch_id]
        return json.dumps({
            "status": "ok", "total": len(notes),
            "notes": [{"id": n["id"], "title": n["title"], "content": n.get("content", "")} for n in notes]
        }, ensure_ascii=False, indent=2)

# ── 注册新工具 ───────────────────────────────────

tools_registry["list_files"] = _make_async_tool(
    name="list_files",
    description="列出目录下的文件。相对路径以 workspace/ 为基准；非安全模式下可用绝对路径列出项目或磁盘目录。",
    args_schema=ListFilesParams,
    func=_list_files,
)

tools_registry["todo"] = _make_async_tool(
    name="todo",
    description="管理任务清单。可以列出所有任务、添加新任务、标记任务完成、删除任务。当你需要跟踪多步工作进度或帮用户记住待办事项时使用。",
    args_schema=TodoParams,
    func=_exec_todo,
)

tools_registry["conversation_notes"] = _make_async_tool(
    name="conversation_notes",
    description="管理当前对话的笔记。可以列出、添加、更新、删除笔记。笔记会保存到服务器。"
                "【重要】写入笔记内容时，必须充分利用 Markdown 富文本能力，做到图文并茂、结构清晰、可视化丰富："
                "① 用 #/##/### 分级标题组织长文结构；"
                "② 用有序/无序列表、**加粗**、*斜体*、> 引用等增强可读性；"
                "③ 涉及数据对比、参数、结果汇总时，用 Markdown 表格呈现；"
                "④ 需要展示流程、架构、时序、甘特图时，用 ```mermaid 代码块（flowchart / sequenceDiagram / gantt / pie 等）；"
                "⑤ 涉及数学公式时，用 $$...$$ 块级、$...$ 行内 LaTeX；"
                "⑥ 代码示例用 ```语言 代码块。"
                "让每条笔记都像一份排版精良的图文报告，而不是纯文本。",
    args_schema=NotesParams,
    func=_exec_notes,
)

# ── 搜索工具 ─────────────────────────────────────

tools_registry["search_history"] = _make_async_tool(
    name="search_history",
    description="从所有历史对话中搜索相关信息。当你需要回顾之前讨论过的内容、数据、结论时使用。返回最相关的片段及相似度分数。",
    args_schema=SearchMemoryParams,
    func=_search_history,
)

tools_registry["search_knowledge"] = _make_async_tool(
    name="search_knowledge",
    description="从上传的知识库文档中搜索信息。当用户问到已上传文档中的内容时使用。返回最相关的片段及相似度分数。",
    args_schema=SearchMemoryParams,
    func=_search_knowledge,
)

# ── MinerU PDF 解析工具 ──────────────────────────

class MinerUParams(BaseModel):
    path: str = Field(description="要解析的 PDF 文件路径，或包含多个 PDF 的文件夹路径（会递归扫描所有 .pdf）。路径可以是相对 workspace/ 的相对路径，或绝对路径。")
    out_dir: str = Field(default="mineru_output", description="Markdown 输出目录（相对 workspace/）。单文件默认输出到该目录下的同名 .md。")
    single_pdf: bool = Field(default=False, description="为 True 时 path 视为单个 PDF 文件；为 False 时自动判断：是文件就单文件，是目录就批量。一般不需手动设置。")

async def _mineru_parse(path: str, out_dir: str = "mineru_output", single_pdf: bool = False):
    """MinerU 解析工具后端实现：解析单个 PDF 或文件夹下所有 PDF（走 MinerU v4 批量接口）。"""
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
        return {"ok": False, "error": "未配置 MinerU API Token。请在『智能体配置』面板的 MinerU 配置中填写 API Key。"}

    def _run():
        return parse_pdfs(str(p), str(out_root), token)

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return {"ok": False, "error": str(e)}

tools_registry["mineru_parse"] = _make_async_tool(
    name="mineru_parse",
    description="使用 MinerU 将 PDF 文献解析为 Markdown。当用户上传了 PDF 或给出 PDF 文件/文件夹路径，需要把 PDF 转成可读 Markdown 时使用。支持单个 PDF 文件或包含多个 PDF 的文件夹（递归扫描）。走 MinerU v4 批量接口（单文件、或文件夹自动分批，每批至多 50 个）。每个 PDF 输出一个同名 .zip（内含解析产物），返回文件列表路径。需配置 MinerU Token。",
    args_schema=MinerUParams,
    func=_mineru_parse,
)
