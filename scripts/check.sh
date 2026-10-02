#!/usr/bin/env bash
# C4EAI 快速自检（canonical check）—— 提交前跑一次，秒级完成。
#   bash scripts/check.sh
# 覆盖：Python 语法 / JS 语法 / 关键不变量。
# 不含浏览器真机验证（需起实例 + Edge CDP，见 开发中间产物/HANDOFF.md「验证方法」）。
set -u
cd "$(dirname "$0")/.."
PY="${C4EAI_PYTHON:-<ANACONDA-DIR>/python.exe}"
# node 是 Windows 程序，读不了 MSYS 的 /c/... 路径 → 用 cygpath 转成 C:\...
TMPDIR_WIN="$(cygpath -w "${TMPDIR:-/tmp}" 2>/dev/null || echo "$TEMP")"
CHK="$TMPDIR_WIN\\c4eai_chk.js"
fail=0
ok()  { echo "  OK   $1"; }
bad() { echo "  FAIL $1"; fail=$((fail+1)); }

echo "== Python 语法 =="
for f in backend/*.py backend/agent/*.py backend/tools/*.py; do
    [ -f "$f" ] || continue
    if env -u PYTHONPATH "$PY" -m py_compile "$f" 2>/dev/null; then ok "$f"; else bad "$f"; fi
done

echo "== JS 语法 =="
for f in js/*.js; do
    [ -f "$f" ] || continue
    cp "$f" "$CHK"
    if node --check "$CHK" 2>/dev/null; then ok "$f"; else bad "$f"; fi
done
rm -f "$CHK"

echo "== 关键不变量 =="

# ── 布局单源化守卫（2026-10-01）：防止"多套 CSS 相互替代"的堆叠重新长回来 ──
# ① transition:all 会把 padding/margin/font-size 一起做补间，而 JS 又在过渡中途量尺寸 →
#    中间值被写进 inline，越量越歪。全项目必须为 0，只允许显式属性白名单。
n_tall=$(grep -ro 'transition:[[:space:]]*all' css/*.css 2>/dev/null | wc -l | tr -d ' ')
if [ "$n_tall" = "0" ]; then ok "无 transition:all（布局补间不再污染尺寸）"; else bad "仍有 $n_tall 处 transition:all"; fi
# ② 手机断点必须限定给非桌面外壳，否则最小化时的假视口(157x25)会让手机壳接管桌面窗口
chk() { if grep -qF "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3"; fi; }
# ③ 视图状态必须持久化：WebView2 被系统回收会整页重载，不记状态就会"刷新后回到落地页"
chk js/state.js "c4eai-view-mode" 'state.js 持久化视图状态（重载后不丢界面）'
chk index.html "c4eai-view-mode" 'index.html 首帧前恢复视图状态'

# 手机端：隐藏侧栏箭头（与抽屉行为冲突）
# 小e 手机端可拖：touch-action:none 是必要条件
chk css/xiaoe.css 'touch-action: none' 'xiaoe.css touch-action:none'
chk js/xiaoe.js  'pointercancel'        'xiaoe.js pointercancel 兜底'
# 小e 随机天气：2.5%，且屏幕上不标注
chk js/xiaoe.js  'WEATHER_CHANCE = 0.025' 'xiaoe.js 概率 0.025'
# 免密钥聊天：只查 model 字段
chk backend/app.py 'req.model.get("model")' 'app.py 免密钥判据'
# 附件正文必须有截断上限常量（曾漏导入 → NameError → 500 → "未能生成回答"）
chk backend/agent/agent.py 'READ_FILE_MAX_CHARS' 'agent.py 导入 READ_FILE_MAX_CHARS'
# 笔记附件不解析（用户 2026-09-25 定：笔记=总结，附件=补充，天然分层）
chk backend/tools/file_tools.py 'parse: bool = True' 'file_tools upload_file 有 parse 开关'
chk backend/tools/file_tools.py 'if not parse:' 'file_tools parse=False 走 early-return'
chk backend/tools/shared_hub.py 'no_entry=1&parse=0' 'notes 发布附件时不解析'
chk backend/app.py 'folder_parses(folder_id)' 'app.py 解析策略看目标文件夹'
chk backend/tools/file_tools.py 'on_demand' 'read_file 对未解析附件即时提取原文'
# 分组树（用户 2026-09-25 定：顶层用 kind 区分，下层任意深度，分组带简介供 LLM 导航）
chk backend/tools/folder_tree.py '_recompute_paths' '分组树：ParentID 邻接表 + Path 缓存重算'
chk backend/tools/folder_tree.py '_is_descendant' '分组树：防循环移动'
chk backend/agent/tools.py 'knowledge as _knowledge' '知识库工具统一入口（不多写新工具）'
chk backend/tools/shared_hub.py 'async def knowledge' 'shared_hub 统一 knowledge 分发'
chk backend/app.py '@app.post("/api/groups")' 'app.py 分组端点'
chk js/database.js '_showMoveMenu' '前端右键条目「移动到分组」菜单'
chk js/database.js '_editGroup' '分组新建/编辑走经典浮窗'
# 树为骨架（用户 2026-09-25 定）：类型是根节点，分组在下，筛选排序外置
chk js/database.js '_renderGroupChildren' '树形渲染（类型→分组→条目）'
chk js/database.js '_dbTree' '树状态（展开态/筛选/排序）'
chk index.html 'db-f-owner-btn' '筛选器外置：上传者'
chk index.html 'db-f-year-btn' '筛选器外置：年份'
chk index.html 'db-f-sort-btn' '排序器外置'
chk backend/tools/shared_hub.py 'summary_only' 'list_entries 支持 summary_only（省 token）'
chk backend/tools/shared_hub.py 'year: str = ""' 'list_entries 支持 year 过滤（条件下推）'
# 文件夹树模型（用户 2026-09-25 定）：笔记/文档是真实根文件夹，解析看落在哪个夹
chk backend/tools/folder_tree.py 'def ensure_roots' '两个根文件夹（笔记/文档）'
chk backend/tools/folder_tree.py 'def folder_parses' '解析策略挂在文件夹上'
chk backend/tools/folder_tree.py 'ROOT_DOC' '文档夹 = parse=True'
chk backend/app.py 'folder_parses(folder_id)' '上传解析看目标文件夹'
chk backend/tools/file_tools.py 'parse=False → **只存原件**' 'parse=False 只存原件'
chk js/database.js '_dbUploadFolder' '上传按钮下沉到文件夹行'
chk js/database.js 'data-gact="upload"' '每个夹带上传按钮'
chk js/database.js 'data-gact="addentry"' '每个夹带新建条目按钮'
# 个人设置后端存储（用户 2026-09-26 定：密钥等改存「个人账号数据」）
chk backend/tools/user_settings.py 'MANAGED_KEYS' '设置键白名单'
chk backend/tools/user_settings.py 'def load_user_settings' '读个人设置'
chk backend/tools/user_settings.py 'def save_user_settings' '写个人设置'
chk backend/tools/user_settings.py '_user_dir(username)' '按身份隔离（本人目录）'
chk backend/app.py '/api/user/settings' '设置读写端点'
chk backend/app.py 'US.load_user_settings(user' 'owner 从登录身份推导（非请求体）'
# 设置后端存储的时序不变量（用户 2026-09-26 提醒：改后端后很多按钮会受影响）
chk js/api.js 'Promise.allSettled([hydrateUserSettings(), hydrateUserChats()])' 'init 必须先 hydrate（设置+对话）再初始化'
chk js/api.js 'state.enabledModels = JSON.parse(localStorage' 'enabledModels 在 hydrate 后读'
chk js/api.js 'loadAgentConfigFromLocal();' '智能体配置在 hydrate 后加载（原在 main.js 顶层）'
chk js/api.js 'updateNeo4jStatusUI(state.neo4j.connected)' 'Neo4j 状态在 hydrate 后更新'
chk js/state.js 'enabledModels: []' 'state.js 不在解析时读设置（那时 hydrate 未跑）'
# 对话历史后端存储（用户 2026-09-26 定：对话才是最该后端化的，换设备不能丢）
chk backend/tools/user_chats.py 'def load_user_chats' '读个人对话'
chk backend/tools/user_chats.py 'def save_user_chats' '写个人对话（按 id 合并）'
chk backend/tools/user_chats.py '_user_dir(username)' '对话按身份隔离（本人目录）'
chk backend/app.py '/api/user/chats' '对话读写端点'
chk backend/app.py 'UC.load_user_chats(user' '对话 owner 从登录身份推导（非请求体）'
chk js/user_chats.js 'function hydrateUserChats' '对话启动时从后端灌回缓存'
chk js/user_chats.js 'function setUserChats' '对话写入后节流推后端'
chk js/user_chats.js 'function syncChatsFromServer' '收到推送后拉全量并合并'
# ★ 对话**不写浏览器 localStorage 副本**（2026-09-27 用户报「本地缓存空间不足」后撤掉）：
#   localStorage 每站点只有几 MB，装不下全部对话；而「服务器」就是本机后端，再存一份副本
#   既无必要，又正是墓碑误删事件的温床。唯一真相 = workspace/users/<身份>/chats.json。
if grep -q "identityKey('ai-branches'" js/branching.js js/user_chats.js 2>/dev/null; then
    bad '仍在写 localStorage 对话副本（已改为只存本机后端）'
else
    ok '对话不写 localStorage 副本（唯一真相=本机后端 chats.json）'
fi
chk js/user_chats.js 'function _purgeLegacyCache' '旧对话缓存一次性清理（把配额还回来）'
chk js/user_chats.js 'function markBranchDeleted' '★墓碑只由显式删除写（看不见≠被删）'
chk js/user_chats.js '墓碑作废' '★墓碑作废：活副本更新→丢墓碑（防反复删数据）'
chk backend/tools/user_chats.py '墓碑作废' '★后端同样墓碑作废（防旧墓碑再删一次）'
chk js/user_chats.js 'function _mergeBranchSets' '按对话 id 合并（多设备同时在线的基础）'
chk js/branching.js "if (typeof setUserChats === 'function') setUserChats();" 'saveBranches 挂钩推后端'
# 并发模型：允许两边同时登录（用户 2026-09-26 收回单点登录）
_chk_kick="$(grep -c 'for _old, _rec in list(_sessions.items())' backend/auth.py 2>/dev/null | head -1)"
_chk_kick="${_chk_kick:-0}"
if [ "$_chk_kick" -eq 0 ]; then ok '未启用单点登录（两边可同时在线）'; else bad '仍存在登录踢旧 token 逻辑（用户已收回该做法）'; fi
# 多设备同步：SSE 推送（用户不要轮询）
chk backend/app.py '/api/events' 'SSE 事件推送端点'
chk backend/app.py '_notify(user["username"], "chats")' '对话变更推送给该账号其它页面'
chk backend/tools/user_chats.py 'def chats_rev' '对话版本号（断线重连后对齐用）'
chk js/events.js 'function startEvents' 'SSE 客户端'
chk js/events.js 'resp.body.getReader()' 'SSE 用 fetch+getReader（EventSource 带不了 Authorization 头）'
chk js/events.js 'resp.status === 401' '未登录不空转重连'
chk js/api.js 'startEvents()' 'init 后启动事件流'
# 共享库性能（用户反馈"卡顿、每次都要加载"）：列表不带正文 + 按需取 + gzip
chk backend/app.py 'with_content' '列表可只返回目录（不含正文）'
chk backend/app.py 'def _strip_content' '去正文实现'
chk backend/app.py 'GZipMiddleware' 'gzip 压缩（435KB → 约 1/3）'
chk backend/tools/file_tools.py 'def get_note(' '取单条条目（正文按需）'
chk js/database.js 'with_content=0' '数据库面板列表不带正文'
chk js/database.js 'function _dbEnsureContent' '正文按需取（展开/编辑时才取那一条）'
chk js/database.js 'function _dbFillEntryBody' '展开时才填充正文'
# 产出区（用户 2026-09-26 定：同笔记附件一样一个占位符，链接后端真实文件位置）
chk backend/tools/file_tools.py 'def _user_outputs_dir' '本人产出区目录'
chk backend/tools/file_tools.py 'def register_output' '产出登记：只记指针、不复制文件'
chk backend/tools/file_tools.py '"rel": rel_path' '★附件 meta 存相对指针（后端只有一份）'
chk backend/tools/file_tools.py 'def list_outputs' '列产出（未登记自动补登记）'
chk backend/tools/file_tools.py '".." in rel.split("/")' '★指针越界校验（唯一边界）'
chk backend/app.py '/api/outputs/list' '产出清单端点'
chk backend/agent/tools.py 'tools_registry["list_outputs"]' 'agent 侧产出工具'
chk backend/agent/agent.py '产出文件一律写到' '提示词：产出规则 + 必须给占位符'
chk backend/tools/code_sandbox.py 'cwd=outdir' 'run_python 产出落产出区（不再随临时目录蒸发）'
chk backend/tools/terminal_tool.py '_user_outputs_dir()' 'terminal 默认 cwd = 产出区（原为项目根）'
chk backend/tools/file_tools.py 'fid = uuid.uuid4().hex' '★file_id 必须纯十六进制（前端 {{file:}} 正则只认 [0-9a-fA-F]+）'
chk js/formatting.js "ctx === 'note'" '聊天里的附件卡按设置派发；笔记/数据库仍走 openDbFile'
# Neo4j 失败必须降级而非终止对话（三处：两流式 + 一非流式）
_n4="$(grep -c '连不上只降级摘掉图谱工具' backend/agent/agent.py 2>/dev/null || echo 0)"
if [ "$_n4" -ge 3 ]; then ok "Neo4j 失败降级 ($_n4 处)"; else bad "Neo4j 降级仅 $_n4 处（应≥3）"; fi
_hn="$(grep -c 'Neo4j连接失败: {cr.get' backend/agent/agent.py 2>/dev/null | head -1)"
_hn="${_hn:-0}"
if [ "$_hn" -eq 0 ]; then ok '无 Neo4j 硬失败 return'; else bad "仍有 $_hn 处 Neo4j 硬失败"; fi
# 对话区必须把附件 file_id 传给 agent
chk js/messages.js 'fileIds: attachmentsMetadata.map' 'messages.js 传附件 fileId 给 agent'
# 共享库分流：判据 = 「库在哪」（C4EAI_HUB 有无地址），不是「我是谁」
chk backend/tools/shared_hub.py 'def _hub_base()' 'shared_hub 库地址判据'
chk backend/tools/shared_hub.py 'from tools.vector_search import search_knowledge' 'shared_search 云端直读本地索引'
# 后端不得存在未定义的全局名（pyflakes）
_pf="$(env -u PYTHONPATH python -m pyflakes backend/ 2>/dev/null | grep -i 'undefined' || true)"
if [ -z "$_pf" ]; then ok '后端无 undefined name (pyflakes)'; else bad "未定义名: $_pf"; fi
# 附件多选：笔记编辑器动态 input 必须 multiple（曾漏改 → 用户无法多选）
chk js/messages.js 'inp.multiple = true' 'messages.js 笔记附件支持多选'
# 全仓不应再有只取 files[0] 的附件上传（image_tools 单图工具例外）
_stray="$(grep -rn 'files\[0\]\|files?\.\[0\]' js/*.js 2>/dev/null | grep -v '^js/image_tools.js' || true)"
if [ -z "$_stray" ]; then ok '无遗漏的单选 files[0]'; else bad "仍有单选: $_stray"; fi

echo

# ── 顶栏统一（2026-10-02）守卫 ──
grep -q -- "--header-h" css/variables.css || { echo "FAIL: variables.css 缺 --header-h 单源"; exit 1; }
! grep -q "@media" css/window.css || { echo "FAIL: window.css 出现媒体查询（窗口键被隐藏过）"; exit 1; }
! grep -q "auth-chip-slot\|logo-text-wrapper\|company-tagline" index.html || { echo "FAIL: index.html 顶栏残留登录槽/字标/副标"; exit 1; }
grep -q "header-brand" index.html || { echo "FAIL: index.html 顶栏缺右侧字标 header-brand"; exit 1; }
grep -q "theme-btn" index.html || { echo "FAIL: index.html 顶栏缺主题按键 theme-btn"; exit 1; }
! grep -q "mobile-more-btn\|mobile-more-sheet" index.html || { echo "FAIL: index.html 残留 ⋯ 面板"; exit 1; }
! grep -q "mobile-more" css/layout.css || { echo "FAIL: layout.css 残留 ⋯ 面板样式"; exit 1; }
grep -q "nav-container.tight" css/layout.css || { echo "FAIL: layout.css 缺顶栏 tight 阶梯"; exit 1; }
grep -q "bindHeaderFit" js/topbar.js || { echo "FAIL: topbar.js 缺顶栏溢出阶梯"; exit 1; }
! grep -q "orientationchange" js/topbar.js || { echo "FAIL: topbar.js 又用设备事件 orientationchange"; exit 1; }
! grep -rq --exclude='*.bak*' --exclude-dir=node_modules "agent-config-panel\|agent-config-toggle" index.html js/ css/ || { echo "FAIL: 智能体配置独立面板残留（应已并入设置）"; exit 1; }
grep -q "settings-nav" index.html || { echo "FAIL: 设置面板缺左分类 settings-nav"; exit 1; }
grep -q "settings-pane" index.html || { echo "FAIL: 设置面板缺右侧内容区 settings-pane"; exit 1; }
grep -q "pane-agent" index.html || { echo "FAIL: 设置面板缺「智能体」分类"; exit 1; }
grep -q "win-tight" css/input.css js/topbar.js || { echo "FAIL: 输入条窄屏判据 win-tight 缺失"; exit 1; }
grep -q "css/settings.css" index.html || { echo "FAIL: 设置样式表 css/settings.css 未引入"; exit 1; }
! grep -rq "shell-desktop" index.html js/ css/ --include="*.js" --include="*.css" --include="*.html" 2>/dev/null || { echo "FAIL: 外壳(css/js 形态分叉)判据残留"; exit 1; }
! grep -rq --exclude="*.bak*" "mobile-item-more\|mobile-actions-open\|mobile-chat-title" index.html js/ css/ || { echo "FAIL: 手机专用元素残留"; exit 1; }
grep -q "js/topbar.js" index.html || { echo "FAIL: 顶栏脚本未引入"; exit 1; }
grep -q "reasoning-rail-btn" index.html || { echo "FAIL: 思考收起浮动键缺失（方案 A：对话区右侧）"; exit 1; }
grep -q "reasoning-rail-btn" css/messages.css || { echo "FAIL: 浮动键样式缺失"; exit 1; }
! grep -rq --exclude="*.bak*" "agent-reasoning-toggle" index.html js/ css/ || { echo "FAIL: 旧的顶部内联收起键复活（已按方案 A 移除）"; exit 1; }
grep -q "updateReasoningRail" js/messages.js js/ui.js js/graph_rag.js || { echo "FAIL: 浮动键状态同步缺失"; exit 1; }
grep -q "reasoningRailVisible" js/messages.js || { echo "FAIL: 浮动键丢了「屏幕上有没有思考」的视口判据"; exit 1; }
[ $(grep -l "^\.settings-panel\|^\.settings-nav\|^\.settings-pane" css/*.css | grep -v "css/settings.css" | wc -l) -eq 0 ] || { echo "FAIL: 设置样式散落在 settings.css 之外"; exit 1; }
! grep -q "agent-config-panel" index.html || { echo "FAIL: 智能体独立面板复活"; exit 1; }
! grep -q "settings-content" index.html || { echo "FAIL: 旧设置内容区残留"; exit 1; }
! grep -q "placeNavControls" js/topbar.js || { echo "FAIL: mobile.js placeNavControls 复活"; exit 1; }

# ── 侧栏平推（2026-10-02）守卫 ──
grep -q -- "--sb-w" css/variables.css || { echo "FAIL: variables.css 缺 --sb-w 单源"; exit 1; }
grep -q -- "--chat-min" css/variables.css || { echo "FAIL: variables.css 缺 --chat-min"; exit 1; }
! grep -q "sb.style.width" js/sidebar.js || { echo "FAIL: sidebar.js 仍有 inline 宽度（须走 _sbSetW/--sb-w）"; exit 1; }
grep -q "_sbSetW" js/sidebar.js || { echo "FAIL: sidebar.js 缺 _sbSetW 入口"; exit 1; }
! grep -q "adoptSidebar" js/topbar.js || { echo "FAIL: mobile.js adoptSidebar 复活"; exit 1; }
grep -q "chat-section.narrow .chat-sidebar" css/layout.css || { echo "FAIL: layout.css 缺窄空间抽屉样式"; exit 1; }
# ★ 收起 = 整体平移滑出（2026-10-03 用户拍板「选B方案，2.3可以」）：宽度恒定、只补间 margin-left，容器负责裁切。
#   老做法是补间 width + 归零 padding/border → 面板内容被一点点吃掉，看起来「先胀再收」。以下守卫防它长回来。
grep -q "chat-section:not(.narrow) .chat-sidebar.collapsed" css/layout.css || { echo "FAIL: 缺侧栏平推收起规则"; exit 1; }
grep -q "margin-left: calc(-1 \* var(--sb-w))" css/layout.css || { echo "FAIL: 侧栏收起未走 margin-left 平推"; exit 1; }
! grep -qE "^[[:space:]]*\.chat-sidebar\.collapsed[[:space:]]*\{" css/*.css || { echo "FAIL: 又出现 width 归零式收起的 .chat-sidebar.collapsed{ 块"; exit 1; }
! grep -q "transition:[[:space:]]*width" css/layout.css css/input.css || { echo "FAIL: 侧栏又开始补间 width（内容会被裁切挤压）"; exit 1; }
grep -q "flex: 0 0 auto" css/layout.css || { echo "FAIL: 侧栏未锁定不可压缩（最小化假视口会把它挤扁）"; exit 1; }
# ★ 宽度只有两个来源：用户意图（存档）+ 客观上限。禁止再出现「把量出来的渲染宽度当输入」的记忆变量
! grep -q "_sbAutoBack\|_sbFocusBack" js/sidebar.js || { echo "FAIL: 侧栏又出现渲染宽度记忆变量（最小化会压成 82vw）"; exit 1; }
grep -q "_sbApplyWidth" js/sidebar.js || { echo "FAIL: 侧栏缺唯一宽度贴合入口 _sbApplyWidth"; exit 1; }
# ★ 新建对话键（方案 B：淡主题底 + 主题色图标，28px；不许回到实心蓝渐变 + 光晕）
grep -q "var(--primary-soft)" css/sidebar.css || { echo "FAIL: 新建对话键未用方案 B（淡主题底）"; exit 1; }
! grep -q "primary-gradient" css/sidebar.css || { echo "FAIL: 新建对话键又用回实心渐变"; exit 1; }
grep -q "color-mix" css/sidebar.css || { echo "FAIL: 新建对话键 hover 未用主题色加深"; exit 1; }
# ★ 对话区空态卡「还没有对话 / 点下面的按钮开始新的聊天 + 按钮」已按用户要求删除（2026-10-03「完全删除，不要那个功能」），别复活
! grep -rq --exclude='*.bak*' "empty-chat-cta\|点下面的按钮开始新的聊天" index.html js/ css/ || { echo "FAIL: 对话区空态卡/CTA 复活（用户已要求删除）"; exit 1; }

# ── CSS 残渣体检（2026-10-02）——改前端必看：死样式 / 括号残渣 ──
for a in scripts/css_audit.py scripts/css_class_audit.py; do
    [ -f "$a" ] || { echo "FAIL: 缺体检脚本 $a"; exit 1; }
done
_css_struct="$(env -u PYTHONPATH "$PY" scripts/css_audit.py . 2>&1)"
if printf '%s' "$_css_struct" | grep -q '全部 CSS 括号平衡'; then
    ok "CSS 结构干净（无游离括号 / 空规则块 / 空行堆积）"
else
    bad "CSS 结构残渣：$(printf '%s' "$_css_struct" | tr -d '\n' | cut -c1-200)"
fi
_css_dead="$(env -u PYTHONPATH "$PY" scripts/css_class_audit.py . --warn-only 2>&1)"
_dead_n="$(printf '%s' "$_css_dead" | grep -c '^   \.')"
if [ "$_dead_n" = "0" ]; then
    ok "CSS 无死样式（每个类都有人生成）"
else
    echo "  INFO 发现 $_dead_n 个「CSS 定义但代码从不生成」的类 —— 用 scripts/css_purge_dead.py 预演后再决定"
fi
if [ "$fail" -eq 0 ]; then echo "ALL_PASS ✅"; else echo "FAILED ($fail)"; fi
exit "$fail"
