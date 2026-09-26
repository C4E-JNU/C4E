        // ==================== 模型选择与管理 ====================
        // ==================== 模型选择与管理 ====================
        function selectModel(modelId) {
            state.currentModel = modelId;
            // 保存到 localStorage，下次启动恢复
            try { setUserSetting('ai-current-model', modelId); } catch(e) {}

            // 1. 更新新版UI的高亮状态
            // 重新渲染以更新高亮
            // 如果在当前显示的provider里
            if (providers[currentEditingProviderId] && providers[currentEditingProviderId].models.find(m => m.id === modelId)) {
                renderProviderConfig(currentEditingProviderId);
            }

            // 2. 更新下方聊天输入框的模型选择器
            updateChatModelSelector();

            // 3. 检查API Key
            const providerKey = state.apiKeys[modelId]; // state.apiKeys 现在通过 rebuildModelConfig 同步

            const hasKey = !!providerKey;
            messageInput.disabled = !hasKey;
            setSendBtnDisabled(!hasKey);

            if (!hasKey) {
                messageInput.placeholder = `请在上方为 [${modelConfig[modelId]?.name || modelId}] 配置API密钥并“亮起”模型`;
            } else {
                messageInput.placeholder = '发消息...';
                // messageInput.focus();
            }
        }

        // ==================== 新版服务商系统逻辑 ====================
        let currentEditingProviderId = 'deepseek';

        function renderProviderUI() {
            const providerList = document.getElementById('provider-list');
            if (!providerList) return;
            providerList.innerHTML = '';

            // 1. 渲染服务商列表 (内置 + 自定义；hidden 的内置服务商如"本地千问"不显示但可用)
            Object.values(providers).filter(p => !p.hidden).forEach(provider => {
                const item = document.createElement('div');
                item.className = `provider-item ${provider.id === currentEditingProviderId ? 'active' : ''}`;
                item.onclick = () => {
                    currentEditingProviderId = provider.id;
                    renderProviderUI(); // 刷新高亮
                    renderProviderConfig(provider.id);
                };
                item.innerHTML = `
                    <div class="provider-icon-placeholder">●</div>
                    <span>${provider.name}</span>
                `;
                providerList.appendChild(item);
            });

            // 2. 添加自定义按钮
            const addBtn = document.createElement('div');
            addBtn.className = 'provider-item';
            addBtn.innerHTML = `<div class="provider-icon-placeholder" style="border:1px solid var(--gray);">+</div><span>添加服务商</span>`;
            addBtn.onclick = () => {
                document.getElementById('add-provider-modal').style.display = 'flex';
            };
            providerList.appendChild(addBtn);

            // 3. 渲染当前选中的配置
            renderProviderConfig(currentEditingProviderId);

            // 4. 绑定事件 (只需要绑定一次，或者每次渲染都重新绑定)
            setupProviderEvents();
        }

        function renderProviderConfig(providerId) {
            const container = document.getElementById('provider-config-area');
            if (!container) return;

            const provider = providers[providerId];
            if (!provider) return;

            // 头部
            const nameEl = document.getElementById('current-provider-name');
            const iconEl = document.getElementById('current-provider-icon');
            if (nameEl) nameEl.innerHTML = `${provider.name}` +
                (provider.desc ? `<div style="font-weight:400;font-size:.75rem;color:var(--gray);margin-top:2px;white-space:normal;">${provider.desc}</div>` : '');
            if (iconEl) iconEl.innerText = '⚙️';

            // 配置回填
            const settings = providerSettings[providerId] || {};
            const effectiveUrl = settings.endpoint || provider.defaultEndpoint || '';
            const effectiveKey = settings.apiKey || '';

            const urlInput = document.getElementById('provider-endpoint');
            const keyInput = document.getElementById('provider-apikey');

            if (urlInput) urlInput.value = effectiveUrl;
            if (keyInput) keyInput.value = effectiveKey;

            // 渲染模型列表
            const listContainer = document.getElementById('provider-models-list');
            if (listContainer) {
                listContainer.innerHTML = '';
                if (!provider.models || provider.models.length === 0) {
                    const hint = document.createElement('div');
                    hint.style.cssText = 'font-size:.78rem;color:var(--gray);padding:8px 4px;';
                    hint.textContent = '在上方配置好 URL 与 API 密钥并保存后，点击「+ 添加模型」添加该服务商支持的模型，点亮即可使用。';
                    listContainer.appendChild(hint);
                }
                if (provider.models) {
                    provider.models.forEach(model => {
                        const card = document.createElement('div');
                        const isActivated = state.enabledModels.includes(model.id);
                        const isCurrent = state.currentModel === model.id;

                        // 判断是否为系统内置模型 (内置服务商且在该服务商的原始模型列表中)
                        const isBuiltInModel = defaultProviders[providerId] &&
                            defaultProviders[providerId].models.some(m => m.id === model.id);

                        card.className = `model-select-item ${isActivated ? 'activated' : ''} ${isCurrent ? 'active' : ''}`;
                        card.innerHTML = `
                            <div class="model-select-name">${model.name}</div>
                            <div class="model-select-id">${model.id}</div>
                            <div style="font-size: 0.8rem; color: var(--gray); margin-top:4px;">${model.desc || ''}</div>
                            ${isBuiltInModel ? '' : '<button class="model-delete-btn" title="删除此模型">×</button>'}
                        `;

                        // 点击卡片切换“亮起”（激活）状态
                        card.onclick = (e) => {
                            if (e.target.classList.contains('model-delete-btn')) return;
                            toggleModelActivation(model.id);
                        };

                        // 删除模型按钮逻辑 (仅非内置模型)
                        const delBtn = card.querySelector('.model-delete-btn');
                        if (delBtn) {
                            delBtn.onclick = (e) => {
                                e.stopPropagation();
                                deleteModel(providerId, model.id);
                            };
                        }

                        listContainer.appendChild(card);
                    });
                }

                // 添加模型按钮
                const addModelBtn = document.createElement('button');
                addModelBtn.className = 'model-select-item add-model-btn';
                addModelBtn.innerHTML = `<span>+</span> 添加模型`;
                addModelBtn.onclick = () => {
                    document.getElementById('add-custom-modal').style.display = 'flex';
                };
                listContainer.appendChild(addModelBtn);
            }

            // 删除服务商按钮状态
            const deleteBtn = document.getElementById('delete-provider-btn');
            if (deleteBtn) {
                // 只有自定义服务商可以删除
                if (defaultProviders[providerId]) {
                    deleteBtn.style.display = 'none';
                } else {
                    deleteBtn.style.display = 'block';
                    deleteBtn.onclick = () => deleteProvider(providerId);
                }
            }
        }

        function toggleModelActivation(modelId) {
            const index = state.enabledModels.indexOf(modelId);
            if (index > -1) {
                state.enabledModels.splice(index, 1);
            } else {
                state.enabledModels.push(modelId);
            }

            // 同步 state.currentModel
            if (state.currentModel === modelId && index > -1) {
                // 取消激活了当前选中的模型 -> 自动挑选下一个可用的
                selectModel(state.enabledModels.length > 0 ? state.enabledModels[0] : null);
            } else if (!state.currentModel && state.enabledModels.length > 0) {
                // 原本没选模型，现在亮起了第一个 -> 选中它
                selectModel(modelId);
            } else if (state.currentModel === modelId && index === -1) {
                // 已选中的模型重新亮起
                selectModel(modelId);
            }

            // 始终重新渲染当前配置区和下拉列表，确保最后一个处于熄灭状态时UI也能立即更新
            renderProviderConfig(currentEditingProviderId);
            updateChatModelSelector();

            setUserSetting('ragagent-enabled-models', JSON.stringify(state.enabledModels));
        }

        function deleteModel(providerId, modelId) {
            if (!confirm(`确定要删除模型 ${modelId} 吗？`)) return;

            // 1. 获取覆盖记录
            const savedOverrides = localStorage.getItem('ragagent-provider-overrides');
            let providerOverrides = savedOverrides ? JSON.parse(savedOverrides) : {};

            if (!providerOverrides[providerId]) providerOverrides[providerId] = { addedModels: [], deletedModels: [] };

            // 2. 检查是否为内置模型
            const isBuiltIn = defaultProviders[providerId] && defaultProviders[providerId].models.some(m => m.id === modelId);

            if (isBuiltIn) {
                // 如果是内置模型，不执行删除（理论上UI已隐藏按钮）
                return;
            }

            // 3. 处理增加的模型或自定义服务商模型
            if (defaultProviders[providerId]) {
                // 内置服务商中的新增模型 -> 从 addedModels 中移除
                providerOverrides[providerId].addedModels = (providerOverrides[providerId].addedModels || []).filter(m => m.id !== modelId);
            } else {
                // 如果是完全自定义的服务商，从 customProviders 中移除
                const savedCustom = localStorage.getItem('ragagent-custom-providers');
                if (savedCustom) {
                    let customProviders = JSON.parse(savedCustom);
                    if (customProviders[providerId]) {
                        customProviders[providerId].models = (customProviders[providerId].models || []).filter(m => m.id !== modelId);
                        saveCustomProviders(customProviders);
                    }
                }
            }

            setUserSetting('ragagent-provider-overrides', JSON.stringify(providerOverrides));

            // 如果在已激活列表中，也要移除
            const enabledIdx = state.enabledModels.indexOf(modelId);
            if (enabledIdx > -1) {
                state.enabledModels.splice(enabledIdx, 1);
                setUserSetting('ragagent-enabled-models', JSON.stringify(state.enabledModels));
            }

            initProviderSystem(); // 重新加载
        }

        function setupProviderEvents() {
            // 保存配置按钮
            const saveBtn = document.getElementById('save-provider-config');
            if (saveBtn) {
                saveBtn.onclick = () => {
                    const urlInput = document.getElementById('provider-endpoint');
                    const keyInput = document.getElementById('provider-apikey');

                    if (!providerSettings[currentEditingProviderId]) {
                        providerSettings[currentEditingProviderId] = {};
                    }

                    providerSettings[currentEditingProviderId].endpoint = normalizeOpenAIEndpoint(urlInput.value);
                    providerSettings[currentEditingProviderId].apiKey = keyInput.value.trim();

                    saveProviderSettings();

                    // 提供按钮反馈
                    const originalText = saveBtn.innerText;
                    saveBtn.innerText = '✅ 已保存';
                    saveBtn.classList.add('success-state');
                    setTimeout(() => {
                        saveBtn.innerText = originalText;
                        saveBtn.classList.remove('success-state');
                    }, 2000);

                    showKeyStatus('配置已保存（已同步到你的账号）', 'success');

                    // 如果当前选择的是这个服务商下的模型，更新状态
                    if (state.currentModel && providers[currentEditingProviderId].models.find(m => m.id === state.currentModel)) {
                        selectModel(state.currentModel);
                    }
                };
            }

            // 添加服务商 确认/取消
            document.getElementById('confirm-add-provider').onclick = () => {
                const name = document.getElementById('new-provider-name').value.trim();
                const url = normalizeOpenAIEndpoint(document.getElementById('new-provider-url').value);
                const descEl = document.getElementById('new-provider-desc');
                const desc = descEl ? descEl.value.trim() : '';
                if (!name) return showPassiveToast('请输入服务商名称');

                const id = 'custom-' + Date.now();
                providers[id] = {
                    id: id,
                    name: name,
                    icon: 'C',
                    desc: desc,
                    defaultEndpoint: url || '',
                    models: []
                };

                // Save custom provider definition
                let customProv = {};
                const saved = localStorage.getItem('ragagent-custom-providers');
                if (saved) customProv = JSON.parse(saved);
                customProv[id] = providers[id];
                saveCustomProviders(customProv);

                rebuildModelConfig();

                currentEditingProviderId = id;
                renderProviderUI();
                document.getElementById('add-provider-modal').style.display = 'none';
            };

            document.getElementById('cancel-add-provider').onclick = () => {
                document.getElementById('add-provider-modal').style.display = 'none';
            };

            // 添加模型 确认/取消
            document.getElementById('confirm-add-model').onclick = () => {
                const name = document.getElementById('custom-model-name').value.trim();
                const modelId = document.getElementById('custom-model-id').value.trim();
                const desc = document.getElementById('custom-model-desc').value.trim();
                if (!name || !modelId) return showPassiveToast('请填写完整信息');

                const newModel = {
                    id: modelId,
                    name: name,
                    modelParam: modelId,
                    desc: desc || '自定义模型'
                };

                // 1. 如果是自定义服务商，修改 customProviders 并保存
                if (!defaultProviders[currentEditingProviderId]) {
                    const savedCustom = localStorage.getItem('ragagent-custom-providers');
                    if (savedCustom) {
                        let customProviders = JSON.parse(savedCustom);
                        if (customProviders[currentEditingProviderId]) {
                            if (!customProviders[currentEditingProviderId].models) customProviders[currentEditingProviderId].models = [];
                            customProviders[currentEditingProviderId].models.push(newModel);
                            saveCustomProviders(customProviders);
                        }
                    }
                } else {
                    // 2. 如果是内置服务商，保存到覆盖项 overrides
                    const savedOverrides = localStorage.getItem('ragagent-provider-overrides');
                    let providerOverrides = savedOverrides ? JSON.parse(savedOverrides) : {};

                    if (!providerOverrides[currentEditingProviderId]) {
                        providerOverrides[currentEditingProviderId] = { addedModels: [], deletedModels: [] };
                    }
                    providerOverrides[currentEditingProviderId].addedModels.push(newModel);
                    setUserSetting('ragagent-provider-overrides', JSON.stringify(providerOverrides));
                }

                initProviderSystem(); // 重新加载数据和UI
                document.getElementById('add-custom-modal').style.display = 'none';

                // 清空输入框以便下次使用
                document.getElementById('custom-model-name').value = '';
                document.getElementById('custom-model-id').value = '';
                document.getElementById('custom-model-desc').value = '';

                // 自动亮起新添加的模型
                toggleModelActivation(modelId);
            };

            document.getElementById('cancel-add-model').onclick = () => {
                document.getElementById('add-custom-modal').style.display = 'none';
            };
        }

        function deleteProvider(providerId) {
            if (!confirm('确定要删除这个服务商吗？')) return;

            delete providers[providerId];
            delete providerSettings[providerId];

            // Update storage
            let customProv = {};
            const saved = localStorage.getItem('ragagent-custom-providers');
            if (saved) customProv = JSON.parse(saved);
            delete customProv[providerId];
            saveCustomProviders(customProv);

            saveProviderSettings();

            currentEditingProviderId = Object.keys(providers)[0];
            renderProviderUI();
        }

        /* 移除旧的API密钥加载函数，使用 initProviderSystem 替代 */

        function updateModelStatus() {
            // 主要是用于更新旧版卡片状态，新版UI在 renderProviderUI 中处理状态
            // 可以留空或者用来更新其他全局状态
        }

        // ==================== 消息管理 ====================
