# -*- coding: utf-8 -*-
"""step4c_errata.py — end of Step 5: normalize per contradiction_taxonomy v1 → per-paper candidate ledger + errata + verification gain

Caliber (since 2026-10-06, single source skills/fenton-multiagent-mining/references/contradiction_taxonomy.md):
  axis 1 severity = A/B/C/D/E (E = other · unclassifiable, also the pending fallback; unknown is retired and normalized to E on sight); two booleans cross_paper / caliber_variance
  axis 2 verification finding q = Q1 (our figure misread) / Q2 (our text misread) / Q3 (caliber difference) / Q4 (consistent)
  field evidence_source = text-vs-figure / text-vs-table / text-vs-text / figure-vs-table
  the legacy category (E1/E2/E3/E5/figure-text/table-text/text-text/legacy single-letter E) is migrated once by this script; **the old E level (cross contradiction) is retired** → replaced by the cross_paper boolean.
  Grading (A–E) is done by the cross-paper arbiter fenton-arbiter in Step 5B (the verification supplement); this script never changes an existing severity and only backfills empties as E.

Products:
  <paper folder>/datamining/07_contradiction_ledger.json   per-paper candidate contradiction ledger (severity empty/E pending backfill)
  <paper folder>/datamining/05b_verification_gain.json      per-paper verification gain C/e/R/Δ
  <paper folder>/datamining/04c_errata.json                 genuine error table (Q1/Q2)
  <corpus>/_ledgers/<corpus>_contradiction_master.json      all candidates merged (after normalization)
  <corpus>/_ledgers/<corpus>_verification_gain_summary.json cross-paper gain summary

Usage: python step4c_errata.py <paper folder or corpus root>
"""
import json
import os
import re
import sys

# ── caliber constants ─────────────────────────────────────────
SEV_OK = ("A", "B", "C", "D", "E")     # legal severity values (E = other/unclassifiable, also the pending fallback; unknown is retired)
Q_OK = ("Q1", "Q2", "Q3", "Q4")
EV_OK = ("text-vs-figure", "text-vs-table", "text-vs-text", "figure-vs-table")
CONSISTENT_KEYS = ("consistent", "dual_source", "dual-source", "within_tolerance", "一致", "相符")


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def _is_paper_dir(d):
    return (os.path.isdir(os.path.join(d, "datamining"))
            or os.path.exists(os.path.join(d, "00_full_cleaned.md")))


def papers_of(root):
    """Three-way auto-detection: ① root itself is a paper folder → single; ② root holds paper folders → multiple;
    ③ root/<Bxx>/paper folder (corpus root) → step down one level and rescan."""
    root = os.path.abspath(root)
    if _is_paper_dir(root):
        return [root]
    subs = [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p)) and not p.startswith("_")]
    out = [d for d in subs if _is_paper_dir(d)]
    if out:
        return out
    for bd in subs:                                   # ③ corpus root: root/Bxx/paper folder
        for q in sorted(os.listdir(bd)):
            d = os.path.join(bd, q)
            if os.path.isdir(d) and not q.startswith("_") and _is_paper_dir(d):
                out.append(d)
    return out


def _first(it, *keys):
    for k in keys:
        v = it.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _norm_ev(raw):
    """Normalize the evidence carrier (template placement only; never a classification again)."""
    u = str(raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not u:
        return ""
    if "figure" in u or "image" in u or "图" in u:
        return "figure-vs-table" if "table" in u or "表" in u else "text-vs-figure"
    if "table" in u or "表" in u:
        return "figure-vs-table" if "figure" in u or "图" in u else "text-vs-table"
    if "text" in u or "文" in u:
        return "text-vs-text"
    return ""


def _guess_ev(raw, ev=""):
    if ev:
        return _norm_ev(ev)
    if str(raw or "").strip().upper() in ("TT", "T-T"):
        return "text-vs-text"
    return _norm_ev(raw)


def _is_consistent(raw, u):
    if any(k in str(raw) for k in CONSISTENT_KEYS):
        return True
    return u.startswith("CONSISTENT") or u.startswith("DUAL") or u.startswith("WITHIN")


def _guess_sev(raw, u):
    """Legacy letter level → severity. The legacy single-letter E was a cross-paper marker (handled by cross_paper) and does not consume a severity letter;
    anything ungradable falls back to E (other · unclassifiable) — `unknown` is retired."""
    if str(raw).strip() and any(k in str(raw) for k in ("一致", "相符", "consistent")):
        return "E"
    if not u:
        return "E"
    if u[0] in "ABCD" and (len(u) == 1 or not u[1].isalnum()):
        return u[0]
    return "E"


def normalize(it):
    """One record → its placement result.

    Returns a dict:
      bucket = corrections (Q1/Q2, go to errata and never into the contradiction store) | consistent (Q4, coverage only)
             | caliber (caliber difference, flagged caliber_variance and never stored) | drop (OCR artifact, removed)
             | ledger (candidate contradiction, goes into the 07 ledger)
      other keys: q / ev / sev / cross / note
    """
    raw = _first(it, "category", "类别", "type")
    u = raw.upper()
    ev = _first(it, "evidence_source", "evidence")
    q = _first(it, "q", "finding_q")
    sev = _first(it, "severity", "严重度")
    cal = bool(it.get("caliber_variance")) or ("口径" in raw)
    cross = bool(it.get("cross_paper"))
    note = ""

    q = q.upper() if re.match(r"^Q[1-4]$", q.upper()) else ""

    if not q:                                   # legacy field migration (only when the new field is absent)
        if u.startswith("OCR") or any(k in raw for k in ("伪影", "artifact")):
            return {"bucket": "drop", "note": "OCR artifact, not a problem in the paper"}
        if u.startswith("E1"):
            q = "Q1"
        elif u.startswith("E2"):
            q = "Q2"
        elif u.startswith("E3"):
            # legacy E3 = figure and text each insist on their own value; variants annotated text-text are placed by their annotation
            _r = raw.lower()
            ev = ev or ("text-vs-text" if ("text-text" in _r or "text_text" in _r or "文文" in raw)
                        else "text-vs-figure")
        elif u.startswith("E5"):
            cal, note = True, "legacy E5 caliber difference → caliber_variance"
        elif _is_consistent(raw, u):
            q = "Q4"
        elif u.startswith("E") and not re.match(r"^E[1-9]", u):
            # single-letter E / E(cross-paper…): the legacy "cross contradiction" → cross-paper flag (consumes no severity letter)
            cross, note = True, "legacy E-level cross contradiction → cross_paper"

    if q in ("Q1", "Q2"):
        return {"bucket": "corrections", "q": q, "ev": _guess_ev(raw, ev)}
    if q == "Q4":
        return {"bucket": "consistent", "q": "Q4"}
    if cal:
        return {"bucket": "caliber", "ev": _guess_ev(raw, ev), "note": note or "caliber difference"}

    if not (len(sev) == 1 and sev.upper() in SEV_OK):
        sev = _guess_sev(raw, u)
    else:
        sev = sev.upper()
    if sev not in SEV_OK:
        sev = "E"                                    # fallback: other · unclassifiable (also pending); unknown is retired
    return {"bucket": "ledger", "sev": sev, "ev": _guess_ev(raw, ev), "cross": cross, "note": note}


def collect(root):
    """Read each paper's 4a/4b ledgers → master (never mutating the original dict; normalized results go to _norm)."""
    master = []
    for paper in papers_of(root):
        p = os.path.basename(paper)
        wf = wf_of(paper)
        for src, f in (("4a", os.path.join(wf, "04_discrepancy_ledger.json")),
                       ("4b", os.path.join(wf, "04b_text_table_contradictions.json"))):
            if not os.path.exists(f):
                continue
            d = json.load(open(f, encoding="utf-8"))
            if isinstance(d, dict):
                if isinstance(d.get("adopted_values_E3"), list):
                    d = [dict(x) for x in d["adopted_values_E3"]]
                else:
                    d = d.get("entries") or d.get("items") or [d]
            for it in d:
                if isinstance(it, str):
                    it = {"finding": it, "category": "text-text"}
                it = dict(it)
                it["source"] = src
                it["paper"] = p
                it["_norm"] = normalize(it)
                master.append(it)
    return master


def finalize(master):
    """Automatic cleanup: remove OCR artifacts. Final severity grading is left for the Step 5B arbiter."""
    return [it for it in master if it["_norm"]["bucket"] != "drop"]


def gen_ledger(root, master):
    """Per-paper 07_contradiction_ledger.json — the candidate contradiction ledger (caliber: severity + evidence_source + the two flags)."""
    by_paper = {}
    for it in master:
        n = it["_norm"]
        if n["bucket"] == "caliber":
            by_paper.setdefault(it.get("paper"), []).append(it)   # caliber differences are listed separately and excluded from total
            continue
        if n["bucket"] != "ledger":
            continue
        by_paper.setdefault(it.get("paper"), []).append(it)
    for paper in papers_of(root):
        p = os.path.basename(paper)
        items, caliber = [], []
        for it in by_paper.get(p, []):
            n = it["_norm"]
            sev = n.get("sev") or ("E" if n["bucket"] != "caliber" else "")   # the ledger is never empty: fall back to E (other · unclassifiable)
            rec = {
                "figure": it.get("figure", "-"),
                "field": it.get("field", it.get("finding", "")),
                "severity": sev,
                "evidence_source": n.get("ev", ""),
                "cross_paper": bool(n.get("cross") or it.get("cross_paper")),
                "text_value": it.get("text_value", ""),
                "image_value": it.get("image_value", it.get("conflict", "")),
                "adopted_value": it.get("adopted_value", ""),
                "adopt_reason": it.get("adopt_reason", it.get("adopted_reason", "")),
                "confidence": it.get("confidence", ""),
                "text_quote": it.get("text_quote", it.get("location", "")),
                "needs_human_review": sev == "E",
                "source": it.get("source", ""),
            }
            if n["bucket"] == "caliber":
                rec["caliber_variance"] = True
                rec["needs_human_review"] = False
                caliber.append(rec)
            else:
                rec["caliber_variance"] = False
                items.append(rec)
        for i, it in enumerate(items, 1):
            it["id"] = i
        by_sev = {}
        for it in items:
            by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
        out = os.path.join(wf_of(paper), "07_contradiction_ledger.json")
        json.dump({
            "paper": p, "total": len(items), "by_severity": by_sev,
            "cross_paper_count": sum(1 for it in items if it["cross_paper"]),
            "caliber_variance_count": len(caliber),
            "caliber": "contradiction_taxonomy v1: severity=A/B/C/D/E (E=other · unclassifiable, also the pending fallback); cross_paper / caliber_variance booleans;"
                       "evidence_source is carrier traceability only; E1/E2/OCR never enter the ledger; grading is backfilled by fenton-arbiter (Step 5B)",
            "generated_by": "step4c_errata.py",
            "entries": items,
            "caliber_annotations": caliber,
        }, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"ledger {p[:42]:44s} candidates {len(items)} {by_sev}"
              f" | cross-paper {sum(1 for it in items if it['cross_paper'])}"
              f" | caliber differences {len(caliber)}")


def gen_errata(root, master):
    """Per-paper 04c_errata.json — **our own extraction errors** (Q1/Q2) found by verification; never stored as contradictions."""
    by_paper = {}
    for it in master:
        n = it["_norm"]
        if n["bucket"] == "corrections" and it.get("paper"):
            by_paper.setdefault(it["paper"], []).append((it, n))
    # 04_internal_corrections.json may also carry entries added beyond the annotation table (with q/before/after fields)
    for paper in papers_of(root):
        p = os.path.basename(paper)
        wf = wf_of(paper)
        items = []
        for it, n in by_paper.get(p, []):
            items.append({
                "figure": it.get("figure", "-"),
                "field": it.get("field", it.get("finding", "")),
                "q": n.get("q", "Q1"),
                "before": it.get("before", it.get("text_value", "")),
                "after": it.get("after", it.get("adopted_value", "")),
                "evidence": it.get("basis", it.get("adopt_reason", it.get("text_quote", ""))),
                "confidence": it.get("confidence", ""),
                "source": it.get("source", ""),
            })
        extra = _load(os.path.join(wf, "04_internal_corrections.json")) or []
        seen = {(x.get("figure"), x.get("field")) for x in items}
        for it in extra if isinstance(extra, list) else []:
            if not isinstance(it, dict):
                continue
            q = str(it.get("q") or "").upper()
            if not re.match(r"^Q[1-2]$", q):
                q = "Q1" if str(it.get("category", "")).upper().startswith("E1") else "Q2"
            rec = {"figure": it.get("figure", "-"), "field": it.get("field", ""), "q": q,
                   "before": it.get("before", ""), "after": it.get("after", ""),
                   "evidence": it.get("basis", ""), "confidence": it.get("confidence", ""),
                   "source": "04_internal_corrections"}
            if (rec["figure"], rec["field"]) in seen:
                continue
            items.append(rec)
        if items:
            out = os.path.join(wf, "04c_errata.json")
            json.dump({"paper": p, "errata_count": len(items),
                       "caliber": "Q1/Q2 our own extraction errors (corrected by verification), not paper contradictions",
                       "errata": items},
                      open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"errata {p[:42]:44s} {len(items)} entries (Q1/Q2)")


def _load(fp, default=None):
    if not os.path.exists(fp):
        return default
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception:
        return default


def compute_gain(paper):
    """Per-paper verification gain: N / checked / Q1-Q4 / fixed / C / e / R / Δ.

    Authority: **Q1/Q2 follow `04_internal_corrections.json`** (the authoritative list of our own extraction errors);
    the annotation table is used only for coverage (checked) and Q3/Q4, falling back to its Q1/Q2 hints only when corrections are absent."""
    wf = wf_of(paper)
    ann = _load(os.path.join(wf, "04_annotation_table.json")) or []
    img = _load(os.path.join(wf, "03_image_data.json")) or []
    cor = _load(os.path.join(wf, "04_internal_corrections.json")) or []
    N = len(img) if isinstance(img, list) else 0
    checked = 0
    qc = {k: 0 for k in Q_OK}
    ann_q12 = 0
    for r in ann if isinstance(ann, list) else []:
        if not isinstance(r, dict):
            continue
        if r.get("checked"):
            checked += 1
        f = r.get("finding") if isinstance(r.get("finding"), dict) else {}
        q = str(f.get("q") or (r.get("discrepancy") or {}).get("q") or r.get("q") or "").upper()
        if q in ("Q3", "Q4"):
            qc[q] += 1
        elif q in ("Q1", "Q2"):
            ann_q12 += 1

    seen = set()
    for it in cor if isinstance(cor, list) else []:
        if not isinstance(it, dict):
            continue
        q = str(it.get("q") or "").upper()
        if not re.match(r"^Q[1-2]$", q):
            q = "Q1" if str(it.get("category", "")).upper().startswith("E1") else "Q2"
        kk = (str(it.get("figure") or ""), str(it.get("field") or ""), str(it.get("before") or ""))
        if kk in seen:
            continue
        seen.add(kk)
        qc[q] += 1
    if not seen and ann_q12:                      # no correction list → fall back to the annotation-table hints
        qc["Q1"] = ann_q12

    fixed = sum(1 for it in (cor if isinstance(cor, list) else [])
                if isinstance(it, dict) and it.get("after") not in (None, ""))
    e = qc["Q1"] + qc["Q2"]
    r_ = round(fixed / e, 4) if e else None
    return {
        "paper": os.path.basename(paper), "N": N, "checked": checked,
        "Q1": qc["Q1"], "Q2": qc["Q2"], "Q3": qc["Q3"], "Q4": qc["Q4"],
        "e": e, "fixed": fixed,
        "C": round(checked / N, 4) if N else None,
        "R": r_, "delta": round(fixed / N, 4) if N else None,
        "caliber": "C=checked/N coverage; e=Q1+Q2 own errors found; R=fixed/e correction rate; delta=fixed/N accuracy lifted by verification",
    }


def gen_gain(root):
    """Per-paper 05b_verification_gain.json + the cross-paper summary."""
    rows = []
    for paper in papers_of(root):
        g = compute_gain(paper)
        rows.append(g)
        json.dump(g, open(os.path.join(wf_of(paper), "05b_verification_gain.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"gain   {g['paper'][:42]:44s} N={g['N']} checked={g['checked']} "
              f"Q1={g['Q1']} Q2={g['Q2']} Q3={g['Q3']} Q4={g['Q4']} Δ={g['delta']}")
    N = sum(r["N"] for r in rows)
    checked = sum(r["checked"] for r in rows)
    e = sum(r["e"] for r in rows)
    fixed = sum(r["fixed"] for r in rows)
    agg = {
        "papers": len(rows), "N": N, "checked": checked,
        "Q1": sum(r["Q1"] for r in rows), "Q2": sum(r["Q2"] for r in rows),
        "Q3": sum(r["Q3"] for r in rows), "Q4": sum(r["Q4"] for r in rows),
        "e": e, "fixed": fixed,
        "C": round(checked / N, 4) if N else None,
        "R": round(fixed / e, 4) if e else None,
        "delta": round(fixed / N, 4) if N else None,
        "per_paper": rows,
        "caliber": "cross-paper verification gain (union over papers): delta = the accuracy lift contributed by verification",
    }
    return agg, rows


def main(root):
    ps = papers_of(root)
    if not ps:
        print(f"No paper folder found: {root}\n(a datamining/ directory or 00_full_cleaned.md is required)")
        return 1
    master = collect(root)
    _ld = (os.path.join(wf_of(ps[0]), "_ledgers")
           if len(ps) == 1 else os.path.join(os.path.normpath(root), "_ledgers"))
    os.makedirs(_ld, exist_ok=True)
    kept = finalize(master)
    bname = os.path.basename(os.path.normpath(root))
    master_fp = os.path.join(_ld, f"{bname}_contradiction_master.json")
    json.dump(kept, open(master_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cats = {}
    for it in kept:
        c = it["_norm"]["bucket"]
        cats[c] = cats.get(c, 0) + 1
    print(f"master ledger {len(kept)} entries → {master_fp}")
    print("placement distribution:", cats, "(ledger=candidate contradiction / caliber=caliber difference / corrections=our errors / consistent=agreement)")

    gen_ledger(root, kept)
    gen_errata(root, kept)
    agg, _ = gen_gain(root)
    if len(ps) > 1:
        json.dump(agg, open(os.path.join(_ld, f"{bname}_verification_gain_summary.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"gain summary → {os.path.join(_ld, bname + '_verification_gain_summary.json')} "
              f"C={agg['C']} e={agg['e']} R={agg['R']} Δ={agg['delta']}")
    print("next: Step 5B — dispatch fenton-arbiter to grade, then step5b_arbitrate_merge.py backfills severity")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
