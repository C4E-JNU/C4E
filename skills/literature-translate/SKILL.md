---
name: literature-translate
description: "文献全文中译写入笔记，多子图按 layout_html 网格排版并配中文图注（图块由 render_figure_block.py 生成，勿手抄 HTML）。需先用 mineru-agent-parse 归档。"
version: 1.3.0
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
- `figures[]`：每个逻辑图一项 —— `{folder, kind, token, caption_en, panel_count, panels:[{file, sub, web_url, page}], layout:{cols,width_px}, layout_html}`。**`layout_html` 是排好版的整段 HTML —— 插图时由 `render_figure_block.py` 直接生成成品块（见第 3 步），不要手抄、不要自己排**；
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

### 3) 按位插图 + 中文图注（调脚本生成成品块，**别手抄 HTML**）
遇到正文里的 `![alt](images/<hash>.jpg)` 锚点时，找到所属逻辑图的 `folder`（如 `Figure2`），**不要手抄 `layout_html`**——调脚本直接生成"网格 div + 图注"成品块，粘进笔记即可。脚本：`skills/literature-translate/scripts/render_figure_block.py`（纯标准库，用 terminal 跑）。

```bash
# ① 先看这篇文献有哪些逻辑图（拿到 folder 名 / panel_count / 网格列数）
env -u PYTHONPATH <ANACONDA-DIR>/python.exe skills/literature-translate/scripts/render_figure_block.py "workspace/mineru_output/<论文名>" --list

# ② 生成某个逻辑图的成品块 → 粘进笔记（省略 --out 则直接打印到 stdout）
env -u PYTHONPATH <ANACONDA-DIR>/python.exe skills/literature-translate/scripts/render_figure_block.py "workspace/mineru_output/<论文名>" \
  --figure Figure2 --caption "三个水环境相关数据集的特征与验证。a–c …；d …；e …" \
  --alts "a=膜分离示意;b=装置示意" --out "_fig_blocks/Figure2.md"

# ③ 或一次性生成全篇图块（追加到笔记前按小节拆分 append）
env -u PYTHONPATH <ANACONDA-DIR>/python.exe skills/literature-translate/scripts/render_figure_block.py "workspace/mineru_output/<论文名>" --all --out "_fig_blocks/_all_blocks.md"
```

**参数（脚本 `--help` 有全量自解释说明）**：
- `--figure NAME`：逻辑图 `folder` 名（可重复，如 `Figure2`、`Table1`）；`--all` 生成全部；
- `--caption "中文图注"`：你译好的该图中文图注正文。编号「图N/表N」由脚本按 images_map 的 `token` 自动加，**不要自己写编号**。仅配合单个 `--figure` 用；
- `--alts "a=膜分离示意;b=装置示意"`：子图 `alt` 的中文短说明。键 = 子图字母 `sub`；**该子图 `sub` 为空时用 1-based 序号**（如 `--alts "5=高频词词云"`）。仅配合单个 `--figure` 用；
- `--path-mode web|relative|absolute`：图片路径形式。默认 `web`（原样用 `web_url` 的 `/workspace/...` 绝对路径，**写笔记就用这个**）；
- `--out FILE`：写入文件（UTF-8，自动建目录）；省略则打印到 stdout；
- `--report`：把可核验的 JSON 摘要（每图 src 列表 / `panel_count` / `cols` / `width_px`）打到 stderr，自查用。

**脚本保证（也是核验点）**：
- **一个逻辑图 = 一个 flex 网格 div（`panel_count` 张 `<img>`）+ 一条图注**；不要拆成多段、不要逐张各占一行；
- div 上的 `data-layout-cols` / `data-panel-count` 与 `style` 里的 `width:{layout.width_px}px` 直接来自 images_map 的 `layout`，网格行列即由此体现（1→1列520px；2→2列380；3→3列290；4→2×2/380；5–9→**3列**/290；≥10→4列/215。依据：子图多为 3:2 / 1:1，3 列 ×290px 一行放 3 张不溢出，9 张 → 3×3）；
- 图片 `src` 与 `00_images_map.json` 的 `web_url` **逐字一致**（不再有手抄错字符导致的破图）；
- 图注编号与 images_map 的 `token` 一致（`图 2` / `表 1`）。

**图注翻译**：把你译好的中文用 `--caption` 传入；不传则块里用 `caption_en` 占位并带一行 `<!-- TODO 译成中文 -->` 注释，记得替换。翻译要点：图注译自该组 `caption_en`（含 a/b/c… 分述，照译成"a–c …；d …"格式）；空缺则据正文补齐。
注意：个别子图 `sub` 缺失（images_map 里为 `null`）时，alt 仅"图N"，多个缺字母子图会重名；用 `--alts` 的序号键逐张补中文说明。

**表格（`kind==Table`）**：`TableN.txt` 里有表格正文 → 优先译成 **Markdown 表**再补 `**表 1　…（中文译注）**`；结构复杂要贴截图时同样用脚本（`--figure Table1`）生成该表的块。

### 4) 收尾
- 追加参考文献可选项：若用户要，另起 `## 参考文献` 用 `00_full_cleaned.md` 之外的 `full.md` References 段翻译；默认省略；
- 末尾可加一段「译注」：说明专有名词处理、图注存疑处、MinerU 可能错配的图（如 caption 与图对不上时**以图/ txt 为准**并标注）。

## 完成核验清单（**先跑校验脚本，再人工补漏**）

**第一步一律跑校验脚本**（纯标准库；脚本：`skills/mineru-agent-parse/scripts/verify_note.py`）。先把笔记正文落盘成 `.md`（如 `workspace/mineru_output/<论文名>/_note_check.md`，校验后可删），再在项目根用 **terminal** 跑：

```bash
env -u PYTHONPATH <ANACONDA-DIR>/python.exe skills/mineru-agent-parse/scripts/verify_note.py \
  "workspace/mineru_output/<论文名>/_note_check.md" --archive "workspace/mineru_output/<论文名>"
# 只校验归档目录本身（不查笔记）：
env -u PYTHONPATH <ANACONDA-DIR>/python.exe skills/mineru-agent-parse/scripts/verify_note.py "workspace/mineru_output/<论文名>"
```

脚本自动查并给**行号/文件名**：① 破图（Markdown 引用的图片在归档目录里不存在）；② 图注/子图数（成品块 `data-panel-count` vs 块内 `<img>` 数 vs 清单 `panel_count` vs `images/FigureN/` 实际张数）；③ 正文截断（`$$`/代码块/`div` 未闭合、末行断表、笔记正文与 `00_full_cleaned.md` 长度比过低）；④ 图片重复引用、图注编号跳号/重号、正文引用了清单里没有的编号。
**退出码：0 = 通过（可带 warning）；1 = 有 error；2 = 用法/读文件失败。** 有 error 必须先修（补图 / 改路径 / `append` 续写）再进人工项。

脚本通过后再人眼确认（脚本判不了的部分）：
1. 每张图片都渲染出来了——脚本只查"文件是否存在"，**能不能渲染**还需看一眼；
2. 图片/表格与其图注、正文引用编号一一对应、位置正确；
3. 图块由脚本生成、未手抄：对每个图核对 `render_figure_block.py --figure <名> --report` 的 `srcs` 与你粘进笔记的 `<img src>` 一致、`img_count == panel_count`；
4. 术语前后一致、数字/单位/公式未被"翻译"改动；
5. 笔记未被截断（最后一段完整、无半句结尾）；脚本报截断则用 append 继续补完。

## 坑

- **插图一律调 `render_figure_block.py` 生成成品块，禁止手抄 `layout_html`**：手抄 1.2KB HTML 极易错一个字符就破图，且手写 markdown `![]()` 序列必然一行一张竖着堆满屏。脚本按 `layout.cols/width_px` 预排 flex 网格，`src` 直接取自 images_map，不会错。要改的只有图注文字（`--caption`）和子图中文说明（`--alts`），别动脚本给的 `src` 和 `width`。
- **不要**用 run_code 一次性生成整篇再写：模型输出会被上限截断。一律"分节翻译 + append"。
- 笔记里的图片路径必须是 `/workspace/...` 绝对形式（静态挂载在 `/`）；相对路径或 `C:\...` 不渲染。
- `00_images_map.json` 的 `caption` 可能为空或错配：以 `images/FigureN/FigureN.txt` 内的原文与图本身为准，冲突时人工核对再定图注。
- 一张图被 MinerU 拆成多块（同 FigureN 多 jpg）：归档脚本已把它们聚到同一个 `images/FigureN/`、`figures[]` 里是一条（`panel_count`>1）——**交给 `render_figure_block.py` 一次渲染成一个网格即可**，不要自己拆成多段、也不要逐张各占一行；确属重复图时才择主图。
- 翻译是"忠实 + 通顺"，不增删结论；数据/结论存疑处照原文，不臆测。

## 关联

- 数据源准备：`mineru-agent-parse`（解析 + 解压 + 归档 + 清单）；
- 图块生成脚本：`skills/literature-translate/scripts/render_figure_block.py`（读 `00_images_map.json` → 成品 Markdown 块；`--help` 自解释）；
- 产出校验脚本：`skills/mineru-agent-parse/scripts/verify_note.py`（破图 / 图注子图数 / 正文截断 / 重复引用与编号跳号；归档目录与笔记都查，退出码非 0 即有 error）；
- 图表核验/图文互证可继续走图谱或挖掘类技能，本译本笔记可作为人工审阅底稿。
