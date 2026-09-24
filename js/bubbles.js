        function setGlobalTheme(themeName) {
            // 移除所有主题类
            const themes = ['theme-midnight', 'theme-emerald', 'theme-warm', 'theme-cyber', 'theme-pink', 'theme-macaron-pink', 'theme-macaron-green', 'theme-macaron-violet', 'theme-macaron-blue'];
            document.body.classList.remove(...themes);

            if (themeName !== 'default') {
                document.body.classList.add(`theme-${themeName}`);
            }

            // 更新选中状态
            document.querySelectorAll('.theme-dot').forEach(dot => {
                dot.classList.toggle('active', dot.dataset.theme === themeName);
            });

            // 更新设置里的名称显示
            const themeNames = {
                'default': '经典蓝',
                'midnight': '深夜黑',
                'emerald': '翡翠绿',
                'warm': '暖阳橙',
                'cyber': '赛博风',
                'pink': '樱花粉',
                'macaron-pink': '梦境粉',
                'macaron-green': '梦境绿',
                'macaron-violet': '梦境紫',
                'macaron-blue': '梦境蓝'
            };
            const nameEl = document.getElementById('current-theme-name');
            if (nameEl) nameEl.textContent = themeNames[themeName] || '未知';

            // 保存设置
            localStorage.setItem('ragagent-theme', themeName);

            // 处理特殊的 Mermaid 刷新（如果需要根据背景变色）
            setTimeout(renderMermaidDiagrams, 100);
        }

        function toggleUploadPanel() {
            const isActive = fileUploadPanel.classList.contains('active');
            if (isActive) {
                hideUploadPanel();
            } else {
                showUploadPanel();
            }
        }

        function showUploadPanel() {
            collapseExpandMode();
            fileUploadPanel.classList.add('active');
            attachmentBtn.classList.add('active');
        }

        function collapseExpandMode() {
            expandedMessageArea.classList.remove('active');
            expandBtn.classList.remove('active');
        }

        function hideUploadPanel() {
            fileUploadPanel.classList.remove('active');
            attachmentBtn.classList.remove('active');
        }

        function handleMultipleFiles(files) {
            if (files.length === 0) return;

            files.forEach(file => {
                if (validateFile(file)) {
                    addPendingAttachment(file);
                }
            });

            updateSelectedFilesList();
        }

        function validateFile(file) {
            // 支持的文件类型 (像Gemini一样广泛)
            const imageExtensions = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
            const documentExtensions = ['pdf', 'docx', 'txt', 'md', 'rtf', 'pptx', 'odt'];
            const dataExtensions = ['json', 'csv', 'xlsx', 'xls', 'xml', 'log', 'toml', 'ini'];
            const codeExtensions = ['js', 'py', 'java', 'cpp', 'c', 'h', 'ts', 'jsx', 'tsx', 'go', 'rs', 'php', 'rb', 'swift', 'kt', 'sh', 'yaml', 'yml', 'sql', 'html', 'css', 'lua', 'r', 'pl', 'tex', 'bat', 'ps1'];

            const allExtensions = [...imageExtensions, ...documentExtensions, ...dataExtensions, ...codeExtensions];
            const fileExtension = file.name.split('.').pop().toLowerCase();

            if (!allExtensions.includes(fileExtension)) {
                // 加入错误列表（显示红色芯片提示用户删除）
                addErrorAttachment(file, '不支持的格式');
                return false;
            }

            // 文件大小限制 (50MB)
            if (file.size > 50 * 1024 * 1024) {
                addErrorAttachment(file, '文件过大(>50MB)');
                return false;
            }

            // 检查是否已添加
            const isDuplicate = state.documentUpload.pendingAttachments.some(
                att => att.name === file.name && att.size === file.size
            );

            if (isDuplicate) {
                showToast(`文件已添加: ${file.name}`, 'warning');
                return false;
            }

            return true;
        }

        function addErrorAttachment(file, reason) {
            // 错误文件也显示在列表中，红色标记
            if (!state.documentUpload._errorFiles) {
                state.documentUpload._errorFiles = [];
            }
            // 避免重复添加
            if (state.documentUpload._errorFiles.some(e => e.name === file.name && e.size === file.size)) return;
            state.documentUpload._errorFiles.push({
                id: 'err-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                name: file.name,
                size: file.size,
                icon: '⚠️',
                reason: reason
            });
            updateSelectedFilesList();
        }

        function addPendingAttachment(file) {
            const attachment = {
                id: 'att-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                file: file,
                name: file.name,
                size: file.size,
                type: getFileType(file.name),
                icon: getFileIcon(file.name)
            };

            state.documentUpload.pendingAttachments.push(attachment);

            // 立即上传到后端 + 解析文本
            _autoProcessAttachment(attachment);
        }

        /** 按 file_id 添加附件（气泡→LLM，文件已在后端） */
        function addPendingAttachmentByFileId(fileId, fileName, fileSize) {
            const attachment = {
                id: 'att-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                file: null,       // 无前端 File 对象
                file_id: fileId,  // 后端已有
                name: fileName,
                size: fileSize || 0,
                type: getFileType(fileName),
                icon: getFileIcon(fileName),
                _remote: true,    // 标记为远端文件
                _parsedText: ''   // 不需要解析
            };
            state.documentUpload.pendingAttachments.push(attachment);
            updateSelectedFilesList();
        }

        async function _autoProcessAttachment(att) {
            // 上传到后端
            try {
                var formData = new FormData();
                formData.append('file', att.file);
                var resp = await fetch('/api/file/upload', { method: 'POST', body: formData });
                var data = await resp.json();
                if (data.file_id) att.file_id = data.file_id;
            } catch (err) {
                console.warn('Auto upload failed:', err);
            }
            // 正文一律由后端按 file_id 注入（智能体侧 read_file 读取）。
            // 前端不再做任何本地解析 —— 此前这里对非 pdf/doc/docx 走
            // extractTextFromFile()（mammoth/JSZip/Tesseract OCR 全套前端解析），
            // 与"上传统一走 read_file"的设计冲突，且凭空多一遍无用解析。
            att._backendInjected = true;
            att._parsedText = '';
            updateSelectedFilesList(); // 刷新 UI 显示 file_id
        }

        function removePendingAttachment(attachmentId) {
            state.documentUpload.pendingAttachments = state.documentUpload.pendingAttachments.filter(
                att => att.id !== attachmentId
            );
            updateSelectedFilesList();
        }

        function updateSelectedFilesList() {
            if (state.documentUpload.pendingAttachments.length === 0 && (!state.documentUpload._errorFiles || state.documentUpload._errorFiles.length === 0)) {
                selectedFilesList.innerHTML = '';
                return;
            }

            let html = '';
            // 正常文件（点击可打开预览）
            html += state.documentUpload.pendingAttachments.map(att => {
                var title, openUrl;
                if (att._remote) {
                    // 远端文件（已在后端）
                    openUrl = null;  // 不使用 blob URL
                    title = att.file_id ? '已上传，点击打开' : '...';
                } else {
                    openUrl = URL.createObjectURL(att.file);
                    title = att.file_id ? '已上传，点击打开' : '点击打开';
                }
                return '<div class="file-chip file-chip-clickable" data-id="' + att.id + '"' +
                    (openUrl ? ' data-url="' + openUrl + '"' : '') +
                    ' title="' + title + '">' +
                    '<span class="file-chip-icon">' + att.icon + '</span>' +
                    '<span class="file-chip-name" title="' + (title) + ': ' + att.name + '">' + att.name + '</span>' +
                    '<span class="file-chip-size">' + formatFileSize(att.size) + '</span>' +
                    '<button class="file-chip-remove" onclick="removePendingAttachment(\'' + att.id + '\');event.stopPropagation();" title="移除">×</button>' +
                '</div>';
            }).join('');
            // 错误文件（红色）
            if (state.documentUpload._errorFiles) {
                html += state.documentUpload._errorFiles.map(att => `
                    <div class="file-chip file-chip-error" data-id="${att.id}">
                        <span class="file-chip-icon">${att.icon}</span>
                        <span class="file-chip-name" title="${att.name}">${att.name}</span>
                        <span class="file-chip-size file-chip-error-reason">${att.reason}</span>
                        <button class="file-chip-remove" onclick="removeErrorAttachment('${att.id}')" title="删除">×</button>
                    </div>
                `).join('');
            }
            selectedFilesList.innerHTML = html;
        }

        function removeErrorAttachment(attachmentId) {
            if (state.documentUpload._errorFiles) {
                state.documentUpload._errorFiles = state.documentUpload._errorFiles.filter(
                    att => att.id !== attachmentId
                );
                updateSelectedFilesList();
            }
        }

        function getFileIcon(fileName) {
            const extension = fileName.split('.').pop().toLowerCase();
            const iconMap = {
                // 图片
                'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'webp': '🖼️', 'svg': '🖼️',
                // 文档
                'pdf': '📕', 'docx': '📘', 'txt': '📄', 'md': '📝', 'rtf': '📄',
                'pptx': '📑', 'odt': '📃',
                // 数据
                'json': '📊', 'csv': '📊', 'xlsx': '📊', 'xls': '📊', 'xml': '📊',
                'log': '📋', 'toml': '⚙️', 'ini': '⚙️',
                // 代码
                'js': '📜', 'py': '🐍', 'java': '☕', 'cpp': '⚙️', 'c': '⚙️', 'ts': '📜',
                'jsx': '⚛️', 'tsx': '⚛️', 'go': '🔷', 'rs': '🦀', 'php': '🐘',
                'html': '🌐', 'css': '🎨', 'sh': '💻', 'yaml': '📋', 'yml': '📋',
                'sql': '🗃️', 'lua': '🌙', 'r': '📈', 'pl': '🐪', 'tex': '📐',
                'bat': '🪟', 'ps1': '🪟'
            };
            return iconMap[extension] || '📎';
        }

        function getFileType(fileName) {
            const extension = fileName.split('.').pop().toLowerCase();
            const typeMap = {
                'jpg': '图片', 'jpeg': '图片', 'png': '图片', 'gif': '图片', 'webp': '图片',
                'svg': 'SVG矢量图', 'bmp': '位图', 'tiff': 'TIFF图像',
                'pdf': 'PDF文档', 'docx': 'Word文档', 'txt': '文本文件', 'md': 'Markdown',
                'rtf': '富文本', 'pptx': 'PPT演示', 'odt': '文本文档',
                'json': 'JSON数据', 'csv': 'CSV数据', 'xlsx': 'Excel表格',
                'xls': 'Excel表格', 'xml': 'XML数据', 'log': '日志文件',
                'toml': 'TOML配置', 'ini': 'INI配置',
                'js': 'JavaScript', 'py': 'Python', 'java': 'Java', 'cpp': 'C++', 'c': 'C语言',
                'html': 'HTML网页', 'css': 'CSS样式', 'ts': 'TypeScript',
                'sh': 'Shell脚本', 'yaml': 'YAML配置', 'yml': 'YAML配置',
                'sql': 'SQL脚本', 'lua': 'Lua脚本', 'r': 'R语言', 'pl': 'Perl脚本',
                'tex': 'LaTeX', 'bat': '批处理', 'ps1': 'PowerShell'
            };
            return typeMap[extension] || extension.toUpperCase();
        }

        function formatFileSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        let _bubbleMenuBranchId = null;
        let _bubbleMenuBubbleEl = null;
        let _bubbleAddBranchId = null;
        let _bubbleAddItem = null;

        function showBubbleMenu(e, bubbleEl, _unused_msg, branchId) {
            const menu = document.getElementById('bubble-context-menu');
            _bubbleMenuBranchId = branchId;
            _bubbleMenuBubbleEl = bubbleEl;

            // 根据是否有文件，显示/隐藏「发送给 LLM」和「打开文件地址」按钮；
            // 非 Windows 后端（Linux/服务器部署）隐藏「打开文件」「打开文件地址」（os.startfile/explorer 不存在）
            const filePath = bubbleEl?.dataset?.filePath || '';
            const hasFileId = !!bubbleEl?.dataset?.fileId;
            const isWinBackend = (window.C4EAI_PLATFORM || 'win32') === 'win32';
            const sendLlmItem = menu.querySelector('[data-action="send-llm"]');
            const openFileItem = menu.querySelector('[data-action="open-file"]');
            const openLocItem = menu.querySelector('[data-action="open-location"]');
            if (sendLlmItem) {
                sendLlmItem.style.display = filePath ? '' : 'none';
            }
            if (openFileItem) {
                openFileItem.style.display = isWinBackend ? '' : 'none';
            }
            if (openLocItem) {
                openLocItem.style.display = (hasFileId && isWinBackend) ? '' : 'none';
            }

            menu.style.left = e.clientX + 'px';
            menu.style.top = e.clientY + 'px';
            menu.classList.add('show');

            requestAnimationFrame(() => {
                const rect = menu.getBoundingClientRect();
                if (rect.right > window.innerWidth) {
                    menu.style.left = (window.innerWidth - rect.width - 5) + 'px';
                }
                if (rect.bottom > window.innerHeight) {
                    menu.style.top = (window.innerHeight - rect.height - 5) + 'px';
                }
            });
        }

        // 打开添加气泡面板
        function openBubbleAddPanel(e, branchId) {
            const panel = document.getElementById('bubble-add-panel');
            const input = document.getElementById('bubble-add-input');
            _bubbleAddBranchId = branchId;

            input.value = '';
            panel.style.left = e.clientX + 'px';
            panel.style.top = e.clientY + 'px';
            panel.classList.add('show');
            setTimeout(() => input.focus(), 50);

            requestAnimationFrame(() => {
                const rect = panel.getBoundingClientRect();
                if (rect.right > window.innerWidth) {
                    panel.style.left = (window.innerWidth - rect.width - 5) + 'px';
                }
                if (rect.bottom > window.innerHeight) {
                    panel.style.top = (window.innerHeight - rect.height - 5) + 'px';
                }
            });
        }

        function closeBubbleAddPanel() {
            document.getElementById('bubble-add-panel')?.classList.remove('show');
        }

        // 关闭气泡菜单和添加面板
        document.addEventListener('click', (e) => {
            const menu = document.getElementById('bubble-context-menu');
            const panel = document.getElementById('bubble-add-panel');
            if (menu && !menu.contains(e.target) && !e.target.closest('.history-bubble')) {
                menu.classList.remove('show');
            }
            if (panel && !panel.contains(e.target) && !e.target.closest('.bubble-add-btn')) {
                panel.classList.remove('show');
            }
        });

        // 气泡菜单按钮事件
        document.addEventListener('click', (e) => {
            const menuItem = e.target.closest('.bubble-context-menu .menu-item');
            if (!menuItem) return;

            const menu = document.getElementById('bubble-context-menu');
            menu.classList.remove('show');

            const action = menuItem.dataset.action;
            const branchId = _bubbleMenuBranchId;
            const branch = branchId ? state.branches[branchId] : null;
            const bubbleEl = _bubbleMenuBubbleEl;
            const bubbleText = bubbleEl ? bubbleEl.dataset.bubbleText : '';
            const bubbleId = bubbleEl ? bubbleEl.dataset.bubbleId : '';

            if (action === 'open-file') {
                const bubbleId = bubbleEl ? bubbleEl.dataset.bubbleId : '';
                const branch = state.branches[_bubbleMenuBranchId];
                const bub = branch?._bubbles?.find(b => b.id === bubbleId);
                if (bub && bub.fileId) {
                    if (typeof window.openFileById === 'function') {
                        window.openFileById(bub.fileId, bub.filePath || bub.text || 'file');
                    } else if (state.settings.fileOpenMode === 'backend') {
                        fetch('/api/file/' + bub.fileId + '/open', { method: 'POST' });
                    } else {
                        window.open('/api/file/' + bub.fileId, '_blank');
                    }
                } else if (bub && bub.fileDataKey) {
                    // 从 IndexedDB 读取并触发下载
                    _dbGetFile(bub.fileDataKey).then(data => {
                        if (!data) { showPassiveToast('文件数据未找到'); return; }
                        const a = document.createElement('a');
                        a.href = data;
                        a.download = bub.filePath || bub.text || 'file';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        if (typeof showToast === 'function') {
                            showToast(`✅ 文件 "${bub.filePath || bub.text}" 已下载`, 'success');
                        }
                    });
                } else if (bub && bub.fileData) {
                    // 兼容旧格式（内嵌 base64）
                    window.open(bub.fileData, '_blank');
                } else if (bub && bub.filePath) {
                    const a = document.createElement('a');
                    a.href = 'file:///' + bub.filePath.replace(/\\\\/g, '/');
                    a.target = '_blank';
                    a.click();
                } else {
                    showPassiveToast('该气泡没有关联文件数据');
                }
            } else if (action === 'send-llm') {
                const bubbleId = bubbleEl ? bubbleEl.dataset.bubbleId : '';
                const branch = state.branches[_bubbleMenuBranchId];
                const bub = branch?._bubbles?.find(b => b.id === bubbleId);
                if (bub && bub.fileId) {
                    // 直接传 file_id 到上传区（文件已在后端）
                    addPendingAttachmentByFileId(bub.fileId, bub.filePath || bub.text || 'file', bub.fileSize);
                    updateSelectedFilesList();
                    showUploadPanel();
                } else if (bub && bub.fileDataKey) {
                    // 从 IndexedDB 读取并重建 File
                    _dbGetFile(bub.fileDataKey).then(data => {
                        if (!data) { showPassiveToast('文件数据未找到'); return; }
                        const mimeMatch = data.match(/^data:([^;]+);/);
                        const mime = mimeMatch ? mimeMatch[1] : 'application/octet-stream';
                        const byteStr = atob(data.split(',')[1]);
                        const ab = new ArrayBuffer(byteStr.length);
                        const ia = new Uint8Array(ab);
                        for (let i = 0; i < byteStr.length; i++) ia[i] = byteStr.charCodeAt(i);
                        const blob = new Blob([ab], { type: mime });
                        const reconstructedFile = new File([blob], bub.filePath || bub.text, { type: mime });
                        addPendingAttachment(reconstructedFile);
                        updateSelectedFilesList();
                        showUploadPanel();
                    });
                } else if (bub && bub.fileData) {
                    // 兼容旧格式
                    const mimeMatch = bub.fileData.match(/^data:([^;]+);/);
                    const mime = mimeMatch ? mimeMatch[1] : 'application/octet-stream';
                    const byteStr = atob(bub.fileData.split(',')[1]);
                    const ab = new ArrayBuffer(byteStr.length);
                    const ia = new Uint8Array(ab);
                    for (let i = 0; i < byteStr.length; i++) ia[i] = byteStr.charCodeAt(i);
                    const blob = new Blob([ab], { type: mime });
                    const reconstructedFile = new File([blob], bub.filePath || bub.text, { type: mime });
                    addPendingAttachment(reconstructedFile);
                    updateSelectedFilesList();
                    showUploadPanel();
                } else if (bub) {
                    // 纯文字气泡
                    const input = document.getElementById('message-input');
                    if (input) {
                        input.value = (input.value ? input.value + ' ' : '') + bub.text;
                        input.focus();
                    }
                    showUploadPanel();
                }
            } else if (action === 'open-location') {
                const bubbleId = bubbleEl ? bubbleEl.dataset.bubbleId : '';
                const branch = state.branches[_bubbleMenuBranchId];
                const bub = branch?._bubbles?.find(b => b.id === bubbleId);
                if (bub && bub.fileId) {
                    fetch('/api/file/' + bub.fileId + '/open-location', { method: 'POST' })
                        .then(r => r.json())
                        .then(d => {
                            if (typeof showToast === 'function' && d && d.message) showToast(d.message, 'info');
                        })
                        .catch(err => console.error('打开文件地址失败:', err));
                } else {
                    showPassiveToast('该气泡没有后端关联文件，无法打开文件地址');
                }
            } else if (action === 'edit-text') {
                const editModal = document.getElementById('bubble-edit-modal');
                const editInput = document.getElementById('bubble-edit-input');
                editInput.value = bubbleText;

                // 小浮层：定位到气泡右侧
                if (bubbleEl) {
                    const br = bubbleEl.getBoundingClientRect();
                    editModal.style.left = (br.right + 6) + 'px';
                    editModal.style.top = br.top + 'px';
                    requestAnimationFrame(() => {
                        const rect = editModal.getBoundingClientRect();
                        if (rect.right > window.innerWidth) editModal.style.left = (window.innerWidth - rect.width - 5) + 'px';
                        if (rect.bottom > window.innerHeight) editModal.style.top = (window.innerHeight - rect.height - 5) + 'px';
                    });
                }
                editModal.classList.add('show');
                editInput.focus();

                const confirmBtn = document.getElementById('bubble-edit-confirm');
                const newConfirm = confirmBtn.cloneNode(true);
                confirmBtn.parentNode.replaceChild(newConfirm, confirmBtn);
                newConfirm.addEventListener('click', () => {
                    const newText = editInput.value.trim();
                    if (newText && branch && branch._bubbles) {
                        const bub = branch._bubbles.find(b => b.id === bubbleId);
                        if (bub) bub.text = newText;
                        saveBranches();
                        updateHistoryList();
                    }
                    editModal.classList.remove('show');
                });
            } else if (action === 'delete') {
                if (branch && branch._bubbles && confirm(`确定要删除气泡"${bubbleText}"吗？`)) {
                    branch._bubbles = branch._bubbles.filter(b => b.id !== bubbleId);
                    saveBranches();
                    updateHistoryList();
                }
            }
        });

        // 添加气泡面板按钮事件 - 延迟初始化（等 DOM 加载完）
        document.addEventListener('DOMContentLoaded', () => {
            const addTextBtn = document.getElementById('bubble-add-text-btn');
            const addFileBtn = document.getElementById('bubble-add-file-btn');
            const addCancelBtn = document.getElementById('bubble-add-cancel');
            const bubbleFileInput = document.getElementById('bubble-file-input');
            if (!addTextBtn) return;

            addTextBtn.addEventListener('click', () => {
                const input = document.getElementById('bubble-add-input');
                const text = input.value.trim();
                if (text && _bubbleAddBranchId) {
                    addBubbleToBranch(_bubbleAddBranchId, text, '');
                }
                closeBubbleAddPanel();
            });

            addFileBtn.addEventListener('click', () => {
                if (_bubbleAddBranchId) {
                    const input = document.getElementById('bubble-add-input');
                    const text = input.value.trim() || '链接文件';
                    _bubbleAddPanelText = text;
                    closeBubbleAddPanel();
                    bubbleFileInput.click();
                }
            });

            addCancelBtn.addEventListener('click', closeBubbleAddPanel);

            let _bubbleAddPanelText = '';

            bubbleFileInput.addEventListener('change', async (e) => {
                // 支持多选：逐个文件各生成一个气泡
                const files = Array.from(e.target.files || []);
                e.target.value = '';          // 允许再次选同一文件
                if (!files.length || !_bubbleAddBranchId) return;

                const branch = state.branches[_bubbleAddBranchId];
                if (!branch) return;
                if (!branch._bubbles) branch._bubbles = [];

                for (let idx = 0; idx < files.length; idx++) {
                    const file = files[idx];
                    // 首个气泡用面板里填的文字；多选时其余用各自文件名
                    const text = (idx === 0 && _bubbleAddPanelText) ? _bubbleAddPanelText : file.name;
                    const bubbleId = 'bub-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6);

                    // 上传到后端
                    var formData = new FormData();
                    formData.append('file', file);
                    var fileId = '';
                    try {
                        var resp = await fetch('/api/file/upload', { method: 'POST', body: formData });
                        var data = await resp.json();
                        if (data.file_id) fileId = data.file_id;
                    } catch (err) {
                        console.warn('Bubble file upload failed:', err);
                    }

                    branch._bubbles.push({
                        id: bubbleId,
                        text: text,
                        filePath: file.name,
                        fileType: file.type || 'application/octet-stream',
                        fileSize: file.size,
                        icon: getFileIcon(file.name),
                        fileId: fileId,   // 后端文件 ID
                        hasFile: true
                    });
                }

                // 全部文件处理完，统一保存 + 刷新一次（避免多选时反复渲染）
                saveBranches();
                updateHistoryList();
                if (typeof renderBubbles === 'function') {
                    try { renderBubbles(_bubbleAddBranchId); } catch (err) {}
                }
                _bubbleAddPanelText = '';
            });
        });

        // ==================== 辅助函数 ====================
