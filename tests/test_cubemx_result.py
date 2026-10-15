"""cubemx_result 的步骤/错误码/渲染测试。"""

import os
import tempfile
import unittest
from unittest import mock

from vscode_cube_mcp import cubemx_mcp, cubemx_result, knowledge_loader


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
