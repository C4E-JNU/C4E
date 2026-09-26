// ==================== Skill 系统 ====================
// 技能注册表在 skills/index.json，新增技能只需：
//   1. 创建 skills/<name>/SKILL.md
//   2. 在 skills/index.json 加一条记录
//
// 说明：skill_view / skill_list / skill_write 是**后端工具**（backend/agent/tools.py 的
// tools_registry），由智能体直接调用；前端只负责读取技能索引用于配置面板勾选展示，
// 不再注册任何前端工具（旧的 registerAgentTool 死代码已随 js/agent_tools.js 一并删除）。

// ── 动态加载技能注册表 ──────────────────────
async function loadSkillsIndex() {
    try {
        const resp = await fetch('skills/index.json');
        if (resp.ok) {
            const data = await resp.json();
            const index = {};
            for (const s of (data.skills || [])) {
                index[s.name] = s.description;
            }
            if (Object.keys(index).length > 0) {
                return index;
            }
        }
    } catch(e) { /* 降级 */ }
    // 硬编码兜底
    return { 'graph-guide': '催化降解图谱查询指引 — 图谱结构、别名映射、查询策略、回答格式' };
}

// 缓存
let _skillsIndexCache = null;

async function getSkillsIndex() {
    if (!_skillsIndexCache) {
        _skillsIndexCache = await loadSkillsIndex();
    }
    return _skillsIndexCache;
}

// ── 暴露到全局 ──────────────────────────────────
window.getSkillsIndex = getSkillsIndex;