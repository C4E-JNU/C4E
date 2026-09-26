// ==================== 对话历史的后端存储 + 多设备同步（用户 2026-09-26 定）====================
//
// 对话（分支 + 全部消息 + 分支笔记）原本只存在浏览器 localStorage，
// 换个浏览器回来一看：资料都还在，就聊天没了。这个文件把它搬到后端。
//
// **对话才是最该后端化的东西** —— 设置丢了可以重填，聊天记录丢了就没了。
//
// 并发模型（用户最终定稿）：**两边允许同时登录**，A 进行的对话要出现在 B 的前端。
//   → 多写者 ⇒ 写入不能是「整份覆盖」（否则 A、B 互相抹），必须**按对话 id 合并**。
//   → 合并规则：同一 id 谁的 `updatedAt` 新谁赢；不同 id 取并集。
//   → **删除必须留墓碑**：A 删掉的对话，B 那份旧副本会把它推回来（两台同时在线时必然发生）。
//
// 触发方式（用户定）：**SSE 推送**（见 js/events.js）
//   · 后端在变更时推 {type:"chats"} → 本文件 syncChatsFromServer() 拉全量并合并
//   · 空闲时零请求（轮询是每 N 秒问一次，用户明确不要）
//   · 断线期间的事件必然漏掉 → 重连后用 /api/user/chats/version 对齐（见 events.js）
//
// 启动：hydrateUserChats() 把后端对话**合并**进 localStorage 缓存，
//       branching.js 的 loadBranches() 照旧同步读缓存 —— 一行都不用改。
//
// 存储位置：workspace/users/<身份>/chats.json，见 backend/tools/user_chats.py

var _UC_BRANCHES = 'ai-branches';            // 全部对话（含消息/分支笔记）
var _UC_CURRENT = 'ai-current-branch';       // 上次打开的是哪个（★ 只本地，不参与同步）
var _UC_ORDER = 'branchOrder';               // 侧栏排序
var _UC_DELETED = 'ai-branches-deleted';     // 墓碑：id → 删除时间（本地镜像）

var _chatsPushTimer = null;
var _chatsLastPush = '';                     // 上次成功推送的报文，用于「内容没变就别推」
var _chatsMeta = {};                         // id → 指纹（判断哪个对话真的变了）
var _chatsDeleted = {};                      // 本地墓碑镜像

// ── 指纹：判断某个对话这次是否真的变了（变了才打新时间戳）──────
// 不靠"调用 saveBranches 就认为变了"——切换对话、重渲染也会调它，
// 那会让没变的对话也拿到新时间戳，从而在合并时错误地"赢过"别的设备。
function _branchFp(b) {
    if (!b) return '';
    return [(b.messages || []).length, b.name || '', (b.notes || []).length,
            b.isTemporary ? 1 : 0, b.hasContent ? 1 : 0].join('|');
}

function _stampChanged() {
    var now = new Date().toISOString();
    var seen = {};
    Object.keys(state.branches || {}).forEach(function (id) {
        var b = state.branches[id];
        if (!b) return;
        seen[id] = 1;
        var fp = _branchFp(b);
        if (_chatsMeta[id] !== fp) { b.updatedAt = now; _chatsMeta[id] = fp; }
        if (!b.updatedAt) b.updatedAt = now;
        if (_chatsDeleted[id]) delete _chatsDeleted[id];   // 本地看得见 = 活着
    });
    Object.keys(_chatsMeta).forEach(function (id) {
        if (!seen[id]) { _chatsDeleted[id] = now; delete _chatsMeta[id]; }   // 刚被删
    });
}

// ── 合并（唯一实现，启动 hydrate 与线上同步共用）──────────────
// 返回 {branches, deleted, added}；added = 本地有、后端没有的对话数（>0 说明要推上去）
function _mergeBranchSets(localBranches, localDeleted, remote) {
    var rb = (remote && remote.branches) || {};
    var rd = (remote && remote.deleted) || {};
    var br = {}, dl = {}, added = 0;

    Object.keys(localBranches || {}).forEach(function (id) { br[id] = localBranches[id]; });
    Object.keys(localDeleted || {}).forEach(function (id) { dl[id] = localDeleted[id]; });

    // 后端墓碑 → 删掉本地那份（除非本地那份更新，即"删后又改")
    Object.keys(rd).forEach(function (id) {
        var ts = String(rd[id] || '');
        if (br[id] && String(br[id].updatedAt || '') <= ts) delete br[id];
        if (ts > String(dl[id] || '')) dl[id] = ts;
    });
    // 后端对话 → 合并（同 id 取 updatedAt 新的；本地没有的直接收下）
    Object.keys(rb).forEach(function (id) {
        var r = rb[id], l = br[id];
        if (!l) {
            var ts = String(dl[id] || '');
            if (ts && String((r || {}).updatedAt || '') <= ts) return;   // 已删且副本更旧 → 不复活
            br[id] = r;
        } else if (String((r || {}).updatedAt || '') > String(l.updatedAt || '')) {
            br[id] = r;
        }
    });
    // 本地有、后端没有 → 计数（用于决定是否要把本地的推上去）
    Object.keys(localBranches || {}).forEach(function (id) { if (!rb[id]) added++; });

    return { branches: br, deleted: dl, added: added };
}

function _cacheKey(k) { return window.identityKey(k); }

function _readCache() {
    var br = {}, dl = {};
    try { br = JSON.parse(localStorage.getItem(_cacheKey(_UC_BRANCHES)) || '{}') || {}; } catch (e) {}
    try { dl = JSON.parse(localStorage.getItem(_cacheKey(_UC_DELETED)) || '{}') || {}; } catch (e) {}
    return { branches: br, deleted: dl };
}

function _writeCache(branches, deleted, order) {
    try {
        localStorage.setItem(_cacheKey(_UC_BRANCHES), JSON.stringify(branches || {}));
        localStorage.setItem(_cacheKey(_UC_DELETED), JSON.stringify(deleted || {}));
        if (order) localStorage.setItem(_cacheKey(_UC_ORDER), JSON.stringify(order));
    } catch (e) {
        // localStorage 配额满：后端数据仍在，只是这台机器缓存不下
        if (typeof showToast === 'function') showToast('本地缓存空间不足，对话已存在服务器上', 'error');
    }
}

/** 把当前 state 的对话整成推送报文 */
function _snapshotChats() {
    var out = {};
    _stampChanged();
    Object.keys(state.branches || {}).forEach(function (id) {
        var b = state.branches[id];
        if (b) out[id] = b;
    });
    var o = localStorage.getItem(_cacheKey(_UC_ORDER));
    var order = [];
    try { order = o ? JSON.parse(o) : []; } catch (e) { order = []; }
    return { branches: out, deleted: _chatsDeleted, branchOrder: order };
}

// ── 启动：后端 → 合并进本地缓存 ─────────────────
async function hydrateUserChats() {
    var remote = null;
    try {
        var r = await fetch('/api/user/chats');
        if (!r.ok) return false;
        remote = (await r.json()).chats || {};
    } catch (e) {
        return false;
    }
    var local = _readCache();
    var m = _mergeBranchSets(local.branches, local.deleted, remote);
    _chatsDeleted = m.deleted;
    Object.keys(m.branches).forEach(function (id) { _chatsMeta[id] = _branchFp(m.branches[id]); });
    _writeCache(m.branches, m.deleted, (remote && remote.branchOrder) || null);
    // 本地有、后端没有（本功能上线前聊的 / 上一台设备还没推上来的）→ 推一次
    if (m.added > 0) setUserChats();
    return true;
}

// ── 线上同步（收到 SSE 推送后调用）───────────────
/** 拉全量并与本地合并；返回真正变了的对话数。生成回复期间跳过（别踩掉流式输出）。 */
async function syncChatsFromServer() {
    if (state.isGenerating) return 0;
    var r;
    try { r = await fetch('/api/user/chats'); } catch (e) { return 0; }
    if (!r.ok) return 0;
    var remote = (await r.json()).chats || {};
    var cur = state.currentBranchId;
    var m = _mergeBranchSets(state.branches || {}, _chatsDeleted, remote);
    var changed = 0;
    Object.keys(m.branches).forEach(function (id) {
        if (!(state.branches || {})[id] ||
            String((state.branches[id] || {}).updatedAt || '') !== String((m.branches[id] || {}).updatedAt || '')) changed++;
    });
    Object.keys(state.branches || {}).forEach(function (id) { if (!m.branches[id]) changed++; });
    if (!changed) { _chatsDeleted = m.deleted; return 0; }

    state.branches = m.branches;
    _chatsDeleted = m.deleted;
    Object.keys(state.branches).forEach(function (id) { _chatsMeta[id] = _branchFp(state.branches[id]); });
    _writeCache(state.branches, _chatsDeleted, remote.branchOrder || null);
    // 当前打开的对话被别的设备删了 → 回到空态
    if (cur && !state.branches[cur]) {
        state.currentBranchId = null;
        try { localStorage.removeItem(_cacheKey(_UC_CURRENT)); } catch (e) {}
    }
    if (typeof updateHistoryList === 'function') updateHistoryList();
    if (typeof renderMessages === 'function') renderMessages();
    return changed;
}

// ── 写入：节流推后端 ────────────────────────────
/** 由 saveBranches() / persistCurrentBranch() 调用。节流 1.5s，多次调用合并成一次。 */
function setUserChats() {
    if (_chatsPushTimer) clearTimeout(_chatsPushTimer);
    _chatsPushTimer = setTimeout(pushUserChats, 1500);
}

async function pushUserChats() {
    _chatsPushTimer = null;
    var body = JSON.stringify({ chats: _snapshotChats() });
    if (!body || body === _chatsLastPush) return;   // 内容没变就不推
    try {
        var r = await fetch('/api/user/chats', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: body
        });
        if (r.ok) _chatsLastPush = body;            // 只有成功才记，失败下次重推
    } catch (e) {
        // 后端不可达：本地缓存仍在，下次写入会再推
    }
}

// ── 离开页面前把最后一笔推掉 ────────────────────
document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && _chatsPushTimer) pushUserChats();
});
window.addEventListener('pagehide', function () {
    if (_chatsPushTimer) pushUserChats();
});

window.hydrateUserChats = hydrateUserChats;
window.setUserChats = setUserChats;
window.pushUserChats = pushUserChats;
window.syncChatsFromServer = syncChatsFromServer;
