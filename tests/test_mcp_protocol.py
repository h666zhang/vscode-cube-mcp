#!/usr/bin/env python3
"""MCP 协议级冒烟:用官方 stdio 客户端连本 server,验证"作为 MCP server 真的能用"。

为什么单独一个文件:其余测试都是**直接调 Python 函数**(`cubemx_mcp.cubemx_intent(...)`),
证明不了 server 入口、工具注册、JSON-RPC 序列化这些协议层的东西 —— 那些只有真连一次才验得到
(模型侧接入用的正是这条链路)。

只调**纯本地**能力(cubemx_help / 参数校验),不启动 STM32CubeMX,所以默认就跑(约几秒)。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - 依赖缺失时跳过
    ClientSession = None

SRC = Path(__file__).resolve().parent.parent / "src"

EXPECTED_TOOLS = {
    "cubemx_help",
    "cubemx_script",
    "cubemx_load",
    "cubemx_configure",
    "cubemx_generate",
    "cubemx_export_pinout",
    "cubemx_new_project",
    "cubemx_probe",
    "cubemx_intent",
    "cubemx_remove_peripheral",
}


@unittest.skipUnless(ClientSession is not None, "需要 mcp 包(mcp>=2.0.0)")
class TestMcpProtocol(unittest.TestCase):
    """起一个真实的 server 子进程,走 stdio + JSON-RPC 调它。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        # 白名单收窄到临时目录:这样下面那个"非法路径"用例一定是被拒绝,不受本机环境影响
        self.env = {
            **os.environ,
            "PYTHONPATH": str(SRC),
            "ST_CUBEMX_ALLOWED_ROOTS": self._td.name,
        }

    def _session_call(self, calls):
        """连一次 server,按顺序执行 calls([(tool, args), ...]),返回 (工具名集合, 结果列表)。"""
        results = []

        async def run():
            params = StdioServerParameters(command=sys.executable, args=["-m", "vscode_cube_mcp"], env=self.env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    for tool, args in calls:
                        results.append(await session.call_tool(tool, args))
                    return {t.name for t in tools.tools}

        return asyncio.run(run()), results

    @staticmethod
    def _text(result) -> str:
        return "".join(c.text for c in result.content if hasattr(c, "text"))

    def test_lists_all_tools_and_help_through_protocol(self):
        names, results = self._session_call([("cubemx_help", {"topic": "dma"})])
        self.assertEqual(names, EXPECTED_TOOLS)
        self.assertFalse(results[0].is_error)
        self.assertIn("DMA", self._text(results[0]))

    def test_tool_error_is_reported_not_crashing(self):
        # 参数校验失败(ToolError)在协议层应是 is_error=True + 可读文本,而不是把连接搞崩
        _, results = self._session_call([("cubemx_load", {"ioc": "C:/definitely/not/here.ioc"})])
        self.assertTrue(results[0].is_error)
        self.assertIn("PATH_NOT_ALLOWED", self._text(results[0]))

    def test_intent_rejects_unknown_peripheral_through_protocol(self):
        ioc = os.path.join(self._td.name, "x.ioc")
        with open(ioc, "w", encoding="utf-8") as f:
            f.write("Mcu.IPNb=0\nboard=custom\n")
        _, results = self._session_call([("cubemx_intent", {"ioc": ioc, "intents": [{"peripheral": "FOO9"}]})])
        self.assertTrue(results[0].is_error)
        self.assertIn("UNKNOWN_PERIPHERAL", self._text(results[0]))


if __name__ == "__main__":
    unittest.main()
