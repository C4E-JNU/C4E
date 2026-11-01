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
import contextvars

# ── A0：单次回答内的上下文治理（实现在 agent/context_engine.py）──
from agent.context_engine import (cap_tool_result, prune_tool_results_only,
                                  prune_tool_results_to_target, ContextBudget, estimate_tokens)

# ── 轮次账本（turn ledger）：本轮各步工具结果按顺序落盘，防中断丢失 ──
# 实现在 agent/turn_store.py；只追加，绝不改动 chats.json / history.jsonl（用户 2026-10-02 定）。
from agent import turn_store as _turn_store

# 模型窗口（DeepSeek V4 = 256K，用户铁律：绝不压低，统一 262144）。
# 前端可在 model.context_window 里显式覆盖；这里只是没有该字段时的取值。
DEFAULT_CONTEXT_WINDOW = 262144

# 跨轮压缩（A1 简单版）：滑窗之外的早期轮次压成要点
EARLY_MSG_CHARS = 220        # 早期每条消息摘多少字
EARLY_DIGEST_MAX_CHARS = 6000  # 要点总预算

# ── 当前对话上下文（供工具读取）────────────────────
_current_branch_id = ""
_current_user = ""          # 当前请求身份（""=旧版全局；guest/用户名=按身份分文件）
_current_model_config = {}   # 当前请求的模型配置（子智能体等工具需要读它）


def set_current_model_config(cfg: dict = None):
    global _current_model_config
    _current_model_config = dict(cfg or {})


def get_current_model_config() -> dict:
    return dict(_current_model_config)


# ── 超时保护默认值：只剩一级强制中断（2026-10-13 删催促/警告两级——断点续跑让中断后可
#     继续回答，多级递进失去意义；用户 2026-09-28 定硬停时限 = 一天）──
DEFAULT_TIMEOUTS = {"abort": 86400}

# 子智能体选项（前端设置传入）：轮次 / 并发上限 / 最大子任务数 / 三级超时
_subagent_options = {"max_rounds": 0, "timeout": 86400, "enabled": True,
                     "max_parallel": 8, "max_tasks": 8, "abort": 86400,
                     "feed_steps": False}   # feed_steps=False（默认）：只把子智能体摘要回喂主控，防上下文爆炸


def set_subagent_options(opts: dict = None):
    """设置子智能体选项。opts 里出现的键覆盖默认（含 feed_steps，缺省保持 False）。"""
    global _subagent_options
    o = dict(_subagent_options)
    for k in ("max_rounds", "timeout", "enabled", "max_parallel", "max_tasks", "abort"):
        if opts and opts.get(k) is not None:
            o[k] = opts[k]
    try:
        o["max_rounds"] = max(0, int(o.get("max_rounds", 0)))   # 0 = 无限（用户 2026-09-27：不要锁死 20）
        o["max_parallel"] = max(1, min(8, int(o.get("max_parallel", 8))))     # 并发上限（自由调节 1-8）
        o["max_tasks"] = max(1, min(8, int(o.get("max_tasks", 8))))           # 单次最多子任务数
        o["abort"] = max(0, min(604800, int(o.get("abort", 86400))))  # 唯一一级：强制中断（0=关闭；上限 7 天）
        o["timeout"] = o["abort"]                                             # 旧字段与新终止级保持一致
        o["enabled"] = bool(o.get("enabled", True))
        # feed_steps：False（默认）时子智能体只回喂摘要，不回喂其读取到的正文（防上下文爆炸）
        if opts and opts.get("feed_steps") is not None:
            o["feed_steps"] = bool(opts.get("feed_steps"))
    except Exception:
        pass
    _subagent_options = o


def get_subagent_options() -> dict:
    return dict(_subagent_options)


def _profile_info(name: str) -> dict:
    """子智能体档案的显示信息（前端卡片/浮窗用）。

    用户 2026-10-04：界面上要能看到**专有子智能体的名字**，不能只写"子智能体 1"。
    返回 {profile, profile_title, profile_read_max, profile_rounds, profile_tools}，
    取不到档案时降级为通用标签，绝不抛错（绝不因显示信息影响派发）。
    """
    n = (name or "").strip()
    if not n or n.startswith("__"):
        return {"profile": "", "profile_title": "Generic subagent"}
    out = {"profile": n, "profile_title": n}
    try:
        from agent import profiles as _pf
        p = _pf.get_profile(n)
        if p:
            out["profile_title"] = (p.get("title") or "").strip() or n
            pp = p.get("params") or {}
            try:
                out["profile_read_max"] = int(pp.get("read_file_max_chars") or 0)
            except Exception:
                pass
            if pp.get("max_rounds"):
                out["profile_rounds"] = pp.get("max_rounds")
            tl = p.get("tools")
            if isinstance(tl, list) and tl:
                out["profile_tools"] = len(tl)
            sk = p.get("skills")
            if isinstance(sk, list) and sk:
                out["profile_skills"] = len(sk)
    except Exception:
        pass
    return out


# ── 子智能体事件槽（引擎无关）──────────────────────
# 工具执行时把"派了哪些子任务 / 各自结果"放进来，主循环（custom 与 langchain 都行）取走并流给前端。
# 这样就不依赖 LC 事件里的工具名（实测 astream_events 有时取不到名字）。
# 子智能体的待办/结果：**按请求隔离**。
# 原实现是模块级 list，每次生成入口 clear() 一次 —— 云端并发时 A 的生成会清掉 B 的子智能体
# 待办（真实 bug）。改成 contextvars 持有一份可变对象：请求入口 set 新对象，
# 该请求派生的子任务继承同一份引用（asyncio 子任务共享对象本身），互不干扰。
_delegate_pending = contextvars.ContextVar("delegate_pending", default=None)


def _pending() -> dict:
    p = _delegate_pending.get()
    if p is None:
        p = {"tasks": [], "results": []}
        _delegate_pending.set(p)
    return p


def set_delegate_tasks(tasks: list):
    _pending()["tasks"].extend(tasks or [])


def pop_delegate_tasks() -> list:
    out = list(_pending()["tasks"])
    _pending()["tasks"].clear()
    return out


def set_delegate_results(results: list):
    _pending()["results"].extend(results or [])


def pop_delegate_results() -> list:
    out = list(_pending()["results"])
    _pending()["results"].clear()
    return out


def clear_delegate_pending():
    """本请求新开一份待办/结果（不是去清别人的）。"""
    _delegate_pending.set({"tasks": [], "results": []})


def _resolve_timeouts(t: dict = None) -> dict:
    """超时保护（2026-10-13 起只剩一级）：abort = 强制中断秒数，0/缺省 = 关闭。
    原催促/警告两级已删——断点续跑使中断后可继续回答，多级递进失去意义。"""
    out = dict(DEFAULT_TIMEOUTS)
    if isinstance(t, dict) and t.get("abort") is not None:
        try:
            out["abort"] = max(0, int(t["abort"]))
        except Exception:
            pass
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

# ── 全局「催促回答」令牌（用户 2026-09-27 定、2026-10-13 随按钮改名：点⚡ = 催促马上出答案，不是硬中断）──
#    独立注入通道：把催促提示塞进 messages，下个检查点模型就收尾。硬中断走「立即终止」按钮（/api/agent/stop）。
_IMMEDIATE_ANSWER_REQUESTED = False
_IMMEDIATE_ANSWER_TEXT = ("[System · Answer now] The user asks you to answer now: give the final answer **immediately** from what you have,"
                          "without calling more tools or exploring; the answer may be incomplete but must be output now.")

def reset_immediate_answer():
    """新一轮开始时清除「立即回答」标记。"""
    global _IMMEDIATE_ANSWER_REQUESTED
    _IMMEDIATE_ANSWER_REQUESTED = False

def request_immediate_answer():
    """用户在生成中点⚡立即回答：最近一个检查点注入「立即作答」提示。"""
    global _IMMEDIATE_ANSWER_REQUESTED
    _IMMEDIATE_ANSWER_REQUESTED = True

def immediate_answer_requested() -> bool:
    return _IMMEDIATE_ANSWER_REQUESTED

from agent.tools import tools_registry, get_tools
from tools.neo4j_tools import Neo4jClient
from tools.skill_tools import skill_list as _skill_list
from tools.file_tools import (set_permission_mode, set_current_user as _set_ft_user,
                              set_image_emit, split_image_result as _split_img)

# 项目根 workspace —— 与本项目其余 4 处同类定义对齐。
# 原写法 .parent.parent 只算到 backend/（本文件在 backend/agent/ 下），
# 于是历史日志写进 backend/workspace/，而 vector_search 去项目根 workspace/ 读，
# index_new_history 见文件不存在直接静默 return → 索引恒空、search_history 恒返回 0 条。
_WORKSPACE = Path(__file__).parent.parent.parent / "workspace"


def _user_key(u: str) -> str:
    """身份 → 目录名。规则与 file_tools._user_key / vector_search._user_key_vs 完全一致：
    游客或空 → _guest（与前端用户目录 users/_guest/ 对齐）。"""
    u = (u or "").strip() or "guest"
    if u == "guest":
        return "_guest"
    k = "".join(c for c in u if (c.isalnum() or c in "-_@.·"))
    return k or "_guest"


def _history_file_for(user: str = None) -> Path:
    """按身份归入 users/<key>/history.jsonl —— 同一用户的对话、附件、召回日志共处一个文件夹。
    user=None 用当前上下文（无身份则按游客 _guest）；显式空串=旧版全局（workspace 根，存量归档）。"""
    if user is None:
        return _WORKSPACE / "users" / _user_key(_current_user) / "history.jsonl"
    if user == "":
        return _WORKSPACE / "_history.jsonl"
    return _WORKSPACE / "users" / _user_key(user) / "history.jsonl"


def _save_to_history(branch_id: str, user_input: str, final_answer: str, msg_id: str):
    """保存单次对话到历史文件（追加，按身份归入各自用户目录）"""
    hist_file = _history_file_for()
    hist_file.parent.mkdir(parents=True, exist_ok=True)
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
    u = _current_user or "guest"   # 与写侧 _user_key 对齐：无身份=游客 → users/_guest/
    hist_file = _history_file_for(u)
    def _do_index():
        try:
            from tools.vector_search import index_new_history
            last_id = None
            meta_file = hist_file.parent / "_vector_meta.json"
            if meta_file.exists():
                try:
                    last_id = json.loads(meta_file.read_text(encoding="utf-8")).get("last_indexed_id")
                except Exception:
                    last_id = None
            index_new_history(hist_file.name, last_id, u)
        except Exception as e:
            print(f"[Vector] Index error: {e}")
    threading.Thread(target=_do_index, daemon=True).start()

def _runtime_env_block(enabled_tools=None) -> str:
    """如实交代运行环境：是不是沙箱、能碰哪些路径、哪些工具因账号等级没开。

    判据取客观事实 —— file_tools 里**当前生效**的权限模式与账号作用域，
    以及本次实际下发的工具集；不写死文案，也不含糊其辞。
    （起因：模型曾在没有沙箱的本地实例上自称"我在沙箱里"，于是拒绝操作用户的文件。）
    """
    try:
        from tools.file_tools import get_permission_mode, get_user_scope
        mode, scope = get_permission_mode(), get_user_scope()
    except Exception:
        mode, scope = "safe", None
    tools = set(enabled_tools or [])
    out = ["## Runtime environment (facts; answer from these, do not guess or deflect)"]
    if not scope:
        out.append(
            "- You run directly on the user's own computer, **not in a container, not in a sandbox, with no isolated environment**: "
            "read_file / write_file / list_files / search_files / find_files / edit_file / run_code / terminal "
            "operate on this machine's real filesystem. When the user mentions a file on their computer, use an absolute path "
            "(on Windows e.g. C:/Users/<name>/Documents) to read or write it, "
            "**never refuse with \"I am in a sandbox\" or \"I cannot access your computer\"**.")
        out.append(
            "- If a tool is missing from this session's tool list, the user has simply not enabled it in the Agent settings panel. "
            "Say that the tool is not enabled and can be ticked in Agent settings; never invent environment or sandbox reasons.")
        out.append(
            "- The only boundary is the permission mode `%s`: safe = workspace/ only; ask = read the project and workspace, write workspace and skills/; "
            "full = any read/write. Out-of-range calls return __NEED_FULL__; ask the user to switch to Full access and continue." % mode)
        out.append(
            "- **Do not pre-judge your boundaries or say \"I cannot access your computer\"**: when the user asks you to read or write a path, "
            "**call the tool first** - read or write if allowed. If blocked, the tool reports the reason (e.g. __NEED_FULL__); "
            "relay it and give the next step (switch to Full access). Claiming restrictions from old impressions is wrong.")
    else:
        who = scope.get("user") or "guest"
        out.append(
            "- You run on the group shared server (a multi-user service, not a personal computer): this account (%s) can access "
            "workspace/users/%s/ and the shared library workspace/pool/. This is **account-level data isolation**, "
            "not \"you cannot access any files\"." % (who, who))
        if not ({"terminal", "run_code"} & tools):
            out.append(
                "- This account level does not yet include execution tools (terminal / run_code). When the user asks to run commands, "
                "**say clearly that it is an account-level limit**; do not claim a sandbox and do not pretend you ran anything.")
    return "\n".join(out)


# Language rule: appended at the very end of the system prompt (recency position, highest compliance).
# Constant strings keep the implicit prefix cache stable.
_WORK_AREA_NOTE_TMPL = (
    "## Work area (selected for this conversation)\n"
    "- Project directory: {pd}\n"
    "  Default working directory: relative paths in read_file/write_file resolve here, and the terminal starts here. Put code and final outputs here.\n"
    "- Output directory: {od}\n"
    "  Intermediate, auxiliary and temporary files only: paths starting with output/ land here (e.g. write_file('output/tmp.log')).\n"
    "- For scripts that produce many intermediate files, cd to the output directory first."
)


def _work_area_note(username: str) -> str:
    """Work-area section: tell the agent the real paths of both roots and the output/ convention."""
    try:
        from tools import work_areas as _wa
        a = _wa.current_area(username)
        return _WORK_AREA_NOTE_TMPL.format(pd=a["project_dir"], od=a["output_dir"])
    except Exception:
        return ""


import os as _os_lang

_FORCE_LANG = _os_lang.environ.get("C4EAI_FORCE_LANG", "").strip().lower()

_LANG_DIRECTIVE = (
    "## Language rule (highest priority; overrides every statement above)\n"
    "- Respond and reason ENTIRELY in English, including every reasoning/thinking block, progress note and tool commentary.\n"
    "- Never output Chinese characters in reasoning or in the answer.\n"
) if _FORCE_LANG == "en" else (
    "## Language rule (highest priority; overrides every statement above)\n"
    "- Your **internal reasoning (reasoning, thinking) and final answer should follow the language of the user's message**;\n"
    "- Answer in Chinese when asked in Chinese and in English when asked in English; do not mix languages; "
    "keep technical English terms (catalyst, pollutant, photocatalysis, GraphRAG, DOI, Cypher) unchanged.\n"
    "- Earlier English reasoning examples are history and do not license a language change."
)


async def _build_system_prompt(enabled_tools: list[str], loaded_skills: list[str]) -> str:
    """Hermes-style system prompt: no tool list (the OpenAI tools schema describes tools); skills only name+desc.

    Hermes-style design:
      - Tool behavior guidance lives in each tool's schema description (see tools.py); the prompt does not repeat tool names.
      - Skills are listed as <available_skills> with name+description; details are read on demand via skill_view.
    """
    lines = [
        "You are TruthCross, the C4EAI data-mining agent. You do exactly two things: **the full data-mining pipeline for heterogeneous Fenton literature** and **catalytic degradation knowledge-graph queries**.",
        "Roles:",
        "(1) Pipeline controller: mine figures/tables/text of papers into structured records and load them into the graph (screening -> parsing/archiving -> work orders -> figure mining -> figure-text verification -> 5B adjudication -> node alignment -> schema filling -> numeric normalization -> final inspection -> graph export). The `fenton-multiagent-mining` skill is the only execution standard: follow its references/ step by step and use the skill scripts/ for deterministic IO;",
        "(2) Semantic-judgment orchestrator: dispatch reading, grading and review to dedicated subagents (delegate_task, parallel); do not guess yourself;",
        "(3) Contradiction taxonomy: axis 2 Q1-Q4 (verification findings) and axis 1 A-E (true-contradiction severity, E = other/unclassifiable) are separate; the single source of truth is `references/contradiction_taxonomy.md` in the skill;",
        "(4) Graph query and loading: the Neo4j collaborative knowledge graph (GraphRAG semantic search + Cypher) to query catalysts / pollutants / experiments / contradictions and to load mining results.",
        "Capabilities (trimmed to the tools enabled in this session):",
        ("(1) Graph query: graph_query (schema / cypher / search) - catalysts, pollutants, experimental conditions, contradictions;"
         if "graph_query" in (enabled_tools or []) else
         "(1) Graph query: graph_query is not enabled in this session (Neo4j unreachable) - say honestly that graph questions cannot be answered; never pretend you queried;")
        + ("(2) Full Fenton data-mining pipeline: skill fenton-multiagent-mining + dedicated subagents (screener / workorder / image / verifier / arbiter / node / schema / normalizer / auditor / graph);"
           " after step-9 audit, finish step-10 deliverables: graph_export JSON + tables.xlsx + Neo4j sync & bge vectorization (script step10_graph_export.py / neo4j_stack/step10_graph_sync.py --embed; acceptance by the fenton-graph subagent);"
           "(3) Single-paper contradiction audit: skill literature-contradiction (Q1-Q4 verification + A-E severity);"
           "(4) PDF parsing and archiving: skill mineru-agent-parse + skill scripts step0 / step0c (bbox figure cropping, full-text cleaning);"
           "(5) Scripts and computation: run_code for computation/statistics/plots, terminal for skill scripts/ and installs;"
           "(6) Files and web: read_file / write_file / search_files / find_files / web_search."),
        "When greeted or asked who you are / what you can do: state your name (TruthCross, C4EAI data-mining agent), then list the enabled capabilities above (one per line, at most 8), without pleasantries or generic-assistant framing.",
        "",
        "",
        _runtime_env_block(enabled_tools),
        "## Rules",
        "- **Follow the user's language (including reasoning)**: the final answer and **internal reasoning (reasoning, thinking) should follow the language of the user's message**; do not mix languages; "
        "keep technical English terms (catalyst, pollutant, photocatalysis, GraphRAG, DOI) as they are.",
        "- Use the matching tool directly when a capability is needed (each tool's interface description defines its behavior).",
        "- Choose tools appropriate to the question for graph queries, web search, file parsing and skill reading.",
        "- **Never repeat or quote raw JSON returned by tools**. Tool results are already displayed to the user; give only conclusions and analysis.",
        "- **Keep the final answer concise** without narrating intermediate steps; the user can see which tools ran.",
        "## Discipline",
        "- **Diagnose before switching tactics**: read the error, check your assumption, make one targeted fix, then decide whether to switch. Neither repeat blindly nor give up after one failure.",
        "- **Prefer dedicated tools**: read_file to read (not cat/type), search_files to search (not shell grep/rg), find_files to locate (not recursive ls), edit_file to change one place (no full rewrite); terminal only for system commands and installs.",
        "- **Read before acting**: never propose edits to a file you have not read, and never infer content from a file name.",
        "- **Verify before claiming completion, and report honestly**: say what ran and what did not; do not present unfinished work as done, nor weaken verified results into maybes.",
        "- **Brevity**: keep narration between tool calls under 25 words; the final answer scales with task complexity without dropping conclusions.",
        "- **Files uploaded in this conversation** (the list at the end of the system prompt): if listed, read them directly with read_file(file_id=...) (images too, returned as image blocks); **never say there is no file because the message is old and never ask the user to re-upload**.",
        "- **Converge after tool calls**: after each result decide whether it is enough. If so, answer immediately and stop; **do not call tools pointlessly**. Only call a *different, necessary* tool when key information is missing. After a failure, switch tools at most once; do not retry the same tool repeatedly.",
        "- If a write tool returns `__NEED_FULL__`, the ask mode is active and the operation is out of range; tell the user to switch to Full access and briefly say what you will do.",
        "- **Write output files under `outputs/`** (relative, e.g. `outputs/report.pptx`): with write_file use `outputs/xxx`; "
        "with run_code or skill scripts just use the file name (their working directory is the output area). "
        "Afterwards **call `list_outputs` to get the file_id** and reference it as `{{file:<file_id>:<name>}}` - "
        "the frontend renders an attachment card with Open on backend or Open in frontend (download / in-browser PDF preview). "
        "**Never just say a file was generated without the placeholder**; the user cannot retrieve it otherwise.",
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
    # 当前任务清单（A5）：todo 工具写下的未完成项回灌进系统提示。
    # 放这里而不是三个引擎各自拼 —— 三处调用点行为一致，也避免同一段写三遍。
    from agent.tools import todo_context_block
    _td = todo_context_block()
    if _td:
        lines += [_td, ""]
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
            content = content[:HISTORY_MSG_MAX_CHARS] + "...[history message too long, truncated]"
        if role == "user":
            hist.add_user_message(content)
        elif role == "assistant":
            hist.add_ai_message(content)
    msgs = list(hist.messages)
    # ── 跨轮压缩（简单版）：滑窗之外的消息**不再静默丢弃**，而是压成"早期对话要点" ──
    # 旧实现直接从最早的丢：早期文献事实（第 5 轮说的数据/路径/结论）到第 28 轮就没了，
    # 模型只能反悔式重问。这里改为：最近 context_length 轮保留原文，
    # 更早的压成每条一行（角色 + 前 EARLY_MSG_CHARS 字），整体 ≤ EARLY_DIGEST_MAX_CHARS。
    # 纯机械抽取，不再调一次模型 —— 确定、零成本、不引入新失败面。
    digest = ""
    if context_length and context_length > 0:
        keep_n = context_length * 2
        if len(msgs) > keep_n:
            early = msgs[:-keep_n]
            msgs = msgs[-keep_n:]
            lines, used = [], 0
            for m in reversed(early):                      # 从近到远取，预算不够就先丢最早
                txt = " ".join(str(m.content).split())
                if not txt:
                    continue
                who = "User" if m.__class__.__name__ == "HumanMessage" else "Assistant"
                line = "- [%s] %s" % (who, txt[:EARLY_MSG_CHARS])
                if used + len(line) > EARLY_DIGEST_MAX_CHARS:
                    break
                lines.append(line)
                used += len(line)
            if lines:
                lines.reverse()
                digest = ("[System · early conversation digest] (originals older than the last %d turns are omitted; the excerpts below"
                          "remain valid and citable; do not say something is missing just because the original is not shown)\n%s"
                          % (context_length, "\n".join(lines)))
    # 总量兜底：从最早的开始丢，保证历史字符数在预算内（保留当前 user_input）
    total = sum(len(str(m.content)) for m in msgs) + len(user_input or "") + (len(digest) if digest else 0)
    while msgs and total > HISTORY_TOTAL_MAX_CHARS:
        dropped = msgs.pop(0)
        total -= len(str(dropped.content))
    msgs = msgs + [HumanMessage(content=user_input)]
    if digest:
        # 要点并入**第一条消息**，而不是单独插一条 user 消息 —— 单独插会造成
        # user,user 相邻；kimi 等要求严格交替的模型遇到非交替历史会退化（乱说话、不调用工具）。
        if msgs and isinstance(msgs[0], HumanMessage):
            msgs[0] = HumanMessage(content=str(msgs[0].content) + "\n\n" + digest)
        else:
            msgs = [HumanMessage(content=digest)] + msgs
    msgs = _merge_adjacent_roles(msgs)
    return msgs


def _merge_adjacent_roles(msgs: list) -> list:
    """合并相邻同角色消息，保证发给模型的历史严格 user/assistant 交替。

    空回复被跳过（前面 `if not content: continue`）、投影缺 assistant 的 turn、
    删除提问后留下的孤立回答……都会留下同角色相邻；宽容模型（deepseek）无所谓，
    kimi 这类严格模型会直接退化。合并对任何模型都无害。"""
    out = []
    for m in msgs:
        if out and out[-1].__class__ is m.__class__:
            out[-1] = out[-1].__class__(content=str(out[-1].content) + "\n\n" + str(m.content))
        else:
            out.append(m)
    return out


# ── 文件上下文注入 ────────────────────────────────

async def _build_file_manifest(file_ids: list) -> str:
    """本对话已上传文件的清单 —— 进**系统提示**，每一轮都在。

    为什么放系统提示而不是当轮消息尾部：附件原来只在"当轮用户消息"尾部注入一行引用，
    且前端只收集最近 N 条消息里的附件 —— 更早那轮上传的文件根本进不了请求，
    表现就是"我前面发了文件，下一轮说没有文件"（用户实测）。
    清单按 file_id 排序 = 字节稳定（只在新附件加入时变化），不破坏隐式前缀缓存。
    """
    ids = sorted({f for f in (file_ids or []) if f})
    if not ids:
        return ""
    from tools.file_tools import _resolve_upload, get_upload_text, image_to_base64
    rows = []
    for fid in ids:
        _, meta, _ = _resolve_upload(fid)
        name = (meta or {}).get("name") or fid
        if await image_to_base64(fid):
            rows.append("- [Image] %s (file_id: %s)" % (name, fid))
        else:
            text = await get_upload_text(fid)
            if text:
                rows.append("- [Document] %s (file_id: %s, parsed %d characters)" % (name, fid, len(text)))
            else:
                rows.append("- [Document] %s (file_id: %s, no usable text: for scanned PDFs use mineru_parse)" % (name, fid))
    return ("[Files uploaded in this conversation] - these files are **available for the whole session**;"
            "never say there is no file because the message is old, and never ask the user to re-upload.\n"
            + "\n".join(rows) +
            "\nRead text or images with read_file(file_id=...) (images return image blocks with real pixels); never ask the user for anything already listed.")




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
    permission_mode: str = "safe",
    user: str = "",
    timeouts: dict = None,
    subagent: dict = None,
) -> dict:
    global _current_branch_id
    _current_branch_id = branch_id or ""
    set_permission_mode(permission_mode or "safe")
    set_current_user(user or "")
    from tools import work_areas as _wa
    _wa.set_current_area(_wa.area_for_branch(user or "", branch_id or ""))
    set_image_emit("off")     # 阻塞式入口：工具结果不做图像提升，保守关掉
    set_current_model_config(model_config)
    set_subagent_options(subagent or {})
    _T = _resolve_timeouts(timeouts)
    clear_delegate_pending()
    _t_start = time.time()
    # 1. Neo4j 连接（可选增强：连不上只降级摘掉图谱工具，绝不终止 —— 用户 2026-09-22 定）
    if neo4j_config and neo4j_config.get("uri"):
        if "graph_query" in enabled_tools:
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    enabled_tools = [t for t in enabled_tools
                                     if t != "graph_query"]

    # 2. System Prompt
    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)
    _manifest = await _build_file_manifest(file_ids)
    if _manifest:
        system_prompt = system_prompt + "\n\n" + _manifest
    system_prompt = system_prompt + "\n\n" + _LANG_DIRECTIVE
    _wa_note = _work_area_note(user or "")
    if _wa_note:
        system_prompt = system_prompt + "\n\n" + _wa_note

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
        return {"final_answer": "No tools available", "reasoning_steps": []}

    # 如果没有加载技能，移除 skill_list 和 skill_view 工具
    if not loaded_skills:
        tools = [t for t in tools if t.name not in ("skill_list", "skill_view")]

    # 安全模式下不暴露技能写入工具
    if (permission_mode or "safe") == "safe":
        tools = [t for t in tools if t.name != "skill_write"]

    # 5. Agent（记忆由前端 history 提供）
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

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

        return {"final_answer": final_answer or "The agent produced no answer", "reasoning_steps": reasoning_steps}

    except Exception as e:
        import traceback
        return {"final_answer": f"Agent run failed: {str(e)}", "reasoning_steps": [], "error": str(e), "traceback": traceback.format_exc()}


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
    permission_mode: str = "safe",
    max_rounds: int = 20,
    user: str = "",
    timeouts: dict = None,
    subagent: dict = None,
    client_id: str = "",
    client_msg_id: str = "",
    skip_resume: bool = False,
):
    """真流式 Agent：直接调原始 OpenAI 兼容 API 流，逐 token 抓取。

    Events:
      {"type":"reasoning","text":...}    模型推理 (reasoning_content，推理模型)
      {"type":"token","text":...}        模型消息正文 (content，逐字)
      {"type":"tool_call","name":...,"input":...}
      {"type":"tool_result","name":...,"content":...}
      {"type":"subagent_start","index":i,"goal":...,"total":N}
      {"type":"subagent_end","index":i,"ok":...,"summary":...,"steps":[...],"seconds":...}
      {"type":"timeout_notice","level":"immediate|abort","text":...}
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
    from tools import work_areas as _wa
    _wa.set_current_area(_wa.area_for_branch(user or "", branch_id or ""))
    set_image_emit("blocks")  # custom 引擎：工具返回内容块，由循环提升成 user 图像块
    set_current_model_config(model_config)
    set_subagent_options(subagent or {})
    _T = _resolve_timeouts(timeouts)
    clear_delegate_pending()

    # ── 轮次账本（turn ledger）──────────────────────────────────
    # 设计（用户 2026-10-02 定）：粒度 =「轮次 + 每步工具」，不记逐 token（丢一轮可接受）；
    # 中断即停、**不自动续跑**；下次发消息时由 build_resume_note() 拼出进度摘要注入。
    # 落盘时机：工具结果一到手（yield tool_result 之后）立刻写，不等轮末 ——
    # 否则「第 N 步工具已跑完、结果还没进 messages 就被停止」这一步就丢了。
    _turn_id = _turn_store.new_turn_id()
    _turn_closed = False
    _turn_n = [0]          # 工具步全局计数（旧实现直接用轮号 → 一轮多工具会重号）

    def _turn_finish(status: str, partial: str = "") -> None:
        """收尾本轮。停止/超时/正常/异常多个出口都可能调用，只允许先到的那次生效。"""
        nonlocal _turn_closed
        if _turn_closed:
            return
        _turn_closed = True
        try:
            _turn_store.finalize(user, branch_id, _turn_id, status, partial_text=partial or "")
        except Exception as _e:      # 账本出问题绝不影响正常对话
            print("[turn_store] finalize error:", _e)

    def _turn_step(tool_name: str, tool_args, tool_result, round_no: int = 0) -> None:
        """把一步工具结果写进账本（落全量，不是推给前端的 2k 截断版）。"""
        _turn_n[0] += 1
        try:
            _turn_store.append_step(user, branch_id, _turn_id, _turn_n[0],
                                    tool_name, tool_args, tool_result, round_no=round_no)
        except Exception as _e:
            print("[turn_store] append_step error:", _e)

    def _turn_reasoning(round_no: int, text: str) -> None:
        """本轮推理落盘（投影为 model_reasoning）。"""
        try:
            _turn_store.append_reasoning(user, branch_id, _turn_id, round_no, text)
        except Exception as _e:
            print("[turn_store] append_reasoning error:", _e)

    def _turn_text(round_no: int, text: str) -> None:
        """工具轮的中间正文落盘（投影时作 final 缺失的兜底）。"""
        try:
            _turn_store.append_text(user, branch_id, _turn_id, round_no, text)
        except Exception as _e:
            print("[turn_store] append_text error:", _e)

    def _turn_notice(level: str, text: str, elapsed: int = 0) -> None:
        try:
            _turn_store.append_notice(user, branch_id, _turn_id, level, text, elapsed)
        except Exception as _e:
            print("[turn_store] append_notice error:", _e)

    def _turn_final(text: str) -> None:
        try:
            _turn_store.append_final(user, branch_id, _turn_id, text)
        except Exception as _e:
            print("[turn_store] append_final error:", _e)

    # Neo4j（可选增强：连不上只降级摘掉图谱工具，绝不终止对话 —— 用户 2026-09-22 定）
    # 理由：用户常只想问普通问题/读附件；图谱没连上不该让整个对话失败。
    # 降级后 graph_query 工具本身也返回 {"error":"Neo4j 未连接"}，LLM 自会判断跳过。
    if neo4j_config and neo4j_config.get("uri"):
        if "graph_query" in enabled_tools:
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    _n4_err = cr.get("error", "")
                    enabled_tools = [t for t in enabled_tools
                                     if t != "graph_query"]
                    yield {"type": "reasoning",
                           "text": f"(Graph not connected; skipped graph tools and continued: {_n4_err})\n"}

    # System prompt
    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)
    _manifest = await _build_file_manifest(file_ids)
    if _manifest:
        system_prompt = system_prompt + "\n\n" + _manifest
    system_prompt = system_prompt + "\n\n" + _LANG_DIRECTIVE
    _wa_note = _work_area_note(user or "")
    if _wa_note:
        system_prompt = system_prompt + "\n\n" + _wa_note

    # Tools (LangChain StructuredTool)
    tools = get_tools(enabled_tools)
    if not tools:
        yield {"type": "error", "message": "No tools available"}
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

    # 上下文管理（保留最近轮次）
    ctx = _build_context(history, user_input, context_length)
    # ── 上次中断的进度摘要：仅当上一轮是「被停止」时注入；必须在 begin_turn 之前读 ──
    _resume_note = ""
    try:
        _resume_note = _turn_store.build_resume_note(user, branch_id, skip=skip_resume, current_input=user_input)
    except Exception as _e:
        print("[turn_store] resume note error:", _e)
    messages = [{"role": "system", "content": system_prompt}]
    for m in ctx:
        role = "user" if isinstance(m, HumanMessage) else "assistant"
        messages.append({"role": role, "content": m.content})
    if _resume_note:
        messages.append({"role": "user", "content": _resume_note})
    # 开新轮（顺序关键：必须在 build_resume_note 之后，否则读到的是本轮自己）
    try:
        _turn_store.begin_turn(user, branch_id, _turn_id, user_input, client_id=client_id, client_msg_id=client_msg_id)
    except Exception as _e:
        print("[turn_store] begin_turn error:", _e)

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
    max_tokens = model_config.get("max_tokens") or 32768
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
    # A0：单轮内上下文预算（用 API 真实 usage 校准；触发线 = (窗口 - 输出预留) × 0.75）
    # context_window = hard limit，仅用于计算触发线，绝不据此在本地报错/截断（真的超了交给模型端报错）
    # context_budget = 前端可显式指定的清理触发线；0/缺失 → 自动 (窗口 − 输出预留) × 75%
    _ctx = ContextBudget(window_tokens=int(model_config.get("context_window") or DEFAULT_CONTEXT_WINDOW),
                         max_output_tokens=max_tokens,
                         keep_recent_tokens=int(model_config.get("tool_keep_tokens") or 0),
                         trigger_override=int(model_config.get("context_budget") or 0))
    _overflow_retried = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20, read=300, write=60, pool=20)) as client:
            cap = max_rounds if max_rounds and max_rounds > 0 else 100000  # 0 = 无限
            for _step in range(1, cap + 1):  # 工具循环（步数可配，0=无限）
                if generation_cancelled():
                    _turn_finish("interrupted", all_content)
                    yield {"type": "stopped", "text": "Stopped"}
                    return

                # ── 用户点「催促回答」：注入催促让模型马上收尾（独立于超时保护的通道）──
                if immediate_answer_requested():
                    reset_immediate_answer()
                    messages.append({"role": "user", "content": _IMMEDIATE_ANSWER_TEXT})
                    yield {"type": "timeout_notice", "level": "immediate",
                           "text": "Nudge injected: asking the model to wrap up and answer now.",
                           "elapsed": int(time.time() - _t_start)}
                    _turn_notice("immediate", "Nudge injected: asking the model to wrap up and answer now.",
                                 int(time.time() - _t_start))

                # ── 超时保护：只剩一级强制中断（2026-10-13 删催促/警告两级）──
                _elapsed = time.time() - _t_start
                if _T.get("abort") and _elapsed >= _T["abort"]:
                    yield {"type": "timeout_notice", "level": "abort",
                           "text": f"Reached the abort timeout ({_T['abort']} s, actual {int(_elapsed)} s); forced to end this turn.",
                           "elapsed": int(_elapsed)}
                    _turn_notice("abort", f"Reached the abort timeout ({_T['abort']} s, actual {int(_elapsed)} s); forced to end this turn.",
                                 int(_elapsed))
                    final_answer = all_content or final_answer
                    _turn_final(final_answer)
                    _turn_finish("timeout", final_answer)
                    yield {"type": "final",
                           "text": (final_answer + f"\n\n---\n⏱️ **Timeout**: this turn exceeded {_T['abort']} s and was forced to stop (unfinished exploration was interrupted)."
                                                f"Ask again to let it wrap up, or raise the abort timeout in Settings.") if final_answer
                                   else f"⏱️ **Timeout**: this turn exceeded {_T['abort']} s with no output and was forced to stop."}

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
                        # A0-T0.4：上下文溢出不再"抛一句 error 就结束"。
                        # 判定为溢出 → 清一轮旧工具结果 → 重试一次；仍失败才明确报错，
                        # 并在报错里说清"哪些内容被丢弃/已无处可清"。
                        _low = body.lower()
                        _overflow = resp.status_code in (400, 413, 422) and any(
                            k in _low for k in ("context", "token", "too long", "maximum", "length", "exceed"))
                        if _overflow and not _overflow_retried:
                            _overflow_retried = True
                            _cleared = prune_tool_results_only(
                                messages,
                                keep_recent_tokens=max(4000, _ctx.keep_recent_tokens // 4),
                            )
                            _ctx.record_compress_result(_cleared > 0)
                            yield {"type": "context_pruned", "cleared": _cleared,
                                   "reason": "Model reported context overflow; pruned old tool results and retried once"}
                            if _cleared > 0:
                                continue
                        _hint = ("(Context overflow: all prunable tool results were removed and it still exceeds the window - "
                                 "narrow the task or start a new conversation)") if _overflow else ""
                        yield {"type": "error",
                               "message": f"Model API error {resp.status_code}: {body[:300]}{_hint}"}
                        return
                    async for line in resp.aiter_lines():
                        if generation_cancelled():
                            _turn_finish("interrupted", all_content + cur_content)
                            return
                        # 流式中途的硬终止（长回答不会因为循环顶部检查而失守）
                        if _T.get("abort") and (time.time() - _t_start) >= _T["abort"]:
                            yield {"type": "timeout_notice", "level": "abort",
                                   "text": f"Reached the abort timeout ({_T['abort']} s); streaming stopped.",
                                   "elapsed": int(time.time() - _t_start)}
                            _turn_notice("abort", f"Reached the abort timeout ({_T['abort']} s); streaming stopped.",
                                         int(time.time() - _t_start))
                            _turn_final(all_content + cur_content)
                            _turn_finish("timeout", all_content + cur_content)
                            yield {"type": "final",
                                   "text": (all_content + cur_content) +
                                           f"\n\n---\n⏱️ **Timeout**: generation exceeded {_T['abort']} s; output was interrupted."}
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
                # ★ 账本：本轮推理落盘；工具轮的中间正文本轮也落一次（投影为 model_reasoning）
                _turn_reasoning(_step, cur_reasoning)
                if tool_acc:
                    _turn_text(_step, cur_content)

                # 输出被 max_tokens 截断且本轮还有工具调用没生成完：参数 JSON 必然不完整，
                # 与其静默执行残缺参数（写入残缺笔记等），不如明确告知并终止
                if round_finish == "length" and tool_acc:
                    yield {"type": "error", "message":
                           "Model output was truncated at the max output token limit, so the tool arguments are incomplete."
                           "Raise the max output tokens in Settings, or have the AI write in several passes (e.g. split notes / append)."}
                    return

                # 本轮结束
                if not tool_acc:
                    # 若本轮无正文（正文出现在工具轮），回退到累积正文，避免最终答案丢失
                    answer_text = cur_content.strip() if cur_content.strip() else all_content
                    final_answer = _clean_final(answer_text)
                    if round_finish == "length" and final_answer:
                        final_answer += "\n\n> ⚠️ The answer was truncated at the max output token limit; click Continue to extend it."
                    _turn_final(final_answer or "The agent produced no answer")
                    _turn_finish("complete", final_answer)
                    yield {"type": "final", "text": final_answer or "The agent produced no answer"}
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
                pending_images = []          # 本轮工具产出的图片（image_read 通道）
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
                                _pi = _profile_info((_t.get("profile", "") if isinstance(_t, dict) else "")
                                                    or (_t.get("role", "") if isinstance(_t, dict) else ""))
                                yield {"type": "subagent_start", "index": _i, "total": len(_valid),
                                       "goal": _t.get("goal", ""), "context": _t.get("context", ""),
                                       "role": (_t.get("role", "") if isinstance(_t, dict) else ""),
                                       **_pi}
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
                                timeouts={"abort": _o.get("abort")},
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
                                _sa_results = [{"index": i, "ok": False, "error": "Stopped",
                                                "goal": _valid[i].get("goal", "")} for i in range(len(_valid))]
                            except Exception as _e:
                                _sa_results = [{"index": i, "ok": False, "error": f"Subagent error: {_e}",
                                                "goal": _valid[i].get("goal", "")} for i in range(len(_valid))]
                            for _r in _sa_results:
                                yield {"type": "subagent_end",
                                       "index": _r.get("index", 0), "ok": bool(_r.get("ok")),
                                       "goal": _r.get("goal", ""), "context": _r.get("context", ""),
                                       "summary": _r.get("summary", ""), "error": _r.get("error", ""),
                                       "steps": _r.get("steps", []), "rounds": _r.get("rounds", 0),
                                       "seconds": _r.get("seconds", 0),
                                       "status": _r.get("status", ""),
                                       "missing": _r.get("missing", ""),
                                       "confidence": _r.get("confidence", ""),
                                       **_profile_info(_r.get("profile", ""))}
                            from agent.tools import slim_subagent_results as _slim
                            result = json.dumps({"status": "ok", "count": len(_sa_results),
                                                 "results": _slim(_sa_results)}, ensure_ascii=False)
                    elif not args_ok:
                        result = json.dumps({"error": f"Arguments for tool {name} are not valid JSON (possibly truncated by the output limit). Call it again with simpler arguments."}, ensure_ascii=False)
                    else:
                        tool = tool_map.get(name)
                        if tool is None:
                            result = json.dumps({"error": f"Tool {name} does not exist"})
                        else:
                            try:
                                res = await tool.ainvoke(args)
                                # 结构化内容块（图像通道）原样保留；其余序列化成 JSON 文本
                                result = res if isinstance(res, (str, list)) else json.dumps(res, ensure_ascii=False)
                            except Exception as e:
                                result = json.dumps({"error": f"Tool execution failed: {str(e)}"})
                    # ── image_read：图提升为独立的 user 图像块，工具消息里只留文字桩 ──
                    # （OpenAI 系 role=tool 只收字符串，base64 留在 tool 消息里等于喂 token 乱码）
                    _img_u, _img_txt = _split_img(result)
                    if _img_u:
                        if len(pending_images) < 3:      # 单步最多 3 张，防上下文爆炸
                            pending_images.append(_img_u)
                        result = json.dumps({"ok": True, "mode": "image",
                                             "note": "The image is attached as an image block; inspect the pixels directly",
                                             "meta": _img_txt},
                                            ensure_ascii=False)
                    elif isinstance(result, list):
                        result = json.dumps(result, ensure_ascii=False, default=str)
                    yield {"type": "tool_result", "name": name, "content": str(result)[:2000]}
                    # ★ 落盘点：工具结果已在手 → 立刻按顺序写进账本（「第 N 步已跑完」就在这里保住）
                    _turn_step(name, args, result, round_no=_step)
                    if generation_cancelled():
                        _turn_finish("interrupted", all_content + cur_content)
                        yield {"type": "stopped", "text": "Stopped"}
                        return
                    call_id = tc["id"] or f"call_{_step}_{len(assistant_tc)}"
                    assistant_tc.append({
                        "id": call_id, "type": "function",
                        "function": {"name": name, "arguments": args_str},
                    })
                    # A0-T0.1：结果按工具上限封顶；截断时在文本里显式声明并给出续读出路（绝不静默）
                    tool_messages.append({"role": "tool", "tool_call_id": call_id,
                                          "content": cap_tool_result(name, result)})
                # 追加本轮 assistant(含 tool_calls) + 各 tool 结果，进入下一轮
                messages.append({"role": "assistant", "content": cur_content, "tool_calls": assistant_tc})
                messages.extend(tool_messages)

                # ── A0-T0.2/T0.3：单轮内清理旧的工具结果 ──
                # 拿 API 真实 prompt_tokens 与粗估的较大值作为依据；超阈值就把白名单内
                # （模型能自己重新取回的那些）旧工具结果原地换成占位符。
                # **绝不删除消息**：删掉一条 tool 消息就留下孤儿 tool_call，协议层直接报错。
                _pt = max(_ctx.last_prompt_tokens, estimate_tokens(messages))
                _need, _why = _ctx.should_compress_info(_pt)
                if _need:
                    # 按 token 目标从最早的工具结果开始清，清到回落到目标线为止；
                    # 不按“保留最近 N 条”处理，也不在未超线时做无谓清理。
                    _target = max(_ctx.keep_recent_tokens, int(_ctx.trigger_tokens * 0.75))
                    _cleared = prune_tool_results_to_target(
                        messages, _target, keep_recent_tokens=_ctx.keep_recent_tokens)
                    _ctx.pruned_total += _cleared
                    if _cleared:
                        yield {"type": "context_pruned", "cleared": _cleared,
                               "tokens_est": _pt, "reason": _why}

                if pending_images:
                    _vis = [{"type": "text", "text": "[System] The previous tool returned the following images; inspect the pixels directly:"}]
                    _vis += [{"type": "image_url", "image_url": {"url": _u}} for _u in pending_images]
                    messages.append({"role": "user", "content": _vis})

        if not final_answer:
            cap = max_rounds if max_rounds and max_rounds > 0 else 100000
            # 步数耗尽 ≠ 没有信息：每一轮的工具结果都还在 messages 里。
            # 旧实现在这里只吐一句「已达最大步数」就结束，把已经跑出来的探索全部丢掉，
            # 用户看到的就是「忙了半天什么也没给」。改成再要一次【不带 tools 字段】的请求：
            # 协议层面它无法再发起工具调用，只能就已有信息收敛出结论。
            yield {"type": "timeout_notice", "level": "cap",
                   "text": f"Reached the tool-call limit ({cap}); wrapping up from the information gathered.",
                   "elapsed": int(time.time() - _t_start)}
            _turn_notice("cap", f"Reached the tool-call limit ({cap}); wrapping up from the information gathered.",
                         int(time.time() - _t_start))
            # 强制收尾前只在确实超出触发线时清理，且按 token 目标从最早的工具结果开始清；
            # 达到循环上限本身不是清理理由，未超线就原样进入收尾。
            if estimate_tokens(messages) > _ctx.trigger_tokens:
                _cleared_cap = prune_tool_results_to_target(
                    messages, _ctx.trigger_tokens, keep_recent_tokens=_ctx.keep_recent_tokens)
                if _cleared_cap:
                    _ctx.pruned_total += _cleared_cap
                    yield {"type": "context_pruned", "cleared": _cleared_cap,
                           "reason": "Tool-call limit reached and context budget exceeded; pruned older tool results to the token target before wrapping up"}
            forced = ""
            try:
                _fmsg = messages + [{"role": "user", "content":
                    "[System · step limit] The tool budget is exhausted; you **cannot call any more tools** in this answer."
                    "Answer only from the information above:"
                    "(1) state the conclusion; (2) list the key evidence (file paths / data sources);"
                    "(3) state clearly what is still unknown and what to do next."}]
                _fpayload = {"model": model, "messages": _fmsg,
                             "temperature": temperature, "max_tokens": max_tokens,
                             "stream": True}
                async with client.stream("POST", endpoint, json=_fpayload, headers=headers) as _fr:
                    if _fr.status_code == 200:
                        async for _line in _fr.aiter_lines():
                            if generation_cancelled():
                                _turn_finish("interrupted", all_content + forced)
                                break
                            if not _line or not _line.startswith("data: "):
                                continue
                            _data = _line[6:].strip()
                            if _data == "[DONE]":
                                break
                            try:
                                _ev = json.loads(_data)
                            except Exception:
                                continue
                            _ch = (_ev.get("choices") or [{}])[0]
                            _delta = _ch.get("delta") or {}
                            _rc = _delta.get("reasoning_content")
                            _ct = _delta.get("content")
                            if _rc:
                                yield {"type": "reasoning", "text": _rc}
                            if _ct:
                                forced += _ct
                                yield {"type": "token", "text": _ct}
            except Exception:
                forced = ""
            if not forced.strip():
                forced = all_content  # 兜底：把前面各轮已写出的正文还给用户，别让它凭空消失
            final_answer = _clean_final(forced)
            _turn_final(final_answer)
            _tail = (f"\n\n---\n⏳ **Step limit**: the tool loop reached {cap} steps; the conclusions above are from the"
                     "information gathered so far and may be incomplete. Ask again to continue,"
                     "or raise the maximum tool steps in Settings.")
            _turn_finish("complete", final_answer)
            yield {"type": "final", "text": (final_answer + _tail) if final_answer.strip() else (
                f"Reached the tool-call limit ({cap}) with nothing to summarize."
                "Try simplifying the question or splitting the task.")}

    except Exception as e:
        import traceback
        _turn_finish("error", all_content)
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
    permission_mode: str = "safe",
    max_rounds: int = 20,
    user: str = "",
    timeouts: dict = None,
    subagent: dict = None,
    client_id: str = "",
    client_msg_id: str = "",
    skip_resume: bool = False,
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
    from tools import work_areas as _wa
    _wa.set_current_area(_wa.area_for_branch(user or "", branch_id or ""))
    # 图像通道：LangChain 原生支持 list[dict] 内容块经 ToolMessage 直达 wire
    # （tool 角色带 image_url，见 langchain_openai._sanitize_chat_completions_content），
    # 故默认 "blocks" 走原生；若所用端点拒收 tool 角色图像（400），
    # 可设环境变量 C4EAI_LC_IMAGE_MODE=off 关闭通道后重启，不做 middleware 绕行。
    import os as _os
    _lc_img = (_os.environ.get("C4EAI_LC_IMAGE_MODE") or "blocks").strip().lower()
    set_image_emit(_lc_img if _lc_img in ("blocks", "off") else "blocks")
    set_current_model_config(model_config)
    set_subagent_options(subagent or {})
    _T = _resolve_timeouts(timeouts)
    clear_delegate_pending()
    _t_start = time.time()

    # ── 轮次账本（turn ledger）—— 与 custom 引擎同款，两引擎行为一致 ──
    _turn_id = _turn_store.new_turn_id()
    _turn_closed = False
    _turn_n = [0]          # 工具步计数（LangChain 事件流没有步号，自己数）
    _lc_args = {}          # 工具名 → 最近一次调用参数（on_tool_end 拿不到 input）

    def _turn_finish(status: str, partial: str = "") -> None:
        nonlocal _turn_closed
        if _turn_closed:
            return
        _turn_closed = True
        try:
            _turn_store.finalize(user, branch_id, _turn_id, status, partial_text=partial or "")
        except Exception as _e:
            print("[turn_store] finalize error:", _e)

    def _turn_step(tool_name: str, tool_args, tool_result, round_no: int = 0) -> None:
        _turn_n[0] += 1
        try:
            _turn_store.append_step(user, branch_id, _turn_id, _turn_n[0],
                                    tool_name, tool_args, tool_result, round_no=round_no)
        except Exception as _e:
            print("[turn_store] append_step error:", _e)

    def _turn_text(round_no: int, text: str) -> None:
        try:
            _turn_store.append_text(user, branch_id, _turn_id, round_no, text)
        except Exception as _e:
            print("[turn_store] append_text error:", _e)

    def _turn_notice(level: str, text: str, elapsed: int = 0) -> None:
        try:
            _turn_store.append_notice(user, branch_id, _turn_id, level, text, elapsed)
        except Exception as _e:
            print("[turn_store] append_notice error:", _e)

    def _turn_final(text: str) -> None:
        try:
            _turn_store.append_final(user, branch_id, _turn_id, text)
        except Exception as _e:
            print("[turn_store] append_final error:", _e)

    # Neo4j（可选增强：连不上只降级摘掉图谱工具，绝不终止对话 —— 用户 2026-09-22 定）
    # 理由：用户常只想问普通问题/读附件；图谱没连上不该让整个对话失败。
    # 降级后 graph_query 工具本身也返回 {"error":"Neo4j 未连接"}，LLM 自会判断跳过。
    if neo4j_config and neo4j_config.get("uri"):
        if "graph_query" in enabled_tools:
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    _n4_err = cr.get("error", "")
                    enabled_tools = [t for t in enabled_tools
                                     if t != "graph_query"]
                    yield {"type": "reasoning",
                           "text": f"(Graph not connected; skipped graph tools and continued: {_n4_err})\n"}

    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)
    _manifest = await _build_file_manifest(file_ids)
    if _manifest:
        system_prompt = system_prompt + "\n\n" + _manifest
    system_prompt = system_prompt + "\n\n" + _LANG_DIRECTIVE
    _wa_note = _work_area_note(user or "")
    if _wa_note:
        system_prompt = system_prompt + "\n\n" + _wa_note

    tools = get_tools(enabled_tools)
    if not tools:
        yield {"type": "error", "message": "No tools available"}
        return
    if not loaded_skills:
        tools = [t for t in tools if t.name not in ("skill_list", "skill_view")]
    if (permission_mode or "safe") == "safe":
        tools = [t for t in tools if t.name != "skill_write"]

    messages = _build_context(history, user_input, context_length)
    # 上次中断的进度摘要（须在 begin_turn 之前读，否则读到本轮自己）
    _resume_note = ""
    try:
        _resume_note = _turn_store.build_resume_note(user, branch_id, skip=skip_resume, current_input=user_input)
    except Exception as _e:
        print("[turn_store] resume note error:", _e)
    if _resume_note:
        messages = list(messages) + [HumanMessage(content=_resume_note)]
    try:
        _turn_store.begin_turn(user, branch_id, _turn_id, user_input, client_id=client_id, client_msg_id=client_msg_id)
    except Exception as _e:
        print("[turn_store] begin_turn error:", _e)

    llm = ChatOpenAI(
        model=model_config.get("model", "deepseek-chat"),
        api_key=model_config.get("api_key") or "sk-noauth",  # 免密钥本地 llama-server 占位
        base_url=model_config.get("base_url", ""),
        temperature=model_config.get("temperature", 0.7),
        max_tokens=model_config.get("max_tokens") or 32768,
    )
    # 上下文护栏：与 custom 引擎的 prune/cap 策略对齐（lc 路径 2026-10-14 前无治理）
    from agent.lc_middleware import make_context_guard
    _lc_guard = make_context_guard(
        int(model_config.get("context_window") or 200000),
        keep_recent_tokens=int(model_config.get("tool_keep_tokens") or 0),
    )
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt,
                         middleware=[_lc_guard])

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
                _turn_final(final_answer)
                _turn_finish("interrupted", final_answer)
                yield {"type": "stopped", "text": "Stopped"}
                return
            # 用户点「⚡立即回答」：LangChain 图内无法安全注入提示 → 直接用已有内容收尾作答
            if immediate_answer_requested():
                reset_immediate_answer()
                yield {"type": "timeout_notice", "level": "immediate",
                       "text": "Wrapped up for Answer now (the LangChain engine cannot inject mid-run prompts; answering from what exists)."}
                _turn_notice("immediate", "Wrapped up for Answer now (the LangChain engine cannot inject mid-run prompts; answering from what exists).")
                _turn_final(final_answer)
                _turn_finish("complete", final_answer)
                yield {"type": "final",
                       "text": (final_answer.strip() + "\n\n---\n⚡ **Answer now**: wrapped up early as requested; the above is what exists.")
                               if final_answer.strip() else "⚡ **Answer now**: wrapped up as requested, but nothing was produced this turn; ask again to continue."}
                return
            # 超时终止（LangChain 引擎：图内无法安全注入催促/警告提示，故只做硬终止级保护）
            if _T.get("abort") and (time.time() - _t_start) >= _T["abort"]:
                yield {"type": "timeout_notice", "level": "abort",
                       "text": f"Reached the abort timeout ({_T['abort']} s); forced to end this turn (the LangChain engine supports only the abort level).",
                       "elapsed": int(time.time() - _t_start)}
                _turn_notice("abort", f"Reached the abort timeout ({_T['abort']} s); forced to end this turn (the LangChain engine supports only the abort level).",
                             int(time.time() - _t_start))
                _turn_final(final_answer)
                _turn_finish("timeout", final_answer)
                yield {"type": "final",
                       "text": (final_answer.strip() + f"\n\n---\n⏱️ **Timeout**: generation exceeded {_T['abort']} s and was forced to stop.")
                               if final_answer.strip() else f"⏱️ **Timeout**: exceeded {_T['abort']} s with no output; forced to stop."}
                return
            e = ev.get("event")
            _parents = ev.get("parent_ids") or []
            # ── 子智能体事件：从事件槽取（引擎无关，不依赖 LC 事件里的工具名）──
            _new_tasks = pop_delegate_tasks()
            if _new_tasks:
                _delegate_active += 1
                for _i, _t in enumerate(_new_tasks):
                    _pi = _profile_info(((_t or {}).get("profile", "") if isinstance(_t, dict) else "")
                                        or ((_t or {}).get("role", "") if isinstance(_t, dict) else ""))
                    yield {"type": "subagent_start", "index": _i, "total": len(_new_tasks),
                           "goal": (_t or {}).get("goal", "") if isinstance(_t, dict) else str(_t),
                           "context": (_t or {}).get("context", "") if isinstance(_t, dict) else "",
                           "role": (_t or {}).get("role", "") if isinstance(_t, dict) else "",
                           **_pi}
            _new_res = pop_delegate_results()
            if _new_res:
                _delegate_active = max(0, _delegate_active - 1)
                for _r in _new_res:
                    yield {"type": "subagent_end",
                           "index": _r.get("index", 0), "ok": bool(_r.get("ok")),
                           "goal": _r.get("goal", ""), "context": _r.get("context", ""),
                           "summary": _r.get("summary", ""), "error": _r.get("error", ""),
                           "steps": _r.get("steps", []), "rounds": _r.get("rounds", 0),
                           "seconds": _r.get("seconds", 0),
                           "status": _r.get("status", ""), "missing": _r.get("missing", ""),
                           "confidence": _r.get("confidence", ""),
                           **_profile_info(_r.get("profile", ""))}
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
                _lc_args[name] = inp_s
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
                # 工具产出里若含图像块：只给前端发摘要，绝不让 base64 进事件流
                _u, _t = _split_img(out.content if hasattr(out, "content") else out)
                if _u:
                    out = json.dumps({"ok": True, "mode": "image",
                                      "note": "Images were sent to the model with the tool message (native LangChain image_url blocks)",
                                      "meta": _t}, ensure_ascii=False)
                elif not isinstance(out, str):
                    try:
                        out = json.dumps(out, ensure_ascii=False, default=str)
                    except Exception:
                        out = str(out)
                yield {"type": "tool_result", "name": name, "content": out[:2000]}
                # ★ 落盘点：LC 事件同样在结果一到手就写账本
                _turn_step(name, _lc_args.pop(name, None), out)

        if isinstance(final_answer, str):
            final_answer = final_answer.strip()
        else:
            final_answer = str(final_answer).strip()
        if not final_answer:
            # LangChain 引擎的 final_answer 是逐步累加各轮 AIMessage 正文的，
            # 走到这里说明确实一个字都没产出；措辞与 custom 引擎保持一致，
            # 告诉用户怎么办，而不是只报"失败了"。
            _turn_finish("complete", "")
            yield {"type": "final", "text":
                   f"⏳ **Step limit**: the tool loop reached {cap} steps without producing displayable text."
                   "Ask again to answer from existing information, or raise the maximum tool steps in Settings;"
                   "The LangChain engine is request-oriented; use the custom engine for long tasks."}
        else:
            if saw_length:
                final_answer += "\n\n> ⚠️ Model output was truncated at the max output tokens; if incomplete, raise the limit in Settings or have the AI write in several passes."
            _turn_final(final_answer)
            _turn_finish("complete", final_answer)
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
        _turn_finish("error", final_answer if isinstance(final_answer, str) else "")
        yield {"type": "error", "message": f"LangChain agent failed: {str(e)}", "traceback": traceback.format_exc()}
