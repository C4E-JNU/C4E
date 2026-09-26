// ==================== 对话历史的后端存储（用户 2026-09-26 定）====================
//
// 对话（分支 + 全部消息 + 分支笔记）原本只存在浏览器 localStorage，
// 换个浏览器回来一看：资料都还在，就聊天没了。这个文件把它搬到后端。
//
// **对话才是最该后端化的东西** —— 设置丢了可以重填，聊天记录丢了就没了。
//
// 模型：与个人设置完全同一套（后端权威 + localStorage 作本地缓存）
//   · 启动：hydrateUserChats() 把后端对话灌回 localStorage，
//           branching.js 的 loadBranches() 照旧同步读缓存 —— 一行都不用改
//   · 写入：saveBranches() 里调 setUserChats() → 节流 1.5s 推后端
//   · 离开页面：visibilitychange(hidden) / pagehide 强制推一次
//
// 并发（用户定）：后端是**单账号单会话**（登录踢掉旧 token，见 auth.py login），
// 所以同一时刻只有一个写者 → 整份覆盖即可，前端不做任何合并/冲突逻辑。
//
// 存储位置：workspace/users/<身份>/chats.json，见 backend/tools/user_chats.py

// 参与同步的三个键（都是「按身份隔离」的键，见 auth.js 的 identityKey）
var _UC_BRANCHES = 'ai-branches';          // 全部对话（含消息/分支笔记）
var _UC_CURRENT = 'ai-current-branch';     // 上次打开的是哪个对话
var _UC_ORDER = 'branchOrder';             // 侧栏排序

var _chatsPushTimer = null;
var _chatsLastPush = '';                   // 上次成功推送的报文，用于「内容没变就别推」

// ── 本地缓存 → 报文 ─────────────────────────────

/** 把当前 3 个键的快照整成对象（读的是按身份隔离后的实际键） */
function _snapshotChats() {
    var out = {};
    try {
        var b = localStorage.getItem(window.identityKey(_UC_BRANCHES));
        if (b !== null) out.branches = JSON.parse(b);
        var o = localStorage.getItem(window.identityKey(_UC_ORDER));
        if (o !== null) out.branchOrder = JSON.parse(o);
        var c = localStorage.getItem(window.identityKey(_UC_CURRENT));
        if (c) out.currentBranchId = c;
    } catch (e) { /* 解析失败就当这次没内容 */ }
    return out;
}

// ── 启动：后端 → 本地缓存 ───────────────────────

/** 返回 Promise。必须在 loadBranches() 之前完成（见 api.js 的 init）。 */
async function hydrateUserChats() {
    var remote = null;
    try {
        var r = await fetch('/api/user/chats');
        if (!r.ok) return false;            // 未登录/后端不可达：本地缓存继续用
        remote = (await r.json()).chats || {};
    } catch (e) {
        return false;
    }

    var hasRemote = remote.branches && Object.keys(remote.branches).length > 0;
    if (!hasRemote) {
        // 后端还没有这个人的对话，而本地已有（本功能上线前聊的）
        // → 推上去，完成一次性迁移。推完后端非空，此分支不会再来。
        var local = _snapshotChats();
        if (local.branches && Object.keys(local.branches).length > 0) await pushUserChats();
        return true;
    }

    // 后端有：灌回本地缓存（后端权威）
    try {
        localStorage.setItem(window.identityKey(_UC_BRANCHES), JSON.stringify(remote.branches));
        if (remote.branchOrder) {
            localStorage.setItem(window.identityKey(_UC_ORDER), JSON.stringify(remote.branchOrder));
        }
        if (remote.currentBranchId) {
            localStorage.setItem(window.identityKey(_UC_CURRENT), remote.currentBranchId);
        }
    } catch (e) {
        // localStorage 配额满：后端数据仍在，只是这台机器缓存不下
        if (typeof showToast === 'function') showToast('本地缓存空间不足，对话已存在服务器上', 'error');
    }
    return true;
}

// ── 写入：节流推后端 ────────────────────────────

/** 由 saveBranches() 调用。节流 1.5s，多次调用合并成一次推送。 */
function setUserChats() {
    if (_chatsPushTimer) clearTimeout(_chatsPushTimer);
    _chatsPushTimer = setTimeout(pushUserChats, 1500);
}

/** 立刻把当前对话推给后端（整份覆盖） */
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
