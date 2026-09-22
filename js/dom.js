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

        // 智能体配置相关元素
        const agentConfigToggle = document.getElementById('agent-config-toggle');
        const agentConfigPanel = document.getElementById('agent-config-panel');
        const closeAgentConfig = document.getElementById('close-agent-config');
        const neo4jStatusDot = document.getElementById('neo4j-status-dot');
        const toolGraphSchema = document.getElementById('tool-graph-schema');
        const toolExecuteCypher = document.getElementById('tool-execute-cypher');
        const toolSkillList = document.getElementById('tool-skill-list');
        const toolSkillView = document.getElementById('tool-skill-view');
        const toolWebSearch = document.getElementById('tool-web-search');
        const toolWebExtract = document.getElementById('tool-web-extract');
        const toolTodo = document.getElementById('tool-todo');
        const toolNotes = document.getElementById('tool-notes');
        const toolRunCode = document.getElementById('tool-run-code');
        const toolReadFile = document.getElementById('tool-read-file');
        const toolWriteFile = document.getElementById('tool-write-file');
        const toolListFiles = document.getElementById('tool-list-files');
        const toolSkillWrite = document.getElementById('tool-skill-write');
        const toolSearchHistory = document.getElementById('tool-search-history');
        const toolSearchKnowledge = document.getElementById('tool-shared-search');   // 数据库检索（云端 shared_search）
        const toolSharedUpload = document.getElementById('tool-shared-upload');      // 数据库上传（shared_upload）
        const toolMineru = document.getElementById('tool-mineru');
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
        const streamingToggle = document.getElementById('streaming-toggle');

        // 设置值显示元素
        const tempValue = document.getElementById('temp-value');
        const contextValue = document.getElementById('context-value');
        const maxTokensValue = document.getElementById('max-tokens-value');
        const streamingStatus = document.getElementById('streaming-status');

        // 默认系统提示词（不再使用，保留null引用不报错）
        const defaultSystemPrompt = '';

        // ==================== 模型配置 ====================
