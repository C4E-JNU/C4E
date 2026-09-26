#!/usr/bin/env python3
"""MinerU v4 批量解析封装（供 C4EAI 智能体工具 mineru_parse 调用）。
基于用户提供的 MinerU v4 批量脚本逻辑（用 requests）：
  官方 v4 接口：
    POST /api/v4/file-urls/batch  申请预签名上传 URL
    PUT  预签名 URL               逐文件上传（data=打开的文件对象）
    GET  /api/v4/extract-results/batch/{batch_id}  轮询结果
    下载 full_zip_url → 同名 .zip
  支持单文件或文件夹（递归 .pdf），自动分批（BATCH_SIZE=50）。
  token 从后端配置读取（配置面板保存，不硬编码）。
"""
import json
import os
import re
import time
import uuid
import requests

BASE_URL = "https://mineru.net"
FILE_URLS_ENDPOINT = BASE_URL + "/api/v4/file-urls/batch"
RESULTS_ENDPOINT = BASE_URL + "/api/v4/extract-results/batch"
BATCH_SIZE = 50
MAX_POLL = 60
POLL_INTERVAL = 10
UPLOAD_TIMEOUT = 120
HTTP_TIMEOUT = 30

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace", "mineru_config.json")
_ALT_CONFIG = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mineru_config.json"),
               os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace", "mineru_config.json")]


def load_token():
    """从后端配置读取 MinerU API token。"""
    for path in [CONFIG_PATH] + _ALT_CONFIG:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if cfg.get("token"):
                    return cfg["token"].strip()
            except Exception:
                pass
    return os.environ.get("MINERU_TOKEN") or os.environ.get("MINERU_API_KEY")


def save_token(token):
    """把 token 写入后端配置。"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"token": token.strip() if token else ""}, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _generate_data_id(filename):
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
    return f"{base}_{uuid.uuid4().hex[:8]}"[:128]


def _collect_pdfs(root):
    if os.path.isfile(root) and root.lower().endswith(".pdf"):
        return [root]
    if not os.path.isdir(root):
        return []
    out = []
    for dp, _, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(".pdf"):
                out.append(os.path.join(dp, fn))
    return sorted(out)


def _process_chunk(pdf_chunk, token, out_folder):
    files_data = [{"name": os.path.basename(f), "is_ocr": True, "data_id": _generate_data_id(os.path.basename(f))} for f in pdf_chunk]
    data = {"enable_formula": True, "language": "ch", "enable_table": True, "files": files_data}

    # 1. 申请上传 URL
    try:
        res = requests.post(FILE_URLS_ENDPOINT, headers=_headers(token), json=data, timeout=HTTP_TIMEOUT)
        rj = res.json()
    except Exception as e:
        return {"ok": False, "error": f"申请上传URL异常: {e}"}
    if res.status_code != 200 or rj.get("code") != 0:
        return {"ok": False, "error": f"申请上传URL失败 code={res.status_code}: {rj}"}
    batch_id = rj["data"]["batch_id"]
    upload_urls = rj["data"]["file_urls"]

    # 2. PUT 上传（data=文件对象，与用户原代码一致）
    failed_uploads = []
    for i, url in enumerate(upload_urls):
        if i >= len(pdf_chunk):
            break
        fpath = pdf_chunk[i]
        try:
            with open(fpath, "rb") as f:
                r = requests.put(url, data=f, timeout=UPLOAD_TIMEOUT)
            if r.status_code != 200:
                failed_uploads.append({"file": os.path.basename(fpath), "error": f"PUT {r.status_code}"})
        except Exception as e:
            failed_uploads.append({"file": os.path.basename(fpath), "error": str(e)})
    if failed_uploads:
        return {"ok": False, "error": f"部分上传失败: {failed_uploads[:5]}", "batch_id": batch_id}

    # 3. 轮询解析结果
    result_url = f"{RESULTS_ENDPOINT}/{batch_id}"
    results = []
    for _ in range(MAX_POLL):
        try:
            rj = requests.get(result_url, headers=_headers(token), timeout=HTTP_TIMEOUT).json()
            er = (rj.get("data") or {}).get("extract_result") or []
            if er:
                results = er
                done = [x for x in er if x.get("state") == "done"]
                failed = [x for x in er if x.get("state") == "failed"]
                if len(done) + len(failed) == len(er):
                    break
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)

    # 4. 下载每个 done 的 zip
    downloaded, parse_failed = [], []
    for fr in results:
        fname = fr.get("file_name") or ""
        if fr.get("state") == "done" and fr.get("full_zip_url"):
            zname = os.path.join(out_folder, os.path.splitext(fname)[0] + ".zip")
            try:
                r = requests.get(fr["full_zip_url"], timeout=120)
                if r.status_code == 200:
                    with open(zname, "wb") as fz:
                        fz.write(r.content)
                    downloaded.append({"file": fname, "zip": zname})
                else:
                    parse_failed.append({"file": fname, "error": f"下载 {r.status_code}"})
            except Exception as e:
                parse_failed.append({"file": fname, "error": f"下载: {e}"})
        elif fr.get("state") == "failed":
            parse_failed.append({"file": fname, "error": fr.get("err_msg", "解析失败")})

    return {"ok": True, "batch_id": batch_id, "downloaded": downloaded, "failed": parse_failed}


def parse_pdfs(root, out_dir, token=None):
    token = token if token is not None else load_token()
    if not token:
        return {"ok": False, "error": "未配置 MinerU API Token。请在『智能体配置』面板 → MinerU 解析配置 填写并保存。"}
    pdfs = _collect_pdfs(root)
    if not pdfs:
        return {"ok": False, "error": f"未找到 PDF: {root}"}
    os.makedirs(out_dir, exist_ok=True)

    chunks = [pdfs[i:i+BATCH_SIZE] for i in range(0, len(pdfs), BATCH_SIZE)]
    all_downloaded, all_failed, batch_errors = [], [], []
    for ci, chunk in enumerate(chunks, 1):
        r = _process_chunk(chunk, token, out_dir)
        if r.get("ok"):
            all_downloaded += r.get("downloaded", [])
            all_failed += r.get("failed", [])
        else:
            batch_errors.append({"batch": ci, "error": r.get("error")})
    ok = bool(all_downloaded) or not batch_errors
    return {
        "ok": ok,
        "pdf_count": len(pdfs),
        "downloaded": all_downloaded,
        "failed": all_failed,
        "batch_errors": batch_errors,
        "output_dir": out_dir,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="MinerU v4 批量解析：单文件或文件夹")
    ap.add_argument("path", help="PDF 文件或文件夹路径")
    ap.add_argument("--out", default="mineru_output", help="输出目录")
    ap.add_argument("--token", help="(可选) 手动指定 token")
    a = ap.parse_args()
    res = parse_pdfs(a.path, a.out, a.token)
    print(json.dumps(res, ensure_ascii=False, indent=2))
