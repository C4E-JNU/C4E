// ==================== ReAct 智能体引擎 + Neo4j 连接器 ====================

async function buildAgentSystemPrompt(enabledTools, loadedSkills) {
    const lines = [];
    lines.push('你是一个催化降解领域的智能科研助手。你实时连接 Neo4j 知识图谱。');
    lines.push('');
    lines.push('## 可用的工具');
    for (const [name, tool] of Object.entries(agentTools)) {
        if (!enabledTools.includes(name)) continue;
        lines.push(`### ${name}`);
        lines.push(tool.description);
        lines.push(`参数 JSON Schema: ${JSON.stringify(tool.parameters)}`);
        lines.push('');
    }
    if (loadedSkills.length > 0) {
        const skillsIndex = typeof getSkillsIndex === 'function' ? await getSkillsIndex() : {};
        lines.push('## 可用的技能');
        lines.push('当你需要领域知识时，使用 skill_view(name) 读取完整指引。');
        for (const skillName of loadedSkills) {
            const desc = skillsIndex[skillName];
            lines.push(desc ? `- ${skillName}: ${desc}` : `- ${skillName}`);
        }
        lines.push('');
    }
    lines.push('## ReAct 推理格式');
    lines.push('格式1 (需要调用工具):\nThought: <推理>\nAction: <工具名>\nAction Input: <JSON>\n');
    lines.push('格式2 (信息足够):\nThought: <总结>\nFinal Answer: <回答>\n');
    lines.push('## 规则\n- 每步只能做一个 Action\n- Action Input 必须是合法 JSON\n- 用中文回答，保留专业英文术语');
    return lines.join('\n');
}

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

function parseToolCall(text) {
    const am = text.match(/Action:\s*(\w+)/);
    const im = text.match(/Action Input:\s*(\{[\s\S]*?\})/);
    if (!am) return null;
    let p = {};
    if (im) try { p = JSON.parse(im[1]); } catch(e) { try { p = JSON.parse(im[1].replace(/'/g,'"').replace(/,\s*}/g,'}').replace(/,\s*\]/g,']')); } catch(e2) {} }
    return { name: am[1], params: p };
}
function extractFinalAnswer(text) {
    const m = text.match(/Final Answer:\s*([\s\S]*)/);
    return m ? m[1].trim() : null;
}

// ── 流式 LLM ───────────────────────────────────────
async function streamLLMResponse(messages, onToken) {
    const model = state.currentModel;
    const apiKey = state.apiKeys[model];
    const config = modelConfig[model];
    const headers = { 'Content-Type': 'application/json' };
    if (config.apiKeyHeader && config.apiKeyPrefix) headers[config.apiKeyHeader] = config.apiKeyPrefix + apiKey;
    else headers['Authorization'] = `Bearer ${apiKey}`;
    if (state.abortController) state.abortController.abort();
    state.abortController = new AbortController();
    const resp = await fetch(config.endpoint, {
        method: 'POST', headers,
        body: JSON.stringify({ model: config.modelParam, messages, temperature: state.settings.temperature, max_tokens: state.settings.maxTokens, stream: true }),
        signal: state.abortController.signal
    });
    if (!resp.ok) { let e=`API请求失败: ${resp.status}`; try{const d=await resp.json(); e=d.error?.message||e}catch(e2){} throw new Error(e); }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf='', ft='', fr='';
    while(true) {
        const {done,value} = await reader.read();
        if(done) break;
        buf += decoder.decode(value,{stream:true});
        const lines = buf.split('\n');
        buf = lines.pop()||'';
        for(const line of lines) {
            if(!line.trim()||!line.startsWith('data: ')) continue;
            const d=line.slice(6);
            if(d==='[DONE]') return {content:ft, reasoning:fr};
            try {
                const p=JSON.parse(d);
                const delta=p.choices?.[0]?.delta;
                let ru=false;
                if(delta.reasoning_content!==undefined){fr+=delta.reasoning_content||'';ru=true}
                if(delta.thinking!==undefined){fr+=delta.thinking||'';ru=true}
                if(delta?.content||ru){if(delta?.content)ft+=delta.content;if(onToken)onToken(ft,fr)}
            }catch(e){}
        }
    }
    return {content:ft, reasoning:fr};
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
    // 生成开始：切入停止态（sendBtn 变 ■，可点击停止）
    state.isGenerating = true;
    if (typeof sendBtn !== 'undefined' && sendBtn) { sendBtn.innerHTML = '■'; sendBtn.classList.add('stop'); }
    if (typeof updateSendButtonState === 'function') updateSendButtonState();
    const enabledTools = state.agentConfig?.enabledTools || ['graph_schema', 'execute_cypher', 'document_search', 'skill_view', 'web_search', 'web_extract', 'clarify', 'todo', 'mineru_parse'];
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

    try {
        var recentHistory = getRecentMessagesForContext(currentBranch.messages);
        // 主动发送时排除最后一条消息（它就是当前 input，避免重复）；续写(skipLastHistory=false)时保留完整历史
        if (options.skipLastHistory !== false && recentHistory.length > 0) {
            recentHistory = recentHistory.slice(0, -1);
        }
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
            },
            context_length: state.settings?.contextLength || 10,
            branch_id: state.currentBranchId || '',
            permission_mode: state.agentConfig?.permissionMode || 'safe',
            engine: state.agentConfig?.engine || 'custom',
            max_rounds: (state.agentConfig?.maxRounds === 0 || state.agentConfig?.maxRounds) ? state.agentConfig.maxRounds : 8,
            file_ids: overrideFileIds ? overrideFileIds : (function(){
                var msgs = state.branches[state.currentBranchId]?.messages || [];
                for (var i = msgs.length - 1; i >= 0; i--) {
                    var m = msgs[i];
                    if (m.role === 'user' && m.attachments && m.attachments.length > 0) {
                        return m.attachments.map(function(a){ return a.file_id; }).filter(function(f){ return !!f; });
                    }
                    if (m.role === 'user') break;
                }
                return [];
            })(),
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
            if (state.settings.fileOpenMode === 'backend') {
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
    // 已上传到后端 → 用统一后端方式打开
    if (state.settings.fileOpenMode === 'backend') {
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
