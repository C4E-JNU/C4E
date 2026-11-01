// ==================== Reference to the agent (notes / database entries) ====================
// Same effect as a chat attachment: click 📌 on an entry card -> it appears as a pending reference chip
// above the input -> sent with the request (the backend injects full text + scope + id into the turn
// context; the agent can read/write it via the conversation_notes tool by scope+id).
// Local notes use scope="notes"; cloud database entries use scope="database".
(function () {
    function _state() {
        if (typeof state === 'undefined') return null;
        if (!Array.isArray(state.pendingNoteRefs)) state.pendingNoteRefs = [];
        return state;
    }

    function _bar() {
        var bar = document.getElementById('pending-refs-bar');
        if (bar) return bar;
        // Mount above the input area (before the modern input bar)
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
        bar.innerHTML = '<span style="font-size:.75rem;color:var(--gray);">' + t('msg.refBar') + '</span>' +
            refs.map(function (r, i) {
                var icon = r.scope === 'database' ? '🗄️' : '📝';
                return '<span class="note-ref-chip" data-idx="' + i + '" title="' + _esc(r.title) + '（' + r.id + '）" ' +
                    'style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border:1px solid var(--border,#d0d5dd);' +
                    'border-radius:12px;background:var(--card-bg,#f6f8fa);font-size:.78rem;max-width:260px;">' +
                    icon + '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _esc(r.title || r.id) + '</span>' +
                    '<button class="note-ref-del" data-idx="' + i + '" title="' + t('msg.refRemove') + '" ' +
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

    // Add a reference: {id, scope, title, content}
    function addNoteRef(ref) {
        var s = _state();
        if (!s) return;
        if (!ref || !ref.id) { if (typeof showToast === 'function') showToast(t('msg.refNoId'), 'error'); return; }
        var scope = ref.scope || 'notes';
        if (s.pendingNoteRefs.some(function (r) { return r.id === ref.id && r.scope === scope; })) {
            if (typeof showToast === 'function') showToast(t('msg.refDup'), 'info');
            return;
        }
        s.pendingNoteRefs.push({ id: ref.id, scope: scope, title: ref.title || ref.id, content: ref.content || '' });
        renderRefsBar();
        if (typeof showPassiveToast === 'function') showPassiveToast(t('msg.refAdded').replace('{title}', ref.title || ref.id));
    }

    // Take and clear (called on send)
    function takeNoteRefs() {
        var s = _state();
        if (!s) return [];
        var out = s.pendingNoteRefs.slice();
        s.pendingNoteRefs = [];
        renderRefsBar();
        return out;
    }

    // Read without clearing (so a validation failure does not lose references)
    function peekNoteRefs() {
        var s = _state();
        return s ? s.pendingNoteRefs.slice() : [];
    }

    function clearNoteRefs() { var s = _state(); if (s) { s.pendingNoteRefs = []; renderRefsBar(); } }

    // Find a local note in the notes panel cache
    function refLocalNote(noteId) {
        var notes = (typeof _currentNotes !== 'undefined' && _currentNotes) ? _currentNotes : [];
        var n = notes.find(function (x) { return x.id === noteId; });
        if (!n) { if (typeof showToast === 'function') showToast(t('msg.refNoteNotFound'), 'error'); return; }
        addNoteRef({ id: n.id, scope: 'notes', title: n.title || t('edit.noteUntitled'), content: n.content || '' });
    }

    // Find a database entry (js/database.js _dbEntries)
    function refDbEntry(entryId) {
        var arr = (typeof _dbEntries !== 'undefined' && _dbEntries) ? _dbEntries : [];
        var n = arr.find(function (x) { return x.id === entryId; });
        if (!n) { if (typeof showToast === 'function') showToast(t('msg.refEntryNotFound'), 'error'); return; }
        addNoteRef({ id: n.id, scope: 'database', title: n.title || t('msg.refUnnamedEntry'), content: n.content || '' });
    }

    window.addNoteRef = addNoteRef;
    window.noteRefs = { add: addNoteRef, take: takeNoteRefs, peek: peekNoteRefs, clear: clearNoteRefs, render: renderRefsBar };
    window.refLocalNote = refLocalNote;
    window.refDbEntry = refDbEntry;
})();
