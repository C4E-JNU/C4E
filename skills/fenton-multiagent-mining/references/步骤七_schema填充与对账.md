# 步骤七：Schema 填充与对账

> 前置：步骤五（`04_verified_image_data.json` + `04c_errata.json` + `04_annotation_table.json`）+ 步骤六（`05_node_inventory.json`）就绪
> 执行：每篇 1 个 `fenton-schema-catalyst` + 每个实验节点 1 个 `fenton-schema-experiment`（**一次全批并行**）
>       + 主控（装配 → 曲线回填 → 对账门 → 数值标准化）

## 派发（拆两档案，一次全批并行）

单次回喂给主控的 summary 有长度上限，完整 schema（多实验 + 全部字段）远超此限——一篇一发必被截断。
**按节点类型拆两个专属档案，一次 delegate_task 全部并行发出**：

- `fenton-schema-catalyst`（**每篇 1 个实例**）：metadata 五项 + 全部催化剂节点 45 字段——表征数据，
  不碰实验/变量/曲线；
- `fenton-schema-experiment`（**每个实验节点 1 个实例**）：只填自己那个 E 的 41 字段 + stability 6 键 +
  变量 levels[] + 曲线绑定表。

```
delegate_task(tasks=[
  {"profile": "fenton-schema-catalyst",
   "goal": "填本篇 metadata + 全部催化剂节点（C1、C2…）的 45 字段；不碰实验/变量/曲线",
   "context": "<论文文件夹>/datamining 目录路径"},
  {"profile": "fenton-schema-experiment",
   "goal": "填 E1（属 C1）的 41 字段 + stability + 变量节点；只输出这一个实验",
   "context": "<论文文件夹>/datamining 目录路径"},
  # 其余 E2、E3… 各一条，同批并行
])
```

context 给的是 **`datamining` 目录**（子智能体的输入文件、落盘产物都在这一层，勿给上层论文夹）。
单篇仅 1 催化剂 1 实验的小论文可改派兜底档案 `fenton-schema` 一次填完，免拆两实例。

**曲线点位一律不回贴**：子智能体只给绑定表 `{ref: "Fig.7a", curve_label: "…", metric: "C/C0", x_unit: "min", y_unit: "", level_value: "3"}`
（`x_unit`/`y_unit` 从 04 图账 `xy_axes` 照抄，随曲线透传进图谱；绑定表字段定义唯一载体见 fenton-schema-experiment 档案）；
主控脚本按 `(figure, subfigure) + curve_label` 从 `04_verified_image_data.json` 取点，
挂到主 `degradation_profiles`、**各水平自己的 `levels[].degradation_profiles`** 与实验 `other.control_curves`；
`level_value` 随绑定表落到曲线条目上（缺则由回填脚本按所在 level 自动补）。

**变量 = 扫描组，水平进 `levels[]`（v2，2026-10-07 起）**：一个变量扫描组 = 一条 `variable_experiments`；
该组扫的每个水平在 `levels[]` 里各占一项 —— **水平值 + 该水平的指标 + 该水平的曲线三者在同一项内一一对应**
（pH 3/5/8 → 三项，各带自己的去除率与曲线）。**不许**把多个水平平铺进一个字符串后只在别处给一个指标值
（"3、5、8" 却只对一个数）；也**不许**把一组扫描拆成三个变量节点。「有曲线就必须挂到对应水平」；
正文没给各水平去除率时**从曲线终点读数**补 `metric_value`（`metric_source: "curve_endpoint@<时间>"`）。
字段定义与水平对齐铁律见 `backend/agents/fenton-schema/system.md`（唯一载体，本文件不重复）。

## 子智能体回报格式

回报 JSON 的**字段定义见** `backend/agents/fenton-schema-catalyst/system.md`（metadata + 45 清单）与
`backend/agents/fenton-schema-experiment/system.md`（41+6 清单 + 绑定表 + 变量铁律的唯一载体，本文件不重复）。
主控只核：各片段 `fill_stats` 四项和 = 各自口径（催化剂 45×C，实验 47）、键名在清单内、曲线逐条有绑定表
`{ref, curve_label, x_unit, y_unit, level_value}`（水平曲线）、
变量 `levels[]` 逐水平各带指标（见下）、`qc` 标记齐。

## 主控收口

1. **装配**：收集全部片段（片段数 = 派发实例数，缺谁按纪律重派）→ 催化剂片段（含 metadata）为树根 →
   实验片段按其 `catalyst_id` 内嵌进对应催化剂的 `experiments[]`（**ID 锚定，勿按名字配对**；
   装配只做结构插入、不改任何字段值）→ 逐篇落盘 `06_schema.json`；
2. **曲线回填**：`python step7b_curve_backfill.py <论文夹 或 批次根> [--dry-run]`
   - 按 `(figure, subfigure) + curve_label` 从 `04_verified_image_data.json` 取点（缺则 `03_image_data.json` 兜底），就地写回 `06_schema.json`（首次先备份 `06_schema.preraw.json`）；
   - 报告 `06_curve_bindings.json`：`filled / already / unmatched / partial`；**未匹配不静默丢** —— 原条目保留并标 `qc.needs_review`，stdout 打印 `ref` 与原因；
   - 先看结果不落盘用 `--dry-run`。
3. **对账门**：`python step7_reconcile.py <论文夹 或 批次根>`
   - **六项**：① 清点数 ↔ 填充数（催化剂/实验/变量**扫描组**）；② properties 键名在清单内（45 / 41 + stability 6）；③ `fill_stats` 四项和 = 总字段数；④ `pollutant_name` 单值；⑤ verified 每条 curve 在 schema 中有归宿、无缺 `points`；⑥ **变量水平对齐**：`levels[]` 每项须同时有 `level_value` 与 `metric_name`/`metric_value`（**塞了水平却不给对应指标 = FAIL**）、曲线 `level_value` 须与所在项一致、`levels` 项数须 ≥ 步骤六登记的 `declared_levels` 数（少 = 漏水平 FAIL；多 = WARN 待复核）；
   - 产物 `07_reconciliation.json`（单篇 → `<篇>/datamining/`；批次 → `<批次根>/_ledgers/`）；
   - FAIL → 按 `missing` 补齐（缺字段补填 / 缺水平补 `levels` 项 / 缺 `points` 先跑回填）后重跑，直到 PASS。
4. **单位化已完成（填充时 LLM 完成）→ 交步骤八纯 LLM 复核**：`fenton-normalizer` 换主体只读复核
   `06_schema.json` 的【数】字段三元组（单位白名单 / 换算 / 缺失补充），修订闭环回填充员——
   全程无脚本，详见《步骤八_数值标准化与单位统一.md》。

## fill_stats 口径（子智能体与对账门同一口径）

`from_text + from_image + from_errata + blank = total_fields`；**分档案记账**：催化剂实例
`total_fields = 45×催化剂数`，实验实例 `total_fields = 47`（每实例只报自己那个 E）——
主控把全部片段的 `fill_stats` **求和**后交对账门（总和 = 45×C + 47×E）。
**未报道字段省略空键，但计入 `blank`** —— 故四项之和恒等于总字段数，这就是对账门第 ③ 项。

## 核验

1. `06_schema.json` 可解析；装配完整——实验片段的 `catalyst_id` 全部落到对应 C 节点、无悬空引用、
   收到片段数 = 派发实例数；`fill_stats` 求和 = 45×C + 47×E；
2. 字段名与清单逐字一致（对账门 `bad_keys` 为空）；清单载体：催化剂 45 → `fenton-schema-catalyst/system.md`、
   实验 41+6 → `fenton-schema-experiment/system.md`（合订兜底本：`fenton-schema/system.md`）；
3. **【数】字段一律 `{value, unit, raw}` 三元组**（2026-10-09 起，字段清单逐字标【数】/【文】/【数|文】，与
   步骤八 NUM_KEYS 对齐）：value 纯数字、unit 规范单位、raw 原文保真（value 夹单位/文字、缺 raw、
   区间臆造主值均不合格）；**【文】字段纯字符串直书、包空壳三元组 = 不合格**；样本见各填充档案 few-shot；
4. Q1/Q2 修正命中、或采纳候选矛盾 `adopted_value` 的字段带 `qc.q_flag`（历史数据里的 `qc.e3_flag` 等价）；存疑带 `qc.needs_review`；
5. 曲线逐条有归宿、无缺 `points`（对账门第 ⑤ 项）；`curve_label` 与图例原文一致；
6. **变量水平一一对应**（对账门第 ⑥ 项）：每个扫描组的 `levels[]` 项数 = 作者扫的水平数，每项都带自己的指标值与曲线；
   正文没给各水平去除率时，`metric_value` 是否由曲线终点读数补上（`metric_source` 已标）；
   只剩水平值、没有对应指标的 = 不合格；
7. 对账门 PASS。

## 下一步

图谱导出与入库：`step10_graph_export.py` → `step11_neo4j_import.py`（优先读 `06b_numeric_schema.json`，
缺则回退 `06_schema.json`——**新批次无 06b，直接用 06**，三元组由导出脚本原生展平成图谱三键）。
