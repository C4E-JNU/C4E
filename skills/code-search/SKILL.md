---
name: code-search
description: "在代码/文件里定位与修改的正确姿势：search_files 三态收敛（先 files_with_matches 定位 → 再 content 看命中 → count 判断策略）、find_files 按名找、edit_file 唯一匹配改一处，以及不花 token 读 GitHub 仓库。"
version: 1.0.0
tags: [code, search, grep, glob, edit, github]
---

# 查代码 / 改代码（先定位，再读，最后改）

## 何时用

- 「这个功能/变量在哪里实现的」「哪个文件里有 X」→ 定位类；
- 「把这里的 X 改成 Y」→ 改一处；
- 「看看某开源项目怎么做的」→ 拉 GitHub 源码来读。

## 三个本地工具的分工

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `search_files` | **搜内容**（正则）。三态：`files_with_matches` / `content` / `count` | `pattern`、`path`、`file_glob`、`context`(=-A/-B/-C)、`limit`/`offset` |
| `find_files` | **按文件名**找，按修改时间新→旧 | `pattern`（glob，如 `*.py`）、`path` |
| `edit_file` | **改一处**：old_string → new_string | `file_path`、`old_string`、`new_string`、`replace_all` |

## 收敛策略（照这个顺序，别一上来就整篇读）

1. `search_files(pattern="关键词", output_mode="files_with_matches")` → 先只问「在哪些文件」。
2. 文件不多时，`search_files(..., output_mode="content", context=3, path="那一个文件")` → 看命中上下文。
3. 命中太多（几十上百）时，加 `file_glob="*.py"` 或用更细的正则收窄；**不要**改成 `read_file` 整篇读。
4. `output_mode="count"` 用来判断「这条路有没有戏」：某目录 count 全为 0 → 换 pattern，而不是继续翻目录。
5. 定位到之后才 `read_file(path=..., offset=..., limit=...)` 读那一段（分页，别整篇）。

## 改代码

- 用 `edit_file` 改**一处**：`old_string` 必须唯一；不唯一时会报错并列出出现次数 —— 这时**加上下文行**重试，**不要**用 `replace_all=true` 蒙（那会改到不该改的地方）。
- 改完自己要复读一次改后的段落（`read_file` 带 offset）确认落盘。
- 没读过的文件不许提改法。

## 看 GitHub 仓库（不需要 token）

1. 先 `gh_repo_info(repo="owner/name")` 确认仓库与默认分支。
2. 找文件：`gh_search_code(query="函数名或特征串")`（走公开代码搜索，未认证也能用）；或 `gh_list_dir(repo=...)` 逐层看。
3. 读内容：`gh_read_file(repo="owner/name", path="src/x.py")`；要通读整个项目就 `gh_download_tarball(repo=...)` 落地后再用本地 `search_files`/`read_file`。
4. 官方代码搜索接口需要认证，未认证时走的是公开搜索服务 —— 命中率不如官方，**搜不到就换更独特的特征串**，不要原地重复。

## 常见错误

- ❌ 用 `terminal` 里的 `grep -r` / `find` 搜项目 → ✅ 用 `search_files` / `find_files`（结果结构化、有分页、不依赖 shell）。
- ❌ 用 `terminal` 的 `cat`/`type` 读文件 → ✅ `read_file`（分页 + 截断时明确告诉你还有多少）。
- ❌ 一次失败就放弃 → 先看报错、换个更独特的 pattern，再决定换工具。
