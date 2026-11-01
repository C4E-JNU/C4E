# -*- coding: utf-8 -*-
"""test_contradiction_v1.py — contradiction_taxonomy v1 归一逻辑回归测试（零 token，改口径后必跑）

用法：python scripts/test_contradiction_v1.py
覆盖：历史旧 category（E1/E2/E3/E5/图文/表文/text-text/TT/字母级别/一致类/空）全量样本 + 载体归一变体。
退出码 0 = 全绿；非 0 = 有失败（逐条打印期望与实际）。
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("s4c", os.path.join(HERE, "step4c_errata.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CASES = [
    # (输入旧记录, 期望 bucket, 期望附加字段)
    ({"category": "E1"}, "corrections", {"q": "Q1"}),
    ({"category": "E2"}, "corrections", {"q": "Q2"}),
    ({"category": "OCR伪影"}, "drop", {}),
    ({"category": "OCR"}, "drop", {}),
    ({"category": "E3"}, "ledger", {"sev": "E", "ev": "text-vs-figure"}),
    ({"category": "E3(text-text)"}, "ledger", {"sev": "E", "ev": "text-vs-text"}),
    ({"category": "图文矛盾"}, "ledger", {"sev": "E", "ev": "text-vs-figure"}),
    ({"category": "E5"}, "caliber", {}),
    ({"category": "口径差异"}, "caliber", {}),
    ({"category": "text-text"}, "ledger", {"sev": "E", "ev": "text-vs-text"}),
    ({"category": "text_text"}, "ledger", {"ev": "text-vs-text"}),
    ({"category": "TT"}, "ledger", {"ev": "text-vs-text"}),
    ({"category": "表文"}, "ledger", {"ev": "text-vs-table"}),
    ({"category": "text-vs-table"}, "ledger", {"ev": "text-vs-table"}),
    ({"category": "A"}, "ledger", {"sev": "A"}),
    ({"category": "C"}, "ledger", {"sev": "C"}),
    ({"category": "E"}, "ledger", {"sev": "E", "cross": True}),
    ({"category": "E（跨文献）"}, "ledger", {"cross": True}),
    ({"category": "consistent"}, "consistent", {"q": "Q4"}),
    ({"category": "dual_source"}, "consistent", {"q": "Q4"}),
    ({"category": "within_tolerance"}, "consistent", {}),
    ({"category": "一致（<5% 量程）"}, "consistent", {}),
    ({"category": ""}, "ledger", {"sev": "E"}),
    ({"q": "Q1", "field": "x"}, "corrections", {"q": "Q1"}),
    ({"q": "Q4"}, "consistent", {}),
    ({"q": "Q3"}, "ledger", {}),
    ({"q": "Q3", "caliber_variance": True}, "caliber", {}),
    ({"severity": "B", "category": "E3"}, "ledger", {"sev": "B"}),
    ({"severity": "E", "category": "E"}, "ledger", {"sev": "E", "cross": True}),
    # 新口径：severity = A/B/C/D/E 五值（E = 其他·无法归类）；`unknown` 退场 → 一律归一为 E
    ({"severity": "E"}, "ledger", {"sev": "E"}),
    ({"severity": "unknown"}, "ledger", {"sev": "E"}),
    ({"severity": "unknown", "category": "其他（无法归类）"}, "ledger", {"sev": "E"}),
    ({"category": "其他（无法归类）"}, "ledger", {"sev": "E"}),
]
EV = [("text-image", "text-vs-figure"), ("图文", "text-vs-figure"), ("表文", "text-vs-table"),
      ("text_text", "text-vs-text"), ("图表", "figure-vs-table"), ("", "")]

fails = []
for i, (src, want_bucket, want) in enumerate(CASES, 1):
    r = m.normalize(dict(src))
    if r["bucket"] != want_bucket:
        fails.append(f"#{i} {src} → bucket={r['bucket']} 期望 {want_bucket}")
        continue
    for k, v in want.items():
        if r.get(k) != v:
            fails.append(f"#{i} {src} → {k}={r.get(k)!r} 期望 {v!r}")
for raw, want in EV:
    if m._norm_ev(raw) != want:
        fails.append(f"_norm_ev({raw!r}) = {m._norm_ev(raw)!r} 期望 {want!r}")

print(f"用例 {len(CASES) + len(EV)} 条，失败 {len(fails)} 条")
for f in fails:
    print("  ✗", f)
print("结果:", "全部通过 ✅" if not fails else "存在失败 ❌")
sys.exit(1 if fails else 0)
