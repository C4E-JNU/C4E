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
        if (!this._driver) throw new Error('Neo4j 未连接');
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
async function reactAgentLoop(userQuestion, options /* {images:[], fileIds:[]} */) {
    options = options || {};
    const optsImages = options.images || [];
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
    // 生成开始：切入停止态（sendBtn 变 ■，可点击停止）
    state.isGenerating = true;
    if (typeof sendBtn !== 'undefined' && sendBtn) { sendBtn.innerHTML = '■'; sendBtn.classList.add('stop'); }
    if (typeof updateSendButtonState === 'function') updateSendButtonState();
    const enabledTools = state.agentConfig?.enabledTools || ['graph_schema', 'execute_cypher', 'shared_search', 'search_history', 'conversation_notes', 'skill_view', 'web_search', 'web_extract', 'todo', 'delegate_task', 'mineru_parse', 'read_file', 'list_files'];
    const loadedSkills = state.agentConfig?.loadedSkills || ['graph-guide'];
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

    // 单个推理容器（框内顶部，可折叠）
    var reasoningToggle = document.createElement('div');
    reasoningToggle.className = 'agent-reasoning-toggle';
    reasoningToggle.textContent = '🧠 收起智能体推理过程 ▾';
    var reasoningContainer = document.createElement('div');
    reasoningContainer.className = 'reasoning-steps-container';
    reasoningToggle.onclick = function() {
        var collapsed = reasoningContainer.style.display === 'none';
        reasoningContainer.style.display = collapsed ? 'block' : 'none';
        reasoningToggle.textContent = collapsed ? '🧠 收起智能体推理过程 ▾' : '🧠 查看智能体推理过程 ▸';
    };
    liveContent.appendChild(reasoningToggle);
    liveContent.appendChild(reasoningContainer);

    var answerDiv = document.createElement('div');
    liveContent.appendChild(answerDiv);

    // 等待提示
    var stepIndicator = document.createElement('div');
    stepIndicator.className = 'message step-indicator';
    stepIndicator.innerHTML = '<div class="agent-step-header">⏳ 智能体思考中...</div>';
    liveContent.appendChild(stepIndicator);

    liveEl.appendChild(liveContent);
    messagesContainer.appendChild(liveEl);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    // 实时渲染辅助
    function liveScroll() { messagesContainer.scrollTop = messagesContainer.scrollHeight; }
    function ensureReasoningBox() {
        if (!reasoningBox) {
            reasoningBox = document.createElement('div');
            reasoningBox.className = 'agent-reasoning-box';
            reasoningBox.innerHTML = '<div class="reasoning-toggle">🧠 模型推理 ▾</div><div class="reasoning-content"></div>';
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
        appendStepNode('<div class="agent-step-header">🧠 思考</div><div class="obs-content">' + escHtml(text) + '</div>');
        liveScroll();
    }
    function handleToolCall(name, input) {
        // 先把本轮已流出的中间正文持久化为思考步骤（否则工具轮正文只瞬间显示后消失）
        if (currentRoundText.trim()) handleThinking(currentRoundText);
        currentRoundText = '';
        finalizeReasoning();
        reasoningSteps.push({ type: 'tool_call', name: name, input: input || '' });
        appendStepNode('<div class="agent-step-header">🔧 调用: ' + escHtml(name) + '</div>' + (input ? '<pre class="tool-code">' + escHtml(input) + '</pre>' : ''));
        currentRound++;
        liveScroll();
    }
    function handleToolResult(name, content) {
        reasoningSteps.push({ type: 'tool_result', name: name, content: (content || '').substring(0, 500) });
        appendStepNode('<div class="agent-step-header">👁️ 结果</div><div class="obs-content">' + escHtml((content || '').substring(0, 500)) + '</div>');
        liveScroll();
    }

    // ── 超时保护提示（催促 / 警告 / 终止）──
    function handleTimeoutNotice(level, text, elapsed) {
        var icon = level === 'abort' ? '⏹️' : (level === 'warn' ? '⚠️' : '⏳');
        var label = level === 'abort' ? '超时终止' : (level === 'warn' ? '超时警告' : '超时催促');
        var color = level === 'abort' ? '#e02020' : (level === 'warn' ? '#e6a23c' : 'var(--gray)');
        reasoningSteps.push({ type: 'timeout_notice', level: level, text: text || '', elapsed: elapsed || 0 });
        appendStepNode('<div class="agent-step-header" style="color:' + color + ';">' + icon + ' ' + label +
            (elapsed ? '（' + elapsed + 's）' : '') + '</div><div class="obs-content">' + escHtml(text || '') + '</div>');
        liveScroll();
    }

    // ── 子智能体：卡片（可点开查看它的思考与工具调用全过程）──
    var _subagentRuns = {};   // index -> 运行信息（供浮窗查看）
    var _SA_ROLE_NAMES = { contradiction: '矛盾核查', extraction: '数据提取', verify: '数据核查' };
    function _subagentCardHtml(idx, info) {
        var badge = info.running
            ? '<span style="color:#e6a23c;">● 运行中…</span>'
            : (info.ok ? '<span style="color:#2e9e4f;">✅ 完成' + (info.seconds ? '（' + info.seconds + 's）' : '') + '</span>'
                       : '<span style="color:#e02020;">⚠️ 失败</span>');
        var role = info.role ? '<span style="font-size:.75rem;color:#8a6d3b;background:rgba(230,162,60,.15);border-radius:4px;padding:1px 6px;margin-left:4px;">' + (_SA_ROLE_NAMES[info.role] || info.role) + '</span>' : '';
        var goal = escHtml((info.goal || '').slice(0, 120));
        var summary = info.summary ? escHtml(info.summary.slice(0, 200)) : '';
        var live = info.running
            ? '<div class="sa-live" style="margin-top:.25rem;font-size:.78rem;color:var(--primary);min-height:1.1em;white-space:pre-wrap;">正在启动…</div>'
            : '';
        return '<div class="subagent-node" data-sa="' + idx + '" style="border:1px solid var(--border-color,#ddd);' +
            'border-radius:8px;padding:.45rem .6rem;margin:.35rem 0;background:var(--light,#f6f8fa);cursor:pointer;" ' +
            'title="点击查看该子智能体的思考与工具调用全过程">' +
            '<div style="font-weight:600;">🤖 子智能体 ' + (idx + 1) + role + ' &nbsp;' + badge + '</div>' +
            '<div style="margin-top:.15rem;">目标：' + goal + '</div>' +
            live +
            (summary ? '<div style="margin-top:.25rem;color:var(--gray);">结论：' + summary + '…</div>' : '') +
            '<div style="margin-top:.2rem;font-size:.75rem;color:var(--primary);">点击查看全过程 ▸</div>' +
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
        _subagentRuns[idx] = { index: idx, goal: ev.goal || '', context: ev.context || '',
                               role: ev.role || '',
                               running: true, ok: false, steps: [] };
        finalizeReasoning();
        reasoningSteps.push({ type: 'subagent', index: idx, goal: ev.goal || '', context: ev.context || '',
                             role: ev.role || '',
                             ok: null, running: true, summary: '', steps: [], total: ev.total || 1 });
        appendStepNode(_subagentCardHtml(idx, _subagentRuns[idx]));
        _bindSubagentCards();
        liveScroll();
    }
    function handleSubagentEnd(ev) {
        var idx = ev.index || 0;
        _subagentRuns[idx] = { index: idx, goal: ev.goal || '', context: ev.context || '',
                              running: false, ok: !!ev.ok,
                              summary: ev.summary || '', error: ev.error || '',
                              steps: ev.steps || [], rounds: ev.rounds || 0, seconds: ev.seconds || 0 };
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
        var payload = { type: 'subagent', index: idx, goal: _subagentRuns[idx].goal,
                        context: _subagentRuns[idx].context || '', ok: !!ev.ok,
                        running: false, summary: ev.summary || '', error: ev.error || '',
                        steps: ev.steps || [], rounds: ev.rounds || 0, seconds: ev.seconds || 0 };
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
                if (st.type === 'round') txt = '🧭 第 ' + (st.round || '?') + ' 轮：思考中…';
                else if (st.type === 'thinking') txt = '✍️ ' + (st.text || '').replace(/\s+/g, ' ').slice(0, 80);
                else if (st.type === 'tool_call') txt = '🔧 调用 ' + (st.name || '') + '…';
                else if (st.type === 'tool_result') txt = '📥 ' + (st.name || '') + ' 返回';
                else if (st.type === 'notice') txt = '⚠️ ' + (st.text || '').slice(0, 80);
                if (txt) live.textContent = txt;
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
        // 收集上下文窗口内所有用户消息引用的附件 file_id：
        //   allIds  = 窗口内全部（历史附件仅作引用说明，模型可 read_file 自取）
        //   freshIds = 最后一条用户消息的附件（当轮上传 → 后端注入完整文本）
        var _collectFileIds = function () {
            var msgs = state.branches[state.currentBranchId]?.messages || [];
            var windowN = (state.settings?.contextLength || 10) * 2 + 1;
            var recent = msgs.slice(-windowN);
            var lastUserIdx = -1;
            for (var i = recent.length - 1; i >= 0; i--) {
                if (recent[i].role === 'user') { lastUserIdx = i; break; }
            }
            var allIds = [], freshIds = [];
            recent.forEach(function (m, idx) {
                if (m.role !== 'user' || !m.attachments) return;
                m.attachments.forEach(function (a) {
                    if (!a.file_id) return;
                    if (allIds.indexOf(a.file_id) === -1) allIds.push(a.file_id);
                    if (idx === lastUserIdx && freshIds.indexOf(a.file_id) === -1) freshIds.push(a.file_id);
                });
            });
            return { allIds: allIds, freshIds: freshIds };
        };

        var collected = _collectFileIds();
        var fullIds = overrideFileIds ? overrideFileIds.slice() : collected.freshIds;
        var refIds = collected.allIds.slice();
        fullIds.forEach(function (id) { if (refIds.indexOf(id) === -1) refIds.push(id); });

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
                max_tokens: state.settings?.maxTokens || 8192,
            },
            context_length: state.settings?.contextLength || 10,
            branch_id: state.currentBranchId || '',
            permission_mode: state.agentConfig?.permissionMode || 'safe',
            engine: state.agentConfig?.engine || 'custom',
            max_rounds: (state.agentConfig?.maxRounds === 0 || state.agentConfig?.maxRounds) ? state.agentConfig.maxRounds : 8,
            file_ids: refIds,
            full_file_ids: fullIds,
            note_refs: noteRefs,
            timeouts: {
                nudge: state.settings?.timeoutNudge ?? 120,
                warn: state.settings?.timeoutWarn ?? 240,
                abort: state.settings?.timeoutAbort ?? 420,
            },
            subagent: {
                enabled: state.settings?.subagentEnabled !== false,
                max_rounds: state.settings?.subagentMaxRounds ?? 4,
                max_parallel: state.settings?.subagentMaxParallel ?? 2,
                max_tasks: state.settings?.subagentMaxTasks ?? 4,
                nudge: state.settings?.subagentNudge ?? 60,
                warn: state.settings?.subagentWarn ?? 150,
                abort: state.settings?.subagentAbort ?? 300,
            },
            images: optsImages,
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
            var errMsg = '后端请求失败 (' + resp.status + ')';
            try { var d = await resp.json(); if (d.detail) errMsg = d.detail; } catch(e) {}
            throw new Error(errMsg);
        }

        if (!resp.body) { throw new Error('浏览器不支持流式响应'); }

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
                var t = ev.type;
                if (t === 'reasoning') { handleReasoning(ev.text || ''); }
                else if (t === 'token') { if (ev.text) { streamedText += ev.text; currentRoundText += ev.text; answerDiv.textContent += ev.text; } }
                else if (t === 'tool_call') { handleToolCall(ev.name || '', ev.input || ''); }
                else if (t === 'tool_result') { handleToolResult(ev.name || '', ev.content || ''); }
                else if (t === 'subagent_start') { handleSubagentStart(ev); }
                else if (t === 'subagent_step') { handleSubagentStep(ev); }
                else if (t === 'subagent_end') { handleSubagentEnd(ev); }
                else if (t === 'timeout_notice') { handleTimeoutNotice(ev.level || 'nudge', ev.text || '', ev.elapsed || 0); }
                else if (t === 'final') { finalAnswer = ev.text || ''; currentRoundText = ''; answerDiv.textContent = finalAnswer; }
                else if (t === 'stopped') { finalAnswer = '已停止生成'; currentRoundText = ''; answerDiv.textContent = (streamedText || '') + '\n\n⏹️ 已停止'; }
                else if (t === 'error') { throw new Error(ev.message || '后端错误'); }
            }
        }
        finalizeReasoning();  // 收尾：flush 未落盘的推理
        if (!finalAnswer && streamedText) finalAnswer = streamedText;  // 兜底：final 缺失/为空时用已流出的正文

    } catch (error) {
        if (error.name === 'AbortError') {
            finalAnswer = '已取消生成';
        } else {
            finalAnswer = '⚠️ 智能体执行失败: ' + error.message;
        }
    }

    // 保存最终回答（reasoningSteps 已含 model_reasoning/tool_call/tool_result，持久化）
    var answer = finalAnswer || '未能获取回答';
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
    saveBranches();

    if (mergeInto) {
        // 续写：整体重渲染消息列表 → 原消息框显示【原文+续写+推理】在同一框
        if (typeof renderMessages === 'function') renderMessages();
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    } else {
        // 用规范渲染替换临时 live DOM（含操作按钮 + 持久化推理容器）
        var finalMsg = currentBranch.messages[currentBranch.messages.length - 1];
        var msgEl = createMessageElement(finalMsg);
        if (msgEl && liveEl.parentNode) {
            liveEl.parentNode.replaceChild(msgEl, liveEl);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    }

    updateHistoryList();
    updateTokenDisplay();
    // 生成结束：恢复发送态
    state.isGenerating = false;
    if (typeof sendBtn !== 'undefined' && sendBtn) { sendBtn.innerHTML = '➤'; sendBtn.classList.remove('stop'); }
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
        neo4jStatusDot.title = connected ? 'Neo4j 已连接' : 'Neo4j 未连接';
    }
    if (typeof neo4jSettingsStatus !== 'undefined' && neo4jSettingsStatus) {
        neo4jSettingsStatus.textContent = connected ? '✅ 已连接' : '❌ 未连接';
    }
}

window.reactAgentLoop = reactAgentLoop;
window.neo4jConnector = neo4jConnector;
window.ensureNeo4jConnected = ensureNeo4jConnected;
window.updateNeo4jStatusUI = updateNeo4jStatusUI;
