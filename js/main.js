        init();

        // 初始化智能体配置面板
        loadAgentConfigFromLocal();
        initAgentConfigPanel();
        // 更新 Neo4j 状态指示灯
        updateNeo4jStatusUI(state.neo4j.connected);
        // 注：桌宠小e 的初始化在 js/xiaoe.js 内部自行完成（它在本文件之后加载）

        // 暴露函数给全局作用域
        window.editMessage = editMessage;
        window.saveMessageEdit = saveMessageEdit;
        window.cancelMessageEdit = cancelMessageEdit;
        window.regenerateMessage = regenerateMessage;
        window.branchFromMessage = branchFromMessage;
        window.deleteMessage = deleteMessage;
        window.toggleThinking = toggleThinking;
        window.copyMessage = copyMessage;
        window.copyRawMath = copyRawMath;
        window.scrollToMessage = scrollToMessage;
        window.toggleGroup = toggleGroup;
        window.openRenameBranchModal = openRenameBranchModal;
        window.deleteBranchWithConfirm = deleteBranchWithConfirm;
        window.exportConversation = exportConversation;

        // 新增：表格与Mermaid工具函数
        window.setGlobalTheme = setGlobalTheme;
        window.switchTableStyle = switchTableStyle;
        window.copyTableToClipboard = copyTableToClipboard;
        window.exportTableToExcel = exportTableToExcel;
        window.zoomMermaid = zoomMermaid;
        window.toggleMermaidCollapse = toggleMermaidCollapse;
        window.copyMermaidCode = copyMermaidCode;
        window.copyMermaidImageAsPng = copyMermaidImageAsPng;

        // 推理链展开/收起功能
        function toggleReasoningChain(messageId) {
            const reasoningEl = document.querySelector(`[data-id="${messageId}"] .reasoning-chain`);
            if (reasoningEl) {
                reasoningEl.classList.toggle('collapsed');
            }
        }
        window.toggleReasoningChain = toggleReasoningChain;

        // 笔记功能
        window.toggleNotesPanel = toggleNotesPanel;
        window.addNoteCard = addNoteCard;
        window.deleteNoteCard = deleteNoteCard;
        window.updateNoteTitle = updateNoteTitle;
        window.editNoteCard = editNoteCard;
        window.publishNoteToDb = publishNoteToDb;
        window.saveNoteEdit = saveNoteEdit;
        window.cancelNoteEdit = cancelNoteEdit;
        window.toggleNoteCollapse = toggleNoteCollapse;
        window.handleBranchClick = handleBranchClick;
        window.switchToBranch = switchToBranch;
        window.syncNotesPanel = syncNotesPanel;
        window.toggleSidebarHeader = toggleSidebarHeader;

        // 添加CSS动画
        const style = document.createElement('style');
        style.textContent = `
            @keyframes highlight {
                0% { background-color: rgba(67, 97, 238, 0.3); }
                100% { background-color: transparent; }
            }
        `;
        document.head.appendChild(style);
