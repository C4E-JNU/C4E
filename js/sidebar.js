        // 切换侧边栏状态：收起 ↔ 展开 ↔ 全屏
        function toggleSidebar() {
            var sidebar = document.getElementById('chat-sidebar');
            var section = document.querySelector('.chat-section');
            var btn = document.getElementById('sidebar-toggle-btn');
            if (!sidebar || !section || !btn) return;
            var isCollapsed = sidebar.classList.contains('collapsed');
            var isFull = section.classList.contains('sidebar-full');
            if (isCollapsed) {
                // 收起 → 展开(260px)
                sidebar.classList.remove('collapsed');
                section.classList.remove('sidebar-full');
                btn.classList.remove('active');
            } else if (!isFull) {
                // 展开 → 全屏(sidebar 50% + notes 50%)
                section.classList.add('sidebar-full');
                btn.classList.add('active');
                btn.classList.add('full');
            } else {
                // 全屏 → 收起
                sidebar.classList.add('collapsed');
                section.classList.remove('sidebar-full');
                btn.classList.add('active');
                btn.classList.remove('full');
            }
        }

        // 初始化绑定
        document.getElementById('sidebar-toggle-btn').addEventListener('click', toggleSidebar);

        // 点击 C4E 图标折叠侧边栏头部搜索/过滤区
        function toggleSidebarHeader() {
            var sidebar = document.getElementById('chat-sidebar');
            sidebar.classList.toggle('header-collapsed');
        }


        // ── 知识库搜索 + 向量检索（正则已移除） ──────
        async function sendMessageWithDatabaseSearch(question) {
            showTyping(true);

            try {
                const searchResult = await searchDocuments(question);

                if (!searchResult || !searchResult.context) {
                    addMessage('assistant', `知识库中未找到与「${question}」相关的内容。`, 0);
                    showTyping(false);
                    return;
                }

                // 让智能体处理搜索结果
                const augmentedQuestion = `【知识库检索结果】\n${searchResult.context}\n\n---\n\n用户问题: ${question}\n\n请基于以上知识库内容回答。`;
                await reactAgentLoop(augmentedQuestion);

            } catch (error) {
                addSystemMessageWithDelete(`知识库查询失败: ${error.message}`);
                await reactAgentLoop(question);
            } finally {
                showTyping(false);
            }
        }

        // ==================== 数据库状态存储 ====================
        function openDatabaseStorage() {
            return new Promise((resolve, reject) => {
                if (!window.indexedDB) {
                    reject(new Error('当前浏览器不支持 IndexedDB'));
                    return;
                }
                const request = indexedDB.open('ragagent-database-storage', 1);
                request.onupgradeneeded = () => {
                    const db = request.result;
                    if (!db.objectStoreNames.contains('knowledgeBase')) {
                        db.createObjectStore('knowledgeBase', { keyPath: 'id' });
                    }
                };
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
        }

        async function loadDatabaseState() {
            const savedDatabase = localStorage.getItem('ai-database');
            let savedMeta = null;

            if (savedDatabase) {
                try {
                    savedMeta = JSON.parse(savedDatabase);
                } catch (e) {
                    console.error('解析数据库元数据失败:', e);
                }
            }

            try {
                const db = await openDatabaseStorage();
                const content = await new Promise((resolve, reject) => {
                    const tx = db.transaction('knowledgeBase', 'readonly');
                    const store = tx.objectStore('knowledgeBase');
                    const request = store.get('active');
                    request.onsuccess = () => resolve(request.result?.content || savedMeta?.content || '');
                    request.onerror = () => reject(request.error);
                });
                db.close();

                if (savedMeta?.loaded && content) {
                    const sentences = splitIntoSentences(content);
                    state.database.loaded = true;
                    state.database.content = content;
                    state.database.sentences = sentences;
                    state.database.fileName = savedMeta.fileName || '';
                    state.database.stats = savedMeta.stats || {
                        sentences: sentences.length,
                        characters: content.length,
                        updatedAt: new Date().toISOString()
                    };

                    if (!state.database.stats.sentences) state.database.stats.sentences = sentences.length;
                    if (!state.database.stats.characters) state.database.stats.characters = content.length;

                    updateDatabaseStats();
                }
            } catch (e) {
                console.error('加载数据库状态失败:', e);
                if (savedMeta?.loaded && savedMeta.content) {
                    const sentences = splitIntoSentences(savedMeta.content);
                    state.database.loaded = true;
                    state.database.content = savedMeta.content;
                    state.database.sentences = sentences;
                    state.database.fileName = savedMeta.fileName || '';
                    state.database.stats = savedMeta.stats || {
                        sentences: sentences.length,
                        characters: savedMeta.content.length,
                        updatedAt: new Date().toISOString()
                    };
                    updateDatabaseStats();
                }
            }
        }

        async function saveDatabaseState() {
            const databaseMeta = {
                loaded: state.database.loaded,
                fileName: state.database.fileName || state.database.file?.name || '',
                stats: state.database.stats
            };

            try {
                const db = await openDatabaseStorage();
                await new Promise((resolve, reject) => {
                    const tx = db.transaction('knowledgeBase', 'readwrite');
                    const store = tx.objectStore('knowledgeBase');
                    store.put({
                        id: 'active',
                        content: state.database.content,
                        savedAt: new Date().toISOString()
                    });
                    tx.oncomplete = resolve;
                    tx.onerror = () => reject(tx.error);
                });
                db.close();

                localStorage.setItem('ai-database', JSON.stringify(databaseMeta));
            } catch (e) {
                const fallback = {
                    ...databaseMeta,
                    content: state.database.content
                };
                localStorage.setItem('ai-database', JSON.stringify(fallback));
            }
        }

        // ==================== 侧边栏初始化 ====================
        function initSidebar() {
            historySearch.addEventListener('input', (e) => {
                state.sidebar.searchQuery = e.target.value.toLowerCase();
                updateHistoryList();
            });

            filterButtons.forEach(btn => {
                btn.addEventListener('click', () => {
                    // 跳过笔记按钮，笔记有独立的点击处理
                    if (btn.id === 'notes-toggle-btn') return;
                    filterButtons.forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    state.sidebar.currentFilter = btn.dataset.filter;
                    updateHistoryList();
                });
            });

            updateHistoryList();
        }

        // ==================== 笔记面板 ====================
        // 初始化时绑定笔记事件（在 api.js init 末尾调用）
        function initNotesPanel() {
            var btn = document.getElementById('notes-toggle-btn');
            var addBtn = document.getElementById('notes-add-btn');
            if (btn) btn.addEventListener('click', toggleNotesPanel);
            if (addBtn) addBtn.addEventListener('click', function() {
                addNoteCard(state.currentBranchId, '', '');
            });
        }

        function toggleNotesPanel() {
            var np = document.getElementById('notes-panel');
            var btn = document.getElementById('notes-toggle-btn');
            if (!np || !btn) return;
            // 3 级切换: 0=隐藏 → 1=分屏(420px) → 2=全屏(隐藏聊天) → 0
            if (!np.classList.contains('active')) {
                // 0 → 1：打开笔记，分屏
                np.classList.add('active', 'phase-1');
                btn.classList.add('active');
                btn.textContent = '笔记';
            } else if (np.classList.contains('phase-1')) {
                // 1 → 2：笔记最大化，隐藏聊天
                np.classList.replace('phase-1', 'phase-2');
                btn.textContent = '笔记▶';
            } else {
                // 2 → 0：关闭
                np.classList.remove('active', 'phase-1', 'phase-2');
                btn.classList.remove('active');
                btn.textContent = '笔记';
            }
            renderNotesPanel();
        }

        function renderNotesPanel() {
            var np = document.getElementById('notes-panel');
            if (!np || !np.classList.contains('active')) return;
            var notesListEl = document.getElementById('notes-list');
            var notesEmptyEl = document.getElementById('notes-empty');
            if (!notesListEl || !notesEmptyEl) return;

            // 从后端加载笔记（带当前对话过滤）
            var branchId = state.currentBranchId || '';
            fetch(BACKEND_API_URL + '/api/notes?branch_id=' + encodeURIComponent(branchId))
                .then(r => r.json())
                .then(data => {
                    var notes = data.notes || [];
                    _currentNotes = notes;  // 缓存，供编辑时查找
                    var branch = state.branches[state.currentBranchId];
                    var titleEl = np.querySelector('.notes-panel-title');
                    if (titleEl) titleEl.textContent = branch ? '📝 ' + branch.name : '📝 对话笔记';

                    if (notes.length === 0) {
                        notesListEl.innerHTML = '';
                        notesEmptyEl.style.display = 'block';
                        return;
                    }
                    notesEmptyEl.style.display = 'none';
                    notesListEl.innerHTML = notes.map(function(n) { return renderNoteCard(n); }).join('');
                })
                .catch(function(err) {
                    console.error('加载笔记失败:', err);
                    notesListEl.innerHTML = '<div style="padding:1rem;color:var(--error);">加载笔记失败</div>';
                });
        }

        // 笔记折叠状态（按 noteId 记录，默认闭合；会话内重绘时保留手动展开状态）
        var _noteCollapsedMap = {};
        var _currentNotes = [];  // 最近一次渲染的笔记缓存（供编辑时查找）

        function renderNoteCard(note) {
            var contentHtml = note.content
                ? formatMessage(note.content, true)
                : '<span style="color:var(--gray);font-style:italic;">空笔记</span>';
            var escapedTitle = escHtml(note.title || '未命名笔记');
            // 默认闭合，用 _noteCollapsedMap 记录手动展开/收起状态
            var isCollapsed = _noteCollapsedMap[note.id] !== undefined ? _noteCollapsedMap[note.id] : true;
            return '<div class="note-card" data-note-id="' + note.id + '">'
                + '<div class="note-card-header" onclick="toggleNoteCollapse(\'' + note.id + '\')" style="cursor:pointer;">'
                + '<span class="note-card-title">' + escapedTitle + '</span>'
                + '<div class="note-card-header-actions" onclick="event.stopPropagation();">'
                + '<button class="note-card-move-btn" onclick="moveNote(\'' + note.id + '\', -1)" title="上移">▲</button>'
                + '<button class="note-card-move-btn" onclick="moveNote(\'' + note.id + '\', 1)" title="下移">▼</button>'
                + '<button class="note-card-edit-btn" onclick="editNoteCard(\'' + note.id + '\')" title="编辑笔记">✏️</button>'
                + '<button class="note-card-delete" onclick="deleteNoteCard(\'' + note.id + '\')" title="删除笔记">×</button>'
                + '</div>'
                + '</div>'
                + '<div class="note-card-body" id="note-body-' + note.id + '"'
                + (isCollapsed ? ' style="display:none;"' : '') + '>'
                + '<div class="message-content">' + contentHtml + '</div>'
                + '</div>'
                + '</div>';
        }

        function addNoteCard(branchId, title, content) {
            fetch(BACKEND_API_URL + '/api/notes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: title || '新笔记', content: content || '', branch_id: branchId || '' })
            })
            .then(r => r.json())
            .then(function(data) {
                if (data.status === 'ok') {
                    var np = document.getElementById('notes-panel');
                    if (np && np.classList.contains('active')) renderNotesPanel();
                    showToast('笔记已添加', 'success');
                } else {
                    showToast('添加笔记失败', 'error');
                }
            })
            .catch(function(err) {
                console.error('添加笔记失败:', err);
                showToast('添加笔记失败', 'error');
            });
        }

        function deleteNoteCard(noteId) {
            if (!confirm('确定删除这条笔记吗？')) return;
            fetch(BACKEND_API_URL + '/api/notes/' + noteId, { method: 'DELETE' })
            .then(r => r.json())
            .then(function(data) {
                if (data.status === 'ok') {
                    renderNotesPanel();
                    showToast('笔记已删除', 'success');
                } else {
                    showToast('删除笔记失败', 'error');
                }
            })
            .catch(function(err) {
                console.error('删除笔记失败:', err);
                showToast('删除笔记失败', 'error');
            });
        }

        function moveNote(noteId, direction) {
            fetch(BACKEND_API_URL + '/api/notes/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note_id: noteId, direction: direction })
            })
            .then(r => r.json())
            .then(function(data) {
                if (data.status === 'ok') {
                    renderNotesPanel();
                    if (data.message) showToast(data.message, 'info');
                } else {
                    showToast('移动笔记失败', 'error');
                }
            })
            .catch(function(err) {
                console.error('移动笔记失败:', err);
                showToast('移动笔记失败', 'error');
            });
        }

        function updateNoteTitle(noteId, newTitle) {
            var branch = state.branches[state.currentBranchId];
            if (!branch || !branch.notes) return;
            var note = branch.notes.find(function(n) { return n.id === noteId; });
            if (note) {
                note.title = newTitle || '未命名笔记';
                note.updatedAt = new Date().toISOString();
                saveBranches();
            }
        }

        function toggleNoteCollapse(noteId) {
            var bodyEl = document.getElementById('note-body-' + noteId);
            if (bodyEl) {
                var isHidden = bodyEl.style.display === 'none';
                bodyEl.style.display = isHidden ? '' : 'none';
                // 记录折叠状态，使重绘（如移动排序）后保持用户的手动选择
                _noteCollapsedMap[noteId] = !isHidden;
            }
        }

        function editNoteCard(noteId) {
            var note = _currentNotes.find(function (n) { return n.id === noteId; });
            if (!note) return;
            var originalContent = note.content || '';
            floatingEditor.open({
                title: '编辑笔记',
                showTitle: true,
                titleValue: note.title || '',
                value: note.content || '',
                // 实时预览到真实笔记卡片（不持久化）
                liveUpdate: function (html) {
                    var bodyEl = document.getElementById('note-body-' + noteId);
                    if (bodyEl) { bodyEl.innerHTML = html; }
                },
                // 取消回退：恢复笔记原始内容
                liveRestore: function () {
                    var bodyEl = document.getElementById('note-body-' + noteId);
                    if (bodyEl) { bodyEl.innerHTML = formatMessage(originalContent, true); }
                },
                onSave: function (newContent, newTitle) {
                    var t = (newTitle !== null && newTitle !== undefined && newTitle !== '') ? newTitle : '未命名笔记';
                    var c = newContent;
                    fetch(BACKEND_API_URL + '/api/notes', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ note_id: noteId, title: t, content: c })
                    })
                    .then(r => r.json())
                    .then(function(data) {
                        if (data.status === 'ok') {
                            note.title = t;
                            note.content = c;
                            note.updatedAt = new Date().toISOString();
                            saveBranches();
                            renderNotesPanel();
                            showToast('笔记已更新', 'success');
                        } else {
                            showToast('更新笔记失败', 'error');
                        }
                    })
                    .catch(function(err) {
                        console.error('更新笔记失败:', err);
                        showToast('更新笔记失败', 'error');
                    });
                }
            });
        }

        // 兼容保留（旧内联编辑已迁移到浮动编辑器）
        function saveNoteEdit(noteId) {}
        function cancelNoteEdit(noteId) {}

        // ==================== 更新历史列表函数 ====================
        function updateHistoryList() {
            if (state.historyMode === 'single') {
                updateSingleHistoryView();
            } else {
                updateGlobalHistoryView();
            }
        }

        function updateSingleHistoryView() {
            const currentBranch = state.currentBranchId ? state.branches[state.currentBranchId] : null;
            // 无当前对话 → 显示空态，避免 currentBranch.messages 崩溃
            if (!currentBranch) {
                historyList.innerHTML = '';
                const empty = document.createElement('div');
                empty.className = 'empty-state';
                empty.innerHTML = '<div class="empty-state-icon">💭</div><div class="empty-state-text">暂无对话历史</div>';
                historyList.appendChild(empty);
                return;
            }
            // 只显示非临时对话且hasContent为true的对话
            const messages = currentBranch.messages.filter(msg => !msg.isSystem);
            // historyCount updated removed

            let filteredMessages = messages.filter(msg => {
                if (state.sidebar.searchQuery) {
                    if (!msg.content.toLowerCase().includes(state.sidebar.searchQuery)) {
                        return false;
                    }
                }

                if (state.sidebar.currentFilter !== 'all') {
                    if (state.sidebar.currentFilter === 'database') {
                        return msg.type === 'database';
                    } else if (state.sidebar.currentFilter === 'document') {
                        return msg.type === 'document';
                    }
                    return msg.role === state.sidebar.currentFilter;
                }

                return true;
            });

            historyList.innerHTML = '';

            if (filteredMessages.length === 0) {
                const emptyMsg = document.createElement('div');
                emptyMsg.className = 'history-empty';
                emptyMsg.style.textAlign = 'center';
                emptyMsg.style.padding = '2rem';
                emptyMsg.style.color = 'var(--gray)';
                emptyMsg.textContent = state.sidebar.searchQuery ? '未找到匹配的消息' : '暂无对话历史';
                historyList.appendChild(emptyMsg);
                return;
            }

            filteredMessages.forEach(msg => {
                const historyItem = createHistoryItem(msg);
                historyList.appendChild(historyItem);
            });
        }

        function updateGlobalHistoryView() {
            const allMessages = [];

            for (const branchId in state.branches) {
                const branch = state.branches[branchId];
                // 显示所有分支
                const branchMessages = branch.messages.filter(msg => !msg.isSystem);

                branchMessages.forEach(msg => {
                    allMessages.push({
                        ...msg,
                        branchId: branchId,
                        branchName: branch.name
                    });
                });
            }

            allMessages.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

            // 尝试加载自定义分支顺序
            let branchOrder = null;
            try {
                const saved = localStorage.getItem('branchOrder');
                if (saved) branchOrder = JSON.parse(saved);
            } catch (e) {}

            const groupedByBranch = {};
            allMessages.forEach(msg => {
                if (!groupedByBranch[msg.branchId]) {
                    groupedByBranch[msg.branchId] = {
                        branchId: msg.branchId,
                        branchName: state.branches[msg.branchId]?.name || '未知对话',
                        messages: []
                    };
                }
                groupedByBranch[msg.branchId].messages.push(msg);
            });

            for (const branchId in groupedByBranch) {
                groupedByBranch[branchId].messages = groupedByBranch[branchId].messages.filter(msg => {
                    if (state.sidebar.searchQuery) {
                        if (!msg.content.toLowerCase().includes(state.sidebar.searchQuery)) {
                            return false;
                        }
                    }

                    if (state.sidebar.currentFilter !== 'all') {
                        if (state.sidebar.currentFilter === 'database') {
                            return msg.type === 'database';
                        } else if (state.sidebar.currentFilter === 'document') {
                            return msg.type === 'document';
                        } else {
                            return msg.role === state.sidebar.currentFilter;
                        }
                    }

                    return true;
                });
            }

            Object.keys(groupedByBranch).forEach(branchId => {
                if (groupedByBranch[branchId].messages.length === 0) {
                    delete groupedByBranch[branchId];
                }
            });

            // 添加空对话（没有消息但需要显示在侧边栏的永久分支）
            // main 是空占位，不显示在侧边栏历史列表（避免每次刷新都出现一个空的“默认对话”）
            for (const branchId in state.branches) {
                if (branchId === 'main') continue;
                const branch = state.branches[branchId];
                if (!groupedByBranch[branchId] && branch.isTemporary === false) {
                    groupedByBranch[branchId] = {
                        branchId: branchId,
                        branchName: branch.name,
                        messages: []
                    };
                }
            }

            historyList.innerHTML = '';

            if (Object.keys(groupedByBranch).length === 0) {
                const emptyState = document.createElement('div');
                emptyState.className = 'empty-state';
                emptyState.innerHTML = `
                    <div class="empty-state-icon">💭</div>
                    <div class="empty-state-text">${state.sidebar.searchQuery ? '未找到匹配的对话' : '暂无对话历史'}</div>
                    <button class="new-chat-btn" id="new-chat-btn-empty">
                        + 开始新的对话
                    </button>
                `;
                historyList.appendChild(emptyState);
                // 重新绑定事件
                document.getElementById('new-chat-btn-empty').addEventListener('click', createNewConversation);
                return;
            }

            let totalMessages = 0;
            for (const branchId in groupedByBranch) {
                totalMessages += groupedByBranch[branchId].messages.length;
            }
            // historyCount update removed

            // 按自定义顺序或时间排序遍历
            const groupKeys = Object.keys(groupedByBranch);
            if (branchOrder && branchOrder.length > 0) {
                // 按保存的顺序排序
                groupKeys.sort((a, b) => {
                    const ia = branchOrder.indexOf(a);
                    const ib = branchOrder.indexOf(b);
                    if (ia === -1 && ib === -1) return 0;
                    if (ia === -1) return 1;
                    if (ib === -1) return -1;
                    return ia - ib;
                });
            } else {
                // 没保存顺序时按最新消息时间降序
                groupKeys.sort((a, b) => {
                    const ma = groupedByBranch[a].messages[0];
                    const mb = groupedByBranch[b].messages[0];
                    return new Date(mb.timestamp) - new Date(ma.timestamp);
                });
            }

            for (const branchId of groupKeys) {
                const group = groupedByBranch[branchId];

                const groupEl = document.createElement('div');
                groupEl.className = 'history-group';
                groupEl.dataset.branchId = branchId;
                groupEl.draggable = true;

                const isCurrentBranch = branchId === state.currentBranchId;
                const isExpanded =
                    state.sidebar.searchQuery ||
                    state.sidebar.currentFilter !== 'all' ||
                    state.sidebar.expandedGroups.has(branchId);

                groupEl.innerHTML = `
                    <div class="group-header" onclick="handleBranchClick('${branchId}')">
                        <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 10px;">
                            ${group.branchName}
                            ${isCurrentBranch ? ' <span style="color: var(--success);">(当前)</span>' : ''}
                        </div>
                        <div class="group-actions-wrapper" onclick="event.stopPropagation()">
                            <div class="group-message-count">${group.messages.length}</div>
                            <div class="group-actions">
                                <button class="group-action-btn" title="导出对话" onclick="event.stopPropagation(); exportConversation('${branchId}')">
                                    📤
                                </button>
                                <button class="group-action-btn" title="重命名对话" onclick="event.stopPropagation(); openRenameBranchModal(event, '${branchId}')">
                                    ✎
                                </button>
                                <button class="group-action-btn delete" title="删除对话" onclick="event.stopPropagation(); deleteBranchWithConfirm('${branchId}')">
                                    ×
                                </button>
                                <button class="group-action-btn" title="添加便携文件" onclick="event.stopPropagation(); openBubbleAddPanel(event, '${branchId}')">
                                    ＋
                                </button>
                            </div>
                        </div>
                    </div>
                    <div class="group-content ${isExpanded ? 'expanded' : ''}" id="group-content-${branchId}">
                        <div class="group-messages" id="group-${branchId}">
                        </div>
                    </div>
                    ${(state.branches[branchId]._bubbles || []).length > 0 ? `
                    <div class="group-bubbles" data-branch-id="${branchId}" id="group-bubbles-${branchId}">
                        ${(state.branches[branchId]._bubbles || []).map((b, bi) => `
                            <span class="history-bubble${b.filePath || b.fileDataKey || b.hasFile ? ' has-file' : ''}" draggable="true"
                                  data-bubble-id="${b.id}"
                                  data-bubble-text="${b.text}"
                                  data-file-path="${b.filePath || ''}"
                                  data-file-id="${b.fileId || ''}"
                                  data-branch-id="${branchId}"
                                  title="${b.text}">
                                <span class="bubble-text">${b.text}</span>
                            </span>
                        `).join('')}
                    </div>` : ''}
                `;

                historyList.appendChild(groupEl);

                // 拖拽事件绑定
                groupEl.addEventListener('dragstart', (e) => {
                    // 阻止从操作按钮开始拖拽
                    if (e.target.closest('.group-action-btn') || e.target.closest('.group-message-count')) {
                        e.preventDefault();
                        return;
                    }
                    e.dataTransfer.setData('text/plain', groupEl.dataset.branchId);
                    groupEl.classList.add('dragging');
                    groupEl.style.opacity = '0.5';
                });

                groupEl.addEventListener('dragend', () => {
                    groupEl.classList.remove('dragging');
                    document.querySelectorAll('.history-group').forEach(g => {
                        g.classList.remove('drag-over-top', 'drag-over-bottom');
                    });
                });

                groupEl.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    if (groupEl.classList.contains('dragging')) return;
                    const rect = groupEl.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    document.querySelectorAll('.history-group').forEach(g => {
                        g.classList.remove('drag-over-top', 'drag-over-bottom');
                    });
                    if (e.clientY < midY) {
                        groupEl.classList.add('drag-over-top');
                    } else {
                        groupEl.classList.add('drag-over-bottom');
                    }
                });

                groupEl.addEventListener('dragleave', () => {
                    groupEl.classList.remove('drag-over-top', 'drag-over-bottom');
                });

                groupEl.addEventListener('drop', (e) => {
                    e.preventDefault();
                    if (groupEl.classList.contains('dragging')) return;

                    const draggedBranchId = e.dataTransfer.getData('text/plain');
                    const targetBranchId = groupEl.dataset.branchId;
                    if (!draggedBranchId || draggedBranchId === targetBranchId) return;

                    // 获取当前可见的分支顺序
                    const allGroups = Array.from(document.querySelectorAll('.history-group'));
                    const branchOrder = allGroups.map(g => g.dataset.branchId);
                    const draggedIdx = branchOrder.indexOf(draggedBranchId);
                    const targetIdx = branchOrder.indexOf(targetBranchId);
                    if (draggedIdx === -1 || targetIdx === -1) return;

                    // 移除被拖动的组
                    branchOrder.splice(draggedIdx, 1);
                    const newTargetIdx = branchOrder.indexOf(targetBranchId);
                    const rect = groupEl.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    const insertIdx = e.clientY < midY ? newTargetIdx : newTargetIdx + 1;
                    branchOrder.splice(insertIdx, 0, draggedBranchId);

                    // 保存顺序
                    try { localStorage.setItem('branchOrder', JSON.stringify(branchOrder)); } catch (err) {}

                    groupEl.classList.remove('drag-over-top', 'drag-over-bottom');
                    updateHistoryList();
                });

                // 组气泡事件绑定
                const branchId2 = branchId;
                const bubblesEl = groupEl.querySelector('.group-bubbles');
                if (bubblesEl) {
                    // 已有气泡：点击菜单、拖拽
                    bubblesEl.querySelectorAll('.history-bubble').forEach(bubble => {
                        bubble.addEventListener('click', (e) => {
                            e.stopPropagation();
                            showBubbleMenu(e, bubble, null, branchId2);
                        });
                        bubble.addEventListener('dragstart', (e) => {
                            bubble.classList.add('dragging');
                            e.dataTransfer.setData('text/plain', bubble.dataset.bubbleText);
                            e.dataTransfer.setData('application/file-path', bubble.dataset.filePath || '');
                            e.dataTransfer.effectAllowed = 'copyMove';
                        });
                        bubble.addEventListener('dragend', () => {
                            bubble.classList.remove('dragging');
                        });
                    });
                }

                const groupMessagesEl = document.getElementById(`group-${branchId}`);
                group.messages.forEach(msg => {
                    const historyItem = createHistoryItem(msg, true);
                    groupMessagesEl.appendChild(historyItem);
                });
            }
        }

        // ==================== 分组展开/收起函数 ====================
        function toggleGroup(branchId) {
            const groupContent = document.getElementById(`group-content-${branchId}`);
            if (groupContent) {
                const isExpanded = groupContent.classList.contains('expanded');
                if (isExpanded) {
                    groupContent.classList.remove('expanded');
                    state.sidebar.expandedGroups.delete(branchId);
                } else {
                    groupContent.classList.add('expanded');
                    state.sidebar.expandedGroups.add(branchId);
                }
                localStorage.setItem('ai-expanded-groups', JSON.stringify([...state.sidebar.expandedGroups]));
            }
        }

        // ==================== 对话点击处理 ====================
        function handleBranchClick(branchId) {
            if (branchId === state.currentBranchId) {
                // 点击当前对话：展开/收拢详情
                toggleGroup(branchId);
            } else {
                // 点击其他对话：切换到该对话（调用下方的 switchToBranch）
                switchToBranch(branchId);
            }
        }

        function syncNotesPanel() {
            setTimeout(function() {
                renderNotesPanel();
            }, 0);
        }

        // ==================== 创建历史项 ====================
        function createHistoryItem(message, showBranchInfo = false) {
            const item = document.createElement('div');
            item.className = 'history-item';

            if (message.type === 'document') {
                item.classList.add('document');
            } else if (message.type === 'database') {
                item.classList.add('database');
            }

            if (state.sidebar.activeMessageId === message.id) {
                item.classList.add('active');
            }

            let preview = message.content;
            if (preview.length > 60) {
                preview = preview.substring(0, 57) + '...';
            }

            const time = new Date(message.timestamp).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit'
            });

            let roleLabel = message.role === 'user' ? '用户' : 'AI';
            if (message.type === 'document') {
                roleLabel = '文档';
            } else if (message.type === 'database') {
                roleLabel = '知识库';
            } else if (message.thinkingChain) {
                roleLabel = '思考';
            }

            const dateTime = new Date(message.timestamp);
            const dateStr = `${dateTime.getMonth() + 1}月${dateTime.getDate()}日`;
            const timeStr = dateTime.toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit'
            });

            item.innerHTML = `
                <div class="history-preview">${preview}</div>
                <div class="history-meta">
                    <span class="history-role ${message.role} ${message.type === 'document' ? 'document' : ''} ${message.type === 'database' ? 'database' : ''}">
                        ${roleLabel}
                    </span>
                    <span>${dateStr} ${timeStr}</span>
                </div>
            `;

            item.addEventListener('click', () => {
                if (state.historyMode === 'global' && message.branchId && message.branchId !== state.currentBranchId) {
                    switchToBranch(message.branchId);
                }

                if (message.id) {
                    scrollToMessage(message.id);
                }

                document.querySelectorAll('.history-item').forEach(el => {
                    el.classList.remove('active');
                });
                item.classList.add('active');
                state.sidebar.activeMessageId = message.id;
            });

            return item;
        }

        // ==================== 辅助函数 ====================
        function scrollToMessage(messageId) {
            const messageEl = document.querySelector(`[data-id="${messageId}"]`);
            if (messageEl) {
                // 仅在对话区域内部滚动，防止主页面上下跳动
                const container = document.getElementById('messages-container');
                if (container) {
                    const containerRect = container.getBoundingClientRect();
                    const messageRect = messageEl.getBoundingClientRect();
                    const relativeTop = messageRect.top - containerRect.top + container.scrollTop;
                    const scrollTarget = relativeTop - (container.clientHeight / 2) + (messageEl.clientHeight / 2);

                    container.scrollTo({
                        top: scrollTarget,
                        behavior: 'smooth'
                    });
                }

                messageEl.style.animation = 'none';
                void messageEl.offsetWidth; // 触发回流重置动画
                setTimeout(() => {
                    messageEl.style.animation = 'highlight 1.5s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                }, 10);

                if (state.historyMode === 'global') {
                    const branchId = getBranchIdFromMessageId(messageId);
                    if (branchId) {
                        const groupEl = document.querySelector(`[data-branch-id="${branchId}"]`);
                        if (groupEl) {
                            const groupContent = document.getElementById(`group-content-${branchId}`);
                            if (groupContent && !groupContent.classList.contains('expanded')) {
                                groupContent.classList.add('expanded');
                                state.sidebar.expandedGroups.add(branchId);
                                localStorage.setItem('ai-expanded-groups', JSON.stringify([...state.sidebar.expandedGroups]));
                            }
                        }
                    }
                }
            }
        }

        function getBranchIdFromMessageId(messageId) {
            for (const branchId in state.branches) {
                const branch = state.branches[branchId];
                const message = branch.messages.find(msg => msg.id === messageId);
                if (message) {
                    return branchId;
                }
            }
            return null;
        }

        function switchToBranch(branchId) {
            if (state.currentBranchId === branchId) return;

            // 保存当前对话状态（不再区分临时/永久）
            state.currentBranchId = branchId;
            // 记住当前对话（切对话不一定走 saveBranches，这里显式持久化以便重启恢复）
            if (typeof persistCurrentBranch === 'function') persistCurrentBranch();
            messageInput.value = '';
            state.documentUpload.pendingAttachments = []; state.documentUpload._errorFiles = [];
            renderMessages();
            updateTokenDisplay();
            updateHistoryList();
            updateSelectedFilesList();
            hideUploadPanel();

            // 笔记同步：setTimeout 0 确保在所有渲染完成后执行
            setTimeout(function() {
                var np = document.getElementById('notes-panel');
                if (np && np.classList.contains('active')) {
                    renderNotesPanel();
                }
            }, 0);
        }

        // ==================== 数据存储与加载 ====================
