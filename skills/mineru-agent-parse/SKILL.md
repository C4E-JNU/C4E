---
name: mineru-agent-parse
description: "把 PDF 解析并归档成'全文+按图注编号的图表文件夹'，产出 00_full_cleaned.md 与 00_images_map.json 清单，供翻译/图文挖掘使用。需配 MinerU Token。"
version: 4.2.0
tags: [mineru, pdf, parse, archive, literature]
---

# MinerU 解析 + 归档（PDF → 全文 + 按图注编号归档的图片 + 清单）

把一篇/一批 PDF 变成后续可复用的标准中间产物：**清洗全文 `00_full_cleaned.md` + 图片按图注编号归到 `images/FigureN/` + 映射清单 `00_images_map.json`**。这是「高阶文献翻译」「图文挖掘」等技能的数据源，先跑本技能归档。

## 何时用

- 用户上传 PDF、给出 PDF 文件或含 PDF 的文件夹路径，要解析/提取内容；
- 要把文献做成「正文 + 图表归档」的规整目录，供翻译、图文互证、数据挖掘；
- 只要 Markdown 正文时也可只跑第 1 步；要图片归类和清单则跑完 1→2→3。

## 前置条件

1. **MinerU Token 已配置**：『智能体配置』面板 → MinerU 解析配置，填 Token 保存（会过期）。
2. 归档脚本用 Python 标准库解压，建议用 **terminal** 工具运行（需要「询问/完全访问」权限以读写 workspace）；纯计算也可 `run_code` 内联，但 `terminal` 更稳。
3. 输出统一落在 `workspace/mineru_output/`（图片经静态挂载可用 `/workspace/...` URL 访问）。

**🔒 本技能的读文件白名单（铁律）**：归档全部由脚本完成，**你不需要读任何 json**。允许读的只有：
- 脚本 stdout 的 JSON 结果（核验用）；
- `00_images_map.json`（抽查图表清单，约 20KB，安全）；
- `images/FigureN/FigureN.txt`（抽查某图图注）。
**❌ 禁止读**：`layout.json`、`*_model.json`、`*_content_list_v2.json`、`*_content_list.json`——它们是几 MB 级坐标/模型转储或脚本内部索引，读它们曾直接把上下文撑到 890 万 token 导致 400 报错。选哪个 content_list 是脚本内部的事，与你无关。

## 三步流程

### 第 1 步：解析（mineru_parse）

```
mineru_parse(path="workspace/uploads/xxx/某论文.pdf", out_dir="mineru_output")
# 或批量：path 给一个含多个 PDF 的目录 → 递归扫 *.pdf，每批≤50，各输出一个同名 .zip
```
产物：`workspace/mineru_output/<论文名>.zip`。zip 是**扁平结构**：`full.md` / `images/<hash>.jpg` / `<uuid>_content_list.json` / `layout.json` / `..._origin.pdf`。

返回 `downloaded` 里的 `zip` 路径就是下一步输入。

### 第 2 步：解压 + 图片归档 + 清洗 + 清单（自带脚本）

用 **terminal** 在项目根执行（脚本：`skills/mineru-agent-parse/scripts/mineru_archive.py`）：

```bash
# 单个 zip
python skills/mineru-agent-parse/scripts/mineru_archive.py "workspace/mineru_output/<论文名>.zip"
# 含多个 zip 的目录 → 批量
python skills/mineru-agent-parse/scripts/mineru_archive.py "workspace/mineru_output" 
# 也可指定输出根
python skills/mineru-agent-parse/scripts/mineru_archive.py "<zip>" "workspace/mineru_output"
```
脚本会：① 防目录穿越解压到 `<out>/<名>/`；② 读正确的 `*_content_list.json`（自动排除 `_v2`/`_model`/`layout`）；③ **以 full.md 的图注块为准归并子图**：`Fig./Table N` 图注前的连续若干 `images/<hash>` 视为同一张图的子图，一起复制进 `images/FigureN/`（一张多子图 → 同一文件夹的多张 jpg），并写 `FigureN.txt`（**完整英文图注** + 每个子图的 a/b/c 字母与页码）；表按 `Table N` 单独归档；content_list 仅用于补字母标号/页码/表格正文；④ 截 References 前正文写 `00_full_cleaned.md`；⑤ 写映射清单 `00_images_map.json`（含 `figures[]` 逻辑图分组，见下）。全程幂等（重跑覆盖），源 zip/论文目录不动。

> 为什么不用单张 caption 抽号：MinerU 常把一张多子图拆成多个 image 项、每项 caption 只有"a/b/C"单字母甚至为空——只靠它会把 Fig.2 的 a–h 打散成 Figure2…Figure9。故改用 full.md 顺序聚类，**保证同一图号的子图进同一个 `FigureN/`**。

### 第 3 步：核验

脚本 stdout 是 JSON，逐篇确认：
- `ok:true`；`stats` 里 `figure/table/other` 合理，`unknown` 很少（若几乎全 `unknown` → 该 zip 的 content_list 取错或 images 命名对不上，检查 json）；
- `cleaned.ok:true` 且 `chars>0`；
- `workspace/mineru_output/<名>/` 下有 `images/FigureN/…`、`00_full_cleaned.md`、`00_images_map.json`。

## 产物结构

```
workspace/mineru_output/<论文名>/
  00_full_cleaned.md      # 清洗全文（References 前）——翻译/挖掘文本源
  00_images_map.json      # 图文映射清单（figures[] 逻辑图分组 + 逐子图，见下）
  full.md                 # MinerU 原始全文（含内联 ![](images/<hash>.jpg)）
  <uuid>_content_list.json
  images/
    <hash>.jpg            # 原始散图（保留，不动）
    Figure2/              # ★ 一张多子图的图：该图号的全部子图 jpg 都在这里
      <hashA>.jpg ...     #   每个子图一张 jpg
      Figure2.txt         #   完整英文图注 + 每个子图的 a/b/c 字母与页码
    Figure4/<hash>.jpg + Figure4.txt   # 单子图的图（1 张）
    TableN/<hash>.jpg + TableN.txt     # 表（含表格正文转文本）
    unknown_NN/                          # 未被任何 Fig./Table 图注收留的散图
```

## 清单 `00_images_map.json` 字段（翻译技能按位插图 + 一行多图靠它）

```json
{ "paper":"…", "out_dir":"…", "content_list":"…_content_list.json",
  "counts":{"figures":6,"tables":1,"unknown":4,"image_files":40,
            "multi_panel_figures":["Figure1","Figure2","Figure3","Figure5","Figure6"]},
  "figures":[
    {"folder":"Figure2","kind":"Figure","token":"2",
     "caption_en":"Fig. 2. Characteristics and validation of three water-related datasets. a-c …",
     "panel_count":8,
     "layout":{"cols":3,"width_px":290},
     "layout_html":"<div style=\"display:flex;flex-wrap:wrap;…\"><img src=\"/workspace/…/Figure2/<h1>.jpg\" alt=\"图2a\" style=\"width:290px;…\" /> ×8</div>",
     "panels":[{"file":"<hash>.jpg","sub":"a","web_url":"/workspace/…/images/Figure2/<hash>.jpg","page":4}]}
  ],
  "images":[
    {"orig":"images/<hash>.jpg","filename":"<hash>.jpg","folder":"Figure2","group":"Figure2",
     "sub":"a","type":"image","figure":"2","caption_en":"Fig. 2. …","page":4,
     "web_url":"/workspace/mineru_output/<论文名>/images/Figure2/<hash>.jpg"}
  ]
}
```
- `figures[]`：**按逻辑图分组**——一张多子图是**一条**，`panels` 列出该图全部子图（含字母 `sub`、`web_url`、`page`）。翻译技能据此把多个子图排进**一行**、共用一条中文图注。
- `layout` / `layout_html`：**脚本按子图张数预排好的网格**（1→1列520px；2→2列380；3→3列290；4→2×2；5–9→3列290；≥10→4列215），`layout_html` 是可直接照抄的 flex HTML（写笔记插图用整段这个，只改 alt）。
- `caption_en`：该图的**完整英文图注**（含 a/b/c… 分述），翻译后作图注。
- `web_url`：笔记/正文 `![](…)` 直接可用的绝对路径（经静态挂载渲染）。

## 坑

- `mineru_parse` **只下载 zip、不解压**；不解压就没有 `full.md/images/`，必须跑第 2 步。
- content_list 有两份（`_content_list.json` 与 `_v2.json`）+ `_model.json`：脚本已锁定标准 `_content_list.json`；若手工读 json 别用 `_v2`/`_model`。
- **子图归并依赖 full.md 的图注块**：若某篇 `full.md` 里图注写法特殊（如 `Fig. 2:` 冒号、或图注在子图之前），该图可能没聚成组而落到 `unknown_`。核验时留意 `unknown` 数量，必要时人工核对。部分图 caption 为空仍会归入正确 `FigureN`（靠 full.md 顺序），只是 `sub` 标号缺失、图注要靠正文补。
- Token 过期 → 第 1 步报鉴权错误，让用户在配置面板重填。
- **别用 `read_file` 整读巨型 JSON**：归档目录里的 `layout.json`、`*_model.json`、`*_content_list_v2.json` 常是"单行数 MB"的坐标/模型转储，读进来会瞬间撑爆模型上下文（曾触发 890 万 token 的 400 报错）。要正文只读 `00_full_cleaned.md`，要图表信息读 `00_images_map.json`（不大）。工具已加 20k 字符截断护栏，但仍应避免去 `list_files` 后逐个 `read_file` 这些 json。
- 大 PDF / 中文深路径：脚本已内置 Windows 长路径 `\\?\` 处理，无需手动改。

## 关联

归档完成后：要翻译并图文写笔记 → 用技能 **literature-translate**；要做图文互证/实体挖掘 → 用本清单与 `00_full_cleaned.md` 作为输入。
