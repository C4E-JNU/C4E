# -*- coding: utf-8 -*-
"""投影层：把轮次账本（turns/*.jsonl）投影成前端的 branch 对象（含 messages）。

「后端接管消息树」的地基：
    真相源      = turns/<branch_id>.jsonl（append-only 事件流，见 turn_store.py）
    投影产物    = branch.messages（前端 GET /api/user/chats 直接渲染的结构）
    chats.json  = 投影快照（缓存 + 兼容那些不刷新的老前端）

★ 三条安全阀（缺一不可，都是为了「绝不弄丢已有对话」）：
  1. **账本为空 → 绝不投影**（返回 None）。老对话（10 个分支、155 条消息）没有
     对应账本，投影一跑就会产出空 messages 把它覆盖掉 —— 这条拦住它。
  2. **只追加、不删改**：投影在原有 branch 上「追加账本消息」，legacy 消息一条不删。
     账本消息 id 为 `msg-u-<turn_id>` / `agent-<turn_id>`，与前端 legacy id 天然不撞。
  3. **异常一律吞掉并返回 None**：投影/写盘出问题绝不能影响正在进行的对话。

投影产物与前端 js/graph_rag.js 实际落库的结构逐字段对齐（经 chats.json 实测核对）：
    branch  : {id,name,messages,parentId,createdAt,totalTokens,sessionTokens,hasContent,notes,updatedAt}
    user msg: {id,role,content,tokens,timestamp,branchId,thinkingChain,type,attachments,noteRefs}
    asst msg: {id,role,content,timestamp,branchId,reasoningSteps[]}
    step 元素: model_reasoning{type,text,step} / tool_call{type,name,input}
               / tool_result{type,name,content} / timeout_notice{type,level,text,elapsed}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from agent import turn_store as _turn_store

# 总开关（环境变量）：off / shadow / on
#   off    —— 完全不投影（保持旧行为）
#   shadow —— 只投影到日志对拍，**不写** chats.json（阶段 1 观察期用）
#   on     —— 投影并写 chats.json，后端成为消息树生产者（默认）
_TREE_MODE = (os.environ.get("C4EAI_TREE_MODE") or "on").strip().lower()
if _TREE_MODE not in ("off", "shadow", "on"):
    _TREE_MODE = "on"


def tree_mode() -> str:
    return _TREE_MODE


def _iso_now() -> str:
    """与前端 `new Date().toISOString()` 完全同款格式：YYYY-MM-DDTHH:MM:SS.mmmZ。

    格式必须一致 —— 后端按字符串比较 updatedAt 判新旧（user_chats.py 的 _ts），
    若这里产出 '+00:00' 结尾的 ISO，与前端 'Z' 结尾的串比较会得出错误的新旧关系。
    """
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def _bump(prev: str) -> str:
    """生成严格大于 prev 的时间戳（同一毫秒内连续两次生成时 +1ms）。"""
    now = _iso_now()
    if prev and now <= str(prev):
        try:
            base = datetime.strptime(str(prev)[:23], "%Y-%m-%dT%H:%M:%S.%f")
            base = base.replace(tzinfo=timezone.utc)
            from datetime import timedelta
            nxt = base + timedelta(milliseconds=1)
            return nxt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (nxt.microsecond // 1000)
        except Exception:
            return now
    return now


def _norm_ts(ts: str) -> str:
    """账本 ts（2026-10-03T03:57:37）→ 前端 ISO（2026-10-03T03:57:37.000Z）。"""
    s = str(ts or "").strip()
    if not s:
        return _iso_now()
    if s.endswith("Z"):
        return s
    if "T" in s:
        return s + (".000Z" if "." not in s else "Z")
    return s


def _as_text(v) -> str:
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        return str(v)


_AGENT_FINAL_RE = __import__("re").compile(r"^agent-final-(\d{10,})$")


def _epoch_ms_iso(ms: str) -> str:
    """epoch 毫秒 → 前端同款 ISO（YYYY-MM-DDTHH:MM:SS.mmmZ）。"""
    try:
        from datetime import datetime as _dt
        ms = int(ms)
        # 账本 turn ts 用的是**本地墙钟**加 Z 后缀（turn_store 的写法），
        # 这里必须同帧比较：用本地时区格式化，而不是真 UTC（否则时差整 8 小时）。
        return _dt.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S.") \
            + "%03dZ" % (ms % 1000)
    except Exception:
        return ""


def _apply_truncate(blocks: list, e: dict) -> None:
    """对**当前已累积**的 turn 块套用一条 truncate 补偿（就地裁剪列表尾部）。

    被点名的 turn 及其后的 turn 全部丢弃；op 之后才出现的 turn 不受影响
    （调用方按事件顺序回放，本函数只看得见 op 之前的块）。
    """
    cut_tids, cut_ts = set(), ""
    tgt = str(e.get("from") or "")
    if tgt:
        cut_tids.add(tgt)
        # 允许带前缀的消息 id（agent-X / msg-u-X）：剥前缀后按 turn_id 也匹配一把。
        # 正常前端会规整成 turn_id，但账本里可能存着未规整的旧值。
        for _pfx in ("msg-u-", "agent-"):
            if tgt.startswith(_pfx):
                cut_tids.add(tgt[len(_pfx):])
        # 前端本地消息 id 形如 agent-final-<epoch_ms>：id 本身编码了时间戳，
        # 解析成水位 —— 修好 from_ts 丢失前的旧账（那段 truncate 曾是死操作）。
        _m = _AGENT_FINAL_RE.match(tgt)
        if _m:
            _iso = _epoch_ms_iso(_m.group(1))
            if _iso:
                cut_ts = max(cut_ts, _iso)
    if e.get("from_ts"):
        cut_ts = max(cut_ts, str(e["from_ts"]))
    if not cut_tids and not cut_ts:
        return
    cut_at = None
    for i, k in enumerate(blocks):
        dead = (k["turn_id"] in cut_tids) or bool(cut_ts and _norm_ts(k.get("ts")) >= cut_ts)
        if dead:
            cut_at = i if cut_at is None else min(cut_at, i)
    if cut_at is not None:
        del blocks[cut_at:]


def _turn_blocks(events: list) -> list:
    """把事件流切成 turn 块（保持出现顺序）。"""
    by_turn = {}
    order = []
    for e in events:
        if not isinstance(e, dict):
            continue
        tid = e.get("turn_id") or ""
        if not tid:
            continue
        if tid not in by_turn:
            by_turn[tid] = {"turn_id": tid, "input": "", "ts": "", "events": [],
                            "texts": [], "final": "", "end_ts": "", "partial": "",
                            "status": ""}
            order.append(tid)
        k = by_turn[tid]
        t = e.get("type")
        if t == "turn_start":
            k["input"] = e.get("input", "") or ""
            k["ts"] = e.get("ts", "") or k["ts"]
        elif t in ("reasoning", "step", "notice"):
            k["events"].append(e)
        elif t == "text":
            k["texts"].append(e.get("text", "") or "")
        elif t == "final":
            k["final"] = e.get("text", "") or ""
        elif t == "turn_end":
            k["status"] = e.get("status", "") or ""
            k["end_ts"] = e.get("ts", "") or k["end_ts"]
            k["partial"] = e.get("partial_text", "") or ""
    return [by_turn[t] for t in order]


def _steps_of(block: dict) -> list:
    """一个 turn 的事件 → 前端 reasoningSteps 数组（顺序与实时流一致）。"""
    out = []
    for e in block.get("events") or []:
        t = e.get("type")
        if t == "reasoning":
            out.append({"type": "model_reasoning",
                        "text": e.get("text", "") or "",
                        "step": int(e.get("round") or 0)})
        elif t == "step":
            name = e.get("tool", "") or ""
            args = e.get("args")
            out.append({"type": "tool_call", "name": name,
                        "input": args if isinstance(args, str)
                                 else json.dumps(args, ensure_ascii=False, default=str)})
            res = e.get("result")
            if res is None:
                res = e.get("result_head", "")     # 兼容 v1 账本（旧字段）
            out.append({"type": "tool_result", "name": name,
                        "content": _as_text(res)})
        elif t == "notice":
            out.append({"type": "timeout_notice",
                        "level": e.get("level", "") or "",
                        "text": e.get("text", "") or "",
                        "elapsed": int(e.get("elapsed") or 0)})
    return out


def messages_from_events(events: list, branch_id: str) -> list:
    """账本事件 → 消息数组（一个 turn = 一条 user + 一条 assistant）。"""
    # ── 截断水位（truncate 补偿事件）：**按事件顺序回放**，op 只杀它出现**之前**的 turn ──
    #    账本是 append-only：op 追加在尾部，它之后的 turn 是「删除后又继续聊」的新内容，
    #    绝不能杀（旧实现全局扫描，一条历史 truncate 会把后来聊的全部判死 —— 2026-10-04 实测）。
    #    定位支持：from=<turn_id>（点投影消息）/ from_ts=<时间戳> / agent-final-<epoch>（旧账）。
    blocks = []
    _buf = []

    def _flush(cut_event=None):
        nonlocal _buf
        blocks.extend(_turn_blocks(_buf))
        _buf = []
        if cut_event is not None:
            _apply_truncate(blocks, cut_event)

    for e in events:
        if isinstance(e, dict) and e.get("type") == "truncate":
            _flush(e)
        else:
            _buf.append(e)
    _flush()

    msgs = []
    for k in blocks:
        tid = k["turn_id"]
        ts = _norm_ts(k.get("ts"))
        if (k.get("input") or "").strip():
            msgs.append({
                "id": "msg-u-" + tid, "role": "user", "content": k["input"],
                "tokens": 0, "timestamp": ts, "branchId": branch_id,
                "thinkingChain": None, "type": None,
                "attachments": [], "noteRefs": [],
            })
        content = (k.get("final") or "").strip()
        if not content:
            joined = "".join(k.get("texts") or []).strip()
            content = joined or (k.get("partial") or "").strip()
        steps = _steps_of(k)
        if not content and not steps:
            continue                                  # 空 turn（一开就断）不产消息
        msgs.append({
            "id": "agent-" + tid, "role": "assistant", "content": content,
            "timestamp": _norm_ts(k.get("end_ts") or k.get("ts")),
            "branchId": branch_id, "reasoningSteps": steps,
        })
    return _apply_ops(msgs, events)


def _apply_ops(msgs: list, events: list) -> list:
    """套用编辑补偿事件（edit）。截断（truncate）已在 messages_from_events 里按水位处理。

    jsonl 只增不改：用户的「编辑消息」以一条补偿记录追加在账本尾部，投影时套用 ——
    这样别的设备也能看到这次编辑（用户 2026-10-03 定：前端怎么变，后端就怎么变）。
    target 允许三种写法：turn_id / msg-u-<turn_id> / agent-<turn_id>，也接受消息本身的 id。
    """
    for e in events:
        if not isinstance(e, dict) or e.get("type") != "edit":
            continue
        tgt = str(e.get("target") or "")
        if not tgt:
            continue
        for m in msgs:
            if m.get("id") in (tgt, "msg-u-" + tgt, "agent-" + tgt):
                m["content"] = e.get("text", "") or ""
                break
    return msgs


def project_branch(user: str, branch_id: str, legacy: dict = None):
    """投影某对话。返回完整 branch 对象；**无可投影内容时返回 None**（安全阀 1）。"""
    try:
        events = _turn_store.load_branch_events(user, branch_id)
        if not events:
            return None
        new_msgs = messages_from_events(events, branch_id)
        if not new_msgs:
            return None
        base = dict(legacy) if isinstance(legacy, dict) else {}
        old = [m for m in (base.get("messages") or []) if isinstance(m, dict)]
        # 「账本管理的 id」以账本为唯一真相：投影能再生的（msg-u-t*/agent-t*）必须镜像
        # 账本 —— 账本里已被 truncate/delete 判死的，旧快照里的同名消息是**死数据**，
        # 不能靠「只追加」一直养着（2026-10-04：branch-100 快照里 3 条已删回答反复复活）。
        # agent-final-* 是前端本地流式消息 id（真 legacy），一条不动（安全阀 2 原样生效）。
        import re as _re
        _ledger_id = _re.compile(r"^(?:msg-u-|agent-)t[0-9a-f]+$")
        old = [m for m in old if not _ledger_id.match(str(m.get("id") or ""))]
        have = {m.get("id") for m in old}
        merged = old + [m for m in new_msgs if m.get("id") not in have]
        # ── 单条删除（delete 补偿事件）：前端「删除这条消息」按完整消息 id 精确剔除 ──
        #    target 可能是 turn_id（msg-u-X/agent-X 规整来的）或消息本身 id（含 legacy 本地 id）。
        dead = set()
        for e in events:
            if isinstance(e, dict) and e.get("type") == "delete":
                tgt = str(e.get("target") or "")
                if tgt:
                    dead.add(tgt)
        if dead:
            expanded = set()
            for t in dead:
                expanded.update((t, "msg-u-" + t, "agent-" + t))
            merged = [m for m in merged if m.get("id") not in expanded]
        base["messages"] = merged
        base.setdefault("id", branch_id)
        base.setdefault("name", "")
        base.setdefault("parentId", None)
        base.setdefault("createdAt", _iso_now())
        base.setdefault("notes", [])
        base.setdefault("totalTokens", 0)
        base.setdefault("sessionTokens", 0)
        base["hasContent"] = bool(base["messages"])
        base["id"] = branch_id
        return base
    except Exception as e:                            # 安全阀 3
        print("[projection] project_branch error:", e)
        return None


def sync_branch(user: str, branch_id: str) -> bool:
    """投影 + 写回 chats.json（后端接管写入的唯一入口）。返回是否成功落盘。

    shadow 模式：只打印对拍结果，不写盘。
    """
    if _TREE_MODE == "off":
        return False
    try:
        from tools import user_chats as UC
        raw = json.loads(UC.load_user_chats(user))
        chats = raw.get("chats") or {}
        legacy = (chats.get("branches") or {}).get(branch_id)
        proj = project_branch(user, branch_id, legacy)
        if proj is None:
            return False
        old_n = len((legacy or {}).get("messages") or [])
        new_n = len(proj.get("messages") or [])
        if _TREE_MODE == "shadow":
            print("[projection:shadow] %s/%s legacy=%d -> projected=%d"
                  % (user, branch_id, old_n, new_n))
            return False
        proj["updatedAt"] = _bump(_ts_of(legacy))
        out = json.loads(UC.save_user_chats(user, {"branches": {branch_id: proj}}))
        if out.get("status") != "ok":
            print("[projection] save failed:", out)
            return False
        # 对拍日志：老消息一条不丢是硬要求
        if new_n < old_n:
            print("[projection] ⚠️ 消息数减少 %d -> %d（%s/%s）" % (old_n, new_n, user, branch_id))
        return True
    except Exception as e:
        print("[projection] sync_branch error:", e)
        return False


def _ts_of(b) -> str:
    if not isinstance(b, dict):
        return ""
    return str(b.get("updatedAt") or "")
