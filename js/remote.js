// 大屏右下角「远程控制」：显示手机/其他电脑访问这台电脑用的固定地址 + 二维码。
// 地址由后端 /api/remote/info 给出（隧道实现在项目根 tunnel.py）。

(function initRemoteControl() {
    const btn = document.getElementById('remote-ctrl-btn');
    const panel = document.getElementById('remote-ctrl-panel');
    if (!btn || !panel) return;

    const urlInput = document.getElementById('remote-ctrl-url');
    const qrBox = document.getElementById('remote-ctrl-qr');
    const statusEl = document.getElementById('remote-ctrl-status');
    const toggleBtn = document.getElementById('remote-ctrl-toggle');
    const copyBtn = document.getElementById('remote-ctrl-copy');

    let info = { enabled: false, running: false, url: '' };

    function render() {
        btn.classList.toggle('on', !!info.running);
        urlInput.value = info.url || '—';
        if (info.running) {
            statusEl.textContent = t('remote.running');
        } else if (info.enabled) {
            statusEl.textContent = t('remote.off');
        } else {
            statusEl.textContent = t('remote.notConfigured');
        }
        statusEl.classList.toggle('on', !!info.running);

        toggleBtn.hidden = !info.enabled || !!info.running;
        copyBtn.disabled = !info.url;

        qrBox.innerHTML = '';
        // 只在隧道真的在跑时才出二维码：没开隧道时扫码打不开，不如让用户先点「开启隧道」
        if (info.running && info.url && typeof QRCode !== 'undefined') {
            new QRCode(qrBox, {
                text: info.url, width: 132, height: 132,
                correctLevel: QRCode.CorrectLevel.M,
            });
        }
    }

    async function load() {
        try {
            const r = await fetch('/api/remote/info');
            info = await r.json();
        } catch (e) {
            info = { enabled: false, running: false, url: '' };
        }
        render();
    }

    btn.addEventListener('click', () => {
        panel.hidden = !panel.hidden;
        if (!panel.hidden) load();
    });

    const closeBtn = document.getElementById('remote-ctrl-close');
    closeBtn.addEventListener('click', () => { panel.hidden = true; });

    document.addEventListener('click', (e) => {          // 点面板外面收起
        if (!panel.hidden && !panel.contains(e.target) && e.target !== btn) panel.hidden = true;
    });

    copyBtn.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(info.url);
            copyBtn.textContent = t('remote.copied');
        } catch (e) {
            urlInput.select();
            document.execCommand('copy');
            copyBtn.textContent = t('remote.copied');
        }
        setTimeout(() => { copyBtn.textContent = t('remote.copy'); }, 1500);
    });

    toggleBtn.addEventListener('click', async () => {
        toggleBtn.disabled = true;
        try {
            const r = await fetch('/api/remote/start', { method: 'POST' });
            info = await r.json();
        } catch (e) { /* 保持原状态，状态行会如实说明 */ }
        toggleBtn.disabled = false;
        render();
    });

    load();
})();
