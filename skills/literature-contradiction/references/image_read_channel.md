# 像素读数通道（image_read）—— 已落地的实现与用法

> **状态：已应用到代码并通过端到端自检**（`backend/tools/file_tools.py` / `backend/agent/agent.py` / `backend/agent/subagent.py`）。
> 改动是**加法式**的：无图时行为与从前完全一致。改后**必须重启后端**才生效。

## 一、通道怎么走通的（原理）

工具产出图片有**三种形态**（`file_tools.set_image_emit(mode)` 引擎级开关）：

| 形态 | 工具返回 | 谁把它变成"模型能看到的图" | 用途 |
|---|---|---|---|
| `blocks`（默认） | `[{"type":"text",...},{"type":"image_url","image_url":{"url":"data:image/..."}}]` | **langchain 引擎**：LangChain/LangGraph 原生吃 `list[dict]` 块 —— `langchain_core.tools.base._format_output` → `_normalize_message_content` 装进 `ToolMessage.content`（type 须在 `TOOL_MESSAGE_BLOCK_TYPES` 内），再由 `langchain_openai._sanitize_chat_completions_content` 原样发到 wire。**custom 引擎**：循环把图提升成独立的 `user` 图像块。 | 主路径 |
| `hoist` | 含 `data_url` 的 JSON 文本 | 引擎嗅探后提升（旧行为） | 兜底/诊断 |
| `off` | 只有提示，无 base64 | —— | 端点/模型不支持视觉时 |

**为什么 base64 绝不能留在工具消息里**：OpenAI 系 `role=tool` 只接受字符串，dataURL 一旦当文本塞进去就被编码成 token（二十万字符乱码），模型**看不到图**。所以 blocks 形态的 text 块里不含 data_url，hoist 形态的 meta 也会剔除 `data_url`。

| 环节 | 实现 |
|---|---|
| 取图 | `read_file(path="x.jpg")` 或 `read_file(file_id=...)` |
| 尺寸闸门 | `IMAGE_INLINE_MAX_CHARS = 200000`（≈150 KB 原图）；超限返回 `mode=image_too_large` |
| 拆分助手 | `split_image_result(result) -> (data_url, text)`，两个引擎循环 + 子智能体共用；`extract_image_data_url()` 是它的薄包装 |
| 子智能体 | `subagent.py` 同样处理，且**在 4000 字符截断之前**抽走图；但它**不能裁图**（无写文件权限），裁 panel 只能主控做 |
| 引擎开关 | custom 流式 = `blocks`；langchain = `blocks`（可用环境变量 `C4EAI_LC_IMAGE_MODE=off` 关闭）；阻塞式入口 = `off` |

## 二、自检记录（2026-09 实测，全部通过）

```
[1] blocks: 返回 list，块类型 ['text','image_url']，均在框架白名单内
[2] hoist : 返回 JSON 文本，meta 已剔除 data_url（工具消息不留 base64）
[3] off   : 只回提示、不回吐 base64；超限图给裁剪提示
[4] BaseTool.arun(tool_call_id=…)  → ToolMessage.content 是内容块（未被 stringify）
[5] 真实 create_agent 循环（假模型录消息）：第二轮模型的 ToolMessage
    content = ['text','image_url']，image_url = data:image/png;base64,…（无 base64 文本残留）
[6] langchain_openai._convert_message_to_dict → {role: tool, content: [text, image_url]}  ← 真发出去的 dict
[7] custom 入口 tool.ainvoke(args) 返回 list；提升后桩文本无 base64，图进 user 消息
[8] off 形态在同一循环里退化为文本提示，无 base64
```

## 三、用法（图账前先做，主控自己跑）

```python
# ① 把目标图裁成 panel 并压小（run_code 工作目录即产出区，直接写文件名）
from PIL import Image
im = Image.open(r"<归档>/images/Figure6/<hash>.jpg")
im.crop((0, 0, im.width, im.height//2)).convert("L").save("fig6_top.png", optimize=True)
```
② `list_outputs()` → 拿 `file_id` → ③ `read_file(file_id="...")` → 模型看到图 → ④ 逐 panel 转录"像素读数表"（`panel / 轴量程 / 图例 / 读数 / 单位 / 不确定度`）。

**尺寸闸门**：单张 dataURL ≤ `IMAGE_INLINE_MAX_CHARS = 200000`（≈150 KB 原图）；实操建议 ≤20 KB/张（越小越省 token）。超限时工具返回 `mode=image_too_large` 并提示裁 panel。
**登记闸门**：按 `file_id` 读要求图在产出区（`users/<u>/outputs/`）并被登记 —— 用 `list_outputs()` 自动补登记。
**路径读**：直接 `read_file(path="...")` 也行（非安全模式可给绝对路径），省掉登记步骤。

## 四、常量与回滚

| 项 | 位置 | 说明 |
|---|---|---|
| `IMAGE_INLINE_MAX_CHARS` | `file_tools.py` 顶部 | 单图 dataURL 上限，改小可省 token |
| `set_image_emit(mode)` | `file_tools.py` | 通道三态开关：`blocks`/`hoist`/`off` |
| `split_image_result(result)` | `file_tools.py` | 拆分助手（返回 `data_url, text`），两引擎循环共用 |
| `C4EAI_LC_IMAGE_MODE` | 环境变量 | langchain 引擎图片通道，默认 `blocks`；端点 400 时设 `off` 重启 |
| 回滚 | `git checkout backend/tools/file_tools.py backend/agent/agent.py backend/agent/subagent.py` | 三文件加法式改动，可整体回退 |

## 五、纪律（不改代码，改行为）

- 读数值必须带**不确定度**（轴最小刻度半格）；没不确定度的读数不进台账。
- 单步最多 3 张图（代码已限），别一次塞十张 —— 上下文会爆。
- 读不到就标 `需图核`，**禁止虚构数值**（历史事故：未读原文编出 0.88 → 假矛盾）。
- 模型需支持视觉；若配的是纯文本模型，图会变成无效载荷 —— 此时关掉通道（custom 改 `off`、langchain 设 `C4EAI_LC_IMAGE_MODE=off`），改走"用户把图作为当轮附件发送"。
- 若端点**拒收 `role=tool` 里的图像**（400 报错），把 langchain 引擎退到 `off`；custom 不受影响（它提升到 `user` 消息）。
- 改代码前先读 `self-modify` 技能；改后**必须重启后端**（`python backend/app.py`）才生效。
