#!/usr/bin/env bash
# C4EAI 快速自检（canonical check）—— 提交前跑一次，秒级完成。
#   bash scripts/check.sh
# 覆盖：Python 语法 / JS 语法 / 关键不变量。
# 不含浏览器真机验证（需起实例 + Edge CDP）。
set -u
cd "$(dirname "$0")/.."
PY="${C4EAI_PYTHON:-python}"
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
chk() { if grep -qF "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3"; fi; }
# 手机端：隐藏侧栏箭头（与抽屉行为冲突）
chk css/mobile.css 'sidebar-toggle-btn { display: none' 'mobile.css 隐藏 sidebar-toggle-btn'
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
chk js/user_chats.js "ai-branches-deleted" '★删除留墓碑（否则别处的旧副本会把删掉的复活）'
chk js/user_chats.js 'function _mergeBranchSets' '按对话 id 合并（多设备同时在线的基础）'
chk js/branching.js "if (typeof setUserChats === 'function') setUserChats();" 'saveBranches 挂钩推后端'
chk js/branching.js "identityKey('ai-branches')" '对话仍按身份隔离存缓存'
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
if [ "$fail" -eq 0 ]; then echo "ALL_PASS ✅"; else echo "FAILED ($fail)"; fi
exit "$fail"
