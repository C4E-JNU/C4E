"""
C4EAI 账号系统（局域网共享库用）

- 用户存 workspace/_auth/users.json：PBKDF2 哈希 + 盐，不存明文
- 会话 token 持久化到 workspace/_auth/sessions.json（重启不掉线）
- 初始 admin：密码取环境变量 C4EAI_ADMIN_PASSWORD；未设置则生成随机密码并打日志
- 注册开放（自由注册），但数据库权限需 admin 授权（users.json 的 approved 字段）
- admin 可删号：连带删除 workspace/users/<名> 整个目录
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from datetime import datetime

_WORKSPACE = Path(__file__).parent.parent / "workspace"
_AUTH_DIR = _WORKSPACE / "_auth"
_USERS_FILE = _AUTH_DIR / "users.json"
_SESSIONS_FILE = _AUTH_DIR / "sessions.json"
_SESSION_TTL_DAYS = 30

_lock = threading.Lock()


def _load_sessions() -> dict:
    """token -> {username, ts}；过期（30 天）自动丢弃。"""
    try:
        d = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {}
        cut = datetime.now().timestamp() - _SESSION_TTL_DAYS * 86400
        out = {}
        for tok, v in d.items():
            if isinstance(v, dict) and float(v.get("ts", 0)) >= cut:
                out[tok] = v
            elif isinstance(v, str):          # 兼容旧格式 token->username
                out[tok] = {"username": v, "ts": datetime.now().timestamp()}
        return out
    except Exception:
        return {}


def _save_sessions():
    try:
        _AUTH_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _SESSIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_sessions, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _SESSIONS_FILE)
    except Exception:
        pass


_sessions: dict = _load_sessions()  # token -> {username, ts}（磁盘持久化，重启不掉线）


def _load_users() -> dict:
    if _USERS_FILE.exists():
        try:
            return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_users(users: dict):
    _AUTH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _USERS_FILE)


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()


def init_auth():
    """启动时确保 admin 存在。"""
    with _lock:
        users = _load_users()
        changed = False
        if "admin" not in users:
            pw = os.environ.get("C4EAI_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
            salt = secrets.token_hex(16)
            users["admin"] = {
                "salt": salt,
                "hash": _hash_password(pw, salt),
                "role": "admin",
                "level": 5,                    # 主管理员 = 金色(L5) + 额外特权
                "approved": True,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            _save_users(users)
            print(f"[auth] 初始管理员账号已创建：admin / {pw}（仅显示一次，请立即记录）")
        else:
            # 存量用户补 level 字段（按旧的 role/approved 推导等级）
            for name, u in users.items():
                if "level" not in u:
                    if name == "admin" or u.get("role") == "admin":
                        u["level"] = 5
                    elif u.get("approved"):
                        u["level"] = 1          # 旧 approved → 蓝色(L1)
                    else:
                        u["level"] = 0          # 未授权 → 游客(L0)
                    changed = True
                if "approved" not in u:
                    u["approved"] = (u.get("level", 0) >= 1)
                    changed = True
            if changed:
                _save_users(users)
                print("[auth] 存量用户已补 level 字段（金5/蓝1/游0）")


# ── 会话 ──

def login(username: str, password: str) -> str | None:
    users = _load_users()
    u = users.get(username)
    if not u:
        return None
    if not hmac.compare_digest(u["hash"], _hash_password(password, u["salt"])):
        return None
    token = secrets.token_urlsafe(24)
    with _lock:
        _sessions[token] = {"username": username, "ts": datetime.now().timestamp()}
        _save_sessions()
    return token


def logout(token: str):
    with _lock:
        _sessions.pop(token, None)
        _save_sessions()


def user_by_token(token: str) -> dict | None:
    """返回 {username, role, level, approved} 或 None。"""
    with _lock:
        rec = _sessions.get(token)
    username = rec.get("username") if isinstance(rec, dict) else rec
    if not username:
        return None
    u = _load_users().get(username)
    if not u:
        return None
    lv = u.get("level")
    if not isinstance(lv, int):
        # 兼容未迁移的数据
        lv = 5 if (username == "admin" or u.get("role") == "admin") else (1 if u.get("approved") else 0)
    return {"username": username, "role": u.get("role", "user"),
            "level": lv, "approved": bool(u.get("approved")) or lv >= 1}


def set_level(username: str, level: int) -> tuple[bool, str]:
    """设置用户等级（L0-L5）。admin 账号不可被改。调用处已做权限检查。"""
    if not isinstance(level, int) or not (0 <= level <= 5):
        return False, "等级必须是 0-5 的整数"
    if username == "admin":
        return False, "主管理员不可被调整"
    with _lock:
        users = _load_users()
        if username not in users:
            return False, "用户不存在"
        users[username]["level"] = level
        # 同步 approved（L1+ 视为已授权，用于兼容旧逻辑）
        users[username]["approved"] = level >= 1
        # role 保持 user（除非是 admin），颜色代表身份
        if username != "admin":
            users[username]["role"] = "user"
        _save_users(users)
    return True, f"已将 {username} 调整为 {level} 级"


def extract_token(headers) -> str:
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


# ── 用户管理（仅 admin 调用，调用处已鉴权）──

def create_user(username: str, password: str, role: str = "user",
                approved: bool = False, level: int | None = None) -> tuple[bool, str]:
    if not username or not password:
        return False, "用户名和密码不能为空"
    if role not in ("user", "admin"):
        return False, "角色必须是 user 或 admin"
    if len(password) < 4:
        return False, "密码至少 4 位"
    if level is None:
        # 未显式指定：admin→5(金)，approved→1(蓝)，否则 0(游)
        level = 5 if role == "admin" else (1 if approved else 0)
    if not isinstance(level, int) or not (0 <= level <= 5):
        return False, "等级必须是 0-5 的整数"
    with _lock:
        users = _load_users()
        if username in users:
            return False, "用户名已存在"
        salt = secrets.token_hex(16)
        users[username] = {
            "salt": salt,
            "hash": _hash_password(password, salt),
            "role": role,
            "level": level,
            "approved": bool(approved) or level >= 1,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_users(users)
    return True, "ok"


def register_user(username: str, password: str) -> tuple[bool, str]:
    """自由注册：默认 L1 蓝色（注册即可用自己数据库）。
    等级由 role_policy.default_level_for_new_user() 决定，便于日后调整。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名不能为空"
    if any(c in username for c in ('/', '\\', '..', ' ')):
        return False, "用户名不能包含 / \\ .. 空格"
    # 默认等级：L1 蓝色（集中配置在 role_policy，避免多处写死）
    try:
        import role_policy as _rp
        lv = _rp.default_level_for_new_user()
    except Exception:
        lv = 1
    ok, msg = create_user(username, password, role="user", approved=(lv >= 1), level=lv)
    if ok:
        msg = "注册成功，可用自己的数据库"
    return ok, msg


def set_approved(username: str, approved: bool) -> tuple[bool, str]:
    with _lock:
        users = _load_users()
        if username not in users:
            return False, "用户不存在"
        if users[username].get("role") == "admin":
            return False, "admin 恒为已授权"
        users[username]["approved"] = bool(approved)
        _save_users(users)
    return True, "ok"


def set_role(username: str, role: str, by_user: str) -> tuple[bool, str]:
    """设角色（user/admin）。仅主管理员（admin 账号本人）可调用——调用处已校验 by_user=='admin'。
    admin 账号不可被改角色、不可自我降级。设为 admin 时自动 approved=True。"""
    if role not in ("user", "admin"):
        return False, "角色必须是 user 或 admin"
    if username == "admin":
        return False, "主管理员账号角色不可修改"
    if by_user == username:
        return False, "不能修改自己的角色"
    with _lock:
        users = _load_users()
        if username not in users:
            return False, "用户不存在"
        users[username]["role"] = role
        if role == "admin":
            users[username]["approved"] = True
        _save_users(users)
    return True, "ok"


def delete_user(username: str) -> tuple[bool, str]:
    """删号：移除账号+会话，并连带删除 workspace/users/<名> 整个目录。"""
    import shutil
    with _lock:
        users = _load_users()
        if username not in users:
            return False, "用户不存在"
        if users[username].get("role") == "admin":
            return False, "不能删除 admin"
        del users[username]
        _save_users(users)
        for tok, rec in list(_sessions.items()):
            if (rec.get("username") if isinstance(rec, dict) else rec) == username:
                _sessions.pop(tok, None)
        _save_sessions()
    user_dir = _WORKSPACE / "users" / username
    if user_dir.exists():
        shutil.rmtree(user_dir, ignore_errors=True)
    return True, "ok"


def list_users() -> list:
    users = _load_users()
    out = []
    for n, u in sorted(users.items()):
        lv = u.get("level")
        if not isinstance(lv, int):
            lv = 5 if (n == "admin" or u.get("role") == "admin") else (1 if u.get("approved") else 0)
        out.append({
            "username": n,
            "role": u.get("role", "user"),
            "level": lv,
            "approved": bool(u.get("approved")) or lv >= 1,
            "created": u.get("created", ""),
        })
    return out


def set_password(username: str, new_password: str) -> tuple[bool, str]:
    if len(new_password) < 4:
        return False, "密码至少 4 位"
    with _lock:
        users = _load_users()
        if username not in users:
            return False, "用户不存在"
        salt = secrets.token_hex(16)
        users[username]["salt"] = salt
        users[username]["hash"] = _hash_password(new_password, salt)
        _save_users(users)
    return True, "ok"
