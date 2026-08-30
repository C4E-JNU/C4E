"""
向量搜索引擎 — bge-small-zh-v1.5 (fastembed)
对话历史 + 知识库的增量索引与语义搜索
"""
import json, os, time, numpy as np
from pathlib import Path

_WORKSPACE = Path(__file__).parent.parent.parent / "workspace"
_HISTORY_INDEX_FILE = _WORKSPACE / "_vector_history.json"
_KNOWLEDGE_INDEX_FILE = _WORKSPACE / "_vector_knowledge.json"
_META_FILE = _WORKSPACE / "_vector_meta.json"

# ── 全局单例 ───────────────────────────────────────
_model = None

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


def _load_meta() -> dict:
    """加载索引元数据（last_indexed_id 等）"""
    if not _META_FILE.exists():
        return {"last_indexed_id": None, "history_chunks": 0, "knowledge_chunks": 0}
    try:
        with open(_META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"last_indexed_id": None, "history_chunks": 0, "knowledge_chunks": 0}


def _save_meta(meta: dict):
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    with open(_META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ── 公开 API ──────────────────────────────────────

def add_to_history_index(chunks: list[dict]):
    """追加新的历史对话块到索引"""
    index = _load_index(_HISTORY_INDEX_FILE)
    index.extend(chunks)
    _save_index(_HISTORY_INDEX_FILE, index)
    meta = _load_meta()
    meta["history_chunks"] = len(index)
    _save_meta(meta)


def add_to_knowledge_index(chunks: list[dict]):
    """追加新的知识库块到索引"""
    index = _load_index(_KNOWLEDGE_INDEX_FILE)
    index.extend(chunks)
    _save_index(_KNOWLEDGE_INDEX_FILE, index)
    meta = _load_meta()
    meta["knowledge_chunks"] = len(index)
    _save_meta(meta)


async def search_history(query: str, top_k: int = 3, scope: str = None) -> str:
    """搜索历史对话，返回 JSON 字符串"""
    index = _load_index(_HISTORY_INDEX_FILE)
    if not index:
        return json.dumps({"status": "ok", "count": 0, "results": []})

    texts = [c["text"] for c in index]
    try:
        q_vec = embed_texts([query])[0]
        doc_vecs = embed_texts(texts)
        scores = cosine_similarity(q_vec, doc_vecs)
        top_idx = np.argsort(scores)[-top_k:][::-1]

        results = []
        for i in top_idx:
            if scores[i] > 0.3:  # 阈值过滤
                results.append({
                    "score": round(float(scores[i]), 4),
                    "text": index[i]["text"][:300],
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

    texts = [c["text"] for c in index]
    try:
        q_vec = embed_texts([query])[0]
        doc_vecs = embed_texts(texts)
        scores = cosine_similarity(q_vec, doc_vecs)
        top_idx = np.argsort(scores)[-top_k:][::-1]

        results = []
        for i in top_idx:
            if scores[i] > 0.3:
                results.append({
                    "score": round(float(scores[i]), 4),
                    "text": index[i]["text"][:300],
                    "doc_name": index[i].get("doc_name", ""),
                })

        return json.dumps({"status": "ok", "count": len(results), "results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {str(e)}"})


# ── 增量索引（供 agent.py 回调）────────────────────

def index_new_history(history_file: str, last_id: str | None) -> str:
    """读取历史文件，找到 last_id 之后的新增内容，切块向量化

    Args:
        history_file: workspace/_history.jsonl 路径
        last_id: 上次索引到的消息 id

    Returns:
        新的 last_indexed_id
    """
    hist_path = _WORKSPACE / history_file
    if not hist_path.exists():
        return last_id

    meta = _load_meta()
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
        add_to_history_index(new_chunks)
        meta["last_indexed_id"] = new_last_id
        _save_meta(meta)

    return new_last_id
