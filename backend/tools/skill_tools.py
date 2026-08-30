"""
技能读取工具 — skill_list + skill_view
读取 skills/ 目录下的 SKILL.md 和引用文件
"""
import json
import os


def _get_skills_dir():
    """获取 skills 目录的绝对路径"""
    # backend/tools/skill_tools.py → 项目根目录/skills/
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "skills")
    )


async def skill_list():
    """列出所有可用技能"""
    skills_dir = _get_skills_dir()
    index_path = os.path.join(skills_dir, "index.json")

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        return json.dumps({"skills": [], "note": "技能索引文件不存在"})
    except json.JSONDecodeError:
        return json.dumps({"skills": [], "note": "技能索引文件格式错误"})
    except Exception as e:
        return json.dumps({"error": f"读取技能列表失败: {str(e)}"})


async def skill_view(name: str, file_path: str = "SKILL.md"):
    """读取一个技能的完整内容（SKILL.md）或技能中的某个文件"""
    name = name.strip()
    file_path = file_path.strip() or "SKILL.md"

    if not name:
        return json.dumps({"error": "技能名称不能为空"})

    skills_dir = _get_skills_dir()

    # 安全检查：不允许突破 skills/ 目录
    normalized_path = file_path.replace("\\", "/")
    if ".." in normalized_path.split("/"):
        return json.dumps({"error": "不允许读取 skills/ 目录外的文件"})

    # 找到技能的物理路径
    skill_dir = os.path.join(skills_dir, name)
    if not os.path.isdir(skill_dir):
        # 检查 index.json 看看技能是否存在
        return json.dumps({
            "error": f"技能 \"{name}\" 不存在",
            "available": _list_available_skills()
        })

    target_file = os.path.join(skill_dir, normalized_path)
    target_file = os.path.normpath(target_file)

    # 验证仍在 skills_dir 内
    if not target_file.startswith(os.path.normpath(skills_dir) + os.sep):
        return json.dumps({"error": "越权访问"})

    if not os.path.isfile(target_file):
        return json.dumps({"error": f"文件 \"{file_path}\" 不存在"})

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 如果是 SKILL.md，去掉 YAML frontmatter
        if normalized_path == "SKILL.md":
            content = _strip_frontmatter(content)

        return json.dumps({
            "skill": name,
            "file": normalized_path,
            "content": content
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": f"读取技能文件失败: {str(e)}"})


def _strip_frontmatter(content: str) -> str:
    """去掉 YAML frontmatter (--- ... ---)"""
    import re
    content = re.sub(r'^---[\s\S]*?---\n*', "", content).strip()
    return content


# ═══════════════════════════════════════════════
# 技能写入（自我生成技能 / 自改技能）
# ═══════════════════════════════════════════════

def _parse_frontmatter(content: str) -> dict:
    """解析 SKILL.md 的 YAML frontmatter，返回 {name, description, version, ...}"""
    import re
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?', content, re.DOTALL)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip().strip('"\'')
    return meta


def _upsert_skill_index(name: str, meta: dict):
    """在 skills/index.json 中登记或刷新技能条目"""
    skills_dir = _get_skills_dir()
    index_path = os.path.join(skills_dir, "index.json")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"skills": []}
    skills = data.setdefault("skills", [])
    entry = {
        "name": name,
        "description": meta.get("description", ""),
        "version": meta.get("version", "1.0.0"),
    }
    for i, s in enumerate(skills):
        if s.get("name") == name:
            skills[i] = entry
            break
    else:
        skills.append(entry)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def skill_write(name: str, content: str, file_path: str = "SKILL.md"):
    """创建或更新一个技能。

    - name: 技能名（作为 skills/<name>/ 目录名）
    - content: 文件完整内容；写 SKILL.md 时需含 YAML frontmatter
    - file_path: 可选，指定子文件（references/x.md、templates/x、scripts/x.py），默认 SKILL.md
    写 SKILL.md 时会自动更新 skills/index.json。
    """
    name = (name or "").strip()
    file_path = (file_path or "SKILL.md").strip().replace("\\", "/")
    if not name:
        return json.dumps({"error": "技能名称不能为空"})
    if "/" in name or "\\" in name or ".." in name:
        return json.dumps({"error": "非法技能名"})
    if ".." in file_path.split("/"):
        return json.dumps({"error": "非法路径"})

    skills_dir = _get_skills_dir()
    skill_dir = os.path.join(skills_dir, name)
    target = os.path.normpath(os.path.join(skill_dir, file_path))
    if not target.startswith(os.path.normpath(skills_dir) + os.sep):
        return json.dumps({"error": "越权访问"})

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)

    if file_path == "SKILL.md":
        _upsert_skill_index(name, _parse_frontmatter(content))

    return json.dumps({"status": "ok", "skill": name, "file": file_path}, ensure_ascii=False)


def _list_available_skills():
    """从 index.json 获取可用技能列表"""
    skills_dir = _get_skills_dir()
    index_path = os.path.join(skills_dir, "index.json")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [f"{s['name']}: {s.get('description', '')}" for s in data.get("skills", [])]
    except Exception:
        return []
