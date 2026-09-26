        function renderMessages() {
            const currentBranch = state.currentBranchId ? state.branches[state.currentBranchId] : null;

            messagesContainer.innerHTML = '';

            // 无当前对话（null 或指向不存在的对话）→ 显示空态引导，不崩
            if (!currentBranch) {
                const emptyMsg = document.createElement('div');
                emptyMsg.className = 'message assistant';
                emptyMsg.dataset.id = 'empty';
                emptyMsg.innerHTML = `
                    <div class="message-content">
                        <div class="empty-state-icon">💬</div>
                        <div style="text-align:center;font-size:1.1rem;">还没有对话</div>
                        <div style="text-align:center;color:var(--gray);margin-top:0.4rem;">点击「新建对话」开始新的聊天</div>
                    </div>
                `;
                messagesContainer.appendChild(emptyMsg);
                return;
            }

            if (currentBranch.messages.length === 0) {
                const welcomeMsg = document.createElement('div');
                welcomeMsg.className = 'message assistant';
                welcomeMsg.dataset.id = 'welcome';
                welcomeMsg.innerHTML = `
                    <div class="message-content">
                        您好！我是RAGAgent，基于多模型的智能助手。<br>
                        请开始新的对话。
                    </div>
                `;
                messagesContainer.appendChild(welcomeMsg);
                return;
            }

            currentBranch.messages.forEach(message => {
                if (message.type === 'document') {
                    const docMsg = document.createElement('div');
                    docMsg.className = 'message document';
                    docMsg.dataset.id = message.id;

                    docMsg.innerHTML = `
                        <div class="message-content">
                            ${formatMessage(message.content)}
                        </div>
                        <div class="message-actions">
                            <button class="action-btn document-btn" onclick="copyMessage('${message.id}')">复制</button>
                            <button class="action-btn" onclick="deleteMessage('${message.id}')">删除</button>
                        </div>
                    `;

                    messagesContainer.appendChild(docMsg);
                } else if (message.type === 'database') {
                    const dbMsg = document.createElement('div');
                    dbMsg.className = 'message database';
                    dbMsg.dataset.id = message.id;

                    dbMsg.innerHTML = `
                        <div class="message-content">
                            ${formatMessage(message.content)}
                        </div>
                        <div class="message-actions">
                            <button class="action-btn database-btn" onclick="copyMessage('${message.id}')">复制</button>
                            <button class="action-btn" onclick="deleteMessage('${message.id}')">删除</button>
                        </div>
                    `;

                    messagesContainer.appendChild(dbMsg);
                } else if (message.type === 'agent_thought') {
                    const step = message.step || '?';
                    const total = message.totalSteps || '?';
                    const thoughtEl = document.createElement('div');
                    thoughtEl.className = 'message agent-thought';
                    thoughtEl.dataset.id = message.id;
                    thoughtEl.innerHTML = `
                        <div class="agent-step-header">🧠 Step ${step}/${total}</div>
                        <div class="message-content agent-thought-content">${escHtml(message.content)}</div>
                    `;
                    messagesContainer.appendChild(thoughtEl);
                } else if (message.type === 'tool_call') {
                    const step = message.step || '?';
                    const total = message.totalSteps || '?';
                    const toolEl = document.createElement('div');
                    toolEl.className = 'message tool-call';
                    toolEl.dataset.id = message.id;
                    const toolName = message.toolName || '未知工具';
                    const statusIcon = message.toolSuccess ? '✅' : '❌';
                    const resultPreview = message.toolResult
                        ? message.toolResult.substring(0, 300) + (message.toolResult.length > 300 ? '...' : '')
                        : '';
                    toolEl.innerHTML = `
                        <div class="agent-step-header">🔧 Step ${step}/${total} &middot; ${statusIcon} ${toolName}</div>
                        <div class="tool-input-block">
                            <div class="tool-label">输入:</div>
                            <pre class="tool-code">${escHtml(message.toolInput || '')}</pre>
                        </div>
                        <div class="tool-result-block">
                            <div class="tool-label">结果:</div>
                            <pre class="tool-result">${escHtml(resultPreview)}</pre>
                        </div>
                    `;
                    messagesContainer.appendChild(toolEl);
                } else if (message.type === 'agent_final') {
                    const messageEl = createMessageElement(message);
                    messagesContainer.appendChild(messageEl);
                    setTimeout(renderMermaidDiagrams, 100);
                } else if (message.type === 'agent_observation') {
                    const step = message.step || '?';
                    const total = message.totalSteps || '?';
                    const obsEl = document.createElement('div');
                    obsEl.className = 'message agent-observation';
                    obsEl.dataset.id = message.id;
                    let resultPreview = '';
                    try {
                        const raw = typeof message.toolResult === 'string' ? message.toolResult : JSON.stringify(message.toolResult);
                        const parsed = JSON.parse(raw);
                        // 如果是 JSON，格式化显示
                        if (parsed.data && Array.isArray(parsed.data)) {
                            resultPreview = `返回 ${parsed.count || parsed.data.length} 条数据`;
                        } else if (parsed.content) {
                            resultPreview = parsed.content.substring(0, 150) + (parsed.content.length > 150 ? '...' : '');
                        } else if (parsed.error) {
                            resultPreview = '❌ ' + parsed.error;
                        } else {
                            resultPreview = raw.substring(0, 200);
                        }
                    } catch(e) {
                        resultPreview = String(message.toolResult || '').substring(0, 200);
                    }
                    obsEl.innerHTML = `
                        <div class="agent-step-header">👁️ Step ${step}/${total} · 观察结果</div>
                        <div class="obs-content">${escHtml(resultPreview)}</div>
                    `;
                    messagesContainer.appendChild(obsEl);
                } else if (message.type === 'read_skill') {
                    // 阅读技能/文档
                    const step = message.step || '?';
                    const total = message.totalSteps || '?';
                    const skillEl = document.createElement('div');
                    skillEl.className = 'message read-skill';
                    skillEl.dataset.id = message.id;
                    const toolName = message.toolName || 'skill_view';
                    // 显示读的是什么技能/文件
                    let inputInfo = '';
                    try {
                        const params = JSON.parse(message.toolInput || '{}');
                        const name = params.name || '';
                        const filePath = params.file_path || 'SKILL.md';
                        inputInfo = filePath === 'SKILL.md' ? name : `${name} / ${filePath}`;
                    } catch(e) { inputInfo = message.toolName; }
                    // 结果预览（截取摘要）
                    let resultPreview = '';
                    try {
                        const parsed = JSON.parse(message.toolResult || '{}');
                        const content = parsed.content || '';
                        // 取前 200 字作为摘要
                        const firstLines = content.split('\n').filter(l => l.trim()).slice(0, 3).join('\n');
                        resultPreview = firstLines.substring(0, 200) + (firstLines.length > 200 ? '...' : '');
                    } catch(e) { /* ignore */ }
                    skillEl.innerHTML = `
                        <div class="agent-step-header">📖 Step ${step}/${total} · 阅读: ${inputInfo}</div>
                        ${resultPreview ? `<div class="skill-preview">${escHtml(resultPreview)}</div>` : ''}
                    `;
                    messagesContainer.appendChild(skillEl);
                } else {
                    const messageEl = createMessageElement(message);
                    messagesContainer.appendChild(messageEl);
                    // 历史消息渲染后触发一次绘图
                    setTimeout(renderMermaidDiagrams, 100);
                }
            });

            // 批量渲染图表
            setTimeout(renderMermaidDiagrams, 100);

            messagesContainer.scrollTop = messagesContainer.scrollHeight;
            updateHistoryList();
        }

        // ==================== 设置面板 ====================
        function openSettings() {
            // 二次点击收起（与智能体配置的 toggle 行为一致）
            if (settingsPanel.classList.contains('active')) {
                closeSettings();
                return;
            }
            // 首次打开时把面板移入输入区（紧跟智能体配置，并列显示）
            if (!settingsPanel.dataset.relocated) {
                const ap = document.getElementById('agent-config-panel');
                if (ap) {
                    ap.insertAdjacentElement('afterend', settingsPanel);
                } else {
                    const inputArea = document.querySelector('.input-area');
                    if (inputArea) inputArea.appendChild(settingsPanel);
                }
                settingsPanel.dataset.relocated = '1';
            }
            settingsPanel.classList.add('active');
            if (settingsOverlay) settingsOverlay.classList.remove('active'); // 内联卡片不需要遮罩
            settingsPanel.classList.remove('opening');
            // 与智能体配置互斥
            const ap2 = document.getElementById('agent-config-panel');
            if (ap2) ap2.classList.remove('active');
            settingsBtn.classList.add('settings-clicked');
            setTimeout(() => {
                settingsBtn.classList.remove('settings-clicked');
            }, 450);
        }

        function closeSettings() {
            settingsPanel.classList.remove('active');
            settingsPanel.classList.remove('opening');
            settingsPanel.classList.remove('saved-flash');
            settingsBtn.classList.remove('settings-clicked');
            if (settingsOverlay) settingsOverlay.classList.remove('active');
        }

        // ==================== UI更新 ====================
        function updateUI() {
            updateModelStatus();
            updateChatModelSelector(); // 更新聊天区模型选择器

            const activatedModels = Object.keys(state.apiKeys).filter(
                modelId => state.apiKeys[modelId]
            );

            if (activatedModels.length > 0 && !state.currentModel) {
                selectModel(activatedModels[0]);
            }

            updateTokenDisplay();
        }

        // ==================== 新文件附件功能 (Gemini风格) ====================
        function initFileAttachments() {
            // 附件按钮点击 - 切换上传面板
            attachmentBtn.addEventListener('click', () => {
                if (expandedMessageArea.classList.contains('active')) {
                    collapseExpandMode();
                }
                toggleUploadPanel();
            });

            // 展开按钮点击 - 切换展开消息区域
            expandBtn.addEventListener('click', () => {
                if (expandedMessageArea.classList.contains('active')) {
                    collapseExpandMode();
                } else {
                    if (fileUploadPanel.classList.contains('active')) {
                        hideUploadPanel();
                    }
                    expandedMessageArea.classList.add('active');
                    expandBtn.classList.add('active');
                    setTimeout(() => expandedTextarea.focus(), 100);
                }
            });

            // 关闭面板按钮
            closeUploadPanel.addEventListener('click', () => {
                hideUploadPanel();
            });

            // 拖拽区域点击 - 打开文件选择
            uploadDropZone.addEventListener('click', () => {
                fileAttachmentInput.click();
            });

            // 拖拽事件
            uploadDropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                uploadDropZone.classList.add('drag-over');
            });

            uploadDropZone.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                uploadDropZone.classList.remove('drag-over');
            });

            uploadDropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                uploadDropZone.classList.remove('drag-over');

                const files = Array.from(e.dataTransfer.files);
                handleMultipleFiles(files);
            });

            // 文件输入变化
            fileAttachmentInput.addEventListener('change', (e) => {
                const files = Array.from(e.target.files);
                handleMultipleFiles(files);
                e.target.value = ''; // 清空以允许重复选择同一文件
            });

            // 全局拖拽到面板
            fileUploadPanel.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                fileUploadPanel.classList.add('drag-over');
            });

            fileUploadPanel.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (e.target === fileUploadPanel) {
                    fileUploadPanel.classList.remove('drag-over');
                }
            });

            fileUploadPanel.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                fileUploadPanel.classList.remove('drag-over');

                const files = Array.from(e.dataTransfer.files);
                handleMultipleFiles(files);
            });

            // 支持从剪贴板粘贴文件到输入框
            messageInput.addEventListener('paste', (e) => {
                const items = (e.clipboardData || e.originalEvent.clipboardData).items;
                const files = [];

                for (let i = 0; i < items.length; i++) {
                    if (items[i].kind === 'file') {
                        const file = items[i].getAsFile();
                        if (file) files.push(file);
                    }
                }

                if (files.length > 0) {
                    // 如果有文件，阻止默认粘贴行为（防止粘贴文件名等）
                    e.preventDefault();
                    handleMultipleFiles(files);
                    showUploadPanel();
                    showToast(`已从剪贴板添加 ${files.length} 个文件`, 'success');
                }
            });

            // 展开模式：文本同步
            // 主输入框变化 → 同步到展开框
            messageInput.addEventListener('input', () => {
                if (expandedMessageArea.classList.contains('active')) {
                    expandedTextarea.value = messageInput.value;
                }
            });

            // 展开框变化 → 同步到主输入框
            expandedTextarea.addEventListener('input', () => {
                messageInput.value = expandedTextarea.value;
                updateSendButtonState();
            });

            // 展开框：Enter发送，Shift+Enter换行
            expandedTextarea.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });

            // 初始化全局皮肤
            const savedTheme = localStorage.getItem('ragagent-theme') || 'default';
            setGlobalTheme(savedTheme);
        }
