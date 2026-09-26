
             function loadSettings() {
                const savedSettings = localStorage.getItem('ai-settings');
                if (savedSettings) {
                    try {
                        const parsedSettings = JSON.parse(savedSettings);
                        Object.assign(state.settings, parsedSettings);

                        temperatureSlider.value = state.settings.temperature;
                        contextSlider.value = state.settings.contextLength;
                        maxTokensInput.value = state.settings.maxTokens;
                        if (expectedContextTokensInput) expectedContextTokensInput.value = state.settings.expectedContextTokens ?? 4000;
                        const expTokensVal = document.getElementById('expected-context-tokens-value');
                        if (expTokensVal) expTokensVal.textContent = expectedContextTokensInput.value;
                  
                  
                        // 加载文件打开方式设置（三选一，读 state 渲染）
                        updateFileOpenModeUI();

                        // 加载分区字号设置
                        loadFontSizeSettings();

                        // 加载超时保护三级到面板（子智能体设置已迁至「智能体配置」面板）
                        const _tn = document.getElementById('timeout-nudge');
                        const _tw = document.getElementById('timeout-warn');
                        const _ta = document.getElementById('timeout-abort');
                        if (_tn) _tn.value = (state.settings.timeoutNudge ?? 120);
                        if (_tw) _tw.value = (state.settings.timeoutWarn ?? 240);
                        if (_ta) _ta.value = (state.settings.timeoutAbort ?? 420);
                    } catch (e) {
                        console.error('解析设置失败:', e);
                    }
                }

                // 加载 Neo4j 配置
                const savedNeo4j = localStorage.getItem('ragagent-neo4j-config');
                if (savedNeo4j) {
                    try {
                        const neo4jConfig = JSON.parse(savedNeo4j);
                        Object.assign(state.neo4j, neo4jConfig);
                        if (neo4jUri) neo4jUri.value = state.neo4j.uri;
                        if (neo4jUser) neo4jUser.value = state.neo4j.user;
                        if (neo4jPassword) neo4jPassword.value = state.neo4j.password;
                    } catch (e) { /* ignore */ }
                }

                }

                // 分区字号配置（key=state字段, valueId=显示元素, def=默认rem）
                const FONT_SLIDER_MAP = {
                    'font-size-conv': { key: 'fontSizeConv', valueId: 'font-conv-value', def: 0.85 },
                    'font-size-history': { key: 'fontSizeHistory', valueId: 'font-history-value', def: 0.65 },
                    'font-size-messages': { key: 'fontSizeMessages', valueId: 'font-messages-value', def: 0.75 },
                    'font-size-notes': { key: 'fontSizeNotes', valueId: 'font-notes-value', def: 0.75 },
                    'font-size-thinking': { key: 'fontSizeThinking', valueId: 'font-thinking-value', def: 0.7 },
                    'font-size-edit': { key: 'fontSizeEdit', valueId: 'font-edit-value', def: 0.9 },
                    'font-size-modal': { key: 'fontSizeModal', valueId: 'font-modal-value', def: 0.9 },
                    'font-size-popup': { key: 'fontSizePopup', valueId: 'font-popup-value', def: 0.85 },
                    'font-size-toast': { key: 'fontSizeToast', valueId: 'font-toast-value', def: 0.85 },
                };

                function loadFontSizeSettings() {
                    for (const [sliderId, cfg] of Object.entries(FONT_SLIDER_MAP)) {
                        const slider = document.getElementById(sliderId);
                        const valueEl = document.getElementById(cfg.valueId);
                        if (slider) {
                            const val = state.settings[cfg.key] ?? cfg.def;
                            slider.value = val;
                            if (valueEl) valueEl.textContent = val;
                            // 拖动滑块实时预览并更新状态（仅前端展示，保存时才落盘）
                            slider.addEventListener('input', function () {
                                const v = parseFloat(this.value);
                                state.settings[cfg.key] = v;
                                if (valueEl) valueEl.textContent = v;
                                applyFontSizes();
                            });
                        }
                    }
                    applyFontSizes();
                }

                function applyFontSizes() {
                    const root = document.documentElement.style;
                    for (const [sliderId, cfg] of Object.entries(FONT_SLIDER_MAP)) {
                        const v = state.settings[cfg.key] ?? cfg.def;
                        root.setProperty('--font-size-' + sliderId.replace('font-size-', ''), v + 'rem');
                    }
                }

                // 重置分区字号为小/中/大档（小=默认，每级递增 0.15）
                const FONT_TIER_OFFSET = { small: 0, medium: 0.15, large: 0.3 };
                function resetFontTier(level) {
                    const off = FONT_TIER_OFFSET[level] ?? 0;
                    for (const [sliderId, cfg] of Object.entries(FONT_SLIDER_MAP)) {
                        const v = Math.round((cfg.def + off) * 100) / 100;
                        state.settings[cfg.key] = v;
                        const slider = document.getElementById(sliderId);
                        const valueEl = document.getElementById(cfg.valueId);
                        if (slider) slider.value = v;
                        if (valueEl) valueEl.textContent = v;
                    }
                    applyFontSizes();
                    const btn = document.getElementById('reset-font-' + level + '-btn');
                    const name = { small: '小', medium: '中', large: '大' }[level] || level;
                    if (btn) showActionFeedback('已重置为' + name + '字号', 'success', btn);
                }

                function saveSettings() {
              state.settings.temperature = parseFloat(temperatureSlider.value);
              state.settings.contextLength = parseInt(contextSlider.value);
              state.settings.maxTokens = parseInt(maxTokensInput.value);
              if (expectedContextTokensInput) state.settings.expectedContextTokens = parseInt(expectedContextTokensInput.value) || 4000;

              // 保存超时保护三级（子智能体设置由「智能体配置」面板保存）
              const _tn = document.getElementById('timeout-nudge');
              const _tw = document.getElementById('timeout-warn');
              const _ta = document.getElementById('timeout-abort');
              if (_tn) state.settings.timeoutNudge = Math.max(0, parseInt(_tn.value) || 0);
              if (_tw) state.settings.timeoutWarn = Math.max(0, parseInt(_tw.value) || 0);
              if (_ta) state.settings.timeoutAbort = Math.max(0, parseInt(_ta.value) || 0);
            
              // 保存文件打开方式设置（三选一：以分段控件当前选中项为准）
              const fileModeGroup = document.getElementById('file-open-mode-group');
              if (fileModeGroup) {
                  const activeBtn = fileModeGroup.querySelector('.mode-seg-btn.active');
                  if (activeBtn && activeBtn.dataset.mode) {
                      state.settings.fileOpenMode = activeBtn.dataset.mode;
                  }
              }
            
              // 新增：原始输出过程开关
              const rawDataToggle = document.getElementById('raw-data-toggle');
              if (rawDataToggle) {
                  state.settings.showRawData = rawDataToggle.checked;
                  document.getElementById('raw-data-status').textContent = rawDataToggle.checked ? '开启' : '关闭';
              }

              // 保存分区字号设置（滑块拖动时已实时更新 state.settings，这里再兜底读取一次）
              const fontKeys = {
                  'font-size-conv': 'fontSizeConv',
                  'font-size-history': 'fontSizeHistory',
                  'font-size-messages': 'fontSizeMessages',
                  'font-size-notes': 'fontSizeNotes',
                  'font-size-thinking': 'fontSizeThinking',
                  'font-size-edit': 'fontSizeEdit',
                  'font-size-modal': 'fontSizeModal',
                  'font-size-popup': 'fontSizePopup',
                  'font-size-toast': 'fontSizeToast',
              };
              for (const [sliderId, key] of Object.entries(fontKeys)) {
                  const fs = document.getElementById(sliderId);
                  if (fs) state.settings[key] = parseFloat(fs.value);
              }
              applyFontSizes();

              // 知识库搜索模式（已废弃，纯向量搜索）
              setUserSetting('ai-settings', JSON.stringify(state.settings));

              // 保存 Neo4j 连接配置
              if (neo4jUri && neo4jUser && neo4jPassword) {
                  state.neo4j.uri = neo4jUri.value.trim();
                  state.neo4j.user = neo4jUser.value.trim();
                  state.neo4j.password = neo4jPassword.value;
                  setUserSetting('ragagent-neo4j-config', JSON.stringify(state.neo4j));
                  // 如果密码变了，断开重连
                  if (neo4jConnector && neo4jConnector.isConnected()) {
                      neo4jConnector.disconnect();
                      ensureNeo4jConnected();
                  }
              }

              showActionFeedback('设置已保存', 'success', saveSettingsBtn);

            updateTokenDisplay();  // 保存后立即刷新 Token 显示
            closeSettings();  // 立即收起设置面板（与智能体配置一致，避免 2 秒延迟）
        }

        function loadBranches() {
            const savedBranches = localStorage.getItem(window.identityKey('ai-branches'));
            if (savedBranches) {
                try {
                    const parsedBranches = JSON.parse(savedBranches);
                    for (const id in parsedBranches) {
                        if (!parsedBranches[id].notes) {
                            parsedBranches[id].notes = [];
                        }
                    }
                    state.branches = parsedBranches;
                    // 兼容清理：移除旧版本残留的“默认对话(main)”占位，彻底去掉该概念
                    if (state.branches['main']) {
                        delete state.branches['main'];
                    }
                    let maxCounter = 0;
                    for (const branchId in state.branches) {
                        if (branchId !== 'main' && branchId.startsWith('branch-')) {
                            const num = parseInt(branchId.replace('branch-', ''));
                            if (!isNaN(num) && num > maxCounter) {
                                maxCounter = num;
                            }
                        }
                    }
                    state.branchCounter = maxCounter + 1;
                } catch (e) {
                    console.error('解析分支数据失败:', e);
                }
            }
            // 恢复上次打开的对话（若存在且有效）；否则保持无对话(null)，让用户新建/选择
            const prevBranchId = localStorage.getItem(window.identityKey('ai-current-branch'));
            // 只恢复真实对话（branch-*），不创建任何“默认对话”占位
            if (prevBranchId && prevBranchId.startsWith('branch-')
                && state.branches[prevBranchId] && !state.branches[prevBranchId].isTemporary) {
                state.currentBranchId = prevBranchId;
            } else {
                state.currentBranchId = null;
            }
        }

             function saveBranches() {
                // 保存所有对话，不再区分临时/永久
                try {
                    localStorage.setItem(window.identityKey('ai-branches'), JSON.stringify(state.branches));
                } catch (e) {
                    // 存储超限等原因：不抛出，否则调用方（addMessage 等）会中断、消息气泡不渲染
                    console.error('保存对话到 localStorage 失败:', e);
                    if (typeof showToast === 'function') {
                        showToast('本地存储空间不足，最新对话可能未被保存；建议删除含大附件的旧对话', 'error');
                    }
                }
                // 同时记住当前打开的是哪个对话，便于重启后恢复
                persistCurrentBranch();
                // ★ 推到后端（用户 2026-09-26 定：对话必须存后端，换设备不能丢）。
                // 节流 1.5s，这里 18 个调用点会合并成一次推送。
                if (typeof setUserChats === 'function') setUserChats();
            }

        // 记住当前打开的对话 id，下次打开应用时恢复（避免每次重启都回默认对话）
        // main 是空占位：不持久化，防止它被当作“上次对话”而在刷新时永远回默认空对话
        function persistCurrentBranch() {
            if (state && state.currentBranchId && state.currentBranchId !== 'main') {
                try { localStorage.setItem(window.identityKey('ai-current-branch'), state.currentBranchId); } catch (e) {}
                // 切换对话也要同步（下次在别的设备打开时，停在同一个对话上）
                if (typeof setUserChats === 'function') setUserChats();
            }
        }

        // ==================== 对话管理功能 ====================
        function openRenameBranchModal(e, branchId = null) {
            const targetBranchId = branchId || state.currentBranchId;
            const branch = state.branches[targetBranchId];

            if (!branch) {
                showKeyStatus('对话不存在', 'error');
                return;
            }

            renameBranchNameInput.value = branch.name;
            renameBranchNameInput.dataset.branchId = targetBranchId;

            // 小浮层：定位到点击位置
            const panel = renameBranchModal;
            if (e) {
                panel.style.left = e.clientX + 'px';
                panel.style.top = e.clientY + 'px';
                requestAnimationFrame(() => {
                    const rect = panel.getBoundingClientRect();
                    if (rect.right > window.innerWidth) panel.style.left = (window.innerWidth - rect.width - 5) + 'px';
                    if (rect.bottom > window.innerHeight) panel.style.top = (window.innerHeight - rect.height - 5) + 'px';
                });
            }
            panel.classList.add('show');
            renameBranchNameInput.focus();
        }

        function closeRenameBranchModal() {
            renameBranchModal.classList.remove('show');
            if (renameBranchNameInput.dataset.branchId) {
                delete renameBranchNameInput.dataset.branchId;
            }
        }

        function renameCurrentBranch() {
            const branchId = renameBranchNameInput.dataset.branchId || state.currentBranchId;
            const newName = renameBranchNameInput.value.trim();
            const branch = state.branches[branchId];

            if (!newName) {
                showKeyStatus('请输入新的对话名称', 'error');
                return;
            }

            if (newName === branch.name) {
                showKeyStatus('名称未改变', 'warning');
                closeRenameBranchModal();
                return;
            }

            const oldName = branch.name;
            branch.name = newName;

            saveBranches();
            updateHistoryList();
            closeRenameBranchModal();

            // 只保留一个反馈：showKeyStatus 已更新状态文字 + 弹被动轻提示，避免重复弹两个
            showKeyStatus(`已从"${oldName}"重命名为"${newName}"`, 'success');
        }

        // ==================== 删除分支功能（原生确认） ====================
        function deleteBranchWithConfirm(branchId = null) {
            const targetBranchId = branchId || state.currentBranchId;
            const branch = state.branches[targetBranchId];

            if (!branch) {
                showKeyStatus('对话不存在', 'error');
                return;
            }

            const branchName = branch.name;
            if (!confirm(`确定要删除对话 "${branchName}" 吗？\n此操作不可恢复，将永久删除该对话所有记录、关联的后端附件和 Token 统计。`)) return;

            // 删除该对话所有后端文件（消息附件 + 气泡文件）
            var allFileIds = [];
            if (branch.messages) {
                branch.messages.forEach(function(msg) {
                    if (msg._fileIds) msg._fileIds.forEach(function(f) { allFileIds.push(f); });
                    if (msg.attachments) msg.attachments.forEach(function(a) {
                        if (a.file_id) allFileIds.push(a.file_id);
                    });
                });
            }
            // 气泡文件（如果有 bubble 系统）
            if (branch._bubbles) {
                branch._bubbles.forEach(function(bub) {
                    if (bub.fileId) allFileIds.push(bub.fileId);
                });
            }
            // 去重并删除
            var uniqueIds = [...new Set(allFileIds)];
            uniqueIds.forEach(function(fid) {
                fetch('/api/file/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_id: fid })
                }).catch(function(e) {
                    console.warn('[DeleteBranch] Failed to remove file:', fid, e.message);
                });
            });

            // 如果删除的是当前对话，切换到其他对话
            if (targetBranchId === state.currentBranchId) {
                // 找到第一个可用的对话
                let nextBranchId = null;
                for (const id in state.branches) {
                    if (id !== targetBranchId && !state.branches[id].isTemporary) {
                        nextBranchId = id;
                        break;
                    }
                }

                // 如果没有其他对话，保持无对话空态（不自动新建，直接置 null）
                if (!nextBranchId) {
                    state.currentBranchId = null;
                    persistCurrentBranch();
                    renderMessages();
                    updateTokenDisplay();
                    updateHistoryList();
                } else {
                    state.currentBranchId = nextBranchId;
                    persistCurrentBranch();
                    renderMessages();
                    updateTokenDisplay();
                    // 如果笔记面板打开，同步更新
                    syncNotesPanel();
                }
            }

            delete state.branches[targetBranchId];
            saveBranches();
            updateHistoryList();

            // 只保留一个删除反馈：showKeyStatus 已同时更新状态文字 + 弹被动轻提示，
            // 此处不再重复 addSystemMessageWithDelete（避免弹两个微提示）
            showKeyStatus(`"${branchName}" 已成功删除`, 'success');
        }

        // ==================== 导出对话功能 ====================
        function exportConversation(branchId = null) {
            const targetBranchId = branchId || state.currentBranchId;
            const branch = state.branches[targetBranchId];

            const chatData = {
                meta: {
                    exportDate: new Date().toISOString(),
                    model: state.currentModel,
                    branch: branch.name,
                    branchId: branch.id,
                    totalTokens: branch.totalTokens
                },
                settings: state.settings,
                messages: branch.messages
            };

            const dataStr = JSON.stringify(chatData, null, 2);
            const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);

            const exportFileDefaultName = `RAGAgent-${branch.name}-${new Date().toISOString().split('T')[0]}.json`;

            const linkElement = document.createElement('a');
            linkElement.setAttribute('href', dataUri);
            linkElement.setAttribute('download', exportFileDefaultName);
            linkElement.click();

            showKeyStatus('对话已导出', 'success');
        }

        // ==================== 模型选择与管理 ====================
