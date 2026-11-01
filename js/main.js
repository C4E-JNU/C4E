        init();

        // Note: initialization that depends on personal settings (loadAgentConfigFromLocal / initAgentConfigPanel /
        // updateNeo4jStatusUI) moved to initAfterSettings() in api.js - those settings must first be
        // hydrated from the backend into the local cache, otherwise they read empty here.
        // Desktop pet "xiaoe" initializes itself inside js/xiaoe.js (loaded after this file)
// The desktop pet initializes itself in js/xiaoe.js (loaded after this file)

        // Expose helpers to the global scope
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

        // Table and Mermaid helpers
        window.setGlobalTheme = setGlobalTheme;
        window.switchTableStyle = switchTableStyle;
        window.copyTableToClipboard = copyTableToClipboard;
        window.exportTableToExcel = exportTableToExcel;
        window.zoomMermaid = zoomMermaid;
        window.toggleMermaidCollapse = toggleMermaidCollapse;
        window.copyMermaidCode = copyMermaidCode;
        window.copyMermaidImageAsPng = copyMermaidImageAsPng;

        // Reasoning chain expand/collapse
        function toggleReasoningChain(messageId) {
            const reasoningEl = document.querySelector(`[data-id="${messageId}"] .reasoning-chain`);
            if (reasoningEl) {
                reasoningEl.classList.toggle('collapsed');
            }
        }
        window.toggleReasoningChain = toggleReasoningChain;

        // Notes
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

        // CSS animations
        const style = document.createElement('style');
        style.textContent = `
            @keyframes highlight {
                0% { background-color: rgba(67, 97, 238, 0.3); }
                100% { background-color: transparent; }
            }
        `;
        document.head.appendChild(style);
