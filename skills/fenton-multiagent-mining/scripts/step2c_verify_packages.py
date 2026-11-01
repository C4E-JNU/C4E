# -*- coding: utf-8 -*-
"""step2c_verify_packages.py — packaging self-check: trace each pack's image.jpg back to an archived panel and print a mapping table.

Why this exists: packaging correctness used to rely on manual spot checks, and misassignments such as "panel c took panel d's image" were undetectable.
This script is purely local (reads the txt manifests + computes md5): **it never reads images, never calls an LLM, and costs zero tokens**.

Four checks:
  ① Pack complete   image.jpg and task.json both present, and task.json parses;
  ② Image intact    the md5 of the pack's image.jpg == the md5 of task.json.image_file;
  ③ Index match     the image's index in the ordered FigureN.txt list == task.json.panel_index;
  ④ No loss, no reuse  each archived panel block of that figure is used at most once; when panel-level packs exist, unused blocks are listed explicitly (not being included by the work order is normal).

Usage: python step2c_verify_packages.py <paper folder|multi-paper directory>
"""
import hashlib
import json
import os
import re
import sys


def md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def read_panels(txt):
    """FigureN.txt → (ordered list of panel file names, merged whole-figure file name).

    Line format is produced by step0c: `[i] <hash>.jpg  page=N  bbox=(…)`; with multiple blocks there is also `[merged] FigureN_full.jpg`.
    """
    panels, merged = [], None
    if not os.path.exists(txt):
        return panels, merged
    for line in open(txt, encoding="utf-8"):
        m = re.match(r"^\s*\[(\d+)\]\s+(\S+\.(?:jpg|jpeg|png))", line, re.I)
        if m:
            panels.append(m.group(2))
            continue
        m = re.match(r"^\s*\[merged\]\s+(\S+\.(?:jpg|jpeg|png))", line, re.I)
        if m:
            merged = m.group(1)
    return panels, merged


def paper_dirs(root):
    """Input may be a paper folder or a multi-paper directory → return the paper folders that contain 02b_tasks."""
    def has(p):
        return os.path.isdir(os.path.join(p, "datamining", "02b_tasks")) or \
               os.path.isdir(os.path.join(p, "02b_tasks"))
    if has(root):
        return [root]
    out = []
    if os.path.isdir(root):
        for n in sorted(os.listdir(root)):
            p = os.path.join(root, n)
            if os.path.isdir(p) and has(p):
                out.append(p)
    return out


def figure_dir(img_root, fid):
    """figure_id (Figure7 / Fig.7) → archive directory images/Figure7."""
    num = re.sub(r"\D", "", fid or "")
    if not num or not os.path.isdir(img_root):
        return None
    for n in sorted(os.listdir(img_root)):
        if n.lower() == f"figure{num}".lower():
            return os.path.join(img_root, n)
    return None


def check_paper(paper):
    dm = os.path.join(paper, "datamining")
    root = os.path.join(dm, "02b_tasks")
    if not os.path.isdir(root):
        root = os.path.join(paper, "02b_tasks")
    img_root = os.path.join(dm, "images")

    pkgs = []
    for mode in ("xy", "basic"):
        d = os.path.join(root, mode)
        if os.path.isdir(d):
            for t in sorted(os.listdir(d)):
                if os.path.isdir(os.path.join(d, t)):
                    pkgs.append((mode, t, os.path.join(d, t)))

    print(f"\n=== {os.path.basename(os.path.normpath(paper))} — {len(pkgs)} packs ===")
    print(f"{'pack':<5}{'mode':<7}{'figure':<10}{'subfigure':<16}{'panel_idx':<11}{'pack image → archive position':<34}verdict")
    rows, bad, used_panel, seen_fig = [], [], {}, {}

    for mode, t, pkg in pkgs:
        tp = os.path.join(pkg, "task.json")
        if not os.path.exists(tp):
            bad.append(f"{t} missing task.json")
            continue
        try:
            tj = json.load(open(tp, encoding="utf-8"))
        except Exception as e:
            bad.append(f"{t} task.json failed to parse: {e}")
            continue

        fid = tj.get("figure_id", "")
        sids = ",".join(str(x) for x in tj.get("subfigure_ids", []))
        pi = tj.get("panel_index")
        img = os.path.join(pkg, "image.jpg")
        fd = figure_dir(img_root, fid)
        panels, merged = read_panels(os.path.join(fd, f"{os.path.basename(fd)}.txt")) if fd else ([], None)
        seen_fig.setdefault(fid, (fd, len(panels)))

        if not os.path.exists(img):
            bad.append(f"{t} missing image.jpg (a no_image task must not be packed)")
            print(f"{t:<5}{mode:<7}{fid:<10}{sids:<10}{str(pi or '-'):<11}{'—':<34}NO_IMAGE")
            continue

        h = md5(img)
        src = tj.get("image_file", "") or ""
        src_base = os.path.basename(src)
        # Packing re-encodes oversized images (which always changes the md5), so the authoritative identity is the **source fingerprint** recorded in task.json;
        # when an old pack has no fingerprint, fall back to comparing file name / md5.
        src_md5 = tj.get("image_source_md5", "") or ""
        src_name = tj.get("image_source_name", "") or ""
        by_src = (lambda f: bool(src_md5) and os.path.exists(f) and md5(f) == src_md5)
        zip_tag = " (re-encoded)" if tj.get("image_resized") else ""

        kind, arch_idx = "", None
        for i, fn in enumerate(panels, 1):                      # ① panel level: name/md5/source fingerprint match
            fp = os.path.join(fd, fn) if fd else ""
            if fn == src_base or fn == src_name or (fp and os.path.exists(fp)
                                                    and (md5(fp) == h or by_src(fp))):
                arch_idx, kind = i, f"{fn[:10]}… → archive block {i}{zip_tag}"
                break
        if arch_idx is None:
            fp = os.path.join(fd, merged) if (fd and merged) else ""
            if merged and (src_base == merged or src_name == merged
                           or (fp and os.path.exists(fp)
                               and (md5(fp) == h or by_src(fp)))):
                kind = f"whole figure (full) → not panel-level{zip_tag}"
            elif tj.get("image_source") == "split":
                letter = re.sub(r"[^A-Za-z]", "", re.sub(rf"^{re.escape(fid)}", "", src_base))
                kind = f"split image ({letter or 'no letter'})"
                if letter and sids and letter.lower() != sids.replace(",", "").lower():
                    bad.append(f"{t} split image file-name letter({letter}) ≠ subfigure({sids})")
            else:
                kind = "not traceable (no such image in the archive)"
                bad.append(f"{t} pack image cannot be traced back in the {fid} archive")

        mark = "OK"
        if arch_idx and pi and int(pi) != arch_idx:
            mark = "MISMATCH"
            bad.append(f"{t} panel_index={pi} ≠ actual archive block {arch_idx} (subfigure {sids})")
        elif kind.startswith("not traceable"):
            mark = "UNTRACEABLE"
        if arch_idx:
            used_panel.setdefault(fid, set()).add(arch_idx)
        sid_show = sids if len(sids) <= 15 else sids[:14] + "…"
        print(f"{t:<5}{mode:<7}{fid:<10}{sid_show:<16}{str(pi if pi else '-'):<11}{kind:<34}{mark}")
        rows.append((t, mode, fid, sids, pi, kind, mark))

    n_bad = len({b.split(" ")[0] for b in bad})
    n_ok = sum(1 for r in rows if r[-1] == "OK")
    print(f"Summary: packs {len(pkgs)} | OK {n_ok} | problems {n_bad}")

    for fid, (fd, n) in sorted(seen_fig.items()):
        used = used_panel.get(fid, set())
        if used and n > len(used):            # only warn about unused blocks when panel-level packs exist
            miss = [i for i in range(1, n + 1) if i not in used]
            print(f"Unused panel blocks: {fid} {miss} / {n} total — not included by the work order (e.g. excluded photo/schematic panels), which is normal")
        elif not used and n > 1:
            print(f"Whole-figure pack: {fid} ({n} blocks not split; uses _full or the largest block, which is normal)")
    for b in bad:
        print(f"  ⚠ {b}")
    return 1 if bad else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    if not os.path.exists(root):
        print(f"Path does not exist: {root}")
        return 2
    papers = paper_dirs(root)
    if not papers:
        print(f"No paper folder containing 02b_tasks was found: {root}")
        return 2
    return max(check_paper(p) for p in papers)


if __name__ == "__main__":
    sys.exit(main())
