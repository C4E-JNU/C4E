"""
C4EAI 后端 — FastAPI 入口
工具执行 + LangChain Agent(SSE流式) + 文件上传 + 笔记 CRUD + 静态文件
"""
import json, sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
import asyncio
from pydantic import BaseModel
from typing import Optional

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
)
from tools.vector_search import search_history, search_knowledge
from tools.mineru_tools import save_token as _mineru_save_token, load_token as _mineru_load_token
from agent.tools import tools_registry as _tools_registry
from agent.agent import run_agent, stream_agent, stream_agent_lc

app = FastAPI(title="C4EAI Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ── 禁止缓存 JS/CSS/HTML ─────────────────────────
@app.middleware("http")
async def no_cache_middleware(request, call_next):
    response = await call_next(request)
    if any(request.url.path.startswith(p) for p in ("/js/", "/css/", "/index.html")):
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
class NoteUpdateReq(BaseModel):
    note_id: str; title: Optional[str] = None; content: Optional[str] = None
class NoteMoveReq(BaseModel):
    note_id: str; direction: int
class SearchQueryReq(BaseModel):
    query: str; top_k: Optional[int] = 3
class AgentRunRequest(BaseModel):
    input: str; history: list = []
    enabled_tools: list[str] = []
    loaded_skills: list[str] = []
    neo4j_config: Optional[dict] = None
    model: Optional[dict] = None
    context_length: Optional[int] = 10
    branch_id: Optional[str] = ""
    file_ids: list[str] = []
    images: list[str] = []          # base64(dataURL) 图片，多模态给模型
    permission_mode: str = "safe"
    engine: str = "custom"          # custom=裸API手写循环 / langchain=LangChain流式
    max_rounds: Optional[int] = None  # 工具循环步数，0=无限

# ── 健康检查 ──────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "neo4j_connected": Neo4jClient.is_connected()}

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
async def mineru_config_get():
    """读取 MinerU 配置（脱敏返回是否已配置）。"""
    token = _mineru_load_token()
    return {"configured": bool(token), "token": token if token else ""}

@app.post("/api/config/mineru")
async def mineru_config_set(req: MinerUConfigRequest):
    """保存 MinerU API Token 到后端配置文件。"""
    if not req.token or not req.token.strip():
        _mineru_save_token("")
        return {"success": True, "configured": False}
    _mineru_save_token(req.token.strip())
    return {"success": True, "configured": True}

# ── 工具端点 ──────────────────────────────────────

@app.post("/api/tool/schema")
async def tool_schema():
    return json.loads(await execute_graph_schema())
@app.post("/api/tool/cypher")
async def tool_cypher(req: CypherRequest):
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
async def tool_run_code(req: RunCodeRequest):
    return json.loads(await run_python(req.code, req.timeout or 30))
@app.post("/api/tool/read-file")
async def tool_read_file(req: ReadFileRequest):
    return json.loads(await read_file(req.path, req.offset or 1, req.limit or 500))
@app.post("/api/tool/write-file")
async def tool_write_file(req: WriteFileRequest):
    return json.loads(await write_file(req.path, req.content))
@app.post("/api/tool/list-files")
async def tool_list_files(req: ListFilesRequest):
    return json.loads(await list_files(req.subdir or ""))

# ── 文件上传 ──────────────────────────────────────

@app.post("/api/file/upload")
async def api_file_upload(file: UploadFile = File(...)):
    content = await file.read()
    # upload_file 是 async 但内部无 await（纯 CPU 密集文本提取），在线程池执行避免阻塞事件循环
    def _upload_sync(fn, c):
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_upload_file(fn, c))
            finally:
                loop.close()
        except Exception:
            import asyncio as a2
            return a2.run(_upload_file(fn, c))
    import asyncio
    return json.loads(await asyncio.to_thread(_upload_sync, file.filename or "unknown", content))
@app.post("/api/file/delete")
async def api_file_delete(req: FileIdRequest):
    return json.loads(await _delete_upload(req.file_id))
@app.post("/api/file/text")
async def api_file_text(req: FileIdRequest):
    text = await get_upload_text(req.file_id)
    return {"file_id": req.file_id, "text": text}

@app.get("/api/file/{file_id}")
async def api_file_download(file_id: str):
    from tools.file_tools import _UPLOADS_DIR
    meta_path = os.path.join(_UPLOADS_DIR, file_id, "meta.json")
    if not os.path.isfile(meta_path):
        from fastapi import HTTPException; raise HTTPException(404, "文件不存在")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    filename = meta.get("name", "file")
    file_path = os.path.join(_UPLOADS_DIR, file_id, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".docx", ".doc"):
        html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>打开文件</title>
<style>body{{font-family:sans-serif;text-align:center;padding:60px 20px;}}
.btn{{display:inline-block;padding:14px 32px;font-size:16px;background:#185ab4;color:#fff;border-radius:6px;text-decoration:none;}}</style></head><body>
<h2>📄 {filename}</h2>
<a class="btn" href="ms-word:ofe|u|http://127.0.0.1:8080/api/file/{file_id}/raw">在 Word 中打开</a></body></html>'''
        from fastapi.responses import HTMLResponse; return HTMLResponse(html)
    from fastapi.responses import FileResponse
    from urllib.parse import quote; import mimetypes
    encoded_filename = quote(filename)
    headers = {"Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"}
    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(file_path, headers=headers, media_type=media_type or "application/octet-stream")

@app.post("/api/file/{file_id}/open")
async def api_file_open(file_id: str):
    from tools.file_tools import _UPLOADS_DIR
    meta_path = os.path.join(_UPLOADS_DIR, file_id, "meta.json")
    if not os.path.isfile(meta_path):
        from fastapi import HTTPException; raise HTTPException(404, "文件不存在")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    filename = meta.get("name", "file")
    file_path = os.path.join(_UPLOADS_DIR, file_id, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    os.startfile(file_path)
    return {"status": "ok", "message": f"已用默认应用打开: {filename}"}

@app.get("/api/file/{file_id}/raw")
async def api_file_raw(file_id: str):
    from fastapi.responses import FileResponse
    from tools.file_tools import _UPLOADS_DIR
    meta_path = os.path.join(_UPLOADS_DIR, file_id, "meta.json")
    if not os.path.isfile(meta_path):
        from fastapi import HTTPException; raise HTTPException(404, "文件不存在")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    filename = meta.get("name", "file")
    file_path = os.path.join(_UPLOADS_DIR, file_id, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    from urllib.parse import quote; import mimetypes
    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(file_path, media_type=media_type or "application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

@app.post("/api/file/{file_id}/open-location")
async def api_file_open_location(file_id: str):
    """在资源管理器中打开文件所在文件夹并选中该文件。"""
    import subprocess
    from tools.file_tools import _UPLOADS_DIR
    meta_path = os.path.join(_UPLOADS_DIR, file_id, "meta.json")
    if not os.path.isfile(meta_path):
        from fastapi import HTTPException; raise HTTPException(404, "文件不存在")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    filename = meta.get("name", "file")
    file_path = os.path.join(_UPLOADS_DIR, file_id, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件数据不存在")
    subprocess.Popen(r'explorer /select,"' + file_path + '"')
    return {"status": "ok", "message": f"已在资源管理器中打开: {filename}"}

# ── 笔记 CRUD ─────────────────────────────────────

@app.get("/api/notes")
async def api_notes_list(branch_id: Optional[str] = None):
    return json.loads(await list_notes(branch_id or ""))
@app.post("/api/notes")
async def api_notes_add(req: NoteAddReq):
    return json.loads(await add_note(req.title, req.content, req.branch_id or ""))
@app.post("/api/notes/move")
async def api_notes_move(req: NoteMoveReq):
    return json.loads(await move_note(req.note_id, req.direction))
@app.put("/api/notes")
async def api_notes_update(req: NoteUpdateReq):
    return json.loads(await update_note(req.note_id, req.title, req.content))
@app.delete("/api/notes/{note_id}")
async def api_notes_delete(note_id: str):
    return json.loads(await delete_note(note_id))

# ── 向量搜索 ─────────────────────────────────────

@app.post("/api/search/history")
async def api_search_history(req: SearchQueryReq):
    return json.loads(await search_history(req.query, req.top_k or 3))
@app.post("/api/search/knowledge")
async def api_search_knowledge(req: SearchQueryReq):
    return json.loads(await search_knowledge(req.query, req.top_k or 3))

# ── LangChain Agent ──────────────────────────────

def _build_model_config(req):
    return {
        "model": req.model.get("model", "deepseek-chat"),
        "api_key": req.model.get("api_key"),
        "base_url": req.model.get("base_url", "https://api.deepseek.com"),
        "temperature": req.model.get("temperature", 0.7),
    }

@app.post("/api/agent/run")
async def agent_run(req: AgentRunRequest):
    if not req.model or not req.model.get("api_key"):
        return {"final_answer": "错误: 缺少模型配置"}
    return await run_agent(history=req.history, user_input=req.input,
        enabled_tools=req.enabled_tools, loaded_skills=req.loaded_skills,
        model_config=_build_model_config(req), neo4j_config=req.neo4j_config,
        context_length=req.context_length or 10, branch_id=req.branch_id or "",
        file_ids=req.file_ids or [], permission_mode=req.permission_mode or "safe")

@app.post("/api/agent/stream")
async def agent_stream(req: AgentRunRequest, request: Request = None):
    """SSE 流式 Agent 端点（支持用户停止：前端 abort → 检测断连 → 取消生成）"""
    if not req.model or not req.model.get("api_key"):
        return {"final_answer": "错误: 缺少模型配置"}

    from agent.agent import cancel_active_generation, reset_generation_cancel

    async def event_generator():
        engine = (req.engine or "custom").lower()
        max_rounds = req.max_rounds if req.max_rounds is not None else 8
        runner = stream_agent_lc if engine == "langchain" else stream_agent
        reset_generation_cancel()

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
        try:
            async for event in runner(history=req.history, user_input=req.input,
                enabled_tools=req.enabled_tools, loaded_skills=req.loaded_skills,
                model_config=_build_model_config(req), neo4j_config=req.neo4j_config,
                context_length=req.context_length or 10, branch_id=req.branch_id or "",
                file_ids=req.file_ids or [], permission_mode=req.permission_mode or "safe",
                images=req.images or [],
                max_rounds=max_rounds):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            stop_flag.set()
            w.cancel()
            reset_generation_cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ── 静态文件 ──────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent
if (_PROJECT_ROOT / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_PROJECT_ROOT), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    print("=" * 56)
    print("  C4EAI Server (port 8080, SSE streaming)")
    print("=" * 56)
    print("  Frontend: http://127.0.0.1:8080")
    print("  Agent:    POST /api/agent/run (sync)")
    print("           POST /api/agent/stream (SSE)")
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
