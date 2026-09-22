#!/usr/bin/env bash
# C4EAI 快速自检（canonical check）—— 提交前跑一次，秒级完成。
#   bash scripts/check.sh
# 覆盖：Python 语法 / JS 语法 / 关键不变量。
# 不含浏览器真机验证（需起实例 + Edge CDP，见 HANDOFF.md「验证方法」）。
set -u
cd "$(dirname "$0")/.."
PY="${C4EAI_PYTHON:-<ANACONDA-DIR>/python.exe}"
# node 是 Windows 程序，读不了 MSYS 的 /c/... 路径 → 用 cygpath 转成 C:\...
TMPDIR_WIN="$(cygpath -w "${TMPDIR:-/tmp}" 2>/dev/null || echo "$TEMP")"
CHK="$TMPDIR_WIN\\c4eai_chk.js"
fail=0
ok()  { echo "  OK   $1"; }
bad() { echo "  FAIL $1"; fail=$((fail+1)); }

echo "== Python 语法 =="
for f in backend/*.py backend/agent/*.py backend/tools/*.py; do
    [ -f "$f" ] || continue
    if env -u PYTHONPATH "$PY" -m py_compile "$f" 2>/dev/null; then ok "$f"; else bad "$f"; fi
done

echo "== JS 语法 =="
for f in js/*.js; do
    [ -f "$f" ] || continue
    cp "$f" "$CHK"
    if node --check "$CHK" 2>/dev/null; then ok "$f"; else bad "$f"; fi
done
rm -f "$CHK"

echo "== 关键不变量 =="
chk() { if grep -qF "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3"; fi; }
# 手机端：隐藏侧栏箭头（与抽屉行为冲突）
chk css/mobile.css 'sidebar-toggle-btn { display: none' 'mobile.css 隐藏 sidebar-toggle-btn'
# 小e 手机端可拖：touch-action:none 是必要条件
chk css/xiaoe.css 'touch-action: none' 'xiaoe.css touch-action:none'
chk js/xiaoe.js  'pointercancel'        'xiaoe.js pointercancel 兜底'
# 小e 随机天气：2.5%，且屏幕上不标注
chk js/xiaoe.js  'WEATHER_CHANCE = 0.025' 'xiaoe.js 概率 0.025'
# 免密钥聊天：只查 model 字段
chk backend/app.py 'req.model.get("model")' 'app.py 免密钥判据'
# 附件多选：笔记编辑器动态 input 必须 multiple（曾漏改 → 用户无法多选）
chk js/messages.js 'inp.multiple = true' 'messages.js 笔记附件支持多选'
# 全仓不应再有只取 files[0] 的附件上传（image_tools 单图工具例外）
_stray="$(grep -rn 'files\[0\]\|files?\.\[0\]' js/*.js 2>/dev/null | grep -v '^js/image_tools.js' || true)"
if [ -z "$_stray" ]; then ok '无遗漏的单选 files[0]'; else bad "仍有单选: $_stray"; fi

echo
if [ "$fail" -eq 0 ]; then echo "ALL_PASS ✅"; else echo "FAILED ($fail)"; fi
exit "$fail"
