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
import os
import time

import httpx

# 子智能体可用工具白名单（只读 + 检索；绝不含 write_file / terminal / run_code / delegate_task）
SUBAGENT_TOOL_ALLOWLIST = {
    "web_search", "web_extract",
    "read_file", "list_files",
    "shared_search", "search_history",
    "conversation_notes",
    "graph_query",
    "skill_list", "skill_view",
    "mineru_parse",
}

# 默认只读白名单之外、但**档案里显式写了才给**的只读工具（文件定位类，仍属只读）
SUBAGENT_EXTRA_READ_TOOLS = {"find_files", "search_files", "list_outputs"}

SUBAGENT_MAX_ROUNDS_DEFAULT = 4
SUBAGENT_TIMEOUT_DEFAULT = 300
SUBAGENT_MAX_PARALLEL = 2                 # 默认并发上限（设置里可调）
SUBAGENT_TIMEOUTS_DEFAULT = {"abort": 300}   # 子智能体超时保护（2026-10-13 起只剩强制中断一级，秒）
SUBAGENT_MAX_TASKS = 4                    # 单次最多派发子任务数（设置里可调）

# 专有子智能体角色预设：按任务类型注入领域系统提示（delegate_task 的 tasks[].role）
SUBAGENT_ROLE_PROMPTS = {
    "contradiction": (
        "\n[Role: literature contradiction audit] You are the contradiction-audit subagent; only audit the papers assigned to you."
        "Rules: (1) accept only two kinds - one quantity reported with two values, or a conclusion refuted by its own data;"
        "(2) adjudicate as E1 (our figure-reading error) / E2 (our text-parsing error) / E3 (true contradiction) / E5 (definition difference; keep both values) / OCR artifact (drop);"
        "(3) thresholds: a difference under 5% of the axis range (under 1% relative for scalars) counts as agreement and the text value is adopted; curve endpoints within +/-2% of range agree - prefer omission to false positives;"
        "(4) do not report rounding, definition differences, vague wording (about/nearly) or terminology drift;"
        "(5) each finding needs a verbatim text_quote, both values, adopted_value (the side supported by more sources when the text contradicts itself) and confidence;"
        "(6) table values follow TableN.txt and captions follow FigureN.txt."
    ),
    "extraction": (
        "\n[Role: data extraction] You are the data-extraction subagent; extract quantitative data into the unified schema."
        "Units: concentration mg/L, dosage g/L, temperature degC, time min, k min^-1;"
        "extract only values with an explicit source, each with a verbatim text_quote and section;"
        "mark figure-read values as image_read with uncertainty; mark absent values as missing and never invent them."
    ),
    "verify": (
        "\n[Role: data verification] You are the verification subagent; check every item in the main agent's data list against the source."
        "For each item output: supported by the source (yes/no/partly), text_quote, suggested correction; mark uncertain items as needing human review and never guess."
    ),
}

_SUBAGENT_SYSTEM = (
    "You are a subagent (worker) dispatched by the main agent. You only complete the subtask assigned to you,"
    "not the user's full question.\n"
    "Rules:\n"
    "1. Collect facts with the fewest tool calls ({rounds_hint}); do not probe repeatedly.\n"
    "2. Finish with a **structured summary**: conclusions + key data + sources (if any).\n"
    "3. The summary must stand alone: no 'as above' or 'see previous' phrasing.\n"
    "4. Do not ask the user questions; if information is missing, write 'insufficient information: <what>'.\n"
    "5. You cannot dispatch subagents; do not write files or run terminal commands.\n"
    "6. **Follow the user's language**: the summary and **internal reasoning (reasoning, thinking) should use the user's language**; keep technical English terms."
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
                       emit=None, index: int = 0, role: str = "",
                       profile: str = "") -> dict:
    """跑一个子智能体。返回 {ok, summary, steps[], rounds, seconds, profile, status} 或失败态。

    profile：子智能体档案名（backend/agents/<name>/agent.json）。档案自己挂
    技能目录 / 工具目录 / 运行上下文参数 / 模型参数，**不与主智能体共用**。
    role 是老写法（内置档案 contradiction/extraction/verify），仍兼容：
    传了 profile 以 profile 为准，否则用 role。

    超时保护（2026-10-13 起只剩一级）：到终止时限时中断并返回已完成的部分（绝不卡死）。
    原催促/警告两级已删——断点续跑让中断后可继续，多级递进失去意义。

    emit: 可选回调 emit(index, step_dict)。提供时会在每个步骤发生时实时推送，
          供上层把子智能体过程流式转发给前端（不改变最终返回值）。
    """
    from agent.agent import generation_cancelled, get_current_model_config
    from agent.tools import tools_registry
    from tools.file_tools import split_image_result as _split_img
    from agent import profiles as _pf

    t0 = time.time()

    # ── 档案：独立技能 / 工具 / 参数 / 模型 ──────────────
    _pname = (profile or role or "").strip()
    _prof = _pf.get_profile(_pname)
    if _prof is None:
        return {"ok": False, "error": f"Unknown subagent profile {_pname} (available: see /api/agents/profiles)",
                "goal": goal, "steps": [], "profile": _pname}
    _prof_name = _pname or "__generic__"
    _pp = _prof.get("params") or {}
    _tok = _pf.set_current_profile(_prof)      # 技能根 / 技能白名单 / 读上限 由此生效
    proc_max_chars = int(_pp.get("tool_result_max_chars") or 4000)
    preview_chars = int(_pp.get("tool_result_preview_chars") or 2000)
    ctx_max_chars = int(_pp.get("user_context_max_chars") or 6000)
    _T = dict(SUBAGENT_TIMEOUTS_DEFAULT)
    if isinstance(timeouts, dict) and timeouts.get("abort") is not None:
        try:
            _T["abort"] = max(0, int(timeouts["abort"]))
        except Exception:
            pass
    if timeout and not (timeouts or {}).get("abort"):
        _T["abort"] = int(timeout)          # 兼容旧的单一 timeout（等价于终止级）
    # 档案里显式写了的时限/轮次 → 覆盖（子智能体有自己的一套；2026-10-13 起只剩 abort 一级）
    if _pp.get("abort"):
        try:
            _T["abort"] = max(0, int(_pp["abort"]))
        except Exception:
            pass
    if _pp.get("timeout"):
        try:
            _T["abort"] = max(0, int(_pp["timeout"]))
        except Exception:
            pass
    if _pp.get("max_rounds"):
        try:
            max_rounds = int(_pp["max_rounds"])
        except Exception:
            pass

    # 模型：档案 model 段覆盖主控当前模型（可只覆盖其中几项，未写的沿用主控）
    cfg = dict(get_current_model_config() or {})
    for k in ("model", "base_url", "api_key", "temperature", "max_tokens"):
        v = (_prof.get("model") or {}).get(k)
        if isinstance(v, str) and v.startswith("env:"):
            # 档案只写 env:VAR，实际密钥从运行环境读取，绝不落盘。
            cfg[k] = os.environ.get(v[4:], "")
        elif v not in (None, ""):
            cfg[k] = v
    if not cfg.get("model"):
        return {"ok": False, "error": "Subagent cannot start: model configuration missing", "goal": goal,
                "steps": [], "profile": _prof_name}
    endpoint = _endpoint(cfg.get("base_url", ""))
    if not endpoint:
        return {"ok": False, "error": "Subagent cannot start: model base_url is empty", "goal": goal,
                "steps": [], "profile": _prof_name}

    # 工具：该档案的私有注册表（全局只读白名单 ∩ 档案白名单 + agents/<name>/tools/）
    _reg = _pf.build_registry(_prof, tools_registry, global_allowlist=SUBAGENT_TOOL_ALLOWLIST,
                              extra_allowlist=SUBAGENT_EXTRA_READ_TOOLS)
    if tools:
        _want = set(tools)
        _priv = {getattr(t, "name", "") for t in _pf.load_agent_tools(_prof)}
        _reg = {n: t for n, t in _reg.items() if n in _want or n in _priv}
    tool_map = dict(_reg)
    openai_tools = [_tool_schema(t) for t in _reg.values()]

    _sys = _SUBAGENT_SYSTEM.format(rounds_hint=("suggest <= %d rounds" % int(max_rounds)
                                               if int(max_rounds) > 0 else "unlimited rounds; take what you need"))
    _sys += SUBAGENT_ROLE_PROMPTS.get(role, "")
    _sys += ("\n" + _pf.system_prompt_for(_prof)) if _pf.system_prompt_for(_prof) else ""
    if _prof.get("status_line", True):
        _sys += _pf.STATUS_HINT
    messages = [
        {"role": "system", "content": _sys},
        {"role": "user", "content": f"[Subtask] {goal}\n\n[Background/context]\n{(context or '(no extra context)')[:ctx_max_chars]}"},
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
        nonlocal summary, rounds_used
        _pending_images: list = []       # image_read：待附加的图片 dataURL
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20, read=180, write=60, pool=20)) as client:
            _cap = int(max_rounds) if int(max_rounds) > 0 else 100000   # 0 = 无限（用户 2026-09-27：不要锁死 20）
            for rnd in range(1, _cap + 1):
                if generation_cancelled():
                    _push({"type": "notice", "text": "Subagent stopped by the user"})
                    return
                # ── 子智能体超时保护：只剩一级强制中断（2026-10-13 删催促/警告）──
                _el = time.time() - t0
                if _T.get("abort") and _el >= _T["abort"]:
                    _push({"type": "notice",
                           "text": f"Abort timeout ({_T['abort']} s, actual {int(_el)} s); subagent interrupted."})
                    summary = summary or f"(subtask interrupted by timeout: over {_T['abort']} s)"
                    return
                rounds_used = rnd
                _mark({"type": "round", "round": rnd})
                payload = {
                    "model": cfg.get("model"),
                    "messages": messages,
                    "temperature": cfg.get("temperature", 0.3),
                    "max_tokens": cfg.get("max_tokens") or 32768,
                }
                if openai_tools:
                    payload["tools"] = openai_tools
                r = await client.post(endpoint, json=payload, headers=headers)
                if r.status_code != 200:
                    _push({"type": "notice", "text": f"Model API error {r.status_code}: {r.text[:200]}"})
                    summary = summary or f"(subtask failed: model API error {r.status_code})"
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
                        result = json.dumps({"error": f"Subagent may not use this tool: {name}"}, ensure_ascii=False)
                    else:
                        try:
                            res = await tool.ainvoke(args)
                            result = res if isinstance(res, (str, list)) else json.dumps(res, ensure_ascii=False)
                        except Exception as e:
                            result = json.dumps({"error": f"Tool execution failed: {e}"}, ensure_ascii=False)
                    # ── image_read：先抽出图片，再把正文换成桩（必须在 4000 截断之前）──
                    _img_u, _img_txt = _split_img(result)
                    if _img_u:
                        _pending_images.append(_img_u)
                        result = json.dumps({"ok": True, "mode": "image",
                                             "note": "The image is attached as an image block; inspect the pixels directly",
                                             "meta": _img_txt},
                                            ensure_ascii=False)
                    elif isinstance(result, list):
                        result = json.dumps(result, ensure_ascii=False, default=str)
                    _push({"type": "tool_result", "name": name, "content": str(result)[:preview_chars]})
                    messages.append({"role": "tool", "tool_call_id": tc.get("id") or name,
                                     "content": str(result)[:proc_max_chars]})
                if _pending_images:
                    _vis = [{"type": "text", "text": "[System] The previous tool returned the following images; inspect the pixels directly:"}]
                    _vis += [{"type": "image_url", "image_url": {"url": _u}} for _u in _pending_images]
                    messages.append({"role": "user", "content": _vis})
                    _pending_images.clear()
            summary = summary or "(subagent hit the round limit without a clear conclusion)"

    _hard = int(_T.get("abort") or SUBAGENT_TIMEOUT_DEFAULT)
    try:
        await asyncio.wait_for(_loop(), timeout=max(10, _hard))
    except asyncio.TimeoutError:
        seconds = round(time.time() - t0, 1)
        _pf.reset_current_profile(_tok)
        return {"ok": False, "error": f"Subtask timed out ({_hard} s)", "goal": goal, "profile": _prof_name,
                "status": "failed", "steps": steps, "rounds": rounds_used, "seconds": seconds}
    except Exception as e:
        _pf.reset_current_profile(_tok)
        return {"ok": False, "error": f"Subagent error: {e}", "goal": goal, "profile": _prof_name,
                "status": "failed", "steps": steps, "rounds": rounds_used,
                "seconds": round(time.time() - t0, 1)}

    if not summary:
        summary = "(subagent produced no conclusion)"
    # 结论状态行（档案 status_line）→ 结构化字段，供主控评估「要不要重派/换档案重派」
    _clean, _meta = _pf.parse_status(summary)
    out = {"ok": True, "summary": _clean or summary, "goal": goal, "context": context,
           "steps": steps, "rounds": rounds_used, "seconds": round(time.time() - t0, 1),
           "profile": _prof_name}
    out.update(_meta)
    if _meta.get("status") == "failed":
        out["ok"] = False
        out["error"] = _meta.get("missing") or "subagent reported failed"
    _pf.reset_current_profile(_tok)
    return out


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
                                     emit=emit, index=i,
                                     role=str(t.get("role", "") or ""),
                                     profile=str(t.get("profile", "") or ""))
            res["index"] = i
            if not res.get("goal"):
                res["goal"] = t.get("goal", "")
            if not res.get("profile"):
                res["profile"] = str(t.get("profile", "") or "")
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
