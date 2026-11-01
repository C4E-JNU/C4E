# -*- coding: utf-8 -*-
"""step5b_arbitrate_merge.py — end of Step 5B (verification supplement): write the fenton-arbiter adjudication back into the per-paper ledgers

Background: Step 5 (fenton-verifier) produces **candidate contradictions only** (severity left empty); severity A/B/C/D/E is decided by the cross-paper arbiter
fenton-arbiter in **Step 5B** (the supplement of cross-modal verification, immediately after Step 5), because cross-paper contradictions cannot be decided intra-paper and grading needs one consistent caliber.
This script is **pure deterministic IO**: it merges the adjudication by (paper, figure, field) back into
`<paper folder>/datamining/07_contradiction_ledger.json` and exports the report inputs.

Usage:
  python step5b_arbitrate_merge.py <corpus root or paper folder> [--arbitration <adjudication JSON path>]
Default adjudication file: <corpus>/_ledgers/contradiction_adjudication.json (single paper: <paper folder>/datamining/_ledgers/contradiction_adjudication.json)
Legacy name support: a 09_arbitration.json in the same directory is discovered automatically (old runs need no renaming).

Products:
  ① writes back each paper's 07_contradiction_ledger.json (severity / cross_paper / caliber_variance / decision / review flags)
  ② <corpus>/_ledgers/all_contradiction_entries.json   — the contradiction list consumed by build_report.py (new field names)
  ③ <corpus>/_ledgers/cross_paper_contradiction_pairs.json   — cross-paper pairs + caliber-difference notes
"""
import argparse
import json
import os
import sys

SEV_OK = ("A", "B", "C", "D", "E")     # E = other (unclassifiable), also the fallback for "cannot grade / pending"; unknown is retired
ARB_NAMES = ("contradiction_adjudication.json", "09_arbitration.json")   # the first is canonical, the second is legacy


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def _is_paper_dir(d):
    return (os.path.isdir(os.path.join(d, "datamining"))
            or os.path.exists(os.path.join(d, "00_full_cleaned.md")))


def papers_of(root):
    """Three-way auto-detection: ① root itself is a paper folder ② root holds paper folders directly ③ root/<Bxx>/paper folders."""
    root = os.path.abspath(root)
    if _is_paper_dir(root):
        return [root]
    subs = [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p)) and not p.startswith("_")]
    out = [d for d in subs if _is_paper_dir(d)]
    if out:
        return out
    for bd in subs:
        for q in sorted(os.listdir(bd)):
            d = os.path.join(bd, q)
            if os.path.isdir(d) and not q.startswith("_") and _is_paper_dir(d):
                out.append(d)
    return out


def _key(paper, e):
    return (str(paper or "").strip(), str(e.get("figure") or "").strip(),
            str(e.get("field") or "").strip())


def _load(fp, default=None):
    if not os.path.exists(fp):
        return default
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception:
        return default


def _clean_sev(v):
    """The adjudicated value: legal A–E kept as-is; `unknown` / empty / illegal → E (other · unclassifiable, the fallback)."""
    s = str(v or "").strip().upper()
    return s if s in SEV_OK else "E"


def merge_paper(led, entries, stats):
    """Merge the adjudication entries into one ledger (mutating led['entries'] in place)."""
    idx = {}
    for e in led.get("entries", []):
        idx.setdefault(_key(led.get("paper"), e), e)
    byfig = {}
    for e in led.get("entries", []):
        byfig.setdefault((str(led.get("paper") or ""), str(e.get("figure") or "").strip()), []).append(e)

    for a in entries:
        k = _key(a.get("paper"), a)
        e = idx.get(k)
        if e is None:                                    # lenient: same paper, same figure, and the ledger field is empty
            cand = [x for x in byfig.get((k[0], k[1]), []) if not str(x.get("field") or "").strip()]
            e = cand[0] if len(cand) == 1 else None
        if e is None:                                    # present in the adjudication but absent from the ledger → append
            e = {"figure": a.get("figure", "-"), "field": a.get("field", ""),
                 "text_value": a.get("text_value", ""), "image_value": a.get("image_value", ""),
                 "source": "arbiter", "added_by": "step5b_arbitrate_merge.py"}
            led.setdefault("entries", []).append(e)
            stats["added"] += 1
        else:
            stats["applied"] += 1
        e["severity"] = _clean_sev(a.get("severity"))
        e["cross_paper"] = bool(a.get("cross_paper"))
        e["caliber_variance"] = bool(a.get("caliber_variance"))
        e["evidence_source"] = e.get("evidence_source") or a.get("evidence_source", "")
        if a.get("adopted_value") not in (None, ""):
            e["adopted_value"] = a["adopted_value"]
        if a.get("adopt_reason"):
            e["adopt_reason"] = a["adopt_reason"]
        if a.get("confidence"):
            e["confidence"] = a["confidence"]
        e["needs_human_review"] = (_clean_sev(a.get("severity")) == "E"
                                   or bool(a.get("needs_human_review")))
        e["arbitrated"] = True
        refs = a.get("source_refs")
        if refs:
            e["source_refs"] = refs


def rebuild_stats(led):
    by_sev = {}
    for e in led.get("entries", []):
        if str(e.get("severity") or "").strip().upper() in ("", "UNKNOWN"):
            # not adjudicated / legacy unknown → backfill E (other · unclassifiable, the fallback); the ledger must never keep an empty value
            e["severity"] = "E"
            e.setdefault("needs_human_review", True)
        s = e["severity"]
        by_sev[s] = by_sev.get(s, 0) + 1
    led["total"] = len(led.get("entries", []))
    led["by_severity"] = by_sev
    led["cross_paper_count"] = sum(1 for e in led.get("entries", []) if e.get("cross_paper"))
    led["needs_human_review_count"] = sum(1 for e in led.get("entries", [])
                                          if e.get("needs_human_review"))
    led["arbitrated_by"] = "fenton-arbiter (Step 5B) + step5b_arbitrate_merge.py"


def _find_arb(_ld, explicit):
    if explicit:
        return explicit
    for n in ARB_NAMES:
        fp = os.path.join(_ld, n)
        if os.path.exists(fp):
            return fp
    return os.path.join(_ld, ARB_NAMES[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--arbitration", default=None)
    a = ap.parse_args()
    ps = papers_of(a.root)
    if not ps:
        print(f"No paper folder found: {a.root}")
        return 1
    single = len(ps) == 1
    bname = os.path.basename(os.path.abspath(ps[0])) if single else os.path.basename(os.path.normpath(a.root))
    _ld = os.path.join(wf_of(ps[0]), "_ledgers") if single else os.path.join(os.path.normpath(a.root), "_ledgers")
    arb_fp = _find_arb(_ld, a.arbitration)
    arb = _load(arb_fp)
    if arb is None:
        print(f"[error] adjudication file missing: {arb_fp}\n(dispatch fenton-arbiter first and save its JSON verbatim as this file, name: {ARB_NAMES[0]})")
        return 1
    entries = arb.get("entries") if isinstance(arb, dict) else []
    entries = entries if isinstance(entries, list) else []

    by_paper = {}
    for e in entries:
        by_paper.setdefault(str(e.get("paper") or "").strip(), []).append(e)

    stats = {"applied": 0, "added": 0, "papers": 0, "entries_in": len(entries)}
    all_rows = []
    for paper in ps:
        p = os.path.basename(paper)
        fp = os.path.join(wf_of(paper), "07_contradiction_ledger.json")
        led = _load(fp)
        if led is None:
            print(f"[skip] no ledger: {fp}")
            continue
        merge_paper(led, by_paper.get(p, []), stats)
        rebuild_stats(led)
        json.dump(led, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        stats["papers"] += 1
        print(f"merged {p[:42]:44s} ledger {led['total']} entries {led['by_severity']} "
              f"| cross-paper {led['cross_paper_count']} | pending review {led['needs_human_review_count']}")
        for e in led.get("entries", []):
            all_rows.append({
                "group": bname, "paper": p,
                "figure_location": e.get("figure", ""), "field": e.get("field", ""),
                "severity": e.get("severity") or "E",
                "evidence_source": e.get("evidence_source", ""),
                "cross_paper": bool(e.get("cross_paper")),
                "caliber_variance": bool(e.get("caliber_variance")),
                "text_value": e.get("text_value", ""), "conflict_value": e.get("image_value", ""),
                "adopted_value": e.get("adopted_value", ""),
                "confidence": e.get("confidence", ""),
                "text_quote": e.get("text_quote", ""), "reason": e.get("adopt_reason", ""),
                "needs_human_review": bool(e.get("needs_human_review")),
            })

    unmatched = [e for e in entries if str(e.get("paper") or "").strip() not in
                 {os.path.basename(x) for x in ps}]
    if unmatched:
        print(f"[warn] {len(unmatched)} adjudication entries have a paper name that does not match any paper here and were ignored: "
              f"{sorted({e.get('paper') for e in unmatched})[:5]}")

    os.makedirs(_ld, exist_ok=True)
    cts = os.path.join(_ld, "all_contradiction_entries.json")
    json.dump(all_rows, open(cts, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cross = {"cross_paper_pairs": (arb.get("cross_paper_pairs") if isinstance(arb, dict) else []) or [],
             "caliber_only": (arb.get("caliber_only") if isinstance(arb, dict) else []) or []}
    cross_fp = os.path.join(_ld, "cross_paper_contradiction_pairs.json")
    json.dump(cross, open(cross_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"adjudication {stats['entries_in']} entries → matched {stats['applied']} / added {stats['added']}"
          f" | papers {stats['papers']}")
    print(f"report input → {cts} (the contradiction list for build_report.py; global_score_summary.json must sit in the same directory)")
    print(f"cross-paper pairs {len(cross['cross_paper_pairs'])} | caliber notes {len(cross['caliber_only'])} → {cross_fp}")
    print("next: Step 6 node construction and entity alignment (severity in the 07 ledger is backfilled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
