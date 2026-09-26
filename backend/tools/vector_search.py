"""
向量搜索引擎 — bge-small-zh-v1.5 (fastembed)
对话历史 + 知识库的增量索引与语义搜索
"""
import json, os, time, numpy as np
from pathlib import Path

_WORKSPACE = Path(__file__).parent.parent.parent / "workspace"
# 知识库索引唯一在共享池 pool/（仅云端共享库使用；本地实例无数据库）
_KNOWLEDGE_INDEX_FILE = _WORKSPACE / "pool" / "_vector_knowledge.json"
# 聊天历史索引/元数据按身份分文件：_vector_history_<key>.json / _vector_meta_<key>.json（key 空=旧版全局）
_META_FILE_LEGACY = _WORKSPACE / "_vector_meta.json"


def _user_key_vs(username: str) -> str:
    u = (username or "").strip()
    if not u or u == "guest":
        return "_guest"
    k = "".join(c for c in u if (c.isalnum() or c in "-_@.·"))
    return k or "_guest"


def _history_index_file(user: str = ""):
    if not user:
        return _WORKSPACE / "_vector_history.json"   # 旧版全局
    return _WORKSPACE / f"_vector_history_{_user_key_vs(user)}.json"


def _meta_file(user: str = ""):
    if not user:
        return _META_FILE_LEGACY
    return _WORKSPACE / f"_vector_meta_{_user_key_vs(user)}.json"

# ── 全局单例 ───────────────────────────────────────
_model = None

# 相似度阈值：bge-small-zh 下 0.3 太低（会把不相关块放进来）。
# 0.42 = 实测 bge 中文语义相关的经验下限（真相关通常 0.5~0.8）。
_SCORE_THRESHOLD = float(os.environ.get("C4EAI_SCORE_THRESHOLD", "0.42"))


def _relevance_level(score: float) -> str:
    """把裸分数翻译成人话 —— 给 LLM 判断"该不该采信"用（借鉴 WeKnora）。"""
    if score >= 0.75:
        return "高相关"
    if score >= 0.60:
        return "中相关"
    if score >= _SCORE_THRESHOLD:
        return "低相关"
    return "弱相关"

def _get_model():
    global _model
    if _model is not None:
        return _model
    from fastembed import TextEmbedding
    _model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5", cache_dir=str(_WORKSPACE.parent / "model_cache"))
    return _model


# ── 文本切块 ──────────────────────────────────────

def chunk_text(text: str, max_chars: int = 500, overlap: int = 50) -> list[str]:
    """将长文本切成固定长度的块（与前端 database.js 一致）"""
    if not text:
        return []
    paragraphs = text.split("\n")
    chunks = []
    current = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if len(current) + len(p) + 1 > max_chars and current:
            chunks.append(current.strip())
            # 重叠：保留尾部 overlap 字符
            current = current[-overlap:] + "\n" + p if len(current) > overlap else p
        else:
            current = (current + "\n" + p).strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ── 向量计算 ──────────────────────────────────────

def embed_texts(texts: list[str]) -> np.ndarray:
    """批量生成 embedding，返回 numpy array (n, dim)"""
    model = _get_model()
    results = list(model.embed(texts))
    return np.array(results, dtype=np.float32)


def cosine_similarity(query_vec: np.ndarray, doc_vecs: np.ndarray) -> np.ndarray:
    """计算查询向量与文档向量的余弦相似度"""
    norm_q = np.linalg.norm(query_vec)
    if norm_q == 0:
        return np.zeros(len(doc_vecs))
    norm_d = np.linalg.norm(doc_vecs, axis=1)
    norm_d[norm_d == 0] = 1
    return np.dot(doc_vecs, query_vec) / (norm_d * norm_q + 1e-10)


# ── 索引管理 ──────────────────────────────────────

def _load_index(file_path: Path) -> list[dict]:
    """加载向量索引文件"""
    if not file_path.exists():
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_index(file_path: Path, index: list[dict]):
    """保存向量索引"""
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _load_meta(user: str = "") -> dict:
    """加载索引元数据（last_indexed_id 等），按身份分文件"""
    mf = _meta_file(user)
    if not mf.exists():
        return {"last_indexed_id": None, "history_chunks": 0, "knowledge_chunks": 0}
    try:
        with open(mf, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"last_indexed_id": None, "history_chunks": 0, "knowledge_chunks": 0}


def _save_meta(meta: dict, user: str = ""):
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    with open(_meta_file(user), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ── 公开 API ──────────────────────────────────────

def add_to_history_index(chunks: list[dict], user: str = ""):
    """追加新的历史对话块到索引（按身份）"""
    hf = _history_index_file(user)
    index = _load_index(hf)
    index.extend(chunks)
    _save_index(hf, index)
    meta = _load_meta(user)
    meta["history_chunks"] = len(index)
    _save_meta(meta, user)


def add_to_knowledge_index(chunks: list[dict]):
    """追加新的知识库块到共享池索引"""
    index = _load_index(_KNOWLEDGE_INDEX_FILE)
    index.extend(chunks)
    _save_index(_KNOWLEDGE_INDEX_FILE, index)
    meta = _load_meta()
    meta["knowledge_chunks"] = len(index)
    _save_meta(meta)


def ingest_text_to_knowledge(text: str, doc_name: str, file_id: str = None) -> int:
    """把文档文本切块 + 向量化 + 写入知识索引（上传流水线调用）。
    返回入库的块数；空文本返回 0。入库失败抛异常，由调用方决定是否吞掉。"""
    chunks = chunk_text(text)
    if not chunks:
        return 0
    vecs = embed_texts(chunks)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    items = [{
        "text": c,
        "vector": vecs[i].tolist(),
        "doc_name": doc_name,
        "source": "knowledge",
        "file_id": file_id or "",
        "created": now,
    } for i, c in enumerate(chunks)]
    add_to_knowledge_index(items)
    return len(items)


def remove_from_knowledge_index(file_id: str) -> int:
    """按 file_id 从知识索引里移除该文件的所有块（删除文件时调用），返回移除块数。"""
    index = _load_index(_KNOWLEDGE_INDEX_FILE)
    kept = [c for c in index if c.get("file_id") != file_id]
    removed = len(index) - len(kept)
    if removed:
        _save_index(_KNOWLEDGE_INDEX_FILE, kept)
        meta = _load_meta()
        meta["knowledge_chunks"] = len(kept)
        _save_meta(meta)
    return removed


def _index_vectors(index: list[dict]) -> np.ndarray | None:
    """取索引里现成的向量矩阵 (n, dim)；任一块缺向量则返回 None（调用方回退重算）。

    入库时已把向量写进 chunk["vector"]，检索直接用它 —— 不再每次重算全库。
    """
    try:
        vecs = [c.get("vector") for c in index]
        if not vecs or any(v is None for v in vecs):
            return None
        m = np.array(vecs, dtype=np.float32)
        if m.ndim != 2 or m.shape[0] != len(index):
            return None
        return m
    except Exception:
        return None


async def search_history(query: str, top_k: int = 3, scope: str = None, user: str = "") -> str:
    """搜索历史对话（按身份分索引），返回 JSON 字符串"""
    index = _load_index(_history_index_file(user))
    if not index:
        return json.dumps({"status": "ok", "count": 0, "results": []})

    try:
        q_vec = embed_texts([query])[0]
        # 优先用索引里现成的向量；缺失（老索引/异常）才重算，保证结果不变
        doc_vecs = _index_vectors(index)
        if doc_vecs is None:
            doc_vecs = embed_texts([c["text"] for c in index])
        scores = cosine_similarity(q_vec, doc_vecs)
        top_idx = np.argsort(scores)[-top_k:][::-1]

        results = []
        for i in top_idx:
            if scores[i] > _SCORE_THRESHOLD:  # 阈值过滤
                results.append({
                    "score": round(float(scores[i]), 4),
                    "relevance": _relevance_level(float(scores[i])),
                    "text": index[i]["text"],
                    "conversation": index[i].get("conversation", ""),
                    "source": index[i].get("source", "history"),
                })

        return json.dumps({"status": "ok", "count": len(results), "results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {str(e)}"})


async def search_knowledge(query: str, top_k: int = 3, scope: str = None) -> str:
    """搜索知识库文档，返回 JSON 字符串"""
    index = _load_index(_KNOWLEDGE_INDEX_FILE)
    if not index:
        return json.dumps({"status": "ok", "count": 0, "results": []})

    try:
        q_vec = embed_texts([query])[0]
        # 优先用索引里现成的向量（不再每次重算全库 embedding）
        doc_vecs = _index_vectors(index)
        if doc_vecs is None:
            doc_vecs = embed_texts([c["text"] for c in index])
        scores = cosine_similarity(q_vec, doc_vecs)
        top_idx = np.argsort(scores)[-top_k:][::-1]

        results = []
        for i in top_idx:
            if scores[i] > _SCORE_THRESHOLD:
                results.append({
                    "score": round(float(scores[i]), 4),
                    "relevance": _relevance_level(float(scores[i])),
                    "text": index[i]["text"],
                    "doc_name": index[i].get("doc_name", ""),
                    "file_id": index[i].get("file_id", ""),
                })

        return json.dumps({"status": "ok", "count": len(results), "results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {str(e)}"})


# ── 增量索引（供 agent.py 回调）────────────────────

def index_new_history(history_file: str, last_id: str | None, user: str = "") -> str:
    """读取历史文件，找到 last_id 之后的新增内容，切块向量化（按身份分索引）

    Args:
        history_file: workspace 下历史文件名（如 _history_guest.jsonl / _history.jsonl）
        last_id: 上次索引到的消息 id
        user: 身份（空=旧版全局索引）

    Returns:
        新的 last_indexed_id
    """
    hist_path = _WORKSPACE / history_file
    if not hist_path.exists():
        return last_id

    meta = _load_meta(user)
    last_id = last_id or meta.get("last_indexed_id")

    new_chunks = []
    new_last_id = last_id
    found_last = last_id is None  # 如果没有 last_id，全部索引

    with open(hist_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except:
                continue

            msg_id = entry.get("id", "")
            if not found_last:
                if msg_id == last_id:
                    found_last = True
                continue

            # 将用户输入 + AI回复合并切块
            text = f"user: {entry.get('input', '')}\nassistant: {entry.get('output', '')}"
            chunks = chunk_text(text)
            for ch in chunks:
                new_chunks.append({
                    "id": f"chunk-{msg_id}-{len(new_chunks)}",
                    "text": ch,
                    "source": "history",
                    "conversation": entry.get("branch_id", ""),
                    "timestamp": entry.get("timestamp", ""),
                })
            new_last_id = msg_id

    if new_chunks:
        add_to_history_index(new_chunks, user)
        meta["last_indexed_id"] = new_last_id
        _save_meta(meta, user)

    return new_last_id
