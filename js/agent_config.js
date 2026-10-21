// ==================== 智能体配置面板 UI ====================

// 工具分组（Hermes toolset 风格：按能力域分组勾选）
const TOOL_GROUPS = [
    { name: t('agentcfg.gr.graph'), tools: ['graph_query'] },
    { name: t('agentcfg.gr.database'), tools: ['shared_search', 'shared_upload'] },
    { name: t('agentcfg.gr.web'), tools: ['web_search', 'web_extract'] },
    { name: t('agentcfg.gr.skills'), tools: ['skill_list', 'skill_view', 'skill_write'] },
    { name: t('agentcfg.gr.file'), tools: ['read_file', 'write_file', 'list_files', 'list_outputs', 'search_files', 'find_files', 'edit_file'] },
    { name: t('agentcfg.gr.code'), tools: ['run_code', 'terminal'] },
    { name: t('agentcfg.gr.literature'), tools: ['crossref_lookup', 'arxiv_search', 'semantic_scholar_search'] },
    { name: t('agentcfg.gr.github'), tools: ['gh_repo_info', 'gh_list_dir', 'gh_read_file', 'gh_search_repos', 'gh_search_code', 'gh_download_tarball'] },
    { name: t('agentcfg.gr.memory'), tools: ['search_history', 'conversation_notes'] },
    { name: t('agentcfg.gr.task'), tools: ['todo'] },
    { name: t('agentcfg.gr.subagent'), tools: ['delegate_task'] },
    { name: t('agentcfg.gr.documents'), tools: ['mineru_parse'] },
    { name: 'XWiki', tools: ['wiki_read', 'wiki_write'] },
];

// ── 工具：从后端 /api/tools 动态加载 + 按分组渲染勾选 ──
async function renderToolsGroups() {
    const container = document.getElementById('tools-groups-container');
    if (!container) return;

    // 从后端拿工具描述
    let toolsInfo = {};  // name -> description
    try {
        const resp = await fetch(BACKEND_API_URL + '/api/tools');
        if (resp.ok) {
            const data = await resp.json();
            (data.tools || []).forEach(t => { toolsInfo[t.name] = t.description || ''; });
        }
    } catch (e) { /* 后端不可用时降级 */ }

    const enabled = state.agentConfig?.enabledTools || [];
    const html = TOOL_GROUPS.map(g => {
        // 该组所有工具是否已在后端存在（过滤不存在的）
        const items = g.tools.map(name => {
            const desc = toolsInfo[name] ? '' : '';  // 描述作为 title 提示
            return `<label class="config-option" title="${(toolsInfo[name]||'').replace(/"/g,'&quot;')}">
                <input type="checkbox" class="tool-checkbox" data-tool="${name}" ${enabled.includes(name) ? 'checked' : ''}>
                <span class="config-option-label">${name}</span>
            </label>`;
        }).join('');
        return `<div style="margin-bottom:0.5rem;border-bottom:1px dashed rgba(255,255,255,0.1);padding-bottom:0.3rem;">
            <div style="font-size:0.78rem;color:var(--gray);margin-bottom:0.2rem;">${g.name}</div>
            <div style="display:flex;flex-wrap:wrap;gap:0.2rem 0.8rem;">${items}</div>
        </div>`;
    }).join('');

    container.innerHTML = html || '<div style="color:var(--gray)">' + t('agentcfg.noTools') + '</div>';

}


// ── 动态加载技能列表并生成勾选框 ──────────────
async function renderSkillsCheckboxes() {
    if (!skillsConfigContainer) return;
    const container = skillsConfigContainer;

    // 加载技能注册表（对齐 Hermes：走后端 /api/skills；失败时回退前端 index.json）
    let skills = [];
    try {
        const resp = await fetch(BACKEND_API_URL + '/api/skills');
        if (resp.ok) {
            const data = await resp.json();
            skills = data.skills || [];
        }
    } catch(e) { /* 降级 */ }
    if (skills.length === 0) {
        try {
            const resp2 = await fetch('skills/index.json');
            if (resp2.ok) {
                const data2 = await resp2.json();
                skills = data2.skills || [];
            }
        } catch(e2) { /* 降级 */ }
    }


    // 获取当前已启用的技能列表
    const enabled = state.agentConfig?.loadedSkills || [];

    container.innerHTML = skills.map(s => `
        <label class="config-option" style="align-items:flex-start;">
            <input type="checkbox" class="skill-checkbox" data-skill="${s.name}"
                   style="margin-top:0.3rem;flex:none;"
                   ${enabled.includes(s.name) ? 'checked' : ''}>
            <span class="config-option-label" style="line-height:1.4;">${s.name}<span class="config-option-desc" style="display:block;"> ${s.description}</span></span>
        </label>
    `).join('');

    // 绑定事件（仅 UI 勾选；提交由「保存」按钮统一处理）
    container.querySelectorAll('.skill-checkbox').forEach(cb => {
        // 不实时保存，点击「保存」按钮时统一提交
    });
}

function loadSkillsConfigFromState() {
    const enabled = state.agentConfig?.loadedSkills || [];
    document.querySelectorAll('.skill-checkbox').forEach(cb => {
        cb.checked = enabled.includes(cb.dataset.skill);
    });
}

// ── 权限模式（安全/询问/完全访问） ─────────────────
function initPermissionModeControls() {
    const radios = document.querySelectorAll('input[name="permission-mode"]');
    radios.forEach(radio => {
        radio.addEventListener('change', () => {
            const mode = radio.value;
            // 选取非安全模式时弹出静态风险警告（跟大模型无关，纯前端提醒）
            if (mode !== 'safe') {
                const ok = confirm(t('agentcfg.permWarn'));
                if (!ok) {
                    const cur = state.agentConfig?.permissionMode || 'safe';
                    const curRadio = document.querySelector(`input[name="permission-mode"][value="${cur}"]`);
                    if (curRadio) curRadio.checked = true;
                    return;
                }
            }
            // 不实时写入 state，点击「保存」按钮时统一提交
        });
    });
}

function loadPermissionModeFromState() {
    const mode = state.agentConfig?.permissionMode || 'safe';
    const radio = document.querySelector(`input[name="permission-mode"][value="${mode}"]`);
    if (radio) radio.checked = true;
}

function loadAgentEngineFromState() {
    const eng = state.agentConfig?.engine || 'custom';
    const radio = document.querySelector(`input[name="agent-engine"][value="${eng}"]`);
    if (radio) radio.checked = true;
    const rounds = (state.agentConfig?.maxRounds === 0 || state.agentConfig?.maxRounds) ? state.agentConfig.maxRounds : 20;
    const sel = document.getElementById('agent-max-rounds');
    if (sel) sel.value = String(rounds);
}

// ── 子智能体设置（已从设置面板迁移至此，存储仍用 state.settings.*）──
function loadSubagentConfigToPanel() {
    const s = state.settings || {};
    const _se = document.getElementById('subagent-enabled');
    const _sr = document.getElementById('subagent-rounds');
    const _sp = document.getElementById('subagent-parallel');
    const _sm = document.getElementById('subagent-maxtasks');
    const _sn = document.getElementById('subagent-nudge');
    const _sw = document.getElementById('subagent-warn');
    const _sab = document.getElementById('subagent-abort');
    const _sfs = document.getElementById('subagent-feed-steps');
    if (_se) _se.checked = (s.subagentEnabled !== false);
    if (_sr) _sr.value = (s.subagentMaxRounds ?? 4);
    if (_sp) _sp.value = (s.subagentMaxParallel ?? 2);
    if (_sm) _sm.value = (s.subagentMaxTasks ?? 4);
    if (_sn) _sn.value = (s.subagentNudge ?? 60);
    if (_sw) _sw.value = (s.subagentWarn ?? 150);
    if (_sab) _sab.value = (s.subagentAbort ?? 300);
    if (_sfs) _sfs.checked = (s.subagentFeedSteps === true);
}

function saveSubagentConfigFromPanel() {
    const _se = document.getElementById('subagent-enabled');
    const _sr = document.getElementById('subagent-rounds');
    const _sp = document.getElementById('subagent-parallel');
    const _sm = document.getElementById('subagent-maxtasks');
    const _sn = document.getElementById('subagent-nudge');
    const _sw = document.getElementById('subagent-warn');
    const _sa = document.getElementById('subagent-abort');
    const _sfs = document.getElementById('subagent-feed-steps');
    if (_se) state.settings.subagentEnabled = !!_se.checked;
    if (_sfs) state.settings.subagentFeedSteps = !!_sfs.checked;
    // 0 = 无限（与主智能体「工具循环步数」同一约定）。旧实现 Math.min(20, …) 把上限锁死在 20，
// 用户 2026-09-27 报「设置不了无限」——那正是多余的钳制，去掉。
if (_sr) state.settings.subagentMaxRounds = Math.max(0, parseInt(_sr.value) || 0);
    if (_sp) state.settings.subagentMaxParallel = Math.min(8, Math.max(1, parseInt(_sp.value) || 2));
    if (_sm) state.settings.subagentMaxTasks = Math.min(8, Math.max(1, parseInt(_sm.value) || 4));
    if (_sn) state.settings.subagentNudge = Math.max(0, parseInt(_sn.value) || 0);
    if (_sw) state.settings.subagentWarn = Math.max(0, parseInt(_sw.value) || 0);
    if (_sa) state.settings.subagentAbort = Math.min(3600, Math.max(30, parseInt(_sa.value) || 300));
    try { setUserSetting('ai-settings', JSON.stringify(state.settings)); } catch (e) { /* ignore */ }
}

// 智能体相关控件已整块并入设置面板（「智能体」/「连接与服务」两个分类）：
// 本函数只做一次性控件绑定；面板显隐完全归设置面板，不再有第二套壳。
function initAgentConfigPanel() {
    // 保存 MinerU Token 到后端
    if (panelMineruSave) {
        panelMineruSave.addEventListener('click', function () {
            const token = panelMineruToken ? panelMineruToken.value.trim() : '';
            fetch(BACKEND_API_URL + '/api/config/mineru', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: token })
            })
            .then(r => r.json())
            .then(function (d) {
                if (panelMineruMsg) {
                    panelMineruMsg.textContent = d.configured ? t('agentcfg.saved') : t('agentcfg.tokenCleared');
                }
            })
            .catch(function () {
                if (panelMineruMsg) panelMineruMsg.textContent = t('agentcfg.saveFail');
            });
        });
    }

    // 工具开关：仅 UI 勾选，不实时保存（点设置面板「保存」统一提交）

    // 首次加载当前配置到 UI
    loadToolsConfigFromState();
    initPermissionModeControls();
}

// MinerU Token：从后端读入预填（打开设置面板时调用）
function loadMineruConfigToPanel() {
    if (!panelMineruToken) return;
    fetch(BACKEND_API_URL + '/api/config/mineru')
        .then(r => r.json())
        .then(function (d) {
            if (panelMineruToken) panelMineruToken.value = d.token || '';
            if (panelMineruMsg) panelMineruMsg.textContent = d.configured ? t('agentcfg.configured') : '';
        })
        .catch(function () {
            if (panelMineruMsg) panelMineruMsg.textContent = t('agentcfg.readFail');
        });
}

// 设置面板每次打开：把 state 回填到「智能体 / 连接与服务」两个分类
function loadAgentConfigPanes() {
    loadToolsConfigFromState();
    loadPermissionModeFromState();
    loadAgentEngineFromState();
    loadSubagentConfigToPanel();
    loadTimeoutConfigToPanel();
    syncNeo4jConfigToPanel();
    renderSkillsCheckboxes();
    loadMineruConfigToPanel();
}
window.loadAgentConfigPanes = loadAgentConfigPanes;

function loadToolsConfigFromState() {
    const cfg = state.agentConfig;
    if (!cfg) return;
    // 动态渲染工具分组（异步，用 cfg.enabledTools 同步勾选态）
    renderToolsGroups();

    loadSkillsConfigFromState();
    loadPermissionModeFromState();
    loadAgentEngineFromState();
}

function saveAgentConfigToLocal() {
    try {
        setUserSetting('ragagent-agent-config', JSON.stringify(state.agentConfig));
    } catch (e) { /* ignore */ }
}

// 「保存」按钮：统一读取面板 UI 状态 → 提交到 state/localStorage → 收起面板
function saveAgentConfig() {
    // 工具：唯一真源 = 动态分组勾选（后端 /api/tools 渲染的 .tool-checkbox）
    const tools = [];
    document.querySelectorAll('#tools-groups-container .tool-checkbox:checked')
        .forEach(cb => { if (cb.dataset.tool) tools.push(cb.dataset.tool); });

    const skills = [];
    document.querySelectorAll('.skill-checkbox:checked').forEach(cb => {
        if (cb.dataset.skill) skills.push(cb.dataset.skill);
    });

    const modeRadio = document.querySelector('input[name="permission-mode"]:checked');
    const mode = modeRadio ? modeRadio.value : 'safe';

    state.agentConfig.enabledTools = tools;
    state.agentConfig.loadedSkills = skills;
    state.agentConfig.permissionMode = mode;
    const engRadio = document.querySelector('input[name="agent-engine"]:checked');
    state.agentConfig.engine = engRadio ? engRadio.value : 'custom';
    const roundSel = document.getElementById('agent-max-rounds');
    const rounds = roundSel ? parseInt(roundSel.value, 10) : 8;
    state.agentConfig.maxRounds = isNaN(rounds) ? 20 : rounds;
    state.agentConfig.settingsRev = 2;   // 用户手动保存过 = 迁移不再覆盖他的选择
    saveAgentConfigToLocal();

    // 子智能体设置（写入 state.settings.*，独立持久化到 ai-settings）
    saveSubagentConfigFromPanel();

    // 主智能体超时保护三级（已迁入本面板；id 与 branching.js 读写保持一致）
    const _tn = document.getElementById('timeout-nudge');
    const _tw = document.getElementById('timeout-warn');
    const _ta = document.getElementById('timeout-abort');
    if (_tn) state.settings.timeoutNudge = Math.max(0, parseInt(_tn.value) || 0);
    if (_tw) state.settings.timeoutWarn = Math.max(0, parseInt(_tw.value) || 0);
    if (_ta) state.settings.timeoutAbort = Math.max(0, parseInt(_ta.value) || 0);
    try { setUserSetting('ai-settings', JSON.stringify(state.settings)); } catch (e) { /* ignore */ }

    // 面板收起与「已保存」提示统一由设置面板的保存流程处理（branching.js saveSettings）
}

function loadAgentConfigFromLocal() {
    try {
        const saved = localStorage.getItem('ragagent-agent-config');
        if (saved) {
            const parsed = JSON.parse(saved);
            // 直接用用户保存的配置，不强制合并默认工具
            // 新增的工具默认不会出现在已保存列表中，用户需要手动开启
            state.agentConfig.enabledTools = parsed.enabledTools || [];
            state.agentConfig.permissionMode = parsed.permissionMode || 'safe';
            state.agentConfig.engine = parsed.engine || 'custom';
            state.agentConfig.maxRounds = (parsed.maxRounds === 0 || parsed.maxRounds) ? parsed.maxRounds : 20;
            // 技能勾选状态同样持久化（旧存档无此字段时保留默认）
            if (Array.isArray(parsed.loadedSkills)) {
                state.agentConfig.loadedSkills = parsed.loadedSkills;
            }
            // 一次性放开（2026-09-28 用户定）：除访问权限外全部放开、轮次无限、超时保护只留「一天硬停」。
            // 用 settingsRev 守卫，只对旧存档生效一次；此后用户改什么就是什么。
            if (parsed.settingsRev !== 2) {
                state.agentConfig.enabledTools = TOOL_GROUPS.flatMap(g => g.tools);
                state.agentConfig.loadedSkills = ['self-modify', 'mineru-agent-parse', 'literature-translate',
                    'literature-contradiction', 'literature-search', 'code-search', 'document-entry'];
                state.agentConfig.maxRounds = 0;
                state.agentConfig.settingsRev = 2;
                state.settings.timeoutNudge = 0; state.settings.timeoutWarn = 0; state.settings.timeoutAbort = 86400;
                state.settings.subagentMaxRounds = 0; state.settings.subagentMaxParallel = 8; state.settings.subagentMaxTasks = 8;
                state.settings.subagentNudge = 0; state.settings.subagentWarn = 0; state.settings.subagentAbort = 86400;
                saveAgentConfigToLocal();
                try { setUserSetting('ai-settings', JSON.stringify(state.settings)); } catch (e) { /* ignore */ }
            }
            // XWiki 工具上线（2026-10-21）：旧存档里没有 wiki_read/wiki_write 的，一次性补进已启用列表。
            // 用 wikiRev 守卫，只补一次；之后用户取消勾选就保持取消，不再强塞。
            if (parsed.wikiRev !== 1) {
                const et = new Set(state.agentConfig.enabledTools || []);
                et.add('wiki_read');
                et.add('wiki_write');
                state.agentConfig.enabledTools = Array.from(et);
                state.agentConfig.wikiRev = 1;
                saveAgentConfigToLocal();
            }
        }
    } catch (e) { /* ignore */ }
}

// ── Neo4j 连接（面板内） ──────────────────────────
function syncNeo4jConfigToPanel() {
    if (panelNeo4jUri) panelNeo4jUri.value = state.neo4j.uri || '';
    if (panelNeo4jUser) panelNeo4jUser.value = state.neo4j.user || '';
    updateNeo4jStatusUI(state.neo4j.connected);
    if (panelNeo4jConnect) {
        panelNeo4jConnect.textContent = state.neo4j.connected ? t('agentcfg.connected') : t('agentcfg.connect');
    }
}

if (panelNeo4jConnect) {
    panelNeo4jConnect.addEventListener('click', async function() {
        const uri = (panelNeo4jUri ? panelNeo4jUri.value.trim() : '') || state.neo4j.uri || '';
        const user = (panelNeo4jUser ? panelNeo4jUser.value.trim() : '') || state.neo4j.user || '';
        const pass = panelNeo4jPass ? panelNeo4jPass.value : '';

        if (!pass && !neo4jConnector.isConnected()) {
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = t('agentcfg.enterPass');
                panelNeo4jMsg.style.color = 'var(--error, #ef4444)';
            }
            return;
        }

        if (neo4jConnector.isConnected() && !pass) {
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = t('agentcfg.connected');
                panelNeo4jMsg.style.color = 'var(--success, #10b981)';
            }
            return;
        }

        const password = pass || state.neo4j.password;

        panelNeo4jConnect.disabled = true;
        panelNeo4jConnect.textContent = t('agentcfg.connecting');
        if (panelNeo4jMsg) {
            panelNeo4jMsg.textContent = '';
        }

        const result = await neo4jConnector.connect(uri, user, password);

        panelNeo4jConnect.disabled = false;
        if (result.success) {
            panelNeo4jConnect.textContent = t('agentcfg.connected');
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = t('agentcfg.connectOk');
                panelNeo4jMsg.style.color = 'var(--success, #10b981)';
            }
            state.neo4j.uri = uri;
            state.neo4j.user = user;
            state.neo4j.password = password;
            setUserSetting('ragagent-neo4j-config', JSON.stringify(state.neo4j));
            if (neo4jUri) neo4jUri.value = uri;
            if (neo4jUser) neo4jUser.value = user;
            if (neo4jPassword) neo4jPassword.value = password;
            updateNeo4jStatusUI(true);
        } else {
            panelNeo4jConnect.textContent = t('agentcfg.connect');
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = result.error || t('agentcfg.connectFail');
                panelNeo4jMsg.style.color = 'var(--error, #ef4444)';
            }
            updateNeo4jStatusUI(false);
        }
    });
}

// 主智能体超时保护：打开「智能体配置」面板时从 state.settings 回填
function loadTimeoutConfigToPanel() {
    const st = state.settings || {};
    const _tn = document.getElementById('timeout-nudge');
    const _tw = document.getElementById('timeout-warn');
    const _ta = document.getElementById('timeout-abort');
    if (_tn) _tn.value = (st.timeoutNudge ?? 120);
    if (_tw) _tw.value = (st.timeoutWarn ?? 240);
    if (_ta) _ta.value = (st.timeoutAbort ?? 420);
}
