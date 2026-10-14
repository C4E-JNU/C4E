# 归档脚本（2026-10-06 全流程体检归档）

这些脚本**全库零引用**（不被任何细则、档案或其它脚本 import / 调起），已从 `scripts/` 移出，
保留仅为历史参考。**新流程不要再调用**。

| 脚本 | 说明 |
|---|---|
| `fix_schema_nesting.py` | 早期「schema 嵌套修复」补丁（把顶层 `experiments` 塞回 `catalysts` 下）；现行结构由 `fenton-schema` 档案直出 + `step7_reconcile.py` 把关 |
| `gen_package_prompts.py` | 手写包提示词生成；现行由 `step2b_task_packages.py` 出 `task.json`、包提示词由 `fenton-image/system.md` 定义 |
| `pdf_extract_figures.py` | 早期散图抽图；现行 `step0c_clip_figures.py` 按 content_list 的 bbox 裁图 |
| `setup_batch.py` | 旧批次脚手架 |
| `step12_graph_enrich.py` | 图谱富化补丁；现行 `step10_graph_export.py` 直接算 `material_family` / `loading_pct`，故退化为历史批次补丁工具 |
| `make_paper_report.py` | 更早的单篇报告生成器，被 `build_report.py` 取代（内含旧 `e3_flag` / `E1/E2` 口径，勿复用） |
