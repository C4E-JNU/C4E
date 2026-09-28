# C4EAI：条目 / 气泡 / 上传 / 笔记 —— API 精确地图

> 结论先行：**没有**「创建条目」「创建气泡」的 HTTP 接口。
> 条目与气泡是前端对象，靠 `PUT /api/user/chats` 整份合并落盘；原件与笔记各有自己的接口。

## 1. 条目（branch）
| 用途 | 接口 | 位置 | 要点 |
|---|---|---|---|
| 读全部条目 | `GET /api/user/chats` | `backend/app.py:1464` | 返回 `{status, chats:{branches,deleted,branchOrder}}` |
| 写/建条目 | `PUT /api/user/chats` | `backend/app.py:1476` | body `{"chats":{"branches":{id:obj},"deleted":{},"branchOrder":[…]}}` |
| 版本号 | `GET /api/user/chats/version` | `backend/app.py:1457` | 几十字节，轮询用 |
| 事件推送 | `GET /api/events` | `backend/app.py:1421` | SSE，`{type:'chats'}` |
| 落盘实现 | `save_user_chats()` | `backend/tools/user_chats.py:91` | **按 id 合并**（`:124` 比 `updatedAt`；`:84` `_ts()` 空串最旧）；写后回读核对（`:178`） |
| 读取实现 | `load_user_chats()` | `backend/tools/user_chats.py:65` | `workspace/users/<身份>/chats.json`，`MAX_BYTES=16MB`（`:43`） |

branch 对象字段：`id, name, messages[], parentId, createdAt, updatedAt, totalTokens, sessionTokens, hasContent, isTemporary, notes[], _bubbles[]`
- id 规则：`branch-<整数>`，整数 = 现有最大 + 1（`js/branching.js:190-199`）；前端计数器递增在 `js/api.js:274`
- 特殊 id：`__db__` = 云端共享库条目（`app.py:995`）**不要占用**；`main` 会被清理（`branching.js:182`）
- 前端新建：`js/api.js:249 createNewConversation()`（构造 `:274-288`）、`:311 saveTempConversation()`
- 重命名 `js/branching.js:257`；删除 `:285`（连带删该条目全部 file_id，`:315-323`）
- 保存链：`branching.js:211 saveBranches()` → `:219 setUserChats()`（节流 1.5s）→ `js/user_chats.js:214` → `:224 PUT`
- 前端指纹 `js/user_chats.js:40 _branchFp()` 只看 messages/name/notes 数量，**不看 `_bubbles`**
  → 外部只加气泡时必须自己打新 `updatedAt`，否则合并不生效

## 2. 气泡（bubble）= `branch._bubbles[]`
| 用途 | 实现 | 位置 |
|---|---|---|
| 渲染 | 模板串 `<span class="history-bubble" …>` | `js/sidebar.js:807-820`（有 filePath/fileId 时加 `has-file`，`:810`） |
| 点开菜单 | `showBubbleMenu()` | `js/sidebar.js:899-903` → `js/bubbles.js:290` |
| 动作分发 | open-file / edit-text / delete / 发给模型 | `js/bubbles.js:368-519` |
| 打开原件 | `window.openFileById(fileId,name)` | `js/bubbles.js:382-418` → `js/file_preview.js:356` |
| 新建文件气泡 | 面板选文件→上传→push | `js/bubbles.js:552-599`（上传 `:573`，push `:580`） |
| 新建文字气泡 | `addBubbleToBranch(branchId,text,filePath,icon)` | `js/bubble_actions.js:1-14` |

```js
// 文件型（能点开）
{ id:'bub-<ms>-<rand6>', text:'PDF', filePath:'原名.pdf', fileType:'application/pdf',
  fileSize:123, icon:'📕', fileId:'<uuid hex>', hasFile:true }
// 文字型（点了不开文件）
{ id:'bub-<ms>-<rand6>', text:'2025.11', filePath:'', icon:'📌' }
```
**能打开原件的唯一开关 = `fileId` 非空且后端能解析到**（`js/bubbles.js:382`；后端
`_resolve_upload()` `backend/tools/file_tools.py:545-575` 在 `pool/uploads/<id>/meta.json`
与 `users/*/uploads/<id>/meta.json` 里查找）。
文件访问端点：`GET /api/file/{id}`（inline）/`/raw`（attachment）/`POST /api/file/{id}/open`
（本机默认应用）/`/open-location`（资源管理器选中）—— `app.py:866/933/916/972`。

## 3. 上传原件
`POST /api/file/upload`（`backend/app.py:632-743`）
- multipart，**文件字段名固定 `file`**；query：`source`(默认 `chat`)、`no_entry`、`parse`、`folder_id`
- `source=chat` → `workspace/users/<身份>/uploads/<file_id>/`（私人，**不入向量索引**）
- `source=database` → `workspace/pool/uploads/`（共享池；≥50 字且非占位会异步入索引，云端还会自动建文档条目）
- 返回 `{file_id, name, text, text_full_length, size}` —— **`text` 就是解析全文，别再解析一次**（`backend/tools/file_tools.py:848`）
- 落盘：`<base>/<file_id>/<原文件名>` + `text.txt` + `meta.json{file_id,name,size,mime,text_length,created,owner,source,parsed}`（`file_tools.py:821-832`）
- 权限：`source=chat` 仅本人/admin 可读（`app.py:620-629`）
- 前端样例：`js/bubbles.js:569-573`、`js/database.js:262`

## 4. 条目笔记
| 用途 | 接口 | 位置 |
|---|---|---|
| 新建 | `POST /api/notes` | `app.py:1129`；body `{title, content, branch_id, kind:'note', folder_id}` |
| 列表 | `GET /api/notes?branch_id=&with_content=0` | `app.py:1055`（`with_content=0` 只给目录+`chars`） |
| 单条 | `GET /api/notes/{id}` | `app.py:1109` |
| 改 | `PUT /api/notes` | `app.py:1158`（`{note_id,title?,content?,…}`） |
| 删 / 移动 | `DELETE /api/notes/{id}`、`POST /api/notes/move` | `app.py:1177 / 1146` |
| 发布到共享库 | `POST /api/notes/publish` | `app.py:1014` |
| 底层 | `add_note/update_note/delete_note/move_note/list_notes/get_note` | `backend/tools/file_tools.py:996/1027/1051/1060/971/940` |

- `note_id = 'note-' + 毫秒时间戳`（`file_tools.py:1001`）→ 批量要串行防撞 id
- `content` 是 **Markdown 纯文本**（不是 HTML），前端 `formatMessage(note.content,true,'note')` 渲染
  （`js/sidebar.js:361`；`js/formatting.js` 支持表格/mermaid/KaTeX）
- 正文内嵌附件用 `{{file:<file_id>:<文件名>}}`（`app.py:736` 同款占位）
- 智能体侧上限 `MAX_NOTE_CHARS=20000`（`backend/agent/tools.py:351`）
- 前端：`js/sidebar.js:313 renderNotesPanel()`、`:404 addNoteCard()`、`:486` 编辑、`:426` 删除

## 5. 可复用的现成零件
- `conversation_notes` 智能体工具（`backend/agent/tools.py:636` 注册 / `:428-533` 实现）：能写当前对话的笔记，
  但**建不了条目、管不了气泡** —— 这正是本技能补的唯一缺口。
- `mineru_parse`（`backend/agent/tools.py:753` → `backend/tools/mineru_tools.py:147`）+ 技能
  `skills/mineru-agent-parse`、`literature-translate`、`literature-contradiction`：PDF→归档→写笔记这条链已跑通。
- `tools/backfill_shared_files.py`：批量 HTTP 上传 + 改写 `{{file:id:name}}` 引用的现成范式
  （含 `workspace/_auth/hub_session.json` 会话复用）。
- `backend/scripts/migrate_guest_to_xhq1.py`：直接读写 `users/<身份>/chats.json`、`notes.json` 的样例。

## 6. 调用顺序（依赖关系）
```
Step0 身份/端口(base+token)
Step1 GET /api/user/chats → 算新 id        ─┐
Step2 POST /api/file/upload（拿 file_id）  ─┴→ Step3 PUT /api/user/chats（条目+气泡一次带上）
                                                → Step4 POST /api/notes（branch_id=新条目）
                                                → Step5 回读校验（chats / notes / file）
```
批量：Step2 可并行；Step1 只做一次；Step3 **一批一次 PUT**；Step4 串行。
