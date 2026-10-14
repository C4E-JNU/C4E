# -*- coding: utf-8 -*-
"""step2b_task_packages.py — V5 步骤②收尾：物化提取任务 + 打包（下一部的完整输入）
每篇产出：
  datamining/02b_extraction_tasks.json       任务清单（索引/统计）
  datamining/02b_tasks/<xy|basic>/T01/       每任务一个包：
    ├─ image.jpg                              待提取图像（原图直通；仅超内联上限才重编码）
    └─ task.json                              提取要求（figure/subfigure_ids/mode/描述/图注/图片路径）
status=no_image 的任务不打包、不调用 LLM。
包内 task.json 的 panel_index 一律是**归档面板序号**（1 起，= 图内第几块），
不是「工单收录子图序号」—— 有 excluded_subfigures 时两者不同（如 Fig.7 收 a/c/d，c 是第 3 块）。
用法：python step2b_task_packages.py <批次根目录> [单篇名...]
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# read_file 内联上限（字符）。与后端 backend/tools/file_tools.py:IMAGE_INLINE_MAX_CHARS 同源，
# 优先读环境变量 C4EAI_IMAGE_INLINE_MAX_CHARS，缺省 1600000（≈1.2 MB 原图）。
IMAGE_INLINE_LIMIT_CHARS = int(os.environ.get("C4EAI_IMAGE_INLINE_MAX_CHARS") or 1600000)


def dataurl_chars(path):
    """文件按 base64 dataURL 内联时的字符数（前缀 23 + 4/3 膨胀）。"""
    return 23 + 4 * ((os.path.getsize(path) + 2) // 3)


def fit_for_inline(src, dst, max_chars=None):
    """把待提取图规范成「read_file 能内联」的体量，统一输出 image.jpg。

    原则：**能不重编码就不重编码**。2026-10-05 实测（本地 llama-server / Qwen3.6-35B-A3B）：
    视觉 token 只与**像素数**成正比（1 token ≈ 1024 px = 32×32 patch），与 JPEG 质量、
    与字节数无关 —— 1451×1283 用 q95 JPEG（442 KB）与 PNG 无损（1271 KB）内联同为
    1820 prompt_tokens。故降质量/缩尺寸换不到任何 token 收益，纯粹丢信息。
    内联上限本身已调高到 1600000 字符（≈1.2 MB 原图），现有语料全部走 ① 原图直通。

    ① 源是 jpg 且原图 dataURL ≤ 上限 → 原样复制（零重编码、零损失）；
    ② 源是 png 等其它格式 → 保尺寸近无损编码（q95 + 4:4:4，逐级退到 q78）；
    ③ 仍超限才逐级缩长边（q92），缩到装得下为止。
    返回 (是否重编码, 原字节, 现字节, 现尺寸或 None)。
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
    # ② 保尺寸、近无损（只有非 jpg 源会走到这里）
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
    # ③ 仍装不下 → 才逐级缩长边（先保分辨率、再缩）
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
    """图号 → 归档文件夹名候选集合。
    修复1：SI 图号（Fig.S1/Figure S1）必须映射 FigureS1，不能只取数字误配到 Figure1。
    修复2（B34）：部分归档文件夹名带子图字母（Figure4a = 主图4的a面板整图），
    figure_id "Fig.4a" 须同时尝试 Figure4 与 Figure4a。"""
    s = (fid or "").strip()
    m = re.match(r"(?i)^(?:fig(?:ure)?\.?|fig\.?)\s*([A-Za-z]*)(\d+)([a-zA-Z]*)$", s)
    if m:
        letters, num, trail = m.groups()
        core = letters + num
        cands = {s, f"Figure{core}", f"Figure {core}", f"Fig.{core}", f"Fig. {core}", f"图{core}"}
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
    """「整图一包」时到底取哪张图 —— 绝不盲取多面板里的第一块。

    实测事故（2026-10-05）：Fig.1 归档成 5 块（含 1 块 93×96 的图注碎片），
    旧逻辑取 panels_arch[0] → 把碎片当 XRD 发给提取员，数据必然全错。
    取图优先级：
      ① images/FigureN/FigureN_full.jpg（归档拼接整图，语义 = 该图注对应的整图）；
      ② 多块且无 full 时，取**最大**的一块（唯一整图或页面大图），避开碎片；
      ③ 都没有 → None（该任务落 no_image，不打包不调用）。
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
    """读 images/FigureN/FigureN.txt 的「[-] <file> page=N」有序面板清单 → 全路径列表。

    归档 txt 由步骤二产出，是**权威且有序**的面板清单（顺序 = 论文图注 a/b/c 顺序）。
    多面板图（如 Figure4 的 a/b/c）在 MinerU 归档时已各自独立成图，无需白线分割；
    早期版本在此处退化为「一张图 + 全部子图 id 塞进一个包」，导致 b/c 无图可读。
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
            # 兼容两种面板行格式：新 step0c 归档「  [1] <file> page=N bbox=…」与旧归档「  [-] <file>」
            names = re.findall(r"^\s*\[[-\d]+\]\s+(\S+\.(?:jpg|jpeg|png))", txt, re.M)
            out = [os.path.join(d, n) for n in names if os.path.exists(os.path.join(d, n))]
            if out:
                return out
    return []


def alpha_index(subfigure_id):
    """单字母子图 id（a/b/c…）→ 面板序号（0 起）；非单字母返回 None。"""
    s = str(subfigure_id).strip()
    if len(s) == 1 and s.isalpha():
        return ord(s.lower()) - ord("a")
    return None


def build_and_package(paper):
    paper = os.path.normpath(paper)
    wf = os.path.normpath(wf_of(paper))
    wo = json.load(open(os.path.join(wf, "01_work_order.json"), encoding="utf-8"))
    if not isinstance(wo, dict):
        return None  # 旧版 list 格式工单(如 C4EAI 残留)跳过
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
                cap_flag = f"图注编号({cn})与图号({wn})不符"
        m = match.get(fid)

        # 【2026-10-05 改】XY 判定下沉到子图级：needs_xy_extraction 写在每个 subfigure 内，
        # 同一张图可混装（如 Fig.7 的 a/d 是降解曲线、c 是循环柱状）→ 分别出 xy 包与 basic 包。
        if any("needs_xy_extraction" in s for s in subs):
            xy_subs = [s for s in subs if s.get("needs_xy_extraction")]
        else:  # 兼容旧工单：子图内均无该字段 → 回退图级布尔（旧 true = 全部子图当 XY）
            xy_subs = list(subs) if f.get("needs_xy_extraction") else []
        _xy_ids = {id(s) for s in xy_subs}
        ba_subs = [s for s in subs if id(s) not in _xy_ids]
        idx_of = {}                      # 子图在本图内的序号（= 归档面板序号）
        for i, s in enumerate(subs):
            idx_of.setdefault(s.get("subfigure_id"), i)

        panels_arch = read_panel_list(wf, fid)   # 归档权威面板清单（有序）

        def panel_of(s, fallback_i=0):
            """子图 → 面板图路径：先按子图 id 命名匹配（B33），失败按面板序号取。

            面板序号优先由单字母子图 id 推导（'d' → 第 4 块，与图注字母序一致）；
            工单只登记**收录**子图，若直接取列表序号，在有 excluded_subfigures 时会整体错位。
            子图 id 非单字母（main / left / 数字）时回退列表序号。
            """
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
            """子图 → **归档面板序号**（1 起）。优先按子图字母推导（c → 3），
            无法推导（main / 数字 id）才回退收录列表序号 —— 有 excluded 子图时两者会错位。"""
            k = alpha_index(s.get("subfigure_id"))
            return (k + 1) if k is not None else (idx_of.get(s.get("subfigure_id"), i) + 1)

        def per_panel_ok(group):
            """该组子图能否逐面板拆包：归档面板 > 1，且每个子图都能推出有效面板序号。

            只看子图 id（a/b/c…）与归档面板总数，不要求「工单子图数 == 面板数」——
            工单只登记收录子图，被 excluded 的面板仍占图内位置（如 Fig.7 收 a/c/d 但面板有 4 块）。
            """
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
                # 归档已把各面板独立成图（FigureN.txt 有序清单）→ 逐面板拆包：
                # 每包 1 张图 + 1 个子图 id，面板按子图字母序对应
                for i, s in enumerate(xy_subs):
                    sid = s.get("subfigure_id", f"s{i}")
                    add(fid, [sid], "xy", panel_of(s, i), False,
                        {sid: s.get("description", "")}, source="archive_panel",
                        extra={"panel_index": panel_no(s, i), "archive_panels": len(panels_arch)})
            else:
                img = whole_image(wf, fid, panels_arch)
                extra = None
                if len(xy_subs) > 1:
                    extra = {"panel_mismatch": f"工单声明 {len(xy_subs)} 个 XY 子图，归档仅 {len(panels_arch)} 张图；"
                                               f"本包为整图，请自行辨认图面属于哪个子图"}
                add(fid, [s.get("subfigure_id", f"s{i}") for i, s in enumerate(xy_subs)], "xy", img,
                    bool(m and m.get("split_success")),
                    {s.get("subfigure_id", f"s{i}"): s.get("description", "") for i, s in enumerate(xy_subs)},
                    source="archive_panel" if panels_arch else ("original" if img else "missing"),
                    extra=extra)

        if ba_subs:
            if per_panel_ok(ba_subs):
                # 同理：basic 多面板也逐面板拆包（如 Figure1 的 a/b 两张 XRD）
                # source 区分：取自 02_split 采纳的面板 / 直接用 images/FigureN 散图
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
                    extra = {"panel_mismatch": f"工单声明 {len(ba_subs)} 个 basic 子图，归档仅 {len(panels_arch)} 张图；"
                                               f"本包为整图，请自行辨认图面属于哪个子图"}
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
        # 来源指纹：压缩会重编码（md5 必变），故把「图上哪儿来的」写进 task.json，
        # step2c 靠它反查（否则整图/大图包会被误判 UNTRACEABLE）。
        try:
            t["image_source_name"] = os.path.basename(t["image_file"])
            t["image_source_md5"] = hashlib.md5(open(t["image_file"], "rb").read()).hexdigest()
        except Exception:
            t["image_source_name"], t["image_source_md5"] = "", ""
        _reenc, _ob, _nb, _dims = fit_for_inline(t["image_file"], img_dst)
        t["image_resized"] = bool(_reenc)
        t["image_note"] = ((f"原图 {_ob // 1024} KB 超出内联上限，已重编码为 {_nb // 1024} KB"
                            + (f"（{_dims[0]}×{_dims[1]}）" if _dims else "") + "。")
                           if _reenc else "")
        t["image_bytes"] = _nb
        t["package"] = pkg
        t["image_in_package"] = img_dst
        orig = find_original(wf, t["figure_id"])
        t["original_image"] = orig or ""
        t["images_archive"] = os.path.join(wf, "images")
        # 裸 origin.pdf 与 *_origin.pdf 都要认（B200 语料用的是裸名）
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
    # 用户可直接给「单篇论文文件夹」或「批次目录」（2026-10-05 修：原来只吃批次目录，
    # 传单篇会静默输出 0 包 —— 已实测踩中，故与 step0/step0b 的入参口径对齐）。
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
            continue  # 跳过旧格式工单的文件夹
        t = r["totals"]
        tot_calls += t["calls_planned"]
        tot_xy += t["xy_tasks"]
        print(f"{r['paper'][:44]:46s} 包 {t['calls_planned']:2d}（xy {t['xy_tasks']:2d} + basic {t['basic_tasks']:2d}）"
              f" 无图{t['no_image']} 图注存疑{t['caption_flagged']}")
    print(f"=== 任务包合计 {tot_calls}（xy 面板 {tot_xy}）===")


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else r"D:\fenton_papers\test10b"
    main(root, sys.argv[2:] or None)
