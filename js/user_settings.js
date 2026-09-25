// ==================== 个人设置的后端存储（用户 2026-09-26 定）====================
//
// 这些设置原本只存浏览器 localStorage → 换设备 / 换浏览器就全丢，
// 而且 API Key 明文躺在浏览器里。
// 现在：**后端（个人账号数据）为权威，localStorage 作本地缓存**。
//
//   · 启动：hydrateUserSettings() 把后端设置灌回 localStorage，
//           其它模块照旧用 localStorage.getItem 同步读 —— 一行都不用改
//   · 写入：setUserSetting(k, v) 写本地缓存（同步、立刻生效）
//           + 标脏，节流 800ms 推后端
//   · 离开页面：visibilitychange(hidden) / pagehide 强制推一次，避免丢最后一笔
//
// 为什么保留 localStorage 缓存：面板打开要立刻有值，不能等网络；
// 而且后端不可达时本地仍能用。这不是兜底，是缓存层。
//
// 存储位置（后端）：workspace/users/<身份>/settings.json，见 backend/tools/user_settings.py

// 同步到后端的键（白名单，必须与后端 MANAGED_KEYS 一致）
// 其余键（面板位置 / 主题 / 侧栏宽度…）是纯本机偏好，留在浏览器本地不同步。
var BACKEND_SETTING_KEYS = [
    'ai-settings',                  // 温度 / 上下文轮次 / maxTokens
    'ai-current-model',             // 当前选中的模型
    'ragagent-provider-settings',   // 服务商设置（含 API Key）
    'ragagent-custom-providers',    // 自定义服务商（含 API Key）
    'ragagent-provider-overrides',  // 服务商覆盖项
    'ragagent-enabled-models',      // 已启用的模型
    'ragagent-agent-config',        // 智能体配置（工具 / skills / 超时）
    'ragagent-neo4j-config'         // Neo4j 连接（含密码）
];

var _settingsPushTimer = null;
var _settingsHydrated = false;

// ── 本地缓存读写 ────────────────────────────────

/** 只写 localStorage，不触发后端推送（hydrate 自己用，避免回声） */
function _rawSetItem(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* 配额满 */ }
}

/** 把当前 8 个键的快照整成对象（值尽量还原成 JSON 类型） */
function _snapshotSettings() {
    var out = {};
    for (var i = 0; i < BACKEND_SETTING_KEYS.length; i++) {
        var k = BACKEND_SETTING_KEYS[i];
        var v = localStorage.getItem(k);
        if (v === null) continue;
        try { out[k] = JSON.parse(v); } catch (e) { out[k] = v; }   // 非 JSON 的（如模型 id）
    }
    return out;
}

// ── 启动：后端 → 本地缓存 ───────────────────────

/** 返回 Promise。必须在 loadSettings/initProviderSystem 之前完成。 */
async function hydrateUserSettings() {
    var remote = null;
    try {
        var r = await fetch('/api/user/settings');
        if (!r.ok) return false;
        remote = (await r.json()).settings || {};
    } catch (e) {
        return false;   // 后端不可达：本地缓存继续用
    }

    if (Object.keys(remote).length === 0) {
        // 后端还没有这个人的设置，而本地已有一份（本功能上线前配的）
        // → 推上去，完成一次性迁移。推完后端非空，此分支不会再来。
        var local = _snapshotSettings();
        if (Object.keys(local).length > 0) await pushUserSettings();
        _settingsHydrated = true;
        return true;
    }

    // 后端有：灌回本地缓存（后端权威）
    for (var i = 0; i < BACKEND_SETTING_KEYS.length; i++) {
        var k = BACKEND_SETTING_KEYS[i];
        if (!(k in remote)) continue;
        var v = remote[k];
        _rawSetItem(k, (typeof v === 'string') ? v : JSON.stringify(v));
    }
    _settingsHydrated = true;
    return true;
}

// ── 写入：本地缓存 + 节流推后端 ─────────────────

/** 写设置。调用方自己传字符串（与 localStorage.setItem 同签名）。 */
function setUserSetting(key, value) {
    _rawSetItem(key, value);
    if (BACKEND_SETTING_KEYS.indexOf(key) < 0) return;   // 非同步键：纯本地
    if (_settingsPushTimer) clearTimeout(_settingsPushTimer);
    _settingsPushTimer = setTimeout(pushUserSettings, 800);
}

/** 立刻把 8 个键的当前值推给后端（合并语义，只影响白名单键） */
async function pushUserSettings() {
    _settingsPushTimer = null;
    var snap = _snapshotSettings();
    if (Object.keys(snap).length === 0) return;
    try {
        await fetch('/api/user/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: snap })
        });
    } catch (e) {
        // 后端不可达：本地缓存仍在，下次写入会再推
    }
}

// ── 离开页面前把最后一笔推掉 ────────────────────
// visibilitychange(hidden) 比 unload 可靠，且页面还没销毁，普通 fetch 通常能发完。
document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && _settingsPushTimer) pushUserSettings();
});
window.addEventListener('pagehide', function () {
    if (_settingsPushTimer) pushUserSettings();
});

window.hydrateUserSettings = hydrateUserSettings;
window.setUserSetting = setUserSetting;
window.pushUserSettings = pushUserSettings;
window.BACKEND_SETTING_KEYS = BACKEND_SETTING_KEYS;
