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
    "1. 先用最少的工具调用收集事实（{rounds_hint}），不要反复试探。\n"
    "2. 完成后必须给出一段**结构化结论摘要**：结论要点 + 关键数据 + 来源（如有）。\n"
    "3. 摘要要能独立阅读：不要写“如上”“见上文”这类依赖上下文的话。\n"
    "4. 不要向用户提问；信息不足时，就在摘要里写明“信息不足：<缺什么>”。\n"
    "5. 你无法派发子智能体，也不要尝试写文件或执行终端命令。\n"
    "6. **语言跟随用户**：结论摘要与**内部思考/推理（reasoning、thinking）应尽量使用用户消息所用的语言**；专业英文术语保留原样。"
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
        return {"ok": False, "error": f"未知子智能体档案「{_pname}」（可用：见 /api/agents/profiles）",
                "goal": goal, "steps": [], "profile": _pname}
    _prof_name = _pname or "__generic__"
    _pp = _prof.get("params") or {}
    _tok = _pf.set_current_profile(_prof)      # 技能根 / 技能白名单 / 读上限 由此生效
    proc_max_chars = int(_pp.get("tool_result_max_chars") or 4000)
    preview_chars = int(_pp.get("tool_result_preview_chars") or 2000)
    ctx_max_chars = int(_pp.get("user_context_max_chars") or 6000)
    # 显式 max_images=0 = 该档案声明「不读图」：图片一律不内联（旧写法 `or 3` 会把 0 吃掉）
    _mi = _pp.get("max_images")
    max_images = 3 if _mi is None else max(0, int(_mi))

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
        if v not in (None, ""):
            cfg[k] = v
    if not cfg.get("model"):
        return {"ok": False, "error": "子智能体无法启动：缺少模型配置", "goal": goal,
                "steps": [], "profile": _prof_name}
    endpoint = _endpoint(cfg.get("base_url", ""))
    if not endpoint:
        return {"ok": False, "error": "子智能体无法启动：模型 base_url 为空", "goal": goal,
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

    _sys = _SUBAGENT_SYSTEM.format(rounds_hint=("建议 ≤ %d 轮" % int(max_rounds)
                                               if int(max_rounds) > 0 else "轮次不限，按需取数即可"))
    _sys += SUBAGENT_ROLE_PROMPTS.get(role, "")
    _sys += ("\n" + _pf.system_prompt_for(_prof)) if _pf.system_prompt_for(_prof) else ""
    if _prof.get("status_line", True):
        _sys += _pf.STATUS_HINT
    messages = [
        {"role": "system", "content": _sys},
        {"role": "user", "content": f"【子任务】{goal}\n\n【背景/上下文】\n{(context or '（无额外上下文）')[:ctx_max_chars]}"},
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
                    _push({"type": "notice", "text": "子智能体被用户停止"})
                    return
                # ── 子智能体超时保护：只剩一级强制中断（2026-10-13 删催促/警告）──
                _el = time.time() - t0
                if _T.get("abort") and _el >= _T["abort"]:
                    _push({"type": "notice",
                           "text": f"已达终止时限 {_T['abort']}s（实际 {int(_el)}s），子智能体被中断。"})
                    summary = summary or f"（子任务超时中断：超过 {_T['abort']}s）"
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
                            result = res if isinstance(res, (str, list)) else json.dumps(res, ensure_ascii=False)
                        except Exception as e:
                            result = json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)
                    # ── image_read：先抽出图片，再把正文换成桩（必须在 4000 截断之前）──
                    _img_u, _img_txt = _split_img(result)
                    if _img_u:
                        if max_images > 0 and len(_pending_images) < max_images:
                            _pending_images.append(_img_u)
                            _img_note = "图片已作为图像附加，可直接查看像素"
                        else:
                            _img_note = ("本次未内联图片：当前档案 max_images=%d（声明为非读图档案）。"
                                         "不要试图靠读 base64 文本猜图内容——需要图面数据请改由 "
                                         "fenton-image / fenton-verifier 档案处理，或读归档的 FigureN.txt 图注文字。"
                                         % max_images)
                        result = json.dumps({"ok": True, "mode": "image",
                                             "note": _img_note,
                                             "meta": _img_txt},
                                            ensure_ascii=False)
                    elif isinstance(result, list):
                        result = json.dumps(result, ensure_ascii=False, default=str)
                    _push({"type": "tool_result", "name": name, "content": str(result)[:preview_chars]})
                    messages.append({"role": "tool", "tool_call_id": tc.get("id") or name,
                                     "content": str(result)[:proc_max_chars]})
                if _pending_images:
                    _vis = [{"type": "text", "text": "[系统] 上一步工具返回的图片如下，可直接读取像素："}]
                    _vis += [{"type": "image_url", "image_url": {"url": _u}} for _u in _pending_images]
                    messages.append({"role": "user", "content": _vis})
                    _pending_images.clear()
            summary = summary or "（子智能体达到轮次上限，未给出明确结论）"

    _hard = int(_T.get("abort") or SUBAGENT_TIMEOUT_DEFAULT)
    try:
        await asyncio.wait_for(_loop(), timeout=max(10, _hard))
    except asyncio.TimeoutError:
        seconds = round(time.time() - t0, 1)
        _pf.reset_current_profile(_tok)
        return {"ok": False, "error": f"子任务超时（{_hard}s）", "goal": goal, "profile": _prof_name,
                "status": "failed", "steps": steps, "rounds": rounds_used, "seconds": seconds}
    except Exception as e:
        _pf.reset_current_profile(_tok)
        return {"ok": False, "error": f"子智能体异常：{e}", "goal": goal, "profile": _prof_name,
                "status": "failed", "steps": steps, "rounds": rounds_used,
                "seconds": round(time.time() - t0, 1)}

    if not summary:
        summary = "（子智能体未产出结论）"
    # 结论状态行（档案 status_line）→ 结构化字段，供主控评估「要不要重派/换档案重派」
    _clean, _meta = _pf.parse_status(summary)
    out = {"ok": True, "summary": _clean or summary, "goal": goal, "context": context,
           "steps": steps, "rounds": rounds_used, "seconds": round(time.time() - t0, 1),
           "profile": _prof_name}
    out.update(_meta)
    if _meta.get("status") == "failed":
        out["ok"] = False
        out["error"] = _meta.get("missing") or "子智能体自报 failed"
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
