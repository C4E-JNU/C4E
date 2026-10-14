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
                uri: '',                   // 由用户自填，不预置任何实例
                user: '',                  // 由用户自填，不预置任何实例
                password: '',
                connected: false
            },

            // 智能体配置
            agentConfig: {
                enabledTools: ['graph_query', 'shared_search', 'shared_upload', 'web_search', 'web_extract', 'skill_list', 'skill_view', 'skill_write', 'read_file', 'write_file', 'list_files', 'list_outputs', 'search_files', 'find_files', 'edit_file', 'run_code', 'terminal', 'crossref_lookup', 'arxiv_search', 'semantic_scholar_search', 'gh_repo_info', 'gh_list_dir', 'gh_read_file', 'gh_search_repos', 'gh_search_code', 'gh_download_tarball', 'search_history', 'conversation_notes', 'todo', 'delegate_task', 'mineru_parse'],   // 默认全开（用户 2026-09-28：除访问权限外一律放开）
                loadedSkills: ['self-modify', 'mineru-agent-parse', 'literature-translate', 'literature-contradiction', 'literature-search', 'code-search', 'document-entry'],
                permissionMode: 'safe',
                engine: 'custom',          // custom=裸API手写循环 / langchain=LangChain流式
                maxRounds: 0,              // 0=无限轮次（用户 2026-09-28 定）
            },

            // 模型设置
                settings: {
                     temperature: 0.7,
                     contextLength: 10,
                     contextWindow: 262144,   // hard limit：只用于算清理触发线，本机绝不据此报错（用户 2026-10 定）
                     contextBudget: 0,        // 清理触发线（token）；0 = 自动 (window−输出预留)×75%，保底 ≥1024
                     maxTokens: 65536,        // 每次请求的 max_tokens（输出上限）；64K = DeepSeek 思考模式默认
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
                timeoutNudge: 0,            // 0=关闭该级（用户 2026-09-28：超时保护只留一天硬停）
                timeoutWarn: 0,
                timeoutAbort: 86400,        // 一天
                    // 子智能体（delegate_task）
                    subagentEnabled: true,
                subagentMaxRounds: 0,       // 0=无限
                subagentMaxParallel: 8,
                subagentMaxTasks: 8,
                subagentNudge: 0,
                subagentWarn: 0,
                subagentAbort: 86400,       // 一天
                subagentFeedSteps: false,   // false（默认）=子智能体只把摘要回喂主控，不回喂它读到的正文，防上下文爆炸
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

            // （原「临时对话状态 tempConversation」已于 2026-09-30 删除：零使用，且用户已取消
            //   「临时对话/草稿」概念 —— 所有对话一律永久，空对话也保留。）

            // 对话条目状态（内存态：运行时转圈 / 答完未读绿点）
            chatMarks: {},

            // 运行状态
            isGenerating: false,
            abortController: null,
            _runBranchId: null,          // 本次回答归属的对话（中途切走也不标错）
            userHasScrolledUp: false,
            tableTheme: localStorage.getItem('ragagent-table-theme') || '', // 全局表格皮肤（本机偏好，不同步）
            // 已启用的模型：这里留空 —— 本对象的字面量在**脚本解析时**求值，
            // 那时 hydrateUserSettings() 还没从后端灌回缓存。
            // 真正读缓存的地方在 api.js::initAfterSettings()。
            enabledModels: []
        };

        var FILE_OPEN_MODE_LABEL = {
            backend: t('misc.fileOpenBackend'),
            browser: t('misc.fileOpenBrowser'),
            float: t('misc.fileOpenFloat')
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
                if (window._sbRefit) window._sbRefit();   // 进对话区 → 复测窄空间
                // 记住"在对话界面"：WebView2 被系统回收后整页重载时，靠它原样回到这里
                try { localStorage.setItem('c4eai-view-mode', 'chat'); } catch (e) {}
                btn.classList.add('active');
                if (typeof showToast === 'function') showToast(t('misc.enteredChat'), 'success');
            } else {
                document.body.classList.add('no-scroll-transition');
                document.body.classList.remove('full-screen-mode');
                if (window._sbRefit) window._sbRefit();
                try { localStorage.setItem('c4eai-view-mode', 'home'); } catch (e) {}
                btn.classList.remove('active');
                // 退出全屏即回到启动页：滚到顶部，让大 logo 与入口按钮居中呈现
                window.scrollTo({ top: 0, behavior: 'auto' });
                requestAnimationFrame(() => {
                    setTimeout(() => document.body.classList.remove('no-scroll-transition'), 50);
                });
            }
        }


        // ==================== DOM元素 ====================
