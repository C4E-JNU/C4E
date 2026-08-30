// ==================== 文档知识库（纯前端 IndexedDB 版）====================

// ── 词频向量（轻量级嵌入，无需下载模型） ──────────

function _tokenize(text) {
    // 分词：按非字母数字切分，转小写
    return text.toLowerCase().split(/[^a-zA-Z\u4e00-\u9fff0-9]+/).filter(t => t.length > 0);
}

function _computeEmbedding(text) {
    const tokens = _tokenize(text);
    const freq = {};
    for (const t of tokens) {
        freq[t] = (freq[t] || 0) + 1;
    }
    // TF 归一化：除以总词数
    const total = tokens.length || 1;
    const vec = {};
    for (const [word, count] of Object.entries(freq)) {
        vec[word] = count / total;
    }
    return vec;
}

function _cosineSimilarity(vecA, vecB) {
    let dot = 0, normA = 0, normB = 0;
    const allKeys = new Set([...Object.keys(vecA), ...Object.keys(vecB)]);
    for (const k of allKeys) {
        const a = vecA[k] || 0;
        const b = vecB[k] || 0;
        dot += a * b;
        normA += a * a;
        normB += b * b;
    }
    return dot / (Math.sqrt(normA) * Math.sqrt(normB) || 1);
}

function _chunkText(text, maxChars = 500, overlap = 50) {
    const paras = text.split('\n');
    const chunks = [];
    let current = '';
    for (const p of paras) {
        const trimmed = p.trim();
        if (!trimmed) continue;
        if (current.length + trimmed.length + 1 > maxChars && current) {
            chunks.push(current.trim());
            current = current.length > overlap ? current.slice(-overlap) + '\n' + trimmed : trimmed;
        } else {
            current = (current + '\n' + trimmed).trim();
        }
    }
    if (current) chunks.push(current.trim());
    return chunks.length ? chunks : [text.slice(0, maxChars)];
}

// ── IndexedDB 操作 ──────────────────────────

const DB_NAME = 'C4EAI_KB', DB_VER = 1, STORE = 'docs';

function _openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VER);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(STORE)) {
                db.createObjectStore(STORE, { keyPath: 'id' });
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

async function _dbPut(doc) {
    const db = await _openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).put(doc);
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

async function _dbGetAll() {
    const db = await _openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).getAll();
        req.onsuccess = () => { db.close(); resolve(req.result || []); };
        req.onerror = () => { db.close(); reject(req.error); };
    });
}

async function _dbDelete(id) {
    const db = await _openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).delete(id);
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

// ── 初始化 ──────────────────────────────────

function initDatabase() {
    document.getElementById('db-upload-btn').addEventListener('click', () => {
        document.getElementById('db-file-input').click();
    });
    document.getElementById('db-file-input').addEventListener('change', async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        await uploadDocument(file);
        e.target.value = '';
    });
    document.getElementById('cancel-database-btn').addEventListener('click', closeDatabaseModal);
}

function openDatabaseModal() {
    document.getElementById('database-modal').classList.add('active');
    refreshDocumentList();
}

function closeDatabaseModal() {
    document.getElementById('database-modal').classList.remove('active');
}

// ── 上传文档 ────────────────────────────────

async function uploadDocument(file) {
    showToast('正在解析文档...', 'info');
    try {
        const content = await extractTextFromFile(file);
        if (!content || !content.trim()) throw new Error('未能提取到文本内容');

        showToast('🔄 正在向量化...', 'info');
        const chunks = _chunkText(content);
        const embeddings = chunks.map(chunk => _computeEmbedding(chunk));

        // 同时把原文件上传到后端，以便点击文档时能打开源文件
        let fileId = null;
        try {
            const fd = new FormData();
            fd.append('file', file);
            const resp = await fetch('/api/file/upload', { method: 'POST', body: fd });
            const data = await resp.json();
            if (data && data.file_id) fileId = data.file_id;
        } catch (e) {
            console.warn('源文件上传失败:', e.message);
        }

        const doc = {
            id: 'doc-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6),
            name: file.name,
            chunks: chunks,
            embeddings: embeddings,
            file_id: fileId,          // 后端源文件 ID（无则无法打开源文件）
            charCount: content.length,
            chunkCount: chunks.length,
            createdAt: new Date().toISOString()
        };

        await _dbPut(doc);
        state.database.loaded = true;
        showToast(`✅ 「${file.name}」已上传并向量化`, 'success');
        refreshDocumentList();
    } catch (e) {
        showToast('❌ 上传失败: ' + e.message, 'error');
    }
}

// ── 重命名文档 ──────────────────────────────

async function renameDocument(docId, newName) {
    const db = await _openDB();
    try {
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).get(docId);
        req.onsuccess = async () => {
            const doc = req.result;
            if (!doc) { showToast('❌ 文档未找到', 'error'); return; }
            doc.name = newName;
            await _dbPut(doc);
            showToast('✏️ 已重命名', 'success');
            refreshDocumentList();
        };
    } finally {
        db.close();
    }
}

// ── 删除文档 ────────────────────────────────

async function deleteDocument(docId) {
    // 先查 doc，拿到后端 file_id 一并删除
    const all0 = await _dbGetAll();
    const doc0 = all0.find(d => d.id === docId);
    await _dbDelete(docId);
    if (doc0 && doc0.file_id) {
        fetch('/api/file/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: doc0.file_id })
        }).catch(function(e) { console.warn('删除后端源文件失败:', e.message); });
    }
    const all = await _dbGetAll();
    state.database.loaded = all.length > 0;
    showToast('🗑️ 文档已删除', 'info');
    refreshDocumentList();
}

// ── 文档列表 ────────────────────────────────

async function refreshDocumentList() {
    const docs = await _dbGetAll();
    const container = document.getElementById('db-doc-list');
    const badge = document.getElementById('database-status-badge');
    badge.textContent = docs.length + ' 个文档';

    if (docs.length === 0) {
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--gray);font-size:0.9rem;">暂无文档</div>';
        state.database.loaded = false;
        return;
    }
    state.database.loaded = true;

    container.innerHTML = docs.map(d => `
        <div class="db-doc-item" data-id="${d.id}"
            style="display:flex;align-items:center;gap:0.5rem;padding:0.6rem 0.8rem;
                   border:1px solid var(--border);border-radius:8px;margin-bottom:0.4rem;background:var(--card-bg);">
            <span style="font-size:1.1rem;">📄</span>
            <span class="db-doc-name" style="flex:1;font-weight:500;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                title="${d.file_id ? '点击打开源文件' : '点击查看内容'}">${escHtml(d.name)}</span>
            <span style="font-size:0.8rem;color:var(--gray);white-space:nowrap;">${(d.charCount/1000).toFixed(1)}K</span>
            <button class="db-doc-rename-btn" data-id="${d.id}"
                style="background:none;border:none;cursor:pointer;color:var(--primary);font-size:0.9rem;padding:0.2rem 0.4rem;" title="重命名">✏️</button>
            <button class="db-doc-delete-btn" data-id="${d.id}"
                style="background:none;border:none;cursor:pointer;color:var(--danger);font-size:1.1rem;padding:0.2rem;" title="删除">🗑️</button>
        </div>
    `).join('');

    container.querySelectorAll('.db-doc-name').forEach(el => {
        el.addEventListener('click', async () => {
            const id = el.closest('.db-doc-item').dataset.id;
            const docs2 = await _dbGetAll();
            const doc = docs2.find(d => d.id === id);
            if (!doc) return;
            if (doc.file_id) {
                // 打开后端源文件（与消息附件一致）
                if (state.settings.fileOpenMode === 'backend') {
                    fetch('/api/file/' + doc.file_id + '/open', { method: 'POST' }).catch(function(){});
                } else {
                    window.open('/api/file/' + doc.file_id, '_blank');
                }
            } else {
                // 无源文件（旧文档/后端丢失）→ 提示无法打开
                showPassiveToast('后端文件丢失，浏览器前端无法打开该文档源文件');
            }
        });
    });

    container.querySelectorAll('.db-doc-rename-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const id = btn.dataset.id;
            const docs2 = await _dbGetAll();
            const doc = docs2.find(d => d.id === id);
            if (!doc) return;
            const name = prompt('重命名：', doc.name);
            if (name && name.trim() && name.trim() !== doc.name) {
                await renameDocument(id, name.trim());
            }
        });
    });

    container.querySelectorAll('.db-doc-delete-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (confirm('确定删除？')) {
                await deleteDocument(btn.dataset.id);
            }
        });
    });
}

// ── 向量搜索（纯向量，正则已移除） ────────────
async function searchDocuments(query) {
    const docs = await _dbGetAll();
    if (!docs.length) return { context: '', results: [], total: 0 };

    // 向量搜索
    try {
        const qVec = _computeEmbedding(query);
        const scored = [];
        for (const doc of docs) {
            for (let i = 0; i < doc.chunks.length; i++) {
                const sim = _cosineSimilarity(qVec, doc.embeddings[i]);
                scored.push({ score: sim, doc, idx: i });
            }
        }
        scored.sort((a, b) => b.score - a.score);
        const top = scored.slice(0, 5);

        const seen = new Set();
        const results = [];
        for (const r of top) {
            if (seen.has(r.doc.id)) continue;
            seen.add(r.doc.id);
            const start = Math.max(0, r.idx - 1);
            const end = Math.min(r.doc.chunks.length, r.idx + 2);
            results.push({
                doc_name: r.doc.name,
                chunk: r.doc.chunks[r.idx],
                context: r.doc.chunks.slice(start, end).join('\n'),
                score: r.score
            });
        }

        const context = results.map(r => `[${r.doc_name} (${(r.score*100).toFixed(0)}%)]\n${r.context}`).join('\n\n');
        return { context, results, total: results.length };
    } catch (e) {
        showToast('❌ 向量搜索失败: ' + e.message, 'error');
        return { context: '', results: [], total: 0 };
    }
}

// ── 导出搜索函数给智能体使用 ────────────────────
// toggleDatabaseSearch 和 updateDatabaseUI 已移除，
// 文档上传/搜索功能保留供智能体的 document_search 工具使用

function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}
