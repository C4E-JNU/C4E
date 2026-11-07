# -*- coding: utf-8 -*-
"""把 Neo4j 连接信息混淆后写进便携包（不是不可逆哈希）。

用法:
    python scripts/make_neo4j_secret.py            # 读 backend/tools/neo4j_local.py
    python scripts/make_neo4j_secret.py --env path/to/neo4j.env

产物: dist_private/neo4j_secret.py
build_portable.py 会把它作为 backend/tools/neo4j_secret.py 打进 zip。

注意: 这是「可逆混淆」——能挡住解压后直接搜密码的人，挡不住专门逆向的人。
真正的隔离要靠服务端代理，或数据库侧只读账号（Aura Free 不支持多用户）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist_private" / "neo4j_secret.py"
LOCAL_PY = ROOT / "backend" / "tools" / "neo4j_local.py"

# 每次构建随机生成密钥，只嵌进包内模块；仓库里不保留任何可解密材料


def _keystream(key: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(key + i.to_bytes(4, "big")).digest()
        i += 1
    return bytes(out[:n])


def _xor(data: bytes, key: bytes) -> bytes:
    ks = _keystream(key, len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


def _load_local_py(path: Path) -> dict:
    values = {}
    text = path.read_text(encoding="utf-8")
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
        m = re.search(rf'^{name}\s*=\s*["\'](.*?)["\']', text, re.M)
        if m:
            values[name] = m.group(1)
    return {
        "uri": values.get("NEO4J_URI", ""),
        "user": values.get("NEO4J_USERNAME", "neo4j"),
        "password": values.get("NEO4J_PASSWORD", ""),
        "database": values.get("NEO4J_DATABASE", ""),
    }


def _load_env(path: Path) -> dict:
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip().upper()] = v.strip()
    return {
        "uri": cfg.get("NEO4J_URI", ""),
        "user": cfg.get("NEO4J_USERNAME") or cfg.get("NEO4J_USER") or "neo4j",
        "password": cfg.get("NEO4J_PASSWORD", ""),
        "database": cfg.get("NEO4J_DATABASE", ""),
    }


TEMPLATE = '''# -*- coding: utf-8 -*-
"""便携包内置的图谱连接信息（混淆存储，请勿单独转发本文件）。

这不是密码学意义上的保密：解压后直接搜索看不到明文，但专门逆向仍可还原。
如需真正隔离，请改为服务端代理。
"""
import base64
import hashlib
import json

_K = {key_ints}
_S = "{blob}"


def _key():
    return bytes(_K)


def _keystream(n):
    key = _key()
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(key + i.to_bytes(4, "big")).digest()
        i += 1
    return bytes(out[:n])


_raw = base64.b64decode(_S)
_data = json.loads(bytes(a ^ b for a, b in zip(_raw, _keystream(len(_raw)))).decode("utf-8"))

NEO4J_URI = _data.get("uri", "")
NEO4J_USERNAME = _data.get("user", "neo4j")
NEO4J_PASSWORD = _data.get("password", "")
NEO4J_DATABASE = _data.get("database", "")
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    if args.env:
        cfg = _load_env(Path(args.env))
    elif LOCAL_PY.is_file():
        cfg = _load_local_py(LOCAL_PY)
    else:
        print("[ERROR] 找不到 backend/tools/neo4j_local.py，请用 --env 指定")
        return 1

    if not cfg.get("uri") or not cfg.get("password"):
        print("[ERROR] 凭据不完整（uri/password 为空）")
        return 1

    payload = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
    key = os.urandom(32)
    blob = base64.b64encode(_xor(payload, key)).decode("ascii")
    key_ints = ", ".join(str(b) for b in key)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.format(key_ints="bytes([" + key_ints + "])", blob=blob),
                   encoding="utf-8", newline="\n")
    print(f"written {out} ({out.stat().st_size} bytes), uri={cfg['uri']}, user={cfg['user']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())