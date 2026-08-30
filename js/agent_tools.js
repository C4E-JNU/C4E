// ==================== 智能体工具注册表 ====================
// 每个工具定义：name, description, parameters (JSON Schema), execute 函数

const agentTools = {};

function registerAgentTool(name, description, parameters, executeFn) {
    agentTools[name] = { name, description, parameters, execute: executeFn };
}

// ── 工具1: graph_schema ──────────────────────────────
// 获取 Neo4j 图谱的节点类型、关系类型、属性结构
registerAgentTool(
    'graph_schema',
    '获取 Neo4j 知识图谱的完整结构，包括所有节点类型（如 Pollutant, Catalyst）、关系类型（如 DEGRADES）、各类型的属性列表、向量索引信息。首次查询时调用此工具了解图谱结构。',
    {
        type: 'object',
        properties: {},
        required: []
    },
    async function (args, ctx) {
        const connector = ctx.connector;
        if (!connector || !connector.isConnected()) {
            return JSON.stringify({ error: 'Neo4j 未连接' });
        }
        try {
            // 节点类型 + 属性
            const nodeProps = await connector.run(`
                CALL db.schema.nodeTypeProperties()
                YIELD nodeLabels, propertyName, propertyTypes, mandatory
                RETURN nodeLabels, propertyName, propertyTypes, mandatory
                ORDER BY nodeLabels, propertyName
            `);
            // 关系类型
            const rels = await connector.run(`
                CALL db.relationshipTypes() YIELD relationshipType
                RETURN relationshipType ORDER BY relationshipType
            `);
            // 节点计数
            const labels = await connector.run(`CALL db.labels() YIELD label RETURN label ORDER BY label`);
            const counts = {};
            for (const r of labels) {
                const cnt = await connector.run(`MATCH (n:\`${r.label}\`) RETURN count(n) AS cnt`);
                counts[r.label] = cnt[0]?.cnt || 0;
            }
            // 索引
            const indexes = await connector.run('SHOW INDEXES');
            const vectorIdx = indexes.filter(i => i.type === 'VECTOR').map(i => ({
                name: i.name, labelsOrTypes: i.labelsOrTypes, properties: i.properties
            }));

            // 格式化输出
            const nodeTypes = {};
            for (const r of nodeProps) {
                const label = Array.isArray(r.nodeLabels) ? r.nodeLabels[0] : r.nodeLabels;
                if (!nodeTypes[label]) nodeTypes[label] = { properties: [], count: counts[label] || 0 };
                if (r.propertyName) nodeTypes[label].properties.push({ name: r.propertyName, type: r.propertyTypes });
            }

            return JSON.stringify({
                nodeTypes: Object.entries(nodeTypes).map(([label, info]) => ({
                    label, count: info.count,
                    properties: info.properties.map(p => `${p.name}: ${p.type}`)
                })),
                relationships: rels.map(r => r.relationshipType),
                vectorIndexes: vectorIdx,
            }, null, 2);
        } catch (err) {
            return JSON.stringify({ error: `查询 schema 失败: ${err.message}` });
        }
    }
);

// ── 工具2: execute_cypher ────────────────────────────
// 执行任意 Cypher 查询，返回结果数组
registerAgentTool(
    'execute_cypher',
    '执行任意 Cypher 查询语句，返回查询结果数组。所有图谱数据都通过此工具获取。支持任何 MATCH / RETURN / CALL 语句。如果是写操作（CREATE / DELETE / SET）需谨慎。',
    {
        type: 'object',
        properties: {
            cypher: {
                type: 'string',
                description: '要执行的 Cypher 查询语句，例如: MATCH (n:Pollutant) WHERE n.name CONTAINS $q RETURN n.name, n.formula LIMIT 10'
            },
            params: {
                type: 'object',
                description: '查询参数（可选），用于安全传参，例如: {"q": "CIP"}',
                default: {}
            }
        },
        required: ['cypher']
    },
    async function (args, ctx) {
        const connector = ctx.connector;
        if (!connector || !connector.isConnected()) {
            return JSON.stringify({ error: 'Neo4j 未连接' });
        }
        const cypher = args.cypher || '';
        const params = args.params || {};
        if (!cypher.trim()) {
            return JSON.stringify({ error: 'Cypher 查询不能为空' });
        }
        try {
            const results = await connector.run(cypher, params);
            const truncated = results.length > 20 ? results.slice(0, 20) : results;
            const result = {
                count: results.length,
                truncated: results.length > 20,
                columns: results.length > 0 ? Object.keys(results[0]) : [],
                data: truncated
            };
            return JSON.stringify(result, null, 2);
        } catch (err) {
            return JSON.stringify({ error: `Cypher 查询失败: ${err.message}`, cypher: cypher });
        }
    }
);

// ── 工具3: document_search ───────────────────────────
// 搜索已上传的知识库文档
registerAgentTool(
    'document_search',
    '搜索已上传的知识库文档。在用户问到之前上传过的文档内容时使用。返回匹配的文档片段及来源。只有上传文档后才能使用此工具。',
    {
        type: 'object',
        properties: {
            query: {
                type: 'string',
                description: '搜索关键词或问题',
            }
        },
        required: ['query']
    },
    async function (args, ctx) {
        const query = args.query || '';
        if (!query.trim()) {
            return JSON.stringify({ error: '搜索词不能为空' });
        }
        if (typeof searchDocuments !== 'function') {
            return JSON.stringify({ error: '知识库搜索功能不可用' });
        }
        try {
            const result = await searchDocuments(query);
            const results = result.results || [];
            if (results.length === 0) {
                return JSON.stringify({ note: '知识库未匹配到相关内容', data: [] });
            }
            return JSON.stringify({
                count: results.length,
                data: results.slice(0, 5).map(r => ({
                    source: r.doc_name,
                    score: r.score,
                    content: r.chunk || r.content || ''
                }))
            }, null, 2);
        } catch (err) {
            return JSON.stringify({ error: `文档搜索失败: ${err.message}` });
        }
    }
);

// ── 工具4: web_search ──────────────────────────────
// 搜索网页（浏览器环境受限，尝试通过 CORS 代理搜索）
registerAgentTool(
    'web_search',
    '搜索互联网获取最新信息。当用户问到最新研究、新闻、论文或你不确定的知识时，使用此工具获取实时信息。默认返回 5 条结果，包含标题、URL 和摘要。',
    {
        type: 'object',
        properties: {
            query: {
                type: 'string',
                description: '搜索关键词，支持 site: 等高级搜索语法'
            },
            limit: {
                type: 'integer',
                description: '返回结果数量（1-10），默认 5',
                default: 5,
                minimum: 1,
                maximum: 10
            }
        },
        required: ['query']
    },
    async function(args, ctx) {
        const query = (args.query || '').trim();
        const limit = Math.min(args.limit || 5, 10);
        if (!query) return JSON.stringify({ error: '搜索词不能为空' });

        // 方法1：走本地服务器代理（start.bat 模式，无 CORS 问题）
        try {
            const proxyUrl = `/api/proxy/search?q=${encodeURIComponent(query)}&limit=${limit}`;
            const resp = await fetch(proxyUrl, { signal: AbortSignal.timeout(10000) });
            if (resp.ok) {
                const data = await resp.json();
                if (data.results && data.results.length > 0) {
                    return JSON.stringify({ source: 'local_proxy', count: data.count, results: data.results });
                }
            }
        } catch(e) { /* 本地代理不可用 */ }

        // 本地代理不可用，直接返回
        return JSON.stringify({
            error: '搜索功能当前不可用',
            note: '浏览器环境下无法直接访问搜索引擎。请确保 start.bat（Python 服务器）在运行。',
            query: query
        });
    }
);

// ── 工具5: web_extract ─────────────────────────────
// 抓取指定 URL 的网页内容
registerAgentTool(
    'web_extract',
    '抓取指定 URL 的网页内容并转为纯文本。当需要阅读论文全文、博客文章、新闻详情时使用。支持大多数公开网页。注意：部分网站可能限制访问。',
    {
        type: 'object',
        properties: {
            url: {
                type: 'string',
                description: '要抓取的网页完整 URL，必须以 http:// 或 https:// 开头'
            }
        },
        required: ['url']
    },
    async function(args, ctx) {
        const url = (args.url || '').trim();
        if (!url) return JSON.stringify({ error: 'URL 不能为空' });
        if (!url.startsWith('http://') && !url.startsWith('https://')) {
            return JSON.stringify({ error: 'URL 必须以 http:// 或 https:// 开头' });
        }

        // 限制可能有害的域名
        const blocked = ['localhost', '127.0.0.1', '0.0.0.0', '10.', '192.168.', '172.16.'];
        for (const b of blocked) {
            if (url.includes(b)) return JSON.stringify({ error: '不允许访问内网地址' });
        }

        try {
            // 方法1：走本地服务器代理
            try {
                const proxyResp = await fetch('/api/proxy/fetch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url }),
                    signal: AbortSignal.timeout(15000)
                });
                if (proxyResp.ok) {
                    const data = await proxyResp.json();
                    if (data.content && data.content.length > 50) {
                        return JSON.stringify(data, null, 2);
                    }
                }
            } catch(e) { /* 本地代理不可用 */ }

            return JSON.stringify({ error: '无法访问该网页。请确保 start.bat（Python 服务器）在运行。' });
        } catch (err) {
            if (err.name === 'TimeoutError' || err.name === 'AbortError') {
                return JSON.stringify({ error: '请求超时，该网站可能响应较慢或被屏蔽' });
            }
            return JSON.stringify({ error: `抓取失败: ${err.message}` });
        }
    }
);

// ── 工具6: clarify ─────────────────────────────────
// 向用户提问确认，获取更多信息
registerAgentTool(
    'clarify',
    '当你需要更多信息才能回答用户问题时，使用此工具向用户提问。只用于获取额外信息，不要用于确认用户是否同意某事。一次只问一个问题，等待用户回复后再继续。',
    {
        type: 'object',
        properties: {
            question: {
                type: 'string',
                description: '你要向用户提出的问题，清晰明确，一次只问一个'
            }
        },
        required: ['question']
    },
    async function(args, ctx) {
        const question = (args.question || '').trim();
        if (!question) return JSON.stringify({ error: '问题不能为空' });

        // 在聊天中显示问题，等待用户回复
        const msgId = 'clarify-' + Date.now();
        const currentBranch = state.branches[state.currentBranchId];
        currentBranch.messages.push({
            id: msgId,
            role: 'assistant',
            content: `❓ ${question}\n\n> 请回复你的答案，我会继续处理。`,
            timestamp: new Date().toISOString(),
            branchId: state.currentBranchId,
            type: 'agent_clarify'
        });
        renderMessages();
        messageInput.placeholder = '请回答上述问题...';

        // 这个工具比较特殊：返回后 LLM 会看到 Observation，但需要用户先输入
        // 所以在 Observation 里提示用户
        return JSON.stringify({
            status: 'awaiting_user_input',
            question: question,
            instruction: '用户将在界面中输入回答。请告诉用户你需要什么信息，等待回复。'
        });
    }
);

// ── 工具7: todo ────────────────────────────────────
// 任务清单管理
registerAgentTool(
    'todo',
    '管理任务清单。可以创建任务、标记完成、列出任务。当你需要跟踪多步工作进度或帮用户记住待办事项时使用。任务会保存在当前会话中。',
    {
        type: 'object',
        properties: {
            action: {
                type: 'string',
                enum: ['list', 'add', 'complete', 'remove'],
                description: '操作类型: list=列出所有任务, add=添加任务, complete=标记完成, remove=删除任务'
            },
            task: {
                type: 'string',
                description: '任务描述（add 操作时需要）'
            },
            task_id: {
                type: 'integer',
                description: '任务编号（complete/remove 操作时需要）'
            }
        },
        required: ['action']
    },
    async function(args, ctx) {
        const action = args.action || 'list';
        const task = (args.task || '').trim();
        const taskId = args.task_id;

        // 初始化任务列表
        if (!state._todoList) state._todoList = [];
        const todos = state._todoList;

        switch (action) {
            case 'add':
                if (!task) return JSON.stringify({ error: '任务描述不能为空' });
                todos.push({ id: todos.length + 1, content: task, done: false, created: new Date().toISOString() });
                return JSON.stringify({ status: 'ok', message: `已添加任务: ${task}`, total: todos.length });

            case 'complete':
                if (!taskId) return JSON.stringify({ error: '需要指定任务编号' });
                const item = todos.find(t => t.id === taskId);
                if (!item) return JSON.stringify({ error: `任务 #${taskId} 不存在` });
                item.done = true;
                return JSON.stringify({ status: 'ok', message: `✅ 已完成: ${item.content}`, total: todos.length });

            case 'remove':
                if (!taskId) return JSON.stringify({ error: '需要指定任务编号' });
                const idx = todos.findIndex(t => t.id === taskId);
                if (idx === -1) return JSON.stringify({ error: `任务 #${taskId} 不存在` });
                const removed = todos.splice(idx, 1)[0];
                return JSON.stringify({ status: 'ok', message: `已删除: ${removed.content}`, total: todos.length });

            case 'list':
            default:
                if (todos.length === 0) return JSON.stringify({ status: 'ok', tasks: [], note: '暂无任务' });
                const list = todos.map(t => ({
                    id: t.id,
                    content: t.content,
                    done: t.done
                }));
                const doneCount = todos.filter(t => t.done).length;
                return JSON.stringify({
                    status: 'ok',
                    total: todos.length,
                    completed: doneCount,
                    pending: todos.length - doneCount,
                    tasks: list
                }, null, 2);
        }
    }
);

// ── 工具8: conversation_notes ──────────────────────────
registerAgentTool(
    'conversation_notes',
    '管理当前对话的笔记卡片。可以列出、添加、更新、删除笔记卡片。每个笔记卡片包含标题(title)和内容(content)，内容支持完整Markdown。',
    {
        type: 'object',
        properties: {
            action: {
                type: 'string',
                enum: ['list', 'add', 'update', 'delete'],
                description: '操作类型: list, add, update, delete'
            },
            note_id: { type: 'string', description: '笔记ID（update/delete时需要）' },
            title: { type: 'string', description: '卡片标题（add时需要）' },
            content: { type: 'string', description: '卡片内容，支持Markdown（add时需要）' }
        },
        required: ['action']
    },
    async function(args, ctx) {
        var branch = state.branches[state.currentBranchId];
        if (!branch) return JSON.stringify({ error: '当前对话不存在' });
        if (!branch.notes) branch.notes = [];
        switch (args.action) {
            case 'add': {
                var title = (args.title || '').trim();
                var content = (args.content || '').trim();
                if (!title && !content) return JSON.stringify({ error: '标题和内容不能同时为空' });
                var note = {
                    id: 'note-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6),
                    title: title || '新笔记',
                    content: content || '',
                    createdAt: new Date().toISOString(),
                    updatedAt: new Date().toISOString()
                };
                branch.notes.push(note);
                saveBranches();
                if (typeof renderNotesPanel === 'function') renderNotesPanel();
                return JSON.stringify({ status: 'ok', message: '已添加笔记: ' + title, note_id: note.id });
            }
            case 'update': {
                var note = branch.notes.find(function(n) { return n.id === args.note_id; });
                if (!note) return JSON.stringify({ error: '笔记不存在' });
                if (args.title !== undefined) note.title = (args.title || '').trim() || '未命名笔记';
                if (args.content !== undefined) note.content = (args.content || '').trim();
                note.updatedAt = new Date().toISOString();
                saveBranches();
                if (typeof renderNotesPanel === 'function') renderNotesPanel();
                return JSON.stringify({ status: 'ok', message: '已更新笔记: ' + note.title });
            }
            case 'delete': {
                var idx = branch.notes.findIndex(function(n) { return n.id === args.note_id; });
                if (idx === -1) return JSON.stringify({ error: '笔记不存在' });
                branch.notes.splice(idx, 1);
                saveBranches();
                if (typeof renderNotesPanel === 'function') renderNotesPanel();
                return JSON.stringify({ status: 'ok', message: '已删除笔记' });
            }
            case 'list': default: {
                if (branch.notes.length === 0) return JSON.stringify({ status: 'ok', notes: [], note: '暂无笔记' });
                return JSON.stringify({ status: 'ok', total: branch.notes.length, notes: branch.notes.map(function(n) {
                    return { id: n.id, title: n.title, content: n.content };
                })});
            }
        }
    }
);
