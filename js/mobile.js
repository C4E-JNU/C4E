/* ==================== 手机端交互（侧栏抽屉） ====================
 * 仅 ≤768px 生效；桌面端不受影响（该宽度下 .mobile-menu-btn 被 CSS 隐藏）。
 * 行为（类 DeepSeek）：
 *   · 点汉堡 → 侧栏从左侧滑出 + 半透明遮罩
 *   · 点遮罩 / 点消息区 / 按 Esc / 选了对话 → 关闭抽屉
 *   · 打开抽屉时锁定 body 滚动
 */
(function () {
    'use strict';

    // ★ 桌面外壳（C4EAI.exe 启动时 <html> 带 .shell-desktop）里本模块必须完全不工作。
    //   它用 matchMedia('(max-width:768px)') 判断"是不是手机"，而窗口最小化时 WebView2
    //   报的假视口（157x25）会命中 —— 更致命的是抽屉打开时它把 #chat-sidebar 搬到
    //   document.body 下（adoptSidebar），一搬走，css/layout.css 里那条
    //     .chat-sidebar:not(.collapsed) ~ .sidebar-resizer { display: block }
    //   的兄弟关系就永久失效 —— 拖动键从此再也不出现（用户反馈的"拖动键没能正常化"）。
    if (document.documentElement.classList.contains('shell-desktop')) return;

    var MQ = '(max-width: 768px)';
    var OPEN_CLS = 'mobile-drawer-open';

    function isMobile() {
        try { return window.matchMedia(MQ).matches; } catch (e) { return false; }
    }

    function drawerOpen() {
        return document.body.classList.contains(OPEN_CLS);
    }

    /** 侧栏在 DOM 里的老家（.chat-section 内）。家园页那一块是 display:none，
     *  而且 .chat-section 是层叠上下文（position:relative + z-index:2000），会把
     *  position:fixed 的侧栏困住、永远压在 body::before 遮罩(2900) 之下 —— 用户看到的
     *  就是"点了只暗屏、抽屉不出来"。纯 CSS 盖不住 → 打开时把侧栏搬到 body 下，
     *  关掉时原样搬回（DOM 搬家不影响它自己与子节点上的监听器）。 */
    var sbHome = null;

    function adoptSidebar() {
        var sb = document.getElementById('chat-sidebar');
        if (!sb || sb.parentElement === document.body) return;
        sbHome = { parent: sb.parentElement, next: sb.nextElementSibling };
        document.body.appendChild(sb);
        void sb.offsetWidth;               // 强制重排，让 translateX 的过渡从屏外开始
    }

    function restoreSidebar() {
        var sb = document.getElementById('chat-sidebar');
        if (!sb || !sbHome || !sbHome.parent) return;
        if (sbHome.next && sbHome.next.parentElement === sbHome.parent) {
            sbHome.parent.insertBefore(sb, sbHome.next);
        } else {
            sbHome.parent.appendChild(sb);
        }
        sbHome = null;
    }

    function openDrawer() {
        if (!isMobile()) return;
        closeSheet();                      // 抽屉与 ⋯ 面板不叠加
        adoptSidebar();
        document.body.classList.add(OPEN_CLS);
        // 打开抽屉时锁滚动，避免背景跟着滑
        document.body.style.overflow = 'hidden';
        var b = document.getElementById('mobile-menu-btn');
        if (b) { b.textContent = '✕'; b.setAttribute('aria-label', '关闭对话管理'); }
    }

    function closeDrawer() {
        document.body.classList.remove(OPEN_CLS);
        restoreSidebar();
        document.body.style.overflow = '';
        var b = document.getElementById('mobile-menu-btn');
        if (b) { b.textContent = '☰'; b.setAttribute('aria-label', '打开对话管理'); }
    }

    function toggleDrawer() {
        if (drawerOpen()) closeDrawer(); else openDrawer();
    }

    window.__mobileOpenDrawer = openDrawer;
    window.__mobileCloseDrawer = closeDrawer;
    window.__mobileIsDrawerOpen = drawerOpen;

    function bind() {
        var btn = document.getElementById('mobile-menu-btn');
        if (btn && !btn.dataset.bound) {
            btn.dataset.bound = '1';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                toggleDrawer();
            });
        }

        // 点遮罩（body::before 区域）或消息区 → 关闭
        // 用 pointerdown 捕获阶段，避免被其它监听吞掉
        document.addEventListener('pointerdown', function (e) {
            if (!drawerOpen()) return;
            var t = e.target;
            // 点在抽屉内部（sidebar / 笔记面板 / 数据库面板）不关
            if (t.closest && (t.closest('.chat-sidebar') ||
                              t.closest('.notes-panel') ||
                              t.closest('#database-panel') ||
                              t.closest('#mobile-menu-btn') ||
                              t.closest('#auth-login-overlay') ||
                              t.closest('#auth-admin-overlay'))) {
                return;
            }
            closeDrawer();
        }, true);

        // Esc 关闭（复用现有键盘习惯）
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && drawerOpen()) closeDrawer();
        });

        // 选了某个对话 / 新建对话 → 自动收起抽屉（手机上更顺手）
        var list = document.getElementById('history-list');
        if (list && !list.dataset.mobileBound) {
            list.dataset.mobileBound = '1';
            list.addEventListener('click', function (e) {
                if (!isMobile()) return;
                var it = e.target.closest && e.target.closest('.history-item, .branch-item, .new-chat-btn');
                if (it) setTimeout(closeDrawer, 180);   // 稍等让切换动作先执行
            });
        }
        var nb = document.getElementById('new-chat-btn');
        if (nb && !nb.dataset.mobileBound) {
            nb.dataset.mobileBound = '1';
            nb.addEventListener('click', function () {
                if (isMobile()) setTimeout(closeDrawer, 180);
            });
        }

        // 从桌面宽度缩到手机宽度时，清掉可能残留的打开态
        try {
            var mql = window.matchMedia(MQ);
            var onChange = function (ev) {
                if (!ev.matches) {          // 变宽（回桌面）
                    document.body.classList.remove(OPEN_CLS);
                    restoreSidebar();
                    document.body.style.overflow = '';
                    var b2 = document.getElementById('mobile-menu-btn');
                    if (b2) b2.textContent = '☰';
                }
            };
            if (mql.addEventListener) mql.addEventListener('change', onChange);
            else if (mql.addListener) mql.addListener(onChange);
        } catch (e) {}

        // 切到横屏 / 旋转 → 关抽屉（避免布局怪异）
        window.addEventListener('orientationchange', function () {
            setTimeout(closeDrawer, 100);
        });
    
        bindMore();
    }

    // ==================== 方案 A（2026-09-27）：顶栏 ☰ + 对话标题 + ⋯ ====================

    /** 顶栏标题 = 当前对话名（ui.js 的 renderMessages 每轮会调一次） */
    function syncTitle() {
        var el = document.getElementById('mobile-chat-title');
        if (!el) return;
        // ⚠️ state 是 state.js 里的顶层 const 绑定（不在 window 上），只能用裸 state 判断
        var st = (typeof state !== 'undefined') ? state : null;
        var b = (st && st.branches) ? st.branches[st.currentBranchId] : null;
        el.textContent = (b && b.name) ? b.name : '对话';
    }
    window.__mobileSyncTitle = syncTitle;

    function sheetOpen() { return document.body.classList.contains('mobile-more-open'); }
    function openSheet() { if (isMobile()) document.body.classList.add('mobile-more-open'); }
    function closeSheet() { document.body.classList.remove('mobile-more-open'); }
    window.__mobileOpenSheet = openSheet;
    window.__mobileCloseSheet = closeSheet;

    /** 把桌面顶栏那组控件（主题圆点 / 数据库 / 登录）整块**搬进** ⋯ 面板；
     *  回到桌面宽度再搬回去。移动 DOM 而不是复制 —— 事件监听、auth.js 渲染的
     *  登录芯片都在原位照旧工作（用户偏好：复用现成机制，不新建平行的一套）。 */
    function placeNavControls() {
        var sheet = document.getElementById('mobile-more-sheet');
        var container = document.querySelector('.nav-container');
        if (!sheet || !container) return;
        var nav = container.querySelector(':scope > .nav-controls') || sheet.querySelector(':scope > .nav-controls');
        if (!nav) return;
        if (isMobile()) {
            if (nav.parentElement !== sheet) sheet.appendChild(nav);
        } else {
            if (nav.parentElement !== container) container.insertBefore(nav, sheet);
            closeSheet();
        }
    }

    /** 抽屉搜索框：手机端占位文字太长会被截断（"搜索（点击C4E图标可以收起该区域）"），
     *  这里换成短的；回到桌面宽度再还原。 */
    function syncSearchPlaceholder() {
        var el = document.getElementById('history-search');
        if (!el) return;
        if (!el.dataset.fullPh) el.dataset.fullPh = el.getAttribute('placeholder') || '';
        el.setAttribute('placeholder', isMobile() ? '搜索对话…' : el.dataset.fullPh);
    }

    function bindMore() {
        var btn = document.getElementById('mobile-more-btn');
        if (btn && !btn.dataset.bound) {
            btn.dataset.bound = '1';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                if (sheetOpen()) closeSheet(); else openSheet();
            });
        }
        // 点面板外 → 收起
        document.addEventListener('pointerdown', function (e) {
            if (!sheetOpen()) return;
            var t = e.target;
            if (t.closest && (t.closest('#mobile-more-sheet') || t.closest('#mobile-more-btn'))) return;
            closeSheet();
        }, true);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && sheetOpen()) closeSheet();
        });
        try {
            var mql2 = window.matchMedia(MQ);
            var onResize = function () { placeNavControls(); syncSearchPlaceholder(); };
            if (mql2.addEventListener) mql2.addEventListener('change', onResize);
            else if (mql2.addListener) mql2.addListener(onResize);
        } catch (e) {}
        window.addEventListener('orientationchange', function () { closeSheet(); });
        placeNavControls();
        syncSearchPlaceholder();
        syncTitle();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        setTimeout(bind, 400);   // 等主流程 init() 之后
    }
})();
