---
name: document-entry
description: "把文献/文档做成 C4EAI 条目 — 原件气泡 + 总结笔记，支持批量（子智能体分任务并行读）"
version: 1.0.0
tags: [c4eai, literature, branch, bubble, note, batch]
---


# When to Use
用户说「把这篇/这几篇文献建成条目」「给这个文档立个条目」「批量导入文献」之类；
或需要把一批 PDF/文档变成「标题命名条目 + 原件可点气泡 + 信息气泡 + Markdown 富文本总结笔记」。
对**任意文档**通用（论文/报告/标准/说明书…），不限于文献。

# 目标形态
条目名 = 文档标题；条目下挂气泡（PDF 原件可点开 + 日期/期刊/DOI 等信息气泡）；
条目配一篇 Markdown 富文本总结笔记（表格/公式/mermaid 流程图）。

# 调用预算（硬约束，先看这段）
单篇文献 **目标 ≤7 次工具调用**；多篇靠子智能体并行，**不要**用调用次数换"保险"。

| # | 调用 | 说明 |
|---|------|------|
| 1 | `list_files` / `terminal` | 确认路径与篇数（用户已给明确路径/附件时可省） |
| 2 | `terminal` 跑 `--prepare` | **一次传完所有 PDF**（上传+解析+全文落到 `parse_out/`） |
| 3–4 | `read_file` | 一次读完 `parse_out/<名>.txt`，**别反复读同一个文件** |
| 5 | `write_file` | 一次写好 `plan.json` |
| 6 | `terminal` 跑 `--plan` | 建条目+挂气泡+写笔记（回读校验在脚本里做完了） |
| 7 | — | 回报结果，不需要再调工具 |

**明确禁止（"慢"和"疯狂调用"的主因就是这几条）**：
- ❌ **联网查**（`web_search` / `crossref_lookup` / `arxiv_search` / `semantic_scholar_search`）：
  条目要的信息**只在原件里找**。**原件没有的信息，直接不放进气泡**（不编、也不去联网补）。
- ❌ **跑代码**（`run_code` 或自己写 python 片段）：重活全在 `make_doc_entry.py` 里，
  agent 只负责"读解析文本 + 写 JSON + 跑那两条命令"。
- ❌ 同一文件重复 `read_file`、写完再逐个 `GET` 复核（回读校验脚本已做）。
- ❌ 每篇各走一遍 `--prepare` / `--plan`（多篇就一次传全部）。
- ❌ 为了"更稳"多跑一轮校验、多读一遍原文。

# 工作方式（用户偏好，照这个做）
用户通常直接在对话里说「这里的这几篇 PDF 建成条目」并给出路径（或就是本轮对话里的附件）。此时：
1. **先自己找文件**：从用户消息里的路径、所在文件夹、本轮对话的附件里收集 PDF，**列清单确认**
   （篇数 + 文件名）再动手，不要猜、不要只处理第一篇。
2. `--prepare <全部路径或 glob>` 一次跑完（上传与解析天然可以并行）。
3. **然后分任务并行读**：篇数多就用 `delegate_task` 起子智能体，每个子智能体负责 1~3 篇：
   读 `parse_out/<名>.txt` → 产出该条的 `title` / `bubbles` / `note_title` / `note_content`（**只返回 JSON 文本**）。
   ⚠️ 子智能体**只做阅读与写作**，绝不让它们各自发 HTTP 写入 —— `chats.json` 是单体文件，
   并发 PUT 会互相覆盖。
4. **父 agent 汇总后一次写**：把子智能体的 JSON 片段合并进 `plan.json`，跑一次 `--plan`
   （内部 = 一次 PUT 建全部条目 + 逐个 POST 笔记，串行安全）。
5. 回读校验，然后回报每条：条目 id / 标题 / 气泡 / 笔记 id / 校验结果。
单篇不必起子智能体：prepare → 自己填 → plan 即可。

# 由 AI 决定的内容（不要反过来问用户）
- **条目名**：自己读完全文起一个「论文标题」式的名字；**不要**用解析全文首行（常是 `ARTICLE IN PRESS`、
  期刊头、`Contents lists available at…`），也不要用文件名。保留副标题，过长可截到 ~80 字。
- **信息气泡**：从 PDF 正文里自己找，通常 2~4 个：年月（`2025.11`）、期刊缩写（`ACS Catal.`）、
  `DOI 10.xxxx/…`，必要时加一个主题标签（如 `光催化降解`）。**找不到就不编，宁少勿假**。
- **总结笔记**：通用科研总结（用户明确要“通用科研”，**不用**项目里那套催化降解字段模板）：
  `## 一句话结论` / `## 研究背景与问题` / `## 方法与体系` / `## 关键数据`（Markdown 表格）/
  `## 关键公式`（`$$…$$`）/ `## 方法流程`（mermaid，可省）/ `## 结论与局限`。
  能用表格/公式/mermaid 就用。

# 机制（先记住这句，少走弯路）
C4EAI **没有**「创建条目」的后端接口。条目(branch) 与气泡(bubble) 都是 `state.branches` 里的对象，
唯一持久化入口是 **`PUT /api/user/chats`**（按 id 逐个合并，谁的 `updatedAt` 新谁赢）；
原件靠 **`POST /api/file/upload`** 拿 `file_id`，挂到 `branch._bubbles[].fileId`（**fileId 非空 = 点击能打开原件**）；
条目笔记靠 **`POST /api/notes`**（`content` = Markdown，表格/公式/mermaid 都能渲染）。
每个接口的文件:行号、精确字段见 `references/api-map.md`。

# 一条命令
```bash
python scripts/make_doc_entry.py --base http://127.0.0.1:8080 --token "$C4EAI_TOKEN" \
    --prepare "D:/papers/*.pdf" --plan plan.json     # ① 上传原件+拿解析全文+生成计划骨架
#  （中间：agent 读解析全文 → 填标题/信息气泡/总结笔记进 plan.json）
python scripts/make_doc_entry.py --base ... --token ... --plan plan.json   # ② 建条目+挂气泡+写笔记+回读校验
```
`note_content` 留空则只建条目挂气泡，笔记稍后单独补。

# 执行步骤（agent 侧）
1. **确认端口与身份**（默认 `http://127.0.0.1:8080`；**绝不启动/重启用户的 start.bat 或 8080 进程**，只发 HTTP）。
   没有可信 token 时会以 `guest` 写入 → 落到 `users/_guest`，**必须先告诉用户**。
2. `--prepare`：逐个上传原件（`source=chat`）。上传接口**顺带返回解析全文**（`text`），别重复解析；
   脚本把全文写进 `parse_out/<名字>.txt`，并为每条生成 `title` 猜测 + 空的气泡/笔记占位。
3. **agent 自己读那些全文**，提取标题/日期/期刊/DOI（论文通常在首页顶部），写总结笔记：
   必须 **Markdown 富文本**：`##` 分节、表格放关键数据、`$$…$$` 放公式、mermaid 放流程/框架图，
   必要时用 `{{file:<file_id>:<文件名>}}` 内嵌原件卡片。填进 `plan.json`。
4. `--plan`：算新 id → 一次 PUT 建全部条目+气泡 → 逐个 POST 笔记 → 回读校验。
5. **回报**：每条 条目 id / 标题 / 气泡（文件型带 fileId）/ 笔记 id / 校验结果。

# 坑（都踩过，别重犯）
- **身份（实测踩过）**：不带 `Authorization: Bearer <c4eai_token>` 时，**本机实例会写到它的默认身份**
  （本项目实测默认 = `xhq1`，也就是用户自己的账号；不是 guest！）—— 调试时不要拿裸请求试，
  否则测试条目会直接落到用户真实数据里。要指定其他账号或云端必须带 token（浏览器 localStorage 的 `c4eai_token`）。
  测完务必按原样清理（条目 + `branchOrder` + 笔记 + `uploads/<file_id>/`），并给删掉的 id 留一条墓碑防前端推回。
- **标题提取不能全信自动猜测**：解析全文首行常是 `ARTICLE IN PRESS`/`Contents lists`/期刊头，
  agent 必须自己读全文定标题（脚本的 `title` 只是草猜）。
- **DOI / 标题常被硬换行切断**：PDF 解析出来的 DOI 可能是 `https://doi.org/10.1038/` + 换行 + `s41467-026-70180-1`，
  标题也可能夹在 `Cite this article as: Li, J., Li, M., …` 这种引用串里。脚本的 `extract_meta()` 会把软换行拼回去抽 DOI/期刊/年份
  （塞进 `meta_auto` 供参考），但**最终仍要 agent 读原文确认**（找不到就不放，宁少勿假）。
- **`updatedAt` 必须显式给、且是最新 ISO 时间**：合并按它判胜负，空串/旧值会被后端丢弃（回执仍可能 ok）。
- **新 id 规则**：`branch-` + (现有最大 N + 1)，且要同时进 `branchOrder`，否则侧栏排序取不到。
- **气泡两种**：文件型 `{id,text,filePath,fileType,fileSize,icon,fileId,hasFile:true}`；
  文字型 `{id,text,filePath,icon}`（**无 fileId → 点了不会开文件**）。展示顺序 = 数组顺序（`[PDF][2025.11][ACS catalyst]`）。
- **气泡不是独立资源**：必须随 branch 一起 PUT；没有单独的气泡接口。
- **笔记 id 是 `note-<毫秒时间戳>`** → 批量建笔记要串行/隔 1ms；单条 content ≤ 20000 字符（超了分段）。
- **回执成功 ≠ 落盘**：写完必须 `GET /api/user/chats` + `GET /api/notes?branch_id=` + `GET /api/file/<id>` 回读校验。
- **别人可能用 `kind='document'`** 那是共享库文档条目语义；个人文献条目用默认 `kind='note'`。
- 前端若正开着，内存里的旧分支会在下次推送时按 `updatedAt` 竞争；写完让用户刷新一次最稳。
- 想让文献进**全组共享库**才用 `source=database`；只挂自己条目里当附件用默认 `source=chat`。
