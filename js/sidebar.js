        // ==================== 历史侧栏 ↔ 聊天 左右占比（宽度持久化） ====================
        // ★ 三件事共用一个判据，但"谁在动"是客观不同的，所以行为也不同：
        //   · 拖分隔条 = 用户主动要更多侧栏 → 拖过"对话区容忍最小"就整个让位（专注对话管理）
        //   · 窗口变小 = 用户没说要藏对话  → 侧栏让位（自动收起），对话区保住
        var _SB_KEY = 'c4eai-sidebar-width';   // localStorage: 历史侧栏宽度(px)
        var _SB_DEFAULT = 300;
        var _SB_MIN = 240;
        var _CHAT_MIN = 480;                   // 对话区最小可呼吸宽度（= variables.css --chat-min；低于它侧栏让位，宽度驱动）
        var _SB_FOCUS_SLACK = 24;              // 拖过分界多少像素才算"我要全屏看对话管理"（防边界抖）

        var _sbFocusBack = 0;                  // 进专注前的侧栏宽度
        var _sbAutoBack = 0;                   // 被自动收起前的侧栏宽度
        var _sbFocusPending = null;            // 专注期间点过、但没渲染的对话（退出时补）

        function _sbEl() { return document.getElementById('chat-sidebar'); }
        function _sbSection() { return document.querySelector('.chat-section'); }
        function _sbResizer() { return document.getElementById('sidebar-resizer'); }

        // 读取持久化宽度（默认 _SB_DEFAULT）
        function _sbSavedWidth() {
            try {
                var s = parseInt(localStorage.getItem(_SB_KEY), 10);
                if (s && s >= _SB_MIN) return s;
            } catch (e) {}
            return _SB_DEFAULT;
        }
        function _sbStoreWidth(w) {
            try { localStorage.setItem(_SB_KEY, String(Math.round(w))); } catch (e) {}
        }
        // 宽度唯一真源 = --sb-w（variables.css）。唯一合法的写宽度入口：
        // 收起/展开/恢复只切类，永不写 inline width（体检 P3 收口，消灭「先 1/4 再变肥」两段跳）。
        function _sbSetW(w) {
            try { document.documentElement.style.setProperty('--sb-w', Math.round(w) + 'px'); } catch (e) {}
        }
        var _sbDrawerIntent = false;           // 窄空间下用户主动开抽屉的意图：尺寸变化前不自动收回
        // 侧栏可用最大宽度 = 区域宽 - 对话区容忍最小宽度
        function _sbMaxWidth() {
            var sec = _sbSection();
            return Math.max(_SB_MIN + 1, (sec ? sec.clientWidth : 1200) - _CHAT_MIN);
        }
        // 分隔条应处的 x（相对 chat-section 左边）——自然留白的中点
        function _sbDividerX() {
            var sb = _sbEl(), sec = _sbSection();
            if (!sb || !sec) return 0;
            var sr = sb.getBoundingClientRect(), cr = sec.getBoundingClientRect();
            var g = 0;
            try { g = parseFloat(getComputedStyle(sec).columnGap) || 0; } catch (e) {}
            return (sr.right - cr.left) + g / 2;
        }
        // 定位分隔条：只写 left。
        // ★ 显示与否一律交给 CSS（唯一真相）：
        //     .chat-sidebar:not(.collapsed) ~ .sidebar-resizer { display: block }
        //     手机断点 .sidebar-resizer { display: none !important }
        //   JS 再写 inline display 就会有残留：写死过 none 之后，CSS 的 block 永远盖不回来 ——
        //   "拖动键有时会消失"就是这么来的（inline 优先级高于样式表，除 !important 外）。
        function _sbPlaceDivider() {
            var rs = _sbResizer(), sb = _sbEl(), sec = _sbSection();
            if (!rs || !sb || !sec || !sec.clientWidth) return;   // 区域没显示：什么都不用做
            var x = _sbDividerX();
            if (!isFinite(x) || x <= 0) return;
            rs.style.left = Math.min(x, sec.clientWidth) + 'px';
        }

        // 窗口是不是最小化着：最小化时 WebView2 报的视口是假的（本机实测 157x25），
        // 据此做布局决定是错的（会把侧栏自动收起、还会命中手机断点）。
        var _sbMinimized = false;
        var _sbMinPoll = null;
        function _sbSyncMinimized() {
            try {
                if (!window.pywebview || !pywebview.api || !pywebview.api.is_minimized) return;
                pywebview.api.is_minimized().then(function (v) {
                    var was = _sbMinimized;
                    _sbMinimized = !!v;
                    if (_sbMinimized) {
                        // 最小化中：页面自己判断不出"什么时候回来"（恢复时不一定有 resize 事件），
                        // 所以最小化期间才轮询宿主状态，一恢复立刻停 —— 纯查询接口，不涉及 GUI 线程。
                        if (!_sbMinPoll) {
                            _sbMinPoll = setInterval(function () {
                                try { pywebview.api.is_minimized().then(function (v2) {
                                    if (v2) return;
                                    clearInterval(_sbMinPoll); _sbMinPoll = null;
                                    _sbMinimized = false;
                                    _sbFitToWindow();
                                }).catch(function () {}); } catch (e) {}
                            }, 800);
                        }
                    } else if (was) {
                        _sbFitToWindow();                  // 恢复回来，按真尺寸重新贴合一次
                    }
                }).catch(function () {});
            } catch (e) {}
        }

        // 展开 / 收起（不再有 50% 全屏档）
        function toggleSidebar() {
            var sb = _sbEl(), sec = _sbSection(), btn = document.getElementById('sidebar-toggle-btn');
            if (!sb || !sec || !btn) return;
            if (_sbInFocus()) { _sbExitFocus(true); return; }
            btn.classList.remove('full');
            if (sb.classList.contains('collapsed')) {
                // 收起 → 展开：回到用户拖好的宽度
                sb.classList.remove('collapsed');
                _sbSetW(_sbSavedWidth());
                btn.classList.remove('active');
            } else {
                // 展开 → 收起
                sb.classList.add('collapsed');
                btn.classList.add('active');
            }
            _sbPlaceDivider();
            setTimeout(_sbPlaceDivider, 350);        // 宽度过渡结束后再定位
        }
        // 只有"用户自己按按钮"才算用户意图；自动收起打的是 autoCollapsed 记号，两者不混
        function _sbUserToggle() {
            var sb = _sbEl();
            if (sb) {
                delete sb.dataset.autoCollapsed;
                sb.dataset.userCollapsed = sb.classList.contains('collapsed') ? '0' : '1';
                var btn = document.getElementById('sidebar-toggle-btn');
                if (btn) btn.classList.toggle('active', sb.dataset.userCollapsed === '1');
            }
            toggleSidebar();
        }

        // ==================== 专注对话管理（只看条目和笔记） ====================
        function _sbInFocus() {
            var sec = _sbSection();
            return !!(sec && sec.classList.contains('focus-manage'));
        }
        function _sbEnterFocus() {
            var sb = _sbEl(), sec = _sbSection();
            if (!sb || !sec || _sbInFocus()) return;
            _sbFocusBack = sb.getBoundingClientRect().width || _sbSavedWidth();
            sec.classList.add('focus-manage');       // CSS：对话区 display:none
            /* 铺满宽度由 CSS 类管：.chat-section.focus-manage .chat-sidebar{width:100%}（layout.css），不抢 --sb-w */
            _sbPlaceDivider();                       // 拖动键留在右边界，随时能拖回来
        }
        function _sbExitFocus(loadPending) {
            var sb = _sbEl(), sec = _sbSection();
            if (!sb || !sec || !_sbInFocus()) return;
            sec.classList.remove('focus-manage');
            _sbSetW(Math.min(_sbFocusBack || _SB_DEFAULT, _sbMaxWidth()));
            _sbStoreWidth(_sbSavedWidth());   // _sbSetW 刚写过，存档与 --sb-w 同步
            _sbPlaceDivider();
            var pending = _sbFocusPending;
            _sbFocusPending = null;
            if (loadPending && pending && typeof switchToBranch === 'function') {
                switchToBranch(pending);             // 退出专注才真正加载（专注期间点过的那条）
            }
        }

        // 窗口尺寸变化时的自适应：放不下"侧栏最小 + 对话区容忍最小"→ 自动收起侧栏，把空间全给对话。
        // 窗口重新变宽 → 自动弹回来（只弹"我自动收的"那次，用户自己收的不动）。
        function _sbFitToWindow() {
            var sb = _sbEl(), sec = _sbSection();
            if (!sec || !sec.clientWidth) return;   // 对话区未布局（落地页 display:none）时宽度不可测，不裁决
            _sbSyncMinimized();                            // 顺手问一次（异步返回后若已恢复会再调一次本函数）
            if (_sbMinimized) return;                      // 最小化中：尺寸是假的，别做布局决定
            if (!sb || !sec || !sec.clientWidth) return;   // 落地页/未布局：等下一次尺寸变化
            if (!_sbInFocus()) {
                if ((function () {
                        // 窄空间模式（宽度驱动，与外壳/设备无关）：剩余对话区 < _CHAT_MIN → 挂 .narrow，
                        // 侧栏让位（自动收起），重开 = 覆盖式抽屉（layout.css 末段）
                        // 判据 = 侧栏占位宽度（--sb-w 真源）：collapsed 是结果不是输入；
                        // 按收起后 0 算 → 自动收起瞬间剩余宽度"够用" → 窄空间被误判 false 关抽屉
                        var _narrow = (sec.clientWidth - _sbSavedWidth()) < _CHAT_MIN;
                        sec.classList.toggle('narrow', _narrow);
                        if (!_narrow) { sec.classList.remove('drawer-open'); _sbDrawerIntent = false; }
                        try { document.body.classList.toggle('narrow-space', _narrow); } catch (e) {}
                        return _narrow && !_sbDrawerIntent;
                    })()) {
                    if (!sb.classList.contains('collapsed')) {
                        _sbAutoBack = sb.getBoundingClientRect().width || _sbSavedWidth();
                        sb.dataset.autoCollapsed = '1';
                        sb.classList.add('collapsed');
                    }
                } else if (sb.dataset.autoCollapsed === '1' && sb.dataset.userCollapsed !== '1') {
                    delete sb.dataset.autoCollapsed;
                    sb.classList.remove('collapsed');
                    // 回到"被自动收起之前"的那个宽度（不是上一次持久化的值 —— 否则窗口一缩一放，
                    // 侧栏宽度就自己跳成别的数）
                    _sbSetW(Math.min(_sbAutoBack || _sbSavedWidth(), _sbMaxWidth()));
                    _sbStoreWidth(_sbSavedWidth());   // _sbSetW 刚写过，存档与 --sb-w 同步
                    _sbAutoBack = 0;
                }
                if (!sb.classList.contains('collapsed')) {
                    var mw = _sbMaxWidth(), cur = _sbSavedWidth();
                    if (cur > mw) { _sbSetW(mw); _sbStoreWidth(mw); }
                }
            }
            _sbPlaceDivider();
            // ★ 必须再定位一次：进/出 collapsed 会走宽度过渡，过渡途中取到的侧栏宽度是动画中间
            //   值（接近 0），_sbPlaceDivider 里的 x<=0 守卫会直接 return —— 于是拖动键停在旧坐标
            //   上再也不动（"最小化恢复后拖动键没能正常化"就是这个）。等过渡结束再摆一次。
            setTimeout(_sbPlaceDivider, 360);
        }

        // 初始化：绑定 + 恢复持久化宽度 + 分隔条拖动
        (function _initSidebarSplit() {
            var sb = _sbEl();
            var btn = document.getElementById('sidebar-toggle-btn');
            if (btn) btn.addEventListener('click', _sbUserToggle);
            if (sb && !sb.classList.contains('collapsed')) {
                _sbSetW(Math.min(_sbSavedWidth(), _sbMaxWidth()));
            }

            var rs = _sbResizer(), sec = _sbSection();
            if (sb && rs && sec) {
                rs.addEventListener('mousedown', function (e) {
                    if (e.button !== 0) return;
                    e.preventDefault();
                    var startX = e.clientX;
                    // 专注态下从"上限"起步（继续往左拖才是回收）
                    var startW = _sbInFocus() ? _sbMaxWidth()
                        : (sb.classList.contains('collapsed') ? 0
                           : sb.getBoundingClientRect().width);
                    var prevTr = sb.style.transition;
                    sb.style.transition = 'none';     // 拖动中禁用过渡，避免卡顿
                    rs.classList.add('rs-active');
                    document.body.style.userSelect = 'none';
                    function mv(ev) {
                        var base = startW + (ev.clientX - startX), limit = _sbMaxWidth();
                        if (!_sbInFocus()) {
                            if (base - limit > _SB_FOCUS_SLACK) { _sbEnterFocus(); return; }
                            _sbSetW(Math.max(_SB_MIN, Math.min(limit, base)));
                        } else if (base - limit < -_SB_FOCUS_SLACK) {
                            _sbExitFocus(false);                       // 先退专注，宽度在 mouseup 时结算
                            startW = sb.getBoundingClientRect().width;
                            startX = ev.clientX;
                            return;
                        }
                        if (!_sbInFocus()) rs.style.left = _sbDividerX() + 'px';
                    }
                    function up() {
                        document.removeEventListener('mousemove', mv);
                        document.removeEventListener('mouseup', up);
                        document.body.style.userSelect = '';
                        sb.style.transition = prevTr;
                        rs.classList.remove('rs-active');
                        if (!_sbInFocus()) {
                            var fw = sb.getBoundingClientRect().width;
                            if (fw && fw >= _SB_MIN) _sbStoreWidth(fw);
                            if (_sbFocusPending) {                     // 刚从专注拖回来 → 补加载
                                var pd = _sbFocusPending; _sbFocusPending = null;
                                if (typeof switchToBranch === 'function') switchToBranch(pd);
                            }
                        }
                        _sbPlaceDivider();
                    }
                    document.addEventListener('mousemove', mv);
                    document.addEventListener('mouseup', up);
                });

                // 尺寸变化（含最小化恢复、进出全屏、落地页 ↔ 对话页切换）都重新贴合
                if (window.ResizeObserver) {
                    new ResizeObserver(function () { _sbFitToWindow(); }).observe(sec);
                }
                window.addEventListener('resize', _sbFitToWindow);
                setTimeout(_sbFitToWindow, 60);
                setTimeout(_sbFitToWindow, 300);
                _sbSyncMinimized();                        // 启动先问一次，别等第一次 resize
            _sbFitToWindow();                        // ★ 启动即定窄空间归属（窗口一开就是 700px 也得进 narrow）
            window._sbRefit = _sbFitToWindow;       // 视图切换（进/出对话）后外部可触发复测
            }
        })();

        // ==================== 召唤对话管理（点顶栏 C4E logo；宽度驱动，全外壳通用；事件委托版，DOM 时序无关） ====================
        (function _sbBindDrawerToggle() {
            document.addEventListener('click', function (e) {
                var sec = _sbSection();
                if (!sec) return;
                var t = e.target;
                // 点顶栏 C4E logo = 召唤对话管理：窄空间开/关抽屉；桌面把收起的侧栏展开
                if (t.closest && t.closest('.c4e-art-logo')) {
                    e.preventDefault(); e.stopPropagation();
                    if (sec.classList.contains('narrow')) {
                        _sbDrawerIntent = sec.classList.toggle('drawer-open');
                    } else {
                        var _sbx = _sbEl();
                        if (_sbx && _sbx.classList.contains('collapsed')) toggleSidebar();
                    }
                    return;
                }
                // 点遮罩（抽屉外任意处）关闭
                if (!sec.classList.contains('drawer-open')) return;
                if (t.closest && (t.closest('#chat-sidebar') || t.closest('.notes-panel') || t.closest('#database-panel'))) return;
                sec.classList.remove('drawer-open');
                _sbDrawerIntent = false;
            });
        })();


        // 点击 C4E 图标折叠侧边栏头部搜索/过滤区
        function toggleSidebarHeader() {
            var sidebar = document.getElementById('chat-sidebar');
            sidebar.classList.toggle('header-collapsed');
        }


        // ==================== 侧边栏初始化 ====================
        function initSidebar() {
            historySearch.addEventListener('input', (e) => {
                state.sidebar.searchQuery = e.target.value.toLowerCase();
                updateHistoryList();
            });

            filterButtons.forEach(btn => {
                btn.addEventListener('click', () => {
                    // 跳过笔记按钮，笔记有独立的点击处理
                    if (btn.id === 'notes-toggle-btn') return;
                    filterButtons.forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    state.sidebar.currentFilter = btn.dataset.filter;
                    updateHistoryList();
                });
            });

            updateHistoryList();
        }

        // ==================== 笔记面板 ====================
        // 初始化时绑定笔记事件（在 api.js init 末尾调用）
        function initNotesPanel() {
            var btn = document.getElementById('notes-toggle-btn');
            var addBtn = document.getElementById('notes-add-btn');
            var scopeBtn = document.getElementById('notes-scope-btn');
            if (scopeBtn) scopeBtn.addEventListener('click', function () {
                _notesScope = (_notesScope === 'all') ? 'branch' : 'all';
                scopeBtn.textContent = (_notesScope === 'all') ? '📄 本对话' : '📚 全部';
                renderNotesPanel();
            });
            if (btn) btn.addEventListener('click', toggleNotesPanel);
            if (addBtn) addBtn.addEventListener('click', function() {
                addNoteCard(state.currentBranchId, '', '');
            });
        }

        // ==================== 笔记浮动窗口（位置/大小前端持久化） ====================
        var _notesWinKey = 'c4eai-notes-window';   // localStorage 存储 {x,y,w,h}
        var _notesWinInited = false;
        var _NP_MIN_W = 320, _NP_MIN_H = 280;        // 与 CSS min-width/min-height 配套

        function _notesEl() { return document.getElementById('notes-panel'); }
        function _notesBtn() { return document.getElementById('notes-toggle-btn'); }

        // 保存当前几何到 localStorage
        function _saveNotesRect() {
            var np = _notesEl(); if (!np) return;
            try {
                localStorage.setItem(_notesWinKey, JSON.stringify({
                    x: parseInt(np.style.left, 10) || 0,
                    y: parseInt(np.style.top, 10) || 0,
                    w: np.offsetWidth,
                    h: np.offsetHeight
                }));
            } catch (e) {}
        }

        // 依据几何设置并夹紧到可视区（兼容全屏/非全屏/窗口缩放）
        function _applyNotesRect(rect) {
            var np = _notesEl(); if (!np || !rect) return;
            var vw = window.innerWidth, vh = window.innerHeight, pad = 8;
            var w = Math.min(Math.max(rect.w, _NP_MIN_W), vw - 2 * pad);
            var h = Math.min(Math.max(rect.h, _NP_MIN_H), vh - 2 * pad);
            var x = Math.min(Math.max(rect.x, pad), Math.max(pad, vw - w - pad));
            var y = Math.min(Math.max(rect.y, pad), Math.max(pad, vh - h - pad));
            np.style.left = x + 'px'; np.style.top = y + 'px';
            np.style.width = w + 'px'; np.style.height = h + 'px';
        }

        // 恢复保存的几何；无保存或已越界则首次居中
        function _resolveNotesRect() {
            var vw = window.innerWidth, vh = window.innerHeight;
            var saved = null;
            try { saved = JSON.parse(localStorage.getItem(_notesWinKey) || 'null'); } catch (e) {}
            if (saved && saved.w && saved.h) return saved;
            var w = Math.min(480, vw - 24), h = Math.min(560, vh - 24);
            return { x: Math.max(8, (vw - w) / 2), y: Math.max(8, (vh - h) / 2), w: w, h: h };
        }

        // 绑定一次：标题栏拖动、四角缩放、关闭按钮、窗口缩放重夹紧
        function _initNotesFloatWindow() {
            if (_notesWinInited) return;
            var np = _notesEl(); if (!np) return;
            _notesWinInited = true;

            var hd = np.querySelector('.notes-panel-header');
            if (hd) {
                hd.addEventListener('mousedown', function (e) {
                    if (e.button !== 0) return;
                    if (e.target.closest('button')) return;   // 点 ＋ / × 不拖动
                    var r = np.getBoundingClientRect();
                    var offX = e.clientX - r.left, offY = e.clientY - r.top;
                    function mv(ev) {
                        np.style.left = Math.max(-60, ev.clientX - offX) + 'px';
                        np.style.top = Math.max(0, ev.clientY - offY) + 'px';
                    }
                    function up() {
                        document.removeEventListener('mousemove', mv);
                        document.removeEventListener('mouseup', up);
                        document.body.style.userSelect = '';
                        _saveNotesRect();
                    }
                    document.body.style.userSelect = 'none';
                    document.addEventListener('mousemove', mv);
                    document.addEventListener('mouseup', up);
                    e.preventDefault();
                });
            }

            if (!np.querySelector('.notes-resize')) {
                var corners = ['se', 'nw', 'ne', 'sw'];
                corners.forEach(function (c) {
                    var d = document.createElement('div');
                    d.className = 'notes-resize ' + c;
                    d.dataset.corner = c;
                    np.appendChild(d);
                    d.addEventListener('mousedown', function (e) {
                        e.preventDefault(); e.stopPropagation();
                        var sx = e.clientX, sy = e.clientY;
                        var r0 = np.getBoundingClientRect();
                        var sW = r0.width, sH = r0.height, sL = r0.left, sT = r0.top;
                        function mv(ev) {
                            var dx = ev.clientX - sx, dy = ev.clientY - sy;
                            var nW = sW, nH = sH, nL = sL, nT = sT;
                            if (c.indexOf('e') >= 0) nW = sW + dx;
                            if (c.indexOf('s') >= 0) nH = sH + dy;
                            if (c.indexOf('w') >= 0) { nW = sW - dx; nL = sL + dx; }
                            if (c.indexOf('n') >= 0) { nH = sH - dy; nT = sT + dy; }
                            if (nW < _NP_MIN_W) { if (c.indexOf('w') >= 0) nL = sL + (sW - _NP_MIN_W); nW = _NP_MIN_W; }
                            if (nH < _NP_MIN_H) { if (c.indexOf('n') >= 0) nT = sT + (sH - _NP_MIN_H); nH = _NP_MIN_H; }
                            if (c.indexOf('w') >= 0 && nL < 0) { nW += nL; nL = 0; }
                            if (c.indexOf('n') >= 0 && nT < 0) { nH += nT; nT = 0; }
                            np.style.width = nW + 'px'; np.style.height = nH + 'px';
                            np.style.left = nL + 'px'; np.style.top = nT + 'px';
                        }
                        function up() {
                            document.removeEventListener('mousemove', mv);
                            document.removeEventListener('mouseup', up);
                            document.body.style.userSelect = '';
                            _saveNotesRect();
                        }
                        document.body.style.userSelect = 'none';
                        document.addEventListener('mousemove', mv);
                        document.addEventListener('mouseup', up);
                    });
                });
            }

            var closeBtn = document.getElementById('notes-close-btn');
            if (closeBtn) closeBtn.addEventListener('click', _closeNotesFloat);

            // 窗口缩放（含进出全屏）时保持浮窗留在可视区内
            window.addEventListener('resize', function () {
                if (!np.classList.contains('active')) return;
                var r = np.getBoundingClientRect();
                _applyNotesRect({ x: r.left, y: r.top, w: r.width, h: r.height });
            });
        }

        function _openNotesFloat() {
            var np = _notesEl(), btn = _notesBtn();
            if (!np) return;
            _initNotesFloatWindow();
            _applyNotesRect(_resolveNotesRect());
            np.classList.add('active');
            if (btn) { btn.classList.add('active'); btn.textContent = '笔记'; }
            renderNotesPanel();
        }

        function _closeNotesFloat() {
            var np = _notesEl(), btn = _notesBtn();
            if (np) np.classList.remove('active');
            if (btn) { btn.classList.remove('active'); btn.textContent = '笔记'; }
        }

        function toggleNotesPanel() {
            var np = _notesEl();
            if (!np) return;
            if (np.classList.contains('active')) _closeNotesFloat();
            else _openNotesFloat();
        }

        function renderNotesPanel() {
            var np = document.getElementById('notes-panel');
            if (!np || !np.classList.contains('active')) return;
            var notesListEl = document.getElementById('notes-list');
            var notesEmptyEl = document.getElementById('notes-empty');
            if (!notesListEl || !notesEmptyEl) return;

            // 从后端加载笔记（带当前对话过滤）
            // 范围由「📚 全部 / 📄 本对话」按钮决定（用户 2026-09-27 定）：本对话=带 branch_id 过滤；
            // 全部=不带该参数（后端 file_tools.list_notes 空 branch_id 即不过滤）。
            var branchId = state.currentBranchId || '';
            var _nq = _notesScope === 'all' ? '' : ('?branch_id=' + encodeURIComponent(branchId));
            fetch(BACKEND_API_URL + '/api/notes' + _nq)
                .then(r => r.json())
                .then(data => {
                    var notes = data.notes || [];
                    _currentNotes = notes;  // 缓存，供编辑时查找
                    var branch = state.branches[state.currentBranchId];
                    var titleEl = np.querySelector('.notes-panel-title');
                    if (titleEl) titleEl.textContent = _notesScope === 'all' ? '📚 全部笔记' : (branch ? '📝 ' + branch.name : '📝 对话笔记');

                    if (notes.length === 0) {
                        notesListEl.innerHTML = '';
                        notesEmptyEl.style.display = 'block';
                        return;
                    }
                    notesEmptyEl.style.display = 'none';
                    notesListEl.innerHTML = notes.map(function(n) { return renderNoteCard(n); }).join('');
                    // 附件卡片 × 删除（与数据库条目一致）
                    if (typeof window._enhanceFileCardDelete === 'function') {
                        window._enhanceFileCardDelete(notesListEl, function (noteId) {
                            return (_currentNotes || []).find(function (x) { return x.id === noteId; });
                        }, function () { renderNotesPanel(); });
                    }
                })
                .catch(function(err) {
                    console.error('加载笔记失败:', err);
                    notesListEl.innerHTML = '<div style="padding:1rem;color:var(--error);">加载笔记失败</div>';
                });
        }

        // 笔记折叠状态（按 noteId 记录，默认闭合；会话内重绘时保留手动展开状态）
        var _notesScope = 'branch';   // 'branch' = 本对话笔记；'all' = 我的全部笔记（点面板里「📚 全部」切换）
        var _noteCollapsedMap = {};
        var _currentNotes = [];  // 最近一次渲染的笔记缓存（供编辑时查找）

        function renderNoteCard(note) {
            var contentHtml = note.content
                ? formatMessage(note.content, true, 'note')
                : '<span style="color:var(--gray);font-style:italic;">空笔记</span>';
            var escapedTitle = escHtml(note.title || '未命名笔记');
            // 默认闭合，用 _noteCollapsedMap 记录手动展开/收起状态
            var isCollapsed = _noteCollapsedMap[note.id] !== undefined ? _noteCollapsedMap[note.id] : true;
            // 归属：非本人且非 admin → 只读（无编辑/删除/排序按钮）
            // ⚠️ 用户选 B：金色 L5 也不能改他人笔记，故按「登录名」判而非 role
            //    （历史账号 xhq1/xhq2 的 role 仍写着 admin，按 role 判会误放行）
            var me = (typeof window.getAuthUser === 'function') ? window.getAuthUser() : null;
            var myName = me ? me.username : 'admin';
            var isAdmin = me ? (me.username === 'admin') : true;
            var owner = note.owner || 'admin';
            var canEdit = (owner === myName) || isAdmin;
            var ownerTag = (owner !== myName)
                ? '<span title="归属用户" style="font-size:.72rem;color:var(--gray);margin-left:.4rem;">👤 ' + escHtml(owner) + '</span>'
                : '';
            var actions = canEdit
                ? ('<button class="note-card-move-btn" onclick="moveNote(\'' + note.id + '\', -1)" title="上移">▲</button>'
                + '<button class="note-card-move-btn" onclick="moveNote(\'' + note.id + '\', 1)" title="下移">▼</button>'
                + (typeof window.getAuthUser === 'function' && window.getAuthUser() && window.getAuthUser().username !== 'guest'
                    ? '<button class="note-card-publish-btn" onclick="publishNoteToDb(\'' + note.id + '\')" title="把这条笔记（含附件/图片）原样复制到共享数据库" '
                      + 'style="background:none;border:none;cursor:pointer;font-size:.82rem;padding:.1rem .3rem;">'
                      + (note.cloud_entry_id ? '⬆️ 更新数据库' : '⬆️ 上传数据库') + '</button>'
                    : '')
                + '<button class="note-card-ref-btn" onclick="refLocalNote(\'' + note.id + '\')" title="引用给智能体（发送时带上该笔记全文，可让它增删改查）" '
                  + 'style="background:none;border:none;cursor:pointer;font-size:.82rem;padding:.1rem .3rem;">📌</button>'
                + '<button class="note-card-edit-btn" onclick="editNoteCard(\'' + note.id + '\')" title="编辑笔记">✏️</button>'
                + '<button class="note-card-delete" onclick="deleteNoteCard(\'' + note.id + '\')" title="删除笔记">×</button>')
                : '';
            return '<div class="note-card" data-note-id="' + note.id + '">'
                + '<div class="note-card-header" onclick="toggleNoteCollapse(\'' + note.id + '\')" style="cursor:pointer;">'
                + '<span class="note-card-title">' + escapedTitle + '</span>' + ownerTag
                + '<div class="note-card-header-actions" onclick="event.stopPropagation();">'
                + actions
                + '</div>'
                + '</div>'
                + '<div class="note-card-body" id="note-body-' + note.id + '"'
                + (isCollapsed ? ' style="display:none;"' : '') + '>'
                + '<div class="message-content">' + contentHtml + '</div>'
                + '</div>'
                + '</div>';
        }

        function addNoteCard(branchId, title, content) {
            fetch(BACKEND_API_URL + '/api/notes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: title || '新笔记', content: content || '', branch_id: branchId || '' })
            })
            .then(r => r.json())
            .then(function(data) {
                if (data.status === 'ok') {
                    var np = document.getElementById('notes-panel');
                    if (np && np.classList.contains('active')) renderNotesPanel();
                    showToast('笔记已添加', 'success');
                } else {
                    showToast('添加笔记失败', 'error');
                }
            })
            .catch(function(err) {
                console.error('添加笔记失败:', err);
                showToast('添加笔记失败', 'error');
            });
        }

        function deleteNoteCard(noteId) {
            if (!confirm('确定删除这条笔记吗？')) return;
            fetch(BACKEND_API_URL + '/api/notes/' + noteId, { method: 'DELETE' })
            .then(r => r.json())
            .then(function(data) {
                if (data.status === 'ok') {
                    renderNotesPanel();
                    showToast('笔记已删除', 'success');
                } else {
                    showToast('删除笔记失败', 'error');
                }
            })
            .catch(function(err) {
                console.error('删除笔记失败:', err);
                showToast('删除笔记失败', 'error');
            });
        }

        function moveNote(noteId, direction) {
            fetch(BACKEND_API_URL + '/api/notes/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note_id: noteId, direction: direction })
            })
            .then(r => r.json())
            .then(function(data) {
                if (data.status === 'ok') {
                    renderNotesPanel();
                    if (data.message) showToast(data.message, 'info');
                } else {
                    showToast('移动笔记失败', 'error');
                }
            })
            .catch(function(err) {
                console.error('移动笔记失败:', err);
                showToast('移动笔记失败', 'error');
            });
        }

        function updateNoteTitle(noteId, newTitle) {
            var branch = state.branches[state.currentBranchId];
            if (!branch || !branch.notes) return;
            var note = branch.notes.find(function(n) { return n.id === noteId; });
            if (note) {
                note.title = newTitle || '未命名笔记';
                note.updatedAt = new Date().toISOString();
                saveBranches();
            }
        }

        function toggleNoteCollapse(noteId) {
            var bodyEl = document.getElementById('note-body-' + noteId);
            if (bodyEl) {
                var isHidden = bodyEl.style.display === 'none';
                bodyEl.style.display = isHidden ? '' : 'none';
                // 记录折叠状态，使重绘（如移动排序）后保持用户的手动选择
                _noteCollapsedMap[noteId] = !isHidden;
            }
        }

        function editNoteCard(noteId) {
            var note = _currentNotes.find(function (n) { return n.id === noteId; });
            if (!note) return;
            var originalContent = note.content || '';
            floatingEditor.open({
                title: '编辑笔记',
                showTitle: true,
                titleValue: note.title || '',
                value: note.content || '',
                // 实时预览到真实笔记卡片（不持久化）
                liveUpdate: function (html) {
                    var bodyEl = document.getElementById('note-body-' + noteId);
                    if (bodyEl) { bodyEl.innerHTML = html; }
                },
                // 取消回退：恢复笔记原始内容
                liveRestore: function () {
                    var bodyEl = document.getElementById('note-body-' + noteId);
                    if (bodyEl) { bodyEl.innerHTML = formatMessage(originalContent, true, 'note'); }
                },
                onSave: function (newContent, newTitle) {
                    var t = (newTitle !== null && newTitle !== undefined && newTitle !== '') ? newTitle : '未命名笔记';
                    var c = newContent;
                    fetch(BACKEND_API_URL + '/api/notes', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ note_id: noteId, title: t, content: c })
                    })
                    .then(r => r.json())
                    .then(function(data) {
                        if (data.status === 'ok') {
                            note.title = t;
                            note.content = c;
                            note.updatedAt = new Date().toISOString();
                            saveBranches();
                            renderNotesPanel();
                            showToast('笔记已更新', 'success');
                        } else {
                            showToast('更新笔记失败', 'error');
                        }
                    })
                    .catch(function(err) {
                        console.error('更新笔记失败:', err);
                        showToast('更新笔记失败', 'error');
                    });
                }
            });
        }

        // 兼容保留（旧内联编辑已迁移到浮动编辑器）
        function saveNoteEdit(noteId) {}
        function cancelNoteEdit(noteId) {}

        // ── 笔记原样复制到共享数据库（文本 + 附件 + 图片）──
        function publishNoteToDb(noteId) {
            var note = _currentNotes.find(function (n) { return n.id === noteId; });
            var isUpdate = !!(note && note.cloud_entry_id);
            if (!confirm(isUpdate
                ? '用这条笔记的最新内容更新云端数据库里对应的那条条目？（附件/图片会一并复制）'
                : '把这条笔记（文本 + 附件 + 图片）原样复制一份到共享数据库？本地笔记保持不动。')) return;
            showPassiveToast(isUpdate ? '正在更新云端条目…' : '正在上传到共享数据库…');
            fetch(BACKEND_API_URL + '/api/notes/publish', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note_id: noteId, entry_id: (note && note.cloud_entry_id) || '' })
            })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; }); })
            .then(function (res) {
                var d = res.d || {};
                if (d.status === 'ok') {
                    var tip = (d.updated ? '云端条目已更新' : '已上传到数据库') +
                              '（复制文件 ' + (d.files_copied || 0) + ' 个）';
                    if (d.files_failed && d.files_failed.length) {
                        tip += '；' + d.files_failed.length + ' 个附件失败：' + d.files_failed.join(', ');
                    }
                    showToast('✅ ' + tip, d.files_failed && d.files_failed.length ? 'info' : 'success');
                    renderNotesPanel();
                } else {
                    showToast('❌ 上传数据库失败：' + (d.error || d.message || res.status), 'error');
                }
            })
            .catch(function (e) { showToast('❌ 上传数据库失败：' + (e && e.message ? e.message : '网络错误'), 'error'); });
        }

        // ==================== 对话条目状态标记（2026-09-28）====================
        // 用户等回答时切到别的对话，回来能一眼看出「哪条还在跑」「哪条已经答完还没看」。
        // 只在内存里（刷新即清）；打开该对话 = 已看过 = 标记消失。
        function chatMarkHtml(branchId) {
            var m = state.chatMarks[branchId] || '';
            return '<span class="chat-mark' + (m ? ' ' + m : '') + '"></span>';
        }
        function markChatRunning(branchId) {
            if (!branchId) return;
            state.chatMarks[branchId] = 'running';
            updateHistoryList();
        }
        function markChatDone(branchId) {
            if (!branchId) return;
            if (branchId === state.currentBranchId) { delete state.chatMarks[branchId]; }  // 人正在看 = 不用提醒
            else { state.chatMarks[branchId] = 'done'; }
            updateHistoryList();
        }
        function clearChatMark(branchId) {
            if (branchId && state.chatMarks[branchId] !== undefined) delete state.chatMarks[branchId];
        }

        // ==================== 更新历史列表函数 ====================
        function updateHistoryList() {
            if (state.historyMode === 'single') {
                updateSingleHistoryView();
            } else {
                updateGlobalHistoryView();
            }
        }

        function updateSingleHistoryView() {
            const currentBranch = state.currentBranchId ? state.branches[state.currentBranchId] : null;
            // 无当前对话 → 显示空态，避免 currentBranch.messages 崩溃
            if (!currentBranch) {
                historyList.innerHTML = '';
                const empty = document.createElement('div');
                empty.className = 'empty-state';
                empty.innerHTML = '<div class="empty-state-icon">💭</div><div class="empty-state-text">暂无对话历史</div>';
                historyList.appendChild(empty);
                return;
            }
            // 只显示非临时对话且hasContent为true的对话
            const messages = currentBranch.messages.filter(msg => !msg.isSystem);
            // historyCount updated removed

            let filteredMessages = messages.filter(msg => {
                if (state.sidebar.searchQuery) {
                    if (!msg.content.toLowerCase().includes(state.sidebar.searchQuery)) {
                        return false;
                    }
                }

                if (state.sidebar.currentFilter !== 'all') {
                    if (state.sidebar.currentFilter === 'database') {
                        return msg.type === 'database';
                    } else if (state.sidebar.currentFilter === 'document') {
                        return msg.type === 'document';
                    }
                    return msg.role === state.sidebar.currentFilter;
                }

                return true;
            });

            historyList.innerHTML = '';

            if (filteredMessages.length === 0) {
                const emptyMsg = document.createElement('div');
                emptyMsg.className = 'history-empty';
                emptyMsg.style.textAlign = 'center';
                emptyMsg.style.padding = '2rem';
                emptyMsg.style.color = 'var(--gray)';
                emptyMsg.textContent = state.sidebar.searchQuery ? '未找到匹配的消息' : '暂无对话历史';
                historyList.appendChild(emptyMsg);
                return;
            }

            filteredMessages.forEach(msg => {
                const historyItem = createHistoryItem(msg);
                historyList.appendChild(historyItem);
            });
        }

        function updateGlobalHistoryView() {
            const allMessages = [];

            for (const branchId in state.branches) {
                const branch = state.branches[branchId];
                // 显示所有分支
                const branchMessages = branch.messages.filter(msg => !msg.isSystem);

                branchMessages.forEach(msg => {
                    allMessages.push({
                        ...msg,
                        branchId: branchId,
                        branchName: branch.name
                    });
                });
            }

            allMessages.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

            // 尝试加载自定义分支顺序
            let branchOrder = null;
            try {
                const saved = localStorage.getItem(window.identityKey('branchOrder'));
                if (saved) branchOrder = JSON.parse(saved);
            } catch (e) {}

            const groupedByBranch = {};
            allMessages.forEach(msg => {
                if (!groupedByBranch[msg.branchId]) {
                    groupedByBranch[msg.branchId] = {
                        branchId: msg.branchId,
                        branchName: state.branches[msg.branchId]?.name || '未知对话',
                        messages: []
                    };
                }
                groupedByBranch[msg.branchId].messages.push(msg);
            });

            for (const branchId in groupedByBranch) {
                groupedByBranch[branchId].messages = groupedByBranch[branchId].messages.filter(msg => {
                    if (state.sidebar.searchQuery) {
                        if (!msg.content.toLowerCase().includes(state.sidebar.searchQuery)) {
                            return false;
                        }
                    }

                    if (state.sidebar.currentFilter !== 'all') {
                        if (state.sidebar.currentFilter === 'database') {
                            return msg.type === 'database';
                        } else if (state.sidebar.currentFilter === 'document') {
                            return msg.type === 'document';
                        } else {
                            return msg.role === state.sidebar.currentFilter;
                        }
                    }

                    return true;
                });
            }

            Object.keys(groupedByBranch).forEach(branchId => {
                if (groupedByBranch[branchId].messages.length === 0) {
                    delete groupedByBranch[branchId];
                }
            });

            // 添加空对话（没有消息，但需要显示在侧边栏的**永久对话**）
            // main 是历史遗留的空占位，不显示（避免每次刷新都出现一个空的“默认对话”）
            // ★ 2026-09-30：去掉原先的 isTemporary === false 条件 —— 已无「临时对话/草稿」
            //   概念，所有对话一律永久，空对话同样列出、且永不自动删除。
            for (const branchId in state.branches) {
                if (branchId === 'main') continue;
                const branch = state.branches[branchId];
                if (!groupedByBranch[branchId]) {
                    groupedByBranch[branchId] = {
                        branchId: branchId,
                        branchName: branch.name,
                        messages: []
                    };
                }
            }

            historyList.innerHTML = '';

            if (Object.keys(groupedByBranch).length === 0) {
                const emptyState = document.createElement('div');
                emptyState.className = 'empty-state';
                emptyState.innerHTML = `
                    <div class="empty-state-icon">💭</div>
                    <div class="empty-state-text">${state.sidebar.searchQuery ? '未找到匹配的对话' : '暂无对话历史'}</div>
                    <button class="new-chat-btn" id="new-chat-btn-empty">
                        + 开始新的对话
                    </button>
                `;
                historyList.appendChild(emptyState);
                // 重新绑定事件
                document.getElementById('new-chat-btn-empty').addEventListener('click', createNewConversation);
                return;
            }

            let totalMessages = 0;
            for (const branchId in groupedByBranch) {
                totalMessages += groupedByBranch[branchId].messages.length;
            }
            // historyCount update removed

            // 按自定义顺序或时间排序遍历
            const groupKeys = Object.keys(groupedByBranch);
            if (branchOrder && branchOrder.length > 0) {
                // 按保存的顺序排序
                groupKeys.sort((a, b) => {
                    const ia = branchOrder.indexOf(a);
                    const ib = branchOrder.indexOf(b);
                    if (ia === -1 && ib === -1) return 0;
                    if (ia === -1) return 1;
                    if (ib === -1) return -1;
                    return ia - ib;
                });
            } else {
                // 没保存顺序时按最新消息时间降序（空对话组不参与排序，避免取 messages[0] 崩溃）
                groupKeys.sort((a, b) => {
                    const ma = groupedByBranch[a].messages[0];
                    const mb = groupedByBranch[b].messages[0];
                    if (!ma && !mb) return 0;
                    if (!ma) return 1;
                    if (!mb) return -1;
                    return new Date(mb.timestamp) - new Date(ma.timestamp);
                });
            }

            for (const branchId of groupKeys) {
                const group = groupedByBranch[branchId];

                const groupEl = document.createElement('div');
                groupEl.className = 'history-group';
                groupEl.dataset.branchId = branchId;
                groupEl.draggable = true;

                const isCurrentBranch = branchId === state.currentBranchId;
                if (isCurrentBranch) groupEl.classList.add('current');
                const isExpanded =
                    state.sidebar.searchQuery ||
                    state.sidebar.currentFilter !== 'all' ||
                    state.sidebar.expandedGroups.has(branchId);

                groupEl.innerHTML = `
                    <div class="group-header" onclick="handleBranchClick('${branchId}')">
                        ${chatMarkHtml(branchId)}
                        <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 10px;">
                            ${group.branchName}
                            
                        </div>
                        <!-- 手机端：⋯ 展开条目操作（导出/重命名/删除/便携文件）。
                             放在 wrapper 之前 + 手机端 order 控制行序：第一行 = 标题/消息数/⋯，第二行 = 操作。
                             桌面端隐藏，桌面照旧 hover 出操作。 -->
                        <button class="mobile-item-more" title="更多操作"
                                onclick="event.stopPropagation(); this.closest('.history-group').classList.toggle('mobile-actions-open');">⋯</button>
                        <div class="group-actions-wrapper" onclick="event.stopPropagation()">
                            <div class="group-message-count">${group.messages.length}</div>
                            <div class="group-actions">
                                <button class="group-action-btn" title="导出对话" onclick="event.stopPropagation(); exportConversation('${branchId}')">
                                    📤
                                </button>
                                <button class="group-action-btn" title="重命名对话" onclick="event.stopPropagation(); openRenameBranchModal(event, '${branchId}')">
                                    ✎
                                </button>
                                <button class="group-action-btn delete" title="删除对话" onclick="event.stopPropagation(); deleteBranchWithConfirm('${branchId}')">
                                    ×
                                </button>
                                <button class="group-action-btn" title="添加便携文件" onclick="event.stopPropagation(); openBubbleAddPanel(event, '${branchId}')">
                                    ＋
                                </button>
                            </div>
                        </div>
                    </div>
                    <div class="group-content ${isExpanded ? 'expanded' : ''}" id="group-content-${branchId}">
                        <div class="group-messages" id="group-${branchId}">
                        </div>
                    </div>
                    ${(state.branches[branchId]._bubbles || []).length > 0 ? `
                    <div class="group-bubbles" data-branch-id="${branchId}" id="group-bubbles-${branchId}">
                        ${(state.branches[branchId]._bubbles || []).map((b, bi) => `
                            <span class="history-bubble${b.filePath || b.fileDataKey || b.hasFile ? ' has-file' : ''}" draggable="true"
                                  data-bubble-id="${b.id}"
                                  data-bubble-text="${b.text}"
                                  data-file-path="${b.filePath || ''}"
                                  data-file-id="${b.fileId || ''}"
                                  data-branch-id="${branchId}"
                                  title="${b.text}">
                                <span class="bubble-text">${b.text}</span>
                            </span>
                        `).join('')}
                    </div>` : ''}
                `;

                historyList.appendChild(groupEl);

                // 拖拽事件绑定
                groupEl.addEventListener('dragstart', (e) => {
                    // 阻止从操作按钮开始拖拽
                    if (e.target.closest('.group-action-btn') || e.target.closest('.group-message-count')) {
                        e.preventDefault();
                        return;
                    }
                    e.dataTransfer.setData('text/plain', groupEl.dataset.branchId);
                    groupEl.classList.add('dragging');
                    groupEl.style.opacity = '0.5';
                });

                groupEl.addEventListener('dragend', () => {
                    groupEl.classList.remove('dragging');
                    document.querySelectorAll('.history-group').forEach(g => {
                        g.classList.remove('drag-over-top', 'drag-over-bottom');
                    });
                });

                groupEl.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    if (groupEl.classList.contains('dragging')) return;
                    const rect = groupEl.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    document.querySelectorAll('.history-group').forEach(g => {
                        g.classList.remove('drag-over-top', 'drag-over-bottom');
                    });
                    if (e.clientY < midY) {
                        groupEl.classList.add('drag-over-top');
                    } else {
                        groupEl.classList.add('drag-over-bottom');
                    }
                });

                groupEl.addEventListener('dragleave', () => {
                    groupEl.classList.remove('drag-over-top', 'drag-over-bottom');
                });

                groupEl.addEventListener('drop', (e) => {
                    e.preventDefault();
                    if (groupEl.classList.contains('dragging')) return;

                    const draggedBranchId = e.dataTransfer.getData('text/plain');
                    const targetBranchId = groupEl.dataset.branchId;
                    if (!draggedBranchId || draggedBranchId === targetBranchId) return;

                    // 获取当前可见的分支顺序
                    const allGroups = Array.from(document.querySelectorAll('.history-group'));
                    const branchOrder = allGroups.map(g => g.dataset.branchId);
                    const draggedIdx = branchOrder.indexOf(draggedBranchId);
                    const targetIdx = branchOrder.indexOf(targetBranchId);
                    if (draggedIdx === -1 || targetIdx === -1) return;

                    // 移除被拖动的组
                    branchOrder.splice(draggedIdx, 1);
                    const newTargetIdx = branchOrder.indexOf(targetBranchId);
                    const rect = groupEl.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    const insertIdx = e.clientY < midY ? newTargetIdx : newTargetIdx + 1;
                    branchOrder.splice(insertIdx, 0, draggedBranchId);

                    // 保存顺序
                    try { localStorage.setItem(window.identityKey('branchOrder'), JSON.stringify(branchOrder)); } catch (err) {}

                    groupEl.classList.remove('drag-over-top', 'drag-over-bottom');
                    updateHistoryList();
                });

                // 组气泡事件绑定
                const branchId2 = branchId;
                const bubblesEl = groupEl.querySelector('.group-bubbles');
                if (bubblesEl) {
                    // 已有气泡：点击菜单、拖拽
                    bubblesEl.querySelectorAll('.history-bubble').forEach(bubble => {
                        bubble.addEventListener('click', (e) => {
                            e.stopPropagation();
                            showBubbleMenu(e, bubble, null, branchId2);
                        });
                        bubble.addEventListener('dragstart', (e) => {
                            bubble.classList.add('dragging');
                            e.dataTransfer.setData('text/plain', bubble.dataset.bubbleText);
                            e.dataTransfer.setData('application/file-path', bubble.dataset.filePath || '');
                            e.dataTransfer.effectAllowed = 'copyMove';
                        });
                        bubble.addEventListener('dragend', () => {
                            bubble.classList.remove('dragging');
                        });
                    });
                }

                const groupMessagesEl = document.getElementById(`group-${branchId}`);
                group.messages.forEach(msg => {
                    const historyItem = createHistoryItem(msg, true);
                    groupMessagesEl.appendChild(historyItem);
                });
            }
        }

        // ==================== 分组展开/收起函数 ====================
        function toggleGroup(branchId) {
            const groupContent = document.getElementById(`group-content-${branchId}`);
            if (groupContent) {
                const isExpanded = groupContent.classList.contains('expanded');
                if (isExpanded) {
                    groupContent.classList.remove('expanded');
                    state.sidebar.expandedGroups.delete(branchId);
                } else {
                    groupContent.classList.add('expanded');
                    state.sidebar.expandedGroups.add(branchId);
                }
                localStorage.setItem(window.identityKey('ai-expanded-groups'), JSON.stringify([...state.sidebar.expandedGroups]));
            }
        }

        // ==================== 对话点击处理 ====================
        function handleBranchClick(branchId) {
            if (branchId === state.currentBranchId) {
                // 点击当前对话：展开/收拢详情
                toggleGroup(branchId);
            } else {
                // 点击其他对话：切换到该对话（调用下方的 switchToBranch）
                switchToBranch(branchId);
            }
        }

        function syncNotesPanel() {
            setTimeout(function() {
                renderNotesPanel();
            }, 0);
        }

        // ==================== 创建历史项 ====================
        function createHistoryItem(message, showBranchInfo = false) {
            const item = document.createElement('div');
            item.className = 'history-item';

            if (message.type === 'document') {
                item.classList.add('document');
            } else if (message.type === 'database') {
                item.classList.add('database');
            }

            if (state.sidebar.activeMessageId === message.id) {
                item.classList.add('active');
            }

            let preview = message.content;
            if (preview.length > 60) {
                preview = preview.substring(0, 57) + '...';
            }

            const time = new Date(message.timestamp).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit'
            });

            let roleLabel = message.role === 'user' ? '用户' : 'AI';
            if (message.type === 'document') {
                roleLabel = '文档';
            } else if (message.type === 'database') {
                roleLabel = '知识库';
            } else if (message.thinkingChain) {
                roleLabel = '思考';
            }

            const dateTime = new Date(message.timestamp);
            const dateStr = `${dateTime.getMonth() + 1}月${dateTime.getDate()}日`;
            const timeStr = dateTime.toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit'
            });

            item.innerHTML = `
                <div class="history-preview">${preview}</div>
                <div class="history-meta">
                    <span class="history-role ${message.role} ${message.type === 'document' ? 'document' : ''} ${message.type === 'database' ? 'database' : ''}">
                        ${roleLabel}
                    </span>
                    <span>${dateStr} ${timeStr}</span>
                </div>
            `;

            item.addEventListener('click', () => {
                if (state.historyMode === 'global' && message.branchId && message.branchId !== state.currentBranchId) {
                    switchToBranch(message.branchId);
                }

                if (message.id) {
                    scrollToMessage(message.id);
                }

                document.querySelectorAll('.history-item').forEach(el => {
                    el.classList.remove('active');
                });
                item.classList.add('active');
                state.sidebar.activeMessageId = message.id;
            });

            return item;
        }

        // ==================== 辅助函数 ====================
        function scrollToMessage(messageId) {
            const messageEl = document.querySelector(`[data-id="${messageId}"]`);
            if (messageEl) {
                // 仅在对话区域内部滚动，防止主页面上下跳动
                const container = document.getElementById('messages-container');
                if (container) {
                    const containerRect = container.getBoundingClientRect();
                    const messageRect = messageEl.getBoundingClientRect();
                    const relativeTop = messageRect.top - containerRect.top + container.scrollTop;
                    const scrollTarget = relativeTop - (container.clientHeight / 2) + (messageEl.clientHeight / 2);

                    container.scrollTo({
                        top: scrollTarget,
                        behavior: 'smooth'
                    });
                }

                messageEl.style.animation = 'none';
                void messageEl.offsetWidth; // 触发回流重置动画
                setTimeout(() => {
                    messageEl.style.animation = 'highlight 1.5s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                }, 10);

                if (state.historyMode === 'global') {
                    const branchId = getBranchIdFromMessageId(messageId);
                    if (branchId) {
                        const groupEl = document.querySelector(`[data-branch-id="${branchId}"]`);
                        if (groupEl) {
                            const groupContent = document.getElementById(`group-content-${branchId}`);
                            if (groupContent && !groupContent.classList.contains('expanded')) {
                                groupContent.classList.add('expanded');
                                state.sidebar.expandedGroups.add(branchId);
                                localStorage.setItem(window.identityKey('ai-expanded-groups'), JSON.stringify([...state.sidebar.expandedGroups]));
                            }
                        }
                    }
                }
            }
        }

        function getBranchIdFromMessageId(messageId) {
            for (const branchId in state.branches) {
                const branch = state.branches[branchId];
                const message = branch.messages.find(msg => msg.id === messageId);
                if (message) {
                    return branchId;
                }
            }
            return null;
        }

        function switchToBranch(branchId) {
            // 专注对话管理时（只看条目和笔记）：只跟手更新选中态，不渲染对话 ——
            // 这正是"避免切换对话卡顿"的兑现；记下是哪条，退出专注时再补渲染。
            if (_sbInFocus()) {
                state.currentBranchId = branchId;
                if (typeof persistCurrentBranch === 'function') persistCurrentBranch();
                _sbFocusPending = branchId;
                if (typeof updateHistoryList === 'function') updateHistoryList();
                if (typeof clearChatMark === 'function') clearChatMark(branchId);
                // 笔记要跟着条目走（用户就是来看"条目 + 对应笔记"的）；
                // 不渲染的只是对话消息 —— 那才是切换对话卡顿的来源。
                var np = document.getElementById('notes-panel');
                if (np && np.classList.contains('active') && typeof renderNotesPanel === 'function') {
                    renderNotesPanel();
                }
                return;
            }
            // 点已经打开的那条对话 → 只重渲染一次（用户 2026-09-27：早退会让"默认亮起但没加载"
            // 的对话永远点不出来）。不清输入框、不收起附件面板。
            if (state.currentBranchId === branchId) {
                state.userHasScrolledUp = false;
                renderMessages();
                return;
            }

            // 保存当前对话状态（不再区分临时/永久）
            state.currentBranchId = branchId;
            state.userHasScrolledUp = false;   // 切对话 = 看底部
            // 记住当前对话（切对话不一定走 saveBranches，这里显式持久化以便重启恢复）
            if (typeof persistCurrentBranch === 'function') persistCurrentBranch();
            messageInput.value = '';
            state.documentUpload.pendingAttachments = []; state.documentUpload._errorFiles = [];
            renderMessages();
            updateTokenDisplay();
            clearChatMark(branchId);   // 打开即视为已看过
            updateHistoryList();
            updateSelectedFilesList();
            hideUploadPanel();

            // 笔记同步：setTimeout 0 确保在所有渲染完成后执行
            setTimeout(function() {
                var np = document.getElementById('notes-panel');
                if (np && np.classList.contains('active')) {
                    renderNotesPanel();
                }
            }, 0);
        }

        // ==================== 数据存储与加载 ====================
