# 更新日志（CHANGELOG）

> 本文件记录**每次改动做了什么、为什么、影响哪些文件**。
> 接手项目前先读本文件 + [`HANDOFF.md`](HANDOFF.md)（当前状态快照）。
> 格式：`[日期] 类型(范围): 摘要` + 改动明细 + 影响文件。

---

## [2026-09-24] fix(search): 知识库检索 —— 性能 1359x + 补齐 12 个文档索引缺口

**用户最在乎**：知识库检索。实测（读源码 + 云端真实索引）发现 5 个问题，本轮修 4 个。

**问题 ① 每次检索都重算全库 embedding（最严重）**
```python
doc_vecs = embed_texts(texts)          # 旧：把全库文本重新跑一遍 embedding
doc_vecs = _index_vectors(index)       # 新：直接用索引里现成的 vector
```
索引里本就存了每块的 `vector`（512 维），但检索**不用它**而是重算。
实测：580 块重算 **11.50s** → 取现成向量 **0.0085s**，**提速约 1359x**。

**问题 ② 24 个附件有条目但没进索引（12 个文档搜不到）**
索引只覆盖 52 个文档，共享库有 64 个 document 条目 —— 其中「2026年meeting」
一个条目挂了 14 个附件，只有 1 个进了索引。
补齐脚本 `tools/backfill_knowledge_index.py`（默认 dry-run、幂等、**写入后重新核对**）：
**249 块 → 580 块（+331）**，24/24 落盘核对通过。
跳过 1 个（`meeting12.29.pptx` 仅 20 字符，无实质内容）。

**问题 ③ 阈值 0.3 偏低** → `_SCORE_THRESHOLD = 0.42`（可用 `C4EAI_SCORE_THRESHOLD` 覆盖）。
bge-small-zh 下真相关通常 0.5~0.8，0.3 会把不相关块放进来。

**问题 ④ 返回文本截断到 300 字符** → 返回**完整块**（块本身才 500）。
此前答案可能正好被切在 300 处。

**问题 ⑤（新增）relevance 分级** —— 借 WeKnora：把裸分数翻译成人话给 LLM 判断
```
>=0.75 高相关   >=0.60 中相关   >=阈值 低相关   其余 弱相关
```
检索结果同时带上 `relevance` 与 `file_id`（可溯源到附件）。

**未做（评估后否决）**：重排（Rerank）。原理是把查询与文档拼一起喂模型逐条细读，
比向量检索准但**每条要一次模型调用**，与"小模型、单次调用、不复杂"的偏好冲突；
且当前数据量（580 块）向量粗筛问题不严重。留待按需。

**文件**：`backend/tools/vector_search.py`、`tools/backfill_knowledge_index.py`（新增）
**验证**：24/24 ad-hoc —— 含线上三条查询均命中（含新补文档）、分级与分数一致、
全部高于新阈值、文本不再截断；`bash scripts/check.sh` ALL_PASS

---

## [2026-09-24] feat(tools): 共享库体检 lint —— 借鉴 WeKnora 的 stale_ref 机制

**Why**：共享库 66 个条目引用的附件，此前**没有任何机制验证它们还在**。
之前云端 pptx 404 就是这么来的（条目引用本机 file_id，云端 pool 没文件），
修了一整轮 + 回填 14 个文件。有 lint 就能**提前发现**而不是等用户点开才 404。

**设计**（对齐用户偏好）
- **独立只读脚本**，不碰后端主流程（宁可写独立脚本也不碰复杂流程）
- **0 次 LLM 调用**，纯本地判定
- **判据全部是客观事实**（文件在不在），无兜底默认值、无防呆断言

**检查项**
| 类型 | 级别 | 含义 |
|---|---|---|
| `cloud_missing` | error | 引用附件在云端找不到 → 会 404 |
| `local_only` | warning | 本机有、云端无 → 同步缺口 |
| `empty_entry` | info | 空条目 |
| `dup_file` | info | 同一附件被多条引用 |
| `orphan` | info | 磁盘有附件但无人引用 |
| `note_with_file` | info | note 里含附件（按新设计应在 document）|

**健康分**：0-100（借鉴 WeKnora HealthScore；error −10 / warning −3 / info −0.5）
**退出码**：0 无问题 / 1 有 warning / 2 有 error（可接 CI/cron）

**文件**：`tools/lint_shared_hub.py`（约 230 行，只读）
**用法**
```bash
python tools/lint_shared_hub.py --token <token>              # 云端体检
python tools/lint_shared_hub.py --token <token> --local-root <workspace>  # 对比同步
python tools/lint_shared_hub.py --token <token> --json       # 机器可读
```

**验证**：9/9 ad-hoc —— **注入 5 条假数据确认真能检出**（cloud_missing / empty_entry /
dup_file / note_with_file 全部命中，且正常引用不误报）；真实云端 **健康分 100/100**，
77 个引用全部有效。

---

## [2026-09-24] feat(hub): 共享库条目分类 kind（笔记/文档）+ 存量迁移

**用户设计**：共享库条目分两类，**数据结构完全一致**（都是 文本+附件），
只是语义标签不同：
- `kind="note"` —— 人写的笔记，适合直接阅读，**共享库默认展示**
- `kind="document"` —— 机器解析的文档正文，文本乱但**用于检索/向量索引**

**Why**：「共享库编辑上传」把解析正文塞进笔记条目 → 一个条目既有手写内容又有
机器解析的几千字 → **笔记混乱**。解决：解析结果**独立成文档条目**。

**改动**
- `backend/tools/file_tools.py`：`list_notes(kind=)` 过滤 / `add_note(kind=)` /
  `update_note(kind=)`；老数据无 kind → 视为 `note`（向后兼容）
- `backend/app.py`：`NoteAddReq.kind` / `NoteUpdateReq.kind` / `GET /api/notes?kind=`
  查询参数 / 代理转发带 kind；**上传建条目改为 `kind="document"`**，
  正文去掉「附件正文（上传时自动解析）」标记（不再混入笔记）
- `backend/tools/shared_hub.py`：`db_notes(kind=)` 参数；云端 add 与本地转发均传 kind；
  **PUT(update) 分支补转发 kind**（此前漏传 → kind 永远写不进，迁移首次失败的真因）
- `js/database.js`：`_dbKind` 状态 + `_switchDbKind()` + 按 kind 拉取；
  新建笔记带 `kind:'note'`；**上传后自动切到文档 Tab**
- `index.html`：`#db-tabs` 切换按钮（📝 笔记 / 📄 文档），笔记默认 active
- `css/layout.css`：`.db-tabs` 样式
- `tools/migrate_note_kind.py`：存量迁移（默认 dry-run）——**判据为客观事实**
  （正文含 `{{file:...}}` 占位符 ⇒ 上传产物 ⇒ document；纯手写 ⇒ note）；
  幂等；**写入后重新拉取逐条核对**

**迁移实况**：云端 66 条 → **document 64 / note 2**
（2 条纯手写：CATDA论文·全文翻译、AgentCAT 论文全文翻译）

**教训**
- 只信 HTTP 200 会报**假成功**：首次迁移报「成功 66/66」，实测 `{None: 66}`。
  真因三重：① 云端跑旧代码 ② shared_hub PUT 漏传 kind ③ 脚本没核对落盘
  → 迁移脚本此后**必须写入后重新 GET 核对**
- Python 不热加载：改后端后云端须重启
  （`pkill -f 'backend/app[.]py'` 再 `setsid nohup ~/start_c4eai.sh`）

**验证**：31/31 ad-hoc 定向验证；`bash scripts/check.sh` ALL_PASS

---

## [2026-09-24] fix(shared-hub): 云端 shared_search 恒报「需要登录身份」—— 云端不该请求自己

**用户反馈**：「shared search 无法访问，我明确已经登录，且数据库可以点击查看，
共享服务器需要登录身份：请在网页右上角点击「登录」一次即可」

**根因**
- `shared_hub.shared_search()` 用 `_hub_base()` 取地址，默认 `http://<HUB-IP>:8088`
- 云端实例**就在这台服务器上** → 等于「HTTP 请求自己」
- 而 `_token()` 读的是 `workspace/_auth/hub_session.json` —— 该文件**只在本地实例生成**
  （云端登录写的是 `sessions.json`）→ 云端拿不到 token → 云端自己回 401
  → `_friendly()` 把 401 译成「共享服务器需要登录身份」
- 即：**云端检索的是一条注定失败的回环**

**修复（`backend/tools/shared_hub.py`）**
新增 `_is_hub_instance()`，判据 = `C4EAI_AUTH=1`（**与 `role_policy.HUB` 完全同口径**，全项目统一）：

| 跑在哪 | `C4EAI_AUTH` | 行为 |
|---|---|---|
| 云端（服务器） | `1`（`start_c4eai.sh` 设） | **直读本地索引**（`vector_search.search_knowledge`），不走 HTTP |
| 本地（用户电脑） | 未设置（`start.bat` 不设） | **保持原样 HTTP 转发**到服务器 |

- `shared_search` / `shared_upload` 均按此分流
- 云端路径打日志 `[shared_hub] 云端实例：直接读写本地共享库（不走 HTTP 回环）`
- **默认安全**：只有显式设 `C4EAI_AUTH=1` 才走本地 → 本地行为 100% 不变

**为什么不做「云端也走 HTTP 回环」**（用户问过）
1. 服务器上再请求自己是纯浪费（网络栈 → uvicorn → 同一进程）
2. 需要额外维护一份 `hub_session.json` 状态，将来必出别的问题
3. 单 worker 下自己等自己有死锁风险（此前已踩过并发卡死）

**canonical check 增强**（防呆）
- `shared_hub 云端/本地分流判据` / `shared_search 云端直读本地索引` / `role_policy.HUB 同口径`
- **`start.bat 不得设置 C4EAI_AUTH`** ← 一旦手滑，本地会误判为云端

**实测**
- 分流单测（httpx 打桩）**12/12**：本地确实发 HTTP 到 `<HUB-IP>:8088/api/search/knowledge`；
  云端走本地索引、HTTP 调用次数 0；与 `role_policy.HUB` 口径一致
- 云端真机（SSH 进程内调用）：`is_hub_instance=True`、
  `RESULT: {"status":"ok","count":3,...}`、**`HAS_NEED_LOGIN: False`** ✅
- 云端 HTTP：admin 登录 → `/api/search/knowledge` HTTP 200、`count=3`
- `bash scripts/check.sh` ALL_PASS ✅

---

## [2026-09-22] fix(agent): 上传附件后「未能生成回答」—— NameError 500 + file_id 漏传 + Neo4j 硬终止

**用户反馈**：「我在对话区域上传的时候，发送消息提示 agent 未能生成回答」

**三个根因（逐个实测确认）**

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| ① | `agent.py:327` `_prepare_file_context()` | 用了 `READ_FILE_MAX_CHARS` 但**从未导入** | 附件**有可提取文本**就抛 `NameError` → **HTTP 500** → 前端「未能生成回答」 |
| ② | `js/messages.js` `sendMessage()` | 调 `reactAgentLoop()` 时**没传 `fileIds`** | `file_ids` 恒为空 → 附件正文不进 LLM（答非所问） |
| ③ | `agent.py` 三处 Neo4j 启动检查 | 连接失败用 `yield error + return` **终止整个对话** | 只要勾了 `graph_schema`/`execute_cypher` 且 Neo4j 未连上，普通对话也直接失败 |

**修复**

① `backend/agent/agent.py`：`_prepare_file_context` 的 import 补上 `READ_FILE_MAX_CHARS`
```python
from tools.file_tools import (image_to_base64, _resolve_upload,
                              get_upload_text as get_file_text,
                              READ_FILE_MAX_CHARS)
```

② `js/messages.js`：把附件 file_id 传给 agent
```js
await reactAgentLoop(fullMessage, {
    images: base64Images.map(b => b.base64),
    note_refs: refsMeta,
    fileIds: attachmentsMetadata.map(a => a.file_id).filter(Boolean)   // 新增
});
```

③ `backend/agent/agent.py`（**3 处**：2 处流式 + 1 处非流式 `run_agent`）：
Neo4j 连不上 → **降级摘掉图谱工具并继续**，不再 `return`。
```python
if not cr.get("success"):
    _n4_err = cr.get("error", "")
    enabled_tools = [t for t in enabled_tools
                     if t not in ("graph_schema", "execute_cypher")]
    yield {"type": "reasoning",
           "text": f"（图谱未连接，已跳过图谱工具继续回答：{_n4_err}）\n"}
```
> 用户定：**Neo4j 是可选增强**。工具层 `execute_cypher` 失败时本就返回
> `{"error":"Neo4j 未连接"}` 让 LLM 自行判断跳过；启动检查也不该反过来打断整个对话。

**canonical check 增强**（`scripts/check.sh`，防同类 bug 复发）
- `agent.py 导入 READ_FILE_MAX_CHARS`
- `Neo4j 失败降级 (≥3 处)` + `无 Neo4j 硬失败 return`
- `messages.js 传附件 fileId 给 agent`
- **`后端无 undefined name (pyflakes)`** ← 这类 NameError 本该被静态检查拦住

**实测**
- `_prepare_file_context` 5 场景 9/9 PASS：短文本注入 ✅ / 超长附件截断分支正常（44000→20000，无 NameError）✅ / 历史附件只给引用说明 ✅ / 未知 file_id 不崩 ✅ / 空输入返回空 ✅
- Neo4j 三路径（含 graph_schema / 含 execute_cypher / 两者都有）均**不再硬失败** ✅
- 端到端：上传附件带 file_ids 请求 HTTP **200**（原 500）、响应 len **4746**（原 21 =「未能生成回答」）✅
- `bash scripts/check.sh` ALL_PASS ✅

---

## [2026-09-22] feat(perm): 权限体系细化 —— 红+全读写/全工具、金=管理员（方案 B）

**用户裁定（三条）**
1. 橙色 L3 **不允许**读写服务器文件
2. 红色 L4 及以上 → **全部读写 + 全部工具**
3. 金色 L5 = **管理员权限一致**，但「删账号」+「改他人数据」仍归 admin（**方案 B**）

**改动**

`backend/role_policy.py`
- `is_super()` 语义扩展为**管理级**（L5 金 + admin）；新增 `is_root()` = 仅 admin
  - 分离原因：金色要"与管理员一致"，但**删账号**和**授予 L5** 必须 admin 独占（防金色互相提权/批量造金）
- `filter_tools`：`lv >= L_RED` 给全部工具（原仅 admin）
- `user_sandbox`：`lv >= L_RED` 不锁目录（原仅 admin）→ 红+可读写全盘
- `tool_denied("code")`：`lv >= L_RED` 放行（**修复**：原仅 admin，与 filter_tools 口径不一致 → 红色看得到工具却一用就 403）
- `enforce_mode`：金色保留所选 permission_mode
- `can_set_level`：改用 `is_root`（只有 admin 能授予 L5）

`backend/app.py`
- 新增 `_is_authorized()`（L1+ 授权门槛）、`_owns_or_root()`（本人 或 admin）
- **10 处 `role != "admin"` 全部改为按 level 判断**（旧判断不认 level，且 xhq1/xhq2 的 role 恰为 admin → 会误放行金色）
- 新增 `_require_root()`：删账号仅 admin（原 `_require_admin` 金色也能过）
- 删除死代码 `_require_super()`（无调用点）
- `/api/auth/users` 返回增加 `is_root`；`is_super` 改为反映 L5

`js/auth.js`
- 用户管理面板：删账号按钮 **仅 `is_root` 可见**；等级下拉 `maxLv` 按 `is_root` 判定（金色封顶 L4）

`js/database.js` / `js/sidebar.js`
- `isAdmin` 判定由 `me.role === 'admin'` 改为 **`me.username === 'admin'`**
- 原因：历史账号 xhq1/xhq2 的 `role` 字段仍是 `admin`，按 role 判会让金色**看到**编辑/删除按钮（点了才被后端拒），体验割裂
- 版本号：`database.js?v=11→12`、`sidebar.js?v=8→9`

**实测**（云端真机，37/37 PASS）
- 文件基座：L0/L1/L3 锁本人目录；**L4/L5/admin 不锁**（全盘读写）✅
- 工具：L1-L3 六个（无执行类）；**L4/L5/admin 九个（含 run_code/terminal/skill_write）** ✅
- `tool_denied(code)`：L3 拒 / L4 放 / L5 放 ✅
- `is_super`=仅 L5+admin；`is_root`=仅 admin ✅
- 金不能授 L5、可授 L4；admin 可授 L5、自身不可被调 ✅
- **★ `_owns_or_root`：本人 True / 金色改他人 False / 红色改他人 False / admin True** ✅

---

## [2026-09-22] fix(auth): 身份色点不跟随等级 —— 三处漏 level 致顶栏芯片恒为蓝色

**用户反馈**：「注册账号边上的点，我已经授予了对应权限，但是边上的点没有变成对应的颜色，
比如 xhq1 还是蓝色」

**诊断（实测）**
- 后端 `/api/auth/users` 返回的 `xhq1` **已是 `level=5`**，用户管理面板渲染 **金色** ✅
- 但**顶栏身份芯片**显示蓝色 —— 说明问题在**前端身份存储**
- 定位到三处漏传 `level`：

| # | 位置 | 问题 |
|---|---|---|
| 1 | `backend/app.py` `/api/auth/login` 返回体 | **漏 `level`** 字段 |
| 2 | `js/auth.js:203` 登录成功 `setAuth` | **漏存 level**（只有 role/approved）|
| 3 | `js/auth.js:500` 刷新时走 `/api/auth/me` 的 `setAuth` | **漏存 level** |

⇒ 芯片只能按 `approved ? 1 : 0` 兜底 → **恒定蓝色**；而管理面板取后端 `u.level`（后端本身有）
→ **金色**。这就是"面板金色、顶栏蓝色"不一致的根因。

**修复**
- `backend/app.py`：login 返回体补 `"level": u.get("level", 0)`
- `js/auth.js:203`：登录时存 `level`（带 `typeof === 'number'` 校验 + 兜底推导）
- `js/auth.js:500`：`/me` 分支同样存 `level`
- `js/auth.js?v=18 → 19`

**实测**（云端真机，20/21 PASS；唯一 FAIL 为临时探针未清理，已删）
- 建临时账号 → 授予 L5 → 登录返回 `level=5`
- **顶栏圆点 `rgb(234, 179, 8)` 金色**、标题「账号 · 金色」✅
- 同一账号降为 L1 → **顶栏圆点 `rgb(59, 130, 246)` 蓝色**、标题「账号 · 蓝色」✅
  （证明**权限变更即时反映**，不是一次性快照）

---

## [2026-09-22] fix(storage): 共享库附件跨版本同步 —— 存量回填 + 架构文档纠错

**用户反馈**：「我检查云端数据库的 meeting 的 ppt 文件，我使用**本地版本**的应用上传的，
在本地版本能打开，但是在**云端版本**不能打开。」

**诊断（实测）**
- 云端条目「2026年meeting」（owner=xhq1，14 个 pptx 占位符）**存在**（`branch_id=__db__`）
- 但 14 个 `{{file:...}}` 里的 `file_id` **全是本机 id**（`c9973c0c…` 等，正是本地
  `workspace/users/xhq1/uploads/` 的目录名），**不是云端 id**
- 服务器 `workspace/pool/uploads/` 里**没有**这 14 个目录 → `/api/file/<id>` 全 404
- 结论：**附件文件本体从未上传到服务器**，只有条目正文复制过去了

**根因**
- 现有代码逻辑**是对的**：`shared_hub.publish_note_to_db()` 会逐个上传附件到云端并替换 `file_id`；
  `app.py::api_file_upload(source=database)` 在本地实例会代理到云端
- 实测（起本地实例 + 走 `source=database` 上传）**同步正常** → 说明**当时的本地实例跑的是旧代码**
  （Python 不热加载，用户未重启 `start.bat`，与之前"本地下载共享库文件失败"同源）

**处置**
1. **新增 `tools/backfill_shared_files.py`** —— 存量回填工具
   - 扫云端全部 `branch_id='__db__'` 条目 → 收集 `{{file:id:名}}` 引用
   - 找出云端 `pool/uploads/` 缺失的 id → 在本机 `workspace/users/*/uploads/<id>/` 找原件
   - 上传到云端（`source=database&no_entry=1`，不另立条目）→ 用新 id **改写云端条目正文**
   - 支持 `--apply`；不加则 dry-run 只报告
2. **执行回填**：14 个 pptx 全部上传成功（0.41 MB ~ 8.42 MB），云端条目改写 HTTP 200
3. **验证**：云端 14/14 可 `raw` 下载；本地实例代理读与云端**逐字节一致**

**文档纠错（重要）**
- `HANDOFF.md` 原写「数据库唯一存在地（云端）」「数据库与账号只有云端一份」——**错**
- 改为用户确认的架构：**个人数据库**存「当前前端对应的后端所在电脑」；
  **共享数据库**统一存服务器；**存在哪取决于用哪个版本的软件操作**
- 补「下载/打开」链路说明 + 历史坑排查口诀

**实测**（下载/打开链路，11/11 PASS）
- ① 云端直读 `/raw` `/ {id}` `/text` ✅
- ② 本地实例代理读，与云端逐字节一致 ✅
- ③ 个人库附件本地直读（不走云端）✅
- ④ 未知 id → 404（不假装成功）✅

---

## [2026-09-22] fix(upload): 笔记编辑器附件支持多选（真正根因：动态 input 漏改）

**用户反馈**：「笔记和共享数据库的附件上传与编辑时候进行附件上传，打开管理器选择界面时候无法选择多个文件一起上传」

**根因**（上次修复不彻底）：上次只给 `index.html` 里 3 个**静态** input 加了 `multiple`，
漏了笔记编辑器「插入文件」**运行时动态创建**的 input：

```js
// js/messages.js  insertFile()   —— 修复前
inp.type = 'file';                                   // ❌ 无 multiple → 系统选择器只能选 1 个
inp.onchange = () => { const f = inp.files[0]; … }   // ❌ 且只取第一个
```

**修复**
- `js/messages.js`：`inp.multiple = true`；改为遍历全部文件，用 `files.reduce` **串行上传**
  （保证插入顺序与选择顺序一致）
- `index.html`：`js/messages.js?v=5`

**排查确认（全仓扫描）**
- 三个静态 input（`db-file-input` / `bubble-file-input` / `file-attachment-input`）均已有 `multiple`
- 唯一剩余的 `files[0]` 在 `js/image_tools.js:45` —— 单图编辑工具的 `chooseFile`，
  `accept='image/*'` 且回调处理单个 blob，**语义上就该单选**，保持不变

**实测**（真机 + 云端，18/19 PASS；唯一 FAIL 是临时探针未清理，已删）
- 笔记编辑器塞 3 文件 → **3 次上传** `['n1.pdf','n2.pdf','n3.pdf']`
- 数据库塞 2 文件 → **2 次上传** `['d1.pdf','d2.pdf']`

**新增 canonical 不变量**（`scripts/check.sh`，防回归）
- `messages.js` 含 `inp.multiple = true`
- 全仓无遗漏的 `files[0]`（`image_tools` 例外）

**教训**：`multiple` 要查**动态创建的 input**，不能只改 HTML 静态标签。

---

## [2026-09-22] fix(mobile+xiaoe): 隐藏侧栏箭头 / 修复手机端小e 拖动 / 降低惊喜概率

**为什么**：红米 K50 实测反馈三个问题。

**① 手机端隐藏「展开/收缩侧栏」箭头**
- 手机改用汉堡抽屉后，`.sidebar-toggle-btn` 会与抽屉行为打架（bug）
- `css/mobile.css`：≤768px 时 `.sidebar-toggle-btn, #sidebar-toggle-btn { display: none }`
- 桌面端保持不变（实测仍为 `flex`）

**② 修复手机端小e 拖不动（真因：缺 `touch-action: none`）**
- 现象：手指按住小e 滑动 → 被浏览器识别为**页面滚动** → 触发 `pointercancel`
  → `pointermove` 收不到 → 小e 纹丝不动
- 三处修复：
  1. `css/xiaoe.css`：`#xiaoe-root` 及其 `.xiaoe-stage`/`svg`/`.xiaoe-body` 加
     `touch-action: none` + `-webkit-touch-callout: none`
  2. `js/xiaoe.js`：`pointerdown` 里 `if (e.button !== 0) return` → 改为
     `if (typeof e.button === 'number' && e.button > 0) return`
     （触摸事件的 `button` 可能是 `-1`/`undefined`，原写法直接 return 导致拖不动）；
     `setPointerCapture` 包 try；`cancelable` 时 `preventDefault()`
  3. `js/xiaoe.js`：新增 `pointercancel` 兜底，手势被打断时安全收尾（不残留拖拽态）

**③ 降低惊喜概率 + 去掉屏幕备注**
- `WEATHER_CHANCE`：`0.05` → **`0.025`**（降低一半）
- toast 文案：去掉「（随机惊喜✨）」，只留天气本身提示

**实测**（红米 K50 393×852，真实触摸事件序列）：**22/22 PASS**
- 箭头 `display=none`；桌面端仍 `flex`
- 小e 触摸拖动 `(305,690) → (305,530)` 成功，`style.left` 落定、无拖拽残留
- 界面与 toast 均无「随机惊喜」；源码概率 = `0.025`

---

## [2026-09-22] fix(mobile): 手机端返工 —— 底部空白/输入区/顶栏（针对红米 K50）

**为什么**：用户红米 K50 实测反馈 4 点（详见当次提交信息）。

**① 底部空白真凶** = 中间层 `.chat-container` 的 `flex: 0 1 auto` 只占 600px
→ 改 `flex: 1 1 auto` 撑满；并移除 `.chat-section` 上撑破父容器的
`min-height: calc(100dvh - Npx)`。实测输入区贴底 gap=1px（原 193px 空白）

**② 输入区**：模型选择 `127px → 66px`；上下文长度显示隐藏；输入框 `8px → 122px`；
附件/配置/设置/发送统一 30px，输入条 `flex-wrap: nowrap` 单行同排

**③④ 顶栏**：`52px → 46px`；`logo-area` 整体隐藏（用户要求）；主题圆点保留并缩到
12px 定宽 92px；数据库按钮 65px；身份芯片 54px；三段互不重叠
（theme 162-254 / db 260-325 / login 331-385）

**js/auth.js**：手机端不自动弹登录框后，401 分支改为**渲染「🔑 登录」芯片**
（否则顶栏没有任何登录入口）；用户主动点击仍可打开登录框

---

## [2026-09-22] feat(mobile): 手机端适配（抽屉式侧栏）+ 注册默认 L1 蓝色

**为什么**：主界面按桌面设计，手机上是一条超长竖向流（对话区在 y=2385，要滚 3 屏才到聊天），顶栏还挤成两行。

**形态**：类 DeepSeek —— 对话优先 + 侧栏抽屉（汉堡按钮滑出）。桌面端完全不受影响（只加 `@media (max-width: 768px)`）。

**改动明细**
- `css/mobile.css`（新增，仅 ≤768px 生效）
  - 顶栏精简为单行 52px：☰ + logo + 数据库 + 身份芯片；主题圆点隐藏；去 logo 椭圆边框
  - 隐藏欢迎区（`.hero`）与模型配置区（`.models-section`），对话区占满视口
  - 侧栏 `position:fixed` + `translateX(-102%)` 藏起，`body.mobile-drawer-open` 时滑入 + 遮罩
  - 笔记/数据库面板底部全屏铺开；弹窗限制在 96vw；小e 缩放至 74px 移到右下
  - iOS 防缩放（输入框字号 ≥16px）、代码块/表格横向滚动、触摸区域加高
- `js/mobile.js`（新增）— 汉堡开合、点遮罩/消息区/Esc 关闭、开抽屉锁滚动、旋转屏自动收起、切回桌面宽度自动复原
- `js/auth.js` — **手机端不自动弹登录框**（401 时跳过；桌面端保留）。加 `window.__userAskedLogin` 标记区分「用户主动点登录」与「自动弹」
- `backend/role_policy.py` / `backend/auth.py` — 注册默认等级改为 **L1 蓝色**（注册即可用自己数据库）；等级集中在 `default_level_for_new_user()`

**排查中定位的真实问题**（记下来避免重犯）
1. `.logo-area` 有 `border:1px solid` + `border-radius:50px` 胶囊边框且 `flex:1 1 auto` 撑到 188px，与 `.db-analysis-btn`（x=188）**重叠** → 手机端去边框 + 改 `flex:0 1 auto` 按内容收缩
2. 欢迎区真实类名是 **`.hero`**（不是 `.hero-section`）、模型区是 **`.models-section`**（不是 `.models-content-wrapper`）—— 首次写错导致对话区仍在 y=1850 → 补正确类名 + 加「`main.container` 下除 `.chat-section` 外全隐藏」兜底

---

## [2026-09-22] feat(auth): 六级身份体系（颜色代表身份）

**为什么**：原权限只有 4 类（游客/待授权/已授权/管理员），无法区分"能查所有人数据库"与"能改所有人数据库"，也无法给不同人不同 token 额度。

**新等级表**

| 等级 | 颜色 | 上传/查询 | 修改/删除 | 本地千问 token | 身份管理 |
|---|---|---|---|---|---|
| L0 游客 | 灰 `#9aa9be` | ❌ 不可用云端数据库 | ❌ | 限 5M/日 | ❌ |
| L1 | 🔵 蓝 `#3b82f6` | 自己 | 自己 | 限 5M/日 | ❌ |
| L2 | 🟣 紫 `#a855f7` | 自己 | 自己 | 限 5M/日 | ❌ |
| L3 | 🟠 橙 `#f59e0b` | **所有人** | 自己 | 限 5M/日 | ❌ |
| L4 | 🔴 红 `#ef4444` | 所有人 | 自己 | **不限** | ❌ |
| L5 | 🟡 金 `#eab308` | 所有人 | 自己 | 不限 | ✅ 可调 L0–L4 |
| admin 主管理员 | — | 所有人 | **所有人** | 不限 | ✅ 可授任何等级 |

**改动明细**
- `backend/role_policy.py` — **重写**为 level 0-5 模型；保留旧函数签名（`classify`/`filter_tools`/`enforce_mode`/`user_sandbox` 等）以免其它模块改动；新增 `can_use_database` / `can_query_all` / `can_modify_all` / `can_manage_levels` / `can_set_level`
- `backend/auth.py` — 用户结构加 `level` 字段；新增 `set_level()`；存量迁移规则：`role=admin→L5`、`approved→L1`、其余 `→L0`
- `backend/app.py` — 新增 `POST /api/auth/users/level`、`GET /api/auth/levels`；`_require_admin` 放开金色(L5)；新增 `_require_super`；`/api/auth/me` 与 `/api/auth/users` 返回 `level` / `my_level`；hub 会话持久化 level
- `backend/tools/shared_hub.py` — `db_notes` 的 `list` 在 L3+ 时用 `list_all_users_notes()` 返回所有人的条目
- `js/auth.js` — 顶栏芯片加等级彩色圆点；用户管理面板改为「彩色头像 + 等级选择器 + 六色等级名」；新建用户下拉改为 0–5；金色用户可见「用户管理」入口；移除旧「开通数据库授权」按钮

**注意**：`xhq1`/`xhq2` 原 `role=admin` 迁移为 `level=5`（金色），但**没有**"改所有人数据库"特权——该特权只属 `admin` 账号名。

---

## [2026-09-22] fix(upload): 笔记/数据库附件支持多选上传

**为什么**：用户反馈笔记附件、数据库上传都只能选一个文件（对话附件本来就能多选）。

**改动明细**
- `index.html` — `db-file-input`、`bubble-file-input` 补 `multiple`（`file-attachment-input` 原有）
- `js/bubbles.js` — 笔记附件改为遍历 `files` 逐个建气泡，循环外统一 `saveBranches()` + 刷新
- `js/database.js` — 数据库上传改为遍历 `files`，逐项错误处理 + 多选汇总提示

---

## [2026-09-22] chore(security): 移除硬编码密码 + 排除大文件

**为什么**：`deploy_cloud.py` 硬编码了服务器 SSH 密码作为兜底；`model_cache/`（91MB）不应入库。

**改动明细**
- `deploy_cloud.py` — 密码改由 `C4EAI_SSH_PASSWORD` 环境变量提供，缺失即拒跑（原硬编码已删除）
- `.gitignore` — 排除 `model_cache/`、`_syn/`、`*_log.txt`

**⚠️ 部署脚本用法变更**：现在必须先 `export C4EAI_SSH_PASSWORD='...'` 才能运行 `deploy_cloud.py`。

---

## [2026-09-22] chore(git): 建立提交基线

**为什么**：仓库只有 1 个"初始提交"，之后几十项改动全未提交，无法追溯、多 agent 协作易互相覆盖。

**做法**：把 56 个未提交文件按功能分成 8 个语义化提交（auth / shared-hub / backend / xiaoe / frontend / ui fix / assets+skills / tools+docs）。

---

## [2026-09-21] feat(xiaoe): 桌宠小e

**为什么**：用户想要一个可爱的云朵团桌宠，能互动、能当导览员介绍系统功能。

**改动明细**
- `js/xiaoe.js` + `css/xiaoe.css`（新增）— 云朵团外形（SVG 矢量）；10 组 Q弹动画（戳/敲打/发飙/起飞/落地/睡眠/惊醒/悬浮/颤抖/待机呼吸）；7 类天气（出太阳/下雨/刮风/雷阵雨/红温/寒冷/变色）；互动时 **5% 概率随机惊喜天气**；单击开对话，气泡在小e 右侧避开头顶特效；无 ✕ 关闭键，显隐只由设置开关控制
- `backend/app.py` — 新增 `POST /api/xiaoe/ask`：单次 LLM 调用、不带工具、**不落任何存储**（关闭即忘）；注入系统技能清单 + 用户当前对话最近 8 轮
- `index.html` — 设置面板加「小e 桌宠」开关（默认开，localStorage 记忆）

**坑（已修）**：思考型模型（Qwen3）的 `reasoning_content` 会先吃掉 token 额度，`max_tokens` 太小会导致 `content` 为空被误判"答不出" → 改为 2048 + `chat_template_kwargs:{enable_thinking:false}` 并兜底读 `reasoning_content`。

---

## [2026-09-21] fix(ui): 登录弹窗 / 用户管理浮窗 / 主题 / 顶栏 视觉与交互修复

- **用户管理浮窗"点一下就消失"**：浮窗内残留全屏 `data-auth-blank` 层（`inset:0` + `pointer-events:auto` 且绑定 click→`ov.remove()`），点击卡片任何位置都命中它 → 删除该层，关闭途径统一为 ✕ / Esc
- **登录弹窗**：✕ 移入标题行（不与注册页签重叠）；取消点空白/遮罩关闭；自动弹出的登录框补 `closable=true`；关掉登录框后顶栏保留身份入口（否则页面上再无登录入口）
- **主题按钮**：圆点 14px + 单层细环；移除 `.theme-dot[title]::after` 黑色 tooltip（"点后上方黑块"真因）
- **favicon**：原图白底烤死（不跟随浏览器主题）→ 用四角泛洪填充抠背景，改用**透明 SVG 优先** + PNG/ICO 回退

---

## [2026-09-21] feat(shared-hub): 共享知识库（数据库）

- `backend/tools/shared_hub.py`（新增）— 云端共享库条目 CRUD、`publish_note_to_db` 复制语义、附件上传、hub 代理
- `backend/tools/vector_search.py` — 向量检索 + 正文入库
- `backend/agent/tools.py` — 工具注册表对齐（`shared_search` / `shared_upload`）

**关键修复（两个真实 bug）**
1. **数据库上传的条目正文只有占位符**，LLM 读不到内容 → 改为「占位符（原件入口）+ 抽取正文（可召回）」
2. **`list` 全量返回正文**，一篇 10 万字笔记会撑爆上下文 → 改为**目录形态**（`id`/`title`/`owner`/`content_len`/`preview`，不含正文）；`read` 单次封顶 20,000 字并标注被截断

---

## [2026-09-21] fix(backend): 本地实例打开共享库附件报「文件不存在」

**为什么**：`/api/file/{id}/raw`、`/api/file/{id}`、`/api/file/text` 三个**读取**接口只查本地磁盘，没有像 list/增删改那样代理转发到云端 → 本地页面打开云端附件永远 404。

**改动**：三接口在本地实例（`not _AUTH_ENABLED`）本地找不到时，用已存的 hub 会话代理转发到云端；云端 401/403 翻成「请登录」，其他失败返回 502 带原因。

---

## [2026-09-21] feat(auth): 账号体系与角色权限策略

- `backend/auth.py`（新增）— 注册/登录/会话**持久化**（`workspace/_auth/sessions.json`）、密码哈希、admin 初始化
- `js/auth.js`（新增）— 登录/注册弹窗、身份芯片、用户管理浮窗

**关键修复**：会话原为内存 token，服务重启即失效（症状：删除数据库条目 401 失败）→ 落盘持久化。
