# -*- coding: utf-8 -*-
"""对话历史的后端存储（用户 2026-09-26 定）。

背景：对话（分支 + 全部消息 + 分支笔记）原本只存在浏览器 localStorage，
换浏览器/换设备就全丢 —— 而它恰恰是最该后端化的东西。

存储位置：workspace/users/<身份>/chats.json
数据结构：
    {
      "branches": { "branch-1": {...分支对象（含 messages/notes/updatedAt）...}, ... },
      "deleted":  { "branch-0": "2026-09-26T04:31:00.123Z" },   # 墓碑：id → 删除时间
      "branchOrder": ["branch-2", "branch-1"],
      "_updated": "2026-09-26 12:20:11"                          # 服务器写入时间（轮询/推送用）
    }

★ 并发模型（用户 2026-09-26 定，几经反复后的最终版）
  用户先提过「限制单账号单会话」，随后收回：「两边应该允许同时登录，
  a 进行的对话会同步到 b 设备的前端」。→ 于是**允许多写者**，
  写入语义必须从「整份覆盖」改成**按对话 id 逐个合并**，否则 A、B 互相抹。

  · 每个对话带 `updatedAt`（ISO-8601 UTC，前端在内容变化时打戳）
  · 合并规则：同一 id → `updatedAt` 较新的赢；不同 id → 并集
  · **删除必须用墓碑**（`deleted`）：否则 A 删掉的对话会被 B 那份旧副本推回来。
    这是多设备下必然会发生的（两台同时在线）。
  · `currentBranchId`（"上次打开的是哪个"）**不参与同步** —— 那是每台设备
    各自的视图状态，A 停在对话1、B 停在对话2 本来是正常的。
  · 时间戳靠设备本地钟 → 有偏差时可能判错先后。同一台人、同一内网，可接受。

  为什么不 SSE 而先轮询：轮询纯同步、无锁、不可能卡死；
  SSE 要维护订阅表+长连接+断线清理（多一份常驻状态）。见 app.py 的 /api/events。

安全（与 user_settings 同款原则）：owner **一律从登录 token 推导，绝不接受请求体传入**。
"""
import json
import os
import time

from tools.file_tools import _user_dir

CHAT_KEYS = ("branches", "deleted", "branchOrder")

# 单份对话文件上限（防手滑传进来一个巨型对象把磁盘写爆）
MAX_BYTES = 16 * 1024 * 1024

# 墓碑保留上限：超过就按时间从旧到新丢，避免无限增长（每条几十字节，够用很久）
MAX_TOMBSTONES = 500


def _chats_file(username: str) -> str:
    return os.path.join(_user_dir(username), "chats.json")


def _read_raw(username: str) -> dict:
    path = _chats_file(username)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}   # 文件损坏 → 当作空（前端会把本地对话重新推上来）


def load_user_chats(username: str) -> str:
    """读该身份的全部对话，返回 {"status":"ok","chats":{...}}。

    文件不存在 → 空对象（前端据此判断「后端还没有，本地有就当种子推上来」）。
    """
    raw = _read_raw(username)
    data = {k: v for k, v in raw.items() if k in CHAT_KEYS}
    return json.dumps({"status": "ok", "chats": data}, ensure_ascii=False)


def chats_rev(username: str) -> str:
    """轻量版本号（给轮询/推送判断「变了没」用）：文件里最后一次写入时间 + 对话数。
    只有几十字节 —— 前端每 10 秒问这个，变了才拉全量。"""
    raw = _read_raw(username)
    return json.dumps({"rev": raw.get("_updated", ""),
                       "branches": len(raw.get("branches") or {}),
                       "deleted": len(raw.get("deleted") or {})}, ensure_ascii=False)


def _ts(b) -> str:
    """取对话/墓碑的时间戳，没有就空串（空串最小 → 输给任何有时间戳的副本）。"""
    if not isinstance(b, dict):
        return ""
    return str(b.get("updatedAt") or "")


def save_user_chats(username: str, payload: dict) -> str:
    """**按对话 id 合并**写入（不是覆盖）。返回写入结果（含落盘核对）。

    合并规则：
      · 传入的新对话：本地没有 → 收下；已有 → `updatedAt` 新的赢
      · 传入的墓碑：把该 id 删掉（若本地那份更旧），并记下墓碑
      · 已有但本次没提到的对话：**保留**（那是另一台设备的，不是"被删了"）
      · branchOrder：并集去重（两边各自排过的都留着）
    """
    payload = payload or {}
    body = {k: v for k, v in payload.items() if k in CHAT_KEYS}
    if not body:
        return json.dumps({"status": "error", "error": "payload 里没有可写入的字段 "
                                                     f"（应为 {list(CHAT_KEYS)}）"}, ensure_ascii=False)

    cur = _read_raw(username)
    branches = dict(cur.get("branches") or {})
    deleted = dict(cur.get("deleted") or {})
    order = list(cur.get("branchOrder") or [])

    incoming = body.get("branches")
    if isinstance(incoming, dict):
        for bid, b in incoming.items():
            if not isinstance(b, dict):
                continue
            old = branches.get(bid)
            if old is None:
                # 本地没有：但若已被墓碑删过、且这份副本不比墓碑新 → 丢弃（别复活）
                tomb_ts = str(deleted.get(bid) or "")
                if tomb_ts and _ts(b) <= tomb_ts:
                    continue
                branches[bid] = b
            elif _ts(b) > _ts(old):
                branches[bid] = b

    inc_del = body.get("deleted")
    if isinstance(inc_del, dict):
        for bid, ts in inc_del.items():
            ts = str(ts or "")
            old = branches.get(bid)
            if old is not None:
                if _ts(old) <= ts:          # 本地那份不比墓碑新 → 真删
                    branches.pop(bid, None)
                else:
                    continue                # 本地那份更新（可能是删后又改）→ 不删
            if ts > str(deleted.get(bid) or ""):
                deleted[bid] = ts

    inc_order = body.get("branchOrder")
    if isinstance(inc_order, list):
        for bid in inc_order:
            if bid not in order:
                order.append(bid)

    # 墓碑太多时按时间丢最旧的
    if len(deleted) > MAX_TOMBSTONES:
        keep = sorted(deleted.items(), key=lambda kv: str(kv[1] or ""), reverse=True)[:MAX_TOMBSTONES]
        deleted = dict(keep)

    out = {"branches": branches, "deleted": deleted, "branchOrder": order}
    dumped = json.dumps(out, ensure_ascii=False)
    if len(dumped.encode("utf-8")) > MAX_BYTES:
        return json.dumps({"status": "error",
                           "error": f"对话数据过大（{len(dumped.encode('utf-8')) // 1024 // 1024}MB），"
                                    f"上限 {MAX_BYTES // 1024 // 1024}MB"}, ensure_ascii=False)
    out["_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    path = _chats_file(username)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, path)

    # ★ 落盘核对：回读确认（不轻信写入回执）
    back = _read_raw(username)
    ok = all(k in back for k in ("branches", "deleted", "branchOrder"))

    return json.dumps({
        "status": "ok" if ok else "error",
        "verified": bool(ok),
        "branches": len(back.get("branches") or {}),
        "deleted": len(back.get("deleted") or {}),
        "rev": back.get("_updated", ""),
    }, ensure_ascii=False)
