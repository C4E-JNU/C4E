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
        ['ai-branches', 'ai-current-branch', 'branchOrder', 'ai-expanded-groups', 'ai-database'].forEach(function (k) {
            var nk = k + '::guest';
            if (localStorage.getItem(nk) == null && localStorage.getItem(k) != null) {
                localStorage.setItem(nk, localStorage.getItem(k));
            }
        });
    }

    window.getAuthToken = function () { return localStorage.getItem(TOKEN_KEY) || ''; };
    window.getAuthUser = function () {
        try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) { return null; }
    };
    function setAuth(token, user) {
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(USER_KEY, JSON.stringify(user));
    }
    function clearAuth() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
    }

    // ── fetch 注入：同源 /api 自动带 token；401 弹登录框 ──
    var _origFetch = window.fetch.bind(window);
    var _loginOpen = false;
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
            // 401 自动弹登录框：桌面端保留（方便），手机端不弹（会遮屏，改为点顶栏「登录」）
            var _isMobile = false;
            try { _isMobile = window.matchMedia('(max-width: 768px)').matches; } catch (e) {}
            if (r.status === 401 && !_loginOpen && !_isMobile) { _loginOpen = true; _openLogin(true); }
            return r;
        });
    };

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
                '<div style="' + OV_CARD_STYLE + 'width:320px;background:var(--card-bg,#fff);border:1px solid var(--border,#ddd);border-radius:12px;padding:1.4rem;box-shadow:0 8px 30px rgba(0,0,0,.25);position:relative;">' +
                    '<div style="display:flex;gap:.4rem;margin-bottom:1rem;">' +
                        '<button data-m="login" class="auth-tab" style="flex:1;padding:.4rem;border:none;border-radius:8px;cursor:pointer;font-size:.9rem;background:var(--primary,#4a7dff);color:#fff;">登 录</button>' +
                        '<button data-m="register" class="auth-tab" style="flex:1;padding:.4rem;border:none;border-radius:8px;cursor:pointer;font-size:.9rem;background:var(--hover,#eef1f5);color:inherit;">注 册</button>' +
                    '</div>' +
                    // 标题行：关闭按钮放在标题行右侧（与标题同一行，不再压在页签/注册按钮上）
                    '<div style="display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-bottom:1rem;">' +
                        '<div style="font-size:1.05rem;font-weight:600;">🔐 <span id="auth-mode-title">登录 C4EAI 共享库</span></div>' +
                        (closable ? '<button id="auth-login-close" title="关闭" style="flex:0 0 auto;width:1.6rem;height:1.6rem;line-height:1;background:none;border:none;border-radius:6px;font-size:1rem;cursor:pointer;color:var(--gray,#999);">✕</button>' : '') +
                    '</div>' +
                    '<input id="auth-login-user" placeholder="用户名" autocomplete="username" style="width:100%;padding:.5rem .6rem;border:1px solid var(--border,#ddd);border-radius:8px;margin-bottom:.6rem;box-sizing:border-box;">' +
                    '<input id="auth-login-pass" type="password" placeholder="密码" autocomplete="current-password" style="width:100%;padding:.5rem .6rem;border:1px solid var(--border,#ddd);border-radius:8px;margin-bottom:.6rem;box-sizing:border-box;">' +
                    '<div id="auth-login-err" style="color:var(--danger,#d33);font-size:.82rem;min-height:1.1em;margin-bottom:.4rem;"></div>' +
                    '<button id="auth-login-btn" style="width:100%;padding:.55rem;border:none;border-radius:8px;background:var(--primary,#4a7dff);color:#fff;cursor:pointer;font-size:.95rem;">登 录</button>' +
                    '<div id="auth-login-hint" style="font-size:.78rem;color:var(--gray,#888);margin-top:.7rem;text-align:center;">登录后可使用共享库与自己的数据库</div>' +
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
            title.textContent = m === 'login' ? '登录 C4EAI 共享库' : '注册新账号';
            btn.textContent = m === 'login' ? '登 录' : '注 册';
            hint.textContent = m === 'login'
                ? '登录后可使用共享库与自己的数据库'
                : '自由注册；注册后私人功能立即可用，数据库需管理员授权';
            err.textContent = '';
            ov.querySelectorAll('.auth-tab').forEach(function (b) {
                var on = b.dataset.m === m;
                b.style.background = on ? 'var(--primary,#4a7dff)' : 'var(--hover,#eef1f5)';
                b.style.color = on ? '#fff' : 'inherit';
            });
        }
        ov.querySelectorAll('.auth-tab').forEach(function (b) {
            b.addEventListener('click', function () { _setMode(b.dataset.m); });
        });

        function submit() {
            btn.disabled = true;
            btn.textContent = mode === 'login' ? '登录中…' : '注册中…';
            var url = mode === 'login' ? '/api/auth/login' : '/api/auth/register';
            _origFetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: u.value.trim(), password: p.value })
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
              }).catch(function () {
                  err.textContent = '网络错误';
                  btn.disabled = false; btn.textContent = mode === 'login' ? '登 录' : '注 册';
              });
        }
        btn.addEventListener('click', submit);
        [u, p].forEach(function (el) { el.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); }); });
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
                '<div style="' + OV_CARD_STYLE + 'width:420px;max-height:80vh;overflow:auto;background:var(--card-bg,#fff);border:1px solid var(--border,#ddd);border-radius:12px;padding:1.2rem;box-shadow:0 8px 30px rgba(0,0,0,.25);position:relative;z-index:1;">' +
                    '<div style="display:flex;align-items:center;margin-bottom:.8rem;">' +
                        '<div style="font-size:1rem;font-weight:600;flex:1;">👥 用户管理</div>' +
                        '<button id="auth-admin-close" style="background:none;border:none;font-size:1.1rem;cursor:pointer;">✕</button>' +
                    '</div>' +
                    '<div id="auth-admin-list" style="margin-bottom:.9rem;font-size:.88rem;">加载中…</div>' +
                    '<div style="border-top:1px solid var(--border,#eee);padding-top:.8rem;">' +
                        '<div style="font-size:.85rem;font-weight:600;margin-bottom:.5rem;">新建账号</div>' +
                        '<div style="display:flex;gap:.4rem;flex-wrap:wrap;">' +
                            '<input id="auth-admin-newuser" placeholder="用户名" style="flex:1;min-width:90px;padding:.4rem .5rem;border:1px solid var(--border,#ddd);border-radius:6px;">' +
                            '<input id="auth-admin-newpw" placeholder="密码(≥6位)" style="flex:1;min-width:90px;padding:.4rem .5rem;border:1px solid var(--border,#ddd);border-radius:6px;">' +
                            '<select id="auth-admin-newrole" style="padding:.4rem;border:1px solid var(--border,#ddd);border-radius:6px;"><option value="0">0·游客</option><option value="1">1·蓝色</option><option value="2">2·紫色</option><option value="3">3·橙色</option><option value="4">4·红色</option><option value="5">5·金色</option></select>' +
                            '<button id="auth-admin-create" style="padding:.4rem .8rem;border:none;border-radius:6px;background:var(--primary,#4a7dff);color:#fff;cursor:pointer;">创建</button>' +
                        '</div>' +
                        '<div id="auth-admin-msg" style="font-size:.8rem;min-height:1.1em;margin-top:.4rem;"></div>' +
                    '</div>' +
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
                        ops = '<button class="auth-del" data-u="' + _esc(u.username) + '" style="background:none;border:none;cursor:pointer;font-size:.78rem;color:var(--danger,#d33);">删除</button>';
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
                        lvSel = '<select class="auth-lv" data-u="' + _esc(u.username) + '" style="padding:.15rem .3rem;border:1px solid var(--border,#ddd);border-radius:5px;font-size:.75rem;">' + opts + '</select>';
                    }
                    var usage = (u.qwen_tokens_today != null && u.qwen_tokens_today > 0)
                        ? '<span style="font-size:.7rem;color:var(--gray,#999);">千问' + (u.qwen_tokens_today >= 1e6 ? (u.qwen_tokens_today/1e6).toFixed(1)+'M' : Math.round(u.qwen_tokens_today/1e3)+'k') + '/日</span>' : '';
                    // 彩色圆点头像 + 用户名 + 等级名
                    return '<div style="display:flex;align-items:center;gap:.5rem;padding:.35rem 0;border-bottom:1px dashed var(--border,#eee);flex-wrap:wrap;">' +
                        '<span title="' + meta.n + '" style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;background:' + meta.c + ';color:#fff;font-size:.7rem;font-weight:600;flex:0 0 auto;">' + _esc(u.username.slice(0, 1).toUpperCase()) + '</span>' +
                        '<span style="flex:1;min-width:70px;">' + _esc(u.username) + (isRoot ? ' <span style="font-size:.7rem;color:#b8860b;">主管理员</span>' : '') + '</span>' +
                        usage +
                        '<span style="font-size:.72rem;color:' + meta.c + ';font-weight:600;">' + meta.n + '</span>' +
                        lvSel + ops +
                        '<button class="auth-rpw" data-u="' + _esc(u.username) + '" style="background:none;border:none;cursor:pointer;font-size:.78rem;color:var(--primary,#4a7dff);">重置密码</button>' +
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

    // ── 用户芯片（渲染到顶部导航栏 #auth-chip-slot，挨着「数据库上传」）──
    function _renderChip() {
        var me = window.getAuthUser();
        var slot = document.getElementById('auth-chip-slot');
        if (!me || !slot) return;
        slot.innerHTML = '';
        var chip = document.createElement('button');
        chip.id = 'auth-user-chip';
        var isGuest = me.username === 'guest';
        // 等级（0-5）→ 颜色/名称；缺省按 role/approved 推导
        var LVN = ['游客', '蓝色', '紫色', '橙色', '红色', '金色'];
        var LVC = ['#9aa9be', '#3b82f6', '#a855f7', '#f59e0b', '#ef4444', '#eab308'];
        var isRoot = (me.username === 'admin');
        var lv = (typeof me.level === 'number') ? me.level
               : (isRoot ? 5 : (me.approved ? 1 : 0));
        var lvColor = LVC[lv] || LVC[0];
        var lvName = LVN[lv] || '游客';
        chip.title = isGuest ? '未登录：本地功能照常用；登录后可使用共享库工具与数据库'
                             : ('账号 · ' + lvName + (isRoot ? '（主管理员）' : ''));
        chip.textContent = isGuest ? '🔑 登录'
            : '👤 ' + me.username + (isRoot ? ' ▾' : ' ▾');
        // 用左侧彩色圆点标识等级
        chip.style.cssText = 'display:inline-flex;align-items:center;gap:.35rem;' +
            'font-size:.78rem;padding:.28rem .7rem;border:1px solid var(--border);' +
            'border-radius:999px;background:var(--card-bg,#fff);color:var(--text-secondary,inherit);' +
            'cursor:pointer;line-height:1.5;white-space:nowrap;';
        if (!isGuest) {
            var dot = document.createElement('span');
            dot.style.cssText = 'width:8px;height:8px;border-radius:50%;background:' + lvColor +
                ';flex:0 0 auto;box-shadow:0 0 0 1.5px ' + lvColor + '33;';
            dot.title = lvName;
            chip.insertBefore(dot, chip.firstChild);
        }
        chip.onmouseover = function () { chip.style.borderColor = lvColor; };
        chip.onmouseout = function () { chip.style.borderColor = 'var(--border)'; };
        slot.appendChild(chip);

        var menu = null;
        function _closeMenu() { if (menu) { menu.remove(); menu = null; } }
        chip.addEventListener('click', function () {
            // 游客：弹登录框（可选登录，可关闭）
            if (isGuest) { window.__userAskedLogin = true; _openLogin(true); return; }
            // 已登录（含 admin）：小菜单
            if (menu) { _closeMenu(); return; }
            var r = chip.getBoundingClientRect();
            menu = document.createElement('div');
            menu.style.cssText = 'position:fixed;top:' + (r.bottom + 6) + 'px;right:' +
                (window.innerWidth - r.right) + 'px;background:var(--card-bg,#fff);border:1px solid var(--border,#ddd);' +
                'border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.18);z-index:3600;min-width:160px;overflow:hidden;';
            // 用户管理入口：主管理员 或 金色(L5) 可见
            var _myLv = (typeof me.level === 'number') ? me.level : (me.username === 'admin' ? 5 : 0);
            var _canManage = (me.username === 'admin') || (me.role === 'admin') || _myLv >= 5;
            var items = (_canManage ? '<div data-a="admin" style="padding:.5rem .9rem;cursor:pointer;font-size:.88rem;">👥 用户管理</div>' : '') +
                '<div data-a="switch" style="padding:.5rem .9rem;cursor:pointer;font-size:.88rem;">🔁 切换账号</div>' +
                '<div data-a="logout" style="padding:.5rem .9rem;cursor:pointer;font-size:.88rem;">🚪 退出登录</div>';
            menu.innerHTML = items;
            document.body.appendChild(menu);
            menu.querySelectorAll('div').forEach(function (d) {
                d.onmouseover = function () { d.style.background = 'var(--hover,#f0f2f5)'; };
                d.onmouseout = function () { d.style.background = 'none'; };
            });
            menu.querySelector('[data-a="logout"]').addEventListener('click', function () {
                window.fetch('/api/auth/logout', { method: 'POST' }).finally(function () {
                    clearAuth(); location.reload();
                });
            });
            var sw = menu.querySelector('[data-a="switch"]');
            if (sw) sw.addEventListener('click', function () {
                window.fetch('/api/auth/logout', { method: 'POST' }).finally(function () {
                    clearAuth(); _openLogin(true);
                });
            });
            var ad = menu.querySelector('[data-a="admin"]');
            if (ad) ad.addEventListener('click', function () { _closeMenu(); _openAdminPanel(); });
        });
        document.addEventListener('click', function (e) {
            if (menu && !menu.contains(e.target) && e.target !== chip) _closeMenu();
        });
    }

    // ── 启动：校验会话；服务端未启用鉴权时 me 返回 admin，自动通过 ──
    function _init() {
        var tok = window.getAuthToken();
        if (!tok || tok === 'guest') {
            clearAuth();
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
