        /** Initialization that depends on personal settings (call after hydrateUserSettings).
          *  WARNING: anything that reads personal settings from localStorage belongs here -
          *  those cached keys are only populated after hydrate restores them from the backend. */
        function initAfterSettings() {
            // Enabled models cannot be read from state (state.js evaluates at parse time,
            // before hydrate runs), so read the cache afterwards.
            state.enabledModels = JSON.parse(localStorage.getItem('ragagent-enabled-models') || '[]');

            initProviderSystem(); // Provider system (replaces loadApiKeys)
            loadSettings();       // Fill state.settings / state.neo4j
            loadBranches();
            updateUI();
            initSidebar();
            // Render the last opened conversation (user report 2026-09-27: on entering the chat view the
            // highlighted conversation was blank and unresponsive - loadBranches() restored only
            // currentBranchId, initSidebar() highlighted the row, but nothing called renderMessages();
            // clicking the current row again hit switchToBranch early-return, so only switching back and forth helped).
            if (typeof renderMessages === 'function') renderMessages();
            initFileAttachments(); // File attachment system
            initDatabase();
            initNotesPanel(); // Initialize the notes panel

            // Update the chat-area model selector
            updateChatModelSelector();

            // These ran synchronously at the top of main.js before hydrate, so settings read empty;
            // moved here (they depend on ragagent-agent-config / state.neo4j).
            loadAgentConfigFromLocal();
            initAgentConfigPanel();
            updateNeo4jStatusUI(state.neo4j.connected);   // state.neo4j filled by loadSettings above

            // Restore the last used model
            const savedModel = localStorage.getItem('ai-current-model');
            if (savedModel && modelConfig[savedModel]) {
                selectModel(savedModel);
            } else if (!state.currentModel) {
                // No saved model or it no longer exists: pick the first available one
                const models = Object.keys(modelConfig);
                if (models.length > 0) selectModel(models[0]);
            }

            // Event push (SSE): notify this page when another device changes conversations/entries.
            // Placed here because it needs the login identity (guests have nothing to sync).
            if (typeof startEvents === 'function') startEvents();
        }

        function init() {
            // Detect the backend platform: hide "open locally" buttons off Windows (Linux/server)
            fetch('/api/platform').then(function (r) { return r.json(); }).then(function (d) {
                window.C4EAI_PLATFORM = d.platform || 'win32';
            }).catch(function () { window.C4EAI_PLATFORM = 'win32'; });

            // Pull personal settings and chat history into the localStorage cache before initializing dependents.
            // WARNING: conversations must be restored before loadBranches() (inside initAfterSettings above),
            // otherwise a new device reads an empty cache and all conversations disappear.
            // (Event listeners do not depend on this and stay outside.)
            Promise.allSettled([hydrateUserSettings(), hydrateUserChats()]).finally(initAfterSettings);

            // Event listeners
            startExperienceBtn.addEventListener('click', () => {
                // Landing-page entry: full screen = chat view (leaving full screen returns to the landing page)
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
                // Check the disabled state manually (the disabled attribute was removed so clicks still fire)
                if (sendBtn.classList.contains('btn-disabled')) return;
                if (state.isGenerating) {
                    // While generating: send position = Stop (hard interrupt; completed turns kept). Nudge sits under "collapse thinking".
                    stopCurrentRun();
                } else {
                    sendMessage();
                }
            });

            // Auto show/hide of the top navigation bar (non-full-screen enhancement)
            let lastScrollTop = 0;
            const header = document.querySelector('header');

            function updateHeaderState(e) {
                // Landing state (body.full-screen-mode): header show/hide handled by CSS, skip the logic
                if (document.body.classList.contains('full-screen-mode')) return;

                const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                const isMouseAtTop = e ? e.clientY <= 70 : false;

                if (isMouseAtTop) {
                    // Feature 1: expand the nav bar when the pointer is at the top
                    header.classList.remove('hidden-header');
                } else if (scrollTop <= 100) {
                    // Near the top of the page: always visible
                    header.classList.remove('hidden-header');
                } else {
                    // Not at the top and pointer away: decide whether to collapse
                    if (e && e.type === 'mousemove') {
                        // Pointer leaves the top trigger zone: auto-collapse
                        header.classList.add('hidden-header');
                    } else if (!e) {
                        // Scroll: down collapses, up expands
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

            // Watch input and update the send button state live
            messageInput.addEventListener('input', updateSendButtonState);

            // Chat-area model selector events
            chatModelSelector.addEventListener('change', (e) => {
                const selectedModel = e.target.value;
                if (selectedModel && selectedModel !== state.currentModel) {
                    selectModel(selectedModel);
                }
            });

            // New conversation button
            newChatBtn.addEventListener('click', function () { openWorkAreaPicker(newChatBtn); });
            newChatBtnEmpty.addEventListener('click', function () { openWorkAreaPicker(newChatBtnEmpty); });
            fetch('/api/user/work_areas').then(function (r) { return r.json(); }).then(function (d) {
                if (d && d.areas) state.workAreas = d.areas;
            }).catch(function () {});

            // Branch rename events
            cancelRenameBtn.addEventListener('click', closeRenameBranchModal);
            confirmRenameBtn.addEventListener('click', renameCurrentBranch);

            // Database events
            dbAnalysisBtn.addEventListener('click', openDatabaseModal);
            // manageDatabaseBtn was removed; use the database upload button in the top bar
            // Agent configuration panel is handled by initAgentConfigPanel() in agent_config.js
            // Full-screen button binding
            document.getElementById('full-screen-toggle').addEventListener('click', toggleFullScreen);

            // Settings events
            settingsBtn.addEventListener('click', openSettings);
            closeSettingsBtn.addEventListener('click', closeSettings);
            saveSettingsBtn.addEventListener('click', saveSettings);
            bindSettingsNav();   // Category switching / overlay close / Esc

            // Settings slider events
            temperatureSlider.addEventListener('input', (e) => {
                tempValue.textContent = e.target.value;
            });

            contextSlider.addEventListener('input', (e) => {
                contextValue.textContent = e.target.value;
                updateTokenDisplay();  // Context turns changed -> refresh the token count sent to the LLM
            });

            maxTokensInput.addEventListener('input', (e) => {
                maxTokensValue.textContent = e.target.value;
            });

            // Context window (hard limit): only affects when cleanup happens
            if (contextWindowInput) contextWindowInput.addEventListener('input', (e) => {
                const v = document.getElementById('context-window-value');
                if (v) v.textContent = e.target.value || '262144';
            });

            // Cleanup trigger line (context budget): 0 = auto
            if (contextBudgetInput) contextBudgetInput.addEventListener('input', (e) => {
                const v = document.getElementById('context-budget-value');
                if (v) v.textContent = (parseInt(e.target.value) > 0) ? e.target.value : t('misc.auto');
            });

            expectedContextTokensInput.addEventListener('input', (e) => {
                const v = document.getElementById('expected-context-tokens-value');
                if (v) v.textContent = e.target.value;
                updateTokenDisplay();  // Threshold changed -> refresh the warning state
            });

                      // File open mode (backend / browser tab / in-app window) - UI only, saved on Save
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
            
                            // Raw output stream toggle - UI only, saved on Save
              const rawDataToggle = document.getElementById('raw-data-toggle');
              if (rawDataToggle) {
                  rawDataToggle.addEventListener('change', () => {
                      document.getElementById('raw-data-status').textContent = rawDataToggle.checked ? t('misc.on') : t('misc.off');
                  });
              }

            // Show initial settings values
            tempValue.textContent = state.settings.temperature;
            contextValue.textContent = state.settings.contextLength;
            maxTokensValue.textContent = state.settings.maxTokens;

            // Render messages of the current branch
            renderMessages();

            // Watch the chat container scroll to detect manual scrolling up
            // Whether the user is reading earlier content (2026-09-27: streaming must not yank them down).
            // The old flag was set only while generating, so a new generation yanked readers again; and nothing read it.
            messagesContainer.addEventListener('scroll', () => {
                const isAtBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < 50;
                state.userHasScrolledUp = !isAtBottom;
            });
        }

        // ==================== Chat-area model selector ====================
        function updateChatModelSelector() {
            chatModelSelector.innerHTML = t('in.model.opt');

            // Show only models that are enabled and have an API key configured
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

            // If no model is configured, add a hint option
            if (!hasAnyOption) {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = t('misc.noModelActive');
                option.disabled = true;
                chatModelSelector.appendChild(option);
            }
        }

        // ==================== Work area (choose/create when starting a conversation) ====================
        // User decision 2026-10-03: not in settings - choose a work area on new conversation; none = default work area.
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
                // Anchor hidden (sidebar collapsed) -> center above the viewport
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
                "<label>" + t('misc.wa.name') + "</label><input type=\"text\" id=\"wa-f-name\" placeholder=\"e.g. TruthCross data mining\">" +
                '<label>' + t('misc.wa.proj') + '</label><div class="wa-f-row"><input type="text" id="wa-f-proj" placeholder="D:\…"><button type="button" class="wa-f-browse" data-t="proj">' + t('misc.browse') + '</button></div>' +
                "<label>" + t('misc.wa.out') + "</label><div class=\"wa-f-row\"><input type=\"text\" id=\"wa-f-out\" placeholder=\"default: project folder\\output\"><button type=\"button\" class=\"wa-f-browse\" data-t=\"out\">" + t('misc.browse') + "</button></div>" +
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

        // ==================== New conversation ====================
        function createNewConversation(projectId) {
            // 2026-09-30: drop the temporary-chat / empty-draft / placeholder concepts.
            // New conversation = a permanent conversation; empty ones stay in the sidebar and are never auto-deleted.
            // (Only the desktop pet chat is temporary: in memory, /api/xiaoe/*, never persisted.)
            // Create a permanent conversation (empty ones are still saved to the sidebar)
            // Compute the smallest free ordinal: scan existing branch names for the lowest gap
            var existingNumbers = [];
            for (var bid in state.branches) {
                var bName = state.branches[bid].name || '';
                var match = bName.match(/^(?:New Conversation|New chat|新建对话)\s*(\d*)$/);
                if (match) {
                    existingNumbers.push(match[1] === '' ? 1 : parseInt(match[1], 10));
                }
            }
            var convNumber = 1;
            while (existingNumbers.indexOf(convNumber) !== -1) {
                convNumber++;
            }
            var convName = convNumber === 1 ? t('misc.conv.new') : t('misc.conv.new') + ' ' + convNumber;

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
            // If the notes panel is open, switch it in sync
            syncNotesPanel();

            // Clear and focus the input
            messageInput.value = '';
            setSendBtnDisabled(true);
            messageInput.focus();

            // Show a hint message
            const feedbackTarget = document.activeElement === newChatBtnEmpty ? newChatBtnEmpty : newChatBtn;
            showActionFeedback(t('msg.conv.created'), 'success', feedbackTarget);
        }

        // -- saveTempConversation was removed on 2026-09-30 --
        // It used to do two things:
        //   1) promote a temporary conversation to a permanent branch-N and rename it;
        //   2) delete a conversation with no content - the only code path that auto-deleted
        //      empty conversations.
        // The 2026-09-30 decision removed temporary/draft/placeholder concepts, so it lost
        // its purpose; the whole block was deleted to prevent silent loss of empty chats.
        // Both call sites now create/fill permanent conversations directly:
        //   - api.js createNewConversation()  creates a permanent conversation directly
        //   - messages.js branchFromMessage() fills and names the new conversation directly

        // ==================== Unified API call ====================

        // ==================== Unified message send ====================

        // Button while generating = "Nudge answer" (2026-10-13; user decision 2026-09-27):
        // does not interrupt or lose explored content, only signals the backend to wrap up and answer.
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

        // Stop this generation (2026-10-21: the send-position Stop calls this; recorded turns are kept)
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

            // Collect the context up to this message
            const messageIndex = currentBranch.messages.findIndex(m => m.id === messageId);
            const contextMessages = currentBranch.messages.slice(0, messageIndex);

            // Use the last user message as the question
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

            // Collect attachment file_ids of user messages up to and including this one (re-inject PDFs on continuation)
            var continueFileIds = [];
            for (var ci = 0; ci < currentBranch.messages.length; ci++) {
                var cm = currentBranch.messages[ci];
                if (cm.attachments && cm.attachments.length > 0) {
                    cm.attachments.forEach(function(a) {
                        if (a.file_id && continueFileIds.indexOf(a.file_id) === -1) continueFileIds.push(a.file_id);
                    });
                }
                if (ci === messageIndex) break; // only up to the message being continued
            }

            // Minimal instruction; continuation goes through the backend agent (which re-injects file context)
            const minimalistTrigger = "Continue the previous answer. Start exactly where the last character ended; no preamble, no transition words, seamless continuation.";

            // Call the agent (langchain agent if the config engine is langchain)
            // skipLastHistory=false keeps the full history (including the first agent reply)
            // mergeInto: merge the continuation into the target message instead of creating a new bubble
            await reactAgentLoop(minimalistTrigger, { fileIds: continueFileIds, skipLastHistory: false, mergeInto: messageId });
        }

        // ==================== API error handling ====================
