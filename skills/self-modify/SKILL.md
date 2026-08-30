---
name: self-modify
description: "自我修改指南 — 如何用 skill_write 创建/更新技能（含 references/templates/scripts 子文件），以及在完全访问模式下如何修改项目代码。加速定位与修改。"
version: 1.0.0
tags: [self-modify, skills, tools, code]
---

# 自我修改指南

本技能教导你如何自我生成/修改技能，以及在完全访问模式下安全、高效地修改项目代码。目标是让你**更快找到位置、更快完成修改**。

## 一、自我生成 / 修改技能（用 skill_write）

1. 先 `skill_list` 查看已有技能；`skill_view` 读取某技能全文（读 SKILL.md 会自动去掉 frontmatter）。
2. 创建或更新技能用 `skill_write`，参数：
   - `name`：技能名，即 `skills/<name>/` 目录名（不能含 `/` 或 `\`）。
   - `content`：文件完整内容；写 SKILL.md 时**必须以 YAML frontmatter 开头**：
     ```markdown
     ---
     name: <技能名>
     description: "<一句话说明用途>"
     version: 1.0.0
     tags: [可选标签]
     ---
     <正文>
     ```
   - `file_path`：默认 `SKILL.md`；需挂子文件时填 `references/xx.md`、`templates/xx`、`scripts/xx.py`。
3. 写 SKILL.md 后 `skills/index.json` 会自动更新，无需手动编辑索引。

**技能目录结构规范**：
- `SKILL.md` — frontmatter（name/description/version/tags）+ 正文
- `references/` — 补充参考文档
- `templates/` — 可复用模板
- `scripts/` — 可复用脚本

## 二、调整"工具用法"＝写技能（不直接改工具代码）

工具定义（`backend/agent/tools.py`）是源码。不要为了"改工具提示词"去改它。
想调整某个工具怎么用、何时用：**写一个技能**，在正文里写清该工具的用法要点、注意事项、示例。技能被加载后注入系统提示词的"可用技能"区块，从而影响你的行为。

## 三、完全访问模式下修改项目代码

处于**完全访问**模式时，你可读写磁盘任意路径（含项目代码、系统文件）。操作务必谨慎：
1. 先用 `list_files` / `read_file` 定位并阅读相关文件，理解结构后再动手。
2. 项目结构速览（加快定位）：
   - `backend/app.py` — FastAPI 入口与路由
   - `backend/agent/tools.py` — 工具注册（工具描述/参数 schema）
   - `backend/agent/agent.py` — agent 构建、系统提示词
   - `backend/tools/file_tools.py` — 文件读写与权限沙箱
   - `backend/tools/skill_tools.py` — 技能读写
   - `js/*.js` — 前端逻辑
   - `skills/` — 技能目录
3. 修改后如需验证，可提示用户重启后端（`python backend/app.py`）或用 curl 测接口。

## 四、权限模式须知
- **安全模式**：只能 workspace 读写，不能写技能、不能改项目代码。
- **询问模式**：可读项目/任意文件；写 workspace 与 skills 允许；写其他路径会返回 `__NEED_FULL__` —— 遇到时**明确告知用户需要切换到完全访问模式才能执行**，并简要说明要做什么。
- **完全访问**：任意读写。被授予此权限时，须警惕你可能对系统文件或软件自身造成**不可逆修改甚至崩溃**，执行高风险改动前先阅读相关文件、谨慎操作，并提醒用户。
