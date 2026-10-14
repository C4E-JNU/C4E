# -*- coding: utf-8 -*-
"""轮次账本（turn ledger）—— v2：把每轮生成的全过程按顺序落盘，作为消息树的真相源。

设计约束（用户 2026-10-02 定；2026-10-03 扩为「完整消息树」）：
  · 只追加，绝不改动 chats.json / history.jsonl —— 历史数据零风险；
  · 粒度：「轮次 + 每轮推理 + 每步工具」，不记逐 token（丢一轮可接受）；
  · 中断即停，不自动续跑；下次由 build_resume_note() 拼出进度摘要注入消息。
  · v2 变更（2026-10-03，后端接管消息树）：
      1. 事件集扩全：turn_start / reasoning / text / step / notice / final / turn_end，
         覆盖前端 reasoningSteps 的全部 type（model_reasoning / tool_call / tool_result /
         timeout_notice），使本文件可被 agent/projection.py 投影成 branch.messages；
      2. 工具结果**不再截到 2000 字**（旧值让真相源比 SSE 出口还穷），改为
         全量落盘 + 200k 安全上限 + 显式 `truncated` 标记（绝不静默丢）；
      3. 双编号：`round`（模型轮次，对应前端 model_reasoning.step）与
         `n`（工具序号，全局递增，修旧实现「一轮多工具 → step 号重复」）；
      4. `begin_turn` 记 `client_id`，供多端广播过滤回声。

落点：workspace/users/<身份>/turns/<branch_id>.jsonl
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

_WORKSPACE = Path(__file__).parent.parent.parent / "workspace"

# 单个工具结果落盘上限（字符）。**这不是「截断到 2k」那个谎**：
# 只有超过 200k 的巨型结果（整篇 PDF 文本之类）才封顶，且会在记录里显式标 truncated。
MAX_RESULT_CHARS = 200_000

# ── 工具副作用分级：只读的可直接重跑；有副作用的标「效果未知」，重试前要确认 ──
READ_ONLY_TOOLS = {
    "read_file", "list_files", "search_files", "find_files", "list_outputs",
    "graph_query", "web_search", "web_extract", "shared_search", "search_history",
    "skill_view", "skill_list", "crossref_lookup", "arxiv_search",
    "semantic_scholar_search", "gh_repo_info", "gh_list_dir", "gh_read_file",
    "gh_search_repos", "gh_search_code", "todo",
}
SIDE_EFFECT_TOOLS = {
    "write_file", "edit_file", "terminal", "run_code", "conversation_notes",
    "shared_upload", "delegate_task", "skill_write", "mineru_parse",
}


def effect_of(tool_name: str) -> str:
    """none=只读可重跑 / unknown=有副作用，效果未知。未知工具一律保守按 unknown。"""
    if tool_name in READ_ONLY_TOOLS:
        return "none"
    if tool_name in SIDE_EFFECT_TOOLS:
        return "unknown"
    return "unknown"


def _user_key(u: str) -> str:
    """与 agent.py / file_tools / vector_search 完全一致的目录名规则。"""
    u = (u or "").strip() or "guest"
    if u == "guest":
        return "_guest"
    k = "".join(c for c in u if (c.isalnum() or c in "-_@.·"))
    return k or "_guest"


def _branch_slug(branch_id: str) -> str:
    s = "".join(c for c in (branch_id or "") if c.isalnum() or c in "-_")
    return s or "default"


def _turn_file(user: str, branch_id: str) -> Path:
    d = _WORKSPACE / "users" / _user_key(user) / "turns"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_branch_slug(branch_id)}.jsonl"


def _append(path: Path, obj: dict) -> None:
    obj.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def _read_records(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _s(v) -> str:
    """把任意结果统一成字符串（投影与落盘共用）。"""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        return str(v)


def new_turn_id() -> str:
    return "t" + uuid.uuid4().hex[:12]


# ══════════════════════════════════════════════════════
#  写入侧
# ══════════════════════════════════════════════════════

def begin_turn(user: str, branch_id: str, turn_id: str, user_input: str,
               client_id: str = "") -> None:
    """一轮生成开始：写下 turn_start（含发起端 client_id，供广播过滤回声）。"""
    _append(_turn_file(user, branch_id), {
        "type": "turn_start", "turn_id": turn_id,
        "branch_id": branch_id or "", "input": user_input or "",
        "client_id": client_id or "",
    })


def append_reasoning(user: str, branch_id: str, turn_id: str, round_no: int, text: str) -> None:
    """一轮模型的推理内容（reasoning_content）→ 投影为 model_reasoning。"""
    t = text or ""
    if not t.strip():
        return
    _append(_turn_file(user, branch_id), {
        "type": "reasoning", "turn_id": turn_id, "round": int(round_no or 0), "text": t,
    })


def append_text(user: str, branch_id: str, turn_id: str, round_no: int, text: str) -> None:
    """一轮模型的正文（工具轮的中间正文）→ 投影时作 final 缺失的兜底。"""
    t = text or ""
    if not t.strip():
        return
    _append(_turn_file(user, branch_id), {
        "type": "text", "turn_id": turn_id, "round": int(round_no or 0), "text": t,
    })


def append_step(user: str, branch_id: str, turn_id: str, step_no: int,
                tool: str, args, result, ok: bool = True, round_no: int = 0) -> None:
    """一步工具执行完（结果已在手）就立刻落盘 —— 不等轮末，防中断丢步。

    落**全量** result（上限 MAX_RESULT_CHARS，超限显式标 truncated），
    不是推给前端的 2k 截断版。投影时展开为 tool_call + tool_result 两条。
    """
    s = _s(result)
    truncated = len(s) > MAX_RESULT_CHARS
    _append(_turn_file(user, branch_id), {
        "type": "step", "turn_id": turn_id, "n": int(step_no or 0),
        "round": int(round_no or 0),
        "tool": tool, "args": args if isinstance(args, (str, dict, list)) else str(args),
        "ok": bool(ok), "effect": effect_of(tool),
        "result": s[:MAX_RESULT_CHARS], "truncated": truncated, "result_len": len(s),
    })


def append_notice(user: str, branch_id: str, turn_id: str, level: str,
                  text: str, elapsed: int = 0) -> None:
    """超时催促/警告/终止/立即回答 → 投影为 timeout_notice。"""
    _append(_turn_file(user, branch_id), {
        "type": "notice", "turn_id": turn_id, "level": level or "nudge",
        "text": text or "", "elapsed": int(elapsed or 0),
    })


def append_final(user: str, branch_id: str, turn_id: str, text: str) -> None:
    """本轮最终答复全文 → 投影为 assistant 消息的 content。"""
    _append(_turn_file(user, branch_id), {
        "type": "final", "turn_id": turn_id, "text": text or "",
    })


def append_op(user: str, branch_id: str, op: dict) -> bool:
    """补偿事件（改历史用）：edit / truncate / regen。

    jsonl 只增不改，所以「编辑消息」「删除后续消息」只能追加一条补偿记录，
    由投影（agent/projection.py 的 _apply_ops）按顺序套用。
    好处：账本天然是完整历史 —— 连「你删过什么」都能回溯。
    """
    t = (op or {}).get("type")
    if t not in ("edit", "truncate", "regen", "delete"):
        return False
    rec = {"type": t, "op": True}
    for k in ("target", "from", "from_ts", "text", "turn_id", "supersedes"):
        if k in (op or {}):
            rec[k] = op[k]
    _append(_turn_file(user, branch_id), rec)
    return True


def finalize(user: str, branch_id: str, turn_id: str, status: str,
             partial_text: str = "", error: str = "") -> bool:
    """收尾。CAS：同一 turn 只允许第一次生效（三个中断出口 + 正常出口只写一次）。"""
    path = _turn_file(user, branch_id)
    for r in _read_records(path):
        if r.get("type") == "turn_end" and r.get("turn_id") == turn_id:
            return False
    _append(path, {
        "type": "turn_end", "turn_id": turn_id, "status": status,
        "partial_text": (partial_text or "")[:20000], "error": error or "",
    })
    return True


# ══════════════════════════════════════════════════════
#  读取侧
# ══════════════════════════════════════════════════════

def load_branch_events(user: str, branch_id: str) -> list:
    """该对话的全部账本事件（按写入顺序）。投影用。"""
    return _read_records(_turn_file(user, branch_id))


def has_turns(user: str, branch_id: str) -> bool:
    return bool(load_branch_events(user, branch_id))


def load_last_turn(user: str, branch_id: str):
    """返回最近一个 turn：{turn_id, input, status, steps, partial_text}；无则 None。"""
    recs = _read_records(_turn_file(user, branch_id))
    starts = [i for i, r in enumerate(recs) if r.get("type") == "turn_start"]
    if not starts:
        return None
    i0 = starts[-1]
    tid = recs[i0].get("turn_id")
    turn = {"turn_id": tid, "input": recs[i0].get("input", ""),
            "status": "running", "steps": [], "partial_text": "", "error": ""}
    for r in recs[i0:]:
        if r.get("turn_id") != tid:
            continue
        t = r.get("type")
        if t == "step":
            turn["steps"].append(r)
        elif t == "turn_end":
            turn["status"] = r.get("status", "complete")
            turn["partial_text"] = r.get("partial_text", "")
            turn["error"] = r.get("error", "")
    return turn


def build_resume_note(user: str, branch_id: str, skip: bool = False,
                      current_input: str = "") -> str:
    """上一次生成若是中断的，生成一段进度说明，供下次消息注入（不自动执行）。

    skip=True：本次是「重新生成」——用户要的是重做，不是续跑，绝不能注入中断摘要
    （否则模型会以为要接着上次的进度干）。
    另有一道自动判断：被中断那轮的输入与本次输入**完全相同** → 同样视为重新生成，
    这样前端「重新生成」即使不额外传参也拦得住（双保险）。
    """
    if skip:
        return ""
    turn = load_last_turn(user, branch_id)
    if not turn or turn.get("status") != "interrupted":
        return ""
    if current_input and (turn.get("input") or "").strip() == (current_input or "").strip():
        return ""
    steps = turn.get("steps", [])
    lines = [f"[系统·上次中断] 上一轮回答被停止，已执行 {len(steps)} 步工具调用："]
    for s in steps:
        tag = "（只读，可重跑）" if s.get("effect") == "none" else "（⚠️ 有副作用，效果未知）"
        lines.append(f"  {s.get('n') or s.get('step')}. {s.get('tool')} {tag}")
    if turn.get("partial_text"):
        lines.append("上一轮已写出的部分正文（未完成）：")
        lines.append(turn["partial_text"][:1500])
    lines.append("用户若说「继续」，请从中断处接着做，不要重复执行有副作用的步骤"
                 "（如确需重试，先确认其效果再动）。")
    return "\n".join(lines)


def orphan_side_effects(user: str, branch_id: str) -> list:
    """上一次中断时「有副作用且结果未知」的步骤，供上层提示或检查。"""
    turn = load_last_turn(user, branch_id)
    if not turn or turn.get("status") != "interrupted":
        return []
    return [s for s in turn.get("steps", []) if s.get("effect") == "unknown"]
