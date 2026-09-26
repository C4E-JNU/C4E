// ==================== 引用给智能体（笔记 / 数据库条目） ====================
// 效果与"对话附件"一致：点条目卡片上的 📌 → 作为待发引用出现在输入区上方芯片栏 →
// 发送时随请求带给后端（后端把全文+scope+id 注入当轮上下文，智能体可用 conversation_notes 工具
// 按 scope+id 直接增删改查该条目）。本地笔记 scope='notes'，云端数据库条目 scope='database'。

(function () {
    function _state() {
        if (typeof state === 'undefined') return null;
        if (!Array.isArray(state.pendingNoteRefs)) state.pendingNoteRefs = [];
        return state;
    }

    function _bar() {
        var bar = document.getElementById('pending-refs-bar');
        if (bar) return bar;
        // 挂在输入区上方（现代输入条之前）
        var anchor = document.querySelector('.modern-input-bar');
        if (!anchor || !anchor.parentNode) return null;
        bar = document.createElement('div');
        bar.id = 'pending-refs-bar';
        bar.style.cssText = 'display:none;flex-wrap:wrap;gap:6px;padding:6px 10px 2px;align-items:center;';
        anchor.parentNode.insertBefore(bar, anchor);
        return bar;
    }

    function renderRefsBar() {
        var s = _state();
        if (!s) return;
        var bar = _bar();
        if (!bar) return;
        var refs = s.pendingNoteRefs;
        if (!refs.length) { bar.style.display = 'none'; bar.innerHTML = ''; return; }
        bar.style.display = 'flex';
        bar.innerHTML = '<span style="font-size:.75rem;color:var(--gray);">引用给智能体：</span>' +
            refs.map(function (r, i) {
                var icon = r.scope === 'database' ? '🗄️' : '📝';
                return '<span class="note-ref-chip" data-idx="' + i + '" title="' + _esc(r.title) + '（' + r.id + '）" ' +
                    'style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border:1px solid var(--border,#d0d5dd);' +
                    'border-radius:12px;background:var(--card-bg,#f6f8fa);font-size:.78rem;max-width:260px;">' +
                    icon + '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _esc(r.title || r.id) + '</span>' +
                    '<button class="note-ref-del" data-idx="' + i + '" title="移除引用" ' +
                    'style="background:none;border:none;cursor:pointer;color:var(--gray);font-size:.85rem;padding:0 2px;">×</button>' +
                    '</span>';
            }).join('');
        bar.querySelectorAll('.note-ref-del').forEach(function (b) {
            b.addEventListener('click', function (e) {
                e.stopPropagation();
                var idx = parseInt(b.dataset.idx, 10);
                s.pendingNoteRefs.splice(idx, 1);
                renderRefsBar();
            });
        });
    }

    function _esc(t) {
        return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // 加引用：{id, scope, title, content}
    function addNoteRef(ref) {
        var s = _state();
        if (!s) return;
        if (!ref || !ref.id) { if (typeof showToast === 'function') showToast('该条目缺少 id，无法引用', 'error'); return; }
        var scope = ref.scope || 'notes';
        if (s.pendingNoteRefs.some(function (r) { return r.id === ref.id && r.scope === scope; })) {
            if (typeof showToast === 'function') showToast('该条目已在引用列表中', 'info');
            return;
        }
        s.pendingNoteRefs.push({ id: ref.id, scope: scope, title: ref.title || ref.id, content: ref.content || '' });
        renderRefsBar();
        if (typeof showPassiveToast === 'function') showPassiveToast('已引用：' + (ref.title || ref.id) + '（发送时智能体将读取其全文）');
    }

    // 取走并清空（发送时调用）
    function takeNoteRefs() {
        var s = _state();
        if (!s) return [];
        var out = s.pendingNoteRefs.slice();
        s.pendingNoteRefs = [];
        renderRefsBar();
        return out;
    }

    // 只看不清（发送前读取，避免校验失败时丢引用）
    function peekNoteRefs() {
        var s = _state();
        return s ? s.pendingNoteRefs.slice() : [];
    }

    function clearNoteRefs() { var s = _state(); if (s) { s.pendingNoteRefs = []; renderRefsBar(); } }

    // 从笔记面板缓存里找一条本地笔记
    function refLocalNote(noteId) {
        var notes = (typeof _currentNotes !== 'undefined' && _currentNotes) ? _currentNotes : [];
        var n = notes.find(function (x) { return x.id === noteId; });
        if (!n) { if (typeof showToast === 'function') showToast('找不到该笔记', 'error'); return; }
        addNoteRef({ id: n.id, scope: 'notes', title: n.title || '未命名笔记', content: n.content || '' });
    }

    // 从数据库条目缓存里找一条（js/database.js 的 _dbEntries）
    function refDbEntry(entryId) {
        var arr = (typeof _dbEntries !== 'undefined' && _dbEntries) ? _dbEntries : [];
        var n = arr.find(function (x) { return x.id === entryId; });
        if (!n) { if (typeof showToast === 'function') showToast('找不到该数据库条目', 'error'); return; }
        addNoteRef({ id: n.id, scope: 'database', title: n.title || '未命名条目', content: n.content || '' });
    }

    window.addNoteRef = addNoteRef;
    window.noteRefs = { add: addNoteRef, take: takeNoteRefs, peek: peekNoteRefs, clear: clearNoteRefs, render: renderRefsBar };
    window.refLocalNote = refLocalNote;
    window.refDbEntry = refDbEntry;
})();
