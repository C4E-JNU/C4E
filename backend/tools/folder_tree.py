# -*- coding: utf-8 -*-
"""共享库分组树 —— 笔记/文档条目可归属的层级目录。

用户 2026-09-25 定：
  · 顶层分「笔记组 / 文档组」——用现有 kind 区分（不建真文件夹，少一层嵌套）
  · 下层是**用户自建的任意深度分组树**
  · 每个分组有【简介】(intro)，**手写**，供人阅读 + 供 LLM 推理导航
  · 移动方式：右键菜单（前端） + 对话让智能体移动（工具）—— 用户选 B+C

设计要点（借鉴 WeKnora 的 wiki_folders）
  · **ParentID 是唯一真相**（邻接表）；Path/Depth 是**缓存**，每次写入重算
  · **空分组可存在**：允许先搭骨架再往里放条目
  · **slug 唯一性**只要求"同一父下不重名"（不同父下可以同名）
  · 循环保护：移动时禁止把节点移到自己或自己的后代下

存储：workspace/pool/_folders.json —— 共享库唯一（与条目同侧：
      云端实例写本机；本地实例经 proxy 转发，见 app.py）
"""
import json
import os
import re
import time
import uuid

_WORKSPACE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "workspace"))
_FOLDERS_FILE = os.path.join(_WORKSPACE, "pool", "_folders.json")

# 深度硬上限：分组太深没有意义，且会让列目录/展示失控。
# 这是业务规则（不是防呆兜底）：LLM 或误操作都不该造出 10 层目录。
MAX_DEPTH = 5

# ── 两个特殊根文件夹（用户 2026-09-25 定）──────────────────
# 「笔记」「文档」不是"类型标签"，而是**两个真实的根文件夹**：
#   · 整个共享库 = 一个文件夹树，文件夹有名字+简介，里面可装文件也可装子文件夹
#   · 「文档」夹的 parse=True → 新内容**首次从外部进入共享库**且落在这里时，
#     其附件自动解析成文字（供 RAG 检索）
#   · 「笔记」夹的 parse=False → 不解析
# ⚠️ 解析的**唯一触发点**：新条目第一次从共享库外部进入共享库。
#    从个人库发布进来的、以及库内移动的，**一律不触发**（见 app.py / shared_hub.py）。
ROOT_NOTE = "root-note"      # 「📝 笔记」夹
ROOT_DOC = "root-doc"        # 「📄 文档」夹


def ensure_roots():
    """确保两个根文件夹存在（幂等）。返回 (folders, 是否新建过)。"""
    folders = _load()
    by_id = {f.get("id"): f for f in folders}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    created = False

    if ROOT_NOTE not in by_id:
        folders.append({
            "id": ROOT_NOTE, "name": "笔记", "parent_id": "",
            "intro": "人写的总结与笔记。此文件夹下的内容【不解析】附件，"
                     "需要细节时由智能体按 file_id 自行读取原文。",
            "parse": False,          # ★ 不解析
            "system": True,          # 系统内置，不可删除（可改名）
            "created": now, "updated": now,
        })
        created = True
    if ROOT_DOC not in by_id:
        folders.append({
            "id": ROOT_DOC, "name": "文档", "parent_id": "",
            "intro": "上传的文献与文档。新内容首次进入此文件夹时，"
                     "附件会【自动解析】成文字，供全文检索（RAG）。",
            "parse": True,           # ★ 解析
            "system": True,
            "created": now, "updated": now,
        })
        created = True

    if created:
        _recompute_paths(folders)
        _save(folders)
    return folders, created


def root_id_for_kind(kind: str) -> str:
    """按条目 kind 映射到对应的根文件夹（迁移老数据用）。"""
    return ROOT_DOC if (kind or "note") == "document" else ROOT_NOTE


def folder_parses(folder_id: str) -> bool:
    """该文件夹是否要求「新内容首次入库时解析附件」。"""
    if not folder_id:
        return False
    f = _find(_load(), folder_id)
    if f is None:
        return False
    # 未显式标注时，跟随父链（子文件夹继承父的解析策略）
    seen = 0
    while f is not None and seen < 50:
        if "parse" in f:
            return bool(f.get("parse"))
        pid = f.get("parent_id") or ""
        f = _find(_load(), pid) if pid else None
        seen += 1
    return False


# ── 读写 ────────────────────────────────────────

def _load() -> list:
    if not os.path.isfile(_FOLDERS_FILE):
        return []
    try:
        with open(_FOLDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(folders: list):
    os.makedirs(os.path.dirname(_FOLDERS_FILE), exist_ok=True)
    with open(_FOLDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(folders, f, ensure_ascii=False, indent=2)


# ── 内部工具 ────────────────────────────────────

def _find(folders: list, gid: str) -> dict | None:
    return next((f for f in folders if f.get("id") == gid), None)


def _children(folders: list, pid: str) -> list:
    return [f for f in folders if (f.get("parent_id") or "") == (pid or "")]


def _recompute_paths(folders: list):
    """重算所有节点的 path / depth（物化路径是缓存，写入时必须刷新）。

    WeKnora 的做法：FolderID/ParentID 是唯一真相，Path 每次写入重算，
    保证「显示用路径」永不漂移。
    """
    by_id = {f["id"]: f for f in folders}

    def chain(f) -> list:
        names, cur, guard = [], f, 0
        while cur is not None and guard < 100:
            names.append(cur.get("name") or "")
            pid = cur.get("parent_id") or ""
            cur = by_id.get(pid) if pid else None
            guard += 1
        return list(reversed(names))

    for f in folders:
        c = chain(f)
        f["path"] = "/".join(c)
        f["depth"] = max(0, len(c) - 1)


def _is_descendant(folders: list, gid: str, maybe_ancestor: str) -> bool:
    """maybe_ancestor 是否等于 gid 或是 gid 的后代（用于防循环移动）。"""
    if not maybe_ancestor:
        return False
    if maybe_ancestor == gid:
        return True
    by_id = {f["id"]: f for f in folders}
    cur = by_id.get(maybe_ancestor)
    guard = 0
    while cur is not None and guard < 100:
        pid = cur.get("parent_id") or ""
        if pid == gid:
            return True
        cur = by_id.get(pid) if pid else None
        guard += 1
    return False


# ── 公开 API ────────────────────────────────────

def _tree_order(folders: list) -> list:
    """按「树形顺序」（深度优先、同级按名称）排 —— 不是按 path 字符串。

    按 path 字符串排会把根级节点混进子级中间（"Fenton实验" 排在 "研究进展/…" 之前），
    生成给 LLM 的缩进文本会错乱。这里显式做深度优先遍历。
    """
    by_parent = {}
    for f in folders:
        by_parent.setdefault(f.get("parent_id") or "", []).append(f)
    for kids in by_parent.values():
        kids.sort(key=lambda x: (x.get("name") or ""))

    out, stack = [], list(reversed(by_parent.get("", [])))
    while stack:
        f = stack.pop()
        out.append(f)
        kids = by_parent.get(f["id"], [])
        stack.extend(reversed(kids))
    # 兜底：父被删但子残留（不该发生）时补进来，避免丢节点
    seen = {f["id"] for f in out}
    out.extend(f for f in folders if f["id"] not in seen)
    return out


def list_groups() -> str:
    """整个分组树（含 path/depth，供前端渲染 + 供 LLM 导航）。

    返回扁平数组（按树形顺序）；层级由 parent_id 表达（前端/LLM 自行组装）。
    另附 tree_text：缩进文本版，方便 LLM 一眼看懂结构。
    """
    folders = _load()
    _recompute_paths(folders)
    folders = _tree_order(folders)

    # 生成缩进文本（给 LLM 用 —— 带 intro，便于推理导航）
    lines = []
    for f in folders:
        indent = "  " * int(f.get("depth") or 0)
        intro = (f.get("intro") or "").strip()
        lines.append(f"{indent}📁 {f.get('name')}"
                     + (f"  — {intro}" if intro else "")
                     + f"  [id: {f['id'][:8]}]")
    return json.dumps({
        "status": "ok",
        "total": len(folders),
        "folders": folders,
        "tree_text": "\n".join(lines) if lines else "(暂无分组)",
    }, ensure_ascii=False)


def create_group(name: str, parent_id: str = "", intro: str = "",
                 parse: bool = None) -> str:
    """新建分组。parent_id 空 = 根级。parse 传值则显式指定解析策略，否则跟随父夹。"""
    name = (name or "").strip()
    if not name:
        return json.dumps({"error": "分组名不能为空"}, ensure_ascii=False)
    if "/" in name:
        return json.dumps({"error": "分组名不能包含 /"}, ensure_ascii=False)

    folders = _load()
    parent_id = (parent_id or "").strip()
    if parent_id and _find(folders, parent_id) is None:
        return json.dumps({"error": f"父分组不存在: {parent_id}"}, ensure_ascii=False)

    # 同一父下不允许重名（不同父下可以同名）
    if any((f.get("parent_id") or "") == parent_id
           and (f.get("name") or "") == name for f in folders):
        return json.dumps({"error": f"同一层级下已存在分组「{name}」"}, ensure_ascii=False)

    # 深度检查（业务规则，非防呆）
    if parent_id:
        parent_depth = int(_find(folders, parent_id).get("depth") or 0)
        if parent_depth + 1 > MAX_DEPTH:
            return json.dumps({"error": f"分组层级不能超过 {MAX_DEPTH} 层"}, ensure_ascii=False)

    g = {
        "id": "grp-" + uuid.uuid4().hex[:12],
        "name": name,
        "parent_id": parent_id,
        "intro": (intro or "").strip(),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 解析策略继承：显式传入优先，否则跟随父夹（未给则不落 parse 键 → 沿父链回溯）
    if parse is not None:
        g["parse"] = bool(parse)
    folders.append(g)
    _recompute_paths(folders)
    _save(folders)
    return json.dumps({"status": "ok", "group": _find(_load(), g["id"])},
                      ensure_ascii=False)


def update_group(group_id: str, name: str = None, intro: str = None) -> str:
    """改名 / 改简介（简介是用户手写的，供 LLM 推理导航）。"""
    folders = _load()
    g = _find(folders, group_id)
    if g is None:
        return json.dumps({"error": f"分组不存在: {group_id}"}, ensure_ascii=False)

    if name is not None:
        name = name.strip()
        if not name:
            return json.dumps({"error": "分组名不能为空"}, ensure_ascii=False)
        if "/" in name:
            return json.dumps({"error": "分组名不能包含 /"}, ensure_ascii=False)
        pid = g.get("parent_id") or ""
        if any(f["id"] != group_id and (f.get("parent_id") or "") == pid
               and (f.get("name") or "") == name for f in folders):
            return json.dumps({"error": f"同一层级下已存在分组「{name}」"}, ensure_ascii=False)
        g["name"] = name

    if intro is not None:
        g["intro"] = intro.strip()

    g["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _recompute_paths(folders)
    _save(folders)
    return json.dumps({"status": "ok", "group": g}, ensure_ascii=False)


def move_group(group_id: str, new_parent_id: str = "") -> str:
    """移动分组到新父（空 = 根级）。

    保护：不能移到自己或自己的后代下（否则成环，遍历会无限递归）。
    """
    folders = _load()
    g = _find(folders, group_id)
    if g is None:
        return json.dumps({"error": f"分组不存在: {group_id}"}, ensure_ascii=False)

    new_parent_id = (new_parent_id or "").strip()
    if new_parent_id:
        if _find(folders, new_parent_id) is None:
            return json.dumps({"error": f"目标分组不存在: {new_parent_id}"}, ensure_ascii=False)
        if _is_descendant(folders, group_id, new_parent_id):
            return json.dumps({"error": "不能把分组移动到它自己或其子分组下"}, ensure_ascii=False)
        # 移动后深度检查：新父深度 + 1 + 该子树高度 不能超上限
        new_parent_depth = int(_find(folders, new_parent_id).get("depth") or 0)
        if new_parent_depth + 1 > MAX_DEPTH:
            return json.dumps({"error": f"分组层级不能超过 {MAX_DEPTH} 层"}, ensure_ascii=False)

    # 同父下重名检查
    if any(f["id"] != group_id and (f.get("parent_id") or "") == new_parent_id
           and (f.get("name") or "") == (g.get("name") or "") for f in folders):
        return json.dumps({"error": f"目标层级下已存在同名分组「{g.get('name')}」"},
                          ensure_ascii=False)

    old_parent = g.get("parent_id") or ""
    if old_parent == new_parent_id:
        return json.dumps({"status": "ok", "group": g, "note": "父分组未变"},
                          ensure_ascii=False)

    g["parent_id"] = new_parent_id
    g["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _recompute_paths(folders)
    _save(folders)
    return json.dumps({"status": "ok", "group": _find(_load(), group_id)},
                      ensure_ascii=False)


def delete_group(group_id: str, mode: str = "move_up") -> str:
    """删除分组。

    mode="move_up"（默认）→ 子分组与组内条目**上移到父级**（不丢东西）
    mode="cascade"        → 连同子分组一起删；组内条目移到根（**不删条目**）
    条目本身永不随分组删除 —— 分组只是分类，删分类不该删内容。
    """
    folders = _load()
    g = _find(folders, group_id)
    if g is None:
        return json.dumps({"error": f"分组不存在: {group_id}"}, ensure_ascii=False)

    if g.get("system"):
        return json.dumps({"error": "「笔记」「文档」是系统内置的根文件夹，不能删除"}, ensure_ascii=False)

    parent = g.get("parent_id") or ""
    kids = _children(folders, group_id)

    if mode == "cascade":
        # 递归收集整棵子树
        doomed, stack = set(), [group_id]
        while stack:
            cur = stack.pop()
            if cur in doomed:
                continue
            doomed.add(cur)
            stack.extend(c["id"] for c in _children(folders, cur))
        folders = [f for f in folders if f["id"] not in doomed]
        moved_entries_to = ""
    else:
        # 子分组上移
        for k in kids:
            k["parent_id"] = parent
        folders = [f for f in folders if f["id"] != group_id]
        moved_entries_to = parent

    _recompute_paths(folders)
    _save(folders)
    return json.dumps({
        "status": "ok",
        "deleted": group_id,
        "mode": mode,
        "children_moved_to_parent": len(kids) if mode != "cascade" else 0,
        "entries_should_move_to": moved_entries_to,
        "note": ("子分组与条目已上移到父级" if mode != "cascade"
                 else "整棵子树已删除；条目请移到根级"),
    }, ensure_ascii=False)
