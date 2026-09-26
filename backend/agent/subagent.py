"""
子智能体（A 方案：引擎无关实现）

设计要点（对应用户铁律：绝不卡死 → 全部硬上限，且不做任何"自我重试/自我回退"）：
  - 深度 = 1：子智能体不能再派子智能体（allowlist 里没有 delegate_task）
  - 并发 ≤ 2（asyncio.Semaphore）
  - 每子轮次 ≤ max_rounds（默认 4）
  - 总超时（默认 300s，asyncio.wait_for 包住）
  - 工具子集 = 只读/检索类白名单
  - 失败即返回错误文本，不重试、不激活其它智能体

子智能体不走任何引擎的 agent（不嵌套 LangGraph），自己用裸 OpenAI 兼容 HTTP 循环，
因此 engine=custom（手写 ReAct）与 engine=langchain 两条路都能用同一份实现。
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

# 子智能体可用工具白名单（只读 + 检索；绝不含 write_file / terminal / run_code / delegate_task）
SUBAGENT_TOOL_ALLOWLIST = {
    "web_search", "web_extract",
    "read_file", "list_files",
    "shared_search", "search_history",
    "conversation_notes",
    "graph_schema", "execute_cypher",
    "skill_list", "skill_view",
    "mineru_parse",
}

SUBAGENT_MAX_ROUNDS_DEFAULT = 4
SUBAGENT_TIMEOUT_DEFAULT = 300
SUBAGENT_MAX_PARALLEL = 2                 # 默认并发上限（设置里可调）
SUBAGENT_TIMEOUTS_DEFAULT = {"nudge": 60, "warn": 150, "abort": 300}   # 子智能体三级超时（秒）
SUBAGENT_MAX_TASKS = 4                    # 单次最多派发子任务数（设置里可调）

# 专有子智能体角色预设：按任务类型注入领域系统提示（delegate_task 的 tasks[].role）
SUBAGENT_ROLE_PROMPTS = {
    "contradiction": (
        "\n【专属角色·文献矛盾核查】你是文献矛盾核查子智能体，只负责对分配给你的文献做矛盾清扫。"
        "规则：① 只收两类——同一量两个值、结论被自身数据否定；"
        "② 裁决分类 E1(我方读图错)/E2(我方读文错)/E3(文献真矛盾)/E5(口径差异，两值并存)/OCR伪影(剔除)；"
        "③ 阈值：数值差<5%量程(标量<1%相对差)判一致取正文值，曲线终点±2%量程内一致——宁缺毋滥；"
        "④ 不报：舍入/修约、口径差异、模糊表述(about/nearly)、术语漂移；"
        "⑤ 每条给 text_quote 原句、两个值、adopted_value(正文自相矛盾时取多源佐证方)与 confidence；"
        "⑥ 表格数值以 TableN.txt 为准，图注以 FigureN.txt 为准。"
    ),
    "extraction": (
        "\n【专属角色·数据提取】你是文献数据提取子智能体，按统一 schema 抽取定量数据。"
        "单位铁律：浓度 mg/L、投加量 g/L、温度 ℃、时间 min、k min⁻¹；"
        "只抽有明确出处的数值，每个值带 text_quote 原句与章节；"
        "图上估读值标注 image_read 并给不确定度；抽不到就标 missing，不要编造。"
    ),
    "verify": (
        "\n【专属角色·数据核查】你是数据核查子智能体，对主智能体给出的数据清单逐条回到原文核验。"
        "每条输出：原文是否支持(是/否/部分)、text_quote、建议修正值；不确定就标'需人工复核'，不要猜。"
    ),
}

_SUBAGENT_SYSTEM = (
    "你是一个被主智能体派发的子智能体（worker）。你只负责完成交给你的那个子任务，"
    "不负责回答用户的完整问题。\n"
    "规则：\n"
    "1. 先用最少的工具调用收集事实（建议 ≤ {max_rounds} 轮），不要反复试探。\n"
    "2. 完成后必须给出一段**结构化结论摘要**：结论要点 + 关键数据 + 来源（如有）。\n"
    "3. 摘要要能独立阅读：不要写“如上”“见上文”这类依赖上下文的话。\n"
    "4. 不要向用户提问；信息不足时，就在摘要里写明“信息不足：<缺什么>”。\n"
    "5. 你无法派发子智能体，也不要尝试写文件或执行终端命令。"
)


def _tool_schema(tool) -> dict:
    params = {"type": "object", "properties": {}}
    try:
        if getattr(tool, "args_schema", None) is not None:
            params = tool.args_schema.schema()
    except Exception:
        pass
    return {"type": "function",
            "function": {"name": tool.name,
                         "description": (tool.description or "")[:1500],
                         "parameters": params}}


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


async def run_subagent(goal: str, context: str = "", tools: list[str] | None = None,
                       max_rounds: int = SUBAGENT_MAX_ROUNDS_DEFAULT,
                       timeout: int = SUBAGENT_TIMEOUT_DEFAULT,
                       timeouts: dict | None = None,
                       emit=None, index: int = 0, role: str = "") -> dict:
    """跑一个子智能体。返回 {ok, summary, steps[], rounds, seconds} 或 {ok: False, error}。

    三级超时（催促 / 警告 / 终止）：到催促时注入"尽快收尾"，到警告时注入"立即给结论"，
    到终止时中断并返回已完成的部分（绝不卡死）。

    emit: 可选回调 emit(index, step_dict)。提供时会在每个步骤发生时实时推送，
          供上层把子智能体过程流式转发给前端（不改变最终返回值）。
    """
    from agent.agent import generation_cancelled, get_current_model_config
    from agent.tools import tools_registry

    t0 = time.time()
    _T = dict(SUBAGENT_TIMEOUTS_DEFAULT)
    if isinstance(timeouts, dict):
        for k in ("nudge", "warn", "abort"):
            if timeouts.get(k) is not None:
                try:
                    _T[k] = max(0, int(timeouts[k]))
                except Exception:
                    pass
    if timeout and not (timeouts or {}).get("abort"):
        _T["abort"] = int(timeout)          # 兼容旧的单一 timeout（等价于终止级）
    _nudged = _warned = False

    cfg = get_current_model_config() or {}
    if not cfg.get("model"):
        return {"ok": False, "error": "子智能体无法启动：缺少模型配置", "goal": goal, "steps": []}
    endpoint = _endpoint(cfg.get("base_url", ""))
    if not endpoint:
        return {"ok": False, "error": "子智能体无法启动：模型 base_url 为空", "goal": goal, "steps": []}

    # 工具子集：白名单 ∩ 实际注册 ∩ 调用方指定
    want = [n for n in (tools or list(SUBAGENT_TOOL_ALLOWLIST))]
    names = [n for n in want if n in SUBAGENT_TOOL_ALLOWLIST and n in tools_registry]
    tool_objs = [tools_registry[n] for n in names]
    tool_map = {t.name: t for t in tool_objs}
    openai_tools = [_tool_schema(t) for t in tool_objs]

    messages = [
        {"role": "system", "content": _SUBAGENT_SYSTEM.format(max_rounds=max_rounds)
         + SUBAGENT_ROLE_PROMPTS.get(role, "")},
        {"role": "user", "content": f"【子任务】{goal}\n\n【背景/上下文】\n{(context or '（无额外上下文）')[:6000]}"},
    ]
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    steps: list[dict] = []
    summary = ""
    rounds_used = 0

    def _push(step: dict) -> None:
        """记录一个步骤；若提供 emit 则同时实时推送给上层。"""
        steps.append(step)
        if emit:
            try:
                emit(index, dict(step))
            except Exception:
                pass

    def _mark(step: dict) -> None:
        """仅推送（不写进 steps，例如"第 N 轮思考中"这类瞬时提示）。"""
        if emit:
            try:
                emit(index, dict(step))
            except Exception:
                pass

    async def _loop() -> None:
        nonlocal summary, rounds_used, _nudged, _warned
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20, read=180, write=60, pool=20)) as client:
            for rnd in range(1, max(1, int(max_rounds)) + 1):
                if generation_cancelled():
                    _push({"type": "notice", "text": "子智能体被用户停止"})
                    return
                # ── 子智能体三级超时：催促 → 警告 → 终止 ──
                _el = time.time() - t0
                if _T.get("abort") and _el >= _T["abort"]:
                    _push({"type": "notice",
                           "text": f"已达终止时限 {_T['abort']}s（实际 {int(_el)}s），子智能体被中断。"})
                    summary = summary or f"（子任务超时中断：超过 {_T['abort']}s）"
                    return
                if _T.get("warn") and not _warned and _el >= _T["warn"]:
                    _warned = True
                    _push({"type": "notice", "text": f"超时警告：已用 {int(_el)}s，注入「立即给结论」提示。"})
                    messages.append({"role": "user", "content":
                        "[系统·超时警告] 立即用你已获得的信息给出结论摘要，不要再调用任何工具。"})
                elif _T.get("nudge") and not _nudged and _el >= _T["nudge"]:
                    _nudged = True
                    _push({"type": "notice", "text": f"超时催促：已用 {int(_el)}s，注入「尽快收尾」提示。"})
                    messages.append({"role": "user", "content":
                        "[系统·超时催促] 尽快收尾：停止进一步探索和多余的工具调用，"
                        "把已得到的信息总结成结论摘要；有缺口就在摘要里标注「信息不足：…」。"})
                rounds_used = rnd
                _mark({"type": "round", "round": rnd})
                payload = {
                    "model": cfg.get("model"),
                    "messages": messages,
                    "temperature": cfg.get("temperature", 0.3),
                    "max_tokens": cfg.get("max_tokens") or 8192,
                }
                if openai_tools:
                    payload["tools"] = openai_tools
                r = await client.post(endpoint, json=payload, headers=headers)
                if r.status_code != 200:
                    _push({"type": "notice", "text": f"模型接口错误 {r.status_code}: {r.text[:200]}"})
                    summary = summary or f"（子任务失败：模型接口错误 {r.status_code}）"
                    return
                msg = (r.json().get("choices") or [{}])[0].get("message") or {}
                content = (msg.get("content") or "").strip()
                tool_calls = msg.get("tool_calls") or []
                if content:
                    _push({"type": "thinking", "text": content})
                    summary = content
                if not tool_calls:
                    return
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
                for tc in tool_calls:
                    fn = (tc.get("function") or {})
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except Exception:
                        args = {}
                    _push({"type": "tool_call", "name": name,
                           "input": json.dumps(args, ensure_ascii=False)[:1500]})
                    tool = tool_map.get(name)
                    if tool is None:
                        result = json.dumps({"error": f"子智能体不可用该工具: {name}"}, ensure_ascii=False)
                    else:
                        try:
                            res = await tool.ainvoke(args)
                            result = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
                        except Exception as e:
                            result = json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)
                    _push({"type": "tool_result", "name": name, "content": str(result)[:2000]})
                    messages.append({"role": "tool", "tool_call_id": tc.get("id") or name,
                                     "content": str(result)[:4000]})
            summary = summary or "（子智能体达到轮次上限，未给出明确结论）"

    _hard = int(_T.get("abort") or SUBAGENT_TIMEOUT_DEFAULT)
    try:
        await asyncio.wait_for(_loop(), timeout=max(10, _hard))
    except asyncio.TimeoutError:
        seconds = round(time.time() - t0, 1)
        return {"ok": False, "error": f"子任务超时（{_hard}s）", "goal": goal,
                "steps": steps, "rounds": rounds_used, "seconds": seconds}
    except Exception as e:
        return {"ok": False, "error": f"子智能体异常：{e}", "goal": goal,
                "steps": steps, "rounds": rounds_used, "seconds": round(time.time() - t0, 1)}

    if not summary:
        summary = "（子智能体未产出结论）"
    return {"ok": True, "summary": summary, "goal": goal, "context": context,
            "steps": steps, "rounds": rounds_used, "seconds": round(time.time() - t0, 1)}


async def run_subagents_parallel(tasks: list[dict], max_rounds: int = SUBAGENT_MAX_ROUNDS_DEFAULT,
                                 timeout: int = SUBAGENT_TIMEOUT_DEFAULT,
                                 max_parallel: int = SUBAGENT_MAX_PARALLEL,
                                 timeouts: dict | None = None,
                                 emit=None) -> list[dict]:
    """并行跑多个子智能体（并发上限由设置传入，默认 2）。失败即失败，不重试。

    emit: 可选回调 emit(index, step_dict)，透传给每个子智能体用于流式上报进度。
    """
    sem = asyncio.Semaphore(max(1, int(max_parallel)))

    async def _one(i: int, t: dict) -> dict:
        async with sem:
            res = await run_subagent(t.get("goal", ""), t.get("context", ""),
                                     t.get("tools"), max_rounds, timeout, timeouts,
                                     emit=emit, index=i, role=str(t.get("role", "") or ""))
            res["index"] = i
            if not res.get("goal"):
                res["goal"] = t.get("goal", "")
            return res

    results = await asyncio.gather(*[_one(i, t) for i, t in enumerate(tasks)],
                                   return_exceptions=True)
    out = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            out.append({"index": i, "ok": False, "error": f"子智能体异常：{r}",
                        "goal": tasks[i].get("goal", ""), "steps": []})
        else:
            out.append(r)
    return out
