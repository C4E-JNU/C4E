# -*- coding: utf-8 -*-
"""工作区域（项目文件夹 + 产物文件夹）—— 用户 2026-10-03 定。

模型：每个对话归属一个「工作区域」。区域 = 项目目录（智能体读写开发的主战场）
+ 产物目录（中间文件 / 辅助文件 / 垃圾专用，默认 <项目目录>/output）。

路径路由（file_tools._resolve_safe_path 与 terminal 共用）：
  相对路径                    → 项目目录
  相对路径以 output/ 开头      → 产物目录（剥掉前缀）
  绝对路径                    → 原样使用
  带 .. 逃逸出对应根目录        → 拒绝（在 _resolve_safe_path 的 _within 校验里）

默认工作区域（id="default"）= 旧行为：project_dir=workspace/、
output_dir=workspace/users/<本人>/outputs/ —— 不配的用户零感知。
存储：workspace/users/<身份>/work_areas.json
"""
import json
import os
import time

# 当前请求的工作区域（agent 每次运行前 set_current_area 注入；file_tools 实时读）
_AREA_CURRENT = {}


def _workspace_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspace"))


def _user_dir(username):
    k = "".join(c for c in (username or "guest") if (c.isalnum() or c in "-_@.·"))
    return os.path.join(_workspace_dir(), "users", k or "guest")


def _areas_path(username):
    d = _user_dir(username)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "work_areas.json")


def default_area(username=""):
    return {
        "id": "default",
        "name": "默认工作区域",
        "project_dir": _workspace_dir(),
        "output_dir": os.path.join(_user_dir(username), "outputs"),
        "created_at": 0,
    }


def load_areas(username):
    """用户自建区域（不含虚拟的默认区域）。"""
    try:
        with open(_areas_path(username), encoding="utf-8") as f:
            data = json.load(f)
        return [a for a in data.get("areas", [])
                if isinstance(a, dict) and a.get("project_dir") and a.get("id")]
    except Exception:
        return []


def all_areas(username):
    return [default_area(username)] + load_areas(username)


def save_area(username, name, project_dir, output_dir):
    areas = load_areas(username)
    area = {
        "id": "wa-%d" % int(time.time() * 1000),
        "name": (name or "").strip() or "工作区域",
        "project_dir": os.path.abspath(project_dir),
        "output_dir": os.path.abspath(output_dir or os.path.join(project_dir, "output")),
        "created_at": time.time(),
    }
    areas.append(area)
    p = _areas_path(username)
    tmp = p + ".tmp_e"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"areas": areas}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return area


def get_area(username, area_id):
    for a in load_areas(username):
        if a.get("id") == area_id:
            return a
    return None


def area_for_branch(username, branch_id):
    """对话的 project_id → 工作区域；无 / 失效 → 默认。读 chats.json（延迟导入防循环）。"""
    if branch_id:
        try:
            from tools import user_chats as UC
            raw = json.loads(UC.load_user_chats(username))
            br = ((raw.get("chats") or {}).get("branches") or {}).get(branch_id) or {}
            pid = br.get("project_id")
            if pid and pid != "default":
                a = get_area(username, pid)
                if a:
                    return a
        except Exception:
            pass
    return default_area(username)


def set_current_area(area):
    global _AREA_CURRENT
    _AREA_CURRENT = area if isinstance(area, dict) else {}


def current_area(username=""):
    a = _AREA_CURRENT
    if a and a.get("project_dir"):
        return a
    return default_area(username)


def route(username, rel_path):
    """路由一条相对路径 → 绝对路径（不做逃逸校验，校验在调用方的 _within）。"""
    area = current_area(username)
    norm = (rel_path or "").replace("\\", "/")
    parts = norm.split("/")
    if parts and parts[0].lower() == "output":
        base = area["output_dir"]
        rest = "/".join(parts[1:])
        return os.path.abspath(os.path.join(base, rest)) if rest else os.path.abspath(base)
    return os.path.abspath(os.path.join(area["project_dir"], norm))
