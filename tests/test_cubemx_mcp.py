"""cubemx_mcp 的进程调用/路径校验/工具包装层测试(不依赖 CubeMX 实体)。"""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from _helpers import _FakeProc

from vscode_cube_mcp import cubemx_mcp, cubemx_result


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


class TestRunScript(unittest.TestCase):
    def setUp(self):
        # _javaw_pids 会真的去调 tasklist,而它内部走 subprocess.Popen —— 与下面各用例对
        # Popen 的 mock 会互相干扰,所以统一 mock 掉(超时兜底的差集逻辑另有专门用例)
        patcher = mock.patch("vscode_cube_mcp.cubemx_mcp._javaw_pids", return_value=set())
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_timeout_kills_new_javaw_only(self):
        # 超时兜底:把"调用前就存在的 javaw 集合"交给 _kill_new_javaw 做差集(不动用户自己开的 GUI)
        fake = _FakeProc(0, timeout_error=subprocess.TimeoutExpired("cmd", 1))
        with (
            mock.patch("vscode_cube_mcp.cubemx_mcp.subprocess.Popen", return_value=fake),
            mock.patch("vscode_cube_mcp.cubemx_mcp._kill_process_tree"),
            mock.patch("vscode_cube_mcp.cubemx_mcp._kill_new_javaw") as knj,
            mock.patch("vscode_cube_mcp.cubemx_mcp._javaw_pids", return_value={"999"}),
        ):
            cubemx_mcp._run_script("x")
        knj.assert_called_once_with({"999"})

    def test_temp_script_cleaned(self):
        before = set(os.listdir(tempfile.gettempdir()))
        with mock.patch("vscode_cube_mcp.cubemx_mcp.subprocess.Popen", return_value=_FakeProc(0, "OK\n")):
            cubemx_mcp._run_script("config load x")
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual(before, after)


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
            self.assertIn("[OK exit=0]", cubemx_mcp.cubemx_raw_script("config load x"))

    def test_script_fail(self):
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": False, "output": "KO", "exit_code": 2}):
            self.assertIn("[FAIL exit=2]", cubemx_mcp.cubemx_raw_script("config load x"))

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

    def _mock_generating_run(self, gen_dir, seen=None, ok=True):
        """mock _run_script:执行含 project generate 的调用时,在 gen_dir 造出骨架文件。"""

        def fake(script, stage="run_cubemx"):
            if seen is not None:
                seen.append(script)
            if "project generate" in script:
                src = os.path.join(gen_dir, "Core", "Src")
                os.makedirs(src, exist_ok=True)
                with open(os.path.join(src, "main.c"), "w", encoding="utf-8") as f:
                    f.write("int main(void){return 0;}\n")
            return {
                "ok": ok,
                "output": "OK",
                "exit_code": 0,
                "error_code": "" if ok else "CUBEMX_KO",
                "ko_command": "",
                "stage": stage,
            }

        return mock.patch.object(cubemx_mcp, "_run_script", side_effect=fake)

    def test_generate_with_project_dir(self):
        # project_dir 指向别的目录:不再发 `project path`(6.18 返回 KO、会让 generate
        # 静默零产出),改为把 .ioc 复制过去再就地生成。
        gen_dir = os.path.join(self._td.name, "gen")
        seen = []
        with self._mock_generating_run(gen_dir, seen):
            r = cubemx_mcp.cubemx_generate(self.ioc, project_dir=gen_dir)
        self.assertIn("[OK", r)
        self.assertTrue(os.path.isdir(gen_dir))
        self.assertTrue(os.path.isfile(os.path.join(gen_dir, "demo.ioc")))
        self.assertNotIn("project path", "".join(seen))
        self.assertIn("生成文件(1): Core/Src/main.c", r)

    def test_load_rejects_directory_path(self):
        # 把目录当 .ioc 传时要说清"你给的是目录",而不是笼统的"文件不存在"
        self._allow_tmp()
        with self.assertRaises(ValueError) as ctx:
            cubemx_mcp.cubemx_load(self._td.name)
        self.assertIn("是目录", str(ctx.exception))

    def test_generate_no_project_path_command(self):
        # 就地生成同样不发 project path
        seen = []
        with self._mock_generating_run(self._td.name, seen):
            r = cubemx_mcp.cubemx_generate(self.ioc)
        self.assertIn("[OK", r)
        self.assertNotIn("project path", "".join(seen))

    def test_generate_flags_missing_output(self):
        # CubeMX 报成功但一个文件都没生成:必须显式失败,不能假装成功
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            r = cubemx_mcp.cubemx_generate(self.ioc)
        self.assertIn("[FAIL", r)
        self.assertIn("CUBEMX_NO_OUTPUT", r)

    def test_script_rejects_project_path(self):
        # 手工脚本里带 project path 直接拒绝(6.18 会 KO + 静默零产出 + 可能卡死)
        with self.assertRaises(ValueError) as cm:
            cubemx_mcp.cubemx_raw_script('project path "C:/tmp/x"')
        self.assertIn("project path", str(cm.exception))

    def test_ko_command_is_reported(self):
        # 失败时要说清是哪条命令 KO,而不是笼统的"命令返回 KO"
        output = 'config load "x"\nOK\nproject path "y"\nKO\nproject generate\nOK'
        with mock.patch.object(
            cubemx_mcp,
            "_run_script",
            return_value={
                "ok": False,
                "output": output,
                "exit_code": 0,
                "error_code": "CUBEMX_KO",
                "ko_command": cubemx_mcp._ko_command(output),
            },
        ):
            r = cubemx_mcp.cubemx_raw_script("config load x")
        self.assertIn("返回 KO 的命令", r)
        self.assertIn("project path", r)

    def test_ko_command_parser(self):
        self.assertEqual(cubemx_mcp._ko_command("a\nOK\nb\nKO\nc\nOK"), "b")
        self.assertEqual(cubemx_mcp._ko_command("a\nOK"), "")

    def _allow_tmp(self):
        old = cubemx_mcp.ALLOWED_ROOTS
        cubemx_mcp.ALLOWED_ROOTS = [self._td.name]
        self.addCleanup(setattr, cubemx_mcp, "ALLOWED_ROOTS", old)

    def test_inspect_output_ok(self):
        # 生成产物查看:文件树 + 函数签名 + pattern 匹配行(只有白名单内目录可看)
        self._allow_tmp()
        gen = os.path.join(self._td.name, "out", "Core", "Src")
        os.makedirs(gen, exist_ok=True)
        with open(os.path.join(gen, "adc.c"), "w", encoding="utf-8") as f:
            f.write("void MX_ADC1_Init(void)\n{\n  hadc1.Init.ContinuousConvMode = DISABLE;\n}\n")
        out = cubemx_mcp.cubemx_inspect_output(os.path.join(self._td.name, "out"), pattern="ContinuousConvMode")
        self.assertIn("[OK] cubemx_inspect_output", out)
        self.assertIn("void MX_ADC1_Init(void)", out)  # 函数签名
        self.assertIn("ContinuousConvMode = DISABLE", out)  # 最终值(不看全文也能拿到)
        self.assertIn("Core/Src/adc.c", out)

    def test_inspect_output_rejects_outside_roots(self):
        self._allow_tmp()
        with self.assertRaises(ValueError):
            cubemx_mcp.cubemx_inspect_output("C:/Windows")

    def test_intent_reports_defaults(self):
        # 返回值里要有"本次采用的默认值/推导值"这一段(工具自报,不靠文档)
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            out = cubemx_mcp.cubemx_intent(self.ioc, [{"peripheral": "ADC1", "pins": ["PA0", "PA1"]}])
        self.assertIn("本次采用的默认值/推导值", out)
        self.assertIn("ContinuousConvMode=DISABLE", out)

    def test_load_via_cubemx_gives_verdict(self):
        # via_cubemx=True 要明确给出"CubeMX 认不认"的结论,而不是只回显原始日志
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            ok = cubemx_mcp.cubemx_load(self.ioc, summary=False, via_cubemx=True)
        self.assertIn("CubeMX 接受这份 .ioc", ok)

    def test_load_via_cubemx_reports_rejection(self):
        with mock.patch.object(
            cubemx_mcp,
            "_run_script",
            return_value={
                "ok": False,
                "output": 'config load "x"\nKO',
                "exit_code": 1,
                "error_code": "CUBEMX_KO",
                "ko_command": 'config load "x"',
            },
        ):
            bad = cubemx_mcp.cubemx_load(self.ioc, summary=False, via_cubemx=True)
        self.assertIn("CubeMX 拒绝", bad)
        self.assertIn('config load "x"', bad)

    def test_export_pinout_ok(self):
        with mock.patch.object(cubemx_mcp, "_run_script", return_value={"ok": True, "output": "OK", "exit_code": 0}):
            r = cubemx_mcp.cubemx_export_pinout(self.ioc)
        self.assertIn("[OK]", r)
        self.assertIn("--- CSV ---", r)


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


class TestIpNotReadyDiagnosis(unittest.TestCase):
    """零产出时别再说"去查残留进程":CubeMX 日志里的 IP not ready 才是直接原因。"""

    def test_parse_single(self):
        out = "2026-10-15 23:06:47,192 [INFO] ProjectBuilder:2453 - IP not ready for code generation: SPI1\nOK\n"
        self.assertEqual(cubemx_mcp._ip_not_ready(out), ["SPI1"])

    def test_parse_dedups_and_sorts(self):
        out = "IP not ready for code generation: SPI1\nIP not ready for code generation: ADC1\nx\n"
        self.assertEqual(cubemx_mcp._ip_not_ready(out), ["ADC1", "SPI1"])

    def test_absent(self):
        self.assertEqual(cubemx_mcp._ip_not_ready("OK\nproject generate\nOK\nexit\nBye bye\n"), [])

    def test_reason_prefers_ip_not_ready(self):
        code, why = cubemx_mcp._no_output_reason("IP not ready for code generation: SPI1", "生成目录: X")
        self.assertEqual(code, "IP_NOT_READY")
        self.assertIn("SPI1", why)
        self.assertIn("生成目录: X", why)

    def test_reason_falls_back_to_no_output(self):
        code, why = cubemx_mcp._no_output_reason("OK\nBye bye\n", "生成目录: X")
        self.assertEqual(code, "CUBEMX_NO_OUTPUT")
        self.assertEqual(why, "生成目录: X")

    def test_ip_not_ready_error_code_explained(self):
        stage, desc, hint = cubemx_result.explain("IP_NOT_READY")
        self.assertEqual(stage, "post_check")
        self.assertTrue(desc and hint)
