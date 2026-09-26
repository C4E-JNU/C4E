"""
LangChain Agent 构建（LangChain 1.3.14 API）
使用 create_agent → 直接返回编译好的 LangGraph
"""
import json, os, time, asyncio
from pathlib import Path
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.chat_history import InMemoryChatMessageHistory

# ── 当前对话上下文（供工具读取）────────────────────
_current_branch_id = ""
_current_user = ""          # 当前请求身份（""=旧版全局；guest/用户名=按身份分文件）
_current_model_config = {}   # 当前请求的模型配置（子智能体等工具需要读它）


def set_current_model_config(cfg: dict = None):
    global _current_model_config
    _current_model_config = dict(cfg or {})


def get_current_model_config() -> dict:
    return dict(_current_model_config)


# ── 超时三级（催促 / 警告 / 终止）默认值，可被请求参数覆盖 ──
DEFAULT_TIMEOUTS = {"nudge": 120, "warn": 240, "abort": 420}

# 子智能体选项（前端设置传入）：轮次 / 并发上限 / 最大子任务数 / 三级超时
_subagent_options = {"max_rounds": 4, "timeout": 300, "enabled": True,
                     "max_parallel": 2, "max_tasks": 4,
                     "nudge": 60, "warn": 150, "abort": 300}


def set_subagent_options(opts: dict = None):
    global _subagent_options
    o = dict(_subagent_options)
    for k in ("max_rounds", "timeout", "enabled", "max_parallel", "max_tasks",
              "nudge", "warn", "abort"):
        if opts and opts.get(k) is not None:
            o[k] = opts[k]
    try:
        o["max_rounds"] = max(1, min(20, int(o.get("max_rounds", 4))))
        o["max_parallel"] = max(1, min(8, int(o.get("max_parallel", 2))))     # 并发上限（自由调节 1-8）
        o["max_tasks"] = max(1, min(8, int(o.get("max_tasks", 4))))           # 单次最多子任务数
        o["nudge"] = max(0, min(3600, int(o.get("nudge", 60))))
        o["warn"] = max(0, min(3600, int(o.get("warn", 150))))
        o["abort"] = max(30, min(3600, int(o.get("abort", 300))))
        o["timeout"] = o["abort"]                                             # 旧字段与新终止级保持一致
        o["enabled"] = bool(o.get("enabled", True))
    except Exception:
        pass
    # 递进保护：三级单调递增
    if o["nudge"] and o["warn"] and o["warn"] < o["nudge"]:
        o["warn"] = o["nudge"]
    if o["abort"] and o["warn"] and o["abort"] < o["warn"]:
        o["abort"] = o["warn"]
    _subagent_options = o


def get_subagent_options() -> dict:
    return dict(_subagent_options)


# ── 子智能体事件槽（引擎无关）──────────────────────
# 工具执行时把"派了哪些子任务 / 各自结果"放进来，主循环（custom 与 langchain 都行）取走并流给前端。
# 这样就不依赖 LC 事件里的工具名（实测 astream_events 有时取不到名字）。
_delegate_pending_tasks: list = []
_delegate_pending_results: list = []


def set_delegate_tasks(tasks: list):
    _delegate_pending_tasks.extend(tasks or [])


def pop_delegate_tasks() -> list:
    out = list(_delegate_pending_tasks)
    _delegate_pending_tasks.clear()
    return out


def set_delegate_results(results: list):
    _delegate_pending_results.extend(results or [])


def pop_delegate_results() -> list:
    out = list(_delegate_pending_results)
    _delegate_pending_results.clear()
    return out


def clear_delegate_pending():
    _delegate_pending_tasks.clear()
    _delegate_pending_results.clear()


def _resolve_timeouts(t: dict = None) -> dict:
    """解析三级超时（秒）：nudge=催促、warn=警告、abort=终止。0/缺省 = 关闭该级。"""
    out = dict(DEFAULT_TIMEOUTS)
    if isinstance(t, dict):
        for k in ("nudge", "warn", "abort"):
            if t.get(k) is not None:
                try:
                    out[k] = max(0, int(t[k]))
                except Exception:
                    pass
    # 递进保护：三级必须单调递增，避免误配导致顺序错乱
    if out["nudge"] and out["warn"] and out["warn"] < out["nudge"]:
        out["warn"] = out["nudge"]
    if out["abort"] and out["warn"] and out["abort"] < out["warn"]:
        out["abort"] = out["warn"]
    if out["abort"] and out["nudge"] and out["abort"] < out["nudge"]:
        out["abort"] = out["nudge"]
    return out


def set_current_user(user: str = ""):
    """记录当前登录身份：agent 内部用（历史/路径），同时**转发**给 file_tools ——
    产出区与附件登记也要知道"文件属于谁"（用户 2026-09-26 定）。"""
    global _current_user
    u = (user or "").strip()
    if not u or u == "guest":
        _current_user = "guest"
    else:
        _current_user = u
    _set_ft_user(_current_user)

# ── 全局生成取消令牌（用户点停止 / 前端断连时置位，两引擎每轮检查）──
_GENERATION_CANCELLED = False

def reset_generation_cancel():
    """新一轮开始时清除取消标记。"""
    global _GENERATION_CANCELLED
    _GENERATION_CANCELLED = False

def cancel_active_generation():
    """置位取消标记：当前/后续生成应在最近检查点提前结束。"""
    global _GENERATION_CANCELLED
    _GENERATION_CANCELLED = True

def generation_cancelled() -> bool:
    return _GENERATION_CANCELLED

from agent.tools import tools_registry, get_tools
from tools.neo4j_tools import Neo4jClient
from tools.skill_tools import skill_list as _skill_list
from tools.file_tools import set_permission_mode, set_current_user as _set_ft_user

_WORKSPACE = Path(__file__).parent.parent / "workspace"


def _history_file_for(user: str = None) -> Path:
    """按身份分文件：_history_guest.jsonl / _history_<名>.jsonl；user=None 用当前上下文；空串=旧版全局。"""
    u = _current_user if user is None else user
    if not u:
        return _WORKSPACE / "_history.jsonl"
    key = "".join(c for c in u if (c.isalnum() or c in "-_@.·")) or "_guest"
    return _WORKSPACE / f"_history_{key}.jsonl"


def _save_to_history(branch_id: str, user_input: str, final_answer: str, msg_id: str):
    """保存单次对话到历史文件（追加，按身份分文件）"""
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": msg_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "branch_id": branch_id,
        "input": user_input,
        "output": final_answer,
    }
    with open(_history_file_for(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _trigger_vector_index():
    """异步触发向量索引（在后台线程执行，不阻塞返回，按身份分索引）"""
    import threading
    u = _current_user
    hf = _history_file_for(u).name
    def _do_index():
        try:
            from tools.vector_search import index_new_history
            meta_file = _WORKSPACE / (f"_vector_meta_{''.join(c for c in u if (c.isalnum() or c in '-_@.·')) or '_guest'}.json" if u else "_vector_meta.json")
            last_id = None
            if meta_file.exists():
                import json as j
                meta = j.loads(open(meta_file).read())
                last_id = meta.get("last_indexed_id")
            index_new_history(hf, last_id, u)
        except Exception as e:
            print(f"[Vector] Index error: {e}")
    threading.Thread(target=_do_index, daemon=True).start()

async def _build_system_prompt(enabled_tools: list[str], loaded_skills: list[str]) -> str:
    """Hermes 式系统提示词：不列出工具清单（工具能力靠 OpenAI tools schema），技能只简列 name+desc。

    与 Hermes 一致的设计：
      - 工具能力/行为指引由每个工具的 schema description 提供（见 tools.py），系统提示词不重复工具名。
      - 技能以 <available_skills> 形式简列勾选技能的 name+description；详情按需用 skill_view 读取（不漏全文）。
    """
    lines = [
        "你是 C4EAI · Shared Wiki ——实验室协作知识与文献智能体（组内共享库）。核心能力：",
        "① 图谱检索：Neo4j 协作知识图谱 + GraphRAG（schema/execute_cypher）；"
        "② 多智能体协作：delegate_task 派发专有子智能体（矛盾核查/数据提取/数据核查）并行处理；"
        "③ 数据分析与代码：run_code 计算/统计/绘图，terminal 执行命令；"
        "④ 共享数据库：组内文献/笔记/文档的上传与语义检索（shared_upload / shared_search）；"
        "⑤ RAG 检索增强：本地知识库与历史对话检索；"
        "⑥ 高阶文献翻译：MinerU 解析归档 → 全文中译、图文并茂写笔记；"
        "⑦ 文献内容审查：图账/表账/段落账三轨道数据对账与矛盾裁决；"
        "⑧ 联网搜索与文件处理：web_search、read/write_file。",
        "被问候或问「你是谁/能做什么」时：简短自我介绍，并按用户已启用的工具从上面列举核心能力（一行一项，不超过 8 项），不要空泛客套。",
        "",
        "## 规则",
        "- 用中文回答，保留专业英文术语",
        "- 需要哪些能力时，直接使用对应工具（工具的能力与用法由各工具的接口说明给出）。",
        "- 图谱查询、联网搜索、文件解析、技能读取等，按用户问题需求选择合适的工具。",
        "- **不要在回答中重复或引用工具返回的原始 JSON 数据**。工具执行结果已被系统自动展示给用户，你在回答中只给出结论和分析。",
        "- **最终回答简洁明了**，不要包含中间步骤的描述。用户能看到你调用了哪些工具。",
        "- **调用工具后必须收敛**：每次工具执行完，先根据返回内容判断是否已足够回答用户问题。若已获得所需信息，立即生成最终回答并结束，**不要无意义地重复调用工具**。只有确实缺少关键信息时才继续调用**不同的、有必要的**工具。同一工具返回失败时，最多按需换用别的工具一次，不要反复重试同一工具。",
        "- 若某写操作工具返回包含 `__NEED_FULL__` 的错误，说明当前为询问模式、该操作超出权限范围。请明确告知用户：需要切换到完全访问模式后才能执行，并简要说明要做什么。",
        "- **产出文件一律写到 `outputs/` 目录**（相对路径，例如 `outputs/报告.pptx`）：用 write_file 写文本时路径就用 `outputs/xxx`；"
        "用 run_code 或技能脚本生成文件时**直接写文件名即可**（它执行时的工作目录就是产出区）。"
        "写完后**必须调 `list_outputs` 拿到 file_id**，并在回答里用 `{{file:<file_id>:<文件名>}}` 引用它 —— "
        "前端会渲染成附件卡片，用户点它可选『后端打开』（服务器上用默认程序打开）或『前端打开』（下载 / 浏览器内预览 PDF 等）。"
        "**绝不要只写「已生成 xxx.pptx」而不给占位符**，那样用户拿不到文件。",
        "",
    ]
    # 技能：Hermes 式 <available_skills> 简列（只列勾选技能；详情用 skill_view，不注入全文）
    if loaded_skills:
        try:
            skills_data = json.loads(await _skill_list())
            skills_index = {s["name"]: s.get("description", "") for s in skills_data.get("skills", [])}
        except Exception:
            skills_index = {}
        skill_lines = []
        for sn in loaded_skills:
            desc = skills_index.get(sn)
            skill_lines.append(f"  - {sn}: {desc}" if desc else f"  - {sn}")
        if skill_lines:
            lines += [
                "## Skills (mandatory)",
                "Before replying, if a listed skill below matches or is even partially relevant to the task, load it with skill_view(name) and follow its instructions. Skills contain specialized knowledge — API endpoints, tool-specific commands, and proven workflows. Load it even if you think you could handle the task with the basic tools. Details of each skill are read on demand via skill_view; do not assume their content from the one-line description alone.",
                "<available_skills>",
                *skill_lines,
                "</available_skills>",
                "",
            ]
    return "\n".join(lines)


# ── 历史持久化（按身份分文件，实现在上方 _history_file_for/_save_to_history/_trigger_vector_index）──


# ── 上下文截断 ────────────────────────────────────

# ── 上下文管理（LangChain 原生）───────────────────

def _build_context(history: list[dict], user_input: str, context_length: int):
    """用 InMemoryChatMessageHistory 装载，保留最近 context_length 轮（每轮约 2 条消息）。
    安全护栏：单条消息按字符截断、并按总字符预算从最早的开始丢弃，防止个别超大历史消息撑爆上下文。"""
    from tools.file_tools import HISTORY_MSG_MAX_CHARS, HISTORY_TOTAL_MAX_CHARS
    hist = InMemoryChatMessageHistory()
    for m in history or []:
        role = m.get("role")
        content = m.get("content")
        if not content:
            continue
        content = str(content)
        if len(content) > HISTORY_MSG_MAX_CHARS:
            content = content[:HISTORY_MSG_MAX_CHARS] + "…[历史消息过长，已截断]"
        if role == "user":
            hist.add_user_message(content)
        elif role == "assistant":
            hist.add_ai_message(content)
    msgs = list(hist.messages)
    if context_length and context_length > 0:
        keep_n = context_length * 2
        if len(msgs) > keep_n:
            msgs = msgs[-keep_n:]
    # 总量兜底：从最早的开始丢，保证历史字符数在预算内（保留当前 user_input）
    total = sum(len(str(m.content)) for m in msgs) + len(user_input or "")
    while msgs and total > HISTORY_TOTAL_MAX_CHARS:
        dropped = msgs.pop(0)
        total -= len(str(dropped.content))
    msgs = msgs + [HumanMessage(content=user_input)]
    return msgs


# ── 文件上下文注入 ────────────────────────────────

async def _prepare_file_context(file_ids: list, full_file_ids, images: list):
    """统一构建附件上下文。

    - full_file_ids（当轮上传的附件）→ 注入完整文本 / 图片转 base64 多模态
    - 其余 file_ids（仅历史消息引用）→ 注入简短引用说明，模型需要时用 read_file(file_id=...) 自取，
      避免大文档每轮重复注入撑爆上下文
    - full_file_ids=None（旧前端未区分）→ 全部按完整注入，兼容旧行为

    返回 (text_block, image_urls)：text_block 拼到当前用户输入之后；image_urls 附加到最后一条 user 消息。
    """
    # ⚠️ READ_FILE_MAX_CHARS 必须一并导入：函数内用它做正文截断上限，
    #    漏导入时只要附件有可提取文本就抛 NameError → HTTP 500 → 前端"未能生成回答"
    from tools.file_tools import (image_to_base64, _resolve_upload,
                                  get_upload_text as get_file_text,
                                  READ_FILE_MAX_CHARS)

    file_ids = [f for f in (file_ids or []) if f]
    full_set = None if full_file_ids is None else set(full_file_ids or [])
    image_urls = list(images or [])
    full_texts, refs = [], []

    for fid in file_ids:
        _, meta, _ = _resolve_upload(fid)
        name = (meta or {}).get("name") or fid
        is_full = full_set is None or fid in full_set

        data_url = await image_to_base64(fid)
        if data_url:
            if is_full:
                image_urls.append(data_url)
            else:
                refs.append(f"[图片文件: {name}]（file_id: {fid}）— 图片已在前文轮次提供")
            continue

        text = await get_file_text(fid)
        if is_full and text:
            if len(text) > READ_FILE_MAX_CHARS:
                text = (text[:READ_FILE_MAX_CHARS] +
                        f"\n…[文件 {name} 正文 {len(text)} 字符，已截断至 {READ_FILE_MAX_CHARS}；"
                        "如需更多内容请用 read_file 指定 file_id 或分页读取]")
            full_texts.append(f"[文件: {name}]\n{text}")
        elif text:
            refs.append(f'[文件: {name}]（file_id: {fid}）— 正文未随本轮注入；如需该文件内容，请调用 read_file(file_id="{fid}") 获取')
        else:
            refs.append(f"[文件: {name}]（file_id: {fid}）— 后端未提取到该文件的文本（扫描版 PDF 请用 mineru_parse 解析）")

    parts = []
    if full_texts:
        parts.append("---\n用户上传了以下文件内容：\n" + "\n---\n".join(full_texts))
    if refs:
        parts.append("---\n本轮对话引用了以下历史附件：\n" + "\n".join(refs))
    text_block = ("\n\n" + "\n\n".join(parts)) if parts else ""
    return text_block, image_urls


# ── 运行 Agent ────────────────────────────────────

async def run_agent(
    history: list[dict],
    user_input: str,
    enabled_tools: list[str],
    loaded_skills: list[str],
    model_config: dict,
    neo4j_config: dict = None,
    context_length: int = 10,
    branch_id: str = "",
    file_ids: list[str] = None,
    full_file_ids: list[str] = None,
    permission_mode: str = "safe",
    user: str = "",
    timeouts: dict = None,
    subagent: dict = None,
) -> dict:
    global _current_branch_id
    _current_branch_id = branch_id or ""
    set_permission_mode(permission_mode or "safe")
    set_current_user(user or "")
    set_current_model_config(model_config)
    set_subagent_options(subagent or {})
    _T = _resolve_timeouts(timeouts)
    clear_delegate_pending()
    _t_start = time.time()
    # 1. Neo4j 连接（可选增强：连不上只降级摘掉图谱工具，绝不终止 —— 用户 2026-09-22 定）
    if neo4j_config and neo4j_config.get("uri"):
        if {"graph_schema", "execute_cypher"} & set(enabled_tools):
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    enabled_tools = [t for t in enabled_tools
                                     if t not in ("graph_schema", "execute_cypher")]

    # 2. System Prompt
    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)

    # 3. LLM
    llm = ChatOpenAI(
        model=model_config.get("model", "deepseek-chat"),
        api_key=model_config.get("api_key") or "sk-noauth",  # 免密钥本地 llama-server 占位
        base_url=model_config.get("base_url", ""),
        temperature=model_config.get("temperature", 0.7),
    )

    # 4. 工具（只取前端启用的）
    tools = get_tools(enabled_tools)
    if not tools:
        return {"final_answer": "没有可用工具", "reasoning_steps": []}

    # 如果没有加载技能，移除 skill_list 和 skill_view 工具
    if not loaded_skills:
        tools = [t for t in tools if t.name not in ("skill_list", "skill_view")]

    # 安全模式下不暴露技能写入工具
    if (permission_mode or "safe") == "safe":
        tools = [t for t in tools if t.name != "skill_write"]

    # 5. Agent（记忆由前端 history 提供）
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

    # 6. 文件内容注入（当轮附件全文 / 历史附件引用说明）
    file_block, _ = await _prepare_file_context(file_ids, full_file_ids, [])
    if file_block:
        user_input = user_input + file_block

    # 7. 上下文管理（保留最近轮次）
    messages = _build_context(history, user_input, context_length)

    # 7. 运行
    try:
        # 记录输入消息的 id，用于跳过作为上下文传入的历史消息（避免旧回答被当作"思考"展示）
        input_ids = {id(m) for m in messages}
        result = await agent.ainvoke({"messages": messages})
        final_answer = ""
        reasoning_steps = []
        for msg in result.get("messages", []):
            if id(msg) in input_ids:
                continue  # 跳过历史上下文消息，只处理本次新生成的消息
            if isinstance(msg, ToolMessage):
                reasoning_steps.append({"type": "tool_result", "name": msg.name or "", "content": str(msg.content)[:300]})
            elif isinstance(msg, AIMessage):
                if msg.content:
                    reasoning_steps.append({"type": "thinking", "text": msg.content})
                    final_answer = msg.content
                # tool_call 紧随 thinking 之后
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        name = tc.get("name", "")
                        if name:
                            reasoning_steps.append({"type": "tool_call", "name": name, "input": json.dumps(tc.get("args", {}), ensure_ascii=False)})

        # 8. 保存到历史文件 + 触发向量索引
        msg_id = f"msg-{int(time.time() * 1000)}"
        _save_to_history(branch_id, user_input, final_answer, msg_id)
        _trigger_vector_index()

        return {"final_answer": final_answer or "Agent 未能生成回答", "reasoning_steps": reasoning_steps}

    except Exception as e:
        import traceback
        return {"final_answer": f"Agent 执行失败: {str(e)}", "reasoning_steps": [], "error": str(e), "traceback": traceback.format_exc()}


# ── 流式 Agent ────────────────────────────────────

async def stream_agent(
    history: list[dict],
    user_input: str,
    enabled_tools: list[str],
    loaded_skills: list[str],
    model_config: dict,
    neo4j_config: dict = None,
    context_length: int = 10,
    branch_id: str = "",
    file_ids: list[str] = None,
    full_file_ids: list[str] = None,
    permission_mode: str = "safe",
    images: list[str] = None,
    max_rounds: int = 8,
    user: str = "",
    timeouts: dict = None,
    subagent: dict = None,
):
    """真流式 Agent：直接调原始 OpenAI 兼容 API 流，逐 token 抓取。

    Events:
      {"type":"reasoning","text":...}    模型推理 (reasoning_content，推理模型)
      {"type":"token","text":...}        模型消息正文 (content，逐字)
      {"type":"tool_call","name":...,"input":...}
      {"type":"tool_result","name":...,"content":...}
      {"type":"subagent_start","index":i,"goal":...,"total":N}
      {"type":"subagent_end","index":i,"ok":...,"summary":...,"steps":[...],"seconds":...}
      {"type":"timeout_notice","level":"nudge|warn|abort","text":...}
      {"type":"final","text":...}
      {"type":"error","message":...}
      {"type":"stopped","text":...}
    """
    import httpx
    import re

    reset_generation_cancel()

    global _current_branch_id
    _current_branch_id = branch_id or ""
    set_permission_mode(permission_mode or "safe")
    set_current_user(user or "")
    set_current_model_config(model_config)
    set_subagent_options(subagent or {})
    _T = _resolve_timeouts(timeouts)
    clear_delegate_pending()

    # Neo4j（可选增强：连不上只降级摘掉图谱工具，绝不终止对话 —— 用户 2026-09-22 定）
    # 理由：用户常只想问普通问题/读附件；图谱没连上不该让整个对话失败。
    # 降级后 execute_cypher 工具本身也返回 {"error":"Neo4j 未连接"}，LLM 自会判断跳过。
    if neo4j_config and neo4j_config.get("uri"):
        if {"graph_schema", "execute_cypher"} & set(enabled_tools):
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    _n4_err = cr.get("error", "")
                    enabled_tools = [t for t in enabled_tools
                                     if t not in ("graph_schema", "execute_cypher")]
                    yield {"type": "reasoning",
                           "text": f"（图谱未连接，已跳过图谱工具继续回答：{_n4_err}）\n"}

    # System prompt
    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)

    # Tools (LangChain StructuredTool)
    tools = get_tools(enabled_tools)
    if not tools:
        yield {"type": "error", "message": "没有可用工具"}
        return
    if not loaded_skills:
        tools = [t for t in tools if t.name not in ("skill_list", "skill_view")]
    if (permission_mode or "safe") == "safe":
        tools = [t for t in tools if t.name != "skill_write"]
    tool_map = {t.name: t for t in tools}

    # 将 LangChain 工具转为 OpenAI function schema，供模型调用
    def _tool_to_openai(t):
        try:
            return t.to_openai_tool_schema()
        except Exception:
            return {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.args_schema.schema() if t.args_schema else {"type": "object", "properties": {}},
                },
            }
    openai_tools = [_tool_to_openai(t) for t in tools]

    # Files injection（当轮附件全文 / 历史附件引用说明）
    file_block, image_urls = await _prepare_file_context(file_ids, full_file_ids, images or [])
    if file_block:
        user_input = user_input + file_block

    # 上下文管理（保留最近轮次）
    ctx = _build_context(history, user_input, context_length)
    messages = [{"role": "system", "content": system_prompt}]
    for m in ctx:
        role = "user" if isinstance(m, HumanMessage) else "assistant"
        messages.append({"role": role, "content": m.content})

    # 多模态：把图片(base64)附加到最后一条 user 消息
    if image_urls:
        # 找到最后一条 user 消息，把 content 扩展为 [text, image_url...]
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                base_content = messages[i]["content"]
                vision_content = [{"type": "text", "text": base_content}]
                for img in image_urls:
                    vision_content.append({"type": "image_url", "image_url": {"url": img}})
                messages[i]["content"] = vision_content
                break

    # LLM 端点
    model = model_config.get("model", "deepseek-chat")
    api_key = model_config.get("api_key", "")
    base = (model_config.get("base_url") or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        endpoint = base
    elif base.endswith("/v1"):
        endpoint = base + "/chat/completions"
    else:
        endpoint = base + "/v1/chat/completions"
    temperature = model_config.get("temperature", 0.7)
    max_tokens = model_config.get("max_tokens") or 8192
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _clean_final(answer):
        raw = answer
        if answer:
            answer = re.sub(r'```json\s*\n.*?```', '', answer, flags=re.DOTALL)
            answer = re.sub(r'^\s*\{[\s\S]*?\}\s*$', '', answer, flags=re.MULTILINE)
            answer = answer.strip()
            if len(answer) < 10:
                answer = raw
        return answer

    final_answer = ""
    all_content = ""   # 累积所有轮次的模型正文，防止工具轮正文丢失
    _t_start = time.time()
    _nudged = _warned = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20, read=300, write=60, pool=20)) as client:
            cap = max_rounds if max_rounds and max_rounds > 0 else 100000  # 0 = 无限
            for _step in range(1, cap + 1):  # 工具循环（步数可配，0=无限）
                if generation_cancelled():
                    yield {"type": "stopped", "text": "已停止"}
                    return

                # ── 三级超时保护：催促 → 警告 → 终止 ──
                _elapsed = time.time() - _t_start
                if _T.get("abort") and _elapsed >= _T["abort"]:
                    yield {"type": "timeout_notice", "level": "abort",
                           "text": f"已达终止时限 {_T['abort']}s（实际 {int(_elapsed)}s），强制结束本轮生成。",
                           "elapsed": int(_elapsed)}
                    final_answer = all_content or final_answer
                    yield {"type": "final",
                           "text": (final_answer + f"\n\n---\n⏱️ **超时终止**：本次生成用时超过 {_T['abort']} 秒上限，已强制结束（未完成的探索被中断）。"
                                                f"可继续追问让它收尾，或在设置里调大「超时终止」时限。") if final_answer
                                   else f"⏱️ **超时终止**：本次生成超过 {_T['abort']} 秒上限且没有产出内容，已强制结束。"}
                    return
                if _T.get("warn") and not _warned and _elapsed >= _T["warn"]:
                    _warned = True
                    messages.append({"role": "user", "content":
                        "[系统·超时警告] 已用时较长。请**立即**基于已获得的信息给出最终答案，"
                        "不要再调用任何工具、不要继续探索；答案可以不完整，但必须马上输出。"})
                    yield {"type": "timeout_notice", "level": "warn",
                           "text": f"已超过警告时限 {_T['warn']}s，已注入「立即作答」提示。", "elapsed": int(_elapsed)}
                elif _T.get("nudge") and not _nudged and _elapsed >= _T["nudge"]:
                    _nudged = True
                    messages.append({"role": "user", "content":
                        "[系统·超时催促] 时间过半，请尽快收尾：停止进一步探索与多余的工具调用，"
                        "把已得到的信息总结成答案；如仍有缺口，在答案中标注「未完成部分」。"})
                    yield {"type": "timeout_notice", "level": "nudge",
                           "text": f"已超过催促时限 {_T['nudge']}s，已注入「尽快收尾」提示。", "elapsed": int(_elapsed)}

                cur_reasoning = ""
                cur_content = ""
                tool_acc = {}  # index -> {id,name,arguments}
                round_finish = ""  # 本轮 finish_reason（length=输出被 max_tokens 截断）
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                    "tools": openai_tools,
                }
                async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="replace")
                        yield {"type": "error", "message": f"模型接口错误 {resp.status_code}: {body[:300]}"}
                        return
                    async for line in resp.aiter_lines():
                        if generation_cancelled():
                            return
                        # 流式中途的硬终止（长回答不会因为循环顶部检查而失守）
                        if _T.get("abort") and (time.time() - _t_start) >= _T["abort"]:
                            yield {"type": "timeout_notice", "level": "abort",
                                   "text": f"已达终止时限 {_T['abort']}s，流式输出中断。",
                                   "elapsed": int(time.time() - _t_start)}
                            yield {"type": "final",
                                   "text": (all_content + cur_content) +
                                           f"\n\n---\n⏱️ **超时终止**：生成超过 {_T['abort']} 秒上限，已中断本次输出。"}
                            return
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            ev = json.loads(data)
                        except Exception:
                            continue
                        choices = ev.get("choices") or []
                        if not choices:
                            continue
                        if choices[0].get("finish_reason"):
                            round_finish = choices[0]["finish_reason"]
                        delta = choices[0].get("delta") or {}
                        rc = delta.get("reasoning_content")
                        ct = delta.get("content")
                        tcs = delta.get("tool_calls")
                        if rc:
                            cur_reasoning += rc
                            yield {"type": "reasoning", "text": rc}
                        if ct:
                            cur_content += ct
                            yield {"type": "token", "text": ct}
                        if tcs:
                            for tc in tcs:
                                idx = tc.get("index", 0)
                                slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                if tc.get("function"):
                                    fn = tc["function"]
                                    if fn.get("name"):
                                        slot["name"] += fn["name"]
                                    if fn.get("arguments"):
                                        slot["arguments"] += fn["arguments"]

                all_content += cur_content

                # 输出被 max_tokens 截断且本轮还有工具调用没生成完：参数 JSON 必然不完整，
                # 与其静默执行残缺参数（写入残缺笔记等），不如明确告知并终止
                if round_finish == "length" and tool_acc:
                    yield {"type": "error", "message":
                           "模型输出因达到“最大输出 Token”上限被截断，工具调用参数不完整。"
                           "请在设置中调大最大输出 Token，或让 AI 分多次写入（例如笔记分卷 / 追加写入）。"}
                    return

                # 本轮结束
                if not tool_acc:
                    # 若本轮无正文（正文出现在工具轮），回退到累积正文，避免最终答案丢失
                    answer_text = cur_content.strip() if cur_content.strip() else all_content
                    final_answer = _clean_final(answer_text)
                    if round_finish == "length" and final_answer:
                        final_answer += "\n\n> ⚠️ 回答因达到最大输出 Token 上限被截断，可点击「继续生成」续写。"
                    yield {"type": "final", "text": final_answer or "Agent 未能生成回答"}
                    # 保存历史 + 触发向量索引
                    try:
                        msg_id = f"msg-{int(time.time() * 1000)}"
                        _save_to_history(branch_id, user_input, final_answer, msg_id)
                        _trigger_vector_index()
                    except Exception as e:
                        print("[stream] save history error:", e)
                    return

                # 有工具调用：执行一轮
                ordered = [tool_acc[i] for i in sorted(tool_acc)]
                assistant_tc = []
                tool_messages = []
                for tc in ordered:
                    name = tc["name"]
                    args_str = tc["arguments"] or "{}"
                    try:
                        args = json.loads(args_str)
                        args_ok = True
                    except Exception:
                        args, args_ok = {}, False
                    yield {"type": "tool_call", "name": name, "input": json.dumps(args, ensure_ascii=False)}
                    # ── 子智能体：自定义引擎流式执行（后台任务 + 事件队列，边跑边流）──
                    if (name == "delegate_task" and isinstance(args, dict) and args_ok
                            and tool_map.get("delegate_task") is not None):
                        from agent import subagent as _sa
                        from agent.tools import delegate_prepare
                        _valid, _err = delegate_prepare(args.get("tasks"))
                        if _err is not None:
                            result = _err
                        else:
                            _o = get_subagent_options()
                            for _i, _t in enumerate(_valid):
                                yield {"type": "subagent_start", "index": _i, "total": len(_valid),
                                       "goal": _t.get("goal", ""), "context": _t.get("context", ""),
                                       "role": (_t.get("role", "") if isinstance(_t, dict) else "")}
                            _q = asyncio.Queue()

                            def _emit(_i, _step):
                                try:
                                    _q.put_nowait({"type": "subagent_step", "index": _i, "step": dict(_step)})
                                except Exception:
                                    pass

                            _sa_task = asyncio.ensure_future(_sa.run_subagents_parallel(
                                _valid,
                                max_rounds=_o.get("max_rounds", 4),
                                timeout=_o.get("timeout", 300),
                                max_parallel=int(_o.get("max_parallel", 2) or 2),
                                timeouts={"nudge": _o.get("nudge"), "warn": _o.get("warn"), "abort": _o.get("abort")},
                                emit=_emit))
                            _draining = True
                            while _draining:
                                try:
                                    _ev = await asyncio.wait_for(_q.get(), timeout=0.25)
                                    yield _ev
                                    continue
                                except asyncio.TimeoutError:
                                    pass
                                if _sa_task.done():
                                    while not _q.empty():
                                        try:
                                            yield _q.get_nowait()
                                        except Exception:
                                            break
                                    _draining = False
                                elif generation_cancelled():
                                    _sa_task.cancel()
                                    _draining = False
                            try:
                                _sa_results = await _sa_task
                            except asyncio.CancelledError:
                                _sa_results = [{"index": i, "ok": False, "error": "已停止",
                                                "goal": _valid[i].get("goal", "")} for i in range(len(_valid))]
                            except Exception as _e:
                                _sa_results = [{"index": i, "ok": False, "error": f"子智能体异常：{_e}",
                                                "goal": _valid[i].get("goal", "")} for i in range(len(_valid))]
                            for _r in _sa_results:
                                yield {"type": "subagent_end",
                                       "index": _r.get("index", 0), "ok": bool(_r.get("ok")),
                                       "goal": _r.get("goal", ""), "context": _r.get("context", ""),
                                       "summary": _r.get("summary", ""), "error": _r.get("error", ""),
                                       "steps": _r.get("steps", []), "rounds": _r.get("rounds", 0),
                                       "seconds": _r.get("seconds", 0)}
                            result = json.dumps({"status": "ok", "count": len(_sa_results),
                                                 "results": _sa_results}, ensure_ascii=False)
                    elif not args_ok:
                        result = json.dumps({"error": f"工具 {name} 的调用参数不是合法 JSON（可能被输出长度截断）。请重新调用并简化参数。"}, ensure_ascii=False)
                    else:
                        tool = tool_map.get(name)
                        if tool is None:
                            result = json.dumps({"error": f"工具 {name} 不存在"})
                        else:
                            try:
                                res = await tool.ainvoke(args)
                                result = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
                            except Exception as e:
                                result = json.dumps({"error": f"工具执行失败: {str(e)}"})
                    yield {"type": "tool_result", "name": name, "content": str(result)[:2000]}
                    if generation_cancelled():
                        yield {"type": "stopped", "text": "已停止"}
                        return
                    call_id = tc["id"] or f"call_{_step}_{len(assistant_tc)}"
                    assistant_tc.append({
                        "id": call_id, "type": "function",
                        "function": {"name": name, "arguments": args_str},
                    })
                    tool_messages.append({"role": "tool", "tool_call_id": call_id, "content": str(result)})
                # 追加本轮 assistant(含 tool_calls) + 各 tool 结果，进入下一轮
                messages.append({"role": "assistant", "content": cur_content, "tool_calls": assistant_tc})
                messages.extend(tool_messages)

        if not final_answer:
            cap = max_rounds if max_rounds and max_rounds > 0 else 100000
            yield {"type": "final", "text": f"已达最大工具循环步数({cap})，仍未生成最终回答。可尝试精简问题或拆分任务。"}

    except Exception as e:
        import traceback
        yield {"type": "error", "message": str(e), "traceback": traceback.format_exc()}


# ── LangChain 流式 Agent ────────────────────────────
async def stream_agent_lc(
    history: list[dict],
    user_input: str,
    enabled_tools: list[str],
    loaded_skills: list[str],
    model_config: dict,
    neo4j_config: dict = None,
    context_length: int = 10,
    branch_id: str = "",
    file_ids: list[str] = None,
    full_file_ids: list[str] = None,
    permission_mode: str = "safe",
    images: list[str] = None,
    max_rounds: int = 8,
    user: str = "",
    timeouts: dict = None,
    subagent: dict = None,
):
    """LangChain 流式 Agent：create_agent + astream_events。

    注：LangChain ChatOpenAI 不解析 reasoning_content，故不产出 reasoning 事件
    （推理模型按非推理看待，正文/工具/最终结果正常）。
    Events: token / tool_call / tool_result / final / error
    """
    import json
    reset_generation_cancel()
    global _current_branch_id
    _current_branch_id = branch_id or ""
    set_permission_mode(permission_mode or "safe")
    set_current_user(user or "")
    set_current_model_config(model_config)
    set_subagent_options(subagent or {})
    _T = _resolve_timeouts(timeouts)
    clear_delegate_pending()
    _t_start = time.time()

    # Neo4j（可选增强：连不上只降级摘掉图谱工具，绝不终止对话 —— 用户 2026-09-22 定）
    # 理由：用户常只想问普通问题/读附件；图谱没连上不该让整个对话失败。
    # 降级后 execute_cypher 工具本身也返回 {"error":"Neo4j 未连接"}，LLM 自会判断跳过。
    if neo4j_config and neo4j_config.get("uri"):
        if {"graph_schema", "execute_cypher"} & set(enabled_tools):
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    _n4_err = cr.get("error", "")
                    enabled_tools = [t for t in enabled_tools
                                     if t not in ("graph_schema", "execute_cypher")]
                    yield {"type": "reasoning",
                           "text": f"（图谱未连接，已跳过图谱工具继续回答：{_n4_err}）\n"}

    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)

    tools = get_tools(enabled_tools)
    if not tools:
        yield {"type": "error", "message": "没有可用工具"}
        return
    if not loaded_skills:
        tools = [t for t in tools if t.name not in ("skill_list", "skill_view")]
    if (permission_mode or "safe") == "safe":
        tools = [t for t in tools if t.name != "skill_write"]

    # 文件内容注入（当轮附件全文 / 历史附件引用说明）
    file_block, image_urls = await _prepare_file_context(file_ids, full_file_ids, images or [])
    if file_block:
        user_input = user_input + file_block

    messages = _build_context(history, user_input, context_length)

    # 多模态：file_ids 图片(auto转base64) + 显式 images 附加到最后一条 user 消息
    if image_urls:
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                import copy
                vision = copy.deepcopy(messages[i])
                base_text = messages[i].content if isinstance(messages[i].content, str) else ""
                parts = [{"type": "text", "text": base_text}]
                for img in image_urls:
                    parts.append({"type": "image_url", "image_url": {"url": img}})
                vision.content = parts
                messages[i] = vision
                break

    llm = ChatOpenAI(
        model=model_config.get("model", "deepseek-chat"),
        api_key=model_config.get("api_key") or "sk-noauth",  # 免密钥本地 llama-server 占位
        base_url=model_config.get("base_url", ""),
        temperature=model_config.get("temperature", 0.7),
        max_tokens=model_config.get("max_tokens") or 8192,
    )
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

    cap = max_rounds if max_rounds and max_rounds > 0 else 100000  # 0 = 无限
    config = {"recursion_limit": cap * 2 + 5}
    final_answer = ""
    saw_length = False  # 任一轮模型输出因 max_tokens 被截断
    _delegate_runs = set()   # 正在执行的 delegate_task 运行 id（用于屏蔽子智能体内部事件）
    _delegate_active = 0     # 正在跑的子智能体批次数（引擎无关的嵌套过滤依据）
    clear_delegate_pending()
    def _ev_name(ev, data):
        """工具名可能落在 event.name / data.name / metadata.name 三处。"""
        return (ev.get("name") or data.get("name")
                or ((ev.get("metadata") or {}).get("name") if isinstance(ev.get("metadata"), dict) else "") or "")
    try:
        async for ev in agent.astream_events({"messages": messages}, version="v2", config=config):
            if generation_cancelled():
                yield {"type": "stopped", "text": "已停止"}
                return
            # 超时终止（LangChain 引擎：图内无法安全注入催促/警告提示，故只做硬终止级保护）
            if _T.get("abort") and (time.time() - _t_start) >= _T["abort"]:
                yield {"type": "timeout_notice", "level": "abort",
                       "text": f"已达终止时限 {_T['abort']}s，强制结束本轮生成（LangChain 引擎仅支持终止级）。",
                       "elapsed": int(time.time() - _t_start)}
                yield {"type": "final",
                       "text": (final_answer.strip() + f"\n\n---\n⏱️ **超时终止**：生成超过 {_T['abort']} 秒上限，已强制结束。")
                               if final_answer.strip() else f"⏱️ **超时终止**：超过 {_T['abort']} 秒上限且无产出，已强制结束。"}
                return
            e = ev.get("event")
            _parents = ev.get("parent_ids") or []
            # ── 子智能体事件：从事件槽取（引擎无关，不依赖 LC 事件里的工具名）──
            _new_tasks = pop_delegate_tasks()
            if _new_tasks:
                _delegate_active += 1
                for _i, _t in enumerate(_new_tasks):
                    yield {"type": "subagent_start", "index": _i, "total": len(_new_tasks),
                           "goal": (_t or {}).get("goal", "") if isinstance(_t, dict) else str(_t),
                           "context": (_t or {}).get("context", "") if isinstance(_t, dict) else "",
                           "role": (_t or {}).get("role", "") if isinstance(_t, dict) else ""}
            _new_res = pop_delegate_results()
            if _new_res:
                _delegate_active = max(0, _delegate_active - 1)
                for _r in _new_res:
                    yield {"type": "subagent_end",
                           "index": _r.get("index", 0), "ok": bool(_r.get("ok")),
                           "goal": _r.get("goal", ""), "context": _r.get("context", ""),
                           "summary": _r.get("summary", ""), "error": _r.get("error", ""),
                           "steps": _r.get("steps", []), "rounds": _r.get("rounds", 0),
                           "seconds": _r.get("seconds", 0)}
            _inside_delegate = (_delegate_active > 0) or any(p in _delegate_runs for p in _parents)
            if e == "on_chat_model_stream":
                chunk = (ev.get("data") or {}).get("chunk")
                if chunk and getattr(chunk, "content", None):
                    yield {"type": "token", "text": chunk.content}
            elif e == "on_chat_model_end":
                if _inside_delegate:
                    continue
                out = (ev.get("data") or {}).get("output")
                try:
                    if getattr(out, "response_metadata", {}).get("finish_reason") == "length":
                        saw_length = True
                except Exception:
                    pass
                if out is not None and getattr(out, "content", None):
                    final_answer = out.content
            elif e == "on_tool_start":
                data = ev.get("data") or {}
                name = _ev_name(ev, data)
                # 子智能体内部的工具调用：不往主流程抛（收进 subagent_start/end 卡片里）
                if _inside_delegate:
                    continue
                inp = data.get("input")
                try:
                    inp_s = json.dumps(inp, ensure_ascii=False) if inp is not None else ""
                except Exception:
                    inp_s = str(inp)
                yield {"type": "tool_call", "name": name, "input": inp_s[:2000]}
                if name == "delegate_task":
                    _delegate_runs.add(ev.get("run_id"))
            elif e == "on_tool_end":
                data = ev.get("data") or {}
                name = _ev_name(ev, data)
                rid = ev.get("run_id")
                # delegate_task 结束：只需清掉运行 id（子智能体的 subagent_end 已由事件槽统一发出）
                if (rid and rid in _delegate_runs) or name == "delegate_task":
                    _delegate_runs.discard(rid)
                    _delegate_active = max(0, _delegate_active - 1)
                    continue
                if _inside_delegate:
                    continue
                out = data.get("output")
                if not isinstance(out, str):
                    try:
                        out = json.dumps(out, ensure_ascii=False)
                    except Exception:
                        out = str(out)
                yield {"type": "tool_result", "name": name, "content": out[:2000]}

        if isinstance(final_answer, str):
            final_answer = final_answer.strip()
        else:
            final_answer = str(final_answer).strip()
        if not final_answer:
            yield {"type": "final", "text": f"已达最大工具循环步数({cap})，仍未生成最终回答。可尝试精简问题或拆分任务。"}
        else:
            if saw_length:
                final_answer += "\n\n> ⚠️ 检测到模型输出曾因达到最大输出 Token 上限被截断；若结果不完整，请在设置中调大 Token 或让 AI 分多次写入。"
            yield {"type": "final", "text": final_answer}
        # 保存历史 + 触发向量索引
        try:
            msg_id = f"msg-{int(time.time() * 1000)}"
            _save_to_history(branch_id, user_input, final_answer, msg_id)
            _trigger_vector_index()
        except Exception as e:
            print("[lc-stream] save history error:", e)
    except Exception as e:
        import traceback
        yield {"type": "error", "message": f"LangChain Agent 执行失败: {str(e)}", "traceback": traceback.format_exc()}
