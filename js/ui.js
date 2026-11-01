        function renderMessages() {
            // Mobile top-bar title (option A: hamburger + current conversation title + ...)
            if (window.__topbarSyncTitle) window.__topbarSyncTitle();
            const currentBranch = state.currentBranchId ? state.branches[state.currentBranchId] : null;

            // Scroll anchor: a full re-render resets container scrollTop (jumping back to the first message).
            // Record the topmost visible message and its offset, restore after render; stay pinned when following the bottom.
            var _anchorId = '', _anchorOff = 0;
            (function () {
                var top = messagesContainer.scrollTop;
                var els = messagesContainer.querySelectorAll('[data-id]');
                for (var i = 0; i < els.length; i++) {
                    if (els[i].offsetTop + els[i].offsetHeight > top) {
                        _anchorId = els[i].dataset.id;
                        _anchorOff = els[i].offsetTop - top;
                        break;
                    }
                }
            })();

            messagesContainer.innerHTML = '';

            updateReasoningRail();   // Hide the floating button while empty, then show it again after rendering

            // No current conversation (null or a missing conversation) -> leave the chat area empty, do not crash
            // (2026-10-03 user request: removed the empty-state guidance card and its buttons)
            if (!currentBranch) {
                return;
            }

            if (currentBranch.messages.length === 0) {
                const welcomeMsg = document.createElement('div');
                welcomeMsg.className = 'message assistant';
                welcomeMsg.dataset.id = 'welcome';
                welcomeMsg.innerHTML = `
                    <div class="message-content">
                        ${t('ui.emptyWelcome')}
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
                            <button class="action-btn document-btn" onclick="copyMessage('${message.id}')">${t('ui.copy')}</button>
                            <button class="action-btn" onclick="deleteMessage('${message.id}')">${t('ui.delete')}</button>
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
                            <button class="action-btn database-btn" onclick="copyMessage('${message.id}')">${t('ui.copy')}</button>
                            <button class="action-btn" onclick="deleteMessage('${message.id}')">${t('ui.delete')}</button>
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
                    const toolName = message.toolName || t('ui.unknownTool');
                    const statusIcon = message.toolSuccess ? '✅' : '❌';
                    const resultPreview = message.toolResult
                        ? message.toolResult.substring(0, 300) + (message.toolResult.length > 300 ? '...' : '')
                        : '';
                    toolEl.innerHTML = `
                        <div class="agent-step-header">🔧 Step ${step}/${total} &middot; ${statusIcon} ${toolName}</div>
                        <div class="tool-input-block">
                            <div class="tool-label">" + t('ui.input') + "</div>
                            <pre class="tool-code">${escHtml(message.toolInput || '')}</pre>
                        </div>
                        <div class="tool-result-block">
                            <div class="tool-label">" + t('ui.result') + "</div>
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
                        // If JSON, pretty-print it
                        if (parsed.data && Array.isArray(parsed.data)) {
                            resultPreview = t('ui.returnedN').replace('{n}', parsed.count || parsed.data.length);
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
                        <div class="agent-step-header">👁️ Step ${step}/${total} · Observation</div>
                        <div class="obs-content">${escHtml(resultPreview)}</div>
                    `;
                    messagesContainer.appendChild(obsEl);
                } else if (message.type === 'read_skill') {
                    // Reading a skill/document
                    const step = message.step || '?';
                    const total = message.totalSteps || '?';
                    const skillEl = document.createElement('div');
                    skillEl.className = 'message read-skill';
                    skillEl.dataset.id = message.id;
                    const toolName = message.toolName || 'skill_view';
                    // Show which skill/file is being read
                    let inputInfo = '';
                    try {
                        const params = JSON.parse(message.toolInput || '{}');
                        const name = params.name || '';
                        const filePath = params.file_path || 'SKILL.md';
                        inputInfo = filePath === 'SKILL.md' ? name : `${name} / ${filePath}`;
                    } catch(e) { inputInfo = message.toolName; }
                    // Result preview (truncated summary)
                    let resultPreview = '';
                    try {
                        const parsed = JSON.parse(message.toolResult || '{}');
                        const content = parsed.content || '';
                        // Take the first 200 characters as the summary
                        const firstLines = content.split('\n').filter(l => l.trim()).slice(0, 3).join('\n');
                        resultPreview = firstLines.substring(0, 200) + (firstLines.length > 200 ? '...' : '');
                    } catch(e) { /* ignore */ }
                    skillEl.innerHTML = `
                        <div class="agent-step-header">📖 Step ${step}/${total} · Reading: ${inputInfo}</div>
                        ${resultPreview ? `<div class="skill-preview">${escHtml(resultPreview)}</div>` : ''}
                    `;
                    messagesContainer.appendChild(skillEl);
                } else {
                    const messageEl = createMessageElement(message);
                    messagesContainer.appendChild(messageEl);
                    // Draw charts once after rendering history messages
                    setTimeout(renderMermaidDiagrams, 100);
                }
            });

            // Render charts in bulk
            setTimeout(renderMermaidDiagrams, 100);

            // If the backend overwrote the view while the user was reading history, restore the position
            if (state.userHasScrolledUp && _anchorId) {
                var _ae = messagesContainer.querySelector('[data-id="' + _anchorId + '"]');
                if (_ae) messagesContainer.scrollTop = _ae.offsetTop - _anchorOff;
            }
            scrollToBottomIfFollowing();
            updateHistoryList();
            updateReasoningRail();   // Update after rendering based on whether a thinking block exists
        }

        // ==================== Settings panel (modal: categories left, content right) ====================
        function _settingsShowPane(paneId) {
            document.querySelectorAll('#settings-panel .settings-pane').forEach(p => {
                p.classList.toggle('active', p.id === paneId);
            });
            const nav = document.getElementById('settings-nav');
            if (nav) nav.querySelectorAll('.settings-nav-item').forEach(b => {
                b.classList.toggle('active', b.dataset.pane === paneId);
            });
        }

        function bindSettingsNav() {
            const nav = document.getElementById('settings-nav');
            if (nav) {
                nav.addEventListener('click', (e) => {
                    const btn = e.target.closest('.settings-nav-item');
                    if (btn && btn.dataset.pane) _settingsShowPane(btn.dataset.pane);
                });
            }
            if (settingsOverlay) settingsOverlay.addEventListener('click', closeSettings);
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && settingsPanel.classList.contains('active')) closeSettings();
            });
        }

        function openSettings() {
            if (settingsPanel.classList.contains('active')) { closeSettings(); return; }
            settingsPanel.classList.add('active');
            if (settingsOverlay) settingsOverlay.classList.add('active');
            const cur = document.querySelector('#settings-nav .settings-nav-item.active') ||
                        document.querySelector('#settings-nav .settings-nav-item');
            if (cur && cur.dataset.pane) _settingsShowPane(cur.dataset.pane);
            // Prefill the agent and connection categories on open (state -> UI)
            if (typeof window.loadAgentConfigPanes === 'function') window.loadAgentConfigPanes();
            settingsBtn.classList.add('settings-clicked');
            setTimeout(() => { settingsBtn.classList.remove('settings-clicked'); }, 450);
        }

        function closeSettings() {
            settingsPanel.classList.remove('active');
            settingsBtn.classList.remove('settings-clicked');
            if (settingsOverlay) settingsOverlay.classList.remove('active');
        }
        // ==================== UI updates ====================
        function updateUI() {
            updateModelStatus();
            updateChatModelSelector(); // Update the chat-area model selector

            const activatedModels = Object.keys(state.apiKeys).filter(
                modelId => state.apiKeys[modelId]
            );

            if (activatedModels.length > 0 && !state.currentModel) {
                selectModel(activatedModels[0]);
            }

            updateTokenDisplay();
        }

        // ==================== File attachment feature (Gemini style) ====================
        function initFileAttachments() {
            // Attachment button: toggle the upload panel
            attachmentBtn.addEventListener('click', () => {
                if (expandedMessageArea.classList.contains('active')) {
                    collapseExpandMode();
                }
                toggleUploadPanel();
            });

            // Expand button: toggle the expanded message area
            expandBtn.addEventListener('click', () => {
                if (expandedMessageArea.classList.contains('active')) {
                    collapseExpandMode();
                } else {
                    if (fileUploadPanel.classList.contains('active')) {
                        hideUploadPanel();
                    }
                    expandedMessageArea.classList.add('active');
                    expandBtn.classList.add('active');
                    // Copy existing content from the main input when expanding
                    expandedTextarea.value = messageInput.value;
                    expandedTextarea.selectionStart = expandedTextarea.selectionEnd = expandedTextarea.value.length;
                    if (typeof updateSendButtonState === 'function') updateSendButtonState();
                    setTimeout(() => expandedTextarea.focus(), 100);
                }
            });

            // Close panel button
            closeUploadPanel.addEventListener('click', () => {
                hideUploadPanel();
            });

            // Drop-zone click: open the file picker
            uploadDropZone.addEventListener('click', () => {
                fileAttachmentInput.click();
            });

            // Drag events
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

            // File input change
            fileAttachmentInput.addEventListener('change', (e) => {
                const files = Array.from(e.target.files);
                handleMultipleFiles(files);
                e.target.value = ''; // Clear so the same file can be selected again
            });

            // Drag anywhere onto the panel
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

            // Support pasting files from the clipboard into the input
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
                    // If files are present, block the default paste behavior (avoid pasting file names)
                    e.preventDefault();
                    handleMultipleFiles(files);
                    showUploadPanel();
                    showToast(t('ui.clipboardAdded').replace('{n}', files.length), 'success');
                }
            });

            // Expanded mode: text sync
            // Main input change -> sync to the expanded box
            messageInput.addEventListener('input', () => {
                // Mirror unconditionally: the expanded box always follows the main input.
                // (Previously synced only while active, so typing then expanding lost content.)
                if (expandedTextarea.value !== messageInput.value) {
                    expandedTextarea.value = messageInput.value;
                }
            });

            // Expanded box change -> sync to the main input
            expandedTextarea.addEventListener('input', () => {
                messageInput.value = expandedTextarea.value;
                updateSendButtonState();
            });

            // Expanded box: Enter sends, Shift+Enter inserts a newline
            expandedTextarea.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });

            // Initialize the global skin
            const savedTheme = localStorage.getItem('ragagent-theme') || 'default';
            setGlobalTheme(savedTheme);
        }
