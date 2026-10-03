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
                                  ContextBudget, estimate_tokens)

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


# ── 超时三级（催促 / 警告 / 终止）默认值，可被请求参数覆盖 ──
DEFAULT_TIMEOUTS = {"nudge": 0, "warn": 0, "abort": 86400}   # 用户 2026-09-28：只剩「一天硬停」

# 子智能体选项（前端设置传入）：轮次 / 并发上限 / 最大子任务数 / 三级超时
_subagent_options = {"max_rounds": 0, "timeout": 86400, "enabled": True,
                     "max_parallel": 8, "max_tasks": 8,
                     "nudge": 0, "warn": 0, "abort": 86400,
                     "feed_steps": False}   # feed_steps=False（默认）：只把子智能体摘要回喂主控，防上下文爆炸


def set_subagent_options(opts: dict = None):
    """设置子智能体选项。opts 里出现的键覆盖默认（含 feed_steps，缺省保持 False）。"""
    global _subagent_options
    o = dict(_subagent_options)
    for k in ("max_rounds", "timeout", "enabled", "max_parallel", "max_tasks",
              "nudge", "warn", "abort"):
        if opts and opts.get(k) is not None:
            o[k] = opts[k]
    try:
        o["max_rounds"] = max(0, int(o.get("max_rounds", 0)))   # 0 = 无限（用户 2026-09-27：不要锁死 20）
        o["max_parallel"] = max(1, min(8, int(o.get("max_parallel", 8))))     # 并发上限（自由调节 1-8）
        o["max_tasks"] = max(1, min(8, int(o.get("max_tasks", 8))))           # 单次最多子任务数
        o["nudge"] = max(0, min(604800, int(o.get("nudge", 0))))     # 0=关闭该级；上限 7 天
        o["warn"] = max(0, min(604800, int(o.get("warn", 0))))
        o["abort"] = max(0, min(604800, int(o.get("abort", 86400))))  # 用户 2026-09-28：超时保护 = 一天
        o["timeout"] = o["abort"]                                             # 旧字段与新终止级保持一致
        o["enabled"] = bool(o.get("enabled", True))
        # feed_steps：False（默认）时子智能体只回喂摘要，不回喂其读取到的正文（防上下文爆炸）
        if opts and opts.get("feed_steps") is not None:
            o["feed_steps"] = bool(opts.get("feed_steps"))
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

# ── 全局「立即回答」令牌（用户 2026-09-27 定：生成中点⚡立即回答 = 催促它马上出答案，不是硬中断）──
#    与超时保护的「警告级」走同一条注入通道：把催促提示塞进 messages，下个检查点模型就收尾。
_IMMEDIATE_ANSWER_REQUESTED = False
_IMMEDIATE_ANSWER_TEXT = ("[系统·立即回答] 用户要求你现在就作答：**立即**基于已获得的信息给出最终答案，"
                          "不要再调用任何工具、不要继续探索；答案可以不完整，但必须马上输出。")

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
    out = ["## 运行环境（事实，照此回答，别猜、别推辞）"]
    if not scope:
        out.append(
            "- 你直接跑在用户自己的这台电脑上，**不是容器、不是沙箱、没有隔离环境**："
            "read_file / write_file / list_files / search_files / find_files / edit_file / run_code / terminal "
            "操作的就是这台机器真实的文件系统。用户提到自己电脑上的文件时，直接用系统绝对路径"
            "（Windows 形如 C:/Users/<用户名>/Documents）去读去写，"
            "**绝不要以「我在沙箱中」「我无法访问你的电脑」为由拒绝**。")
        out.append(
            "- 若某个工具不在你本次的工具列表里：本机实例的工具是用户在「智能体配置」面板里勾选的。"
            "如实说明「该工具当前未启用，可在智能体配置里勾上」，不要编造"+"「环境限制/沙箱」这类理由。")
        out.append(
            "- 唯一的边界是权限模式 `%s`：safe = 仅 workspace/；ask = 可读项目与 workspace、可写 workspace 与 skills/；"
            "full = 任意读写。越界时工具会返回 __NEED_FULL__，那时请用户切到「完全访问」后继续即可。" % mode)
        out.append(
            "- **不要预判自己的边界，也别说「我无法访问你的电脑」这种话**：用户让你读/写某个路径，"
            "**先直接调工具去试**——能读就读、能写就写；真被拦住时，工具会告诉你具体原因（如 __NEED_FULL__），"
            "那时再如实转述并给出下一步（切「完全访问」）。凭旧印象宣称自己受限是错误行为。")
    else:
        who = scope.get("user") or "guest"
        out.append(
            "- 你跑在组内共享服务器上（多用户服务，不是个人电脑）：本账号（%s）可访问的范围 = "
            "workspace/users/%s/ 与共享库 workspace/pool/。这是**账号级数据隔离**，"
            "不等于「你无法访问任何文件」。" % (who, who))
        if not ({"terminal", "run_code"} & tools):
            out.append(
                "- 本账号等级尚未开放执行类工具（terminal / run_code）。用户要求执行命令时，"
                "**如实说明是账号等级限制**，不要说「我在沙箱里无法执行」，也不要假装执行过。")
    return "\n".join(out)


# 语言铁律：拼在系统提示【最末】（recency 位，服从度最高）——确保 reasoning/thinking 也走中文。
# 常量字符串，字节稳定，不破坏隐式前缀缓存。
_WORK_AREA_NOTE_TMPL = (
    "## 工作区域（本对话指定）\n"
    "- 项目目录：{pd}\n"
    "  默认工作区：read_file/write_file 的相对路径都以它为基准，终端默认目录也是它。代码与正式产出写这里。\n"
    "- 产物目录：{od}\n"
    "  只收中间产物/辅助/临时文件：写路径以 output/ 开头即落在这里（例：write_file('output/tmp.log')）。\n"
    "- 跑会产生大量中间文件的脚本时，先 cd 到产物目录再跑。"
)


def _work_area_note(username: str) -> str:
    """工作区域一节：让智能体知道两个根的实际路径与 output/ 约定。"""
    try:
        from tools import work_areas as _wa
        a = _wa.current_area(username)
        return _WORK_AREA_NOTE_TMPL.format(pd=a["project_dir"], od=a["output_dir"])
    except Exception:
        return ""


_LANG_DIRECTIVE = (
    "## 语言铁律（最高优先级，优先于本条以上任何表述）\n"
    "- 你的**内部思考 / 推理过程（reasoning、thinking）与最终回答都必须用中文书写**。\n"
    "- 不要用英文思考再译回中文，不要中英夹杂、不要整段英文推理；"
    "专业英文术语（catalyst、pollutant、photocatalysis、GraphRAG、DOI、Cypher 等）保留原样。\n"
    "- 若上文出现过英文思考示例，那是历史记录，不构成对你的语言授权。"
)


async def _build_system_prompt(enabled_tools: list[str], loaded_skills: list[str]) -> str:
    """Hermes 式系统提示词：不列出工具清单（工具能力靠 OpenAI tools schema），技能只简列 name+desc。

    与 Hermes 一致的设计：
      - 工具能力/行为指引由每个工具的 schema description 提供（见 tools.py），系统提示词不重复工具名。
      - 技能以 <available_skills> 形式简列勾选技能的 name+description；详情按需用 skill_view 读取（不漏全文）。
    """
    lines = [
        "你叫 Quine（奎因），全称 C4EAI Quine Agent ——实验室通用智能体（以 computer science / AI 为底色，服务组内科研协作）。四重定位：",
        "① 通用智能体：文件、代码、数据、网络皆可操作，什么任务都能接；",
        "② 自定义智能体：可创建/修改自己的技能、工具与代码（改动前先备份，改动后需重启后端才生效）；",
        "③ 科研辅助智能体：文献解析与全文中译、图账/表账/段落账三轨道数据对账、把对话笔记整理成规范文献笔记；",
        "④ 组内共享 wiki 编辑：组内条目与分组的读写维护（读用 shared_search；写用 conversation_notes 且 scope 必须为 database，切勿写进私人笔记）。",
        "核心能力：",
        ("① 图谱检索：Neo4j 协作知识图谱 + GraphRAG（graph_query：schema / cypher / search）；"
         if "graph_query" in (enabled_tools or []) else
         "① 图谱检索：本会话图谱未连接（Neo4j 不可用，graph_query 不在本次工具列表里）——"
         "涉及图谱的问题请如实说明暂时查不了，不要谎称已查询；")
        + ("② 多智能体协作：delegate_task 派发专有子智能体（矛盾核查/数据提取/数据核查）并行处理；"
           "③ 数据分析与代码：run_code 计算/统计/绘图，terminal 执行命令；"
           "④ 共享数据库：组内文献/笔记/文档的上传与语义检索（shared_upload / shared_search）；"
           "⑤ RAG 检索增强：本地知识库与历史对话检索；"
           "⑥ 高阶文献翻译：MinerU 解析归档 → 全文中译、图文并茂写笔记；"
           "⑦ 文献内容审查：图账/表账/段落账三轨道数据对账与矛盾裁决；"
           "⑧ 联网搜索与文件处理：web_search、read/write_file。"),
        "被问候或问「你是谁/能做什么」时：先报名字（Quine / 奎因，全称 C4EAI Quine Agent），再按用户已启用的工具从上面列举核心能力（一行一项，不超过 8 项），不要空泛客套。",
        "",
        "",
        _runtime_env_block(enabled_tools),
        "## 规则",
        "- **全程中文（含思考）**：最终回答与**内部思考/推理过程（reasoning、thinking）都必须用中文**书写，不要中英夹杂、不要整段用英文思考再翻回中文；"
        "专业英文术语（如 catalyst、pollutant、photocatalysis、GraphRAG、DOI）保留原样即可，不必翻译。",
        "- 用中文回答，保留专业英文术语",
        "- 需要哪些能力时，直接使用对应工具（工具的能力与用法由各工具的接口说明给出）。",
        "- 图谱查询、联网搜索、文件解析、技能读取等，按用户问题需求选择合适的工具。",
        "- **不要在回答中重复或引用工具返回的原始 JSON 数据**。工具执行结果已被系统自动展示给用户，你在回答中只给出结论和分析。",
        "- **最终回答简洁明了**，不要包含中间步骤的描述。用户能看到你调用了哪些工具。",
        "## 纪律（照着做，别打折扣）",
        "- **先诊断再换招**：工具报错时先读错误信息、核对自己的假设、做一次针对性修正，再决定是否换别的办法。既不要盲目重复同一个调用，也不要一次失败就放弃可行的路子。",
        "- **专用工具优先**：读文件用 read_file（不要 cat/type）、搜内容用 search_files（不要 shell 里 grep/rg）、找文件用 find_files（不要 ls 递归猜路径）、改文件用 edit_file（改一处不必整篇重写）；terminal 只用于系统命令与装包。",
        "- **动手前先读**：没读过的文件不要提改法，也不要凭文件名猜内容。",
        "- **完成前必须验证，并如实汇报**：跑过的就说跑过，没跑的就明说没跑；不要把未完成说成完成，也不要把已验证通过的过度弱化成「可能可以」。",
        "- **长度定量**：工具调用之间的旁白不超过 25 个词；最终回答按任务复杂度写，不要为了短丢掉结论。",
        "- **本对话已上传的文件**（系统提示末尾那段清单）：只要在清单里就直接 read_file(file_id=…) 取（图片也走 read_file，会以图像块返回像素），**不要因为消息变旧就说没有文件、也不要让用户重新上传**。",
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
            content = content[:HISTORY_MSG_MAX_CHARS] + "…[历史消息过长，已截断]"
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
                who = "用户" if m.__class__.__name__ == "HumanMessage" else "助手"
                line = "- [%s] %s" % (who, txt[:EARLY_MSG_CHARS])
                if used + len(line) > EARLY_DIGEST_MAX_CHARS:
                    break
                lines.append(line)
                used += len(line)
            if lines:
                lines.reverse()
                digest = ("[系统·早期对话要点]（早于最近 %d 轮的原文已省略，以下是逐条摘录，"
                          "其中的事实/数值/路径仍有效，可直接引用；不要因为看不到原文就说「没有」）\n%s"
                          % (context_length, "\n".join(lines)))
    # 总量兜底：从最早的开始丢，保证历史字符数在预算内（保留当前 user_input）
    total = sum(len(str(m.content)) for m in msgs) + len(user_input or "") + (len(digest) if digest else 0)
    while msgs and total > HISTORY_TOTAL_MAX_CHARS:
        dropped = msgs.pop(0)
        total -= len(str(dropped.content))
    msgs = msgs + [HumanMessage(content=user_input)]
    if digest:
        msgs = msgs[:0] + [HumanMessage(content=digest)] + msgs   # 要点放最前（稳定前缀）
    return msgs


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
            rows.append("- [图片] %s（file_id: %s）" % (name, fid))
        else:
            text = await get_upload_text(fid)
            if text:
                rows.append("- [文档] %s（file_id: %s，已解析 %d 字）" % (name, fid, len(text)))
            else:
                rows.append("- [文档] %s（file_id: %s，无可用文本：扫描版请用 mineru_parse 解析）" % (name, fid))
    return ("[本对话已上传的文件] —— 这些文件在本会话里**一直可用**，"
            "不要因为消息变旧就说「没有文件」或者要求用户重新上传。\n"
            + "\n".join(rows) +
            "\n取正文/取图片一律 read_file(file_id=…)（图片返回图像块，可直接看像素）；清单里有的就不要问用户要。")




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
        return {"final_answer": "没有可用工具", "reasoning_steps": []}

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
    permission_mode: str = "safe",
    max_rounds: int = 20,
    user: str = "",
    timeouts: dict = None,
    subagent: dict = None,
    client_id: str = "",
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
                           "text": f"（图谱未连接，已跳过图谱工具继续回答：{_n4_err}）\n"}

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
        _turn_store.begin_turn(user, branch_id, _turn_id, user_input, client_id=client_id)
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
    _nudged = _warned = False
    # A0：单轮内上下文预算（用 API 真实 usage 校准；触发线 = (窗口 - 输出预留) × 0.75）
    # context_window = hard limit，仅用于计算触发线，绝不据此在本地报错/截断（真的超了交给模型端报错）
    # context_budget = 前端可显式指定的清理触发线；0/缺失 → 自动 (窗口 − 输出预留) × 75%
    _ctx = ContextBudget(window_tokens=int(model_config.get("context_window") or DEFAULT_CONTEXT_WINDOW),
                         max_output_tokens=max_tokens,
                         trigger_override=int(model_config.get("context_budget") or 0))
    _overflow_retried = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20, read=300, write=60, pool=20)) as client:
            cap = max_rounds if max_rounds and max_rounds > 0 else 100000  # 0 = 无限
            for _step in range(1, cap + 1):  # 工具循环（步数可配，0=无限）
                if generation_cancelled():
                    _turn_finish("interrupted", all_content)
                    yield {"type": "stopped", "text": "已停止"}
                    return

                # ── 用户点「⚡立即回答」：注入与超时警告同款的催促，让模型马上收尾 ──
                if immediate_answer_requested():
                    reset_immediate_answer()
                    messages.append({"role": "user", "content": _IMMEDIATE_ANSWER_TEXT})
                    yield {"type": "timeout_notice", "level": "immediate",
                           "text": "已按你的「立即回答」注入催促：请它马上收尾出答案。",
                           "elapsed": int(time.time() - _t_start)}
                    _turn_notice("immediate", "已按你的「立即回答」注入催促：请它马上收尾出答案。",
                                 int(time.time() - _t_start))

                # ── 三级超时保护：催促 → 警告 → 终止 ──
                _elapsed = time.time() - _t_start
                if _T.get("abort") and _elapsed >= _T["abort"]:
                    yield {"type": "timeout_notice", "level": "abort",
                           "text": f"已达终止时限 {_T['abort']}s（实际 {int(_elapsed)}s），强制结束本轮生成。",
                           "elapsed": int(_elapsed)}
                    _turn_notice("abort", f"已达终止时限 {_T['abort']}s（实际 {int(_elapsed)}s），强制结束本轮生成。",
                                 int(_elapsed))
                    final_answer = all_content or final_answer
                    _turn_final(final_answer)
                    _turn_finish("timeout", final_answer)
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
                    _turn_notice("warn", f"已超过警告时限 {_T['warn']}s，已注入「立即作答」提示。", int(_elapsed))
                elif _T.get("nudge") and not _nudged and _elapsed >= _T["nudge"]:
                    _nudged = True
                    messages.append({"role": "user", "content":
                        "[系统·超时催促] 时间过半，请尽快收尾：停止进一步探索与多余的工具调用，"
                        "把已得到的信息总结成答案；如仍有缺口，在答案中标注「未完成部分」。"})
                    yield {"type": "timeout_notice", "level": "nudge",
                           "text": f"已超过催促时限 {_T['nudge']}s，已注入「尽快收尾」提示。", "elapsed": int(_elapsed)}
                    _turn_notice("nudge", f"已超过催促时限 {_T['nudge']}s，已注入「尽快收尾」提示。", int(_elapsed))

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
                            _cleared = prune_tool_results_only(messages, keep_recent=2)
                            _ctx.record_compress_result(_cleared > 0)
                            yield {"type": "context_pruned", "cleared": _cleared,
                                   "reason": "模型报上下文溢出，已清理旧工具结果并重试一次"}
                            if _cleared > 0:
                                continue
                        _hint = ("（上下文溢出：可清的工具结果已全部清理，仍超出窗口 —— "
                                 "请缩小本次任务范围或新开对话）") if _overflow else ""
                        yield {"type": "error",
                               "message": f"模型接口错误 {resp.status_code}: {body[:300]}{_hint}"}
                        return
                    async for line in resp.aiter_lines():
                        if generation_cancelled():
                            _turn_finish("interrupted", all_content + cur_content)
                            return
                        # 流式中途的硬终止（长回答不会因为循环顶部检查而失守）
                        if _T.get("abort") and (time.time() - _t_start) >= _T["abort"]:
                            yield {"type": "timeout_notice", "level": "abort",
                                   "text": f"已达终止时限 {_T['abort']}s，流式输出中断。",
                                   "elapsed": int(time.time() - _t_start)}
                            _turn_notice("abort", f"已达终止时限 {_T['abort']}s，流式输出中断。",
                                         int(time.time() - _t_start))
                            _turn_final(all_content + cur_content)
                            _turn_finish("timeout", all_content + cur_content)
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
                # ★ 账本：本轮推理落盘；工具轮的中间正文本轮也落一次（投影为 model_reasoning）
                _turn_reasoning(_step, cur_reasoning)
                if tool_acc:
                    _turn_text(_step, cur_content)

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
                    _turn_final(final_answer or "Agent 未能生成回答")
                    _turn_finish("complete", final_answer)
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
                            from agent.tools import slim_subagent_results as _slim
                            result = json.dumps({"status": "ok", "count": len(_sa_results),
                                                 "results": _slim(_sa_results)}, ensure_ascii=False)
                    elif not args_ok:
                        result = json.dumps({"error": f"工具 {name} 的调用参数不是合法 JSON（可能被输出长度截断）。请重新调用并简化参数。"}, ensure_ascii=False)
                    else:
                        tool = tool_map.get(name)
                        if tool is None:
                            result = json.dumps({"error": f"工具 {name} 不存在"})
                        else:
                            try:
                                res = await tool.ainvoke(args)
                                # 结构化内容块（图像通道）原样保留；其余序列化成 JSON 文本
                                result = res if isinstance(res, (str, list)) else json.dumps(res, ensure_ascii=False)
                            except Exception as e:
                                result = json.dumps({"error": f"工具执行失败: {str(e)}"})
                    # ── image_read：图提升为独立的 user 图像块，工具消息里只留文字桩 ──
                    # （OpenAI 系 role=tool 只收字符串，base64 留在 tool 消息里等于喂 token 乱码）
                    _img_u, _img_txt = _split_img(result)
                    if _img_u:
                        if len(pending_images) < 3:      # 单步最多 3 张，防上下文爆炸
                            pending_images.append(_img_u)
                        result = json.dumps({"ok": True, "mode": "image",
                                             "note": "图片已作为图像附加，可直接查看像素",
                                             "meta": _img_txt},
                                            ensure_ascii=False)
                    elif isinstance(result, list):
                        result = json.dumps(result, ensure_ascii=False, default=str)
                    yield {"type": "tool_result", "name": name, "content": str(result)[:2000]}
                    # ★ 落盘点：工具结果已在手 → 立刻按顺序写进账本（「第 N 步已跑完」就在这里保住）
                    _turn_step(name, args, result, round_no=_step)
                    if generation_cancelled():
                        _turn_finish("interrupted", all_content + cur_content)
                        yield {"type": "stopped", "text": "已停止"}
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
                    _cleared = prune_tool_results_only(messages, keep_recent=_ctx.keep_recent)
                    _ctx.pruned_total += _cleared
                    if _cleared:
                        yield {"type": "context_pruned", "cleared": _cleared,
                               "tokens_est": _pt, "reason": _why}

                if pending_images:
                    _vis = [{"type": "text", "text": "[系统] 上一步工具返回的图片如下，可直接读取像素："}]
                    _vis += [{"type": "image_url", "image_url": {"url": _u}} for _u in pending_images]
                    messages.append({"role": "user", "content": _vis})

        if not final_answer:
            cap = max_rounds if max_rounds and max_rounds > 0 else 100000
            # 步数耗尽 ≠ 没有信息：每一轮的工具结果都还在 messages 里。
            # 旧实现在这里只吐一句「已达最大步数」就结束，把已经跑出来的探索全部丢掉，
            # 用户看到的就是「忙了半天什么也没给」。改成再要一次【不带 tools 字段】的请求：
            # 协议层面它无法再发起工具调用，只能就已有信息收敛出结论。
            yield {"type": "timeout_notice", "level": "cap",
                   "text": f"已达最大工具循环步数({cap})，改为基于已获得信息强制收尾。",
                   "elapsed": int(time.time() - _t_start)}
            _turn_notice("cap", f"已达最大工具循环步数({cap})，改为基于已获得信息强制收尾。",
                         int(time.time() - _t_start))
            forced = ""
            try:
                _fmsg = messages + [{"role": "user", "content":
                    "[系统·步数上限] 工具调用次数已用完，这次回答【不能再调用工具】。"
                    "请只依据上面已获得的信息给出最终答复："
                    "① 先给结论；② 列出关键依据（带文件路径 / 数据出处）；"
                    "③ 明确写出「还没查到的部分」和「下一步该做什么」。"}]
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
            _tail = (f"\n\n---\n⏳ **步数上限**：本次工具循环达 {cap} 步上限，以上结论由已获得的"
                     "信息收敛而来，可能不完整。可继续追问让它接着做，"
                     "或在「设置」里调大「最大工具步数」。")
            _turn_finish("complete", final_answer)
            yield {"type": "final", "text": (final_answer + _tail) if final_answer.strip() else (
                f"已达最大工具循环步数({cap})，且没有可总结的内容。"
                "可尝试精简问题或拆分任务。")}

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
                           "text": f"（图谱未连接，已跳过图谱工具继续回答：{_n4_err}）\n"}

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
        yield {"type": "error", "message": "没有可用工具"}
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
        _turn_store.begin_turn(user, branch_id, _turn_id, user_input, client_id=client_id)
    except Exception as _e:
        print("[turn_store] begin_turn error:", _e)

    llm = ChatOpenAI(
        model=model_config.get("model", "deepseek-chat"),
        api_key=model_config.get("api_key") or "sk-noauth",  # 免密钥本地 llama-server 占位
        base_url=model_config.get("base_url", ""),
        temperature=model_config.get("temperature", 0.7),
        max_tokens=model_config.get("max_tokens") or 32768,
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
                _turn_final(final_answer)
                _turn_finish("interrupted", final_answer)
                yield {"type": "stopped", "text": "已停止"}
                return
            # 用户点「⚡立即回答」：LangChain 图内无法安全注入提示 → 直接用已有内容收尾作答
            if immediate_answer_requested():
                reset_immediate_answer()
                yield {"type": "timeout_notice", "level": "immediate",
                       "text": "已按「立即回答」收尾（LangChain 引擎无法中途注入提示，直接用已有内容作答）。"}
                _turn_notice("immediate", "已按「立即回答」收尾（LangChain 引擎无法中途注入提示，直接用已有内容作答）。")
                _turn_final(final_answer)
                _turn_finish("complete", final_answer)
                yield {"type": "final",
                       "text": (final_answer.strip() + "\n\n---\n⚡ **立即回答**：按你的要求提前收尾，以上是已有内容。")
                               if final_answer.strip() else "⚡ **立即回答**：按你的要求收尾，但本轮还没有产出内容，可继续追问。"}
                return
            # 超时终止（LangChain 引擎：图内无法安全注入催促/警告提示，故只做硬终止级保护）
            if _T.get("abort") and (time.time() - _t_start) >= _T["abort"]:
                yield {"type": "timeout_notice", "level": "abort",
                       "text": f"已达终止时限 {_T['abort']}s，强制结束本轮生成（LangChain 引擎仅支持终止级）。",
                       "elapsed": int(time.time() - _t_start)}
                _turn_notice("abort", f"已达终止时限 {_T['abort']}s，强制结束本轮生成（LangChain 引擎仅支持终止级）。",
                             int(time.time() - _t_start))
                _turn_final(final_answer)
                _turn_finish("timeout", final_answer)
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
                                      "note": "图片已随工具消息一并发送给模型（LangChain 原生 image_url 内容块）",
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
                   f"⏳ **步数上限**：本次工具循环达 {cap} 步上限，仍未收敛出可展示的正文。"
                   "可继续追问让它基于已有信息作答，或在「设置」里调大「最大工具步数」；"
                   "本引擎（LangChain）为批处理，长任务建议改用 custom 引擎。"}
        else:
            if saw_length:
                final_answer += "\n\n> ⚠️ 检测到模型输出曾因达到最大输出 Token 上限被截断；若结果不完整，请在设置中调大 Token 或让 AI 分多次写入。"
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
        yield {"type": "error", "message": f"LangChain Agent 执行失败: {str(e)}", "traceback": traceback.format_exc()}
