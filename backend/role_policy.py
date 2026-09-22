"""C4EAI 角色权限中心（仅对云端共享库实例生效；本地实例不设 C4EAI_AUTH → 全部放行，各人管自己机器）。

矩阵（用户 2026-09-21 审定）：
  未登录            → 中间件 401（除登录/注册/健康检查）
  已注册未授权      → 仅聊天；可用工具∩{notes,search_history,web_search,web_extract}；本地千问 500万 token/天；文件基座锁本人目录；无图谱/MinerU/子智能体/执行类
  approved(注册授权)→ 聊天不限自带模型；本地千问 1亿/天；工具全开，但禁 run_code/terminal/skill_write；cypher 删语句拒；文件基座锁本人目录+pool
  admin(管理员)     → 全部功能（含执行类）；本地千问不限；不可编辑他人 pool 条目
  主管理员(admin账号)→ 全部 + 角色调整 + 改他人 pool + 改服务器 MinerU token；不可被降级/删除

说明：run_code 的“沙箱”只是子进程+关键词过滤，非真隔离，故执行类工具默认只给管理员。
"""
import json, os, re, threading, datetime
from pathlib import Path

_WORKSPACE = Path(__file__).parent.parent / "workspace"
_USAGE_FILE = _WORKSPACE / "_auth" / "usage.json"
_LOCK = threading.Lock()

HUB = os.environ.get("C4EAI_AUTH", "") == "1"          # True=云端共享库实例，False=本地实例
SUPER_USER = "admin"
QWEN_UNAPPROVED = 5_000_000
QWEN_APPROVED = 100_000_000
CHAT_ONLY_TOOLS = {"conversation_notes", "search_history", "web_search", "web_extract"}
EXEC_TOOLS = {"run_code", "terminal", "skill_write"}   # approved 仍不可用
_CYPHER_DEL = re.compile(r"\b(DELETE|DETACH|DROP)\b", re.IGNORECASE)

# 按请求设置的“当前用户类别”全局（与 file_tools 的 _PERMISSION_MODE 同机制：每请求入口设置一次）
_CURRENT_CLS = "super"

def set_current(user: dict):
    global _CURRENT_CLS
    _CURRENT_CLS = classify(user) if HUB else "super"


def agent_cypher_denied(cypher: str) -> bool:
    """供 agent 内 execute_cypher 工具调用：approved 的删除类语句拒。"""
    return HUB and _CURRENT_CLS == "approved" and bool(_CYPHER_DEL.search(cypher or ""))


def classify(user: dict) -> str:
    """user: {username, role, approved} → super/admin/approved/unapproved"""
    if not user:
        return "unapproved"
    name = user.get("username") or ""
    role = user.get("role") or "user"
    if name == SUPER_USER:
        return "super"
    if role == "admin":
        return "admin"
    if user.get("approved"):
        return "approved"
    return "unapproved"


def filter_tools(user: dict, requested: list) -> list:
    """云端按角色裁剪 agent 可用工具；本地实例原样返回。"""
    if not HUB:
        return list(requested or [])
    t = set(requested or [])
    cls = classify(user)
    if cls == "unapproved":
        return sorted(t & CHAT_ONLY_TOOLS)
    if cls == "approved":
        return sorted(t - EXEC_TOOLS)
    return sorted(t)  # admin / super


def enforce_mode(user: dict, requested_mode: str) -> str:
    """云端把 approved/unapproved 的 permission_mode 钳到 safe（前端传什么都无效）。"""
    if not HUB:
        return requested_mode or "safe"
    if classify(user) in ("approved", "unapproved"):
        return "safe"
    return requested_mode or "safe"


def user_sandbox(user: dict):
    """approved/unapproved 的文件基座锁到本人目录 + pool；admin/super 不锁（None）。"""
    if not HUB:
        return None
    if classify(user) in ("approved", "unapproved"):
        return {"user": user.get("username") or "guest"}
    return None


def is_local_qwen(model_config: dict) -> bool:
    if not model_config:
        return False
    base = (model_config.get("base_url") or "")
    model = (model_config.get("model") or "")
    return (":8080" in base) or ("c4eai" in base and "8080" in base) or ("本地" in model) \
        or ("qwen3-local" in model.lower()) or ("Qwen3.6" in model)


def cypher_denied(user: dict, cypher: str) -> bool:
    """图谱删除类语句：approved 拒，admin/super 放行；unapproved 根本没有图谱工具。"""
    if not HUB:
        return False
    if classify(user) == "approved" and _CYPHER_DEL.search(cypher or ""):
        return True
    return False


def tool_denied(user: dict, tool: str) -> bool:
    """直接面向前端的 /api/tool/* 端点的角色闸门。tool∈code/read-file/cypher/fetch/schema。"""
    if not HUB:
        return False
    cls = classify(user)
    if tool == "code":
        return cls not in ("admin", "super")          # 执行类：仅管理员/主管理员
    return cls == "unapproved"                         # 其余工具类：未授权一律拒


def mineru_write_allowed(user: dict) -> bool:
    return (not HUB) or classify(user) == "super"


def _load_usage() -> dict:
    try:
        d = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_usage(d: dict):
    _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _USAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _USAGE_FILE)


def _qwen_limit(cls: str):
    return {"unapproved": QWEN_UNAPPROVED, "approved": QWEN_APPROVED}.get(cls)  # admin/super→None 不限


def estimate_tokens(chars: int) -> int:
    return int(chars * 0.6) + 1


def qwen_usage(user: dict):
    """返回 (今日已用token, 上限 或 None=不限, 是否仍可用)。"""
    cls = classify(user)
    limit = _qwen_limit(cls)
    if not HUB or limit is None:
        return 0, None, True
    today = datetime.date.today().isoformat()
    rec = _load_usage().get(user.get("username") or "", {})
    used = rec.get("tokens") if rec.get("date") == today else 0
    return used, limit, used < limit


def add_qwen_usage(user: dict, chars: int):
    if not HUB or _qwen_limit(classify(user)) is None or chars <= 0:
        return
    today = datetime.date.today().isoformat()
    with _LOCK:
        d = _load_usage()
        name = user.get("username") or ""
        rec = d.get(name, {})
        if rec.get("date") != today:
            rec = {"date": today, "tokens": 0}
        rec["tokens"] = int(rec.get("tokens", 0)) + estimate_tokens(chars)
        d[name] = rec
        _save_usage(d)


def all_usage() -> dict:
    """今日各账号本地千问用量（管理面板展示）。"""
    today = datetime.date.today().isoformat()
    d = _load_usage()
    return {name: (rec.get("tokens") if rec.get("date") == today else 0) for name, rec in d.items()}
