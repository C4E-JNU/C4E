// ==================== 事件推送客户端（SSE，用户 2026-09-26 定）====================
//
// 为什么不是轮询：用户明确不要「不对话时还每 N 秒问一次」。
// SSE 由后端**在变更时**推送 → 空闲时零请求。
//
// ⚠️ 为什么不用浏览器原生的 EventSource：
//    它**不能设置请求头**，而本项目的鉴权是 Authorization: Bearer <token>。
//    所以这里用 fetch + body.getReader() 手工解析 SSE ——
//    与 js/graph_rag.js 里读 /api/agent/stream 是同一套写法（本项目已有先例）。
//
// 收到的事件：
//    {type:"hello", rev}  建连时后端先发一次，记下当前版本
//    {type:"chats"}       该账号的对话变了（可能来自另一台设备）→ 拉全量并合并
//    {type:"notes"}       条目变了（共享库=所有人 / 个人=本人）→ 刷新开着的面板
//
// ⚠️ 断开期间的事件必然漏掉 —— 所以**每次(重)连成功后先对齐一次**（见 _evCatchUp）。
//    nginx 侧已配 proxy_buffering off + proxy_read_timeout 3600s，流式不会被憋。

var _evRetry = 0;
var _evHiddenAt = 0;
var _evStarted = false;

function _evHandleBlock(block) {
    block.split('\n').forEach(function (line) {
        if (line.indexOf('data:') !== 0) return;      // ": keepalive" 之类是注释，忽略
        var p;
        try { p = JSON.parse(line.slice(5).trim()); } catch (e) { return; }
        if (!p || !p.type) return;
        if (p.type === 'chats') {
            if (typeof window.syncChatsFromServer === 'function') window.syncChatsFromServer();
        } else if (p.type === 'notes') {
            _evOnNotes();
        }
        // type === 'hello'：只是建连问候，不必处理（对齐由 _evCatchUp 做）
    });
}

/** 条目变了：只刷新**正开着**的面板，别做无用的重活 */
function _evOnNotes() {
    var dbp = document.getElementById('database-panel');
    if (dbp && dbp.classList.contains('active') && typeof refreshDocumentList === 'function') {
        refreshDocumentList();
    }
    if (typeof window.renderNotesPanel === 'function') window.renderNotesPanel();
}

/** (重)连成功后对齐一次：断线期间漏掉的变化靠这一次补回来 */
function _evCatchUp() {
    if (typeof window.syncChatsFromServer === 'function') window.syncChatsFromServer();
    _evOnNotes();
}

async function _evLoop() {
    while (true) {
        var ctrl;
        try {
            ctrl = new AbortController();
            var resp = await fetch('/api/events', {
                headers: { 'Accept': 'text/event-stream' },
                signal: ctrl.signal
            });
            if (resp.status === 401) return;          // 未登录/掉线：交给 auth.js 弹登录，别再转圈
            if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

            _evRetry = 0;
            _evCatchUp();                             // 建连即对齐（含首次连接）

            var reader = resp.body.getReader();
            var dec = new TextDecoder('utf-8');
            var buf = '';
            while (true) {
                var chunk = await reader.read();
                if (chunk.done) break;
                buf += dec.decode(chunk.value, { stream: true });
                var blocks = buf.split('\n\n');
                buf = blocks.pop();                   // 最后一段可能不完整，留到下一块
                blocks.forEach(_evHandleBlock);
            }
        } catch (e) {
            // 断线（网络抖动 / 服务重启 / 页面隐藏被浏览器掐）→ 下面退避重连
        }
        _evRetry = Math.min(_evRetry + 1, 6);
        await new Promise(function (r) { setTimeout(r, 1000 * _evRetry); });
    }
}

/** 启动事件流。未登录不连（游客没有账号数据要同步）。 */
function startEvents() {
    if (_evStarted) return;
    if (typeof window.getAuthToken !== 'function' || !window.getAuthToken()) return;
    _evStarted = true;
    _evLoop();
}

// 切回页面时：隐藏久了浏览器可能把连接掐掉，且期间的事件一定漏了 → 补一次
document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') {
        _evHiddenAt = Date.now();
    } else if (_evStarted && _evHiddenAt && Date.now() - _evHiddenAt > 30000) {
        _evHiddenAt = 0;
        if (typeof window.syncChatsFromServer === 'function') window.syncChatsFromServer();
    }
});

window.startEvents = startEvents;
