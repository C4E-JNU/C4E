/* ============================================================
   C4EAI 文件浮窗预览（应用内打开文件）
   - 纯前端；复用笔记浮窗手法：拖标题栏移动、四角缩放、位置/大小记忆
   - 后端零改动，仅用现成接口：
       GET  /api/file/{id}        inline（图片/PDF 直接在 <img>/<iframe> 里渲染）
       POST /api/file/{id}/open   本机默认应用打开（os.startfile）
       GET  /api/file/{id}/raw    附件下载
       POST /api/file/text        后端已解析的文本（text.txt）
   对外：
       window.openFilePreviewFloat(fileId, fileName)  强制用浮窗打开
       window.openFileById(fileId, fileName)          按设置里的打开方式派发
   ============================================================ */
(function () {
    'use strict';

    var _FP_KEY = 'c4eai-file-preview-window';   // localStorage: {x,y,w,h}
    // 最小尺寸与 .file-preview-panel 的 CSS min-width/min-height 保持一致（320×240）
    var _FP_MIN_W = 320, _FP_MIN_H = 240;
    var _fpInited = false;
    var _cur = { fileId: '', name: '' };

    var IMG_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico', 'avif'];
    var PDF_EXT = ['pdf'];
    var TEXT_EXT = ['txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'xml', 'yaml', 'yml', 'log',
        'ini', 'toml', 'py', 'js', 'ts', 'jsx', 'tsx', 'java', 'c', 'cpp', 'h', 'hpp', 'go', 'rs',
        'php', 'rb', 'swift', 'kt', 'sh', 'bat', 'ps1', 'sql', 'html', 'htm', 'css', 'lua', 'r',
        'pl', 'tex', 'srt', 'vtt'];
    var OFFICE_EXT = ['docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'odt', 'rtf'];

    function _el() { return document.getElementById('file-preview-panel'); }
    function _bodyEl() { return document.getElementById('file-preview-body'); }

    function _extOf(name) {
        var m = String(name || '').toLowerCase().match(/\.([a-z0-9]+)$/);
        return m ? m[1] : '';
    }
    function _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
    function _openMode() {
        try {
            if (typeof state !== 'undefined' && state.settings && state.settings.fileOpenMode) {
                return state.settings.fileOpenMode;
            }
        } catch (e) {}
        return 'backend';
    }
    function _isWinBackend() {
        return (window.C4EAI_PLATFORM || 'win32') === 'win32';
    }
    function _toast(msg, type) {
        if (typeof showPassiveToast === 'function') showPassiveToast(msg);
        else if (typeof showToast === 'function') showToast(msg, type || 'info');
    }

    /* ── 共享池跨实例取数（本地实例文件在云端 hub 时的回退） ── */
    var _hubCache = null;
    function _hubBase(cb) {
        if (_hubCache !== null) { cb(_hubCache); return; }
        fetch('/api/platform').then(function (r) { return r.json(); }).then(function (p) {
            _hubCache = (p && p.hub) || '';
            cb(_hubCache);
        }).catch(function () { _hubCache = ''; cb(''); });
    }
    function _authTok() {
        return (typeof window.getAuthToken === 'function') ? (window.getAuthToken() || '') : '';
    }

    // 返回 Promise<blob>：先本机，404 时经 hub 带 token 拉
    function _fetchFileBlob(fileId) {
        return fetch('/api/file/' + encodeURIComponent(fileId) + '/raw').then(function (r) {
            if (r.ok) return r.blob();
            if (r.status !== 404) throw new Error('HTTP ' + r.status);
            return new Promise(function (resolve, reject) {
                _hubBase(function (base) {
                    if (!base) { reject(new Error('文件不在本实例')); return; }
                    var tok = _authTok();
                    fetch(base + '/api/file/' + encodeURIComponent(fileId) + '/raw', {
                        headers: tok && tok !== 'guest' ? { 'Authorization': 'Bearer ' + tok } : {}
                    }).then(function (r2) {
                        if (!r2.ok) throw new Error('HTTP ' + r2.status);
                        return r2.blob();
                    }).then(resolve, reject);
                });
            });
        });
    }

    // 文本解析回退：本机 /api/file/text → 404 时 hub 同端点
    function _fetchText(fileId, cb) {
        fetch('/api/file/text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: fileId })
        }).then(function (r) {
            if (r.ok) return r.json();
            if (r.status !== 404) throw new Error('HTTP ' + r.status);
            return new Promise(function (resolve, reject) {
                _hubBase(function (base) {
                    if (!base) { reject(new Error('not-local')); return; }
                    var tok = _authTok();
                    fetch(base + '/api/file/text', {
                        method: 'POST',
                        headers: Object.assign({ 'Content-Type': 'application/json' },
                            (tok && tok !== 'guest' ? { 'Authorization': 'Bearer ' + tok } : {})),
                        body: JSON.stringify({ file_id: fileId })
                    }).then(function (r2) { return r2.ok ? r2.json() : { text: '' }; }).then(resolve, reject);
                });
            });
        }).then(function (d) { cb((d && d.text) || ''); })
          .catch(function () { cb(''); });
    }

    /* ── 几何：保存 / 夹紧 / 恢复 ───────────────────────── */
    // 几何版本号：样式/默认尺寸变更时 +1，让旧的 localStorage 记忆失效
    // （2026-09-22 v2：默认尺寸与笔记编辑浮窗统一为 660×480、最小 320×240、圆角 12px）
    var _FP_VER = 2;
    function _saveRect() {
        var p = _el(); if (!p) return;
        try {
            localStorage.setItem(_FP_KEY, JSON.stringify({
                v: _FP_VER,
                x: parseInt(p.style.left, 10) || 0,
                y: parseInt(p.style.top, 10) || 0,
                w: p.offsetWidth,
                h: p.offsetHeight
            }));
        } catch (e) {}
    }

    function _applyRect(rect) {
        var p = _el(); if (!p || !rect) return;
        var vw = window.innerWidth, vh = window.innerHeight, pad = 8;
        var w = Math.min(Math.max(rect.w, _FP_MIN_W), vw - 2 * pad);
        var h = Math.min(Math.max(rect.h, _FP_MIN_H), vh - 2 * pad);
        var x = Math.min(Math.max(rect.x, pad), Math.max(pad, vw - w - pad));
        var y = Math.min(Math.max(rect.y, pad), Math.max(pad, vh - h - pad));
        p.style.left = x + 'px'; p.style.top = y + 'px';
        p.style.width = w + 'px'; p.style.height = h + 'px';
    }

    function _resolveRect() {
        var vw = window.innerWidth, vh = window.innerHeight;
        var saved = null;
        try { saved = JSON.parse(localStorage.getItem(_FP_KEY) || 'null'); } catch (e) {}
        // 仅当几何版本一致时才复用记忆（样式变更后旧的 760×620 不再生效）
        if (saved && saved.w && saved.h && saved.v === _FP_VER) return saved;
        // 默认尺寸与笔记编辑浮窗（660×480）对齐
        var w = Math.min(660, vw - 24), h = Math.min(480, vh - 24);
        return { x: Math.max(8, (vw - w) / 2), y: Math.max(8, (vh - h) / 2), w: w, h: h };
    }

    /* ── 初始化：拖动 / 缩放 / 关闭 / 窗口缩放重夹紧 ─────── */
    function _initFloatWindow() {
        if (_fpInited) return;
        var p = _el(); if (!p) return;
        _fpInited = true;

        var hd = document.getElementById('file-preview-header');
        if (hd) {
            hd.addEventListener('mousedown', function (e) {
                if (e.button !== 0) return;
                if (e.target.closest('button')) return;   // 点按钮不拖窗
                var r = p.getBoundingClientRect();
                var offX = e.clientX - r.left, offY = e.clientY - r.top;
                function mv(ev) {
                    p.style.left = Math.max(-60, ev.clientX - offX) + 'px';
                    p.style.top = Math.max(0, ev.clientY - offY) + 'px';
                }
                function up() {
                    document.removeEventListener('mousemove', mv);
                    document.removeEventListener('mouseup', up);
                    document.body.style.userSelect = '';
                    _saveRect();
                }
                document.body.style.userSelect = 'none';
                document.addEventListener('mousemove', mv);
                document.addEventListener('mouseup', up);
                e.preventDefault();
            });
        }

        if (!p.querySelector('.file-preview-resize')) {
            ['se', 'nw', 'ne', 'sw'].forEach(function (c) {
                var d = document.createElement('div');
                d.className = 'file-preview-resize ' + c;
                d.dataset.corner = c;
                p.appendChild(d);
                d.addEventListener('mousedown', function (e) {
                    e.preventDefault(); e.stopPropagation();
                    var sx = e.clientX, sy = e.clientY;
                    var r0 = p.getBoundingClientRect();
                    var sW = r0.width, sH = r0.height, sL = r0.left, sT = r0.top;
                    function mv(ev) {
                        var dx = ev.clientX - sx, dy = ev.clientY - sy;
                        var nW = sW, nH = sH, nL = sL, nT = sT;
                        if (c.indexOf('e') >= 0) nW = sW + dx;
                        if (c.indexOf('s') >= 0) nH = sH + dy;
                        if (c.indexOf('w') >= 0) { nW = sW - dx; nL = sL + dx; }
                        if (c.indexOf('n') >= 0) { nH = sH - dy; nT = sT + dy; }
                        if (nW < _FP_MIN_W) { if (c.indexOf('w') >= 0) nL = sL + (sW - _FP_MIN_W); nW = _FP_MIN_W; }
                        if (nH < _FP_MIN_H) { if (c.indexOf('n') >= 0) nT = sT + (sH - _FP_MIN_H); nH = _FP_MIN_H; }
                        if (c.indexOf('w') >= 0 && nL < 0) { nW += nL; nL = 0; }
                        if (c.indexOf('n') >= 0 && nT < 0) { nH += nT; nT = 0; }
                        p.style.width = nW + 'px'; p.style.height = nH + 'px';
                        p.style.left = nL + 'px'; p.style.top = nT + 'px';
                    }
                    function up() {
                        document.removeEventListener('mousemove', mv);
                        document.removeEventListener('mouseup', up);
                        document.body.style.userSelect = '';
                        _saveRect();
                    }
                    document.body.style.userSelect = 'none';
                    document.addEventListener('mousemove', mv);
                    document.addEventListener('mouseup', up);
                });
            });
        }

        var closeBtn = document.getElementById('file-preview-close-btn');
        if (closeBtn) closeBtn.addEventListener('click', closeFilePreviewFloat);

        var nativeBtn = document.getElementById('file-preview-open-native');
        if (nativeBtn) nativeBtn.addEventListener('click', function () {
            if (_cur.fileId) _openNative(_cur.fileId, _cur.name);
        });

        var dlBtn = document.getElementById('file-preview-download');
        if (dlBtn) dlBtn.addEventListener('click', function () {
            if (_cur.fileId) _download(_cur.fileId);
        });

        window.addEventListener('resize', function () {
            if (!p.classList.contains('active')) return;
            var r = p.getBoundingClientRect();
            _applyRect({ x: r.left, y: r.top, w: r.width, h: r.height });
        });
    }

    /* ── 内容渲染 ─────────────────────────────────────── */
    function _renderLoading(msg) {
        var b = _bodyEl(); if (!b) return;
        b.innerHTML = '<div class="file-preview-hint">' + _esc(msg || '加载中…') + '</div>';
    }

    function _renderText(text, hint) {
        var b = _bodyEl(); if (!b) return;
        b.innerHTML = (hint ? '<div class="file-preview-note">' + _esc(hint) + '</div>' : '') +
            '<pre class="file-preview-text">' + _esc(text) + '</pre>';
    }

    function _renderBody() {
        var b = _bodyEl(); if (!b) return;
        b.innerHTML = '';
        var id = _cur.fileId, ext = _extOf(_cur.name);

        if (IMG_EXT.indexOf(ext) >= 0) {
            _renderLoading('正在加载图片…');
            _fetchFileBlob(id).then(function (blob) {
                b.innerHTML = '<div class="file-preview-center"><img class="file-preview-img" src="' +
                    URL.createObjectURL(blob) + '" alt="' + _esc(_cur.name) + '"></div>';
            }).catch(function () {
                _renderText('', '图片加载失败（本实例与共享库均未找到该文件）。');
            });
            return;
        }
        if (PDF_EXT.indexOf(ext) >= 0) {
            _renderLoading('正在加载 PDF…');
            _fetchFileBlob(id).then(function (blob) {
                b.innerHTML = '<iframe class="file-preview-frame" src="' + URL.createObjectURL(blob) + '"></iframe>';
            }).catch(function () {
                _renderText('', 'PDF 加载失败（本实例与共享库均未找到该文件）。');
            });
            return;
        }
        if (OFFICE_EXT.indexOf(ext) >= 0) {
            _renderLoading('正在读取后端解析文本…');
            _fetchText(id, function (t) {
                if (!t) {
                    _renderText('', '浏览器无法直接渲染 .' + ext + ' 排版，且后端没有该文件的解析文本。可用上方 ⬇️ 下载，或点 🖥️ 用本机 ' +
                        (['docx', 'doc', 'odt', 'rtf'].indexOf(ext) >= 0 ? 'Word' : (['xlsx', 'xls'].indexOf(ext) >= 0 ? 'Excel' : 'PowerPoint')) +
                        ' 打开。');
                    return;
                }
                _renderText(t, '⚠️ 浏览器无法直接渲染 .' + ext + ' 的排版，以下是后端解析出的纯文本内容（点 🖥️ 可用本机应用打开原文件）。');
            });
            return;
        }
        if (TEXT_EXT.indexOf(ext) >= 0) {
            _renderLoading('正在读取文本…');
            _fetchText(id, function (t) {
                if (!t) { _renderText('', '该文件没有可用文本，点 ⬇️ 下载查看。'); return; }
                _renderText(t, '');
            });
            return;
        }
        // 其它二进制：给提示 + 操作
        _renderText('', '该类型（.' + (ext || '未知') + '）无法在浏览器内预览。点上方 🖥️ 用本机默认应用打开，或点 ⬇️ 下载。');
    }

    /* ── 打开 / 关闭 ──────────────────────────────────── */
    function openFilePreviewFloat(fileId, fileName) {
        var p = _el();
        if (!p) return;
        if (!fileId) { _toast('该文件没有后端数据，无法浮窗预览'); return; }
        _cur.fileId = fileId;
        _cur.name = fileName || 'file';
        var t = document.getElementById('file-preview-title');
        if (t) t.textContent = _cur.name;
        // 非 Windows 后端（Linux/服务器部署）隐藏「用本机默认应用打开」按钮
        var nb = document.getElementById('file-preview-open-native');
        if (nb) nb.style.display = _isWinBackend() ? '' : 'none';
        _initFloatWindow();
        _applyRect(_resolveRect());
        p.classList.add('active');
        _renderBody();
    }

    function closeFilePreviewFloat() {
        var p = _el();
        if (p) p.classList.remove('active');
        // 释放 iframe/图片资源
        var b = _bodyEl();
        if (b) b.innerHTML = '';
    }

    /* ── 三种打开方式派发 ─────────────────────────────── */
    function _openNative(fileId, fileName) {
        fetch('/api/file/' + encodeURIComponent(fileId) + '/open', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.status === 'unsupported') _toast(d.message || '当前部署不支持本机打开');
                else if (d && d.message) _toast(d.message, 'success');
            })
            .catch(function () { _toast('用默认应用打开失败'); });
    }

    function _download(fileId) {
        // 先本机；404（共享池文件在 hub 上）时经 hub 拉 blob 再触发浏览器下载
        _fetchFileBlob(fileId).then(function (blob) {
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = _cur.name || 'file';
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
        }).catch(function () {
            var a = document.createElement('a');
            a.href = '/api/file/' + encodeURIComponent(fileId) + '/raw';
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
        });
    }

    // 按设置派发：backend=本机默认应用, browser=新标签页, float=应用内浮窗
    function openFileById(fileId, fileName) {
        if (!fileId) return false;
        var mode = _openMode();
        if (mode === 'float') {
            openFilePreviewFloat(fileId, fileName);
        } else if (mode === 'browser') {
            window.open('/api/file/' + encodeURIComponent(fileId), '_blank');
        } else {
            _openNative(fileId, fileName);
        }
        return true;
    }

    window.openFilePreviewFloat = openFilePreviewFloat;
    window.closeFilePreviewFloat = closeFilePreviewFloat;
    window.openFileById = openFileById;
    // 数据库页专用：浮窗能预览就浮窗，不能就下载（与"设置里的打开方式"无关）
    window.openDbFile = function (fileId, fileName) {
        if (!fileId) return false;
        var ext = _extOf(fileName);
        if (IMG_EXT.indexOf(ext) >= 0 || PDF_EXT.indexOf(ext) >= 0 ||
            TEXT_EXT.indexOf(ext) >= 0 || OFFICE_EXT.indexOf(ext) >= 0) {
            openFilePreviewFloat(fileId, fileName);
        } else {
            _cur.fileId = fileId; _cur.name = fileName || 'file';   // 供 _download 取文件名
            _download(fileId);
            _toast('该类型（.' + (ext || '未知') + '）浏览器无法预览，已下载');
        }
        return true;
    };
})();
