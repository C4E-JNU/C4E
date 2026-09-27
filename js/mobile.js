/* ==================== 手机端交互（侧栏抽屉） ====================
 * 仅 ≤768px 生效；桌面端不受影响（该宽度下 .mobile-menu-btn 被 CSS 隐藏）。
 * 行为（类 DeepSeek）：
 *   · 点汉堡 → 侧栏从左侧滑出 + 半透明遮罩
 *   · 点遮罩 / 点消息区 / 按 Esc / 选了对话 → 关闭抽屉
 *   · 打开抽屉时锁定 body 滚动
 */
(function () {
    'use strict';

    var MQ = '(max-width: 768px)';
    var OPEN_CLS = 'mobile-drawer-open';

    function isMobile() {
        try { return window.matchMedia(MQ).matches; } catch (e) { return false; }
    }

    function drawerOpen() {
        return document.body.classList.contains(OPEN_CLS);
    }

    function openDrawer() {
        if (!isMobile()) return;
        closeSheet();                      // 抽屉与 ⋯ 面板不叠加
        document.body.classList.add(OPEN_CLS);
        // 打开抽屉时锁滚动，避免背景跟着滑
        document.body.style.overflow = 'hidden';
        var b = document.getElementById('mobile-menu-btn');
        if (b) { b.textContent = '✕'; b.setAttribute('aria-label', '关闭对话管理'); }
    }

    function closeDrawer() {
        document.body.classList.remove(OPEN_CLS);
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
