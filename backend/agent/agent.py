"""
LangChain Agent 构建（LangChain 1.3.14 API）
使用 create_agent → 直接返回编译好的 LangGraph
"""
import json, os, time
from pathlib import Path
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.chat_history import InMemoryChatMessageHistory

# ── 当前对话上下文（供工具读取）────────────────────
_current_branch_id = ""

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
from tools.file_tools import set_permission_mode

_WORKSPACE = Path(__file__).parent.parent / "workspace"
_HISTORY_FILE = _WORKSPACE / "_history.jsonl"


# ── 构建 System Prompt ────────────────────────────

async def _build_system_prompt(enabled_tools: list[str], loaded_skills: list[str]) -> str:
    """Hermes 式系统提示词：不列出工具清单（工具能力靠 OpenAI tools schema），技能只简列 name+desc。

    与 Hermes 一致的设计：
      - 工具能力/行为指引由每个工具的 schema description 提供（见 tools.py），系统提示词不重复工具名。
      - 技能以 <available_skills> 形式简列勾选技能的 name+description；详情按需用 skill_view 读取（不漏全文）。
    """
    lines = [
        "你是 C4EAI 智能助手，连接知识图谱、联网搜索、文件处理、代码执行等工具。",
        "",
        "## 规则",
        "- 用中文回答，保留专业英文术语",
        "- 需要哪些能力时，直接使用对应工具（工具的能力与用法由各工具的接口说明给出）。",
        "- 图谱查询、联网搜索、文件解析、技能读取等，按用户问题需求选择合适的工具。",
        "- **不要在回答中重复或引用工具返回的原始 JSON 数据**。工具执行结果已被系统自动展示给用户，你在回答中只给出结论和分析。",
        "- **最终回答简洁明了**，不要包含中间步骤的描述。用户能看到你调用了哪些工具。",
        "- **调用工具后必须收敛**：每次工具执行完，先根据返回内容判断是否已足够回答用户问题。若已获得所需信息，立即生成最终回答并结束，**不要无意义地重复调用工具**。只有确实缺少关键信息时才继续调用**不同的、有必要的**工具。同一工具返回失败时，最多按需换用别的工具一次，不要反复重试同一工具。",
        "- 若某写操作工具返回包含 `__NEED_FULL__` 的错误，说明当前为询问模式、该操作超出权限范围。请明确告知用户：需要切换到完全访问模式后才能执行，并简要说明要做什么。",
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


# ── 历史持久化 ────────────────────────────────────

def _save_to_history(branch_id: str, user_input: str, final_answer: str, msg_id: str):
    """保存单次对话到历史文件（追加）"""
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": msg_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "branch_id": branch_id,
        "input": user_input,
        "output": final_answer,
    }
    with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _trigger_vector_index():
    """异步触发向量索引（在后台线程执行，不阻塞返回）"""
    import threading
    def _do_index():
        try:
            from tools.vector_search import index_new_history
            meta_file = _WORKSPACE / "_vector_meta.json"
            last_id = None
            if meta_file.exists():
                import json as j
                meta = j.loads(open(meta_file).read())
                last_id = meta.get("last_indexed_id")
            index_new_history("_history.jsonl", last_id)
        except Exception as e:
            print(f"[Vector] Index error: {e}")
    threading.Thread(target=_do_index, daemon=True).start()


# ── 上下文截断 ────────────────────────────────────

def _truncate_history(history: list[dict], max_context_length: int) -> list[dict]:
    """按 contextLength 截取最近 N 轮消息"""
    if not history or max_context_length <= 0:
        return []
    # 过滤有效消息
    valid = [m for m in history if m.get("role") in ("user", "assistant") and m.get("content")]
    if len(valid) <= max_context_length:
        return valid
    return valid[-max_context_length:]


# ── 上下文管理（LangChain 原生）───────────────────

def _build_context(history: list[dict], user_input: str, context_length: int):
    """用 InMemoryChatMessageHistory 装载，保留最近 context_length 轮（每轮约 2 条消息）"""
    hist = InMemoryChatMessageHistory()
    for m in history or []:
        role = m.get("role")
        content = m.get("content")
        if not content:
            continue
        if role == "user":
            hist.add_user_message(content)
        elif role == "assistant":
            hist.add_ai_message(content)
    msgs = list(hist.messages)
    if context_length and context_length > 0:
        keep_n = context_length * 2
        if len(msgs) > keep_n:
            msgs = msgs[-keep_n:]
    msgs = msgs + [HumanMessage(content=user_input)]
    return msgs


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
) -> dict:
    global _current_branch_id
    _current_branch_id = branch_id or ""
    set_permission_mode(permission_mode or "safe")
    # 1. Neo4j 连接
    if neo4j_config and neo4j_config.get("uri"):
        if {"graph_schema", "execute_cypher"} & set(enabled_tools):
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    return {"final_answer": f"⚠️ Neo4j 连接失败: {cr.get('error', '')}", "reasoning_steps": []}

    # 2. System Prompt
    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)

    # 3. LLM
    llm = ChatOpenAI(
        model=model_config.get("model", "deepseek-chat"),
        api_key=model_config.get("api_key"),
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

    # 6. 文件内容注入
    file_texts = []
    if file_ids:
        from tools.file_tools import _UPLOADS_DIR
        for fid in file_ids:
            text_path = os.path.join(_UPLOADS_DIR, fid, "text.txt")
            if os.path.isfile(text_path):
                with open(text_path, "r", encoding="utf-8") as f:
                    file_texts.append(f.read())  # 注入完整文件文本，不做长度截断
    if file_texts:
        file_block = "\n\n---\n用户上传了以下文件内容：\n" + "\n---\n".join(file_texts)
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
    permission_mode: str = "safe",
    images: list[str] = None,
    max_rounds: int = 8,
):
    """真流式 Agent：直接调原始 OpenAI 兼容 API 流，逐 token 抓取。

    Events:
      {"type":"reasoning","text":...}    模型推理 (reasoning_content，推理模型)
      {"type":"token","text":...}        模型消息正文 (content，逐字)
      {"type":"tool_call","name":...,"input":...}
      {"type":"tool_result","name":...,"content":...}
      {"type":"final","text":...}
      {"type":"error","message":...}
    """
    import httpx
    import re

    reset_generation_cancel()

    global _current_branch_id
    _current_branch_id = branch_id or ""
    set_permission_mode(permission_mode or "safe")

    # Neo4j
    if neo4j_config and neo4j_config.get("uri"):
        if {"graph_schema", "execute_cypher"} & set(enabled_tools):
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    yield {"type": "error", "message": f"Neo4j连接失败: {cr.get('error','')}"}
                    return

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

    # Files injection
    if file_ids:
        from tools.file_tools import _UPLOADS_DIR, image_to_base64
        file_texts = []
        image_urls = list(images or [])
        for fid in file_ids:
            # 图片：后端自动转 base64 构造多模态（首次上传只要 file_id 就能识图）
            data_url = await image_to_base64(fid)
            if data_url:
                image_urls.append(data_url)
                continue
            text_path = os.path.join(_UPLOADS_DIR, fid, "text.txt")
            if os.path.isfile(text_path):
                with open(text_path, "r", encoding="utf-8") as f:
                    file_texts.append(f.read())  # 注入完整文件文本，不做长度截断
        if file_texts:
            file_block = "\n\n---\n用户上传了以下文件内容：\n" + "\n---\n".join(file_texts)
            user_input = user_input + file_block

    # 上下文管理（保留最近轮次）
    ctx = _build_context(history, user_input, context_length)
    messages = [{"role": "system", "content": system_prompt}]
    for m in ctx:
        role = "user" if isinstance(m, HumanMessage) else "assistant"
        messages.append({"role": role, "content": m.content})

    # 多模态：file_ids 里的图片(auto转base64) + 前端显式 images，合并附加到最后一条 user 消息
    if 'image_urls' not in locals():
        image_urls = list(images or [])
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
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20, read=300, write=60, pool=20)) as client:
            cap = max_rounds if max_rounds and max_rounds > 0 else 100000  # 0 = 无限
            for _step in range(1, cap + 1):  # 工具循环（步数可配，0=无限）
                if generation_cancelled():
                    yield {"type": "stopped", "text": "已停止"}
                    return
                cur_reasoning = ""
                cur_content = ""
                tool_acc = {}  # index -> {id,name,arguments}
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

                # 本轮结束
                if not tool_acc:
                    # 若本轮无正文（正文出现在工具轮），回退到累积正文，避免最终答案丢失
                    answer_text = cur_content.strip() if cur_content.strip() else all_content
                    final_answer = _clean_final(answer_text)
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
                    except Exception:
                        args = {}
                    yield {"type": "tool_call", "name": name, "input": json.dumps(args, ensure_ascii=False)}
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
    permission_mode: str = "safe",
    images: list[str] = None,
    max_rounds: int = 8,
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

    # Neo4j
    if neo4j_config and neo4j_config.get("uri"):
        if {"graph_schema", "execute_cypher"} & set(enabled_tools):
            if not Neo4jClient.is_connected():
                cr = Neo4jClient.connect(neo4j_config["uri"], neo4j_config["user"], neo4j_config["password"])
                if not cr.get("success"):
                    yield {"type": "error", "message": f"Neo4j连接失败: {cr.get('error','')}"}
                    return

    system_prompt = await _build_system_prompt(enabled_tools, loaded_skills)

    tools = get_tools(enabled_tools)
    if not tools:
        yield {"type": "error", "message": "没有可用工具"}
        return
    if not loaded_skills:
        tools = [t for t in tools if t.name not in ("skill_list", "skill_view")]
    if (permission_mode or "safe") == "safe":
        tools = [t for t in tools if t.name != "skill_write"]

    # 文件内容注入
    image_urls = list(images or [])
    if file_ids:
        from tools.file_tools import _UPLOADS_DIR, image_to_base64
        file_texts = []
        for fid in file_ids:
            data_url = await image_to_base64(fid)
            if data_url:
                image_urls.append(data_url)
                continue
            text_path = os.path.join(_UPLOADS_DIR, fid, "text.txt")
            if os.path.isfile(text_path):
                with open(text_path, "r", encoding="utf-8") as f:
                    file_texts.append(f.read())  # 注入完整文件文本，不做长度截断
        if file_texts:
            file_block = "\n\n---\n用户上传了以下文件内容：\n" + "---\n".join(file_texts)
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
        api_key=model_config.get("api_key"),
        base_url=model_config.get("base_url", ""),
        temperature=model_config.get("temperature", 0.7),
        max_tokens=model_config.get("max_tokens") or 8192,
    )
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

    cap = max_rounds if max_rounds and max_rounds > 0 else 100000  # 0 = 无限
    config = {"recursion_limit": cap * 2 + 5}
    final_answer = ""
    try:
        async for ev in agent.astream_events({"messages": messages}, version="v2", config=config):
            if generation_cancelled():
                yield {"type": "stopped", "text": "已停止"}
                return
            e = ev.get("event")
            if e == "on_chat_model_stream":
                chunk = (ev.get("data") or {}).get("chunk")
                if chunk and getattr(chunk, "content", None):
                    yield {"type": "token", "text": chunk.content}
            elif e == "on_chat_model_end":
                out = (ev.get("data") or {}).get("output")
                if out is not None and getattr(out, "content", None):
                    final_answer = out.content
            elif e == "on_tool_start":
                data = ev.get("data") or {}
                name = data.get("name") or ""
                inp = data.get("input")
                try:
                    inp_s = json.dumps(inp, ensure_ascii=False) if inp is not None else ""
                except Exception:
                    inp_s = str(inp)
                yield {"type": "tool_call", "name": name, "input": inp_s[:2000]}
            elif e == "on_tool_end":
                data = ev.get("data") or {}
                name = data.get("name") or ""
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
