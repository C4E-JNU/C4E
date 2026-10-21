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
            // 把「上次打开的对话」的内容渲染出来（用户 2026-09-27 报：刚进对话界面时
            // 默认亮起的对话一片空白、点它也没反应——loadBranches() 只恢复了
            // currentBranchId，列表由 initSidebar() 点亮那一条，但**没人调 renderMessages()**；
            // 再点当前那条又被 switchToBranch 的早退挡掉，必须点别的对话再点回来才出来）。
            if (typeof renderMessages === 'function') renderMessages();
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
                    // 生成中：发送位 = 终止（硬中断；已完成轮次保留）。催促在「收起思考」下方。
                    stopCurrentRun();
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
            messageInput.addEventListener('input', updateSendButtonState);

            // 聊天区模型选择器事件
            chatModelSelector.addEventListener('change', (e) => {
                const selectedModel = e.target.value;
                if (selectedModel && selectedModel !== state.currentModel) {
                    selectModel(selectedModel);
                }
            });

            // 新建对话按钮事件
            newChatBtn.addEventListener('click', function () { openWorkAreaPicker(newChatBtn); });
            newChatBtnEmpty.addEventListener('click', function () { openWorkAreaPicker(newChatBtnEmpty); });
            fetch('/api/user/work_areas').then(function (r) { return r.json(); }).then(function (d) {
                if (d && d.areas) state.workAreas = d.areas;
            }).catch(function () {});

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
            bindSettingsNav();   // 设置面板左分类切换/遮罩关闭/Esc

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

            // 上下文窗口（hard limit）：只影响清理时机的计算
            if (contextWindowInput) contextWindowInput.addEventListener('input', (e) => {
                const v = document.getElementById('context-window-value');
                if (v) v.textContent = e.target.value || '262144';
            });

            // 清理触发线（context budget）：0 = 自动
            if (contextBudgetInput) contextBudgetInput.addEventListener('input', (e) => {
                const v = document.getElementById('context-budget-value');
                if (v) v.textContent = (parseInt(e.target.value) > 0) ? e.target.value : t('misc.auto');
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
                      document.getElementById('raw-data-status').textContent = rawDataToggle.checked ? t('misc.on') : t('misc.off');
                  });
              }

            // 初始加载设置值显示
            tempValue.textContent = state.settings.temperature;
            contextValue.textContent = state.settings.contextLength;
            maxTokensValue.textContent = state.settings.maxTokens;

            // 渲染当前分支的消息
            renderMessages();

            // 监听对话容器滚动，检测用户是否手动向上滚动
            // 用户是否正在自己看上面的内容（用户 2026-09-27：上滑时流式输出不得强制拉到底）。
            // 旧实现只在"生成中"才置位 → 一开新生成又把正在读的人拽走；而且那个标记从来没人读。
            messagesContainer.addEventListener('scroll', () => {
                const isAtBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < 50;
                state.userHasScrolledUp = !isAtBottom;
            });
        }

        // ==================== 更新聊天区模型选择器 ====================
        function updateChatModelSelector() {
            chatModelSelector.innerHTML = t('in.model.opt');

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
                option.textContent = t('misc.noModelActive');
                option.disabled = true;
                chatModelSelector.appendChild(option);
            }
        }

        // ==================== 工作区域（新建对话时选/建） ====================
        // 用户 2026-10-03 定：不放进设置页 —— 新建对话时选工作区域；不选 = 默认工作区域。
        function _waFetch(url, opts) {
            return fetch(url, opts).then(function (r) { return r.json(); }).catch(function () { return null; });
        }
        function closeWaPicker() {
            var old = document.getElementById('wa-picker-popover');
            if (old) old.remove();
            document.removeEventListener('click', _waPickerOutside, true);
        }
        function _waPickerOutside(e) {
            var p = document.getElementById('wa-picker-popover');
            if (p && !p.contains(e.target)) closeWaPicker();
        }
        function openWorkAreaPicker(anchorBtn) {
            closeWaPicker();
            var pop = document.createElement('div');
            pop.id = 'wa-picker-popover';
            pop.className = 'wa-popover';
            document.body.appendChild(pop);
            var rr = anchorBtn ? anchorBtn.getBoundingClientRect() : null;
            if (!rr || (rr.left === 0 && rr.top === 0 && rr.width === 0)) {
                // 锚点不可见（如侧栏收起态）→ 视口上方居中
                pop.style.top = '70px';
                pop.style.left = 'calc(50vw - 160px)';
            } else {
                pop.style.top = (rr.bottom + 6) + 'px';
                pop.style.left = Math.max(8, Math.min(rr.left, window.innerWidth - 320)) + 'px';
            }
            pop.innerHTML = '<div class="wa-pop-title">' + t('misc.wa.select') + '</div><div class="wa-pop-list" id="wa-pop-list"><div class="wa-item wa-loading">' + t('loading') + '</div></div>';
            document.addEventListener('click', _waPickerOutside, true);
            _waFetch('/api/user/work_areas').then(function (d) {
                var areas = (d && d.areas) || [];
                state.workAreas = areas;
                var list = pop.querySelector('#wa-pop-list');
                if (!list) return;
                list.innerHTML = '';
                areas.forEach(function (a) {
                    var item = document.createElement('div');
                    item.className = 'wa-item';
                    item.innerHTML = '<span class="wa-item-icon">📁</span><span class="wa-item-name"></span><span class="wa-item-path"></span>';
                    item.querySelector('.wa-item-name').textContent = a.name;
                    item.querySelector('.wa-item-path').textContent = a.project_dir;
                    item.title = a.project_dir + '\n' + t('misc.wa.output') + a.output_dir;
                    item.addEventListener('click', function () { closeWaPicker(); createNewConversation(a.id); });
                    list.appendChild(item);
                });
                var addBtn = document.createElement('div');
                addBtn.className = 'wa-item wa-add';
                addBtn.innerHTML = '<span class="wa-item-icon">＋</span><span class="wa-item-name">' + t('misc.wa.new') + '</span>';
                addBtn.addEventListener('click', function () { _waShowCreateForm(pop); });
                list.appendChild(addBtn);
            });
        }
        function _waShowCreateForm(pop) {
            var list = pop.querySelector('#wa-pop-list');
            list.innerHTML = '';
            var form = document.createElement('div');
            form.className = 'wa-form';
            form.innerHTML =
                '<div class="wa-form-title">' + t('misc.wa.newTitle') + '</div>' +
                '<label>' + t('misc.wa.name') + '</label><input type="text" id="wa-f-name" placeholder="如：TruthCross 数据挖掘">' +
                '<label>' + t('misc.wa.proj') + '</label><div class="wa-f-row"><input type="text" id="wa-f-proj" placeholder="D:\…"><button type="button" class="wa-f-browse" data-t="proj">' + t('misc.browse') + '</button></div>' +
                '<label>' + t('misc.wa.out') + '</label><div class="wa-f-row"><input type="text" id="wa-f-out" placeholder="默认 项目文件夹\\output"><button type="button" class="wa-f-browse" data-t="out">' + t('misc.browse') + '</button></div>' +
                '<div class="wa-f-actions"><button type="button" id="wa-f-ok">' + t('misc.wa.createStart') + '</button><button type="button" id="wa-f-cancel">' + t('btn.cancel') + '</button></div>' +
                '<div class="wa-f-err" id="wa-f-err"></div>';
            list.appendChild(form);
            form.querySelector('#wa-f-name').focus();
            form.querySelectorAll('.wa-f-browse').forEach(function (b) {
                b.addEventListener('click', function () {
                    _waFetch('/api/browse_dir').then(function (r) {
                        if (!r || r.cancelled || !r.path) return;
                        if (b.dataset.t === 'proj') {
                            form.querySelector('#wa-f-proj').value = r.path;
                            var out = form.querySelector('#wa-f-out');
                            if (!out.value) out.value = r.path + '\\output';
                        } else {
                            form.querySelector('#wa-f-out').value = r.path;
                        }
                    });
                });
            });
            form.querySelector('#wa-f-cancel').addEventListener('click', function () { closeWaPicker(); });
            form.querySelector('#wa-f-ok').addEventListener('click', function () {
                var name = form.querySelector('#wa-f-name').value.trim();
                var proj = form.querySelector('#wa-f-proj').value.trim();
                var out = form.querySelector('#wa-f-out').value.trim();
                var err = form.querySelector('#wa-f-err');
                if (!proj) { err.textContent = t('misc.wa.needProj'); return; }
                err.textContent = t('misc.creating');
                _waFetch('/api/user/work_areas', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: name, project_dir: proj, output_dir: out })
                }).then(function (r) {
                    if (!r || r.status !== 'ok' || !r.area) { err.textContent = (r && r.error) || t('misc.createFailed'); return; }
                    closeWaPicker();
                    createNewConversation(r.area.id);
                });
            });
        }

        // ==================== 新建对话功能 ====================
        function createNewConversation(projectId) {
            // ★ 2026-09-30 用户定：取消「临时对话 / 空草稿 / 默认占位」三种概念 ——
            //   新建对话 = 直接创建一条 **永久** 对话；空对话也保留在侧栏，永不自动删除。
            //   （只有小e 的对话是临时的：内存态、走 /api/xiaoe/*、不落盘，不在此列。）
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
            var convName = convNumber === 1 ? t('misc.conv.new') : t('misc.conv.new') + convNumber;

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
                project_id: projectId || 'default',
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
            showActionFeedback(t('msg.conv.created'), 'success', feedbackTarget);
        }

        // ── saveTempConversation 已于 2026-09-30 删除 ─────────────────────────────
        // 旧职能有两件：
        //   ① 把「临时对话」转成正式编号的永久对话（branch-N）并改名；
        //   ② ★ 若该对话「没有内容」，就 delete 掉它 —— 这是全项目**唯一**会自动删掉
        //      空对话的代码路径。
        // 用户 2026-09-30 定「不要临时对话/草稿/占位概念，空对话永不自动删除」，它彻底
        // 失去存在意义，故删掉整段，防止「悄悄删掉空对话」再次被走到。
        // 原两处调用点均已改写为直接创建/填充永久对话：
        //   · api.js   createNewConversation()      —— 建的就是永久对话，无需先保存临时对话
        //   · messages.js branchFromMessage()       —— 直接给新对话填消息并命名，不再转正

        // ==================== 统一API调用函数 ====================

        // ==================== 统一的消息发送函数 ====================

        // 生成中的按钮 = 「⚡催促回答」（原「立即回答」，2026-10-13 随按钮改名；用户 2026-09-27 定）：
        // 不中断连接、不丢已探索的内容，只给后端一个"马上收尾出答案"的信号，然后照常输出。
        async function requestImmediateAnswer() {
            if (!state.isGenerating) return;
            try {
                const r = await fetch('/api/agent/nudge', { method: 'POST' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                showToast(t('msg.urged'), 'success');
            } catch (e) {
                showToast(t('msg.reqFailed') + (e && e.message ? e.message : t('msg.netErr')), 'error');
            }
        }

        // 终止本轮生成（2026-10-21：发送位的 ⏹ 调这里；账本已落的轮次保留，可继续追问）
        async function stopCurrentRun() {
            if (!state.isGenerating) return;
            try {
                const r = await fetch('/api/agent/stop', { method: 'POST' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                showToast(t('gr.abortedToast'), 'success');
            } catch (e) {
                showToast('❌ ' + t('gr.abortFailed') + (e && e.message ? e.message : t('gr.networkError')), 'error');
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
                addSystemMessageWithDelete(t('msg.continueNoQ'));
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
