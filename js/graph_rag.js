// ==================== ReAct 智能体引擎 + Neo4j 连接器 ====================
// 注：旧的 buildAgentSystemPrompt（前端 ReAct prompt 拼装，依赖已删除的 js/agent_tools.js）
// 已移除——智能体提示词与工具全部由后端提供（backend/agent/）。

// ── Neo4j ──────────────────────────────────────────
class Neo4jConnector {
    constructor() { this._driver = null; }
    isConnected() { return this._driver !== null; }
    async connect(uri, user, password) {
        try { if (this._driver) { await this._driver.close(); this._driver = null; }
            this._driver = neo4j.driver(uri, neo4j.auth.basic(user, password), { maxConnectionPoolSize: 2, connectionTimeout: 10000, disableLosslessIntegers: true });
            await this._driver.verifyConnectivity(); state.neo4j.connected = true; return { success: true };
        } catch (err) { state.neo4j.connected = false; this._driver = null; return { success: false, error: err.message }; }
    }
    async disconnect() { if (this._driver) { try { await this._driver.close(); } catch(e) {} this._driver = null; } state.neo4j.connected = false; }
    async run(cypher, params = {}) {
        if (!this._driver) throw new Error(t('gr.neo4jNotConnected'));
        const session = this._driver.session();
        try { const r = await session.run(cypher, params); return r.records.map(r => { const o = {}; r.keys.forEach(k => { o[k] = _sN(r.get(k)); }); return o; }); }
        finally { await session.close(); }
    }
}
function _sN(v) {
    if (v === null || v === undefined) return null;
    if (v instanceof neo4j.types.Node) return { _type:'Node', labels:v.labels, properties:v.properties };
    if (v instanceof neo4j.types.Relationship) return { _type:'Relationship', type:v.type, properties:v.properties };
    if (v instanceof neo4j.types.Path) return { _type:'Path', segments:v.segments.map(s=>{return{start:_sN(s.start),relationship:_sN(s.relationship),end:_sN(s.end)}}) };
    if (typeof v === 'object' && v !== null) { const s=v.toString(); const n=Number(s); return isNaN(n)?s:n; }
    if (typeof v === 'number' && !Number.isInteger(v)) return Math.round(v*10000)/10000;
    return v;
}
const neo4jConnector = new Neo4jConnector();
async function ensureNeo4jConnected() {
    if (neo4jConnector.isConnected()) return { success: true };
    const r = await neo4jConnector.connect(state.neo4j.uri, state.neo4j.user, state.neo4j.password);
    if (r.success) updateNeo4jStatusUI(true);
    return r;
}

// ── 后端 API 地址（如需改端口，改这里） ────────
const BACKEND_API_URL = '';  // 空 = 同源（前后端都在 8080 端口）

// ── 辅助：从模型 endpoint 提取 base_url ─────────
function getModelBaseUrl(endpoint) {
    // endpoint: https://api.deepseek.com/chat/completions
    // → base:   https://api.deepseek.com              (去掉 /chat/completions)
    // endpoint: https://api.openai.com/v1/chat/completions
    // → base:   https://api.openai.com/v1              (去掉 /chat/completions)
    // endpoint: http://localhost:8642/v1
    // → base:   http://localhost:8642/v1                (无 /chat/completions)
    if (!endpoint) return '';
    // 只需去掉末尾的 /chat/completions
    return endpoint.replace(/\/chat\/completions\/?$/, '');
}

// ── 主 ReAct 循环（后端模式）────────────────────
async function reactAgentLoop(userQuestion, options /* {fileIds:[], note_refs:[]} */) {
    options = options || {};
    const overrideFileIds = options.fileIds || null;
    // 引用的笔记/数据库条目：优先用本次传入；重新生成/续写时从上一条用户消息里恢复
    let noteRefs = options.note_refs || null;
    if (!noteRefs) {
        const _msgs = (state.branches[state.currentBranchId] || {}).messages || [];
        for (let i = _msgs.length - 1; i >= 0; i--) {
            if (_msgs[i].role === 'user' && _msgs[i].noteRefs && _msgs[i].noteRefs.length) {
                noteRefs = _msgs[i].noteRefs;
                break;
            }
        }
    }
    noteRefs = (noteRefs || []).map(function (r) { return { id: r.id, scope: r.scope || 'notes', title: r.title || '' }; });
    // 生成开始：按钮切成「⚡立即回答」（用户 2026-09-27 定：不硬中断、不丢已探索内容，只催它马上出答案）
    state.isGenerating = true;
    state._runBranchId = state.currentBranchId;   // 记住这次回答属于哪条对话（中途切走也不会标错）
    if (typeof markChatRunning === 'function') markChatRunning(state._runBranchId);
    state.userHasScrolledUp = false;   // 刚发出去 = 人在看底部，允许贴底
    if (typeof sendBtn !== 'undefined' && sendBtn) { sendBtn.innerHTML = '⚡'; sendBtn.classList.add('stop'); sendBtn.title = t('gr.nudge'); }
    var _stopBtn = document.getElementById('agent-stop-btn'); if (_stopBtn) _stopBtn.hidden = false;
    if (typeof updateSendButtonState === 'function') updateSendButtonState();
    const enabledTools = state.agentConfig?.enabledTools || ['graph_query', 'shared_search', 'search_history', 'conversation_notes', 'skill_view', 'web_search', 'web_extract', 'todo', 'delegate_task', 'mineru_parse', 'read_file', 'list_files', 'search_files', 'find_files', 'edit_file', 'crossref_lookup', 'arxiv_search', 'semantic_scholar_search', 'gh_search_code', 'gh_read_file', 'wiki_read', 'wiki_write'];
    const loadedSkills = state.agentConfig?.loadedSkills || ['self-modify'];
    const currentBranch = state.branches[state.currentBranchId];
    const modelId = state.currentModel;
    const modelCfg = modelConfig[modelId];
    const apiKey = state.apiKeys[modelId];

    // ── 流式回复：先建好回复框（单个推理容器 + 答案区）────────
    var reasoningSteps = [];
    var finalAnswer = '';
    var streamedText = '';   // 累积流式正文，final 缺失时兜底
    var currentRoundText = '';  // 当前轮次的正文（工具轮时转成思考步骤持久化）
    var currentRound = 0;
    var pendingReasoning = '';
    var reasoningBox = null;

    var mergeInto = options.mergeInto || null;   // 续写时把结果合并进目标消息（不新建持久化消息）
    var msgId = 'agent-final-' + Date.now();
    var liveEl = document.createElement('div');
    liveEl.className = 'message assistant';
    liveEl.dataset.id = msgId;
    var liveContent = document.createElement('div');
    liveContent.className = 'message-content';

    // 推理容器（框内）。收起/展开由对话区右侧那颗浮动键统一控制，
    // 这里只留「收起态的一行提示」，点了展开这一条。
    var reasoningContainer = document.createElement('div');
    reasoningContainer.className = 'reasoning-steps-container';
    var reasoningHint = document.createElement('div');
    reasoningHint.className = 'reasoning-collapsed-hint';
    reasoningHint.textContent = t('gr.reasoningCollapsed');
    var reasoningBlock = document.createElement('div');
    reasoningBlock.className = 'agent-reasoning-block';
    reasoningHint.onclick = function () {
        reasoningBlock.classList.remove('collapsed');
        updateReasoningRail();
    };
    reasoningBlock.appendChild(reasoningContainer);
    reasoningBlock.appendChild(reasoningHint);
    liveContent.appendChild(reasoningBlock);
    var answerDiv = document.createElement('div');
    liveContent.appendChild(answerDiv);

    // 运行中状态行：转圈 + 当前动作 + 已运行秒数。
    // 全程保留（不再「一连上 SSE 就删掉」）——否则工具调用/模型思考那几十秒里界面毫无动静，
    // 看起来就像卡死。只有流结束、用户停止或异常时才移除。
    var stepIndicator = document.createElement('div');
    stepIndicator.className = 'message step-indicator';
    stepIndicator.innerHTML = '<div class="agent-step-header q-live-header">' +
        '<span class="q-spinner"></span><span class="q-live-text">' + t('gr.agentThinking') + '</span>' +
        '<span class="q-live-elapsed"></span></div>';
    liveContent.appendChild(stepIndicator);
    var liveTextEl = stepIndicator.querySelector('.q-live-text');
    var liveElapsedEl = stepIndicator.querySelector('.q-live-elapsed');
    var liveT0 = 0, liveTimer = null;
    function liveTick() {
        if (!liveT0 || !liveElapsedEl) return;
        var s = Math.round((Date.now() - liveT0) / 1000);
        liveElapsedEl.textContent = s >= 60 ? (Math.floor(s / 60) + 'm' + (s % 60) + 's') : (s + 's');
    }
    function showLiveStatus(text) {
        if (text && liveTextEl && liveTextEl.textContent !== text) liveTextEl.textContent = text;
        if (!liveT0) { liveT0 = Date.now(); liveTick(); liveTimer = setInterval(liveTick, 1000); }
        if (!stepIndicator.parentNode) liveContent.appendChild(stepIndicator);
    }
    function hideLiveStatus() {
        if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
        if (stepIndicator.parentNode) stepIndicator.parentNode.removeChild(stepIndicator);
    }

    liveEl.appendChild(liveContent);
    messagesContainer.appendChild(liveEl);
    updateReasoningRail();   // 有思考块了 → 右侧浮动键出现
    scrollToBottomIfFollowing();

    // 实时渲染辅助
    function liveScroll() { scrollToBottomIfFollowing(); }
    function ensureReasoningBox() {
        if (!reasoningBox) {
            reasoningBox = document.createElement('div');
            reasoningBox.className = 'agent-reasoning-box';
            reasoningBox.innerHTML = '<div class="reasoning-toggle">' + t('gr.mrOpen') + '</div><div class="reasoning-content"></div>';
            // 实时这张卡以前没接点击（箭头是摆设）—— 与历史渲染的 toggleMrBox 表现一致：切显隐 + 换箭头
            (function () {
                var tgl = reasoningBox.querySelector('.reasoning-toggle');
                var box = reasoningBox.querySelector('.reasoning-content');
                tgl.onclick = function () {
                    var hidden = box.style.display === 'none';
                    box.style.display = hidden ? 'block' : 'none';
                    tgl.textContent = hidden ? t('gr.mrOpen') : t('gr.mrClosed');
                };
            })();
            reasoningContainer.appendChild(reasoningBox);
        }
        return reasoningBox.querySelector('.reasoning-content');
    }
    function handleReasoning(text) {
        pendingReasoning += text;
        ensureReasoningBox().textContent += text;
        liveScroll();
    }
    function finalizeReasoning() {
        if (pendingReasoning) {
            reasoningSteps.push({ type: 'model_reasoning', text: pendingReasoning, step: currentRound });
            pendingReasoning = '';
            reasoningBox = null;
        }
    }
    function appendStepNode(html) {
        var d = document.createElement('div');
        d.innerHTML = html;
        reasoningContainer.appendChild(d);
    }
    function handleThinking(text) {
        reasoningSteps.push({ type: 'thinking', text: text });
        appendStepNode('<div class="agent-step-header">' + t('gr.stepThink') + '</div><div class="obs-content">' + escHtml(text) + '</div>');
        liveScroll();
    }
    function handleToolCall(name, input) {
        // 先把本轮已流出的中间正文持久化为思考步骤（否则工具轮正文只瞬间显示后消失）
        if (currentRoundText.trim()) handleThinking(currentRoundText);
        currentRoundText = '';
        finalizeReasoning();
        reasoningSteps.push({ type: 'tool_call', name: name, input: input || '' });
        appendStepNode('<div class="agent-step-header">' + t('gr.stepCall') + escHtml(name) + '</div>' + (input ? '<pre class="tool-code">' + escHtml(input) + '</pre>' : ''));
        showLiveStatus(t('gr.executing') + name + ' …');
        currentRound++;
        liveScroll();
    }
    function handleToolResult(name, content) {
        reasoningSteps.push({ type: 'tool_result', name: name, content: (content || '') });
        appendStepNode('<div class="agent-step-header">' + t('gr.stepResult') + '</div><div class="obs-content">' + escHtml(content || '') + '</div>');
        showLiveStatus('📥 ' + name + t('gr.toolReturned'));
        liveScroll();
    }

    // ── 超时保护提示（催促 / 警告 / 终止）──
    function handleTimeoutNotice(level, text, elapsed) {
        var icon = level === 'abort' ? '⏹️' : (level === 'warn' ? '⚠️' : (level === 'immediate' ? '⚡' : '⏳'));
        var label = level === 'abort' ? t('gr.abort') : (level === 'immediate' ? t('gr.nudge') : level);
        var color = level === 'abort' ? '#e02020' : (level === 'warn' ? '#e6a23c' : (level === 'immediate' ? 'var(--primary)' : 'var(--gray)'));
        reasoningSteps.push({ type: 'timeout_notice', level: level, text: text || '', elapsed: elapsed || 0 });
        appendStepNode('<div class="agent-step-header" style="color:' + color + ';">' + icon + ' ' + label +
            (elapsed ? '（' + elapsed + 's）' : '') + '</div><div class="obs-content">' + escHtml(text || '') + '</div>');
        if (level !== 'abort') {
            showLiveStatus(icon + ' ' + label + (elapsed ? '（' + elapsed + 's）' : '') + t('gr.stillProcessing'));
        }
        liveScroll();
    }

    // ── 子智能体：卡片（可点开查看它的思考与工具调用全过程）──
    var _subagentRuns = {};   // index -> 运行信息（供浮窗查看）
    var _SA_ROLE_NAMES = { contradiction: t('gr.roleContradiction'), extraction: t('gr.roleExtraction'), verify: t('gr.roleVerify') };
    // 档案显示字段（后端 _profile_info 事件字段）——前端卡片/浮窗据此显示专有子智能体名字
    function _saProfileFields(ev) {
        ev = ev || {};
        return { profile: ev.profile || '', profile_title: ev.profile_title || '',
                 profile_read_max: ev.profile_read_max || 0, profile_rounds: ev.profile_rounds || 0,
                 profile_tools: ev.profile_tools || 0, profile_skills: ev.profile_skills || 0 };
    }
    function _subagentCardHtml(idx, info) {
        var badge = info.running
            ? '<span style="color:#e6a23c;"><span class="q-spinner q-spinner-sm"></span>' + t('gr.running') + '</span>'
            : (info.ok ? '<span style="color:#2e9e4f;">✅ ' + t('gr.done') + (info.seconds ? '（' + info.seconds + 's）' : '') + '</span>'
                       : '<span style="color:#e02020;">⚠️ ' + t('gr.failed') + '</span>');
        var role = info.role ? '<span style="font-size:.75rem;color:#8a6d3b;background:rgba(230,162,60,.15);border-radius:4px;padding:1px 6px;margin-left:4px;">' + (_SA_ROLE_NAMES[info.role] || info.role) + '</span>' : '';
        // 用户 2026-10-04：卡片必须显示「专有子智能体」的名字（档案名），不能只写「子智能体 N」
        var pTitle = String(info.profile_title || info.profile || '').trim() || t('gr.genericSubagent');
        var pMeta = [];
        if (info.profile_read_max) pMeta.push(t('gr.metaRead') + info.profile_read_max + t('gr.metaChars'));
        if (info.profile_rounds) pMeta.push(t('gr.metaRounds') + info.profile_rounds);
        if (info.profile_tools) pMeta.push(t('gr.metaTools') + info.profile_tools);
        if (info.profile_skills) pMeta.push(t('gr.metaSkills') + info.profile_skills);
        var pMetaHtml = pMeta.length
            ? '<span style="font-size:.72rem;color:var(--gray);font-weight:400;">（' + pMeta.join(' · ') + '）</span>' : '';
        var goal = escHtml((info.goal || '').slice(0, 120));
        var summary = info.summary ? escHtml(info.summary.slice(0, 200)) : '';
        var live = info.running
            ? '<div class="sa-live" style="margin-top:.25rem;font-size:.78rem;color:var(--primary);min-height:1.1em;white-space:pre-wrap;">' + t('gr.starting') + '</div>'
            : '';
        return '<div class="subagent-node" data-sa="' + idx + '" style="border:1px solid var(--border-color,#ddd);' +
            'border-radius:8px;padding:.45rem .6rem;margin:.35rem 0;background:var(--light,#f6f8fa);cursor:pointer;" ' +
            'title="点击查看该子智能体的思考与工具调用全过程">' +
            '<div style="font-weight:600;">🤖 ' + escHtml(pTitle) + ' <span style="color:var(--gray);font-weight:400;">· ' + t('gr.task') + ' ' + (idx + 1) + '</span>' + role + pMetaHtml + ' &nbsp;' + badge + '</div>' +
            '<div style="margin-top:.15rem;">' + t('gr.goal') + goal + '</div>' +
            live +
            (summary ? '<div style="margin-top:.25rem;color:var(--gray);">' + t('gr.conclusion') + summary + '…</div>' : '') +
            '<div style="margin-top:.2rem;font-size:.75rem;color:var(--primary);">' + t('gr.viewFull') + '</div>' +
            '</div>';
    }
    function _bindSubagentCards() {
        reasoningSteps = reasoningSteps;  // no-op 占位（保持闭包可读性）
        var nodes = reasoningContainer.querySelectorAll('.subagent-node');
        Array.prototype.forEach.call(nodes, function (n) {
            if (n.dataset.bound === '1') return;
            n.dataset.bound = '1';
            n.addEventListener('click', function () {
                var idx = parseInt(n.dataset.sa, 10);
                var info = _subagentRuns[idx] || {};
                if (typeof window.subagentViewer !== 'undefined') window.subagentViewer.open(info);
            });
        });
    }
    function handleSubagentStart(ev) {
        var idx = ev.index || 0;
        _subagentRuns[idx] = Object.assign({ index: idx, goal: ev.goal || '', context: ev.context || '',
                               role: ev.role || '',
                               running: true, ok: false, steps: [] }, _saProfileFields(ev));
        finalizeReasoning();
        reasoningSteps.push(Object.assign({ type: 'subagent', index: idx, goal: ev.goal || '', context: ev.context || '',
                             role: ev.role || '',
                             ok: null, running: true, summary: '', steps: [], total: ev.total || 1 }, _saProfileFields(ev)));
        appendStepNode(_subagentCardHtml(idx, _subagentRuns[idx]));
        _bindSubagentCards();
        liveScroll();
    }
    function handleSubagentEnd(ev) {
        var idx = ev.index || 0;
        _subagentRuns[idx] = Object.assign({ index: idx, goal: ev.goal || '', context: ev.context || '',
                              running: false, ok: !!ev.ok,
                              summary: ev.summary || '', error: ev.error || '',
                              steps: ev.steps || [], rounds: ev.rounds || 0, seconds: ev.seconds || 0 }, _saProfileFields(ev));
        // 更新已渲染的卡片
        var node = reasoningContainer.querySelector('.subagent-node[data-sa="' + idx + '"]');
        if (node) {
            node.outerHTML = _subagentCardHtml(idx, _subagentRuns[idx]);
            _bindSubagentCards();
        } else {
            appendStepNode(_subagentCardHtml(idx, _subagentRuns[idx]));
            _bindSubagentCards();
        }
        // 同步进 reasoningSteps（持久化：刷新/切换对话后仍可看）
        var rec = null;
        for (var i = reasoningSteps.length - 1; i >= 0; i--) {
            if (reasoningSteps[i].type === 'subagent' && reasoningSteps[i].index === idx) { rec = reasoningSteps[i]; break; }
        }
        var payload = Object.assign({ type: 'subagent', index: idx, goal: _subagentRuns[idx].goal,
                        context: _subagentRuns[idx].context || '', ok: !!ev.ok,
                        running: false, summary: ev.summary || '', error: ev.error || '',
                        status: ev.status || '', missing: ev.missing || '', confidence: ev.confidence || '',
                        steps: ev.steps || [], rounds: ev.rounds || 0, seconds: ev.seconds || 0 }, _saProfileFields(ev));
        if (rec) { Object.assign(rec, payload); } else { reasoningSteps.push(payload); }
        liveScroll();
    }
    // ── 子智能体流式进度：实时更新卡片 + 累积步骤（供浮窗查看）──
    function handleSubagentStep(ev) {
        var idx = ev.index || 0;
        var run = _subagentRuns[idx];
        if (!run || !run.running) return;
        var st = ev.step || {};
        if (st.type && st.type !== 'round') {
            run.steps = run.steps || [];
            run.steps.push(st);
            var rec = null;
            for (var i = reasoningSteps.length - 1; i >= 0; i--) {
                if (reasoningSteps[i].type === 'subagent' && reasoningSteps[i].index === idx) { rec = reasoningSteps[i]; break; }
            }
            if (rec) rec.steps = run.steps;
        }
        var node = reasoningContainer.querySelector('.subagent-node[data-sa="' + idx + '"]');
        if (node) {
            var live = node.querySelector('.sa-live');
            if (live) {
                var txt = '';
                if (st.type === 'round') txt = '🧭 ' + t('gr.round') + ' ' + (st.round || '?') + t('gr.thinkingSuffix');
                else if (st.type === 'thinking') txt = '✍️ ' + (st.text || '').replace(/\s+/g, ' ').slice(0, 80);
                else if (st.type === 'tool_call') txt = t('gr.stepCall') + (st.name || '') + '…';
                else if (st.type === 'tool_result') txt = '📥 ' + (st.name || '') + t('gr.returned');
                else if (st.type === 'notice') txt = '⚠️ ' + (st.text || '').slice(0, 80);
                if (txt) {
                    live.textContent = txt;
                    showLiveStatus('🤖 ' + t('gr.subagent') + ' ' + (idx + 1) + '　' + txt);
                }
            }
        }
        liveScroll();
    }

    try {
        var recentHistory = getRecentMessagesForContext(currentBranch.messages);
        // 主动发送时排除最后一条消息（它就是当前 input，避免重复）；续写(skipLastHistory=false)时保留完整历史
        if (options.skipLastHistory !== false && recentHistory.length > 0) {
            recentHistory = recentHistory.slice(0, -1);
        }
        // 收集本对话全部用户消息引用的附件 file_id（当轮/历史、文档/图片一视同仁）：
        //   一律只传 id —— 正文与像素由模型按需 read_file(file_id=…) 自取（用户 2026-10-01 定）
        var _collectFileIds = function () {
            var msgs = state.branches[state.currentBranchId]?.messages || [];
            // 收集**本对话全部**用户消息引用的附件（不再只扫最近 contextLength*2+1 条）。
            // 旧实现只扫最近 21 条 → 更早那轮上传的附件根本不进请求 → 后端拿不到
            // file_ids → 模型下一轮就说"目前没有文件"（用户实测的 bug）。
            // 附件本来就存在消息里，收全量只是多几个字符串，不新增任何存储。
            var recent = msgs;
            var allIds = [];
            recent.forEach(function (m) {
                if (m.role !== 'user' || !m.attachments) return;
                m.attachments.forEach(function (a) {
                    if (!a.file_id) return;
                    if (allIds.indexOf(a.file_id) === -1) allIds.push(a.file_id);
                });
            });
            return { allIds: allIds };
        };

        var collected = _collectFileIds();
        var refIds = collected.allIds.slice();
        (overrideFileIds || []).forEach(function (id) { if (refIds.indexOf(id) === -1) refIds.push(id); });

        var requestBody = {
            input: userQuestion,
            history: recentHistory,
            enabled_tools: enabledTools,
            loaded_skills: loadedSkills,
            neo4j_config: state.neo4j,
            model: {
                model: modelCfg ? modelCfg.modelParam : modelId,
                api_key: apiKey || '',
                base_url: modelCfg ? getModelBaseUrl(modelCfg.endpoint) : '',
                temperature: state.settings?.temperature || 0.7,
                max_tokens: state.settings?.maxTokens || 65536,
                context_window: state.settings?.contextWindow || 262144,
                context_budget: state.settings?.contextBudget || 0,   // 清理触发线；0 = 后端自动
            },
            context_length: state.settings?.contextLength || 10,
            branch_id: state.currentBranchId || '',
            permission_mode: state.agentConfig?.permissionMode || 'safe',
            engine: state.agentConfig?.engine || 'custom',
            max_rounds: (state.agentConfig?.maxRounds === 0 || state.agentConfig?.maxRounds) ? state.agentConfig.maxRounds : 20,
            file_ids: refIds,
            note_refs: noteRefs,
            timeouts: {
                // 2026-10-13 起只剩一级强制中断（催促/警告两级已删，断点续跑使中断可继续）
                abort: state.settings?.timeoutAbort ?? 86400,
            },
            subagent: {
                enabled: state.settings?.subagentEnabled !== false,
                max_rounds: state.settings?.subagentMaxRounds ?? 4,
                max_parallel: state.settings?.subagentMaxParallel ?? 2,
                max_tasks: state.settings?.subagentMaxTasks ?? 4,
                nudge: state.settings?.subagentNudge ?? 60,
                warn: state.settings?.subagentWarn ?? 150,
                abort: state.settings?.subagentAbort ?? 300,
                feed_steps: !!state.settings?.subagentFeedSteps,   // 默认 false：只回喂摘要，防主控上下文爆炸
            },
            // 后端接管消息树（2026-10-03）：client_id 供多端广播滤掉「自己发的回声」
            client_id: (typeof window.getClientId === 'function') ? window.getClientId() : '',
            // 本地用户消息 id：账本记录后投影合并凭它挤掉本地副本（防双气泡）
            client_msg_id: (options && options.client_msg_id) || '',
        };

        console.log('[Agent] Full request:', JSON.stringify({
            input: (requestBody.input || '').substring(0, 300),
            history: requestBody.history.map(function(m) {
                return {role: m.role, content: (m.content||'').substring(0, 200)};
            }),
            file_ids: requestBody.file_ids,
            tools: requestBody.enabled_tools,
            skills: requestBody.loaded_skills,
            context_length: requestBody.context_length,
            model: requestBody.model.model,
        }));
        var resp = await fetch(BACKEND_API_URL + '/api/agent/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
            signal: state.abortController?.signal,
        });

        if (!resp.ok) {
            var errMsg = t('gr.backendFailed') + ' (' + resp.status + ')';
            try { var d = await resp.json(); if (d.detail) errMsg = d.detail; } catch(e) {}
            throw new Error(errMsg);
        }

        if (!resp.body) { throw new Error(t('gr.noStream')); }

        // 移除等待提示
        if (stepIndicator.parentNode) stepIndicator.parentNode.removeChild(stepIndicator);

        // 逐行解析 SSE 流
        var reader = resp.body.getReader();
        var decoder = new TextDecoder('utf-8');
        var buffer = '';
        while (true) {
            var chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            var parts = buffer.split('\n\n');
            buffer = parts.pop();
            for (var pi = 0; pi < parts.length; pi++) {
                var line = parts[pi].trim();
                if (line.indexOf('data: ') !== 0) continue;
                var dataStr = line.slice(6).trim();
                if (!dataStr) continue;
                var ev;
                try { ev = JSON.parse(dataStr); } catch(e) { continue; }
                var evType = ev.type;
                if (evType === 'reasoning') { handleReasoning(ev.text || ''); }
                else if (evType === 'token') { if (ev.text) { streamedText += ev.text; currentRoundText += ev.text; answerDiv.textContent += ev.text; showLiveStatus(t('gr.generating')); } }
                else if (evType === 'tool_call') { handleToolCall(ev.name || '', ev.input || ''); }
                else if (evType === 'tool_result') { handleToolResult(ev.name || '', ev.content || ''); }
                else if (evType === 'subagent_start') { handleSubagentStart(ev); }
                else if (evType === 'subagent_step') { handleSubagentStep(ev); }
                else if (evType === 'subagent_end') { handleSubagentEnd(ev); }
                else if (evType === 'timeout_notice') { handleTimeoutNotice(ev.level || 'nudge', ev.text || '', ev.elapsed || 0); }
                else if (evType === 'final') { finalAnswer = ev.text || ''; currentRoundText = ''; answerDiv.textContent = finalAnswer; }
                else if (evType === 'stopped') { finalAnswer = t('gr.stoppedGen'); currentRoundText = ''; answerDiv.textContent = (streamedText || '') + '\n\n⏹️ ' + t('gr.stopped'); }
                else if (evType === 'error') { throw new Error(ev.message || '后端错误'); }
            }
        }
        hideLiveStatus();     // 本轮结束：收掉转圈状态行
        finalizeReasoning();  // 收尾：flush 未落盘的推理
        if (!finalAnswer && streamedText) finalAnswer = streamedText;  // 兜底：final 缺失/为空时用已流出的正文

    } catch (error) {
        hideLiveStatus();  // 停止/异常：立即收掉转圈，别让它一直转
        if (error.name === 'AbortError') {
            finalAnswer = t('gr.cancelledGen');
        } else {
            finalAnswer = t('gr.runFailed') + error.message;
        }
    }

    // 保存最终回答（reasoningSteps 已含 model_reasoning/tool_call/tool_result，持久化）
    var answer = finalAnswer || t('gr.noAnswer');
    if (mergeInto) {
        // 续写：合并进目标消息（同框显示），不新建持久化消息
        var tMsg = (currentBranch.messages || []).find(function(m){ return m.id === mergeInto; });
        if (tMsg) {
            if (answer) tMsg.content = (tMsg.content ? tMsg.content + '\n\n' : '') + answer;
            tMsg.reasoningSteps = (tMsg.reasoningSteps || []).concat(reasoningSteps);
            tMsg.timestamp = new Date().toISOString();
        }
    } else {
        currentBranch.messages.push({
            id: msgId,
            role: 'assistant',
            content: answer,
            timestamp: new Date().toISOString(),
            branchId: state.currentBranchId,
            reasoningSteps: reasoningSteps,  // 持久化思考过程（切换对话/刷新后仍显示；不会进入模型上下文）
        });
    }
    // 本轮生成的推理块，重渲染后保持【展开】（与实时流式所见一致）；
    // 用户手动收起的块由 __mrUser 记住。见 messages.js 的 mrOpenState / toggleMrBox。
    if (typeof window.__mrMarkAuto === 'function') window.__mrMarkAuto(mergeInto || msgId);
    saveBranches();

    if (mergeInto) {
        // 续写：整体重渲染消息列表 → 原消息框显示【原文+续写+推理】在同一框
        if (typeof renderMessages === 'function') renderMessages();
        scrollToBottomIfFollowing();
    } else {
        // 用规范渲染替换临时 live DOM（含操作按钮 + 持久化推理容器）
        var finalMsg = currentBranch.messages[currentBranch.messages.length - 1];
        var msgEl = createMessageElement(finalMsg);
        if (msgEl && liveEl.parentNode) {
            liveEl.parentNode.replaceChild(msgEl, liveEl);
            scrollToBottomIfFollowing();
        }
    }

    updateHistoryList();
    updateTokenDisplay();
    // 生成结束：恢复发送态；人没在看这条对话 → 留绿点提醒
    if (typeof markChatDone === 'function') markChatDone(state._runBranchId);
    state._runBranchId = null;
    state.isGenerating = false;
    // 生成期间被推迟的同步通知，现在补做（见 js/user_chats.js 的 flushPendingSync）
    if (typeof window.flushPendingSync === 'function') { try { window.flushPendingSync(); } catch (e) {} }
    if (typeof sendBtn !== 'undefined' && sendBtn) { sendBtn.innerHTML = '➤'; sendBtn.classList.remove('stop'); sendBtn.title = t('gr.send'); }
    var _stopBtn2 = document.getElementById('agent-stop-btn'); if (_stopBtn2) _stopBtn2.hidden = true;
    if (typeof updateSendButtonState === 'function') updateSendButtonState();
    return answer;
}

// ── 附件点击打开（委托事件）──────────────────────
document.addEventListener('click', function(e) {
    // 消息中的附件
    var att = e.target.closest('.attachment-clickable');
    if (att) {
        var url = att.dataset.fileUrl;
        if (url) {
            var attId2 = url.replace(/^\/api\/file\//, '');
            if (typeof window.openFileById === 'function') {
                window.openFileById(attId2, att.dataset.fileName || att.textContent || 'file');
            } else if (state.settings.fileOpenMode === 'backend') {
                fetch(url + '/open', { method: 'POST' }).catch(function(){});
            } else {
                window.open(url, '_blank');
            }
        }
        return;
    }
    // 待发送的文件芯片
    var chip = e.target.closest('.file-chip-clickable');
    if (chip) {
    var attId = chip.dataset.id;
    // 找对应附件
    var atts = state.documentUpload.pendingAttachments || [];
    var att = atts.find(function(a) { return a.id === attId; });
    if (att && att.file_id) {
    // 已上传到后端 → 按设置的三种打开方式派发
    if (typeof window.openFileById === 'function') {
        window.openFileById(att.file_id, att.name || att.fileName || 'file');
    } else if (state.settings.fileOpenMode === 'backend') {
        fetch('/api/file/' + att.file_id + '/open', { method: 'POST' }).catch(function(){});
    } else {
        window.open('/api/file/' + att.file_id, '_blank');
    }
    } else {
    // 未上传 → blob 预览（降级）
    var url = chip.dataset.url;
    if (url) window.open(url, '_blank');
    }
    }
});

function updateNeo4jStatusUI(connected) {
    if (typeof neo4jStatusDot !== 'undefined' && neo4jStatusDot) {
        neo4jStatusDot.className = 'neo4j-status-dot ' + (connected ? 'connected' : 'disconnected');
        neo4jStatusDot.title = connected ? t('gr.neo4jOn') : t('gr.neo4jNotConnected');
    }
    if (typeof neo4jSettingsStatus !== 'undefined' && neo4jSettingsStatus) {
        neo4jSettingsStatus.textContent = connected ? '✅ ' + t('gr.connected') : '❌ ' + t('gr.disconnected');
    }
}

window.reactAgentLoop = reactAgentLoop;
window.neo4jConnector = neo4jConnector;
window.ensureNeo4jConnected = ensureNeo4jConnected;
window.updateNeo4jStatusUI = updateNeo4jStatusUI;

// ── 「立即终止」键（2026-10-13）：硬中断本轮生成；账本已落的轮次保留，可继续追问 ──
(function () {
    var btn = document.getElementById('agent-stop-btn');
    if (!btn) return;
    btn.addEventListener('click', function () {
        if (!state.isGenerating) return;
        fetch('/api/agent/stop', { method: 'POST' }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            if (typeof showToast === 'function') showToast(t('gr.abortedToast'), 'success');
        }).catch(function (e) {
            if (typeof showToast === 'function') showToast('❌ ' + t('gr.abortFailed') + (e && e.message ? e.message : t('gr.networkError')), 'error');
        });
    });
})();
