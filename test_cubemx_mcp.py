"""Vscode_cube_mcp 单元测试(标准库 unittest,不依赖 CubeMX 实体)。

运行: python -m unittest test_cubemx_mcp -v
"""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

import cubemx_mcp


class TestCleanup(unittest.TestCase):
    def test_filters_noise_and_keeps_content(self):
        raw = (
            "2026-08-05 12:00:00,123 [INFO] something\n"
            "Picked up JAVA_TOOL_OPTIONS: -Dfile.encoding=UTF-8\n"
            "log4j:WARN No appenders could be found\n"
            "config load \"C:/proj.ioc\"\n"
            "OK\n"
            "\n"
        )
        self.assertEqual(cubemx_mcp._cleanup(raw), 'config load "C:/proj.ioc"\nOK')

    def test_empty_input(self):
        self.assertEqual(cubemx_mcp._cleanup(""), "")


class TestCheckPath(unittest.TestCase):
    def setUp(self):
        self._orig = cubemx_mcp.ALLOWED_ROOTS
        cubemx_mcp.ALLOWED_ROOTS = [r"C:\MINE\Projects"]

    def tearDown(self):
        cubemx_mcp.ALLOWED_ROOTS = self._orig

    def test_inside_allowlist(self):
        ap = cubemx_mcp._check_path(r"C:\MINE\Projects\TEST\TEST.ioc")
        self.assertTrue(ap.lower().endswith("test.ioc"))

    def test_outside_allowlist_rejected(self):
        with self.assertRaises(ValueError):
            cubemx_mcp._check_path(r"C:\Windows\System32\evil.ioc")

    def test_case_insensitive_windows(self):
        ap = cubemx_mcp._check_path(r"c:\mine\projects\X\Y.ioc")
        self.assertTrue(ap.lower().endswith("y.ioc"))


class TestIocPath(unittest.TestCase):
    def test_missing_file_raises(self):
        cubemx_mcp.ALLOWED_ROOTS = [tempfile.gettempdir()]
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp._ioc_path(os.path.join(tempfile.gettempdir(), "no_such_file.ioc"))
        finally:
            cubemx_mcp.ALLOWED_ROOTS = [os.getcwd()]

    def test_non_ioc_extension_raises(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        cubemx_mcp.ALLOWED_ROOTS = [tempfile.gettempdir()]
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp._ioc_path(path)
        finally:
            os.remove(path)
            cubemx_mcp.ALLOWED_ROOTS = [os.getcwd()]


class TestConfig(unittest.TestCase):
    def test_find_cubemx_respects_env(self):
        with mock.patch.dict(os.environ, {"ST_CUBEMX_EXE": r"C:\custom\STM32CubeMX.exe"}):
            self.assertEqual(cubemx_mcp._find_cubemx(), r"C:\custom\STM32CubeMX.exe")

    def test_find_cubemx_fallback_nonempty(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ST_CUBEMX_EXE", None)
            self.assertTrue(cubemx_mcp._find_cubemx())

    def test_allowed_roots_parses_env(self):
        with mock.patch.dict(os.environ, {"ST_CUBEMX_ALLOWED_ROOTS": r"C:\A;C:\B"}):
            self.assertEqual(cubemx_mcp._allowed_roots(), [r"C:\A", r"C:\B"])

    def test_allowed_roots_default_cwd(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ST_CUBEMX_ALLOWED_ROOTS", None)
            self.assertEqual(cubemx_mcp._allowed_roots(), [os.getcwd()])


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestRunScript(unittest.TestCase):
    def test_ok_output(self):
        with mock.patch("cubemx_mcp.subprocess.run", return_value=_FakeProc(0, "OK\noutput\n")):
            r = cubemx_mcp._run_script("config load x")
        self.assertTrue(r["ok"])
        self.assertIn("output", r["output"])

    def test_ko_marker_detected(self):
        with mock.patch("cubemx_mcp.subprocess.run", return_value=_FakeProc(0, "OK\nKO\n")):
            r = cubemx_mcp._run_script("set bad")
        self.assertFalse(r["ok"])

    def test_nonzero_exit_detected(self):
        with mock.patch("cubemx_mcp.subprocess.run", return_value=_FakeProc(1, "boom")):
            r = cubemx_mcp._run_script("x")
        self.assertFalse(r["ok"])

    def test_timeout_path(self):
        with mock.patch("cubemx_mcp.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            r = cubemx_mcp._run_script("x")
        self.assertFalse(r["ok"])
        self.assertIn("TIMEOUT", r["output"])

    def test_temp_script_cleaned(self):
        before = set(os.listdir(tempfile.gettempdir()))
        with mock.patch("cubemx_mcp.subprocess.run", return_value=_FakeProc(0, "OK\n")):
            cubemx_mcp._run_script("config load x")
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()