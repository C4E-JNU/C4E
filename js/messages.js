        // ── 模型推理块的展开状态 ────────────────────────────────────────────────
        // 实时流式期间推理正文是【展开】可见的；而 renderMessages() 每次切对话都会清空
        // 重建 DOM，旧实现把 display 写死为 none → 一切回对话，推理正文全被收成一行
        // "🧠 模型推理 ▸"，看起来像"思考过程收集不到了"。
        // 方案：本轮生成的消息默认展开（__mrAutoOpen），用户手动收/展记入 __mrUser
        // （优先于默认值），两者都跨重渲染保留；老对话仍默认折叠，避免长对话卡顿。
        window.__mrUser = window.__mrUser || {};
        window.__mrAutoOpen = window.__mrAutoOpen || {};
        (function () {
            try {
                var saved = JSON.parse(localStorage.getItem('mr-auto-open') || '[]');
                if (saved && saved.length) saved.forEach(function (m) { window.__mrAutoOpen[m] = true; });
            } catch (e) {}
        })();

        function _mrMsgIdOf(rid) {          // rid 形如 mr-<msgId>-<step>
            var rest = String(rid).slice(3);
            var cut = rest.lastIndexOf('-');
            return cut > 0 ? rest.slice(0, cut) : rest;
        }
        function mrOpenState(rid) {         // 渲染时调用：决定该块初始展开还是折叠
            if (Object.prototype.hasOwnProperty.call(window.__mrUser, rid)) return !!window.__mrUser[rid];
            return !!window.__mrAutoOpen[_mrMsgIdOf(rid)];
        }
        window.toggleMrBox = function (rid, el) {   // 内联 onclick 调用（必须挂 window）
            var c = document.getElementById(rid);
            if (!c) return;
            var open = c.style.display === 'block';
            c.style.display = open ? 'none' : 'block';
            window.__mrUser[rid] = !open;
            if (el) el.innerHTML = open ? t('msg.mrCollapsed') : t('msg.mrExpanded');
        };
        window.__mrMarkAuto = function (msgId) {    // 生成结束时调用：本条消息的推理块默认展开
            if (!msgId) return;
            window.__mrAutoOpen[msgId] = true;
            try {
                localStorage.setItem('mr-auto-open', JSON.stringify(Object.keys(window.__mrAutoOpen).slice(-200)));
            } catch (e) {}
        };

        function addMessage(role, content, tokens = 0, isSystem = false, thinkingChain = null, type = null, attachments = [], noteRefs = []) {
            const currentBranch = state.branches[state.currentBranchId];
            const messageId = 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);

            const message = {
                id: messageId,
                role,
                content,
                tokens,
                timestamp: new Date().toISOString(),
                branchId: state.currentBranchId,
                thinkingChain: thinkingChain,
                type: type,
                attachments: attachments,
                noteRefs: noteRefs      // [{id, scope:'notes'|'database', title}] 引用的笔记/数据库条目
            };

            currentBranch.messages.push(message);

            // 标记对话有内容（★ 只用于显示/排序，不参与任何删除判定）
            // 2026-09-30 用户定：取消「临时对话 → 自动转正」这套 —— 对话创建时就已经是
            // 永久对话，不需要在这里迁移 id；空对话也保留、永不自动删除。
            if (!isSystem && role === 'user') {
                currentBranch.hasContent = true;
            }

            if (tokens > 0) {
                currentBranch.totalTokens += tokens;
                currentBranch.sessionTokens += tokens;
                updateTokenDisplay();
            }

            saveBranches();

            if (!isSystem) {
                const messageEl = createMessageElement(message);
                messagesContainer.appendChild(messageEl);
                scrollToBottomIfFollowing();

                updateHistoryList();
            }

            return messageId;
        }

        // ── addDatabaseMessage / addDocumentMessage 已移除，改用 reactAgentLoop ────

         function createMessageElement(message) {
               const messageEl = document.createElement('div');
               messageEl.className = `message ${message.role} ${message.type === 'document' ? 'document' : ''} ${message.type === 'database' ? 'database' : ''}`;
               messageEl.dataset.id = message.id;

               let content = message.content;
               let tokenInfo = '';

               if (message.tokens > 0) {
                   tokenInfo = `<div class="token-info">Tokens: ${message.tokens}</div>`;
               }

               let thinkingContent = '';
               // 新增：原始输出过程显示
               if (state.settings.showRawData && message.thinkingChain) {
                   thinkingContent = `
                       <div class="reasoning-chain" onclick="toggleReasoningChain('${message.id}')">
                           <div class="reasoning-content">
                               <strong>${t('msg.thinkingChain')}</strong><br>
                               ${formatMessage(message.thinkingChain, true)}
                           </div>
                       </div>
                   `;
               }

               // 新增：工具调用和原始数据显示
               let rawToolCalls = '';
               if (state.settings.showRawData && message.toolCalls && message.toolCalls.length > 0) {
                   rawToolCalls = `
                       <div class="reasoning-chain" onclick="toggleReasoningChain('${message.id}-tools')">
                           <div class="reasoning-content">
                               <strong>${t('msg.toolCalls')}</strong><br>
                               <pre style="background:rgba(0,0,0,0.05);padding:0.5rem;border-radius:0.5rem;margin-top:0.25rem;overflow-x:auto;font-size:0.8rem;">${JSON.stringify(message.toolCalls, null, 2)}</pre>
                           </div>
                       </div>
                   `;
               }

               let rawDataSection = '';
               if (state.settings.showRawData && message.rawData) {
                   rawDataSection = `
                       <div class="reasoning-chain" onclick="toggleReasoningChain('${message.id}-raw')">
                           <div class="reasoning-content">
                               <strong>${t('msg.rawData')}</strong>
                               <pre style="background:rgba(0,0,0,0.05);padding:0.5rem;border-radius:0.5rem;margin-top:0.25rem;overflow-x:auto;font-size:0.7rem;">${message.rawData}</pre>
                           </div>
                       </div>
                   `;
               }

               // 智能体推理过程（可折叠，不参与上下文）
               let reasoningStepsHtml = '';
               if (message.reasoningSteps && message.reasoningSteps.length > 0) {
                   const stepsHtml = message.reasoningSteps.map(step => {
                       switch(step.type) {
                           case 'agent_thought':
                           case 'thinking':
                               return `<div class="agent-step-header">${t('msg.stepThought')}</div><div class="streaming-step-content">${escHtml(step.text||'')}</div>`;
                           case 'model_reasoning': {
                               const rid = 'mr-' + message.id + '-' + (step.step||'0');
                               const _mrOpen = mrOpenState(rid);
                               return `<div class="agent-reasoning-box"><div class="reasoning-toggle" onclick="toggleMrBox('${rid}',this)">${t('msg.mrTitle')} ${_mrOpen?'▾':'▸'}</div><div id="${rid}" class="reasoning-content" style="display:${_mrOpen?'block':'none'}">${escHtml(step.text||'')}</div></div>`;
                           }
                           case 'tool_call': {
                               const icon = step.name==='skill_view'?'📖':'🔧';
                               const inputStr = step.input||'';
                               const obsHtml = step.error ? `<div class="obs-content err">❌ ${escHtml(step.error)}</div>` :
                                               step.result ? `<div class="agent-step-header">${t('msg.stepResult')}</div><div class="obs-content">${escHtml(step.result)}</div>` : '';
                               return `<div class="agent-step-header">${icon} ${t('msg.stepCall')} ${step.name}</div><pre class="tool-code">${escHtml(inputStr)}</pre>${obsHtml}`;
                           }
                           case 'observation':
                           case 'tool_result':
                               return `<div class="agent-step-header">${t('msg.stepResult')}</div><div class="obs-content">${escHtml(step.text||step.content||'')}</div>`;
                           case 'subagent': {
                               // 子智能体卡片：点击打开浮窗查看它的思考/工具调用全过程
                               const sbBadge = step.running
                                   ? '<span style="color:#e6a23c;">' + t('msg.saRunning') + '</span>'
                                   : (step.ok ? '<span style="color:#2e9e4f;">' + t('msg.saDone') + (step.seconds ? t('msg.secElapsed').replace('{s}', step.seconds) : '') + '</span>'
                                              : '<span style="color:#e02020;">' + t('msg.saFail') + '</span>');
                               const payloadId = 'sa-data-' + message.id + '-' + (step.index || 0);
                               // 用户 2026-10-04：卡片显示「专有子智能体」名字（档案名）
                               const saProfileTitle = String(step.profile_title || step.profile || '').trim() || t('msg.saDefault');
                               const saMeta = [];
                               if (step.profile_read_max) saMeta.push(t('msg.saRead').replace('{n}', step.profile_read_max));
                               if (step.profile_rounds) saMeta.push(t('msg.saRounds').replace('{n}', step.profile_rounds));
                               if (step.profile_tools) saMeta.push(t('msg.saTools').replace('{n}', step.profile_tools));
                               if (step.profile_skills) saMeta.push(t('msg.saSkills').replace('{n}', step.profile_skills));
                               const saMetaHtml = saMeta.length
                                   ? `<span style="font-size:.72rem;color:var(--gray);font-weight:400;">（${saMeta.join(' · ')}）</span>` : '';
                               // 注册数据，供点击时打开浮窗
                               window.__saData = window.__saData || {};
                               window.__saData[payloadId] = step;
                               return `<div class="subagent-node subagent-open" data-payload="${payloadId}" style="cursor:pointer;border:1px solid var(--border-color,#ddd);border-radius:8px;padding:.45rem .6rem;margin:.35rem 0;background:var(--light,#f6f8fa);">
                                   <div style="font-weight:600;">🤖 ${escHtml(saProfileTitle)} <span style="color:var(--gray);font-weight:400;">${t('msg.saTask')}${(step.index || 0) + 1}</span> ${saMetaHtml} &nbsp;${sbBadge}</div>
                                   <div style="margin-top:.15rem;">${t('msg.saGoal')}${escHtml((step.goal || '').slice(0, 140))}</div>
                                   ${step.summary ? `<div style="margin-top:.25rem;color:var(--gray);">${t('msg.saSummary')}${escHtml(step.summary.slice(0, 200))}…</div>` : ''}
                                   <div style="margin-top:.2rem;font-size:.75rem;color:var(--primary);">${t('msg.saView')}</div>
                               </div>`;
                           }
                           case 'timeout_notice': {
                               const tColor = step.level === 'abort' ? '#e02020' : (step.level === 'warn' ? '#e6a23c' : (step.level === 'immediate' ? 'var(--primary)' : 'var(--gray)'));
                               const tIcon = step.level === 'abort' ? '⏹️' : (step.level === 'warn' ? '⚠️' : (step.level === 'immediate' ? '⚡' : '⏳'));
                               const tLabel = step.level === 'abort' ? t('msg.toAbort') : (step.level === 'immediate' ? t('msg.toImmediate') : step.level);
                               return `<div class="agent-step-header" style="color:${tColor};">${tIcon} ${tLabel}${step.elapsed ? t('msg.secElapsed').replace('{s}', step.elapsed) : ''}</div><div class="obs-content">${escHtml(step.text || '')}</div>`;
                           }
                           default: return '';
                       }
                   }).join('\n');
                   reasoningStepsHtml = `<div class="agent-reasoning-block"><div class="reasoning-steps-container">${stepsHtml}</div><div class="reasoning-collapsed-hint" onclick="this.parentElement.classList.remove('collapsed');updateReasoningRail()">${t('msg.collapsedHint')}</div></div>`;
               }

               // 附件显示 (仅用户消息)
            let attachmentsHTML = '';
            if (message.role === 'user' && message.attachments && message.attachments.length > 0) {
                var attDivId = 'att-' + message.id;
                var attachmentItems = message.attachments.map(function(att) {
                    var fileUrl = att.file_id ? '/api/file/' + att.file_id : null;
                    return '<div class="attachment-item' + (fileUrl ? ' attachment-clickable' : '') + '" data-file-url="' + (fileUrl || '') + '" data-file-name="' + (att.name || '') + '">' +
                        '<span class="attachment-icon">' + (fileUrl ? '\uD83D\uDCC4' : att.icon) + '</span>' +
                        '<div class="attachment-info">' +
                            '<div class="attachment-name">' + att.name + '</div>' +
                            '<div class="attachment-meta">' + (fileUrl ? t('msg.attUploaded') : '') + (att.type || '') + ' \u2022 ' + formatFileSize(att.size) + '</div>' +
                        '</div>' +
                        (att.file_id ? '<button class="attachment-remove" title="' + t('msg.delAttachment') + '" onclick="event.stopPropagation();removeAttachmentFromMessage(\'' + message.id + '\',\'' + att.file_id + '\')">\u2715</button>' : '') +
                    '</div>';
                }).join('');

                attachmentsHTML = `<div class="message-attachments">${attachmentItems}</div>`;
            }

            // 引用给智能体的笔记/数据库条目（仅用户消息，显示为芯片）
            let refsHTML = '';
            if (message.role === 'user' && message.noteRefs && message.noteRefs.length > 0) {
                refsHTML = '<div class="message-refs" style="display:flex;flex-wrap:wrap;gap:6px;margin:4px 0;">' +
                    message.noteRefs.map(function (r) {
                        var icon = r.scope === 'database' ? '🗄️' : '📝';
                        var label = (r.scope === 'database' ? t('msg.refDb') : t('msg.refNote'));
                        return '<span title="' + (r.title || '') + '（' + label + ' ' + r.id + '）" ' +
                            'style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border:1px solid var(--border,#d0d5dd);' +
                            'border-radius:12px;background:var(--card-bg,#f6f8fa);font-size:.76rem;max-width:260px;">' +
                            icon + '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
                            (r.title || r.id) + '</span><span style="color:var(--gray);font-size:.7rem;">' + label + '</span></span>';
                    }).join('') + '</div>';
            }

            messageEl.innerHTML = `
                 <div class="message-content">
                     ${attachmentsHTML}
                     ${refsHTML}
                     ${thinkingContent}
                     ${rawToolCalls}
                     ${rawDataSection}
                     ${reasoningStepsHtml}
                      <div class="msg-body">${formatMessage(content, true)}</div>
                     ${tokenInfo}
                 </div>
                <div class="message-actions">
                    ${message.role === 'user' ?
                    `<button class="action-btn" onclick="copyMessage('${message.id}')">${t('msg.btnCopy')}</button>
                         <button class="action-btn" onclick="editMessage('${message.id}')">${t('msg.btnEdit')}</button>
                         <button class="action-btn branch-btn" onclick="branchFromMessage('${message.id}')">${t('msg.btnBranch')}</button>
                         <button class="action-btn" onclick="regenerateMessage('${message.id}')">${t('msg.btnRegen')}</button>
                         <button class="action-btn" onclick="deleteMessage('${message.id}')">${t('msg.btnDelete')}</button>` :
                    `<button class="action-btn" onclick="copyMessage('${message.id}')">${t('msg.btnCopy')}</button>
                         <button class="action-btn" onclick="editMessage('${message.id}')">${t('msg.btnEdit')}</button>
                         <button class="action-btn branch-btn" onclick="branchFromMessage('${message.id}')">${t('msg.btnBranch')}</button>
                         <button class="action-btn continue-btn" onclick="continueGeneration('${message.id}')">${t('msg.btnContinue')}</button>
                         <button class="action-btn" onclick="regenerateMessage('${message.id}')">${t('msg.btnRegen')}</button>
                         <button class="action-btn" onclick="deleteMessage('${message.id}')">${t('msg.btnDelete')}</button>`
                }
                </div>
            `;

            return messageEl;
        }

        // ==================== 思考链功能 ====================
        function toggleThinking(messageId) {
            const thinkingContent = document.getElementById(`thinking-${messageId}`);
            const toggleBtn = thinkingContent.previousElementSibling;

            if (thinkingContent.classList.contains('show')) {
                thinkingContent.classList.remove('show');
                toggleBtn.textContent = t('msg.expandThinking');
            } else {
                thinkingContent.classList.add('show');
                toggleBtn.textContent = t('msg.collapseThinking');
            }
        }

        // ==================== 系统消息通知功能 (Toast) ====================
        // ==================== 消息提示功能 (Toast) ====================
        function showToast(message, type = 'info', duration = 3000) {
            // 全部信息类提示改为被动轻提示（自动消失、无需点击确认）
            showPassiveToast(message);
        }

        function showActionFeedback(message, type = 'success', target = null, duration = 2200) {
            if (target) {
                target.classList.remove('ui-action-flash');
                void target.offsetWidth;
                target.classList.add('ui-action-flash');
                setTimeout(() => target.classList.remove('ui-action-flash'), 700);
            }
            showPassiveToast(message);
        }

        // 被动轻提示：自动消失、无需点击确认（用于"已复制"等纯信息反馈）
        function showPassiveToast(message) {
            var container = document.getElementById('passive-toast-container');
            if (!container) {
                container = document.createElement('div');
                container.id = 'passive-toast-container';
                container.className = 'passive-toast-container';
                document.body.appendChild(container);
            }
            var el = document.createElement('div');
            el.className = 'passive-toast';
            el.textContent = message;
            container.appendChild(el);
            setTimeout(function() {
                el.classList.add('out');
                setTimeout(function() { el.remove(); }, 300);
            }, 1600);
        }

        function addSystemMessageWithDelete(content) {
            showToast(content, 'info');
        }

        // ==================== 消息操作功能 ====================
        // ==================== 统一浮动 Markdown 编辑器 ====================
        const floatingEditor = (function () {
            let _onSave = null;
            let _initialized = false;
            let _liveUpdate = null;    // (html)=>void 实时渲染到真实目标容器
            let _liveRestore = null;   // ()=>void 取消时恢复真实目标容器到原始
            let _originalValue = '';   // 打开时的原始内容快照（取消时回退用）

            function _el() { return document.getElementById('floating-editor'); }
            function _title() { return document.getElementById('fe-title'); }
            function _textarea() { return document.getElementById('fe-textarea'); }
            function _titleRow() { return document.getElementById('fe-title-row'); }
            function _titleInput() { return document.getElementById('fe-title-input'); }
            function _sizeSel() { return document.getElementById('fe-fontsize'); }
            function _colorPicker() { return document.getElementById('fe-color'); }

            function _render() {
                const t = _textarea();
                if (!t) return;
                const html = formatMessage(t.value);
                // 实时渲染到真实目标容器（预览替代：直接改真实消息/笔记框）
                if (typeof _liveUpdate === 'function') _liveUpdate(html);
            }

            // ── 撤销 / 重做（会话内历史，Word 式）──
            const _MAX_HISTORY = 100;   // 缓存上限（防止无限占用内存）
            let _history = [];            // undo 栈（含当前状态）
            let _future = [];             // redo 栈
            let _applyingHistory = false;

            function _resetHistory(initialVal) {
                _history = [initialVal === undefined ? (_textarea() ? _textarea().value : '') : initialVal];
                _future = [];
            }
            function _pushHistory() {
                if (_applyingHistory) return;
                const ta = _textarea(); if (!ta) return;
                const v = ta.value;
                if (_history.length === 0 || _history[_history.length - 1] !== v) {
                    _history.push(v);
                    if (_history.length > _MAX_HISTORY) _history.shift(); // 超出上限丢弃最旧
                    _future = [];  // 新输入清空重做
                }
            }
            function _applyText(v) {
                const ta = _textarea(); if (!ta) return;
                _applyingHistory = true;
                ta.value = v;
                _applyingHistory = false;
                _render();
                _updateHistoryButtons();
            }
            function undo() {
                if (_history.length <= 1) return;
                _future.push(_history.pop());
                const v = _history[_history.length - 1];
                _applyText(v);
            }
            function redo() {
                if (_future.length === 0) return;
                const v = _future.pop();
                _history.push(v);
                _applyText(v);
            }
            function _updateHistoryButtons() {
                const u = document.getElementById('fe-undo'), r = document.getElementById('fe-redo');
                if (u) u.style.opacity = (_history.length > 1) ? '1' : '0.35';
                if (r) r.style.opacity = (_future.length > 0) ? '1' : '0.35';
            }

            function _init() {
                if (_initialized) return;
                _initialized = true;
                const elm = _el();
                const hd = document.getElementById('fe-header');
                if (hd && elm) {
                    hd.addEventListener('mousedown', function (e) {
                        if (e.target.closest('.fe-close')) return;
                        const rect = elm.getBoundingClientRect();
                        const offX = e.clientX - rect.left;
                        const offY = e.clientY - rect.top;
                        function move(ev) {
                            elm.style.left = Math.max(-80, ev.clientX - offX) + 'px';
                            elm.style.top = Math.max(0, ev.clientY - offY) + 'px';
                        }
                        function up() {
                            document.removeEventListener('mousemove', move);
                            document.removeEventListener('mouseup', up);
                        }
                        document.addEventListener('mousemove', move);
                        document.addEventListener('mouseup', up);
                        e.preventDefault();
                    });
                }
                // 四角缩放手柄（与图片编辑面板一致的角控）——右下、左上、右上、左下
                const corners = ['se','nw','ne','sw'];
                corners.forEach(function (c) {
                    var h = document.createElement('div');
                    h.className = 'fe-resize ' + c;
                    h.dataset.corner = c;
                    // 只负责定位；外观由 CSS .fe-resize 控制（角标视觉已按要求移除）
                    // 尺寸 16px，与数据库文件预览浮窗的 .file-preview-resize 统一
                    h.style.cssText = 'position:absolute;width:16px;height:16px;z-index:5;background:transparent;pointer-events:auto;' +
                        (c === 'se' ? 'right:2px;bottom:2px;cursor:nwse-resize;' :
                         c === 'nw' ? 'left:2px;top:2px;cursor:nwse-resize;' :
                         c === 'ne' ? 'right:2px;top:2px;cursor:nesw-resize;' :
                                      'left:2px;bottom:2px;cursor:nesw-resize;');
                    elm.appendChild(h);
                    h.addEventListener('mousedown', function (e) {
                        e.preventDefault(); e.stopPropagation();
                        var sx = e.clientX, sy = e.clientY;
                        var sW = elm.offsetWidth, sH = elm.offsetHeight;
                        var sL = elm.offsetLeft, sT = elm.offsetTop;
                        var cc = h.dataset.corner;
                        function mv(ev) {
                            var dx = ev.clientX - sx, dy = ev.clientY - sy;
                            var nW = sW, nH = sH, nL = sL, nT = sT;
                            if (cc === 'se' || cc === 's') { nH = sH + dy; }
                            if (cc === 'e' || cc === 'se') { nW = sW + dx; }
                            if (cc === 'nw' || cc == 'w') { nW = sW - dx; nL = sL + dx; }
                            if (cc === 'nw' || cc === 'n') { nH = sH - dy; nT = sT + dy; }
                            if (cc === 'ne') { nW = sW + dx; nH = sH - dy; nT = sT + dy; }
                            if (cc === 'sw') { nW = sW - dx; nL = sL + dx; nH = sH + dy; }
                            // 约束最小尺寸
                            if (nW < 320) { nW = 320; }
                            if (nH < 240) { nH = 240; }
                            // 左上类约束不超出可视区左/顶
                            if (cc.indexOf('w') >= 0 && nL < 0) { nW += nL; nL = 0; }
                            if (cc.indexOf('n') >= 0 && nT < 0) { nH += nT; nT = 0; }
                            elm.style.width = nW + 'px';
                            elm.style.height = nH + 'px';
                            elm.style.left = nL + 'px';
                            elm.style.top = nT + 'px';
                        }
                        function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
                        document.addEventListener('mousemove', mv);
                        document.addEventListener('mouseup', up);
                    });
                });
                // 上下分栏比例调节器
                const dv = document.getElementById('fe-divider');
                if (dv) {
                    dv.addEventListener('mousedown', function (e) {
                        e.preventDefault();
                        const body = document.getElementById('fe-body');
                        if (!body) return;
                        // 用当前 textarea 实际比例作基准，避免按硬编码 0.5 导致首帧跳动
                        const ta = document.getElementById('fe-textarea');
                        const pv = document.getElementById('fe-preview');
                        let initial = 0.5;
                        try {
                            if (ta && body.offsetHeight > 0) {
                                const bodyRect = body.getBoundingClientRect();
                                const taRect = ta.getBoundingClientRect();
                                initial = (taRect.bottom - bodyRect.top) / body.offsetHeight;
                                if (!isFinite(initial) || initial <= 0 || initial >= 1) initial = 0.5;
                            }
                        } catch (e2) { initial = 0.5; }
                        const startY = e.clientY;
                        const startH = body.offsetHeight;
                        function move(ev) {
                            const ratio = Math.min(0.92, Math.max(0.08, (startH * initial + (ev.clientY - startY)) / startH));
                            _applyRatio(ratio);
                        }
                        function up() {
                            document.removeEventListener('mousemove', move);
                            document.removeEventListener('mouseup', up);
                            document.body.style.userSelect = '';
                        }
                        // 拖动时禁用文本选择，防止划过文字时抖动/选中
                        document.body.style.userSelect = 'none';
                        document.addEventListener('mousemove', move);
                        document.addEventListener('mouseup', up);
                    });
                    _applyRatio(0.5); // 默认上下各半
                }
                const ta = _textarea();
                if (ta) {
                    ta.addEventListener('input', function () {
                        _render();
                        _pushHistory();
                        _updateHistoryButtons();
                    });
                }
            }

            function open(cfg) {
                _init();
                initPaste();       // 绑定粘贴图片
                _onSave = cfg.onSave || null;
                _liveUpdate = cfg.liveUpdate || null;    // 实时渲染到真实目标
                _liveRestore = cfg.liveRestore || null;  // 取消时恢复真实目标
                _originalValue = cfg.value || '';
                if (_title()) _title().textContent = cfg.title || t('edit.defaultTitle');
                if (_textarea()) _textarea().value = _originalValue;
                _resetHistory(_textarea() ? _textarea().value : '');
                _updateHistoryButtons();
                if (cfg.showTitle) {
                    if (_titleRow()) _titleRow().style.display = 'block';
                    if (_titleInput()) _titleInput().value = cfg.titleValue || '';
                } else {
                    if (_titleRow()) _titleRow().style.display = 'none';
                }
                const w = (_el().offsetWidth || 660), h = (_el().offsetHeight || 480);
                _el().style.left = Math.max(8, (window.innerWidth - w) / 2) + 'px';
                _el().style.top = Math.max(8, (window.innerHeight - h) / 2) + 'px';
                _el().classList.add('open');
                _render();   // 打开时立即用当前值实时渲染到真实目标
                if (_textarea()) _textarea().focus();
            }

            function close() {
                _el().classList.remove('open');
                _onSave = null;
                _liveUpdate = null; _liveRestore = null; _originalValue = '';
                _history = []; _future = [];   // 会话内历史：关闭清空
            }

            function save() {
                const val = _textarea().value;
                let title = null;
                if (_titleRow() && _titleRow().style.display !== 'none' && _titleInput()) {
                    title = _titleInput().value.trim();
                }
                if (_onSave) {
                    const keep = _onSave(val, title);
                    if (keep === false) return;
                }
                close();
            }

            // 取消：把真实目标容器恢复为打开时的原始内容（未修改版本），再关窗
            function cancel() {
                if (typeof _liveRestore === 'function') { try { _liveRestore(); } catch (e) {} }
                close();
            }

            function _wrap(before, after) {
                const ta = _textarea(); if (!ta) return;
                const start = ta.selectionStart, end = ta.selectionEnd;
                const val = ta.value;
                // 若已包裹 → 取消包裹（加粗/斜体等可再次点击取消）
                if (val.slice(Math.max(0, start - before.length), start) === before && val.slice(end, end + after.length) === after) {
                    const inner = val.slice(start, end);
                    ta.value = val.slice(0, start - before.length) + inner + val.slice(end + after.length);
                    ta.focus();
                    ta.selectionStart = start - before.length;
                    ta.selectionEnd = start - before.length + inner.length;
                    _render();
                    return;
                }
                const sel = val.slice(start, end) || t('edit.selFallback');
                ta.value = val.slice(0, start) + before + sel + after + val.slice(end);
                ta.focus();
                ta.selectionStart = start + before.length;
                ta.selectionEnd = start + before.length + sel.length;
                _render();
            }
            function _line(prefix) {
                const ta = _textarea(); if (!ta) return;
                const start = ta.selectionStart;
                const lineStart = ta.value.lastIndexOf('\n', start - 1) + 1;
                ta.value = ta.value.slice(0, lineStart) + prefix + ta.value.slice(lineStart);
                ta.focus();
                ta.selectionStart = ta.selectionEnd = lineStart + prefix.length;
                _render();
            }
            function link() {
                const ta = _textarea(); if (!ta) return;
                const url = prompt(t('edit.linkPrompt'), 'https://');
                if (url === null) return;
                const start = ta.selectionStart, end = ta.selectionEnd;
                const sel = ta.value.slice(start, end) || t('edit.linkFallback');
                const text = '[' + sel + '](' + url + ')';
                ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
                ta.focus();
                ta.selectionStart = start;
                ta.selectionEnd = start + text.length;
                _render();
            }
            function applyColor() {
                const c = _colorPicker() ? _colorPicker().value : '#e02020';
                _wrap('<span style="color:' + c + '">', '</span>');
            }
            // 插入图片：上传后把 ![图](url) 插到光标处，光标保持在插入点，预览区即时显示
            function _insertImageMarkdown(url) {
                const ta = _textarea(); if (!ta) return;
                const line = ta.value.slice(Math.max(0, ta.selectionStart - 1), ta.selectionStart);
                const prefix = (line === '' || line === '\n') ? '' : '\n';
                const suffix = '\n';
                const md = prefix + '![' + t('edit.imageAlt') + '](' + url + ')' + suffix;
                ta.value = ta.value.slice(0, ta.selectionStart) + md + ta.value.slice(ta.selectionEnd);
                ta.focus();
                ta.selectionStart = ta.selectionEnd = ta.selectionStart + md.length - suffix.length;
                _render();
                _bindPreviewImageClicks();
            }

            // 给预览区已渲染的图片绑定点击 → 打开非内联裁剪面板（模态），完成后替换 markdown 并实时重渲染
            // 工具栏"编辑选中图片"：识别 textarea 选区/光标所在的 markdown 图片，打开图片编辑器
            function editSelectedImage() {
                const ta = _textarea(); if (!ta) return;
                if (typeof window.imageTools === 'undefined' || !window.imageTools.openCropFromUrl) {
                    showKeyStatus(t('edit.imgEditorUnavailable'), 'error');
                    return;
                }
                const val = ta.value;
                const start = ta.selectionStart, end = ta.selectionEnd;
                let s = start, e = end;
                // 若选区不完整，扩展选中光标所在/覆盖的整个 ![...](...) 图片语法
                const imgRe = /!\[[^\]]*\]\(([^)\s]+)(?:[ ]+=[0-9x]+)?\)/g;
                let img = null;
                if (s !== e) {
                    // 选区内容优先当整体匹配
                    const sel = val.slice(s, e);
                    const mm = imgRe.exec(sel);
                    if (mm) img = { url: mm[1], s, e };
                }
                if (!img) {
                    // 扩展选区到包含光标处的完整图片语法
                    imgRe.lastIndex = 0;
                    let m;
                    while ((m = imgRe.exec(val)) !== null) {
                        if (m.index <= start && start <= m.index + m[0].length) {
                            img = { url: m[1], s: m.index, e: m.index + m[0].length };
                            break;
                        }
                    }
                }
                if (!img) {
                    showKeyStatus(t('edit.selectImageCode'), 'error');
                    return;
                }
                const oldUrl = img.url;
                window.imageTools.openCropFromUrl(oldUrl, function (newUrl) {
                    // 替换选中的图片整体（含尺寸后缀）为编辑结果
                    const oldMarkdown = val.slice(img.s, img.e);
                    ta.value = val.slice(0, img.s) + oldMarkdown.replace(oldUrl, newUrl) + val.slice(img.e);
                    _render();
                });
            }

            // 把 textarea 中引用了旧 url 的 markdown 图片替换为新 url，并重渲染
            function _replaceImageUrl(oldUrl, newUrl) {
                const ta = _textarea(); if (!ta) return;
                const esc = oldUrl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const re = new RegExp('(!\\[[^\\]]*\\]\\()' + esc + '(?:[ ]+=[0-9x]+)?(\\))', 'g');
                ta.value = ta.value.replace(re, '$1' + newUrl + '$2');
                _render();
            }

            // 工具栏“插入图片”：选文件 → 上传 → 光标处插入（不弹模态，光标不丢）
            function insertImage() {
                const ta = _textarea(); if (!ta) return;
                ta.focus(); // 确保有光标位置
                if (typeof window.imageTools === 'undefined' || !imageTools.chooseFile) return;
                imageTools.chooseFile(function (file) {
                    if (!file) return;
                    imageTools.uploadBlob(file, file.name, file.type, function (url) {
                        _insertImageMarkdown(url);
                    });
                });
            }

            // 粘贴剪贴板图片 → 上传 → 光标处插入（光标保持）
            function initPaste() {
                const ta = _textarea(); if (!ta) return;
                if (ta.dataset.pasteBound) return;   // 防重复绑定（否则多次打开浮窗会多次上传）
                ta.dataset.pasteBound = '1';
                ta.addEventListener('paste', function (e) {
                    const items = e.clipboardData && e.clipboardData.items;
                    if (!items) return;
                    for (let i = 0; i < items.length; i++) {
                        if (items[i].type && items[i].type.indexOf('image/') === 0) {
                            e.preventDefault();
                            const file = items[i].getAsFile();
                            if (file && typeof window.imageTools !== 'undefined') {
                                imageTools.uploadBlob(file, file.name || 'pasted.png', file.type, function (url) {
                                    _insertImageMarkdown(url);
                                });
                            }
                            break;
                        }
                    }
                });
                // 拖入任意文件 → 上传 → 按类型插入（图片走 ![]()，其它走 {{file:}} 附件卡片）
                ta.addEventListener('dragover', function (e) { e.preventDefault(); });
                ta.addEventListener('drop', function (e) {
                    const files = e.dataTransfer && e.dataTransfer.files;
                    if (!files || !files.length) return;
                    e.preventDefault();
                    for (let i = 0; i < files.length; i++) {
                        _uploadAndInsert(files[i]);
                    }
                });
            }

            // ── 任意文件上传 → 光标处插入 ──
            // 图片 → ![name](url)；其它（pdf/docx/xlsx…）→ {{file:file_id:文件名}} 附件卡片
            function _uploadAndInsert(file) {
                if (typeof window.imageTools === 'undefined') return;
                if (typeof showPassiveToast === 'function') showPassiveToast(t('edit.uploading') + (file.name || '') + ' …');
                imageTools.uploadFileRaw(file, function (url) {
                    if (!url) { if (typeof showPassiveToast === 'function') showPassiveToast(t('edit.uploadFail')); return; }
                    if (file.type && file.type.indexOf('image/') === 0) {
                        _insertImageMarkdown(url);
                    } else {
                        const m = String(url).match(/\/api\/file\/([0-9a-f]+)/i);
                        const fid = m ? m[1] : '';
                        if (!fid) return;
                        _insertAtCursor('\n{{file:' + fid + ':' + (file.name || t('edit.fileFallback')) + '}}\n');
                        if (typeof showPassiveToast === 'function') showPassiveToast(t('edit.fileInserted'));
                    }
                });
            }

            function insertFile() {
                const ta = _textarea(); if (!ta) return;
                ta.focus();
                const inp = document.createElement('input');
                inp.type = 'file';
                // 支持多选：逐个上传并依次插入（用户要求笔记附件可一次选多个）
                inp.multiple = true;
                inp.onchange = function () {
                    const files = Array.from(inp.files || []);
                    if (!files.length) return;
                    // 顺序串行上传，保证插入顺序与选择顺序一致
                    files.reduce(function (chain, f) {
                        return chain.then(function () { return _uploadAndInsert(f); });
                    }, Promise.resolve());
                };
                inp.click();
            }

            function insertTable() {
                const skeleton = '\n<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;margin:6px 0;">\n' +
                    '<tr><th>' + t('edit.col1') + '</th><th>' + t('edit.col2') + '</th><th>' + t('edit.col3') + '</th></tr>\n' +
                    '<tr><td></td><td></td><td></td></tr>\n' +
                    '<tr><td></td><td></td><td></td></tr>\n</table>\n';
                _insertAtCursor(skeleton);
            }

            function _insertAtCursor(text) {
                const ta = _textarea(); if (!ta) return;
                ta.focus();
                const s = ta.selectionStart || 0, e = ta.selectionEnd || 0;
                ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
                ta.selectionStart = ta.selectionEnd = s + text.length;
                _pushHistory();
                _render();
            }

            function applySize() {
                const v = _sizeSel() ? _sizeSel().value : '';
                if (!v) return;
                _wrap('<span style="font-size:' + v + '">', '</span>');
            }

            return { open, save, cancel, wrap: _wrap, line: _line, link, applyColor, applySize, insertImage, insertFile, insertTable, initPaste, undo, redo, editSelectedImage };
        })();

        // 暴露给内联工具栏按钮
        window.__feWrap = function (b, a) { floatingEditor.wrap(b, a); };
        window.__feLine = function (p) { floatingEditor.line(p); };
        window.__feLink = function () { floatingEditor.link(); };
        window.__feColor = function () { floatingEditor.applyColor(); };
        window.__feSize = function () { floatingEditor.applySize(); };
        window.__feInsertImage = function () { floatingEditor.insertImage(); };
        window.__feInsertFile = function () { floatingEditor.insertFile(); };
        window.__feInsertTable = function () { floatingEditor.insertTable(); };
        window.__feEditImage = function () { floatingEditor.editSelectedImage(); };
        window.__feUndo = function () { floatingEditor.undo(); };
        window.__feRedo = function () { floatingEditor.redo(); };
        window.__feSave = function () { floatingEditor.save(); };

        // ── 字号/着色复合按钮的下拉面板（Word 式：箭头展开选择）──
        window.__feDropdown = function (type, anchorBtn) {
            const menu = document.getElementById('fe-dropdown');
            if (!menu) {
                const m = document.createElement('div');
                m.id = 'fe-dropdown';
                m.style.cssText = 'position:fixed;z-index:100001;background:#fff;border:1px solid #ccc;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,0.2);padding:6px;display:none;min-width:120px;';
                document.body.appendChild(m);
            }
            const exist = document.getElementById('fe-dropdown');
            // 若已打开同类型 → 关闭
            if (exist.style.display !== 'none' && exist.dataset.type === type) { exist.style.display = 'none'; return; }
            exist.dataset.type = type;
            // 定位到箭头按钮下方
            var r = anchorBtn.getBoundingClientRect();
            exist.style.left = (r.left) + 'px';
            exist.style.top = (r.bottom + 4) + 'px';
            // 填充选项
            if (type === 'font') {
                const opts = [['0.8em',t('edit.sizeSmall')],['1em',t('edit.sizeNormal')],['1.2em',t('edit.sizeLarge')],['1.5em',t('edit.sizeXLarge')]];
                exist.innerHTML = opts.map(o => {
                    const cur = (window.floatingEditor && window.document.getElementById('fe-fontsize')) ? document.getElementById('fe-fontsize').value : '';
                    const sel = (cur === o[0]) ? 'style="background:#e6f0ff"' : '';
                    return '<div data-v="'+o[0]+'" onclick="__fePickFont(\''+o[0]+'\')" style="padding:5px 10px;cursor:pointer;border-radius:5px;font-size:0.85rem;'+sel+'">'+o[1]+'</div>';
                }).join('');
            } else {
                const colors = ['#e02020','#ff6b00','#ffcc00','#2ecc40','#00a8e8','#0052cc','#8b00ff','#000000','#888888'];
                exist.innerHTML = colors.map(c => {
                    return '<div title="'+c+'" onclick="__fePickColor(\''+c+'\')" style="display:inline-block;width:24px;height:24px;background:'+c+';border-radius:4px;cursor:pointer;margin:3px;border:1px solid #ddd;"></div>';
                }).join('');
            }
            exist.style.display = 'block';
        };
        window.__fePickFont = function (v) {
            const s = document.getElementById('fe-fontsize');
            if (s) { s.value = v; }
            // 同步更新字号按钮的值显示（把 em 值映射为档名）
            const valEl = document.getElementById('fe-font-value');
            if (valEl) { valEl.textContent = __feFontLabel(v); }
            document.getElementById('fe-dropdown').style.display = 'none';
        };
        function __feFontLabel(v) {
            var m = { '0.8em':t('edit.sizeSmall'), '1em':t('edit.sizeNormal'), '1.2em':t('edit.sizeLarge'), '1.5em':t('edit.sizeXLarge') };
            return m[v] || (v ? v.replace('em','') : t('edit.sizeNormal'));
        }
        window.__fePickColor = function (c) {
            const inp = document.getElementById('fe-color');
            if (inp) { inp.value = c; }
            // 同步更新着色按钮下的色条（显示当前选择颜色）
            const bar = document.getElementById('fe-color-bar');
            if (bar) { bar.style.background = c; }
            document.getElementById('fe-dropdown').style.display = 'none';
        };
        window.__feCancel = function () { floatingEditor.cancel(); };
        window.floatingEditor = floatingEditor;

        // ==================== 编辑消息（迁移到浮动编辑器 + 实时预览到真实消息框） ====================
        function updateMessageElContent(messageId, html) {
            // 只更新正文容器（.msg-body），保留附件/推理/tokenInfo，避免编辑时附件消失
            const bodyEl = document.querySelector(`[data-id="${messageId}"] .message-content .msg-body`);
            if (!bodyEl) {
                // 无 msg-body 容器（旧消息）时回退整段 content 刷新
                const messageEl = document.querySelector(`[data-id="${messageId}"] .message-content`);
                if (messageEl) messageEl.innerHTML = html;
                else return;
                return;
            }
            bodyEl.innerHTML = html;
        }

        // 检查 file_id 是否还被其他消息 / 历史气泡引用（决定能否安全删除后端文件）
        function _fileIdReferencedElsewhere(fileId, exceptMessageId) {
            for (var bid in state.branches) {
                var br = state.branches[bid];
                var msgs = br.messages || [];
                for (var i = 0; i < msgs.length; i++) {
                    var m = msgs[i];
                    if (m.id === exceptMessageId || !m.attachments) continue;
                    for (var j = 0; j < m.attachments.length; j++) {
                        if (m.attachments[j].file_id === fileId) return true;
                    }
                }
                var bubbles = br._bubbles || [];
                for (var k = 0; k < bubbles.length; k++) {
                    if (bubbles[k].fileId === fileId) return true;
                }
            }
            return false;
        }

        // 删除单个附件：删后端文件 + 移除消息引用 + 重渲染
        function removeAttachmentFromMessage(messageId, fileId) {
            const branch = state.branches[state.currentBranchId];
            const msg = (branch && branch.messages) ? branch.messages.find(m => m.id === messageId) : null;
            if (!msg || !msg.attachments) return;
            const idx = msg.attachments.findIndex(a => a.file_id === fileId);
            if (idx === -1) return;
            msg.attachments.splice(idx, 1);
            // 同步清理 _fileIds（部分消息用它记录）
            if (msg._fileIds) {
                const fi = msg._fileIds.indexOf(fileId);
                if (fi !== -1) msg._fileIds.splice(fi, 1);
            }
            saveBranches();
            // 后端删除（仍被其他消息/气泡引用时保留文件）
            if (_fileIdReferencedElsewhere(fileId, messageId)) {
                                console.info('[Attachment] file_id is still referenced by other messages; keeping the backend file:', fileId);
            } else {
                fetch('/api/file/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_id: fileId })
                                }).catch(function(e) { console.warn('Failed to delete backend attachment', fileId, e); });
            }
            // 重渲染消息列表（附件移除后刷新显示）
            if (typeof renderMessages === 'function') renderMessages();
            showKeyStatus(t('edit.attDeleted'), 'success');
        }
        function editMessage(messageId) {
            const currentBranch = state.branches[state.currentBranchId];
            const message = currentBranch.messages.find(m => m.id === messageId);
            if (!message) return;
            const originalContent = message.content;
            floatingEditor.open({
                title: message.role === 'assistant' ? t('edit.titleAi') : t('edit.titleMsg'),
                value: message.content,
                // 实时预览：把编辑中的 markdown 实时渲染到真实消息框（不持久化）
                liveUpdate: function (html) { updateMessageElContent(messageId, html); },
                // 取消回退：恢复为打开时的原始内容
                liveRestore: function () { updateMessageElContent(messageId, formatMessage(originalContent)); },
                onSave: function (newContent) {
                    const text = newContent.trim();
                    if (!text) {
                        showKeyStatus(t('edit.empty'), 'error');
                        return false;
                    }
                    const idx = currentBranch.messages.findIndex(m => m.id === messageId);
                    if (idx !== -1) {
                        currentBranch.messages[idx].content = text;
                        saveBranches();
                        // ★ 报给后端（账本 edit 补偿）→ 后端重新投影 → 别的设备也看到这次编辑
                        if (typeof window.pushChatOps === 'function') {
                            window.pushChatOps(state.currentBranchId, [{ type: 'edit', target: messageId, text: text }]);
                        }
                        updateMessageElContent(messageId, formatMessage(text));
                        if (currentBranch.messages[idx].role === 'user') {
                            const nextMessage = currentBranch.messages[idx + 1];
                            if (nextMessage && nextMessage.role === 'assistant') {
                                showKeyStatus(t('edit.updated'), 'success');
                            }
                        }
                    }
                }
            });
        }

        // 兼容保留（旧内联编辑已迁移到浮动编辑器）
        function saveMessageEdit(messageId) {}
        function cancelMessageEdit(messageId) {}

        function copyMessage(messageId) {
            const currentBranch = state.branches[state.currentBranchId];
            const message = currentBranch.messages.find(m => m.id === messageId);

            if (message) {
                navigator.clipboard.writeText(message.content)
                    .then(() => showKeyStatus(t('msg.copied'), 'success'))
                    .catch(err => showKeyStatus(t('msg.copyFail') + err.message, 'error'));
            }
        }

        // ==================== 修复重新生成功能 ====================
        async function regenerateMessage(messageId) {
            const currentBranch = state.branches[state.currentBranchId];
            const messageIndex = currentBranch.messages.findIndex(m => m.id === messageId);
            if (messageIndex === -1) return;

            const message = currentBranch.messages[messageIndex];

            if (message.role === 'user') {
                 // 重建用户消息（含附件内容）
                 let userMessage = message.content;
                 if (message._attachmentContent) {
                     userMessage += '\n\n' + message._attachmentContent;
                 }

                 // 截断后续对话（删除当前消息之后的所有消息）
                 await deleteMessagesAfter(messageIndex);

                 // 重新生成 — 智能体自动决策，不走旧的知识库模式
                 await reactAgentLoop(userMessage, { client_msg_id: message.id });

             } else if (message.role === 'assistant') {
                 const prevMessage = currentBranch.messages[messageIndex - 1];
                 if (prevMessage && prevMessage.role === 'user') {
                     // 重建用户消息（含附件内容）
                     let userMessage = prevMessage.content;
                     if (prevMessage._attachmentContent) {
                         userMessage += '\n\n' + prevMessage._attachmentContent;
                     }

                     // 截断对话（删除当前消息及后续所有消息）
                     await deleteMessagesAfter(messageIndex - 1);

                     await reactAgentLoop(userMessage, { client_msg_id: prevMessage.id });
                 }
             }
        }

        function deleteMessagesAfter(startIndex) {
            const currentBranch = state.branches[state.currentBranchId];
            if (startIndex >= currentBranch.messages.length - 1) return;

            // 获取要删除的消息ID列表
            const messagesToDelete = currentBranch.messages.slice(startIndex + 1);

            // 删除后端文件（_fileIds + attachments.file_id）
            messagesToDelete.forEach(msg => {
                var fileIdsToDelete = msg._fileIds || [];
                // 也检查 attachments 里的 file_id
                if (msg.attachments && msg.attachments.length > 0) {
                    msg.attachments.forEach(function(a) {
                        if (a.file_id && !fileIdsToDelete.includes(a.file_id)) {
                            fileIdsToDelete.push(a.file_id);
                        }
                    });
                }
                fileIdsToDelete.forEach(function(fid) {
                    fetch('/api/file/delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ file_id: fid })
                    }).catch(function(e) {
                        console.warn('[Delete] Failed to remove file from backend:', fid, e.message);
                    });
                });
            });

            // 扣除 tokens
            messagesToDelete.forEach(msg => {
                if (msg.tokens > 0) {
                    currentBranch.totalTokens -= msg.tokens;
                    currentBranch.sessionTokens -= msg.tokens;
                }
            });

            // 截断数组前，先删除被删消息关联的后端文件
            var deletedMsgs = currentBranch.messages.slice(startIndex + 1);
            for (var dmi = 0; dmi < deletedMsgs.length; dmi++) {
                var dm = deletedMsgs[dmi];
                if (dm.attachments) {
                    for (var ai = 0; ai < dm.attachments.length; ai++) {
                        var att = dm.attachments[ai];
                        if (att.file_id) {
                            fetch('/api/file/delete', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ file_id: att.file_id })
                            }).catch(function(){});
                        }
                    }
                }
            }

            // 截断数组
            const _cutFirst = currentBranch.messages[startIndex + 1];
            currentBranch.messages = currentBranch.messages.slice(0, startIndex + 1);

            // 更新 hasContent
            const hasUserMessages = currentBranch.messages.some(msg => msg.role === 'user' && !msg.isSystem);
            currentBranch.hasContent = hasUserMessages;

            saveBranches();
            // ★ 报给后端（账本 truncate 补偿）→ 后端重新投影 → 别的设备也看到这次删除
            //   返回 promise：重新生成要先等它落账再发新消息，避免新旧轮次竞争投影。
            if (_cutFirst && typeof window.pushChatOps === 'function') {
                return window.pushChatOps(state.currentBranchId, [{
                    type: 'truncate', from: _cutFirst.id, from_ts: _cutFirst.timestamp || ''
                }]);
            }
            return Promise.resolve();
            updateTokenDisplay();

            // 重新渲染整个消息列表以反映删除
            renderMessages();
        }

        function branchFromMessage(messageId) {
            const currentBranch = state.branches[state.currentBranchId];
            const messageIndex = currentBranch.messages.findIndex(m => m.id === messageId);
            if (messageIndex === -1) return;

            const messagesBefore = currentBranch.messages.slice(0, messageIndex + 1);

            // 创建新对话（★ 2026-09-30：createNewConversation 建的就是**永久**对话，
            //   不再需要 saveTempConversation 那套「临时对话转正 + 重新分配编号」）
            createNewConversation();

            // 复制消息到新对话（branchId 改指新对话，避免全局视图把消息归到旧对话）
            const newBranch = state.branches[state.currentBranchId];
            newBranch.messages = messagesBefore.map(function (m) {
                return Object.assign({}, m, { branchId: newBranch.id });
            });
            newBranch.hasContent = true;

            // 沿用原行为：用第一条用户消息（前 20 字）命名新分支
            const firstUserMessage = messagesBefore.find(m => m.role === 'user' && m.content);
            if (firstUserMessage) {
                const text = String(firstUserMessage.content).trim();
                newBranch.name = text.substring(0, 20) + (text.length > 20 ? '...' : '');
            }

            saveBranches();
            renderMessages();
            updateHistoryList();
            addSystemMessageWithDelete(t('msg.branched'));
        }

        function deleteMessage(messageId) {
            if (!confirm(t('msg.confirmDelMsg'))) return;
            const currentBranch = state.branches[state.currentBranchId];
            const messageIndex = currentBranch.messages.findIndex(m => m.id === messageId);
            if (messageIndex === -1) return;

            const message = currentBranch.messages[messageIndex];

            // 删除该消息关联的后端附件（仍被其他消息/气泡引用时保留）
            var fileIdsToDelete = message._fileIds || [];
            if (message.attachments && message.attachments.length > 0) {
                message.attachments.forEach(function(a) {
                    if (a.file_id && !fileIdsToDelete.includes(a.file_id)) {
                        fileIdsToDelete.push(a.file_id);
                    }
                });
            }
            fileIdsToDelete.forEach(function(fid) {
                if (_fileIdReferencedElsewhere(fid, messageId)) {
                                        console.info('[DeleteMessage] file_id is still referenced by other messages; keeping the backend file:', fid);
                    return;
                }
                fetch('/api/file/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_id: fid })
                }).catch(function(e) {
                    console.warn('[DeleteMessage] Failed to remove file from backend:', fid, e.message);
                });
            });

            if (message.tokens > 0) {
                currentBranch.totalTokens -= message.tokens;
                currentBranch.sessionTokens -= message.tokens;
                updateTokenDisplay();
            }

            currentBranch.messages.splice(messageIndex, 1);

            // ★ 后端接管的对话：单条删除必须报给后端（账本 delete 补偿），
            //   否则下次同步后端把消息推回来（删除刷新复活）。
            if (typeof window.isBranchManaged === 'function' && window.isBranchManaged(state.currentBranchId)) {
                window.pushChatOps(state.currentBranchId, [{ type: 'delete', target: messageId }]);
            }

            // 检查对话是否还有内容
            const hasUserMessages = currentBranch.messages.some(msg => msg.role === 'user' && !msg.isSystem);
            currentBranch.hasContent = hasUserMessages;

            saveBranches();

            const messageEl = document.querySelector(`[data-id="${messageId}"]`);
            if (messageEl) {
                messageEl.remove();
            }

            updateHistoryList();
        }

        // ==================== 系统消息删除函数 ====================

        // ==================== 对话功能 ====================
        async function sendMessage() {
            const message = messageInput.value.trim();
            // 待发引用（笔记/数据库条目）
            let pendingRefs = (window.noteRefs && typeof window.noteRefs.peek === 'function')
                ? window.noteRefs.peek() : ((state.pendingNoteRefs || []).slice());

            // 允许纯附件 / 纯引用发送（无文字时也能发送）
            const hasAttachments = state.documentUpload.pendingAttachments.length > 0;
            if (!message && !hasAttachments && pendingRefs.length === 0) return;
            if (!state.currentModel) {
                addSystemMessageWithDelete(t('msg.selectModel'));
                return;
            }
            if (!state.apiKeys[state.currentModel]) {
                addSystemMessageWithDelete(t('msg.noApiKey'));
                return;
            }

            // 无当前对话（刚打开/删光后）→ 自动新建一个对话再发送
            if (!state.currentBranchId || !state.branches[state.currentBranchId]) {
                createNewConversation();
            }

            // 处理附件
                 let attachmentsMetadata = [];
                 let inlineNotes = '';   // 保底：后端已不需要前端补正文，通常为空

                 if (state.documentUpload.pendingAttachments.length > 0) {
                     showToast(t('msg.processingAtt'), 'info');

                     for (const att of state.documentUpload.pendingAttachments) {
                         try {
                             // 收集 file_id
                             var fileId = att.file_id || '';
                             var result = att._parsedText || '';

                             // 如果既没有 file_id 也没解析过（正常上传的文件）
                             if (!fileId && att.file) {
                                 var formData = new FormData();
                                 formData.append('file', att.file);
                                 var resp = await fetch('/api/file/upload', {
                                     method: 'POST', body: formData
                                 }).then(function(r) { return r.json(); }).catch(function() { return {}; });
                                 if (resp && resp.file_id) {
                                     fileId = resp.file_id;
                                     result = resp.text || '';
                                 }
                             }

                             // 图片不再自动转 base64 多模态、不再内嵌 OCR 文字（用户 2026-10-01 定）：
                             // 图片与文档同权 —— 只带 file_id，模型需要时自己 read_file(file_id=…) 读像素

                             if (!fileId) {
                                 // 历史重发但后端文件已被删除等情况：明确提示而不是静默丢弃
                                 showToast(t('msg.attLost').replace('{name}', att.name), 'error');
                                 continue;
                             }

                             // 只要有 file_id 就交给后端：正文/像素一律由模型按需 read_file 自取，前端不做任何内嵌
                             attachmentsMetadata.push({
                                 id: att.id,
                                 name: att.name,
                                 size: att.size,
                                 type: att.type,
                                 icon: att.icon,
                                 file_id: fileId
                             });

                         } catch (error) {
                                                          console.error(`Failed to process file ${att.name}:`, error);
                             showToast(t('msg.attProcessFail').replace('{name}', att.name), 'error');
                         }
                     }
                 }

                 // 组合用户消息：正文只含用户文字；文件内容由模型按 file_ids 自取
                 const refsMeta = pendingRefs.map(function (r) { return { id: r.id, scope: r.scope, title: r.title }; });
                 if (!message && attachmentsMetadata.length === 0 && refsMeta.length === 0) {
                     showToast(t('msg.nothingToSend'), 'error');
                     return;
                 }
                 let displayContent = message;
                 if (!displayContent && attachmentsMetadata.length > 0) {
                     displayContent = t('msg.attOnly').replace('{list}', attachmentsMetadata.map(a => a.name).join(t('msg.listSep')));
                 }
                 if (!displayContent && refsMeta.length > 0) {
                     displayContent = t('msg.refOnly').replace('{list}', refsMeta.map(r => r.title || r.id).join(t('msg.listSep')));
                 }
                 let fullMessage = displayContent;
                 if (inlineNotes) {
                     fullMessage = displayContent + '\n\n' + inlineNotes;
                 }

                 // 添加用户消息（附件只存元数据，不再把文件全文写入 localStorage）
                 const _localMsgId = addMessage('user', displayContent, 0, false, null, null, attachmentsMetadata, refsMeta);
                 messageInput.value = '';
                 expandedTextarea.value = '';
                 collapseExpandMode();

                 // 清空待发送附件（仅清空成功处理的）与待发引用
                 state.documentUpload.pendingAttachments = []; state.documentUpload._errorFiles = [];
                 updateSelectedFilesList();
                 hideUploadPanel();
                 if (window.noteRefs) window.noteRefs.clear();

                 // 发送消息 — 智能体自动决策
                 // 不再有"知识库模式"/"图谱模式"的手动切换
                 // ⚠️ 必须把附件 file_id 传下去：后端据此注入文件正文（此前漏传 →
                 //    file_ids 恒为空 → 附件内容没进 LLM → 表现为"答非所问/未能生成回答"）
                 await reactAgentLoop(fullMessage, {
                     note_refs: refsMeta,
                     fileIds: attachmentsMetadata.map(function (a) { return a.file_id; }).filter(Boolean),
                     client_msg_id: _localMsgId
                 });
        }

        function getRecentMessagesForContext(messages) {
            const maxMessages = state.settings.contextLength * 2;

            // 过滤并处理消息
            const processedMessages = messages
                .filter(m => {
                    if (!m || m.isSystem) return false;
                    if (m.role !== 'user' && m.role !== 'assistant' && m.role !== 'system') return false;
                    if ((m.role === 'user' || m.role === 'assistant') && !String(m.content || '').trim()) return false;
                    return true;
                })
                .map(m => {
                if (m.type === 'document') {
                    // 如果是文档，确保内容被包含
                    return {
                        role: 'system',
                        content: `以下是用户上传的文档内容 [${m.name || '文档'}]:\n\n${m.fullContent || m.content}`
                    };
                }
                // 如果有附件内容，追加到消息中（确保重新生成／连续对话时附件上下文不丢失）
                let content = m.content || '';
                if (m._attachmentContent) {
                    content += '\n\n' + m._attachmentContent;
                }
                return { role: m.role, content: content };
            });

            if (processedMessages.length <= maxMessages) {
                return processedMessages;
            }

            return processedMessages.slice(-maxMessages);
        }

        // ==================== 辅助函数 ====================

// 智能体推理：每步默认收成几行（CSS 限高），点该步标题展开/收起（覆盖 messages.js 与 graph_rag.js 两处渲染）
document.addEventListener('click', function (e) {
    var h = e.target.closest && e.target.closest('.reasoning-steps-container .agent-step-header');
    if (!h) return;
    var n = h.nextElementSibling;
    if (!n) return;
    n.classList.toggle('expanded');
    h.classList.toggle('open');
});

// ==================== 对话区右侧「收起思考」浮动键 ====================
// 判据取客观事实：DOM 里存在「未收起」的思考块，且它与消息区可视范围相交 ——
// 也就是"此刻屏幕上真的有一片思考"，键才出现；全部收起、或思考块被滚出视野，就隐藏。
// 只看"对话里有没有思考块"是不够的（收起后屏幕上只剩一行提示，键却还挂着，用户 2026-10-02 指出）。
function reasoningRailVisible() {
    var mc = document.getElementById('messages-container');
    if (!mc) return false;
    var vr = mc.getBoundingClientRect();
    var blocks = mc.querySelectorAll('.agent-reasoning-block:not(.collapsed)');
    for (var i = 0; i < blocks.length; i++) {
        var r = blocks[i].getBoundingClientRect();
        if (r.height > 0 && r.bottom > vr.top && r.top < vr.bottom) return true;   // 与可视范围相交
    }
    return false;
}

function updateReasoningRail() {
    var btn = document.getElementById('reasoning-rail-btn');
    if (!btn) return;
    btn.hidden = !reasoningRailVisible();
}

window.updateReasoningRail = updateReasoningRail;

(function () {
    var btn = document.getElementById('reasoning-rail-btn');
    var mc = document.getElementById('messages-container');
    if (!btn || !mc) return;

    // 点一下 = 把屏幕上的思考全部收起（收起后键自己消失；单条的展开用消息里那行提示）
    btn.addEventListener('click', function () {
        var blocks = mc.querySelectorAll('.agent-reasoning-block:not(.collapsed)');
        Array.prototype.forEach.call(blocks, function (b) { b.classList.add('collapsed'); });
        updateReasoningRail();
    });

    // 滚动/改窗口大小都会改变"屏幕上有没有思考"，跟着重判（rAF 节流，一次事件只算一帧）
    var queued = false;
    function onMove() {
        if (queued) return;
        queued = true;
        requestAnimationFrame(function () { queued = false; updateReasoningRail(); });
    }
    mc.addEventListener('scroll', onMove, { passive: true });
    window.addEventListener('resize', onMove);
})();
