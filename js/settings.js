        // ==================== 服务商与模型配置 ====================
        const defaultProviders = {
            'deepseek': {
                id: 'deepseek',
                name: 'DeepSeek',
                defaultEndpoint: 'https://api.deepseek.com/chat/completions',
                models: [
                    { id: 'deepseek-chat', name: 'DeepSeek Chat', desc: '通用对话模型，适合日常对话、编程辅助，仅支持文本输入与输出', modelParam: 'deepseek-chat' },
                    { id: 'deepseek-reasoner', name: 'DeepSeek R1', desc: '推理增强模型，逻辑推理能力强，仅支持文本输入与输出', modelParam: 'deepseek-reasoner', enableReasoning: true }
                ]
            },
            'openai': {
                id: 'openai',
                name: 'OpenAI',
                defaultEndpoint: 'https://api.openai.com/v1/chat/completions',
                models: [
                    { id: 'gpt-4o', name: 'GPT-4o', desc: '高性能多模态模型', modelParam: 'gpt-4o' },
                    { id: 'gpt-5-mini', name: 'GPT-5 Mini', desc: '轻量级快速模型', modelParam: 'gpt-5-mini' },
                    { id: 'gpt-5.2', name: 'GPT-5.2', desc: '增强版推理模型', modelParam: 'gpt-5.2' }
                ]
            },
            'alibaba': {
                id: 'alibaba',
                name: 'Alibaba',
                defaultEndpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                apiKeyHeader: 'Authorization',
                apiKeyPrefix: 'Bearer ',
                models: [
                    { id: 'qwen-3.0', name: 'Qwen 3.0', desc: '强大的中文理解和代码生成', modelParam: 'qwen-plus' },
                    { id: 'qwen3-flash', name: 'Qwen3 Flash', desc: '高速推理模型', modelParam: 'qwen3-flash' }
                ]
            },
            'google': {
                id: 'google',
                name: 'Google',
                defaultEndpoint: 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
                models: [
                    { id: 'gemini-3-flash-preview', name: 'Gemini 3 Flash', desc: '新一代极速多模态模型', modelParam: 'gemini-3-flash-preview' },
                    { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', desc: '高性能复杂任务处理', modelParam: 'gemini-2.5-pro' },
                    { id: 'gemini-2.5-flash-lite', name: 'Gemini 2.5 Flash Lite', desc: '轻量化极速响应模型', modelParam: 'gemini-2.5-flash-lite' },
                    { id: 'gemini-2.5-flash-native-audio-preview-12-2025', name: 'Gemini 2.5 Flash Live', desc: '支持原生音频输入与实时互动', modelParam: 'gemini-2.5-flash-native-audio-preview-12-2025' },
                    { id: 'gemma-3-27b-it', name: 'Gemma 3', desc: '高性能开源模型', modelParam: 'gemma-3-27b-it' },
                    { id: 'gemini-3-pro-preview', name: 'Gemini 3 Pro', desc: '新一代推理与多模态旗舰', modelParam: 'gemini-3-pro-preview' },
                    { id: 'gemini-3-pro-image-preview', name: 'Nano Banana Pro', desc: '新一代视觉理解与图像生成预览版', modelParam: 'gemini-3-pro-image-preview' },
                    { id: 'gemini-2.5-computer-use-preview-10-2025', name: 'Gemini 2.5 Computer Use', desc: '支持计算机操控任务的预览模型', modelParam: 'gemini-2.5-computer-use-preview-10-2025' }
                ]
            },
            'xai': {
                id: 'xai',
                name: 'xAI',
                defaultEndpoint: 'https://api.x.ai/v1/responses',
                models: [
                    { id: 'grok-4.1', name: 'Grok 4.1', desc: '具有强推理能力和幽默感', modelParam: 'grok-4.1' }
                ]
            },
            'anthropic': {
                id: 'anthropic',
                name: 'Anthropic',
                defaultEndpoint: 'https://api.anthropic.com/v1/messages',
                models: [
                    { id: 'claude-4.5-opus', name: 'Claude 4.5 Opus', desc: '长文本与复杂推理', modelParam: 'claude-4.5-opus' }
                ]
            },
            'meta': {
                 id: 'meta',
                 name: 'Meta',
                 defaultEndpoint: 'https://api.openai.com/v1/chat/completions',
                 models: [
                     { id: 'llama-4', name: 'Llama 4', desc: '开源大模型最新版', modelParam: 'llama-4' }
                 ]
             },
             'hermes': {
                 id: 'hermes',
                 name: 'Hermes',
                 type: 'openai',
                 defaultEndpoint: 'http://localhost:8642/v1',
                 apiKeyHeader: 'Authorization',
                 apiKeyPrefix: 'Bearer ',
                 models: [
                     { id: 'hermes-qwen3', name: 'Hermes (qwen3)', desc: '通过 Hermes Gateway 运行的本地 Agent，支持工具调用和持久记忆', modelParam: 'qwen3', enableReasoning: false }
                 ]
             }
            };

        // 运行时状态
        let providers = {};
        let providerSettings = {}; // { providerId: { apiKey: '', endpoint: '' } }
        let modelConfig = {}; // 兼容旧代码的扁平化配置

        // ==================== 气泡文件数据存储（IndexedDB，无大小限制） ====================
        async function _dbStoreFile(key, data) {
            try { await localforage.setItem('_bub_' + key, data); } catch (e) { console.error('文件存储失败', e); }
        }
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

             // 预填 Hermes Gateway API Key（用户未保存设置时自动填充）
             if (!providerSettings['hermes']) {
                 providerSettings['hermes'] = {
                     apiKey: 'hermes-api-key-2026-local',
                     endpoint: 'http://localhost:8642/v1'
                 };
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
            localStorage.setItem('ragagent-provider-settings', JSON.stringify(providerSettings));
            rebuildModelConfig();
            updateChatModelSelector(); // 更新聊天输入框的模型下拉
        }

        function saveCustomProviders(customProviders) {
            localStorage.setItem('ragagent-custom-providers', JSON.stringify(customProviders));
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
