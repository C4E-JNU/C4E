// -*- coding: utf-8 -*-
/* C4EAI i18n (2026-10-14): English by default; Chinese can be selected.
   The English copy in HTML is the source (data-i18n / data-i18n-ph / data-i18n-title keys);
   this file stores only the zh table plus bilingual dynamic JS strings (JS has no HTML fallback).
   On first apply the English source is cached in dataset, so switching back and forth restores it. */
(function () {
    var LS_KEY = 'c4eai-lang';
    var lang = 'en';
    try { lang = localStorage.getItem(LS_KEY) || 'en'; } catch (e) {}
    if (lang !== 'zh') lang = 'en';

    var ZH = {
  "theme.classic": "经典蓝",
  "theme.midnight": "深夜黑",
  "theme.emerald": "翡翠绿",
  "theme.warm": "琥珀护眼",
  "theme.cyber": "赛博2077",
  "theme.pink": "樱花粉",
  "theme.mg": "梦境绿",
  "theme.mv": "梦境紫",
  "theme.mp": "梦境粉",
  "theme.mb": "梦境蓝",
  "theme.title": "主题",
  "brand.sub": "江南大学环境计算组",
  "win.min": "最小化",
  "win.max": "最大化",
  "win.close": "关闭",
  "rename.title": "重命名对话",
  "rename.ph": "输入新的对话名称",
  "btn.cancel": "取消",
  "btn.rename": "重命名",
  "db.title": "📚 共享笔记库",
  "nav.sharedNotes": "共享笔记库",
  "nav.wiki": "团队 Wiki",
  "db.f.owner": "按上传者筛选",
  "db.f.year": "按上传年份筛选",
  "db.f.sort": "排序方式",
  "db.sort.new": "🕒 上传时间（新→旧）",
  "db.sort.old": "🕒 上传时间（旧→新）",
  "db.sort.name": "🔤 名称",
  "btn.close": "关闭",
  "db.empty": "暂无条目",
  "set.title": "设置",
  "nav.model": "模型与参数",
  "nav.providers": "模型配置",
  "nav.agent": "智能体",
  "nav.service": "连接与服务",
  "nav.ui": "界面",
  "pane.model": "模型与参数",
  "set.temp": "温度",
  "set.temp.desc": "越低越确定，越高越发散。",
  "set.ctx": "上下文轮次",
  "set.ctx.desc": "保留的对话轮数。",
  "set.maxtokens": "最大输出 Token 数",
  "set.maxtokens.desc": "单次回答的 token 上限，思维链也占额度；DeepSeek 合法范围 1–393216，超上限会被服务端拒收。",
  "set.ctxwin": "模型上下文窗口",
  "set.ctxwin.desc": "模型上下文大小（256K 填 262144，1M 填 1048576）。仅决定何时开始压缩旧工具结果。",
  "set.budget": "清理触发线",
  "set.budget.desc": "达到该估算 token 数时压缩旧工具结果（只动工具结果，正文永不清）。0=自动：(窗口−预留)×75%。",
  "set.expected": "预期输入 Token 数（阈值）",
  "set.expected.desc": "发送给模型的 token 数超过此值时红字警告（仅提示）。",
  "pane.providers": "模型配置",
  "pane.providers.desc": "服务商、API 密钥与模型管理。",
  "pp.config": "模型与服务商配置",
  "pp.delete": "删除服务商",
  "pp.save": "保存",
  "pp.models": "可用模型",
  "pane.agent": "智能体",
  "pa.engine": "Agent 引擎",
  "pa.engine.custom": "自定义流式",
  "pa.engine.custom.d": "裸 API 手写循环 · 显示思考链",
  "pa.engine.lc": "LangChain 流式",
  "pa.engine.lc.d": "框架循环 · 无思考链",
  "pa.rounds": "工具循环轮次",
  "pa.rounds.8": "8 轮",
  "pa.rounds.20": "20 轮",
  "pa.rounds.50": "50 轮",
  "pa.rounds.inf": "无限",
  "pa.sub": "子智能体",
  "pa.sub.enable": "启用子智能体",
  "pa.sub.enable.d": "主智能体可派生子智能体并行处理独立子任务（需勾选 delegate_task 工具）。",
  "pa.sub.feed": "回喂子智能体完整步骤",
  "pa.sub.feed.d": "关闭时主智能体只收到结论摘要（防上下文膨胀）；仅在需逐字复核时打开。",
  "pa.sub.rounds": "轮次/个（0=无限）",
  "pa.sub.rounds.t": "每个子智能体的工具循环上限；0 = 无限",
  "pa.sub.parallel": "并发上限",
  "pa.sub.maxtasks": "最多子任务",
  "pa.sub.legend": "<b>轮次/个</b>=单个子智能体循环上限 · <b>并发上限</b>=同时运行的子智能体数 · <b>最多子任务</b>=单次 delegate 接受数（超出丢弃）。",
  "pa.abort": "终止(秒)",
  "pa.sub.abort.d": "秒，0=关闭，每个子智能体独立计时。中断后已完成的进度保留，可在主对话中续跑。",
  "pa.timeout": "超时保护（主智能体）",
  "pa.timeout.d": "秒，0=关闭。到达时限强制结束本轮；账本逐轮落盘，下一条消息可断点续跑。",
  "pa.perm": "权限模式",
  "pa.perm.safe": "安全模式",
  "pa.perm.safe.d": "仅 workspace",
  "pa.perm.ask": "询问模式",
  "pa.perm.ask.d": "可读项目 + 写技能",
  "pa.perm.full": "完全访问",
  "pa.perm.full.d": "任意读写",
  "pa.tools": "工具",
  "pa.tools.d": "（勾选 = 对 LLM 可用；数据来自后端 tools_registry）",
  "loading": "加载中...",
  "pane.service": "连接与服务",
  "svc.neo4j": "Neo4j 连接",
  "svc.neo4j.t": "Neo4j 连接状态",
  "svc.user": "用户名",
  "svc.pass": "密码",
  "svc.connect": "连接",
  "svc.mineru": "MinerU 解析配置",
  "svc.mineru.d": "（PDF→Markdown，随保存提交）",
  "svc.mineru.save": "保存 Token",
  "pane.ui": "界面",
  "ui.lang": "语言",
  "ui.lang.en": "English",
  "ui.lang.zh": "中文",
  "ui.fileopen": "文件打开方式",
  "ui.fileopen.unified": "应用内浮窗预览",
  "ui.fileopen.unifiedDesc": "图片、PDF、文本、Office 文件都在应用内浮窗打开；浏览器打不开的类型会提示「文件无法在浏览器内打开，请使用下载功能保存」。",
  "ui.xiaoe": "小e 桌宠",
  "ui.xiaoe.on": "已开启",
  "ui.xiaoe.desc": "云朵桌宠：点击聊天、双击敲打、拖动把玩、右键换天气；不保存任何记录。",
  "ui.fonts": "界面字号",
  "ui.font.conv": "对话管理字号",
  "ui.font.conv.d": "侧边栏的对话名称列表",
  "ui.font.history": "历史记录字号",
  "ui.font.history.d": "侧边栏每条历史消息的预览文字",
  "ui.font.messages": "对话回复消息字号",
  "ui.font.messages.d": "聊天区 AI/用户回复气泡内的文字",
  "ui.font.notes": "笔记字号",
  "ui.font.notes.d": "笔记卡片的标题与正文",
  "ui.font.thinking": "思考链/推理字号",
  "ui.font.thinking.d": "聊天区思考链与推理内容的文字",
  "ui.font.toast": "微提示/轻提示字号",
  "ui.font.toast.d": "右上角自动消失的轻提示（如\"已复制\"）",
  "ui.font.popup": "弹窗浮层字号",
  "ui.font.popup.d": "简单操作的小浮层（如重命名/编辑气泡）",
  "ui.font.modal": "大模态框字号",
  "ui.font.modal.d": "居中大模态框（如添加模型/服务商/数据库）",
  "ui.font.edit": "浮动编辑器字号",
  "ui.font.edit.d": "编辑消息/笔记的浮动 Markdown 编辑器",
  "ui.font.small": "重置为小字号",
  "ui.font.medium": "重置为中字号",
  "ui.font.large": "重置为大字号",
  "ui.font.tiers": "小字号为默认：各档在上一档基础上 +0.15。",
  "hero.start": "立即开始探索 →",
  "remote.btn": "远程控制",
  "remote.btn.t": "手机/其他电脑远程访问这台电脑",
  "remote.title": "📱 远程控制",
  "remote.tip": "在其他设备打开此地址即可访问本机；地址固定，本机 IP 变化不受影响。",
  "btn.copy": "复制",
  "remote.loading": "读取中…",
  "remote.tunnel": "开启隧道",
  "m.model": "添加自定义模型",
  "btn.add": "添加",
  "m.provider": "添加自定义服务商",
  "m.provider.name": "服务商名称",
  "m.provider.url": "默认 Base URL",
  "m.provider.desc": "简介（可选）",
  "m.provider.desc.ph": "一句话说明这个服务商",
  "btn.create": "创建",
  "sb.home": "返回主页",
  "sb.new": "新建对话",
  "sb.search": "搜索（点击C4E图标可以收起该区域）",
  "sb.all": "全部",
  "sb.user": "用户",
  "sb.ai": "AI",
  "sb.notes": "笔记",
  "sb.empty": "暂无对话历史",
  "sb.start": "+ 开始新的对话",
  "sb.resize": "拖动调整历史/聊天左右占比",
  "sb.toggle": "切换侧边栏",
  "notes.title": "📝 个人知识库",
  "notes.scope": "📚 全部",
  "notes.scope.t": "在本对话的笔记 / 全部笔记之间切换",
  "notes.add": "＋ 添加笔记",
  "notes.close": "关闭笔记",
  "notes.empty": "暂无笔记",
  "gen.stop.t": "立即终止本轮生成（已完成的部分保留，可继续追问让它接着跑）",
  "gen.stop": "立即终止",
  "gen.nudge.t": "催它马上出答案（不中断，已探索的内容都保留）",
  "gen.nudge": "快速回答",
  "gen.collapse.t": "收起智能体推理",
  "gen.collapse": "收起思考",
  "welcome": "您好——我是 <b>TruthCross</b>，面向 Fenton 文献数据挖掘与催化降解知识图谱的智能体。<br>\n我能帮您：<b>① 知识图谱查询</b>（Neo4j + GraphRAG）｜<b>② 文献挖掘全流程</b>（查重筛选 → MinerU 归档 → 工单 → 图像/曲线提取 → 图文互证 → 台账 → 图谱入库）｜<b>③ 矛盾核查与裁决</b>（Q1–Q4 发现项 · A–E 严重度 · 三轨对账）｜<b>④ PDF 解析归档</b>（全文 + 按图注编号裁图）｜<b>⑤ 脚本与数据分析</b>｜<b>⑥ 文件处理与联网检索</b>。<br>\n直接提问即可；点输入框左侧 📎 上传 PDF 文献。",
  "gen.thinking": "AI 正在思考…",
  "up.title": "📎 添加文件",
  "up.drop": "点击或拖拽文件到此处",
  "up.hint": "支持：图片｜文档(pdf/docx/txt/md/rtf/pptx/odt)｜数据(json/csv/xml/log/toml/ini)｜代码(50+ 格式)。⚠️ .xlsx/.xls 请先转为 .csv。",
  "in.expanded": "在此输入消息，支持多行输入...",
  "in.main": "发消息...",
  "in.attach": "上传文件",
  "in.expand": "展开消息输入",
  "in.model": "选择模型",
  "in.ctx": "当前发送给 LLM 的 Token 数（考虑上下文压缩）",
  "in.send": "发送",
  "bub.open": "📂 打开文件",
  "bub.llm": "🤖 发送给 LLM",
  "bub.loc": "📁 打开文件地址",
  "bub.edit": "✏️ 编辑文字",
  "bub.del": "🗑️ 删除",
  "bub.ph": "输入气泡文字...",
  "bub.addtext": "添加文字",
  "bub.addfile": "链接文件",
  "bub.edittitle": "编辑气泡文字",
  "bub.editph": "输入新的气泡文字",
  "btn.ok": "确定",
  "ed.title": "编辑内容",
  "ed.undo": "撤销",
  "ed.redo": "重做",
  "ed.bold": "加粗",
  "ed.italic": "斜体",
  "ed.strike": "删除线",
  "ed.h1": "标题1",
  "ed.h2": "标题2",
  "ed.h3": "标题3",
  "ed.ul": "无序列表",
  "ed.ol": "有序列表",
  "ed.quote": "引用",
  "ed.code": "行内代码",
  "ed.codeblock": "代码块",
  "ed.img": "插入图片",
  "ed.attach": "插入文档附件（pdf/docx/xlsx 等任意文件）",
  "ed.table": "插入表格",
  "ed.editimg": "编辑选中的图片",
  "ed.link": "链接",
  "ed.fontsize": "应用字号",
  "ed.fontsize.s": "选择字号",
  "ed.color": "应用着色",
  "ed.color.s": "选择颜色",
  "ed.normal": "正常",
  "ed.ul.l": "• 列表",
  "ed.ol.l": "1. 列表",
  "ed.quote.l": "❝ 引用",
  "ed.code.l": "<代码>",
  "ed.codeblock.l": "代码块",
  "ed.img.l": "图片",
  "ed.attach.l": "📎 附件",
  "ed.table.l": "▦ 表格",
  "ed.editimg.l": "✂️编辑图片",
  "ed.link.l": "链接",
  "ed.size.small": "小",
  "ed.size.normal": "正常",
  "ed.size.large": "大",
  "ed.size.xl": "特大",
  "ed.body": "输入 Markdown 内容…",
  "btn.save": "保存",
  "fp.title": "文件预览",
  "fp.native": "用本机默认应用打开",
  "fp.download": "下载"
};

    // JS 动态文案：{ key: { en: ..., zh: ... } }
    var JS = {
  "misc.auto": {
    "en": "0 (Auto)",
    "zh": "0（自动）"
  },
  "misc.on": {
    "en": "On",
    "zh": "开启"
  },
  "misc.off": {
    "en": "Off",
    "zh": "关闭"
  },
  "in.model.opt": {
    "en": "<option value=\"\" disabled selected>Select model</option>",
    "zh": "<option value=\"\" disabled selected>选择模型</option>"
  },
  "misc.noModelActive": {
    "en": "Configure and activate a model first",
    "zh": "请先配置并“亮起”模型"
  },
  "misc.wa.select": {
    "en": "Select workspace",
    "zh": "选择工作区域"
  },
  "misc.wa.output": {
    "en": "Output: ",
    "zh": "产物: "
  },
  "misc.wa.new": {
    "en": "New workspace…",
    "zh": "新建工作区域…"
  },
  "misc.wa.newTitle": {
    "en": "New Workspace",
    "zh": "新建工作区域"
  },
  "misc.wa.name": {
    "en": "Name",
    "zh": "名称"
  },
  "misc.wa.proj": {
    "en": "Project folder",
    "zh": "项目文件夹"
  },
  "misc.browse": {
    "en": "Browse…",
    "zh": "浏览…"
  },
  "misc.wa.out": {
    "en": "Output folder",
    "zh": "产物文件夹"
  },
  "misc.wa.createStart": {
    "en": "Create & start chat",
    "zh": "创建并开始对话"
  },
  "misc.wa.needProj": {
    "en": "Please fill in the project folder",
    "zh": "请填写项目文件夹"
  },
  "misc.creating": {
    "en": "Creating…",
    "zh": "创建中…"
  },
  "misc.createFailed": {
    "en": "Creation failed",
    "zh": "创建失败"
  },
  "misc.conv.new": {
    "en": "New Conversation",
    "zh": "新建对话"
  },
  "misc.selectAll": {
    "en": "Select All",
    "zh": "全选"
  },
  "msg.conv.created": {
    "en": "Conversation created",
    "zh": "新建对话已创建"
  },
  "msg.urged": {
    "en": "Answer urged: it will wrap up with the information at hand",
    "zh": "已催促回答：它会用现有信息收尾"
  },
  "msg.reqFailed": {
    "en": "❌ Request failed: ",
    "zh": "❌ 请求失败："
  },
  "msg.netErr": {
    "en": "Network error",
    "zh": "网络错误"
  },
  "msg.continueNoQ": {
    "en": "Cannot continue: no preceding question found.",
    "zh": "无法继续生成：未找到上文问题。"
  },
  "msg.mrCollapsed": {
    "en": "🧠 Model Reasoning ▸",
    "zh": "🧠 模型推理 ▸"
  },
  "msg.mrExpanded": {
    "en": "🧠 Model Reasoning ▾",
    "zh": "🧠 模型推理 ▾"
  },
  "msg.mrTitle": {
    "en": "🧠 Model Reasoning",
    "zh": "🧠 模型推理"
  },
  "msg.thinkingChain": {
    "en": "🧠 Thinking Process:",
    "zh": "🧠 思考过程："
  },
  "msg.toolCalls": {
    "en": "🛠️ Tool Calls:",
    "zh": "🛠️ 工具调用："
  },
  "msg.rawData": {
    "en": "📦 Raw Response Data:",
    "zh": "📦 原始响应数据："
  },
  "msg.stepThought": {
    "en": "🧠 Thought",
    "zh": "🧠 思考"
  },
  "msg.stepResult": {
    "en": "👁️ Result",
    "zh": "👁️ 结果"
  },
  "msg.stepCall": {
    "en": "Call:",
    "zh": "调用:"
  },
  "msg.saRunning": {
    "en": "● Running…",
    "zh": "● 运行中…"
  },
  "msg.saDone": {
    "en": "✅ Done",
    "zh": "✅ 完成"
  },
  "msg.saFail": {
    "en": "⚠️ Failed",
    "zh": "⚠️ 失败"
  },
  "msg.saDefault": {
    "en": "General Subagent",
    "zh": "通用子智能体"
  },
  "msg.saRead": {
    "en": "Read {n} chars",
    "zh": "读入 {n} 字"
  },
  "msg.saRounds": {
    "en": "Rounds {n}",
    "zh": "轮次 {n}"
  },
  "msg.saTools": {
    "en": "Tools {n}",
    "zh": "工具 {n}"
  },
  "msg.saSkills": {
    "en": "Skills {n}",
    "zh": "技能 {n}"
  },
  "msg.saTask": {
    "en": "Task",
    "zh": "任务"
  },
  "msg.saGoal": {
    "en": "Goal:",
    "zh": "目标："
  },
  "msg.saSummary": {
    "en": "Conclusion:",
    "zh": "结论："
  },
  "msg.saView": {
    "en": "Click to view the full process ▸",
    "zh": "点击查看全过程 ▸"
  },
  "msg.toAbort": {
    "en": "Force-stopped",
    "zh": "强制中断"
  },
  "msg.toImmediate": {
    "en": "Nudge to answer",
    "zh": "催促回答"
  },
  "msg.secElapsed": {
    "en": "({s}s)",
    "zh": "（{s}s）"
  },
  "msg.collapsedHint": {
    "en": "🧠 Thinking collapsed · click to expand",
    "zh": "🧠 思考过程已收起 · 点开"
  },
  "msg.attUploaded": {
    "en": "✅ Uploaded • ",
    "zh": "✅ 已上传 • "
  },
  "msg.delAttachment": {
    "en": "Remove attachment",
    "zh": "删除附件"
  },
  "msg.refDb": {
    "en": "Database",
    "zh": "数据库"
  },
  "msg.refNote": {
    "en": "Note",
    "zh": "笔记"
  },
  "msg.btnCopy": {
    "en": "Copy",
    "zh": "复制"
  },
  "msg.btnEdit": {
    "en": "Edit",
    "zh": "编辑"
  },
  "msg.btnBranch": {
    "en": "Branch",
    "zh": "分支生成"
  },
  "msg.btnRegen": {
    "en": "Regenerate",
    "zh": "重新生成"
  },
  "msg.btnDelete": {
    "en": "Delete",
    "zh": "删除"
  },
  "msg.btnContinue": {
    "en": "Continue",
    "zh": "继续生成"
  },
  "msg.expandThinking": {
    "en": "Expand thinking process",
    "zh": "展开思考过程"
  },
  "msg.collapseThinking": {
    "en": "Collapse thinking process",
    "zh": "收起思考过程"
  },
  "msg.branched": {
    "en": "New branch created from the original conversation. Explore a different direction here.",
    "zh": "已从原对话创建新分支，可以在此探索不同的对话方向。"
  },
  "msg.confirmDelMsg": {
    "en": "Delete this message?",
    "zh": "确定删除这条消息吗？"
  },
  "msg.selectModel": {
    "en": "Please select a model first",
    "zh": "请先选择模型"
  },
  "msg.noApiKey": {
    "en": "Model not active. Please configure an API key first.",
    "zh": "模型未激活，请先配置API密钥"
  },
  "msg.processingAtt": {
    "en": "Processing attachments...",
    "zh": "正在处理附件..."
  },
  "msg.attLost": {
    "en": "File {name} was not uploaded (may have been deleted) and was not sent with this message.",
    "zh": "文件 {name} 未上传成功（可能已被删除），本次未随消息发送"
  },
  "msg.attProcessFail": {
    "en": "Failed to process file {name}",
    "zh": "文件 {name} 处理失败"
  },
  "msg.nothingToSend": {
    "en": "Nothing to send (attachments failed to process)",
    "zh": "没有可发送的内容（附件均未成功处理）"
  },
  "msg.attOnly": {
    "en": "(Attachments uploaded: {list})",
    "zh": "（上传了附件：{list}）"
  },
  "msg.refOnly": {
    "en": "(Referenced entries: {list})",
    "zh": "（引用条目：{list}）"
  },
  "msg.listSep": {
    "en": ", ",
    "zh": "、"
  },
  "msg.copied": {
    "en": "Message copied to clipboard",
    "zh": "消息已复制到剪贴板"
  },
  "msg.copyFail": {
    "en": "Copy failed: ",
    "zh": "复制失败: "
  },
  "edit.defaultTitle": {
    "en": "Edit content",
    "zh": "编辑内容"
  },
  "edit.selFallback": {
    "en": "text",
    "zh": "文本"
  },
  "edit.linkPrompt": {
    "en": "Enter link URL:",
    "zh": "请输入链接地址："
  },
  "edit.linkFallback": {
    "en": "link text",
    "zh": "链接文字"
  },
  "edit.imageAlt": {
    "en": "image",
    "zh": "图片"
  },
  "edit.imgEditorUnavailable": {
    "en": "Image editor unavailable",
    "zh": "图片编辑器不可用"
  },
  "edit.selectImageCode": {
    "en": "Select image markdown in the text",
    "zh": "请选中文本中的图片代码"
  },
  "edit.uploading": {
    "en": "📎 Uploading ",
    "zh": "📎 正在上传 "
  },
  "edit.uploadFail": {
    "en": "❌ Upload failed",
    "zh": "❌ 文件上传失败"
  },
  "edit.fileFallback": {
    "en": "attachment",
    "zh": "附件"
  },
  "edit.fileInserted": {
    "en": "📎 Attachment placeholder inserted",
    "zh": "📎 已插入附件占位符"
  },
  "edit.col1": {
    "en": "Column 1",
    "zh": "列1"
  },
  "edit.col2": {
    "en": "Column 2",
    "zh": "列2"
  },
  "edit.col3": {
    "en": "Column 3",
    "zh": "列3"
  },
  "edit.sizeSmall": {
    "en": "Small",
    "zh": "小"
  },
  "edit.sizeNormal": {
    "en": "Normal",
    "zh": "正常"
  },
  "edit.sizeLarge": {
    "en": "Large",
    "zh": "大"
  },
  "edit.sizeXLarge": {
    "en": "XL",
    "zh": "特大"
  },
  "edit.attDeleted": {
    "en": "Attachment removed",
    "zh": "附件已删除"
  },
  "edit.titleAi": {
    "en": "Edit AI Response",
    "zh": "编辑 AI 回复"
  },
  "edit.titleMsg": {
    "en": "Edit Message",
    "zh": "编辑消息"
  },
  "edit.noteUntitled": {
    "en": "Untitled note",
    "zh": "未命名笔记"
  },
  "edit.empty": {
    "en": "Message cannot be empty",
    "zh": "消息内容不能为空"
  },
  "edit.updated": {
    "en": "Message updated. Click \"Regenerate\" to refresh the AI reply.",
    "zh": "消息已更新。如需更新AI回复，请点击\"重新生成\""
  },
  "msg.fmt.mermaidTitle": {
    "en": "📊 Diagram",
    "zh": "📊 流程图 / 图表"
  },
  "msg.fmt.zoomIn": {
    "en": "Zoom in",
    "zh": "放大"
  },
  "msg.fmt.zoomOut": {
    "en": "Zoom out",
    "zh": "缩小"
  },
  "msg.fmt.copyCode": {
    "en": "Copy code",
    "zh": "复制代码"
  },
  "msg.fmt.copyImg": {
    "en": "Copy image",
    "zh": "复制图片"
  },
  "msg.fmt.toggle": {
    "en": "Collapse/Expand",
    "zh": "收起/展开"
  },
  "msg.fmt.copyCodeBtn": {
    "en": "📋 Copy Code",
    "zh": "📋 复制代码"
  },
  "msg.fmt.tableTitle": {
    "en": "📅 Data Table",
    "zh": "📅 数据表格"
  },
  "msg.fmt.skin": {
    "en": "🎨 Theme",
    "zh": "🎨 换肤"
  },
  "msg.fmt.copyTableBtn": {
    "en": "📋 Copy Table",
    "zh": "📋 复制表格"
  },
  "msg.fmt.exportExcelBtn": {
    "en": "📊 Export Excel",
    "zh": "📊 导出 Excel"
  },
  "msg.fmt.openFile": {
    "en": "Click to open (preview in popover, or download)",
    "zh": "点击打开（浮窗预览，不支持则下载）"
  },
  "msg.fmt.delAtt": {
    "en": "Remove attachment (also deletes the backend file)",
    "zh": "删除附件（同时删除后端文件）"
  },
  "msg.fmt.copyLatex": {
    "en": "Copy LaTeX code",
    "zh": "复制LaTeX代码"
  },
  "msg.fmt.fnBack": {
    "en": "Back to reference",
    "zh": "回到引用处"
  },
  "msg.fmt.copied": {
    "en": "✅ Copied",
    "zh": "✅ 已复制"
  },
  "msg.fmt.formulaCopied": {
    "en": "Formula copied",
    "zh": "公式已复制"
  },
  "msg.fmt.copyFailed": {
    "en": "Copy failed",
    "zh": "复制失败"
  },
  "msg.fmt.theme0": {
    "en": "Follow Theme",
    "zh": "跟随主题"
  },
  "msg.fmt.theme1": {
    "en": "Morandi Purple",
    "zh": "莫兰迪紫"
  },
  "msg.fmt.theme2": {
    "en": "Business Gray",
    "zh": "商务灰"
  },
  "msg.fmt.theme3": {
    "en": "Minimal White",
    "zh": "极简白"
  },
  "msg.fmt.theme4": {
    "en": "Finance Green",
    "zh": "金融绿"
  },
  "msg.fmt.theme5": {
    "en": "Vibrant Orange",
    "zh": "活力橙"
  },
  "msg.fmt.theme6": {
    "en": "Hacker",
    "zh": "黑客风"
  },
  "msg.fmt.theme7": {
    "en": "Academic",
    "zh": "学术风"
  },
  "msg.fmt.theme8": {
    "en": "Luxury Gold",
    "zh": "轻奢金"
  },
  "msg.fmt.themeSwitched": {
    "en": "All tables switched to {name}",
    "zh": "所有表格已切换至 {name} 风格"
  },
  "msg.fmt.themeReset": {
    "en": "All tables restored to Classic Blue",
    "zh": "所有表格已恢复 经典蓝 风格"
  },
  "msg.fmt.tableCopied": {
    "en": "Table copied (formatting preserved)",
    "zh": "表格已完整复制 (保留格式与配色)"
  },
  "msg.fmt.tableCopiedText": {
    "en": "Table content copied (plain text only)",
    "zh": "表格内容已复制 (仅纯文本)"
  },
  "msg.fmt.exported": {
    "en": "Table exported as CSV (opens directly in Excel)",
    "zh": "表格已导出为CSV格式 (Excel可直接打开)"
  },
  "msg.fmt.deletingAtt": {
    "en": "🗑 Removing attachment…",
    "zh": "🗑 正在删除附件…"
  },
  "msg.fmt.attDeleted": {
    "en": "🗑 Attachment removed",
    "zh": "🗑 附件已删除"
  },
  "msg.fmt.delFileFail": {
    "en": "⚠️ Failed to delete file",
    "zh": "⚠️ 删除文件失败"
  },
  "msg.refBar": {
    "en": "Reference for agent:",
    "zh": "引用给智能体："
  },
  "msg.refRemove": {
    "en": "Remove reference",
    "zh": "移除引用"
  },
  "msg.refNoId": {
    "en": "This item lacks an id and cannot be referenced.",
    "zh": "该条目缺少 id，无法引用"
  },
  "msg.refDup": {
    "en": "Already in the reference list",
    "zh": "该条目已在引用列表中"
  },
  "msg.refAdded": {
    "en": "Referenced: {title} (the agent will read its full text when sending)",
    "zh": "已引用：{title}（发送时智能体将读取其全文）"
  },
  "msg.refNoteNotFound": {
    "en": "Note not found",
    "zh": "找不到该笔记"
  },
  "msg.refEntryNotFound": {
    "en": "Database entry not found",
    "zh": "找不到该数据库条目"
  },
  "msg.refUnnamedEntry": {
    "en": "Untitled entry",
    "zh": "未命名条目"
  },
  "sb.scopeThis": {
    "en": "📄 This chat",
    "zh": "📄 本对话"
  },
  "sb.scopeAll": {
    "en": "📚 All",
    "zh": "📚 全部"
  },
  "sb.notes": {
    "en": "Notes",
    "zh": "笔记"
  },
  "sb.allNotes": {
    "en": "📚 All Notes",
    "zh": "📚 全部笔记"
  },
  "sb.chatNotes": {
    "en": "📝 Chat Notes",
    "zh": "📝 对话笔记"
  },
  "sb.loadNotesFail": {
    "en": "Failed to load notes",
    "zh": "加载笔记失败"
  },
  "sb.emptyNote": {
    "en": "Empty note",
    "zh": "空笔记"
  },
  "sb.ownerTag": {
    "en": "Owner",
    "zh": "归属用户"
  },
  "sb.moveUp": {
    "en": "Move up",
    "zh": "上移"
  },
  "sb.moveDown": {
    "en": "Move down",
    "zh": "下移"
  },
  "sb.publishTitle": {
    "en": "Copy this note (with attachments/images) to the shared database",
    "zh": "把这条笔记（含附件/图片）原样复制到共享数据库"
  },
  "sb.updateDb": {
    "en": "⬆️ Update Database",
    "zh": "⬆️ 更新数据库"
  },
  "sb.uploadDb": {
    "en": "⬆️ Upload to Database",
    "zh": "⬆️ 上传数据库"
  },
  "sb.refTitle": {
    "en": "Reference for agent (sends the note's full text; the agent can then read/edit/delete it)",
    "zh": "引用给智能体（发送时带上该笔记全文，可让它增删改查）"
  },
  "sb.editNoteTitle": {
    "en": "Edit note",
    "zh": "编辑笔记"
  },
  "sb.deleteNoteTitle": {
    "en": "Delete note",
    "zh": "删除笔记"
  },
  "sb.newNote": {
    "en": "New note",
    "zh": "新笔记"
  },
  "sb.noteAdded": {
    "en": "Note added",
    "zh": "笔记已添加"
  },
  "sb.addNoteFail": {
    "en": "Failed to add note",
    "zh": "添加笔记失败"
  },
  "sb.confirmDelNote": {
    "en": "Delete this note?",
    "zh": "确定删除这条笔记吗？"
  },
  "sb.noteDeleted": {
    "en": "Note deleted",
    "zh": "笔记已删除"
  },
  "sb.delNoteFail": {
    "en": "Failed to delete note",
    "zh": "删除笔记失败"
  },
  "sb.moveNoteFail": {
    "en": "Failed to move note",
    "zh": "移动笔记失败"
  },
  "sb.editNoteDlg": {
    "en": "Edit Note",
    "zh": "编辑笔记"
  },
  "sb.noteUpdated": {
    "en": "Note updated",
    "zh": "笔记已更新"
  },
  "sb.updateNoteFail": {
    "en": "Failed to update note",
    "zh": "更新笔记失败"
  },
  "sb.pubConfirmUpdate": {
    "en": "Update the corresponding cloud database entry with this note's latest content? (Attachments/images will be copied along.)",
    "zh": "用这条笔记的最新内容更新云端数据库里对应的那条条目？（附件/图片会一并复制）"
  },
  "sb.pubConfirmUpload": {
    "en": "Copy this note (text + attachments + images) as-is to the shared database? The local note stays unchanged.",
    "zh": "把这条笔记（文本 + 附件 + 图片）原样复制一份到共享数据库？本地笔记保持不动。"
  },
  "sb.pubUpdating": {
    "en": "Updating cloud entry…",
    "zh": "正在更新云端条目…"
  },
  "sb.pubUploading": {
    "en": "Uploading to shared database…",
    "zh": "正在上传到共享数据库…"
  },
  "sb.pubUpdated": {
    "en": "Cloud entry updated",
    "zh": "云端条目已更新"
  },
  "sb.pubUploaded": {
    "en": "Uploaded to database",
    "zh": "已上传到数据库"
  },
  "sb.pubFilesCopied": {
    "en": " ({n} files copied)",
    "zh": "（复制文件 {n} 个）"
  },
  "sb.pubFilesFailed": {
    "en": "; {n} attachment(s) failed: ",
    "zh": "；{n} 个附件失败："
  },
  "sb.pubFail": {
    "en": "❌ Upload to database failed: ",
    "zh": "❌ 上传数据库失败："
  },
  "sb.netError": {
    "en": "Network error",
    "zh": "网络错误"
  },
  "sb.emptyHistory": {
    "en": "No chat history yet",
    "zh": "暂无对话历史"
  },
  "sb.noMatchMsg": {
    "en": "No matching messages found",
    "zh": "未找到匹配的消息"
  },
  "sb.unknownBranch": {
    "en": "Unknown chat",
    "zh": "未知对话"
  },
  "sb.noMatchChat": {
    "en": "No matching conversations found",
    "zh": "未找到匹配的对话"
  },
  "sb.newChat": {
    "en": "+ Start a new chat",
    "zh": "+ 开始新的对话"
  },
  "sb.defaultWa": {
    "en": "Default workspace",
    "zh": "默认工作区域"
  },
  "sb.workspace": {
    "en": "Workspace",
    "zh": "工作区域"
  },
  "sb.exportChat": {
    "en": "Export chat",
    "zh": "导出对话"
  },
  "sb.renameChat": {
    "en": "Rename chat",
    "zh": "重命名对话"
  },
  "sb.deleteChat": {
    "en": "Delete chat",
    "zh": "删除对话"
  },
  "sb.addBubble": {
    "en": "Add portable file",
    "zh": "添加便携文件"
  },
  "sb.viewNotes": {
    "en": "View notes",
    "zh": "查看笔记"
  },
  "sb.today": {
    "en": "Today",
    "zh": "今天"
  },
  "sb.yesterday": {
    "en": "Yesterday",
    "zh": "昨天"
  },
  "sb.dateMD": {
    "en": "{m}/{d}",
    "zh": "{m}月{d}日"
  },
  "branch.budgetAuto": {
    "en": "0 (Auto)",
    "zh": "0（自动）"
  },
  "branch.fontSmall": {
    "en": "Small",
    "zh": "小"
  },
  "branch.fontMedium": {
    "en": "Medium",
    "zh": "中"
  },
  "branch.fontLarge": {
    "en": "Large",
    "zh": "大"
  },
  "branch.fontReset": {
    "en": "Font size reset to {name}",
    "zh": "已重置为{name}字号"
  },
  "branch.on": {
    "en": "On",
    "zh": "开启"
  },
  "branch.off": {
    "en": "Off",
    "zh": "关闭"
  },
  "branch.saved": {
    "en": "Settings saved",
    "zh": "设置已保存"
  },
  "branch.notFound": {
    "en": "Conversation not found",
    "zh": "对话不存在"
  },
  "branch.nameRequired": {
    "en": "Please enter a new conversation name",
    "zh": "请输入新的对话名称"
  },
  "branch.nameUnchanged": {
    "en": "Name unchanged",
    "zh": "名称未改变"
  },
  "branch.renamed": {
    "en": "Renamed from \"{old}\" to \"{new}\"",
    "zh": "已从\"{old}\"重命名为\"{new}\""
  },
  "branch.confirmDel": {
    "en": "Delete conversation \"{name}\"?\nThis cannot be undone. All messages, backend attachments, and token stats will be permanently deleted.",
    "zh": "确定要删除对话 \"{name}\" 吗？\n此操作不可恢复，将永久删除该对话所有记录、关联的后端附件和 Token 统计。"
  },
  "branch.deleted": {
    "en": "\"{name}\" deleted successfully",
    "zh": "\"{name}\" 已成功删除"
  },
  "branch.exported": {
    "en": "Conversation exported",
    "zh": "对话已导出"
  },
  "uchat.backendDown": {
    "en": "Cannot read conversation list: is the local backend running?",
    "zh": "读不到对话列表：本机后端没起来？"
  },
  "top.fallbackTitle": {
    "en": "Chat",
    "zh": "对话"
  },
  "top.searchPh": {
    "en": "Search chats…",
    "zh": "搜索对话…"
  },
  "msg.fmt.skinTitle": {
    "en": "Switch table skin globally",
    "zh": "全局切换表格皮肤"
  },

    "agentcfg.configured": {
      "en": "✅ Configured",
      "zh": "✅ 已配置"
    },
    "agentcfg.connect": {
      "en": "🔌 Connect",
      "zh": "🔌 连接"
    },
    "agentcfg.connectFail": {
      "en": "Connection failed",
      "zh": "连接失败"
    },
    "agentcfg.connectOk": {
      "en": "Connected",
      "zh": "连接成功"
    },
    "agentcfg.connected": {
      "en": "✅ Connected",
      "zh": "✅ 已连接"
    },
    "agentcfg.connecting": {
      "en": "⏳ Connecting...",
      "zh": "⏳ 连接中..."
    },
    "agentcfg.enterPass": {
      "en": "Please enter the password",
      "zh": "请输入密码"
    },
    "agentcfg.gr.code": {
      "en": "Code / terminal",
      "zh": "代码/终端 (code)"
    },
    "agentcfg.gr.database": {
      "en": "Shared Notes (group library)",
      "zh": "共享笔记库 (shared)"
    },
    "agentcfg.gr.personal": {
      "en": "My Knowledge Base (private)",
      "zh": "个人知识库 (private)"
    },
    "agentcfg.gr.documents": {
      "en": "Documents",
      "zh": "文档解析 (documents)"
    },
    "agentcfg.gr.file": {
      "en": "File",
      "zh": "文件 (file)"
    },
    "agentcfg.gr.github": {
      "en": "GitHub repos",
      "zh": "代码仓库 (github)"
    },
    "agentcfg.gr.graph": {
      "en": "Graph",
      "zh": "图谱 (graph)"
    },
    "agentcfg.gr.literature": {
      "en": "Literature",
      "zh": "文献检索 (literature)"
    },
    "agentcfg.gr.memory": {
      "en": "Memory / history",
      "zh": "记忆/历史 (memory)"
    },
    "agentcfg.gr.skills": {
      "en": "Skills",
      "zh": "技能 (skills)"
    },
    "agentcfg.gr.subagent": {
      "en": "Subagents",
      "zh": "子智能体 (subagent)"
    },
    "agentcfg.gr.task": {
      "en": "Tasks",
      "zh": "任务 (task)"
    },
    "agentcfg.gr.web": {
      "en": "Web",
      "zh": "联网 (web)"
    },
    "agentcfg.noTools": {
      "en": "(Backend returned no tools)",
      "zh": "（后端未返回工具）"
    },
    "agentcfg.permWarn": {
      "en": "⚠️ Non-safe modes grant the agent read/write access to【system files】and【the app itself】, which may cause irreversible changes or even crashes. Continue?",
      "zh": "⚠️ 非安全模式授予智能体对【系统文件】及【软件自身】的读写/修改权限，\n可能导致不可逆的修改甚至崩溃。确定要继续吗？"
    },
    "agentcfg.readFail": {
      "en": "Read failed",
      "zh": "读取失败"
    },
    "agentcfg.saveFail": {
      "en": "Save failed",
      "zh": "保存失败"
    },
    "agentcfg.saved": {
      "en": "✅ Saved",
      "zh": "✅ 已保存"
    },
    "agentcfg.tokenCleared": {
      "en": "⚠️ Token cleared",
      "zh": "⚠️ 已清空 Token"
    },
    "auth.acctPrefix": {
      "en": "Account · ",
      "zh": "账号 · "
    },
    "auth.adjustLevel": {
      "en": "Adjust level",
      "zh": "调整等级"
    },
    "auth.brand": {
      "en": "Jiangnan University<br>Computing for Environment Group",
      "zh": "江南大学<br>环境计算组"
    },
    "auth.clickToLogin": {
      "en": "Click here to log in",
      "zh": "点这里登录"
    },
    "auth.close": {
      "en": "Close",
      "zh": "关闭"
    },
    "auth.confirmPassword": {
      "en": "Confirm password",
      "zh": "确认密码"
    },
    "auth.create": {
      "en": "Create",
      "zh": "创建"
    },
    "auth.created": {
      "en": "✅ Created {u} ({lv})",
      "zh": "✅ 已创建 {u}（{lv}）"
    },
    "auth.delAccount": {
      "en": "Delete account",
      "zh": "删除账号"
    },
    "auth.delAccountConfirm": {
      "en": "Delete account {u}? Its cloud personal directory (notes/attachments) will be deleted too and cannot be recovered.",
      "zh": "确定删除账号 {u}？其云端个人目录（笔记/附件）将一并删除，不可恢复。"
    },
    "auth.deleted": {
      "en": "✅ Deleted {u}",
      "zh": "✅ 已删除 {u}"
    },
    "auth.enterPass": {
      "en": "Please enter a password",
      "zh": "请输入密码"
    },
    "auth.enterUser": {
      "en": "Please enter a username",
      "zh": "请输入用户名"
    },
    "auth.expiredSuffix": {
      "en": " · Shared-library session expired — click here to log in again",
      "zh": " · 共享库会话已过期，点这里重新登录"
    },
    "auth.guestRowTitle": {
      "en": "Not logged in: click here to log in to the shared library",
      "zh": "未登录：点这里登录共享库"
    },
    "auth.guestTitle": {
      "en": "Not logged in: local features work as usual; log in to use the Shared Notes library and the Wiki tools",
      "zh": "未登录：本地功能照常用；登录后可使用共享笔记库与 Wiki 工具"
    },
    "auth.hintLogin": {
      "en": "After logging in you can use the Shared Notes library; your personal knowledge base is private to your account",
      "zh": "登录后可使用共享笔记库；个人知识库只属于你自己的账号"
    },
    "auth.hintRegister": {
      "en": "Free registration; private features work right away, the Shared Notes library needs admin approval",
      "zh": "自由注册；注册后私人功能立即可用，共享笔记库需管理员授权"
    },
    "auth.levelSet": {
      "en": "✅ Set {u} to {lv}",
      "zh": "✅ 已将 {u} 调整为 {lv}"
    },
    "auth.loadFail": {
      "en": "Failed to load",
      "zh": "加载失败"
    },
    "auth.loading": {
      "en": "Loading…",
      "zh": "加载中…"
    },
    "auth.loggingIn": {
      "en": "Logging in…",
      "zh": "登录中…"
    },
    "auth.login": {
      "en": "Log in",
      "zh": "登录"
    },
    "auth.loginBtn": {
      "en": "Log in",
      "zh": "登 录"
    },
    "auth.loginChip": {
      "en": "🔑 Log in",
      "zh": "🔑 登录"
    },
    "auth.loginFail": {
      "en": "Login failed",
      "zh": "登录失败"
    },
    "auth.logout": {
      "en": "🚪 Log out",
      "zh": "🚪 退出登录"
    },
    "auth.lv0": {
      "en": "Guest",
      "zh": "游客"
    },
    "auth.lv1": {
      "en": "Blue",
      "zh": "蓝色"
    },
    "auth.lv2": {
      "en": "Purple",
      "zh": "紫色"
    },
    "auth.lv3": {
      "en": "Orange",
      "zh": "橙色"
    },
    "auth.lv4": {
      "en": "Red",
      "zh": "红色"
    },
    "auth.lv5": {
      "en": "Gold",
      "zh": "金色"
    },
    "auth.netErr": {
      "en": "Network error: cannot reach the shared library",
      "zh": "网络错误：连不上共享库"
    },
    "auth.newAccount": {
      "en": "Create account",
      "zh": "新建账号"
    },
    "auth.newBtn": {
      "en": "＋ New",
      "zh": "＋ 新建"
    },
    "auth.notLoggedIn": {
      "en": "Not logged in",
      "zh": "未登录"
    },
    "auth.openAcctMenu": {
      "en": " · Click to open the account menu",
      "zh": " · 点这里打开账号菜单"
    },
    "auth.passMismatch": {
      "en": "The two passwords do not match",
      "zh": "两次输入的密码不一致"
    },
    "auth.passReset": {
      "en": "✅ Password of {u} reset",
      "zh": "✅ 已重置 {u} 的密码"
    },
    "auth.password": {
      "en": "Password",
      "zh": "密码"
    },
    "auth.password6": {
      "en": "Password (≥6 chars)",
      "zh": "密码(≥6位)"
    },
    "auth.qwenDaily": {
      "en": "Qwen {n}/day",
      "zh": "千问{n}/日"
    },
    "auth.registerBtn": {
      "en": "Register",
      "zh": "注 册"
    },
    "auth.registerFail": {
      "en": "Registration failed",
      "zh": "注册失败"
    },
    "auth.registerOk": {
      "en": "✅ Registration successful! Switch to the「Login」tab to log in",
      "zh": "✅ 注册成功！请切换到「登录」页签登录"
    },
    "auth.registerTitle": {
      "en": "Register a new account",
      "zh": "注册新账号"
    },
    "auth.registering": {
      "en": "Registering…",
      "zh": "注册中…"
    },
    "auth.relogin": {
      "en": "🔑 Log in again (session expired)",
      "zh": "🔑 重新登录（会话已过期）"
    },
    "auth.resetPass": {
      "en": "Reset password",
      "zh": "重置密码"
    },
    "auth.rootAdmin": {
      "en": "root admin",
      "zh": "主管理员"
    },
    "auth.rootDotAdmin": {
      "en": " · Root admin",
      "zh": " · 主管理员"
    },
    "auth.rootSuffix": {
      "en": "(root admin)",
      "zh": "（主管理员）"
    },
    "auth.searchUser": {
      "en": "Search username…",
      "zh": "搜索用户名…"
    },
    "auth.sessionExpiredToast": {
      "en": "Shared-library session expired — click the identity chip at the top right to log in again",
      "zh": "共享库会话已过期，点右上角身份芯片重新登录"
    },
    "auth.setNewPass": {
      "en": "Set a new password for {u} (≥4 chars):",
      "zh": "为 {u} 设置新密码（≥4位）："
    },
    "auth.switchAcct": {
      "en": "🔁 Switch account",
      "zh": "🔁 切换账号"
    },
    "auth.tabLogin": {
      "en": "Account login",
      "zh": "账号登录"
    },
    "auth.tabRegister": {
      "en": "Register",
      "zh": "注册"
    },
    "auth.timeout": {
      "en": "Request timed out: cannot reach the shared library — check your network and retry",
      "zh": "请求超时：连不上共享库，请检查网络后重试"
    },
    "auth.userMgmt": {
      "en": "User management",
      "zh": "用户管理"
    },
    "auth.userMgmtMenu": {
      "en": "👥 User management",
      "zh": "👥 用户管理"
    },
    "auth.username": {
      "en": "Username",
      "zh": "用户名"
    },
    "db.addEntryHere": {
      "en": "Create a note entry in this folder",
      "zh": "在此夹新建笔记条目"
    },
    "db.addSubfolder": {
      "en": "Create a subfolder under this folder",
      "zh": "在此夹下新建子文件夹"
    },
    "db.allOwners": {
      "en": "👤 All uploaders",
      "zh": "👤 全部上传者"
    },
    "db.allYears": {
      "en": "📅 All years",
      "zh": "📅 全部年份"
    },
    "db.attachRemoved": {
      "en": "Attachment placeholder removed",
      "zh": "已移除附件占位"
    },
    "db.createFail": {
      "en": "Create failed",
      "zh": "创建失败"
    },
    "db.delEntry": {
      "en": "Delete entry",
      "zh": "删除条目"
    },
    "db.delEntryConfirm": {
      "en": "Delete this entry? (Attachment files themselves are kept)",
      "zh": "确定删除这条条目吗？（附件文件本身保留）"
    },
    "db.delFail": {
      "en": "Delete failed: ",
      "zh": "删除失败："
    },
    "db.delFolder": {
      "en": "Delete folder (contents are kept)",
      "zh": "删除文件夹（内容不会删除）"
    },
    "db.delGroupConfirm": {
      "en": "Delete this folder?\n\nEntries inside are not deleted; subfolders and entries move up to the parent.",
      "zh": "删除该分组？\n\n分组里的条目不会被删除，子分组与条目会上移到父级。"
    },
    "db.editEntry": {
      "en": "Edit entry",
      "zh": "编辑条目"
    },
    "db.editEntryTitle": {
      "en": "Edit database entry",
      "zh": "编辑数据库条目"
    },
    "db.editGroup": {
      "en": "Edit folder",
      "zh": "编辑分组"
    },
    "db.editNameIntro": {
      "en": "Edit name and description",
      "zh": "编辑名称与简介"
    },
    "db.editorNotReady": {
      "en": "Editor not ready",
      "zh": "编辑器未就绪"
    },
    "db.emptyEntry": {
      "en": "Empty entry",
      "zh": "空条目"
    },
    "db.emptyFiltered": {
      "en": "No entries match the current filters<br><span style=\"font-size:.78rem;\">Try setting「👤 / 📅」back to「All」</span>",
      "zh": "当前筛选条件下没有条目<br><span style=\"font-size:.78rem;\">试试把「👤 / 📅」改回「全部」</span>"
    },
    "db.emptyHint": {
      "en": "No entries yet<br><span style=\"font-size:.78rem;\">Click「📤 Upload」or「＋ Note」</span>",
      "zh": "暂无条目<br><span style=\"font-size:.78rem;\">点「📤 上传」或「＋ 笔记」</span>"
    },
    "db.entriesSuffix": {
      "en": " entries",
      "zh": " 个条目"
    },
    "db.entryCreated": {
      "en": "✅ Entry created",
      "zh": "✅ 条目已创建"
    },
    "db.entryDeleted": {
      "en": "Entry deleted",
      "zh": "条目已删除"
    },
    "db.entryUpdated": {
      "en": "Entry updated",
      "zh": "条目已更新"
    },
    "db.filesSuffix": {
      "en": " file(s)",
      "zh": " 个文件"
    },
    "db.groupCreated": {
      "en": "✅ Folder「{name}」created",
      "zh": "✅ 已创建分组「{name}」"
    },
    "db.groupDeleted": {
      "en": "✅ Folder deleted (entries moved up, nothing lost)",
      "zh": "✅ 分组已删除（条目已上移，未丢失）"
    },
    "db.groupNameEmpty": {
      "en": "Folder name cannot be empty",
      "zh": "分组名不能为空"
    },
    "db.loadFail": {
      "en": "Failed to load",
      "zh": "加载失败"
    },
    "db.loading": {
      "en": "Loading…",
      "zh": "加载中…"
    },
    "db.loginFirst": {
      "en": "Please log in first",
      "zh": "请先登录"
    },
    "db.moveFail": {
      "en": "Move failed: ",
      "zh": "移动失败："
    },
    "db.moveGroupTitle": {
      "en": "Move「{name}」to",
      "zh": "移动「{name}」到"
    },
    "db.moveOwnOnly": {
      "en": "You can only move your own entries",
      "zh": "只能移动自己的条目"
    },
    "db.moveToFolder": {
      "en": "Move to folder",
      "zh": "移动到文件夹"
    },
    "db.moveToRoot": {
      "en": "(Move to root level)",
      "zh": "（移动到根级）"
    },
    "db.moved": {
      "en": "✅ Moved",
      "zh": "✅ 已移动"
    },
    "db.movedOut": {
      "en": "✅ Moved out of folder",
      "zh": "✅ 已移出分组"
    },
    "db.movedTo": {
      "en": "✅ Moved to folder",
      "zh": "✅ 已移动到分组"
    },
    "db.needLogin": {
      "en": "🔐 The database lives on the shared server: please click「Login」at the top right",
      "zh": "🔐 数据库在共享服务器：请先点击右上角「登录」"
    },
    "db.netError": {
      "en": "network error",
      "zh": "网络错误"
    },
    "db.newEntry": {
      "en": "New database entry",
      "zh": "新建数据库条目"
    },
    "db.newEntryIn": {
      "en": "New entry in「{name}」",
      "zh": "在「{name}」新建条目"
    },
    "db.newGroup": {
      "en": "New folder",
      "zh": "新建分组"
    },
    "db.newSubGroup": {
      "en": "New subfolder",
      "zh": "新建子分组"
    },
    "db.noFolders": {
      "en": "(No folders yet)",
      "zh": "（还没有文件夹）"
    },
    "db.noIntro": {
      "en": "(No description — click ✏️ to add)",
      "zh": "（无简介，点 ✏️ 添加）"
    },
    "db.notLoggedIn": {
      "en": "Not logged in",
      "zh": "未登录"
    },
    "db.orphan": {
      "en": "📁 Unfiled",
      "zh": "📁 未归属"
    },
    "db.orphanHint": {
      "en": "These entries are not in any folder yet (right-click to move them)",
      "zh": "这些条目还没有归入任何文件夹（可右键移动到目标夹）"
    },
    "db.ownerAllShort": {
      "en": "👤 All",
      "zh": "👤 全部"
    },
    "db.readGroupsFail": {
      "en": "Failed to read folders: ",
      "zh": "读取分组失败："
    },
    "db.refTitle": {
      "en": "Reference for the agent (sends the full entry text so it can add/delete/query)",
      "zh": "引用给智能体（发送时带上该条目全文，可让它增删改查）"
    },
    "db.removeAttachConfirm": {
      "en": "Remove this attachment placeholder from the entry? (The file itself is kept)",
      "zh": "从条目中移除该附件占位？（文件本身保留）"
    },
    "db.removeAttachTitle": {
      "en": "Remove this attachment from the entry (file itself is kept)",
      "zh": "从条目中移除该附件（不删文件）"
    },
    "db.rootSuffix": {
      "en": " (root)",
      "zh": "（根）"
    },
    "db.saveFail": {
      "en": "Save failed: ",
      "zh": "保存失败："
    },
    "db.saved": {
      "en": "✅ Saved",
      "zh": "✅ 已保存"
    },
    "db.sharedLib": {
      "en": "the shared library",
      "zh": "共享库"
    },
    "db.sort.name": {
      "en": "🔤 Name",
      "zh": "🔤 名称"
    },
    "db.sort.newest": {
      "en": "🕒 Latest",
      "zh": "🕒 最新"
    },
    "db.sort.oldest": {
      "en": "🕒 Earliest",
      "zh": "🕒 最早"
    },
    "db.system": {
      "en": "System",
      "zh": "系统"
    },
    "db.unknown": {
      "en": "unknown reason",
      "zh": "未知原因"
    },
    "db.unnamed": {
      "en": "Untitled entry",
      "zh": "未命名条目"
    },
    "db.updateFail": {
      "en": "❌ Update failed",
      "zh": "❌ 更新失败"
    },
    "db.updateFailPlain": {
      "en": "Update failed",
      "zh": "更新失败"
    },
    "db.uploadFail": {
      "en": "❌ Upload failed: ",
      "zh": "❌ 上传失败: "
    },
    "db.uploadFailed": {
      "en": "Upload failed: ",
      "zh": "上传失败："
    },
    "db.uploadOk": {
      "en": "✅ \"{name}\" saved to {folder}; searchable by the whole group",
      "zh": "✅ 「{name}」已存入{folder}，全组可检索"
    },
    "db.uploadToFolder": {
      "en": "Upload a document to「{name}」(attachments in this folder are parsed to text automatically)",
      "zh": "上传文档到「{name}」（此夹内的附件会自动解析成文字）"
    },
    "db.uploadedPrefix": {
      "en": "Uploaded ",
      "zh": "已上传 "
    },
    "db.uploading": {
      "en": "Uploading to the shared database...",
      "zh": "正在上传到共享数据库..."
    },
    "db.waitApprove": {
      "en": "⏳ Registered — you can upload once an admin grants database access",
      "zh": "⏳ 已注册，等待管理员开通数据库权限后即可上传"
    },
    "db.yearAllShort": {
      "en": "📅 All",
      "zh": "📅 全部"
    },
    "db.yearSuffix": {
      "en": "",
      "zh": "年"
    },
    "db.zeroEntries": {
      "en": "0 entries",
      "zh": "0 个条目"
    },
    "fp.cantPreview": {
      "en": "This file (.{ext}) cannot be opened in the browser. Please use Download to save it.",
      "zh": "该文件（.{ext}）无法在浏览器内打开，请使用下载功能保存。"
    },
    "fp.download": {
      "en": "Download",
      "zh": "下载"
    },
    "fp.cantPreviewDownloaded": {
      "en": "This type (.{ext}) cannot be previewed in the browser; it has been downloaded",
      "zh": "该类型（.{ext}）浏览器无法预览，已下载"
    },
    "fp.imgFail": {
      "en": "Image failed to load (not found on this instance or the shared library).",
      "zh": "图片加载失败（本实例与共享库均未找到该文件）。"
    },
    "fp.loading": {
      "en": "Loading…",
      "zh": "加载中…"
    },
    "fp.noBackend": {
      "en": "This file has no backend data and cannot be previewed in a floating window",
      "zh": "该文件没有后端数据，无法浮窗预览"
    },
    "fp.noText": {
      "en": "No usable text in this file — click ⬇️ to download and view it.",
      "zh": "该文件没有可用文本，点 ⬇️ 下载查看。"
    },
    "fp.officeNoText": {
      "en": "This file (.{ext}) cannot be opened in the browser. Please use Download to save it.",
      "zh": "该文件（.{ext}）无法在浏览器内打开，请使用下载功能保存。"
    },
    "fp.officeText": {
      "en": "⚠️ This file (.{ext}) cannot be shown in the browser — what follows is the plain text parsed by the backend. Use ⬇️ Download above to save the original.",
      "zh": "⚠️ 该文件（.{ext}）无法在浏览器内显示原件，以下是后端解析出的纯文本；要看原件请用上方 ⬇️ 下载功能保存。"
    },
    "fp.openNativeFail": {
      "en": "Failed to open with the default app",
      "zh": "用默认应用打开失败"
    },
    "fp.readingParsed": {
      "en": "Reading parsed text from backend…",
      "zh": "正在读取后端解析文本…"
    },
    "fp.readingText": {
      "en": "Reading text…",
      "zh": "正在读取文本…"
    },
    "fp.render": {
      "en": "Render",
      "zh": "渲染"
    },
    "fp.source": {
      "en": "Source",
      "zh": "源码"
    },
    "fp.unknown": {
      "en": "unknown",
      "zh": "未知"
    },
    "fp.unsupported": {
      "en": "This deployment does not support opening with the local app",
      "zh": "当前部署不支持本机打开"
    },
    "prov.addModel": {
      "en": "+ Add model",
      "zh": "+ 添加模型"
    },
    "prov.addProvider": {
      "en": "Add provider",
      "zh": "添加服务商"
    },
    "prov.configSaved": {
      "en": "Configuration saved (synced to your account)",
      "zh": "配置已保存（已同步到你的账号）"
    },
    "prov.customModel": {
      "en": "Custom model",
      "zh": "自定义模型"
    },
    "prov.delModelConfirm": {
      "en": "Delete model {id}?",
      "zh": "确定要删除模型 {id} 吗？"
    },
    "prov.delProviderConfirm": {
      "en": "Delete this provider?",
      "zh": "确定要删除这个服务商吗？"
    },
    "prov.deleteModel": {
      "en": "Delete this model",
      "zh": "删除此模型"
    },
    "prov.desc.alibaba": {
      "en": "Alibaba Cloud Model Studio (Tongyi Qwen) official API | strong Chinese & long-text, generous quota",
      "zh": "通义千问 Qwen 官方 API｜中文与长文本能力强，额度友好"
    },
    "prov.desc.anthropic": {
      "en": "Anthropic official API | Claude family, long-context & complex reasoning",
      "zh": "Claude 系列官方 API｜长文本与复杂推理"
    },
    "prov.desc.deepseek": {
      "en": "DeepSeek official API | strong at general chat and code, great value",
      "zh": "深度求索官方 API｜通用对话与代码见长，性价比高"
    },
    "prov.desc.gateway": {
      "en": "Agent gateway: connect any mainstream agent to C4EAI via the agent gateway; data stays in your browser — we collect no private info. Note: C4EAI\\u0027s own tools and skills are unavailable with an external agent; Q&A only.",
      "zh": "Agent 网关：可通过 agent gateway 连接任意主流 agent 到 C4EAI 进行回答，数据保存在您的浏览器前端，我们不获取您的任何隐私信息。注意：使用外部 agent 连接时，C4EAI 自身的工具与技能不可用，仅可纯问答。"
    },
    "prov.desc.glm": {
      "en": "Zhipu AI official API | GLM family, standout agent & coding ability",
      "zh": "智谱 AI 官方 API｜GLM 系列，Agent 与代码能力突出"
    },
    "prov.desc.google": {
      "en": "Gemini official API (OpenAI-compatible endpoint) | flagship multimodal",
      "zh": "Gemini 官方 API（OpenAI 兼容端点）｜多模态旗舰"
    },
    "prov.desc.kimi": {
      "en": "Moonshot Kimi official API (China site) | long context & strong agents; international site: https://api.moonshot.ai/v1, Kimi coding plan keys use https://api.kimi.com/coding",
      "zh": "Moonshot Kimi 官方 API（国内站）｜长上下文与 Agent 能力强；国际站为 https://api.moonshot.ai/v1，Kimi 编程套餐 key 用 https://api.kimi.com/coding"
    },
    "prov.desc.openai": {
      "en": "OpenAI official API | GPT family, richest multimodal ecosystem",
      "zh": "OpenAI 官方 API｜GPT 系列，多模态与生态最全"
    },
    "prov.desc.qwenLocal": {
      "en": "Lab server local inference | free & unlimited, built-in",
      "zh": "实验室服务器本地推理｜免费不限量，内置无需配置"
    },
    "prov.desc.qwenLocalModel": {
      "en": "Lab server local inference — free, unlimited, prebuilt",
      "zh": "实验室服务器本地推理，免费不限量，已内置无需配置"
    },
    "prov.desc.xai": {
      "en": "xAI official API | Grok family, real-time web & strong reasoning",
      "zh": "Grok 系列官方 API｜实时联网与强推理"
    },
    "prov.emptyModels": {
      "en": "After configuring the URL and API key above and saving, click「+ Add model」to add models supported by this provider, then light them up to use.",
      "zh": "在上方配置好 URL 与 API 密钥并保存后，点击「+ 添加模型」添加该服务商支持的模型，点亮即可使用。"
    },
    "prov.enterName": {
      "en": "Please enter a provider name",
      "zh": "请输入服务商名称"
    },
    "prov.fillAll": {
      "en": "Please fill in all fields",
      "zh": "请填写完整信息"
    },
    "prov.msgPh": {
      "en": "Type a message...",
      "zh": "发消息..."
    },
    "prov.needKey": {
      "en": "Configure an API key for [{name}] above and \"light up\" the model",
      "zh": "请在上方为 [{name}] 配置API密钥并“亮起”模型"
    },
    "prov.saved": {
      "en": "✅ Saved",
      "zh": "✅ 已保存"
    },
    "tool.copied": {
      "en": "✅ Copied",
      "zh": "✅ 已复制"
    },
    "tool.copyFail": {
      "en": "Copy failed",
      "zh": "复制失败"
    },
    "tool.copyImageFail": {
      "en": "Failed to copy image",
      "zh": "复制图片失败"
    },
    "tool.diagramCopied": {
      "en": "Diagram copied to clipboard as an image",
      "zh": "流程图已作为图片复制到剪贴板"
    },
    "tool.fallbackDownload": {
      "en": "This browser cannot copy images directly — download started instead",
      "zh": "浏览器不支持直接复制图片，已为您开启下载"
    },
    "tool.mermaidCopied": {
      "en": "Mermaid source code copied",
      "zh": "Mermaid 源代码已复制"
    },
    "tool.mermaidFail": {
      "en": "⚠️ This Mermaid code failed to render. Common cause: special characters like <code>&gt;</code> <code>,</code> <code>?</code> <code>( )</code> in node labels without quotes (write A[\"efficiency &gt; 90% ?\"] instead).",
      "zh": "⚠️ 这段 Mermaid 代码没能渲染。常见原因：节点文字里含 <code>&gt;</code> <code>,</code> <code>?</code> <code>( )</code> 等特殊字符却没加引号（写成 A[\"效率 &gt; 90% ？\"] 即可）。"
    },
    "tool.overThreshold": {
      "en": "Over expected-token threshold {n}",
      "zh": "超出预期Token数阈值 {n}"
    },
    "tool.preparingImage": {
      "en": "Preparing image...",
      "zh": "正在准备图片..."
    }
  ,
  "gr.neo4jNotConnected": {
    "en": "Neo4j not connected",
    "zh": "Neo4j 未连接"
  },
  "gr.nudge": {
    "en": "Nudge for answer",
    "zh": "催促回答"
  },
  "gen.nudge": {
    "en": "Quick answer",
    "zh": "快速回答"
  },
  "gen.nudge.t": {
    "en": "Nudge: ask it to answer now (nothing is lost)",
    "zh": "催它马上出答案（不中断，已探索的内容都保留）"
  },
  "gen.stop": {
    "en": "Stop",
    "zh": "立即终止"
  },
  "gen.stop.t": {
    "en": "Stop this run (finished progress is kept; ask a follow-up to resume)",
    "zh": "立即终止本轮生成（已完成的部分保留，可继续追问让它接着跑）"
  },
  "gr.reasoningCollapsed": {
    "en": "🧠 Reasoning collapsed · tap to open",
    "zh": "🧠 思考过程已收起 · 点开"
  },
  "gr.agentThinking": {
    "en": "Agent thinking...",
    "zh": "智能体思考中..."
  },
  "gr.mrOpen": {
    "en": "🧠 Model reasoning ▾",
    "zh": "🧠 模型推理 ▾"
  },
  "gr.mrClosed": {
    "en": "🧠 Model reasoning ▸",
    "zh": "🧠 模型推理 ▸"
  },
  "gr.stepThink": {
    "en": "🧠 Thinking",
    "zh": "🧠 思考"
  },
  "gr.stepCall": {
    "en": "🔧 Call: ",
    "zh": "🔧 调用: "
  },
  "gr.executing": {
    "en": "🔧 Running ",
    "zh": "🔧 正在执行 "
  },
  "gr.stepResult": {
    "en": "👁️ Result",
    "zh": "👁️ 结果"
  },
  "gr.toolReturned": {
    "en": " returned, continuing to reason…",
    "zh": " 已返回，继续推理…"
  },
  "gr.abort": {
    "en": "Force stop",
    "zh": "强制中断"
  },
  "gr.stillProcessing": {
    "en": ", still processing…",
    "zh": "，仍在处理…"
  },
  "gr.roleContradiction": {
    "en": "Contradiction check",
    "zh": "矛盾核查"
  },
  "gr.roleExtraction": {
    "en": "Data extraction",
    "zh": "数据提取"
  },
  "gr.roleVerify": {
    "en": "Data verification",
    "zh": "数据核查"
  },
  "gr.running": {
    "en": "Running…",
    "zh": "运行中…"
  },
  "gr.done": {
    "en": "Done",
    "zh": "完成"
  },
  "gr.failed": {
    "en": "Failed",
    "zh": "失败"
  },
  "gr.genericSubagent": {
    "en": "General subagent",
    "zh": "通用子智能体"
  },
  "gr.metaRead": {
    "en": "Read ",
    "zh": "读入 "
  },
  "gr.metaChars": {
    "en": " chars",
    "zh": " 字"
  },
  "gr.metaRounds": {
    "en": "Rounds ",
    "zh": "轮次 "
  },
  "gr.metaTools": {
    "en": "Tools ",
    "zh": "工具 "
  },
  "gr.metaSkills": {
    "en": "Skills ",
    "zh": "技能 "
  },
  "gr.starting": {
    "en": "Starting…",
    "zh": "正在启动…"
  },
  "gr.task": {
    "en": "Task",
    "zh": "任务"
  },
  "gr.goal": {
    "en": "Goal: ",
    "zh": "目标："
  },
  "gr.conclusion": {
    "en": "Conclusion: ",
    "zh": "结论："
  },
  "gr.viewFull": {
    "en": "Tap to view full process ▸",
    "zh": "点击查看全过程 ▸"
  },
  "gr.round": {
    "en": "Round",
    "zh": "第 "
  },
  "gr.thinkingSuffix": {
    "en": ": thinking…",
    "zh": " 轮：思考中…"
  },
  "gr.returned": {
    "en": " returned",
    "zh": " 返回"
  },
  "gr.subagent": {
    "en": "Subagent",
    "zh": "子智能体"
  },
  "gr.backendFailed": {
    "en": "Backend request failed",
    "zh": "后端请求失败"
  },
  "gr.noStream": {
    "en": "Browser does not support streaming responses",
    "zh": "浏览器不支持流式响应"
  },
  "gr.generating": {
    "en": "✍️ Generating answer…",
    "zh": "✍️ 正在生成回答…"
  },
  "gr.stoppedGen": {
    "en": "Generation stopped",
    "zh": "已停止生成"
  },
  "gr.stopped": {
    "en": "Stopped",
    "zh": "已停止"
  },
  "gr.cancelledGen": {
    "en": "Generation cancelled",
    "zh": "已取消生成"
  },
  "gr.runFailed": {
    "en": "⚠️ Agent execution failed: ",
    "zh": "⚠️ 智能体执行失败: "
  },
  "gr.noAnswer": {
    "en": "Failed to get an answer",
    "zh": "未能获取回答"
  },
  "gr.send": {
    "en": "Send",
    "zh": "发送"
  },
  "gr.neo4jOn": {
    "en": "Neo4j connected",
    "zh": "Neo4j 已连接"
  },
  "gr.connected": {
    "en": "Connected",
    "zh": "已连接"
  },
  "gr.disconnected": {
    "en": "Not connected",
    "zh": "未连接"
  },
  "gr.abortedToast": {
    "en": "Stopped: completed rounds are kept; ask a follow-up to continue",
    "zh": "已终止：本轮已完成的轮次保留，可继续追问让它接着跑"
  },
  "gr.abortFailed": {
    "en": "Stop failed: ",
    "zh": "终止失败："
  },
  "gr.networkError": {
    "en": "Network error",
    "zh": "网络错误"
  },
  "sub.title": {
    "en": "Subagent",
    "zh": "子智能体"
  },
  "sub.close": {
    "en": "Close",
    "zh": "关闭"
  },
  "sub.thinking": {
    "en": "Subagent thinking",
    "zh": "子智能体思考"
  },
  "sub.call": {
    "en": "Call ",
    "zh": "调用 "
  },
  "sub.resultSuffix": {
    "en": " result",
    "zh": " 结果"
  },
  "sub.task": {
    "en": "Task",
    "zh": "任务"
  },
  "sub.running": {
    "en": "Running…",
    "zh": "运行中…"
  },
  "sub.runningDot": {
    "en": "Running…",
    "zh": "运行中…"
  },
  "sub.done": {
    "en": "Done",
    "zh": "完成"
  },
  "sub.failed": {
    "en": "Failed",
    "zh": "失败"
  },
  "sub.maxRead": {
    "en": "Max read per call ",
    "zh": "单次读入上限 "
  },
  "sub.chars": {
    "en": " chars",
    "zh": " 字"
  },
  "sub.maxRounds": {
    "en": "Max rounds ",
    "zh": "最大轮次 "
  },
  "sub.availTools": {
    "en": "Available tools ",
    "zh": "可用工具 "
  },
  "sub.readSkills": {
    "en": "Readable skills ",
    "zh": "可读技能 "
  },
  "sub.countUnit": {
    "en": "",
    "zh": " 个"
  },
  "sub.profile": {
    "en": "Profile: ",
    "zh": "档案："
  },
  "sub.took": {
    "en": "Took",
    "zh": "用时"
  },
  "sub.roundsUnit": {
    "en": " rounds",
    "zh": " 轮"
  },
  "sub.toolsTimes": {
    "en": "Tools",
    "zh": "工具"
  },
  "sub.timesUnit": {
    "en": "x",
    "zh": " 次"
  },
  "sub.input": {
    "en": "Input (task delegated by the main agent)",
    "zh": "输入（主智能体派给它的任务）"
  },
  "sub.goal": {
    "en": "Goal: ",
    "zh": "目标："
  },
  "sub.context": {
    "en": "Context: ",
    "zh": "上下文："
  },
  "sub.noContext": {
    "en": "(No extra context)",
    "zh": "（无额外上下文）"
  },
  "sub.error": {
    "en": "Error: ",
    "zh": "错误："
  },
  "sub.statusBack": {
    "en": "Returned status: ",
    "zh": "回传状态："
  },
  "sub.gap": {
    "en": "Gap: ",
    "zh": "缺口："
  },
  "sub.confidence": {
    "en": "Confidence: ",
    "zh": "置信度："
  },
  "sub.process": {
    "en": "Reasoning & tool calls",
    "zh": "思考过程与工具调用"
  },
  "sub.steps": {
    "en": " steps, thinking ",
    "zh": " 步，思考 "
  },
  "sub.thinkSegments": {
    "en": " segments）",
    "zh": " 段）"
  },
  "sub.working1": {
    "en": "The subagent is working in its own context (calling tools and reasoning by itself);",
    "zh": "子智能体正在独立上下文里工作（它自己调工具、自己思考）；"
  },
  "sub.working2": {
    "en": "When done, its full reasoning and tool calls will appear here.",
    "zh": "完成后这里会显示它的完整思考与工具调用过程。"
  },
  "sub.conclusion": {
    "en": "Conclusion summary (returned to the main agent)",
    "zh": "结论摘要（回给主智能体）"
  },
  "remote.running": {
    "en": "Tunnel running · open directly on your phone",
    "zh": "隧道运行中 · 手机可直接打开"
  },
  "remote.off": {
    "en": "Tunnel not enabled",
    "zh": "隧道未开启"
  },
  "remote.notConfigured": {
    "en": "Remote access not configured",
    "zh": "未配置远程访问"
  },
  "remote.copied": {
    "en": "Copied",
    "zh": "已复制"
  },
  "remote.copy": {
    "en": "Copy",
    "zh": "复制"
  },
  "win.restore": {
    "en": "Restore",
    "zh": "还原"
  },
  "win.maximize": {
    "en": "Maximize",
    "zh": "最大化"
  },
  "ui.unknownTool": {
    "en": "Unknown tool",
    "zh": "未知工具"
  },
  "ui.input": {
    "en": "Input:",
    "zh": "输入:"
  },
  "ui.result": {
    "en": "Result:",
    "zh": "结果:"
  },
  "ui.returnedN": {
    "en": "Returned {n} records",
    "zh": "返回 {n} 条数据"
  },
  "ui.clipboardAdded": {
    "en": "Added {n} file(s) from clipboard",
    "zh": "已从剪贴板添加 {n} 个文件"
  },
  "bub.themeClassic": {
    "en": "Classic Blue",
    "zh": "经典蓝"
  },
  "bub.themeMidnight": {
    "en": "Midnight Black",
    "zh": "深夜黑"
  },
  "bub.themeEmerald": {
    "en": "Emerald Green",
    "zh": "翡翠绿"
  },
  "bub.themeWarm": {
    "en": "Warm Amber",
    "zh": "暖阳橙"
  },
  "bub.themeCyber": {
    "en": "Cyberpunk",
    "zh": "赛博风"
  },
  "bub.themePink": {
    "en": "Sakura Pink",
    "zh": "樱花粉"
  },
  "bub.themeMP": {
    "en": "Dream Pink",
    "zh": "梦境粉"
  },
  "bub.themeMG": {
    "en": "Dream Green",
    "zh": "梦境绿"
  },
  "bub.themeMV": {
    "en": "Dream Violet",
    "zh": "梦境紫"
  },
  "bub.themeMB": {
    "en": "Dream Blue",
    "zh": "梦境蓝"
  },
  "bub.unknown": {
    "en": "Unknown",
    "zh": "未知"
  },
  "bub.unsupported": {
    "en": "Unsupported format",
    "zh": "不支持的格式"
  },
  "bub.tooLarge": {
    "en": "File too large (>50MB)",
    "zh": "文件过大(>50MB)"
  },
  "bub.fileAdded": {
    "en": "File added: {name}",
    "zh": "文件已添加: {name}"
  },
  "bub.uploadedOpen": {
    "en": "Uploaded, click to open",
    "zh": "已上传，点击打开"
  },
  "bub.clickOpen": {
    "en": "Click to open",
    "zh": "点击打开"
  },
  "bub.remove": {
    "en": "Remove",
    "zh": "移除"
  },
  "bub.ft.image": {
    "en": "Image",
    "zh": "图片"
  },
  "bub.ft.svg": {
    "en": "SVG vector",
    "zh": "SVG矢量图"
  },
  "bub.ft.bmp": {
    "en": "Bitmap",
    "zh": "位图"
  },
  "bub.ft.tiff": {
    "en": "TIFF image",
    "zh": "TIFF图像"
  },
  "bub.ft.pdf": {
    "en": "PDF document",
    "zh": "PDF文档"
  },
  "bub.ft.docx": {
    "en": "Word document",
    "zh": "Word文档"
  },
  "bub.ft.txt": {
    "en": "Text file",
    "zh": "文本文件"
  },
  "bub.ft.rtf": {
    "en": "Rich text",
    "zh": "富文本"
  },
  "bub.ft.pptx": {
    "en": "PPT presentation",
    "zh": "PPT演示"
  },
  "bub.ft.odt": {
    "en": "Text document",
    "zh": "文本文档"
  },
  "bub.ft.json": {
    "en": "JSON data",
    "zh": "JSON数据"
  },
  "bub.ft.csv": {
    "en": "CSV data",
    "zh": "CSV数据"
  },
  "bub.ft.xlsx": {
    "en": "Excel sheet",
    "zh": "Excel表格"
  },
  "bub.ft.xml": {
    "en": "XML data",
    "zh": "XML数据"
  },
  "bub.ft.log": {
    "en": "Log file",
    "zh": "日志文件"
  },
  "bub.ft.toml": {
    "en": "TOML config",
    "zh": "TOML配置"
  },
  "bub.ft.ini": {
    "en": "INI config",
    "zh": "INI配置"
  },
  "bub.ft.c": {
    "en": "C",
    "zh": "C语言"
  },
  "bub.ft.html": {
    "en": "HTML page",
    "zh": "HTML网页"
  },
  "bub.ft.css": {
    "en": "CSS stylesheet",
    "zh": "CSS样式"
  },
  "bub.ft.sh": {
    "en": "Shell script",
    "zh": "Shell脚本"
  },
  "bub.ft.yaml": {
    "en": "YAML config",
    "zh": "YAML配置"
  },
  "bub.ft.sql": {
    "en": "SQL script",
    "zh": "SQL脚本"
  },
  "bub.ft.lua": {
    "en": "Lua script",
    "zh": "Lua脚本"
  },
  "bub.ft.r": {
    "en": "R",
    "zh": "R语言"
  },
  "bub.ft.pl": {
    "en": "Perl script",
    "zh": "Perl脚本"
  },
  "bub.ft.bat": {
    "en": "Batch script",
    "zh": "批处理"
  },
  "bub.fileDataNotFound": {
    "en": "File data not found",
    "zh": "文件数据未找到"
  },
  "bub.fileDownloaded": {
    "en": "✅ File \"{name}\" downloaded",
    "zh": "✅ 文件 \"{name}\" 已下载"
  },
  "bub.noFileData": {
    "en": "This bubble has no associated file data",
    "zh": "该气泡没有关联文件数据"
  },
  "bub.noBackendFile": {
    "en": "This bubble has no backend file; cannot open file location",
    "zh": "该气泡没有后端关联文件，无法打开文件地址"
  },
  "bub.confirmDelete": {
    "en": "Delete bubble \"{name}\"?",
    "zh": "确定要删除气泡\"{name}\"吗？"
  },
  "bub.linkFile": {
    "en": "Link file",
    "zh": "链接文件"
  },
  "bub.recognizedFile": {
    "en": "File recognized: {name}\\nPlease select it manually to upload (browser security prevents direct disk access)",
    "zh": "已识别文件: {name}\\n请手动选择该文件上传（浏览器安全限制，无法直接读取磁盘文件）"
  },
  "bub.recognizedFileShort": {
    "en": "File recognized: {name}\\nPlease select it manually to upload",
    "zh": "已识别文件: {name}\\n请手动选择该文件上传"
  },
  "xe.weather.sunny": {
    "en": "Sunny out ☀️",
    "zh": "出太阳了 ☀️"
  },
  "xe.weather.rain": {
    "en": "Raining 🌧️",
    "zh": "下雨了 🌧️"
  },
  "xe.weather.wind": {
    "en": "Windy 🍃",
    "zh": "刮风了 🍃"
  },
  "xe.weather.storm": {
    "en": "Thunderstorm ⛈️",
    "zh": "雷阵雨 ⛈️"
  },
  "xe.weather.hot": {
    "en": "Too hot, overheating 🔥",
    "zh": "太热了，红温 🔥"
  },
  "xe.weather.cold": {
    "en": "So cold, snowy ❄️",
    "zh": "好冷，下雪感 ❄️"
  },
  "xe.weather.pink": {
    "en": "Turned pink 💗",
    "zh": "变成粉色了 💗"
  },
  "xe.weather.green": {
    "en": "Turned green 🌿",
    "zh": "变成绿色了 🌿"
  },
  "xe.weather.purple": {
    "en": "Turned purple 💜",
    "zh": "变成紫色了 💜"
  },
  "xe.prefix": {
    "en": "Xiaoe: ",
    "zh": "小e: "
  },
  "xe.helloToast": {
    "en": "I'm Xiaoe ✨ Ask me about C4EAI features and usage",
    "zh": "我是小e ✨ 问我 C4EAI 的功能用法～"
  },
  "xe.hit1": {
    "en": "Stop hitting me!",
    "zh": "别敲啦！"
  },
  "xe.hit2": {
    "en": "Ouch ouch QAQ",
    "zh": "疼疼疼 QAQ"
  },
  "xe.hit3": {
    "en": "Hit me again and I'll rain on you!",
    "zh": "再敲我就下雨了！"
  },
  "xe.hit4": {
    "en": "Hmph, I'm ignoring you",
    "zh": "哼，不理你了"
  },
  "xe.rage": {
    "en": "I'm overheating!!! You're done for 🔥⚡",
    "zh": "我红温了！！！你完蛋啦 🔥⚡"
  },
  "xe.welcome": {
    "en": "Hi! I'm Xiaoe 👋<br>Ask me what C4EAI can do, or \"how to use the shared library\"",
    "zh": "嗨！我是小e 👋<br>问我 C4EAI 有什么功能，或者\"怎么用共享库\"～"
  },
  "xe.askPh": {
    "en": "Ask Xiaoe…",
    "zh": "问问小e…"
  },
  "xe.send": {
    "en": "Send",
    "zh": "发送"
  },
  "xe.noSave": {
    "en": "Xiaoe keeps no records — close and forget 💭",
    "zh": "小e 不保存任何记录，关掉就忘 💭"
  },
  "xe.thinking": {
    "en": "Let me think… 🤔",
    "zh": "让我想想… 🤔"
  },
  "xe.roleUser": {
    "en": "User: ",
    "zh": "用户："
  },
  "xe.roleAssistant": {
    "en": "Assistant: ",
    "zh": "助手："
  },
  "xe.distracted": {
    "en": "(Xiaoe got distracted…)",
    "zh": "（小e 走神了…）"
  },
  "xe.backendErr": {
    "en": "⚠️ Cannot reach backend: ",
    "zh": "⚠️ 连不上后端："
  },
  "xe.greet1": {
    "en": "I'm Xiaoe ✨ Tap to chat",
    "zh": "我是小e ✨ 点我聊天～"
  },
  "xe.greet2": {
    "en": "I'm Xiaoe ✨ Tap to chat, right-click for weather",
    "zh": "我是小e ✨ 点我聊天，右键换天气～"
  },
  "xe.on": {
    "en": "Enabled",
    "zh": "已开启"
  },
  "xe.off": {
    "en": "Disabled",
    "zh": "已关闭"
  },
  "misc.fileOpenBackend": {
    "en": "Open with backend app",
    "zh": "后端打开"
  },
  "misc.fileOpenBrowser": {
    "en": "Open in new browser tab",
    "zh": "浏览器新标签"
  },
  "misc.fileOpenFloat": {
    "en": "In-app floating preview",
    "zh": "应用内浮窗"
  },
  "misc.enteredChat": {
    "en": "Entered conversation view",
    "zh": "已进入对话界面"
  },
  "misc.img.title": {
    "en": "Image edit",
    "zh": "图片编辑"
  },
  "misc.img.crop": {
    "en": "✂️ Crop",
    "zh": "✂️ 裁剪"
  },
  "misc.img.apply": {
    "en": "✅ Apply",
    "zh": "✅ 应用"
  },
  "misc.img.close": {
    "en": "Close",
    "zh": "关闭"
  },
  "misc.img.hintZoom": {
    "en": "Drag the image corners to resize (keeps aspect ratio)",
    "zh": "拖曳图片四角可放大/缩小（保持比例）"
  },
  "misc.img.cropBtn": {
    "en": "✂️ Crop",
    "zh": "✂️ 裁剪"
  },
  "misc.img.hintCrop": {
    "en": "Drag the crop box to move; use the bottom-right handle to resize the crop area",
    "zh": "拖动裁剪框可移动，右下角缩放手柄可调裁剪范围"
  },
  "misc.img.zoomBtn": {
    "en": "↔️ Zoom",
    "zh": "↔️ 缩放"
  },
  "misc.img.hintZoom2": {
    "en": "Drag the corner handles to resize (keeps aspect ratio)",
    "zh": "拖曳图片四角手柄可放大/缩小（保持比例）"
  },
  "misc.img.original": {
    "en": "Original",
    "zh": "原始"
  },
  "ui.copy": {
    "en": "Copy",
    "zh": "复制"
  },
  "ui.delete": {
    "en": "Delete",
    "zh": "删除"
  },
  "sub.generic": {
    "en": "General subagent",
    "zh": "通用子智能体"
  },
  "bub.deleteTitle": {
    "en": "Delete",
    "zh": "删除"
  },
  "ui.emptyWelcome": {
    "en": "Hello! I am <b>TruthCross</b> (C4EAI TruthCross Agent) — a data-mining agent for Fenton literature mining and catalytic degradation knowledge graphs.<br>Please start a new conversation.",
    "zh": "您好！我是 <b>TruthCross</b>（C4EAI TruthCross Agent）—— 数据挖掘智能体，Fenton 文献数据挖掘与催化降解知识图谱方向。<br>\n                        请开始新的对话。"
  }
};

    window.t = function (key) {
        var e = JS[key];
        if (!e) return key;
        return lang === 'zh' ? (e.zh || e.en) : e.en;
    };
    window.c4eLang = function () { return lang; };
    window.setC4eLang = function (l, opts) {
        lang = (l === 'zh') ? 'zh' : 'en';
        try { localStorage.setItem(LS_KEY, lang); } catch (e) {}
        document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
        applyAll();
        if (!opts || !opts.silent) {
            window.dispatchEvent(new CustomEvent('c4e-lang-change', { detail: { lang: lang } }));
        }
    };

    function swap(el, attr, zhVal) {
        var cacheAttr = 'i18nEn' + (attr ? '_' + attr : '');
        var cur = attr ? el.getAttribute(attr) : el.textContent;
        if (!(cacheAttr in el.dataset)) el.dataset[cacheAttr] = cur;
        var v = (lang === 'zh' && zhVal != null) ? zhVal : el.dataset[cacheAttr];
        if (attr) el.setAttribute(attr, v); else el.textContent = v;
    }
    function applyAll() {
        var els = document.querySelectorAll('[data-i18n]');
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            var k = el.getAttribute('data-i18n');
            if (el.hasAttribute('data-i18n-html')) {
                // HTML 词条（如欢迎语含 <b>）：首应用缓存 en 原文，zh 用 innerHTML 渲染
                if (!('i18nEn' in el.dataset)) el.dataset.i18nEn = el.innerHTML;
                el.innerHTML = (lang === 'zh' && ZH[k] != null) ? ZH[k] : el.dataset.i18nEn;
            } else {
                swap(el, null, ZH[k]);
            }
        }
        var phs = document.querySelectorAll('[data-i18n-ph]');
        for (var j = 0; j < phs.length; j++) swap(phs[j], 'placeholder', ZH[phs[j].getAttribute('data-i18n-ph')]);
        var tis = document.querySelectorAll('[data-i18n-title]');
        for (var m = 0; m < tis.length; m++) swap(tis[m], 'title', ZH[tis[m].getAttribute('data-i18n-title')]);
    }
    window.c4eApplyI18n = applyAll;

    document.addEventListener('DOMContentLoaded', function () {
        document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
        applyAll();
    });
})();

/* 设置→界面 Language 段接线 */
document.addEventListener('DOMContentLoaded', function () {
    var group = document.getElementById('lang-group');
    if (!group) return;
    function sync() {
        var cur = window.c4eLang();
        var btns = group.querySelectorAll('.mode-seg-btn');
        for (var i = 0; i < btns.length; i++) btns[i].classList.toggle('active', btns[i].getAttribute('data-lang') === cur);
        var v = document.getElementById('lang-value');
        if (v) v.textContent = cur === 'zh' ? '中文' : 'English';
    }
    group.addEventListener('click', function (e) {
        var b = e.target.closest('.mode-seg-btn');
        if (!b) return;
        window.setC4eLang(b.getAttribute('data-lang'));
        sync();
    });
    window.addEventListener('c4e-lang-change', sync);
    sync();
});
