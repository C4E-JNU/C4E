/* C4EAI 账号系统前端 v5：fetch 注入 token、登录/注册框、用户芯片（切换账号）、管理员面板（授权/删号）
 * 身份命名空间：聊天记录/文档库按当前身份隔离（identityKey），切换账号=整页刷新。
 */
(function () {
    'use strict';
    var TOKEN_KEY = 'c4eai_token';
    var USER_KEY = 'c4eai_user';

    // ── 身份命名空间（须在 branching/sidebar/database 之前定义好；auth.js 在它们之前加载）──
    function _identity() {
        try {
            var u = JSON.parse(localStorage.getItem(USER_KEY) || 'null');
            if (u && u.username) return u.username;
        } catch (e) {}
        return 'guest';
    }
    var _id = _identity();
    localStorage.setItem('c4eai_identity', _id);
    window.C4EAI_IDENTITY = _id;
    window.identityKey = function (k) { return k + '::' + _id; };
    // 旧数据一次性归到 guest/guest 命名空间
    if (_id === 'guest') {
        // 旧数据一次性归到 guest 命名空间；复制完把老键删掉（老键不再被任何代码读，
        // 留着白占 localStorage 配额 —— 用户 2026-09-27 报「本地缓存空间不足」就是配额满了）。
        ['ai-current-branch', 'branchOrder', 'ai-expanded-groups', 'ai-database'].forEach(function (k) {
            var nk = k + '::guest';
            if (localStorage.getItem(nk) == null && localStorage.getItem(k) != null) {
                localStorage.setItem(nk, localStorage.getItem(k));
            }
            localStorage.removeItem(k);
        });
        // 对话缓存老键（`ai-branches`）：内容在本机后端，直接删（见 js/user_chats.js）
        ['ai-branches', 'ai-branches-deleted'].forEach(function (k) { localStorage.removeItem(k); });
    }

    window.getAuthToken = function () { return localStorage.getItem(TOKEN_KEY) || ''; };
    window.getAuthUser = function () {
        try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) { return null; }
    };
    function setAuth(token, user) {
        localStorage.setItem(TOKEN_KEY, token);
        _sessionExpired = false;   // 重新登录成功：清掉「会话已过期」标记
        localStorage.setItem(USER_KEY, JSON.stringify(user));
    }
    function clearAuth() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
    }

    // ── fetch 注入：同源 /api 自动带 token；401 弹登录框 ──
    var _origFetch = window.fetch.bind(window);
    var _loginOpen = false;
    var _sessionExpired = false;   // 共享库会话过期（代理请求 401）：芯片常驻标红，登录成功后清掉
    window.fetch = function (url, opts) {
        opts = opts || {};
        try {
            var u = typeof url === 'string' ? url : (url && url.url) || '';
            if (u.indexOf('/api/') === 0) {
                var tok = window.getAuthToken();
                if (tok) opts.headers = Object.assign({}, opts.headers, { 'Authorization': 'Bearer ' + tok });
            }
        } catch (e) {}
        return _origFetch(url, opts).then(function (r) {
            // 401：先**静默复检**一次会话，只有「确实没登录 / 会话真失效」才弹登录框。
            // 为什么（用户 2026-09-27 报「用着用着突然弹登录」）：本机实例的对话/笔记/设置/SSE
            // 全是代理到共享库的，共享库会话一过期，后台自动推送（节流保存、SSE 重连）就 401 ——
            // 以前这里直接弹框，表现为「明明登录过却莫名被弹窗打断」。代理请求的 401 不该打断用户。
            // 说明：/api/auth/* 自身（如密码错）的 401 有它自己的错误提示，不走这里。
            if (r.status === 401 && !_loginOpen && (u || '').indexOf('/api/auth/') !== 0) {
                var _isMobile = false;
                try { _isMobile = window.matchMedia('(max-width: 768px)').matches; } catch (e) {}
                if (_isMobile) {
                    _markSessionExpired();      // 手机端不弹（会遮屏）：只标红顶栏芯片
                } else {
                    _loginOpen = true;
                    _recheckSessionThenMaybeLogin();
                }
            }
            return r;
        });
    };

    /** 401 后静默问一次 /api/auth/me：它也 401 → 真没登录，弹框；它正常 → 只是那条代理请求的 401
     *  （共享库会话过期）→ 不打断，轻提示 + 顶栏芯片标红，点芯片即可重新登录。 */
    function _recheckSessionThenMaybeLogin() {
        var h = {};
        try { var _t = window.getAuthToken(); if (_t) h.Authorization = 'Bearer ' + _t; } catch (e) {}
        _origFetch('/api/auth/me', { headers: h }).then(function (r2) {
            return r2.status === 401 ? 'expired' : 'ok';
        }).catch(function () { return 'unknown'; }).then(function (st) {
            if (st === 'expired') { _openLogin(true); return; }
            _loginOpen = false;       // 不是会话问题 → 放开，日后真 401 仍能弹
            if (st === 'ok') _markSessionExpired();
        });
    }

    /** 标记「共享库会话已过期」：置状态位 → 重渲染顶栏芯片（标红/改成「重新登录」）→ 一条轻提示。
     *  放在状态位里而不是只改一次 DOM，是因为 _renderChip 会被多处重渲染，改 DOM 会被冲掉（踩过）。 */
    function _markSessionExpired() {
        if (_sessionExpired) return;        // 已经标过，别重复弹提示
        _sessionExpired = true;
        _renderChip();
        if (typeof showToast === 'function') showToast('共享库会话已过期，点右上角身份芯片重新登录', 'error');
    }

    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── 顶栏高度同步到 CSS 变量 ──
    // 认证遮罩用 --c4e-header-h 从顶栏下方开始显示；顶栏高度可能因字号/全屏模式变化，
    // 故用 JS 实测并写入变量（改布局也不会再出现"遮罩吃掉顶栏点击"的问题）。
    function _syncHeaderHeight() {
        try {
            var h = document.querySelector('header');
            if (!h) return;
            var rect = h.getBoundingClientRect();
            var px = Math.max(0, Math.round(rect.height || 0));
            if (px > 0) {
                document.documentElement.style.setProperty('--c4e-header-h', px + 'px');
            }
        } catch (e) { /* 忽略 */ }
    }
    window.syncHeaderHeight = _syncHeaderHeight;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _syncHeaderHeight);
    } else {
        setTimeout(_syncHeaderHeight, 0);
    }
    window.addEventListener('resize', _syncHeaderHeight);
    setTimeout(_syncHeaderHeight, 400);

    // ── 登录/注册框 ──
    // closable=true（本地可选登录）：✕/点遮罩/Esc 可关闭；强制登录（云端）不可关闭
    // ⚠️ 遮罩必须避开顶栏：app.py/header 的 z-index 是 1000，遮罩是 4000；若遮罩铺满 inset:0
    //    会把顶栏（主题切换器/数据库按钮/登录芯片）整条吃掉点击 → 圆点"点不动"。
    //    故：① 遮罩从顶栏下方开始（inset:var(--c4e-header-h,56px) 0 0 0）
    //        ② 遮罩本身 pointer-events:none、只有卡片 auto（双保险，改顶栏高度也不会复发）
    var OV_LAYER_STYLE = 'position:fixed;inset:var(--c4e-header-h,56px) 0 0 0;background:rgba(0,0,0,.45);' +
        'z-index:4000;display:flex;align-items:center;justify-content:center;pointer-events:none;';
    var OV_CARD_STYLE = 'pointer-events:auto;';
    function _openLogin(closable) {
        // ── 手机端：只有「用户主动点登录」才弹框 ──
        // 自动弹（401 触发、切账号等）在手机上传参 closable=true 且非用户手势，
        // 会遮住整屏严重影响体验 → 手机端直接跳过，用户可点顶栏「登录」。
        try {
            if (window.matchMedia('(max-width: 768px)').matches && !window.__userAskedLogin) {
                return;
            }
        } catch (e) {}
        window.__userAskedLogin = false;   // 用完即清，避免影响后续
        var old = document.getElementById('auth-login-overlay');
        if (old) old.remove();
        var mode = 'login';   // login | register
        var ov = document.createElement('div');
        ov.id = 'auth-login-overlay';
        ov.innerHTML =
            '<div data-auth-mask style="' + OV_LAYER_STYLE + '">' +
                '<div style="' + OV_CARD_STYLE + 'width:346px;background:var(--card-bg,#fff);border:1px solid var(--border,#ddd);border-radius:14px;overflow:hidden;box-shadow:0 16px 44px rgba(0,0,0,.28);position:relative;display:flex;">' +
                    // 左：品牌栏（底色跟随主题）
                    '<div style="flex:0 0 94px;background:linear-gradient(160deg,var(--primary-soft,#e8f4fd),#e9f8f5);border-right:1px solid var(--border,#eee);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:9px;padding:14px 6px;text-align:center;">' +
                        '<img src="assets/icons/c4e-logo.png" alt="" style="width:34px;height:34px;border-radius:50%;object-fit:contain;background:#fff;padding:2px;box-sizing:border-box;">' +
                        '<div style="font-size:.64rem;line-height:1.35;color:var(--text-secondary,#5b6676);">江南大学<br>环境计算组</div>' +
                    '</div>' +
                    // 右：表单
                    '<div style="flex:1;min-width:0;padding:15px 15px 12px;">' +
                        '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:.4rem;">' +
                            '<div style="font-size:.95rem;font-weight:600;" id="auth-mode-title">登录</div>' +
                            (closable ? '<button id="auth-login-close" title="关闭" style="flex:0 0 auto;width:1.5rem;height:1.5rem;line-height:1;background:none;border:none;border-radius:6px;font-size:.95rem;cursor:pointer;color:var(--text-secondary,#999);">✕</button>' : '') +
                        '</div>' +
                        '<div style="display:flex;gap:14px;border-bottom:1px solid var(--border,#eef1f5);margin:.5rem 0 .75rem;">' +
                            '<button data-m="login" class="auth-tab" type="button">账号登录</button>' +
                            '<button data-m="register" class="auth-tab" type="button">注册</button>' +
                        '</div>' +
                        '<input id="auth-login-user" placeholder="用户名" autocomplete="username" style="width:100%;height:34px;padding:0 .6rem;border:1px solid var(--border,#ddd);border-radius:9px;margin-bottom:.5rem;box-sizing:border-box;background:var(--input-bg,#fbfcfd);">' +
                        '<input id="auth-login-pass" type="password" placeholder="密码" autocomplete="current-password" style="width:100%;height:34px;padding:0 .6rem;border:1px solid var(--border,#ddd);border-radius:9px;margin-bottom:.35rem;box-sizing:border-box;background:var(--input-bg,#fbfcfd);">' +
                        '<input id="auth-login-pass2" type="password" placeholder="确认密码" autocomplete="new-password" style="display:none;width:100%;height:34px;padding:0 .6rem;border:1px solid var(--border,#ddd);border-radius:9px;margin-bottom:.35rem;box-sizing:border-box;background:var(--input-bg,#fbfcfd);">' +
                        '<div id="auth-login-err" style="color:var(--danger,#d33);font-size:.76rem;min-height:1.05em;margin-bottom:.35rem;"></div>' +
                        '<button id="auth-login-btn" style="width:100%;height:34px;border:none;border-radius:9px;background:var(--primary,#4a7dff);color:#fff;cursor:pointer;font-size:.9rem;font-weight:600;">登 录</button>' +
                        '<div id="auth-login-hint" style="font-size:.72rem;color:var(--text-secondary,#888);margin-top:.6rem;text-align:center;line-height:1.4;">登录后可使用共享库与自己的数据库</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
        document.body.appendChild(ov);
        if (closable) {
            function _closeLogin() {
                ov.remove();
                _loginOpen = false;
                // 关掉登录框后，顶栏必须留一个身份入口（否则页面上再无登录/用户管理入口）
                try {
                    var slot = document.getElementById('auth-chip-slot');
                    if (slot && !slot.children.length) {
                        if (!window.getAuthUser()) setAuth('guest', { username: 'guest', role: 'guest' });
                        _renderChip();
                    }
                } catch (e) {}
            }
            var xc = document.getElementById('auth-login-close');
            if (xc) xc.addEventListener('click', _closeLogin);
            // 注：不再支持"点空白/遮罩关闭"（避免误触，也避免与顶栏元素抢点击）；仅 ✕ 与 Esc 关闭
            var _escH = function (e) { if (e.key === 'Escape') { _closeLogin(); document.removeEventListener('keydown', _escH); } };
            document.addEventListener('keydown', _escH);
        }
        var u = document.getElementById('auth-login-user');
        var p = document.getElementById('auth-login-pass');
        var btn = document.getElementById('auth-login-btn');
        var err = document.getElementById('auth-login-err');
        var hint = document.getElementById('auth-login-hint');
        var title = document.getElementById('auth-mode-title');

        function _setMode(m) {
            mode = m;
            title.textContent = m === 'login' ? '登录' : '注册新账号';
            btn.textContent = m === 'login' ? '登 录' : '注 册';
            hint.textContent = m === 'login'
                ? '登录后可使用共享库与自己的数据库'
                : '自由注册；注册后私人功能立即可用，数据库需管理员授权';
            err.textContent = '';
            var p2 = document.getElementById('auth-login-pass2');
            if (p2) {
                p2.style.display = (m === 'register') ? 'block' : 'none';
                if (m !== 'register') p2.value = '';   // 切回登录时清掉，别把密码留在框里
            }
            ov.querySelectorAll('.auth-tab').forEach(function (b) {
                var on = b.dataset.m === m;
                // 下划线式页签（主流登录框做法）：不再用两块实心按钮
                b.style.background = 'none';
                b.style.border = 'none';
                b.style.padding = '0 0 7px';
                b.style.fontSize = '.86rem';
                b.style.cursor = 'pointer';
                b.style.borderBottom = '2px solid ' + (on ? 'var(--primary,#4a7dff)' : 'transparent');
                b.style.color = on ? 'inherit' : 'var(--text-secondary,#8a94a3)';
                b.style.fontWeight = on ? '600' : '400';
            });
        }
        ov.querySelectorAll('.auth-tab').forEach(function (b) {
            b.addEventListener('click', function () { _setMode(b.dataset.m); });
        });

        function submit() {
            var nm = u.value.trim(), pw = p.value;
            if (!nm) { err.textContent = '请输入用户名'; u.focus(); return; }
            if (!pw) { err.textContent = '请输入密码'; p.focus(); return; }
            if (mode === 'register') {
                var p2 = document.getElementById('auth-login-pass2');
                if (p2 && p2.value !== pw) {
                    err.textContent = '两次输入的密码不一致';
                    p2.value = ''; p2.focus(); return;
                }
            }
            err.textContent = '';
            btn.disabled = true;
            btn.textContent = mode === 'login' ? '登录中…' : '注册中…';
            var url = mode === 'login' ? '/api/auth/login' : '/api/auth/register';
            // ★ 超时保护：共享库不可达时请求会一直挂着，按钮会永远停在「登录中…」点不动
            //   （用户反馈过：登录一阵子之后按钮失效）。20 秒没应答就当失败，按钮一定恢复可点。
            var ctl = (typeof AbortController === 'function') ? new AbortController() : null;
            var timer = ctl ? setTimeout(function () { ctl.abort(); }, 20000) : null;
            _origFetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: nm, password: pw }),
                signal: ctl ? ctl.signal : undefined
            }).then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
              .then(function (res) {
                  if (mode === 'register') {
                      if (res.status === 200) {
                          hint.style.color = 'var(--success,#2a9d68)';
                          hint.textContent = '✅ 注册成功！请切换到「登录」页签登录';
                          _setMode('login');
                          btn.disabled = false; btn.textContent = '登 录';
                      } else {
                          err.textContent = res.data.error || '注册失败';
                          btn.disabled = false; btn.textContent = '注 册';
                      }
                      return;
                  }
                  if (res.status === 200 && res.data.token) {
                      setAuth(res.data.token, { username: res.data.username, role: res.data.role,
                                                level: (typeof res.data.level === 'number') ? res.data.level
                                                       : (res.data.role === 'admin' ? 5 : (res.data.approved ? 1 : 0)),
                                                approved: !!res.data.approved });
                      location.reload();
                  } else {
                      err.textContent = res.data.error || '登录失败';
                      btn.disabled = false; btn.textContent = '登 录';
                  }
              }).catch(function (e2) {
                  var aborted = e2 && e2.name === 'AbortError';
                  err.textContent = aborted ? '请求超时：连不上共享库，请检查网络后重试'
                                            : '网络错误：连不上共享库';
                  btn.textContent = mode === 'login' ? '登 录' : '注 册';
              }).finally(function () {
                  if (timer) clearTimeout(timer);
                  btn.disabled = false;      // 无论成功/失败/超时，按钮都必须恢复可点
              });
        }
        btn.addEventListener('click', submit);
        [u, p].forEach(function (el) { el.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); }); });
        _setMode('login');      // 页签初态（下划线样式在上面统一给）
        setTimeout(function () { u.focus(); }, 50);
    }
    window.openLoginModal = function () { window.__userAskedLogin = true; _loginOpen = true; _openLogin(true); };

    // ── 管理员面板：用户列表 / 建号 / 重置密码 ──
    function _openAdminPanel() {
        var old = document.getElementById('auth-admin-overlay');
        if (old) old.remove();
        var ov = document.createElement('div');
        ov.id = 'auth-admin-overlay';
        ov.innerHTML =
            '<div data-auth-mask style="' + OV_LAYER_STYLE + '">' +
                '<div style="' + OV_CARD_STYLE + 'width:470px;max-height:80vh;overflow:auto;background:var(--card-bg,#fff);border:1px solid var(--border,#ddd);border-radius:14px;padding:1.1rem 1.1rem 1rem;box-shadow:0 16px 44px rgba(0,0,0,.26);position:relative;z-index:1;">' +
                    // 面板内样式：操作按钮只在悬停该行时出现（避免误点删除）
                    '<style>#auth-admin-list .auth-urow:hover .auth-ops{opacity:1 !important;} ' +
                           '#auth-admin-list .auth-urow:hover{background:var(--hover,#fafbfd);} ' +
                           '#auth-admin-list .auth-urow .auth-ib:hover{border-color:var(--primary,#4a7dff);color:var(--primary,#4a7dff);}</style>' +
                    '<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.7rem;">' +
                        '<div style="font-size:1rem;font-weight:600;flex:0 0 auto;">用户管理</div>' +
                        '<input id="auth-admin-search" placeholder="搜索用户名…" style="flex:1;min-width:0;height:26px;padding:0 .5rem;border:1px solid var(--border,#ddd);border-radius:8px;font-size:.78rem;">' +
                        '<button id="auth-admin-new-toggle" title="新建账号" style="flex:0 0 auto;font-size:.76rem;padding:.22rem .6rem;border:none;border-radius:8px;background:var(--primary,#4a7dff);color:#fff;cursor:pointer;">＋ 新建</button>' +
                        '<button id="auth-admin-close" title="关闭" style="flex:0 0 auto;width:1.6rem;height:1.6rem;background:none;border:none;font-size:1rem;cursor:pointer;color:var(--text-secondary,#999);">✕</button>' +
                    '</div>' +
                    '<div id="auth-admin-list" style="margin-bottom:.5rem;font-size:.86rem;">加载中…</div>' +
                    // 新建表单默认收起（原来常驻占一大块）
                    '<div id="auth-admin-create-box" style="display:none;border-top:1px solid var(--border,#eee);padding-top:.7rem;">' +
                        '<div style="display:flex;gap:.4rem;flex-wrap:wrap;">' +
                            '<input id="auth-admin-newuser" placeholder="用户名" style="flex:1;min-width:90px;height:30px;padding:0 .5rem;border:1px solid var(--border,#ddd);border-radius:8px;box-sizing:border-box;">' +
                            '<input id="auth-admin-newpw" placeholder="密码(≥6位)" style="flex:1;min-width:90px;height:30px;padding:0 .5rem;border:1px solid var(--border,#ddd);border-radius:8px;box-sizing:border-box;">' +
                            '<select id="auth-admin-newrole" style="height:30px;padding:0 .3rem;border:1px solid var(--border,#ddd);border-radius:8px;"><option value="0">0·游客</option><option value="1">1·蓝色</option><option value="2">2·紫色</option><option value="3">3·橙色</option><option value="4">4·红色</option><option value="5">5·金色</option></select>' +
                            '<button id="auth-admin-create" style="height:30px;padding:0 .9rem;border:none;border-radius:8px;background:var(--primary,#4a7dff);color:#fff;cursor:pointer;">创建</button>' +
                        '</div>' +
                    '</div>' +
                    '<div id="auth-admin-msg" style="font-size:.78rem;min-height:1.1em;margin-top:.4rem;"></div>' +
                '</div>' +
            '</div>';
        document.body.appendChild(ov);
        document.getElementById('auth-admin-close').addEventListener('click', function () { ov.remove(); });
        // 注：已移除全屏 data-auth-blank 层（它是"点一下就消失"的根因——铺满遮罩且 pointer-events:auto，
        //     点击卡片/滚动区域时会命中它而直接关掉浮窗）。与登录浮窗保持一致：只由 ✕ 关闭。
        //     另支持 Esc 关闭。
        var _escAdmin = function (e) {
            if (e.key === 'Escape') {
                var o = document.getElementById('auth-admin-overlay');
                if (o) o.remove();
                document.removeEventListener('keydown', _escAdmin);
            }
        };
        document.addEventListener('keydown', _escAdmin);

        function reload() {
            window.fetch('/api/auth/users').then(function (r) { return r.json(); }).then(function (d) {
                var el = document.getElementById('auth-admin-list');
                if (!el) return;
                if (d.error) { el.innerHTML = '<span style="color:var(--danger,#d33);">' + _esc(d.error) + '</span>'; return; }
                var isSuper = !!d.is_super;
                var isRootMe = !!d.is_root;   // 仅 admin：可删账号、可授 L5
                // 等级元数据（颜色/名称）
                var LV = [
                    { n: '游客', c: '#9aa9be' }, { n: '蓝色', c: '#3b82f6' }, { n: '紫色', c: '#a855f7' },
                    { n: '橙色', c: '#f59e0b' }, { n: '红色', c: '#ef4444' }, { n: '金色', c: '#eab308' }
                ];
                el.innerHTML = (d.users || []).map(function (u) {
                    var isRoot = (u.username === 'admin');
                    var lv = (typeof u.level === 'number') ? u.level : (isRoot ? 5 : (u.approved ? 1 : 0));
                    var meta = LV[lv] || LV[0];
                    var ops = '';
                    // 删除账号：仅主管理员（金色与管理员一致，但删账号排除）
                    if (!isRoot && isRootMe) {
                        ops = '<button class="auth-del auth-ib" data-u="' + _esc(u.username) + '" title="删除账号" style="width:24px;height:24px;border:1px solid var(--border,#ddd);border-radius:7px;background:var(--card-bg,#fff);cursor:pointer;font-size:.72rem;line-height:1;color:var(--danger,#d33);">🗑</button>';
                    }
                    // 等级选择器：主管理员可选 0-5；金色(L5) 可选 0-4；其他不可选
                    var lvSel = '';
                    var myLv = (typeof d.my_level === 'number') ? d.my_level : (isSuper ? 5 : 0);
                    if (!isRoot && (isSuper || myLv >= 5)) {
                        var maxLv = isRootMe ? 5 : 4;   // 只有 admin 能授予 L5
                        var opts = '';
                        for (var i = 0; i <= maxLv; i++) {
                            opts += '<option value="' + i + '"' + (i === lv ? ' selected' : '') + '>' +
                                    i + '·' + LV[i].n + '</option>';
                        }
                        lvSel = '<select class="auth-lv" data-u="' + _esc(u.username) + '" title="调整等级" style="height:24px;padding:0 .2rem;border:1px solid var(--border,#ddd);border-radius:7px;font-size:.72rem;">' + opts + '</select>';
                    }
                    var usage = (u.qwen_tokens_today != null && u.qwen_tokens_today > 0)
                        ? '<span style="font-size:.7rem;color:var(--gray,#999);">千问' + (u.qwen_tokens_today >= 1e6 ? (u.qwen_tokens_today/1e6).toFixed(1)+'M' : Math.round(u.qwen_tokens_today/1e3)+'k') + '/日</span>' : '';
                    // 头像 + 用户名 + 等级徽章；操作（改等级/重置密码/删除）收进右侧，悬停该行才出现
                    return '<div class="auth-urow" data-name="' + _esc(u.username.toLowerCase()) + '" style="display:flex;align-items:center;gap:.5rem;padding:.4rem .35rem;border-bottom:1px solid var(--border,#f1f3f7);">' +
                        '<span style="display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;background:' + meta.c + ';color:#fff;font-size:.68rem;font-weight:600;flex:0 0 auto;">' + _esc(u.username.slice(0, 1).toUpperCase()) + '</span>' +
                        '<span style="flex:1;min-width:60px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _esc(u.username) + (isRoot ? ' <span style="font-size:.66rem;color:#b8860b;">主管理员</span>' : '') + '</span>' +
                        usage +
                        '<span style="flex:0 0 auto;font-size:.68rem;font-weight:600;padding:1px 7px;border-radius:99px;color:' + meta.c + ';background:' + meta.c + '22;border:1px solid ' + meta.c + '55;">L' + lv + ' · ' + meta.n + '</span>' +
                        '<span class="auth-ops" style="flex:0 0 auto;display:flex;gap:4px;align-items:center;opacity:0;transition:opacity .12s;">' +
                            lvSel +
                            '<button class="auth-rpw auth-ib" data-u="' + _esc(u.username) + '" title="重置密码" style="width:24px;height:24px;border:1px solid var(--border,#ddd);border-radius:7px;background:var(--card-bg,#fff);cursor:pointer;font-size:.72rem;line-height:1;color:var(--text-secondary,#5b6676);">🔑</button>' +
                            ops +
                        '</span>' +
                        '</div>';
                }).join('');
                // 等级调整
                el.querySelectorAll('.auth-lv').forEach(function (sel) {
                    sel.addEventListener('change', function () {
                        var nlv = parseInt(sel.value, 10);
                        window.fetch('/api/auth/users/level', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username: sel.dataset.u, level: nlv })
                        }).then(function (r) { return r.json(); }).then(function (d2) {
                            var m = document.getElementById('auth-admin-msg');
                            if (m) m.textContent = d2.error ? ('⚠️ ' + d2.error)
                                : ('✅ 已将 ' + sel.dataset.u + ' 调整为 ' + nlv + '·' + LV[nlv].n);
                            reload();
                        });
                    });
                });
                el.querySelectorAll('.auth-rpw').forEach(function (b) {
                    b.addEventListener('click', function () {
                        var npw = prompt('为 ' + b.dataset.u + ' 设置新密码（≥4位）：');
                        if (!npw) return;
                        window.fetch('/api/auth/users/password', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username: b.dataset.u, password: npw })
                        }).then(function (r) { return r.json(); }).then(function (d2) {
                            var m = document.getElementById('auth-admin-msg');
                            if (m) m.textContent = d2.error ? ('⚠️ ' + d2.error) : '✅ 已重置 ' + b.dataset.u + ' 的密码';
                        });
                    });
                });
                // 注：原「开通/收回数据库授权」按钮已由等级选择器取代（L1+ 即可用数据库）
                el.querySelectorAll('.auth-del').forEach(function (b) {
                    b.addEventListener('click', function () {
                        if (!confirm('确定删除账号 ' + b.dataset.u + '？其云端个人目录（笔记/附件）将一并删除，不可恢复。')) return;
                        window.fetch('/api/auth/users/delete', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username: b.dataset.u })
                        }).then(function (r) { return r.json(); }).then(function (d2) {
                            var m = document.getElementById('auth-admin-msg');
                            if (m) m.textContent = d2.error ? ('⚠️ ' + d2.error) : '✅ 已删除 ' + b.dataset.u;
                            reload();
                        });
                    });
                });
            }).catch(function () {
                var el = document.getElementById('auth-admin-list');
                if (el) el.innerHTML = '<span style="color:var(--danger,#d33);">加载失败</span>';
            });
        }
        document.getElementById('auth-admin-new-toggle').addEventListener('click', function () {
            var box = document.getElementById('auth-admin-create-box');
            if (box) box.style.display = (box.style.display === 'none' ? 'block' : 'none');
        });
        document.getElementById('auth-admin-search').addEventListener('input', function () {
            var q = this.value.trim().toLowerCase();
            document.querySelectorAll('#auth-admin-list .auth-urow').forEach(function (r) {
                r.style.display = (!q || (r.dataset.name || '').indexOf(q) >= 0) ? 'flex' : 'none';
            });
        });
        document.getElementById('auth-admin-create').addEventListener('click', function () {
            var u = document.getElementById('auth-admin-newuser').value.trim();
            var pw = document.getElementById('auth-admin-newpw').value;
            var lv = parseInt(document.getElementById('auth-admin-newrole').value, 10);
            var LVN2 = ['游客', '蓝色', '紫色', '橙色', '红色', '金色'];
            var m = document.getElementById('auth-admin-msg');
            window.fetch('/api/auth/users', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: u, password: pw, level: lv })
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (m) m.textContent = d.error ? ('⚠️ ' + d.error) : ('✅ 已创建 ' + u + '（' + (LVN2[lv] || lv) + '）');
                if (!d.error) { document.getElementById('auth-admin-newuser').value = ''; document.getElementById('auth-admin-newpw').value = ''; reload(); }
            });
        });
        reload();
    }

    // ── 用户身份（③方案B）：顶栏只留一个小头像（未登录=小「登录」按钮），
    //    完整账号行放到侧栏底部 —— 就是 Codex / VS Code 的做法 ──
    var _LV_N = ['游客', '蓝色', '紫色', '橙色', '红色', '金色'];
    var _LV_C = ['#9aa9be', '#3b82f6', '#a855f7', '#f59e0b', '#ef4444', '#eab308'];

    function _lvInfo(me) {
        var isRoot = (me.username === 'admin');
        var lv = (typeof me.level === 'number') ? me.level : (isRoot ? 5 : (me.approved ? 1 : 0));
        return { n: lv, color: _LV_C[lv] || _LV_C[0], name: _LV_N[lv] || '游客', isRoot: isRoot };
    }

    function _canManage(me) {
        var lv = (typeof me.level === 'number') ? me.level : (me.username === 'admin' ? 5 : 0);
        return me.username === 'admin' || me.role === 'admin' || lv >= 5;
    }

    var _acctMenu = null;
    function _closeAcctMenu() { if (_acctMenu) { _acctMenu.remove(); _acctMenu = null; } }

    // 账号菜单：从顶栏小头像往↓弹、从侧栏底部账号行往↑弹
    function _openAcctMenu(anchor, above) {
        var me = window.getAuthUser();
        if (!me) return;
        if (_acctMenu) { _closeAcctMenu(); return; }
        var r = anchor.getBoundingClientRect();
        var m = document.createElement('div');
        m.style.cssText = 'position:fixed;background:var(--card-bg,#fff);border:1px solid var(--border,#ddd);' +
            'border-radius:10px;box-shadow:0 10px 26px rgba(0,0,0,.18);z-index:3600;min-width:170px;padding:4px;';
        m.style.right = (window.innerWidth - r.right) + 'px';
        if (above) m.style.bottom = (window.innerHeight - r.top + 6) + 'px';
        else m.style.top = (r.bottom + 6) + 'px';
        var it = 'padding:6px 10px;border-radius:7px;font-size:.84rem;cursor:pointer;color:var(--text-secondary,inherit);';
        m.innerHTML = (_canManage(me) ? '<div data-a="admin" style="' + it + '">👥 用户管理</div>' : '') +
            (_sessionExpired ? '<div data-a="relogin" style="' + it + 'color:var(--danger,#ef4444);">🔑 重新登录（会话已过期）</div>' : '') +
            '<div data-a="switch" style="' + it + '">🔁 切换账号</div>' +
            '<div data-a="logout" style="' + it + '">🚪 退出登录</div>';
        document.body.appendChild(m);
        _acctMenu = m;
        m.querySelectorAll('div').forEach(function (d) {
            d.onmouseover = function () { d.style.background = 'var(--hover,#f2f4f8)'; d.style.color = 'inherit'; };
            d.onmouseout = function () { d.style.background = 'none'; d.style.color = 'var(--text-secondary,inherit)'; };
        });
        var rl = m.querySelector('[data-a="relogin"]');
        if (rl) rl.addEventListener('click', function () {
            clearAuth(); window.__userAskedLogin = true; _openLogin(true);
        });
        m.querySelector('[data-a="logout"]').addEventListener('click', function () {
            window.fetch('/api/auth/logout', { method: 'POST' }).finally(function () { clearAuth(); location.reload(); });
        });
        m.querySelector('[data-a="switch"]').addEventListener('click', function () {
            window.fetch('/api/auth/logout', { method: 'POST' }).finally(function () {
                clearAuth(); window.__userAskedLogin = true; _openLogin(true);
            });
        });
        var ad = m.querySelector('[data-a="admin"]');
        if (ad) ad.addEventListener('click', function () { _closeAcctMenu(); _openAdminPanel(); });
    }

    document.addEventListener('click', function (e) {
        if (_acctMenu && !_acctMenu.contains(e.target) &&
            !e.target.closest('#auth-user-chip') && !e.target.closest('#auth-side-user')) _closeAcctMenu();
    });

    function _renderChip() {
        var me = window.getAuthUser();
        var slot = document.getElementById('auth-chip-slot');
        if (!me || !slot) return;
        var isGuest = (me.username === 'guest');
        var info = _lvInfo(me);

        // ① 顶栏：登录后只有一个小头像；状态改用小圆点，不再出现「⚠ xxx 重新登录」这种长红字
        slot.innerHTML = '';
        var mini = document.createElement('button');
        mini.id = 'auth-user-chip';
        mini.type = 'button';
        if (isGuest) {
            mini.textContent = '🔑 登录';
            mini.title = '未登录：本地功能照常用；登录后可使用共享库工具与数据库';
            mini.style.cssText = 'font-size:.76rem;padding:.2rem .55rem;border:1px solid var(--border);' +
                'border-radius:999px;background:var(--card-bg,#fff);color:var(--text-secondary,inherit);' +
                'cursor:pointer;line-height:1.5;white-space:nowrap;';
            mini.addEventListener('click', function () { window.__userAskedLogin = true; _openLogin(true); });
        } else {
            mini.textContent = me.username.slice(0, 1).toUpperCase();
            mini.title = '账号 · ' + info.name + (info.isRoot ? '（主管理员）' : '') +
                (_sessionExpired ? ' · 共享库会话已过期，点这里重新登录' : '');
            mini.style.cssText = 'position:relative;width:26px;height:26px;padding:0;border:none;border-radius:50%;' +
                'cursor:pointer;display:inline-flex;align-items:center;justify-content:center;font-size:.72rem;' +
                'font-weight:600;color:#fff;background:linear-gradient(135deg,#7cc7ec,#7edcd8);';
            if (_sessionExpired) {
                var dot = document.createElement('span');
                dot.style.cssText = 'position:absolute;width:8px;height:8px;border-radius:50%;' +
                    'background:var(--danger,#ef4444);border:1.5px solid var(--card-bg,#fff);right:-1px;top:-1px;';
                mini.appendChild(dot);
            }
            mini.addEventListener('click', function () { _openAcctMenu(mini, false); });
        }
        slot.appendChild(mini);

        // ② 侧栏底部账号行：完整身份（头像 + 名字 + 等级徽章 + ⋯）
        var row = document.getElementById('auth-side-row');
        if (row) {
            row.innerHTML = '';
            var b = document.createElement('button');
            b.id = 'auth-side-user';
            b.type = 'button';
            b.style.cssText = 'display:flex;align-items:center;gap:8px;width:100%;padding:5px 6px;border:none;' +
                'background:none;border-radius:8px;cursor:pointer;font-size:.82rem;color:inherit;text-align:left;';
            var av = '<span style="flex:0 0 auto;width:24px;height:24px;border-radius:50%;display:inline-flex;' +
                'align-items:center;justify-content:center;font-size:.7rem;font-weight:600;color:#fff;background:' +
                (isGuest ? '#c2cbd8' : 'linear-gradient(135deg,#7cc7ec,#7edcd8)') + ';">' +
                (isGuest ? '?' : _esc(me.username.slice(0, 1).toUpperCase())) + '</span>';
            var tail = isGuest
                ? '<span style="flex:0 0 auto;font-size:.72rem;color:var(--text-secondary,#8a94a3);">点这里登录</span>'
                : '<span style="flex:0 0 auto;font-size:.66rem;font-weight:600;padding:1px 6px;border-radius:99px;' +
                  'color:' + info.color + ';background:' + info.color + '22;border:1px solid ' + info.color + '55;">L' + info.n + '</span>' +
                  '<span style="flex:0 0 auto;color:var(--text-secondary,#8a94a3);">⋯</span>';
            b.innerHTML = av + '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;' +
                'white-space:nowrap;">' + (isGuest ? '未登录' : _esc(me.username)) + '</span>' + tail;
            b.title = isGuest ? '未登录：点这里登录共享库'
                              : (info.name + (info.isRoot ? ' · 主管理员' : '') + ' · 点这里打开账号菜单');
            b.addEventListener('click', function () {
                if (isGuest) { window.__userAskedLogin = true; _openLogin(true); return; }
                _openAcctMenu(b, true);
            });
            row.appendChild(b);
        }
    }

    // ── 启动：校验会话；服务端未启用鉴权时 me 返回 admin，自动通过 ──
    function _init() {
        var tok = window.getAuthToken();
        if (!tok || tok === 'guest') {
            clearAuth();
            // ★ 先把"未登录"这个本地就能确定的事实**同步**渲染出来，再去问服务器。
            //   原来只 clearAuth() 不渲染，账号行要等 /api/auth/me 回来才出现（实测空行 2.8 秒、
            //   回来时又突然撑开 43px）—— 用户反馈的"登录要隔一段时间才显示"就是它。
            //   没有 token 就是游客，这是客观事实，不需要等任何请求。
            setAuth('guest', { username: 'guest', role: 'guest' });
            window.fetch('/api/auth/me').then(function (r) {
                if (r.status === 401) {
                    // 未登录：弹登录框。
                    // ⚠️ 手机端 _openLogin 会被守卫拦掉（不遮屏），此时必须渲染
                    // 「🔑 登录」芯片，否则顶栏没有任何登录入口（用户要求保证登录按钮可用）。
                    _loginOpen = true;
                    var _mob = false;
                    try { _mob = window.matchMedia('(max-width: 768px)').matches; } catch (e) {}
                    if (_mob) {
                        setAuth('guest', { username: 'guest', role: 'guest' });
                        _renderChip();
                    } else {
                        _openLogin(true);
                    }
                    return null;
                }
                return r.json();
            }).then(function (d) {
                if (!d) return;
                if (d.auth_enabled && d.username === 'guest') {
                    // 本地实例（登录可选）：不弹框，右上角给「点击登录」芯片
                    setAuth('guest', { username: 'guest', role: 'guest' });
                    _renderChip();
                } else if (d.auth_enabled) {
                    _loginOpen = true; _openLogin(true);
                }
            }).catch(function () {}).finally(function () {
                // 兜底：弹了登录框（或请求失败）但顶栏没有任何身份入口时，
                // 补一个游客芯片 —— 否则用户关掉登录框后页面上再无登录入口（点不到用户管理）
                setTimeout(function () {
                    var slot = document.getElementById('auth-chip-slot');
                    if (slot && !slot.children.length && !window.getAuthUser()) {
                        setAuth('guest', { username: 'guest', role: 'guest' });
                        _renderChip();
                    }
                }, 300);
            });
            return;
        }
        window.fetch('/api/auth/me').then(function (r) {
            if (r.status === 401) { clearAuth(); _loginOpen = true; _openLogin(true); return null; }
            return r.json();
        }).then(function (d) {
            if (!d) return;
            if (!d.auth_enabled) { clearAuth(); return; }  // 本地实例且未连共享库：不显示芯片
            // ⚠️ 必须带上 level：刷新页面走这条路径，漏 level 会让顶栏芯片退回蓝色
            setAuth(window.getAuthToken(), {
                username: d.username, role: d.role,
                level: (typeof d.level === 'number') ? d.level
                       : (d.role === 'admin' ? 5 : (d.approved ? 1 : 0)),
                approved: !!d.approved
            });
            _renderChip();
        }).catch(function () {});
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
    else _init();
})();
