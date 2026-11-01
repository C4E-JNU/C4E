# -*- coding: utf-8 -*-
"""step5b1_pollutant_align.py — Step 6② entity alignment (pollutants from three sources + catalysts from Neo4j)

【reworked 2026-10-05】Alignment splits into two classes with different calibers:

  Pollutants (finite set, the same substance across papers) — **the goal is to merge nodes**:
    ① query existing Pollutant nodes in Neo4j first (normalized match on name/English name/expanded abbreviation)
       → on a hit, reuse its CID/CAS/InChIKey (consistent across papers and saving network calls; Aura drops idle connections, so this script retries automatically)
    ② on a miss → the two-step PubChem lookup (name→cids→cid properties); if PubChem fails repeatedly → NCI CACTUS as fallback
    `graph_hits` > 1 means the substance has already split into several nodes in the database (merge debt, not an error).

  Catalysts (extremely varied: loading / doping ratio / support all differ) — **nodes are never merged**:
    only `material_family` (the family skeleton, with an algorithm strictly identical to step10_graph_export.py) is computed as the family key,
    and same-family nodes already in the graph are recorded for cross-paper queries that "aggregate by family, distinguish by variant".

Usage:
  python step5b1_pollutant_align.py <paper folder or corpus root>
  python step5b1_pollutant_align.py <path> --no-net     # Neo4j only, no network
  python step5b1_pollutant_align.py <path> --dry-run    # match only, write nothing
Ledger directory: single paper → <paper>/datamining/_ledgers; multi-paper → <corpus root>/_ledgers
Products: <corpus>_pollutant_alignment.json (joined by step10), <corpus>_catalyst_alignment.json, <corpus>_entity_alignment.json
"""
import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request

H = {"User-Agent": "Mozilla/5.0 (research script)"}
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# abbreviation → full-name candidates (tried in order; the first lookup term comes first)
SYNONYMS = {
    "ao7": ["Acid Orange 7", "Orange II", "4-[(2-hydroxynaphthyl)azo]benzenesulfonic acid"],
    "acid orange 7": ["Orange II", "4-[(2-hydroxynaphthyl)azo]benzenesulfonic acid"],
    "orange ii": ["Acid Orange 7"],
    "orange g": ["Orange G"],
    "cip": ["Ciprofloxacin"], "ofx": ["Ofloxacin"], "ofl": ["Ofloxacin"],
    "nor": ["Norfloxacin"], "lev": ["Levofloxacin"], "enr": ["Enrofloxacin"],
    "smx": ["Sulfamethoxazole"], "smz": ["Sulfamethazine"], "sdz": ["Sulfadiazine"],
    "tc": ["Tetracycline"], "otc": ["Oxytetracycline"], "amx": ["Amoxicillin"],
    "mb": ["Methylene blue"], "rhb": ["Rhodamine B"], "mo": ["Methyl orange"],
    "cr": ["Congo red"], "mg": ["Malachite green"], "cv": ["Crystal violet"],
    "bpa": ["Bisphenol A"], "pnp": ["4-Nitrophenol"], "4-np": ["4-Nitrophenol"],
    "2,4-d": ["2,4-Dichlorophenoxyacetic acid"], "2,4-dcp": ["2,4-Dichlorophenol"],
    "4-cp": ["4-Chlorophenol"], "rb5": ["Reactive Black 5"],
    "rb19": ["Reactive Blue 19"], "atz": ["Atrazine"], "dbp": ["Dibutyl phthalate"],
    "cfx": ["Cefalexin"],
}
# legacy Chinese mineral aliases are kept so historical Chinese-written names still map
_MINERAL = {"goethite": "FeOOH", "针铁矿": "FeOOH", "hematite": "Fe2O3", "赤铁矿": "Fe2O3",
            "magnetite": "Fe3O4", "磁铁矿": "Fe3O4", "ferrihydrite": "FeOOH", "水铁矿": "FeOOH"}


# ────────────────────────── basic helpers ──────────────────────────
def load_env(path):
    cfg = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def papers_of(root):
    """Auto-adapt: one paper (root itself is a paper folder) or several (paper folders under root)."""
    root = os.path.abspath(root)
    if os.path.isdir(os.path.join(root, "datamining")) or os.path.exists(
            os.path.join(root, "00_full_cleaned.md")):
        return [root]
    out = []
    for p in sorted(os.listdir(root)):
        d = os.path.join(root, p)
        if not os.path.isdir(d) or p.startswith("_"):
            continue
        if os.path.isdir(os.path.join(d, "datamining")) or os.path.exists(
                os.path.join(d, "00_full_cleaned.md")):
            out.append(d)
    return out


def norm(s):
    """Normalize an entity name: lowercase → drop parenthetical notes → drop concentration/dosage → drop punctuation → collapse whitespace."""
    s = str(s or "").strip().lower()
    s = re.sub(r"[（(][^）)]*[）)]", " ", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*(?:mg|ml|l|g|μg|ug|nm|μm|um|mm|mol|mM|%)\b", " ", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def english_of(raw):
    """Node name (often "Chinese (English, ABBR)") → an English query term; strip a trailing comma plus abbreviation suffix."""
    m = re.search(r"[A-Za-z][A-Za-z0-9 ,\.\-']+", raw)
    en = m.group(0).strip(" ,.-") if m else raw
    return re.sub(r",\s*[A-Z][A-Z0-9\-]{0,5}\s*$", "", en).strip(" ,.-")


def material_family(name):
    """Catalyst name → material family skeleton. **Must match the algorithm in step10_graph_export.py**."""
    s = str(name or "").strip()
    if not s:
        return ""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"^\s*\d+(?:\.\d+)?\s*%\s*", "", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*%", "", s)
    for k, v in _MINERAL.items():
        if re.search(k, s, re.I):
            s = v
            break
    s = re.sub(r"\s*\+\s*", "+", s)
    s = re.sub(r"\s+", " ", s).strip(" +/")
    return s.replace("α-FeOOH", "FeOOH").replace("γ-Fe2O3", "Fe2O3")


# ────────────────────────── network lookup (PubChem → CACTUS) ──────────────────────────
def get(url, timeout=20):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_text(url, timeout=25):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace").strip()


pubchem_fail = 0  # consecutive failure counter; the circuit breaker trips at >=2


def lookup_pubchem(name):
    global pubchem_fail
    if pubchem_fail >= 2:
        raise RuntimeError("PubChem circuit breaker open (consecutive failures)")
    try:
        cids = get("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                   f"{urllib.parse.quote(name)}/cids/JSON")["IdentifierList"]["CID"]
        cid = cids[0]
        time.sleep(0.3)
        p = get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/"
                "property/Title,MolecularFormula,MolecularWeight/JSON"
                )["PropertyTable"]["Properties"][0]
        pubchem_fail = 0
        return {"query": name, "cid": cid, "pubchem_title": p.get("Title"),
                "formula": p.get("MolecularFormula"), "mw": p.get("MolecularWeight"),
                "source": "PubChem", "status": "ok"}
    except Exception:
        pubchem_fail += 1
        raise


def cactus_props(query_name, cand):
    formula = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                       f"{urllib.parse.quote(cand)}/formula")
    mw = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                  f"{urllib.parse.quote(cand)}/mw")
    ik = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                  f"{urllib.parse.quote(cand)}/stdinchikey").replace("InChIKey=", "")
    try:
        cas = get_text("https://cactus.nci.nih.gov/chemical/structure/"
                       f"{urllib.parse.quote(cand)}/cas")
    except Exception:
        cas = ""
    return {"query": query_name, "cid": None, "pubchem_title": cand,
            "formula": formula, "mw": mw, "inchikey": ik, "cas": cas,
            "source": "NCI_CACTUS", "synonym_used": cand if cand != query_name else "",
            "status": "ok",
            "note": "PubChem unreachable; formula/mw/InChIKey come from NCI CACTUS and the CID is left empty pending"}


def lookup(name):
    """PubChem first (expand abbreviations to full names) → CACTUS → raise."""
    cands = [name] + SYNONYMS.get(name.lower(), [])
    pub_err = None
    for q in dict.fromkeys(cands):
        try:
            r = lookup_pubchem(q)
            if q != name:
                r["synonym_used"] = q
            return r
        except Exception as e:
            pub_err = str(e)[:60]
            if pubchem_fail >= 2:
                break
    for q in dict.fromkeys(cands):
        try:
            r = cactus_props(name, q)
            if pub_err:
                r["pubchem_error"] = pub_err
            return r
        except Exception:
            time.sleep(0.5)
            continue
    raise RuntimeError(f"both PubChem and CACTUS failed: {pub_err}")


# ────────────────────────── graph read and index ──────────────────────────
def fetch_graph(env_path, tries=3):
    """Fetch all Pollutant / Catalyst nodes (read once to avoid per-record round trips); Aura idle disconnects are retried."""
    cfg = load_env(env_path)
    if not cfg.get("NEO4J_URI"):
        return None, f"cannot read NEO4J_URI (--env points to neo4j.env, default {env_path})"
    try:
        from neo4j import GraphDatabase
    except Exception as e:
        return None, f"neo4j driver not installed: {e}"
    last = ""
    for i in range(tries):
        try:
            drv = GraphDatabase.driver(cfg["NEO4J_URI"],
                                       auth=(cfg.get("NEO4J_USERNAME", ""),
                                             cfg.get("NEO4J_PASSWORD", "")),
                                       connection_timeout=30)
            with drv.session(database=cfg.get("NEO4J_DATABASE") or None) as s:
                # pollutant nodes are :Entity {label:'Pollutant'} (not a standalone :Pollutant label),
                # so properties(n) fetches everything at once, avoiding references to property keys (cas/inchikey) that may not exist yet.
                raw = s.run("MATCH (n:Entity {label:'Pollutant'}) "
                            "RETURN properties(n) AS props").data()
                pol = []
                for x in raw:
                    r = dict(x.get("props") or {})
                    r.setdefault("nid", r.get("node_id"))
                    r["cid"] = r.get("pubchem_cid")
                    r["ik"] = r.get("inchikey")
                    pol.append(r)
                cat = s.run("MATCH (c:Catalyst) "
                            "RETURN c.node_id AS nid, coalesce(c.name, c.catalyst_name) AS name, "
                            "c.material_family AS fam, c.paper_id AS pid").data()
            drv.close()
            return {"pollutant": pol, "catalyst": cat, "n_retry": i}, None
        except Exception as e:
            last = str(e)[:120]
            time.sleep(1.5)
    return None, f"Neo4j connection failed (retried {tries} times): {last}"


def index_graph(g):
    pol_by, cat_by_name, cat_by_fam = {}, {}, {}
    for r in g["pollutant"]:
        pol_by.setdefault(norm(r.get("name")), []).append(r)
    for r in g["catalyst"]:
        cat_by_name.setdefault(norm(r.get("name")), []).append(r)
        f = norm(r.get("fam"))
        if f:
            cat_by_fam.setdefault(f, []).append(r)
    return pol_by, cat_by_name, cat_by_fam


def match_pollutant(entry, pol_by):
    """① normalized exact → ② bidirectional substring (length ≥4). Returns (match_type, rows)."""
    cands = [norm(entry["query"])]
    cands += [norm(x) for x in SYNONYMS.get(str(entry["query"]).lower(), [])]
    cands += [norm(x) for x in entry.get("raw_names", [])]
    cands = [c for c in dict.fromkeys(cands) if c]
    for c in cands:
        if c in pol_by:
            return "exact", pol_by[c]
    for c in cands:
        if len(c) < 4:
            continue
        rows = []
        for k, v in pol_by.items():
            if k and (c in k or k in c):
                rows += v
        if rows:
            return "substr", rows
    return "", []


def match_catalyst(name, cat_by_name, cat_by_fam):
    """① exact full-name → ② material_family hit. Returns (match_type, family, exact_rows, fam_rows)."""
    n = norm(name)
    fam = material_family(name)
    rows = cat_by_name.get(n, [])
    if rows:
        return "exact", fam, rows, cat_by_fam.get(norm(fam), [])
    frows = cat_by_fam.get(norm(fam), []) if fam else []
    return ("family" if frows else "none"), fam, [], frows


# ────────────────────────── main flow ──────────────────────────
def collect(papers):
    pol, cat = {}, {}
    for paper in papers:
        inv = os.path.join(wf_of(paper), "05_node_inventory.json")
        if not os.path.exists(inv):
            print(f"! no 05_node_inventory.json: {paper}")
            continue
        try:
            d = json.load(open(inv, encoding="utf-8"))
        except Exception as e:
            print(f"FAIL parse error {inv}: {str(e)[:60]}")
            continue
        pname = os.path.basename(os.path.normpath(paper))[:25]
        for e in d.get("pollutants", []) or []:
            raw = str(e.get("name", "")).strip()
            en = english_of(raw)
            if not raw or not en:
                continue
            it = pol.setdefault(en, {"query": en, "raw_names": [], "papers": []})
            if raw not in it["raw_names"]:
                it["raw_names"].append(raw)
            if pname not in it["papers"]:
                it["papers"].append(pname)
        for e in d.get("catalysts", []) or []:
            # tolerate two fenton-node key names: the current schema uses catalyst_name, older ones use name
            name = str(e.get("name") or e.get("catalyst_name") or "").strip()
            if not name:
                continue
            it = cat.setdefault(name, {"name": name, "papers": [], "role": e.get("role", "")})
            if pname not in it["papers"]:
                it["papers"].append(pname)
    return pol, cat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="paper folder or corpus root (auto-detected)")
    ap.add_argument("--env", default=os.path.join(_SCRIPT_DIR, "neo4j.env"))
    ap.add_argument("--no-net", action="store_true", help="Neo4j only, no network")
    ap.add_argument("--dry-run", action="store_true", help="match only, write no products")
    a = ap.parse_args()

    root = os.path.abspath(a.path)
    if not os.path.isdir(root):
        print(f"FAIL not a directory: {root}")
        return 1
    papers = papers_of(root)
    if not papers:
        print(f"FAIL no paper folder found (must contain datamining/05_node_inventory.json): {root}")
        return 1
    single = os.path.isdir(os.path.join(root, "datamining"))
    batch = os.path.basename(os.path.normpath(root))
    ledger = os.path.join(root, "datamining", "_ledgers") if single else os.path.join(root, "_ledgers")

    print(f"{len(papers)} papers ({'single' if single else 'multiple'}) | ledger → {ledger}")
    pol, cat = collect(papers)
    print(f"to align: {len(pol)} pollutants / {len(cat)} catalysts")

    g, err = fetch_graph(a.env)
    if err:
        print(f"✗ {err}")
        print("  note: the graph runs on a remote Neo4j Aura instance and idle connections are reclaimed — this script already retries 3 times;"
              "still failing means rerun later, **never conclude from that failure that the graph holds no data**")
        return 1
    n_retry = g.pop("n_retry")
    print(f"graph read: Pollutant {len(g['pollutant'])} / Catalyst {len(g['catalyst'])}"
          f" (connection retries {n_retry})")
    pol_by, cat_by_name, cat_by_fam = index_graph(g)

    # ── pollutants ──
    pout = {}
    for en, entry in sorted(pol.items()):
        mt, rows = match_pollutant(entry, pol_by)
        if rows:
            r0 = rows[0]
            rec = {"query": en, "status": "ok", "source": "neo4j", "match_type": mt,
                   "cid": r0.get("cid"), "cas": r0.get("cas"), "inchikey": r0.get("ik"),
                   "pubchem_title": r0.get("name"), "matched_node_id": r0.get("nid"),
                   "graph_hits": len(rows),
                   "graph_node_ids": [x.get("nid") for x in rows][:8],
                   "raw_names": entry["raw_names"], "papers": entry["papers"]}
            print(f"OK  {en:30s} [graph {mt}] {r0.get('name','')[:34]}"
                  f"{'  ! in-graph ' + str(len(rows)) + ' same-named nodes (pending merge)' if len(rows) > 1 else ''}")
        elif a.no_net:
            rec = {"query": en, "status": "unresolved", "source": "graph_only",
                   "error": "no Neo4j hit, and --no-net skipped the network lookup",
                   "raw_names": entry["raw_names"], "papers": entry["papers"]}
            print(f"MISS {en:30s} no graph hit (--no-net, network skipped)")
        else:
            try:
                rec = lookup(en)
                rec.update({"raw_names": entry["raw_names"], "papers": entry["papers"]})
                tag = f"CID {rec['cid']}" if rec.get("cid") else f"CACTUS {rec.get('formula','')}"
                print(f"OK  {en:30s} [{rec.get('source')}] {tag}")
            except Exception as e:
                rec = {"query": en, "status": "unresolved", "error": str(e)[:80],
                       "raw_names": entry["raw_names"], "papers": entry["papers"]}
                print(f"ERR {en:30s} {str(e)[:50]}")
        pout[en] = rec
        if rec.get("source") != "neo4j":
            time.sleep(0.4)

    # ── catalysts (nodes are not merged; only compute the family key + find same-family nodes) ──
    cout = {}
    for name, entry in sorted(cat.items()):
        mt, fam, exact_rows, fam_rows = match_catalyst(name, cat_by_name, cat_by_fam)
        hit = exact_rows or fam_rows
        cout[name] = {"name": name, "family": fam, "role": entry.get("role", ""),
                      "match_type": mt, "action": "reuse_family" if hit else "new_family",
                      "matched_node_ids": [x.get("nid") for x in hit][:10],
                      "graph_same_name": len(exact_rows), "graph_same_family": len(fam_rows),
                      "matched_papers": len({x.get("pid") for x in hit if x.get("pid")}),
                      "papers": entry["papers"],
                      "note": "catalysts are never merged; cross-paper aggregation uses family (=material_family)"}
        print(f"{'CAT ' if hit else 'NEW '} {name[:46]:46s} family={fam[:22]:22s} {mt}"
              f"{'  same family ' + str(len(hit)) + ' nodes/' + str(cout[name]['matched_papers']) + ' papers' if hit else ''}")

    n_pol_ok = sum(1 for v in pout.values() if v.get("status") == "ok")
    n_pol_graph = sum(1 for v in pout.values() if v.get("source") == "neo4j")
    n_pol_dup = sum(1 for v in pout.values() if v.get("graph_hits", 0) > 1)
    n_cat_hit = sum(1 for v in cout.values() if v["action"] == "reuse_family")
    print(f"=== pollutant alignment {n_pol_ok}/{len(pout)} (graph hits {n_pol_graph},"
          f" multi-node pending merge {n_pol_dup}) | catalysts {n_cat_hit}/{len(cout)} matched an existing family")

    if a.dry_run:
        print("(--dry-run, no products written)")
        return 0
    os.makedirs(ledger, exist_ok=True)
    f_pol = os.path.join(ledger, f"{batch}_pollutant_alignment.json")
    f_cat = os.path.join(ledger, f"{batch}_catalyst_alignment.json")
    f_all = os.path.join(ledger, f"{batch}_entity_alignment.json")
    json.dump(pout, open(f_pol, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(cout, open(f_cat, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"batch": batch, "papers": len(papers), "ledger": ledger,
               "pollutants": pout, "catalysts": cout,
               "summary": {"pollutant_total": len(pout), "pollutant_ok": n_pol_ok,
                           "pollutant_by_graph": n_pol_graph, "pollutant_dup_in_graph": n_pol_dup,
                           "catalyst_total": len(cout), "catalyst_family_hit": n_cat_hit}},
              open(f_all, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"→ {f_pol}\n→ {f_cat}\n→ {f_all}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
