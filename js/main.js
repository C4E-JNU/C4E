        init();

        // 注：依赖个人设置的初始化（loadAgentConfigFromLocal / initAgentConfigPanel /
        // updateNeo4jStatusUI）已移入 api.js 的 initAfterSettings() ——
        // 那些设置现在要先由 hydrateUserSettings() 从后端灌回本地缓存才有值，
        // 放在这里会在 hydrate 完成前执行、读到空值。
        // 桌宠小e 的初始化在 js/xiaoe.js 内部自行完成（它在本文件之后加载）

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
