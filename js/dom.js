        const modelCards = document.querySelectorAll('.model-card');
        const apiKeyInput = document.getElementById('api-key-input');
        const saveKeyBtn = document.getElementById('save-key-btn');
        const keyStatus = document.getElementById('key-status');
        const currentModelName = null; // 已移除顶部模型显示
        const messagesContainer = document.getElementById('messages-container');
        const messageInput = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        // 修复：用 data-disabled 替代 disabled 属性，避免浏览器阻止点击事件
        sendBtn.removeAttribute('disabled');
        function setSendBtnDisabled(val) { sendBtn.classList.toggle('btn-disabled', val); }
        // 发送按钮的**唯一状态真源**（用户 2026-09-27）：
        //   · 生成中 = 「⚡催促回答」（2026-10-13 由「立即回答」改名），**必须可点**。旧代码发送后把按钮置 btn-disabled，
        //     而点击处理第一行就是 `if (btn-disabled) return` → 停止/立即回答永远点不到（踩过）。
        //   · 空闲 = 有文字 + 模型已配置 才可点。
        function updateSendButtonState() {
            if (state.isGenerating) { setSendBtnDisabled(false); return; }
            const hasText = messageInput.value.trim().length > 0;
            const hasKey = !!(state.currentModel && state.apiKeys[state.currentModel]);
            setSendBtnDisabled(!hasText || !hasKey);
        }
        // 流式输出的自动滚动（用户 2026-09-27）：只在用户没往上滑时贴底——
        //   否则正在看前面的思考内容会被强制拽到底部看最新消息。
        function scrollToBottomIfFollowing() {
            if (state.userHasScrolledUp) return;
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        const typingIndicator = document.getElementById('typing-indicator');
        const startExperienceBtn = document.getElementById('start-experience');
        const totalTokensEl = document.getElementById('total-tokens');

        // 新增：聊天区模型选择器
        const chatModelSelector = document.getElementById('chat-model-selector');

        // 侧边栏相关元素
        const chatSidebar = document.getElementById('chat-sidebar');
        // const historyCount = document.getElementById('history-count'); // Removed from DOM
        const historyList = document.getElementById('history-list');
        const historySearch = document.getElementById('history-search');
        const filterButtons = document.querySelectorAll('.filter-btn');
        const globalHistoryMode = document.getElementById('global-history-mode');
        const newChatBtn = document.getElementById('new-chat-btn');
        const newChatBtnEmpty = document.getElementById('new-chat-btn-empty');

        // 笔记面板元素
        const notesToggleBtn = document.getElementById('notes-toggle-btn');
        const notesPanel = document.getElementById('notes-panel');
        const notesList = document.getElementById('notes-list');
        const notesEmpty = document.getElementById('notes-empty');
        const notesAddBtn = document.getElementById('notes-add-btn');

        // 分支重命名相关元素
        const renameBranchModal = document.getElementById('rename-branch-modal');
        const renameBranchNameInput = document.getElementById('rename-branch-name-input');
        const cancelRenameBtn = document.getElementById('cancel-rename-btn');
        const confirmRenameBtn = document.getElementById('confirm-rename-btn');

        // 新的文件附件相关元素
          const attachmentBtn = document.getElementById('attachment-btn');
          const expandBtn = document.getElementById('expand-btn');
          const expandedTextarea = document.getElementById('expanded-textarea');
          const expandedMessageArea = document.getElementById('expanded-message-area');
          const fileAttachmentInput = document.getElementById('file-attachment-input');
          const fileUploadPanel = document.getElementById('file-upload-panel');
          const closeUploadPanel = document.getElementById('close-upload-panel');
          const uploadDropZone = document.getElementById('upload-drop-zone');
        const selectedFilesList = document.getElementById('selected-files-list');

        // 数据库相关元素
        const dbAnalysisBtn = document.getElementById('db-analysis-btn');
        const manageDatabaseBtn = null; // 已移除顶部数据库管理按钮
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

        // 智能体配置已并入设置面板（见 agent_config.js / ui.js），不再有独立面板元素
        const neo4jStatusDot = document.getElementById('neo4j-status-dot');
        // MinerU 配置面板元素
        const panelMineruToken = document.getElementById('panel-mineru-token');
        const panelMineruSave = document.getElementById('panel-mineru-save');
        const panelMineruMsg = document.getElementById('panel-mineru-msg');
         const skillsConfigContainer = document.getElementById('skills-config-container');
         const connectNeo4jBtn = document.getElementById('panel-neo4j-connect');
         // Neo4j 设置元素
        const neo4jUri = document.getElementById('neo4j-uri');
        const neo4jUser = document.getElementById('neo4j-user');
        const neo4jPassword = document.getElementById('neo4j-password');
        const neo4jTestBtn = document.getElementById('neo4j-test-btn');
        const neo4jTestResult = document.getElementById('neo4j-test-result');
        const neo4jSettingsStatus = document.getElementById('neo4j-settings-status');

        // 面板内 Neo4j 连接元素
        const panelNeo4jUri = document.getElementById('panel-neo4j-uri');
        const panelNeo4jUser = document.getElementById('panel-neo4j-user');
        const panelNeo4jPass = document.getElementById('panel-neo4j-pass');
        const panelNeo4jConnect = document.getElementById('panel-neo4j-connect');
        const panelNeo4jMsg = document.getElementById('panel-neo4j-msg');

        // 智能体配置相关元素
        const settingsBtn = document.getElementById('settings-btn');
        const settingsPanel = document.getElementById('settings-panel');
        const settingsOverlay = document.getElementById('settings-overlay');
        const closeSettingsBtn = document.getElementById('close-settings');
        const saveSettingsBtn = document.getElementById('save-settings');

        // 设置输入元素
        const temperatureSlider = document.getElementById('temperature');
        const contextSlider = document.getElementById('context-length');
        const maxTokensInput = document.getElementById('max-tokens');
        const expectedContextTokensInput = document.getElementById('expected-context-tokens');
        const contextWindowInput = document.getElementById('context-window');
        const contextBudgetInput = document.getElementById('context-budget');
        const streamingToggle = document.getElementById('streaming-toggle');

        // 设置值显示元素
        const tempValue = document.getElementById('temp-value');
        const contextValue = document.getElementById('context-value');
        const maxTokensValue = document.getElementById('max-tokens-value');
        const streamingStatus = document.getElementById('streaming-status');

        // 默认系统提示词（不再使用，保留null引用不报错）
        const defaultSystemPrompt = '';

        // ==================== 模型配置 ====================

// ── 自定义右键菜单：复制 / 全选（2026-10-14）──
// 背景：WebView2 原生上下文菜单在该环境里不弹出（设置已开、探针管道却能弹，原因未明），
// 改为页面层 contextmenu 事件自绘小菜单，浏览器/桌面端行为一致。
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
        // 输入框/textarea 走系统自带菜单（WebView2 对编辑控件有内置菜单）；且别拦数据库面板的移动菜单
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
