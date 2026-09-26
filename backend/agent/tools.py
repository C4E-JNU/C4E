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
from tools.vector_search import search_history as _search_history
from tools.shared_hub import shared_upload as _shared_upload, shared_search as _shared_search, knowledge as _knowledge
from tools.mineru_tools import parse_pdfs, load_token


# ── Pydantic 参数模型（提供给 LLM 的 JSON Schema） ──

class CypherParams(BaseModel):
    cypher: str = Field(description="要执行的 Cypher 查询语句，例如: MATCH (n) WHERE n.name CONTAINS $q RETURN n.name LIMIT 10")
    params: dict = Field(default={}, description="查询参数（可选），例如: {'q': '关键词'}")

class SearchParams(BaseModel):
    query: str = Field(description="搜索关键词，例如: '向量数据库 Qdrant 用法'")
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
    file_id: str = Field(default="", description="上传附件的 file_id：已解析文档返回解析全文；未解析附件（笔记附件）会【即时提取原文】返回；图片返回 base64(dataURL)。给 file_id 时优先于 path")
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
    action: str = Field(description="操作类型: list=列表, read=读取单条, add=添加, append=向已有条目末尾追加内容, update=更新, delete=删除")
    scope: str = Field(
        default="notes",
        description="目标存储，必须按用户意图明确选择："
                    "scope='notes' = 本地对话笔记（用户说「我的笔记」「笔记」「刚记的」）；"
                    "scope='database' = 云端共享数据库条目（用户说「数据库」「共享库」「组里的条目」「共享数据库」）。"
                    "两者格式相同但存储不同，改错地方是不可逆错误：拿不准时先 list 确认，或直接问用户。"
    )
    note_id: Optional[str] = Field(default=None, description="条目ID（read/append/update/delete时需要；数据库条目的 id 形如 note-1234567890）")
    title: Optional[str] = Field(default=None, description="笔记标题（add时需要）")
    content: Optional[str] = Field(
        default=None,
        description="笔记内容（add/append/update时需要）。必须使用完整 Markdown 富文本，充分利用排版与可视化能力："
                    "① 用 #/##/### 分级标题组织结构；② 用 - / 1. / **加粗** / *斜体* 等列表与强调；"
                    "③ 数据对比用 Markdown 表格；④ 流程图、时序图、甘特图等用 ```mermaid 代码块；"
                    "⑤ 数学公式用 $$...$$ 块级 / $...$ 行内 LaTeX；⑥ 代码用 ```语言 代码块。"
                    "让笔记图文并茂、结构清晰、可视化丰富。"
                    "【长度纪律】单次调用建议不超过 6000 字：长文先 add 首部，再用 append 分段追加；超过 2 万字符的写入会被拒绝。"
    )

class ListFilesParams(BaseModel):
    subdir: Optional[str] = Field(default="", description="目录路径；不传则列出 workspace 根目录，非安全模式可用绝对路径列出项目/磁盘目录")

class SkillWriteParams(BaseModel):
    name: str = Field(description="技能名（作为 skills/<name>/ 目录名，不能含 / 或 \\\\\\\\）")
    content: str = Field(description="文件完整内容；写 SKILL.md 时需含 YAML frontmatter（name/description/version）")
    file_path: str = Field(default="SKILL.md", description="子文件路径（如 references/x.md、templates/x、scripts/x.py），默认 SKILL.md")

class SearchMemoryParams(BaseModel):
    query: str = Field(description="搜索关键词，例如: '实验记录 仪器标定'")
    scope: str = Field(default="auto", description="搜索范围: auto=自动判断, history=历史对话, knowledge=知识库")

class SharedUploadParams(BaseModel):
    file_id: str = Field(default="", description="本地上传文件的 file_id（8 位 id，上传后返回的）")
    path: str = Field(default="", description="或 workspace 内相对路径/绝对路径。file_id 与 path 二选一必填")


class SharedSearchParams(BaseModel):
    """共享知识库工具 —— 检索 + 分组管理统一入口（用户 2026-09-25 定：
    「知识库工具统一，不要多写太多新工具」）。

    检索类：action="search"（默认），走语义检索。
    分组类：action=list_groups / create_group / update_group / move_group / delete_group。
    每次只做一件事，用 action 指定。
    """
    action: str = Field(
        default="search",
        description=("要执行的操作，取值：\n"
                     "  search        —— 语义检索共享库（默认）\n"
                     "  list_groups   —— 查看分组树（含每个分组的简介，用于定位资料在哪组）\n"
                     "  create_group  —— 新建分组（需 name；可给 parent_id、intro）\n"
                     "  update_group  —— 改分组名或简介（需 group_id；可给 name、intro）\n"
                     "  move_group    —— 移动分组（需 group_id；parent_id 为目标父，空=移到根级）\n"
                     "  delete_group  —— 删除分组（需 group_id；mode=move_up 子上移(默认) / cascade 连子删）\n"
                     "  list_entries  —— 列出条目（**支持 owner/year/kind/sort/summary_only 过滤，见下**）\n"
                     "  move_entry    —— 把条目移到分组（需 entry_id、to_group_id；to_group_id 空=移出分组）"),
    )
    query: str = Field(default="", description="search 用：搜索关键词或自然语言问题，例如 '组内仪器使用规范'")
    top_k: int = Field(default=5, description="search 用：返回最相关的条数，默认 5")
    group_id: str = Field(default="", description="list_entries 用：限定某分组（空=全部；\"__root__\"=未分组）；update/move/delete_group 用：要操作的分组 id（支持 8 位短 id）")
    name: str = Field(default="", description="create/update_group 用：分组名")
    parent_id: str = Field(default="", description="create/move_group 用：父分组 id；空字符串 = 根级")
    intro: str = Field(default="", description="create/update_group 用：分组简介（用户手写或你代写的一句话说明，供人阅读与检索导航）")
    mode: str = Field(default="move_up", description="delete_group 用：move_up=子分组与条目上移到父级（默认）；cascade=连同子分组一起删")
    entry_id: str = Field(default="", description="move_entry 用：要移动的条目 id")
    to_group_id: str = Field(default="", description="move_entry 用：目标分组 id；空字符串 = 移出分组（回到未分组）")
    owner: str = Field(default="", description="list_entries 用：只看某用户上传的（如 \"xhq1\"；空=全部用户）")
    year: str = Field(default="", description="list_entries 用：只看某年上传的（如 \"2026\"；按上传时间 createdAt；空=全部年份）")
    kind: str = Field(default="", description="list_entries 用：\"note\"=笔记 / \"document\"=文档；空=两者都要")
    sort: str = Field(default="time", description="list_entries 用：\"time\"=上传时间新→旧(默认) / \"time_asc\"=旧→新 / \"title\"=按名称")
    summary_only: bool = Field(default=False, description=("list_entries 用：True 时**只返回各分组的条目数量**，不返回明细。\n"
                                                           "**当你不确定资料在哪个分组时，先用 summary_only=True 看「哪个组有货」，"
                                                           "再针对具体 group_id 拉明细 —— 这样最省 token，不要先拉全部再自己筛。**"))


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
        "WHEN to use: 用户问到最新研究、新闻、论文、领域进展，或你对事实不确定、知识可能过时（如最新研究进展、某技术性能数据、市场信息）时，先搜索再回答；不要用自己可能过时的知识直接下结论。\n\n"
        "WHEN NOT: 能直接从图谱/上传文档/用户给定资料可靠回答时，优先用那些，不必搜索。\n\n"
        "HOW: 构造一个客观、聚焦的查询（可用关键词组合，如 'Qwen3 发布 特性 评测 2026'）。若第一次结果不佳，换关键词或加限定词再搜一次；不要无限重试同一查询。\n\n"
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


async def _list_outputs_tool():
    """列出本人产出区（owner 由登录身份推导，**不接受模型传身份**）。"""
    from tools.file_tools import list_outputs
    return list_outputs()


tools_registry["list_outputs"] = StructuredTool.from_function(
    name="list_outputs",
    description=(
        "列出你生成的文件（产出区里的 csv/png/pptx/xlsx/md 等）及其 file_id。\n\n"
        "WHEN to use: 你用 write_file('outputs/xxx') 或 run_code / 技能脚本生成了文件之后 —— "
        "把产出交给用户时必须先拿到 file_id。\n\n"
        "HOW: 拿到 file_id 后，在回答里写 {{file:<file_id>:<文件名>}} —— 前端会渲染成附件卡片，"
        "用户点它就能『后端打开』或『前端下载/浏览器预览』（与笔记附件同款）。\n"
        "注意：产出文件请写到 outputs/ 目录（相对路径），否则不会被收录。"
    ),
    coroutine=_list_outputs_tool,
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

# 单条笔记内容硬上限：超过即拒绝并提示拆分/追加（防止模型单次生成过长内容被输出上限截断）
MAX_NOTE_CHARS = 20000


def _note_too_long(content: str) -> str:
    if content and len(content) > MAX_NOTE_CHARS:
        return (f"单次写入内容 {len(content)} 字符，超过上限 {MAX_NOTE_CHARS}，已拒绝（强行写入会在生成端被截断）。"
                f"请拆分为多条笔记，或先写入前半部分、再用 append 动作分段追加。")
    return ""

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

async def _exec_notes(action: str, note_id: str = None, title: str = None, content: str = None,
                      scope: str = "notes") -> str:
    """笔记/数据库条目统一入口。

    scope='notes'    → 本地对话笔记 users/<身份>/notes.json（按当前对话分支）
    scope='database' → 云端共享数据库条目（branch=__db__；本地实例自动转发云端）
    """
    from agent.agent import _current_branch_id, _current_user
    from tools import file_tools as _ft
    owner = _current_user or "guest"
    now = str(__import__("datetime").datetime.now())

    # ── 数据库条目：走云端（云端实例直接本地库；本地实例转发 hub）──
    if (scope or "notes").strip().lower() in ("database", "db", "__db__", "cloud", "shared"):
        from tools.shared_hub import db_notes
        act = action
        r = await db_notes(act, note_id or "", title or "", content or "", owner)
        if "error" in r:
            return json.dumps(r, ensure_ascii=False)
        if act == "list":
            entries = r.get("entries", [])
            # 数据库 list 已是目录形态（id/title/owner/content_len/preview，无正文），直接透传
            slim = [{"id": e.get("id"), "title": e.get("title"), "owner": e.get("owner", ""),
                     "content_len": e.get("content_len", 0), "preview": e.get("preview", "")}
                    for e in entries]
            return json.dumps({"status": "ok", "scope": "database", "total": len(slim),
                               "note": "以上仅为目录（不含正文，避免撑爆上下文）。需要正文用 action=read 按 id 读取；"
                                       "增删改用 update/delete/add",
                               "entries": slim}, ensure_ascii=False, indent=2)
        if act == "read":
            return json.dumps({"status": "ok", "scope": "database", "entry": r.get("entry")},
                              ensure_ascii=False, indent=2)
        msg = {"add": "已在数据库新建条目", "append": "已追加到数据库条目",
               "update": "已更新数据库条目", "delete": "已删除数据库条目"}.get(act, "完成")
        return json.dumps({"status": "ok", "scope": "database", "id": r.get("id", ""),
                           "message": f"{msg}（id={r.get('id','')}）"}, ensure_ascii=False)

    # ── 本地对话笔记 ──
    if action == "add":
        if not title and not content:
            return json.dumps({"error": "标题和内容不能同时为空"})
        too_long = _note_too_long(content)
        if too_long:
            return json.dumps({"error": too_long}, ensure_ascii=False)
        r = json.loads(await _ft.add_note(title, content, _current_branch_id, owner))
        return json.dumps({"status": "ok", "message": f"已添加笔记: {title}（{len(content or '')} 字符）",
                           "note_id": r.get("note_id")}, ensure_ascii=False)

    if action == "append":
        if not note_id:
            return json.dumps({"error": "append 需要提供 note_id"})
        if not content:
            return json.dumps({"error": "append 的内容不能为空"})
        too_long = _note_too_long(content)
        if too_long:
            return json.dumps({"error": too_long}, ensure_ascii=False)
        notes = _ft._read_notes(owner)
        for n in notes:
            if n.get("id") == note_id:
                n["content"] = (n.get("content", "") + "\n\n" + content) if n.get("content") else content
                n["updatedAt"] = now
                _ft._write_notes(owner, notes)
                return json.dumps({"status": "ok", "message": f"已追加到笔记: {n['title']}（现共 {len(n['content'])} 字符）"}, ensure_ascii=False)
        return json.dumps({"error": "笔记不存在"})

    if action == "update":
        too_long = _note_too_long(content) if content is not None else None
        if too_long:
            return json.dumps({"error": too_long}, ensure_ascii=False)
        r = json.loads(await _ft.update_note(note_id, title, content, owner))
        if "error" in r:
            return json.dumps(r, ensure_ascii=False)
        notes = _ft._read_notes(owner)
        t = next((n for n in notes if n.get("id") == note_id), {})
        return json.dumps({"status": "ok", "message": f"已更新笔记: {t.get('title','')}（{len(t.get('content',''))} 字符）"}, ensure_ascii=False)

    if action == "delete":
        r = json.loads(await _ft.delete_note(note_id, owner))
        if "error" in r:
            return json.dumps(r, ensure_ascii=False)
        return json.dumps({"status": "ok", "message": "已删除笔记"}, ensure_ascii=False)

    # read：按 id 读单条本地笔记
    if action == "read":
        n, _ = _ft._find_note(owner, note_id)
        if n is None:
            return json.dumps({"error": "笔记不存在"}, ensure_ascii=False)
        return json.dumps({"status": "ok", "scope": "notes",
                           "entry": {"id": n["id"], "title": n.get("title", ""),
                                     "content": (n.get("content") or "")[:20000],
                                     "content_len": len(n.get("content") or ""),
                                     "truncated": len(n.get("content") or "") > 20000,
                                     "branch_id": n.get("branch_id", ""),
                                     "owner": n.get("owner", owner)}}, ensure_ascii=False, indent=2)

    # list：当前对话(branch)的本地笔记 —— 只给目录，不含正文（正文可能极大，全量返回会撑爆上下文）
    notes = [n for n in _ft._read_notes(owner) if n.get("branch_id", "") == _current_branch_id]
    def _slim_local(n):
        c = n.get("content") or ""
        return {"id": n["id"], "title": n.get("title", ""), "content_len": len(c),
                "preview": c[:120].replace("\n", " ") + ("…" if len(c) > 120 else "")}
    return json.dumps({
        "status": "ok", "total": len(notes),
        "note": "以上仅为笔记目录（不含正文）。需要正文请用 action=read 按 id 读取。",
        "notes": [_slim_local(n) for n in notes]
    }, ensure_ascii=False, indent=2)

# ── 子智能体（delegate_task）──────────────────────

class DelegateTaskParams(BaseModel):
    tasks: list[dict] = Field(
        description="要派发的子任务列表（1-4 个）。每个元素形如 "
                    "{\"goal\": \"这个子任务要达成的目标（必填，具体可执行）\", "
                    "\"context\": \"必要的背景信息（可选，把主对话里相关的关键事实写进来，子智能体看不到主对话）\", "
                    "\"tools\": [\"可选：限定该子智能体可用的工具名\"], "
                    "\"role\": \"可选：专有角色预设 contradiction=文献矛盾核查 / extraction=数据提取 / verify=数据核查，注入领域系统提示，同类任务建议使用\"}"
    )


def delegate_prepare(tasks) -> tuple:
    """校验并裁剪子任务列表。返回 (valid_tasks, error_json_str 或 None)。

    供两条路径复用：① _exec_delegate（非流式 / LangChain 工具路径）；
    ② agent.stream_agent 的自定义引擎流式路径（边跑边流）。
    """
    from agent.agent import get_subagent_options, get_current_model_config
    from agent.subagent import SUBAGENT_MAX_TASKS

    opts = get_subagent_options()
    if not opts.get("enabled", True):
        return [], json.dumps({"error": "子智能体功能已在设置中关闭"}, ensure_ascii=False)
    if not get_current_model_config().get("model"):
        return [], json.dumps({"error": "子智能体无法启动：缺少模型配置（请先选择模型）"}, ensure_ascii=False)
    if not isinstance(tasks, list) or not tasks:
        return [], json.dumps({"error": "tasks 不能为空"}, ensure_ascii=False)
    limit = int(opts.get("max_tasks", SUBAGENT_MAX_TASKS) or SUBAGENT_MAX_TASKS)
    valid = [t for t in tasks if isinstance(t, dict) and (t.get("goal") or "").strip()][:limit]
    if not valid:
        return [], json.dumps({"error": "每个子任务都必须提供非空 goal"}, ensure_ascii=False)
    return valid, None


async def _exec_delegate(tasks: list[dict], max_rounds: int = None, timeout: int = None) -> str:
    """派发子智能体（并发上限 & 三级超时由设置决定；失败即失败，不重试）。

    非流式路径（LangChain 引擎 / /api/agent/run）：跑完再一次性回传。
    自定义引擎的流式路径在 agent.stream_agent 中直接调用 run_subagents_parallel(emit=...)。
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
    # 把任务清单放进事件槽 → 主循环立刻能向前端流 subagent_start（引擎无关）
    set_delegate_tasks(valid)
    results = await run_subagents_parallel(valid, max_rounds=mr, timeout=to,
                                           max_parallel=int(opts.get("max_parallel", 2) or 2),
                                           timeouts=sub_timeouts)
    # 结果放进槽 → 主循环流 subagent_end（含完整步骤/结论）
    set_delegate_results(results)
    return json.dumps({"status": "ok", "count": len(results), "results": results},
                      ensure_ascii=False)


tools_registry["delegate_task"] = _make_async_tool(
    name="delegate_task",
    description=(
        "派发子智能体（子智能体=独立上下文的小助手）并行去做**互相独立的子任务**，你负责汇总它们的结论。"
        "【什么时候用】任务可以切成 2-4 块彼此独立、且每块都需要多步检索/阅读时（例如"
        "「分别查 A、B、C 三个课题的文献进展」「一篇文章读结构、一篇文章读数据」）；"
        "或者需要并行搜集多方资料以节省时间时。"
        "【什么时候不要用】① 一个问题就能答完（自己调工具更快）；② 子任务之间存在前后依赖（必须先拿到前一个结果才能定下一个）——"
        "这种情况应你自己按顺序做；③ 只是想写笔记/改数据库（用 conversation_notes）；④ 不要为了「显得正式」而派子智能体。"
        "【硬限制】子智能体不能再派子智能体（深度=1）；单次可派的子任务数与并发数、每个子智能体的轮次与三级超时，"
        "都以用户在设置里的「子智能体」配置为准（默认 4 个任务 / 并发 2 / 轮次 4 / 超时 300s）；"
        "子智能体只能使用只读/检索类工具，不能写文件、不能执行终端命令。"
        "【怎么用】每个子任务的 goal 要写明「做什么+要产出什么」，context 里带上它需要的背景"
        "（子智能体看不到我们的对话历史，不写它就只能自己瞎猜）；拿到 results 后，你**必须**把它们综合成给用户的最终答案，"
        "不要只是把子智能体的原文拼贴返回。"
    ),
    args_schema=DelegateTaskParams,
    func=_exec_delegate,
)


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
    description="管理「笔记」与「数据库条目」两套同格式存储，用 scope 参数明确指定目标（必填判断）："
                "scope='notes'=本地对话笔记（用户说「笔记」「我的笔记」）；"
                "scope='database'=云端共享数据库条目（用户说「数据库」「共享库」「组里的条目」）。"
                "支持 list=列表, read=按id读取, add=新建, append=末尾追加, update=更新, delete=删除。"
                "【读取纪律·重要】list 只返回**目录**（id/标题/owner/正文长度/前120字预览），**不含正文**——"
                "这是为了防止大条目（有的几十万字）一次性撑爆上下文。要看某条正文，必须再用 read 按 id 取；"
                "read 单次最多返回 20000 字（超出会标记 truncated=true 并告知原文总长），"
                "不要反复 read 同一超长条目，需要时请让用户分段查看或指定要看哪部分。"
                "【意图识别铁律】用户提到「数据库/共享库/组里」→ scope='database'；只提「笔记/记一下」→ scope='notes'；"
                "拿不准时先用 list 看两边内容再决定，或直接问用户，绝不要猜。"
                "【重要·长度纪律】单次写入的 content 不要超过约 6000 字：超出模型单轮输出上限的内容会被截断。"
                "长内容必须分步构建——先 add 写入第一部分，随后用 append 逐段追加（每次约 2000-5000 字）；"
                "或拆分为多条并在标题中标注（上/中/下、卷一/卷二等）。绝对不要试图一次写完全文。"
                "【排版要求】充分利用 Markdown 富文本能力，做到图文并茂、结构清晰、可视化丰富："
                "① 用 #/##/### 分级标题组织长文结构；"
                "② 用有序/无序列表、**加粗**、*斜体*、> 引用等增强可读性；"
                "③ 涉及数据对比、参数、结果汇总时，用 Markdown 表格呈现；"
                "④ 需要展示流程、架构、时序、甘特图时，用 ```mermaid 代码块（flowchart / sequenceDiagram / gantt / pie 等）；"
                "⑤ 涉及数学公式时，用 $$...$$ 块级、$...$ 行内 LaTeX；"
                "⑥ 代码示例用 ```语言 代码块。"
                "让每条内容都像一份排版精良的图文报告，而不是纯文本。",
    args_schema=NotesParams,
    func=_exec_notes,
)

# ── 搜索工具 ─────────────────────────────────────

async def _search_history_wrapped(query: str, top_k: int = 3, scope: str = None) -> str:
    """按当前登录身份搜索本人聊天历史（游客=guest 空间）。"""
    from agent.agent import _current_user
    return await _search_history(query, top_k, scope, user=_current_user)


tools_registry["search_history"] = _make_async_tool(
    name="search_history",
    description="从当前用户的历史对话中搜索相关信息（按登录身份隔离，每人只搜到自己的）。当你需要回顾之前讨论过的内容、数据、结论时使用。返回最相关的片段及相似度分数。",
    args_schema=SearchMemoryParams,
    func=_search_history_wrapped,
)

# search_knowledge 已移除：本地实例无数据库；检索共享数据库统一用 shared_search。

tools_registry["shared_upload"] = _make_async_tool(
    name="shared_upload",
    description="把本地文件上传到组内共享库（局域网服务器上的 C4EAI 实例）。上传后服务器自动解析文本并入知识索引，"
                "全组人都能用 shared_search 搜到。当用户说「共享」「传到服务器」「给组里用」等意图时使用。"
                "参数给 file_id（本地上传的文件）或 path（workspace 内路径）之一。",
    args_schema=SharedUploadParams,
    func=_shared_upload,
)

tools_registry["shared_search"] = _make_async_tool(
    name="shared_search",
    description=(
        "组内共享知识库的统一入口：语义检索 + 分组树管理 + 条目查询。\n"
        "\n"
        "【共享库的组织方式】\n"
        "  顶层按条目类型分：笔记（人写的总结，附件不解析）与文档（上传自动解析）。\n"
        "  下层是分组树，每个分组有【简介】说明这组放什么 —— 找资料先看简介判断方向。\n"
        "  条目归属于某个分组，并记录了上传者(owner)与上传时间。\n"
        "\n"
        "【找资料的正确顺序 —— 请按这个流程，能大幅省 token】\n"
        "  1) action=list_groups      看分组树与简介，了解有哪些方向\n"
        "  2) action=list_entries + summary_only=True\n"
        "     看「哪个分组有多少条」。**不确定资料在哪组时先做这步，不要直接拉全部明细。**\n"
        "  3) action=list_entries 针对具体 group_id 拉明细（只返回标题/id/owner/时间，不含正文）\n"
        "  4) 需要正文再 action=search 做语义检索，或读具体条目\n"
        "\n"
        "【过滤条件下推 —— 直接用参数筛，不要把全部拉回来自己筛】\n"
        "  例：用户问「26 年 xhq1 传的 XX 组的内容」\n"
        "     → action=list_entries, owner=\"xhq1\", year=\"2026\", group_id=\"<该组>\"\n"
        "  例：不知道在哪组 → 先 summary_only=True + owner=\"xhq1\" + year=\"2026\"\n"
        "     返回各组的条数，再挑有货的组拉明细。\n"
        "\n"
        "【语义检索】action=search（默认）：在共享库中做语义搜索，返回相关片段与来源文档。\n"
        "  适合「我不知道具体在哪，按意思找」的场景。\n"
        "\n"
        "【管理】用户说「建个分组/把这组移到xx下/改一下简介/把某条移到某组」等，\n"
        "  用 create_group / move_group / update_group / move_entry / delete_group 直接做，不必问怎么做。\n"
        "  注意：分组只是分类，删除分组不会删除条目（子分组与条目默认上移到父级）。"
    ),
    args_schema=SharedSearchParams,
    func=_knowledge,
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
