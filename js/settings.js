        // ==================== 服务商与模型配置 ====================
        // 内置只给「服务商 + 官方端点 + 简介」；模型列表一律清空，由用户自行添加。
        // 例外：qwen-local（隐藏内置，写死可用）与 hermes/codex（Agent 网关，预填免配置）。
        const defaultProviders = {
            'deepseek': {
                id: 'deepseek', name: 'DeepSeek',
                defaultEndpoint: 'https://api.deepseek.com/v1',
                desc: '深度求索官方 API｜通用对话与代码见长，性价比高',
                models: []
            },
            'openai': {
                id: 'openai', name: 'OpenAI',
                defaultEndpoint: 'https://api.openai.com/v1',
                desc: 'OpenAI 官方 API｜GPT 系列，多模态与生态最全',
                models: []
            },
            'alibaba': {
                id: 'alibaba', name: '阿里云百炼',
                defaultEndpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: '通义千问 Qwen 官方 API｜中文与长文本能力强，额度友好',
                models: []
            },
            'glm': {
                id: 'glm', name: '智谱 GLM',
                defaultEndpoint: 'https://open.bigmodel.cn/api/paas/v4',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: '智谱 AI 官方 API｜GLM 系列，Agent 与代码能力突出',
                models: []
            },
            'google': {
                id: 'google', name: 'Google',
                defaultEndpoint: 'https://generativelanguage.googleapis.com/v1beta/openai',
                desc: 'Gemini 官方 API（OpenAI 兼容端点）｜多模态旗舰',
                models: []
            },
            'xai': {
                id: 'xai', name: 'xAI',
                defaultEndpoint: 'https://api.x.ai/v1',
                desc: 'Grok 系列官方 API｜实时联网与强推理',
                models: []
            },
            'anthropic': {
                id: 'anthropic', name: 'Anthropic',
                defaultEndpoint: 'https://api.anthropic.com/v1',
                desc: 'Claude 系列官方 API｜长文本与复杂推理',
                models: []
            },
            'qwen-local': {
                id: 'qwen-local', name: '本地千问', hidden: true, type: 'openai',
                defaultEndpoint: '',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: '实验室服务器本地推理｜免费不限量，内置无需配置',
                models: [
                    { id: 'qwen3-local', name: 'Qwen3.6 35B（本地）', desc: '实验室服务器本地推理，免费不限量，已内置无需配置', modelParam: 'Qwen3.6-35B-A3B-Q4_K_M.gguf', enableReasoning: false }
                ]
            },
            'hermes': {
                id: 'hermes', name: 'Hermes', type: 'openai',
                defaultEndpoint: '',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: 'Agent 网关：可通过 agent gateway 连接任意主流 agent 到 C4EAI 进行回答，数据保存在您的浏览器前端，我们不获取您的任何隐私信息。注意：使用外部 agent 连接时，C4EAI 自身的工具与技能不可用，仅可纯问答。',
                models: []
            },
            'codex': {
                id: 'codex', name: 'Codex', type: 'openai',
                defaultEndpoint: '',
                apiKeyHeader: 'Authorization', apiKeyPrefix: 'Bearer ',
                desc: 'Agent 网关：可通过 agent gateway 连接任意主流 agent 到 C4EAI 进行回答，数据保存在您的浏览器前端，我们不获取您的任何隐私信息。注意：使用外部 agent 连接时，C4EAI 自身的工具与技能不可用，仅可纯问答。',
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

              // 本地千问（自建 llama-server，通常免密钥）：地址不内置，未配过则留空
              if (!providerSettings['qwen-local']) {
                  providerSettings['qwen-local'] = {
                      apiKey: 'sk-local',
                      endpoint: ''
                  };
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
