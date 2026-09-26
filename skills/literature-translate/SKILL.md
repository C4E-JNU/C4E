---
name: literature-translate
description: "文献全文中译写入笔记，多子图按 layout_html 网格排版并配中文图注。需先用 mineru-agent-parse 归档。"
version: 1.2.0
tags: [translation, literature, mineru, notes, figures]
---

# 高阶文献翻译（全文中译 → 写笔记 + 按位插图并配中文图注）

把「已经 MinerU 解析并归档」的文献，**整篇译成中文**，写进一条排版精良的笔记；并把原文每张图/表**在正确位置**插入对应图片、配**翻译后的图注**。产出像一份图文并茂的中文译本，而不是纯文本。

## 何时用

- 用户给一篇 PDF（或已解析的文献），要"全文翻译成中文并存进笔记/带上图"；
- 需要"图注也翻成中文、图和文字对得上位置"的高质量译本笔记。

## 前置（先满足再开工）

1. 该文献已用 **mineru-agent-parse** 归档，`workspace/mineru_output/<论文名>/` 下有：
   - `00_full_cleaned.md`（清洗全文，References 前）或 `full.md`；
   - `00_images_map.json`（图片清单：`filename`→`folder`/`web_url`/`caption`/`number`/`type`）；
   - `images/FigureN/<hash>.jpg` 等实际图片。
   若没有，先跑 mineru-agent-parse 技能的三步。
2. 笔记要长期保留 → **不要删除/移动 `workspace/mineru_output/<论文名>/`**，笔记里的图片用其中的 `/workspace/...` 绝对路径引用（经静态挂载渲染）。

## 输入解析（建立"图"的索引）

**🔒 读文件白名单（铁律）**：本技能只允许 `read_file` 以下文件——
1. `00_images_map.json`（图表清单，先读这个）
2. `00_full_cleaned.md`（正文，用 offset/limit 分页读，截断返回里有 `next_offset` 照着续读）
3. 需要核对某张图时：`images/FigureN/FigureN.txt`（该图完整英文图注与子图字母）

**❌ 其余一律不读**，尤其禁止读：`layout.json`、`*_model.json`、`*_content_list_v2.json`、`*_content_list.json`（都是几 MB 级坐标/模型转储或脚本内部用的索引，你需要的信息已全部在 `00_images_map.json` 里）。`full.md` 只在用户明确要参考文献列表时才读其 References 段。

---

先 `read_file` 读 `00_images_map.json`。它按**逻辑图**组织，两个关键结构：
- `figures[]`：每个逻辑图一项 —— `{folder, kind, token, caption_en, panel_count, panels:[{file, sub, web_url, page}], layout:{cols,width_px}, layout_html}`。**`layout_html` 就是排好版的整段 HTML（按张数定列数：5–9 张→一行 3 张），插图时整段照抄**；
- `images[]`：逐张子图，字段 `orig / folder / sub / figure / caption_en / page / web_url`。

再 `read_file` 读 `00_full_cleaned.md`（分页读，见白名单）。**正文里的 `![...](images/<hash>.jpg)` 就是图片在原文中的锚点**；一个逻辑图的多个子图锚点通常连在一起。翻译时把"同一 `folder`/`token` 的若干子图"当作**一组**处理，别逐张各占一行。

## 翻译与写入流程

### 1) 新建笔记（只建一次）
```
conversation_notes(action="add", scope="notes", title="<论文名>｜中文全译", content="# <论文中文标题>\n\n> 原文：<论文名.pdf>｜本译本由 MinerU 解析归档后翻译，图片取自归档目录。\n\n## 摘要\n<译文摘要…>")
```
记下返回的笔记 `id`，后续用 `append` 续写。

### 2) 分节翻译、逐段 append（长度纪律是铁律）
**单次 append 不超过约 4000 字**（笔记后端硬上限 20000，但模型单轮输出更易被截断）。按原文小节（## / ###）推进，每节译完就 append 一次，宁多分几段、绝不一次写完整篇。

排版要求（像成品报告，不是纯文本）：
- 用 `#/##/###` 复刻原文层级；
- **图/表在其原位置就地插入**（见第 3 步）；
- 表格：译表头/文字单元格，**数字与单位保持原样**，用 Markdown 表格；
- 公式：保留 `$...$` / `$$...$$`（LaTeX 不译）；化学式、专业符号照抄；
- 术语首次出现给"中文（English）"，全篇一致；引文编号 `[12]` 原样保留。

### 3) 按位插图 + 中文图注（照抄 layout_html，别自己排）
遇到正文里的 `![alt](images/<hash>.jpg)` 锚点时，按 `figures[]` 找到所属逻辑图，**整段复制它的 `layout_html`**，只做一处修改：把每个 `<img>` 的 `alt="图2a"` 换成中文短说明（如 `alt="图2a 膜分离示意"`）。**不要自己手写 markdown 图片序列**——自己排十有八九变成一行一张竖着堆满屏。

为什么必须照抄：`layout_html` 是脚本按子图张数**预排好的 flex 容器**（一个 div 包住全部子图），行数/宽度已算好，与段落怎么断行无关。而 markdown `![]()` 各写一行 = 各自成段 = 一行一张。

**排版规则（脚本已算好，写在 `layout.cols` / `layout.width_px`，无需你计算）**：

| 该图子图数 | 每行张数 | 每张宽度 |
|---|---|---|
| 1 | 1 | 520px |
| 2 | 2 | 380px |
| 3 | 3 | 290px |
| 4 | 2×2 | 380px |
| 5–9 | **3** | 290px |
| ≥10 | 4 | 215px |

（依据：论文子图多为 3:2 / 1:1，3 列 ×290px 恰好一行放 3 张不溢出；9 张 → 3×3 九宫格。）

**写法示例**（Fig.2 有 8 张子图，`layout_html` 已含 8 个 `<img>`，复制后仅改 alt）：

```html
<div style="display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-start;gap:8px;">
<img src="/workspace/mineru_output/<论文名>/images/Figure2/<h1>.jpg" alt="图2a 膜分离示意" style="width:290px;height:auto;border-radius:6px;cursor:zoom-in;" />
<img src="/workspace/mineru_output/<论文名>/images/Figure2/<h2>.jpg" alt="图2b 装置示意" style="width:290px;height:auto;border-radius:6px;cursor:zoom-in;" />
…（共 8 张，全部在这个 div 里）
</div>

**图 2　三个水环境相关数据集的特征与验证。** a–c 三种研究主题示意；d 高频词词云；e 年度发文量；f 3T 达标计数；g 3T 准则示例；h 数据集 III 结构。*（原注：Fig. 2. Characteristics and validation of…）*
```

要点：
- **一个逻辑图 = 一个 `layout_html` div + 一条中文图注**；子图再多也不拆成多段图片；
- 图注译自该组 `caption_en`（含 a/b/c… 分述，照译成"a–c …；d …"格式）；空缺则据正文补齐；
- `web_url` 已在 HTML 里写好（`/workspace/...` 绝对路径），别改路径；改了就不渲染；
- 单子图（`panel_count==1`）同样用它的 `layout_html`（就是一张 520px 居中图）+ 图注一行；
- 表格（`kind==Table`）优先把 `TableN.txt` 里的表格正文译成 **Markdown 表**，再补 `**表 1　…（中文译注）**`；结构复杂再贴它的 `layout_html` 截图。

### 4) 收尾
- 追加参考文献可选项：若用户要，另起 `## 参考文献` 用 `00_full_cleaned.md` 之外的 `full.md` References 段翻译；默认省略；
- 末尾可加一段「译注」：说明专有名词处理、图注存疑处、MinerU 可能错配的图（如 caption 与图对不上时**以图/ txt 为准**并标注）。

## 完成核验清单

打开笔记逐条检查：
1. 每张图片都渲染出来了（不出现破图；破图多半 `web_url` 路径不对或归档目录被移走）；
2. 图片/表格与其图注、正文引用编号一一对应、位置正确；
3. 术语前后一致、数字/单位/公式未被"翻译"改动；
4. 笔记未被截断（最后一段完整、无半句结尾）；截断则用 append 继续补完。

## 坑

- **插图一律照抄 `layout_html`**：自己手写 markdown `![]()` 序列，模型几乎必然一行一张竖着堆满屏；`layout_html` 的 flex div 与段落断行无关，照抄必成网格。仅改 alt 为中文，别动 src 和 width。
- **不要**用 run_code 一次性生成整篇再写：模型输出会被上限截断。一律"分节翻译 + append"。
- 笔记里的图片路径必须是 `/workspace/...` 绝对形式（静态挂载在 `/`）；相对路径或 `C:\...` 不渲染。
- `00_images_map.json` 的 `caption` 可能为空或错配：以 `images/FigureN/FigureN.txt` 内的原文与图本身为准，冲突时人工核对再定图注。
- 一张图被 MinerU 拆成多块（同 FigureN 多 jpg）：择主图或并列插入，别重复堆图。
- 翻译是"忠实 + 通顺"，不增删结论；数据/结论存疑处照原文，不臆测。

## 关联

- 数据源准备：`mineru-agent-parse`（解析 + 解压 + 归档 + 清单）；
- 图表核验/图文互证可继续走图谱或挖掘类技能，本译本笔记可作为人工审阅底稿。
