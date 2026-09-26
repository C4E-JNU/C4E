"""C4EAI 角色权限中心（仅对云端共享库实例生效；本地实例不设 C4EAI_AUTH → 全部放行，各人管自己机器）。

═══ 等级体系（用户 2026-09-22 审定：用颜色代表身份）═══

  L0 游客(灰)   未登录/已注册未授权 → 聊天可用；工具受限；**不可用云端数据库**；本地千问 500万 token/天
  L1 蓝色       自用数据库         → 上传/查询/修改/删除**自己**的；工具全开；本地千问 500万 token/天
  L2 紫色       自用数据库         → 同 L1（颜色区分身份，能力一致）；本地千问 500万 token/天
  L3 橙色       查所有人/改自己    → 查询**所有人**条目；修改/删除**仅自己**；本地千问 500万 token/天
  L4 红色       不限 token         → 同 L3 的数据库权限，但本地千问**不限量**
  L5 金色       可调身份           → 同 L3 数据库权限；**可调整 L0-L4 的身份等级**
  主管理员(admin)                 → 全部权限；可删除/修改**所有人**的数据库；可改任何非 admin 权限；自身不可被降级/删除

说明：
- 「限制 token」= 本地千问（8080）按日限额；自带模型（用户自己的 API key）不受此限。
- run_code 的"沙箱"只是子进程+关键词过滤，非真隔离，故执行类工具只给主管理员。
- 颜色仅作身份可视化：蓝(L1)/紫(L2)/橙(L3)/红(L4)/金(L5)，游客灰。
"""
import json, os, re, threading, datetime
from pathlib import Path

_WORKSPACE = Path(__file__).parent.parent / "workspace"
_USAGE_FILE = _WORKSPACE / "_auth" / "usage.json"
_LOCK = threading.Lock()

HUB = os.environ.get("C4EAI_AUTH", "") == "1"          # True=云端共享库实例，False=本地实例
SUPER_USER = "admin"

# ── 等级常量 ──
L_GUEST, L_BLUE, L_PURPLE, L_ORANGE, L_RED, L_GOLD = 0, 1, 2, 3, 4, 5
LEVEL_NAMES = {0: "游客", 1: "蓝色", 2: "紫色", 3: "橙色", 4: "红色", 5: "金色"}
LEVEL_COLORS = {0: "#9aa9be", 1: "#3b82f6", 2: "#a855f7", 3: "#f59e0b", 4: "#ef4444", 5: "#eab308"}

# 本地千问日限额（token 估算；None=不限）
QWEN_LIMITED = 5_000_000        # L0-L3：限制
# L4/L5/admin：不限

# 游客可用工具（受限）
GUEST_TOOLS = {"conversation_notes", "search_history", "web_search", "web_extract"}
# 执行类工具：仅主管理员
EXEC_TOOLS = {"run_code", "terminal", "skill_write"}
# 数据库相关工具（需 L1+ 才能用云端数据库）
DB_TOOLS = {"shared_search", "shared_upload"}

_CYPHER_DEL = re.compile(r"\b(DELETE|DETACH|DROP)\b", re.IGNORECASE)

# 按请求设置的"当前用户类别"全局（与 file_tools 的 _PERMISSION_MODE 同机制：每请求入口设置一次）
_CURRENT_CLS = "super"


def level_of(user: dict) -> int:
    """把用户字典换算成 0-5 等级。admin 恒为 5（另有 super 特权标记）。"""
    if not user:
        return L_GUEST
    name = user.get("username") or ""
    if name == SUPER_USER:
        return L_GOLD                      # 主管理员拥有 L5 全部能力 + 额外特权
    lv = user.get("level")
    if isinstance(lv, int) and 0 <= lv <= 5:
        return lv
    # 兼容旧数据：role=admin 视为 L5；approved=True 视为 L1；否则游客
    if (user.get("role") or "") == "admin":
        return L_GOLD
    if user.get("approved"):
        return L_BLUE
    return L_GUEST


def is_super(user: dict) -> bool:
    """主管理员：全部权限 + 可改他人数据库 + 可删任何非 admin 账号。

    ⚠️ 语义已扩展（用户 2026-09-22 定：金色 L5 与管理员权限一致）：
       本函数现在表示「管理级权限」= 主管理员 admin **或** 金色 L5。
       但有两个能力仍为 admin 独有，须用 is_root() 判断：
         · 删除他人账号
         · 授予/调整 L5 等级（防止金色互相提权、或批量造金色）
    """
    if not user:
        return False
    if (user.get("username") or "") == SUPER_USER:
        return True
    return level_of(user) >= L_GOLD


def is_root(user: dict) -> bool:
    """主管理员本体（admin）。用于 admin 独有的两项能力：删账号、授予 L5。"""
    return bool(user) and (user.get("username") or "") == SUPER_USER


def set_current(user: dict):
    global _CURRENT_CLS
    _CURRENT_CLS = classify(user) if HUB else "super"


# ── 兼容层：把等级映射回旧类别名，避免其它模块改动 ──
def classify(user: dict) -> str:
    """user → 'super' | 'admin' | 'approved' | 'unapproved'（保留旧签名）。"""
    if not user:
        return "unapproved"
    if is_super(user):
        return "super"
    lv = level_of(user)
    if lv >= L_BLUE:          # L1 及以上都算"已授权"
        return "approved"
    return "unapproved"


# ── 能力查询 ──

def can_query_all(user: dict) -> bool:
    """能否查询所有人的数据库条目。L3(橙) 起。"""
    return (not HUB) or level_of(user) >= L_ORANGE


def can_manage_levels(user: dict) -> bool:
    """能否调整他人身份等级。L5(金) 或主管理员。"""
    return (not HUB) or level_of(user) >= L_GOLD


def agent_cypher_denied(cypher: str) -> bool:
    """供 agent 内 execute_cypher 工具调用：非主管理员的删除类语句拒。"""
    if not HUB:
        return False
    return _CURRENT_CLS != "super" and bool(_CYPHER_DEL.search(cypher or ""))


def filter_tools(user: dict, requested: list) -> list:
    """云端按等级裁剪 agent 可用工具；本地实例原样返回。"""
    if not HUB:
        return list(requested or [])
    t = set(requested or [])
    lv = level_of(user)
    if lv == L_GUEST:
        # 游客：受限工具集，且不含数据库工具
        return sorted((t & GUEST_TOOLS) - DB_TOOLS)
    if is_super(user) or lv >= L_RED:
        # 主管理员 / L4红 / L5金：全部工具（用户 2026-09-22 定：红色以上给全部工具）
        return sorted(t)
    if lv >= L_BLUE:
        return sorted(t - EXEC_TOOLS)                       # L1-L3：除执行类
    return sorted(t & GUEST_TOOLS)


def enforce_mode(user: dict, requested_mode: str) -> str:
    """云端把非管理级的 permission_mode 钳到 safe（前端传什么都无效）。

    L5 金 / 主管理员 → 保留用户所选模式（用户 2026-09-22 定：金色与管理员一致）。
    """
    if not HUB:
        return requested_mode or "safe"
    if is_super(user):
        return requested_mode or "safe"
    return "safe"


def user_sandbox(user: dict) -> dict | None:
    """文件基座：L0-L3 锁到本人目录 + pool；L4红/L5金/主管理员不锁（可读写全盘）。

    用户 2026-09-22 定：
      · 橙色 L3 及以下 → 锁定本人目录（不放开读写）
      · 红色 L4 及以上 → 不锁，全部读写
    """
    if not HUB:
        return None
    if is_super(user) or level_of(user) >= L_RED:
        return None
    return {"user": user.get("username") or "guest"}


def is_local_qwen(model_config: dict) -> bool:
    if not model_config:
        return False
    base = (model_config.get("base_url") or "")
    model = (model_config.get("model") or "")
    return (":8080" in base) or ("c4eai" in base and "8080" in base) or ("本地" in model) \
        or ("qwen3-local" in model.lower()) or ("Qwen3.6" in model)


def cypher_denied(user: dict, cypher: str) -> bool:
    """图谱删除类语句：管理级（L5金/admin）放行，其余（含 L4红）拒。"""
    if not HUB:
        return False
    return (not is_super(user)) and bool(_CYPHER_DEL.search(cypher or ""))


def tool_denied(user: dict, tool: str) -> bool:
    """直接面向前端的 /api/tool/* 端点的等级闸门。tool∈code/read-file/cypher/fetch/schema。

    用户 2026-09-22 定：红色 L4 及以上可用全部工具（含执行类），须与 filter_tools 口径一致。
    """
    if not HUB:
        return False
    lv = level_of(user)
    if tool == "code":
        return not (is_super(user) or lv >= L_RED)   # 执行类：L4红/L5金/admin
    return lv == L_GUEST                             # 其余工具类：游客一律拒


def mineru_write_allowed(user: dict) -> bool:
    """矿工(MinerU) token 写操作：仅主管理员。"""
    return (not HUB) or is_super(user)


# ── 本地千问用量 ──

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


def _qwen_limit(user_or_cls) -> int | None:
    """L4(红) 起不限 token；L0-L3 限制。兼容传入旧类别名。"""
    if isinstance(user_or_cls, str):
        return None if user_or_cls in ("admin", "super") else QWEN_LIMITED
    lv = level_of(user_or_cls)
    return None if lv >= L_RED else QWEN_LIMITED


def estimate_tokens(chars: int) -> int:
    return int(chars * 0.6) + 1


def qwen_usage(user: dict):
    """返回 (今日已用token, 上限 或 None=不限, 是否仍可用)。"""
    limit = _qwen_limit(user)
    if not HUB or limit is None:
        return 0, None, True
    today = datetime.date.today().isoformat()
    rec = _load_usage().get(user.get("username") or "", {})
    used = rec.get("tokens") if rec.get("date") == today else 0
    return used, limit, used < limit


def add_qwen_usage(user: dict, chars: int):
    if not HUB or _qwen_limit(user) is None or chars <= 0:
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


# ── 等级调整（供 /api/auth/level 端点使用）──

def can_set_level(actor: dict, target_username: str, new_level: int) -> tuple[bool, str]:
    """判断 actor 能否把 target 调整为 new_level。
    规则：
      - 非 HUB（本地实例）：允许（自己机器随便改）
      - 目标不存在 / 目标是 admin(主管理员)：任何人不可改
      - 只能改 L0-L4（L5 金 只能由主管理员授予，见下）
      - actor 为 L5(金)：可改 L0-L4 任意用户
      - actor 为主管理员：可改任何非 admin 用户（含授予 L5）
      - 其余：不可
    """
    if not HUB:
        return True, "ok"
    if not isinstance(new_level, int) or not (0 <= new_level <= 5):
        return False, "等级必须是 0-5 的整数"
    if target_username == SUPER_USER:
        return False, "主管理员不可被调整"
    if is_root(actor):
        return True, "ok"                       # 主管理员：可授任何等级（含 L5）
    if can_manage_levels(actor):
        if new_level >= L_GOLD:
            return False, "金色等级只能由主管理员授予"
        return True, "ok"                       # 金色：可调 L0-L4
    return False, "你没有调整他人等级的权限"


def default_level_for_new_user() -> int:
    """自由注册的默认等级 = L1 蓝色（用户 2026-09-22 定：注册即可用自己数据库）。
    如需改回"注册后需开通"，把返回值改为 L_GUEST。"""
    return L_BLUE
