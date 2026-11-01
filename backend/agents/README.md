# 子智能体档案（agent profiles）

用户 2026-10-04 定：「subagent / xxagent 里面挂对应的技能目录、工具目录，和运行上下文参数、
模型参数等等。**不要和主智能体共用。**」

## 目录约定

```
backend/agents/
├── reader/                   # 一个档案 = 一个目录
│   ├── agent.json            # 档案本体：技能白名单、工具白名单、参数、模型
│   ├── system.md             # 专属系统提示（提示词的**唯一载体**，不要再写 agent.json）
│   ├── skills/               # 可选：自带技能目录（含 index.json）。
│   │                         #   存在 → 子智能体只看这里，主智能体的 skills/ 对它不可见
│   └── tools/*.py            # 可选：私有工具（主智能体没有的能力）
└── verifier/
    ├── agent.json
    └── skills/               # 自带技能目录示例
        ├── index.json
        └── data-audit/SKILL.md
```

不建目录、不写 `agent.json` 的档案不存在 —— 内置档案（contradiction / extraction / verify）
仍可用，兼容旧 `tasks[].role` 写法。

## agent.json 字段

| 字段 | 说明 | 缺省 |
|---|---|---|
| `title` / `description` | 人看的名字与说明（前端/主控选档案用） | 空 |
| ~~`system_prompt`~~ | **已弃用**：仅内置档案 `contradiction`/`extraction`/`verify` 兜底用。目录档案的提示词**一律写 `system.md`**；两处都写时 `system.md` 生效，json 里那份是**读不到的死字段** | 空 |
| `skills` | 技能白名单。`null`=不限制；`[]`=全部禁用；`["a"]`=只给 a | `null` |
| `skills_dir` | 相对本档案目录的技能目录名；该目录存在则**取代**全局 skills/ | `skills` |
| `tools` | 全局工具白名单（只读类）。`null`=默认只读全集；`[]`=无工具 | `null` |
| `tools_dir` | 私有工具目录名（相对本档案目录） | `tools` |
| `allow_write` | 是否解锁写类工具（默认关闭，见 `write_tools`） | `false` |
| `status_line` | 结论末尾附 `[STATUS]/[MISSING]/[CONFIDENCE]` 状态行 | `true` |
| `model.model` / `base_url` / `api_key` / `temperature` / `max_tokens` | 档案独立模型；只写其中几项时其余沿用主控 | 全空白=沿用主控 |
| `params.max_rounds` | 轮次；不写=沿用设置里的子智能体轮次 | `null` |
| `params.timeout` / `nudge` / `warn` / `abort` | 三级超时（秒）；不写=沿用设置 | `null` |
| `params.read_file_max_chars` | **单次读入字符上限**（主智能体默认 20000） | `20000` |
| `params.tool_result_max_chars` | 单条工具结果灌进子智能体上下文的封顶 | `4000` |
| `params.tool_result_preview_chars` | 推给前端的预览长度 | `2000` |
| `params.user_context_max_chars` | 主控传进来的 context 截断长度 | `6000` |

## 工具白名单的两个来源

1. **全局只读工具**：按 `SUBAGENT_TOOL_ALLOWLIST` 取（同一份实现，不复制代码）。
   额外可用 `SUBAGENT_EXTRA_READ_TOOLS`（find_files / search_files / list_outputs）—— 但必须
   在档案 `tools` 里**显式写出**才给。
2. **私有工具**：`agents/<name>/tools/*.py`，模块里给 `TOOLS = [tool, ...]`
   或 `def register(registry): ...`。私有工具不受全局白名单限制（它本来就是主智能体没有的），
   但**默认只应做只读**；要写必须 `allow_write: true` + `write_tools: [...]`。
   ⚠️ 私有工具是**目录级**的、不跨档案共享：`fenton-screener/tools/read_pdf.py` 与
   `fenton-verifier/tools/read_pdf.py` 是**同源副本**，改一处必须同步另一处。

## 生效方式

- 改 `agent.json` / `system.md` / 私有技能：**下一次派发即生效**（每次派发重新读盘）。
- 改 `tools/*.py`：按文件 mtime 缓存，改完下一次派发即重新加载。
- 进程内隔离靠 contextvar；并发派多个不同档案的子智能体互不污染。

## 内置档案

| 名字 | 用途 |
|---|---|
| `contradiction` | 文献矛盾核查（原 role 预设） |
| `extraction` | 数据提取（原 role 预设） |
| `verify` | 数据核查（原 role 预设） |

## 自检

```
cd backend && python -c "import json; from agent import profiles as p; print(json.dumps(p.describe_profiles(), ensure_ascii=False, indent=2))"
```

或调接口：`GET /api/agents/profiles`。
