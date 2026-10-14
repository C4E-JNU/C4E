# -*- coding: utf-8 -*-
"""子智能体档案（agent profile）—— 用户 2026-10-04 定。

设计（用户原话）：「subagent / xxagent 里面挂对应的技能目录、工具目录，
和运行上下文参数、模型参数等等。不要和主智能体共用。」

三层独立（共享的是**代码**，独立的是**配置与可见性**）：
  ① 技能目录：agents/<name>/skills/（可自带 index.json 自成体系）
     · 该目录存在 → 子智能体只看它，主智能体的 skills/ 完全不进视野
     · 不存在   → 回落全局 skills/，再用档案的 skills 白名单裁剪（skill_view 也校验）
  ② 工具目录：agents/<name>/tools/*.py（动态加载进**该档案的私有 registry**）
     · 全局工具按「只读白名单 ∩ 档案 tools 白名单」取（同一份实现，不复制代码）
     · 私有工具是主智能体没有的能力（如一次读 5 万字的 fulltext_reader）
  ③ 参数：agents/<name>/agent.json
     · model 段：model / base_url / api_key / temperature / max_tokens（可混服务商）
     · params 段：max_rounds / timeout / 三级超时 / read_file_max_chars /
                  tool_result_max_chars / user_context_max_chars / max_images

进程内隔离靠 contextvar：子智能体启动前 set_current_profile()，
file_tools / skill_tools 实时读 —— asyncio 每个 Task 独立拷贝 context，
因此并发派多个不同档案的子智能体互不污染。

档案改动**不需重启后端**（每次派发时重新读盘）；私有工具按 mtime 缓存，
改了 .py 文件下一次派发即生效。
"""
from __future__ import annotations

import contextvars
import importlib.util
import json
import os
import re
import sys

# ── 目录 ────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))          # backend/agent
_BACKEND_DIR = os.path.dirname(_THIS_DIR)                        # backend
_AGENTS_ROOT = os.path.join(_BACKEND_DIR, "agents")              # backend/agents
_GLOBAL_SKILLS = os.path.join(os.path.dirname(_BACKEND_DIR), "skills")   # 项目根/skills

# 子智能体的全局只读工具白名单（与 subagent.SUBAGENT_TOOL_ALLOWLIST 保持一致，
# 这里复制一份是为了避免循环导入；以 subagent 的那份为准，见 sync 注释）
GLOBAL_READONLY_ALLOWLIST = (
    "web_search", "web_extract",
    "read_file", "list_files",
    "shared_search", "search_history",
    "conversation_notes",
    "graph_query",
    "skill_list", "skill_view",
    "mineru_parse",
)

# ── 默认参数（= 旧行为，保证不配档案时零感知）──────────
DEFAULTS = {
    "title": "",
    "description": "",
    "system_prompt": "",          # 【已弃用】仅内置档案/旧档案兜底；目录档案一律用 system.md（优先且唯一载体）
    "skills": None,               # None = 不限制；[] = 无技能；["a","b"] = 白名单
    "skills_dir": "",             # 相对 agents/<name>/；空 = 自动（有 skills/ 就用，否则全局）
    "tools": None,                # None = 全局只读白名单全集；[] = 无工具；list = 裁剪
    "tools_dir": "tools",         # 相对 agents/<name>/
    "allow_write": False,         # 是否解锁写类工具（默认关闭，见 write_tools）
    "write_tools": [],
    "status_line": True,          # 结论末尾附 【STATUS】/【MISSING】/【CONFIDENCE】机器可读行
    "model": {},                  # model / base_url / api_key / temperature / max_tokens
    "params": {
        # None = 「不表态」：轮次/超时沿用主控设置（保证不配档案时零感知）
        # 档案里显式写了才覆盖 —— 也就是子智能体自己的一套时限
        "max_rounds": None,
        "timeout": None,
        "nudge": None, "warn": None, "abort": None,
        # 以下四项是**档案专属**的运行上下文参数（与主控设置无关）
        "read_file_max_chars": 20000,
        "tool_result_max_chars": 4000,
        "tool_result_preview_chars": 2000,
        "user_context_max_chars": 6000,
        "max_images": 3,
    },
}

# ── 内置档案：原 SUBAGENT_ROLE_PROMPTS 三个角色（保持向后兼容）──
BUILTIN_PROFILES = {
    "contradiction": {
        "title": "文献矛盾核查",
        "description": "对单篇已归档文献做内部矛盾清扫，输出分级矛盾台账。",
        "system_prompt": (
            "\n【专属角色·文献矛盾核查】你是文献矛盾核查子智能体，只负责对分配给你的文献做矛盾清扫。"
            "规则：① 只收两类——同一量两个值、结论被自身数据否定；"
            "② 裁决分类 E1(我方读图错)/E2(我方读文错)/E3(文献真矛盾)/E5(口径差异，两值并存)/OCR伪影(剔除)；"
            "③ 阈值：数值差<5%量程(标量<1%相对差)判一致取正文值，曲线终点±2%量程内一致——宁缺毋滥；"
            "④ 不报：舍入/修约、口径差异、模糊表述(about/nearly)、术语漂移；"
            "⑤ 每条给 text_quote 原句、两个值、adopted_value(正文自相矛盾时取多源佐证方)与 confidence；"
            "⑥ 表格数值以 TableN.txt 为准，图注以 FigureN.txt 为准。"
        ),
    },
    "extraction": {
        "title": "数据提取",
        "description": "按统一 schema 抽取定量数据（浓度/投加量/温度/时间/k）。",
        "system_prompt": (
            "\n【专属角色·数据提取】你是文献数据提取子智能体，按统一 schema 抽取定量数据。"
            "单位铁律：浓度 mg/L、投加量 g/L、温度 ℃、时间 min、k min⁻¹；"
            "只抽有明确出处的数值，每个值带 text_quote 原句与章节；"
            "图上估读值标注 image_read 并给不确定度；抽不到就标 missing，不要编造。"
        ),
    },
    "verify": {
        "title": "数据核查",
        "description": "对主智能体给出的数据清单逐条回原文核验。",
        "system_prompt": (
            "\n【专属角色·数据核查】你是数据核查子智能体，对主智能体给出的数据清单逐条回到原文核验。"
            "每条输出：原文是否支持(是/否/部分)、text_quote、建议修正值；不确定就标'需人工复核'，不要猜。"
        ),
    },
}

# ── 当前档案（contextvar；asyncio 每 Task 独立拷贝）────
_CURRENT = contextvars.ContextVar("c4eai_agent_profile", default=None)
_SKILLS_DIR = contextvars.ContextVar("c4eai_agent_skills_dir", default="")
_SKILL_ALLOW = contextvars.ContextVar("c4eai_agent_skill_allow", default=None)


def agents_root() -> str:
    return _AGENTS_ROOT


def global_skills_dir() -> str:
    return _GLOBAL_SKILLS


# ══════════════════ 加载与归一化 ══════════════════

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize(raw: dict, name: str) -> dict:
    p = _deep_merge(
        {"name": name, **DEFAULTS},
        {"model": dict(DEFAULTS["model"]), "params": dict(DEFAULTS["params"])},
    )
    p = _deep_merge(p, raw or {})
    p["name"] = name
    p["builtin"] = name in BUILTIN_PROFILES
    p["dir"] = os.path.join(_AGENTS_ROOT, name)
    return p


def load_profiles(include_builtin: bool = True) -> dict:
    """扫描 backend/agents/*/agent.json + 内置档案。返回 {name: profile}。"""
    profs: dict[str, dict] = {}
    if include_builtin:
        for n, raw in BUILTIN_PROFILES.items():
            profs[n] = _normalize(raw, n)
    try:
        entries = sorted(os.listdir(_AGENTS_ROOT))
    except OSError:
        entries = []
    for entry in entries:
        d = os.path.join(_AGENTS_ROOT, entry)
        if not os.path.isdir(d) or entry.startswith("_"):
            continue
        cfg = os.path.join(d, "agent.json")
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            profs[entry] = _normalize({"description": f"⚠️ agent.json 解析失败：{e}"}, entry)
            continue
        if not isinstance(raw, dict):
            continue
        profs[entry] = _normalize(raw, entry)
    return profs


def get_profile(name: str) -> dict:
    """按名取档案。空数组/空串 → 通用档案（等价旧行为）。未知名字 → None。"""
    name = (name or "").strip()
    if not name:
        return _normalize({}, "__generic__")
    return load_profiles().get(name)


def describe_profiles() -> list[dict]:
    """给前端 / 主控看的档案清单（技能、工具、参数一览）。"""
    out = []
    for n, p in sorted(load_profiles().items()):
        tools = p.get("tools")
        out.append({
            "name": n,
            "title": p.get("title") or ("通用" if n == "__generic__" else n),
            "description": p.get("description", ""),
            "builtin": bool(p.get("builtin")),
            "dir": p.get("dir", ""),
            "skills_dir": skills_dir_for(p) or "",
            "skills_dir_own": bool(_own_skills_dir(p)),
            "skills_allow": p.get("skills"),
            "tools_allow": tools,
            "private_tools": [getattr(t, "name", "") for t in load_agent_tools(p)],
            "model": p.get("model") or {},
            "params": p.get("params") or {},
        })
    return out


# ══════════════════ 技能目录 / 工具目录 ══════════════════

def system_prompt_for(profile: dict) -> str:
    """该档案的专属系统提示：agents/<name>/system.md 优先，否则 agent.json 的 system_prompt。"""
    name = profile.get("name") or ""
    if name and not name.startswith("__"):
        p = os.path.join(_AGENTS_ROOT, name, "system.md")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    txt = f.read().strip()
                if txt:
                    return txt
            except Exception:
                pass
    return (profile.get("system_prompt") or "").strip()


def _own_skills_dir(profile: dict) -> str:
    """该档案自带的技能目录（存在才返回，否则 ""）。"""
    name = profile.get("name") or ""
    if not name or name.startswith("__"):
        return ""
    rel = (profile.get("skills_dir") or "").strip() or "skills"
    d = os.path.abspath(os.path.join(_AGENTS_ROOT, name, rel))
    return d if os.path.isdir(d) else ""


def skills_dir_for(profile: dict) -> str:
    """当前档案该用哪个技能根：自带目录优先，否则全局 skills/。"""
    own = _own_skills_dir(profile)
    return own or _GLOBAL_SKILLS


def load_agent_tools(profile: dict) -> list:
    """动态加载 agents/<name>/tools/*.py 里的私有工具（按 mtime 缓存）。

    约定：模块里放 `TOOLS = [tool_obj, ...]`，或定义 `def register(registry): ...`。
    一个文件加载失败只跳过它，不影响其它（绝不因坏插件让子智能体起不来）。
    """
    name = profile.get("name") or ""
    if not name or name.startswith("__"):
        return []
    rel = (profile.get("tools_dir") or "tools").strip() or "tools"
    d = os.path.abspath(os.path.join(_AGENTS_ROOT, name, rel))
    if not os.path.isdir(d):
        return []
    if _BACKEND_DIR not in sys.path:
        sys.path.insert(0, _BACKEND_DIR)
    out: list = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        path = os.path.join(d, fn)
        try:
            mt = os.path.getmtime(path)
            hit = _TOOL_CACHE.get(path)
            if hit and hit[0] == mt:
                out.extend(hit[1])
                continue
            mod = _load_module(path, f"c4eai_priv_{name}_{fn[:-3]}")
            tools = list(getattr(mod, "TOOLS", []) or [])
            reg_fn = getattr(mod, "register", None)
            if callable(reg_fn):
                reg: dict = {}
                reg_fn(reg)
                tools.extend([v for v in reg.values() if hasattr(v, "name")])
            _TOOL_CACHE[path] = (mt, tools)
            out.extend(tools)
        except Exception:
            continue
    return out


_TOOL_CACHE: dict[str, tuple] = {}


def _load_module(path: str, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_registry(profile: dict, base_registry: dict, global_allowlist=None,
                   extra_allowlist=None) -> dict:
    """构造**该档案私有**的工具注册表。

    = 全局只读白名单 ∩ 档案白名单（同一份工具对象，不复制代码） + 私有工具（可覆盖同名）。
    extra_allowlist：默认白名单之外、但**档案可以显式申请**的只读工具
    （如 find_files / search_files —— 定位文件用，仍属只读）。档案没写就不给。
    """
    base = set(global_allowlist or GLOBAL_READONLY_ALLOWLIST)
    extra = set(extra_allowlist or ())
    pick = profile.get("tools")
    if isinstance(pick, list) and pick:
        chosen = [n for n in pick if n in base or n in extra]
    elif pick == []:
        chosen = []
    else:
        chosen = list(base)
    reg = {n: base_registry[n] for n in chosen if n in base_registry}
    if profile.get("allow_write"):
        for n in (profile.get("write_tools") or []):
            if n in base_registry:
                reg[n] = base_registry[n]
    for t in load_agent_tools(profile):
        n = getattr(t, "name", "")
        if n:
            reg[n] = t
    return reg


# ══════════════════ 当前档案上下文（contextvar）══════

def set_current_profile(profile: dict | None):
    """设置当前档案；返回 token 供 reset（子智能体跑完务必 reset）。"""
    p = profile or {}
    tok = _CURRENT.set(p)
    _SKILLS_DIR.set(skills_dir_for(p) if p else "")
    _SKILL_ALLOW.set(p.get("skills") if p else None)
    return tok


def reset_current_profile(token):
    try:
        _CURRENT.reset(token)
    except Exception:
        pass
    try:
        _SKILLS_DIR.set("")
        _SKILL_ALLOW.set(None)
    except Exception:
        pass


def current_profile() -> dict:
    return _CURRENT.get() or {}


def current_skills_dir() -> str:
    return _SKILLS_DIR.get() or ""


def current_skill_allowlist():
    return _SKILL_ALLOW.get()


def current_param(key: str, default=None):
    return (current_profile().get("params") or {}).get(key, default)


def current_read_file_max_chars() -> int:
    try:
        v = int(current_param("read_file_max_chars", 0) or 0)
        return v if v > 0 else 0
    except Exception:
        return 0


# ══════════════════ 结论状态行（评估 + 重派用）══════

STATUS_HINT = (
    "\n【结论状态行·必填】在结论摘要的**最后一行**输出机器可读状态（正文照常写，这行是给主控评估用的）：\n"
    "【STATUS】ok|partial|failed 【MISSING】缺什么（没有就写 none） 【CONFIDENCE】0~1\n"
    "· ok=任务完整完成；partial=部分完成（MISSING 写明缺口）；failed=没完成（MISSING 写原因）。\n"
    "· 不要为了让状态好看而谎报：主控会拿你的 MISSING 决定是否换一个子智能体重做。"
)

_STATUS_RE = re.compile(r"[\[【]\s*STATUS\s*[\]】]\s*([A-Za-z\u4e00-\u9fff_]+)")
_MISSING_RE = re.compile(r"[\[【]\s*MISSING\s*[\]】]\s*([^\n\[【]*)")
_CONF_RE = re.compile(r"[\[【]\s*CONFIDENCE\s*[\]】]\s*([0-9]*\.?[0-9]+)")


def parse_status(summary: str) -> tuple[str, dict]:
    """从结论里抽状态行。返回 (去掉状态行的正文, {status, missing, confidence})。"""
    text = summary or ""
    meta: dict = {}
    m = _STATUS_RE.search(text)
    if m:
        meta["status"] = m.group(1).strip().lower()
    m = _MISSING_RE.search(text)
    if m:
        v = m.group(1).strip()
        if v and v.lower() not in ("none", "无", "n/a", "-"):
            meta["missing"] = v
    m = _CONF_RE.search(text)
    if m:
        try:
            meta["confidence"] = max(0.0, min(1.0, float(m.group(1))))
        except Exception:
            pass
    if meta:
        clean = "\n".join(
            ln for ln in text.splitlines()
            if not (_STATUS_RE.search(ln) or _MISSING_RE.search(ln) or _CONF_RE.search(ln))
        ).strip()
        text = clean or text
    return text, meta
