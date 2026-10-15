# golden corpus(回归基准)

用**已知正确**的工程配置当基准:改动 `intent_resolver` / 知识库 / 换 CubeMX 版本之后重放一遍,
看有没有把本来能用的配置改坏。素材来自作者真实用过的工程(`STM32VScode/` 下的几个示例)。

## 结构

每个用例一个目录,`base` 统一用 `templates/STM32F103C8T6.ioc`(最小模板,无外设),不逐个存:

```
golden/F103_i2c1_oled/
  case.json      # 意图 + 断言
  expected.ioc   # 已知正确的目标配置(来自那个真实工程)
```

`case.json` 字段:

| 字段 | 说明 |
|---|---|
| `name` / `desc` | 用例说明(目录名即用例名) |
| `source` | `expected.ioc` 来自哪个真实工程 |
| `intents` | 重放时喂给 `cubemx_intent` 的意图 |
| `expect_commands` | `apply_intents` 应产出的 set 命令(离线断言) |
| `expect_ioc_lines` | 落盘后 `.ioc` 里必须出现的行(离线 + 集成都断言) |
| `expect_init` | 生成代码里必须出现的 `MX_*_Init`(集成用) |

## 两层测试

- **离线**(默认跑,`tests/test_golden.py::TestGoldenCorpus`):不启 CubeMX —— 校验用例自洽、
  `expected.ioc` 里确实有断言行、意图能解析出预期命令。
- **集成**(设 `ST_CUBEMX_RUN_INTEGRATION=1`):真跑 CubeMX 把意图落盘,断言 `.ioc` 里的关键行,
  并与 `expected.ioc` 做**结构化 diff** 供人工核对。diff 只**报告**、不判定失败 ——
  真实工程里带 `ProjectManager` / 工具链 / NVIC 优先级等我们不管的噪声。

## 加一个用例

1. 从真实工程里挑一个"你确认配对了"的 `.ioc`,复制成 `golden/<名字>/expected.ioc`
2. 写 `case.json`:意图字段见 `cubemx_help`(topic 与工具 description),断言挑几条关键行
3. `python -m pytest tests/test_golden.py` —— 离线部分必须过
4. 有条件时设 `ST_CUBEMX_RUN_INTEGRATION=1` 再跑一次,确认重放能落出同样的关键行
