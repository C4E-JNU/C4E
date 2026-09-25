# -*- coding: utf-8 -*-
"""个人设置的后端存储（用户 2026-09-26 定）。

背景：密钥/模型/智能体配置等设置原本存在浏览器 localStorage。
现改为存「**个人账号数据**」——与个人笔记、附件同级：
    workspace/users/<身份>/settings.json
用户明确：密钥「放在个人账号数据上，反正也就开发者知道密钥」。

安全（与 WeKnora 同款原则）：
  owner **一律从请求上下文（登录 token）推导，绝不接受请求体传入** ——
  否则可以越权读写他人设置。见 app.py 里两个端点的实现。

数据结构：整份设置就是**一个扁平 dict**（key → 值），与前端 localStorage 的
键名保持一致，前端才能「读回来直接写进 localStorage」当缓存用。
"""
import json
import os
import time

from tools.file_tools import _user_dir

# 存后端的设置键白名单（其余键留在浏览器本地 —— 见前端注释）
# 为什么是白名单：避免把「面板位置/主题」这类纯本机偏好也同步到别的机器上。
MANAGED_KEYS = (
    "ai-settings",                   # 温度 / 上下文轮次 / maxTokens
    "ai-current-model",              # 当前选中的模型
    "ragagent-provider-settings",    # 服务商设置（★ 含 API Key）
    "ragagent-custom-providers",     # 自定义服务商（★ 含 API Key）
    "ragagent-provider-overrides",   # 服务商覆盖项
    "ragagent-enabled-models",       # 已启用的模型
    "ragagent-agent-config",         # 智能体配置（工具 / skills / 超时）
    "ragagent-neo4j-config",         # Neo4j 连接（★ 含密码）
)


def _settings_file(username: str) -> str:
    return os.path.join(_user_dir(username), "settings.json")


def load_user_settings(username: str) -> str:
    """读该身份的全部后端设置，返回 JSON 字符串 {"settings": {...}}。

    文件不存在 → 返回空 dict（前端据此判断「首次登录，需要把本地设置传上来」）。
    """
    path = _settings_file(username)
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                # 只返回白名单内的键（历史遗留的其它键不返回）
                data = {k: v for k, v in raw.items() if k in MANAGED_KEYS}
        except Exception:
            data = {}   # 文件损坏 → 当作空（前端会把本地设置重新推上来）
    return json.dumps({"status": "ok", "settings": data}, ensure_ascii=False)


def save_user_settings(username: str, patch: dict) -> str:
    """合并写入（patch 里只取白名单键）。返回写入后的全量设置。

    patch 的值传 None 表示删除该键（前端「清空某项设置」用）。
    """
    path = _settings_file(username)
    cur = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                cur = raw
        except Exception:
            cur = {}

    for k, v in (patch or {}).items():
        if k not in MANAGED_KEYS:
            continue
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    cur["_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    # ★ 落盘核对：回读确认（不轻信写入回执）
    # 判据 = 「本次**打算写入**的键」是否都真的落盘了 —— 即白名单内且值非 None。
    # 白名单外被拒绝的键不参与核对（它们本就不该落盘）。
    back = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            back = json.load(f)
    except Exception:
        back = {}
    intended = [k for k, v in (patch or {}).items() if k in MANAGED_KEYS and v is not None]
    ok = all(back.get(k) == (patch or {})[k] for k in intended)

    rejected = [k for k in (patch or {}) if k not in MANAGED_KEYS]

    return json.dumps({
        "status": "ok" if ok else "error",
        "verified": bool(ok),
        "settings": {k: v for k, v in back.items() if k in MANAGED_KEYS},
        "count": len([k for k in back if k in MANAGED_KEYS]),
        "rejected": rejected,
    }, ensure_ascii=False)
