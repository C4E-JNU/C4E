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

// IndexedDB 按登录身份分库（identityKey）：切换账号=各自的文档库
const DB_NAME = 'C4EAI_KB_' + ((window.C4EAI_IDENTITY || 'guest').replace(/[^A-Za-z0-9_-]/g, '_') || 'guest');
const DB_VER = 1, STORE = 'docs';

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

// 旧版单一库 C4EAI_KB → 当前身份库（仅 guest 首次启动时迁移一次）
async function _migrateLegacyDBIfNeeded() {
    if (window.C4EAI_IDENTITY !== 'guest') return;
    try {
        const dbs = await indexedDB.databases ? indexedDB.databases() : [];
        const hasLegacy = (dbs || []).some(function (d) { return d.name === 'C4EAI_KB'; });
        if (!hasLegacy) return;
        const marked = localStorage.getItem('c4eai_idb_migrated_guest');
        if (marked) return;
        const oldDb = await new Promise(function (res, rej) {
            const r = indexedDB.open('C4EAI_KB', 1);
            r.onsuccess = function () { res(r.result); };
            r.onerror = function () { rej(r.error); };
        });
        const oldDocs = await new Promise(function (res, rej) {
            try {
                const tx = oldDb.transaction('docs', 'readonly');
                const q = tx.objectStore('docs').getAll();
                q.onsuccess = function () { res(q.result || []); };
                q.onerror = function () { rej(q.error); };
            } catch (e) { res([]); }
        });
        oldDb.close();
        for (const d of oldDocs) {
            try { await _dbPut(d); } catch (e) {}
        }
        localStorage.setItem('c4eai_idb_migrated_guest', '1');
        if (oldDocs.length) showToast(`已将 ${oldDocs.length} 个旧文档归入游客文档库`, 'info');
    } catch (e) { /* 迁移失败不阻塞 */ }
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
    _migrateLegacyDBIfNeeded().finally(function () { refreshDocumentList(); });
    document.getElementById('db-close-btn').addEventListener('click', closeDatabaseModal);
    document.getElementById('db-upload-btn').addEventListener('click', () => {
        document.getElementById('db-file-input').click();
    });

    // ＋ 新建笔记（空笔记，浮动编辑器，可贴图/插附件/表格）
    var addEntryBtn = document.getElementById('db-add-entry-btn');
    if (addEntryBtn) addEntryBtn.addEventListener('click', dbAddEntry);

    // 筛选按钮：下拉切换「笔记 / 文档」——用户 2026-09-22 定
    // （原 Tab 条占一整行、且未登录时两栏内容相同，看着像"点了没反应"，故改为单按钮）
    var _fbtn = document.getElementById('db-filter-btn');
    var _fmenu = document.getElementById('db-filter-menu');
    if (_fbtn && _fmenu) {
        _fbtn.addEventListener('click', function (e) {
            e.stopPropagation();
            var open = !_fmenu.hidden;
            _fmenu.hidden = open;
            _fbtn.setAttribute('aria-expanded', open ? 'false' : 'true');
        });
        _fmenu.querySelectorAll('.db-filter-item').forEach(function (b) {
            b.addEventListener('click', function (e) {
                e.stopPropagation();
                _fmenu.hidden = true;
                _fbtn.setAttribute('aria-expanded', 'false');
                _switchDbKind(b.dataset.kind || 'note');
            });
        });
        // 点别处收起
        document.addEventListener('click', function () {
            if (!_fmenu.hidden) {
                _fmenu.hidden = true;
                _fbtn.setAttribute('aria-expanded', 'false');
            }
        });
    }
    document.getElementById('db-file-input').addEventListener('change', async (e) => {
        // 支持多选：逐个上传（每个文件各自建立一个条目）
        const files = Array.from(e.target.files || []);
        e.target.value = ''; // 允许重复选择同一文件
        if (!files.length) return;
        for (const f of files) {
            try {
                await uploadDocument(f);
            } catch (err) {
                console.warn('数据库上传失败:', f.name, err);
                if (typeof showToast === 'function') showToast('上传失败：' + f.name, 'error');
            }
        }
        if (files.length > 1 && typeof showToast === 'function') {
            showToast('已上传 ' + files.length + ' 个文件', 'success');
        }
    });
}

function openDatabaseModal() {
    var p = document.getElementById('database-panel');
    if (!p) return;
    // 用浮窗控制器打开（应用记忆的尺寸/位置）；控制器未就绪时退化为基础显示
    if (window.__dbPanelFloat && window.__dbPanelFloat.open) {
        window.__dbPanelFloat.open();
    } else {
        p.classList.add('active');
    }
    refreshDocumentList();
}

function closeDatabaseModal() {
    var p = document.getElementById('database-panel');
    if (p) p.classList.remove('active');
}

// 数据库浮动面板：拖动 + 四角缩放 + 尺寸记忆（与笔记面板/笔记编辑浮窗同款）
(function _initDbPanelFloat() {
    var MIN_W = 320, MIN_H = 280;              // 与 .notes-panel 的 CSS min-width/min-height 配套
    var KEY = 'c4eai-db-panel-rect';           // localStorage: {x,y,w,h}
    var VER = 1;                               // 几何版本，样式变更时 +1 使旧记忆失效

    function el() { return document.getElementById('database-panel'); }

    function saveRect() {
        var p = el(); if (!p) return;
        try {
            localStorage.setItem(KEY, JSON.stringify({
                v: VER,
                x: parseInt(p.style.left, 10) || 0,
                y: parseInt(p.style.top, 10) || 0,
                w: p.offsetWidth,
                h: p.offsetHeight
            }));
        } catch (e) {}
    }

    function applyRect(r) {
        var p = el(); if (!p || !r) return;
        var vw = window.innerWidth, vh = window.innerHeight, pad = 8;
        var w = Math.min(Math.max(r.w, MIN_W), vw - 2 * pad);
        var h = Math.min(Math.max(r.h, MIN_H), vh - 2 * pad);
        var x = Math.min(Math.max(r.x, pad), Math.max(pad, vw - w - pad));
        var y = Math.min(Math.max(r.y, pad), Math.max(pad, vh - h - pad));
        p.style.left = x + 'px'; p.style.top = y + 'px';
        p.style.width = w + 'px'; p.style.height = h + 'px';
        p.style.right = 'auto';
    }

    function resolveRect() {
        var vw = window.innerWidth, vh = window.innerHeight;
        var saved = null;
        try { saved = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) {}
        if (saved && saved.w && saved.h && saved.v === VER) return saved;
        // 首次：沿用 index.html 里的初始位置（右上），尺寸取 CSS 默认
        var p = el();
        var w = (p && p.offsetWidth) ? p.offsetWidth : Math.min(520, vw - 24);
        var h = (p && p.offsetHeight) ? p.offsetHeight : Math.min(600, vh - 24);
        var r = p ? p.getBoundingClientRect() : null;
        return { x: r ? r.left : Math.max(8, vw - w - 40), y: r ? r.top : 140, w: w, h: h };
    }

    function bind() {
        var panel = el();
        var hd = document.getElementById('db-panel-header');
        if (!panel) return;

        // ① 拖动（头部按下；点按钮不触发）
        if (hd && !hd.dataset.dragBound) {
            hd.dataset.dragBound = '1';
            hd.addEventListener('mousedown', function (e) {
                if (e.button !== 0) return;
                if (e.target.closest('button')) return;
                var rect = panel.getBoundingClientRect();
                var offX = e.clientX - rect.left, offY = e.clientY - rect.top;
                function mv(ev) {
                    panel.style.left = Math.max(0, Math.min(window.innerWidth - 80, ev.clientX - offX)) + 'px';
                    panel.style.top = Math.max(0, Math.min(window.innerHeight - 60, ev.clientY - offY)) + 'px';
                    panel.style.right = 'auto';
                }
                function up() {
                    document.removeEventListener('mousemove', mv);
                    document.removeEventListener('mouseup', up);
                    saveRect();
                }
                document.addEventListener('mousemove', mv);
                document.addEventListener('mouseup', up);
                e.preventDefault();
            });
        }

        // ② 四角缩放（热区透明，无角标 UI）
        if (!panel.querySelector('.notes-resize')) {
            ['se', 'nw', 'ne', 'sw'].forEach(function (c) {
                var d = document.createElement('div');
                d.className = 'notes-resize ' + c;
                d.dataset.corner = c;
                panel.appendChild(d);
                d.addEventListener('mousedown', function (e) {
                    if (e.button !== 0) return;
                    e.preventDefault(); e.stopPropagation();
                    var sx = e.clientX, sy = e.clientY;
                    var r0 = panel.getBoundingClientRect();
                    var sW = r0.width, sH = r0.height, sL = r0.left, sT = r0.top;
                    function mv(ev) {
                        var dx = ev.clientX - sx, dy = ev.clientY - sy;
                        var nW = sW, nH = sH, nL = sL, nT = sT;
                        if (c.indexOf('e') >= 0) nW = sW + dx;
                        if (c.indexOf('s') >= 0) nH = sH + dy;
                        if (c.indexOf('w') >= 0) { nW = sW - dx; nL = sL + dx; }
                        if (c.indexOf('n') >= 0) { nH = sH - dy; nT = sT + dy; }
                        if (nW < MIN_W) { if (c.indexOf('w') >= 0) nL = sL + (sW - MIN_W); nW = MIN_W; }
                        if (nH < MIN_H) { if (c.indexOf('n') >= 0) nT = sT + (sH - MIN_H); nH = MIN_H; }
                        if (c.indexOf('w') >= 0 && nL < 0) { nW += nL; nL = 0; }
                        if (c.indexOf('n') >= 0 && nT < 0) { nH += nT; nT = 0; }
                        panel.style.width = nW + 'px'; panel.style.height = nH + 'px';
                        panel.style.left = nL + 'px'; panel.style.top = nT + 'px';
                        panel.style.right = 'auto';
                    }
                    function up() {
                        document.removeEventListener('mousemove', mv);
                        document.removeEventListener('mouseup', up);
                        document.body.style.userSelect = '';
                        saveRect();
                    }
                    document.body.style.userSelect = 'none';
                    document.addEventListener('mousemove', mv);
                    document.addEventListener('mouseup', up);
                });
            });
        }

        // ③ 窗口尺寸变化时重新夹紧
        if (!window.__dbPanelResizeBound) {
            window.__dbPanelResizeBound = true;
            window.addEventListener('resize', function () {
                var p = el(); if (!p || !p.classList.contains('active')) return;
                applyRect(resolveRect());
            });
        }
    }

    function open() {
        var p = el(); if (!p) return;
        p.classList.add('active');
        applyRect(resolveRect());
    }

    window.__dbPanelFloat = { open: open, applyRect: applyRect, saveRect: saveRect };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else { bind(); }
})();

// ── 上传文档 → 共享数据库池（云端唯一数据库）────────────────
// 游客/未授权：前端拦截并提示；已授权：source=database 上传（本地实例由后端代理到云端），不写本地 IndexedDB。

async function uploadDocument(file) {
    var me = (typeof window.getAuthUser === 'function') ? window.getAuthUser() : null;
    if (!me || me.username === 'guest') {
        showToast('🔐 数据库在共享服务器：请先点击右上角「登录」', 'info');
        if (typeof window.openLoginModal === 'function') window.openLoginModal();
        return;
    }
    if (!me.approved && me.role !== 'admin') {
        showToast('⏳ 已注册，等待管理员开通数据库权限后即可上传', 'info');
        return;
    }
    showToast('正在上传到共享数据库...', 'info');
    try {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch('/api/file/upload?source=database', { method: 'POST', body: fd });
        const data = await resp.json();
        if (!resp.ok) {
            showToast('❌ 上传失败: ' + (data.error || resp.status), 'error');
            return;
        }
        showToast(`✅ 「${file.name}」已入共享数据库，全组可检索`, 'success');
        // 上传建的是「文档条目」→ 自动切到文档 Tab，否则用户在笔记 Tab 看不到结果
        if (_dbKind !== 'document' && typeof _switchDbKind === 'function') _switchDbKind('document');
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

// ── 数据库条目列表（笔记形态：每条 = 标题 + markdown 正文 + 附件/图片内嵌）────────────────
// 后端存储复用笔记体系：branch_id = '__db__'；附件/图片走 {{file:file_id:文件名}} / ![图](url) 占位，
// 渲染用 formatMessage，与聊天附件、笔记完全一致。

var DB_BRANCH = '__db__';
var _dbEntryCollapsed = {};
var _dbEntries = [];   // 最近一次条目缓存（供编辑回显）
// 当前展示的条目类型：note=笔记（默认，人写）/ document=文档（机器解析，供检索）
// 用户 2026-09-22 定：两者数据结构完全一致，只是分类不同，用于 Tab 切换展示。
var _dbKind = 'note';

function _dbEntryBodyHtml(n) {
    return n.content
        ? formatMessage(n.content, true)
        : '<span style="color:var(--gray);font-style:italic;">空条目</span>';
}

// 给容器内渲染出的附件卡片（.msg-file-card）追加 × 删除按钮。
// getNote(noteId) → 条目对象（需含 content/owner）；onChanged() → 删除成功后回调（重新渲染）。
// 仅当当前用户是条目 owner 或 admin 时加 ×。
function _enhanceFileCardDelete(container, getNote, onChanged) {
    var me = (typeof window.getAuthUser === 'function') ? window.getAuthUser() : null;
    if (!me) return;
    // 仅 admin 可改他人内容（用户选 B：金色 L5 也不能）—— 用「当前登录名」而非 role
    // （历史账号 xhq1/xhq2 的 role 仍写着 admin，按 role 判会误放行）
    var isAdmin = (me.username === 'admin');
    container.querySelectorAll('.msg-file-card, .attachment-item[data-file-id]').forEach(function (card) {
        var noteEl = card.closest('[data-eid],[data-note-id]');
        if (!noteEl) return;
        var noteId = noteEl.dataset.eid || noteEl.dataset.noteId;
        var note = getNote(noteId);
        if (!note) return;
        // 笔记卡片里 note.id 是字段；条目卡片同样
        var owner = note.owner || 'admin';
        if (owner !== me.username && !isAdmin) return;
        if (card.querySelector('.file-card-del')) return;
        var btn = document.createElement('button');
        btn.className = 'file-card-del';
        btn.textContent = '×';
        btn.title = '从条目中移除该附件（不删文件）';
        btn.style.cssText = 'margin-left:4px;border:none;background:none;color:var(--danger);cursor:pointer;font-size:.95rem;line-height:1;padding:0 2px;';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var fid = card.dataset.fileId;
            if (!fid) return;
            if (!confirm('从条目中移除该附件占位？（文件本身保留）')) return;
            var re = new RegExp('\\n?\\{\\{file:' + fid + ':[^}]*\\}\\}\\n?', 'g');
            var newContent = (note.content || '').replace(re, '\n');
            fetch('/api/notes', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note_id: note.id, title: note.title, content: newContent })
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (d.status === 'ok') {
                    note.content = newContent;
                    if (typeof showPassiveToast === 'function') showPassiveToast('已移除附件占位');
                    if (onChanged) onChanged();
                } else {
                    if (typeof showPassiveToast === 'function') showPassiveToast('❌ 更新失败', 'error');
                }
            }).catch(function () {
                if (typeof showPassiveToast === 'function') showPassiveToast('❌ 更新失败', 'error');
            });
        });
        card.appendChild(btn);
    });
}
window._enhanceFileCardDelete = _enhanceFileCardDelete;

// 切换条目分类（笔记/文档）：更新按钮文案 + 菜单态 + 重新拉取
function _switchDbKind(kind) {
    kind = kind || 'note';
    if (kind === _dbKind) return;
    _dbKind = kind;
    var label = document.getElementById('db-filter-label');
    if (label) label.textContent = (kind === 'document') ? '📄 文档' : '📝 笔记';
    var menu = document.getElementById('db-filter-menu');
    if (menu) {
        menu.querySelectorAll('.db-filter-item').forEach(function (b) {
            b.classList.toggle('active', (b.dataset.kind || 'note') === kind);
        });
    }
    refreshDocumentList();
}

async function refreshDocumentList() {
    const container = document.getElementById('db-doc-list');
    const badge = document.getElementById('database-status-badge');
    var me = (typeof window.getAuthUser === 'function') ? window.getAuthUser() : null;
    if (!me || me.username === 'guest') {
        badge.textContent = '未登录';
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--gray);">🔐 数据库在共享服务器：请先点击右上角「登录」</div>';
        return;
    }
    container.innerHTML = '<div style="text-align:center;padding:1.5rem;color:var(--gray);">加载中…</div>';
    const _kind = _dbKind;
    try {
        const r = await fetch('/api/notes?branch_id=' + encodeURIComponent(DB_BRANCH) + '&kind=' + _kind);
        const d = await r.json();
        const notes = d.notes || [];
        _dbEntries = notes;
        badge.textContent = notes.length + ' 个' + (_kind === 'document' ? '文档' : '笔记');
        if (!notes.length) {
            container.innerHTML = _kind === 'document'
                ? '<div style="text-align:center;padding:2rem;color:var(--gray);font-size:0.9rem;">暂无文档条目<br><span style="font-size:.78rem;">点「📤 上传」自动解析文件并建条目</span></div>'
                : '<div style="text-align:center;padding:2rem;color:var(--gray);font-size:0.9rem;">暂无笔记<br><span style="font-size:.78rem;">点「＋ 笔记」手写，或从个人笔记「上传到数据库」</span></div>';
            return;
        }
        var myName = me.username, isAdmin = (me.username === 'admin');
        // 按 owner 分组：每组一个 "👤 用户名" 小标题（本人排最前）
        var groups = {};
        var order = [];
        notes.forEach(function (n) {
            var o = n.owner || 'admin';
            if (!groups[o]) { groups[o] = []; order.push(o); }
            groups[o].push(n);
        });
        order.sort(function (a, b) {
            if (a === myName) return -1;
            if (b === myName) return 1;
            return a.localeCompare(b);
        });
        container.innerHTML = order.map(function (o) {
            var sectionHead = '<div style="padding:.45rem .2rem .25rem;font-size:.78rem;color:var(--gray,#999);font-weight:600;">👤 '
                + escHtml(o) + (o === myName ? '（我）' : '') + '</div>';
            return sectionHead + groups[o].map(function (n) {
                var owner = n.owner || 'admin';
                var canEdit = (owner === myName) || isAdmin;
                var collapsed = _dbEntryCollapsed[n.id] !== undefined ? _dbEntryCollapsed[n.id] : true;
                var refBtn = '<button class="db-entry-ref" data-id="' + n.id + '" title="引用给智能体（发送时带上该条目全文，可让它增删改查）" style="background:none;border:none;cursor:pointer;font-size:.9rem;padding:.1rem .3rem;">📌</button>';
                var actions = refBtn + (canEdit
                    ? ('<button class="db-entry-edit" data-id="' + n.id + '" title="编辑条目" style="background:none;border:none;cursor:pointer;font-size:.95rem;padding:.1rem .3rem;">✏️</button>'
                    +  '<button class="db-entry-del" data-id="' + n.id + '" title="删除条目" style="background:none;border:none;cursor:pointer;color:var(--danger);font-size:1.05rem;padding:.1rem .3rem;">×</button>')
                    : '');
                return '<div class="note-card" data-eid="' + n.id + '" data-owner="' + escHtml(owner) + '">'
                    + '<div class="note-card-header db-entry-toggle" data-id="' + n.id + '" style="cursor:pointer;">'
                    + '<span class="note-card-title">' + escHtml(n.title || '未命名条目') + '</span>'
                    + '<div class="note-card-header-actions" style="display:flex;gap:.15rem;" onclick="event.stopPropagation();">' + actions + '</div>'
                    + '</div>'
                    + '<div class="note-card-body" id="db-entry-body-' + n.id + '"' + (collapsed ? ' style="display:none;"' : '') + '>'
                    + '<div class="message-content">' + _dbEntryBodyHtml(n) + '</div>'
                    + '</div></div>';
            }).join('');
        }).join('');

        // 附件卡片 × 删除（编辑权限内）：从条目正文移除对应 {{file:..}} 占位
        _enhanceFileCardDelete(container, function (noteId) {
            return _dbEntries.find(function (x) { return x.id === noteId; });
        }, function () { refreshDocumentList(); });

        container.querySelectorAll('.db-entry-toggle').forEach(function (el) {
            el.addEventListener('click', function () {
                var id = el.dataset.id;
                var bodyEl = document.getElementById('db-entry-body-' + id);
                if (bodyEl) {
                    var hide = bodyEl.style.display !== 'none';
                    bodyEl.style.display = hide ? 'none' : '';
                    _dbEntryCollapsed[id] = hide;
                }
            });
        });
        container.querySelectorAll('.db-entry-edit').forEach(function (btn) {
            btn.addEventListener('click', function () { dbEditEntry(btn.dataset.id); });
        });
        container.querySelectorAll('.db-entry-del').forEach(function (btn) {
            btn.addEventListener('click', function () { dbDeleteEntry(btn.dataset.id); });
        });
        // 引用给智能体（任何可见条目都能引用，用于让智能体读取/修改）
        container.querySelectorAll('.db-entry-ref').forEach(function (btn) {
            btn.addEventListener('click', function () { if (window.refDbEntry) window.refDbEntry(btn.dataset.id); });
        });

        // 右键菜单：把条目移到分组（用户 2026-09-25 定：方案 B —— 右键「移动到…」）
        container.querySelectorAll('.note-card[data-eid]').forEach(function (card) {
            card.addEventListener('contextmenu', function (ev) {
                ev.preventDefault();
                var owner = card.dataset.owner || '';
                if (!((owner === myName) || isAdmin)) { showToast('只能移动自己的条目'); return; }
                _showMoveMenu(ev.clientX, ev.clientY, card.dataset.eid);
            });
        });
    } catch (e) {
        console.error('加载数据库条目失败:', e);
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--error);">加载失败</div>';
    }
}

function dbToggleCollapse(id) { /* 由 header 点击委托处理 */ }

function dbEditEntry(id) {
    var n = _dbEntries.find(function (x) { return x.id === id; });
    if (!n) return;
    if (typeof window.floatingEditor === 'undefined') { showToast('编辑器未就绪', 'error'); return; }
    window.floatingEditor.open({
        title: '编辑数据库条目',
        showTitle: true,
        titleValue: n.title || '',
        value: n.content || '',
        onSave: function (newContent, newTitle) {
            var t = (newTitle !== null && newTitle !== undefined && newTitle !== '') ? newTitle : '未命名条目';
            fetch('/api/notes', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note_id: id, title: t, content: newContent, branch_id: DB_BRANCH })
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (d.status === 'ok') { showToast('条目已更新', 'success'); refreshDocumentList(); }
                else showToast('更新失败', 'error');
            }).catch(function () { showToast('更新失败', 'error'); });
        }
    });
}

function dbAddEntry() {
    var me = (typeof window.getAuthUser === 'function') ? window.getAuthUser() : null;
    if (!me || me.username === 'guest') { showToast('请先登录', 'info'); return; }
    if (typeof window.floatingEditor === 'undefined') { showToast('编辑器未就绪', 'error'); return; }
    window.floatingEditor.open({
        title: '新建数据库条目',
        showTitle: true,
        titleValue: '',
        value: '',
        onSave: function (newContent, newTitle) {
            var t = (newTitle !== null && newTitle !== undefined && newTitle !== '') ? newTitle : '未命名条目';
            fetch('/api/notes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: t, content: newContent, branch_id: DB_BRANCH, kind: 'note' })
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (d.status === 'ok') { showToast('条目已创建', 'success'); refreshDocumentList(); }
                else showToast('创建失败', 'error');
            }).catch(function () { showToast('创建失败', 'error'); });
        }
    });
}

function dbDeleteEntry(id) {
    if (!confirm('确定删除这条条目吗？（附件文件本身保留）')) return;
    // 数据库条目只在云端 → 显式带来源标识，后端直连云端删除（不再本地扫描）
    fetch('/api/notes/' + encodeURIComponent(id) + '?source=cloud', { method: 'DELETE' })
        .then(function (r) { return r.json(); }).then(function (d) {
            if (d.status === 'ok') { showToast('条目已删除', 'success'); refreshDocumentList(); }
            else { showToast('删除失败：' + (d.error || d.message || d.detail || '未知原因'), 'error'); }
        }).catch(function (e) { showToast('删除失败：' + (e && e.message ? e.message : '网络错误'), 'error');
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
// 文档上传/搜索功能保留供智能体的 shared_search（云端）工具使用

function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

// ═══════════════════════════════════════════════
// 分组：右键菜单移动条目（用户 2026-09-25 定：方案 B）
// 方案 C（对话让智能体操作）由 shared_search 工具的
// create_group/move_group/move_entry 等 action 承担，不在此处。
// ═══════════════════════════════════════════════

var _moveMenuEl = null;

function _closeMoveMenu() {
    if (_moveMenuEl && _moveMenuEl.parentNode) _moveMenuEl.parentNode.removeChild(_moveMenuEl);
    _moveMenuEl = null;
}

async function _fetchGroups() {
    const resp = await fetch('/api/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'list_groups' })
    });
    const d = await resp.json();
    return d.folders || [];
}

/** 在鼠标位置弹出「移动到…」菜单：列出全部分组（缩进显示层级）+「移出分组」 */
async function _showMoveMenu(x, y, entryId) {
    _closeMoveMenu();
    let folders = [];
    try {
        folders = await _fetchGroups();
    } catch (e) {
        showToast('读取分组失败：' + e.message, 'error');
        return;
    }

    const menu = document.createElement('div');
    menu.className = 'db-move-menu';
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';

    const title = document.createElement('div');
    title.className = 'db-move-menu-title';
    title.textContent = '移动到分组';
    menu.appendChild(title);

    // 「移出分组」（回到未分组）
    const rootItem = document.createElement('div');
    rootItem.className = 'db-move-menu-item';
    rootItem.textContent = '（未分组）';
    rootItem.addEventListener('click', function () { _doMoveEntry(entryId, ''); });
    menu.appendChild(rootItem);

    if (!folders.length) {
        const empty = document.createElement('div');
        empty.className = 'db-move-menu-item';
        empty.style.color = 'var(--gray, #999)';
        empty.style.cursor = 'default';
        empty.textContent = '（还没有分组，可在对话里让智能体创建）';
        menu.appendChild(empty);
    } else {
        folders.forEach(function (f) {
            const it = document.createElement('div');
            it.className = 'db-move-menu-item';
            it.style.paddingLeft = (12 + (f.depth || 0) * 14) + 'px';
            it.textContent = '📁 ' + f.name;
            if (f.intro) it.title = f.intro;
            it.addEventListener('click', function () { _doMoveEntry(entryId, f.id); });
            menu.appendChild(it);
        });
    }

    document.body.appendChild(menu);
    // 防溢出屏幕
    const r = menu.getBoundingClientRect();
    if (r.right > window.innerWidth - 8) menu.style.left = (window.innerWidth - r.width - 8) + 'px';
    if (r.bottom > window.innerHeight - 8) menu.style.top = (y - r.height) + 'px';
    _moveMenuEl = menu;

    // 点别处关闭（一次性监听）
    setTimeout(function () {
        document.addEventListener('click', _closeMoveMenu, { once: true });
        document.addEventListener('contextmenu', _closeMoveMenu, { once: true });
    }, 0);
}

async function _doMoveEntry(entryId, groupId) {
    _closeMoveMenu();
    try {
        const resp = await fetch('/api/notes', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ note_id: entryId, branch_id: '__db__', folder_id: groupId })
        });
        const d = await resp.json();
        if (!resp.ok || d.error) { showToast('移动失败：' + (d.error || resp.status), 'error'); return; }
        showToast(groupId ? '✅ 已移动到分组' : '✅ 已移出分组', 'success');
        refreshDocumentList();
    } catch (e) {
        showToast('移动失败：' + e.message, 'error');
    }
}

window._showMoveMenu = _showMoveMenu;
