        const modelCards = document.querySelectorAll('.model-card');
        const apiKeyInput = document.getElementById('api-key-input');
        const saveKeyBtn = document.getElementById('save-key-btn');
        const keyStatus = document.getElementById('key-status');
        const currentModelName = null; // Top model display removed
        const messagesContainer = document.getElementById('messages-container');
        const messageInput = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        // Fix: use data-disabled instead of the disabled attribute so browsers still deliver clicks
        sendBtn.removeAttribute('disabled');
        function setSendBtnDisabled(val) { sendBtn.classList.toggle('btn-disabled', val); }
        // Single source of truth for the send button state (user decision 2026-09-27):
        //   - Generating = "⚡ Nudge answer" (renamed 2026-10-13), must stay clickable. The old code set btn-disabled,
        //     and the click handler began with `if (btn-disabled) return`, so Stop/Nudge never fired.
        //   - Idle = clickable only when there is text and a model is configured.
        function updateSendButtonState() {
            if (state.isGenerating) { setSendBtnDisabled(false); return; }
            const hasText = messageInput.value.trim().length > 0;
            const hasKey = !!(state.currentModel && state.apiKeys[state.currentModel]);
            setSendBtnDisabled(!hasText || !hasKey);
        }
        // Auto-scroll during streaming (user decision 2026-09-27): pin to the bottom only when the user has not scrolled up,
        //   otherwise reading earlier reasoning would be yanked to the newest message.
        function scrollToBottomIfFollowing() {
            if (state.userHasScrolledUp) return;
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        const typingIndicator = document.getElementById('typing-indicator');
        const startExperienceBtn = document.getElementById('start-experience');
        const totalTokensEl = document.getElementById('total-tokens');

        // Chat-area model selector
        const chatModelSelector = document.getElementById('chat-model-selector');

        // Sidebar elements
        const chatSidebar = document.getElementById('chat-sidebar');
        // const historyCount = document.getElementById('history-count'); // Removed from DOM
        const historyList = document.getElementById('history-list');
        const historySearch = document.getElementById('history-search');
        const filterButtons = document.querySelectorAll('.filter-btn');
        const globalHistoryMode = document.getElementById('global-history-mode');
        const newChatBtn = document.getElementById('new-chat-btn');
        const newChatBtnEmpty = document.getElementById('new-chat-btn-empty');

        // Notes panel elements
        const notesToggleBtn = document.getElementById('notes-toggle-btn');
        const notesPanel = document.getElementById('notes-panel');
        const notesList = document.getElementById('notes-list');
        const notesEmpty = document.getElementById('notes-empty');
        const notesAddBtn = document.getElementById('notes-add-btn');

        // Branch rename elements
        const renameBranchModal = document.getElementById('rename-branch-modal');
        const renameBranchNameInput = document.getElementById('rename-branch-name-input');
        const cancelRenameBtn = document.getElementById('cancel-rename-btn');
        const confirmRenameBtn = document.getElementById('confirm-rename-btn');

        // File attachment elements
          const attachmentBtn = document.getElementById('attachment-btn');
          const expandBtn = document.getElementById('expand-btn');
          const expandedTextarea = document.getElementById('expanded-textarea');
          const expandedMessageArea = document.getElementById('expanded-message-area');
          const fileAttachmentInput = document.getElementById('file-attachment-input');
          const fileUploadPanel = document.getElementById('file-upload-panel');
          const closeUploadPanel = document.getElementById('close-upload-panel');
          const uploadDropZone = document.getElementById('upload-drop-zone');
        const selectedFilesList = document.getElementById('selected-files-list');

        // Database elements
        const dbAnalysisBtn = document.getElementById('db-analysis-btn');
        const manageDatabaseBtn = null; // Top database-manage button removed
        const databaseModal = document.getElementById('database-modal');
        const dbFileUploadArea = document.getElementById('db-file-upload-area');
        const databaseFileInput = document.getElementById('database-file-input');
        const dbFileInfo = document.getElementById('db-file-info');
        const dbFileName = document.getElementById('db-file-name');
        const clearDbFileBtn = document.getElementById('clear-db-file-btn');
        const databasePreview = document.getElementById('database-preview');
        const cancelDatabaseBtn = document.getElementById('cancel-database-btn');
        const loadDatabaseBtn = document.getElementById('load-database-btn');
        const databaseStatus = document.getElementById('database-status');
        const dbSentences = document.getElementById('db-sentences');
        const dbChars = document.getElementById('db-chars');
        const dbUpdated = document.getElementById('db-updated');

        // Agent configuration merged into the settings panel (see agent_config.js / ui.js)
        const neo4jStatusDot = document.getElementById('neo4j-status-dot');
        // MinerU configuration panel elements
        const panelMineruToken = document.getElementById('panel-mineru-token');
        const panelMineruSave = document.getElementById('panel-mineru-save');
        const panelMineruMsg = document.getElementById('panel-mineru-msg');
         const skillsConfigContainer = document.getElementById('skills-config-container');
         const connectNeo4jBtn = document.getElementById('panel-neo4j-connect');
         // Neo4j settings elements
        const neo4jUri = document.getElementById('neo4j-uri');
        const neo4jUser = document.getElementById('neo4j-user');
        const neo4jPassword = document.getElementById('neo4j-password');
        const neo4jTestBtn = document.getElementById('neo4j-test-btn');
        const neo4jTestResult = document.getElementById('neo4j-test-result');
        const neo4jSettingsStatus = document.getElementById('neo4j-settings-status');

        // In-panel Neo4j connection elements
        const panelNeo4jUri = document.getElementById('panel-neo4j-uri');
        const panelNeo4jUser = document.getElementById('panel-neo4j-user');
        const panelNeo4jPass = document.getElementById('panel-neo4j-pass');
        const panelNeo4jConnect = document.getElementById('panel-neo4j-connect');
        const panelNeo4jMsg = document.getElementById('panel-neo4j-msg');

        // Agent configuration elements
        const settingsBtn = document.getElementById('settings-btn');
        const settingsPanel = document.getElementById('settings-panel');
        const settingsOverlay = document.getElementById('settings-overlay');
        const closeSettingsBtn = document.getElementById('close-settings');
        const saveSettingsBtn = document.getElementById('save-settings');

        // Settings input elements
        const temperatureSlider = document.getElementById('temperature');
        const contextSlider = document.getElementById('context-length');
        const maxTokensInput = document.getElementById('max-tokens');
        const expectedContextTokensInput = document.getElementById('expected-context-tokens');
        const contextWindowInput = document.getElementById('context-window');
        const contextBudgetInput = document.getElementById('context-budget');
        const streamingToggle = document.getElementById('streaming-toggle');

        // Setting value display elements
        const tempValue = document.getElementById('temp-value');
        const contextValue = document.getElementById('context-value');
        const maxTokensValue = document.getElementById('max-tokens-value');
        const streamingStatus = document.getElementById('streaming-status');

        // Default system prompt (unused; keep the null reference so nothing breaks)
        const defaultSystemPrompt = '';

        // ==================== Model configuration ====================

// -- Custom context menu: copy / select all (2026-10-14) --
// WebView2 does not show its native context menu in this environment, so draw a small menu
// on the page contextmenu event; behavior is then identical in browsers and the desktop shell.
(function () {
    var menu = null;
    function hide() { if (menu) { menu.remove(); menu = null; } }
    function copySel() {
        var txt = String(window.getSelection() || '');
        if (!txt) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(txt).catch(function () { legacyCopy(txt); });
        } else { legacyCopy(txt); }
    }
    function legacyCopy(txt) {
        var ta = document.createElement('textarea');
        ta.value = txt; ta.style.cssText = 'position:fixed;opacity:0;';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); } catch (e) {}
        ta.remove();
    }
    document.addEventListener('contextmenu', function (ev) {
        // Inputs/textareas use the system menu; do not intercept the database panel move menu
        var evT = ev.target;
        if (evT && (evT.tagName === 'INPUT' || evT.tagName === 'TEXTAREA' || evT.isContentEditable)) return;
        if (evT && evT.closest && evT.closest('.db-move-menu')) return;
        ev.preventDefault();
        hide();
        var sel = String(window.getSelection() || '');
        menu = document.createElement('div');
        menu.className = 'ctx-copy-menu';
        var itemCopy = document.createElement('div');
        itemCopy.className = 'ctx-item' + (sel ? '' : ' disabled');
        itemCopy.textContent = t('btn.copy');
        itemCopy.addEventListener('click', function () { copySel(); hide(); });
        var itemAll = document.createElement('div');
        itemAll.className = 'ctx-item';
        itemAll.textContent = t('misc.selectAll');
        itemAll.addEventListener('click', function () {
            var mc = document.getElementById('messages-container');
            if (mc) {
                var r = document.createRange(); r.selectNodeContents(mc);
                var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
            } else { document.execCommand('selectAll'); }
            hide();
        });
        menu.appendChild(itemCopy); menu.appendChild(itemAll);
        document.body.appendChild(menu);
        var x = ev.clientX, y = ev.clientY;
        var r = menu.getBoundingClientRect();
        if (x + r.width > innerWidth - 8) x = innerWidth - r.width - 8;
        if (y + r.height > innerHeight - 8) y = y - r.height;
        menu.style.left = x + 'px'; menu.style.top = y + 'px';
        setTimeout(function () {
            document.addEventListener('click', hide, { once: true });
        }, 0);
    });
})();
