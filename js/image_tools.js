// ==================== 图片工具 ====================
// 图片以 markdown ![图](url) 插入编辑浮窗，实时显示。
// 点击预览区图片 → 弹出【可移动】独立浮动面板，双模式：
//   裁剪 / 缩放 （一个按钮切换）
// 裁剪实时预览、完成后保留原比例（不拉伸），替换 markdown url。
// 图片存后端：/api/file/upload → /api/file/{file_id}

var imageTools = (function () {
    var state = { img: null, file: null, url: null, naturalW: 0, naturalH: 0, replaceCb: null, mode: 'crop' };
    var ui = { panel: null, box: null, cropBox: null, drag: null };

    // ── 通用：上传 blob → url（图片专用：非图片扩展名强制 .png，供裁剪/缩放管线使用） ──
    function uploadBlob(blob, name, mime, cb) {
        var fd = new FormData();
        var fname = name || 'image.png';
        if (!/\.(png|jpg|jpeg|gif|webp)$/i.test(fname)) fname = fname.replace(/\.[^.]+$/, '') + '.png';
        fd.append('file', new File([blob], fname, { type: mime || blob.type || 'image/png' }));
        fetch('/api/file/upload', { method: 'POST', body: fd })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                if (!res || !res.file_id) { console.error('图片上传失败'); return; }
                if (cb) cb('/api/file/' + res.file_id);
            })
            .catch(function (e) { console.error('图片上传失败', e); });
    }

    // ── 任意文件上传（笔记附件用）：保留原名与原类型，不强制 .png ──
    function uploadFileRaw(file, cb) {
        var fd = new FormData();
        fd.append('file', file);   // 浏览器自动带真实 name/type
        fetch('/api/file/upload', { method: 'POST', body: fd })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                if (!res || !res.file_id) { console.error('文件上传失败', res); if (cb) cb(''); return; }
                if (cb) cb('/api/file/' + res.file_id);
            })
            .catch(function (e) { console.error('文件上传失败', e); if (cb) cb(''); });
    }
    function fetchBlob(url, cb) {
        fetch(url).then(function (r) { return r.blob(); }).then(cb).catch(function (e) { console.error('加载图片失败', e); });
    }
    function chooseFile(cb) {
        var inp = document.createElement('input');
        inp.type = 'file'; inp.accept = 'image/*';
        inp.onchange = function () { var f = inp.files[0]; if (f) cb(f); };
        inp.click();
    }

    // ── 独立可移动浮动面板 ──
    function ensurePanel() {
        if (ui.panel) return;
        var p = document.createElement('div');
        p.id = 'img-edit-panel';
        p.style.cssText = 'position:fixed;z-index:90000;background:#fff;border:1px solid var(--border-color);border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,0.3);display:none;flex-direction:column;overflow:hidden;min-width:320px;min-height:360px;max-width:80vw;max-height:85vh;width:520px;height:440px;';
        p.innerHTML =
            '<div class="fe-header">' +
                '<span id="img-ep-title" style="font-weight:600;">🖼️ 图片编辑</span>' +
                '<div style="display:flex;gap:4px;">' +
                    '<button id="img-ep-mode" class="config-secondary-btn" style="padding:2px 10px;font-size:12px;">✂️ 裁剪</button>' +
                    '<button id="img-ep-done" class="config-secondary-btn" style="padding:2px 10px;font-size:12px;background:var(--primary);color:#fff;">✅ 应用</button>' +
                    '<button class="fe-close" id="img-ep-cancel" title="关闭">&times;</button>' +
                '</div>' +
            '</div>' +
            '<div id="img-ep-size" style="padding:4px 12px;font-size:12px;color:#888;background:#fafafa;border-bottom:1px solid #f0f0f0;"></div>' +
            '<div id="img-ep-stage" style="position:relative;overflow:hidden;background:#f2f2f2;margin:8px;border-radius:6px;flex:1;min-height:120px;display:flex;align-items:center;justify-content:center;"></div>' +
            '<div id="img-ep-hint" style="padding:6px 12px;font-size:12px;color:#888;border-top:1px solid #eee;">拖曳图片四角可放大/缩小（保持比例）</div>' +
        '</div>';
        document.body.appendChild(p);
        ui.panel = p;
        // 拖动标题栏移动
        var hd = p.querySelector('.fe-header');
        hd.addEventListener('mousedown', function (e) {
            if (e.target.closest('button')) return;
            var sx = e.clientX, sy = e.clientY;
            var ox = p.offsetLeft, oy = p.offsetTop;
            function mv(ev) {
                p.style.left = (ox + ev.clientX - sx) + 'px';
                p.style.top = (oy + ev.clientY - sy) + 'px';
            }
            function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
            document.addEventListener('mousemove', mv);
            document.addEventListener('mouseup', up);
        });
        // 四角缩放手柄（面板窗口尺寸，独立于图片，不跟随图片大小）
        ['se','nw','ne','sw'].forEach(function (c) {
            var h = document.createElement('div');
            h.className = 'fe-resize ' + c;
            h.dataset.corner = c;
            h.style.cssText = 'position:absolute;width:14px;height:14px;z-index:20;' +
                (c === 'se' ? 'right:2px;bottom:2px;cursor:nwse-resize;' :
                 c === 'nw' ? 'left:2px;top:2px;cursor:nwse-resize;' :
                 c === 'ne' ? 'right:2px;top:2px;cursor:nesw-resize;' :
                              'left:2px;bottom:2px;cursor:nesw-resize;');
            p.appendChild(h);
            h.addEventListener('mousedown', function (e) {
                e.preventDefault(); e.stopPropagation();
                var sx = e.clientX, sy = e.clientY;
                var sW = p.offsetWidth, sH = p.offsetHeight;
                var sL = p.offsetLeft, sT = p.offsetTop;
                var cc = h.dataset.corner;
                function mv(ev) {
                    var dx = ev.clientX - sx, dy = ev.clientY - sy;
                    var nW = sW, nH = sH, nL = sL, nT = sT;
                    if (cc === 'se') { nW = sW + dx; nH = sH + dy; }
                    if (cc === 'nw') { nW = sW - dx; nL = sL + dx; nH = sH - dy; nT = sT + dy; }
                    if (cc === 'ne') { nW = sW + dx; nH = sH - dy; nT = sT + dy; }
                    if (cc === 'sw') { nW = sW - dx; nL = sL + dx; nH = sH + dy; }
                    if (nW < 320) nW = 320;
                    if (nH < 360) nH = 360;
                    if (cc.indexOf('w') >= 0 && nL < 0) { nW += nL; nL = 0; }
                    if (cc.indexOf('n') >= 0 && nT < 0) { nH += nT; nT = 0; }
                    p.style.width = nW + 'px';
                    p.style.height = nH + 'px';
                    p.style.left = nL + 'px';
                    p.style.top = nT + 'px';
                }
                function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
                document.addEventListener('mousemove', mv);
                document.addEventListener('mouseup', up);
            });
        });
        // 模式切换
        document.getElementById('img-ep-mode').addEventListener('click', function () {
            state.mode = (state.mode === 'crop') ? 'zoom' : 'crop';
            applyMode();
        });
        document.getElementById('img-ep-done').addEventListener('click', applyAndSave);
        document.getElementById('img-ep-cancel').addEventListener('click', close);
    }

    function applyMode() {
        var btn = document.getElementById('img-ep-mode');
        var hint = document.getElementById('img-ep-hint');
        if (state.mode === 'crop') {
            btn.textContent = '✂️ 裁剪';
            if (hint) hint.textContent = '拖动裁剪框可移动，右下角缩放手柄可调裁剪范围';
            hideZoomHandles();
            showCropFrame();
        } else {
            btn.textContent = '↔️ 缩放';
            if (hint) hint.textContent = '拖曳图片四角手柄可放大/缩小（保持比例）';
            hideCropFrame();
            showZoomHandles();
        }
    }

    // ── 图片加载进 stage（按原比例，max-width 100% 等比）──
    function showImage(url) {
        var stage = document.getElementById('img-ep-stage');
        stage.innerHTML = '';
        var im = document.createElement('img');
        im.id = 'img-ep-img';
        im.src = url;
        im.style.cssText = 'max-width:100%;max-height:50vh;width:auto;height:auto;display:block;';
        stage.appendChild(im);
        state.img = im;
        // 位置/尺寸信息
        updateSizeInfo();
    }

    function updateSizeInfo() {
        var el = document.getElementById('img-ep-size');
        if (el && state.img) {
            el.textContent = '原始 ' + state.naturalW + '×' + state.naturalH + 'px';
        }
    }

    // ── 裁剪框（可拖/缩放，实时预览：框外暗、框内亮）──
    function showCropFrame() {
        var stage = document.getElementById('img-ep-stage'), im = state.img;
        if (!stage || !im) return;
        hideCropFrame();
        var c = document.createElement('div');
        c.id = 'img-crop-frame';
        c.style.cssText = 'position:absolute;border:1px dashed #e02020;background:transparent;box-shadow:0 0 0 9999px rgba(0,0,0,0.55);z-index:5;cursor:move;';
        stage.appendChild(c);
        ui.cropBox = c;
        var r = im.getBoundingClientRect(), s = stage.getBoundingClientRect();
        var iw = r.width, ih = r.height;
        c.style.left = (r.left - s.left + iw * 0.1) + 'px';
        c.style.top = (r.top - s.top + ih * 0.1) + 'px';
        c.style.width = (iw * 0.8) + 'px';
        c.style.height = (ih * 0.8) + 'px';
        // 拖整体
        c.addEventListener('mousedown', function (e) {
            if (e.target.closest('.crop-handle')) return;
            e.preventDefault(); e.stopPropagation();
            var sx2 = e.clientX, sy2 = e.clientY;
            var ox = parseFloat(c.style.left), oy = parseFloat(c.style.top);
            function mv(ev) {
                var r2 = im.getBoundingClientRect(), s2 = stage.getBoundingClientRect();
                var il = r2.left - s2.left, it = r2.top - s2.top;
                var nx = ox + (ev.clientX - sx2), ny = oy + (ev.clientY - sy2);
                c.style.left = Math.max(il, Math.min(nx, il + r2.width - parseFloat(c.style.width))) + 'px';
                c.style.top = Math.max(it, Math.min(ny, it + r2.height - parseFloat(c.style.height))) + 'px';
                updateSizeInfo();
            }
            function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
            document.addEventListener('mousemove', mv); document.addEventListener('mouseup', up);
        });
        // 四角缩放手柄：每角可独立拖拽调整裁剪框边界
        ['nw','ne','se','sw'].forEach(function (dir) {
            var corner = document.createElement('div');
            corner.className = 'crop-handle ' + dir;
            corner.style.cssText = 'position:absolute;width:14px;height:14px;background:#e02020;border:2px solid #fff;' +
                (dir === 'nw' ? 'left:-7px;top:-7px;cursor:nwse-resize;' :
                 dir === 'ne' ? 'right:-7px;top:-7px;cursor:nesw-resize;' :
                 dir === 'se' ? 'right:-7px;bottom:-7px;cursor:nwse-resize;' :
                               'left:-7px;bottom:-7px;cursor:nesw-resize;') +
                'border-radius:3px;box-sizing:border-box;z-index:7;';
            c.appendChild(corner);
            corner.addEventListener('mousedown', function (e) {
                e.preventDefault(); e.stopPropagation();
                var sx3 = e.clientX, sy3 = e.clientY;
                var dd = dir;
                // 记录裁剪框当前几何（相对 stage）
                var x0 = parseFloat(c.style.left), y0 = parseFloat(c.style.top);
                var w0 = parseFloat(c.style.width), h0 = parseFloat(c.style.height);
                function mv(ev) {
                    var dx = ev.clientX - sx3, dy = ev.clientY - sy3;
                    var r2 = im.getBoundingClientRect(), s2 = stage.getBoundingClientRect();
                    var il = r2.left - s2.left, it = r2.top - s2.top, iR = il + r2.width, iB = it + r2.height;
                    var nL = x0, nT = y0, nR = x0 + w0, nB = y0 + h0;
                    if (dd.indexOf('w') >= 0) nL = Math.max(il, x0 + dx);
                    if (dd.indexOf('e') >= 0) nR = Math.min(iR, x0 + w0 + dx);
                    if (dd.indexOf('n') >= 0) nT = Math.max(it, y0 + dy);
                    if (dd.indexOf('s') >= 0) nB = Math.min(iB, y0 + h0 + dy);
                    if (nR - nL >= 16) { c.style.left = nL + 'px'; c.style.width = (nR - nL) + 'px'; }
                    if (nB - nT >= 16) { c.style.top = nT + 'px'; c.style.height = (nB - nT) + 'px'; }
                    updateSizeInfo();
                }
                function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
                document.addEventListener('mousemove', mv); document.addEventListener('mouseup', up);
            });
        });
    }
    function hideCropFrame() { if (ui.cropBox && ui.cropBox.parentNode) ui.cropBox.parentNode.removeChild(ui.cropBox); ui.cropBox = null; }

    // ── 缩放四角手柄：自由拖拽改图片显示宽高（可压扁，不锁比例），记录显示尺寸供保存写回 markdown ──
    function showZoomHandles() {
        var stage = document.getElementById('img-ep-stage'), im = state.img;
        if (!stage || !im) return;
        hideZoomHandles();
        // 以图片当前显示尺寸为基准（用 offsetWidth/Height 布局尺寸）
        im.style.maxWidth = 'none';
        im.style.width = (im.width ? im.width + 'px' : (state.naturalW + 'px'));
        im.style.height = (im.height ? im.height + 'px' : 'auto');
        state.zoomW = 0; state.zoomH = 0; // 0 = 未缩放，用原尺寸
        var dirs = ['se','nw','ne','sw'];
        var handles = [];
        dirs.forEach(function (d) {
            var h = document.createElement('div');
            h.dataset.d = d;
            h.style.cssText = 'position:absolute;width:12px;height:12px;background:#185ab4;border:1.5px solid #fff;border-radius:3px;z-index:6;box-sizing:border-box;' +
                ((d === 'se' || d === 'nw') ? 'cursor:nwse-resize;' : 'cursor:nesw-resize;');
            handles.push(h);
            stage.appendChild(h);
            h.addEventListener('mousedown', function (e) {
                e.preventDefault(); e.stopPropagation();
                var sx3 = e.clientX, sy3 = e.clientY;
                var sW = im.offsetWidth, sH = im.offsetHeight;
                var sL = im.offsetLeft + (d.indexOf('w') >= 0 ? sW : 0);
                var dd = h.dataset.d;
                function mv(ev) {
                    var dx = ev.clientX - sx3, dy = ev.clientY - sy3;
                    var nW = sW, nH = sH;
                    if (dd.indexOf('e') >= 0) nW = sW + dx;
                    if (dd.indexOf('w') >= 0) nW = sW - dx;
                    if (dd.indexOf('s') >= 0) nH = sH + dy;
                    if (dd.indexOf('n') >= 0) nH = sH - dy;
                    nW = Math.max(30, Math.round(nW));
                    nH = Math.max(20, Math.round(nH));
                    // 直接改显示宽高（自由压扁/拉伸，不锁比例）；不重采样，保留原图质量
                    im.style.width = nW + 'px';
                    im.style.height = nH + 'px';
                    state.zoomW = nW; state.zoomH = nH;
                    positionZoomHandles();
                    updateSizeInfo();
                }
                function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
                document.addEventListener('mousemove', mv); document.addEventListener('mouseup', up);
            });
        });
        ui.zoomHandles = handles;
        positionZoomHandles();
    }
    function positionZoomHandles() {
        var im = state.img, stage = document.getElementById('img-ep-stage');
        if (!im || !stage || !ui.zoomHandles) return;
        var r = im.getBoundingClientRect(), s = stage.getBoundingClientRect();
        var left = r.left - s.left, top = r.top - s.top, w = r.width, h = r.height;
        var pos = { nw:[left-6, top-6], ne:[left+w-6, top-6], se:[left+w-6, top+h-6], sw:[left-6, top+h-6] };
        ui.zoomHandles.forEach(function (hEl) {
            var p = pos[hEl.dataset.d]; if (!p) return;
            hEl.style.left = p[0] + 'px'; hEl.style.top = p[1] + 'px';
        });
    }
    function hideZoomHandles() {
        if (ui.zoomHandles) { ui.zoomHandles.forEach(function (h) { if (h.parentNode) h.parentNode.removeChild(h); }); }
        ui.zoomHandles = null;
        state.zoomW = state.zoomH = 0;
    }

    // ── 打开面板 ──
    function openCropFromUrl(url, replaceCb) {
        ensurePanel();
        state.replaceCb = replaceCb || null;
        state.mode = 'crop';
        // 位置：编辑浮窗附近/屏幕中央偏右
        ui.panel.style.left = 'calc(50% + 40px)';
        ui.panel.style.top = '80px';
        ui.panel.style.display = 'flex';
        fetchBlob(url, function (blob) {
            state.file = blob;
            if (state._url) { try { URL.revokeObjectURL(state._url); } catch (e) {} }
            var u = URL.createObjectURL(blob);
            state._url = u;
            var im = new Image();
            im.onload = function () {
                state.naturalW = im.naturalWidth; state.naturalH = im.naturalHeight;
                showImage(u);
                applyMode();
            };
            im.src = u;
        });
    }

    // ── 应用并保存（裁剪或缩放）──
    function applyAndSave() {
        var im = state.img; if (!im) return;
        var stage = document.getElementById('img-ep-stage');
        var nw = im.naturalWidth, nh = im.naturalHeight;
        if (!nw) { close(); return; }
        // 基准显示尺寸用未 transform 的布局尺寸（transform scale 不影响布局）
        var dispW = im.offsetWidth || nw, dispH = im.offsetHeight || nh;
        var scaleX = nw / dispW, scaleY = nh / dispH;

        var sx = 0, sy = 0, sw = nw, sh = nh; // 源(原像素)
        var outW = nw, outH = nh;             // 输出像素（默认原图）
        var sizeSuffix = '';                  // 显示尺寸后缀（=WxH），zoom 时写入 markdown

        if (state.mode === 'crop' && ui.cropBox) {
            // 裁剪：源区域=裁剪框（基于显示的图区域换算到原像素）
            var imgRect = im.getBoundingClientRect();
            var cr = ui.cropBox.getBoundingClientRect();
            // scaleX/Y 用 图显示尺寸 到 原像素；但 getBoundingClientRect 已被 transform 影响，用 offsetWidth 更稳
            var dx = (cr.left - imgRect.left) * scaleX;
            var dy = (cr.top - imgRect.top) * scaleY;
            var dw = cr.width * scaleX, dh = cr.height * scaleY;
            sx = Math.max(0, dx); sy = Math.max(0, dy);
            sw = Math.min(nw, dx + dw) - sx;
            sh = Math.min(nh, dy + dh) - sy;
            if (sw >= 4 && sh >= 4) { outW = Math.round(sw); outH = Math.round(sh); }
        } else if (state.mode === 'zoom') {
            // 缩放：保留原图全分辨率（不降像素），只记录显示尺寸写入 markdown；渲染时按该尺寸显示
            outW = nw; outH = nh;
            if (state.zoomW && state.zoomH) {
                sizeSuffix = '=' + Math.round(state.zoomW) + 'x' + Math.round(state.zoomH);
            }
        }

        // 重采样（裁剪时真正裁剪；缩放时输出原图=不重采样降质）
        var canvas = document.createElement('canvas');
        canvas.width = outW; canvas.height = outH;
        var ctx = canvas.getContext('2d');
        ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
        try { ctx.drawImage(im, sx, sy, sw, sh, 0, 0, outW, outH); }
        catch (e) { close(); return; }
        canvas.toBlob(function (blob) {
            if (!blob) { close(); return; }
            uploadBlob(blob, 'edited.png', 'image/png', function (newUrl) {
                var rUrl = newUrl + (sizeSuffix ? ' ' + sizeSuffix : '');
                if (state.replaceCb) { try { state.replaceCb(rUrl); } catch (e) {} }
                close();
            });
        }, 'image/png', 0.92);
    }

    function close() {
        if (ui.panel) ui.panel.style.display = 'none';
        hideCropFrame();
        hideZoomHandles();
        if (state._url) { try { URL.revokeObjectURL(state._url); } catch (e) {} }
        state._url = null;
        state.img = state.file = state.replaceCb = null;
    }

    // ── 点击图片放大预览（消息区）──
    function openPreview(src) {
        var layer = document.getElementById('img-preview-layer');
        if (!layer) {
            layer = document.createElement('div');
            layer.id = 'img-preview-layer';
            layer.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:100000;display:none;align-items:center;justify-content:center;cursor:zoom-out;';
            layer.innerHTML = '<img id="img-preview-img" style="max-width:90vw;max-height:88vh;border-radius:8px;">';
            document.body.appendChild(layer);
            layer.addEventListener('click', function (e) { if (e.target === layer) imageTools.closePreview(); });
        }
        document.getElementById('img-preview-img').src = src;
        layer.style.display = 'flex';
    }
    function closePreview() {
        var l = document.getElementById('img-preview-layer');
        if (l) l.style.display = 'none';
    }

    return {
        uploadBlob: uploadBlob,
        uploadFileRaw: uploadFileRaw,
        chooseFile: chooseFile,
        openCropFromUrl: openCropFromUrl,
        close: close,
        openPreview: openPreview,
        closePreview: closePreview
    };
})();

window.imageTools = imageTools;
window.openImagePreview = function (src) { imageTools.openPreview(src); };
