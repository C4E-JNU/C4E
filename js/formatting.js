        // 初始化 marked 配置
        if (typeof marked !== 'undefined') {
            const renderer = new marked.Renderer();

            // 自定义代码块渲染
            renderer.code = function (code, language) {
                // Mermaid 图表支持
                if (language === 'mermaid') {
                    const mermaidId = 'mermaid-' + Math.random().toString(36).substr(2, 9);
                    return `
                        <div class="mermaid-wrapper" id="wrapper-${mermaidId}">
                            <div class="mermaid-header" onclick="toggleMermaidCollapse('${mermaidId}')">
                                <div class="mermaid-title">📊 流程图 / 图表</div>
                                <div class="mermaid-controls" onclick="event.stopPropagation()">
                                    <button class="mermaid-btn" onclick="zoomMermaid('${mermaidId}', 0.2)" title="放大">➕</button>
                                    <button class="mermaid-btn" onclick="zoomMermaid('${mermaidId}', -0.2)" title="缩小">➖</button>
                                    <button class="mermaid-btn" onclick="copyMermaidCode('${encodeURIComponent(code)}')" title="复制代码">📜</button>
                                    <button class="mermaid-btn" onclick="copyMermaidImageAsPng('${mermaidId}')" title="复制图片">🖼️</button>
                                    <button class="mermaid-btn" id="toggle-btn-${mermaidId}" onclick="toggleMermaidCollapse('${mermaidId}')" title="收起/展开">🔓</button>
                                </div>
                            </div>
                            <div class="mermaid-container" id="container-${mermaidId}">
                                <div class="mermaid" id="${mermaidId}">${code}</div>
                            </div>
                        </div>
                    `;
                }

                const validLang = !!(language && hljs.getLanguage(language));
                const highlighted = validLang ? hljs.highlight(code, { language }).value : hljs.highlightAuto(code).value;
                const langLabel = language ? language : 'text';

                // 生成唯一ID以便复制代码
                const codeId = 'code-' + Math.random().toString(36).substr(2, 9);

                return `
                    <div class="code-block-wrapper">
                        <div class="code-header">
                            <span class="code-lang">${langLabel}</span>
                            <button class="copy-code-btn" onclick="copyCode(this, '${codeId}')">
                                📋 复制代码
                            </button>
                        </div>
                        <pre><code id="${codeId}" class="hljs ${language}">${highlighted}</code></pre>
                    </div>
                `;
            };

            // 自定义表格渲染
            renderer.table = function (header, body) {
                const tableId = 'table-' + Math.random().toString(36).substr(2, 9);
                return `
                    <div class="table-wrapper ${state.tableTheme}" id="${tableId}-wrapper">
                        <div class="table-header">
                            <div class="table-title">📅 数据表格</div>
                            <div class="table-actions">
                                <button class="table-action-btn" onclick="switchTableStyle()" title="全局切换表格皮肤">
                                    🎨 换肤
                                </button>
                                <button class="table-action-btn" onclick="copyTableToClipboard('${tableId}')">
                                    📋 复制表格
                                </button>
                                <button class="table-action-btn" onclick="exportTableToExcel('${tableId}')">
                                    📊 导出 Excel
                                </button>
                            </div>
                        </div>
                        <div style="overflow-x: auto;">
                            <table id="${tableId}">
                                <thead>${header}</thead>
                                <tbody>${body}</tbody>
                            </table>
                        </div>
                    </div>
                `;
            };

            marked.use({
                renderer: renderer,
                gfm: true,
                breaks: true,
                mangle: false,
                headerIds: false
            });
        }

        function formatMessage(content, isFinal = false) {
            if (!content) return '';

            let processedContent = content;
            const mathBlocks = [];

            // 1. 预处理 LaTeX 公式，避免 marked 解析过程破坏反斜杠等内容
            // 匹配块级公式: $$...$$ 或 \[...\]
            processedContent = processedContent.replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, (match, p1, p2) => {
                const formula = (p1 || p2).trim();
                const placeholder = `MATH_BLOCK_PLACEHOLDER_${mathBlocks.length}_MATH`;
                mathBlocks.push({ placeholder, formula, displayMode: true });
                return placeholder;
            });

            // 匹配行内公式: $...$ 或 \(...\)
            processedContent = processedContent.replace(/\$([^\$\n]+?)\$|\\\(([\s\S]+?)\\\)/g, (match, p1, p2) => {
                const formula = (p1 || p2).trim();
                const placeholder = `MATH_INLINE_PLACEHOLDER_${mathBlocks.length}_MATH`;
                mathBlocks.push({ placeholder, formula, displayMode: false });
                return placeholder;
            });

            // 检查 marked 是否可用
            if (typeof marked !== 'undefined') {
                try {
                    // 1.5 图片 markdown 预处理：解析 ![alt](url =WxH) 尺寸与 ![alt](url)，转成带宽度控制的 <img>
                    // 尺寸为可选（=300 宽自适应 或 =300x150 压扁），用于"屏幕缩小但保持原图质量"
                    processedContent = processedContent.replace(/\!\[(.*?)\]\(([^)\s]+)(?:\s+=(?:(\d+))?(?:x(\d+))?)?\)/g, function (m, alt, url, w, h) {
                        var a = (alt || '').replace(/"/g, '&quot;');
                        var u = (url || '').replace(/"/g, '&quot;');
                        // 构造内联样式：若指定宽度则以 px 限定，否则 max-width:100%（自适应）；高度可选（压扁才设，否则 auto 保原比例）
                        var wpx = w ? parseInt(w, 10) : 0;
                        var hpx = h ? parseInt(h, 10) : 0;
                        var wStyle = wpx ? ('width:' + wpx + 'px;') : 'max-width:100%;width:auto;';
                        var hStyle = hpx ? ('height:' + hpx + 'px;') : 'height:auto;';
                        var style = wStyle + hStyle + 'border-radius:6px;cursor:zoom-in;vertical-align:middle;';
                        return '<span class="msg-image-item"><img class="msg-image" src="' + u + '" alt="' + a + '" loading="lazy" onclick="openImagePreview(this.getAttribute(\'src\'))" style="' + style + '"></span>';
                    });
                    let html = marked.parse(processedContent);

                    // 2. 将公式占位符替换为 KaTeX 渲染后的 HTML
                    mathBlocks.forEach(item => {
                        try {
                            const renderedMath = katex.renderToString(item.formula, {
                                displayMode: item.displayMode,
                                throwOnError: false,
                                strict: false
                            });

                            let finalOutput = renderedMath;
                            if (item.displayMode) {
                                // 为块级公式添加包装器和复制按钮
                                finalOutput = `
                                    <div class="math-block-wrapper">
                                        ${renderedMath}
                                        <button class="copy-math-btn" onclick="copyRawMath(this, '${encodeURIComponent(item.formula)}')">
                                            复制LaTeX代码
                                        </button>
                                    </div>
                                `;
                            }

                            // 使用 split 和 join 替换
                            html = html.split(item.placeholder).join(finalOutput);
                        } catch (err) {
                            console.error('KaTeX error:', err);
                            html = html.split(item.placeholder).join(item.formula);
                        }
                    });

                    // 3. 图片渲染增强：给 HTML 中的 <img> 加样式类、点击放大预览、懒加载
                    html = html.replace(/<img\s+([^>]*)>/gi, function (m, attrs) {
                        // 已带 class 的跳过（避免重复叠加）
                        if (/class\s*=/.test(attrs)) return m;
                        return '<img ' + attrs + ' class="msg-image" loading="lazy" onclick="openImagePreview(this.src)" style="max-width:100%;height:auto;border-radius:6px;cursor:zoom-in;margin:4px 0;">';
                    });

                    // 异步调用 mermaid 渲染（仅在生成完成时）
                    if (isFinal) {
                        setTimeout(renderMermaidDiagrams, 100);
                    }
                    return html;
                } catch (e) {
                    console.error('Markdown parsing error:', e);
                }
            }

            // Fallback
            return content
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\n/g, '<br>');
        }

        function copyRawMath(btn, encodedFormula) {
            const formula = decodeURIComponent(encodedFormula);
            navigator.clipboard.writeText(formula).then(() => {
                const originalText = btn.innerHTML;
                btn.innerHTML = '✅ 已复制';
                btn.style.borderColor = 'var(--success)';
                btn.style.color = 'var(--success)';
                setTimeout(() => {
                    btn.innerHTML = originalText;
                    btn.style.borderColor = 'var(--primary)';
                    btn.style.color = 'var(--primary)';
                }, 2000);
                showPassiveToast('公式已复制');
            }).catch(err => {
                console.error('复制失败:', err);
                showPassiveToast('复制失败');
            });
        }

        // ==================== 表格增强功能 (全局) ====================
        function switchTableStyle() {
            const themes = [
                '',
                'theme-modern',
                'theme-pro',
                'theme-minimal',
                'theme-success',
                'theme-warm',
                'theme-dark',
                'theme-academic',
                'theme-luxury'
            ];
            const themeNames = [
                '跟随主题', '莫兰迪紫', '商务灰', '极简白', '金融绿', '活力橙', '黑客风', '学术风', '轻奢金'
            ];

            let currentIdx = themes.indexOf(state.tableTheme);
            if (currentIdx === -1) currentIdx = 0;

            // 切换到下一个
            const nextIdx = (currentIdx + 1) % themes.length;
            const nextTheme = themes[nextIdx];

            // 更新全局状态
            state.tableTheme = nextTheme;
            localStorage.setItem('ragagent-table-theme', nextTheme);

            // 应用到页面上所有的表格
            const wrappers = document.querySelectorAll('.table-wrapper');
            wrappers.forEach(wrapper => {
                // 移除所有已知主题类
                themes.forEach(t => { if (t) wrapper.classList.remove(t); });
                // 添加新主题
                if (nextTheme) wrapper.classList.add(nextTheme);
            });

            if (nextTheme) {
                showToast(`所有表格已切换至 ${themeNames[nextIdx]} 风格`, 'success');
            } else {
                showToast('所有表格已恢复 经典蓝 风格', 'info');
            }
        }

        function copyTableToClipboard(tableId) {
            const table = document.getElementById(tableId);
            if (!table) return;

            try {
                // 模拟浏览器原生选择并复制，以保留样式和配色
                const range = document.createRange();
                range.selectNode(table);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);

                const successful = document.execCommand('copy');
                selection.removeAllRanges();

                if (successful) {
                    showPassiveToast('表格已完整复制 (保留格式与配色)');
                } else {
                    throw new Error('execCommand failed');
                }
            } catch (err) {
                // 降级方案：仅复制纯文本
                let text = "";
                const rows = table.querySelectorAll('tr');
                rows.forEach(row => {
                    const cols = row.querySelectorAll('th, td');
                    const rowData = Array.from(cols).map(col => col.innerText.trim());
                    text += rowData.join('\t') + '\n';
                });

                navigator.clipboard.writeText(text).then(() => {
                    showPassiveToast('表格内容已复制 (仅纯文本)');
                }).catch(e => {
                    showPassiveToast('复制失败');
                });
            }
        }

        function exportTableToExcel(tableId) {
            const table = document.getElementById(tableId);
            if (!table) return;

            let csvContent = "\uFEFF"; // BOM for UTF-8
            const rows = table.querySelectorAll('tr');
            rows.forEach(row => {
                const cols = row.querySelectorAll('th, td');
                const rowData = Array.from(cols).map(col => {
                    let cell = col.innerText.replace(/"/g, '""');
                    return `"${cell}"`;
                });
                csvContent += rowData.join(',') + '\n';
            });

            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement("a");
            const url = URL.createObjectURL(blob);
            link.setAttribute("href", url);
            link.setAttribute("download", `table_export_${new Date().getTime()}.csv`);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            showToast('表格已导出为CSV格式 (Excel可直接打开)', 'success');
        }

        // ==================== Mermaid 增强功能 ====================
