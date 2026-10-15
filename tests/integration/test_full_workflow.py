"""端到端集成测试:真启动 CubeMX。默认跳过,见文件末尾说明。"""

import os
import shutil
import tempfile
import unittest

from _helpers import INTEGRATION, TEMPLATE_IOC

from vscode_cube_mcp import cubemx_mcp, intent_resolver, ioc_editor


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
        # summary=True(默认)只解析文本、不起 CubeMX;要证明"CubeMX 真的认这份 .ioc"
        # 必须看 via_cubemx 的 run 输出。
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc, summary=False, via_cubemx=True))

    def test_pwm_ioc_accepted(self):
        intent_resolver.apply_intents(
            self.ioc, [{"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "prescaler": 72, "period": 100, "pulse": 50}]
        )
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc, summary=False, via_cubemx=True))

    def test_internal_clock_ioc_accepted(self):
        intent_resolver.apply_intents(self.ioc, [{"peripheral": "TIM2", "mode": "internal_clock"}])
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc, summary=False, via_cubemx=True))

    def test_input_capture_ioc_accepted(self):
        intent_resolver.apply_intents(self.ioc, [{"peripheral": "TIM2", "mode": "input_capture", "pin": "PA0-WKUP"}])
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc, summary=False, via_cubemx=True))

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

    def test_generate_into_other_dir_works(self):
        # 核心回归:project_dir 指向别的目录必须真能生成。以前这里发 `project path`
        # (6.18 返回 KO)→ 零产出,还被误报成失败。
        gen = os.path.join(self._td.name, "gen_out")
        out = cubemx_mcp.cubemx_generate(self.ioc, project_dir=gen)
        self.assertIn("[OK", out)
        self.assertNotIn("返回 KO 的命令", out)
        self.assertTrue(os.path.isfile(os.path.join(gen, "Core", "Src", "main.c")))
        self.assertTrue(os.path.isfile(os.path.join(gen, "it.ioc")))  # .ioc 被复制过去了
        self.assertIn("生成文件(", out)

    def test_inspect_output_sees_generated_values(self):
        # 真生成后能直接看到"最终值":CubeMX 不在 .ioc 里写 ContinuousConvMode/ScanConvMode,
        # 只有生成的 adc.c 里有 —— 这正是 cubemx_inspect_output 存在的理由
        proj = os.path.join(self._td.name, "it_inspect")
        self.assertIn(
            "[OK",
            cubemx_mcp.cubemx_new_project("it_inspect", proj, intents=[{"peripheral": "ADC1", "pins": ["PA0", "PA1"]}]),
        )
        out = cubemx_mcp.cubemx_inspect_output(proj, pattern="ContinuousConvMode|ScanConvMode")
        self.assertIn("[OK] cubemx_inspect_output", out)
        self.assertIn("MX_ADC1_Init", out)
        self.assertIn("ContinuousConvMode", out)
        self.assertIn("ADC_SCAN_ENABLE", out)

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
        self.assertIn("[OK", cubemx_mcp.cubemx_load(self.ioc, summary=False, via_cubemx=True))
