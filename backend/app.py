"""
C4EAI 后端 — FastAPI 入口
工具执行 + LangChain Agent(SSE流式) + 文件上传 + 笔记 CRUD + 静态文件
"""
import json, sys, os
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse
import asyncio
from pydantic import BaseModel
from typing import Optional, Any, Dict

from tools.neo4j_tools import Neo4jClient, execute_graph_schema, execute_cypher
from tools.web_tools import web_search, web_extract
from tools.skill_tools import skill_list, skill_view
from tools.code_sandbox import run_python
from tools.file_tools import (
    read_file, write_file, list_files,
    upload_file as _upload_file,
    delete_upload as _delete_upload,
    get_upload_text,
    list_notes, add_note, update_note, delete_note, move_note,
    get_note, get_note_owner,
)
import auth as _auth
from tools.vector_search import search_history, search_knowledge
from tools.shared_hub import publish_note_to_db
import tools.user_settings as US
import tools.user_chats as UC
from tools.mineru_tools import save_token as _mineru_save_token, load_token as _mineru_load_token
from agent.tools import tools_registry as _tools_registry
from agent.agent import run_agent, stream_agent, stream_agent_lc

app = FastAPI(title="C4EAI Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
# gzip（用户 2026-09-26 定，性能）：共享库列表 435KB 原样传输 → 压到约 1/3。
# 加在 FastAPI 而不是 nginx：nginx 那个 location 特意 gzip off 了（为了不干扰 SSE 流式）。
# Starlette 的 GZipMiddleware 对 text/event-stream 自动跳过压缩，不会破坏流式 ——
# 见 test: 流式响应带 Accept-Encoding: gzip 时仍无 Content-Encoding。
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ── 禁止缓存 JS/CSS/HTML ─────────────────────────
_AUTH_PUBLIC = {"/api/auth/login", "/api/auth/register", "/api/health", "/api/platform"}
# 账号系统开关：仅共享库实例设置 C4EAI_AUTH=1 启用；本地实例不设 → 一切照旧（无登录）
_AUTH_ENABLED = os.environ.get("C4EAI_AUTH", "") == "1"

# 组内共享库默认地址：本地实例即使环境变量没生效（start.bat 环境丢失等），也连这台
_DEFAULT_HUB = ""   # 共享库地址请设环境变量 C4EAI_HUB；不设 = 本机独立运行

def _hub_base() -> str:
    return os.environ.get("C4EAI_HUB", "").rstrip("/") or _DEFAULT_HUB

# ── 角色权限策略（仅云端实例 HUB 生效）────────────────
import role_policy as _RP
from tools.file_tools import set_user_scope as _set_user_scope

def _policy_for(request, req):
    """返回 (cur_user, enabled_tools, permission_mode)。云端按角色裁剪工具+钳制模式+注入文件沙盒。"""
    u = _cur_user(request)
    _RP.set_current(u)
    req.enabled_tools = _RP.filter_tools(u, req.enabled_tools)
    req.permission_mode = _RP.enforce_mode(u, req.permission_mode)
    _set_user_scope(_RP.user_sandbox(u))   # admin/super→None 不锁；approved/unapproved→本人目录
    return u

def _cur_user(request: Request) -> dict:
    """鉴权关闭（本地实例）时：已登录→共享库身份；未登录→游客（本地功能照常用）"""
    u = getattr(request.state, "user", None)
    if u:
        return u
    if not _AUTH_ENABLED:
        s = _load_hub_session()
        if s.get("token"):
            lv = s.get("level")
            if not isinstance(lv, int):
                lv = 5 if (s.get("username") == "admin" or s.get("role") == "admin") else (1 if s.get("approved") else 0)
            return {"username": s.get("username", "guest"), "role": s.get("role", "guest"),
                    "level": lv, "approved": bool(s.get("approved", False)) or lv >= 1}
        return {"username": "guest", "role": "guest", "level": 0, "approved": False}
    return {"username": "guest", "role": "guest", "level": 0}

@app.middleware("http")
async def auth_no_cache_middleware(request, call_next):
    # 账号鉴权：白名单外的所有 /api/* 必须携带有效 token（存量数据/会话缺失时 401，前端弹登录框）
    if _AUTH_ENABLED and request.url.path.startswith("/api/") and request.url.path not in _AUTH_PUBLIC:
        user = _auth.user_by_token(_auth.extract_token(request.headers))
        if not user:
            return JSONResponse({"error": "未登录或会话已过期"}, status_code=401)
        request.state.user = user
    response = await call_next(request)
    # 页面与静态资源一律禁缓存（含 "/" 入口页——之前漏掉它导致浏览器用旧版 index.html，
    # 旧版里主题点只有 3 个、且引用旧版本号 js，新修复永远到不了浏览器）
    p = request.url.path
    if p.startswith("/js/") or p.startswith("/css/") or p in ("/", "/index.html") or p.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ── Pydantic 模型 ──────────────────────────────────

class Neo4jConnectRequest(BaseModel):
    uri: str; user: str; password: str
class CypherRequest(BaseModel):
    cypher: str; params: Optional[dict] = {}
class SearchRequest(BaseModel):
    query: str; limit: Optional[int] = 5
class FetchRequest(BaseModel):
    url: str
class SkillViewRequest(BaseModel):
    name: str; file_path: Optional[str] = "SKILL.md"
class RunCodeRequest(BaseModel):
    code: str; timeout: Optional[int] = 30
class ReadFileRequest(BaseModel):
    path: str; offset: Optional[int] = 1; limit: Optional[int] = 500
class WriteFileRequest(BaseModel):
    path: str; content: str
class ListFilesRequest(BaseModel):
    subdir: Optional[str] = ""
class FileIdRequest(BaseModel):
    file_id: str
class NoteAddReq(BaseModel):
    title: str; content: str = ""; branch_id: Optional[str] = ""
    kind: Optional[str] = "note"     # note=笔记（默认）/ document=文档条目
    folder_id: Optional[str] = ""    # 归属分组（分组树 id）用户 2026-09-25 定
class NoteUpdateReq(BaseModel):
    note_id: str; title: Optional[str] = None; content: Optional[str] = None
    kind: Optional[str] = None        # 迁移/改分类时用（note/document）
    folder_id: Optional[str] = None   # 归属分组（分组树 id；""=移出分组）用户 2026-09-25 定
    branch_id: Optional[str] = None   # "__db__" = 数据库条目（唯一存云端，本地实例须转发）
class NoteMoveReq(BaseModel):
    note_id: str; direction: int
    branch_id: Optional[str] = None
class NotePublishReq(BaseModel):
    note_id: str
    entry_id: Optional[str] = ""   # 已上传过则传云端条目 id（更新而非新建）
class SearchQueryReq(BaseModel):
    query: str; top_k: Optional[int] = 3
class XiaoeAskReq(BaseModel):
    question: str
    context: Optional[str] = ""      # 当前对话上下文（前端截取最近若干轮）
    history: list = []               # 小e 自己的对话历史（内存，不落盘）
class AgentRunRequest(BaseModel):
    input: str; history: list = []
    note_refs: list = []     # 用户引用的笔记/数据库条目：[{id, scope:'notes'|'database', title, content}]
    timeouts: dict = {}      # 三级超时（秒）：{"nudge":120,"warn":240,"abort":420}，0=关闭该级
    subagent: dict = {}      # 子智能体设置：{"enabled":True,"max_rounds":4,"timeout":300}
    enabled_tools: list[str] = []
    loaded_skills: list[str] = []
    neo4j_config: Optional[dict] = None
    model: Optional[dict] = None
    context_length: Optional[int] = 10
    branch_id: Optional[str] = ""
    file_ids: list[str] = []
    # 当轮附件（注入完整文本）；None=旧前端未区分，全部按完整注入；[]=仅 file_ids 中标记
    full_file_ids: Optional[list[str]] = None
    images: list[str] = []          # base64(dataURL) 图片，多模态给模型
    permission_mode: str = "safe"
    engine: str = "custom"          # custom=裸API手写循环 / langchain=LangChain流式
    max_rounds: Optional[int] = None  # 工具循环步数，0=无限

# ── 健康检查 ──────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "neo4j_connected": Neo4jClient.is_connected()}

# ── 账号系统（初始 admin 在首次启动时创建，密码见启动日志或 C4EAI_ADMIN_PASSWORD）──
# 两种模式：
#   共享库实例（C4EAI_AUTH=1）：本地账号 + 强制登录（middleware 校验 token）
#   本地实例（未设 C4EAI_AUTH）：登录可选——登录=向共享库(C4EAI_HUB)验证同一套账号，
#     会话存 workspace/_auth/hub_session.json，shared_* 工具自动取用；不登录=游客，功能照常用。

_auth.init_auth()

_HUB_SESSION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "workspace", "_auth", "hub_session.json",
)

def _save_hub_session(data: dict):
    os.makedirs(os.path.dirname(_HUB_SESSION_PATH), exist_ok=True)
    with open(_HUB_SESSION_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

def _load_hub_session() -> dict:
    try:
        with open(_HUB_SESSION_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _clear_hub_session():
    try:
        os.remove(_HUB_SESSION_PATH)
    except Exception:
        pass

async def _hub_api(method: str, path: str, payload: dict = None, token: str = ""):
    """向共享库发一次 API 请求，返回 (status, text)；未配置/不可达返回 None"""
    import httpx as _httpx
    base = _hub_base()
    if not base:
        return None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = _httpx.Timeout(connect=5, read=15, write=15, pool=5)
    async with _httpx.AsyncClient(timeout=timeout) as cli:
        r = await cli.request(method, f"{base}{path}", json=payload, headers=headers)
        return r.status_code, r.text

class LoginReq(BaseModel):
    username: str; password: str
class CreateUserReq(BaseModel):
    username: str; password: str; role: str = "user"; level: int | None = None
class SetPwReq(BaseModel):
    username: str; password: str
class RegisterReq(BaseModel):
    username: str; password: str
class ApproveReq(BaseModel):
    username: str; approved: bool = True
class RoleReq(BaseModel):
    username: str; role: str = "user"
class LevelReq(BaseModel):
    username: str; level: int = 0
class DeleteUserReq(BaseModel):
    username: str

@app.post("/api/auth/login")
async def api_auth_login(req: LoginReq):
    if not _AUTH_ENABLED:
        # 本地实例：代理到共享库验证（同一套账号）
        res = await _hub_api("POST", "/api/auth/login", {"username": req.username, "password": req.password})
        if res is None:
            return JSONResponse({"error": "本地实例未连接共享服务器（未设 C4EAI_HUB），无需登录"}, status_code=400)
        code, text = res
        if code != 200:
            return JSONResponse({"error": "用户名或密码错误"}, status_code=401)
        d = json.loads(text)
        _save_hub_session({"token": d["token"], "username": d["username"],
                           "role": d.get("role", "user"),
                           "level": d.get("level") if isinstance(d.get("level"), int)
                                    else (5 if d.get("role") == "admin" else (1 if d.get("approved") else 0)),
                           "approved": bool(d.get("approved", False))})
        return d
    token = _auth.login(req.username, req.password)
    if not token:
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)
    u = _auth.user_by_token(token)
    # ⚠️ 必须返回 level：前端登录后把身份存进 localStorage（顶栏芯片据此上色），
    #    漏掉 level 会导致芯片只能按 approved 兜底显示蓝色（与用户管理面板不一致）。
    return {"status": "ok", "token": token, "username": u["username"],
            "role": u["role"], "level": u.get("level", 0),
            "approved": u["approved"]}

@app.post("/api/auth/register")
async def api_auth_register(req: RegisterReq):
    """自由注册：人人可注册，默认 L1 蓝色（注册即可用自己数据库）。"""
    if not _AUTH_ENABLED:
        res = await _hub_api("POST", "/api/auth/register", {"username": req.username, "password": req.password})
        if res is None:
            return JSONResponse({"error": "共享服务器不可达，无法注册"}, status_code=502)
        code, text = res
        try:
            return JSONResponse(json.loads(text) if text else {}, status_code=code)
        except Exception:
            return JSONResponse({"error": text}, status_code=code)
    ok, msg = _auth.register_user(req.username, req.password)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"status": "ok", "message": "注册成功，可用自己的数据库"}

@app.post("/api/auth/logout")
async def api_auth_logout(request: Request):
    if not _AUTH_ENABLED:
        s = _load_hub_session()
        if s.get("token"):
            await _hub_api("POST", "/api/auth/logout", {}, token=s["token"])
        _clear_hub_session()
        return {"status": "ok"}
    _auth.logout(_auth.extract_token(request.headers))
    return {"status": "ok"}

@app.get("/api/auth/me")
async def api_auth_me(request: Request):
    if not _AUTH_ENABLED:
        # 登录可选：有会话→向共享库核实（失效则退回游客）；无会话→游客；未连共享库→不显示芯片
        s = _load_hub_session()
        if s.get("token"):
            res = await _hub_api("GET", "/api/auth/me", token=s["token"])
            if res and res[0] == 200:
                d = json.loads(res[1])
                d["auth_enabled"] = True
                return d
            _clear_hub_session()  # 共享库重启等导致 token 失效 → 退回游客
        if _hub_base():
            return {"status": "ok", "username": "guest", "role": "guest", "auth_enabled": True}
        return {"status": "ok", "username": "guest", "role": "guest", "auth_enabled": False}
    return {"status": "ok", **_cur_user(request), "auth_enabled": True}

def _is_authorized(user: dict) -> bool:
    """是否已开通数据库权限（L1 蓝色及以上，或 admin）。

    用户 2026-09-22 定：注册即 L1，故等价于「非游客」。
    """
    return _RP.level_of(user) >= _RP.L_BLUE


def _owns_or_root(user: dict, owner: str) -> bool:
    """能否操作该资源：本人所有，或主管理员 admin。

    ⚠️ 用户 2026-09-22 选 B：**金色 L5 也不能改/删他人数据库/笔记**。
       数据管理权（改他人内容）保留给 admin 独占 —— 金色只是「人事权限」。
       L4 红的「全读写」仅指**服务器文件**，不含他人的数据库条目。
    """
    return (owner or "") == (user.get("username") or "") or _RP.is_root(user)


def _require_admin(request: Request):
    """能否进入用户管理面板：主管理员 或 金色(L5)。"""
    u = getattr(request.state, "user", None)
    if not u and not _AUTH_ENABLED:
        # 本地实例：看共享库会话是否为 admin
        s = _load_hub_session()
        if s.get("token"):
            u = {"username": s.get("username"), "role": s.get("role"), "level": s.get("level")}
    if not u:
        return False
    if u.get("role") == "admin":
        return True
    # 金色(L5) 也可管理面板（但只能调 L0-L4，见 /api/auth/users/level）
    return _RP.level_of(u) >= _RP.L_GOLD


def _require_root(request: Request):
    """仅主管理员 admin：删除账号、授予 L5 等 admin 独有操作。

    用户 2026-09-22 定：金色 L5 与管理员权限一致，但「删账号」排除在外。
    """
    u = getattr(request.state, "user", None)
    if not u and not _AUTH_ENABLED:
        s = _load_hub_session()
        if s.get("token"):
            u = {"username": s.get("username"), "role": s.get("role"), "level": s.get("level")}
    if not u:
        return False
    return u.get("username") == "admin" or u.get("role") == "admin"


def _req_dump(req) -> dict:
    d = getattr(req, "model_dump", None)
    return d() if callable(d) else dict(req)

async def _proxy_admin_api(method: str, path: str, payload: dict = None):
    """本地实例把用户管理接口代理给共享库（用本地保存的会话 token）"""
    s = _load_hub_session()
    if not s.get("token"):
        return JSONResponse({"error": "请先用管理员账号登录本地实例"}, status_code=403)
    res = await _hub_api(method, path, payload, token=s["token"])
    if res is None:
        return JSONResponse({"error": "共享服务器不可达"}, status_code=502)
    code, text = res
    return JSONResponse(json.loads(text) if text else {}, status_code=code)

@app.get("/api/auth/users")
async def api_auth_users(request: Request):
    if not _AUTH_ENABLED:
        return await _proxy_admin_api("GET", "/api/auth/users")
    if not _require_admin(request):
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    usage = _RP.all_usage()
    users = _auth.list_users()
    for u in users:
        u["qwen_tokens_today"] = usage.get(u["username"], 0)
    _cu = _cur_user(request)
    return {"status": "ok", "users": users,
            "is_super": (_cu.get("username") == "admin") or _RP.level_of(_cu) >= _RP.L_GOLD,
            "is_root": (_cu.get("username") == "admin"),
            "my_level": _RP.level_of(_cu),
            "levels": [{"level": i, "name": _RP.LEVEL_NAMES[i], "color": _RP.LEVEL_COLORS[i]}
                       for i in range(6)]}

@app.post("/api/auth/users")
async def api_auth_users_create(req: CreateUserReq, request: Request):
    if not _AUTH_ENABLED:
        return await _proxy_admin_api("POST", "/api/auth/users", _req_dump(req))
    if not _require_admin(request):
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    cu = _cur_user(request)
    # 等级：仅主管理员可创建 L5(金)；金色(L5) 最多创建 L4
    want_lv = req.level if isinstance(req.level, int) else 0
    if cu.get("username") != "admin" and want_lv >= _RP.L_GOLD:
        want_lv = _RP.L_ORANGE   # 非主管理员降级请求
    # 仅主管理员可创建 admin 角色
    role = req.role if (cu.get("username") == "admin" and req.role == "admin") else "user"
    ok, msg = _auth.create_user(req.username, req.password, role, level=want_lv)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"status": "ok"}

@app.post("/api/auth/users/level")
async def api_auth_users_level(req: LevelReq, request: Request):
    """调整用户身份等级（L0-L5）。
    - 主管理员：可调任何非 admin 用户（含授予 L5 金色）
    - 金色(L5)：可调 L0-L4（不可授予金色）
    - 其他：无权
    """
    if not _AUTH_ENABLED:
        return await _proxy_admin_api("POST", "/api/auth/users/level", _req_dump(req))
    cu = _cur_user(request)
    if not _require_admin(request):
        return JSONResponse({"error": "需要管理员或金色等级权限"}, status_code=403)
    ok, msg = _RP.can_set_level(cu, req.username, req.level)
    if not ok:
        return JSONResponse({"error": msg}, status_code=403 if "权限" in msg else 400)
    ok, msg = _auth.set_level(req.username, req.level)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"status": "ok", "message": msg}


@app.get("/api/auth/levels")
async def api_auth_levels():
    """等级元数据（前端据此渲染头像颜色与名称）。"""
    return {"status": "ok", "levels": [
        {"level": i, "name": _RP.LEVEL_NAMES[i], "color": _RP.LEVEL_COLORS[i],
         "desc": [
             "游客：聊天可用；工具受限；不可用云端数据库；本地千问限量",
             "蓝色：可用自己数据库（上传/查询/改/删）；工具全开；本地千问限量",
             "紫色：同蓝色，颜色区分身份；本地千问限量",
             "橙色：可查询所有人的数据库；仅能改/删自己的；本地千问限量",
             "红色：同橙色权限，且本地千问**不限量**",
             "金色：同橙色权限；可调整他人身份等级（不可授予金色）",
         ][i]} for i in range(6)]}


@app.post("/api/auth/users/role")
async def api_auth_users_role(req: RoleReq, request: Request):
    """设角色（user↔admin）。仅主管理员（admin 账号本人）可用。"""
    if not _AUTH_ENABLED:
        return await _proxy_admin_api("POST", "/api/auth/users/role", _req_dump(req))
    cu = _cur_user(request)
    if cu.get("username") != "admin":
        return JSONResponse({"error": "仅主管理员可调整账号角色"}, status_code=403)
    ok, msg = _auth.set_role(req.username, req.role, cu.get("username"))
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"status": "ok"}

@app.post("/api/auth/users/password")
async def api_auth_users_pw(req: SetPwReq, request: Request):
    if not _AUTH_ENABLED:
        return await _proxy_admin_api("POST", "/api/auth/users/password", _req_dump(req))
    if not _require_admin(request):
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    ok, msg = _auth.set_password(req.username, req.password)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"status": "ok"}

@app.post("/api/auth/users/approve")
async def api_auth_users_approve(req: ApproveReq, request: Request):
    if not _AUTH_ENABLED:
        return await _proxy_admin_api("POST", "/api/auth/users/approve", _req_dump(req))
    if not _require_admin(request):
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    ok, msg = _auth.set_approved(req.username, req.approved)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"status": "ok"}

@app.post("/api/auth/users/delete")
async def api_auth_users_delete(req: DeleteUserReq, request: Request):
    """删号：连带删除 workspace/users/<名> 整个目录。

    ⚠️ 仅主管理员（用户 2026-09-22 定：金色与管理员一致，但「删账号」排除）。
    """
    if not _AUTH_ENABLED:
        return await _proxy_admin_api("POST", "/api/auth/users/delete", _req_dump(req))
    if not _require_root(request):
        return JSONResponse({"error": "只有主管理员可以删除账号"}, status_code=403)
    ok, msg = _auth.delete_user(req.username)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"status": "ok"}

# ── Neo4j ─────────────────────────────────────────

@app.post("/api/neo4j/connect")
async def neo4j_connect(req: Neo4jConnectRequest):
    return Neo4jClient.connect(req.uri, req.user, req.password)
@app.post("/api/neo4j/disconnect")
async def neo4j_disconnect():
    Neo4jClient.disconnect(); return {"success": True}

# ── MinerU 配置端点 ──────────────────────────────

class MinerUConfigRequest(BaseModel):
    token: str = ""

@app.get("/api/config/mineru")
async def mineru_config_get(request: Request):
    """读取 MinerU 配置。云端非主管理员：只返回是否已配置，不回显 token 明文。"""
    token = _mineru_load_token()
    if _RP.HUB and not _RP.mineru_write_allowed(_cur_user(request)):
        return {"configured": bool(token), "token": ""}
    return {"configured": bool(token), "token": token if token else ""}

@app.post("/api/config/mineru")
async def mineru_config_set(req: MinerUConfigRequest, request: Request):
    """保存 MinerU API Token。云端：仅主管理员可改服务器全局 token。"""
    if _RP.HUB and not _RP.mineru_write_allowed(_cur_user(request)):
        return JSONResponse({"error": "云端 MinerU Token 仅主管理员可修改；请在本地实例填你自己的 Token。"}, status_code=403)
    if not req.token or not req.token.strip():
        _mineru_save_token("")
        return {"success": True, "configured": False}
    _mineru_save_token(req.token.strip())
    return {"success": True, "configured": True}

# ── 工具端点 ──────────────────────────────────────

def _tool_gate(request, tool):
    """云端直连工具端点角色闸。放行则返回 user，否则返回 403 JSONResponse。"""
    from tools.file_tools import set_permission_mode as _spm, set_current_user as _scu
    if not _RP.HUB:
        u = _cur_user(request)
        _scu(u["username"])            # 产出区/附件登记按身份（本地实例也要）
        return u
    u = _cur_user(request)
    if _RP.tool_denied(u, tool):
        return JSONResponse({"error": "该功能需注册并由管理员开通权限后使用（未授权账号仅可使用聊天）。"}, status_code=403)
    _spm(_RP.enforce_mode(u, "safe"))
    _set_user_scope(_RP.user_sandbox(u))   # 文件类端点也按身份沙盒
    _scu(u["username"])
    return u


@app.get("/api/outputs/list")
async def api_outputs_list(request: Request):
    """我的产出：agent / 技能生成的文件（未登记的顺手补登记），返回 file_id。

    用户 2026-09-26 定：「和我笔记里的一样，就一个占位符，链接后端真实文件位置」——
    所以这里只发 file_id，前端用现成的 openFileById()，三种打开方式零改动；
    后端也只有一份文件（登记只记指针，不复制）。
    """
    from tools.file_tools import list_outputs, set_current_user as _scu
    user = _cur_user(request)
    _scu(user["username"])
    return json.loads(list_outputs(user["username"]))

@app.post("/api/tool/schema")
async def tool_schema(request: Request):
    g = _tool_gate(request, "schema")
    if isinstance(g, JSONResponse): return g
    return json.loads(await execute_graph_schema())
@app.post("/api/tool/cypher")
async def tool_cypher(req: CypherRequest, request: Request):
    g = _tool_gate(request, "cypher")
    if isinstance(g, JSONResponse): return g
    if _RP.cypher_denied(g, req.cypher):
        return JSONResponse({"error": "图谱删除类语句（DELETE/DETACH/DROP）需管理员权限。"}, status_code=403)
    return json.loads(await execute_cypher(req.cypher, req.params))
@app.post("/api/tool/search")
async def tool_web_search(req: SearchRequest):
    return json.loads(await web_search(req.query, req.limit or 5))
@app.post("/api/tool/fetch")
async def tool_web_fetch(req: FetchRequest):
    return json.loads(await web_extract(req.url))
@app.post("/api/tool/skill-list")
async def tool_skill_list():
    return json.loads(await skill_list())
@app.get("/api/tools")
async def api_tools():
    """返回全部可用工具的名称+描述（供前端动态渲染工具勾选，Hermes 式注册表）。"""
    tools = []
    for name, t in _tools_registry.items():
        desc = getattr(t, "description", "") or ""
        tools.append({"name": name, "description": desc})
    return {"tools": tools}
@app.get("/api/skills")
async def api_skills():
    """返回全部可用技能（供前端动态渲染技能勾选）。"""
    try:
        data = json.loads(await skill_list())
        return {"skills": data.get("skills", [])}
    except Exception as e:
        return {"skills": [], "error": str(e)}
@app.post("/api/tool/skill-view")
async def tool_skill_view(req: SkillViewRequest):
    return json.loads(await skill_view(req.name, req.file_path))
@app.post("/api/tool/code")
async def tool_run_code(req: RunCodeRequest, request: Request):
    g = _tool_gate(request, "code")
    if isinstance(g, JSONResponse): return g
    return json.loads(await run_python(req.code, req.timeout or 30))
@app.post("/api/tool/read-file")
async def tool_read_file(req: ReadFileRequest, request: Request):
    g = _tool_gate(request, "read-file")
    if isinstance(g, JSONResponse): return g
    return json.loads(await read_file(req.path, req.offset or 1, req.limit or 500))
@app.post("/api/tool/write-file")
async def tool_write_file(req: WriteFileRequest, request: Request):
    g = _tool_gate(request, "read-file")
    if isinstance(g, JSONResponse): return g
    return json.loads(await write_file(req.path, req.content))
@app.post("/api/tool/list-files")
async def tool_list_files(req: ListFilesRequest, request: Request):
    g = _tool_gate(request, "read-file")
    if isinstance(g, JSONResponse): return g
    return json.loads(await list_files(req.subdir or ""))

# ── 文件上传 ──────────────────────────────────────

def _check_file_access(meta: dict, user: dict):
    """私人附件(source=chat/缺省)：仅本人或 admin；共享池(source=database)：登录身份皆可。"""
    if (meta or {}).get("source") == "database":
        return
    owner = (meta or {}).get("owner", "admin")
    if owner == (user or {}).get("username"):
        return True
    if (user or {}).get("role") == "admin":
        return True
    raise HTTPException(403, "无权访问他人的私人文件")


@app.post("/api/file/upload")
async def api_file_upload(file: UploadFile = File(...), request: Request = None,
                          source: str = "chat", no_entry: int = 0,
                          parse: Optional[int] = None, folder_id: str = ""):
    """source=chat → 私人附件（users/<身份>/uploads，任何身份可用）；
    source=database → 共享数据库：本地实例代理到云端共享池（需登录+授权）；云端写 pool 并入索引。

    parse（用户 2026-09-25 定）——解析的**唯一触发点**是「新内容第一次从共享库外部进入共享库」：
      · 显式传 parse 优先
      · 否则看目标文件夹：落在「文档」夹（parse=true）→ 解析；「笔记」夹 → 不解析
      · 从个人库发布进来的（no_entry=1）→ 一律不解析
    """
    content = await file.read()
    user = _cur_user(request)
    owner = user["username"]
    source = (source or "chat").strip().lower()
    if source not in ("chat", "database"):
        source = "chat"

    # 是否解析：显式 > 从个人库发布(no_entry=1 → 否) > 目标文件夹的 parse 标记
    if parse is not None:
        _parse = int(parse) != 0
    elif no_entry:
        _parse = False          # 个人库发布进共享库：不解析（用户明确要求）
    elif source == "database" and folder_id:
        from tools.folder_tree import folder_parses
        _parse = folder_parses(folder_id)
    else:
        _parse = True           # 直接上传到云端共享库且未指定文件夹 → 解析（默认文档语义）

    if source == "database":
        if not _AUTH_ENABLED:
            # 本地实例：数据库唯一在云端 → 用本地登录会话代理上传
            s = _load_hub_session()
            if not s.get("token"):
                return JSONResponse({"error": "数据库在共享服务器上：请先点击右上角「登录」；"
                                              "新账号需等管理员开通数据库权限后再上传"},
                                    status_code=401)
            base = _hub_base()
            if not base:
                return JSONResponse({"error": "本地实例未配置共享服务器（C4EAI_HUB），无法使用数据库"},
                                    status_code=400)
            import httpx as _httpx
            try:
                timeout = _httpx.Timeout(connect=30, read=600, write=600, pool=30)
                async with _httpx.AsyncClient(timeout=timeout) as cli:
                    r = await cli.post(f"{base}/api/file/upload?source=database&no_entry={1 if no_entry else 0}"
                                       f"&parse={1 if _parse else 0}"
                                       + (f"&folder_id={folder_id}" if folder_id else ""),
                                       files={"file": (file.filename or "unknown", content)},
                                       headers={"Authorization": f"Bearer {s['token']}"})
                try:
                    if r.status_code != 200:
                        # 透传云端错误（403 未授权等），保住状态码并翻人话
                        from tools.shared_hub import _friendly
                        return JSONResponse({"error": _friendly(r.text)},
                                            status_code=r.status_code)
                    return json.loads(r.text)
                except json.JSONDecodeError:
                    return JSONResponse({"error": r.text}, status_code=r.status_code)
            except Exception as e:
                return JSONResponse({"error": f"上传到共享服务器失败: {e}"}, status_code=502)
        # 云端共享库：数据库需 admin 已授权
        if not _is_authorized(user):
            return JSONResponse({"error": "已注册，等待管理员开通数据库权限后即可上传"},
                                status_code=403)

    # upload_file 是 async 但内部无 await（纯 CPU 密集文本提取），在线程池执行避免阻塞事件循环
    def _upload_sync(fn, c, ow, src, pst):
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_upload_file(fn, c, ow, src, pst))
            finally:
                loop.close()
        except Exception:
            import asyncio as a2
            return a2.run(_upload_file(fn, c, ow, src, pst))
    import asyncio
    _res = json.loads(await asyncio.to_thread(
        _upload_sync, file.filename or "unknown", content, owner, source, _parse))
    # 数据库上传：自动建「文档条目」（用户 2026-09-22 定）
    #   · kind=document —— 解析正文放这里，不污染人写的笔记条目
    #   · 正文 = {{file:...}} 占位 + 解析文本（供检索；原件完整保留可下载/预览）
    #   · no_entry=1 例外：笔记发布时复制附件，附件是笔记内容的一部分，不另立条目
    #   · _parse=False 例外：笔记附件不解析（用户 2026-09-25 定）→ 无正文可建条目
    if (source == "database" and not no_entry and _parse
            and isinstance(_res, dict) and _res.get("file_id")):
        try:
            _fn = file.filename or "file"
            _fid = _res["file_id"]
            _text = (_res.get("text") or "").strip()
            if len(_text) < 20:
                # 短文本/纯图片等：读落盘的 text.txt，保证正文尽量完整
                try:
                    from tools.file_tools import _pool_uploads_dir
                    _tp = os.path.join(_pool_uploads_dir(), _fid, "text.txt")
                    if os.path.isfile(_tp):
                        with open(_tp, "r", encoding="utf-8") as _f:
                            _text = _f.read().strip()
                except Exception:
                    pass
            if _text:
                _body = ("\n{{file:%s:%s}}\n\n---\n\n%s\n" % (_fid, _fn, _text))
            else:
                _body = "\n{{file:%s:%s}}\n" % (_fid, _fn)
            await add_note(_fn, _body, "__db__", owner, kind="document",
                           folder_id=folder_id or "")
        except Exception:
            pass   # 自动建条目不阻断上传主流程
    return _res


@app.get("/api/file/list")
async def api_file_list(request: Request):
    """我可见的上传文件：自己的私人附件 + 共享池（admin 额外可见全部用户的私人附件）"""
    from tools.file_tools import _upload_roots, _pool_uploads_dir, _UPLOADS_DIR, _user_key
    user = _cur_user(request)
    my_key = _user_key(user["username"])
    is_admin = user.get("role") == "admin"
    pool_root = os.path.abspath(_pool_uploads_dir())
    legacy_root = os.path.abspath(_UPLOADS_DIR)
    items = []
    for root in _upload_roots():
        root_abs = os.path.abspath(root)
        try:
            fids = sorted(os.listdir(root_abs))
        except Exception:
            continue
        for fid in fids:
            mp = os.path.join(root_abs, fid, "meta.json")
            if not os.path.isfile(mp):
                continue
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    m = json.load(f)
            except Exception:
                continue
            if root_abs == pool_root:
                m["scope"] = "database"
            elif root_abs == legacy_root:
                m.setdefault("owner", "admin")
                m["scope"] = "legacy"
                if not is_admin and m.get("owner") != user["username"]:
                    continue
            else:
                m["scope"] = "chat"
                # users/<X>/uploads：目录名即归属 key
                ukey = os.path.basename(os.path.dirname(root_abs))
                m.setdefault("owner", "_guest" if ukey == "_guest" else ukey)
                if not is_admin and _user_key(m.get("owner", "")) != my_key:
                    continue
            items.append(m)
    return {"status": "ok", "files": items}


@app.get("/api/pool/list")
async def api_pool_list(request: Request):
    """共享数据库池清单（云端网页数据库面板用）。需已授权。本地实例代理到共享库。"""
    user = _cur_user(request)
    if not _AUTH_ENABLED:
        s = _load_hub_session()
        if not s.get("token"):
            return JSONResponse({"error": "请先登录"}, status_code=401)
        res = await _hub_api("GET", "/api/pool/list", token=s["token"])
        if res is None:
            return JSONResponse({"error": "共享服务器不可达"}, status_code=502)
        code, text = res
        try:
            return JSONResponse(json.loads(text) if text else {}, status_code=code)
        except Exception:
            return JSONResponse({"error": text}, status_code=code)
    if not _is_authorized(user):
        return JSONResponse({"error": "已注册，等待管理员开通数据库权限"}, status_code=403)
    from tools.file_tools import _pool_uploads_dir
    items = []
    root = _pool_uploads_dir()
    for fid in sorted(os.listdir(root)):
        mp = os.path.join(root, fid, "meta.json")
        if os.path.isfile(mp):
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    items.append(json.load(f))
            except Exception:
                pass
    # 按身份过滤：admin 看全部；其余（含金色 L5）只看自己的 —— 用户选 B
    if not _RP.is_root(user):
        items = [m for m in items if m.get("owner", "admin") == user["username"]]
    return {"status": "ok", "files": items}


@app.post("/api/file/delete")
async def api_file_delete(req: FileIdRequest, request: Request):
    # 仅本人或 admin 可删（共享池文件：上传者或 admin）
    from tools.file_tools import _resolve_upload
    user = _cur_user(request)
    d, meta, _ = _resolve_upload(req.file_id)
    if d:
        owner = (meta or {}).get("owner", "admin")
        if not _owns_or_root(user, owner):
            return JSONResponse({"error": "只能删除自己的文件"}, status_code=403)
    return json.loads(await _delete_upload(req.file_id))


@app.post("/api/file/text")
async def api_file_text(req: FileIdRequest, request: Request):
    from tools.file_tools import _resolve_upload
    user = _cur_user(request)
    d, meta, _ = _resolve_upload(req.file_id)
    if not d:
        # 本地实例：共享库文件只在云端 → 代理取文本（否则前端/智能体拿不到解析结果）
        if not _AUTH_ENABLED:
            s = _load_hub_session()
            base = _hub_base()
            if s.get("token") and base:
                import httpx as _httpx
                try:
                    timeout = _httpx.Timeout(connect=30, read=300, write=300, pool=30)
                    async with _httpx.AsyncClient(timeout=timeout) as cli:
                        r = await cli.post(f"{base}/api/file/text",
                                           json={"file_id": req.file_id},
                                           headers={"Authorization": f"Bearer {s['token']}",
                                                    "Content-Type": "application/json"})
                    if r.status_code == 200:
                        return json.loads(r.text)
                except Exception:
                    pass
        return {"file_id": req.file_id, "text": ""}
    _check_file_access(meta, user)
    text = await get_upload_text(req.file_id)
    return {"file_id": req.file_id, "text": text}


@app.get("/api/file/{file_id}")
async def api_file_download(file_id: str, request: Request):
    from tools.file_tools import _resolve_upload
    user = _cur_user(request)
    d, meta, file_path = _resolve_upload(file_id)
    if not d:
        # 本地实例：共享库文件只在云端 → 代理到 hub（与 /raw 一致），避免前端「文件不存在」
        if not _AUTH_ENABLED:
            s = _load_hub_session()
            base = _hub_base()
            if s.get("token") and base:
                import httpx as _httpx
                from fastapi.responses import Response as _Resp
                try:
                    timeout = _httpx.Timeout(connect=30, read=600, write=600, pool=30)
                    async with _httpx.AsyncClient(timeout=timeout) as cli:
                        r = await cli.get(f"{base}/api/file/{file_id}",
                                          headers={"Authorization": f"Bearer {s['token']}"})
                    if r.status_code == 200:
                        return _Resp(content=r.content,
                                     media_type=r.headers.get("content-type", "application/octet-stream"),
                                     headers={"Content-Disposition": r.headers.get("content-disposition", "inline")})
                    if r.status_code in (401, 403):
                        return JSONResponse({"error": "共享库文件需要登录：请点击右上角「登录」后重试"}, status_code=401)
                except Exception as _e:
                    return JSONResponse({"error": f"共享库连接失败：{str(_e)[:120]}"}, status_code=502)
        raise HTTPException(404, "文件不存在")
    _check_file_access(meta, user)
    filename = meta.get("name", "file")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".docx", ".doc"):
        # 动态取本机地址：本机用 127.0.0.1，局域网/服务器部署用请求的 Host（客户端 Word 经 http 来取文件）
        host = request.headers.get("host", "127.0.0.1:8080")
        scheme = request.url.scheme
        base = f"{scheme}://{host}"
        html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>打开文件</title>
<style>body{{font-family:sans-serif;text-align:center;padding:60px 20px;}}
.btn{{display:inline-block;padding:14px 32px;font-size:16px;background:#185ab4;color:#fff;border-radius:6px;text-decoration:none;}}</style></head><body>
<h2>📄 {filename}</h2>
<a class="btn" href="ms-word:ofe|u|{base}/api/file/{file_id}/raw">在 Word 中打开</a></body></html>'''
        from fastapi.responses import HTMLResponse; return HTMLResponse(html)
    from fastapi.responses import FileResponse
    from urllib.parse import quote; import mimetypes
    encoded_filename = quote(filename)
    headers = {"Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"}
    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(file_path, headers=headers, media_type=media_type or "application/octet-stream")

@app.post("/api/file/{file_id}/open")
async def api_file_open(file_id: str, request: Request):
    from tools.file_tools import _resolve_upload
    user = _cur_user(request)
    if sys.platform != "win32":
        return JSONResponse({"status": "unsupported", "message": "当前部署在 Linux/服务器，无法用本机默认应用打开文件；请用「浏览器新标签」或「应用内浮窗」打开"},
                            status_code=200)
    d, meta, file_path = _resolve_upload(file_id)
    if not d:
        raise HTTPException(404, "文件不存在")
    _check_file_access(meta, user)
    filename = meta.get("name", "file")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    os.startfile(file_path)
    return {"status": "ok", "message": f"已用默认应用打开: {filename}"}

@app.get("/api/file/{file_id}/raw")
async def api_file_raw(file_id: str, request: Request):
    from fastapi.responses import FileResponse, Response
    from tools.file_tools import _resolve_upload
    user = _cur_user(request)
    d, meta, file_path = _resolve_upload(file_id)
    if not d:
        # 本地实例：共享库文件只在云端 → 用本地登录会话代理到 hub（否则前端拿到 404「文件不存在」）
        if not _AUTH_ENABLED:
            s = _load_hub_session()
            base = _hub_base()
            if s.get("token") and base:
                import httpx as _httpx
                try:
                    timeout = _httpx.Timeout(connect=30, read=600, write=600, pool=30)
                    async with _httpx.AsyncClient(timeout=timeout) as cli:
                        r = await cli.get(f"{base}/api/file/{file_id}/raw",
                                          headers={"Authorization": f"Bearer {s['token']}"})
                    if r.status_code == 200:
                        ctype = r.headers.get("content-type", "application/octet-stream")
                        cdisp = r.headers.get("content-disposition", "attachment")
                        return Response(content=r.content, media_type=ctype,
                                        headers={"Content-Disposition": cdisp})
                    if r.status_code in (401, 403):
                        return JSONResponse({"error": "共享库文件需要登录：请点击右上角「登录」后重试"},
                                            status_code=401)
                    # 云端也 404 → 透传
                except Exception as _e:
                    return JSONResponse({"error": f"共享库连接失败：{str(_e)[:120]}"}, status_code=502)
        raise HTTPException(404, "文件不存在")
    _check_file_access(meta, user)
    filename = meta.get("name", "file")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    from urllib.parse import quote; import mimetypes
    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(file_path, media_type=media_type or "application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

@app.post("/api/file/{file_id}/open-location")
async def api_file_open_location(file_id: str, request: Request):
    """在资源管理器中打开文件所在文件夹并选中该文件。"""
    import subprocess
    from tools.file_tools import _resolve_upload
    user = _cur_user(request)
    if sys.platform != "win32":
        return JSONResponse({"status": "unsupported", "message": "当前部署在 Linux/服务器，无资源管理器可打开"},
                            status_code=200)
    d, meta, file_path = _resolve_upload(file_id)
    if not d:
        raise HTTPException(404, "文件不存在")
    _check_file_access(meta, user)
    filename = meta.get("name", "file")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    subprocess.Popen(r'explorer /select,"' + file_path + '"')
    return {"status": "ok", "message": f"已在资源管理器中打开: {filename}"}

# ── 笔记 CRUD（按身份物理分文件；admin 可见全部）────────────────
# 本地实例特例：branch_id=__db__（数据库条目）转发到共享库，与云端数据一致；
# 普通对话笔记仍存本地（本地/云端存储天然分离）。

DB_BRANCH_ID = "__db__"

async def _notes_proxy(method: str, path: str, payload: dict = None):
    """本地实例 → 共享库转发（数据库条目唯一存云端）。返回 None=不适用，否则返回 JSONResponse。"""
    if _AUTH_ENABLED:
        return None
    s = _load_hub_session()
    if not s.get("token"):
        return JSONResponse({"error": "数据库在共享服务器上：请先点击右上角「登录」"}, status_code=401)
    r = await _hub_api(method, path, payload, s["token"])
    if r is None:
        return JSONResponse({"error": "共享库不可达"}, status_code=502)
    status, text = r
    try:
        body = json.loads(text)
    except Exception:
        body = {"error": text}
    return JSONResponse(body, status_code=status)

@app.post("/api/notes/publish")
async def api_notes_publish(req: NotePublishReq, request: Request):
    """把本地笔记原样复制到共享数据库（云端）：文本 + 附件 + 图片，云端同结构。
    权限：需已登录且管理员已开通数据库权限（approved）；仅能上传自己的笔记。"""
    user = _cur_user(request)
    if user["username"] == "guest" or user.get("role") == "guest":
        return JSONResponse({"error": "请先登录（右上角「登录」）后再上传数据库"}, status_code=401)
    if not _is_authorized(user):
        return JSONResponse({"error": "已注册，等待管理员开通数据库权限后即可上传"}, status_code=403)

    from tools import file_tools as _ft
    note, _okey = _ft._find_note(user["username"], req.note_id)
    if note is None:
        return JSONResponse({"error": "笔记不存在"}, status_code=404)
    if not _owns_or_root(user, note.get("owner", "admin")):
        return JSONResponse({"error": "只能上传自己的笔记"}, status_code=403)

    if _AUTH_ENABLED:
        # 云端实例：目标是它自己；用当前请求的 token 走同一套鉴权
        base = f"http://127.0.0.1:{os.environ.get('C4EAI_PORT', '8088')}"
        token = _auth.extract_token(request.headers)
    else:
        base, token = "", ""     # 本地实例：用共享库配置（hub 地址 + 已存会话）

    r = await publish_note_to_db(req.note_id, user["username"], base=base, token=token,
                                entry_id=req.entry_id or note.get("cloud_entry_id", ""))
    if "error" in r:
        return JSONResponse(r, status_code=502)
    # 记下云端条目 id：再次点击即「更新云端副本」，不重复堆条目
    try:
        notes = _ft._read_notes(user["username"])
        for n in notes:
            if n.get("id") == req.note_id:
                n["cloud_entry_id"] = r.get("entry_id", "")
                n["cloudUploadedAt"] = str(datetime.now())
        _ft._write_notes(user["username"], notes)
    except Exception:
        pass
    return r


@app.get("/api/notes")
async def api_notes_list(branch_id: Optional[str] = None, kind: Optional[str] = None,
                         folder_id: Optional[str] = None, with_content: Optional[int] = None,
                         request: Request = None):
    """列条目。kind=note（默认前端取）/document；不传则全部。

    用户 2026-09-22 定：共享库按钮切换「笔记 / 文档」，两者数据结构一致。
    folder_id（用户 2026-09-25 定）：按分组过滤；"__root__" = 未分组。

    with_content（用户 2026-09-26 定，性能）：**传 0 = 只返回目录，不含正文**。
    共享库 65 条正文合计 413KB，全塞进列表 → 每次打开面板下载 435KB（用户反馈"卡顿"）。
    正文改用 GET /api/notes/<id> 按需取。不传该参数 = 旧行为（带正文），
    以免动到笔记面板等其它调用方。
    """
    if branch_id == DB_BRANCH_ID:
        _q = f"/api/notes?branch_id={DB_BRANCH_ID}" + (f"&kind={kind}" if kind else "") \
             + (f"&folder_id={folder_id}" if folder_id else "") \
             + ("&with_content=0" if with_content == 0 else "")
        p = await _notes_proxy("GET", _q)
        if p is not None:
            return p
    user = _cur_user(request)
    _kn = kind or ""
    if _AUTH_ENABLED and user.get("role") == "admin":
        from tools.file_tools import list_all_users_notes
        notes = list_all_users_notes()
        if branch_id:
            notes = [n for n in notes if n.get("branch_id", "") == branch_id]
        if _kn:
            notes = [n for n in notes if (n.get("kind") or "note") == _kn]
        if folder_id:
            if folder_id == "__root__":
                notes = [n for n in notes if not (n.get("folder_id") or "")]
            else:
                notes = [n for n in notes if (n.get("folder_id") or "") == folder_id]
        return {"status": "ok", "total": len(notes), "notes": _strip_content(notes, with_content)}
    out = json.loads(await list_notes(branch_id or "", user["username"], _kn, folder_id or ""))
    out["notes"] = _strip_content(out.get("notes", []), with_content)
    return out


def _strip_content(notes: list, with_content: Optional[int]) -> list:
    """with_content==0 → 去掉正文（连 content 字段都不出现，省 payload）；
    并补一个 chars 字段，让前端列表能显示字数而无需正文。"""
    if with_content != 0:
        return notes
    lean = []
    for n in notes:
        d = {k: v for k, v in n.items() if k != "content"}
        d["chars"] = len(n.get("content") or "")
        lean.append(d)
    return lean


@app.get("/api/notes/{note_id}")
async def api_notes_get(note_id: str, request: Request, source: Optional[str] = None):
    """取**单条**条目（含正文）。配合列表的 with_content=0 —— 展开/编辑时才取这一条。"""
    if (source == "cloud" or source == DB_BRANCH_ID) and not _AUTH_ENABLED:
        p = await _notes_proxy("GET", f"/api/notes/{note_id}")
        return p if p is not None else JSONResponse({"error": "共享库不可达"}, status_code=502)
    user = _cur_user(request)
    n = get_note(note_id, user["username"])
    if not n:
        return JSONResponse({"error": "条目不存在"}, status_code=404)
    return {"status": "ok", "note": n}
def _notify_notes(branch_id: Optional[str], username: str):
    """条目变了 → 推事件。共享库（branch=__db__）是所有人在看的 → 通知所有人；
    个人笔记只有本人看 → 只通知本人。"""
    if (branch_id or "") == DB_BRANCH_ID:
        _notify_all("notes")
    else:
        _notify(username, "notes")


@app.post("/api/notes")
async def api_notes_add(req: NoteAddReq, request: Request):
    _kd = req.kind or "note"
    user = _cur_user(request)
    if (req.branch_id or "") == DB_BRANCH_ID:
        p = await _notes_proxy("POST", "/api/notes",
                               {"title": req.title, "content": req.content,
                                "branch_id": DB_BRANCH_ID, "kind": _kd,
                                "folder_id": req.folder_id})
        if p is not None:
            return p
    out = json.loads(await add_note(req.title, req.content, req.branch_id or "",
                                    user["username"], kind=_kd,
                                    folder_id=req.folder_id or ""))
    if out.get("status") == "ok":
        _notify_notes(req.branch_id, user["username"])
    return out
@app.post("/api/notes/move")
async def api_notes_move(req: NoteMoveReq, request: Request):
    user = _cur_user(request)
    if (req.branch_id or "") == DB_BRANCH_ID and not _AUTH_ENABLED:
        p = await _notes_proxy("POST", "/api/notes/move", {"note_id": req.note_id, "direction": req.direction})
        return p if p is not None else JSONResponse({"error": "共享库不可达"}, status_code=502)
    if not _owns_or_root(user, get_note_owner(req.note_id, user["username"])):
        return JSONResponse({"error": "只能调整自己的笔记"}, status_code=403)
    out = json.loads(await move_note(req.note_id, req.direction, user["username"]))
    if out.get("status") == "ok":
        _notify_notes(req.branch_id, user["username"])
    return out
@app.put("/api/notes")
async def api_notes_update(req: NoteUpdateReq, request: Request):
    user = _cur_user(request)
    _kd = req.kind
    # 来源显式分流：数据库条目（branch=__db__）只在云端，本地实例直接转发
    if (req.branch_id or "") == DB_BRANCH_ID and not _AUTH_ENABLED:
        p = await _notes_proxy("PUT", "/api/notes",
                               {"note_id": req.note_id, "title": req.title,
                                "content": req.content, "kind": _kd,
                                "folder_id": req.folder_id})
        return p if p is not None else JSONResponse({"error": "共享库不可达"}, status_code=502)
    if not _owns_or_root(user, get_note_owner(req.note_id, user["username"])):
        return JSONResponse({"error": "只能修改自己的笔记"}, status_code=403)
    out = json.loads(await update_note(req.note_id, req.title, req.content,
                                       user["username"], kind=_kd,
                                       folder_id=req.folder_id))
    if out.get("status") == "ok":
        _notify_notes(req.branch_id, user["username"])
    return out
@app.delete("/api/notes/{note_id}")
async def api_notes_delete(note_id: str, request: Request, source: Optional[str] = None):
    user = _cur_user(request)
    # 来源显式分流：source=cloud（数据库条目）→ 本地实例直接转发云端，不做本地扫描
    if (source == "cloud" or source == DB_BRANCH_ID) and not _AUTH_ENABLED:
        p = await _notes_proxy("DELETE", f"/api/notes/{note_id}")
        return p if p is not None else JSONResponse({"error": "共享库不可达"}, status_code=502)
    if not _owns_or_root(user, get_note_owner(note_id, user["username"])):
        return JSONResponse({"error": "只能删除自己的笔记"}, status_code=403)
    out = json.loads(await delete_note(note_id, user["username"]))
    # 删除只知道 note_id、拿不到 branch_id → 走广播（删除是低频操作，多刷一次无害）
    if out.get("status") == "ok":
        _notify_all("notes")
    return out

# ── 向量搜索 ─────────────────────────────────────

@app.post("/api/search/history")
async def api_search_history(req: SearchQueryReq, request: Request):
    return json.loads(await search_history(req.query, req.top_k or 3, user=_cur_user(request)["username"]))
@app.post("/api/xiaoe/ask")
async def api_xiaoe_ask(req: XiaoeAskReq, request: Request):
    """小e 导览问答：单次 LLM 调用，不带工具、不写任何存储（关闭即丢）。
    上下文 = C4EAI 功能说明 + 技能清单 + 用户当前对话（前端传入）。"""
    import httpx as _httpx
    q = (req.question or "").strip()
    if not q:
        return JSONResponse({"error": "问题为空"}, status_code=400)

    # 1) 技能清单（让小e 知道系统有哪些技能）
    skills_txt = ""
    try:
        _sl = json.loads(await skill_list())
        items = _sl.get("skills") or []
        if items:
            skills_txt = "\n".join(
                "- " + str(s.get("name", "")) + "：" + str(s.get("description", ""))[:120]
                for s in items[:40])
    except Exception:
        pass

    # 2) C4EAI 功能说明（内置，便于小e 讲清用法）
    guide = (
        "【C4EAI 功能地图】\n"
        "1) 对话：主区直接提问；可切换「自定义引擎」/「LangChain 引擎」；支持引用笔记/数据库条目（📌）、\n"
        "   附件上传解析、思考过程可视化（工具调用/子智能体均可点开看）。\n"
        "2) 笔记：侧栏笔记面板，markdown 富文本，可插图片/表格/文档附件；支持上下移动排序；\n"
        "   可「上传数据库」把本地笔记原样复制到共享库（云端）。\n"
        "3) 数据库（共享库）：条目=笔记形态，含附件原件+自动解析正文；按 owner 分组；\n"
        "   支持 📌 引用给智能体、浮窗预览/下载原件、删除；上传后全组可检索（向量检索 shared_search）。\n"
        "4) 图谱检索：Neo4j 图谱（graph_schema/execute_cypher）；需要先在配置里连库。\n"
        "5) 设置：模型/服务商配置、界面字号、打开方式、显示小e 开关等。\n"
        "6) 账号：可用自己的数据库（注册即蓝色 L1）；查询所有人数据库需橙色 L3 起，"
        "身份等级由金色(L5)或主管理员在用户管理里调整。\n"
        "7) 子智能体：主智能体可派发子任务（delegate_task），并行处理、有超时保护。\n"
        "8) 技能：智能体可读取技能库（skill_view）按流程做事。\n"
    )

    # 3) 组装 prompt（单次调用，不带工具）
    sys_p = ("你是 C4EAI 的桌宠导览员「小e」，一只可爱的云朵团。性格：活泼、亲切、爱用颜文字，"
             "回答简短（一般 2-5 句，最多 6 行），多用换行和 emoji，不要长篇大论。"
             "你的职责是介绍 C4EAI 的功能、教用户怎么用、回答与本站功能/检索/笔记/数据库相关的问题。"
             "如果用户问的是纯学术问题，也可以简短回答，但优先引导他用主对话的智能体（功能更全）。"
             "你不知道或不确定的，直接说不确定，不要编造功能。")
    user_p = guide
    if skills_txt:
        user_p += "\n【系统当前技能清单】\n" + skills_txt + "\n"
    if (req.context or "").strip():
        user_p += "\n【用户当前对话片段（供参考，不要复述隐私内容）】\n" + req.context.strip()[:3000] + "\n"
    hist_txt = ""
    for h in (req.history or [])[-8:]:
        if isinstance(h, dict) and h.get("content"):
            hist_txt += ("小e之前说过：" if h.get("role") == "assistant" else "用户之前问过：") + str(h["content"])[:300] + "\n"
    if hist_txt:
        user_p += "\n【你们之前的对话】\n" + hist_txt
    user_p += "\n【用户现在问】\n" + q

    # 4) 模型配置：沿用请求里的（前端传主对话同款模型）；缺省用环境变量
    mc = {}
    try:
        body = await request.json()
        mc = (body or {}).get("model") or {}
    except Exception:
        mc = {}
    endpoint = mc.get("base_url") or os.environ.get("C4EAI_LLM_BASE", "")
    if not endpoint.rstrip("/").endswith("/chat/completions"):
        endpoint = endpoint.rstrip("/") + "/chat/completions"
    model = mc.get("model") or os.environ.get("C4EAI_LLM_MODEL", "qwen3")
    api_key = mc.get("api_key") or ""

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
        "temperature": 0.7,
        # 思考型模型（Qwen3 等）的 reasoning_content 会先占额度：
        # 太小会导致 content 为空、被误判成"答不出"。小e 只答短句，但需给思考留余地。
        "max_tokens": 2048,
        "stream": False,
        # 关闭思考链：桌宠问答要快（同主对话的关 thinking 策略）
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(connect=15, read=180, write=30, pool=15)) as cli:
            r = await cli.post(endpoint, json=payload, headers=headers)
            if r.status_code != 200 and "chat_template_kwargs" in r.text:
                # 个别后端不认该参数 → 去掉重试一次（保证兼容）
                payload.pop("chat_template_kwargs", None)
                r = await cli.post(endpoint, json=payload, headers=headers)
        if r.status_code != 200:
            return JSONResponse({"error": f"模型接口 {r.status_code}: {r.text[:150]}"}, status_code=200)
        data = r.json()
        _msg = ((data.get("choices") or [{}])[0].get("message") or {})
        ans = (_msg.get("content") or "").strip()
        if not ans:
            # 兜底：部分思考模型把答案放在 reasoning_content（关闭思考失败时）
            ans = (_msg.get("reasoning_content") or "").strip()
        ans = ans or "（小e 没想出来…再问一次？）"
        return {"answer": ans}
    except Exception as e:
        return JSONResponse({"error": f"小e 连不上模型：{str(e)[:120]}"}, status_code=200)


@app.post("/api/search/knowledge")
async def api_search_knowledge(req: SearchQueryReq, request: Request):
    """共享池语义检索（仅云端；需已授权）。"""
    user = _cur_user(request)
    if _AUTH_ENABLED and not _is_authorized(user):
        return JSONResponse({"error": "已注册，等待管理员开通数据库权限"}, status_code=403)
    return json.loads(await search_knowledge(req.query, req.top_k or 3))


class GroupOpReq(BaseModel):
    """分组树操作（分组数据在共享库 = 云端 pool/_folders.json）"""
    action: str = "list_groups"
    group_id: str = ""
    name: str = ""
    parent_id: str = ""
    intro: str = ""
    mode: str = "move_up"
    parse: Optional[bool] = None   # 解析策略（一般跟随父夹，无需显式传）


@app.post("/api/groups")
async def api_groups(req: GroupOpReq, request: Request):
    """共享库分组树：查看/新建/改名/移动/删除。

    分组属于共享库（与条目同侧）—— 本地实例经此端点代理到云端；
    云端实例直接操作本机 pool/_folders.json。
    权限：与共享库一致（需已开通数据库权限）。
    """
    user = _cur_user(request)
    if _AUTH_ENABLED and not _is_authorized(user):
        return JSONResponse({"error": "已注册，等待管理员开通数据库权限"}, status_code=403)

    import tools.folder_tree as FT
    FT.ensure_roots()      # 幂等：确保「笔记」「文档」两个根文件夹存在
    action = (req.action or "list_groups").strip().lower()
    if action == "list_groups":
        return json.loads(FT.list_groups())
    if action == "create_group":
        return json.loads(FT.create_group(req.name, req.parent_id or "", req.intro or "",
                                          parse=req.parse))
    if action == "update_group":
        return json.loads(FT.update_group(req.group_id, req.name or None,
                                          req.intro if req.intro != "" else None))
    if action == "move_group":
        return json.loads(FT.move_group(req.group_id, req.parent_id or ""))
    if action == "delete_group":
        return json.loads(FT.delete_group(req.group_id, req.mode or "move_up"))
    return JSONResponse({"error": f"未知 action: {action}"}, status_code=400)


# ── 个人设置（后端存储）──────────────────────────
# 用户 2026-09-26 定：密钥/模型/智能体配置等原本存浏览器 localStorage，
# 改为存「个人账号数据」（workspace/users/<身份>/settings.json）。
# 「反正也就开发者知道密钥」→ 明文存本人目录即可。
#
# 安全：owner 一律从登录身份推导（_cur_user），**绝不用请求体传入的身份** ——
# 否则可以越权读写他人设置。与 WeKnora「记忆工具不接受 owner 参数」同款原则。

class UserSettingsReq(BaseModel):
    settings: Dict[str, Any] = {}


@app.get("/api/user/settings")
async def api_user_settings_get(request: Request):
    """读本人的后端设置。本地实例 → 转发云端（与笔记/附件同一套分流）。"""
    p = await _notes_proxy("GET", "/api/user/settings")
    if p is not None:
        return p
    user = _cur_user(request)
    return json.loads(US.load_user_settings(user["username"]))


@app.put("/api/user/settings")
async def api_user_settings_put(req: UserSettingsReq, request: Request):
    """合并写入本人的后端设置。只接受白名单键（见 user_settings.MANAGED_KEYS）。"""
    p = await _notes_proxy("PUT", "/api/user/settings", {"settings": req.settings})
    if p is not None:
        return p
    user = _cur_user(request)
    return json.loads(US.save_user_settings(user["username"], req.settings))


# ── 对话历史的后端存储（用户 2026-09-26 定）──────────────────────
# 对话（分支 + 全部消息 + 分支笔记 + 排序 + 当前对话）原本只在浏览器 localStorage，
# 换设备就全丢。这里把它搬到「个人账号数据」，与 settings.json 同套机制。
# 并发：单账号单会话（auth.login 登录即踢旧 token）→ 只有一个写者 → 整份覆盖，不做合并。

class UserChatsReq(BaseModel):
    chats: Dict[str, Any] = {}


# ── 事件推送（SSE）────────────────────────────────
# 用户 2026-09-26 定：两边允许同时登录，A 的对话要出现在 B 的前端。
# 轮询要每 N 秒问一次；SSE 由后端**在变更时**推送 → 空闲时零请求。
# 复用现成机制：app.py 本来就有 StreamingResponse 流式，nginx 也早就配了 proxy_buffering off。
#
# 前端用 fetch + body.getReader() 消费（与 /api/agent/stream 同一套写法），
# 因为它要带 Authorization 头 —— EventSource 不能设请求头，这是必须知道的。
#
# 断开期间的事件必然漏掉 → 客户端重连后先拉一次全量对齐（见 js/events.js）。

_subscribers: Dict[str, set] = {}      # username → {asyncio.Queue}


def _notify(username: str, kind: str):
    """通知某个用户的在线页面：他的某类数据变了。没有订阅者时零开销。"""
    for q in list(_subscribers.get(username) or ()):
        try:
            q.put_nowait({"type": kind})
        except Exception:
            pass


def _notify_all(kind: str):
    """共享库变了 → 所有在线的人都要刷新（不限本人）。"""
    for _u in list(_subscribers.keys()):
        _notify(_u, kind)


@app.get("/api/events")
async def api_events(request: Request):
    """事件流（SSE）。前端收到 {type:"chats"|"notes"} 就去拉对应数据并合并。"""
    user = _cur_user(request)
    uname = user["username"]
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(uname, set()).add(q)

    async def gen():
        try:
            yield ": connected\n\n"          # 立刻建连（否则某些代理会等首字节超时）
            yield f"data: {json.dumps({'type': 'hello', 'rev': _chats_rev_of(uname)})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # 心跳，防中间设备掐掉空闲连接
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            _subscribers.get(uname, set()).discard(q)
            if not _subscribers.get(uname):
                _subscribers.pop(uname, None)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _chats_rev_of(username: str) -> str:
    try:
        return json.loads(UC.chats_rev(username)).get("rev", "")
    except Exception:
        return ""


@app.get("/api/user/chats/version")
async def api_user_chats_version(request: Request):
    """几十字节的版本号。SSE 断线重连后用它判断「我们漏掉了变化」→ 才拉全量。"""
    p = await _notes_proxy("GET", "/api/user/chats/version")
    if p is not None:
        return p
    user = _cur_user(request)
    return json.loads(UC.chats_rev(user["username"]))


@app.get("/api/user/chats")
async def api_user_chats_get(request: Request):
    """读本人的全部对话。本地实例 → 转发云端（与笔记/设置同一套分流）。"""
    p = await _notes_proxy("GET", "/api/user/chats")
    if p is not None:
        return p
    user = _cur_user(request)
    return json.loads(UC.load_user_chats(user["username"]))


@app.put("/api/user/chats")
async def api_user_chats_put(req: UserChatsReq, request: Request):
    """**按对话 id 合并**写入本人的对话（不是覆盖 —— 两边同时在线会互相抹）。
    owner 从 token 推导，不接受请求体传身份。"""
    p = await _notes_proxy("PUT", "/api/user/chats", {"chats": req.chats})
    if p is not None:
        return p
    user = _cur_user(request)
    out = json.loads(UC.save_user_chats(user["username"], req.chats))
    if out.get("status") == "ok":
        _notify(user["username"], "chats")   # 推给该账号的**其它**页面（本页收到也无害）
    return out


# ── LangChain Agent ──────────────────────────────

def _build_model_config(req):
    return {
        "model": req.model.get("model", "deepseek-chat"),
        "api_key": req.model.get("api_key"),
        "base_url": req.model.get("base_url", "https://api.deepseek.com"),
        "temperature": req.model.get("temperature", 0.7),
        "max_tokens": req.model.get("max_tokens") or 8192,
    }

async def _resolve_refs(refs: list, owner: str) -> str:
    """把用户引用的笔记/数据库条目拼成上下文块：按 scope+id 取**全文**并注入（不截断）。
    scope='notes'    → 本地对话笔记（users/<身份>/notes.json）
    scope='database' → 云端共享数据库条目（branch=__db__）
    前端只传 {id, scope, title} 元数据，正文由后端按 id 现取——保证与库中内容一致，
    也避免把长正文塞进浏览器 localStorage。"""
    if not refs:
        return ""
    from tools import file_tools as _ft
    from tools.shared_hub import db_notes
    blocks = []
    for r in refs:
        if not isinstance(r, dict):
            continue
        scope = (r.get("scope") or r.get("kind") or "notes").strip()
        rid = str(r.get("id") or "")
        title = str(r.get("title") or "")
        content = ""
        try:
            if scope in ("database", "db", "__db__", "cloud", "shared"):
                scope = "database"
                res = await db_notes("read", rid, "", "", owner)
                e = res.get("entry") or {}
                content = e.get("content") or ""
                title = title or e.get("title", "")
                if not content:
                    content = "（读取失败：" + str(res.get("error", "条目不存在或无权访问")) + "）"
            else:
                scope = "notes"
                n, _ = _ft._find_note(owner, rid)
                if n is None:
                    content = "（读取失败：笔记不存在或无权访问）"
                else:
                    content = n.get("content") or ""
                    title = title or n.get("title", "")
        except Exception as ex:
            content = f"（读取异常：{ex}）"
        blocks.append(f"▸ scope={scope} id={rid} 标题={title}\n{content}")
    if not blocks:
        return ""
    head = ("【用户引用的条目（以下为完整内容；如需修改请用 conversation_notes 工具，"
            "参数带同样的 scope 与 id）】")
    return head + "\n\n" + "\n\n".join(blocks) + \
        "\n\n—————— 以上为引用内容，以下是用户本次的问题 ——————\n"


@app.post("/api/agent/run")
async def agent_run(req: AgentRunRequest, request: Request):
    if not req.model or not req.model.get("model"):
        return {"final_answer": "错误: 缺少模型配置"}
    u = _policy_for(request, req)
    mc = _build_model_config(req)
    if _RP.HUB and _RP.is_local_qwen(mc):
        used, limit, ok = _RP.qwen_usage(u)
        if not ok:
            return {"final_answer": f"⛔ 今日本地千问额度已用完（{used:,}/{limit:,} token）。请明日再试、切换你自己配置的模型，或联系管理员开通数据库权限。"}
    _ans = await run_agent(history=req.history, user_input=await _resolve_refs(req.note_refs, u["username"]) + req.input,
        enabled_tools=req.enabled_tools, loaded_skills=req.loaded_skills,
        model_config=mc, neo4j_config=req.neo4j_config,
        context_length=req.context_length or 10, branch_id=req.branch_id or "",
        file_ids=req.file_ids or [], full_file_ids=req.full_file_ids,
        permission_mode=req.permission_mode or "safe",
        user=u["username"],
        timeouts=req.timeouts or {}, subagent=req.subagent or {})
    try:
        _txt = json.loads(_ans).get("final_answer", "") if isinstance(_ans, str) else str(_ans)
    except Exception:
        _txt = str(_ans)
    if _RP.HUB and _RP.is_local_qwen(mc):
        _RP.add_qwen_usage(u, len(req.input) + sum(len(str(m.get('content',''))) for m in req.history if isinstance(m, dict)) + len(_txt))
    return _ans

@app.post("/api/agent/stream")
async def agent_stream(req: AgentRunRequest, request: Request = None):
    """SSE 流式 Agent 端点（支持用户停止：前端 abort → 检测断连 → 取消生成）"""
    if not req.model or not req.model.get("model"):
        return {"final_answer": "错误: 缺少模型配置"}

    from agent.agent import cancel_active_generation, reset_generation_cancel

    async def event_generator():
        engine = (req.engine or "custom").lower()
        max_rounds = req.max_rounds if req.max_rounds is not None else 8
        runner = stream_agent_lc if engine == "langchain" else stream_agent
        reset_generation_cancel()

        # 云端角色策略：裁剪工具 + 钳制权限模式 + 注入文件沙盒
        user = _policy_for(request, req) if request is not None else _cur_user(request)
        mc = _build_model_config(req)
        _qwen = _RP.HUB and _RP.is_local_qwen(mc)
        if _qwen:
            _used, _limit, _ok = _RP.qwen_usage(user)
            if not _ok:
                msg = f"⛔ 今日本地千问额度已用完（{_used:,}/{_limit:,} token）。请明日再试、切换你自己配置的模型，或联系管理员开通数据库权限。"
                yield f"data: {json.dumps({'type':'error','message':msg}, ensure_ascii=False)}\n\n"
                return

        # 断连看门狗：前端 abort / 踢掉连接时置位取消标记（不依赖 HTTP 层，可靠）
        stop_flag = asyncio.Event()
        async def watchdog():
            while not stop_flag.is_set():
                try:
                    if request is not None and await request.is_disconnected():
                        cancel_active_generation()
                        return
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(stop_flag.wait(), timeout=0.4)
                except asyncio.TimeoutError:
                    pass

        w = asyncio.create_task(watchdog())
        cur_user = user["username"]
        _chars = len(req.input) + sum(len(str(m.get("content", ""))) for m in req.history if isinstance(m, dict))
        try:
            async for event in runner(history=req.history, user_input=await _resolve_refs(req.note_refs, cur_user) + req.input,
                enabled_tools=req.enabled_tools, loaded_skills=req.loaded_skills,
                model_config=mc, neo4j_config=req.neo4j_config,
                context_length=req.context_length or 10, branch_id=req.branch_id or "",
                file_ids=req.file_ids or [], full_file_ids=req.full_file_ids,
                permission_mode=req.permission_mode or "safe",
                images=req.images or [],
                max_rounds=max_rounds,
                user=cur_user,
                timeouts=req.timeouts or {}, subagent=req.subagent or {}):
                if isinstance(event, dict) and event.get("type") in ("token", "reasoning"):
                    _chars += len(event.get("text", "") or "")
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            stop_flag.set()
            w.cancel()
            reset_generation_cancel()
            if _qwen:
                _RP.add_qwen_usage(user, _chars)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ── 平台探测（前端据此隐藏 Windows 专属按钮） ──────────

@app.get("/api/platform")
async def api_platform():
    # hub=共享库地址（本地实例前端据此拼接共享池文件链接；共享库自身为空=同源）
    return {"platform": sys.platform, "os": os.name, "hub": _hub_base()}


# ── 静态文件 ──────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent
if (_PROJECT_ROOT / "index.html").exists():
    # 品牌路径：http://<IP>:8088/c4eai/  与 http://<IP>:8088/ 指向同一应用（组员零配置）
    # 必须先于 "/" 挂载注册（路由按注册顺序匹配，"/" 会兜底吃掉一切）
    app.mount("/c4eai", StaticFiles(directory=str(_PROJECT_ROOT), html=True), name="frontend-brand")
    app.mount("/", StaticFiles(directory=str(_PROJECT_ROOT), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    # 监听地址/端口可用环境变量覆盖：服务器/局域网部署设 C4EAI_HOST=0.0.0.0（默认仍只监听本机，安全）
    _host = os.environ.get("C4EAI_HOST", "127.0.0.1")
    _port = int(os.environ.get("C4EAI_PORT", "8080"))
    print("=" * 56)
    print(f"  C4EAI Server ({_host}:{_port}, SSE streaming)")
    print("=" * 56)
    print(f"  Frontend: http://{_host}:{_port}")
    print("  Agent:    POST /api/agent/run (sync)")
    print("           POST /api/agent/stream (SSE)")
    uvicorn.run(app, host=_host, port=_port, log_level="info")
