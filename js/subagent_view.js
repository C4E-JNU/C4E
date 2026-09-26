// ==================== 子智能体查看器（可拖拽 / 四角缩放的浮窗） ====================
// 与笔记浮窗（.floating-editor）同款外观与交互：拖动标题栏移动、四角缩放、× 关闭。
// 内容 = 该子智能体的目标、状态、结论摘要、以及它的思考/工具调用/结果全过程。

(function () {
    var _el = null;

    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function _ensure() {
        if (_el && document.body.contains(_el)) return _el;
        var d = document.createElement('div');
        d.className = 'floating-editor';
        d.id = 'subagent-viewer';
        d.style.width = '720px';
        d.style.height = '520px';
        d.style.left = '260px';
        d.style.top = '110px';
        d.innerHTML =
            '<div class="fe-header" id="sv-header">' +
                '<span class="fe-title" id="sv-title">🤖 子智能体</span>' +
                '<button class="fe-close" id="sv-close" title="关闭">×</button>' +
            '</div>' +
            '<div id="sv-body" style="flex:1;overflow:auto;padding:0.8rem 1rem;font-size:0.86rem;line-height:1.6;"></div>';
        document.body.appendChild(d);

        // 拖动
        var hd = d.querySelector('#sv-header');
        hd.style.cursor = 'move';
        hd.addEventListener('mousedown', function (e) {
            if (e.target.closest('.fe-close')) return;
            var rect = d.getBoundingClientRect();
            var offX = e.clientX - rect.left, offY = e.clientY - rect.top;
            function mv(ev) {
                d.style.left = Math.max(-80, ev.clientX - offX) + 'px';
                d.style.top = Math.max(0, ev.clientY - offY) + 'px';
            }
            function up() {
                document.removeEventListener('mousemove', mv);
                document.removeEventListener('mouseup', up);
            }
            document.addEventListener('mousemove', mv);
            document.addEventListener('mouseup', up);
            e.preventDefault();
        });
        // 四角缩放（同浮动编辑器）
        ['se', 'nw', 'ne', 'sw'].forEach(function (c) {
            var h = document.createElement('div');
            h.className = 'fe-resize ' + c;
            h.dataset.corner = c;
            h.style.cssText = 'position:absolute;width:14px;height:14px;z-index:5;' +
                (c === 'se' ? 'right:2px;bottom:2px;cursor:nwse-resize;' :
                 c === 'nw' ? 'left:2px;top:2px;cursor:nwse-resize;' :
                 c === 'ne' ? 'right:2px;top:2px;cursor:nesw-resize;' :
                              'left:2px;bottom:2px;cursor:nesw-resize;');
            d.appendChild(h);
            h.addEventListener('mousedown', function (e) {
                e.preventDefault(); e.stopPropagation();
                var sx = e.clientX, sy = e.clientY;
                var sW = d.offsetWidth, sH = d.offsetHeight, sL = d.offsetLeft, sT = d.offsetTop;
                function mv(ev) {
                    var dx = ev.clientX - sx, dy = ev.clientY - sy;
                    var nW = sW, nH = sH, nL = sL, nT = sT;
                    if (c === 'se') { nW = sW + dx; nH = sH + dy; }
                    if (c === 'nw') { nW = sW - dx; nH = sH - dy; nL = sL + dx; nT = sT + dy; }
                    if (c === 'ne') { nW = sW + dx; nH = sH - dy; nT = sT + dy; }
                    if (c === 'sw') { nW = sW - dx; nL = sL + dx; nH = sH + dy; }
                    if (nW < 320) nW = 320;
                    if (nH < 220) nH = 220;
                    d.style.width = nW + 'px';
                    d.style.height = nH + 'px';
                    d.style.left = nL + 'px';
                    d.style.top = nT + 'px';
                }
                function up() {
                    document.removeEventListener('mousemove', mv);
                    document.removeEventListener('mouseup', up);
                }
                document.addEventListener('mousemove', mv);
                document.addEventListener('mouseup', up);
            });
        });
        d.querySelector('#sv-close').addEventListener('click', function () { d.classList.remove('open'); });
        _el = d;
        return d;
    }

    function _stepHtml(st) {
        var t = st.type || '';
        if (t === 'thinking') {
            return '<div style="margin:.4rem 0;"><div style="font-weight:600;color:var(--primary);">🧠 子智能体思考</div>' +
                   '<div style="white-space:pre-wrap;background:var(--light,#f6f8fa);border-radius:6px;padding:.4rem .6rem;margin-top:.2rem;">' +
                   _esc(st.text || '') + '</div></div>';
        }
        if (t === 'tool_call') {
            return '<div style="margin:.4rem 0;"><div style="font-weight:600;">🔧 调用 ' + _esc(st.name || '') + '</div>' +
                   '<pre style="white-space:pre-wrap;background:var(--light,#f6f8fa);border-radius:6px;padding:.4rem .6rem;margin:.2rem 0 0;">' +
                   _esc(st.input || '') + '</pre></div>';
        }
        if (t === 'tool_result') {
            return '<div style="margin:.4rem 0;"><div style="font-weight:600;">👁️ ' + _esc(st.name || '') + ' 结果</div>' +
                   '<pre style="white-space:pre-wrap;max-height:220px;overflow:auto;background:var(--light,#f6f8fa);border-radius:6px;padding:.4rem .6rem;margin:.2rem 0 0;">' +
                   _esc(st.content || '') + '</pre></div>';
        }
        if (t === 'notice') {
            return '<div style="margin:.4rem 0;color:var(--gray);">⚠️ ' + _esc(st.text || '') + '</div>';
        }
        return '';
    }

    function open(info) {
        // info: {index, goal, context, ok, summary, error, steps[], rounds, seconds, running(bool)}
        var d = _ensure();
        d.querySelector('#sv-title').textContent =
            '🤖 子智能体 ' + ((info.index || 0) + 1) + (info.running ? ' · 运行中…' : (info.ok ? ' · ✅ 完成' : ' · ⚠️ 失败'));
        var badge = info.running
            ? '<span style="color:#e6a23c;">● 运行中…</span>'
            : (info.ok ? '<span style="color:#2e9e4f;">✅ 完成</span>' : '<span style="color:#e02020;">⚠️ 失败</span>');
        var steps = info.steps || [];
        var nThink = steps.filter(function (s) { return s.type === 'thinking'; }).length;
        var nTool = steps.filter(function (s) { return s.type === 'tool_call'; }).length;
        var html = '<div style="margin-bottom:.5rem;">' + badge +
            (info.seconds ? ' <span style="color:var(--gray);">· 用时 ' + info.seconds + 's</span>' : '') +
            (info.rounds ? ' <span style="color:var(--gray);">· ' + info.rounds + ' 轮</span>' : '') +
            (nTool ? ' <span style="color:var(--gray);">· 工具 ' + nTool + ' 次</span>' : '') + '</div>';

        // 1) 输入：主智能体交给它的任务 + 背景上下文（这就是"和子智能体的对话输入"）
        html += '<div style="margin:.6rem 0;padding:.5rem .7rem;border:1px solid var(--border-color,#ddd);border-radius:8px;">' +
                '<div style="font-weight:600;margin-bottom:.25rem;">📥 输入（主智能体派给它的任务）</div>' +
                '<div style="margin-bottom:.3rem;"><b>目标：</b>' + _esc(info.goal || '') + '</div>' +
                '<div style="color:var(--gray);white-space:pre-wrap;">' +
                (info.context ? ('<b style="color:var(--text-color,#333);">上下文：</b>' + _esc(info.context)) : '（无额外上下文）') +
                '</div></div>';
        if (info.error) {
            html += '<div style="color:#e02020;margin-bottom:.5rem;">错误：' + _esc(info.error) + '</div>';
        }
        // 2)+3) 过程：思考 / 工具调用 / 工具结果
        if (steps.length) {
            html += '<div style="margin-top:.6rem;font-weight:600;">🧠 思考过程与工具调用（' + steps.length + ' 步，思考 ' + nThink + ' 段）</div>' +
                    '<div style="border:1px solid var(--border-color,#ddd);border-radius:8px;padding:.4rem .6rem;margin-top:.3rem;">' +
                    steps.map(_stepHtml).join('') + '</div>';
        } else if (info.running) {
            html += '<div style="color:var(--gray);margin-top:.5rem;">子智能体正在独立上下文里工作（它自己调工具、自己思考）；' +
                    '完成后这里会显示它的完整思考与工具调用过程。</div>';
        }
        // 4) 结论：它回给主智能体的摘要
        if (info.summary) {
            var rendered = (typeof formatMessage === 'function') ? formatMessage(info.summary) : _esc(info.summary);
            html += '<div style="margin:.7rem 0;padding:.6rem .8rem;border-left:3px solid var(--primary);' +
                    'background:var(--light,#f6f8fa);border-radius:0 6px 6px 0;"><div style="font-weight:600;margin-bottom:.3rem;">✅ 结论摘要（回给主智能体）</div>' +
                    '<div class="message-content">' + rendered + '</div></div>';
        }
        d.querySelector('#sv-body').innerHTML = html;
        d.classList.add('open');
    }

    window.subagentViewer = { open: open };

    // 持久化卡片（刷新/切换对话后仍在）：事件委托，从渲染时注册的数据里取回全过程
    document.addEventListener('click', function (e) {
        var el = e.target && e.target.closest ? e.target.closest('.subagent-open') : null;
        if (!el) return;
        var id = el.dataset ? el.dataset.payload : '';
        var data = (window.__saData || {})[id];
        if (data) open(data);
    });
})();
