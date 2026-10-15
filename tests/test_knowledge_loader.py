"""知识库加载、引脚表、help 文本与 db 导出解析的测试。"""

import os
import pathlib
import tempfile
import unittest
from unittest import mock

from vscode_cube_mcp import cubemx_mcp, knowledge_loader


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

    def test_pin_error_examples_cover_ports(self):
        # 示例要覆盖各端口:按字母序取前几个一个 PB 都挑不到,对"PB99"这种错没提示价值
        with self.assertRaises(ValueError) as ctx:
            knowledge_loader.resolve_pin_name("PB99")
        msg = str(ctx.exception)
        self.assertIn("PB", msg)
        self.assertIn("每个端口最多到 Pxx15", msg)


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

    def test_help_unknown_topic_lists_available(self):
        # 未知 topic 必须明确报错并列出可用 topic(宿主的工具描述可能被截断,
        # agent 只能靠这个错误发现有哪些 topic)
        with self.assertRaises(ValueError) as ctx:
            cubemx_mcp.cubemx_help(topic="bogus")
        msg = str(ctx.exception)
        self.assertIn("未知 topic", msg)
        self.assertIn("adc", msg)
        self.assertIn("templates", msg)

    def test_help_guide_appends_topic_list(self):
        out = cubemx_mcp.cubemx_help()
        self.assertIn("工具一览", out)
        self.assertIn("【可用 topic】", out)
        self.assertIn("adc", out)

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

    def test_ip_parameter_values_from_db(self):
        # 枚举取值从 db 导出的知识库查(不硬编码),用来校验 ADC 采样时间这类参数
        vals = knowledge_loader.ip_parameter_values("ADC1", "SamplingTime")
        self.assertIn("ADC_SAMPLETIME_55CYCLES_5", vals)
        self.assertIn("ADC_SAMPLETIME_1CYCLE_5", vals)
        self.assertEqual(knowledge_loader.ip_parameter_values("ADC1", "NoSuchParamPrefix"), [])

    def test_help_topics_from_yaml(self):
        # 可用 topic 从 help.yaml 的键算出来(不硬编码);别名与流程 topic 都算
        topics = knowledge_loader.help_topics()
        for name in ("adc", "tim", "dma", "nvic", "gpio", "new_project", "probe", "remove", "clock"):
            self.assertIn(name, topics)
        for meta in ("guide", "source", "peripheral"):
            self.assertNotIn(meta, topics)

    def test_load_cache_follows_file_mtime(self):
        # 改了 knowledge/*.yaml 之后不重启进程也要能读到新内容(实测踩过:help 一直是旧文本)
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = os.path.join(td.name, "probe.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("k: 1\n")
        old = os.environ.get("ST_CUBEMX_KNOWLEDGE")
        os.environ["ST_CUBEMX_KNOWLEDGE"] = td.name
        self.addCleanup(knowledge_loader.reload)  # LIFO:先恢复环境变量,再清缓存

        def restore_env():
            if old is None:
                os.environ.pop("ST_CUBEMX_KNOWLEDGE", None)
            else:
                os.environ["ST_CUBEMX_KNOWLEDGE"] = old

        self.addCleanup(restore_env)
        knowledge_loader.reload()
        self.assertEqual(knowledge_loader._load("probe.yaml"), {"k": 1})
        with open(path, "w", encoding="utf-8") as f:
            f.write("k: 2\n")
        later = os.stat(path).st_mtime + 10
        os.utime(path, (later, later))
        self.assertEqual(knowledge_loader._load("probe.yaml"), {"k": 2})

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
