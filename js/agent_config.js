// ==================== 智能体配置面板 UI ====================

// 工具分组（Hermes toolset 风格：按能力域分组勾选）
const TOOL_GROUPS = [
    { name: '图谱 (graph)', tools: ['graph_schema', 'execute_cypher'] },
    { name: '数据库/共享库 (database)', tools: ['shared_search', 'shared_upload'] },
    { name: '联网 (web)', tools: ['web_search', 'web_extract'] },
    { name: '技能 (skills)', tools: ['skill_list', 'skill_view', 'skill_write'] },
    { name: '文件 (file)', tools: ['read_file', 'write_file', 'list_files'] },
    { name: '代码/终端 (code)', tools: ['run_code', 'terminal'] },
    { name: '记忆/历史 (memory)', tools: ['search_history', 'conversation_notes'] },
    { name: '任务 (task)', tools: ['todo'] },
    { name: '子智能体 (subagent)', tools: ['delegate_task'] },
    { name: '文档解析 (documents)', tools: ['mineru_parse'] },
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

    container.innerHTML = html || '<div style="color:var(--gray)">（后端未返回工具）</div>';

    // 隐藏旧的硬编码工具勾选（避免重复读取）；保留 DOM 兼容
    document.querySelectorAll('#tool-graph-schema, #tool-execute-cypher, #tool-skill-list, #tool-skill-view, #tool-web-search, #tool-web-extract, #tool-todo, #tool-notes, #tool-run-code, #tool-read-file, #tool-write-file, #tool-list-files, #tool-skill-write, #tool-search-history, #tool-shared-search, #tool-shared-upload, #tool-mineru').forEach(cb => {
        cb.style.display = 'none';
        if (cb.closest('label')) cb.closest('label').style.display = 'none';
    });
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

    if (skills.length === 0) {
        skills = [{ name: 'graph-guide', description: '催化降解图谱查询指引' }];
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
                const ok = confirm(
                    '⚠️ 非安全模式授予智能体对【系统文件】及【软件自身】的读写/修改权限，\n' +
                    '可能导致不可逆的修改甚至崩溃。确定要继续吗？'
                );
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
    const rounds = (state.agentConfig?.maxRounds === 0 || state.agentConfig?.maxRounds) ? state.agentConfig.maxRounds : 8;
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
    if (_se) _se.checked = (s.subagentEnabled !== false);
    if (_sr) _sr.value = (s.subagentMaxRounds ?? 4);
    if (_sp) _sp.value = (s.subagentMaxParallel ?? 2);
    if (_sm) _sm.value = (s.subagentMaxTasks ?? 4);
    if (_sn) _sn.value = (s.subagentNudge ?? 60);
    if (_sw) _sw.value = (s.subagentWarn ?? 150);
    if (_sab) _sab.value = (s.subagentAbort ?? 300);
}

function saveSubagentConfigFromPanel() {
    const _se = document.getElementById('subagent-enabled');
    const _sr = document.getElementById('subagent-rounds');
    const _sp = document.getElementById('subagent-parallel');
    const _sm = document.getElementById('subagent-maxtasks');
    const _sn = document.getElementById('subagent-nudge');
    const _sw = document.getElementById('subagent-warn');
    const _sa = document.getElementById('subagent-abort');
    if (_se) state.settings.subagentEnabled = !!_se.checked;
    if (_sr) state.settings.subagentMaxRounds = Math.min(20, Math.max(1, parseInt(_sr.value) || 4));
    if (_sp) state.settings.subagentMaxParallel = Math.min(8, Math.max(1, parseInt(_sp.value) || 2));
    if (_sm) state.settings.subagentMaxTasks = Math.min(8, Math.max(1, parseInt(_sm.value) || 4));
    if (_sn) state.settings.subagentNudge = Math.max(0, parseInt(_sn.value) || 0);
    if (_sw) state.settings.subagentWarn = Math.max(0, parseInt(_sw.value) || 0);
    if (_sa) state.settings.subagentAbort = Math.min(3600, Math.max(30, parseInt(_sa.value) || 300));
    try { setUserSetting('ai-settings', JSON.stringify(state.settings)); } catch (e) { /* ignore */ }
}

function initAgentConfigPanel() {
    if (!agentConfigToggle) return;

    // ── 打开/关闭面板 ──────────────────────────────
    agentConfigToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (fileUploadPanel && fileUploadPanel.classList.contains('active')) {
            hideUploadPanel();
        }
        if (expandedMessageArea && expandedMessageArea.classList.contains('active')) {
            collapseExpandMode();
        }
        agentConfigPanel.classList.toggle('active');
        // 与设置面板互斥
        const sp = document.getElementById('settings-panel');
        if (sp) sp.classList.remove('active');
        // 打开时从已保存配置重新同步 UI（丢弃未保存的改动）
        loadToolsConfigFromState();
        loadPermissionModeFromState();
        loadAgentEngineFromState();
        loadSubagentConfigToPanel();
        loadTimeoutConfigToPanel();
        syncNeo4jConfigToPanel();
        renderSkillsCheckboxes();  // 动态加载技能列表
        loadMineruConfigToPanel();  // 预填 MinerU Token
    });

    // MinerU Token：打开面板时从后端读入预填
    function loadMineruConfigToPanel() {
        if (!panelMineruToken) return;
        fetch(BACKEND_API_URL + '/api/config/mineru')
            .then(r => r.json())
            .then(function (d) {
                if (panelMineruToken) panelMineruToken.value = d.token || '';
                if (panelMineruMsg) panelMineruMsg.textContent = d.configured ? '✅ 已配置' : '';
            })
            .catch(function () {
                if (panelMineruMsg) panelMineruMsg.textContent = '读取失败';
            });
    }
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
                    panelMineruMsg.textContent = d.configured ? '✅ 已保存' : '⚠️ 已清空 Token';
                }
            })
            .catch(function () {
                if (panelMineruMsg) panelMineruMsg.textContent = '保存失败';
            });
        });
    }

    if (closeAgentConfig) {
        closeAgentConfig.addEventListener('click', () => {
            agentConfigPanel.classList.remove('active');
        });
    }

    document.addEventListener('click', (e) => {
        if (agentConfigPanel && agentConfigPanel.classList.contains('active')) {
            if (!agentConfigPanel.contains(e.target) && e.target !== agentConfigToggle && !agentConfigToggle.contains(e.target)) {
                agentConfigPanel.classList.remove('active');
            }
        }
    });

    // 工具开关事件：仅 UI 勾选，不实时保存（点击「保存」按钮统一提交）

    // 加载当前配置到 UI
    loadToolsConfigFromState();
    initPermissionModeControls();
}

function loadToolsConfigFromState() {
    const cfg = state.agentConfig;
    if (!cfg) return;
    // 动态渲染工具分组（异步，用 cfg.enabledTools 同步勾选态）
    renderToolsGroups();

    // 旧 DOM 兜底（向后兼容；动态渲染成功后会隐藏它们）
    if (toolGraphSchema) toolGraphSchema.checked = cfg.enabledTools.includes('graph_schema');
    if (toolExecuteCypher) toolExecuteCypher.checked = cfg.enabledTools.includes('execute_cypher');
    if (toolSkillList) toolSkillList.checked = cfg.enabledTools.includes('skill_list');
    if (toolSkillView) toolSkillView.checked = cfg.enabledTools.includes('skill_view');
    if (toolWebSearch) toolWebSearch.checked = cfg.enabledTools.includes('web_search');
    if (toolWebExtract) toolWebExtract.checked = cfg.enabledTools.includes('web_extract');
    if (toolTodo) toolTodo.checked = cfg.enabledTools.includes('todo');
    if (toolNotes) toolNotes.checked = cfg.enabledTools.includes('conversation_notes');
    if (toolRunCode) toolRunCode.checked = cfg.enabledTools.includes('run_code');
    if (toolReadFile) toolReadFile.checked = cfg.enabledTools.includes('read_file');
    if (toolWriteFile) toolWriteFile.checked = cfg.enabledTools.includes('write_file');
    if (toolListFiles) toolListFiles.checked = cfg.enabledTools.includes('list_files');
    if (toolSkillWrite) toolSkillWrite.checked = cfg.enabledTools.includes('skill_write');
    if (toolSearchHistory) toolSearchHistory.checked = cfg.enabledTools.includes('search_history');
    if (toolSearchKnowledge) toolSearchKnowledge.checked = cfg.enabledTools.includes('shared_search');
    if (toolSharedUpload) toolSharedUpload.checked = cfg.enabledTools.includes('shared_upload');
    if (toolMineru) toolMineru.checked = cfg.enabledTools.includes('mineru_parse');
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
    const tools = [];
    // 优先读动态分组勾选（后端 /api/tools 渲染的 .tool-checkbox）
    const dynChecked = document.querySelectorAll('#tools-groups-container .tool-checkbox:checked');
    if (dynChecked.length > 0) {
        dynChecked.forEach(cb => { if (cb.dataset.tool) tools.push(cb.dataset.tool); });
    } else {
        // 兜底：后端不可用、未渲染时读旧 DOM
        if (toolGraphSchema && toolGraphSchema.checked) tools.push('graph_schema');
        if (toolExecuteCypher && toolExecuteCypher.checked) tools.push('execute_cypher');
        if (toolSkillList && toolSkillList.checked) tools.push('skill_list');
        if (toolSkillView && toolSkillView.checked) tools.push('skill_view');
        if (toolWebSearch && toolWebSearch.checked) tools.push('web_search');
        if (toolWebExtract && toolWebExtract.checked) tools.push('web_extract');
        if (toolTodo && toolTodo.checked) tools.push('todo');
        if (toolNotes && toolNotes.checked) tools.push('conversation_notes');
        if (toolRunCode && toolRunCode.checked) tools.push('run_code');
        if (toolReadFile && toolReadFile.checked) tools.push('read_file');
        if (toolWriteFile && toolWriteFile.checked) tools.push('write_file');
        if (toolListFiles && toolListFiles.checked) tools.push('list_files');
        if (toolSkillWrite && toolSkillWrite.checked) tools.push('skill_write');
        if (toolSearchHistory && toolSearchHistory.checked) tools.push('search_history');
        if (toolSearchKnowledge && toolSearchKnowledge.checked) tools.push('shared_search');
        if (toolSharedUpload && toolSharedUpload.checked) tools.push('shared_upload');
        if (toolMineru && toolMineru.checked) tools.push('mineru_parse');
    }

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
    state.agentConfig.maxRounds = isNaN(rounds) ? 8 : rounds;
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

    // 收起面板
    if (agentConfigPanel) agentConfigPanel.classList.remove('active');
    const btn = document.getElementById('save-agent-config');
    if (btn && typeof showActionFeedback === 'function') showActionFeedback('配置已保存', 'success', btn);
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
            state.agentConfig.maxRounds = (parsed.maxRounds === 0 || parsed.maxRounds) ? parsed.maxRounds : 8;
            // 技能勾选状态同样持久化（旧存档无此字段时保留默认）
            if (Array.isArray(parsed.loadedSkills)) {
                state.agentConfig.loadedSkills = parsed.loadedSkills;
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
        panelNeo4jConnect.textContent = state.neo4j.connected ? '✅ 已连接' : '🔌 连接';
    }
}

if (panelNeo4jConnect) {
    panelNeo4jConnect.addEventListener('click', async function() {
        const uri = (panelNeo4jUri ? panelNeo4jUri.value.trim() : '') || state.neo4j.uri;
        const user = (panelNeo4jUser ? panelNeo4jUser.value.trim() : '') || state.neo4j.user;
        const pass = panelNeo4jPass ? panelNeo4jPass.value : '';

        if (!pass && !neo4jConnector.isConnected()) {
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = '请输入密码';
                panelNeo4jMsg.style.color = 'var(--error, #ef4444)';
            }
            return;
        }

        if (neo4jConnector.isConnected() && !pass) {
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = '✅ 已连接';
                panelNeo4jMsg.style.color = 'var(--success, #10b981)';
            }
            return;
        }

        const password = pass || state.neo4j.password;

        panelNeo4jConnect.disabled = true;
        panelNeo4jConnect.textContent = '⏳ 连接中...';
        if (panelNeo4jMsg) {
            panelNeo4jMsg.textContent = '';
        }

        const result = await neo4jConnector.connect(uri, user, password);

        panelNeo4jConnect.disabled = false;
        if (result.success) {
            panelNeo4jConnect.textContent = '✅ 已连接';
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = '连接成功';
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
            panelNeo4jConnect.textContent = '🔌 连接';
            if (panelNeo4jMsg) {
                panelNeo4jMsg.textContent = result.error || '连接失败';
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
