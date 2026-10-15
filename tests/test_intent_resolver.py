"""意图解析 -> 命令/动作的测试(含 ADC/SPI 的时钟与速率推导)。"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from _helpers import SAMPLE_IOC

from vscode_cube_mcp import cubemx_mcp, intent_resolver, ioc_editor, knowledge_loader


class TestGpioIntent(unittest.TestCase):
    """GPIO 意图:引脚短名必须归一化(否则 `set gpio parameters` 会 KO)。"""

    def setUp(self):
        knowledge_loader.reload()
        self.root = tempfile.mkdtemp(prefix="gpio_intent_")
        self.ioc = os.path.join(self.root, "proj.ioc")
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write("Mcu.Name=STM32F103C(8-B)Tx\nMcu.IPNb=1\nMcu.IP0=RCC\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _cmd(self, intent) -> str:
        return "; ".join(intent_resolver.apply_intents(self.ioc, [intent])["commands"])

    def test_short_pin_name_is_normalized(self):
        # PA0 在 .ioc 里叫 PA0-WKUP:只写短名时 set pin 能被接受,但 set gpio parameters 会返回 KO
        # (实测 2026-10-15,LEDRUN 用例:8 个引脚模式都配上了、只有标签没设上,工具却整体报失败)
        got = self._cmd({"peripheral": "GPIO", "pin": "PA0", "mode": "output", "label": "LED1"})
        self.assertIn("set pin PA0-WKUP GPIO_Output", got)
        self.assertIn("set gpio parameters PA0-WKUP GPIO_Label LED1", got)

    def test_plain_pin_name_unchanged(self):
        got = self._cmd({"peripheral": "GPIO", "pin": "PB12", "mode": "output", "label": "Speaker"})
        self.assertIn("set gpio parameters PB12 GPIO_Label Speaker", got)

    def test_debug_port_conflict_names_the_debug_port(self):
        # PA13/PA14 是 SWD:报错要点名"调试口",而不是只丢一个 SYS_JTMS-SWDIO 让调用方猜
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write(
                "Mcu.Name=STM32F103C(8-B)Tx\nMcu.IPNb=2\nMcu.IP0=RCC\nMcu.IP1=SYS\n"
                "PA13.Signal=SYS_JTMS-SWDIO\nPA14.Signal=SYS_JTCK-SWCLK\n"
            )
        with self.assertRaises(ValueError) as ctx:
            self._cmd({"peripheral": "GPIO", "pin": "PA13", "mode": "output"})
        msg = str(ctx.exception)
        self.assertIn("调试口", msg)
        self.assertIn("PIN_CONFLICT", msg)


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

    def test_over_limit_clock_prescaler_rejected(self):
        # 显式给的超上限分频必须拦住(DIV2 → 36MHz > F103 的 14MHz):以前只校验"值在候选表里",
        # 超限值被静默接受,下一次 generate 会弹 [Clock] 卡到 240s 超时
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(
                self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "adc_clock_div": "RCC_ADCPCLK2_DIV2"}]
            )
        msg = str(ctx.exception)
        self.assertIn("超过上限", msg)
        self.assertIn("RCC_ADCPCLK2_DIV6", msg)  # 顺便给出这个 APB2 频率下的可用值

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
            [{"peripheral": "ADC1", "pin": "PA0", "continuous": True, "trigger": "T2_CC2", "scan": False}],
        )
        joined = "; ".join(plan["commands"])
        self.assertIn("set ip parameters ADC1 ContinuousConvMode ENABLE", joined)
        self.assertIn("set ip parameters ADC1 ExternalTrigConv ADC_EXTERNALTRIGCONV_T2_CC2", joined)
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

    def test_sampling_time_rejects_unknown_value(self):
        # 写错的采样时间 CubeMX 会静默忽略 ⇒ 对着 db 的取值表先拦下并列出合法值
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(
                self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "sampling_time": "ADC_SAMPLETIME_99CYCLES"}]
            )
        msg = str(ctx.exception)
        self.assertIn("ADC_SAMPLETIME_99CYCLES", msg)
        self.assertIn("ADC_SAMPLETIME_55CYCLES_5", msg)

    def test_plan_reports_defaults_and_derived_values(self):
        # 工具自报"本次采用了什么":没显式给的字段也要有明确说法(不用调用方猜/回读)
        self._seed()
        plan = intent_resolver.apply_intents(
            self.ioc,
            [
                {"peripheral": "ADC1", "pins": ["PA0", "PA1"], "dma": {"mode": "circular"}},
                {"peripheral": "I2C1", "pins": ["PB8", "PB9"]},
            ],
        )
        joined = "\n".join(plan["decisions"])
        self.assertIn("ContinuousConvMode=DISABLE", joined)
        self.assertIn("ScanConvMode=ENABLE", joined)
        self.assertIn("DMA(ADC1)", joined)
        self.assertIn("I2C1: 只配引脚/模式", joined)

    def test_trigger_rejects_unknown_value(self):
        # 非法触发值必须在改 .ioc 之前拦下并列出合法取值(CubeMX 对不认识的枚举是静默忽略)
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "trigger": "T9_TRGO"}])
        msg = str(ctx.exception)
        self.assertIn("T9_TRGO", msg)
        self.assertIn("T3_TRGO", msg)
        self.assertIn("ADC_SOFTWARE_START", msg)

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
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "ADC1", "pin": "PA0", "trigger": "T3_TRGO"}])
        self.assertTrue(any("触发源" in n for n in plan["notes"]), plan["notes"])


class TestSpiBaudrate(unittest.TestCase):
    """SPI 意图:自动补波特率分频 —— 缺它 CubeMX 会"报 OK 却零产出 / 弹窗卡死"。"""

    def setUp(self):
        knowledge_loader.reload()
        self.root = tempfile.mkdtemp(prefix="spi_baud_")
        self.ioc = os.path.join(self.root, "proj.ioc")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self, apb2: str = "72000000", apb1: str = "36000000") -> None:
        with open(self.ioc, "w", encoding="utf-8") as f:
            f.write("Mcu.Name=STM32F103C(8-B)Tx\nMcu.IPNb=1\nMcu.IP0=RCC\n")
            f.write(f"RCC.APB1Freq_Value={apb1}\nRCC.APB2Freq_Value={apb2}\n")

    def _cmd(self, intent: dict) -> str:
        return "; ".join(intent_resolver.apply_intents(self.ioc, [intent])["commands"])

    def test_spi1_gets_prescaler_4_and_rate(self):
        # APB2 72MHz:/2=36MBits/s 超 18 上限 -> 最小合法分频是 4。
        # 波特率值也必须一起写:CubeMX 的校验看 CalculateBaudRate,而 set 预分频**不会重算它**
        # (实测:只改预分频 → .ioc 里仍是 36.0 MBits/s → IP not ready → 报 OK 却零产出)
        self._seed()
        got = self._cmd({"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"]})
        self.assertIn("set ip parameters SPI1 BaudRatePrescaler SPI_BAUDRATEPRESCALER_4", got)
        self.assertIn("set ip parameters SPI1 CalculateBaudRate 18000000", got)

    def test_spi2_follows_apb1(self):
        # SPI2 挂 APB1(36MHz):/2=18MBits/s 恰好合法 -> 用最小分频 2(说明写入的分频是按总线算的)
        self._seed()
        self.assertIn(
            "set ip parameters SPI2 BaudRatePrescaler SPI_BAUDRATEPRESCALER_2",
            self._cmd({"peripheral": "SPI2", "pins": ["PB13", "PB14", "PB15"]}),
        )

    def test_prescaler_follows_lower_apb2(self):
        self._seed(apb2="18000000")
        self.assertIn(
            "SPI_BAUDRATEPRESCALER_2",
            self._cmd({"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"]}),
        )

    def test_explicit_prescaler_by_number(self):
        self._seed()
        self.assertIn(
            "SPI_BAUDRATEPRESCALER_8",
            self._cmd({"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"], "prescaler": 8}),
        )

    def test_explicit_prescaler_by_enum_name(self):
        self._seed()
        got = self._cmd({"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"], "prescaler": "SPI_BAUDRATEPRESCALER_16"})
        self.assertIn("SPI_BAUDRATEPRESCALER_16", got)

    def test_over_limit_prescaler_rejected(self):
        # Prescaler=2 @APB2 72MHz = 36 MBits/s:显式给也必须拦住(否则下一次 generate 零产出/卡死)
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            self._cmd({"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"], "prescaler": 2})
        msg = str(ctx.exception)
        self.assertIn("超过上限", msg)
        self.assertIn("SPI_BAUDRATEPRESCALER_4", msg)  # 顺便给出这个频率下的可用值

    def test_bad_prescaler_rejected(self):
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            self._cmd({"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"], "prescaler": 3})
        self.assertIn("BAD_ARGUMENT", str(ctx.exception))

    def test_decisions_report_prescaler(self):
        self._seed()
        plan = intent_resolver.apply_intents(self.ioc, [{"peripheral": "SPI1", "pins": ["PA5", "PA6", "PA7"]}])
        self.assertTrue(any("波特率分频" in d for d in plan["decisions"]), plan["decisions"])


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
