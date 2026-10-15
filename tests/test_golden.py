"""golden corpus:用已知正确的工程配置做回归。

两层:
  - 离线(默认跑):不启 CubeMX —— 校验用例自洽、expected.ioc 里确实有断言行、意图能解析出预期命令。
  - 集成(设 ST_CUBEMX_RUN_INTEGRATION=1):真跑 CubeMX 把意图重放落盘,断言 .ioc 里的关键行,
    并与 expected.ioc 做结构化 diff 供人工核对(只报告,不判定失败)。

用例在仓库根的 golden/ 下,说明见 golden/README.md。
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from vscode_cube_mcp import intent_resolver, ioc_diff, knowledge_loader

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "golden"
TEMPLATE = ROOT / "templates" / "STM32F103C8T6.ioc"
INTEGRATION = os.environ.get("ST_CUBEMX_RUN_INTEGRATION") == "1"


def golden_cases():
    """[(目录, case.json 解析结果), ...],按目录名排序。"""
    out = []
    for d in sorted(GOLDEN.iterdir()):
        spec = d / "case.json"
        if spec.is_file():
            out.append((d, json.loads(spec.read_text(encoding="utf-8"))))
    return out


class TestGoldenCorpus(unittest.TestCase):
    """离线:用例本身自洽 + 意图能解析出预期的 set 命令(不启 CubeMX)。"""

    def setUp(self):
        knowledge_loader.reload()

    def test_at_least_six_cases(self):
        self.assertGreaterEqual(len(golden_cases()), 6, "golden/ 下至少要有 6 个用例")

    def test_case_files_and_assert_lines_are_consistent(self):
        for d, case in golden_cases():
            with self.subTest(case=d.name):
                for field in ("desc", "source", "intents", "expect_commands", "expect_ioc_lines"):
                    self.assertIn(field, case, f"{d.name}: case.json 缺 {field}")
                exp = d / "expected.ioc"
                self.assertTrue(exp.is_file(), f"{d.name}: 缺 expected.ioc")
                text = exp.read_text(encoding="utf-8", errors="replace")
                for line in case["expect_ioc_lines"]:
                    self.assertIn(line, text, f"{d.name}: expected.ioc 里没有断言行 {line!r}")

    def test_intents_resolve_to_expected_commands(self):
        for d, case in golden_cases():
            with self.subTest(case=d.name):
                root = tempfile.mkdtemp(prefix="golden_resolve_")
                try:
                    ioc = os.path.join(root, "proj.ioc")
                    shutil.copy(TEMPLATE, ioc)
                    plan = intent_resolver.apply_intents(ioc, case["intents"])
                    joined = "; ".join(plan["commands"])
                    for cmd in case["expect_commands"]:
                        self.assertIn(cmd, joined, f"{d.name}: 命令里没有 {cmd!r}\n实际: {joined}")
                    # 每个意图都该自报"采用了哪些默认/推导值"
                    self.assertTrue(plan["decisions"], f"{d.name}: 没有自报 decisions")
                finally:
                    shutil.rmtree(root, ignore_errors=True)

    def test_expected_ioc_is_valid_template_shape(self):
        """expected.ioc 应该是 6.18 原生的形态(有 Mcu.Name / board=custom),否则它当不了基准。"""
        for d, _case in golden_cases():
            with self.subTest(case=d.name):
                text = (d / "expected.ioc").read_text(encoding="utf-8", errors="replace")
                self.assertIn("Mcu.Name=STM32F103C(8-B)Tx", text)
                self.assertIn("board=custom", text)


@unittest.skipUnless(INTEGRATION, "设 ST_CUBEMX_RUN_INTEGRATION=1 才会真启动 CubeMX(约 4 分钟)")
class TestGoldenIntegration(unittest.TestCase):
    """集成:真跑 CubeMX 重放意图,断言关键行;并给出与 expected.ioc 的结构化 diff。"""

    def setUp(self):
        knowledge_loader.reload()
        from vscode_cube_mcp import cubemx_mcp

        self.cubemx = cubemx_mcp

    def test_each_case_replays_to_expected_lines(self):
        for d, case in golden_cases():
            with self.subTest(case=d.name):
                root = tempfile.mkdtemp(prefix="golden_replay_")
                try:
                    ioc = os.path.join(root, "proj.ioc")
                    shutil.copy(TEMPLATE, ioc)
                    plan = intent_resolver.apply_intents(ioc, case["intents"])

                    # 会话 1:写配置(与 cubemx_intent 的真实行为一致:load + 命令 + saveas)
                    script = "\n".join([f'config load "{ioc}"', *plan["commands"], f'config saveas "{ioc}"'])
                    r1 = self.cubemx._run_script(script)
                    self.assertTrue(r1["ok"], f"{d.name}: 配置会话失败\n{r1.get('output', '')[-600:]}")

                    # 会话 2:生成(与 cubemx_generate 一致:单独一次 load + generate)
                    r2 = self.cubemx._run_script(f'config load "{ioc}"\nproject generate')
                    self.assertTrue(r2["ok"], f"{d.name}: generate 失败\n{r2.get('output', '')[-600:]}")

                    text = Path(ioc).read_text(encoding="utf-8", errors="replace")
                    for line in case["expect_ioc_lines"]:
                        self.assertIn(line, text, f"{d.name}: 重放后 .ioc 里没有 {line!r}")

                    # 生成的代码里应该出现对应的 MX_*_Init(generate 的产出也可能是空目录)
                    gen = "\n".join(
                        p.read_text(encoding="utf-8", errors="replace") for p in Path(root, "Core", "Src").glob("*.c")
                    )
                    self.assertTrue(gen, f"{d.name}: Core/Src 下没有 .c(零产出?)")
                    for fn in case.get("expect_init") or []:
                        self.assertIn(fn, gen, f"{d.name}: 生成代码里没有 {fn}")

                    # 结构化 diff 只报告(真实工程带 ProjectManager / NVIC 优先级等噪声)。
                    # diff 的 added/removed/changed 都是 "key=value" —— 断言行若出现在里面,
                    # 说明重放出来的这份和 expected.ioc 不一致
                    diff = ioc_diff.diff_files(str(d / "expected.ioc"), ioc)
                    lines = (diff.get("added") or []) + (diff.get("removed") or []) + (diff.get("changed") or [])
                    print(f"\n[{d.name}] 与 expected.ioc 的结构化差异: {diff['summary']}")
                    print(ioc_diff.render_diff(diff)[:1200])
                    missed = [ln for ln in case["expect_ioc_lines"] if any(ln in x for x in lines)]
                    self.assertEqual(missed, [], f"{d.name}: 断言行与 expected.ioc 不一致: {missed}")
                finally:
                    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
