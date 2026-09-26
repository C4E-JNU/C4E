/* ==================== 小e · 桌宠主逻辑 ====================
 * 小e 是 C4EAI 的导览智能体：介绍功能、查技能、读当前对话。
 * 【不落盘】小e 的聊天记录只存在内存变量 _chatLog，刷新/关闭即丢。
 * 【不干扰主流程】小e 的对话走独立轻量接口（/api/xiaoe/ask），不带工具、不入库。
 */
(function () {
    'use strict';

    var PREF_KEY = 'c4eai_xiaoe_enabled';
    var _chatLog = [];         // 内存对话（不持久化）
    var _mood = 'idle';        // idle | happy | angry | hot | sleepy | storm
    var _weather = null;       // null | sunny | rain | wind | storm | hot | cold
    var _busy = false;         // 小e 正在回答
    var _idleTimer = null;
    var _element = null;
    var _bodyEl = null;
    var _bubble = null;

    // ---------- 云朵团外形（原始 B 方案，带内部 id 防与页面渐变冲突） ----------
    function _svg() {
        return ''
        + '<svg viewBox="0 0 160 150" xmlns="http://www.w3.org/2000/svg">'
        + '  <defs>'
        + '    <radialGradient id="xeBody" cx="40%" cy="30%">'
        + '      <stop offset="0%" stop-color="#ffffff"/>'
        + '      <stop offset="60%" stop-color="#dceeff"/>'
        + '      <stop offset="100%" stop-color="#b8dcff"/>'
        + '    </radialGradient>'
        + '    <radialGradient id="xeCheek" cx="50%" cy="50%">'
        + '      <stop offset="0%" stop-color="#8ec8ff" stop-opacity=".8"/>'
        + '      <stop offset="100%" stop-color="#8ec8ff" stop-opacity="0"/>'
        + '    </radialGradient>'
        + '  </defs>'
        // 云朵身体：三圆叠蓬松
        + '  <circle cx="52" cy="84" r="26" fill="url(#xeBody)"/>'
        + '  <circle cx="108" cy="84" r="26" fill="url(#xeBody)"/>'
        + '  <circle cx="80" cy="70" r="32" fill="url(#xeBody)"/>'
        + '  <ellipse cx="80" cy="92" rx="44" ry="30" fill="url(#xeBody)"/>'
        // 高光
        + '  <ellipse cx="62" cy="52" rx="14" ry="10" fill="#fff" opacity=".8" transform="rotate(-22 62 52)"/>'
        + '  <ellipse cx="104" cy="104" rx="9" ry="5" fill="#fff" opacity=".3"/>'
        // 眼睛（可眨眼）
        + '  <g class="xe-eyes" style="transform-origin:center;">'
        + '    <ellipse cx="65" cy="82" rx="6.5" ry="7.5" fill="#2f3e52"/>'
        + '    <ellipse cx="95" cy="82" rx="6.5" ry="7.5" fill="#2f3e52"/>'
        + '    <circle cx="67.2" cy="79" r="2.4" fill="#fff"/>'
        + '    <circle cx="97.2" cy="79" r="2.4" fill="#fff"/>'
        + '  </g>'
        // 腮红
        + '  <ellipse cx="49" cy="95" rx="10" ry="6.5" fill="url(#xeCheek)"/>'
        + '  <ellipse cx="111" cy="95" rx="10" ry="6.5" fill="url(#xeCheek)"/>'
        // 嘴（可变化）
        + '  <path class="xe-mouth" d="M75 95 q5 5 10 0" stroke="#7aa8d6" stroke-width="2.2" fill="none" stroke-linecap="round"/>'
        // 小雨滴装饰
        + '  <circle cx="126" cy="46" r="4" fill="#a8d4ff" opacity=".8"/>'
        + '  <circle cx="136" cy="62" r="2.8" fill="#a8d4ff" opacity=".6"/>'
        + '</svg>';
    }

    var MOUTHS = {
        idle:  'M75 95 q5 5 10 0',                 // 微笑
        happy: 'M72 93 q8 9 16 0',                 // 大笑
        angry: 'M74 99 q6 -5 12 0',                // 倒弧（不悦）
        hot:   'M73 98 q7 2 14 0',                 // 撇嘴
        sleepy:'M76 96 q4 3 8 0',                  // 小嘴
        storm: 'M74 99 q6 -4 12 0'
    };

    // ---------- 构建 DOM ----------
    function _build() {
        if (_element) return;
        var el = document.createElement('div');
        el.id = 'xiaoe-root';
        el.innerHTML = ''
            + '<div class="xiaoe-shadow"></div>'
            + '<div class="xiaoe-fx"></div>'
            + '<div class="xiaoe-body anim-idle">' + _svg() + '</div>'
            + '<div class="xiaoe-zzz" style="display:none;"><span>z</span><span>z</span><span>Z</span></div>';
        document.body.appendChild(el);
        _element = el;
        _bodyEl = el.querySelector('.xiaoe-body');

        // 注：不做 ✕ 关闭键（用户要求）——只用设置面板里的「小e 桌宠」开关控制显隐

        _bindInteractions(el);
        _startIdleWatch();
    }

    // ---------- 互动：点击 / 敲打 / 连击 / 拖拽 / 起飞 ----------
    var _clickCount = 0, _clickTimer = null;
    var _dragging = false, _moved = false, _startX = 0, _startY = 0, _offX = 0, _offY = 0;

    function _anim(name, ms) {
        if (!_bodyEl) return;
        _bodyEl.classList.remove('anim-idle', 'anim-poke', 'anim-hit', 'anim-rage',
            'anim-takeoff', 'anim-land', 'anim-sleep', 'anim-wake', 'anim-float');
        void _bodyEl.offsetWidth;                 // 强制重排，让同名动画能重播
        _bodyEl.classList.add('anim-' + name);
        if (ms) {
            clearTimeout(_anim._t);
            _anim._t = setTimeout(function () {
                _bodyEl.classList.remove('anim-' + name);
                _bodyEl.classList.add(_mood === 'sleepy' ? 'anim-sleep' : 'anim-idle');
            }, ms);
        }
    }

    function _bindInteractions(el) {
        // —— 指针按下：记录起点，准备拖拽 ——
        el.addEventListener('pointerdown', function (e) {
            // 手机上触摸事件的 button 可能是 0/-1/undefined，不能只放行 0（否则触摸拖不动）
            if (typeof e.button === 'number' && e.button > 0) return;
            _dragging = true; _moved = false;
            _startX = e.clientX; _startY = e.clientY;
            var r = el.getBoundingClientRect();
            _offX = e.clientX - r.left; _offY = e.clientY - r.top;
            try { el.setPointerCapture(e.pointerId); } catch (err) {}
            el.classList.add('xiaoe-dragging');
            _wake();
            // 阻止触摸默认行为（页面滚动），否则移动端 pointermove 会被取消
            if (e.cancelable) { try { e.preventDefault(); } catch (err) {} }
        }, { passive: false });

        // —— 指针移动：超过阈值才算拖拽 ——
        el.addEventListener('pointermove', function (e) {
            if (!_dragging) return;
            var dx = e.clientX - _startX, dy = e.clientY - _startY;
            if (!_moved && Math.abs(dx) + Math.abs(dy) > 4) {
                _moved = true;
                _anim('float');                    // 悬空时轻轻浮动
            }
            if (_moved) {
                var x = e.clientX - _offX, y = e.clientY - _offY;
                var maxX = window.innerWidth - 40, maxY = window.innerHeight - 40;
                x = Math.max(-20, Math.min(maxX, x));
                y = Math.max(-10, Math.min(maxY, y));
                el.style.bottom = 'auto';      // 用 left/top 定位后必须清掉 bottom，否则冲突
                el.style.left = x + 'px';
                el.style.top = y + 'px';
                if (_bubble) _placeBubble();
            }
        });

        // —— 手势被浏览器打断（手机常见）：安全收尾，避免卡在拖拽态 ——
        el.addEventListener('pointercancel', function () {
            if (!_dragging) return;
            _dragging = false;
            el.classList.remove('xiaoe-dragging');
            if (_moved) _anim('land', 700);
        });

        // —— 指针抬起：区分「拖拽落地」与「点击互动」 ——
        el.addEventListener('pointerup', function (e) {
            if (!_dragging) return;
            _dragging = false;
            el.classList.remove('xiaoe-dragging');
            try { el.releasePointerCapture(e.pointerId); } catch (err) {}
            if (_moved) {
                _anim('land', 700);                // 落地 Q弹
                _maybeRandomWeather();             // 拖拽落地也算互动（5% 惊喜天气）
                return;
            }
            // 未移动 → 按点击次数分类
            _clickCount++;
            clearTimeout(_clickTimer);
            _clickTimer = setTimeout(function () {
                var n = _clickCount;
                _clickCount = 0;
                if (n === 1) {
                    _onPoke();
                } else if (n === 2) {
                    _onHit();                      // 双击 = 敲打
                } else {
                    _onRage();                     // 3+ 连击 = 发飙
                }
            }, 260);
        });

        el.addEventListener('pointercancel', function () {
            _dragging = false;
            el.classList.remove('xiaoe-dragging');
        });

        // —— 悬停：眼睛跟随光标（用轻微的横向位移模拟） ——
        el.addEventListener('pointermove', function (e) {
            if (_dragging || !_bodyEl) return;
            var r = el.getBoundingClientRect();
            var nx = ((e.clientX - r.left) / r.width - .5) * 5;   // -2.5 ~ 2.5
            var eyes = el.querySelector('.xe-eyes');
            if (eyes) eyes.style.transform = 'translateX(' + nx.toFixed(2) + 'px)';
        });
        el.addEventListener('pointerleave', function () {
            var eyes = el.querySelector('.xe-eyes');
            if (eyes) eyes.style.transform = '';
        });

        // 右键：换天气/变色（快捷互动，不影响主页面右键菜单）
        el.addEventListener('contextmenu', function (e) {
            e.preventDefault();
            e.stopPropagation();
            _cycleWeather();
        });
    }

    // ---------- 随机天气惊喜（与互动挂钩） ----------
    // 天气清单（供随机惊喜与右键手动切换共用）
    var WEATHERS = [
        { k: 'sunny', tone: 'tone-sunny', tip: '出太阳了 ☀️' },
        { k: 'rain',  tone: 'tone-rain',  tip: '下雨了 🌧️' },
        { k: 'wind',  tone: 'tone-wind',  tip: '刮风了 🍃' },
        { k: 'storm', tone: 'tone-storm', tip: '雷阵雨 ⛈️' },
        { k: 'hot',   tone: 'tone-hot',   tip: '太热了，红温 🔥' },
        { k: 'cold',  tone: 'tone-cold',  tip: '好冷，下雪感 ❄️' },
        { k: 'pink',  tone: 'tone-pink',  tip: '变成粉色了 💗' },
        { k: 'green', tone: 'tone-green', tip: '变成绿色了 🌿' },
        { k: 'purple',tone: 'tone-purple',tip: '变成紫色了 💜' }
    ];

    // 每次互动（戳/敲打/连击/拖拽落地）都有 2.5% 概率额外触发一次随机天气，
    // 给"玩小e"一点意外惊喜；右键仍可手动切（保留可控性）。
    // 用户 2026-09-22：概率从 5% 降到 2.5%（降低一半），且不在屏幕上标注"随机惊喜"。
    var WEATHER_CHANCE = 0.025;
    var _bonusCooling = false;          // 避免同一次互动里连续触发

    function _maybeRandomWeather() {
        if (_bonusCooling) return;
        if (Math.random() >= WEATHER_CHANCE) return;
        _bonusCooling = true;
        setTimeout(function () { _bonusCooling = false; }, 1200);
        // 从全部天气里随机挑一个（含纯变色），持续 3~5 秒
        var w = WEATHERS[Math.floor(Math.random() * WEATHERS.length)];
        var dur = 3000 + Math.floor(Math.random() * 2000);
        _setWeather(w.k, dur);
        if (typeof showToast === 'function') showToast('小e: ' + w.tip, 'info');
        // 小e 顺便冒个反应，让"惊喜"更明显
        _pulseEmote(['✨']);
    }

    // ---------- 互动动作 ----------
    function _onPoke() {
        _anim('poke', 640);
        _mood = 'happy';
        _pulseEmote(['✨']);
        // 单击 = 打开对话（小e 的核心用途：问功能/用法）
        _openChat();
        if (!_bubble) {
            // 极端情况下（旧浏览器）退化为只冒一句话
            if (typeof showToast === 'function') showToast('我是小e ✨ 问我 C4EAI 的功能用法～', 'info');
        }
        setTimeout(function () { _mood = 'idle'; }, 1200);
        _maybeRandomWeather();
    }

    function _onHit() {
        _anim('hit', 860);
        _say(['别敲啦！', '疼疼疼 QAQ', '再敲我就下雨了！', '哼，不理你了'][Math.floor(Math.random() * 4)]);
        _setWeather('rain', 2600);
        _mood = 'angry';
        _pulseEmote(['💢']);
        setTimeout(function () { _mood = _weather ? 'idle' : 'idle'; }, 1500);
        _maybeRandomWeather();
    }

    function _onRage() {
        _anim('rage', 1100);
        _bodyEl.classList.add('anim-shiver');
        _setWeather('storm', 4200);
        _mood = 'storm';
        _pulseEmote(['💢', '⚡']);
        _say('我红温了！！！你完蛋啦 🔥⚡');
        setTimeout(function () {
            _bodyEl.classList.remove('anim-shiver');
            _setTone('tone-hot');
            setTimeout(function () { _setTone(null); _mood = 'idle'; }, 2600);
        }, 1100);
    }

    function _pulseEmote(list) {
        if (!_element) return;
        list.forEach(function (ch, i) {
            setTimeout(function () {
                var d = document.createElement('div');
                d.className = 'xiaoe-emote';
                d.textContent = ch;
                _element.appendChild(d);
                setTimeout(function () { d.remove(); }, 1600);
            }, i * 180);
        });
    }

    // ---------- 天气 / 变色 ----------
    // （WEATHERS 清单已上移到「随机天气惊喜」处，供随机触发与手动切换共用）
    var _wIdx = -1;

    function _cycleWeather() {
        _wIdx = (_wIdx + 1) % WEATHERS.length;
        var w = WEATHERS[_wIdx];
        _setWeather(w.k, 3000);
        if (typeof showToast === 'function') showToast('小e: ' + w.tip, 'info');
    }

    function _setTone(tone) {
        if (!_bodyEl) return;
        ['tone-rain', 'tone-wind', 'tone-storm', 'tone-hot', 'tone-sunny', 'tone-cold',
         'tone-pink', 'tone-green', 'tone-purple'].forEach(function (c) { _bodyEl.classList.remove(c); });
        if (tone) _bodyEl.classList.add(tone);
    }

    function _setWeather(kind, durationMs) {
        if (!_element) return;
        var fx = _element.querySelector('.xiaoe-fx');
        if (!fx) return;
        clearTimeout(_setWeather._t);
        fx.innerHTML = '';
        _weather = kind;
        _element.classList.remove('steam');

        if (!kind) { _setTone(null); return; }

        if (kind === 'rain') {
            _setTone('tone-rain');
            for (var i = 0; i < 7; i++) {
                var d = document.createElement('i');
                d.className = 'drop';
                d.style.left = (14 + i * 20) + 'px';
                d.style.animationDelay = (i * .12) + 's';
                fx.appendChild(d);
            }
        } else if (kind === 'wind') {
            _setTone('tone-wind');
            for (var j = 0; j < 4; j++) {
                var w = document.createElement('i');
                w.className = 'wind';
                w.style.top = (30 + j * 22) + 'px';
                w.style.width = (26 + j * 8) + 'px';
                w.style.left = '10px';
                w.style.animationDelay = (j * .28) + 's';
                fx.appendChild(w);
            }
        } else if (kind === 'storm') {
            _setTone('tone-storm');
            for (var m = 0; m < 8; m++) {
                var dr = document.createElement('i');
                dr.className = 'drop';
                dr.style.left = (8 + m * 19) + 'px';
                dr.style.animationDelay = (m * .09) + 's';
                dr.style.background = 'linear-gradient(#8fb8e8,#5a90c8)';
                fx.appendChild(dr);
            }
            var bolt = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            bolt.setAttribute('class', 'xiaoe-bolt');
            bolt.setAttribute('viewBox', '0 0 30 44');
            bolt.innerHTML = '<path d="M17 0 L6 24 L14 24 L10 44 L24 18 L15 18 L21 0 Z" fill="#ffd94a" stroke="#f0b800" stroke-width="1"/>';
            fx.appendChild(bolt);
        } else if (kind === 'sunny') {
            _setTone('tone-sunny');
            var sun = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            sun.setAttribute('class', 'xiaoe-sun');
            sun.setAttribute('viewBox', '0 0 46 46');
            sun.innerHTML = ''
                + '<g class="rays" style="transform-origin:23px 23px;">'
                + '<g stroke="#ffcf4d" stroke-width="2.6" stroke-linecap="round">'
                + '<line x1="23" y1="2" x2="23" y2="8"/><line x1="23" y1="38" x2="23" y2="44"/>'
                + '<line x1="2" y1="23" x2="8" y2="23"/><line x1="38" y1="23" x2="44" y2="23"/>'
                + '<line x1="8" y1="8" x2="12.5" y2="12.5"/><line x1="33.5" y1="33.5" x2="38" y2="38"/>'
                + '<line x1="38" y1="8" x2="33.5" y2="12.5"/><line x1="12.5" y1="33.5" x2="8" y2="38"/>'
                + '</g></g>'
                + '<circle class="glow" cx="23" cy="23" r="12" fill="#ffd94a"/>'
                + '<circle cx="23" cy="23" r="8.5" fill="#ffe26b"/>';
            fx.appendChild(sun);
        } else if (kind === 'hot') {
            _setTone('tone-hot');
            _element.classList.add('steam');
        } else if (kind === 'cold') {
            _setTone('tone-cold');
            for (var s = 0; s < 6; s++) {
                var fl = document.createElement('div');
                fl.textContent = '❄';
                fl.style.cssText = 'position:absolute;left:' + (16 + s * 22) + 'px;top:-8px;font-size:11px;color:#a8d4ff;'
                    + 'animation:xiaoe-rain ' + (2.4 + s * .3) + 's linear infinite;animation-delay:' + (s * .4) + 's;';
                fx.appendChild(fl);
            }
        } else {
            // 纯变色
            _setTone('tone-' + kind);
        }

        if (durationMs) {
            _setWeather._t = setTimeout(function () { _setWeather(null); }, durationMs);
        }
    }

    // ---------- 睡眠 ----------
    function _startIdleWatch() {
        clearTimeout(_idleTimer);
        _idleTimer = setTimeout(function () {
            if (_busy || _dragging || _bubble) return;
            _mood = 'sleepy';
            _anim('sleep', 0);
            var z = _element.querySelector('.xiaoe-zzz');
            if (z) z.style.display = '';
        }, 60000);   // 60 秒无操作 → 睡着
    }

    function _wake() {
        if (_mood === 'sleepy') {
            _mood = 'idle';
            _anim('wake', 800);
            var z = _element.querySelector('.xiaoe-zzz');
            if (z) z.style.display = 'none';
        }
        _startIdleWatch();
    }

    // ---------- 对话气泡 ----------
    function _placeBubble() {
        if (!_bubble || !_element) return;
        var r = _element.getBoundingClientRect();
        var bw = _bubble.offsetWidth || 240;
        var bh = _bubble.offsetHeight || 130;
        var GAP = 14;                                  // 与小e 的间距
        // 默认放小e **右侧**（避开头顶的天气特效：太阳/雨/闪电/蒸汽都在上方）
        var left = r.right + GAP;
        var top = r.top + r.height / 2 - bh / 2;       // 垂直与小e 居中对齐
        // 右侧空间不够 → 翻到左侧
        if (left + bw > window.innerWidth - 12) {
            left = r.left - bw - GAP;
        }
        // 左右都放不下（超窄屏）→ 回到上方
        if (left < 8) {
            left = r.left + r.width / 2 - bw / 2;
            top = r.top - bh - GAP;
        }
        // 夹到视口内
        if (left < 8) left = 8;
        if (left + bw > window.innerWidth - 8) left = window.innerWidth - bw - 8;
        if (top < 8) top = 8;
        if (top + bh > window.innerHeight - 8) top = window.innerHeight - bh - 8;
        _bubble.style.left = Math.round(left) + 'px';
        _bubble.style.top = Math.round(top) + 'px';
        // 气泡尖角朝向小e：在左侧时尖角在左，在上方时尖角在下方
        var tw = _bubble.querySelector('.xiaoe-tip-arrow');
        if (tw) {
            if (top > r.bottom) {                       // 气泡在小e 上方 → 尖角朝下
                tw.style.cssText = 'left:26px;bottom:-8px;top:auto;right:auto;border-left:8px solid transparent;'
                    + 'border-right:8px solid transparent;border-top:9px solid #fff;border-bottom:0;';
            } else {                                    // 气泡在侧面 → 尖角朝左
                tw.style.cssText = 'left:-8px;top:26px;bottom:auto;right:auto;border-top:8px solid transparent;'
                    + 'border-bottom:8px solid transparent;border-right:9px solid #fff;border-left:0;';
            }
        }
    }

    function _say(text) {
        if (!_bubble) return;
        var s = _bubble.querySelector('.xiaoe-say');
        if (s) s.textContent = text;
        _placeBubble();
    }

    function _openChat() {
        // 幂等：已打开就聚焦输入框，不再关掉（避免双击/连击时误关）
        if (_bubble) {
            var ex = _bubble.querySelector('input');
            if (ex) ex.focus();
            _placeBubble();
            return;
        }
        var b = document.createElement('div');
        b.className = 'xiaoe-bubble';
        b.innerHTML = ''
            + '<div class="xiaoe-tip-arrow"></div>'
            + '<div class="xiaoe-say">嗨！我是小e 👋<br>问我 C4EAI 有什么功能，或者"怎么用共享库"～</div>'
            + '<div class="xiaoe-input-row">'
            + '  <input type="text" placeholder="问问小e…" maxlength="300">'
            + '  <button>发送</button>'
            + '</div>'
            + '<div class="xiaoe-tip">小e 不保存任何记录，关掉就忘 💭</div>';
        document.body.appendChild(b);
        _bubble = b;
        _placeBubble();
        var inp = b.querySelector('input');
        var btn = b.querySelector('button');
        inp.focus();
        inp.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); _ask(); }
            e.stopPropagation();
        });
        btn.addEventListener('click', _ask);
        setTimeout(function () {
            var h = function (e) {
                if (_bubble && !_bubble.contains(e.target) && !_element.contains(e.target)) {
                    _bubble.remove(); _bubble = null;
                    document.removeEventListener('pointerdown', h, true);
                }
            };
            document.addEventListener('pointerdown', h, true);
        }, 80);
    }

    function _ask() {
        if (!_bubble || _busy) return;
        var inp = _bubble.querySelector('input');
        var btn = _bubble.querySelector('button');
        var q = (inp.value || '').trim();
        if (!q) return;
        inp.value = '';
        _busy = true;
        btn.disabled = true;
        _say('让我想想… 🤔');
        _anim('float');
        _chatLog.push({ role: 'user', content: q });

        // 取当前对话上下文（最近若干轮，供小e 参考）+ 当前模型配置
        var ctx = '';
        var modelCfg = null;
        try {
            var br = window.state && window.state.branches && window.state.branches[window.state.currentBranchId];
            var msgs = (br && br.messages) || [];
            ctx = msgs.slice(-8).map(function (m) {
                return (m.role === 'user' ? '用户：' : '助手：') + String(m.content || '').slice(0, 400);
            }).join('\n');
        } catch (e) {}
        try {
            var mc = (window.modelConfig || {});
            var mid = (window.state && window.state.currentModel) || Object.keys(mc)[0];
            if (mid && mc[mid]) {
                modelCfg = {
                    model: mc[mid].model || mc[mid].id || mid,
                    base_url: mc[mid].endpoint || mc[mid].base_url || '',
                    api_key: (window.state && window.state.apiKeys && window.state.apiKeys[mid]) || mc[mid].api_key || ''
                };
            }
        } catch (e) {}

        fetch('/api/xiaoe/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: q, context: ctx, history: _chatLog.slice(-8), model: modelCfg })
        }).then(function (r) { return r.json(); }).then(function (d) {
            _busy = false;
            btn.disabled = false;
            var ans = (d && d.answer) || (d && d.error) || '（小e 走神了…）';
            _say(ans);
            _chatLog.push({ role: 'assistant', content: ans });
            _anim(ans.indexOf('⚠️') === 0 ? 'hit' : 'poke', 700);
            if (_bubble) { _bubble.querySelector('button').disabled = false; }
        }).catch(function (e) {
            _busy = false;
            if (btn) btn.disabled = false;
            _say('⚠️ 连不上后端：' + (e.message || e));
            _anim('hit', 800);
        });
    }

    // ---------- 开关 ----------
    function setEnabled(on) {
        try { localStorage.setItem(PREF_KEY, on ? '1' : '0'); } catch (e) {}
        if (on) {
            _build();
            _element.classList.remove('xiaoe-hidden');
            // 从设置里重新召唤时，回到默认位置（防上次拖到屏幕外）
            var r = _element.getBoundingClientRect();
            if (r.left < -20 || r.top < -20 || r.left > window.innerWidth || r.top > window.innerHeight) {
                _element.style.left = '22px';
                _element.style.top = 'auto';
                _element.style.bottom = '22px';
            }
            _anim('poke', 700);
        } else {
            if (_bubble) { _bubble.remove(); _bubble = null; }
            if (_element) _element.classList.add('xiaoe-hidden');
        }
        // 同步设置面板里的复选框
        var cb = document.getElementById('toggle-xiaoe');
        if (cb) cb.checked = !!on;
    }

    function isEnabled() {
        try { return localStorage.getItem(PREF_KEY) !== '0'; } catch (e) { return true; }
    }

    // ---------- 初始化 ----------
    function _init() {
        if (!isEnabled()) return;
        _build();
        // 首次出现：弹一下打招呼
        setTimeout(function () {
            _anim('poke', 700);
            _say('我是小e ✨ 点我聊天～');
            setTimeout(function () { if (_bubble) _say('我是小e ✨ 点我聊天，右键换天气～'); }, 1500);
        }, 900);
    }

    window.xiaoe = {
        init: _init,
        setEnabled: setEnabled,
        isEnabled: isEnabled,
        chat: _openChat,
        openChat: _openChat,
        weather: _setWeather,
        nextWeather: _cycleWeather
    };
    window.__xiaoeSetEnabled = setEnabled;

    // ---------- 自启动（本文件在 main.js 之后加载，故自行接线） ----------
    function _boot() {
        try {
            _init();
            var cb = document.getElementById('toggle-xiaoe');
            if (cb) {
                cb.checked = isEnabled();
                var lbl = document.getElementById('xiaoe-enabled-value');
                if (lbl) lbl.textContent = cb.checked ? '已开启' : '已关闭';
                cb.addEventListener('change', function () {
                    setEnabled(cb.checked);
                    var l = document.getElementById('xiaoe-enabled-value');
                    if (l) l.textContent = cb.checked ? '已开启' : '已关闭';
                });
            }
        } catch (e) { console.warn('小e 启动失败:', e); }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _boot);
    } else {
        setTimeout(_boot, 300);   // 等主流程 init() 跑完再出现，避免抢资源
    }
})();
