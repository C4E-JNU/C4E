        // ==================== 服务商与模型配置 ====================
        // 内置只给「服务商 + 官方端点 + 简介」；模型列表一律清空，由用户自行添加。
        // 例外：qwen-local（隐藏内置，写死可用）与 hermes/codex（Agent 网关，预填免配置）。
        const defaultProviders = {
            'deepseek': {
                id: 'deepseek', name: 'DeepSeek',
                defaultEndpoint: 'https://api.deepseek.com/v1',
                desc: t('prov.desc.deepseek'),
                models: []
            },
            'openai': {
                id: 'openai', name: 'OpenAI',
                defaultEndpoint: 'https://api.openai.com/v1',
                desc: t('prov.desc.openai'),
                models: []
            },
            'alibaba': {
                                id: 'alibaba', name: 'Alibaba Cloud Bailian',
                defaultEndpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: t('prov.desc.alibaba'),
                models: []
            },
            'glm': {
                                id: 'glm', name: 'Zhipu GLM',
                defaultEndpoint: 'https://open.bigmodel.cn/api/paas/v4',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: t('prov.desc.glm'),
                models: []
            },
            'google': {
                id: 'google', name: 'Google',
                defaultEndpoint: 'https://generativelanguage.googleapis.com/v1beta/openai',
                desc: t('prov.desc.google'),
                models: []
            },
            'xai': {
                id: 'xai', name: 'xAI',
                defaultEndpoint: 'https://api.x.ai/v1',
                desc: t('prov.desc.xai'),
                models: []
            },
            'anthropic': {
                id: 'anthropic', name: 'Anthropic',
                defaultEndpoint: 'https://api.anthropic.com/v1',
                desc: t('prov.desc.anthropic'),
                models: []
            },
            'qwen-local': {
                                id: 'qwen-local', name: 'Local Qwen', hidden: true, type: 'openai',
                defaultEndpoint: 'http://<HUB-IP>:8080/v1',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: t('prov.desc.qwenLocal'),
                models: [
                                        { id: 'qwen3-local', name: 'Qwen3.6 35B (local)', desc: t('prov.desc.qwenLocalModel'), modelParam: 'Qwen3.6-35B-A3B-Q4_K_M.gguf', enableReasoning: false }
                ]
            },
            'kimi': {
                                id: 'kimi', name: 'Kimi (Moonshot)',
                defaultEndpoint: 'https://api.moonshot.cn/v1',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: t('prov.desc.kimi'),
                models: []
            },
            'hermes': {
                id: 'hermes', name: 'Hermes', type: 'openai',
                defaultEndpoint: '',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: t('prov.desc.gateway'),
                models: []
            },
            'codex': {
                id: 'codex', name: 'Codex', type: 'openai',
                defaultEndpoint: '',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: t('prov.desc.gateway'),
                models: []
            }
            };

        // 运行时状态
        let providers = {};
        let providerSettings = {}; // { providerId: { apiKey: '', endpoint: '' } }
        let modelConfig = {}; // 兼容旧代码的扁平化配置

        async function _dbGetFile(key) {
            try { return await localforage.getItem('_bub_' + key); } catch { return null; }
        }
        // ==================== END 气泡文件存储 ====================

        // 初始化服务商系统
        function initProviderSystem() {
            // 1. 加载设置
             const savedSettings = localStorage.getItem('ragagent-provider-settings');
             if (savedSettings) {
                 providerSettings = JSON.parse(savedSettings);
             }

              // Hermes / Codex 等 Agent 网关：URL、密钥、模型均不内置——
              // 用户在配置界面自行填写，保存后写 localStorage（本地缓存）+ 同步到本人账号
              // （/api/user/settings → workspace/users/<身份>/settings.json，见 js/user_settings.js）。

              // 本地千问（实验室 llama-server，免密钥内置可用）；旧存档 IP 端点自动迁移到 c4eai.lab
              if (!providerSettings['qwen-local']) {
                  providerSettings['qwen-local'] = {
                      apiKey: 'sk-local',
                      endpoint: 'http://<HUB-IP>:8080/v1'
                  };
              } else if ((providerSettings['qwen-local'].endpoint || '').indexOf('c4eai') >= 0) {
                  providerSettings['qwen-local'].endpoint = 'http://<HUB-IP>:8080/v1';
                  providerSettings['qwen-local'].apiKey = providerSettings['qwen-local'].apiKey || 'sk-local';
                  setUserSetting('ragagent-provider-settings', JSON.stringify(providerSettings));
              }

              // 本地千问写死可用：确保 qwen3-local 始终在“已亮起”列表
              if (!state.enabledModels.includes('qwen3-local')) {
                  state.enabledModels.unshift('qwen3-local');
                  setUserSetting('ragagent-enabled-models', JSON.stringify(state.enabledModels));
              }

            // 2. 加载自定义服务商和模型覆盖项
            const savedCustomProviders = localStorage.getItem('ragagent-custom-providers');
            let customProviders = {};
            if (savedCustomProviders) {
                customProviders = JSON.parse(savedCustomProviders);
            }

            const savedOverrides = localStorage.getItem('ragagent-provider-overrides');
            let providerOverrides = {};
            if (savedOverrides) {
                providerOverrides = JSON.parse(savedOverrides);
            }

            // 3. 合并构建 providers
            providers = JSON.parse(JSON.stringify(defaultProviders));

            // 应用内置服务商的覆盖（增加模型）
            // 注意：不再应用 deletedModels，因为系统模型现在不允许删除/隐藏
            for (const [providerId, overrides] of Object.entries(providerOverrides)) {
                if (providers[providerId]) {
                    if (overrides.addedModels) {
                        providers[providerId].models = providers[providerId].models.concat(overrides.addedModels);
                    }
                }
            }

            // 合并完全自定义的服务商
            Object.assign(providers, customProviders);

            // 4. 重建 modelConfig 兼容层
            rebuildModelConfig();

            // 5. 渲染UI
            renderProviderUI();
        }

        function rebuildModelConfig() {
            modelConfig = {};
            state.apiKeys = {};

            for (const [providerId, provider] of Object.entries(providers)) {
                const settings = providerSettings[providerId] || {};
                const endpoint = normalizeOpenAIEndpoint(settings.endpoint || provider.defaultEndpoint);
                const apiKey = settings.apiKey || '';

                provider.models.forEach(model => {
                    modelConfig[model.id] = {
                        name: model.name,
                        endpoint: endpoint,
                        modelParam: model.modelParam,
                        type: provider.type || 'openai', // Default to openai
                        apiKeyHeader: provider.apiKeyHeader,
                        apiKeyPrefix: provider.apiKeyPrefix,
                        supportsThinking: model.enableReasoning || false // fix naming
                    };
                    state.apiKeys[model.id] = apiKey; // 兼容旧的 apiKey 获取方式
                });
            }
        }

        function saveProviderSettings() {
            setUserSetting('ragagent-provider-settings', JSON.stringify(providerSettings));
            rebuildModelConfig();
            updateChatModelSelector(); // 更新聊天输入框的模型下拉
        }

        function saveCustomProviders(customProviders) {
            setUserSetting('ragagent-custom-providers', JSON.stringify(customProviders));
            //initProviderSystem(); // Reload
        }

        function normalizeOpenAIEndpoint(endpoint) {
            const raw = (endpoint || '').trim();
            if (!raw) return '';

            const lower = raw.toLowerCase();
            if (lower.endsWith('/chat/completions') || lower.endsWith('/responses') || lower.endsWith('/messages')) {
                return raw;
            }

            const withoutTrailingSlash = raw.replace(/\/+$/, '');
            const normalizedLower = withoutTrailingSlash.toLowerCase();

            if (normalizedLower.endsWith('/v1')) {
                return `${withoutTrailingSlash}/chat/completions`;
            }

            try {
                const url = new URL(withoutTrailingSlash);
                if (!url.pathname || url.pathname === '/') {
                    url.pathname = '/v1/chat/completions';
                    return url.toString();
                }
            } catch (e) {
                // Keep non-URL values unchanged so existing custom endpoints are not broken.
            }

            return withoutTrailingSlash;
        }

        // ==================== 初始化 ====================
