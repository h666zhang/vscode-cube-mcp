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


# 构造一个含多外设的最小 .ioc 文本(仿 6.18 格式)
SAMPLE_IOC = """#MicroXplorer Configuration settings - do not modify
File.Version=6
Mcu.IP0=NVIC
Mcu.IP1=RCC
Mcu.IP2=SYS
Mcu.IP3=TIM2
Mcu.IP4=TIM3
Mcu.IPNb=5
Mcu.Pin0=VP_SYS_VS_Systick
Mcu.Pin1=VP_TIM3_VS_ClockSourceINT
Mcu.PinsNb=2
NVIC.TIM3_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:true\\:true
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false,2-MX_GPIO_Init-GPIO-false-HAL-true,3-MX_TIM2_Init-TIM2-false-HAL-true,4-MX_TIM3_Init-TIM3-false-HAL-true
TIM3.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE
TIM3.CounterMode=TIM_COUNTERMODE_UP
TIM3.Period=10000-1
VP_TIM3_VS_ClockSourceINT.Mode=Internal
VP_TIM3_VS_ClockSourceINT.Signal=TIM3_VS_ClockSourceINT
board=custom
"""


class TestRemovePeripheral(unittest.TestCase):
    def _write_ioc(self, content):
        fd, path = tempfile.mkstemp(suffix=".ioc")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def setUp(self):
        self._orig = cubemx_mcp.ALLOWED_ROOTS
        cubemx_mcp.ALLOWED_ROOTS = [tempfile.gettempdir()]

    def tearDown(self):
        cubemx_mcp.ALLOWED_ROOTS = self._orig

    def test_remove_peripheral_cleans_all(self):
        path = self._write_ioc(SAMPLE_IOC)
        try:
            out = cubemx_mcp.cubemx_remove_peripheral(path, "TIM3")
            self.assertIn("TIM3", out)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            # TIM3 的所有痕迹应消失
            self.assertNotIn("TIM3", text)
            # 剩余外设正确重排:NVIC/RCC/SYS/TIM2
            self.assertIn("Mcu.IP0=NVIC", text)
            self.assertIn("Mcu.IP3=TIM2", text)
            self.assertIn("Mcu.IPNb=4", text)
            # functionlistsort 不再含 TIM3
            self.assertNotIn("MX_TIM3_Init", text)
            # NVIC 行应还在(仅 TIM3 的中断被删)
            self.assertIn("Mcu.IP1=RCC", text)
        finally:
            os.remove(path)

    def test_remove_missing_peripheral_raises(self):
        path = self._write_ioc(SAMPLE_IOC)
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_remove_peripheral(path, "USART1")
        finally:
            os.remove(path)


class TestAddSource(unittest.TestCase):
    def _make_project(self):
        root = tempfile.mkdtemp()
        cmake_dir = os.path.join(root, "cmake", "stm32cubemx")
        os.makedirs(cmake_dir)
        with open(os.path.join(root, "proj.ioc"), "w", encoding="utf-8") as f:
            f.write("#MicroXplorer Configuration settings - do not modify\n")
        lists = """set(MX_Application_Src
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Core/Src/main.c
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Core/Src/stm32f1xx_it.c
)
"""
        with open(os.path.join(cmake_dir, "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write(lists)
        return root

    def setUp(self):
        self._orig = cubemx_mcp.ALLOWED_ROOTS
        cubemx_mcp.ALLOWED_ROOTS = [tempfile.gettempdir()]

    def tearDown(self):
        cubemx_mcp.ALLOWED_ROOTS = self._orig

    def test_add_source_inserts_once_and_idempotent(self):
        root = self._make_project()
        try:
            ioc = os.path.join(root, "proj.ioc")
            out = cubemx_mcp.cubemx_add_source(ioc, "Core/Src/OLED.c")
            self.assertIn("OLED.c", out)
            lists_path = os.path.join(root, "cmake", "stm32cubemx", "CMakeLists.txt")
            with open(lists_path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("../../Core/Src/OLED.c", text)
            self.assertEqual(text.count("OLED.c"), 1)
            # 幂等:再次调用不重复
            out2 = cubemx_mcp.cubemx_add_source(ioc, "Core/Src/OLED.c")
            self.assertIn("已在源列表", out2)
            with open(lists_path, encoding="utf-8") as f:
                text2 = f.read()
            self.assertEqual(text2.count("OLED.c"), 1)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_add_source_missing_cmake_raises(self):
        root = self._make_project()
        try:
            os.remove(os.path.join(root, "cmake", "stm32cubemx", "CMakeLists.txt"))
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_add_source(os.path.join(root, "proj.ioc"), "Core/Src/OLED.c")
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()