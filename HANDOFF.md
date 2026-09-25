# 交接文档（HANDOFF）

> **给接手的人 / AI agent**：这是**当前状态快照**，读完再动手。
> 改动的历史原因看 [`CHANGELOG.md`](CHANGELOG.md)；架构细节看 [`项目架构说明.md`](项目架构说明.md)；云端部署看 [`共享库部署指南.md`](共享库部署指南.md)。
> **最后更新：2026-09-22**

---

## 1. 项目是什么

C4EAI —— 组内科研辅助 / 共享 Wiki 智能体（江南大学）。前端纯 HTML/JS，后端 FastAPI，支持多用户、共享知识库、图谱检索、技能库。

**两种部署形态**（同一个代码库）：

| | 本地实例（每人一台电脑） | 云端实例（服务器，50 人共用） |
|---|---|---|
| 位置 | 用户笔记本 `127.0.0.1:8080` | 服务器 `<HUB-IP>:8088`（内网域名 `http://c4eai`） |
| 启动 | `start.bat`（**用户自己管，严禁自动重启**） | `~/start_c4eai.sh`，venv `~/c4eai-venv`，目录 `~/c4eai-hub` |
| 鉴权 | `C4EAI_AUTH` 未设 → 鉴权关闭，全放行 | `C4EAI_AUTH=1` → 六级权限生效 |
| 账号 | 可登录（会话存 `workspace/_auth/hub_session.json`） | **账号唯一存在地**（`workspace/_auth/users.json`） |

## 数据存在哪（用户 2026-09-22 明确确认，务必按此理解）

**两类数据**（过去叫法混乱，统一如下）：

| 类别 | 说明 | 存在哪 |
|---|---|---|
| **个人数据库** | 个人的对话笔记（原来误称"本地数据库"） | **当前前端对应的后端所在电脑**：用云端版本 → 服务器 `workspace/users/<账号>/notes.json`；用本地版本 → 本机同路径 |
| **共享数据库** | 云端共享库（`branch_id=__db__`） | **统一在服务器** `workspace/users/<owner>/notes.json`（条目）+ `workspace/pool/uploads/`（附件） |

**关键规则：存在哪取决于「用哪个版本的软件操作」**

| 用哪个软件 | 上传到个人数据库 | 上传到共享数据库 |
|---|---|---|
| **云端版本**（网页） | 存服务器 | 服务器：个人 1 份 + 共享 1 份 |
| **本地版本**（自己电脑） | **存自己电脑** | **本地 1 份 + 服务器 1 份** |

**附件同步**：本地版本上传到共享数据库时，`/api/file/upload?source=database` 会**代理到云端**写
`pool/uploads/`，并把条目正文里的 `file_id` 换成云端新 id（见 `shared_hub.publish_note_to_db` 与
`app.py::api_file_upload`）。

**下载/打开**：个人库附件从**本机**取；共享库附件从**服务器**取。
- 云端版本：直连本机后端（就是服务器）→ 直接读
- 本地版本：本地找不到时**代理转发到云端**（`/raw`、`/{id}`、`/text` 三接口，`not _AUTH_ENABLED` 分支）

**⚠️ 历史坑（已修，务必记住教训）**：早期版本从本地上传共享数据库时**只写占位符、不传文件**，
导致云端条目里的 `file_id` 是**本机 id**、服务器 `pool/uploads/` 没有文件 → **云端打开 404**（本地能开）。
存量修复脚本：`tools/backfill_shared_files.py`（`--apply` 执行）。
同类问题排查口诀：**云端打开失败但本地能开 = 查 `pool/uploads` 里有没有该 file_id**。

### 条目分类 `kind`（2026-09-24 定）

共享库条目分两类，**数据结构完全一致**（都是 文本+附件，共用同一套 CRUD）：

| kind | 含义 | 谁产生 | UI 位置 |
|---|---|---|---|
| `note`（默认）| 人写的笔记，**适合直接阅读** | 「＋ 笔记」按钮、个人笔记「上传到数据库」 | **笔记 Tab（默认展示）** |
| `document` | 机器解析的文档正文，文本乱但**用于检索/向量索引** | 「📤 上传」按钮（上传时自动解析） | 文档 Tab |

**设计意图**：解析结果**独立成文档条目**，不混进人写的笔记（否则一个条目既有手写内容
又有机器解析的几千字 → 笔记混乱）。**两种条目都进向量索引**（按 `source=database` 判定，与 kind 无关）。

**老数据兼容**：无 `kind` 字段 → 一律视为 `note`。
**存量迁移**：`tools/migrate_note_kind.py`（默认 dry-run，`--apply` 执行）；
判据为客观事实——正文含 `{{file:...}}` 占位符 ⇒ 上传产物 ⇒ `document`。
**迁移实况（2026-09-24）**：云端 66 条 → document 64 / note 2。

**⚠️ 迁移脚本铁律**：**必须写入后重新 GET 核对**，只信 HTTP 200 会报假成功
（首次迁移报「成功 66/66」实测 `{None: 66}`；真因：云端跑旧代码 + `shared_hub` PUT 漏传 kind）。

---

## 2. 当前权限体系（2026-09-22 定稿，方案 B）

| 等级 | 颜色 | 数据库上传/查询 | 改/删**他人**数据 | 读/写服务器文件 | 全部工具 | 本地千问 | 身份管理 | 删账号 |
|---|---|---|---|---|---|---|---|---|
| L0 游客 | 灰 `#9aa9be` | ❌ 不可用云端库 | ❌ | ❌ | ❌ 限 4 个 | 5M/日 | ❌ | ❌ |
| L1 | 🔵 蓝 `#3b82f6` | 自己 | ❌ | ❌ 锁本人目录 | ❌ 6 个 | 5M/日 | ❌ | ❌ |
| L2 | 🟣 紫 `#a855f7` | 自己 | ❌ | ❌ 锁本人目录 | ❌ 6 个 | 5M/日 | ❌ | ❌ |
| L3 | 🟠 橙 `#f59e0b` | **所有人** | ❌ | **❌ 锁本人目录** | ❌ 6 个 | 5M/日 | ❌ | ❌ |
| L4 | 🔴 红 `#ef4444` | 所有人 | ❌ | **✅ 全盘读写** | **✅ 9 个** | **不限** | ❌ | ❌ |
| L5 | 🟡 金 `#eab308` | 所有人 | **❌（方案 B）** | ✅ 全盘读写 | ✅ 9 个 | 不限 | ✅ 调 L0–L4 | ❌ |
| `admin` | — | 所有人 | **✅ 所有人** | ✅ 全盘读写 | ✅ 9 个 | 不限 | ✅ 授任何等级 | ✅ |

**三条已实现规则**
1. **橙色 L3 不允许读写服务器文件** → `user_sandbox` 对 L3 及以下返回锁定目录
2. **红色 L4 及以上：全部读写 + 全部工具** → `user_sandbox` 返回 None（不锁）+ `filter_tools` 全开 + `tool_denied("code")` 放行
3. **金色 L5 = 管理员一致，但排除「删账号」「改他人数据」** → `is_super()`（管理级=L5+admin）与 `is_root()`（仅 admin）分离

**⚠️ 两个判据函数不要混用（`backend/role_policy.py`）**
- `is_super(user)` = **管理级**：L5 金 **或** admin。用于「能否进管理面板 / 全套工具 / 全盘文件 / 保留 permission_mode」
- `is_root(user)` = **仅 admin**。用于「删账号 / 授予 L5 / 改删他人数据库与笔记」
- 混用后果：金色会获得删账号或改他人数据的权限，违背方案 B

**`app.py` 里的两个业务判据（2026-09-22 新增）**
- `_is_authorized(user)` = `level >= L1` —— 授权门槛（原写 `role != "admin"`，不认 level）
- `_owns_or_root(user, owner)` = 本人 或 `is_root` —— 数据所有权（金色/红色均不放行他人数据）

**为什么前端也按登录名判而非 role**：历史账号 `xhq1`/`xhq2` 的 `role` 字段仍是 `admin`，
按 `role` 判会让金色**看到**编辑/删除按钮（点了才被后端拒）。故 `js/database.js`、`js/sidebar.js`
的 `isAdmin` 一律用 `me.username === 'admin'`。

**其他**
- 实现在 `backend/role_policy.py`（`level_of` / `can_*` 系列）
- 改等级端点：`POST /api/auth/users/level`（body `{username, level}`）
- 等级元数据端点：`GET /api/auth/levels`（含颜色/名称/说明，前端据此渲染）
- **`admin` 账号不可被降级/删除**；L5 金色**不能**授予 L5（只能 admin 授）
- **注册默认等级 = L1 蓝色**（`role_policy.default_level_for_new_user()`，改这一个函数即可调整）

### 手机端（≤768px）

形态类 DeepSeek：**对话优先 + 侧栏抽屉**。实现全在 `css/mobile.css` + `js/mobile.js`，桌面端零影响。

- 顶栏单行 52px：`☰` 汉堡 + logo + 数据库 + 身份芯片
- 侧栏默认 `translateX(-102%)` 藏起；点汉堡滑入，`body.mobile-drawer-open` 时显示遮罩
- 关闭抽屉：点遮罩 / 点消息区 / Esc / 选对话后自动收
- 手机端**不自动弹登录框**（改为点顶栏「登录」）

---

## 3. 开发环境与命令

```bash
# 快速自检（canonical check）—— 提交前跑一次，秒级完成
bash scripts/check.sh            # Python/JS 语法 + 关键不变量，ALL_PASS ✅ / FAILED

# Python 编译检查（必须 env -u PYTHONPATH，否则 anaconda 环境冲突）
env -u PYTHONPATH <ANACONDA-DIR>/python.exe -m py_compile backend/app.py

# JS 语法检查（中文/括号路径先拷到临时目录；node 是 Windows 程序，需 Windows 风格路径）
cp js/auth.js <USER-HOME>/AppData/Local/Temp/x.js && cd <USER-HOME>/AppData/Local/Temp && node --check x.js

# 本地测试实例（端口 8100；8080 是用户自己的，别动）
cd <CACHE-DIR>/tmp && env -u PYTHONPATH <ANACONDA-DIR>/python.exe run_test_8097.py

# 真机 UI 验证（Edge headless + CDP；需 websocket-client）
cd <CACHE-DIR>/tmp && env -u PYTHONPATH uv run --quiet --with websocket-client python <脚本>.py
```

**验证纪律（血泪教训，务必遵守）**
- canonical check = `bash scripts/check.sh`（2026-09-22 新增）；它只覆盖**静态**语法与不变量，
  **行为验证仍需**针对改动写断言脚本（Edge CDP 真机），跑完即删
- 只改前端（css/js/html）**也必须部署**到云端 —— 否则线上仍是旧版，用户看到的没变
- 验证**以线上文件内容为准**，不能只看版本号（曾出现版本号相同、内容不同）
- 改了文件**必须重新部署**（DRY 清理后漏部署过一次）
- 后端 `.py` 不热加载：改完要让用户**手动重启** `start.bat`（绝不代重启）

---

## 4. 部署到云端

```bash
# ⚠️ 密码必须走环境变量（deploy_cloud.py 已移除硬编码兜底）
export C4EAI_SSH_PASSWORD='<服务器密码>'
env -u PYTHONPATH <ANACONDA-DIR>/python.exe deploy_cloud.py
```

或手动（paramiko / SSH）：

```bash
# 1) 打包上传
tar -czf /tmp/patch.tgz <改动文件...>       # 然后 sftp 上传到 /tmp
# 2) 服务器上解包
cd ~/c4eai-hub && tar -xzf /tmp/patch.tgz
# 3) 重启（必须用不匹配自身的模式，否则 pkill 会杀掉自己发起的那条命令）
pkill -f 'backend/app[.]py' ; sleep 2
cd ~ && setsid nohup ~/start_c4eai.sh > /tmp/c4eai_boot.log 2>&1 < /dev/null &
# 4) 等待并验证（约 4-10 秒恢复）
curl -s -o /dev/null -w '%{http_code}\n' http://<HUB-IP>:8088/index.html
```

**Python 不热加载**：改 `backend/*.py` 后**必须重启**，否则旧进程还跑旧代码（排查时先看响应体：FastAPI 原生 `{"detail":...}` = 旧代码路径）。

---

## 5. 已知坑（踩过的，别再踩）

| 坑 | 现象 | 正确做法 |
|---|---|---|
| 本地跑旧代码 | 改了没生效 | Python 不热加载，**必须重启**；用户本地 `start.bat` 要他**自己**关掉重开 |
| `pkill` 自杀 | 杀进程时把自己的命令也杀了 | 用 `backend/app[.]py` 这种不匹配自身的模式 |
| git-bash 里 `python -` heredoc | 命令需要审批、常超时被拦 | 用 `write_file` 写脚本文件再执行 |
| 中文/括号路径 `node --check` | 报 MODULE_NOT_FOUND | 先 `cp` 到 `<USER-HOME>/AppData/Local/Temp/` 再检查 |
| headless 里 `DataTransfer.items.add()` | `input.files` 长度变 0，多选测不出来 | 用 `Object.defineProperty(inp,'files',{value:[...]})` 伪造 |
| 思考型模型 `content` 为空 | 明明调通了却答不出 | `max_tokens` 给够（≥2048）+ `chat_template_kwargs:{enable_thinking:false}`，并兜底读 `reasoning_content` |
| anaconda `import neo4j` 崩 | numpy2/bottleneck ABI | 测试实例注入 fake neo4j（见 `run_test_8097.py`） |
| `deploy_cloud.py` 直接跑 | 报缺 `C4EAI_SSH_PASSWORD` | 这是**故意的**（防密码泄漏），先 export 环境变量 |
| Hermes 后台任务起测试实例 | 反复弹 `exit 127 / no job control` 噪音通知 | 它是通知不是错误；进程实际没起，改用前台 `subprocess.Popen` 启动 |

---

## 6. 目录速查

```
backend/
  app.py            主 API（1250+ 行）：鉴权、笔记、文件、数据库代理、agent 入口、小e
  auth.py           账号：注册/登录/会话持久化/set_level
  role_policy.py    六级权限：level_of / can_* 系列 / token 限额
  agent/
    agent.py        三个入口：stream_agent / stream_agent_lc / run_agent
    tools.py        工具注册表（前端不再持有工具表）
    subagent.py     子智能体（裸 HTTP 循环，深度1/并发≤2/轮次≤4/超时300s）
  tools/
    shared_hub.py   共享库条目 CRUD + publish_note_to_db + hub 代理
    file_tools.py   文档解析（OCR/PDF/docx/xlsx）+ 抽取文本落盘 + 笔记读写
    vector_search.py 向量检索 + 正文入库
    neo4j_tools.py  图谱 schema / cypher
    code_sandbox.py 代码执行（仅主管理员）
js/
  auth.js      登录弹窗 / 身份芯片（等级色圆点）/ 用户管理面板（等级选择器）
  xiaoe.js     桌宠小e（Q弹互动 + 天气 + 5%随机惊喜 + 导览问答）
  database.js  数据库面板（多选上传 / 引用 / 浮窗打开）
  bubbles.js   笔记气泡（多选附件）
  graph_rag.js agent 调用主链路（含 note_refs 注入）
  main.js      启动流程
css/xiaoe.css  小e 动画（10组 Q弹关键帧 + 7类天气）
workspace/     ⚠️ 运行数据，**不入库**（上传/解析产物/笔记/账号/sessions）
  _auth/users.json      账号（含 level）
  _auth/sessions.json   会话（持久化，重启不丢）
  _auth/usage.json      千问 token 用量
  pool/                 云端共享库（唯一一份）
model_cache/   ⚠️ 91MB 模型缓存，**不入库**（可重下）
```

---

## 7. 待办 / 未决

- [ ] **无自动化测试** —— 目前只有临时断言脚本（跑完即删）。建议补最小测试套件（至少覆盖权限矩阵 + 多选上传 + 文件代理转发）
- [ ] **旧数据库条目正文回填** —— 权限改造前上传的条目，正文仍只有占位符。用户明确说"旧条目不要管"
- [ ] **手机端响应式** —— 主界面从未做手机适配（只出过问题清单，未动手）
- [ ] **`model_cache/` 首次运行需下载** —— 因不入库，新环境要联网重下（Qdrant bge-small-zh）
- [ ] 本地实例的**数据库读写代理**目前靠 `hub_session.json` 里的 token；token 过期需重新登录

---

## 8. 关键凭据与入口

| 项 | 值 |
|---|---|
| 云端地址 | `http://<HUB-IP>:8088` 或内网 `http://c4eai`（仅校园网） |
| SSH | `user@<HUB-IP>`（root 禁用）；密码走 `C4EAI_SSH_PASSWORD` 环境变量 |
| admin 账号 | `admin` / 密码见启动日志或 `C4EAI_ADMIN_PASSWORD` |
| 云端 LLM | `http://<HUB-IP>:8080/v1`（Qwen3，免密钥） |
| 本地测试实例端口 | **8100**（8092–8098 已废弃） |

> ⚠️ 凭据**不要写进代码或文档提交**。`deploy_cloud.py` 已改为只读环境变量。
