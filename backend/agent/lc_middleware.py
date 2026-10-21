"""LangChain 引擎的上下文护栏 middleware（2026-10-14）。

背景：stream_agent_lc 此前没有任何上下文治理，长对话会原样撑爆窗口；
custom 引擎有 prune_tool_results_only/cap_tool_result（context_engine.py）。
本模块把同一套策略平移到 LangChain 消息对象上，两引擎行为对齐。

设计纪律与 context_engine 相同（Claude Code microcompact 三条）：
  ① 只清白名单内（模型能自己重新取回）的工具结果，原地替换 content，绝不删消息
  ② 保留最近 keep_recent 条，保底 1 条
  ③ 单个结果超 TOOL_RESULT_CAPS 上限的，任何时候都截断
"""

from langchain.agents.middleware import before_model
from langchain_core.messages import AIMessage, ToolMessage

from agent.context_engine import PRUNABLE_TOOLS, TOOL_RESULT_CAPS, DEFAULT_RESULT_CAP, PLACEHOLDER


def _estimate_lc_tokens(messages) -> int:
    """粗估 token：中文字≈1 token，ASCII≈4 字符 1 token，按 len//3 一刀切。"""
    total = 0
    for m in messages:
        c = getattr(m, "content", None)
        if isinstance(c, str):
            total += max(1, len(c) // 3)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    total += max(1, len(str(b.get("text") or b.get("thinking") or "")) // 3)
                else:
                    total += max(1, len(str(b)) // 3)
        elif c is not None:
            total += max(1, len(str(c)) // 3)
    return total


def _brief_args(args) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    try:
        items = list(args.items())[:3]
        s = ", ".join(f"{k}={str(v)[:40]}" for k, v in items)
        return f"（{s}）"
    except Exception:
        return ""


def _prune_lc_tool_results(messages, keep_recent: int = 3) -> int:
    """原地把白名单内、超出最近 keep_recent 条的 ToolMessage.content 换成占位符。"""
    tool_idx = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    if len(tool_idx) <= max(1, keep_recent):
        return 0
    name_by_id = {}
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in (m.tool_calls or []):
                name_by_id[tc.get("id")] = (tc.get("name") or "?",
                                            _brief_args(tc.get("args")))
    cleared = 0
    for i in tool_idx[:-max(1, keep_recent)]:
        m = messages[i]
        name = getattr(m, "name", None) or name_by_id.get(getattr(m, "tool_call_id", ""), ("?", ""))[0]
        if name not in PRUNABLE_TOOLS:
            continue
        brief = name_by_id.get(getattr(m, "tool_call_id", ""), ("", ""))[1]
        new = PLACEHOLDER.format(name=name, brief=brief)
        if isinstance(m.content, str) and m.content == new:
            continue
        m.content = new
        cleared += 1
    return cleared


def _cap_lc_tool_results(messages) -> int:
    """单个工具结果超上限的截断（任何时刻都做，与是否触发清理无关）。"""
    capped = 0
    for m in messages:
        if not isinstance(m, ToolMessage) or not isinstance(m.content, str):
            continue
        name = getattr(m, "name", "") or ""
        budget = TOOL_RESULT_CAPS.get(name, DEFAULT_RESULT_CAP)
        if len(m.content) > budget:
            m.content = (m.content[:budget]
                         + f"\n\n[结果过长已截断：{name} 原始 {len(m.content)} 字符，"
                           f"需要完整内容请带范围参数重新调用]")
            capped += 1
    return capped


def make_context_guard(context_window: int):
    """返回一个 before_model middleware：超阈值清旧工具结果 + 始终截断超长结果。

    context_window：当前模型的上下文窗口（token），从 model_config 传入。
    """
    trigger = max(8000, int(context_window * 0.5))

    @before_model
    def context_guard(state, config):
        messages = state.get("messages") or []
        _cap_lc_tool_results(messages)
        if _estimate_lc_tokens(messages) > trigger:
            _prune_lc_tool_results(messages, keep_recent=3)
        return None

    return context_guard
