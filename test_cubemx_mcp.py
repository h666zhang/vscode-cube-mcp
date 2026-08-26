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

    def test_prefix_sibling_rejected(self):
        # C:\MINE\ProjectsX 不应通过 C:\MINE\Projects 白名单(前缀绕过漏洞回归)
        with self.assertRaises(ValueError):
            cubemx_mcp._check_path(r"C:\MINE\ProjectsX\evil.ioc")


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
    """模拟 Popen:communicate(timeout) 返回 (stdout, stderr),带 returncode;可配置超时异常。"""

    def __init__(self, returncode=0, stdout="", stderr="", timeout_error=None):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout_error = timeout_error

    def communicate(self, timeout=None):
        if self._timeout_error is not None:
            raise self._timeout_error
        return self._stdout, self._stderr


class TestRunScript(unittest.TestCase):
    def test_ok_output(self):
        with mock.patch("cubemx_mcp.subprocess.Popen", return_value=_FakeProc(0, "OK\noutput\n")):
            r = cubemx_mcp._run_script("config load x")
        self.assertTrue(r["ok"])
        self.assertIn("output", r["output"])

    def test_ko_marker_detected(self):
        with mock.patch("cubemx_mcp.subprocess.Popen", return_value=_FakeProc(0, "OK\nKO\n")):
            r = cubemx_mcp._run_script("set bad")
        self.assertFalse(r["ok"])

    def test_nonzero_exit_detected(self):
        with mock.patch("cubemx_mcp.subprocess.Popen", return_value=_FakeProc(1, "boom")):
            r = cubemx_mcp._run_script("x")
        self.assertFalse(r["ok"])

    def test_timeout_path(self):
        with mock.patch("cubemx_mcp.subprocess.Popen",
                        return_value=_FakeProc(0, timeout_error=subprocess.TimeoutExpired("cmd", 1))):
            r = cubemx_mcp._run_script("x")
        self.assertFalse(r["ok"])
        self.assertIn("TIMEOUT", r["output"])

    def test_timeout_kills_process_tree(self):
        fake = _FakeProc(0, timeout_error=subprocess.TimeoutExpired("cmd", 1))
        with mock.patch("cubemx_mcp.subprocess.Popen", return_value=fake):
            with mock.patch("cubemx_mcp._kill_process_tree") as kill:
                cubemx_mcp._run_script("x")
        kill.assert_called_once_with(fake)

    def test_temp_script_cleaned(self):
        before = set(os.listdir(tempfile.gettempdir()))
        with mock.patch("cubemx_mcp.subprocess.Popen", return_value=_FakeProc(0, "OK\n")):
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

    def test_remove_peripheral_updates_pinsnb(self):
        # 删外设后其 VP 引脚必须移除,Mcu.Pin 重排、Mcu.PinsNb 修正
        path = self._write_ioc(SAMPLE_IOC)
        try:
            cubemx_mcp.cubemx_remove_peripheral(path, "TIM3")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Mcu.Pin0=VP_SYS_VS_Systick", text)
            self.assertNotIn("VP_TIM3", text)
            self.assertIn("Mcu.PinsNb=1", text)
            self.assertNotIn("Mcu.Pin1=", text)  # 引脚序号已重排,无残留 Pin1
        finally:
            os.remove(path)

    def test_remove_invalid_peripheral_name_raises(self):
        path = self._write_ioc(SAMPLE_IOC)
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_remove_peripheral(path, "TIM3;DROP")
        finally:
            os.remove(path)

    def test_remove_peripheral_ip_last_line_no_newline(self):
        # 外设 IP 条目是文件最后一行且无换行时,仍能定位并删除
        path = self._write_ioc(SAMPLE_IOC + "Mcu.IP5=USART1")
        try:
            cubemx_mcp.cubemx_remove_peripheral(path, "USART1")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("USART1", text)
            self.assertIn("Mcu.IPNb=5", text)
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

    def test_add_source_rejects_traversal(self):
        root = self._make_project()
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_add_source(os.path.join(root, "proj.ioc"), "../outside.c")
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_add_source(os.path.join(root, "proj.ioc"), "Core/../evil.c")
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_add_source_rejects_absolute_and_newline(self):
        root = self._make_project()
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_add_source(os.path.join(root, "proj.ioc"), r"C:\evil\OLED.c")
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_add_source(os.path.join(root, "proj.ioc"), "Core/Src/OLED.c\nset(OTHER evil)")
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_add_source_no_anchor_raises(self):
        # 两个锚点(main.c/stm32f1xx_it.c)都不存在时必须报错,不能静默假成功
        root = self._make_project()
        try:
            lists_path = os.path.join(root, "cmake", "stm32cubemx", "CMakeLists.txt")
            with open(lists_path, "w", encoding="utf-8") as f:
                f.write("set(MX_Application_Src\n    ${CMAKE_CURRENT_SOURCE_DIR}/../../Core/Src/other.c\n)\n")
            with self.assertRaises(ValueError):
                cubemx_mcp.cubemx_add_source(os.path.join(root, "proj.ioc"), "Core/Src/OLED.c")
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


class TestHelp(unittest.TestCase):
    def test_help_returns_full_guide(self):
        out = cubemx_mcp.cubemx_help()
        self.assertIn("cubemx_new_project", out)
        self.assertIn("从零生成工程", out)
        self.assertIn("已知坑", out)

    def test_help_templates_lists_available(self):
        # 副本 templates/ 下有 3 个 F103 模板,动态扫描应列出
        out = cubemx_mcp.cubemx_help(topic="templates")
        self.assertIn("可用模板", out)
        self.assertIn("STM32F103C8T6.ioc", out)

    def test_help_topics_case_insensitive(self):
        out = cubemx_mcp.cubemx_help(topic="TIM")
        self.assertIn("内部时钟标准表达", out)
        out2 = cubemx_mcp.cubemx_help(topic="gpio")
        self.assertIn("set pin PB13 GPIO_Output", out2)

    def test_help_unknown_topic_returns_full_guide(self):
        out = cubemx_mcp.cubemx_help(topic="bogus")
        self.assertIn("工具一览", out)

    def test_missing_template_error_lists_available(self):
        # 请求不存在的芯片模板时,错误信息应列出可用模板并给自举指引
        with self.assertRaises(ValueError) as ctx:
            cubemx_mcp._project_template("STM32F407VGT6")
        msg = str(ctx.exception)
        self.assertIn("可用模板", msg)
        self.assertIn("STM32F103C8T6.ioc", msg)
        self.assertIn("cubemx_help", msg)


# 薄种子 + TIM 内部时钟片段注入测试(0.4.0)
SEED_IOC = """#MicroXplorer Configuration settings - do not modify
File.Version=6
Mcu.CPN=STM32F103C8T6
Mcu.Family=STM32F1
Mcu.IP0=NVIC
Mcu.IP1=RCC
Mcu.IP2=SYS
Mcu.IPNb=3
Mcu.Name=STM32F103C(8-B)Tx
Mcu.Package=LQFP48
Mcu.Pin0=PA13
Mcu.Pin1=VP_SYS_VS_Systick
Mcu.PinsNb=2
Mcu.UserName=STM32F103C8Tx
MxCube.Version=6.18.0
MxDb.Version=DB.6.0.180
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false
board=custom
"""


class TestInjectTimInternalClock(unittest.TestCase):
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

    def test_inject_tim2_internal_clock(self):
        path = self._write_ioc(SEED_IOC)
        try:
            out = cubemx_mcp._inject_tim_internal_clock(path, "TIM2")
            self.assertIn("TIM2", out)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            # IP 追加 + IPNb 同步
            self.assertIn("Mcu.IP3=TIM2", text)
            self.assertIn("Mcu.IPNb=4", text)
            # VP 条目内联进 Pin 列表 + PinsNb 同步
            self.assertIn("Mcu.Pin2=VP_TIM2_VS_ClockSourceINT", text)
            self.assertIn("Mcu.PinsNb=3", text)
            # 参数 + VP 表达 + NVIC
            self.assertIn("TIM2.Prescaler=7200-1", text)
            self.assertIn("TIM2.Period=10000-1", text)
            self.assertIn("VP_TIM2_VS_ClockSourceINT.Mode=Internal", text)
            self.assertIn("NVIC.TIM2_IRQn=", text)
            # functionlistsort 追加段
            self.assertIn("2-MX_TIM2_Init-TIM2-false-HAL-true", text)
        finally:
            os.remove(path)

    def test_inject_idempotent(self):
        path = self._write_ioc(SEED_IOC)
        try:
            cubemx_mcp._inject_tim_internal_clock(path, "TIM2")
            out2 = cubemx_mcp._inject_tim_internal_clock(path, "TIM2")
            self.assertIn("无需注入", out2)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(text.count("TIM2.IPParameters"), 1)
        finally:
            os.remove(path)

    def test_inject_replaces_existing_etr(self):
        # 已有 ETR 表达(SH/TIM2.* 参数)时,注入应替换而非叠加
        etr = SEED_IOC + "SH.S_TIM2_CH1_ETR.0=TIM2_ETR,ClockSourceETR_Mode2\nTIM2.ClockFilter=0x0f\n"
        path = self._write_ioc(etr)
        try:
            cubemx_mcp._inject_tim_internal_clock(path, "TIM2")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("SH.S_TIM2", text)
            self.assertNotIn("ClockFilter", text)
            self.assertIn("VP_TIM2_VS_ClockSourceINT.Mode=Internal", text)
        finally:
            os.remove(path)

    def test_inject_custom_prescaler_period_no_irq(self):
        path = self._write_ioc(SEED_IOC)
        try:
            cubemx_mcp._inject_tim_internal_clock(path, "TIM3", prescaler=72, period=1000, irq=False)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Mcu.IP3=TIM3", text)
            self.assertIn("TIM3.Prescaler=72-1", text)
            self.assertIn("TIM3.Period=1000-1", text)
            self.assertNotIn("NVIC.TIM3_IRQn", text)
        finally:
            os.remove(path)

    def test_inject_invalid_tim_raises(self):
        path = self._write_ioc(SEED_IOC)
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp._inject_tim_internal_clock(path, "FOO")
        finally:
            os.remove(path)

    def test_inject_functionlistsort_single_seg(self):
        # functionlistsort 只剩 TIM 段(无 SystemClock_Config)时,注入后不产生重复段
        ioc = SEED_IOC.replace("ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false",
                               "ProjectManager.functionlistsort=3-MX_TIM2_Init-TIM2-false-HAL-true")
        path = self._write_ioc(ioc)
        try:
            cubemx_mcp._inject_tim_internal_clock(path, "TIM2")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(text.count("MX_TIM2_Init"), 1)
        finally:
            os.remove(path)

    def test_inject_missing_ipnb_pinsnb(self):
        # 非标准 .ioc 缺 IPNb/PinsNb 行时,注入不丢数据(末尾补全)
        ioc = SEED_IOC.replace("Mcu.IPNb=3\n", "").replace("Mcu.PinsNb=2\n", "")
        path = self._write_ioc(ioc)
        try:
            cubemx_mcp._inject_tim_internal_clock(path, "TIM2")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Mcu.IP3=TIM2", text)
            self.assertIn("Mcu.IPNb=4", text)
            self.assertIn("Mcu.Pin2=VP_TIM2_VS_ClockSourceINT", text)
            self.assertIn("Mcu.PinsNb=3", text)
        finally:
            os.remove(path)


class TestInjectTimPwm(unittest.TestCase):
    """PWM 注入器回归测试(0.4.1)。"""

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

    def test_inject_tim3_pwm(self):
        path = self._write_ioc(SEED_IOC)
        try:
            out = cubemx_mcp._inject_tim_pwm(path, "TIM3", "PA6", "S_TIM3_CH1",
                                              prescaler=72, period=100, pulse=50)
            self.assertIn("TIM3", out)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            # IP 追加 + IPNb 同步
            self.assertIn("Mcu.IP3=TIM3", text)
            self.assertIn("Mcu.IPNb=4", text)
            # 引脚 + 信号 + SH 行(权威枚举名带序号)
            self.assertIn("Mcu.Pin2=PA6", text)
            self.assertIn("Mcu.PinsNb=3", text)
            self.assertIn("PA6.Mode=PWM Generation1 CH1", text)
            self.assertIn("PA6.Signal=S_TIM3_CH1", text)
            self.assertIn("SH.S_TIM3_CH1.0=TIM3_CH1,PWM Generation1 CH1", text)
            self.assertIn("SH.S_TIM3_CH1.ConfNb=1", text)
            # 参数
            self.assertIn("TIM3.OCMode=TIM_OCMODE_PWM1", text)
            self.assertIn("TIM3.Period=100-1", text)
            self.assertIn("TIM3.Prescaler=72-1", text)
            self.assertIn("TIM3.Pulse=50", text)
            # Channel 键(黄金样本:空格转义 \ ,值 TIM_CHANNEL_1)
            self.assertIn("TIM3.Channel-PWM\\ Generation1\\ CH1=TIM_CHANNEL_1", text)
            self.assertIn("IPParameters=Prescaler,Period,OCMode,Pulse,Channel-PWM Generation1 CH1", text)
            # functionlistsort 追加段
            self.assertIn("2-MX_TIM3_Init-TIM3-false-HAL-true", text)
        finally:
            os.remove(path)

    def test_inject_pwm_idempotent(self):
        path = self._write_ioc(SEED_IOC)
        try:
            cubemx_mcp._inject_tim_pwm(path, "TIM3", "PA6", "S_TIM3_CH1")
            out2 = cubemx_mcp._inject_tim_pwm(path, "TIM3", "PA6", "S_TIM3_CH1")
            self.assertIn("无需重复注入", out2)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(text.count("TIM3.IPParameters"), 1)
            self.assertEqual(text.count("PA6.Mode"), 1)
        finally:
            os.remove(path)

    def test_inject_pwm_invalid_tim_raises(self):
        path = self._write_ioc(SEED_IOC)
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp._inject_tim_pwm(path, "FOO", "PA6", "S_TIM3_CH1")
            with self.assertRaises(ValueError):
                cubemx_mcp._inject_tim_pwm(path, "TIM3", "PA6", "S_TIM3_XX")
        finally:
            os.remove(path)


class TestInjectTimInputCapture(unittest.TestCase):
    """输入捕获注入器回归测试(0.4.1)。"""

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

    def test_inject_tim2_input_capture(self):
        path = self._write_ioc(SEED_IOC)
        try:
            out = cubemx_mcp._inject_tim_input_capture(path, "TIM2", "PA0-WKUP", "S_TIM2_CH1_ETR",
                                                       prescaler=72, period=65535)
            self.assertIn("TIM2", out)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            # IP / Pin
            self.assertIn("Mcu.IP3=TIM2", text)
            self.assertIn("Mcu.IPNb=4", text)
            self.assertIn("Mcu.Pin2=PA0-WKUP", text)
            self.assertIn("Mcu.PinsNb=3", text)
            # PA0 组合信号名 + 权威枚举名
            self.assertIn("PA0-WKUP.Mode=Input_Capture1_from_TI1", text)
            self.assertIn("PA0-WKUP.Signal=S_TIM2_CH1_ETR", text)
            self.assertIn("SH.S_TIM2_CH1_ETR.0=TIM2_CH1,Input_Capture1_from_TI1", text)
            # IC1 上升沿 direct + IC2 下降沿 indirect
            self.assertIn("TIM2.IC1Polarity=TIM_ICPOLARITY_RISING", text)
            self.assertIn("TIM2.IC1Selection=TIM_ICSELECTION_DIRECTTI", text)
            self.assertIn("TIM2.IC2Polarity=TIM_ICPOLARITY_FALLING", text)
            self.assertIn("TIM2.IC2Selection=TIM_ICSELECTION_INDIRECTTI", text)
            self.assertIn("TIM2.Period=65535-1", text)
            self.assertIn("TIM2.Prescaler=72-1", text)
            # Channel 键(黄金样本) + ConfNb=1
            self.assertIn("TIM2.Channel-Input_Capture1_from_TI1=TIM_CHANNEL_1", text)
            self.assertIn("SH.S_TIM2_CH1_ETR.ConfNb=1", text)
            # NVIC + functionlistsort
            self.assertIn("NVIC.TIM2_IRQn=", text)
            self.assertIn("2-MX_TIM2_Init-TIM2-false-HAL-true", text)
        finally:
            os.remove(path)

    def test_inject_ic_idempotent(self):
        path = self._write_ioc(SEED_IOC)
        try:
            cubemx_mcp._inject_tim_input_capture(path, "TIM2", "PA0-WKUP", "S_TIM2_CH1_ETR")
            out2 = cubemx_mcp._inject_tim_input_capture(path, "TIM2", "PA0-WKUP", "S_TIM2_CH1_ETR")
            self.assertIn("无需重复注入", out2)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(text.count("TIM2.IPParameters"), 1)
            self.assertEqual(text.count("PA0-WKUP.Mode"), 1)
        finally:
            os.remove(path)

    def test_inject_ic_replaces_existing_internal(self):
        # 先注入内部时钟,再注入输入捕获:旧 VP 表达应被清理,不留残留
        path = self._write_ioc(SEED_IOC)
        try:
            cubemx_mcp._inject_tim_internal_clock(path, "TIM2")
            cubemx_mcp._inject_tim_input_capture(path, "TIM2", "PA0-WKUP", "S_TIM2_CH1_ETR")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("VP_TIM2_VS_ClockSourceINT", text)
            self.assertIn("SH.S_TIM2_CH1_ETR.0=TIM2_CH1,Input_Capture1_from_TI1", text)
            self.assertEqual(text.count("Mcu.IP3=TIM2"), 1)
        finally:
            os.remove(path)

    def test_inject_ic_invalid_tim_raises(self):
        path = self._write_ioc(SEED_IOC)
        try:
            with self.assertRaises(ValueError):
                cubemx_mcp._inject_tim_input_capture(path, "FOO", "PA0-WKUP", "S_TIM2_CH1_ETR")
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()