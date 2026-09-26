        function zoomMermaid(id, delta) {
            const svg = document.querySelector(`#container-${id} svg`);
            if (!svg) return;

            let scale = parseFloat(svg.getAttribute('data-scale') || '1');
            scale = Math.max(0.4, Math.min(3, scale + delta));

            svg.style.transform = `scale(${scale})`;
            svg.setAttribute('data-scale', scale);

            // 调整容器以适应缩放后的内容
            const container = document.getElementById(`container-${id}`);
            container.style.padding = `${1.5 * scale}rem`;
        }

        function toggleMermaidCollapse(id) {
            const wrapper = document.getElementById(`wrapper-${id}`);
            const btn = document.getElementById(`toggle-btn-${id}`);
            if (wrapper) {
                const isCollapsed = wrapper.classList.toggle('collapsed');
                btn.innerHTML = isCollapsed ? '🔒' : '🔓';
            }
        }

        function copyMermaidCode(encodedCode) {
            const code = decodeURIComponent(encodedCode);
            navigator.clipboard.writeText(code).then(() => {
                showPassiveToast('Mermaid 源代码已复制');
            });
        }

        async function copyMermaidImageAsPng(id) {
            const svg = document.querySelector(`#container-${id} svg`);
            if (!svg) return;

            try {
                showToast('正在准备图片...', 'info');
                const serializer = new XMLSerializer();
                const source = '<?xml version="1.0" standalone="no"?>\r\n' + serializer.serializeToString(svg);

                const canvas = document.createElement('canvas');
                const bbox = svg.getBBox();
                const scale = 2; // 高清
                canvas.width = bbox.width * scale;
                canvas.height = bbox.height * scale;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = 'white';
                ctx.fillRect(0, 0, canvas.width, canvas.height);

                const img = new Image();
                const url = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(source)));

                img.onload = () => {
                    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                    canvas.toBlob(blob => {
                        try {
                            const data = [new ClipboardItem({ 'image/png': blob })];
                            navigator.clipboard.write(data).then(() => {
                                showToast('流程图已作为图片复制到剪贴板', 'success');
                            });
                        } catch (e) {
                            // 降级：如果不支持直接复制图片，尝试下载
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = 'mermaid_diagram.png';
                            a.click();
                            showToast('浏览器不支持直接复制图片，已为您开启下载', 'warning');
                        }
                    });
                };
                img.src = url;
            } catch (err) {
                console.error('Copy image error:', err);
                showToast('复制图片失败', 'error');
            }
        }

        function renderMermaidDiagrams() {
            if (typeof mermaid !== 'undefined') {
                mermaid.init(undefined, document.querySelectorAll('.mermaid'));
            }
        }

        function copyCode(btn, codeId) {
            const codeElement = document.getElementById(codeId);
            if (!codeElement) return;

            // 获取纯文本（去除HTML标签）
            const text = codeElement.textContent;

            navigator.clipboard.writeText(text).then(() => {
                const originalText = btn.innerHTML;
                btn.innerHTML = '✅ 已复制';
                setTimeout(() => {
                    btn.innerHTML = originalText;
                }, 2000);
            }).catch(err => {
                console.error('复制失败:', err);
                showToast('复制失败', 'error');
            });
        }


        function showKeyStatus(message, type) {
            if (keyStatus) {
                keyStatus.textContent = message;
                keyStatus.className = 'key-status ' + type;

                setTimeout(() => {
                    if (!keyStatus) return;
                    keyStatus.className = 'key-status';
                    keyStatus.textContent = '';
                }, 3000);
            }

            // 被动轻提示：自动消失、无需点击确认
            if (typeof showPassiveToast === 'function') showPassiveToast(message);
        }

          // 粗略估算文本 token 数：中文按 1 token/字，其他字符按 4 字符/token
          function estimateTokens(text) {
              if (!text) return 0;
              const cjk = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
              const other = text.length - cjk;
              return Math.ceil(cjk + other / 4);
          }

          // 系统提示词等固定开销的近似 token 数
          const SYSTEM_TOKEN_OVERHEAD = 600;

          function updateTokenDisplay() {
              // 显示「当前发送给 LLM 的 token 数」——只统计最近 contextLength 轮(压缩后)，超出阈值则加粗红字警告
              const currentBranch = state.branches[state.currentBranchId];
              const contextSizeText = document.getElementById('context-size-text');
              if (!contextSizeText) return;

              if (!currentBranch) {
                  contextSizeText.textContent = '0';
                  contextSizeText.classList.remove('over-threshold');
                  return;
              }

              const contextLengthSlider = document.getElementById('context-length');
              const contextLength = contextLengthSlider ? (parseInt(contextLengthSlider.value) || 10) : (state.settings?.contextLength || 10);
              const msgs = currentBranch.messages || [];
              // 只统计最近 contextLength 轮（每轮约 2 条：user+assistant）——即压缩后发送给 LLM 的部分
              const recentMsgs = msgs.slice(-contextLength * 2);
              let tokens = SYSTEM_TOKEN_OVERHEAD;
              for (const msg of recentMsgs) {
                  tokens += estimateTokens(msg.content || '');
                  if (msg._attachmentContent) tokens += estimateTokens(msg._attachmentContent);
              }

              const threshold = state.settings?.expectedContextTokens || 4000;
              contextSizeText.textContent = tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'K' : String(tokens);
              if (threshold > 0 && tokens > threshold) {
                  contextSizeText.classList.add('over-threshold');
                  contextSizeText.title = '超出预期Token数阈值 ' + threshold.toLocaleString();
              } else {
                  contextSizeText.classList.remove('over-threshold');
                  contextSizeText.title = '';
              }
          }

        // ==================== 消息渲染 ====================
