"""ioc_diff / output_inspector / new_project 分派的测试。"""

import os
import tempfile
import unittest
from unittest import mock

from _helpers import ADC_C_SAMPLE

from vscode_cube_mcp import cubemx_mcp, ioc_diff


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

    def _mock_run_generating(self, ok=True, output="OK", exit_code=0):
        """mock _run_script:跑到含 project generate 的那次调用时,在工程目录造出骨架文件。

        真实 CubeMX 会落 Core/Src/*.c,而 cubemx_new_project 现在会校验产出 ——
        mock 里不造文件的话,会被产出校验拦下(这正是本次要测的行为)。
        """

        def fake(script, stage="run_cubemx"):
            if "project generate" in script:
                src = os.path.join(self.project_dir, "Core", "Src")
                os.makedirs(src, exist_ok=True)
                with open(os.path.join(src, "main.c"), "w", encoding="utf-8") as f:
                    f.write("int main(void){return 0;}\n")
            return {
                "ok": ok,
                "output": output,
                "exit_code": exit_code,
                "error_code": "" if ok else "CUBEMX_KO",
                "ko_command": "",
                "stage": stage,
            }

        return mock.patch.object(cubemx_mcp, "_run_script", side_effect=fake)

    def test_invalid_name_raises(self):
        with self.assertRaises(ValueError):
            cubemx_mcp.cubemx_new_project("Bad-Name!", self.project_dir)

    def test_no_commands_ok(self):
        with self._mock_run_generating():
            r = cubemx_mcp.cubemx_new_project("Proj1", self.project_dir)
        self.assertIn("[OK", r)
        self.assertIn("工程目录", r)
        self.assertIn("生成文件(1): Core/Src/main.c", r)
        self.assertTrue(os.path.isfile(os.path.join(self.project_dir, "Proj1.ioc")))

    def test_intents_resolved_like_cubemx_intent(self):
        # intents 走与 cubemx_intent 同一套解析:ADC 意图自动补时钟、DMA 键组在 saveas 之后注入
        scripts = []

        def fake_run(script, stage="run_cubemx"):
            scripts.append(script)
            if "project generate" in script:
                src = os.path.join(self.project_dir, "Core", "Src")
                os.makedirs(src, exist_ok=True)
                with open(os.path.join(src, "main.c"), "w", encoding="utf-8") as f:
                    f.write("int main(void){return 0;}\n")
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
            if "project generate" in script:
                src = os.path.join(self.project_dir, "Core", "Src")
                os.makedirs(src, exist_ok=True)
                with open(os.path.join(src, "main.c"), "w", encoding="utf-8") as f:
                    f.write("int main(void){return 0;}\n")
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
        with self._mock_run_generating():
            r = cubemx_mcp.cubemx_new_project(
                "Proj2",
                self.project_dir,
                commands=["set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL Prescaler 720 Period 1000"],
            )
        self.assertIn("TIM2", r)
        with open(os.path.join(self.project_dir, "Proj2.ioc"), encoding="utf-8") as f:
            self.assertIn("VP_TIM2_VS_ClockSourceINT.Mode=Internal", f.read())

    def test_pwm_and_ic_commands(self):
        with self._mock_run_generating():
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


class TestOutputInspector(unittest.TestCase):
    """cubemx_inspect_output 的底层:文件树 / 函数签名 / pattern 匹配(只读,不返回全文)。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = self._td.name
        self._write("Core/Src/adc.c", ADC_C_SAMPLE)
        self._write("Core/Src/main.c", "int main(void)\n{\n  return 0;\n}\n")
        self._write("Core/Inc/adc.h", "/* adc */\n")
        self._write("Drivers/STM32F1xx_HAL_Driver/Src/hal.c", "void HAL_Init(void)\n{\n}\n")
        self._write("CMakeLists.txt", "project(x)\n")

    def _write(self, rel, text):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_function_signatures(self):
        sigs = cubemx_mcp.output_inspector.function_signatures(ADC_C_SAMPLE)
        self.assertIn("void MX_ADC1_Init(void)", sigs)
        self.assertIn("void HAL_ADC_MspInit(ADC_HandleTypeDef* adcHandle)", sigs)
        # 控制语句与变量声明不能被当成函数
        self.assertFalse([s for s in sigs if s.split(" ", 1)[0] in ("if", "for", "while")])
        self.assertFalse([s for s in sigs if s.startswith("ADC_HandleTypeDef ")])
        self.assertFalse([s for s in sigs if s.startswith("#")])

    def test_scan_reports_tree_functions_and_fold(self):
        info = cubemx_mcp.output_inspector.scan(self.root)
        self.assertIn("CMakeLists.txt", info["root_files"])
        self.assertEqual(info["functions"]["Core/Src/adc.c"][0], "void MX_ADC1_Init(void)")
        self.assertEqual(info["dirs"]["Core/Src"], 2)
        # 第三方目录(Drivers/)只计数,不列明细
        self.assertEqual(info["folded"]["Drivers"], 1)
        self.assertFalse([t for t in info["tree"] if "Drivers" in t])

    def test_scan_pattern_returns_lines_with_location(self):
        info = cubemx_mcp.output_inspector.scan(self.root, pattern=r"ContinuousConvMode|ScanConvMode")
        self.assertTrue(info["matches"])
        self.assertTrue(info["matches"][0].startswith("Core/Src/adc.c:"))
        self.assertTrue(any("ContinuousConvMode = DISABLE" in m for m in info["matches"]))

    def test_scan_does_not_warn_when_only_folded(self):
        # 折叠的第三方目录不该被报成"明细被截断"(实测踩过:ADCTest5 报"只列了前 29 行(共 103 个)")
        info = cubemx_mcp.output_inspector.scan(self.root)
        self.assertEqual(info["notes"], [])

    def test_scan_rejects_bad_pattern(self):
        with self.assertRaises(ValueError):
            cubemx_mcp.output_inspector.scan(self.root, pattern="(")

    def test_scan_rejects_missing_dir(self):
        with self.assertRaises(ValueError):
            cubemx_mcp.output_inspector.scan(os.path.join(self.root, "nope"))
