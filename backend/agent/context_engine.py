"""单次回答内的上下文治理（方案 A · A0）。

本模块只解决一件事：**一次回答内部**，工具调用循环的 messages 数组只增不减 →
20 步就能自己撑爆窗口。跨轮历史（_build_context）不归这里管。

设计对照（只取设计，不搬代码）：
  · Hermes（MIT）agent/context_engine.py::prune_tool_results_only / threshold_percent=0.75 /
    update_from_response(usage)（用 API 真实用量校准，不靠字符估算）
  · Claude Code 的 microcompact 三条纪律：
      ① 只清「模型之后能自己重新取回」的工具结果（白名单限定，技能正文/用户消息不清）
      ② 按 token 预算保留最近工具结果（keep_recent_tokens），且保底至少留 1 条——清光了模型就没有工作上下文
      ③ **只原地替换 content，绝不删除消息**：删一条 tool 消息会留下孤儿 tool_call，
         协议层直接报错，比上下文爆更糟

本模块是纯函数 + 一个预算对象，不依赖项目其它模块，可单独测试。
"""

import json

# ── 可清理白名单 ──────────────────────────────────────
# 判据：这些工具的结果**模型之后能自己重新调一次拿回来**。
# 反例（绝不清）：技能正文（skill_view 的结果是"知识"不是"数据"）、用户消息、计划/待办。
PRUNABLE_TOOLS = {
    "read_file",        # 可带 offset/limit 重读
    "list_files",       # 可重列
    "search_files",     # 可重搜
    "find_files",
    "web_search",       # 可重搜（词不同结果不同，但可接受）
    "web_extract",      # 可按 URL 重取
    "terminal",         # 可重跑
    "run_code",         # 可重跑
    "graph_query",      # 可重查（Neo4j）
    "shared_search",    # 可重查（共享库）
    "search_history",   # 可重查
    "crossref_lookup",
    "arxiv_search",
    "semantic_scholar_search",
    "gh_search_repos",
    "gh_search_code",
    "gh_read_file",
    "gh_list_dir",
    "gh_repo_info",
}

# ── 单个工具结果的字符上限（T0.1：上限是工具属性，不是循环里的魔法数）──
TOOL_RESULT_CAPS = {
    "read_file": 20000,
    "web_extract": 15000,
    "web_search": 8000,
    "terminal": 12000,
    "run_code": 12000,
    "graph_query": 15000,
    "shared_search": 15000,
    "search_history": 10000,
    "skill_view": 30000,      # 技能正文偏大但必须完整：它属于"知识"，不在可清名单里
    "mineru_parse": 4000,
    "list_files": 6000,
    "find_files": 6000,
    "search_files": 12000,
    "gh_read_file": 20000,
    "gh_search_code": 12000,
    "arxiv_search": 12000,
    "semantic_scholar_search": 12000,
    "crossref_lookup": 8000,
    "delegate_task": 20000,
}
DEFAULT_RESULT_CAP = 12000     # 未列出的工具用这个（策略值，不是兜底：新工具必须显式登记才更宽松）

PLACEHOLDER = "[该工具结果已清理以腾出上下文：{name}{brief}；需要时用它重新调用一次即可拿回]"


def cap_tool_result(name: str, result, budget_chars=None) -> str:
    """把单个工具结果转成要回喂的文本，超上限则**显式声明**被截（绝不静默截断）。

    budget_chars：本轮剩余预算（None = 只用工具自身上限）。
    返回文本；截断时文本里带着"怎么续读"的出路，避免模型以为已经读全。
    """
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    cap = TOOL_RESULT_CAPS.get(name, DEFAULT_RESULT_CAP)
    if budget_chars is not None:
        cap = max(2000, min(cap, int(budget_chars)))     # 预算再紧也至少给 2000 字符，否则模型没法干活
    if len(text) <= cap:
        return text
    return (text[:cap] +
            "\n…[结果被截断：%s 返回 %d 字符，只回喂前 %d 字符。"
            "需要更多内容请分页续取（read_file 带 offset/limit、search_files 带 offset、terminal 缩小输出范围）]"
            % (name, len(text), cap))


def _brief_args(tc: dict) -> str:
    """从 assistant 的 tool_call 里取一个很短的参数摘要，放进占位符，让模型知道"我当初查的是什么"。"""
    try:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            args = json.loads(args or "{}")
        if not isinstance(args, dict):
            return ""
        keep = ("file_path", "path", "file_id", "pattern", "query", "url", "action", "cypher", "command", "code")
        bits = []
        for k in keep:
            if k in args and args[k] not in (None, "", []):
                v = str(args[k]).replace("\n", " ")
                bits.append("%s=%s" % (k, v[:60]))
        if not bits:
            k0 = next(iter(args), None)
            if k0 is not None:
                bits.append("%s=%s" % (k0, str(args[k0])[:60]))
        return "(" + ", ".join(bits) + ")" if bits else ""
    except Exception:
        return ""


def _prunable_slots(messages: list, whitelist=None):
    """列出当前可清理的工具结果槽位，按消息顺序（早→晚）。

    每个槽位为 (index, tool_name, brief_args, tokens)。已经清过的不再重复计入。
    """
    wl = PRUNABLE_TOOLS if whitelist is None else set(whitelist)
    name_by_id = {}
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                name_by_id[tc.get("id")] = (
                    _brief_args(tc),
                    ((tc.get("function") or {}).get("name") or "?"),
                )
    slots = []
    for i, m in enumerate(messages if isinstance(messages, list) else []):
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        brief, name = name_by_id.get(m.get("tool_call_id"), ("", "?"))
        if name not in wl:
            continue
        if m.get("content") == PLACEHOLDER.format(name=name, brief=brief):
            continue
        slots.append((i, name, brief, estimate_tokens([m])))
    return slots


def _protected_tail(messages: list, keep_recent_tokens: int, whitelist=None) -> set:
    """从最新工具结果往回累计 token，返回必须保留的可清理槽位下标。

    预算口径是 token，不是条数，且把不可清理的工具结果（如技能正文）也计入占用：
    它们不能清，但会挤占最近结果的保留空间。无论预算多小，至少保留最新一条可清理结果，
    否则模型在收尾时会失去当前工作上下文。
    """
    wl = PRUNABLE_TOOLS if whitelist is None else set(whitelist)
    keep_recent_tokens = max(1, int(keep_recent_tokens))
    name_by_id = {}
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                name_by_id[tc.get("id")] = ((tc.get("function") or {}).get("name") or "?")

    prunable_idx = []
    for i, m in enumerate(messages if isinstance(messages, list) else []):
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        name = name_by_id.get(m.get("tool_call_id"), "?")
        if name in wl:
            prunable_idx.append(i)
    protected = {prunable_idx[-1]} if prunable_idx else set()

    kept = 0
    kept_count = 0
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        tok = estimate_tokens([m])
        if kept_count > 0 and kept + tok > keep_recent_tokens:
            break
        name = name_by_id.get(m.get("tool_call_id"), "?")
        if name in wl:
            protected.add(i)
        kept += tok
        kept_count += 1
    return protected


def prune_tool_results_only(messages: list, keep_recent_tokens: int = 24000, whitelist=None) -> int:
    """把最近 token 预算之外的可重取工具结果换成占位符（原地，不删消息）。

    仅用于上下文已溢出、需要尽量腾空间的场景：从最早的可清理结果开始，
    清掉所有落在 keep_recent_tokens 预算之外的结果；最新一条始终保留。
    返回被清理的条数。
    """
    slots = _prunable_slots(messages, whitelist)
    if not slots:
        return 0
    protected = _protected_tail(messages, keep_recent_tokens, whitelist)
    cleared = 0
    for idx, name, brief, _tok in slots:
        if idx in protected:
            continue
        messages[idx]["content"] = PLACEHOLDER.format(name=name, brief=brief)
        cleared += 1
    return cleared


def prune_tool_results_to_target(messages: list, target_tokens: int,
                                 keep_recent_tokens: int = 24000, whitelist=None) -> int:
    """按 token 目标清理：只清到估算总量回落到 target_tokens 为止。

    两个关键点：
      · **先清最早的结果**，而不是按“保留最近 N 条”一刀切；
      · 总量未超过目标时不做任何清理——达到工具循环上限不等于必须清上下文。

    keep_recent_tokens 是最近结果的 token 保留预算（下限保护），不是条数。
    返回被清理的条数。
    """
    slots = _prunable_slots(messages, whitelist)
    if not slots:
        return 0
    target_tokens = max(1, int(target_tokens))
    total = estimate_tokens(messages)
    if total <= target_tokens:
        return 0
    protected = _protected_tail(messages, keep_recent_tokens, whitelist)
    cleared = 0
    for idx, name, brief, tok in slots:          # 早 → 晚
        if idx in protected:
            continue
        placeholder = PLACEHOLDER.format(name=name, brief=brief)
        saved = tok - estimate_tokens([{"role": "tool", "content": placeholder}])
        if saved <= 0:
            continue
        messages[idx]["content"] = placeholder
        total -= saved
        cleared += 1
        if total <= target_tokens:
            break
    return cleared


def estimate_tokens(messages: list) -> int:
    """粗估本轮 token（CJK 约 1.6 字符/token，ASCII 约 3.5）。**只用于请求前的预判**，
    真实值一律以 update_from_response() 收到的 API usage 为准。"""
    total = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            s = c
        elif isinstance(c, list):
            s = "".join(part.get("text", "") for part in c if isinstance(part, dict))
        else:
            s = ""
        cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
        total += int(cjk / 1.6 + (len(s) - cjk) / 3.5) + 8
        for tc in (m.get("tool_calls") or []):
            total += int(len(str((tc.get("function") or {}).get("arguments", ""))) / 3.5) + 12
    return total


class ContextBudget:
    """按 API 真实用量决定"该不该清旧工具结果"。

    · threshold_percent=0.75：用量到窗口 75% 就清（留出输出与缓冲）
    · 熔断：连续失败 3 次后不再尝试（真实事故的教训：失败会话会连续空耗 API）
    · 防递归：压缩自身的调用不会再触发压缩
    """

    def __init__(self, window_tokens: int = 262144, max_output_tokens: int = 8192,
                 threshold_percent: float = 0.75, keep_recent_tokens: int | None = None,
                 trigger_override: int = 0):
        self.window_tokens = int(window_tokens)
        self.max_output_tokens = int(max_output_tokens)
        self.threshold_percent = float(threshold_percent)
        _krt = int(keep_recent_tokens or 0)
        self.keep_recent_tokens = _krt if _krt > 0 else max(8000, int(self.window_tokens * 0.25))
        self.trigger_override = max(0, int(trigger_override or 0))   # 前端显式设定的触发线；0 = 自动
        self.last_prompt_tokens = 0        # 来自 API usage
        self.messages_since_real_usage = 0
        self.consecutive_failures = 0
        self.pruned_total = 0
        self.in_compression = False        # 防递归

    # ── 真实用量校准 ────────────────────────────
    def update_from_response(self, usage) -> None:
        if not usage:
            return
        try:
            pt = int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else int(getattr(usage, "prompt_tokens", 0) or 0)
        except Exception:
            return
        if pt > 0:
            self.last_prompt_tokens = pt
            self.messages_since_real_usage = 0

    @property
    def trigger_tokens(self) -> int:
        """清理触发线（超过它才开始把旧工具结果换占位符）。

        · 前端显式设定 context_budget → 用它（上限为窗口本身，不大于窗口）
        · 否则自动：(窗口 − 输出预留) × 75%
          - 输出预留取 min(max_output, 窗口/4)：输出上限若被填得比窗口还大（UI 可填任意值），
            原公式会算出**负数**触发线 → 每轮都判「该清」（真实踩过的坑）
          - 最终**保底 ≥ 1024**，绝不返回 0 或负数

        注意：触发线只是「开始清理」的软线，**不是硬约束**——本机不会依据它或窗口值报错/截断，
        真的超出模型能力由模型端报错（源头报错才算真报错）。
        """
        if self.trigger_override > 0:
            return max(1, min(self.trigger_override, self.window_tokens))
        reserve = min(max(0, self.max_output_tokens), max(1, self.window_tokens // 4))
        return max(1024, int((self.window_tokens - reserve) * self.threshold_percent))

    def should_compress(self, prompt_tokens=None) -> bool:
        if self.in_compression or self.consecutive_failures >= 3:
            return False
        pt = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        return pt >= self.trigger_tokens

    def should_compress_info(self, prompt_tokens=None):
        """(bool, 原因) —— 原因给 UI/日志用。"""
        if self.consecutive_failures >= 3:
            return False, "连续失败 %d 次，已熔断" % self.consecutive_failures
        if self.in_compression:
            return False, "压缩进行中（防递归）"
        pt = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        if pt >= self.trigger_tokens:
            src = ("用户设定 context budget=%d" % self.trigger_override) if self.trigger_override > 0 \
                else "自动：窗口 %d 的 %.0f%%" % (self.window_tokens, self.threshold_percent * 100)
            return True, "用量 %d ≥ 触发线 %d（%s）" % (pt, self.trigger_tokens, src)
        return False, "用量 %d < 触发线 %d" % (pt, self.trigger_tokens)

    def record_compress_result(self, ok: bool) -> None:
        self.in_compression = False
        self.consecutive_failures = 0 if ok else self.consecutive_failures + 1

    def status(self) -> dict:
        return {"window": self.window_tokens, "trigger": self.trigger_tokens,
                "last_prompt_tokens": self.last_prompt_tokens,
                "keep_recent_tokens": self.keep_recent_tokens,
                "consecutive_failures": self.consecutive_failures,
                "pruned_total": self.pruned_total}
