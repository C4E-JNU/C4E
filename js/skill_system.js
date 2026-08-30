// ==================== Skill 系统 ====================
// 技能注册表在 skills/index.json，新增技能只需：
//   1. 创建 skills/<name>/SKILL.md
//   2. 在 skills/index.json 加一条记录

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

// ── skill_view: 读取 Skill 文件 ──────────────────
registerAgentTool(
    'skill_view',
    '读取一个技能的完整内容（SKILL.md），或技能中的某个文件。技能包含领域知识、操作指引、规则约束。系统已列出所有可用技能的名称和简介。当你不确定如何回答或需要领域专业知识时，先调 skill_list 看有什么技能，再用 skill_view 读取。',
    {
        type: 'object',
        properties: {
            name: {
                type: 'string',
                description: '技能名称，例如: graph-guide'
            },
            file_path: {
                type: 'string',
                description: '（可选）技能内的文件路径。不传则读 SKILL.md。例如: "references/alias.md"'
            }
        },
        required: ['name']
    },
    async function(args, ctx) {
        const name = (args.name || '').trim();
        const filePath = (args.file_path || '').trim() || 'SKILL.md';

        if (!name) return JSON.stringify({ error: '技能名称不能为空' });

        const index = await getSkillsIndex();
        if (!index[name]) {
            return JSON.stringify({
                error: `技能 "${name}" 不存在`,
                available: Object.entries(index).map(([n, d]) => `${n}: ${d}`)
            });
        }

        // 安全检查：只允许读取 skills/<name>/ 目录下的文件
        const normalizedPath = filePath.replace(/\\/g, '/');
        if (normalizedPath.includes('..')) {
            return JSON.stringify({ error: '不允许读取 skills/ 目录外的文件' });
        }

        // 从文件读取（start.bat 模式下可用）
        try {
            const url = `skills/${name}/${normalizedPath}`;
            const resp = await fetch(url);
            if (resp.ok) {
                let text = await resp.text();
                if (normalizedPath === 'SKILL.md') {
                    text = text.replace(/^---[\s\S]*?---\n*/, '').trim();
                }
                return JSON.stringify({ skill: name, file: normalizedPath, content: text });
            }
        } catch(e) { /* 降级 */ }

        if (normalizedPath !== 'SKILL.md') {
            return JSON.stringify({ error: `文件 "${normalizedPath}" 不可用` });
        }

        // 硬编码兜底（graph-guide 的 SKILL.md）
        if (name === 'graph-guide') {
            return JSON.stringify({
                skill: 'graph-guide',
                file: 'SKILL.md',
                content: `## 催化降解图谱查询指引

说明：详细的别名映射表见 references/alias.md，可通过 skill_view("graph-guide", "references/alias.md") 读取。

### 图谱结构
首次使用请先调用 graph_schema() 获取最新结构。
主要节点类型: (Pollutant), (Catalyst), (DegradationMethod)
主要关系: [:DEGRADES] — Pollutant→Catalyst（属性: efficiency, time, ph）

### 别名映射
用 skill_view("graph-guide", "references/alias.md") 查看完整映射表。
常用：环丙沙星/CIP → Ciprofloxacin, 四环素 → Tetracycline
g-C3N4/Bi2WO6/CNBWO → g-C3N4/Bi2WO6, 石墨相氮化碳 → g-C3N4

### 查询策略
分步推理，每步一个工具：
1. graph_schema() 了解结构（仅首次）
2. execute_cypher 写具体查询
   - 模糊搜索: MATCH (n) WHERE n.name CONTAINS $q
   - 精确查询: MATCH (p:Pollutant {name:$name})
   - 展开关系: MATCH (p)-[r]-(c) RETURN p, r, c
3. 结果为空则降级：模糊匹配 → 别名搜索 → 换标签 → 自身知识

### 回答格式
📊 图谱数据 / 💡 自身知识 / 📊+💡 混合
数据用表格（催化剂 | 效率 | 时间 | pH）
保留专业术语英文原文`
            });
        }

        return JSON.stringify({ error: `技能 "${name}" 内容不可用` });
    }
);

// ── 暴露到全局 ──────────────────────────────────
window.getSkillsIndex = getSkillsIndex;
