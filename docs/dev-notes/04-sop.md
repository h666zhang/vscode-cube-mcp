# SOP(加外设 / 加芯片)

---

## 加一个外设的 SOP

1. **先探针**:把猜的写法写出来,`cubemx_probe` 在模板副本上跑一遍 —— `commands` 跑脚本命令、
   `inject` 跑**键注入类**(DMA / TIM 内部时钟 / PWM / 输入捕获 / NVIC 行,用法见
   `cubemx_help(topic="probe")`);给了 `inject` 时输出还会写明 CubeMX 是**原样保留**还是
   **改写/丢弃**了注入内容。看 `ioc_diff` 给出的真实差异;
2. **写规则**:`knowledge/rules/<外设>.yaml` 里写 `command_examples` + `ioc_result` + `known_issues`,
   标 `verified: true` 与 `source`(日期 / CubeMX 版本);
3. **加意图(可选)**:要让 `cubemx_intent` 支持它,就在 `intent_resolver.py` 加一个分支 ——
   命令类(如 GPIO/I2C/USART)复用 `_pin_commands`;片段注入类(如 TIM)照 `_apply_tim` 的写法;
4. **补测试**:单元测试(mock 掉 `_run_script`)+ 集成测试用例(`TestCubeMXIntegration`);
5. **验证**:`python -m pytest`,最后真跑一次集成测试确认 CubeMX 认。

> ⚠️ 探针里**看不到差异 ≠ 命令无效**。实测例子:USART2 的 `BaudRate 115200` 正好等于 db 默认值,
> CubeMX 就不写出 `USART2.BaudRate` 行;换成 9600 才出现。所以探针要用**非默认值**再试一遍。

## 加一个芯片的 SOP


## 加一个芯片的 SOP

1. 用 CubeMX GUI 新建该芯片工程,存成 `templates/{mcu}.ioc`(**必须 6.18 原生生成**,手写会被拒);
2. 跑 `python tools/gen_knowledge_from_db.py --mcu <型号>` 导出引脚表与 IP 事实;
3. 在 `pyproject.toml` 的 `[tool.setuptools.data-files]` 里登记新模板(和 `knowledge/` 一样要随包分发);
4. 跑测试;`knowledge_loader` 会自动找到 `knowledge/mcu/<芯片>.pins.yaml`。

> 注意:知识库里每颗芯片一份引脚表,`--mcu` 换芯片就是重新导出;当前导的是 `STM32F103C8T6`
> (对应 `db/mcu/STM32F103C(8-B)Tx.xml`)。

## 开发与测试
