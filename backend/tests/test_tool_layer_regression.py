# -*- coding: utf-8 -*-
"""工具层回归测试 —— 把 HANDOFF §9「一次性跑过、没固化」的用例固化为可重复运行。

覆盖四类（全部纯函数级调用，不经 HTTP、不启停服务、不碰 8080）：
  1. 分页往返：file_tools._page_lines 按 has_more/next_offset 翻完，拼回逐字节等于原文；
     末段 has_more=False；超长单行不死循环而是给出改用 run_code/terminal 的出路。
  2. SSRF：web_tools._ip_is_internal / _internal_host_reason —— DOI 链接放行、内网地址拦截。
  3. AST 危险检查：code_sandbox._danger_reason —— re.compile/np.array 放行，subprocess/eval 等拦下。
  4. web_search 空结果分支：两源皆空时返回可解析结果，并区分「真没结果」与「工具故障」。

运行（项目根）： python -m unittest backend.tests.test_tool_layer_regression -v
或（backend 目录内）：python -m unittest tests.test_tool_layer_regression -v
"""
import ast
import asyncio
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

# 源码内部用绝对导入 `from tools.xxx import ...`（以 backend/ 为根）。
# 这里把 backend/ 加进 sys.path，保证无论从哪个 cwd 运行都能解析到真实实现。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from tools import web_tools, code_sandbox, file_tools  # noqa: E402
from tools.file_tools import _page_lines, READ_FILE_MAX_CHARS  # noqa: E402
from tools.web_tools import _ip_is_internal, _internal_host_reason  # noqa: E402
from tools.code_sandbox import _danger_reason  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 1. 分页往返
# ════════════════════════════════════════════════════════════════════
class TestPaginationRoundTrip(unittest.TestCase):
    """file_tools._page_lines 的往返一致性（对应 HANDOFF §9 第 2 条）。"""

    def _write_and_readlines(self, content):
        """写临时文件后按 read_file 完全相同的方式读回各行（f.readlines()）。"""
        d = tempfile.mkdtemp(prefix="c4eai_pg_")
        path = os.path.join(d, "sample.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()

    def test_round_trip_byte_equal(self):
        """1200 行文件按 has_more/next_offset 翻完，拼回必须与原文逐字节相等，末段 has_more=False。"""
        content = "".join(f"第 {i:04d} 行：abcdefghijabcdefghij\n" for i in range(1, 1201))
        lines = self._write_and_readlines(content)
        self.assertEqual(len(lines), 1200)

        parts = []
        offset = 1
        segments = 0
        while True:
            pg = _page_lines(lines, offset, 500)
            parts.append(pg["content"])
            segments += 1
            self.assertLess(segments, 50, "分页未收敛，疑似死循环")

            if not pg["has_more"]:
                # 末段：必须明确收尾
                self.assertFalse(pg["truncated"], "末段不应仍被判为截断")
                self.assertIsNone(pg["next_offset"])
                self.assertEqual(pg["total_lines"], 1200)
                break

            # 未到末尾：next_offset 必须存在且严格前进（不死循环）
            self.assertIsNotNone(pg["next_offset"])
            self.assertGreater(pg["next_offset"], offset)
            offset = pg["next_offset"]

        self.assertEqual(segments, 3, "1200 行 / limit=500 应恰好 3 段")
        self.assertEqual("".join(parts), content, "拼接结果必须与原文字节级相等")

    def test_superlong_single_line_no_livelock(self):
        """超长单行（5 万字符）不能让 next_offset 指回本行造成死循环，须给出改用 run_code/terminal 的出路。"""
        content = "A" * 50000  # 单行、无换行
        lines = self._write_and_readlines(content)
        self.assertEqual(len(lines), 1)

        pg = _page_lines(lines, 1, 500)
        self.assertTrue(pg["truncated"])
        # 关键：不提供会原地打转的 next_offset，has_more 直接为 False
        self.assertFalse(pg["has_more"])
        self.assertIsNone(pg["next_offset"])
        self.assertEqual(len(pg["content"]), READ_FILE_MAX_CHARS)
        # 提示必须指出可行出路，而不是让模型反复重试同一行
        self.assertIn("run_code", pg["note"])
        self.assertIn("terminal", pg["note"])


# ════════════════════════════════════════════════════════════════════
# 2. SSRF 判定
# ════════════════════════════════════════════════════════════════════
class TestSsrf(unittest.TestCase):
    """web_tools 的 SSRF 判定（对应 HANDOFF §9 第 3 条）。纯函数调用，不发网络请求。"""

    _INTERNAL = ["127.0.0.1", "192.168.1.1", "10.0.0.5", "169.254.169.254", "::1"]

    def test_ip_literals_flagged_internal(self):
        """内网/保留段 IP 字面量必须被判为内网（纯函数，无 DNS）。"""
        for host in self._INTERNAL:
            with self.subTest(host=host):
                self.assertIsNotNone(_ip_is_internal(host), f"{host} 应被判为内网")

    def test_internal_host_reason_blocks_literals(self):
        """内网 IP 经 _internal_host_reason 必须被拦截（返回非空原因）。"""
        for host in self._INTERNAL:
            with self.subTest(host=host):
                reason = asyncio.run(_internal_host_reason(host))
                self.assertIsNotNone(reason, f"{host} 应被拦截")

    def test_bracketed_ipv6_blocked(self):
        """URL 形态的 [::1]：hostname 取到 '::1' 后必须被拦截。"""
        host = urllib.parse.urlparse("http://[::1]:8080/x").hostname
        self.assertEqual(host, "::1")
        self.assertIsNotNone(asyncio.run(_internal_host_reason(host)))

    def test_localhost_and_suffix_blocked(self):
        """localhost 与 .internal 等内部名称必须被拦截（无 DNS）。"""
        self.assertIsNotNone(asyncio.run(_internal_host_reason("localhost")))
        self.assertIsNotNone(asyncio.run(_internal_host_reason("db.internal")))

    def test_doi_hosts_allowed(self):
        """DOI 链接必须放行 —— 判定只看 host，路径里的 '10.1021' 不得被误伤。

        旧实现对整个 URL 做子串匹配，会把 doi.org/10.1021/… 一律误判为内网（'10.' 命中），
        导致文献检索系统性瘫痪。这里用假 getaddrinfo 顶掉真实 DNS，保证纯函数化。
        """
        def fake_getaddrinfo(*args, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]  # 公网地址

        for url in ("https://doi.org/10.1021/xxx",
                    "https://pubs.acs.org/doi/xxx",
                    "https://rsc.org/doi/xxx"):
            host = urllib.parse.urlparse(url).hostname
            with self.subTest(url=url):
                with mock.patch.object(web_tools.socket, "getaddrinfo", fake_getaddrinfo):
                    reason = asyncio.run(_internal_host_reason(host))
                self.assertIsNone(reason, f"{url} 应放行，实际被拒：{reason}")


# ════════════════════════════════════════════════════════════════════
# 3. AST 危险检查
# ════════════════════════════════════════════════════════════════════
class TestAstDangerCheck(unittest.TestCase):
    """code_sandbox._danger_reason（对应 HANDOFF §9 第 4 条）。纯 AST 扫描，不执行代码。"""

    def _reason(self, code):
        return _danger_reason(ast.parse(code))

    def test_safe_code_passes(self):
        """re.compile / model.compile / numpy 计算必须放行（旧字符串黑名单会误杀 compile）。"""
        safe = [
            "import re\np = re.compile(r'a+')\n",
            "model.compile(optimizer='adam')",
            "import numpy as np\nx = np.array([1, 2, 3])\ny = x * 2\n",
        ]
        for code in safe:
            with self.subTest(code=code):
                self.assertIsNone(self._reason(code), f"正常代码被误杀：{code!r}")

    def test_dangerous_code_blocked(self):
        """subprocess / os.system / 裸 compile / eval / __import__ / os.remove 必须拦下。"""
        dangerous = [
            "import subprocess\n",
            "import os\nos.system('whoami')\n",
            "compile('1 + 1', '<s>', 'eval')",
            "eval('1 + 1')",
            "__import__('os')",
            "import os\nos.remove('/tmp/x')\n",
        ]
        for code in dangerous:
            with self.subTest(code=code):
                self.assertIsNotNone(self._reason(code), f"危险代码未被拦下：{code!r}")


# ════════════════════════════════════════════════════════════════════
# 4. web_search 空结果分支
# ════════════════════════════════════════════════════════════════════
class TestWebSearchEmptyBranch(unittest.TestCase):
    """web_tools.web_search：两源皆空时不得返回 None，且要区分「没结果」与「工具故障」。"""

    def _run_search(self, bing_fn, ddgs_fn):
        with mock.patch.object(web_tools, "_bing_search", bing_fn), \
             mock.patch.object(web_tools, "_ddgs_search", ddgs_fn):
            return asyncio.run(web_tools.web_search("c4eai-nonexistent-query"))

    def test_both_sources_empty_is_no_result_not_fault(self):
        """两个源都正常但没命中 → 可解析的结果，且明确「不是工具故障」。"""
        out = self._run_search(lambda q, l: [], lambda q, l: [])
        # 关键回归：绝不能是 None
        self.assertIsNotNone(out)
        data = json.loads(out)  # 必须可解析
        self.assertEqual(data["source"], "none")
        self.assertEqual(data["count"], 0)
        self.assertNotIn("error", data, "「真没结果」不应报故障")
        self.assertIn("故障", data["note"])

    def test_both_sources_fail_is_fault(self):
        """两个源都抛异常 → 明确标记为工具故障，文案与「没结果」不同。"""
        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        out = self._run_search(boom, boom)
        self.assertIsNotNone(out)
        data = json.loads(out)
        self.assertEqual(data["source"], "none")
        self.assertIn("error", data)
        self.assertIn("不可用", data["error"])

    def test_two_branches_text_differs(self):
        """两种情况的文案必须能区分（否则模型无法判断该换词还是该放弃重试）。"""
        empty = json.loads(self._run_search(lambda q, l: [], lambda q, l: []))

        def boom(*a, **k):
            raise RuntimeError("network down")

        fault = json.loads(self._run_search(boom, boom))
        self.assertNotEqual(json.dumps(empty, sort_keys=True, ensure_ascii=False),
                            json.dumps(fault, sort_keys=True, ensure_ascii=False))


# ════════════════════════════════════════════════════════════════════
# 5. 明确 skip 的用例（不假装通过）
# ════════════════════════════════════════════════════════════════════
@unittest.skip("需要真实 LLM 调用：agent.py 工具循环触顶后『不带 tools 再发一次强制收尾』"
               "无法离线复现，必须接入真实模型后才可验证，故显式跳过。")
class TestToolLoopForcedFinish(unittest.TestCase):
    def test_forced_finish_after_max_rounds(self):
        # 该用例若被误认为通过，会掩盖「触顶丢数据」这一真实缺陷，故用 skip 而非 pass。
        self.fail("此用例只在接入真实模型时有效；skip 状态不得视为通过。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
