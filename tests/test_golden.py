"""golden corpus:用已知正确的工程配置做回归。

两层:
  - 离线(默认跑):不启 CubeMX —— 校验用例自洽、expected.ioc 里确实有断言行、意图能解析出预期命令。
  - 集成(设 ST_CUBEMX_RUN_INTEGRATION=1):真跑 CubeMX 把意图重放落盘,断言 .ioc 里的关键行,
    并与 expected.ioc 做结构化 diff 供人工核对(只报告,不判定失败)。

集成层每个用例是**独立的 test 方法**(`test_replay_<用例名>`),所以可以只跑某一个:

    $env:ST_CUBEMX_RUN_INTEGRATION=1
    python -m pytest tests/test_golden.py -k F103_gpio_out -q -s

用例在仓库根的 golden/ 下,说明见 golden/README.md。
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from vscode_cube_mcp import intent_resolver, ioc_diff, ioc_editor, knowledge_loader

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


@unittest.skipUnless(INTEGRATION, "设 ST_CUBEMX_RUN_INTEGRATION=1 才会真启动 CubeMX(每个用例约 45s)")
class TestGoldenIntegration(unittest.TestCase):
    """集成:真跑 CubeMX 重放意图,断言关键行;并给出与 expected.ioc 的结构化 diff。"""

    def setUp(self):
        knowledge_loader.reload()
        from vscode_cube_mcp import cubemx_mcp

        self.cubemx = cubemx_mcp

    def _replay(self, d: Path, case: dict) -> None:
        root = tempfile.mkdtemp(prefix="golden_replay_")
        try:
            ioc = os.path.join(root, "proj.ioc")
            shutil.copy(TEMPLATE, ioc)
            plan = intent_resolver.apply_intents(ioc, case["intents"])

            # 会话 1:写配置(与 cubemx_intent 的真实行为一致:load + 命令 + saveas)
            script = "\n".join([f'config load "{ioc}"', *plan["commands"], f'config saveas "{ioc}"'])
            r1 = self.cubemx._run_script(script)
            self.assertTrue(
                r1["ok"], f"{d.name}: 配置会话失败(exit={r1.get('exit_code')})\n{r1.get('output', '')[-600:]}"
            )

            # 会话 1 之后、generate 之前,注入"脚本配不了"的键组(NVIC 行 + DMA)—— 与
            # cubemx_intent / cubemx_new_project 的顺序一致;漏了这一步 DMA 根本不会生效
            ioc_editor.add_lines(ioc, plan.get("ioc_lines") or [])
            for params in plan.get("dma") or []:
                ioc_editor.inject_dma(ioc, **params)

            # 会话 2:生成(与 cubemx_generate 一致:单独一次 load + generate)
            r2 = self.cubemx._run_script(f'config load "{ioc}"\nproject generate')
            self.assertTrue(r2["ok"], f"{d.name}: generate 失败\n{r2.get('output', '')[-600:]}")

            text = Path(ioc).read_text(encoding="utf-8", errors="replace")
            for line in case["expect_ioc_lines"]:
                self.assertIn(line, text, f"{d.name}: 重放后 .ioc 里没有 {line!r}")

            # 生成的代码里应该出现对应的 MX_*_Init(generate 也可能"报 OK 却零产出")
            gen = "\n".join(
                p.read_text(encoding="utf-8", errors="replace") for p in Path(root, "Core", "Src").glob("*.c")
            )
            self.assertTrue(gen, f"{d.name}: Core/Src 下没有 .c(零产出?)")
            for fn in case.get("expect_init") or []:
                self.assertIn(fn, gen, f"{d.name}: 生成代码里没有 {fn}")

            # 结构化 diff 只报告(真实工程带 ProjectManager / NVIC 优先级 / 时钟树等噪声)。
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


@unittest.skipUnless(INTEGRATION, "设 ST_CUBEMX_RUN_INTEGRATION=1 才会真启动 CubeMX(这一条约 70s)")
class TestGoldenIdempotent(unittest.TestCase):
    """端到端幂等:同一批命令重复执行,`.ioc` 的结构化内容必须收敛。

    这是 agent 重试的真实形态 —— 它拿着同一批命令再跑一遍(而不是重算意图:重算会撞
    PIN_CONFLICT,因为引脚已经占上了)。比的是**结构化内容**(排除 ioc_diff.DEFAULT_IGNORE
    里的版本 / 面板状态 / 工程名等噪声键),不是文件字节 —— CubeMX 每次 saveas 都会改那些。

    实测(2026-10-15):GPIO 用例第 1 次就稳定;I2C1 / ADC 用例第 1 次与第 2 次不同
    (第一次是"从模板到配置"的过渡形态,CubeMX 再 load + saveas 一次才补全键),但
    **第 2 次起不再变化** —— 对重试是安全的。
    """

    CASE = "F103_i2c1_oled"  # 挑一个"第 1 次不收敛"的,最能验证重复执行的稳定性
    ROUNDS = 3

    def setUp(self):
        knowledge_loader.reload()
        from vscode_cube_mcp import cubemx_mcp

        self.cubemx = cubemx_mcp

    @staticmethod
    def _content_hash(path) -> str:
        import hashlib

        entries = ioc_diff.parse_ioc(Path(path).read_text(encoding="utf-8", errors="replace"))
        keep = {k: v for k, v in entries.items() if not ioc_diff._is_ignored(k, ioc_diff.DEFAULT_IGNORE)}
        return hashlib.sha256(json.dumps(sorted(keep.items()), ensure_ascii=False).encode()).hexdigest()

    def test_replaying_same_commands_converges(self):
        d = GOLDEN / self.CASE
        case = json.loads((d / "case.json").read_text(encoding="utf-8"))
        root = tempfile.mkdtemp(prefix="golden_idem_")
        try:
            ioc = os.path.join(root, "proj.ioc")
            shutil.copy(TEMPLATE, ioc)
            plan = intent_resolver.apply_intents(ioc, case["intents"])  # 只算一次,之后重复执行
            script = "\n".join([f'config load "{ioc}"', *plan["commands"], f'config saveas "{ioc}"'])
            hashes = []
            for _ in range(self.ROUNDS):
                r = self.cubemx._run_script(script)
                self.assertTrue(r["ok"], r.get("output", "")[-500:])
                hashes.append(self._content_hash(ioc))
            print(f"\n[{self.CASE}] 重复 {self.ROUNDS} 次的 .ioc 内容 hash: {[h[:10] for h in hashes]}")
            self.assertEqual(len(set(hashes[1:])), 1, f"重复执行后内容还在变: {hashes}")
        finally:
            shutil.rmtree(root, ignore_errors=True)


def _make_case_test(dir_name: str, case: dict):
    def test(self):
        self._replay(GOLDEN / dir_name, case)

    test.__name__ = f"test_replay_{dir_name}"
    test.__doc__ = f"{dir_name}: {case.get('desc', '')}"
    return test


# 每个用例生成一个独立方法,便于单独跑(-k)
for _d, _case in golden_cases():
    setattr(TestGoldenIntegration, f"test_replay_{_d.name}", _make_case_test(_d.name, _case))


if __name__ == "__main__":
    unittest.main()
