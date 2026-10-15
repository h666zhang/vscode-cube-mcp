""".ioc 文本编辑、注入、移除、离线预检与摘要测试。"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from _helpers import SAMPLE_IOC, SEED_IOC

from vscode_cube_mcp import cubemx_mcp, ioc_editor, knowledge_loader


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


class TestInjectICInvalidSignal(unittest.TestCase):
    def test_ic_signal_without_channel_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.ioc")
            with open(path, "w", encoding="utf-8") as f:
                f.write("Mcu.IPNb=0\n")
            with self.assertRaises(ValueError):
                cubemx_mcp._inject_tim_input_capture(path, "TIM2", "PA0", "S_TIM2_ETR")


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


class TestSemanticPrecheck(unittest.TestCase):
    """离线预检 ioc_editor.check_semantics:纯文本抓"CubeMX 不报错但配错了"的组合问题。"""

    BASE = """#MicroXplorer Configuration settings - do not modify
ADC1.ExternalTrigConv={trigger}
ADC1.NbrOfConversion=1
CAD.provider=
Mcu.IP0=ADC1
Mcu.IP1=NVIC
Mcu.IP2=RCC
Mcu.IP3=SYS
Mcu.IPNb=4
RCC.ADCPresc={adc_presc}
RCC.APB2Freq_Value=72000000
board=custom
"""

    def setUp(self):
        knowledge_loader.reload()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.ioc = os.path.join(self._td.name, "demo.ioc")

    def _write(self, text):
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write(text)

    def test_clean_adc_is_ok(self):
        self._write(self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc="RCC_ADCPCLK2_DIV6"))
        self.assertEqual(ioc_editor.check_semantics(self.ioc), [])

    def test_missing_functionlist_segment_is_reported(self):
        # "移除外设后又加回":外设在 Mcu.IPx 里、functionlistsort 里却缺它的段 ⇒ 要提示
        # (否则只看 cubemx_load 摘要的 init_order 会以为这外设没配上)
        self._write(
            self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc="RCC_ADCPCLK2_DIV6")
            + "ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false,"
            "2-MX_GPIO_Init-GPIO-false-HAL-true\n"
        )
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("functionlistsort" in p and "ADC1" in p for p in problems))

    def test_complete_functionlist_is_ok(self):
        self._write(
            self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc="RCC_ADCPCLK2_DIV6")
            + "ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false,"
            "2-MX_GPIO_Init-GPIO-false-HAL-true,3-MX_ADC1_Init-ADC1-false-HAL-true\n"
        )
        self.assertEqual(ioc_editor.check_semantics(self.ioc), [])

    def test_external_trigger_without_source_tim(self):
        # ADC 用 T3_TRGO,但 TIM3 根本不在外设列表里
        self._write(self.BASE.format(trigger="ADC_EXTERNALTRIGCONV_T3_TRGO", adc_presc="RCC_ADCPCLK2_DIV6"))
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("TIM3 不在 Mcu.IPx" in p for p in problems), problems)

    def test_external_trigger_without_trgo(self):
        # TIM3 在,但没配 TIM_MasterOutputTrigger —— generate 不报错,硬件不触发
        text = self.BASE.format(trigger="ADC_EXTERNALTRIGCONV_T3_TRGO", adc_presc="RCC_ADCPCLK2_DIV6")
        self._write(text.replace("Mcu.IP3=SYS", "Mcu.IP3=SYS\nMcu.IP4=TIM3\nMcu.IPNb=5"))
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("TIM_TRGO_UPDATE" in p for p in problems), problems)

    def test_external_trigger_with_trgo_is_ok(self):
        text = self.BASE.format(trigger="ADC_EXTERNALTRIGCONV_T3_TRGO", adc_presc="RCC_ADCPCLK2_DIV6")
        self._write(
            text.replace("Mcu.IP3=SYS", "Mcu.IP3=SYS\nMcu.IP4=TIM3\nMcu.IPNb=5")
            + "TIM3.TIM_MasterOutputTrigger=TIM_TRGO_UPDATE\n"
        )
        self.assertEqual(ioc_editor.check_semantics(self.ioc), [])

    def test_missing_adc_clock_is_reported(self):
        self._write(self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc=""))
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("ADCPresc" in p for p in problems), problems)

    def test_over_limit_clock_without_presc_key_is_reported(self):
        # 值等于库默认分频时 CubeMX 不写 RCC.ADCPresc 行 —— 这时要靠 RCC.ADCFreqValue 判断
        # (实测踩过:ADCPresc=DIV2 时文件里明明写着 ADCFreqValue=36MHz,预检却误报"没设",
        #  既报了个假的、又漏掉真正的超限)
        self._write(self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc="") + "RCC.ADCFreqValue=36000000\n")
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("超过上限" in p for p in problems), problems)
        self.assertFalse([p for p in problems if "没设" in p], problems)

    def test_adc_clock_over_limit_is_reported(self):
        # PCLK2 72MHz / DIV2 = 36MHz,超过 F103 的 14MHz 上限
        self._write(self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc="RCC_ADCPCLK2_DIV2"))
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("超过上限" in p for p in problems), problems)

    def test_dma_without_ip_entry_or_nvic(self):
        text = self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc="RCC_ADCPCLK2_DIV6")
        self._write(text + "Dma.ADC1.0.Instance=DMA1_Channel1\nDma.Request0=ADC1\n")
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("没有 DMA" in p for p in problems), problems)
        self.assertTrue(any("NVIC.DMA1_Channel1_IRQn" in p for p in problems), problems)

    def test_dma_request_index_mismatch(self):
        text = self.BASE.format(trigger="ADC_SOFTWARE_START", adc_presc="RCC_ADCPCLK2_DIV6")
        self._write(
            text.replace("Mcu.IP3=SYS", "Mcu.IP3=SYS\nMcu.IP4=DMA\nMcu.IPNb=5")
            + "Dma.ADC1.0.Instance=DMA1_Channel1\n"
            + "Dma.Request1=ADC1\n"
        )
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("对不上" in p for p in problems), problems)

    def test_summary_carries_precheck(self):
        self._write(self.BASE.format(trigger="ADC_EXTERNALTRIGCONV_T3_TRGO", adc_presc="RCC_ADCPCLK2_DIV6"))
        self.assertTrue(ioc_editor.summarize(self.ioc)["precheck"])


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


class TestSpiPrecheck(unittest.TestCase):
    """离线预检:SPI 波特率超上限(或没设 → 用默认值 2)要在落盘前就报出来。"""

    def setUp(self):
        knowledge_loader.reload()
        self.root = tempfile.mkdtemp(prefix="spi_pre_")
        self.ioc = os.path.join(self.root, "proj.ioc")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, lines) -> None:
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def test_over_limit_prescaler_reported(self):
        self._write(
            [
                "Mcu.IP0=RCC",
                "Mcu.IP1=SPI1",
                "RCC.APB2Freq_Value=72000000",
                "SPI1.BaudRatePrescaler=SPI_BAUDRATEPRESCALER_2",
            ]
        )
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("超过上限" in p for p in problems), problems)

    def test_missing_prescaler_reported(self):
        # 没写这一行时 CubeMX 用默认值 2 —— 实测 BB8_spi 之前就是这样踩的
        self._write(["Mcu.IP0=RCC", "Mcu.IP1=SPI1", "RCC.APB2Freq_Value=72000000"])
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("超过上限" in p for p in problems), problems)

    def test_stale_rate_reported(self):
        # 预分频合法(4)但 CalculateBaudRate 是旧值(36.0 MBits/s)—— 实测踩过的形态:
        # set 预分频不重算这一行,CubeMX 仍按它判定 → IP not ready → 报 OK 却零产出
        self._write(
            [
                "Mcu.IP0=RCC",
                "Mcu.IP1=SPI1",
                "RCC.APB2Freq_Value=72000000",
                "SPI1.BaudRatePrescaler=SPI_BAUDRATEPRESCALER_4",
                "SPI1.CalculateBaudRate=36.0 MBits/s",
            ]
        )
        problems = ioc_editor.check_semantics(self.ioc)
        self.assertTrue(any("CalculateBaudRate" in p and "超过上限" in p for p in problems), problems)

    def test_legal_declared_rate_not_reported(self):
        self._write(
            [
                "Mcu.IP0=RCC",
                "Mcu.IP1=SPI1",
                "RCC.APB2Freq_Value=72000000",
                "SPI1.BaudRatePrescaler=SPI_BAUDRATEPRESCALER_4",
                "SPI1.CalculateBaudRate=18000000",
            ]
        )
        problems = [p for p in ioc_editor.check_semantics(self.ioc) if "上限" in p]
        self.assertEqual(problems, [])

    def test_legal_prescaler_not_reported(self):
        self._write(
            [
                "Mcu.IP0=RCC",
                "Mcu.IP1=SPI1",
                "RCC.APB2Freq_Value=72000000",
                "SPI1.BaudRatePrescaler=SPI_BAUDRATEPRESCALER_4",
            ]
        )
        problems = [p for p in ioc_editor.check_semantics(self.ioc) if "波特率" in p]
        self.assertEqual(problems, [])
