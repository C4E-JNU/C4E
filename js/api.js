        /** 依赖个人设置的初始化（在 hydrateUserSettings 完成后调用）。
         *  ⚠️ 凡是「读 localStorage 里的个人设置」的初始化都必须放这里 ——
         *  这些键的本地缓存要等 hydrate 从后端灌回来才有值。 */
        function initAfterSettings() {
            // 已启用模型：state 里不能读（state.js 在脚本解析时就求值，
            // 那时 hydrate 还没跑），必须在这之后读缓存。
            state.enabledModels = JSON.parse(localStorage.getItem('ragagent-enabled-models') || '[]');

            initProviderSystem(); // 新的服务商系统初始化 (替代 loadApiKeys)
            loadSettings();       // 填 state.settings / state.neo4j
            loadBranches();
            updateUI();
            initSidebar();
            initFileAttachments(); // 新的文件附件系统
            initDatabase();
            initNotesPanel(); // 初始化笔记面板

            // 更新聊天区模型选择器
            updateChatModelSelector();

            // 以下原先在 main.js 顶层同步执行 → 会跑在 hydrate 之前读不到设置，
            // 故统一移到这里（依赖 ragagent-agent-config / state.neo4j）
            loadAgentConfigFromLocal();
            initAgentConfigPanel();
            updateNeo4jStatusUI(state.neo4j.connected);   // state.neo4j 由上面 loadSettings 填

            // 恢复上次使用的模型
            const savedModel = localStorage.getItem('ai-current-model');
            if (savedModel && modelConfig[savedModel]) {
                selectModel(savedModel);
            } else if (!state.currentModel) {
                // 没有保存的模型或模型不存在，选第一个可用模型
                const models = Object.keys(modelConfig);
                if (models.length > 0) selectModel(models[0]);
            }

            // 事件推送（SSE）：另一台设备改了对话/条目时通知本页。
            // 放在这里是因为它要先有登录身份（未登录=游客，没有账号数据要同步）。
            if (typeof startEvents === 'function') startEvents();
        }

        function init() {
            // 探测后端部署平台：非 Windows（Linux/服务器）时隐藏「本机打开」类按钮
            fetch('/api/platform').then(function (r) { return r.json(); }).then(function (d) {
                window.C4EAI_PLATFORM = d.platform || 'win32';
            }).catch(function () { window.C4EAI_PLATFORM = 'win32'; });

            // 个人设置 + 对话历史先拉回 localStorage 缓存，再初始化依赖它们的模块。
            // ⚠️ 对话必须在 loadBranches()（在上面的 initAfterSettings 里）之前灌回来，
            //    否则新设备上读到的是空缓存 → 对话全没了。
            // （事件监听不依赖这些，保持在外面照旧绑定）
            Promise.allSettled([hydrateUserSettings(), hydrateUserChats()]).finally(initAfterSettings);

            // 事件监听
            startExperienceBtn.addEventListener('click', () => {
                // 启动页唯一入口：进入全屏 = 对话界面（退出全屏即回到启动页）
                if (typeof toggleFullScreen === 'function') toggleFullScreen();
            });



            modelCards.forEach(card => {
                card.addEventListener('click', () => {
                    const model = card.dataset.model;
                    selectModel(model);
                });
            });

            // saveKeyBtn.addEventListener('click', saveApiKey); // Deprecated: New provider system uses own save logic
            sendBtn.addEventListener('click', () => {
                // 手动检查禁用状态（disabled 属性已被移除，避免阻止点击事件）
                if (sendBtn.classList.contains('btn-disabled')) return;
                if (state.isGenerating) {
                    stopGeneration();
                } else {
                    sendMessage();
                }
            });

            // 顶部导航栏自动显隐逻辑 (非全屏模式增强)
            let lastScrollTop = 0;
            const header = document.querySelector('header');

            function updateHeaderState(e) {
                // 返回主页态（body.full-screen-mode）：头部显隐由 CSS 处理，逻辑跳过
                if (document.body.classList.contains('full-screen-mode')) return;

                const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                const isMouseAtTop = e ? e.clientY <= 70 : false;

                if (isMouseAtTop) {
                    // 功能1：鼠标置于顶部时，导航栏展开
                    header.classList.remove('hidden-header');
                } else if (scrollTop <= 100) {
                    // 页面处于顶部附近，始终显示
                    header.classList.remove('hidden-header');
                } else {
                    // 页面处于非顶部且鼠标不在顶部，逻辑决定是否收起
                    if (e && e.type === 'mousemove') {
                        // 鼠标离开顶部触发区，自动收起
                        header.classList.add('hidden-header');
                    } else if (!e) {
                        // 滚动事件触发：维持原有功能（向下滚动收起，向上滚动展开）
                        if (scrollTop > lastScrollTop) {
                            header.classList.add('hidden-header');
                        } else {
                            header.classList.remove('hidden-header');
                        }
                    }
                }
                lastScrollTop = scrollTop <= 0 ? 0 : scrollTop;
            }

            window.addEventListener('scroll', () => updateHeaderState(), { passive: true });
            window.addEventListener('mousemove', (e) => updateHeaderState(e), { passive: true });

            messageInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (!sendBtn.classList.contains('btn-disabled')) sendMessage();
                }
            });

            // 监听输入，实时更新发送按钮状态
            messageInput.addEventListener('input', () => {
                if (!state.isGenerating) {
                    const hasText = messageInput.value.trim().length > 0;
                    const hasKey = state.currentModel && state.apiKeys[state.currentModel];
                    setSendBtnDisabled(!hasText || !hasKey);
                }
            });

            // 聊天区模型选择器事件
            chatModelSelector.addEventListener('change', (e) => {
                const selectedModel = e.target.value;
                if (selectedModel && selectedModel !== state.currentModel) {
                    selectModel(selectedModel);
                }
            });

            // 新建对话按钮事件
            newChatBtn.addEventListener('click', createNewConversation);
            newChatBtnEmpty.addEventListener('click', createNewConversation);

            // 分支重命名事件
            cancelRenameBtn.addEventListener('click', closeRenameBranchModal);
            confirmRenameBtn.addEventListener('click', renameCurrentBranch);

            // 数据库事件
            dbAnalysisBtn.addEventListener('click', openDatabaseModal);
            // manageDatabaseBtn已移除，功能可通过顶部导航栏的数据库上传按钮访问
            // 智能体配置面板由 agent_config.js 的 initAgentConfigPanel() 负责
            // 全屏按钮绑定
            document.getElementById('full-screen-toggle').addEventListener('click', toggleFullScreen);

            // 设置相关事件
            settingsBtn.addEventListener('click', openSettings);
            closeSettingsBtn.addEventListener('click', closeSettings);
            saveSettingsBtn.addEventListener('click', saveSettings);

            // 设置滑块事件
            temperatureSlider.addEventListener('input', (e) => {
                tempValue.textContent = e.target.value;
            });

            contextSlider.addEventListener('input', (e) => {
                contextValue.textContent = e.target.value;
                updateTokenDisplay();  // 调整上下文轮次 → 立即刷新发送给LLM的Token数
            });

            maxTokensInput.addEventListener('input', (e) => {
                maxTokensValue.textContent = e.target.value;
            });

            expectedContextTokensInput.addEventListener('input', (e) => {
                const v = document.getElementById('expected-context-tokens-value');
                if (v) v.textContent = e.target.value;
                updateTokenDisplay();  // 阈值变化 → 立即刷新警告状态
            });

                      // 文件打开方式（三选一：后端打开 / 浏览器新标签 / 应用内浮窗）— 仅 UI，保存时才提交
                            const fileOpenModeGroup = document.getElementById('file-open-mode-group');
                            if (fileOpenModeGroup) {
                                fileOpenModeGroup.addEventListener('click', (e) => {
                                    const btn = e.target.closest('.mode-seg-btn');
                                    if (!btn) return;
                                    state.settings.fileOpenMode = btn.dataset.mode || 'backend';
                                    updateFileOpenModeUI();
                                });
                            }
                            window.updateFileOpenModeUI = updateFileOpenModeUI;
                            updateFileOpenModeUI();
            
                            // 新增：原始输出过程开关事件 — 仅 UI，保存时才提交
              const rawDataToggle = document.getElementById('raw-data-toggle');
              if (rawDataToggle) {
                  rawDataToggle.addEventListener('change', () => {
                      document.getElementById('raw-data-status').textContent = rawDataToggle.checked ? '开启' : '关闭';
                  });
              }

            // 初始加载设置值显示
            tempValue.textContent = state.settings.temperature;
            contextValue.textContent = state.settings.contextLength;
            maxTokensValue.textContent = state.settings.maxTokens;

            // 渲染当前分支的消息
            renderMessages();

            // 监听对话容器滚动，检测用户是否手动向上滚动
            messagesContainer.addEventListener('scroll', () => {
                const isAtBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < 50;
                if (isAtBottom) {
                    state.userHasScrolledUp = false;
                } else if (state.isGenerating) {
                    // 如果正在生成且用户向上滚动力度较大，标记向上滚动
                    state.userHasScrolledUp = true;
                }
            });
        }

        // ==================== 更新聊天区模型选择器 ====================
        function updateChatModelSelector() {
            chatModelSelector.innerHTML = '<option value="" disabled selected>选择模型</option>';

            // 只显示已“亮起”（激活）且已配置API的模型
            let hasAnyOption = false;
            for (const modelId of state.enabledModels) {
                const config = modelConfig[modelId];
                if (config && state.apiKeys[modelId]) {
                    const option = document.createElement('option');
                    option.value = modelId;
                    option.textContent = config.name;
                    if (modelId === state.currentModel) {
                        option.selected = true;
                    }
                    chatModelSelector.appendChild(option);
                    hasAnyOption = true;
                }
            }

            // 如果没有配置任何模型，添加一个提示选项
            if (!hasAnyOption) {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = '请先配置并“亮起”模型';
                option.disabled = true;
                chatModelSelector.appendChild(option);
            }
        }

        // ==================== 新建对话功能 ====================
        function createNewConversation() {
            // 检查是否有当前对话且是否有内容
            const currentBranch = state.branches[state.currentBranchId];

            // 如果当前是临时对话且有内容，先保存它
            if (currentBranch && currentBranch.isTemporary && currentBranch.hasContent) {
                saveTempConversation(currentBranch);
            }

            // 创建新的永久对话（即使空对话也保存到侧边栏）
            // 计算可用的最小命名序号：扫描已有分支名，取最小空缺
            var existingNumbers = [];
            for (var bid in state.branches) {
                var bName = state.branches[bid].name || '';
                var match = bName.match(/^新建对话(\d*)$/);
                if (match) {
                    existingNumbers.push(match[1] === '' ? 1 : parseInt(match[1], 10));
                }
            }
            var convNumber = 1;
            while (existingNumbers.indexOf(convNumber) !== -1) {
                convNumber++;
            }
            var convName = convNumber === 1 ? '新建对话' : '新建对话' + convNumber;

            var newBranchId = 'branch-' + state.branchCounter;
            state.branchCounter++;

            state.branches[newBranchId] = {
                id: newBranchId,
                name: convName,
                messages: [],
                parentId: null,
                createdAt: new Date().toISOString(),
                totalTokens: 0,
                sessionTokens: 0,
                hasContent: false,
                isTemporary: false,
                notes: []
            };

            state.currentBranchId = newBranchId;
            state.documentUpload.pendingAttachments = []; state.documentUpload._errorFiles = [];
            saveBranches();
            renderMessages();
            updateTokenDisplay();
            updateHistoryList();
            updateSelectedFilesList();
            hideUploadPanel();
            // 如果笔记面板打开，同步切换
            syncNotesPanel();

            // 清空输入框并聚焦
            messageInput.value = '';
            setSendBtnDisabled(true);
            messageInput.focus();

            // 显示提示消息
            const feedbackTarget = document.activeElement === newChatBtnEmpty ? newChatBtnEmpty : newChatBtn;
            showActionFeedback('新建对话已创建', 'success', feedbackTarget);
        }

        function saveTempConversation(branch) {
            if (!branch.hasContent || branch.messages.length === 0) {
                // 如果没有内容，删除临时对话
                delete state.branches[branch.id];
                return;
            }

            // 生成对话名称（使用第一条用户消息的前20个字符）
            let conversationName = '新对话';
            const firstUserMessage = branch.messages.find(msg => msg.role === 'user');
            if (firstUserMessage && firstUserMessage.content) {
                const preview = firstUserMessage.content.substring(0, 20);
                conversationName = preview + (firstUserMessage.content.length > 20 ? '...' : '');
            }

            // 创建正式对话ID
            const newBranchId = `branch-${state.branchCounter}`;
            state.branchCounter++;

            // 复制对话数据
            state.branches[newBranchId] = {
                ...branch,
                id: newBranchId,
                name: conversationName,
                isTemporary: false
            };

            // 删除临时对话
            delete state.branches[branch.id];

            // 切换到新对话
            state.currentBranchId = newBranchId;

            saveBranches();
            updateHistoryList();
            // 如果笔记面板打开，同步更新
            syncNotesPanel();

            return newBranchId;
        }

        // ==================== 统一API调用函数 ====================

        // ==================== 统一的消息发送函数 ====================

        function stopGeneration() {
            if (state.isGenerating && state.abortController) {
                state.abortController.abort();
                state.isGenerating = false;
                sendBtn.innerHTML = '➤';
                sendBtn.classList.remove('stop');
                addSystemMessageWithDelete('已停止回答生成。');
            }
        }

        async function continueGeneration(messageId) {
            const currentBranch = state.branches[state.currentBranchId];
            const message = currentBranch.messages.find(m => m.id === messageId);
            if (!message) return;

            // 获取到该消息为止的上下文
            const messageIndex = currentBranch.messages.findIndex(m => m.id === messageId);
            const contextMessages = currentBranch.messages.slice(0, messageIndex);

            // 最后一个用户消息作为问题
            let lastUserMessage = '';
            for (let i = messageIndex - 1; i >= 0; i--) {
                if (currentBranch.messages[i].role === 'user') {
                    lastUserMessage = currentBranch.messages[i].content;
                    break;
                }
            }

            if (!lastUserMessage) {
                addSystemMessageWithDelete('无法继续生成：未找到上文问题。');
                return;
            }

            // 收集【该消息及之前】user 消息的附件 file_id（保证续写时后端重新注入 PDF/文档全文）
            var continueFileIds = [];
            for (var ci = 0; ci < currentBranch.messages.length; ci++) {
                var cm = currentBranch.messages[ci];
                if (cm.attachments && cm.attachments.length > 0) {
                    cm.attachments.forEach(function(a) {
                        if (a.file_id && continueFileIds.indexOf(a.file_id) === -1) continueFileIds.push(a.file_id);
                    });
                }
                if (ci === messageIndex) break; // 只到被续写的消息为止
            }

            // 极简指令；续写走后端 Agent（可重新注入文件上下文）
            const minimalistTrigger = "继续前面的回答。注意：请直接接着上文最后一个字继续写，不要有任何开场白或衔接词，必须实现无缝衔接。";

            // 调用 agent（config engine 若为 langchain 则走 langchain agent）
            // skipLastHistory=false：续写保留完整历史（含第一轮 agent 回复），否则删掉最后一条看不到之前回答
            // mergeInto：续写完成后把内容合并进目标消息 → 同一消息框显示【原文+续写+推理】，不新建消息框
            await reactAgentLoop(minimalistTrigger, { fileIds: continueFileIds, skipLastHistory: false, mergeInto: messageId });
        }

        // ==================== 处理API错误 ====================
