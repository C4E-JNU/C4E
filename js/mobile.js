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
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        setTimeout(bind, 400);   // 等主流程 init() 之后
    }
})();
