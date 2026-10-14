# 归档：本地实体小库（2026-10-06 体检）

`catalyst_db.json` / `pollutant_db.json` / `processed_dois.json` 在技能包内**零引用**：
实体对齐与查重已改走 Neo4j 协作知识图谱（`step5b1_pollutant_align.py` + PubChem / CACTUS），
本地 JSON 小库不再参与流程。保留仅作历史样本，**勿再写入**。
