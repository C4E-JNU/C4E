# 更新日志（CHANGELOG）

> 本文件记录**每次改动做了什么、为什么、影响哪些文件**。
> 接手项目前先读本文件 + [`HANDOFF.md`](HANDOFF.md)（当前状态快照）。
> 格式：`[日期] 类型(范围): 摘要` + 改动明细 + 影响文件。

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
