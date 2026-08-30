        function extractReasoningFromContent(content) {
            const patterns = [
                /(思考过程|推理过程|思考步骤|让我先思考一下)[\s\S]*?(答案|最终答案|结论|所以)/i,
                /([\s\S]*?)\n\n(?:答案|结论|最终答案|因此|所以)[:：]?\s*/i,
                /(首先|第一步|首先分析|让我来分析)[\s\S]*?(因此|所以|综上所述)/i
            ];

            for (const pattern of patterns) {
                const match = content.match(pattern);
                if (match && match[0]) {
                    return match[0].trim();
                }
            }

            return content;
        }

        function estimateTokens(text) {
            if (!text) return 0;
            const chineseChars = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
            const otherChars = text.length - chineseChars;
            return Math.ceil(chineseChars / 2 + otherChars / 4);
        }

        // ==================== 工具函数 ====================
