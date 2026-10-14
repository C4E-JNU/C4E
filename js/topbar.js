/* ==================== 顶栏（宽度驱动，与外壳/设备无关） ====================
 * 2026-10-02 起，本模块只管四件事（布局一律宽度驱动，不再按设备断点）：
 *   1. 当前对话名 → 顶栏窄空间标题（ui.js 切分支时调 __topbarSyncTitle）
 *   2. 抽屉搜索框占位文字：窄空间（body.narrow-space，sidebar.js 宽度裁决）换短文案
 *   3. 顶栏溢出阶梯：ResizeObserver 实测 .nav-container 宽度 → tight/overflow 类
 *      （tight ≲520px：左只留 logo+对话名、右 wiki 只留图标；overflow：只收 wiki 字样）
 *   4. 主题浮层开关（.theme-slot.open 唯一真相；旧的 ⋯ 面板已废） */
(function () {
    'use strict';

    function syncTitle() {
        var el = document.getElementById('header-chat-title');
        if (!el) return;
        // ⚠️ state 是 state.js 里的顶层 const 绑定（不在 window 上），只能用裸 state 判断
        var st = (typeof state !== 'undefined') ? state : null;
        var b = (st && st.branches) ? st.branches[st.currentBranchId] : null;
        el.textContent = (b && b.name) ? b.name : t('top.fallbackTitle');
    }
    window.__topbarSyncTitle = syncTitle;

    /** 抽屉搜索框：窄空间占位文字太长会被截断，换短的；回宽还原。 */
    function syncSearchPlaceholder() {
        var el = document.getElementById('history-search');
        if (!el) return;
        if (!el.dataset.fullPh) el.dataset.fullPh = el.getAttribute('placeholder') || '';
        var narrow = document.body.classList.contains('narrow-space');
        // 文案只跟实测窄空间（sidebar.js 裁决）走，不用设备方向事件
        el.setAttribute('placeholder', narrow ? t('top.searchPh') : el.dataset.fullPh);
    }

    /** 顶栏溢出阶梯：唯一真相 = .nav-container 的实测宽度（任何外壳同一规则）。 */
    function bindHeaderFit() {
        var nav = document.querySelector('.nav-container');
        if (!nav || !window.ResizeObserver) { if (nav) nav.classList.add('tight'); document.body.classList.add('win-tight'); return; }
        var ro = new ResizeObserver(function () {
            var w = nav.clientWidth;
            nav.classList.toggle('tight', w < 520);
            nav.classList.toggle('overflow', nav.scrollWidth > w + 1);
            // 同一测量、同一阈值：输入条也照这个判据收（模型 → 按钮、token 收起）
            document.body.classList.toggle('win-tight', w < 520);
            syncSearchPlaceholder();      // 与顶栏同一测量：窄了就换短占位文字
        });
        ro.observe(nav);
    }

    /** 主题浮层：开关 = .theme-slot.open（点外 / Esc 关）。 */
    function bindThemePopover() {
        var slot = document.querySelector('.theme-slot');
        var btn = document.getElementById('theme-btn');
        if (!slot || !btn) return;
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            slot.classList.toggle('open');
        });
        document.addEventListener('click', function (e) {
            if (!slot.contains(e.target)) slot.classList.remove('open');
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') slot.classList.remove('open');
        });
    }

    function bind() {
        syncTitle();
        syncSearchPlaceholder();
        bindHeaderFit();
        bindThemePopover();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
