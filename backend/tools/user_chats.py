# -*- coding: utf-8 -*-
"""对话历史的后端存储（用户 2026-09-26 定）。

背景：对话（分支 + 全部消息 + 分支笔记）原本只存在浏览器 localStorage，
换浏览器/换设备就全丢 —— 而它恰恰是最该后端化的东西。

存储位置：workspace/users/<身份>/chats.json（与 settings.json / notes.json 同级）
数据结构（整份，一个扁平对象）：
    {
      "branches":        { "branch-1": {...全部分支对象（含 messages/notes）...}, ... },
      "branchOrder":     ["branch-2", "branch-1", ...],   # 侧栏排序
      "currentBranchId": "branch-1",                      # 上次打开的是哪个
      "_updated":        "2026-09-26 09:20:11"
    }

并发模型（用户 2026-09-26 定）：**单账号单会话**（见 auth.login：登录即踢掉该账号旧
token）→ 同一时刻只有一个写者 → 整份覆盖即可，**不做合并、不做冲突处理**。
理由：多设备合并那套逻辑（按 id 并集、版本指针、冲突提示）是复杂且易坏的架构，
而单点登录从根上消除了并发写，简单且不会卡死。

安全（与 user_settings 同款原则）：owner **一律从登录 token 推导，绝不接受请求体传入**。
"""
import json
import os
import time

from tools.file_tools import _user_dir

# 参与同步的字段（payload 里只认这几个，其余忽略）
CHAT_KEYS = ("branches", "branchOrder", "currentBranchId")

# 单份对话文件的上限（防手滑传进来一个巨型对象把磁盘写爆）。
# 5MB 是 localStorage 的量级上限，本地都装得下，后端没有理由装不下。
MAX_BYTES = 16 * 1024 * 1024


def _chats_file(username: str) -> str:
    return os.path.join(_user_dir(username), "chats.json")


def load_user_chats(username: str) -> str:
    """读该身份的全部对话，返回 JSON 字符串 {"status":"ok","chats":{...}}。

    文件不存在 → 空对象（前端据此判断「后端还没有，本地有就当种子推上来」）。
    """
    path = _chats_file(username)
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = {k: v for k, v in raw.items() if k in CHAT_KEYS}
        except Exception:
            data = {}   # 文件损坏 → 当作空（前端会把本地对话重新推上来）
    return json.dumps({"status": "ok", "chats": data}, ensure_ascii=False)


def save_user_chats(username: str, payload: dict) -> str:
    """整份写入该身份的对话。返回写入结果（含落盘核对）。

    语义 = **覆盖**（不是合并）：payload 里出现的字段整体替换。
    注意 branches 是整棵树，所以「删掉一个对话」也能正确同步过去
    （合并语义做不到这点 —— 删掉的对话会被别处的旧副本复活）。
    """
    payload = payload or {}
    body = {k: v for k, v in payload.items() if k in CHAT_KEYS}
    if not body:
        return json.dumps({"status": "error", "error": "payload 里没有可写入的字段 "
                                                     f"（应为 {list(CHAT_KEYS)}）"}, ensure_ascii=False)

    dumped = json.dumps(body, ensure_ascii=False)
    if len(dumped.encode("utf-8")) > MAX_BYTES:
        return json.dumps({"status": "error",
                           "error": f"对话数据过大（{len(dumped.encode('utf-8')) // 1024 // 1024}MB），"
                                    f"上限 {MAX_BYTES // 1024 // 1024}MB"}, ensure_ascii=False)

    out = dict(body)
    out["_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    path = _chats_file(username)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, path)

    # ★ 落盘核对：回读确认（不轻信写入回执）。
    # 判据 = 「本次打算写入的字段」是否都真的落盘且可解析出分支数。
    back = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            back = json.load(f)
    except Exception:
        back = {}
    ok = all(k in back for k in body)

    return json.dumps({
        "status": "ok" if ok else "error",
        "verified": bool(ok),
        "branches": len(back.get("branches") or {}),
        "bytes": len(dumped.encode("utf-8")),
        "updated": back.get("_updated", ""),
    }, ensure_ascii=False)
