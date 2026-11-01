// ==================== Skill system ====================
// The skill registry lives in skills/index.json. To add a skill:
//   1. Create skills/<name>/SKILL.md
//   2. Add one record to skills/index.json
//
// Note: skill_view / skill_list / skill_write are backend tools (backend/agent/tools.py
// tools_registry) called by the agent directly; the frontend only reads the index for the
// settings checkboxes and registers no frontend tools (the old registerAgentTool code is gone).

// -- Load the skill registry dynamically --
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
    } catch(e) { /* degrade gracefully */ }
    return {};   // Missing means none (the old hardcoded fallback was removed; skills/index.json is the only source)
}

// Cache
let _skillsIndexCache = null;

async function getSkillsIndex() {
    if (!_skillsIndexCache) {
        _skillsIndexCache = await loadSkillsIndex();
    }
    return _skillsIndexCache;
}

// -- Expose to the global scope --
window.getSkillsIndex = getSkillsIndex;