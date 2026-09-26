        function estimateTokens(text) {
            if (!text) return 0;
            const chineseChars = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
            const otherChars = text.length - chineseChars;
            return Math.ceil(chineseChars / 2 + otherChars / 4);
        }

        // ==================== 工具函数 ====================
