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
    // <img src>/<iframe src> 不能带 Authorization 头 → 云端（C4EAI_AUTH=1）必须把 token 放查询串。
    // 后端只对 GET /api/file/* 认 ?token=（见 app.py 的鉴权中间件）。
    function _tokQS() {
        var tk = '';
        try { tk = _authTok(); } catch (e) {}
        return (tk && tk !== 'guest') ? ('?token=' + encodeURIComponent(tk)) : '';
    }
    function _fileUrl(fileId) {
        return '/api/file/' + encodeURIComponent(fileId) + _tokQS();
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
    // 2026-10-22：加 20s 超时。之前 fetch 无超时，后端/hub 一挂就永远停在「从后台读取解析文本……」。
    function _fetchText(fileId, cb) {
        function _withTimeout(url, opts, ms) {
            var ctl = (typeof AbortController === 'function') ? new AbortController() : null;
            var timer = ctl ? setTimeout(function () { try { ctl.abort(); } catch (e) {} }, ms) : null;
            if (ctl) opts.signal = ctl.signal;
            return fetch(url, opts).then(function (r) { if (timer) clearTimeout(timer); return r; },
                function (e) { if (timer) clearTimeout(timer); throw e; });
        }
        _withTimeout('/api/file/text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: fileId })
        }, 20000).then(function (r) {
            if (r.ok) return r.json();
            if (r.status !== 404) throw new Error('HTTP ' + r.status);
            return new Promise(function (resolve, reject) {
                _hubBase(function (base) {
                    if (!base) { reject(new Error('not-local')); return; }
                    var tok = _authTok();
                    _withTimeout(base + '/api/file/text', {
                        method: 'POST',
                        headers: Object.assign({ 'Content-Type': 'application/json' },
                            (tok && tok !== 'guest' ? { 'Authorization': 'Bearer ' + tok } : {})),
                        body: JSON.stringify({ file_id: fileId })
                    }, 20000).then(function (r2) { return r2.ok ? r2.json() : { text: '' }; }).then(resolve, reject);
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
        b.innerHTML = '<div class="file-preview-hint">' + _esc(msg || t('fp.loading')) + '</div>';
    }

    // 浏览器打不开原件时的统一落地页：明确告知 + 下载键（用户 2026-10-22：解析文本也要先说明这一点）
    function _renderUnpreviewable(ext, hint) {
        var b = _bodyEl(); if (!b) return;
        b.innerHTML = '<div class="file-preview-hint">' +
            _esc(hint || t('fp.cantPreview').replace('{ext}', ext || t('fp.unknown'))) + '</div>' +
            '<div style="text-align:center;margin-top:12px;">' +
            '<button type="button" class="file-preview-tg active" id="fp-bin-download">⬇️ ' +
            _esc(t('fp.download')) + '</button></div>';
        var bd = document.getElementById('fp-bin-download');
        if (bd) bd.addEventListener('click', function () { if (_cur.fileId) _download(_cur.fileId); });
    }

    function _renderText(text, hint) {
        var b = _bodyEl(); if (!b) return;
        var head = hint ? '<div class="file-preview-note">' + _esc(hint) + '</div>' : '';
        var ext = _extOf(_cur.name);
        // .md/.markdown 走与对话框、笔记框**同一个**渲染器（formatting.js 的 formatMessage），
        // ctx='note' 使文中 {{file:xxx}} 占位同样触发 openDbFile 二级浮窗。
        // 2026-09-28 用户报：产出的小窗只能看到源码，要求像笔记框那样渲染 → 默认渲染 + 可切源码。
        if ((ext === 'md' || ext === 'markdown') && text && typeof window.formatMessage === 'function') {
            var html = '';
            try { html = window.formatMessage(text, true, 'note') || ''; } catch (e) { html = ''; }
            if (html) {
                b.innerHTML = head +
                    '<div class="file-preview-togglebar">' +
                        '<button type="button" class="file-preview-tg active" data-mode="md">' + t('fp.render') + '</button>' +
                        '<button type="button" class="file-preview-tg" data-mode="raw">' + t('fp.source') + '</button>' +
                    '</div>' +
                    '<div class="file-preview-md message-content">' + html + '</div>' +
                    '<pre class="file-preview-text" style="display:none">' + _esc(text) + '</pre>';
                var mdEl = b.querySelector('.file-preview-md');
                var rawEl = b.querySelector('.file-preview-text');
                Array.prototype.forEach.call(b.querySelectorAll('.file-preview-tg'), function (btn) {
                    btn.addEventListener('click', function () {
                        var raw = btn.getAttribute('data-mode') === 'raw';
                        if (mdEl) mdEl.style.display = raw ? 'none' : '';
                        if (rawEl) rawEl.style.display = raw ? '' : 'none';
                        Array.prototype.forEach.call(b.querySelectorAll('.file-preview-tg'), function (o) {
                            o.classList.toggle('active', o === btn);
                        });
                    });
                });
                return;
            }
        }
        b.innerHTML = head + '<pre class="file-preview-text">' + _esc(text) + '</pre>';
    }

    function _renderBody() {
        var b = _bodyEl(); if (!b) return;
        b.innerHTML = '';
        var id = _cur.fileId, ext = _extOf(_cur.name);

        if (IMG_EXT.indexOf(ext) >= 0) {
            // 直接指向内联端点，不走 fetch+blob：
            // blob 路径要把整个文件读进内存再 createObjectURL，大文件实测读不出来
            // （2026-09-28 用户报「智能体加的附件点开小窗加载不出来」：12.5MB PDF 卡在 r.blob()）。
            // /api/file/<id> 本身是 inline，且本地实例会自动带会话代理到共享库，云端文件同样能显示。
            b.innerHTML = '<div class="file-preview-center"><img class="file-preview-img" alt="' +
                _esc(_cur.name) + '"></div>';
            var _im = b.querySelector('img');
            _im.onerror = function () { _renderText('', t('fp.imgFail')); };
            _im.src = _fileUrl(id);
            return;
        }
        if (PDF_EXT.indexOf(ext) >= 0) {
            // 同上：交给浏览器自带 PDF 阅读器，不再经过 blob（大文件会卡）。
            b.innerHTML = '<iframe class="file-preview-frame"></iframe>';
            var _fr = b.querySelector('iframe');
            _fr.src = _fileUrl(id);
            return;
        }
        if (OFFICE_EXT.indexOf(ext) >= 0) {
            // 打开即告知「原件显示不了」（用户 2026-10-22 定），解析文本只是附加信息；
            // 解析失败/超时 → 直接走「请用下载保存」，不再无限转圈。
            var _note = t('fp.officeText').replace('{ext}', ext);
            _renderLoading(t('fp.readingParsed'));
            var _body0 = _bodyEl();
            if (_body0) _body0.insertAdjacentHTML('afterbegin', '<div class="file-preview-note">' + _esc(_note) + '</div>');
            var _myd = _cur.fileId;
            _fetchText(id, function (txt) {
                if (_cur.fileId !== _myd) return;          // 期间换了文件 → 丢弃旧结果
                if (!txt) {
                    _renderUnpreviewable(ext, t('fp.officeNoText')
                        .replace('{ext}', ext)
                        .replace('{app}', ['docx', 'doc', 'odt', 'rtf'].indexOf(ext) >= 0 ? 'Word' : (['xlsx', 'xls'].indexOf(ext) >= 0 ? 'Excel' : 'PowerPoint')));
                    return;
                }
                _renderText(txt, _note);
            });
            return;
        }
        if (TEXT_EXT.indexOf(ext) >= 0) {
            _renderLoading(t('fp.readingText'));
            _fetchText(id, function (t) {
                if (!t) { _renderText('', t('fp.noText')); return; }
                _renderText(t, '');
            });
            return;
        }
        // 其它二进制：浏览器打不开 → 明确告知 + 直接给下载键（用户 2026-10-21 定）
        _renderUnpreviewable(ext);
    }

    /* ── 打开 / 关闭 ──────────────────────────────────── */
    function openFilePreviewFloat(fileId, fileName) {
        var p = _el();
        if (!p) return;
        if (!fileId) { _toast(t('fp.noBackend')); return; }
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
                if (d && d.status === 'unsupported') _toast(d.message || t('fp.unsupported'));
                else if (d && d.message) _toast(d.message, 'success');
            })
            .catch(function () { _toast(t('fp.openNativeFail')); });
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

    // 统一打开方式（2026-10-21 用户定）：
    //   能浏览器预览的（图片/PDF/文本/Office 解析文本）→ 一律应用内浮窗；
    //   不能预览的 → 浮窗里明确写「文件无法在浏览器内打开，请使用下载功能保存」并给下载键。
    // 不再按设置里的三档派发：<img>/<iframe> 才是唯一稳的路（本机应用会跳出应用、切走注意力）。
    function openFileById(fileId, fileName) {
        if (!fileId) { _toast(t('fp.noBackend')); return false; }
        openFilePreviewFloat(fileId, fileName);
        return true;
    }

    window.openFilePreviewFloat = openFilePreviewFloat;
    window.closeFilePreviewFloat = closeFilePreviewFloat;
    window.openFileById = openFileById;
    // 数据库/笔记/产出区：与 openFileById 同一条统一规则（保留别名，旧调用处不用改）
    window.openDbFile = openFileById;
})();
