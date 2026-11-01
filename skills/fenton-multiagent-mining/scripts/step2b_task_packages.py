# -*- coding: utf-8 -*-
"""step2b_task_packages.py — end of Step 4: materialize extraction tasks + pack them (the complete input for the next stage)
Products per paper:
  datamining/02b_extraction_tasks.json       task list (index/statistics)
  datamining/02b_tasks/<xy|basic>/T01/       one pack per task:
    ├─ image.jpg                              the image to extract (original passed through; re-encoded only when over the inline limit)
    └─ task.json                              extraction request (figure/subfigure_ids/mode/description/caption/image path)
Tasks with status=no_image are not packed and never call an LLM.
The panel_index in a pack's task.json is always the **archive panel index** (1-based, i.e. which block within the figure),
not the "index among subfigures included by the work order" — with excluded_subfigures the two differ (e.g. Fig.7 includes a/c/d, where c is block 3).
Usage: python step2b_task_packages.py <corpus root directory> [single paper name...]
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# read_file inline limit (characters). Same source as backend/tools/file_tools.py:IMAGE_INLINE_MAX_CHARS;
# the environment variable C4EAI_IMAGE_INLINE_MAX_CHARS takes precedence, default 1600000 (≈1.2 MB original image).
IMAGE_INLINE_LIMIT_CHARS = int(os.environ.get("C4EAI_IMAGE_INLINE_MAX_CHARS") or 1600000)


def dataurl_chars(path):
    """Character count when a file is inlined as a base64 dataURL (23-character prefix + 4/3 expansion)."""
    return 23 + 4 * ((os.path.getsize(path) + 2) // 3)


def fit_for_inline(src, dst, max_chars=None):
    """Normalize the image to be extracted into a size that read_file can inline, always emitting image.jpg.

    Principle: **never re-encode unless necessary**. Measured 2026-10-05 (local llama-server / Qwen3.6-35B-A3B):
    visual tokens scale only with **pixel count** (1 token ≈ 1024 px = 32×32 patch), independent of JPEG quality
    and byte size — 1451×1283 as q95 JPEG (442 KB) and as lossless PNG (1271 KB) both inline to
    1820 prompt tokens. Lowering quality or downscaling therefore buys no token savings and only loses information.
    The inline limit itself has been raised to 1600000 characters (≈1.2 MB original image), so the current corpus all takes route ①.

    ① the source is jpg and its dataURL is within the limit → copy verbatim (zero re-encoding, zero loss);
    ② the source is png or another format → same-size near-lossless encoding (q95 + 4:4:4, stepping down to q78);
    ③ only if still over the limit, step down the long edge (q92) until it fits.
    Returns (re-encoded?, original bytes, current bytes, current size or None).
    """
    limit = int(max_chars or IMAGE_INLINE_LIMIT_CHARS)
    orig = os.path.getsize(src)
    ext = os.path.splitext(src)[1].lower()
    if ext in (".jpg", ".jpeg") and dataurl_chars(src) <= limit:
        shutil.copyfile(src, dst)
        return False, orig, orig, None
    try:
        from PIL import Image
    except Exception:
        shutil.copyfile(src, dst)
        return False, orig, orig, None
    im = Image.open(src)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    # ② same size, near-lossless (only non-jpg sources reach here)
    for q in (95, 90, 85, 78):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, subsampling=0)
        data = buf.getvalue()
        if 23 + 4 * ((len(data) + 2) // 3) <= limit:
            with open(dst, "wb") as f:
                f.write(data)
            dims = im.size
            im.close()
            return True, orig, len(data), dims
    # ③ still does not fit → only then step down the long edge (preserve resolution first, then downscale)
    best = None
    for long_edge in (2400, 2000, 1600, 1400, 1200, 1000, 900, 800):
        w, h = im.size
        scale = min(1.0, long_edge / max(w, h))
        cur = im if scale >= 1 else im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                              Image.LANCZOS)
        buf = io.BytesIO()
        cur.save(buf, "JPEG", quality=92, optimize=True)
        data = buf.getvalue()
        if best is None or len(data) < len(best[0]):
            best = (data, cur.size)
        if 23 + 4 * ((len(data) + 2) // 3) <= limit:
            with open(dst, "wb") as f:
                f.write(data)
            im.close()
            return True, orig, len(data), cur.size
    data, dims = best
    with open(dst, "wb") as f:
        f.write(data)
    im.close()
    return True, orig, len(data), dims


def wf_of(paper):
    wf = os.path.join(paper, "datamining")
    return wf if os.path.isdir(wf) else paper


def archive_candidates(fid):
    """Figure number → candidate archive folder names.
    Fix 1: an SI figure number (Fig.S1/Figure S1) must map to FigureS1 and must not be mismatched to Figure1 by taking digits only.
    Fix 2 (B34): some archive folder names carry a subfigure letter (Figure4a = the whole a panel of main figure 4),
    so figure_id "Fig.4a" must try both Figure4 and Figure4a."""
    s = (fid or "").strip()
    m = re.match(r"(?i)^(?:fig(?:ure)?\.?|fig\.?)\s*([A-Za-z]*)(\d+)([a-zA-Z]*)$", s)
    if m:
        letters, num, trail = m.groups()
        core = letters + num
        cands = {s, f"Figure{core}", f"Figure {core}", f"Fig.{core}", f"Fig. {core}"}
        if trail:
            cands |= {f"Figure{core}{trail}", f"Figure {core}{trail}", f"Fig.{core}{trail}", f"Fig. {core}{trail}"}
        return cands
    num = "".join(c for c in s if c.isdigit())
    return {s, s.replace(" ", ""), f"Figure{num}", f"Figure {num}"}


def read_caption(wf, fid):
    cands = archive_candidates(fid)
    for sub in cands:
        for fn in (f"{sub}.txt", f"{sub}.TXT"):
            p = os.path.join(wf, "images", sub, fn)
            if os.path.exists(p):
                try:
                    return open(p, encoding="utf-8").read().strip()
                except Exception:
                    pass
    return ""


def caption_number(cap):
    m = re.search(r"(?:Fig(?:ure)?\.?)\s*(\d+)", cap or "", re.IGNORECASE)
    return m.group(1) if m else None


def find_original(wf, fid):
    cands = archive_candidates(fid)
    img_dir = os.path.join(wf, "images")
    for sub in sorted(os.listdir(img_dir)) if os.path.isdir(img_dir) else []:
        p = os.path.join(img_dir, sub)
        if os.path.isdir(p) and sub in cands:
            fns = sorted(os.listdir(p))
            full = [fn for fn in fns if fn.endswith("_full.jpg")]
            if full:
                return os.path.join(p, full[0])
            for fn in fns:
                if fn.lower().endswith((".jpg", ".jpeg", ".png")):
                    return os.path.join(p, fn)
    return None


def whole_image(wf, fid, panels_arch):
    """Which image a "whole-figure pack" actually takes — never blindly grab the first block of a multi-panel figure.

    Measured incident (2026-10-05): Fig.1 was archived as 5 blocks (including a 93×96 caption fragment),
    and the old logic took panels_arch[0] → the fragment was sent to the extractor as an XRD pattern, so the data were necessarily wrong.
    Image acquisition priority:
      ① images/FigureN/FigureN_full.jpg (the stitched whole figure from archiving, semantically the whole figure of that caption);
      ② with several blocks and no full image, take the **largest** block (the only whole figure or the large page image) and avoid fragments;
      ③ neither available → None (the task becomes no_image, never packed or called).
    """
    orig = find_original(wf, fid)
    if orig and "_full" in os.path.basename(orig):
        return orig
    cands = list(panels_arch)
    if orig and orig not in cands:
        cands.append(orig)
    if not cands:
        return None
    return max(cands, key=os.path.getsize)


def ext_of(path):
    return os.path.splitext(path)[1].lower() or ".jpg"


def read_panel_list(wf, fid):
    """Read the ordered panel list "[-] <file> page=N" from images/FigureN/FigureN.txt → a list of full paths.

    The archive txt produced by Step 2 is the **authoritative and ordered** panel list (order = caption a/b/c order).
    Multi-panel figures (e.g. Figure4 a/b/c) were already rendered as separate images during MinerU archiving, so no white-line splitting is needed;
    an early version degraded here to "one image + all subfigure ids stuffed into one pack", leaving b/c with no image to read.
    """
    cands = archive_candidates(fid)
    img_dir = os.path.join(wf, "images")
    if not os.path.isdir(img_dir):
        return []
    for sub in sorted(os.listdir(img_dir)):
        if sub not in cands:
            continue
        d = os.path.join(img_dir, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".txt"):
                continue
            try:
                txt = open(os.path.join(d, fn), encoding="utf-8").read()
            except Exception:
                continue
            if not txt.strip():
                continue
            # tolerate two panel-line formats: the new step0c archive "  [1] <file> page=N bbox=…" and the old archive "  [-] <file>"
            names = re.findall(r"^\s*\[[-\d]+\]\s+(\S+\.(?:jpg|jpeg|png))", txt, re.M)
            out = [os.path.join(d, n) for n in names if os.path.exists(os.path.join(d, n))]
            if out:
                return out
    return []


def alpha_index(subfigure_id):
    """Single-letter subfigure id (a/b/c…) → panel index (0-based); None when not a single letter."""
    s = str(subfigure_id).strip()
    if len(s) == 1 and s.isalpha():
        return ord(s.lower()) - ord("a")
    return None


def build_and_package(paper):
    paper = os.path.normpath(paper)
    wf = os.path.normpath(wf_of(paper))
    wo = json.load(open(os.path.join(wf, "01_work_order.json"), encoding="utf-8"))
    if not isinstance(wo, dict):
        return None  # skip legacy list-format work orders (e.g. leftovers from C4EAI)
    mp = os.path.join(wf, "02_image_match.json")
    match = {m["figure_id"]: m for m in json.load(open(mp, encoding="utf-8"))} if os.path.exists(mp) else {}

    tasks_root = os.path.join(wf, "02b_tasks")
    if os.path.isdir(tasks_root):
        shutil.rmtree(tasks_root)
    os.makedirs(tasks_root, exist_ok=True)

    tasks = []
    for f in wo.get("figures", []):
        fid = f["figure_id"]
        subs = f.get("subfigures", [])
        cap = read_caption(wf, fid)
        cap_flag = ""
        if cap:
            cn = caption_number(cap)
            wn = "".join(c for c in fid if c.isdigit())
            if cn and cn != wn:
                cap_flag = f"caption number ({cn}) does not match the figure number ({wn})"
        m = match.get(fid)

        # 【changed 2026-10-05】the XY decision moved down to subfigure level: needs_xy_extraction is written inside each subfigure,
        # so one figure may mix both (e.g. Fig.7 a/d are degradation curves while c is a cycling bar chart) → emit xy and basic packs separately.
        if any("needs_xy_extraction" in s for s in subs):
            xy_subs = [s for s in subs if s.get("needs_xy_extraction")]
        else:  # tolerate old work orders: no subfigure carries the field → fall back to the figure-level boolean (old true = all subfigures are XY)
            xy_subs = list(subs) if f.get("needs_xy_extraction") else []
        _xy_ids = {id(s) for s in xy_subs}
        ba_subs = [s for s in subs if id(s) not in _xy_ids]
        idx_of = {}                      # the subfigure's index within this figure (= archive panel index)
        for i, s in enumerate(subs):
            idx_of.setdefault(s.get("subfigure_id"), i)

        panels_arch = read_panel_list(wf, fid)   # the authoritative ordered panel list from archiving

        def panel_of(s, fallback_i=0):
            """Subfigure → panel image path: match by subfigure id name first (B33), then fall back to the panel index.

            The panel index is preferably derived from a single-letter subfigure id ('d' → block 4, matching caption letter order);
            the work order registers only **included** subfigures, so taking the list index directly would shift everything when excluded_subfigures exist.
            When the subfigure id is not a single letter (main / left / numeric), fall back to the list index."""
            sid = s.get("subfigure_id", "")
            letter = "A" if sid == "main" else str(sid).upper()
            k = alpha_index(sid)
            if k is None:
                k = idx_of.get(sid, fallback_i)
            if m and m.get("split_success"):
                panels = {os.path.splitext(p)[0]: p for p in m.get("subfigures", [])}
                name = panels.get(f"{fid}{letter}") or panels.get(f"{fid}{sid}")
                if name is None:
                    lst = sorted(panels.values())
                    name = lst[k] if 0 <= k < len(lst) else None
                return os.path.join(m["split_dir"], name) if name else None
            if panels_arch and 0 <= k < len(panels_arch):
                return panels_arch[k]
            return None

        def panel_no(s, i):
            """Subfigure → **archive panel index** (1-based). Prefer deriving it from the subfigure letter (c → 3),
            and fall back to the included-list index only when that fails (main / numeric id) — the two diverge when subfigures are excluded."""
            k = alpha_index(s.get("subfigure_id"))
            return (k + 1) if k is not None else (idx_of.get(s.get("subfigure_id"), i) + 1)

        def per_panel_ok(group):
            """Whether this group can be unpacked panel by panel: archive panels > 1 and every subfigure yields a valid panel index.

            Only the subfigure ids (a/b/c…) and the total archive panel count matter; "work-order subfigure count == panel count" is not required —
            the work order registers only included subfigures, while excluded panels still occupy positions in the figure (e.g. Fig.7 includes a/c/d but has 4 blocks)."""
            if len(panels_arch) <= 1:
                return False
            for s in group:
                k = alpha_index(s.get("subfigure_id"))
                if k is None or k >= len(panels_arch):
                    return False
            return True

        def add(fid, sids, mode, img, split_ok, descs, source=None, extra=None):
            if source is None:
                source = "split" if (m and m.get("split_success") and mode == "xy" and len(sids) == 1) else ("original" if img else "missing")
            t = {"figure_id": fid, "subfigure_ids": sids, "mode": mode,
                 "image_file": img, "image_source": source,
                 "split_success": bool(m and m.get("split_success")) if mode == "xy" else False,
                 "caption": cap[:150], "caption_flag": cap_flag,
                 "descriptions": descs,
                 "status": "ready" if img else "no_image"}
            if extra:
                t.update(extra)
            tasks.append(t)

        if xy_subs:
            if m and m.get("split_success"):
                for i, s in enumerate(xy_subs):
                    sid = s.get("subfigure_id", f"s{i}")
                    add(fid, [sid], "xy", panel_of(s, i), True, {sid: s.get("description", "")},
                        extra={"panel_index": panel_no(s, i), "archive_panels": len(panels_arch)})
            elif per_panel_ok(xy_subs):
                # archiving already made each panel a separate image (ordered FigureN.txt list) → unpack panel by panel:
                # one image + one subfigure id per pack, panels paired in subfigure letter order
                for i, s in enumerate(xy_subs):
                    sid = s.get("subfigure_id", f"s{i}")
                    add(fid, [sid], "xy", panel_of(s, i), False,
                        {sid: s.get("description", "")}, source="archive_panel",
                        extra={"panel_index": panel_no(s, i), "archive_panels": len(panels_arch)})
            else:
                img = whole_image(wf, fid, panels_arch)
                extra = None
                if len(xy_subs) > 1:
                    extra = {"panel_mismatch": f"the work order declares {len(xy_subs)} XY subfigures but the archive has only {len(panels_arch)} images;"
                                               f" this pack is the whole figure, so identify yourself which subfigure the area belongs to"}
                add(fid, [s.get("subfigure_id", f"s{i}") for i, s in enumerate(xy_subs)], "xy", img,
                    bool(m and m.get("split_success")),
                    {s.get("subfigure_id", f"s{i}"): s.get("description", "") for i, s in enumerate(xy_subs)},
                    source="archive_panel" if panels_arch else ("original" if img else "missing"),
                    extra=extra)

        if ba_subs:
            if per_panel_ok(ba_subs):
                # likewise: a multi-panel basic figure is also unpacked panel by panel (e.g. Figure1 a/b as two XRD patterns)
                # source distinguishes: a panel accepted from 02_split / loose images under images/FigureN used directly
                bsrc = "split_panel" if (m and m.get("split_success")) else "archive_panel"
                for i, s in enumerate(ba_subs):
                    sid = s.get("subfigure_id", f"s{i}")
                    add(fid, [sid], "basic", panel_of(s, i), False,
                        {sid: s.get("description", "")}, source=bsrc,
                        extra={"panel_index": panel_no(s, i), "archive_panels": len(panels_arch)})
            else:
                img = whole_image(wf, fid, panels_arch)
                extra = None
                if len(ba_subs) > 1:
                    extra = {"panel_mismatch": f"the work order declares {len(ba_subs)} basic subfigures but the archive has only {len(panels_arch)} images;"
                                               f" this pack is the whole figure, so identify yourself which subfigure the area belongs to"}
                add(fid, [s.get("subfigure_id", f"s{i}") for i, s in enumerate(ba_subs)], "basic", img, False,
                    {s.get("subfigure_id", f"s{i}"): s.get("description", "") for i, s in enumerate(ba_subs)},
                    source="archive_panel" if panels_arch else ("original" if img else "missing"),
                    extra=extra)

    ready = 0
    cat_n = {"xy": 0, "basic": 0}
    for i, t in enumerate(tasks, 1):
        t["task_id"] = f"T{i:02d}"
        if t["status"] != "ready":
            continue
        ready += 1
        cat = "xy" if t["mode"] == "xy" else "basic"
        cat_n[cat] += 1
        t["category"] = cat
        pkg = os.path.join(tasks_root, cat, t["task_id"])
        os.makedirs(pkg, exist_ok=True)
        img_dst = os.path.join(pkg, "image.jpg")
        # Source fingerprint: compression re-encodes (the md5 always changes), so record "where on the figure this came from" in task.json
        # and let step2c trace it back (otherwise whole-figure/large packs are wrongly judged UNTRACEABLE).
        try:
            t["image_source_name"] = os.path.basename(t["image_file"])
            t["image_source_md5"] = hashlib.md5(open(t["image_file"], "rb").read()).hexdigest()
        except Exception:
            t["image_source_name"], t["image_source_md5"] = "", ""
        _reenc, _ob, _nb, _dims = fit_for_inline(t["image_file"], img_dst)
        t["image_resized"] = bool(_reenc)
        t["image_note"] = ((f"original {_ob // 1024} KB exceeds the inline limit and was re-encoded to {_nb // 1024} KB"
                            + (f"（{_dims[0]}×{_dims[1]}）" if _dims else "") + "。")
                           if _reenc else "")
        t["image_bytes"] = _nb
        t["package"] = pkg
        t["image_in_package"] = img_dst
        orig = find_original(wf, t["figure_id"])
        t["original_image"] = orig or ""
        t["images_archive"] = os.path.join(wf, "images")
        # accept both a bare origin.pdf and *_origin.pdf (the B200 corpus uses the bare name)
        pdfs = [f for f in os.listdir(paper) if f.lower().endswith(".pdf")] if os.path.isdir(paper) else []
        pdfs.sort(key=lambda f: (0 if f.lower() == "origin.pdf" else 1, f))
        t["pdf_path"] = os.path.join(paper, pdfs[0]) if pdfs else ""
        tj = {k: t[k] for k in ("task_id", "figure_id", "subfigure_ids", "mode",
                                 "descriptions", "caption", "caption_flag", "image_source",
                                 "original_image", "images_archive", "pdf_path",
                                 "panel_index", "archive_panels", "panel_mismatch",
                                 "image_bytes", "image_note", "image_resized",
                                 "image_source_name", "image_source_md5") if k in t}
        json.dump(tj, open(os.path.join(pkg, "task.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    out = {"paper": os.path.basename(os.path.normpath(paper)),
           "totals": {"tasks": len(tasks), "calls_planned": ready,
                       "no_image": len(tasks) - ready,
                       "xy_tasks": sum(1 for t in tasks if t["mode"] == "xy" and t["status"] == "ready"),
                       "basic_tasks": sum(1 for t in tasks if t["mode"] == "basic" and t["status"] == "ready"),
                       "caption_flagged": sum(1 for t in tasks if t["caption_flag"])},
           "tasks": tasks}
    json.dump(out, open(os.path.join(wf, "02b_extraction_tasks.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return out


def main(root, only=None):
    tot_calls = tot_xy = 0
    # the user may pass either a single paper folder or a multi-paper directory (2026-10-05 fix: it used to accept only multi-paper directories,
    # silently emitting 0 packs for a single paper — measured and confirmed — so the input caliber now matches step0/step0b).
    root = os.path.abspath(root)
    if os.path.exists(os.path.join(root, "datamining", "01_work_order.json")):
        papers = [root]
    else:
        papers = [os.path.join(root, p) for p in (only or sorted(os.listdir(root)))]
    for paper in papers:
        if not os.path.isdir(paper) or not os.path.exists(os.path.join(paper, "datamining", "01_work_order.json")):
            continue
        r = build_and_package(paper)
        if r is None:
            continue  # skip folders with legacy-format work orders
        t = r["totals"]
        tot_calls += t["calls_planned"]
        tot_xy += t["xy_tasks"]
        print(f"{r['paper'][:44]:46s} packs {t['calls_planned']:2d} (xy {t['xy_tasks']:2d} + basic {t['basic_tasks']:2d})"
              f" no-image {t['no_image']} caption-flagged {t['caption_flagged']}")
    print(f"=== total task packs {tot_calls} (xy panels {tot_xy}) ===")


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test10b"
    main(root, sys.argv[2:] or None)
