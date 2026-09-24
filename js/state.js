        // ==================== 状态管理 ====================
        const state = {
            currentModel: null,
            apiKeys: {
                'deepseek-chat': null,
                'deepseek-r1': null,
                'qwen-3.0': null,
                'qwen3-flash': null,
                'gemini-2.5-pro': null,
                'gemini-3-pro': null,
                'gpt-5-mini': null,
                'gpt-5.2': null,
                'gpt-4o': null,
                'grok-4.1': null,
                'claude-4.5-opus': null,
                'llama-4': null
            },

            // 分支对话管理
            branches: {
            },
            currentBranchId: null,
            branchCounter: 0,

            // 文档上传状态
            documentUpload: {
                currentDocument: null,
                uploadedDocuments: [],
                pendingAttachments: [] // 待发送的附件列表
            },

            // 数据库状态（文档已上传/加载，不再有模式开关）
            database: {
                loaded: false
            },

            // Neo4j 配置
            neo4j: {
                uri: 'neo4j+s://7d99a1d8.databases.neo4j.io',
                user: '7d99a1d8',
                password: '',
                connected: false
            },

            // 智能体配置
            agentConfig: {
                enabledTools: ['graph_schema', 'execute_cypher', 'shared_search', 'shared_upload', 'search_history', 'conversation_notes', 'skill_list', 'skill_view', 'skill_write', 'web_search', 'web_extract', 'todo', 'mineru_parse', 'read_file', 'write_file', 'list_files', 'run_code', 'terminal', 'delegate_task'],
                loadedSkills: ['graph-guide', 'self-modify'],
                permissionMode: 'safe',
                engine: 'custom',          // custom=裸API手写循环 / langchain=LangChain流式
                maxRounds: 8,              // 工具循环步数，0=无限
            },

            // 模型设置
                settings: {
                     temperature: 0.7,
                     contextLength: 10,
                     expectedContextTokens: 4000,
                     streaming: true,
                     showRawData: false,
                     fileOpenMode: 'backend', // 'backend' = 服务器os.startfile, 'browser' = 浏览器预览
                     databaseSearchMode: 'vector', // 纯向量搜索（正则已移除）
                     fontSizeConv: 0.85,       // 对话管理字号 (rem)
                     fontSizeHistory: 0.65,    // 历史记录字号 (rem)
                     fontSizeMessages: 0.75,   // 对话回复消息字号 (rem)
                     fontSizeNotes: 0.75,      // 笔记字号 (rem)
                    fontSizeThinking: 0.7,    // 思考链/推理字号 (rem)
                    fontSizeEdit: 0.9,          // 浮动/内联编辑器字号 (rem)
                    fontSizeModal: 0.9,         // 居中模态框字号 (rem)
                    fontSizePopup: 0.85,        // 小浮层字号 (rem)
                    fontSizeToast: 0.85,        // 微提示/轻提示字号 (rem)
                    // 超时保护（秒，0=关闭该级）
                    timeoutNudge: 120,          // 催促：注入「尽快收尾」提示
                    timeoutWarn: 240,           // 警告：注入「立即作答」提示
                    timeoutAbort: 420,          // 终止：强制结束并返回超时说明
                    // 子智能体（delegate_task）
                    subagentEnabled: true,
                    subagentMaxRounds: 4,       // 每个子智能体的工具循环轮次上限
                    subagentMaxParallel: 2,     // 并发上限（1-8，可自由调节）
                    subagentMaxTasks: 4,        // 单次最多派发子任务数（1-8）
                    subagentNudge: 60,          // 子智能体三级超时：催促
                    subagentWarn: 150,          // 子智能体三级超时：警告
                    subagentAbort: 300,         // 子智能体三级超时：终止
                     },

            // 侧边栏状态
            sidebar: {
                currentFilter: 'all',
                searchQuery: '',
                activeMessageId: null,
                expandedGroups: new Set()
            },

            // 历史视图模式
            historyMode: 'global',

            // 临时对话状态
            tempConversation: {
                id: null,
                messages: [],
                hasContent: false
            },

            // 运行状态
            isGenerating: false,
            abortController: null,
            userHasScrolledUp: false,
            tableTheme: localStorage.getItem('ragagent-table-theme') || '', // 全局表格皮肤
            enabledModels: JSON.parse(localStorage.getItem('ragagent-enabled-models') || '[]') // 记录已“亮起”的模型ID
        };

        // ── 全局辅助函数 ────────────────────────────────
        function loadHistoryMode() {
            // 已废弃：globalHistoryMode 已移除
            const savedExpandedGroups = localStorage.getItem('ai-expanded-groups');
            if (savedExpandedGroups) {
                try {
                    const groups = JSON.parse(savedExpandedGroups);
                    state.sidebar.expandedGroups = new Set(groups);
                } catch (e) {}
            }
        }

        function toggleHistoryMode() {
            // 已废弃
        }

        function splitIntoSentences(text) {
            const delimiters = /[。！？!?；;\n]+/;
            return text.split(delimiters).map(s => s.trim()).filter(s => s.length > 0);
        }

        var FILE_OPEN_MODE_LABEL = {
            backend: '后端打开',
            browser: '浏览器新标签',
            float: '应用内浮窗'
        };

        function updateFileOpenModeUI() {
            var mode = (state.settings && state.settings.fileOpenMode) || 'backend';
            var modeValue = document.getElementById('file-open-mode-value');
            if (modeValue) modeValue.textContent = FILE_OPEN_MODE_LABEL[mode] || FILE_OPEN_MODE_LABEL.backend;
            var group = document.getElementById('file-open-mode-group');
            if (group) {
                group.querySelectorAll('.mode-seg-btn').forEach(function (btn) {
                    btn.classList.toggle('active', btn.dataset.mode === mode);
                });
            }
        }

        function toggleFullScreen() {
            const isEntering = !document.body.classList.contains('full-screen-mode');
            const btn = document.getElementById('full-screen-toggle');
            if (!btn) return;
            if (isEntering) {
                document.body.classList.add('full-screen-mode');
                btn.classList.add('active');
                if (typeof showToast === 'function') showToast('已进入沉浸模式', 'success');
            } else {
                document.body.classList.add('no-scroll-transition');
                document.body.classList.remove('full-screen-mode');
                btn.classList.remove('active');
                const chatSection = document.querySelector('.chat-section');
                if (chatSection) chatSection.scrollIntoView({ behavior: 'auto', block: 'start' });
                requestAnimationFrame(() => {
                    setTimeout(() => document.body.classList.remove('no-scroll-transition'), 50);
                });
            }
        }

        function updateDatabaseStats() {
            // 旧的后端已废弃，保留空函数避免报错
            const statusEl = document.getElementById('database-status');
            if (statusEl) statusEl.textContent = state.database.loaded ? '已加载' : '未加载';
        }

        // ==================== DOM元素 ====================
