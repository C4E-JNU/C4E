---
name: fenton-multiagent-mining
description: 当用户发来任意文献或指定文献目录、要求运行非均相 Fenton 文献数据挖掘并提取图表数据时用。流水线（筛选 → 归档 → 工单 → 图像挖掘 → 图文互证 → 5B 补充裁决 → 节点对齐 → schema 填充 → 数值标准化与单位统一 → 成品终检 → 图谱入库）；语义判断派子智能体，确定性规则与 IO 走主控脚本。
version: 3.4.8
---

# 多智能体 Fenton 数据挖掘（总入口 · 主控手册）

你是主控智能体，按下方步骤**顺序**推进：派发任务 → 等结果 → 按细则核验接收。

- 每步「怎么判、怎么读、怎么算」与「**回报 schema**」→ 子智能体档案 `backend/agents/<名>/system.md`
  （唯一载体；**勿抄进细则**，抄两份必漂移）。
- 主控要用的细则（派发模板、核验清单）→ 本技能包 `references/`。
- 路径与编号约定：**本技能包** = `skills/fenton-multiagent-mining/`（细则里「本技能包 `scripts/`」即其下）；实验编号写作 `E1/E2…`（与**已废弃**的矛盾类别字母 E 无关）。

## 步骤细则索引

| # | 步骤 | 谁执行 | 细则文件 |
|---|---|---|---|
| 1 | 文献筛选 | 子智能体 `fenton-screener` | `references/步骤一_文献筛选.md` |
| 2 | 解析与归档 | 主控脚本 | `references/步骤二_解析与归档.md` |
| 3 | 工单生成 | 子智能体 `fenton-workorder` | `references/步骤三_工单生成.md` |
| 4 | 图像挖掘 | 子智能体 `fenton-image` | `references/步骤四_图像挖掘.md` |
| 5 | 图文/表文互证（只产发现项 Q + 候选矛盾） | 子智能体 `fenton-verifier` | `references/步骤五_图文互证.md` |
| **5B** | **图文互证补充 · 批次级矛盾裁决与定级** | 子智能体 `fenton-arbiter` + 主控脚本 | `references/步骤5B_图文互证补充裁决.md` |
| 6 | 节点搭建与实体对齐 | 子智能体 `fenton-node` + 主控脚本 | `references/步骤六_节点搭建与实体对齐.md` |
| 7 | schema 填充与对账 | 子智能体 `fenton-schema-catalyst`（每篇 1）+ `fenton-schema-experiment`（每实验 1，并行）+ 主控装配/脚本 | `references/步骤七_schema填充与对账.md` |
| 8 | 单位复核（单位化已在步骤七由 LLM 完成；本步**换主体纯 LLM 复核，无脚本**） | 子智能体 `fenton-normalizer`（只读复核）+ 主控修订闭环 | `references/步骤八_数值标准化与单位统一.md` |
| 9 | 成品查验（终检） | 子智能体 `fenton-auditor` | `references/步骤九_成品查验.md` |
| 10 | 图谱导出与入库 Neo4j + 表格导出 | 主控脚本（step10/11 入库；step12 表格） | `references/步骤十_图谱导出与入库.md` |
| **10b** | **历史图谱矛盾清洗**（可选·一次性，幂等） | 主控脚本 `step10b_clean_legacy.py` | `references/步骤十_图谱导出与入库.md` |

**5B 是图文互证的补充步骤**：紧接步骤五（全批互证收尾、各篇 `07_contradiction_ledger.json` 出齐）执行，
是流水线里**唯一有权判矛盾严重程度**的环节 —— 跨文献矛盾篇内判不出来、定级需统一口径，故不塞进步骤五。

**分工原则**：语义判断（该不该收、值对不对、口径是否一致、这个数配什么单位）→ 派子智能体，换主体才查得出自己的偏差；
确定性规则与 IO（解析、匹配、MERGE）→ 主控脚本，可复现、可核验。
单位化已并入步骤七填充（LLM 内建换算表，填充即产三元组）；步骤八是**换主体的纯 LLM 复核**（无脚本）：
`fenton-normalizer` 核实三元组合法性、查换算、补缺失单位，修订闭环回填充员——脚本对语义字符串易错，
复核这种语义核对交给带上下文的 LLM 更可靠。旧脚本三段式仅旧批次字符串口径使用。

**数值与单位落地铁律（2026-10-09 起 v2）**：schema 填充阶段**数值字段直接产 `{value, unit, raw}` 三元组**
（value 纯数字、unit 规范单位、raw 原文保真——结构化由 LLM 填充时一次做完）；字段清单**逐字段标【数】/【文】/【数|文】**
（与步骤八校验门 NUM_KEYS 对齐）：只有【数】字段包三元组，【文】字段纯字符串直书、包空壳三元组 = 不合格；
入图谱时**扁平三键** `<field>` / `<field>_unit` / `<field>_raw` —— **单位与原文永不丢**，
跨文献聚合才不会把 1.9 nm 和 19 Å 混比。**步骤八为纯 LLM 复核**（无脚本）：`fenton-normalizer` 换主体
核实单位/换算并补缺失，修订闭环回填充员后 `06_schema.json` 定稿；旧批次字符串口径仍可走旧三段式脚本流程（文档保留）。

**图像轴单位与提取数值纪律（2026-10-09 起）**：图像挖掘结果自身必须单位数值齐全 ——
xy 记录两轴 `label/unit/range_min/range_max` 每轴必填、`unit` 照抄图面不换算；basic 定量项 `value + unit`
成对、item 用规范名（均见 `fenton-image/system.md`）；schema 曲线绑定表随带 `x_unit` / `y_unit`
（从 04 图账 `xy_axes` 照抄），图谱 Curve 节点因此带坐标单位 —— 「60 min vs 1 h」跨文献可对齐。

**变量水平口径（2026-10-07 起）**：一个变量扫描组 = 一条 `variable_experiments`，各水平进 `levels[]`
—— **水平值 + 该水平的指标 + 该水平的曲线三者同层、一一对应**（pH 3/5/8 → 三项，各带自己的去除率与曲线）；
**不许**把多个水平平铺进一个字符串后只在别处给一个指标值，也**不许**把一组扫描拆成三个节点。
图谱导出时逐 level 展开成 `E1::V1::L2`（「pH=3 时去除率」可直接查）。字段定义见 `fenton-schema/system.md`。

**矛盾口径（2026-10-06 起，唯一来源）**：`references/矛盾分类规范.md`
—— 两条互不相干的轴：**严重程度 A/B/C/D/E**（**E = 其他·无法归类**，兼「判不了」兜底；批次级 `fenton-arbiter` 判，步骤 5B）+ **互证发现项 Q1–Q4**（篇内 `fenton-verifier` 判，步骤五）；
载体来源（图文/表文/文文）只作 `evidence_source` 字段，**不再作分类**；旧的 `category`、E1/E2/E3/E5 一律废弃。

**图谱落地（2026-10-06 起）**：只有真矛盾入图谱。每个矛盾**三层锚点** —— `DISPUTES → Paper`（必连，零孤儿，答「哪篇文献」）、
`FROM_EXPERIMENT → Experiment`（答「哪个实验」）、`DISPUTED_IN → Figure`（图节点按论文限定 `FIG::<paper_id>::FigureN`，答「哪张图」）；
节点带 `trace`（`paper_id / 标题 / 字段 / 图号`）一行直读溯源。
非真矛盾换标签 `:ContradictionArchived` 出库（**不删节点**、摘 `embedding`，可回滚审计）。
历史库重排：`python scripts/step10b_clean_legacy.py --dry-run` 先看命中数，再去掉 `--dry-run` 执行。

**改口径后必跑回归**：`python scripts/test_contradiction_v1.py`（35 条历史口径样本，全绿才算收工）。

**输入路径一律由用户给定**（PDF / 论文文件夹 / 批次目录皆可），本技能不假定任何固定路径与篇数。

**现在开始**：读 `references/步骤一_文献筛选.md`，执行第一步。
