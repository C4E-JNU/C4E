---
name: code-search
description: 当要在代码/文件里定位、读、改一处，或要看 GitHub 开源项目实现时用。先确认三大本地检索工具是否启用（缺则走 cmd findstr 退路），远端用 gh_* 系列。
version: 1.1
---

# 查代码 / 改代码（先定位，再读，最后改）

## 何时用

- 「这个功能/变量在哪里实现的」「哪个文件里有 X」→ 定位类；
- 「把这里的 X 改成 Y」→ 改一处；
- 「看看某开源项目怎么做的」→ 拉 GitHub 源码来读。

## 第 0 步（必做）：确认这三个工具在不在

`search_files` / `find_files` / `edit_file` **不是无名工具**——它们由 `backend/agent/tools.py` 的
`get_tools(enabled_names)` 按**会话启用名单**下发，前端没勾到就不在本会话工具表里，调用即失败。
所以按本技能干活前，先扫一眼自己的工具列表：有它们就走「工具版」，没有就走下面的「cmd 版」。
**不要因为技能写了就假设它一定可调。**

## 本地检索：两条路（本机实测）

本机 terminal 是 **Windows `cmd.exe`（不是 bash）**：`ls` 退出码 255、`grep`/`rg`/`head` 均不可用。

| 想做的事 | 工具版（已启用时） | cmd 退路（实测可用） |
|---|---|---|
| 搜内容（正则） | `search_files(pattern=..., output_mode=files_with_matches / content / count, path=..., file_glob=..., context=3)` | `findstr /s /m /c:"特征串" *.py`（`/s` 递归、`/m` 只列文件名、`/c:` 整串匹配） |
| 按文件名找 | `find_files(pattern="*.py", path=...)`（按修改时间新→旧） | `dir /b`；递归 `dir /s /b *.py` |
| 改一处 | `edit_file(file_path=..., old_string=..., new_string=...)` | ⚠️ cmd 无安全替代：没有 edit_file 时**不要**用 `>` 重写整文件，改为把精确旧串+新串交给用户，或申请启用该工具 |
| 读文件 | `read_file(path=..., offset=..., limit=...)` | 用 `read_file`，**别用 `type`**（read_file 有分页与截断提示） |

串联命令用 cmd 的 `&`：`findstr /n /c:"search_files" backend\agent\tools.py`。

### 路径铁律（实测踩过）

- `read_file` 相对路径**以 `workspace/` 为基准**。要读项目代码必须给**绝对路径**：
  `read_file(path="C:/Users/<用户名>/.../C4EAI/backend/agent/tools.py")`；
  只写 `backend/agent/tools.py` 会直接报「文件不存在」。
- `terminal` 的 `workdir` 默认项目根，所以**终端里**的相对路径（`dir /b`、`findstr ... backend\...`）是对的，
  别把两套基准搞混。

## 收敛策略（照这个顺序，别一上来整篇读）

1. `search_files(..., output_mode="files_with_matches")`（或 `findstr /s /m`）→ 先只问「在哪些文件」。
2. 文件不多时，`output_mode="content"` + `context=3` + `path="那一个文件"` → 看命中上下文。
3. 命中太多时加 `file_glob="*.py"` 或收窄正则；**不要**改成整篇 `read_file`。
4. `output_mode="count"` 用来判断「这条路有没有戏」：某目录 count 全 0 → 换 pattern，而不是继续翻目录。
5. 定位到之后才 `read_file(offset=..., limit=...)` 读那一段。

## 改代码

- `edit_file` 改一处：`old_string` 必须唯一；不唯一时它报错并列出出现次数 → **加上下文行**重试，
  **不要** `replace_all=true` 蒙（会改到不该改的地方）。
- 改完复读一次改后段落确认落盘。没读过的文件不许提改法。

## 看 GitHub 仓库（不需要 token）

1. `gh_repo_info(repo="owner/name")` 确认仓库与默认分支。
2. 读远端：`gh_list_dir(repo=..., path=...)` 逐层看 + `gh_read_file(repo=..., path="src/x.py")` 直读（超长会显式标截断）。
3. **`gh_search_code` 在本网络下不可用**（走 grep.app 公开服务，被反爬质询，返回质询页而非结果，换多独特的串都没用）
   → **别原地重试**，改走 `gh_list_dir` + `gh_read_file`，或下载落地后 `findstr`。
4. 要通读整个项目：`gh_download_tarball(repo=...)`。

### tarball 落地路径陷阱（实测）

- 落地位置是**项目根**下的 `outputs/gh_test/<repo>-HEAD`（如 `graphrag-HEAD`，12.3 MB / 910 文件），
  **不是**本人产出区 → `list_outputs` 收不到它，也没有 file_id。
- `run_code` 的 cwd 是**本人产出区**，与上面错位 → 相对路径 `os.walk` 找不到；改绝对路径后又容易
  **30 秒超时**（近千文件目录扫描慢）。
- 正解：落地后用 `terminal` + 绝对路径 `findstr /s /m /c:"特征串" *.py`，或 `read_file` 直读具体文件；
  **不要**用 `run_code` 扫全树。

## 常见错误

- ❌ 假设 `search_files`/`find_files`/`edit_file` 一定可用 → ✅ 先确认本会话工具表，没有就走 cmd 退路。
- ❌ 在 cmd 里打 `grep -r` / `find` / `ls` → ✅ `findstr /s /m`、`dir /s /b`。
- ❌ 用 `terminal` 的 `type`/`cat` 读文件 → ✅ `read_file`。
- ❌ tarball 落地后用 `run_code` 相对路径扫全树 → ✅ 终端绝对路径 `findstr`。
- ❌ 一次失败就放弃 → 先读报错，换更独特的 pattern，再决定换工具。
