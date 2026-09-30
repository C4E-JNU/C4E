/* 桌面版窗口按钮（最小化 / 最大化 / 关闭）—— 挂在顶栏 header 最右端。

   只在 pywebview（桌面版 C4EAI.exe）里生效：
     · 给 body 加 .desktop-app  → CSS 才显示这三个按钮
     · 给 header 加 .pywebview-drag-region → 顶栏空处可拖动窗口
     · 点击调用 desktop.pyw 暴露的 pywebview.api（不依赖 pywebview 未公开的内置方法）
   浏览器里打开时这个文件什么也不做，网页端行为零影响。 */
(function () {
    'use strict';

    var inited = false;

    function hasBridge() {
        return !!(window.pywebview && window.pywebview.api);
    }

    function call(name) {
        try {
            if (window.pywebview.api && typeof window.pywebview.api[name] === 'function') {
                window.pywebview.api[name]();
                return true;
            }
        } catch (e) { /* 桥接不可用就算了，不影响页面其它功能 */ }
        return false;
    }

    // 是否已最大化：无边框窗口没有可靠属性，用「外框≈屏幕可用区」判断
    function isMaximized() {
        return window.outerWidth >= screen.availWidth - 8 &&
               window.outerHeight >= screen.availHeight - 8;
    }

    function syncMaxButton() {
        var b = document.getElementById('win-max');
        if (!b) return;
        var m = isMaximized();
        b.dataset.state = m ? 'max' : 'restore';
        b.title = m ? '还原' : '最大化';
        b.textContent = m ? '\u2750' : '\u25A2';
    }

    function init() {
        if (inited) return;
        inited = true;

        document.body.classList.add('desktop-app');

        var header = document.querySelector('header');
        if (header) header.classList.add('pywebview-drag-region');

        var minBtn = document.getElementById('win-min');
        var maxBtn = document.getElementById('win-max');
        var closeBtn = document.getElementById('win-close');

        if (minBtn) minBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            call('minimize');
        });
        if (maxBtn) maxBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            call(isMaximized() ? 'restore' : 'maximize');
            setTimeout(syncMaxButton, 300);
        });
        if (closeBtn) closeBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            call('close');        // 关窗口 = 退出（desktop.pyw 顺带收掉隧道）
        });

        window.addEventListener('resize', syncMaxButton);
        syncMaxButton();
    }

    if (hasBridge()) {
        init();
    } else {
        // pywebview 的桥是页面加载后才注入的（★事件派发在 window 上，不是 document），
        // 所以两路等它：监听 pywebviewready + 短暂轮询。浏览器里两条路都不会成立，
        // 所以这个文件在网页端天然是空操作。
        window.addEventListener('pywebviewready', init, { once: true });
        var tries = 0;
        var timer = setInterval(function () {
            if (hasBridge() || ++tries > 40) {      // 最多 4 秒
                clearInterval(timer);
                if (hasBridge()) init();
            }
        }, 100);
    }
})();
