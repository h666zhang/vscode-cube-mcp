"""Vscode_cube_mcp 单元测试(标准库 unittest,不依赖 CubeMX 实体)。

运行: python -m pytest -v

测试通过根目录 pyproject.toml 的 `pythonpath = ["src"]` 找到 vscode_cube_mcp 包,不必先安装;
若要用 unittest 跑: pip install -e . 后 python -m unittest discover -s tests -v
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from vscode_cube_mcp import cubemx_mcp, cubemx_result, intent_resolver, ioc_diff, ioc_editor, knowledge_loader


class TestCleanup(unittest.TestCase):
    def test_filters_noise_and_keeps_content(self):
        raw = (
            "2026-08-05 12:00:00,123 [INFO] something\n"
            "Picked up JAVA_TOOL_OPTIONS: -Dfile.encoding=UTF-8\n"
            "log4j:WARN No appenders could be found\n"
            'config load "C:/proj.ioc"\n'
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
        with mock.patch("vscode_cube_mcp.cubemx_mcp.subprocess.Popen", return_value=_FakeProc(0, "OK\noutput\n")):
            r = cubemx_mcp._run_script("config load x")
        self.assertTrue(r["ok"])
        self.assertIn("output", r["output"])

    def test_ko_marker_detected(self):
        with mock.patch("vscode_cube_mcp.cubemx_mcp.subprocess.Popen", return_value=_FakeProc(0, "OK\nKO\n")):
            r = cubemx_mcp._run_script("set bad")
        self.assertFalse(r["ok"])

    def test_nonzero_exit_detected(self):
        with mock.patch("vscode_cube_mcp.cubemx_mcp.subprocess.Popen", return_value=_FakeProc(1, "boom")):
            r = cubemx_mcp._run_script("x")
        self.assertFalse(r["ok"])

    def test_timeout_path(self):
        with mock.patch(
            "vscode_cube_mcp.cubemx_mcp.subprocess.Popen",
            return_value=_FakeProc(0, timeout_error=subprocess.TimeoutExpired("cmd", 1)),
        ):
            r = cubemx_mcp._run_script("x")
        self.assertFalse(r["ok"])
        self.assertIn("TIMEOUT", r["output"])

    def test_timeout_kills_process_tree(self):
        fake = _FakeProc(0, timeout_error=subprocess.TimeoutExpired("cmd", 1))
        with mock.patch("vscode_cube_mcp.cubemx_mcp.subprocess.Popen", return_value=fake):
            with mock.patch("vscode_cube_mcp.cubemx_mcp._kill_process_tree") as kill:
                cubemx_mcp._run_script("x")
        kill.assert_called_once_with(fake)

    def test_temp_script_cleaned(self):
        before = set(os.listdir(tempfile.gettempdir()))
        with mock.patch("vscode_cube_mcp.cubemx_mcp.subprocess.Popen", return_value=_FakeProc(0, "OK\n")):
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
TIM3.Period=9999
VP_TIM3_VS_ClockSourceINT.Mode=Internal
VP_TIM3_VS_ClockSourceINT.Signal=TIM3_VS_ClockSourceINT
board=custom
"""


class TestRemovePeripheral(unittest.TestCase):
    """cubemx_remove_peripheral:文本方式移除外设(连带引脚 / 中断 / DMA 请求)。"""

    # 带 DMA 请求 + 5 通道 ADC 里的一部分引脚(信号名 ADCx_IN0 不含 "ADC1")
    SAMPLE_DMA_IOC = """#MicroXplorer Configuration settings - do not modify
ADC1.ExternalTrigConv=ADC_EXTERNALTRIGCONV_T3_TRGO
ADC1.NbrOfConversion=2
CAD.provider=
Dma.ADC1.0.Direction=DMA_PERIPH_TO_MEMORY
Dma.ADC1.0.Instance=DMA1_Channel1
Dma.ADC1.0.Mode=DMA_CIRCULAR
Dma.ADC1.0.RequestParameters=Instance,Direction,Mode
Dma.Request0=ADC1
Dma.RequestsNb=1
Mcu.IP0=ADC1
Mcu.IP1=DMA
Mcu.IP2=NVIC
Mcu.IP3=RCC
Mcu.IP4=SYS
Mcu.IPNb=5
Mcu.Pin0=PA0-WKUP
Mcu.Pin1=PA1
Mcu.Pin2=VP_SYS_VS_Systick
Mcu.PinsNb=3
NVIC.ADC1_2_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:false\\:false
NVIC.DMA1_Channel1_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:true\\:true
NVIC.ForceEnableDMAVector=true
PA0-WKUP.Locked=true
PA0-WKUP.Signal=ADCx_IN0
PA1.Locked=true
PA1.Signal=ADCx_IN1
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false,2-MX_DMA_Init-DMA-false-HAL-true,3-MX_ADC1_Init-ADC1-false-HAL-true
SH.ADCx_IN0.0=ADC1_IN0,IN0
SH.ADCx_IN0.ConfNb=1
SH.ADCx_IN1.0=ADC1_IN1,IN1
SH.ADCx_IN1.ConfNb=1
board=custom
"""

    # 两个 DMA 请求(ADC1 + USART2_RX),用来验证删除后剩余请求重新编号
    SAMPLE_DMA_IOC_TWO = """#MicroXplorer Configuration settings - do not modify
CAD.provider=
Dma.ADC1.0.Instance=DMA1_Channel1
Dma.ADC1.0.Mode=DMA_CIRCULAR
Dma.ADC1.0.RequestParameters=Instance,Mode
Dma.Request0=ADC1
Dma.Request1=USART2_RX
Dma.RequestsNb=2
Dma.USART2_RX.1.Instance=DMA1_Channel6
Dma.USART2_RX.1.Mode=DMA_NORMAL
Dma.USART2_RX.1.RequestParameters=Instance,Mode
Mcu.IP0=ADC1
Mcu.IP1=DMA
Mcu.IP2=NVIC
Mcu.IP3=RCC
Mcu.IP4=SYS
Mcu.IP5=USART2
Mcu.IPNb=6
Mcu.Pin0=VP_SYS_VS_Systick
Mcu.PinsNb=1
NVIC.ADC1_2_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:false\\:false
NVIC.DMA1_Channel1_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:true\\:true
NVIC.DMA1_Channel6_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:true\\:true
board=custom
"""

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

    def test_remove_cleans_dma_irq_and_pins(self):
        # 删 ADC 要把它的参数、引脚(含 SH 句柄)、共用向量中断、DMA 请求、DMA 实例与初始化段全带走
        path = self._write_ioc(self.SAMPLE_DMA_IOC)
        try:
            cubemx_mcp.cubemx_remove_peripheral(path, "ADC1")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("ADC1", text)  # 参数 + Dma.ADC1.0.* + NVIC.ADC1_2_IRQn + MX_ADC1_Init
            self.assertNotIn("Dma.", text)
            self.assertNotIn("PA0-WKUP", text)  # 引脚行与 Mcu.Pin 条目
            self.assertNotIn("SH.ADCx_IN0", text)
            self.assertNotIn("MX_DMA_Init", text)  # 没有 DMA 请求了,MX_DMA_Init 段一起删
            self.assertIn("Mcu.IP0=NVIC", text)  # DMA 实例也摘掉
            self.assertIn("Mcu.IPNb=3", text)
            self.assertIn("Mcu.PinsNb=1", text)  # 只剩 VP_SYS_VS_Systick
            self.assertNotIn("Mcu.Pin1=", text)
        finally:
            os.remove(path)

    def test_remove_keeps_shared_irq_when_sibling_present(self):
        # ADC1 删掉但 ADC2 还在 → 共用的 ADC1_2_IRQn 必须保留
        ioc = self.SAMPLE_DMA_IOC.replace("Mcu.IP4=SYS\nMcu.IPNb=5", "Mcu.IP4=SYS\nMcu.IP5=ADC2\nMcu.IPNb=6")
        path = self._write_ioc(ioc)
        try:
            cubemx_mcp.cubemx_remove_peripheral(path, "ADC1")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("NVIC.ADC1_2_IRQn=", text)
            self.assertNotIn("ADC1.", text)
            self.assertIn("Mcu.IP3=ADC2", text)
        finally:
            os.remove(path)

    def test_remove_dma_request_renumbers_remaining(self):
        # 删掉 ADC1 后,剩下的 USART2_RX 要重新编号成 0(键前缀里的序号同步改)
        path = self._write_ioc(self.SAMPLE_DMA_IOC_TWO)
        try:
            cubemx_mcp.cubemx_remove_peripheral(path, "ADC1")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("ADC1", text)
            self.assertIn("Dma.Request0=USART2_RX", text)
            self.assertIn("Dma.RequestsNb=1", text)
            self.assertIn("Dma.USART2_RX.0.Instance=DMA1_Channel6", text)
            self.assertNotIn("Dma.USART2_RX.1.", text)
            self.assertNotIn("NVIC.DMA1_Channel1_IRQn", text)  # 被删请求占的通道中断
            self.assertIn("NVIC.DMA1_Channel6_IRQn=", text)  # 保留请求的通道中断留着
            self.assertIn("Mcu.IP0=DMA", text)  # 还有请求 → DMA 实例保留(IP 列表重排后排在最前)
        finally:
            os.remove(path)


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

    def test_help_guide_lists_every_registered_tool(self):
        # help 自述必须覆盖 server 实际注册的每个工具(数量也要对),防止加了工具忘同步文档
        import asyncio

        names = [t.name for t in asyncio.run(cubemx_mcp.mcp.list_tools())]
        guide = knowledge_loader.help_text()
        self.assertIn(f"【工具一览({len(names)} 个)】", guide)
        for name in names:
            self.assertIn(name, guide)

    def test_missing_template_error_lists_available(self):
        # 请求不存在的芯片模板时,错误信息应列出可用模板并给自举指引
        with self.assertRaises(ValueError) as ctx:
            cubemx_mcp._project_template("STM32F407VGT6")
        msg = str(ctx.exception)
        self.assertIn("可用模板", msg)
        self.assertIn("STM32F103C8T6.ioc", msg)
        self.assertIn("cubemx_help", msg)


class TestPinAlias(unittest.TestCase):
    """引脚别名:意图里可以写硬件图上的短名(PA0),工具归一化到引脚表的键。"""

    def test_exact_and_case_insensitive(self):
        self.assertEqual(knowledge_loader.resolve_pin_name("PA1"), "PA1")
        self.assertEqual(knowledge_loader.resolve_pin_name("pa1"), "PA1")

    def test_unique_prefix_alias(self):
        # 引脚表里叫 PA0-WKUP,唯一候选时允许写短名
        self.assertEqual(knowledge_loader.resolve_pin_name("PA0"), "PA0-WKUP")
        self.assertEqual(knowledge_loader.resolve_pin_name("pa0-wkup"), "PA0-WKUP")

    def test_alias_signals_resolve(self):
        self.assertIn("ADC1_IN0", knowledge_loader.pin_signals("PA0"))

    def test_unknown_pin_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.resolve_pin_name("PX9")
        self.assertIn("UNKNOWN_PIN_SIGNAL", str(ctx.exception))


class TestAdcIntent(unittest.TestCase):
    """ADC 意图:自动补 ADC 时钟分频(缺它 CubeMX generate 会卡在 [Clock] 弹窗)。"""

    def setUp(self):
        knowledge_loader.reload()
        self.root = tempfile.mkdtemp(prefix="adc_intent_")
        self.ioc = os.path.join(self.root, "proj.ioc")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self, apb2: str = "72000000") -> None:
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write("Mcu.Name=STM32F103C(8-B)Tx\nMcu.IPNb=1\nMcu.IP0=RCC\n")
            f.write(f"RCC.APB2Freq_Value={apb2}\n")
            f.write("Mcu.Pin0=PA13\nPA13.Signal=SYS_JTMS-SWDIO\n")

    def test_short_pin_name_and_clock_prescaler(self):
        self._seed()
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0"}])
        joined = "; ".join(plan["commands"])
        self.assertIn("set mode ADC1 IN0", joined)
        self.assertIn("set pin PA0-WKUP ADC1_IN0", joined)  # 短名归一化
        # 72MHz PCLK2:/2=36、/4=18 都超 14MHz 上限 -> 选 DIV6(12MHz)
        self.assertIn("set ip parameters RCC ADCPresc RCC_ADCPCLK2_DIV6", joined)

    def test_clock_prescaler_follows_apb2(self):
        self._seed(apb2="24000000")
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA1"}])
        # 24MHz:/2=12 已合法 -> 用最小分频
        self.assertIn("set ip parameters RCC ADCPresc RCC_ADCPCLK2_DIV2", "; ".join(plan["commands"]))

    def test_explicit_clock_prescaler_wins(self):
        self._seed()
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "adc_clock_div": "RCC_ADCPCLK2_DIV8"}]
        )
        self.assertIn("set ip parameters RCC ADCPresc RCC_ADCPCLK2_DIV8", "; ".join(plan["commands"]))

    def test_bad_clock_prescaler_rejected(self):
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(
                self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "adc_clock_div": "RCC_ADCPCLK2_DIV3"}]
            )
        self.assertIn("BAD_ARGUMENT", str(ctx.exception))

    def test_sampling_time_field(self):
        self._seed()
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "sampling_time": "ADC_SAMPLETIME_55CYCLES_5"}]
        )
        self.assertIn(
            "set ip parameters ADC1 SamplingTime-0#ChannelRegularConversion ADC_SAMPLETIME_55CYCLES_5",
            "; ".join(plan["commands"]),
        )

    def test_multichannel_sequence(self):
        # 多通道:pins 顺序即 Rank 顺序;第 2 个通道起必须补 Rank/Channel/SamplingTime
        self._seed()
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pins": ["PA0", "PA1"]}])
        joined = "; ".join(plan["commands"])
        for want in (
            "set mode ADC1 IN0",
            "set mode ADC1 IN1",
            "set pin PA0-WKUP ADC1_IN0",
            "set pin PA1 ADC1_IN1",
            "set ip parameters ADC1 NbrOfConversion 2",
            "set ip parameters ADC1 Rank-1#ChannelRegularConversion 2",
            "set ip parameters ADC1 Channel-1#ChannelRegularConversion ADC_CHANNEL_1",
            "set ip parameters ADC1 SamplingTime-1#ChannelRegularConversion ADC_SAMPLETIME_1CYCLE_5",
            "set ip parameters ADC1 ScanConvMode ADC_SCAN_ENABLE",  # 多通道默认开扫描
        ):
            self.assertIn(want, joined)

    def test_continuous_and_trigger(self):
        self._seed()
        plan = intent_resolver.apply_intents(
            self.ioc,
            [{"peripheral": "ADC1", "pin": "PA0", "continuous": True, "trigger": "T2_TRGO", "scan": False}],
        )
        joined = "; ".join(plan["commands"])
        self.assertIn("set ip parameters ADC1 ContinuousConvMode ENABLE", joined)
        self.assertIn("set ip parameters ADC1 ExternalTrigConv ADC_EXTERNALTRIGCONV_T2_TRGO", joined)
        self.assertNotIn("ScanConvMode", joined)  # 单通道 + 显式 scan=False -> 不写

    def test_full_trigger_enum_passthrough(self):
        # 已经写全枚举时不重复加前缀
        self._seed()
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "trigger": "ADC_EXTERNALTRIGCONV_T1_CC1"}]
        )
        self.assertIn(
            "set ip parameters ADC1 ExternalTrigConv ADC_EXTERNALTRIGCONV_T1_CC1", "; ".join(plan["commands"])
        )

    def test_interrupt_uses_shared_adc_vector(self):
        # F103 上 ADC1/ADC2 共用一个向量:interrupt=true 要用 ADC1_2_IRQn,不是 ADC1_IRQn
        self._seed()
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "interrupt": True}])
        self.assertTrue(any("NVIC.ADC1_2_IRQn" in line for line in plan["ioc_lines"]), plan["ioc_lines"])

    def test_default_irq_name(self):
        self.assertEqual(intent_resolver.default_irq_name("ADC1"), "ADC1_2_IRQn")
        self.assertEqual(intent_resolver.default_irq_name("USART2"), "USART2_IRQn")

    def test_trigger_warns_about_trigger_source(self):
        # 实测:只设 ADC 的 ExternalTrigConv、不配触发源外设 -> generate 卡死,所以要提示
        self._seed()
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "trigger": "T2_TRGO"}])
        self.assertTrue(any("触发源" in n for n in plan["notes"]), plan["notes"])


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
            self.assertIn("TIM2.Prescaler=7199", text)
            self.assertIn("TIM2.Period=9999", text)
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
            self.assertIn("TIM3.Prescaler=71", text)
            self.assertIn("TIM3.Period=999", text)
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
        ioc = SEED_IOC.replace(
            "ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false",
            "ProjectManager.functionlistsort=3-MX_TIM2_Init-TIM2-false-HAL-true",
        )
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
            out = cubemx_mcp._inject_tim_pwm(path, "TIM3", "PA6", "S_TIM3_CH1", prescaler=72, period=100, pulse=50)
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
            self.assertIn("TIM3.Period=99", text)
            self.assertIn("TIM3.Prescaler=71", text)
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
            out = cubemx_mcp._inject_tim_input_capture(
                path, "TIM2", "PA0-WKUP", "S_TIM2_CH1_ETR", prescaler=72, period=65535
            )
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
            self.assertIn("TIM2.Period=65534", text)
            self.assertIn("TIM2.Prescaler=71", text)
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


# ---------------------------------------------------------------- 工程化补强:覆盖率 71% → ≥80%(纯新增,不改旧测试)
class TestProjectTemplate(unittest.TestCase):
    def test_explicit_template_wins(self):
        with tempfile.TemporaryDirectory() as td:
            ioc = os.path.join(td, "custom.ioc")
            with open(ioc, "w", encoding="utf-8") as f:
                f.write("Mcu.IPNb=0\n")
            cubemx_mcp.ALLOWED_ROOTS = [td]
            try:
                self.assertEqual(cubemx_mcp._project_template("STM32F103C8T6", template=ioc), ioc)
            finally:
                cubemx_mcp.ALLOWED_ROOTS = [os.getcwd()]

    def test_mcu_template_found_in_search_dir(self):
        with tempfile.TemporaryDirectory() as td:
            tpl = os.path.join(td, "STM32F103C8T6.ioc")
            with open(tpl, "w", encoding="utf-8") as f:
                f.write("Mcu.IPNb=0\n")
            with mock.patch.object(cubemx_mcp, "_template_search_dirs", return_value=[td]):
                self.assertEqual(cubemx_mcp._project_template("STM32F103C8T6"), tpl)


class TestPatchIocIdentity(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.src = os.path.join(self._td.name, "tpl.ioc")
        self.dst = os.path.join(self._td.name, "out", "proj.ioc")

    def _write(self, text):
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(text)

    def _read_dst(self):
        with open(self.dst, encoding="utf-8") as f:
            return f.read()

    def test_all_replaced_hse_inserts_param(self):
        # 模板已有全部行 → 全部替换;IPParameters 缺 PLLSourceVirtual → 插到 PLLMUL 后
        self._write(
            "Mcu.IPNb=0\n"
            "ProjectManager.ProjectName=TPL\n"
            "ProjectManager.ProjectFileName=TPL.ioc\n"
            "ProjectManager.TargetToolchain=EWARM V8.32\n"
            "ProjectManager.CoupleFile=false\n"
            "RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE\n"
            "RCC.IPParameters=PLLMUL\n"
            "RCC.PLLMUL=RCC_PLL_MUL9\n"
        )
        cubemx_mcp._patch_ioc_identity(
            self.src, self.dst, "MyProj", toolchain="CMake", couple_files=True, clock_source="HSE", pll_mul=9
        )
        text = self._read_dst()
        self.assertIn("ProjectManager.ProjectName=MyProj\n", text)
        self.assertIn("ProjectManager.ProjectFileName=MyProj.ioc\n", text)
        self.assertIn("ProjectManager.TargetToolchain=CMake\n", text)
        self.assertIn("ProjectManager.CoupleFile=true\n", text)
        self.assertIn("RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE\n", text)
        self.assertIn("RCC.PLLMUL=RCC_PLL_MUL9\n", text)
        self.assertIn("RCC.IPParameters=PLLMUL,PLLSourceVirtual\n", text)

    def test_missing_lines_appended(self):
        # 模板缺 ProjectName/FileName/Toolchain/CoupleFile/IPParameters → 全部补行
        self._write("Mcu.IPNb=0\n")
        cubemx_mcp._patch_ioc_identity(self.src, self.dst, "MyProj")
        text = self._read_dst()
        self.assertIn("ProjectManager.ProjectName=MyProj\n", text)
        self.assertIn("ProjectManager.ProjectFileName=MyProj.ioc\n", text)
        self.assertIn("ProjectManager.TargetToolchain=CMake\n", text)
        self.assertIn("ProjectManager.CoupleFile=true\n", text)
        self.assertIn("RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE\n", text)
        self.assertIn("RCC.PLLMUL=RCC_PLL_MUL9\n", text)
        self.assertNotIn("RCC.IPParameters=", text)  # 模板无该行时 _ensure_ip_param 不追加

    def test_ip_param_appended_without_pllmul(self):
        # IPParameters 有值但无 PLLMUL → PLLSourceVirtual 追加到列表末尾
        self._write(
            "ProjectManager.ProjectName=TPL\nProjectManager.ProjectFileName=TPL.ioc\nRCC.IPParameters=Prescaler\n"
        )
        cubemx_mcp._patch_ioc_identity(self.src, self.dst, "MyProj")
        self.assertIn("RCC.IPParameters=Prescaler,PLLSourceVirtual\n", self._read_dst())

    def test_hsi_removes_pll_source(self):
        self._write(
            "ProjectManager.ProjectName=TPL\nProjectManager.ProjectFileName=TPL.ioc\n"
            "RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE\n"
            "RCC.IPParameters=PLLMUL,PLLSourceVirtual\n"
        )
        cubemx_mcp._patch_ioc_identity(self.src, self.dst, "MyProj", clock_source="HSI", pll_mul=16)
        text = self._read_dst()
        self.assertNotIn("PLLSourceVirtual", text)
        self.assertIn("RCC.PLLMUL=RCC_PLL_MUL16\n", text)

    def test_invalid_clock_source_raises(self):
        self._write("ProjectManager.ProjectName=TPL\nProjectManager.ProjectFileName=TPL.ioc\n")
        with self.assertRaises(ValueError):
            cubemx_mcp._patch_ioc_identity(self.src, self.dst, "MyProj", clock_source="LSI")

    def test_invalid_pll_mul_raises(self):
        self._write("ProjectManager.ProjectName=TPL\nProjectManager.ProjectFileName=TPL.ioc\n")
        with self.assertRaises(ValueError):
            cubemx_mcp._patch_ioc_identity(self.src, self.dst, "MyProj", pll_mul=1)


class TestNewProjectDispatch(unittest.TestCase):
    """cubemx_new_project 调度层:mock _run_script(不碰 CubeMX),注入器真实执行。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tpl_dir = os.path.join(self._td.name, "templates")
        os.makedirs(self.tpl_dir)
        with open(os.path.join(self.tpl_dir, "STM32F103C8T6.ioc"), "w", encoding="utf-8") as f:
            f.write(
                "Mcu.IPNb=0\nMcu.PinsNb=0\nboard=custom\n"
                "ProjectManager.ProjectName=TPL\nProjectManager.ProjectFileName=TPL.ioc\n"
            )
        self._orig_search = cubemx_mcp._template_search_dirs
        cubemx_mcp._template_search_dirs = lambda: [self.tpl_dir]
        cubemx_mcp.ALLOWED_ROOTS = [self._td.name]
        self.project_dir = os.path.join(self._td.name, "out")

    def tearDown(self):
        cubemx_mcp._template_search_dirs = self._orig_search
        cubemx_mcp.ALLOWED_ROOTS = [os.getcwd()]

    def _mock_run(self, ok=True, output="OK", exit_code=0):
        return mock.patch.object(
            cubemx_mcp, "_run_script", return_value={"ok": ok, "output": output, "exit_code": exit_code}
        )

    def test_invalid_name_raises(self):
        with self.assertRaises(ValueError):
            cubemx_mcp.cubemx_new_project("Bad-Name!", self.project_dir)

    def test_no_commands_ok(self):
        with self._mock_run():
            r = cubemx_mcp.cubemx_new_project("Proj1", self.project_dir)
        self.assertIn("工程目录", r)
        self.assertTrue(os.path.isfile(os.path.join(self.project_dir, "Proj1.ioc")))

    def test_intents_resolved_like_cubemx_intent(self):
        # intents 走与 cubemx_intent 同一套解析:ADC 意图自动补时钟、DMA 键组在 saveas 之后注入
        scripts = []

        def fake_run(script, stage="run_cubemx"):
            scripts.append(script)
            return {"ok": True, "output": "OK", "exit_code": 0, "error_code": "", "stage": stage}

        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=fake_run):
            out = cubemx_mcp.cubemx_new_project(
                "ProjDma",
                self.project_dir,
                intents=[{"peripheral": "ADC1", "pins": ["PA0", "PA1"], "dma": {"mode": "circular"}}],
            )
        self.assertIn("resolve_intent", out)
        # 第一次调用:load + set 命令(含自动补的 ADC 时钟)+ saveas
        self.assertIn("set ip parameters RCC ADCPresc RCC_ADCPCLK2_DIV6", scripts[0])
        self.assertIn("config saveas", scripts[0])
        # 第二次调用才是 generate
        self.assertIn("project generate", scripts[1])
        # DMA 键组由本工具注入进 .ioc(不是交给 CubeMX 脚本)
        with open(os.path.join(self.project_dir, "ProjDma.ioc"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Dma.ADC1.0.Instance=DMA1_Channel1", text)
        self.assertIn("NVIC.DMA1_Channel1_IRQn=", text)
        self.assertIn("DMA 请求 ADC1", out)

    def test_commands_still_supported_alongside_intents(self):
        # 显式 commands 与 intents 可以一起用;两者都进同一次 CubeMX 调用
        seen = {}

        def fake_run(script, stage="run_cubemx"):
            seen.setdefault("first", script)
            return {"ok": True, "output": "OK", "exit_code": 0, "error_code": "", "stage": stage}

        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=fake_run):
            cubemx_mcp.cubemx_new_project(
                "ProjMix",
                self.project_dir,
                commands=["set pin PB13 GPIO_Output"],
                intents=[{"peripheral": "I2C1", "pins": ["PB8", "PB9"]}],
            )
        self.assertIn("set pin PB13 GPIO_Output", seen["first"])
        self.assertIn("set mode I2C1 I2C", seen["first"])

    def test_tim_internal_clock_command(self):
        with self._mock_run():
            r = cubemx_mcp.cubemx_new_project(
                "Proj2",
                self.project_dir,
                commands=["set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL Prescaler 720 Period 1000"],
            )
        self.assertIn("TIM2", r)
        with open(os.path.join(self.project_dir, "Proj2.ioc"), encoding="utf-8") as f:
            self.assertIn("VP_TIM2_VS_ClockSourceINT.Mode=Internal", f.read())

    def test_pwm_and_ic_commands(self):
        with self._mock_run():
            r = cubemx_mcp.cubemx_new_project(
                "Proj3",
                self.project_dir,
                commands=[
                    "set ip parameters TIM3 PWM PA6 S_TIM3_CH1 Prescaler 72 Period 100 Pulse 50",
                    "set ip parameters TIM2 InputCapture PA0-WKUP S_TIM2_CH1_ETR Prescaler 72 Period 65535",
                ],
            )
        self.assertIn("TIM3", r)
        self.assertIn("TIM2", r)
        with open(os.path.join(self.project_dir, "Proj3.ioc"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("PA6.Mode=PWM Generation1 CH1", text)
        self.assertIn("TIM3.OCMode=TIM_OCMODE_PWM1", text)
        self.assertIn("PA0-WKUP.Mode=Input_Capture1_from_TI1", text)

    def test_first_script_failure_aborts(self):
        with self._mock_run(ok=False, output="KO", exit_code=1):
            r = cubemx_mcp.cubemx_new_project("Proj5", self.project_dir)
        self.assertIn("FAIL", r)
        self.assertIn("已中止生成", r)


class TestToolWrappers(unittest.TestCase):
    """MCP 工具包装层:mock _run_script(不碰 CubeMX)。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write("Mcu.IPNb=0\n")
        cubemx_mcp.ALLOWED_ROOTS = [self._td.name]

    def tearDown(self):
        cubemx_mcp.ALLOWED_ROOTS = [os.getcwd()]

    def test_script_ok(self):
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            self.assertIn("[OK exit=0]", cubemx_mcp.cubemx_script("config load x"))

    def test_script_fail(self):
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": False, "output": "KO", "exit_code": 2}):
            self.assertIn("[FAIL exit=2]", cubemx_mcp.cubemx_script("config load x"))

    def test_load_ok(self):
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            self.assertIn("[OK exit=0]", cubemx_mcp.cubemx_load(self.ioc, via_cubemx=True))

    def test_configure_passes_commands(self):
        with mock.patch.object(
            cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}
        ) as m:
            r = cubemx_mcp.cubemx_configure(self.ioc, ["set pin PB13 GPIO_Output"])
        self.assertIn("[OK", r)
        script = m.call_args[0][0]
        self.assertIn("config load", script)
        self.assertIn("set pin PB13 GPIO_Output", script)
        self.assertIn("config saveas", script)

    def test_generate_with_project_dir(self):
        gen_dir = os.path.join(self._td.name, "gen")
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            self.assertIn("[OK", cubemx_mcp.cubemx_generate(self.ioc, project_dir=gen_dir))
        self.assertTrue(os.path.isdir(gen_dir))

    def test_export_pinout_ok(self):
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            r = cubemx_mcp.cubemx_export_pinout(self.ioc)
        self.assertIn("[OK]", r)
        self.assertIn("--- CSV ---", r)


class TestHelpTopics(unittest.TestCase):
    def test_topic_new_project(self):
        self.assertIn("cubemx_new_project", cubemx_mcp.cubemx_help("new_project"))

    def test_topic_clock_is_rcc_alias(self):
        self.assertEqual(cubemx_mcp.cubemx_help("clock"), cubemx_mcp.cubemx_help("rcc"))

    def test_topic_remove(self):
        self.assertIn("cubemx_remove_peripheral", cubemx_mcp.cubemx_help("remove"))

    def test_templates_empty_dir(self):
        with mock.patch.object(cubemx_mcp, "_available_templates", return_value=[]):
            self.assertIn("为空或不存在", cubemx_mcp.cubemx_help("templates"))


class TestInjectICInvalidSignal(unittest.TestCase):
    def test_ic_signal_without_channel_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.ioc")
            with open(path, "w", encoding="utf-8") as f:
                f.write("Mcu.IPNb=0\n")
            with self.assertRaises(ValueError):
                cubemx_mcp._inject_tim_input_capture(path, "TIM2", "PA0", "S_TIM2_ETR")


class TestResult(unittest.TestCase):
    """cubemx_result:步骤名、错误码、渲染(不依赖 CubeMX)。"""

    def test_render_ok_keeps_legacy_head(self):
        out = cubemx_result.render("cubemx_load", True, exit_code=0, output="OK")
        self.assertIn("[OK exit=0]", out)
        self.assertIn("cubemx_load", out)
        self.assertIn("OK", out)

    def test_render_ok_without_exit_code_keeps_bracket_ok(self):
        out = cubemx_result.render("cubemx_export_pinout", True, output="OK")
        self.assertIn("[OK]", out)

    def test_render_fail_shows_stage_code_and_hint(self):
        out = cubemx_result.render("cubemx_generate", False, exit_code=1, code="CUBEMX_KO", output="KO")
        self.assertIn("[FAIL exit=1]", out)
        self.assertIn("run_cubemx", out)  # 卡在哪一步
        self.assertIn("CUBEMX_KO", out)  # 错误码
        self.assertIn("建议", out)  # 怎么办

    def test_render_steps_line(self):
        out = cubemx_result.render("x", True, steps=[("check_path", "ok"), ("run_cubemx", "FAIL")])
        self.assertIn("check_path(ok)", out)
        self.assertIn("run_cubemx(FAIL)", out)

    def test_render_unknown_code_still_shows_code(self):
        self.assertIn("NOPE", cubemx_result.render("x", False, code="NOPE"))

    def test_render_run_accepts_old_shape_result(self):
        # 旧调用方(mock)只给 ok/output/exit_code,不能因为缺 stage/error_code 而崩
        out = cubemx_result.render_run("cubemx_script", {"ok": False, "output": "KO", "exit_code": 2})
        self.assertIn("[FAIL exit=2]", out)

    def test_render_run_uses_error_code(self):
        out = cubemx_result.render_run(
            "cubemx_script", {"ok": False, "output": "KO", "exit_code": 0, "error_code": "CUBEMX_KO"}
        )
        self.assertIn("CUBEMX_KO", out)
        self.assertIn("run_cubemx", out)

    def test_error_table_is_consistent(self):
        for code, (stage, desc, hint) in cubemx_result.ERRORS.items():
            self.assertIn(stage, cubemx_result.STAGES, f"{code} 的步骤名不在 STAGES 里")
            self.assertTrue(desc, f"{code} 缺说明")
            self.assertTrue(hint, f"{code} 缺建议")

    def test_tool_error_is_valueerror_with_code_and_stage(self):
        with tempfile.TemporaryDirectory() as td:
            cubemx_mcp.ALLOWED_ROOTS = [td]
            try:
                with self.assertRaises(ValueError) as ctx:
                    cubemx_mcp._check_path(os.path.join(td, "..", "outside.ioc"))
            finally:
                cubemx_mcp.ALLOWED_ROOTS = [os.getcwd()]
        msg = str(ctx.exception)
        self.assertEqual(ctx.exception.code, "PATH_NOT_ALLOWED")
        self.assertEqual(ctx.exception.stage, "check_path")
        self.assertIn("[check_path|PATH_NOT_ALLOWED]", msg)
        self.assertIn("建议", msg)

    def test_missing_template_error_has_code(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(cubemx_mcp, "_template_search_dirs", return_value=[td]):
                with self.assertRaises(ValueError) as ctx:
                    cubemx_mcp._project_template("STM32F407VGT6")
        self.assertEqual(ctx.exception.code, "MCU_TEMPLATE_MISSING")

    def test_render_with_warning(self):
        out = cubemx_result.render("x", True, warnings=["CLOCK_DEGRADED"])
        self.assertIn("[OK]", out)
        self.assertIn("CLOCK_DEGRADED", out)
        self.assertIn("建议", out)


class TestKnowledgeLoader(unittest.TestCase):
    """knowledge_loader:读 knowledge/ 下的 YAML(不依赖 CubeMX)。"""

    def setUp(self):
        knowledge_loader.reload()

    def test_knowledge_dir_and_meta(self):
        self.assertTrue(knowledge_loader.knowledge_dir().is_dir())
        info = knowledge_loader.meta()
        self.assertEqual(info.get("mcu"), "STM32F103C8T6")
        self.assertIn("TIM3", info.get("source_ip_xml") or {})

    def test_pin_signals_from_db(self):
        self.assertIn("TIM3_CH1", knowledge_loader.pin_signals("PA6"))
        self.assertIn("I2C1_SCL", knowledge_loader.pin_signals("PB8"))

    def test_unknown_pin_raises_unknown_pin_signal(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.pin_signals("PZ99")
        self.assertEqual(ctx.exception.code, "UNKNOWN_PIN_SIGNAL")

    def test_other_mcu_raises_template_missing(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.pin_signals("PA6", mcu="STM32F407VGT6")
        self.assertEqual(ctx.exception.code, "MCU_TEMPLATE_MISSING")

    def test_ip_mode_names_contain_pwm(self):
        self.assertIn("PWM Generation1 CH1", knowledge_loader.ip_mode_names("TIM3"))

    def test_ip_parameter_from_db(self):
        item = knowledge_loader.ip_parameter("USART2", "BaudRate")
        self.assertEqual(item.get("default"), "115200")

    def test_unknown_parameter_raises(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.ip_parameter("USART2", "NoSuchParam")
        self.assertEqual(ctx.exception.code, "UNKNOWN_PARAM")

    def test_rule_tim_pwm_ok(self):
        item = knowledge_loader.rule_mode("tim", "pwm")
        self.assertIn("{tim}", item["ioc_fragment"])
        self.assertEqual(item["defaults"]["prescaler"], 7200)

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.rule_mode("tim", "no_such_mode")
        self.assertEqual(ctx.exception.code, "UNKNOWN_PARAM")

    def test_unknown_peripheral_raises(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.rule("no_such_peripheral")
        self.assertEqual(ctx.exception.code, "UNKNOWN_PERIPHERAL")

    def test_unverified_rule_blocked(self):
        # 闸门本身:verified 不是 true 就拒绝(用构造数据测,不依赖某个具体规则当前的状态)
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.require_verified({"verified": False, "source": "测试用例"}, "test")
        self.assertEqual(ctx.exception.code, "RULE_NOT_VERIFIED")
        self.assertEqual(ctx.exception.stage, "resolve_intent")

    def test_verified_rule_passes(self):
        self.assertTrue(knowledge_loader.require_rule_verified("tim").get("verified"))

    def test_dma_channel_from_db(self):
        # 通道来自 db 的 DMA Modes XML(不是猜的):ADC1 只有 DMA1_Channel1
        self.assertEqual(knowledge_loader.dma_channel("ADC1"), "DMA1_Channel1")
        self.assertEqual(knowledge_loader.dma_channel("USART2_RX"), "DMA1_Channel6")
        self.assertIn("SPI1_TX", knowledge_loader.dma_requests())

    def test_dma_direction_from_db(self):
        self.assertEqual(knowledge_loader.dma_direction("USART2_TX"), "DMA_MEMORY_TO_PERIPH")
        self.assertEqual(knowledge_loader.dma_direction("ADC1"), "DMA_PERIPH_TO_MEMORY")
        self.assertEqual(knowledge_loader.dma_direction("no_such_request"), "")

    def test_unknown_dma_request_raises(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.dma_channel("ADC9")
        self.assertEqual(ctx.exception.code, "UNKNOWN_PARAM")

    def test_help_text(self):
        self.assertIn("工具一览", knowledge_loader.help_text())
        self.assertIn("set pin PB13 GPIO_Output", knowledge_loader.help_text("gpio"))
        self.assertEqual(knowledge_loader.help_text("clock"), knowledge_loader.help_text("rcc"))
        self.assertIn("移除外设", knowledge_loader.help_text("remove"))
        self.assertIsNone(knowledge_loader.help_text("templates"))

    def test_help_text_covers_new_peripherals(self):
        for topic in ("usart", "spi", "adc", "can", "rtc", "iwdg", "wwdg", "dma", "nvic"):
            text = knowledge_loader.help_text(topic)
            self.assertTrue(text and "【" in text, f"{topic} 没有帮助文本")


class TestRenderFragment(unittest.TestCase):
    """render_fragment:把规则文件里的 .ioc 片段渲染成实际文本。"""

    def setUp(self):
        knowledge_loader.reload()

    def test_internal_clock_fragment(self):
        rule = knowledge_loader.rule("tim")
        item = knowledge_loader.rule_mode("tim", "internal_clock")
        out = knowledge_loader.render_fragment(
            item["ioc_fragment"], rule["values"], tim="TIM2", prescaler=7199, period=9999
        )
        self.assertIn("TIM2.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE", out)
        self.assertIn("TIM2.Period=9999", out)
        self.assertIn("TIM2.Prescaler=7199", out)
        self.assertIn("VP_TIM2_VS_ClockSourceINT.Mode=Internal", out)
        self.assertIn("VP_TIM2_VS_ClockSourceINT.Signal=TIM2_VS_ClockSourceINT", out)
        self.assertIn("NVIC.TIM2_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:true\\:true", out)

    def test_pwm_fragment(self):
        rule = knowledge_loader.rule("tim")
        item = knowledge_loader.rule_mode("tim", "pwm")
        values = rule["values"]
        out = knowledge_loader.render_fragment(
            item["ioc_fragment"],
            values,
            tim="TIM3",
            pin="PA6",
            signal="S_TIM3_CH1",
            prescaler=72,
            period=100,
            pulse=50,
            mode_name=values["pwm_mode"].format(n="1"),
            mode_name_in_key=values["pwm_mode_in_key"].format(n="1"),
            signal_body="TIM3_CH1",
            channel_key=values["channel_key"].format(n="1"),
        )
        self.assertIn("PA6.Mode=PWM Generation1 CH1", out)
        self.assertIn("SH.S_TIM3_CH1.0=TIM3_CH1,PWM Generation1 CH1", out)
        self.assertIn("TIM3.OCMode=TIM_OCMODE_PWM1", out)
        self.assertIn("TIM3.Channel-PWM Generation1\\ CH1=TIM_CHANNEL_1", out)

    def test_missing_placeholder_raises_bad_argument(self):
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.render_fragment("{tim}.X={nope}")
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")


class TestIocEditorWrite(unittest.TestCase):
    """ioc_editor:写回前的备份与写回后的回读校验。"""

    def test_backup_creates_bak(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.ioc")
            with open(path, "w", encoding="utf-8") as f:
                f.write("A=1\n")
            bak = ioc_editor.backup(path)
            self.assertEqual(bak, path + ".bak")
            with open(bak, encoding="utf-8") as f:
                self.assertEqual(f.read(), "A=1\n")

    def test_verify_written_ok_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.ioc")
            with open(path, "w", encoding="utf-8") as f:
                f.write("A=1\nB=2\n")
            ioc_editor.verify_written(path, ["A=1", "B=2"])  # 都在,不抛
            with self.assertRaises(ValueError) as ctx:
                ioc_editor.verify_written(path, ["A=1", "C=3"])
            self.assertEqual(ctx.exception.code, "READBACK_MISMATCH")

    def test_inject_pwm_backs_up_original(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.ioc")
            with open(path, "w", encoding="utf-8") as f:
                f.write(SAMPLE_IOC)
            with open(path, encoding="utf-8") as f:
                before = f.read()
            ioc_editor.inject_tim_pwm(path, "TIM3", "PA6", "S_TIM3_CH1", prescaler=72, period=100, pulse=50)
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(f.read(), before)

    def test_remove_peripheral_backs_up(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.ioc")
            with open(path, "w", encoding="utf-8") as f:
                f.write(SAMPLE_IOC)
            ioc_editor.remove_peripheral(path, "TIM3")
            self.assertTrue(os.path.isfile(path + ".bak"))


class TestStructureCheck(unittest.TestCase):
    """按 knowledge/rules/ioc_structure.yaml 做的写入后结构校验。"""

    BAD_IOC = "Mcu.IP0=RCC\nMcu.IP2=SYS\nMcu.IPNb=3\nMcu.Pin0=PA0\nMcu.PinsNb=2\nboard=custom\n"
    GOOD_IOC = "Mcu.IP0=RCC\nMcu.IP1=SYS\nMcu.IPNb=2\nMcu.Pin0=PA0\nMcu.PinsNb=1\nboard=custom\n"

    def setUp(self):
        knowledge_loader.reload()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")

    def _write(self, text):
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write(text)

    def test_consistent_ioc_has_no_problems(self):
        self._write(self.GOOD_IOC)
        self.assertEqual(ioc_editor.check_structure(self.ioc), [])

    def test_detects_numbering_gap_and_count_mismatch(self):
        self._write(self.BAD_IOC)
        problems = ioc_editor.check_structure(self.ioc)
        self.assertTrue(any("Mcu.IP 编号不连续" in p for p in problems))
        self.assertTrue(any("Mcu.IPNb=3 与实际 2 条不符" in p for p in problems))
        self.assertTrue(any("Mcu.PinsNb=2 与实际 1 条不符" in p for p in problems))

    def test_preexisting_problems_do_not_block_writes(self):
        # 用户 .ioc 本来就有的结构问题不算在工具头上:写入仍然成功
        self._write(self.BAD_IOC)
        added = ioc_editor.add_lines(self.ioc, ["USART2.BaudRate=9600"])
        self.assertEqual(added, ["USART2.BaudRate=9600"])

    def test_new_problem_raises_structure_mismatch(self):
        # 写入后新引入结构问题 → 报 STRUCTURE_MISMATCH(baseline 为空 = 写前没问题)
        self._write(self.BAD_IOC)
        with self.assertRaises(ValueError) as ctx:
            ioc_editor._verify_after_write(self.ioc, ["Mcu.IPNb=3"], [])
        self.assertEqual(ctx.exception.code, "STRUCTURE_MISMATCH")
        self.assertIn("编号不连续", str(ctx.exception))


class TestIocSummary(unittest.TestCase):
    """ioc_editor.summarize / cubemx_load 的结构化摘要(只读解析,不依赖 CubeMX)。"""

    IOC = """#MicroXplorer Configuration settings - do not modify
ADC1.ExternalTrigConv=ADC_EXTERNALTRIGCONV_T3_TRGO
ADC1.IPParameters=NbrOfConversion,ExternalTrigConv
ADC1.NbrOfConversion=2
CAD.provider=
Dma.ADC1.0.Instance=DMA1_Channel1
Dma.ADC1.0.Mode=DMA_CIRCULAR
Dma.Request0=ADC1
Dma.RequestsNb=1
Mcu.CPN=STM32F103C8T6
Mcu.Family=STM32F1
Mcu.IP0=ADC1
Mcu.IP1=DMA
Mcu.IP2=NVIC
Mcu.IP3=RCC
Mcu.IP4=SYS
Mcu.IPNb=5
Mcu.Name=STM32F103C(8-B)Tx
Mcu.Package=LQFP48
MxCube.Version=6.18.0
NVIC.ADC1_2_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:false\\:false
NVIC.DMA1_Channel1_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:true\\:true
NVIC.PriorityGroup=NVIC_PRIORITYGROUP_4
PA0-WKUP.Signal=ADCx_IN0
PA1.Signal=ADCx_IN1
ProjectManager.CoupleFile=true
ProjectManager.ProjectName=Demo
ProjectManager.TargetToolchain=CMake
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false,2-MX_DMA_Init-DMA-false-HAL-true,3-MX_ADC1_Init-ADC1-false-HAL-true
RCC.ADCFreqValue=12000000
RCC.ADCPresc=RCC_ADCPCLK2_DIV6
RCC.APB2Freq_Value=72000000
RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE
RCC.SYSCLKFreq_VALUE=72000000
VP_SYS_VS_Systick.Signal=SYS_VS_Systick
board=custom
"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write(self.IOC)
        cubemx_mcp.ALLOWED_ROOTS = [self._td.name]
        self.addCleanup(setattr, cubemx_mcp, "ALLOWED_ROOTS", [os.getcwd()])

    def test_summary_fields(self):
        s = ioc_editor.summarize(self.ioc)
        self.assertEqual(s["mcu"]["cpn"], "STM32F103C8T6")
        self.assertEqual(s["project"], {"name": "Demo", "toolchain": "CMake", "couple_files": "true"})
        self.assertEqual(s["ips"], ["ADC1", "DMA", "NVIC", "RCC", "SYS"])
        self.assertEqual(s["pins"], {"PA0-WKUP": "ADCx_IN0", "PA1": "ADCx_IN1"})
        self.assertEqual(s["virtual_pins"], {"VP_SYS_VS_Systick": "SYS_VS_Systick"})
        self.assertEqual(s["dma"]["requests_nb"], "1")
        self.assertEqual(s["dma"]["requests"], [{"index": 0, "name": "ADC1"}])
        self.assertEqual(s["dma"]["configs"]["ADC1"]["Instance"], "DMA1_Channel1")
        self.assertIn("DMA1_Channel1_IRQn", s["nvic"])
        self.assertNotIn("PriorityGroup", s["nvic"])  # PriorityGroup 不是中断
        self.assertEqual(s["clock"]["ADCPresc"], "RCC_ADCPCLK2_DIV6")
        self.assertEqual(s["clock"]["PLLSourceVirtual"], "RCC_PLLSOURCE_HSE")
        # 各外设参数(时钟/中断单独成节);IPParameters 拆成列表更好读
        self.assertEqual(s["peripherals"]["ADC1"]["ExternalTrigConv"], "ADC_EXTERNALTRIGCONV_T3_TRGO")
        self.assertEqual(s["peripherals"]["ADC1"]["IPParameters"], ["NbrOfConversion", "ExternalTrigConv"])
        self.assertNotIn("RCC", s["peripherals"])
        self.assertEqual(s["init_order"], ["SystemClock_Config", "MX_DMA_Init", "MX_ADC1_Init"])

    def test_cubemx_load_default_is_text_only_and_fast(self):
        # 默认不启 CubeMX(纯文本解析 → 秒回);这是"回读配置"的常用路径
        with mock.patch.object(cubemx_mcp, "_run_script") as m:
            out = cubemx_mcp.cubemx_load(self.ioc)
        self.assertFalse(m.called, "默认路径不应启动 CubeMX")
        self.assertIn("结构化摘要", out)
        self.assertIn('"cpn": "STM32F103C8T6"', out)

    def test_cubemx_load_via_cubemx_adds_its_output(self):
        ok = {"ok": True, "output": "OK", "exit_code": 0, "error_code": "", "stage": "run_cubemx"}
        with mock.patch.object(cubemx_mcp, "_run_script", return_value=ok) as m:
            out = cubemx_mcp.cubemx_load(self.ioc, via_cubemx=True)
        self.assertTrue(m.called)
        self.assertIn("[OK exit=0]", out)
        self.assertIn("结构化摘要", out)

    def test_cubemx_load_needs_at_least_one_output(self):
        with self.assertRaises(ValueError) as ctx:
            cubemx_mcp.cubemx_load(self.ioc, summary=False)
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")


class TestInjectDma(unittest.TestCase):
    """ioc_editor.inject_dma:DMA 请求写成 .ioc 键组(形态照实测,不依赖 CubeMX)。"""

    IOC = """#MicroXplorer Configuration settings - do not modify
CAD.formats=
CAD.provider=
File.Version=6
Mcu.Family=STM32F1
Mcu.IP0=ADC1
Mcu.IP1=NVIC
Mcu.IP2=RCC
Mcu.IP3=SYS
Mcu.IPNb=4
NVIC.DebugMonitor_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:false\\:false
NVIC.ForceEnableDMAVector=true
NVIC.HardFault_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:false\\:false
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false,2-MX_GPIO_Init-GPIO-false-HAL-true
board=custom
"""

    def setUp(self):
        knowledge_loader.reload()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write(self.IOC)

    def _read(self):
        with open(self.ioc, encoding="utf-8") as f:
            return f.read()

    def test_writes_required_key_group(self):
        note = ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1", mode="DMA_CIRCULAR")
        text = self._read()
        self.assertIn("Dma.ADC1.0.Instance=DMA1_Channel1", text)
        self.assertIn("Dma.ADC1.0.Direction=DMA_PERIPH_TO_MEMORY", text)  # rules 里的默认方向
        self.assertIn("Dma.ADC1.0.Mode=DMA_CIRCULAR", text)
        self.assertIn("Dma.ADC1.0.PeriphInc=DMA_PINC_DISABLE", text)
        self.assertIn("Dma.ADC1.0.MemInc=DMA_MINC_ENABLE", text)
        # 只写必需参数时,RequestParameters 就只列这几项(对齐/优先级由 CubeMX 补)
        self.assertIn("Dma.ADC1.0.RequestParameters=Instance,Direction,PeriphInc,MemInc,Mode", text)
        self.assertNotIn("PeriphDataAlignment", text)
        self.assertIn("Dma.Request0=ADC1", text)
        self.assertIn("Dma.RequestsNb=1", text)
        self.assertIn("Mcu.IP0=ADC1", text)
        self.assertIn("Mcu.IP1=DMA", text)  # 按字母序插在 ADC1 与 NVIC 之间
        self.assertIn("Mcu.IPNb=5", text)
        self.assertIn("NVIC.DMA1_Channel1_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:false\\:true\\:true", text)
        self.assertIn("MX_DMA_Init-DMA-false-HAL-true", text)
        self.assertIn("ADC1 已注入 DMA 键组", note)

    def test_optional_params_written_in_db_order(self):
        ioc_editor.inject_dma(
            self.ioc,
            "ADC1",
            "DMA1_Channel1",
            periph_align="DMA_PDATAALIGN_HALFWORD",
            mem_align="DMA_MDATAALIGN_HALFWORD",
            priority="DMA_PRIORITY_HIGH",
        )
        text = self._read()
        self.assertIn("Dma.ADC1.0.PeriphDataAlignment=DMA_PDATAALIGN_HALFWORD", text)
        self.assertIn("Dma.ADC1.0.MemDataAlignment=DMA_MDATAALIGN_HALFWORD", text)
        self.assertIn("Dma.ADC1.0.Priority=DMA_PRIORITY_HIGH", text)
        self.assertIn(
            "Dma.ADC1.0.RequestParameters=Instance,Direction,PeriphInc,MemInc,"
            "PeriphDataAlignment,MemDataAlignment,Mode,Priority",
            text,
        )

    def test_idempotent_and_value_updated(self):
        ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1")
        ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1", mode="DMA_CIRCULAR")
        text = self._read()
        self.assertEqual(text.count("Dma.ADC1.0.Instance="), 1)
        self.assertEqual(text.count("Dma.Request0="), 1)
        self.assertEqual(text.count("NVIC.DMA1_Channel1_IRQn="), 1)
        self.assertIn("Dma.ADC1.0.Mode=DMA_CIRCULAR", text)
        self.assertNotIn("Dma.ADC1.0.Mode=DMA_NORMAL", text)
        self.assertIn("Dma.RequestsNb=1", text)

    def test_second_request_gets_next_index_and_keeps_first(self):
        ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1")
        ioc_editor.inject_dma(self.ioc, "USART2_RX", "DMA1_Channel6")
        text = self._read()
        self.assertIn("Dma.Request0=ADC1", text)
        self.assertIn("Dma.Request1=USART2_RX", text)
        self.assertIn("Dma.RequestsNb=2", text)
        self.assertIn("Dma.USART2_RX.1.Instance=DMA1_Channel6", text)
        self.assertIn("NVIC.DMA1_Channel6_IRQn=", text)
        self.assertIn("Dma.ADC1.0.Instance=DMA1_Channel1", text)

    def test_nvic_can_be_skipped(self):
        ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1", nvic=False)
        self.assertNotIn("NVIC.DMA1_Channel1_IRQn", self._read())

    def test_backup_written(self):
        ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1")
        self.assertTrue(os.path.isfile(self.ioc + ".bak"))

    def test_rejects_bad_channel_and_enum(self):
        with self.assertRaises(ValueError) as ctx:
            ioc_editor.inject_dma(self.ioc, "ADC1", "DMA9_ChannelX")
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")
        with self.assertRaises(ValueError) as ctx:
            ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1", mode="DMA_ROUND_ROBIN")
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")


class TestDmaIntent(unittest.TestCase):
    """意图里的 dma 子对象 -> 注入参数(通道/方向查 db 表,不猜)。"""

    def setUp(self):
        knowledge_loader.reload()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write(SAMPLE_IOC)

    def test_adc_plan_uses_db_channel_and_direction(self):
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "ADC1", "pins": ["PA0"], "dma": {"mode": "circular"}}]
        )
        self.assertEqual(len(plan["dma"]), 1)
        item = plan["dma"][0]
        self.assertEqual(item["request"], "ADC1")
        self.assertEqual(item["channel"], "DMA1_Channel1")
        self.assertEqual(item["direction"], "DMA_PERIPH_TO_MEMORY")
        self.assertEqual(item["mode"], "DMA_CIRCULAR")
        self.assertEqual(item["periph_align"], "")  # 留空 = 交给 CubeMX 补默认
        self.assertTrue(item["nvic"])
        self.assertIn("DMA 请求 ADC1", "\n".join(plan["notes"]))

    def test_dma_true_keeps_defaults_for_cubemx(self):
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "dma": True}])
        self.assertEqual(plan["dma"][0]["mode"], "")
        self.assertEqual(plan["dma"][0]["direction"], "DMA_PERIPH_TO_MEMORY")

    def test_usart_requires_explicit_request(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "USART2", "pin": "PA2", "dma": True}])
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")
        self.assertIn("USART2_RX", str(ctx.exception))

    def test_usart_rx_from_db(self):
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "USART2", "pins": ["PA2", "PA3"], "dma": {"request": "USART2_RX"}}]
        )
        self.assertEqual(plan["dma"][0]["channel"], "DMA1_Channel6")
        self.assertEqual(plan["dma"][0]["direction"], "DMA_PERIPH_TO_MEMORY")

    def test_usart_tx_direction_from_db(self):
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "USART2", "pins": ["PA2", "PA3"], "dma": {"request": "USART2_TX"}}]
        )
        self.assertEqual(plan["dma"][0]["channel"], "DMA1_Channel7")
        self.assertEqual(plan["dma"][0]["direction"], "DMA_MEMORY_TO_PERIPH")

    def test_explicit_overrides_win(self):
        plan = intent_resolver.apply_intents(
            self.ioc,
            [
                {
                    "peripheral": "ADC1",
                    "pin": "PA0",
                    "dma": {
                        "channel": "DMA1_Channel1",
                        "periph_align": "halfword",
                        "mem_align": "halfword",
                        "priority": "very_high",
                        "interrupt": False,
                    },
                }
            ],
        )
        item = plan["dma"][0]
        self.assertEqual(item["periph_align"], "DMA_PDATAALIGN_HALFWORD")
        self.assertEqual(item["mem_align"], "DMA_MDATAALIGN_HALFWORD")
        self.assertEqual(item["priority"], "DMA_PRIORITY_VERY_HIGH")
        self.assertFalse(item["nvic"])

    def test_bad_align_value_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(
                self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "dma": {"periph_align": "quadword"}}]
            )
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")

    def test_unknown_request_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "dma": {"request": "ADC9"}}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_PARAM")

    def test_plan_applied_to_ioc_end_to_end(self):
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "dma": True}])
        for params in plan["dma"]:
            ioc_editor.inject_dma(self.ioc, **params)
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Dma.ADC1.0.Instance=DMA1_Channel1", text)
        self.assertIn("Mcu.IP0=DMA", text)  # SAMPLE_IOC 的 IP 列表里 DMA 排在最前(NVIC 之前)


class TestIntentResolver(unittest.TestCase):
    """intent_resolver:意图 -> 查表 -> 动作(不依赖 CubeMX)。"""

    def setUp(self):
        knowledge_loader.reload()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write(SAMPLE_IOC)

    def test_tim_clock_comes_from_rules(self):
        self.assertEqual(intent_resolver.tim_clock_mhz(), 72)

    def test_split_divider(self):
        self.assertEqual(intent_resolver.split_divider(7200), (1, 7200))
        prescaler, period = intent_resolver.split_divider(720000)
        self.assertGreater(prescaler, 1)
        self.assertLessEqual(period, 65535)

    def test_signal_name_uses_db_groups(self):
        # TIM3 的 CH1 用共享组名 -> S_TIM3_CH1
        self.assertEqual(intent_resolver.ioc_signal_name("TIM3", "CH1"), "S_TIM3_CH1")
        # TIM2 的 CH1 在 db 里有独占组名(PA0 上 CH1 与 ETR 组合)-> S_TIM2_CH1_ETR
        self.assertEqual(intent_resolver.ioc_signal_name("TIM2", "CH1"), "S_TIM2_CH1_ETR")

    def test_find_channel_from_pin_table(self):
        self.assertEqual(intent_resolver.find_channel("TIM3", "PA6"), "CH1")

    def test_find_channel_unknown_pin_signal(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.find_channel("TIM3", "PB13")
        self.assertEqual(ctx.exception.code, "UNKNOWN_PIN_SIGNAL")

    def test_pwm_business_params_converted(self):
        params = intent_resolver.tim_params("pwm", {"freq_hz": 10000, "duty": 0.5})
        self.assertEqual(params["prescaler"], 1)
        self.assertEqual(params["period"], 7200)
        self.assertEqual(params["pulse"], 3600)

    def test_pwm_explicit_params_win(self):
        params = intent_resolver.tim_params("pwm", {"freq_hz": 10000, "prescaler": 72, "period": 100, "pulse": 50})
        self.assertEqual((params["prescaler"], params["period"], params["pulse"]), (72, 100, 50))

    def test_pwm_out_of_range_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.tim_params("pwm", {"prescaler": 0})
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")

    def test_tim_internal_clock_irq_can_be_disabled(self):
        # 意图里写 irq: false → 注入片段不带 NVIC 行(只想拿它当触发源时用)
        intent_resolver.apply_intents(
            self.ioc,
            [{"peripheral": "TIM2", "mode": "internal_clock", "prescaler": 72, "period": 100, "irq": False}],
        )
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("VP_TIM2_VS_ClockSourceINT.Mode=Internal", text)
        self.assertNotIn("NVIC.TIM2_IRQn", text)

    def test_tim_internal_clock_irq_defaults_on(self):
        intent_resolver.apply_intents(self.ioc, [{"peripheral": "TIM2", "mode": "internal_clock"}])
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("NVIC.TIM2_IRQn", text)

    def test_apply_pwm_writes_ioc(self):
        plan = intent_resolver.apply_intents(
            self.ioc,
            [{"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "prescaler": 72, "period": 100, "pulse": 50}],
        )
        self.assertTrue(plan["notes"])
        self.assertEqual(plan["commands"], [])
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("PA6.Mode=PWM Generation1 CH1", text)
        self.assertIn("PA6.Signal=S_TIM3_CH1", text)

    def test_apply_input_capture_on_pa0(self):
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "TIM2", "mode": "input_capture", "pin": "PA0-WKUP"}]
        )
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("PA0-WKUP.Signal=S_TIM2_CH1_ETR", text)
        self.assertTrue(plan["notes"])

    def test_pin_conflict_within_intents(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(
                self.ioc,
                [
                    {"peripheral": "TIM3", "mode": "pwm", "pin": "PA6"},
                    {"peripheral": "TIM4", "mode": "pwm", "pin": "PA6"},
                ],
            )
        self.assertEqual(ctx.exception.code, "PIN_CONFLICT")

    def test_pin_conflict_with_existing_ioc(self):
        path = os.path.join(self._td.name, "used.ioc")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Mcu.IPNb=0\nMcu.PinsNb=0\nPA6.Signal=GPIO_Output\nboard=custom\n")
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(path, [{"peripheral": "TIM3", "mode": "pwm", "pin": "PA6"}])
        self.assertEqual(ctx.exception.code, "PIN_CONFLICT")

    def test_gpio_intent_returns_commands(self):
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "GPIO", "pin": "PB13", "mode": "output", "label": "LED"}]
        )
        self.assertIn("set pin PB13 GPIO_Output", plan["commands"])
        self.assertIn("set gpio parameters PB13 GPIO_Label LED", plan["commands"])

    def test_i2c_intent_roles_from_pin_table(self):
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "I2C1", "pins": ["PB8", "PB9"]}])
        self.assertIn("set pin PB8 I2C1_SCL", plan["commands"])
        self.assertIn("set pin PB9 I2C1_SDA", plan["commands"])
        self.assertIn("set mode I2C1 I2C", plan["commands"])

    def test_unsupported_peripheral_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "FOO1"}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_PERIPHERAL")

    def test_rtc_commands(self):
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "RTC", "mode": "enabled_calendar"}])
        self.assertIn("set mode RTC RTC_Enabled", plan["commands"])
        self.assertIn("set mode RTC RTC_Calendar", plan["commands"])

    def test_unknown_tim_mode_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "TIM3", "mode": "output_compare", "pin": "PA6"}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_PARAM")

    def test_empty_intents_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [])
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")

    def test_cubemx_intent_tool(self):
        with mock.patch.object(cubemx_mcp, "ALLOWED_ROOTS", [self._td.name]):
            with mock.patch.object(
                cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}
            ):
                out = cubemx_mcp.cubemx_intent(self.ioc, [{"peripheral": "GPIO", "pin": "PB13", "mode": "output"}])
        self.assertIn("[OK", out)
        self.assertIn("cubemx_intent", out)

    def test_usart_intent_commands(self):
        # usart 的规则已经探针实测过(verified: true),所以意图层放行
        plan = intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "USART2", "pins": ["PA2", "PA3"], "baudrate": 9600}]
        )
        self.assertIn("set mode USART2 Asynchronous", plan["commands"])
        self.assertIn("set ip parameters USART2 BaudRate 9600", plan["commands"])
        self.assertIn("set pin PA2 USART2_TX", plan["commands"])
        self.assertIn("set pin PA3 USART2_RX", plan["commands"])

    def test_cubemx_intent_rejects_unsupported_peripheral(self):
        with mock.patch.object(cubemx_mcp, "ALLOWED_ROOTS", [self._td.name]):
            with self.assertRaises(ValueError) as ctx:
                cubemx_mcp.cubemx_intent(self.ioc, [{"peripheral": "FOO1"}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_PERIPHERAL")

    def test_iwdg_wwdg_can_commands(self):
        plan = intent_resolver.apply_intents(
            self.ioc,
            [
                {"peripheral": "IWDG"},
                {"peripheral": "WWDG"},
                {"peripheral": "CAN", "pins": ["PA11", "PA12"]},
            ],
        )
        self.assertIn("set mode IWDG IWDG_Activate", plan["commands"])
        self.assertIn("set mode WWDG WWDG_Activate", plan["commands"])
        self.assertIn("set mode CAN CAN_Activate", plan["commands"])
        self.assertIn("set pin PA11 CAN_RX", plan["commands"])
        self.assertIn("set pin PA12 CAN_TX", plan["commands"])

    def test_nvic_rule_is_verified(self):
        # 中断:脚本 set 配不了,但注入 .ioc 键实测有效(见 knowledge/rules/nvic.yaml)
        self.assertTrue(knowledge_loader.rule("nvic").get("verified"))

    def test_interrupt_line_from_rule(self):
        line = intent_resolver.interrupt_line({"peripheral": "USART2", "interrupt": True})
        self.assertIn("NVIC.USART2_IRQn=", line)
        self.assertIn("\\:", line)

    def test_interrupt_explicit_name(self):
        line = intent_resolver.interrupt_line({"peripheral": "I2C1", "interrupt": "I2C1_EV_IRQn"})
        self.assertIn("NVIC.I2C1_EV_IRQn=", line)

    def test_no_interrupt_no_line(self):
        self.assertEqual(intent_resolver.interrupt_line({"peripheral": "USART2"}), "")

    def test_apply_intents_returns_ioc_lines(self):
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "USART2", "pins": ["PA2"], "interrupt": True}])
        self.assertTrue(any("NVIC.USART2_IRQn" in line for line in plan["ioc_lines"]))

    def test_add_lines_injects_before_board_custom_and_is_idempotent(self):
        path = os.path.join(self._td.name, "lines.ioc")
        with open(path, "w", encoding="utf-8") as f:
            f.write("A=1\nboard=custom\n")
        self.assertEqual(ioc_editor.add_lines(path, ["B=2\n"]), ["B=2\n"])
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("B=2\nboard=custom", text)
        self.assertEqual(ioc_editor.add_lines(path, ["B=2\n"]), [])

    def test_cubemx_intent_injects_interrupt_line(self):
        with mock.patch.object(cubemx_mcp, "ALLOWED_ROOTS", [self._td.name]):
            with mock.patch.object(
                cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}
            ):
                out = cubemx_mcp.cubemx_intent(self.ioc, [{"peripheral": "USART2", "pins": ["PA2"], "interrupt": True}])
        self.assertIn("NVIC.USART2_IRQn", out)
        with open(self.ioc, encoding="utf-8") as f:
            self.assertIn("NVIC.USART2_IRQn", f.read())

    def test_spi_intent_commands(self):
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"]}])
        self.assertIn("set mode SPI1 Full_Duplex_Master", plan["commands"])
        self.assertIn("set pin PA5 SPI1_SCK", plan["commands"])
        self.assertIn("set pin PA6 SPI1_MISO", plan["commands"])
        self.assertIn("set pin PA7 SPI1_MOSI", plan["commands"])

    def test_spi_unknown_mode_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "SPI1", "mode": "nope", "pins": ["PA5"]}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_PARAM")

    def test_adc_channel_from_pin_table(self):
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0-WKUP"}])
        self.assertIn("set mode ADC1 IN0", plan["commands"])
        self.assertIn("set pin PA0-WKUP ADC1_IN0", plan["commands"])
        self.assertIn("set mode ADC1 IN5", intent_resolver.adc_commands({"peripheral": "ADC1", "pin": "PA5"}))

    def test_adc_pin_without_channel_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA13"}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_PIN_SIGNAL")

    def test_dma_not_supported(self):
        # DMA 要挂到具体外设意图下(见 rules/dma.yaml);独立的 "peripheral": "DMA" 仍然不支持
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "DMA", "mode": "SPI1_RX", "pin": "PA5"}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_PERIPHERAL")
        self.assertIn("dma", str(ctx.exception))

    def test_dma_rule_is_verified(self):
        self.assertTrue(knowledge_loader.rule("dma").get("verified"))


class TestGenKnowledgeParsers(unittest.TestCase):
    """tools/gen_knowledge_from_db.py 的 db XML 解析(喂内联 XML,不需要 CubeMX 安装)。

    单独测的理由:知识库里的"事实"(引脚表 / IP 模式 / DMA 请求→通道表)全靠这几个
    解析函数从 db 导出,它们错了下游全错 —— 而这块原先零覆盖。
    """

    DMA_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<IP DBVersion="V3.0" IPType="service" Name="DMA" Version="STM32F103G_dma_v1_0"
    xmlns="http://mcd.rou.st.com/modules.php?name=mcu">
    <RefMode BaseMode="DMA_Request" Comment="ADC1" Name="ADC1">
        <Parameter Name="Direction"><PossibleValue>DMA_PERIPH_TO_MEMORY</PossibleValue></Parameter>
        <Parameter Name="Mode">
            <PossibleValue>DMA_NORMAL</PossibleValue>
            <PossibleValue>DMA_CIRCULAR</PossibleValue>
        </Parameter>
    </RefMode>
    <RefMode BaseMode="DMA_Request" Comment="USART2_RX" Name="USART2_RX">
        <Parameter Name="Direction">
            <PossibleValue>DMA_PERIPH_TO_MEMORY</PossibleValue>
            <PossibleValue>DMA_MEMORY_TO_PERIPH</PossibleValue>
        </Parameter>
    </RefMode>
    <ModeLogicOperator Name="OR">
        <Mode Name="DMA1">
            <ModeLogicOperator Name="OR">
                <Mode Name="DMA1_Channel1">
                    <ModeLogicOperator Name="XOR">
                        <Mode Name="MEMTOMEM"/>
                        <Mode Name="ADC1"/>
                    </ModeLogicOperator>
                </Mode>
                <Mode Name="DMA1_Channel6">
                    <ModeLogicOperator Name="XOR">
                        <Mode Name="USART2_RX"/>
                    </ModeLogicOperator>
                </Mode>
            </ModeLogicOperator>
        </Mode>
    </ModeLogicOperator>
</IP>
"""

    DMA_MULTI_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<IP DBVersion="V3.0" Name="DMA" Version="x" xmlns="http://mcd.rou.st.com/modules.php?name=mcu">
    <ModeLogicOperator Name="OR">
        <Mode Name="DMA1">
            <ModeLogicOperator Name="OR">
                <Mode Name="DMA1_Channel6">
                    <ModeLogicOperator Name="XOR"><Mode Name="USART2_RX"/></ModeLogicOperator>
                </Mode>
                <Mode Name="DMA1_Channel7">
                    <ModeLogicOperator Name="XOR"><Mode Name="USART2_RX"/></ModeLogicOperator>
                </Mode>
            </ModeLogicOperator>
        </Mode>
    </ModeLogicOperator>
</IP>
"""

    MODES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<IP DBVersion="V3.0" Name="USART" Version="sci2_v1_1" xmlns="http://mcd.rou.st.com/modules.php?name=mcu">
    <RefParameter Comment="Baud rate" Name="BaudRate" Type="int" DefaultValue="115200" Min="0" Max="9000000"/>
    <RefSignal IOMode="Tx" Direction="I/O" Name="USART2_TX" ShareableGroupName="S_$IpInstance_TX"/>
    <RefMode BaseMode="Asynchronous" HalMode="Asynchronous" Name="Asynchronous">
        <Parameter Name="BaudRate"><PossibleValue>9600</PossibleValue></Parameter>
    </RefMode>
</IP>
"""

    MCU_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Mcu RefName="STM32F103C8Tx" Family="STM32F1" Package="LQFP48"
    xmlns="http://mcd.rou.st.com/modules.php?name=mcu">
    <Core>Arm Cortex-M3</Core>
    <Frequency>72</Frequency>
    <IP ConfigFile="ADC-aditf_v2_5F1" InstanceName="ADC1" Name="ADC" Version="aditf_v2_5F1_Cube"/>
    <Pin Name="PA0-WKUP" Position="10" Type="I/O">
        <Signal Name="ADC1_IN0"/>
        <Signal Name="TIM2_CH1" IOModes="Input"/>
    </Pin>
</Mcu>
"""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        path = pathlib.Path(__file__).resolve().parent.parent / "tools" / "gen_knowledge_from_db.py"
        spec = importlib.util.spec_from_file_location("gen_knowledge_from_db", path)
        cls.gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gen)

    def _parse(self, func, xml_text):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "db.xml"
            path.write_text(xml_text, encoding="utf-8")
            return func(path)

    def test_dma_channel_tree_with_default_namespace(self):
        # 真实 db 的 XML 带默认命名空间 —— 踩过坑:root.iter("Mode") 一个都找不到
        parsed = self._parse(self.gen.parse_dma_requests, self.DMA_XML)
        self.assertEqual(parsed["channels"]["ADC1"], "DMA1_Channel1")
        self.assertEqual(parsed["channels"]["USART2_RX"], "DMA1_Channel6")
        self.assertNotIn("MEMTOMEM", parsed["channels"])  # 内存到内存不是外设请求
        self.assertEqual(parsed["directions"]["ADC1"], "DMA_PERIPH_TO_MEMORY")
        self.assertNotIn("USART2_RX", parsed["directions"])  # 方向不唯一 → 不写,由规则兜底

    def test_dma_request_on_two_channels_becomes_list(self):
        parsed = self._parse(self.gen.parse_dma_requests, self.DMA_MULTI_XML)
        self.assertEqual(parsed["channels"]["USART2_RX"], ["DMA1_Channel6", "DMA1_Channel7"])

    def test_parse_modes_xml_parameters_signals_modes(self):
        data = self._parse(self.gen.parse_modes_xml, self.MODES_XML)
        self.assertEqual(data["parameters"]["BaudRate"]["default"], "115200")
        self.assertEqual(data["signals"]["USART2_TX"]["shareable_group"], "S_$IpInstance_TX")
        self.assertEqual(data["modes"][0]["name"], "Asynchronous")
        self.assertEqual(data["modes"][0]["possible_values"]["BaudRate"], ["9600"])

    def test_parse_mcu_xml_ips_and_pins(self):
        data = self._parse(self.gen.parse_mcu_xml, self.MCU_XML)
        self.assertEqual(data["chip"]["core"], "Arm Cortex-M3")
        self.assertEqual(data["ips"][0]["instance"], "ADC1")
        self.assertIn("ADC1_IN0", data["pins"]["PA0-WKUP"]["signals"])
        self.assertEqual(data["pins"]["PA0-WKUP"]["gpio_io_modes"], "Input")

    def test_dump_yaml_keeps_order_and_unicode(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "x.yaml"
            self.gen.dump_yaml(out, {"channels_nb": 1, "channels": {"ADC1": "DMA1_Channel1"}, "note": "中文不转义"})
            text = out.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("channels_nb: 1"))
        self.assertIn("中文不转义", text)
        self.assertNotIn("\\u", text)


class TestIocDiff(unittest.TestCase):
    """ioc_diff:结构化差异与噪音忽略(不依赖 CubeMX)。"""

    def test_added_removed_changed(self):
        diff = ioc_diff.diff_texts("A=1\nB=2\nC=3\n", "A=1\nB=9\nD=4\n", ignore=())
        self.assertEqual(diff["added"], ["D=4"])
        self.assertEqual(diff["removed"], ["C=3"])
        self.assertEqual(diff["changed"], ["B: 2 -> 9"])

    def test_noise_ignored_by_default(self):
        old = "PCC.Vdd=3.3\nProjectManager.ProjectFileName=old.ioc\nUSART2.BaudRate=9600\n"
        new = "PCC.Vdd=5.0\nProjectManager.ProjectFileName=new.ioc\nUSART2.BaudRate=115200\n"
        diff = ioc_diff.diff_texts(old, new)
        self.assertEqual(diff["changed"], ["USART2.BaudRate: 9600 -> 115200"])
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])

    def test_diff_files_and_render(self):
        with tempfile.TemporaryDirectory() as td:
            old_path = os.path.join(td, "old.ioc")
            new_path = os.path.join(td, "new.ioc")
            with open(old_path, "w", encoding="utf-8") as f:
                f.write("Mcu.IPNb=3\n")
            with open(new_path, "w", encoding="utf-8") as f:
                f.write("Mcu.IPNb=4\nUSART2.BaudRate=115200\n")
            diff = ioc_diff.diff_files(old_path, new_path)
            self.assertEqual(diff["summary"], "+1 -0 ~1")
            text = ioc_diff.render_diff(diff)
            self.assertIn("[OK", text)
            self.assertIn("USART2.BaudRate=115200", text)


class TestProbeTool(unittest.TestCase):
    """cubemx_probe:在副本上试跑,输出差异(mock 掉 CubeMX)。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write("Mcu.IPNb=3\nboard=custom\n")
        cubemx_mcp.ALLOWED_ROOTS = [self._td.name]
        self.addCleanup(setattr, cubemx_mcp, "ALLOWED_ROOTS", [os.getcwd()])

    def _fake_run(self, extra_lines):
        def run(script, stage="run_cubemx"):
            # 模拟 CubeMX 执行后,副本里多了几行
            with open(os.path.join(self._td.name, "demo.probe.ioc"), "a", encoding="utf-8") as f:
                f.write(extra_lines)
            return {"ok": True, "output": "OK", "exit_code": 0, "error_code": "", "stage": stage}

        return run

    def test_probe_reports_added_and_cleans_up(self):
        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=self._fake_run("USART2.BaudRate=115200\n")):
            out = cubemx_mcp.cubemx_probe(self.ioc, ["set mode USART2 Asynchronous"])
        self.assertIn("USART2.BaudRate=115200", out)
        self.assertIn("+1", out)
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "demo.probe.ioc")))

    def test_probe_keeps_copy_when_asked(self):
        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=self._fake_run("A=1\n")):
            out = cubemx_mcp.cubemx_probe(self.ioc, ["x"], keep_copy=True)
        self.assertIn("副本保留在", out)
        self.assertTrue(os.path.exists(os.path.join(self._td.name, "demo.probe.ioc")))

    def test_probe_requires_some_action(self):
        with self.assertRaises(ValueError) as ctx:
            cubemx_mcp.cubemx_probe(self.ioc)
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")

    def test_probe_inject_reports_cubemx_kept_it(self):
        # inject:先把键写进副本,再让 CubeMX load + saveas —— 这里模拟"它原样保留"
        def run(script, stage="run_cubemx"):
            return {"ok": True, "output": "OK", "exit_code": 0, "error_code": "", "stage": stage}

        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=run):
            out = cubemx_mcp.cubemx_probe(
                self.ioc,
                inject=[{"type": "dma", "request": "ADC1", "channel": "DMA1_Channel1", "mode": "DMA_CIRCULAR"}],
            )
        self.assertIn("原样保留", out)
        self.assertIn("Dma.ADC1.0.Instance=DMA1_Channel1", out)
        self.assertIn("NVIC.DMA1_Channel1_IRQn=", out)

    def test_probe_inject_reports_cubemx_rewrote_it(self):
        # CubeMX 改写注入内容时必须明确报出来,并附"注入后 vs 归一化后"第二段差异
        def run(script, stage="run_cubemx"):
            probe = os.path.join(self._td.name, "demo.probe.ioc")
            with open(probe, encoding="utf-8") as f:
                text = f.read()
            with open(probe, "w", encoding="utf-8") as f:
                f.write(text.replace("Dma.ADC1.0.Mode=DMA_CIRCULAR", "Dma.ADC1.0.Mode=DMA_NORMAL"))
            return {"ok": True, "output": "OK", "exit_code": 0, "error_code": "", "stage": stage}

        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=run):
            out = cubemx_mcp.cubemx_probe(
                self.ioc,
                inject=[{"type": "dma", "request": "ADC1", "channel": "DMA1_Channel1", "mode": "DMA_CIRCULAR"}],
            )
        self.assertIn("改动了", out)
        self.assertIn("注入后 vs CubeMX 归一化后", out)
        self.assertIn("Dma.ADC1.0.Mode: DMA_CIRCULAR -> DMA_NORMAL", out)

    def test_probe_inject_lines(self):
        def run(script, stage="run_cubemx"):
            return {"ok": True, "output": "OK", "exit_code": 0, "error_code": "", "stage": stage}

        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=run):
            out = cubemx_mcp.cubemx_probe(
                self.ioc,
                inject=[
                    {
                        "type": "lines",
                        "lines": ["NVIC.USART2_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:true\\:true"],
                    }
                ],
            )
        self.assertIn("已注入 .ioc 行", out)
        self.assertIn("USART2_IRQn", out)

    def test_probe_unknown_inject_type_raises(self):
        with mock.patch.object(cubemx_mcp, "_run_script", side_effect=self._fake_run("")):
            with self.assertRaises(ValueError) as ctx:
                cubemx_mcp.cubemx_probe(self.ioc, inject=[{"type": "nope"}])
        self.assertEqual(ctx.exception.code, "BAD_ARGUMENT")


# ---------------------------------------------------------------- 集成测试(默认跳过)
INTEGRATION = os.environ.get("ST_CUBEMX_RUN_INTEGRATION") == "1"
TEMPLATE_IOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "STM32F103C8T6.ioc")


@unittest.skipUnless(
    INTEGRATION,
    "集成测试默认跳过;要跑就设 ST_CUBEMX_RUN_INTEGRATION=1(会启动 CubeMX,每个用例约 15s)",
)
class TestCubeMXIntegration(unittest.TestCase):
    """真跑 CubeMX:验证意图层写出的 .ioc 能被 CubeMX 接受、set 命令能被执行。

    单元测试只能证明"代码逻辑对",这个类才证明"写出来的东西 CubeMX 认"。
    默认跳过(慢,而且会启动外部程序)。
    运行: $env:ST_CUBEMX_RUN_INTEGRATION=1; python -m pytest tests/test_cubemx_mcp.py::TestCubeMXIntegration -v
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "it.ioc")
        shutil.copy(TEMPLATE_IOC, self.ioc)
        cubemx_mcp.ALLOWED_ROOTS = [self._td.name]
        self.addCleanup(setattr, cubemx_mcp, "ALLOWED_ROOTS", [os.getcwd()])

    def test_template_loads(self):
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc))

    def test_pwm_ioc_accepted(self):
        intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "prescaler": 72, "period": 100, "pulse": 50}]
        )
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc))

    def test_internal_clock_ioc_accepted(self):
        intent_resolver.apply_intents(self.ioc, [{"peripheral": "TIM2", "mode": "internal_clock"}])
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc))

    def test_input_capture_ioc_accepted(self):
        intent_resolver.apply_intents(self.ioc, [{"peripheral": "TIM2", "mode": "input_capture", "pin": "PA0-WKUP"}])
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc))

    def test_gpio_i2c_usart_commands_accepted(self):
        out = cubemx_mcp.cubemx_configure(
            self.ioc,
            [
                "set pin PB13 GPIO_Output",
                "set gpio parameters PB13 GPIO_Label LED",
                "set pin PB8 I2C1_SCL",
                "set pin PB9 I2C1_SDA",
                "set mode I2C1 I2C",
                "set mode USART2 Asynchronous",
                "set ip parameters USART2 BaudRate 9600",
                "set pin PA2 USART2_TX",
                "set pin PA3 USART2_RX",
            ],
        )
        self.assertIn("[OK", out)

    def test_dma_injection_survives_cubemx_saveas(self):
        # 注入的 DMA 键组必须经得起 CubeMX 的 load + saveas(真跑;保存后键还在才算数)
        cubemx_mcp.cubemx_configure(
            self.ioc,
            [
                "set mode ADC1 IN0",
                "set pin PA0-WKUP ADC1_IN0",
                "set ip parameters RCC ADCPresc RCC_ADCPCLK2_DIV6",
            ],
        )
        ioc_editor.inject_dma(self.ioc, "ADC1", "DMA1_Channel1", mode="DMA_CIRCULAR")
        self.assertIn("[OK", cubemx_mcp.cubemx_configure(self.ioc, []))  # load + saveas,不改任何配置
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Dma.ADC1.0.Instance=DMA1_Channel1", text)
        self.assertIn("Dma.Request0=ADC1", text)
        self.assertIn("NVIC.DMA1_Channel1_IRQn=", text)
        self.assertIn("Mcu.IP1=DMA", text)

    def test_nvic_injection_survives_cubemx_saveas(self):
        cubemx_mcp.cubemx_configure(self.ioc, ["set mode USART2 Asynchronous", "set pin PA2 USART2_TX"])
        ioc_editor.add_lines(self.ioc, ["NVIC.USART2_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:true\\:true"])
        self.assertIn("[OK", cubemx_mcp.cubemx_configure(self.ioc, []))
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("NVIC.USART2_IRQn=", text)

    def test_remove_peripheral_leaves_loadable_ioc(self):
        # 配外设 + 它的 DMA 请求,再移除:连带清理后 CubeMX 仍要能 load
        cubemx_mcp.cubemx_configure(self.ioc, ["set pin PB8 I2C1_SCL", "set pin PB9 I2C1_SDA", "set mode I2C1 I2C"])
        ioc_editor.inject_dma(self.ioc, "I2C1_RX", "DMA1_Channel7")
        self.assertIn("[OK", cubemx_mcp.cubemx_remove_peripheral(self.ioc, "I2C1"))
        with open(self.ioc, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("Dma.", text)
        self.assertNotIn("PB8", text)
        self.assertNotIn("NVIC.DMA1_Channel7_IRQn", text)  # I2C1_RX 占的通道中断也一并清掉
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc))


if __name__ == "__main__":
    unittest.main()
