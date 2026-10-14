# -*- coding: utf-8 -*-
"""step0b_mineru_reparse.py — 步骤一前置补齐：MinerU 缺失产物重解析

场景：论文文件夹缺 MinerU 产物（images/ 散图、full.md、*_content_list.json）时，
自动调用 MinerU v4 批量接口重新解析该篇的 *_origin.pdf，并把缺失内容补齐到论文文件夹。

补齐策略（保真优先，不覆盖已有文件）：
  - 已有 full.md + *_content_list.json 但缺 images/：
      重解析得到新 content_list，按 (page_idx, 页内顺序) 建立 旧hash→新hash 映射，
      把新图重命名为旧文件名后复制进 images/（保证与旧 full.md 引用一致）。
      若新解析哈希与旧哈希完全一致（解析确定性强），直接复制。
      某旧 hash 找不到对应新图 → 打印 WARN（后续步骤该图会走整图兜底）。
  - 缺 full.md 或缺 *_content_list.json：整套采用新解析产物（full.md/content_list/layout/images），
      已有的 _origin.pdf、ground_truth_tree.json 等不动。

Token 来源（依次尝试）：
  1. 环境变量 MINERU_TOKEN / MINERU_API_KEY
  2. C4EAI 项目配置 backend/workspace/mineru_config.json（自动探测常见安装位置）

用法：python step0b_mineru_reparse.py <论文文件夹或批次目录>
"""
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

import requests

BASE_URL = "https://mineru.net"
FILE_URLS_ENDPOINT = BASE_URL + "/api/v4/file-urls/batch"
RESULTS_ENDPOINT = BASE_URL + "/api/v4/extract-results/batch"
MAX_POLL = 90
POLL_INTERVAL = 10

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 本脚本位于 <项目根>/skills/<技能名>/scripts/，上溯三级即项目根
_PROJ_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))

_C4EAI_CANDIDATES = [
    os.path.join(_PROJ_ROOT, "workspace", "mineru_config.json"),
    os.path.join(_PROJ_ROOT, "backend", "workspace", "mineru_config.json"),
]


def load_token():
    for env in ("MINERU_TOKEN", "MINERU_API_KEY"):
        t = os.environ.get(env)
        if t:
            return t.strip()
    for p in _C4EAI_CANDIDATES:
        if os.path.exists(p):
            try:
                cfg = json.load(open(p, encoding="utf-8"))
                if cfg.get("token"):
                    return cfg["token"].strip()
            except Exception:
                pass
    return None


def _pdfs_in(d):
    """目录内 PDF：优先 *_origin.pdf，退化为任意 .pdf（V7 修正：原版只认 *_origin.pdf，
    而大量语料用的是裸 origin.pdf，导致整批被判为「未找到论文文件夹」）"""
    files = sorted(os.listdir(d))
    exact = [f for f in files if f.endswith("_origin.pdf")]
    if exact:
        return exact
    return [f for f in files if f.lower().endswith(".pdf")]


def normalize_input(root):
    """用户给裸 PDF 时：在同级建同名文件夹并【复制】进去（原件不动），返回该文件夹。
    文件夹/批次目录原样返回。"""
    if not (os.path.isfile(root) and root.lower().endswith(".pdf")):
        return root
    stem = os.path.splitext(os.path.basename(root))[0]
    if stem.lower().endswith("_origin"):
        stem = stem[:-7]
    d = os.path.join(os.path.dirname(os.path.abspath(root)), stem)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, os.path.basename(root))
    if not os.path.exists(dst):
        shutil.copy2(root, dst)
    print(f"输入为 PDF → 工作夹 {d}（原件未移动）")
    return d


def papers_in(root):
    """单篇文件夹（含 PDF）或批次目录（遍历子文件夹）"""
    def is_paper(d):
        return any(os.path.isfile(os.path.join(d, f)) for f in _pdfs_in(d))
    if is_paper(root):
        return [root]
    return [os.path.join(root, p) for p in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, p)) and is_paper(os.path.join(root, p))]


def check_missing(paper):
    """返回缺失清单"""
    missing = []
    if not os.path.exists(os.path.join(paper, "full.md")):
        missing.append("full.md")
    cl = [f for f in os.listdir(paper) if f.endswith("_content_list.json")]
    if not cl:
        missing.append("content_list")
    imgs = os.path.join(paper, "images")
    if not (os.path.isdir(imgs) and any(os.scandir(imgs))):
        missing.append("images")
    return missing


def find_origin_pdf(paper):
    cands = _pdfs_in(paper)
    return os.path.join(paper, cands[0]) if cands else None


def mineru_parse_pdf(pdf_path, token, workdir):
    """上传单个 PDF → 轮询 → 下载 zip → 解压到 workdir，返回解压目录或抛异常"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    name = os.path.basename(pdf_path)
    data = {"enable_formula": True, "language": "ch", "enable_table": True,
            "files": [{"name": name, "is_ocr": True, "data_id": name[:120]}]}
    res = requests.post(FILE_URLS_ENDPOINT, headers=headers, json=data, timeout=30)
    rj = res.json()
    if res.status_code != 200 or rj.get("code") != 0:
        raise RuntimeError(f"申请上传URL失败: {rj}")
    batch_id = rj["data"]["batch_id"]
    url = rj["data"]["file_urls"][0]
    with open(pdf_path, "rb") as f:
        r = requests.put(url, data=f, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"上传失败 PUT {r.status_code}")

    result_url = f"{RESULTS_ENDPOINT}/{batch_id}"
    fr = None
    for _ in range(MAX_POLL):
        time.sleep(POLL_INTERVAL)
        try:
            er = requests.get(result_url, headers={"Authorization": f"Bearer {token}"},
                              timeout=30).json().get("data", {}).get("extract_result") or []
            if er and er[0].get("state") in ("done", "failed"):
                fr = er[0]
                break
        except Exception:
            pass
    if fr is None:
        raise RuntimeError("轮询超时")
    if fr.get("state") != "done" or not fr.get("full_zip_url"):
        raise RuntimeError(f"解析失败: {fr.get('err_msg')}")
    zpath = os.path.join(workdir, "result.zip")
    r = requests.get(fr["full_zip_url"], timeout=300)
    with open(zpath, "wb") as fz:
        fz.write(r.content)
    out = os.path.join(workdir, "unzipped")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out)
    return out


def load_image_blocks(cl_path):
    d = json.load(open(cl_path, encoding="utf-8"))
    return [x for x in d if x.get("type") == "image" and x.get("img_path")]


def resolve_bbox_scale(paper, cl_path):
    """确定 content_list bbox 坐标系。
    旧版 MinerU content_list 的 bbox 是 1000x1000 归一化坐标（与 layout.json 图像块
    按 (x/1000*W, y/1000*H) 换算后完全吻合）；新版是 PDF 点距坐标。
    用 layout.json 的图像块 bbox 做基准自动判别，无 layout 时按坐标范围推断。"""
    import fitz
    cl_path = os.path.join(paper, cl_path) if not os.path.isabs(cl_path) else cl_path
    blocks = load_image_blocks(cl_path)
    if not blocks:
        return 1.0, (1.0, 1.0)
    # layout 页面尺寸
    page_size = {}
    lay = os.path.join(paper, "layout.json")
    if os.path.exists(lay):
        try:
            lj = json.load(open(lay, encoding="utf-8"))
            for pg in lj.get("pdf_info", []):
                page_size[pg.get("page_idx")] = pg.get("page_size") or None
        except Exception:
            pass
    pdf_path = find_origin_pdf(paper)
    if pdf_path:
        try:
            pdf = fitz.open(pdf_path)
            for i, pg in enumerate(pdf):
                page_size.setdefault(i, [pg.rect.width, pg.rect.height])
        except Exception:
            pass
    # 假设 H1：bbox 已是页面点距坐标 → 全部块应落在页面内
    def in_page(scale_x, scale_y):
        ok = 0
        for b in blocks:
            ps = page_size.get(b.get("page_idx"))
            if not ps:
                continue
            x0, y0, x1, y1 = b["bbox"]
            if x1 <= ps[0] * 1.05 and y1 <= ps[1] * 1.05:
                ok += 1
        return ok / max(1, len([b for b in blocks if page_size.get(b.get("page_idx"))]))
    if in_page(1.0, 1.0) > 0.9:
        return 1.0, (1.0, 1.0)  # 点距坐标
    return 1000.0, (1000.0, 1000.0)  # 归一化坐标


def merge_images_from_pdf(paper):
    """用旧 content_list 的 bbox 从 origin PDF 裁剪重建 images/（零错配，确定性）"""
    import fitz
    cl = [f for f in os.listdir(paper) if f.endswith("_content_list.json")][0]
    blocks = load_image_blocks(os.path.join(paper, cl))
    norm, (sx, sy) = resolve_bbox_scale(paper, cl)
    pdf_path = find_origin_pdf(paper)
    pdf = fitz.open(pdf_path)
    # 页面实际尺寸（bbox 换算基准）
    page_dims = {i: (pg.rect.width, pg.rect.height) for i, pg in enumerate(pdf)}
    # 若 layout page_size 与 PDF 尺寸不同，以 layout 为准（部分 PDF 有 CropBox 差异）
    lay = os.path.join(paper, "layout.json")
    if os.path.exists(lay):
        try:
            lj = json.load(open(lay, encoding="utf-8"))
            for pg in lj.get("pdf_info", []):
                ps = pg.get("page_size")
                if ps and pg.get("page_idx") in page_dims:
                    page_dims[pg["page_idx"]] = (ps[0], ps[1])
        except Exception:
            pass
    imgs = os.path.join(paper, "images")
    os.makedirs(imgs, exist_ok=True)
    rebuilt, skipped = 0, []
    for b in blocks:
        h = os.path.basename(b["img_path"])
        dst = os.path.join(imgs, h)
        if os.path.exists(dst):
            rebuilt += 1
            continue
        pidx = b.get("page_idx", 0)
        if pidx >= len(pdf):
            skipped.append((h, f"页码 {pidx} 越界"))
            continue
        W, H = page_dims.get(pidx, (pdf[pidx].rect.width, pdf[pidx].rect.height))
        x0, y0, x1, y1 = b["bbox"]
        if norm != 1.0:
            x0, x1 = x0 / sx * W, x1 / sx * W
            y0, y1 = y0 / sy * H, y1 / sy * H
        clip = fitz.Rect(x0, y0, x1, y1)
        if clip.is_empty or clip.width < 5 or clip.height < 5:
            skipped.append((h, "bbox 无效"))
            continue
        pix = pdf[pidx].get_pixmap(clip=clip, dpi=200)
        pix.save(dst)
        rebuilt += 1
    return rebuilt, skipped, len(blocks)


def main(root):
    root = normalize_input(root)
    papers = papers_in(root)
    if not papers:
        print("未找到含 *_origin.pdf 的论文文件夹")
        return
    token = None          # 延迟加载：只有真需要调 MinerU 时才要 token
    any_missing = False
    for paper in papers:
        name = os.path.basename(os.path.normpath(paper))[:44]
        missing = check_missing(paper)
        if not missing:
            print(f"{name:46s} 产物齐全，跳过")
            continue
        any_missing = True
        # 有 full.md + content_list 时：无需 MinerU 重解析，直接用旧 content_list 的
        # bbox 从 origin PDF 裁剪重建 images/（确定性零错配；旧版 bbox 为 1000 归一化坐标）
        if "images" in missing and "full.md" not in missing and "content_list" not in missing:
            rebuilt, skipped, total = merge_images_from_pdf(paper)
            print(f"  从 PDF 裁剪重建 images/ {rebuilt}/{total}，跳过 {len(skipped)} {skipped[:3] if skipped else ''}")
        else:
            if token is None:
                token = load_token()
            if not token:
                print(f"{name:46s} 缺 {missing}，需 MinerU 重解析，但未找到 token"
                      f"（环境变量 MINERU_TOKEN / C4EAI 配置 workspace/mineru_config.json）→ 跳过本篇")
                continue
            pdf = find_origin_pdf(paper)
            print(f"{name:46s} 缺失 {missing}，重解析 {os.path.basename(pdf)} ...")
            with tempfile.TemporaryDirectory(prefix="mineru_reparse_") as td:
                new_dir = mineru_parse_pdf(pdf, token, td)
                need_full = "full.md" in missing
                need_cl = "content_list" in missing
                # 整套缺失 → 直接采用新产物
                if need_full:
                    shutil.copy2(os.path.join(new_dir, "full.md"), os.path.join(paper, "full.md"))
                if need_cl:
                    for f in os.listdir(new_dir):
                        if f.endswith("_content_list.json") and not f.endswith("_v2.json"):
                            shutil.copy2(os.path.join(new_dir, f), os.path.join(paper, f))
                            break
                if need_full:
                    # 整套采用时 layout 也一并补
                    lf = [f for f in os.listdir(new_dir) if f == "layout.json"]
                    if lf and not os.path.exists(os.path.join(paper, "layout.json")):
                        shutil.copy2(os.path.join(new_dir, "layout.json"), os.path.join(paper, "layout.json"))
                    # 新产物整套 images 直接复制
                    nimg = os.path.join(new_dir, "images")
                    if os.path.isdir(nimg):
                        dimg = os.path.join(paper, "images")
                        os.makedirs(dimg, exist_ok=True)
                        for f in os.listdir(nimg):
                            dst = os.path.join(dimg, f)
                            if not os.path.exists(dst):
                                shutil.copy2(os.path.join(nimg, f), dst)
        # 复核
        still = check_missing(paper)
        print(f"  复核：{'仍缺 ' + str(still) if still else '补齐完成 OK'}")
    if not any_missing:
        print("所有论文产物齐全，无需重解析")


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else None
    if not root:
        print("用法：python step0b_mineru_reparse.py <论文文件夹或批次目录>")
        sys.exit(1)
    main(root)
