# 更新日志（CHANGELOG）

> 本文件记录**每次改动做了什么、为什么、影响哪些文件**。
> 接手项目前先读本文件 + [`HANDOFF.md`](HANDOFF.md)（当前状态快照）。
> 格式：`[日期] 类型(范围): 摘要` + 改动明细 + 影响文件。

---

## [2026-09-26] feat(outputs): 产出区 —— agent/技能生成的文件与笔记附件同款

> 用户原话：「和我笔记里的一样，就一个占位符，链接后端真实文件位置，后端打开就自动打开文件，
> 前端打开就从后端进行下载和临时打开在浏览器（pdf等浏览器支持的文件）」

### 原来的毛病
agent 生成文件后报告「已生成 xxx.pptx」并给一个后端链接，**点开没反应**。
根因：三种打开方式全部以 `fileId` 为唯一钥匙（`openFileById(fileId, name)` → 后端打开 /
浏览器新标签 / 应用内浮窗），而 agent 产出的文件**没走上传流程** → 没有 `meta.json`/`file_id`
→ `_resolve_upload()` 解析不到 → 三条路全断。**不是打开方式的问题，是那个文件没有身份。**
另：`run_python` 跑在 `TemporaryDirectory()` 里，脚本写的文件**函数一返回就蒸发**
（所以 agent 过去只能把结果「贴在回答里」）；`terminal` 的 cwd 是**项目根**（云端还是所有用户共享的）。

### 设计（用户拍板）
- **一个占位符** `{{file:<file_id>:<文件名>}}` —— 与笔记附件完全同款，**前端打开逻辑零改动**
- **后端只有一份文件**：登记只写一条**相对指针**（`meta.rel`），**不复制**
  （附件的 `meta.json` 里存 `rel`，文件留在产出区原地不动）
- **产出源无关**：不挂钩子、只认目录 —— 以后装的 PPT 等技能写进产出区就会被收录

### 改动
| 文件 | 改动 |
|---|---|
| `backend/tools/file_tools.py` | `set_current_user()`；`_user_outputs_dir()`；`register_output()`（只写指针）；`list_outputs()`（未登记自动补登记）；**`_resolve_upload()` 支持 `rel` 指针** —— 它是 download/raw/text/open/open-location 五个端点的**唯一咽喉**，一处改动全部自动支持；`write_file('outputs/xxx')` 自动登记并返回 `file_id` |
| `backend/tools/code_sandbox.py` | `run_python` 的 cwd 从临时目录 → **产出区**（产出不再蒸发）；修正「禁止写文件到外部」那句假注释（`PYTHONSAFEPATH` 只管 `sys.path`，管不住 `open()`） |
| `backend/tools/terminal_tool.py` | 默认 cwd 从**项目根** → 产出区（要操作项目请显式传 `workdir`） |
| `backend/agent/tools.py` | 新增 `list_outputs` 工具（无参：owner 由登录身份推导，**不接受模型传身份**） |
| `backend/agent/agent.py` | 提示词加产出规则（产出写 `outputs/`、写完必须调 `list_outputs` 拿 file_id、**必须给占位符**）；`set_current_user` 转发给 file_tools（复用其原有函数，不加重复调用） |
| `backend/app.py` | `/api/outputs/list` 端点；`_tool_gate` 里设置当前身份 |
| `js/formatting.js` | `formatMessage(content, isFinal, ctx='chat')`：**聊天回答里的卡片按「设置」派发**（`openFileById`）；笔记/数据库（`ctx='note'`，3 处调用点）仍走 `openDbFile` |
| 前端其余 | **零改动**（占位符渲染 `formatting.js:114`、`openFileById` 三种打开方式全部复用） |

### 验证
**单元 20/20**：★登记目录里只有 `meta.json`（不复制→后端一份）/ `_resolve_upload` 解析到产出区原文件 /
★既有上传附件（无 `rel`）行为不变（回归）/ 越界（`../`、绝对路径、不存在）一律拒绝 /
直接写进产出区的文件（技能/脚本产出）自动补登记 / `list_outputs` 幂等不产生重复 id。
**云端真机 9/9**：产出 → 拿 file_id → 产出清单可见 → `/api/file/<id>/raw` 下到正确内容（前端打开链路）→
后端只有一份（登记目录仅 `meta.json`、`meta.rel` 指向产出区）。`check_deploy` 全部一致。

### ⚠️ 两个只有实测才会暴露的 bug（都已修，见下一提交）
1. **`file_id` 格式错 → 占位符根本不渲染**：前端正则是 `\{\{file:([0-9a-fA-F]+):`，**只认纯十六进制**
   （`upload_file` 用的就是 `uuid4().hex`）；我最初写成 `"file-" + hex[:16]` → 正则不匹配 →
   用户看到的是**字面文本**、根本点不动。→ 改为 `uuid.uuid4().hex`，并加不变量防回归。
2. **打开方式没区分上下文**：同一个 `formatMessage` 服务聊天与笔记两条路径，卡片都调 `openDbFile`
   （与设置无关）→ 聊天里也忽略了「设置」。→ 增加渲染上下文参数，聊天走 `openFileById`（按设置），
   笔记/数据库保持原样。（云端 `backend` 模式会优雅降级提示「当前部署不支持本机打开」，不会静默失败）

> **需知**：指针 = **活链接** —— 重跑覆盖同名文件后，旧占位符也指向新内容。这是「后端只有一份」的代价。

---

## [2026-09-26] chore(cleanup): 删掉 24 个确认无引用的死函数 + 4 个测试脚本

用 **graphify**（代码→知识图谱 skill，`uv tool install graphifyy` + `graphify install --platform hermes`）
对项目做了一次结构体检：66 个代码文件 → 1243 节点 / 2660 边 / 65 社区。

### 判据（重要：不能用图上的"入度 0"直接当死代码）
图是 **无向导出**（`directed: false`），且**没有建模 HTTP 层** —— 关系只有
`calls/contains/references/imports/inherits/rationale_for/indirect_call/uses`，
**没有** `fetches`/`routes_to` 之类。所以：
- 图上"入度 0"的候选有 **263 个**，其中 **59 个是 FastAPI 路由**（靠 `@app.get` 装饰器注册）、
  **18 个是 Pydantic 参数模型**、其余 162 个在别处（HTML `onclick`、`window.*` 导出、跨文件调用）被引用。
- 最终判据 = **全仓 .py/.js/.html/.sh/.bat 文本检索 + 「带括号的真实调用」双重确认，该名字只有定义本身**。
  这个判据与边方向无关，因此可复核。

### 删掉的（24 个函数）
| 分组 | 位置与函数 |
|---|---|
| 旧前端流式通道 | `messages.js`: `createStreamingMessage` `updateStreamingContent` `completeStreamingMessage` `updateMessageElement`；`graph_rag.js`: `parseToolCall` `extractFinalAnswer` |
| 旧前端向量化 | `database.js`: `_computeEmbedding` `_cosineSimilarity` `_chunkText`；`settings.js`: `_dbStoreFile` |
| 后端权限辅助 | `role_policy.py`: `level_name` `level_color` `can_use_database` `can_modify_all` |
| 后端文件辅助 | `file_tools.py`: `_pool_index_path` `_get_upload_meta` `get_upload_file` |
| 历史截断 | `agent.py`: `_truncate_history`（与「内容不截断、控长只保留最近 N 轮」的设计一致） |
| 空壳/遗留 | `provider.js`: `saveApiKeys` `loadApiKeys`（源码自述 *Legacy … ignored*）；`database.js`: `dbToggleCollapse`（空函数体）；`editing.js`: `extractReasoningFromContent`；`tools.js`: `showTyping`；`state.js`: `loadHistoryMode` `toggleHistoryMode` |

**删掉的测试脚本**：`backend/_test_astream.py`、`backend/_test_stream.py`、`backend/test_agent.py`、`test_sse.py`
**复核后保留**：`provider.js:updateModelStatus` —— `js/ui.js` 里有真实调用。

`index.html` 相应 bump 8 个 js 的缓存版本号。

### 验证
py_compile 33 个 ✅ / pyflakes `undefined name` **0 条** ✅ / node --check 31 个 ✅ /
`check.sh` ALL_PASS ✅ / 被删名字**全仓零残留** ✅ /
逐文件比对「删除前后函数定义清单」确认**无连带误删** ✅ / `check_deploy` 45 文件一致 ✅

### ⚠️ 过程中的一次失误（如实记录）
第一版 JS 删除脚本用「括号配对」定界，在 `graph_rag.js` 上状态机跑飞
（文件里有撇号/正则字面量，导致引号状态误入），**一次误删 514 行**（含核心的 `reactAgentLoop`）。
→ 已从基线提交 `8d291a0` 恢复，改用「**行首 `}` 定界**」重做。
**教训：删除脚本必须 (a) 动手前先存档打 tag，(b) 删完逐文件比对函数定义清单，
(c) 不要用自己写的括号状态机去解析 JS —— 短函数用行首 `}` 定界更稳。**

存档：tag `pre-deadcode-cleanup` 可回退到删除前状态。

## [2026-09-26] perf(hub) + feat(sync): 共享库面板瘦身 195× + 多设备对话实时互见（SSE）

> 用户两条反馈：
> ①「我用你做的共享库的时候感觉比较卡顿，就是每次都要加载信息」
> ②「我收回限制登录的做法，两边应该允许同时登录，a 进行的对话会同步到 b 设备的前端」

### 一、共享库卡顿 —— 实测找到真因（不是缓存，也不是缺推送）

`GET /api/notes?branch_id=__db__` 一次 **435.6 KB**，其中 **413 KB（95%）是条目正文**，
而列表只需要标题（65 条，标题中位数 17 字）。另外服务器**没开 gzip**，
面板 + 侧栏树至少两处各拉一份（≈871 KB/次），年份菜单还重复整份拉了一次。

**改法（用户定：①列表瘦身 + ②gzip + ④只缓存目录 → 再上 ③SSE）**

| | 做法 | 实测 |
|---|---|---|
| ① | `with_content=0` 只返回目录（+`chars`）；新增 `GET /api/notes/<id>` 按需取正文 | 435.6 → **15.0 KB（29×）** |
| ② | FastAPI `GZipMiddleware`（**不用 nginx** —— 那边特意 `gzip off` 是为了 SSE） | 435.6 → 148.4 KB（2.94×） |
| **①+②** | **真实打开场景** | 435.6 KB → **2.2 KB（195×）** |
| ④ | 前端只缓存目录 `_dbEntries`；**展开某条/点编辑时**才取那一条（取到写回缓存，只取一次） | 单条 2.4 KB；折叠再展开命中缓存 |

**关键发现**：条目正文原来是在**渲染时给每条都渲染进 DOM**（哪怕折叠着），
所以列表才被迫带正文；而正文其实只在**展开/编辑某一条**时才需要 —— 这正是"瘦身+按需取"的落点。
另：面板是**树**，根节点默认折叠 → 条目卡片根本不在 DOM 里（测试时必须先展开树，我在这踩过坑）。

### 二、多设备互见 —— 并发模型反转（几经反复后的最终版）

用户先提「限制单账号单会话」，随后**收回**：「两边应该允许同时登录，A 的对话要同步到 B」。
→ 允许**多写者** ⇒ 写入不能是整份覆盖（否则 A、B 互相抹）⇒ **改成按对话 id 合并**。

- 撤销 `auth.login` 里踢旧 token 的代码
- 每个对话带 `updatedAt`（客户端在内容**真的变化**时打戳 —— 用指纹比对，
  不能"调了 saveBranches 就算变"，否则切换对话/重渲染也会让没变的那条"赢过"别的设备）
- 合并：同 id → `updatedAt` 新的赢；不同 id → 并集；`branchOrder` 并集去重
- **`currentBranchId` 不再同步** —— "上次打开哪个"是每台设备各自的视图状态
- ★**删除必须留墓碑**（`chats.json` 的 `deleted` 表）：A 删掉的对话，B 那份旧副本
  一推就复活 —— 两台同时在线时**必然发生**。墓碑按时间戳仲裁，只影响被删的那个 id。
  墓碑上限 500，超了丢最旧的。

### 三、SSE 推送（用户不要轮询：「不对话的时候也不会持续轮询」）

- 新增 `GET /api/events`（`_subscribers`: username → {asyncio.Queue}，25s 心跳）
- 变更时推送：对话 → 只推该账号；条目 → 共享库推所有人 / 个人笔记推本人
- 新增 `GET /api/user/chats/version` —— ⚠️ **断开期间的事件必然漏掉**，
  所以每次(重)连成功后先对齐一次，靠它判断要不要拉全量
- ⚠️ **前端不能用 `EventSource`**：它**不能设置请求头**，而本项目鉴权是
  `Authorization: Bearer`。改用 `fetch` + `body.getReader()` 手工解析 SSE
  （与 `js/graph_rag.js` 读 `/api/agent/stream` 同一套写法）
- ⚠️ **SSE 不能被 gzip 压**（否则流式被憋住）。Starlette 的 GZipMiddleware 对
  `text/event-stream` 自动跳过 —— 已实测确认（带 gzip 请求 SSE 时 `content-encoding` 仍为空）

**影响文件**
- `backend/tools/user_chats.py`（改为按 id 合并 + 墓碑 + `chats_rev`）
- `backend/app.py`（`/api/events`、`/api/user/chats/version`、`_notify`/`_notify_all`、
  `with_content`/`_strip_content`、`GET /api/notes/{id}`、`GZipMiddleware`、条目变更推送）
- `backend/auth.py`（撤销单点登录）
- `backend/tools/file_tools.py`（新增 `get_note`）
- 新增 `js/events.js`（SSE 客户端）；`js/user_chats.js`（合并/墓碑/同步）；
  `js/database.js`（列表瘦身 + 展开才取正文 + 去掉重复的年份拉取）；
  `js/api.js`（init 后 `startEvents()`）；`index.html`（引入 events.js + 版本号）
- `scripts/check.sh`（新增 gzip/SSE/墓碑/按需取不变量；**删除**已作废的单点登录断言；
  另修 `grep -c … || echo 0` 会输出两行的老坑 → 改用 `| head -1` + `${x:-0}`）
- `tools/check_deploy.py`（纳入 events.js、file_tools.py）

**验证（云端真机，一次性账号跑完即删）**
- 合并语义单元 **7/7**：并集互不覆盖 / 同 id 新者赢 / ★**删除不被旧副本复活** / 墓碑不误伤 / 上限生效
- API **12/16 → 失败项全是测试用 L1 账号看不到共享库池**（历史行为），改 admin 后 **14/14**
- ★浏览器两台设备（同账号两个 token）：A 真实按钮建对话 → **B 不刷新就看到**；
  A 发消息 → **B 实时看到正文**；A 删除 → **B 同步掉且不复活**（10/10）
- ★共享库面板：列表 **14.6 KB**（原 435.6 KB）、无 `content` 字段、只请求 1 次；
  展开才取那一条（2.4 KB）并渲染；**折叠再展开命中缓存**
- gzip 原始字节判定：`\x1f\x8b` + 解压后与不压缩版**逐字节一致**，2.94×
- `check.sh ALL_PASS`；`check_deploy 45/45 一致`

> 教训（测试侧，产品无问题）：判断响应头要用**小写键**（FastAPI 返回 `content-encoding`）；
> `subprocess` 里调 bash 必须写全路径（`C:\Windows\system32\bash.exe` 是 WSL 桩）。

---

## [2026-09-26] feat(chats): 对话历史后端化（换浏览器不再丢聊天记录）

> 用户原话：「我换个浏览器数据就没了，这影响使用啊」「对话是最应该后端的」。
> 这是本项目最该后端化、却一直漏掉的一块 —— 设置丢了能重填，聊天记录丢了就没了。

**为什么之前是坏的**
对话（分支 + 全部消息 + 分支笔记 + 排序 + 当前对话）只写浏览器
`localStorage['ai-branches::<身份>']`，后端 40 个端点里没有任何一个跟对话有关。
换浏览器/换设备 → 资料都在，就聊天没了。

**存储**：`workspace/users/<身份>/chats.json`（与 `settings.json`/`notes.json` 同级）
```json
{ "branches": { "branch-1": { ...全部分支对象（含 messages/notes）... } },
  "branchOrder": [...], "currentBranchId": "branch-1", "_updated": "..." }
```

**端点**：`GET/PUT /api/user/chats`
- owner **一律从登录 token 推导**（`_cur_user`），绝不接受请求体传身份 —— 防越权读写他人对话
- 本地实例沿用 `_notes_proxy` 转发云端（与笔记/设置同一套分流）
- 写入后**回读核对**，返回 `verified`；非白名单字段记 `rejected`

**并发模型（用户 2026-09-26 定）**：**单账号单会话** —— `auth.login` 登录即踢掉该账号旧 token。
同一时刻只有一个写者 → 整份覆盖即可，**不做合并、不做冲突处理**。
> 用户否掉了「按对话 id 并集合并」那套（我原本推荐 B 方案）：多设备合并逻辑复杂且易坏，
> 单点登录从根上消除并发写，简单且不可能卡死。

**语义 = 覆盖（不是合并）**：payload 里出现的字段整体替换。
`branches` 是整棵树，所以**「删掉一个对话」也能正确同步过去** —— 合并语义做不到这点
（删掉的对话会被别处的旧副本复活）。

**前端（沿用「后端权威 + localStorage 作本地缓存」，与设置完全同套）**
```
启动：Promise.allSettled([hydrateUserSettings(), hydrateUserChats()])
        .finally(initAfterSettings)      ← loadBranches() 在里面，必须先灌回来
写入：saveBranches() → setUserChats()    ← 节流 1.5s，18 个调用点合并成一次推送
离开：visibilitychange(hidden) / pagehide 强制推一次
```
**现有 18 处读写一行都没改** —— `loadBranches()` 照旧同步读缓存。

**影响文件**
- 新增 `backend/tools/user_chats.py`（load/save/覆盖语义/16MB 上限/落盘核对）
- 新增 `js/user_chats.js`（hydrate / setUserChats / pushUserChats / 离开页面 flush）
- `backend/app.py`：`/api/user/chats` 两端点 + 导入
- `backend/auth.py`：`login` 单账号单会话（踢旧 token）
- `js/api.js`：`Promise.allSettled([...])` 时序（改掉原来的 `hydrateUserSettings().finally(...)`）
- `js/branching.js`：`saveBranches()` / `persistCurrentBranch()` 挂钩推后端
- `index.html`：引入 `js/user_chats.js`；`api.js` v10 / `branching.js` v11
- `scripts/check.sh`：新增 10 条不变量（含**更新**原来那条断言旧 `hydrateUserSettings().finally` 的）
- `tools/check_deploy.py`：纳入 `js/user_chats.js`、`backend/tools/user_chats.py`、`backend/auth.py`

**验证（云端真机，一次性账号，跑完即删；24/24）**
- API：空读 / 写(verified) / 回读(正文+排序+当前对话) / **覆盖语义（删掉的对话没被复活）**
- 单点登录：第二次登录后**旧 token 立刻 401**
- 浏览器真按钮：点「新建对话」+ 加消息 → **2s 后后端落盘**（含磁盘文件核对，不只是内存）
- ★**换浏览器**：`localStorage.clear()` → 重新登录 → **对话 + 消息正文完整回来，侧栏列出**
- 账号隔离：另一账号读不到

**老数据**：不写迁移工具（用户明说现有数据丢得起）。
但后端为空 + 本地有数据时会**自动当种子推一次**（与设置同一条一次性迁移路径）。

**排查口诀**：换设备没恢复 → 看 `workspace/users/<身份>/chats.json` 在不在、`_updated` 是不是最新；
对话没同步 → 先确认是否已登录（未登录=游客，走本地缓存，PUT 会 401 且被静默吞掉）。

**⚠️ 已知残留（未改，等用户拍板）**
`js/provider.js:266` 保存成功提示仍写「配置已保存 (密钥仅保存在本地)」——
密钥**早已推到后端**，该文案是旧的（同类陈旧注释还有 `js/settings.js:97`）。

---

## [2026-09-26] fix(settings): 修「在 hydrate 前读设置」导致的 3 处时序 bug

> 用户提醒：「因为改了后端，然后发送给 llm 的对话形式、设置里面的对话轮次什么的，
> 可能挺多按钮受影响的，你确保处理好」—— 自查果然抓到 3 处。

**根因**：`hydrateUserSettings()` 是**异步**的（要先 GET 后端），
而下列代码在它完成前就读 localStorage 里的设置 → 新设备/新浏览器上读到空值：

| # | 位置 | 症状 |
|---|---|---|
| 1 | `js/state.js` 的 state 字面量里 `enabledModels: JSON.parse(localStorage...)` | 字面量在**脚本解析时**求值（比 init() 还早）→ 新设备「已启用模型」为空 → 模型列表全灭、勾选态全错 |
| 2 | `js/main.js` 顶层紧跟 `init()` 的 `loadAgentConfigFromLocal(); initAgentConfigPanel();` | init() 现在立即返回 → 新设备上智能体配置（工具勾选/skills/引擎/轮次/超时）全为默认值 |
| 3 | `js/main.js` 顶层 `updateNeo4jStatusUI(state.neo4j.connected)` | `state.neo4j` 由 `loadSettings()` 填（也在 hydrate 后）→ Neo4j 指示灯显示错误状态 |

**修法（统一到一个入口，杜绝再漏）**
- `api.js::initAfterSettings()` 成为**唯一**「读个人设置」的初始化入口：
  先重读 `state.enabledModels` → `initProviderSystem` / `loadSettings` / …
  → 末尾补上从 main.js 移来的 `loadAgentConfigFromLocal` / `initAgentConfigPanel` /
  `updateNeo4jStatusUI`
- `state.js`：`enabledModels` 改为 `[]` 占位（注释说明为何不在此读）
- `main.js`：删掉那 3 行（保留注释说明为何移走）
- `scripts/check.sh`：新增 **5 条时序不变量**，防止将来又把读设置的代码放回 hydrate 之前

**★ 排查纪律（同类问题通用）**：
凡是「读 localStorage 里的个人设置」的初始化，**必须**放在 `initAfterSettings()` 里 ——
`state.js` 的 state 字面量、任何 js 的顶层语句、`DOMContentLoaded` 处理器都**早于** hydrate。

**验证（云端真机 31/31，独立测试账号，不碰 admin）**
| 组 | 结果 |
|---|---|
| A. 新设备 state | temperature=0.3 · **contextLength=7（对话轮次）** · maxTokens=4000 · **enabledModels=['qwen3-local']** · **engine=langchain/maxRounds=20/skills=['graph-guide']** · **neo4j.user=newdev-user** · currentModel=qwen3-local |
| B. 设置面板 UI | #temperature=0.3 · **#context-length=7** · #max-tokens=4000 · 超时三级=111 |
| B2. 智能体面板按钮 | tool 勾选=['shared_search'] · engine=langchain · **工具循环轮次下拉=20** · 超时三级=111 |
| C. 端到端 | 改对话轮次 7→13 → 保存 → **后端 settings.json 的 contextLength 真为 13** |

另：`check.sh` 的 `chk` 用 `grep -F`（固定字符串），写判据不需要 `\[\]` 转义。

---

## [2026-09-26] feat(settings): 个人设置改为后端存储（localStorage → 个人账号数据）

> 「密钥当初在的设计就是保存在个人浏览器，在后端的话，那就放在个人账号数据上吧，
> 反正也就开发者知道密钥。」——用户 2026-09-26

**目标**：设置换设备不再丢；API Key 不再只躺在浏览器里。

**模型**：后端（个人账号数据）为权威，localStorage 作本地缓存（缓存不是兜底 ——
面板打开要立刻有值不能等网络）。

| | 之前 | 现在 |
|---|---|---|
| 存放 | 浏览器 localStorage | `workspace/users/<身份>/settings.json` |
| 换设备 | ❌ 全丢 | ✅ 自动恢复 |

**改动**
- 新增 `backend/tools/user_settings.py`：`MANAGED_KEYS` 白名单 8 键 /
  `load_user_settings` / `save_user_settings`（合并语义、`null`=删除、**回读核对** `verified`、
  白名单外记 `rejected`）
- `app.py`：新增 `GET/PUT /api/user/settings`；owner 从 `_cur_user` 推导，
  **绝不接受请求体传入身份**（防越权）；本地实例沿用 `_notes_proxy` 转发云端
- 新增 `js/user_settings.js`：`hydrateUserSettings`（后端→缓存）/
  `setUserSetting`（缓存+节流 800ms 推后端）/ `pushUserSettings`；
  `visibilitychange(hidden)`+`pagehide` 强制推最后一笔
- 15 处 `localStorage.setItem(受管键)` → `setUserSetting(...)`
- `api.js`：`init()` 拆出 `initAfterSettings()`，用 `hydrateUserSettings().finally()` 保证
  「先灌缓存 → 再初始化依赖设置的模块」
- `index.html`：引入新模块；相关 js 版本号 +1

**同步白名单 8 键**：ai-settings / ai-current-model / ragagent-provider-settings(★) /
ragagent-custom-providers(★) / ragagent-provider-overrides / ragagent-enabled-models /
ragagent-agent-config / ragagent-neo4j-config(★)
**留本地**：面板位置、侧栏宽度、主题、小e 开关（纯本机偏好）

**迁移**：前端首次遇到「本地有设置 + 后端空」→ 自动推上去（一次性，之后不再触发）

**修了两个自己发现的判据错误**
1. `save_user_settings` 的落盘核对原先把白名单外被拒的键也算进比对 → `verified` 恒为 False
   （数据其实是对的）。判据改为「本次打算写入的键是否都真落盘」。
2. `tools/check_deploy.py` 文件清单漏了 13 个 js + 3 个 css + 2 个后端文件
   （正是用户此前「漏部署」3 次的隐患根源）→ 补齐到 40 个。
   ★ 补齐后**立刻抓出 6 个从未部署的文件**已补部署。

**验证（云端真机端到端 23/23，独立测试账号，不碰 admin）**
| 组 | 结果 |
|---|---|
| HTTP API | PUT ok + verified=True · 白名单外被拒 · API Key/温度/Neo4j 密码往返一致 |
| 磁盘实证 | 云端 settings.json 真落盘且含 API Key |
| **★ 迁移** | 本地有设置 + 后端空 → 自动推上去 |
| **★ 跨设备** | 清空 localStorage 模拟新设备 → 重载 → 设置（含 apiKey）从后端恢复 |
| 清理 | 测试账号/目录无残留；云端无任何 settings.json（不污染真实数据）|

`check_deploy` 40/40 一致 · `check.sh` ALL_PASS

---

## [2026-09-25] refactor(hub): 共享库改为「文件夹树」模型 —— 笔记/文档是两个真实根文件夹

**用户澄清（推翻"类型标签"的旧模型）**
> 「笔记和文档的属性没有任何区别，只是上传到文档分组的时候，会自动解析里面的附件变成文字，
> 移动则不会。然后把上传和添加条目的按钮放到每一个分组的栏目下（不要以前的上传按钮就到文档，
> 添加就到笔记了，现在二者通用）。从非个人数据库和非共享数据库的内容第一次到共享库中，
> 如果进入的是文档分组，里面的附件就会自动解析，笔记分组则不会。所以移动笔记不会触发解析，
> 上传个人笔记到共享库也不会触发解析。分组下面可以有直接的文档笔记也可以有分组，
> 和文件夹管理一样。整个我们的内容其实就相当于一个文件夹进行管理，
> 只不过每个文件夹有名字带了简介。」

> 「注意，从个人数据库（对话笔记）里上传和共享数据库里移动都不会触发解析，
> **解析的唯一点就是，新的条目第一次从非数据库里面进入共享数据库**。」

### 核心模型
```
整个共享库 = 一个文件夹树（文件夹有名字 + 简介；里面可装文件，也可装子文件夹）
  📁 笔记 (parse=False)   ← 真实根文件夹（系统内置，不可删）
  📁 文档 (parse=True)    ← 真实根文件夹（上传到这里 → 自动解析附件成文字）
    └── 📁 用户自建夹（可任意层，混装条目与子夹）
```

**解析的两层判据**
1. 是不是「**新条目第一次从共享库外部进入**」？不是 → 一律不解析
   （从个人库发布进来 / 库内移动 → **都不解析**）
2. 是的话，落在哪个夹？该夹 `parse` 标记决定（子夹未标注则沿父链继承）

### 改动
- `folder_tree.py`：
  · 新增 `ROOT_NOTE`/`ROOT_DOC` 常量 + `ensure_roots()`（幂等建两个系统根夹）
  · 新增 `root_id_for_kind()`、`folder_parses()`（沿父链回溯解析策略）
  · `create_group(parse=)` 支持显式指定；未给则沿父链继承
  · **系统根夹禁止删除**
- `app.py`：
  · `/api/file/upload` 新增 `folder_id` 参数；`parse` 判据改为
    **显式 > 从个人库发布(no_entry=1→否) > 目标夹的 `folder_parses()`**
  · 自动建条目写入目标 `folder_id`（不再硬编码 kind）
  · `/api/groups` 调 `ensure_roots()`；`GroupOpReq.parse`
- `file_tools.upload_file()` 的 `parse` 语义更新为「是否解析成文字并进索引」
- `shared_hub.publish_note_to_db()`：保持 `no_entry=1&parse=0`
  （**个人库发布永不解析**，注释写明这是用户明确要求）
- `js/database.js`：
  · **树 = 真文件夹树**（删掉按 kind 分桶的假根）；空夹也显示
  · 每夹行尾按钮：**📤 上传（存进此夹）/ ＋ 新建条目 / 🗂️ 建子夹 / ✏️ 编辑 / × 删除**
  · 头部全局「📤 上传」「＋ 笔记」**移除**（按钮下沉）
  · `dbAddEntry(folderId)`、`_dbUploadFolder` 记录上传目标
  · **移动菜单列出全部文件夹**（含另一个根下），移动永不触发解析
  · 删掉「还没有分组」提示
- `css/layout.css`：`.db-tsys`（系统夹标记）
- 新增 `tools/migrate_to_folder_tree.py`（dry-run / `--apply`，带落盘核对）

### 数据迁移（已执行，先备份）
`workspace/_backup_20260925_193058/`（327MB：_folders.json + users + uploads）
```
① ensure_roots(): 新建两个系统根夹
② 69 条按 kind 归位：
   admin 4 条 + xhq1 65 条
   落盘核对 → 笔记夹 2 条 / 文档夹 67 条 / 未归属 0 条 ✅
```

### 实测（云端真机）31/31
| 组 | 关键结果 |
|---|---|
| 数据 | 两根夹存在·`parse` 标记正确(False/True)·系统夹·条目归属 2/67·**系统夹不可删** |
| **解析** | **上传「文档」夹 → 有 text.txt + 索引 1 块**；**上传「笔记」夹 → 无 text.txt + 索引 0 块** |
| 分层 | 未解析附件仍可 `read_file` 现读（`on_demand=True`）|
| 界面 | **无「还没有分组」提示**·笔记/文档作为📁出现·系统标记·**全局上传/＋按钮已移除**·每夹 3 个操作按钮·系统夹无删除 |
| 按钮下沉 | 点「文档」夹的 📤 → `_dbUploadFolder = root-doc` |
| 移动 | 菜单列出**全部文件夹** `['📁 1','📁 文档（根）','📁 笔记（根）']` |

**canonical check**：替换 1 条废弃判据（parse 跟随 no_entry → 看目标文件夹），
新增 7 条（ensure_roots / folder_parses / ROOT_DOC / 上传看夹 / parse=False /
_dbUploadFolder / 两个下沉按钮）

---

## [2026-09-25] feat(ui+hub): 树为骨架（类型→分组→条目）+ 条件下推省 token

**用户反馈（推翻上一轮设计）**
> 「你这个分组设计不好，你有看到笔记和文档的按键吧，我点击文档后可以看到全部文档，
> 然后文档的按键，可以展开到下一级别的分组，可以点击添加分组，编辑分组，
> 添加下一个级别的分组，以一种树状图的形式进行分组管理」

→ 上一轮的「📁 分组」**独立面板**与「笔记/文档」筛选是**两个平行入口**，
用户要的是**合一**：类型本身就是树的根节点。

**用户补充的三个关键要求**
1. **编辑按键直接跳出经典编辑框**（与笔记编辑/AI回复编辑同一个 `floatingEditor`）
2. **具体到某个分组的文件列表时，和以前完全一样** —— 按用户分 + 按上传时间排序
3. **筛选排序外置到「上传/添加」旁边**（不藏在树里）
4. ⭐ **智能体 list 时条件下推**：
   > 「我说查26年和用户xhq1的内容，智能体 list 的时候，是列出条件的内容，
   > 而不是 list 全部，然后智能体再去读取分析浪费大量 token」
   > 「因为 list 会展示大量内容，所以查询工具要教会有什么分组、如何按照分组去 list」

---

### A. 前端：树为骨架

```
▼ 📄 文档  67            ← 根节点 = 类型（kind）
  ▼ 📁 研究进展  3
      汇总 2025-2026 …    ← 分组简介
    ▶ 📁 文本挖掘  3
  ▶ 📝 笔记  2            ← 根节点
```

- `index.html`：**删掉**「📁 分组」按钮与 `#db-groups-view` 独立面板；
  改为在「📤 上传」「＋ 笔记」右侧加三个**外置筛选器**：
  `👤 全部 ▾`（上传者）/ `📅 全部 ▾`（年份）/ `🕒 最新 ▾`（排序）
- `js/database.js`：
  · 新增 `_dbTree`（folders / openKind / openGroup / owner / year / sort）
  · 新增 `_renderGroupChildren()` 递归渲染分组树；`_dbEntryCardHtml()`
    抽出条目卡片渲染（**与改造前逐字一致**，保证"组内列表和以前一样"）
  · `refreshDocumentList()` 重写：拉全部条目 + 分组树 → 应用筛选/排序 →
    按 kind 分桶 → 树形渲染。**每个分组右侧有 ＋ / ✏️ / ×**
  · 筛选器：`owner`/`year` 菜单**从实际数据动态生成**（带计数）
  · 删除废弃：`_switchDbKind` / `openGroupsPanel` / `_showGroupsView` / `_dbKind`
- `css/layout.css`：`.db-troot-row` / `.db-tnode-row` / `.db-tcaret` /
  `.db-tchildren` / `.db-tintro` 等树样式

### B. 后端：条件下推（用户最在意的省 token 点）

- `shared_hub._list_entries_local()` 新增参数：
  · `owner`（只看某用户）/ `year`（按 createdAt 只看某年）/ `kind` /
    `sort`（time / time_asc / title）
  · ⭐ `summary_only=True` → **只返回各分组的条目计数**，不返回明细
- `knowledge()` 与 `_entries_via_hub()` 同步扩展并透传
- `agent/tools.py`：`SharedSearchParams` 补齐上述参数；**工具描述重写**，
  写明**工作流**：
  1. `list_groups` 看分组树与简介
  2. `list_entries + summary_only=True` 看"哪个组有货"（不确定在哪组时必做）
  3. 针对具体 `group_id` 拉明细
  4. 需要正文再 `search`
  并给出**具体例子**：「26 年 xhq1 传的 XX 组的内容」→
  `action=list_entries, owner="xhq1", year="2026", group_id="…"`

**实测（云端真机）32/32**
| 组 | 关键结果 |
|---|---|
| **省 token** | **全部明细 10,841 字符 → summary_only 319 字符（省 97%）** |
| 条件过滤 | owner `xhq1=65`（全部 69）· year=2026 · owner+year 组合 · group_id · kind · sort=title |
| summary_only | `mode=summary_only`，**不含 entries 字段** |
| 树 UI | 根节点 2 个（笔记/文档）· 分组渲染 · 简介显示 · 旧面板已移除 |
| 筛选器 | 三个都在上传右侧**同一行** · 上传者菜单 `['全部','admin (4)','xhq1 (65)']` · 年份 `['全部','2026 (69)']` |
| **✏️=经典浮窗** | 打开浮窗 · **预填组名/简介正确** · 改名+简介后**重新拉取核对真落盘** |
| 展开折叠 | 分组可折叠 |
| 排序 | 切「名称」→ `_dbTree.sort='title'` + 按钮文案变 🔤 名称 |

**canonical check 更新**：移除 2 条已废弃判据（旧独立面板），新增 6 条
（树渲染 / 树状态 / 三个筛选器 / summary_only / year 下推）

---

## [2026-09-25] feat(ui): 分组管理面板 —— 手动新建/编辑简介/移动/删除

**用户反馈**：「我无法自己分组呀，你是还没有做分组的控制按键吗？」
→ 属实：上一轮只做了**智能体工具（C）**与**条目右键移动（B）**，
  **分组本身没有任何 UI** —— 手动无法建组、改简介、移动、删除。补齐。

**用户要求**：编辑界面用「经典浮窗」（与笔记编辑、AI 回复编辑同一个
`window.floatingEditor`）。

**改动**
- `index.html`：数据库面板按钮行加 **「📁 分组」**；新增 `#db-groups-view`
  面板（覆盖条目列表区）+ 「← 返回」+「＋ 新建分组」
- `js/database.js` 新增：
  · `openGroupsPanel()` / `refreshGroupsList()` —— 分组列表（缩进显示层级）
  · `_editGroup()` —— **新建/编辑统一走 `window.floatingEditor`**：
    标题行 = 分组名（`showTitle`），正文区 = **分组简介**
  · 每行操作：**＋**（在此组下建子组）/ **✏️**（编辑名字+简介）/ **×**（删除）
  · `_showMoveGroupMenu()` —— **右键分组 → 移动**；候选项**排除自己与自己的后代**
  · `_deleteGroupConfirm()` —— 原生 confirm（因需确认），说明"条目不会被删除，会上移"
  · `openDatabaseModal()` 每次打开**回到列表视图**（避免停在上次的面板造成误解）
- `css/layout.css`：`.db-group-*` 样式（hover 才显示操作按钮；简介灰色小字）

**交互对照（用户可自查）**
| 操作 | 入口 |
|---|---|
| 新建根级分组 | 面板右上「＋ 新建分组」 |
| 新建子分组 | 某分组行的 **＋** |
| 编辑名字+简介 | 某分组行的 **✏️** → 经典浮窗 |
| 移动分组 | **右键分组** → 「移动到…」（或「移动到根级」）|
| 删除分组 | 某分组行的 **×**（确认框）|

**实测（云端真机，Edge CDP）23/23**
- 面板打开、看到分组与简介
- **新建走浮窗**（标题=新建分组，标题行可见）→ 列表出现、简介正确
- **编辑浮窗预填正确**（名字/简介都对）→ 改后列表更新
- **右键移动** → 菜单含「移动到根级」与目标组 → **★ 重新拉取核对 parent_id 真落盘**
- **删除** → **★ 重新拉取核对真删除**，UI 同步刷新
- 「← 返回」回到条目列表

**测试踩的坑（记录，非产品问题）**
- 本页有 **37 个 script**，`database.js` 靠后，实测约 **10-12s** 才挂上函数；
  测试在 t≈4s 检查 → 误报 "openGroupsPanel undefined"。
  修法：**轮询等待 `typeof openGroupsPanel === 'function'`** 再断言。

---

## [2026-09-25] feat(hub): 共享库分组树 + 知识库工具统一入口

**用户设计**（2026-09-25）
- 顶层分「笔记组 / 文档组」→ **用现有 `kind` 区分**（不建真文件夹，少一层嵌套）
- 下层是**用户自建任意深度分组树**，每个分组有**【简介】(intro)，手写**
- 简介用途：**供人阅读 + 供 LLM 推理导航**（先看目录判断该去哪找，再深入）
- 移动方式：**B（前端右键「移动到…」）** + **C（对话让智能体移动）**
- **工具要统一**：「知识库工具统一，不要多写太多新工具」

**用户的关键判断**（记下来，影响后续设计）
> 「作为智能体，含有数据库工具，这些功能就是必须的，数据库工具肯定了解自己。
> 甚至我没有数据库工具，智能体也能帮我操作这些功能（只要能阅读自身的代码就能知道怎么动手）。
> 做成工具就是快些，省了智能体读代码思考的时间。」
→ 即：**工具是"快路径"，不是"唯一路径"**。不必为每个功能都造工具；
   该造的是**高频、参数易说清**的操作。

**借鉴 WeKnora（`wiki_folders`）的四个设计**
1. **ParentID 邻接表 = 唯一真相**；`Path`/`Depth` 是**缓存**，每次写入重算
   （注释原文：Path "kept purely for cheap display/sort"）
2. **空分组可存在** —— 允许先搭骨架再往里放条目
3. **深度上限是业务规则**（MAX_DEPTH=5），不是防呆兜底
4. **防循环移动**：不能把分组移到自己或自己的后代下

**改动**
- 新增 `backend/tools/folder_tree.py`（分组树：`list/create/update/move/delete_group`）
  · `_recompute_paths()` 写入时重算物化路径
  · `_tree_order()` **深度优先排序**（初版按 path 字符串排会让根级混进子级中间）
  · `delete_group(mode)`：`move_up`（子与条目上移，默认）/ `cascade`（连子删）
  · **删除分组永不删条目** —— 分组只是分类
- `shared_hub.py` 新增 **`knowledge()` 统一入口**（用户要求：不散着写新工具）
  · action: search / list_groups / create_group / update_group / move_group /
    delete_group / list_entries / move_entry
  · `list_entries` **不返回正文**（目录用途，避免撑爆上下文）
  · `_resolve_group_id()` 支持 8 位短 id（工具描述里显示的就是短 id）
  · 云端直读本地；本地实例 HTTP 转发
- `agent/tools.py`：**`shared_search` 工具扩展**为统一入口（不是新增工具）
- `app.py`：新增 `POST /api/groups` 端点（云端执行；本地实例经此代理）
- `file_tools.py`：`add_note(folder_id=)`、`update_note(folder_id=)`、
  `list_notes(folder_id=)`（`__root__` = 未分组）
- `app.py`：`NoteAddReq/NoteUpdateReq.folder_id`；GET `/api/notes` 支持 `folder_id` 过滤
- `js/database.js`：条目卡片**右键菜单**「移动到分组」（列出全部分组，缩进显示层级）
- `css/layout.css`：`.db-move-menu*` 样式

**实测（云端真机）**
| 组 | 结果 |
|---|---|
| API 端点（8）| 建/列/改名/移动/防成环/删 全通过；**`folder_id` PUT 后重新 GET 核对真落盘** |
| 智能体工具（9）| 9 个 action 全通过；`list_entries` **每项无 content 字段**；未知 action 返回可用列表 |
| 前端右键（9）| 67 张卡片渲染；右键弹菜单（列 `['（未分组）','📁 前端验证组']`）；点击后**重新拉取核对**到位；移出同理 |
| 权限 | 无 token → 401 |

**测试自身的两个 bug（已修，非产品问题）**
1. 防成环用例把 B 先移到根级 → A/B 成兄弟，移动合法；应在 B 仍属 A 时测
2. 浏览器测试只调 fetch 登录 → 未写 localStorage，前端仍显示"请先登录"；
   须写 `c4eai_token`/`c4eai_user` 并重载页面

**canonical check 增强**：新增 6 条不变量（分组树重算/防循环/工具统一/端点/右键菜单）

**验证后清理**：测试分组已删（`_folders.json` 为空），无 `folder_id` 残留

---

## [2026-09-25] feat(hub): 笔记附件不解析（天然分层：笔记=总结，附件=补充）

**用户设计**：「笔记不需要解析，因为笔记通常是总结，其附带的文档都是补充信息，
天然的分层结构。RAG 时候读到笔记，然后深入的时候智能体自己读文档。
而文档类型的上传会自动解析，不然无法 RAG 检索。」

**核实结论**：此前「笔记发布到共享库」时附件**确实被解析了** ——
`publish_note_to_db` 调 `/api/file/upload?source=database&no_entry=1`，
而 `no_entry=1` **只阻止"另建条目"**，`upload_file()` 里的解析与向量入库**照常执行**。
（用户"好像还是说了"的感觉是对的，但实现是隐式的，看不出来。）

**改动**
- `file_tools.upload_file()` 加 `parse: bool = True` 参数；
  `parse=False` → **early-return：只存原件，不写 `text.txt`、不入向量索引**，
  meta 记 `parsed: False`（把"未解析"当事实存下来，便于排查）
- `file_tools.read_file(file_id=)` 对**未解析附件即时提取原文**
  （pdf/docx/pptx/文本，不落盘不入索引），返回 `on_demand: True`
  —— 这就是用户要的"深入时智能体自己读文档"
- `app.py /api/file/upload` 加 `parse` 参数；**缺省跟随 `no_entry`**
  （`no_entry=1` → 不解析），显式传入优先
- `app.py` 自动建「文档条目」的条件补 `_parse` 守卫（无正文不建条目）
- `shared_hub.publish_note_to_db` → 改为 `no_entry=1&parse=0`
- `agent/tools.py` `read_file` 的 file_id 参数说明补上"未解析附件即时提取"

**不变量**：`upload_file` 里向量入库代码块必然 `parse=True` 才可达
（`parse=False` 已 early-return）—— 这是"笔记附件不污染检索"的实现点，已写入注释。

**实测（云端真机）**
| 上传方式 | `text.txt` | meta.parsed | 向量索引块数 |
|---|---|---|---|
| `parse=0`（笔记附件）| **无** ✅ | False | **0** ✅ |
| `parse=1`（上传文档）| 有 | True | 1 ✅ |

`read_file(file_id)` 对未解析附件返回 `ok:True, on_demand:True`，
即时提取原文 72 字符成功。

**canonical check 增强**：新增 5 条不变量（parse 开关 / early-return /
notes 发 parse=0 / app 缺省跟随 no_entry / on_demand 即时读取）

**验证**：9/9 上传行为 + 磁盘实证 + 索引核对 + 即时读取；
`bash scripts/check.sh` ALL_PASS

---

## [2026-09-24] fix(ui): 条目筛选从「Tab 条」改为「单个筛选按钮 + 下拉菜单」

**用户反馈**：「我点了笔记和文档的UI按钮没反应，而且你的UI做的太诡异了，
你在上传和添加笔记的边上增加一个按钮用来放筛选功能就行了」

**"点了没反应"的真因**（不是 bug）：**未登录时两个 Tab 拉到的都是
同一句「🔐 数据库在共享服务器：请先点击右上角「登录」」**，所以看着像没切换。
登录后才有实质差别（笔记 2 条 / 文档 64 条）。

**但用户的 UI 意见成立**：Tab 条**占一整行**，且未登录时毫无意义 —— 改掉。

**改动**
- `index.html`：删整条 `#db-tabs`；在「📤 上传」「＋ 笔记」右侧加
  `#db-filter-btn`（显示当前分类）+ `#db-filter-menu`（下拉两项）
- `js/database.js`：Tab 事件绑定 → 下拉逻辑
  （点击展开/收起、点菜单项切换、**点别处自动收起**）；
  `_switchDbKind()` 改写为更新按钮文案 + 菜单 active 态
- `css/layout.css`：`.db-tabs*` → `.db-filter-wrap` / `.db-filter-menu` /
  `.db-filter-item`；菜单**左对齐到按钮**（初版 `right:0` 会顶出面板右边缘、
  压住关闭按钮，看着"飘"，已改）

**按钮排布实测**：上传 `x=1099` → ＋笔记 `x=1174` → 📝笔记▾ `x=1243`，同一行

**验证**：14/14 Edge CDP 实测 —— 旧 Tab 已移除、按钮存在且同行、菜单初始隐藏、
点击展开、两项正确、**切换后文案变「📄 文档」**、菜单自动收起、
点别处收起、可切回；截图目视确认菜单不再压出面板；
`tools/check_deploy.py` 22/22 一致；`bash scripts/check.sh` ALL_PASS

---

## [2026-09-24] fix(ui): 删除 OCR/Base64 设置 + 补齐漏部署文件 + 新增部署校验

**用户反馈**（三个问题）
1. 设置里还能看到 OCR 和 base64 选项
2. 数据库按钮打开后没有筛选「笔记/文档」的按钮
3. 我以为"上传统一走 read_file"已完成，但前端还在自动解析

**① 删除 OCR/Base64 设置（自动解析已废弃）**
用户定：上传一律走 read_file 系列工具，前端不该解析。
- `index.html`：删整个「图片上传方式」设置项 + **删 Tesseract.js CDN script**
- `js/api.js`：删开关监听 + `updateImageUploadModeUI()`
- `js/branching.js`：删加载处 + 保存处
- `js/state.js`：删 `imageUploadMode` 默认值 + UI 同步函数
- `js/bubble_actions.js`：删 OCR/Base64 分支 + **死代码 `fileToBase64()` / `imageToTextOCR()` 共 44 行**
- `js/bubbles.js`：删 `extractTextFromFile()` 调用（mammoth/JSZip/Tesseract 全套前端解析）
  → 正文一律由后端按 file_id 注入

**② 数据库「笔记/文档」Tab 看不到 —— 真因是漏部署**
上次 kind 改造只部署了后端 + `js/database.js`，**漏传了 `index.html`
和 `js/database.js`**（前后两次都漏），导致：
- `index.html` 缺 → `#db-tabs` 元素不存在 → 看不到按钮
- `database.js` 缺 → `_switchDbKind` 未定义 → 按钮点了没反应
- `css/layout.css` 缺 → `.db-tabs` 样式没有 → Tab 无样式
- `js/main.js` 也有改动未传

**③ 新增 `tools/check_deploy.py`（防止再漏传）**
逐个前端文件比 md5（本地 vs 线上）。**这次启用后立刻抓出 2 个漏传文件**
（`css/layout.css` 少 `.db-tabs` 样式、`js/main.js` 有改动）。
退出码 0=全一致 / 1=有遗漏。**部署后必跑**。

**验证**：11/11 org 浏览器实测（Edge CDP）——
设置面板无 OCR/Base64/toggle/Tesseract；Tab 可见、文案正确、
**active 样式生效（font-weight 600）**、**点击可切换 note↔document**、无未捕获错误；
`tools/check_deploy.py` **22/22 全部一致**；`bash scripts/check.sh` ALL_PASS

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
- `shared_hub.shared_search()` 用 `_hub_base()` 取地址，默认 `http://<共享库地址>`
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
- 分流单测（httpx 打桩）**12/12**：本地确实发 HTTP 到 `<共享库地址>/api/search/knowledge`；
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
