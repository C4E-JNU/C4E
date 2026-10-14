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
// 启动：init() → hydrateUserChats() 从**本机后端**拉全量 → 合并进 state.branches（内存）
//       → initAfterSettings() → loadBranches() 直接用它渲染。
// ★ 对话**不写浏览器 localStorage**（2026-09-27 用户报「本地缓存空间不足」后去掉）：
//   localStorage 每站点只有几 MB，全部对话全文（长回答/大表格/附件引用）会把它顶爆；
//   而这里的「服务器」就是本机后端 —— 再存一份副本既无必要，又正是墓碑误删事件的温床。
//   唯一真相 = workspace/users/<身份>/chats.json。
//
// 存储位置：workspace/users/<身份>/chats.json，见 backend/tools/user_chats.py

var _UC_LEGACY_BRANCHES = 'ai-branches';        // 旧的全量缓存键（迁移一次后清除）
var _UC_LEGACY_DELETED = 'ai-branches-deleted'; // 旧墓碑镜像键（同上）
var _UC_CURRENT = 'ai-current-branch';          // 上次打开的是哪个（★ 只本地，不参与同步）
var _UC_ORDER = 'branchOrder';                  // 侧栏排序（每台设备自己的视图）

var _chatsPushTimer = null;
var _chatsLastPush = '';                     // 上次成功推送的报文，用于「内容没变就别推」
var _chatsMeta = {};                         // id → 指纹（判断哪个对话真的变了）
var _chatsDeleted = {};                      // 本地墓碑镜像

// ── 多端实时同看（用户 2026-10-03：后端接管消息树）─────────────────
// 每个页面一个随机 client_id：后端把生成事件广播给该账号所有页面（含发起端），
// 前端用它滤掉「自己发出去的回声」。
var _ucClientId = 'c' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
var _ucTreeMode = 'off';      // on = 生成内容由后端落盘（前端不再回推该对话）
var _ucManaged = {};          // id → 1：已有账本、由后端投影维护的对话
var _ucPendingSync = false;   // 生成中收到的同步通知：结束后补做（旧实现直接丢弃）

// ── 指纹：判断某个对话这次是否真的变了（变了才打新时间戳）──────
// 不靠"调用 saveBranches 就认为变了"——切换对话、重渲染也会调它，
// 那会让没变的对话也拿到新时间戳，从而在合并时错误地"赢过"别的设备。
function _branchFp(b) {
    if (!b) return '';
    // ★ 2026-09-30：去掉 isTemporary 项（该字段已废弃，全项目再无「临时对话」概念）。
    //   指纹只用于判断「内容是否真的变了」，不参与任何删除判定。
    return [(b.messages || []).length, b.name || '', (b.notes || []).length,
            b.hasContent ? 1 : 0].join('|');
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
    // ★ 墓碑只由**显式删除动作**写（markBranchDeleted）。旧实现在这里做「这一轮看不见 = 刚被删」
    //   的推断 → 只要 state.branches 短暂变少（缓存没写全 / 合并时序 / 切身份），就会批量写墓碑
    //   → 推到后端就被真删。2026-09-27 就是这样在**同一毫秒**删掉 21 个对话（branch-17~26 / 36~46，
    //   用户报「没删的对话也不见了」）。**看不见 ≠ 被删。**
    Object.keys(_chatsMeta).forEach(function (id) {
        if (!seen[id]) delete _chatsMeta[id];   // 只清本地指纹账，绝不写墓碑
    });
}

// ── 墓碑取值（★ 类型防护，2026-09-30 修）─────────────────────
// 墓碑的值**约定是 ISO 时间戳字符串**，但 2026-09-29 那次「xhq1 的 30 个对话迁移到 xhq2」
// 留下的是迁移记录对象 {id, name, deletedAt, movedTo}。直接 String(对象) → "[object Object]"，
// 它的字符序**大于任何 ISO 时间戳**（'[' = 0x5B > '2' = 0x32），于是
//     updatedAt <= "[object Object]"  恒为 true
// → 任何拿到这些 id 的对话都被当成「更旧的副本」删掉。叠加计数器退回（活集合最大号
//   从 88 掉到 59 → 新建必然是 branch-60）→ **每次新建对话都被秒删、且永远建不成**。
// 这里统一取值：对象取 deletedAt，取不到则返回空串（空串 = 不作墓碑，绝不误删）。
function _tombTs(v) {
    if (v == null) return '';
    if (typeof v === 'object') return String(v.deletedAt || '');
    return String(v);
}

// ── 合并（唯一实现，启动 hydrate 与线上同步共用）──────────────
// 返回 {branches, deleted, added}；added = 本地有、后端没有的对话数（>0 说明要推上去）
function _mergeBranchSets(localBranches, localDeleted, remote) {
    var rb = (remote && remote.branches) || {};
    var rd = (remote && remote.deleted) || {};
    var br = {}, dl = {}, added = 0;

    Object.keys(localBranches || {}).forEach(function (id) { br[id] = localBranches[id]; });
    Object.keys(localDeleted || {}).forEach(function (id) { dl[id] = _tombTs(localDeleted[id]); });

    // 后端墓碑 → 删掉本地那份（除非本地那份更新，即"删后又改")
    Object.keys(rd).forEach(function (id) {
        var ts = _tombTs(rd[id]);
        if (ts && br[id] && String(br[id].updatedAt || '') <= ts) delete br[id];
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

    // ★ 墓碑作废：某 id 有活着的副本、且它比墓碑新 → 这条墓碑已经没有意义
    //   （墓碑的唯一职责是挡住「更旧的副本复活」，副本更新它就作废）。不删它，
    //   它会被反复推给后端，把刚恢复/刚编辑过的对话再删一次。
    Object.keys(dl).forEach(function (id) {
        var b = br[id];
        if (b && String(b.updatedAt || '') > String(dl[id] || '')) delete dl[id];
    });

    // ★ 2026-09-30 数据归一：清掉已废弃的 isTemporary 字段。
    //   用户取消「临时对话 / 空草稿 / 默认占位」概念后，该字段再没有任何读取方，
    //   留着只会在新老数据混用时误导人。**只删字段、绝不碰 updatedAt**
    //   （碰了就等于凭空"赢过"别的设备的副本）。
    Object.keys(br).forEach(function (id) {
        if (br[id] && Object.prototype.hasOwnProperty.call(br[id], 'isTemporary')) {
            delete br[id].isTemporary;
        }
    });

    return { branches: br, deleted: dl, added: added };
}

/** 显式删除对话时调用：**只有这里**写墓碑（原因见 _stampChanged 的注释）。 */
function markBranchDeleted(branchId) {
    if (!branchId) return;
    _chatsDeleted[branchId] = new Date().toISOString();
    delete _chatsMeta[branchId];
    if (typeof setUserChats === 'function') setUserChats();   // 立刻推后端
}

function _cacheKey(k) { return window.identityKey(k); }

/** 旧版把全部对话全文缓存在 localStorage（`ai-branches`）。这里只读它**一次**用于迁移：
 *  「本地有、后端没有」的对话要推上去；推成功后立刻清掉，把配额还回来。 */
function _readLegacyCache() {
    try { return JSON.parse(localStorage.getItem(_cacheKey(_UC_LEGACY_BRANCHES)) || '{}') || {}; } catch (e) { return {}; }
}

/** 清掉旧缓存（含墓碑镜像）。对话真相在本机后端；这两份副本只会占满配额，
 *  而且浏览器里的旧墓碑会被反复推上来把刚恢复/刚编辑的对话再删一次
 *  （2026-09-27 二次误删 7 个对话就是这么来的）。 */
function _purgeLegacyCache() {
    try {
        localStorage.removeItem(_cacheKey(_UC_LEGACY_BRANCHES));
        localStorage.removeItem(_cacheKey(_UC_LEGACY_DELETED));
        // 命名空间以前的老键：auth.js 已把它复制到 guest 命名空间（复制过才删）
        if (localStorage.getItem('ai-branches::guest') != null) {
            localStorage.removeItem('ai-branches');
            localStorage.removeItem('ai-branches-deleted');
        }
    } catch (e) {}
}

/** 把当前 state 的对话整成推送报文 */
function _snapshotChats() {
    var out = {};
    _stampChanged();
    Object.keys(state.branches || {}).forEach(function (id) {
        var b = state.branches[id];
        if (!b) return;
        // ★ 后端接管的对话（有账本 → 由 projection 写）：前端不再回推，
        //   否则 1.5s 后就会用「截断版」盖掉后端写的完整版（自噬）。
        if (_ucTreeMode === 'on' && _ucManaged[id]) return;
        out[id] = b;
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
        if (!r.ok) { _hydrateFailed(); return false; }
        var _j = await r.json();
        remote = _j.chats || {};
        _ucTreeMode = _j._tree || 'off';
        _ucManaged = {};
        (_j._managed || []).forEach(function (id) { _ucManaged[id] = 1; });
    } catch (e) {
        _hydrateFailed();
        return false;
    }
    // 旧缓存只作一次性迁移输入：本地有、后端没有的对话（本功能上线前聊的）→ 推上去
    var legacy = _readLegacyCache();
    var m = _mergeBranchSets(legacy, {}, remote);
    state.branches = m.branches;
    _chatsDeleted = m.deleted;
    Object.keys(state.branches).forEach(function (id) { _chatsMeta[id] = _branchFp(state.branches[id]); });
    var pushed = true;
    if (m.added > 0) pushed = await pushUserChats();   // 推成功才清旧缓存，避免本地独有的对话丢掉
    if (pushed) _purgeLegacyCache();
    return true;
}

/** 后端读不到 → 如实报错（不假装有数据、也不退到旧缓存）。 */
function _hydrateFailed() {
    if (typeof showToast === 'function') showToast(t('uchat.backendDown'), 'error');
}

// ── 线上同步（收到 SSE 推送后调用）───────────────
/** 拉全量并与本地合并；返回真正变了的对话数。生成回复期间跳过（别踩掉流式输出）。 */
async function syncChatsFromServer() {
    // ★ 生成期间不再「丢弃」通知（旧实现直接 return 0 → 这一次同步永久丢失）：
    //   记一笔待同步，等本页生成结束立刻补拉（见 flushPendingSync）。
    if (state.isGenerating) { _ucPendingSync = true; return 0; }
    var r;
    try { r = await fetch('/api/user/chats'); } catch (e) { return 0; }
    if (!r.ok) return 0;
    var _j = await r.json();
    _ucTreeMode = _j._tree || _ucTreeMode;
    _ucManaged = {};
    (_j._managed || []).forEach(function (id) { _ucManaged[id] = 1; });
    var remote = _j.chats || {};
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
        if (r.ok) { _chatsLastPush = body; return true; }   // 只有成功才记，失败下次重推
    } catch (e) {
        // 后端不可达：数据仍在内存里，下次写入会再推
    }
    return false;
}

// ── 离开页面前把最后一笔推掉 ────────────────────
document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && _chatsPushTimer) pushUserChats();
});
window.addEventListener('pagehide', function () {
    if (_chatsPushTimer) pushUserChats();
});

// ── 多端实时同看（后端广播的 chats_delta）────────────────────
// 另一台设备生成时，后端把它的流式事件广播过来；本页把它们拼成一条
// 「实时预览」消息，实现「A 生成，B 也看得见过程」。生成结束后后端会推
// chats → 全量拉取，预览消息被后端投影出的正式消息整体替换。
var _ucRemoteLive = {};       // branch_id → {content, steps, id}
var _ucLiveTimer = null;
var _ucLiveBranch = '';

function applyChatsDelta(p) {
    if (!p || !p.event) return;
    if (p.client_id && p.client_id === _ucClientId) return;      // 自己的回声 → 丢
    if (state.isGenerating) { _ucPendingSync = true; return; }   // 本页也在生成 → 结束后补拉
    var bid = p.branch_id || '';
    var b = (state.branches || {})[bid];
    if (!bid || !b) return;                                      // 本页没有这条对话
    var ev = p.event;
    var live = _ucRemoteLive[bid];
    if (!live) { live = _ucRemoteLive[bid] = { content: '', steps: [], id: 'live-' + bid }; }
    var t = ev.type;
    if (t === 'token') { live.content += (ev.text || ''); }
    else if (t === 'reasoning') {
        // 与发起端 handleReasoning 同语义：连续 reasoning 增量聚合成一个块，
        // 否则后端按片段广播时，这里会每个片段渲染一个「模型推理」块。
        var _lastStep = live.steps.length ? live.steps[live.steps.length - 1] : null;
        if (_lastStep && _lastStep.type === 'model_reasoning') { _lastStep.text += (ev.text || ''); }
        else { live.steps.push({ type: 'model_reasoning', text: ev.text || '', step: 0 }); }
    }
    else if (t === 'tool_call') { live.steps.push({ type: 'tool_call', name: ev.name || '', input: ev.input || '' }); }
    else if (t === 'tool_result') { live.steps.push({ type: 'tool_result', name: ev.name || '', content: (ev.content || '').substring(0, 500) }); }
    else if (t === 'final') { live.content = ev.text || live.content; }
    else { return; }
    _ucLiveBranch = bid;
    if (_ucLiveTimer) return;
    _ucLiveTimer = setTimeout(function () {
        _ucLiveTimer = null;
        var id = _ucLiveBranch;
        var lv = _ucRemoteLive[id];
        var br = (state.branches || {})[id];
        if (!lv || !br) return;
        var msgs = br.messages || (br.messages = []);
        var last = msgs[msgs.length - 1];
        if (!last || last.id !== lv.id) {
            msgs.push({ id: lv.id, role: 'assistant', content: lv.content,
                        timestamp: new Date().toISOString(), branchId: id,
                        reasoningSteps: lv.steps, _remoteLive: true });
        } else {
            last.content = lv.content;
            last.reasoningSteps = lv.steps;
        }
        if (state.currentBranchId === id && typeof renderMessages === 'function') renderMessages();
        if (typeof updateHistoryList === 'function') updateHistoryList();
    }, 150);
}

/** 生成结束后调用：补做生成期间被推迟的同步 + 收掉实时预览。 */
async function flushPendingSync() {
    _ucRemoteLive = {};
    if (!_ucPendingSync) return;
    _ucPendingSync = false;
    await syncChatsFromServer();
}

/** 消息 id → 账本 turn_id（投影消息形如 msg-u-tXXXX / agent-tXXXX）。 */
function turnIdOfMessageId(mid) {
    var m = /^(?:msg-u-|agent-)(t[0-9a-f]+)$/.exec(String(mid || ''));
    return m ? m[1] : '';
}

/** 把本地的「编辑消息 / 删除后续消息」报给后端（账本补偿事件）→ 后端重新投影。
 *  只对「后端接管的对话」有效（它才有账本可套用补偿）。 */
async function pushChatOps(branchId, ops) {
    if (_ucTreeMode !== 'on' || !branchId || !(ops && ops.length)) return false;
    var norm = ops.map(function (op) {
        var o = {}; for (var k in op) { o[k] = op[k]; }
        if (o.type === 'truncate' && o.from) {
            var t1 = turnIdOfMessageId(o.from); if (t1) o.from = t1;
        }
        if (o.type === 'edit' && o.target) {
            var t2 = turnIdOfMessageId(o.target); if (t2) o.target = t2;
        }
        // ★ delete 不规整成 turn_id：要区分 msg-u-X（删提问）与 agent-X（删回答），
        //   投影按完整消息 id 精确剔除（规整后两条会一起被删）。
        return o;
    });
    try {
        var r = await fetch('/api/user/chat_ops', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ branch_id: branchId, ops: norm })
        });
        return r.ok;
    } catch (e) { return false; }
}

window.hydrateUserChats = hydrateUserChats;
window.setUserChats = setUserChats;
window.pushUserChats = pushUserChats;
window.markBranchDeleted = markBranchDeleted;
window.syncChatsFromServer = syncChatsFromServer;
window.getClientId = function () { return _ucClientId; };
window.applyChatsDelta = applyChatsDelta;
window.flushPendingSync = flushPendingSync;
window.pushChatOps = pushChatOps;
window.isBranchManaged = function (id) { return _ucTreeMode === 'on' && !!_ucManaged[id]; };
window.isBranchManaged = function (id) { return _ucTreeMode === 'on' && !!_ucManaged[id]; };
window.turnIdOfMessageId = turnIdOfMessageId;
