// ==================== 文档知识库（纯前端 IndexedDB 版）====================

// ── 词频向量（轻量级嵌入，无需下载模型） ──────────

function _tokenize(text) {
    // 分词：按非字母数字切分，转小写
    return text.toLowerCase().split(/[^a-zA-Z\u4e00-\u9fff0-9]+/).filter(t => t.length > 0);
}

// ── 初始化 ──────────────────────────────────

function initDatabase() {
    refreshDocumentList();
    document.getElementById('db-close-btn').addEventListener('click', closeDatabaseModal);
    // 上传/添加按钮已下沉到每个文件夹行（用户 2026-09-25 定）；
    // 这里保留全局隐藏的 file input 与默认上传目标
    var _upBtn = document.getElementById('db-upload-btn');
    if (_upBtn) _upBtn.addEventListener('click', function () {
        _dbUploadFolder = '';
        document.getElementById('db-file-input').click();
    });
    var addEntryBtn = document.getElementById('db-add-entry-btn');
    if (addEntryBtn) addEntryBtn.addEventListener('click', function () { dbAddEntry(''); });

    // 筛选/排序按钮（外置在上传旁边）—— 用户 2026-09-25 定
    // 三者都是「点开下拉 → 选中 → 作用到整棵树」
    function _bindFilter(btnId, menuId) {
        var b = document.getElementById(btnId), m = document.getElementById(menuId);
        if (!b || !m) return;
        b.addEventListener('click', function (e) {
            e.stopPropagation();
            var wasHidden = m.hidden;
            // 先关掉其它菜单
            ['db-f-owner-menu', 'db-f-year-menu', 'db-f-sort-menu'].forEach(function (id) {
                var x = document.getElementById(id);
                if (x && x !== m) x.hidden = true;
            });
            m.hidden = !wasHidden;
        });
        m.addEventListener('click', function (e) { e.stopPropagation(); });
        document.addEventListener('click', function () { m.hidden = true; });
    }
    _bindFilter('db-f-owner-btn', 'db-f-owner-menu');
    _bindFilter('db-f-year-btn', 'db-f-year-menu');
    _bindFilter('db-f-sort-btn', 'db-f-sort-menu');

    // 排序菜单：静态项，绑定一次
    var _sm = document.getElementById('db-f-sort-menu');
    if (_sm) {
        _sm.querySelectorAll('.db-filter-item').forEach(function (b) {
            b.addEventListener('click', function (e) {
                e.stopPropagation();
                var s = b.dataset.sort || 'time';
                _dbTree.sort = s;
                _sm.querySelectorAll('.db-filter-item').forEach(function (x) {
                    x.classList.toggle('active', x === b);
                });
                document.getElementById('db-f-sort-label').textContent =
                    s === 'title' ? '🔤 名称' : (s === 'time_asc' ? '🕒 最早' : '🕒 最新');
                _sm.hidden = true;
                refreshDocumentList();
            });
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
    // 直接进入条目树（分组管理已合并进树，无独立面板）
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
        const _fid = (_dbUploadFolder || '');
        const _fname = _fid ? ((_dbTree.folders.find(function (x) { return x.id === _fid; }) || {}).name || '') : '';
        const _u = '/api/file/upload?source=database' + (_fid ? ('&folder_id=' + encodeURIComponent(_fid)) : '');
        const resp = await fetch(_u, { method: 'POST', body: fd });
        const data = await resp.json();
        if (!resp.ok) {
            showToast('❌ 上传失败: ' + (data.error || resp.status), 'error');
            return;
        }
        showToast(`✅ 「${file.name}」已存入${_fname ? '「' + _fname + '」' : '共享库'}，全组可检索`, 'success');
        if (_fid) _dbTree.openGroup[_fid] = true;   // 展开刚放进的夹
        _dbUploadFolder = '';
        refreshDocumentList();
    } catch (e) {
        showToast('❌ 上传失败: ' + e.message, 'error');
    }
}

// ── 数据库条目列表（笔记形态：每条 = 标题 + markdown 正文 + 附件/图片内嵌）────────────────
// 后端存储复用笔记体系：branch_id = '__db__'；附件/图片走 {{file:file_id:文件名}} / ![图](url) 占位，
// 渲染用 formatMessage，与聊天附件、笔记完全一致。

var DB_BRANCH = '__db__';
var _dbEntryCollapsed = {};
var _dbEntries = [];   // 最近一次条目缓存（供编辑回显）
// 当前展示的条目类型：note=笔记（默认，人写）/ document=文档（机器解析，供检索）
// 用户 2026-09-22 定：两者数据结构完全一致，只是分类不同，用于 Tab 切换展示。
// _dbKind 已废弃：树模式下「笔记/文档」是根节点，同时展示，无需切换

function _dbEntryBodyHtml(n) {
    return n.content
        ? formatMessage(n.content, true, 'note')
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

// ── 分组树状态 ─────────────────────────────────
var _dbUploadFolder = '';   // 点某个文件夹的「📤」时，上传目标夹（按钮已下沉到每个夹）
var _dbTree = {
    folders: [],          // 全部文件夹（含 path/depth/parent_id/intro/parse/system）
    openKind: { note: true, document: true },  // 兼容旧字段（树模式下不再按 kind 分组）
    openGroup: {},        // 文件夹展开态（gid -> bool）
    owner: '',            // 筛选：上传者（''=全部）
    year: '',             // 筛选：上传年份（''=全部）
    sort: 'time'          // 排序
};

/** 某分组的后代 id 集合（含自己） */
function _gDescendants(gid) {
    var out = [gid];
    var walk = function (p) {
        _dbTree.folders.filter(function (f) { return (f.parent_id || '') === p; })
            .forEach(function (k) { out.push(k.id); walk(k.id); });
    };
    walk(gid);
    return out;
}

/** 渲染某父夹下的子夹（递归）。
 *  @param parentGid  父夹 id（'' = 根级；判断根级子夹时用 parent_id==='')
 *  @param rootKind   所属根（笔记/文档），用于给按钮标记上传去处
 */
function _renderGroupChildren(parentGid, rootKind, noteLookup, myName, isAdmin) {
    var kids = _dbTree.folders.filter(function (f) { return (f.parent_id || '') === (parentGid || ''); });
    if (!kids.length) return '';

    return kids.map(function (g) {
        // 该夹（含所有后代夹）下的条目
        var ids = _gDescendants(g.id);
        var mine = [];
        ids.forEach(function (id) { (noteLookup[id] || []).forEach(function (n) { mine.push(n); }); });
        var kidCount = _dbTree.folders.filter(function (f) { return (f.parent_id || '') === g.id; }).length;
        // 空夹也显示（可先搭骨架）—— 用户 2026-09-25 定
        var isOpen = !!_dbTree.openGroup[g.id];
        var caret = (kidCount || mine.length) ? '▶' : '';
        var sysBadge = g.system ? '<span class="db-tsys">系统</span>' : '';
        var h = '<div class="db-tnode" data-gid="' + g.id + '">';
        h += '<div class="db-tnode-row' + (isOpen ? ' open' : '') + '" data-gid="' + g.id + '">';
        h += '<span class="db-tcaret' + (isOpen ? ' open' : '') + '">' + caret + '</span>';
        h += '<span class="db-tname">📁 ' + escHtml(g.name) + '</span>' + sysBadge;
        h += '<span class="db-tcount">' + mine.length + '</span>';
        h += '<span class="db-tacts">';
        // ★ 每个夹自己带「上传 / ＋笔记」（用户 2026-09-25 定：按钮下沉到分组）
        h += '<button data-gact="upload" data-gid="' + g.id + '" title="上传文档到「' + escHtml(g.name) + '」（此夹内的附件会自动解析成文字）">📤</button>';
        h += '<button data-gact="addentry" data-gid="' + g.id + '" title="在此夹新建笔记条目">＋</button>';
        h += '<button data-gact="add" data-gid="' + g.id + '" title="在此夹下新建子文件夹">🗂️</button>';
        h += '<button data-gact="edit" data-gid="' + g.id + '" title="编辑名称与简介">✏️</button>';
        if (!g.system) h += '<button data-gact="del" data-gid="' + g.id + '" title="删除文件夹（内容不会删除）">×</button>';
        h += '</span></div>';
        var intro = (g.intro || '').trim();
        h += '<div class="db-tintro' + (intro ? '' : ' empty') + '">'
            + (intro ? escHtml(intro) : '（无简介，点 ✏️ 添加）') + '</div>';
        if (isOpen) {
            h += '<div class="db-tchildren">';
            h += _renderGroupChildren(g.id, rootKind, noteLookup, myName, isAdmin);
            mine.forEach(function (n) { h += _dbEntryCardHtml(n, myName, isAdmin); });
            h += '</div>';
        }
        h += '</div>';
        return h;
    }).join('');
}

/** 单条条目的卡片 HTML（与改造前完全一致的渲染） */
function _dbEntryCardHtml(n, myName, isAdmin) {
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
        // 正文按需渲染：列表只给目录（不带正文），展开时才取这一条的正文。
        // 原来这里把全文渲染进 DOM（哪怕折叠着）→ 65 条正文 413KB 全要下载并解析。
        // 故这里留空，由 _dbFillEntryBody() 在展开时填。
        + '<div class="note-card-body" id="db-entry-body-' + n.id + '"' + (collapsed ? ' style="display:none;"' : '') + '>'
        + '<div class="message-content" id="db-entry-content-' + n.id + '"></div>'
        + '</div></div>';
}

// ── 正文按需取（用户 2026-09-26 定）─────────────────
// 共享库 65 条正文合计 413KB，占列表 payload 的 95%，而列表只需要标题（中位数 17 字）。
// 目录缓存 _dbEntries 只存元数据；展开某条 / 点编辑时才向 /api/notes/<id> 取那一条正文，
// 取到后写回缓存（同一条只取一次）。
async function _dbEnsureContent(id) {
    var n = _dbEntries.find(function (x) { return x.id === id; });
    if (!n) return null;
    if (n.content !== undefined) return n;          // 已取过
    try {
        var d = await fetch('/api/notes/' + encodeURIComponent(id) + '?source=cloud')
            .then(function (r) { return r.json(); });
        n.content = (d && d.note) ? (d.note.content || '') : '';
        if (d && d.note && d.note.title) n.title = d.note.title;
    } catch (e) {
        n.content = '';                              // 取不到当空条目显示，不阻塞展开
    }
    return n;
}

/** 把某条条目的正文填进它的卡片（按需取 + 渲染 + 补附件删除按钮）。 */
async function _dbFillEntryBody(id) {
    var el = document.getElementById('db-entry-content-' + id);
    if (!el || el.dataset.filled === '1') return;
    var n = await _dbEnsureContent(id);
    if (!n) return;
    el.innerHTML = _dbEntryBodyHtml(n);
    el.dataset.filled = '1';
    // 正文里的附件卡片要补 × 删除按钮（原先是渲染时同步做的，现在挪到这里）
    _enhanceFileCardDelete(el, function (noteId) {
        return _dbEntries.find(function (x) { return x.id === noteId; });
    }, function () { refreshDocumentList(); });
}

// ═══════════════════════════════════════════════
// 列表渲染（用户 2026-09-25 定：树为骨架）
//   根节点 = 类型（笔记/文档）；下层 = 分组树；叶子 = 条目卡片（与改造前一致）
//   筛选（上传者/年份/排序）外置在标题栏，作用于整棵树
// ═══════════════════════════════════════════════
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

    try {
        // 拉全部条目（含 kind/folder_id/owner/时间，**不含正文** —— 正文按需取）
        // with_content=0：共享库 65 条正文 413KB，不带它列表就是 435KB（用户反馈的"卡顿"来源）
        // + 分组树
        const [notes, folders] = await Promise.all([
            fetch('/api/notes?branch_id=' + encodeURIComponent(DB_BRANCH) + '&with_content=0')
                .then(function (r) { return r.json(); })
                .then(function (d) { return d.notes || []; }),
            _fetchGroups()
        ]);
        _dbTree.folders = folders || [];

        // 应用筛选（上传者 / 年份 / 排序）—— 条件作用在整棵树
        var all = notes.slice();
        var html = '';
        if (_dbTree.owner) {
            all = all.filter(function (n) { return (n.owner || '') === _dbTree.owner; });
        }
        if (_dbTree.year) {
            all = all.filter(function (n) {
                return String(n.createdAt || n.updatedAt || '').indexOf(_dbTree.year) === 0;
            });
        }
        if (_dbTree.sort === 'title') {
            all.sort(function (a, b) { return String(a.title || '').localeCompare(String(b.title || '')); });
        } else if (_dbTree.sort === 'time_asc') {
            all.sort(function (a, b) { return String(a.createdAt || '').localeCompare(String(b.createdAt || '')); });
        } else {
            all.sort(function (a, b) { return String(b.createdAt || '').localeCompare(String(a.createdAt || '')); });
        }

        _dbEntries = all;
        var myName = me.username, isAdmin = (me.username === 'admin');
        _refreshFilterMenus(all);

        if (!all.length) {
            badge.textContent = '0 个条目';
            container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--gray);font-size:0.9rem;">'
                + (_dbTree.owner || _dbTree.year ? '当前筛选条件下没有条目<br><span style="font-size:.78rem;">试试把「👤 / 📅」改回「全部」</span>'
                   : '暂无条目<br><span style="font-size:.78rem;">点「📤 上传」或「＋ 笔记」</span>') + '</div>';
            return;
        }

        // ★ 整个共享库 = 一个文件夹树（用户 2026-09-25 定）
        //  根级文件夹就是「📝 笔记」「📄 文档」两个（系统内置），下面挂用户自建夹
        var lookup = {};
        all.forEach(function (n) {
            var fid = n.folder_id || '';
            if (!lookup[fid]) lookup[fid] = [];
            lookup[fid].push(n);
        });

        html += _renderGroupChildren('', '', lookup, myName, isAdmin);

        // 未归属任何夹的条目（理论上迁移后不该有；显示出来以免"东西不见了"）
        var orphan = lookup[''] || [];
        if (orphan.length) {
            html += '<div class="db-tnode" data-gid="">';
            html += '<div class="db-tnode-row" data-gid="">'
                + '<span class="db-tcaret">▶</span>'
                + '<span class="db-tname">📁 未归属</span>'
                + '<span class="db-tcount">' + orphan.length + '</span></div>';
            html += '<div class="db-tintro empty">这些条目还没有归入任何文件夹（可右键移动到目标夹）</div>';
            html += '</div>';
        }
        container.innerHTML = html;
        badge.textContent = all.length + ' 个条目'
            + (_dbTree.owner ? ' · ' + _dbTree.owner : '')
            + (_dbTree.year ? ' · ' + _dbTree.year + '年' : '');

        // 附件卡片 × 删除
        _enhanceFileCardDelete(container, function (noteId) {
            return _dbEntries.find(function (x) { return x.id === noteId; });
        }, function () { refreshDocumentList(); });

        // 条目展开/折叠（正文按需取：展开时才去后端取这一条的正文）
        container.querySelectorAll('.db-entry-toggle').forEach(function (el) {
            el.addEventListener('click', function () {
                var id = el.dataset.id;
                var bodyEl = document.getElementById('db-entry-body-' + id);
                if (bodyEl) {
                    var hide = bodyEl.style.display !== 'none';
                    bodyEl.style.display = hide ? 'none' : '';
                    _dbEntryCollapsed[id] = hide;
                    if (!hide) _dbFillEntryBody(id);
                }
            });
        });
        // 上次展开着的条目：本次渲染后恢复展开并补上正文
        Object.keys(_dbEntryCollapsed).forEach(function (id) {
            if (_dbEntryCollapsed[id] === false) _dbFillEntryBody(id);
        });
        container.querySelectorAll('.db-entry-edit').forEach(function (btn) {
            btn.addEventListener('click', function () { dbEditEntry(btn.dataset.id); });
        });
        container.querySelectorAll('.db-entry-del').forEach(function (btn) {
            btn.addEventListener('click', function () { dbDeleteEntry(btn.dataset.id); });
        });
        container.querySelectorAll('.db-entry-ref').forEach(function (btn) {
            btn.addEventListener('click', function () { if (window.refDbEntry) window.refDbEntry(btn.dataset.id); });
        });

        // 右键条目 → 移动到分组
        container.querySelectorAll('.note-card[data-eid]').forEach(function (card) {
            card.addEventListener('contextmenu', function (ev) {
                ev.preventDefault();
                var owner = card.dataset.owner || '';
                if (!((owner === myName) || isAdmin)) { showToast('只能移动自己的条目'); return; }
                _showMoveMenu(ev.clientX, ev.clientY, card.dataset.eid);
            });
        });

        // ── 树交互 ──
        // 文件夹行：点击展开折叠；按钮见下
        container.querySelectorAll('.db-tnode-row').forEach(function (row) {
            row.addEventListener('click', function (ev) {
                if (ev.target.closest('button')) return;
                var gid = row.dataset.gid;
                if (ev.target.classList.contains('db-tcaret')) {
                    _dbTree.openGroup[gid] = !_dbTree.openGroup[gid];
                    refreshDocumentList();
                } else {
                    _dbTree.openGroup[gid] = true;
                    refreshDocumentList();
                }
            });
            // 右键分组 → 移动
            row.addEventListener('contextmenu', function (ev) {
                ev.preventDefault();
                _showMoveGroupMenu(ev.clientX, ev.clientY, row.dataset.gid);
            });
        });
        container.querySelectorAll('.db-tacts button').forEach(function (b) {
            b.addEventListener('click', function (ev) {
                ev.stopPropagation();
                var act = b.dataset.gact, gid = b.dataset.gid || '';
                if (act === 'add') _editGroup(null, gid, '');
                else if (act === 'edit') {
                    var f = _dbTree.folders.find(function (x) { return x.id === gid; });
                    if (f) _editGroup(f.id, null, '', f.name, f.intro || '');
                } else if (act === 'del') _deleteGroupConfirm(gid);
                else if (act === 'upload') {
                    // ★ 上传到该文件夹（按钮下沉）—— 落在 parse=true 的夹会自动解析
                    _dbUploadFolder = gid;
                    var fi = document.getElementById('db-file-input');
                    if (fi) fi.click();
                } else if (act === 'addentry') {
                    // 在该文件夹下新建笔记条目（不解析）
                    dbAddEntry(gid);
                }
            });
        });
    } catch (e) {
        console.error('加载数据库条目失败:', e);
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--error);">加载失败</div>';
    }
}

/** 刷新筛选菜单选项（上传者列表从实际数据里取） */
function _refreshFilterMenus(notes) {
    // 上传者
    var owners = {};
    notes.forEach(function (n) { var o = n.owner || 'admin'; owners[o] = (owners[o] || 0) + 1; });
    var om = document.getElementById('db-f-owner-menu');
    if (om) {
        var items = ['<button class="db-filter-item' + (_dbTree.owner === '' ? ' active' : '')
                     + '" data-owner="" role="menuitem">👤 全部上传者</button>'];
        Object.keys(owners).sort().forEach(function (o) {
            items.push('<button class="db-filter-item' + (_dbTree.owner === o ? ' active' : '')
                       + '" data-owner="' + escHtml(o) + '" role="menuitem">👤 ' + escHtml(o)
                       + ' <span style="color:var(--gray);font-size:.8rem;">(' + owners[o] + ')</span></button>');
        });
        om.innerHTML = items.join('');
        om.querySelectorAll('.db-filter-item').forEach(function (b) {
            b.addEventListener('click', function () {
                _dbTree.owner = b.dataset.owner || '';
                document.getElementById('db-f-owner-label').textContent =
                    _dbTree.owner ? ('👤 ' + _dbTree.owner) : '👤 全部';
                om.hidden = true;
                refreshDocumentList();
            });
        });
    }
    // 年份：从「全部条目」里取（不受当前筛选影响，否则选了就只有一个选项）
    var ym = document.getElementById('db-f-year-menu');
    if (ym && !ym.dataset.filled) {
        _fetchAllYears();
    }
}

async function _fetchAllYears() {
    var ym = document.getElementById('db-f-year-menu');
    if (!ym) return;
    var years = {};
    // 复用列表已经拉到的 _dbEntries（原来这里又整份拉了一次 → 白下 435KB）
    (_dbEntries || []).forEach(function (n) {
        var y = String(n.createdAt || n.updatedAt || '').slice(0, 4);
        if (y.length === 4) years[y] = (years[y] || 0) + 1;
    });
    var items = ['<button class="db-filter-item' + (_dbTree.year === '' ? ' active' : '')
                 + '" data-year="" role="menuitem">📅 全部年份</button>'];
    Object.keys(years).sort().reverse().forEach(function (y) {
        items.push('<button class="db-filter-item' + (_dbTree.year === y ? ' active' : '')
                   + '" data-year="' + y + '" role="menuitem">📅 ' + y + ' 年'
                   + ' <span style="color:var(--gray);font-size:.8rem;">(' + years[y] + ')</span></button>');
    });
    ym.innerHTML = items.join('');
    ym.dataset.filled = '1';
    ym.querySelectorAll('.db-filter-item').forEach(function (b) {
        b.addEventListener('click', function () {
            _dbTree.year = b.dataset.year || '';
            document.getElementById('db-f-year-label').textContent =
                _dbTree.year ? ('📅 ' + _dbTree.year) : '📅 全部';
            ym.hidden = true;
            refreshDocumentList();
        });
    });
}

async function dbEditEntry(id) {
    // 目录不带正文 → 编辑前先把这一条的正文取回来（列表缓存里已有就不重复取）
    var n = await _dbEnsureContent(id);
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

function dbAddEntry(folderId) {
    var me = (typeof window.getAuthUser === 'function') ? window.getAuthUser() : null;
    if (!me || me.username === 'guest') { showToast('请先登录', 'info'); return; }
    if (typeof window.floatingEditor === 'undefined') { showToast('编辑器未就绪', 'error'); return; }
    var _gid = folderId || '';
    var _gn = _gid ? ((_dbTree.folders.find(function (x) { return x.id === _gid; }) || {}).name || '') : '';
    window.floatingEditor.open({
        title: _gn ? ('在「' + _gn + '」新建条目') : '新建数据库条目',
        showTitle: true,
        titleValue: '',
        value: '',
        onSave: function (newContent, newTitle) {
            var t = (newTitle !== null && newTitle !== undefined && newTitle !== '') ? newTitle : '未命名条目';
            fetch('/api/notes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: t, content: newContent, branch_id: DB_BRANCH,
                                      kind: 'note', folder_id: _gid })
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (d.status === 'ok') {
                    showToast('✅ 条目已创建', 'success');
                    if (_gid) _dbTree.openGroup[_gid] = true;
                    refreshDocumentList();
                }
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
    title.textContent = '移动到文件夹';
    menu.appendChild(title);

    // ★ 可以移到**任意文件夹**（含另一个根夹下）—— 用户 2026-09-25 定：移动永不触发解析
    if (!folders.length) {
        const empty = document.createElement('div');
        empty.className = 'db-move-menu-item';
        empty.style.color = 'var(--gray, #999)';
        empty.style.cursor = 'default';
        empty.textContent = '（还没有文件夹）';
        menu.appendChild(empty);
    } else {
        folders.forEach(function (f) {
            const it = document.createElement('div');
            it.className = 'db-move-menu-item';
            it.style.paddingLeft = (12 + (f.depth || 0) * 14) + 'px';
            it.textContent = '📁 ' + f.name + (f.system ? '（根）' : '');
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

// ═══════════════════════════════════════════════
// 分组管理面板（用户 2026-09-25 定：手动控制分组）
// 「📁 分组」按钮 → 列表；每项可编辑（经典浮窗改名字+简介）/移动/删除/加子组
// 编辑用 window.floatingEditor（与笔记编辑、AI 回复编辑同一个浮窗）
// ═══════════════════════════════════════════════

async function _groupOp(payload) {
    const resp = await fetch('/api/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const d = await resp.json();
    if (!resp.ok || d.error) throw new Error(d.error || ('HTTP ' + resp.status));
    return d;
}

// 分组管理已合并进条目树（用户 2026-09-25 定）—— 不再有独立面板；
// 分组的新建/编辑/移动分散在树的每行操作按钮与右键菜单里。
// refreshGroupsList 保留为「刷新树」的薄封装，供 _editGroup / _deleteGroupConfirm 调用。
async function refreshGroupsList() { await refreshDocumentList(); }

/**
 * 新建 / 编辑分组 —— 统一用「经典浮窗」（window.floatingEditor）：
 *   标题行 = 分组名（showTitle）
 *   正文区 = 分组简介（intro）
 * @param editId  传 id = 编辑现有；传 null = 新建
 * @param parentId 新建时的父分组
 */
function _editGroup(editId, parentId, _unused, name, intro) {
    if (typeof window.floatingEditor === 'undefined') { showToast('编辑器未就绪', 'error'); return; }
    const isNew = !editId;
    window.floatingEditor.open({
        title: isNew ? (parentId ? '新建子分组' : '新建分组') : '编辑分组',
        showTitle: true,
        titleValue: name || '',
        value: intro || '',
        onSave: async function (newIntro, newName) {
            const nm = (newName || '').trim();
            if (!nm) { showToast('分组名不能为空', 'error'); return; }
            try {
                if (isNew) {
                    await _groupOp({ action: 'create_group', name: nm,
                                     parent_id: parentId || '', intro: newIntro || '' });
                    showToast('✅ 已创建分组「' + nm + '」', 'success');
                } else {
                    await _groupOp({ action: 'update_group', group_id: editId,
                                     name: nm, intro: newIntro || '' });
                    showToast('✅ 已保存', 'success');
                }
                await refreshGroupsList();
                if (typeof refreshDocumentList === 'function') refreshDocumentList();
            } catch (e) {
                showToast('保存失败：' + e.message, 'error');
            }
        }
    });
}

function _deleteGroupConfirm(gid) {
    if (!window.confirm('删除该分组？\n\n分组里的条目不会被删除，子分组与条目会上移到父级。')) return;
    _groupOp({ action: 'delete_group', group_id: gid, mode: 'move_up' })
        .then(function () {
            showToast('✅ 分组已删除（条目已上移，未丢失）', 'success');
            return refreshGroupsList();
        })
        .then(function () { if (typeof refreshDocumentList === 'function') refreshDocumentList(); })
        .catch(function (e) { showToast('删除失败：' + e.message, 'error'); });
}

/** 右键分组 → 「移动到…」菜单（不可移到自己或自己的后代下） */
var _moveGroupMenuEl = null;

function _closeMoveGroupMenu() {
    if (_moveGroupMenuEl && _moveGroupMenuEl.parentNode) _moveGroupMenuEl.parentNode.removeChild(_moveGroupMenuEl);
    _moveGroupMenuEl = null;
}

async function _showMoveGroupMenu(x, y, gid) {
    _closeMoveGroupMenu();
    let folders = [];
    try {
        folders = (await _groupOp({ action: 'list_groups' })).folders || [];
    } catch (e) { showToast('读取分组失败：' + e.message, 'error'); return; }

    // 排除自己与自己的后代（后端也会校验，这里先不显示以免误点）
    const self = folders.find(function (f) { return f.id === gid; });
    const selfPath = (self && self.path) || '';
    const candidates = folders.filter(function (f) {
        return f.id !== gid && !(selfPath && f.path && f.path.indexOf(selfPath + '/') === 0);
    });

    const menu = document.createElement('div');
    menu.className = 'db-move-menu';
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    const title = document.createElement('div');
    title.className = 'db-move-menu-title';
    title.textContent = '移动「' + ((self && self.name) || '') + '」到';
    menu.appendChild(title);

    const rootItem = document.createElement('div');
    rootItem.className = 'db-move-menu-item';
    rootItem.textContent = '（移动到根级）';
    rootItem.addEventListener('click', function () { _doMoveGroup(gid, ''); });
    menu.appendChild(rootItem);

    candidates.forEach(function (f) {
        const it = document.createElement('div');
        it.className = 'db-move-menu-item';
        it.style.paddingLeft = (12 + (f.depth || 0) * 14) + 'px';
        it.textContent = '📁 ' + f.name;
        it.addEventListener('click', function () { _doMoveGroup(gid, f.id); });
        menu.appendChild(it);
    });

    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    if (r.right > window.innerWidth - 8) menu.style.left = (window.innerWidth - r.width - 8) + 'px';
    if (r.bottom > window.innerHeight - 8) menu.style.top = (y - r.height) + 'px';
    _moveGroupMenuEl = menu;
    setTimeout(function () {
        document.addEventListener('click', _closeMoveGroupMenu, { once: true });
        document.addEventListener('contextmenu', _closeMoveGroupMenu, { once: true });
    }, 0);
}

async function _doMoveGroup(gid, newParentId) {
    _closeMoveGroupMenu();
    try {
        await _groupOp({ action: 'move_group', group_id: gid, parent_id: newParentId });
        showToast('✅ 已移动', 'success');
        await refreshGroupsList();
        if (typeof refreshDocumentList === 'function') refreshDocumentList();
    } catch (e) {
        showToast('移动失败：' + e.message, 'error');
    }
}

window.refreshGroupsList = refreshGroupsList;
