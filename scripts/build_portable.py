# -*- coding: utf-8 -*-
r"""Build the self-contained Windows portable package.

The repository stays source-only. This script combines the source tree with an
already-prepared embedded Python runtime and the embedding-model cache, then
writes a zip that can be attached to a GitHub Release.

Example:
    python scripts/build_portable.py --runtime D:\C4EAI-build\payload
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".git", ".venv", "venv", "workspace", "dist", "python", "model_cache",
    "__pycache__", "_private", "开发中间产物", "outputs", "parse_out",
    "_backup", "_deprecated", ".idea", ".vscode", "node_modules", "graphify-out",
}
SKIP_FILES = {
    "deploy_cloud.py", "C4EAI_hosts设置.bat", "共享库部署指南.md",
    "CHANGELOG.md", "项目架构说明.md", "设计方案-账号存储v2.md",
    "_move_lit.py", "一键打包.bat", "智能体使用指南.html", ".gitignore",
    "自动补",
    "neo4j.env", "run_translate.py", "server.py",
}
SKIP_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp", ".bak")


def tracked_files(source: Path) -> list[Path]:
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(source), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            stderr=subprocess.DEVNULL,
        )
        names = [n for n in raw.decode("utf-8", "replace").split("\0") if n]
        return [source / n for n in names]
    except Exception:
        result = []
        for base, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                result.append(Path(base) / name)
        return result


def wanted(path: Path, source: Path) -> bool:
    rel = path.relative_to(source)
    if any(part in SKIP_DIRS for part in rel.parts):
        return False
    if rel.name in SKIP_FILES:
        return False
    if rel.name.endswith(SKIP_SUFFIXES):
        return False
    return path.is_file()


def add_file(zf: zipfile.ZipFile, path: Path, arcname: str) -> None:
    zf.write(path, arcname.replace("\\", "/"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(ROOT), help="repository root")
    parser.add_argument(
        "--runtime",
        default=os.environ.get("C4EAI_RUNTIME", r"D:\C4EAI-build\payload"),
        help="directory containing python\\ and model_cache\\",
    )
    parser.add_argument("--out", default=str(ROOT / "dist" / "C4EAI-Portable.zip"))
    args = parser.parse_args()

    source = Path(args.source).resolve()
    runtime = Path(args.runtime).resolve()
    out = Path(args.out).resolve()

    if not (runtime / "python" / "python.exe").is_file():
        print(f"[ERROR] python\\python.exe not found under {runtime}", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    files = [p for p in tracked_files(source) if wanted(p, source)]
    print(f"source files: {len(files)}")
    print(f"runtime: {runtime}")
    print(f"output: {out}")

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            add_file(zf, path, str(path.relative_to(source)))

        for top in ("python", "model_cache"):
            base = runtime / top
            if not base.is_dir():
                continue
            print(f"adding {top} ...")
            for base_dir, _dirs, names in os.walk(base):
                for name in names:
                    path = Path(base_dir) / name
                    add_file(zf, path, str(path.relative_to(runtime)))

        launcher = runtime / "C4EAI.exe"
        if launcher.is_file():
            add_file(zf, launcher, "C4EAI.exe")

        # 便携包内置的 Neo4j 凭据（混淆存储，由 scripts/make_neo4j_secret.py 生成）。
        # 源码仓库里没有这个文件；只有构建时才会被打进 zip。
        secret = source / "dist_private" / "neo4j_secret.py"
        if secret.is_file():
            add_file(zf, secret, "backend/tools/neo4j_secret.py")
            print("adding bundled Neo4j credential (obfuscated)")

        readme = (
            "C4EAI portable package\n"
            "======================\n\n"
            "1. Extract this zip to a normal folder on disk.\n"
            "2. Double-click 启动.bat (or C4EAI.exe).\n"
            "3. The browser opens http://127.0.0.1:8080 automatically.\n\n"
            "The bundled python\\ folder contains Python 3.11 and all dependencies.\n"
            "Your data is stored under workspace\\; back it up by copying that folder.\n"
        )
        zf.writestr("使用说明.txt", readme)

    size = out.stat().st_size
    print(f"done: {size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
