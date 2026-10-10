# Vscode_cube_mcp — AGENTS.md(项目约定,agent 必读)

本目录是 Vscode_cube_mcp:一个封装 **STM32CubeMX 命令行(-q)** 的 MCP server。
任何 agent 接入本目录后,**先读本文件,再动手**。

## 能力边界(最高优先)

- 本工具**只做配置层**:.ioc 工程配置 + HAL 骨架生成(引脚 / 外设 / 时钟 / 工具链),**不做应用层**。
- **不生成、不修改、不负责应用业务代码**(LED 闪烁、OLED 显示内容、传感器驱动、中断处理逻辑等)——这些由用户自己编写。
- 你(agent)在对话中若需要业务代码,应在生成后的工程文件里直接编写;但这不是本工具的能力,也不要要求工具"生成完整功能"。
- 以后给本工具提需求 / 加功能,同样遵循同一原则:配置到骨架为止,不替用户写业务逻辑。

## 第一优先:接入后先调 cubemx_help

- 本 server 的核心内置帮助工具是 `cubemx_help`:调用它获取完整工具清单、可用芯片模板、各外设配置方法与已知坑。
- **动手建工程 / 改配置前,必须先调用 `cubemx_help`**,不要凭经验猜 set 命令语法(实测有多个坑,见下)。

## 项目结构

- `cubemx_mcp.py` — MCP server 源码(11 个工具;只做参数校验、编排与结果渲染)
- `cubemx_result.py` — 步骤名、错误码与统一渲染(失败时能看出卡在哪一步)
- `knowledge_loader.py` — 读 `knowledge/` 里的 YAML;查不到就报错,不猜
- `ioc_editor.py` — `.ioc` 文本编辑(注入/移除/模板改写;写前备份、写后回读校验)
- `intent_resolver.py` — 把配置意图翻译成动作(信号名与参数都查表得到)
- `ioc_diff.py` — 比较两份 `.ioc` 的结构化差异(探针用)
- `knowledge/` — 知识库:`ip/`、`mcu/` 从 CubeMX 的 db 导出,`rules/` 是实测规则(每条带 `verified`)
- `tools/gen_knowledge_from_db.py` — 从 CubeMX 安装目录的 `db/` 导出知识库(只读)
- `templates/` — **最小模板**基底模板(6.18 原生 .ioc,按芯片命名;仅芯片标识+基础时钟 72MHz/SWD/SysTick,**无外设**,外设全部由 `cubemx_new_project` 的 `commands` set 现配;新增芯片用 CubeMX GUI 新建后存为 `templates/{mcu}.ioc`)
- `test_cubemx_mcp.py` — 单元测试(不依赖 CubeMX)
- `README.md` — 面向用户的手册(安装/配置/FAQ)
- `README.dev-notes.md` — 开发笔记(配置补丁原理、外设实测、发布流程)

## 工具一览(11 个)

`cubemx_help` / `cubemx_script` / `cubemx_load` / `cubemx_configure` / `cubemx_generate`
/ `cubemx_export_pinout` / `cubemx_new_project` / `cubemx_intent` / `cubemx_probe`
/ `cubemx_remove_peripheral` / `cubemx_add_source`

- **`cubemx_intent`**:只写"要什么"(外设 / 模式 / 引脚 / 频率 / 占空比),`.ioc` 内部名由知识库查出来 —— 优先用它。
  目前支持:`TIMx`(pwm / input_capture / internal_clock)、`GPIO`、`I2Cx`、`USARTx`、`SPIx`、`ADCx`、
  `IWDG`、`WWDG`、`CAN`、`RTC`;中断在意图里写 `interrupt` 字段(靠注入 `.ioc` 键实现);
  **DMA 不支持**(CubeMX 脚本接口没有对应命令,见 `knowledge/rules/dma.yaml`)。
- **`cubemx_probe`**:在 `.ioc` 的副本上试跑命令,输出改前改后的差异;用于收录还没实测过的外设写法。

## 已知坑(实测结论,勿重踩)

- **TIM 内部时钟**:`set mode TIMx` 一律 KO;`set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL`
  只改参数不改 SH/VP 表达(GUI 仍显示 ETR)。**可靠做法(0.4.0 配置补丁)**:`cubemx_new_project` 的
  commands 里含 `set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL` 时,server 自动注入
  6.18 验证过的标准表达,任意 TIM/芯片适用,可附 `Prescaler`/`Period` 自定义;详见 `cubemx_help(topic="tim")`。
- **RCC 时钟**:对 RCC 执行 set 命令(如 PLLMUL)会把 `RCC.PLLSourceVirtual=HSE` 弄丢,时钟静默降级
  HSI(64MHz 而非 72MHz)。改时钟优先用 `cubemx_new_project` 的 `clock_source` / `pll_mul` 参数。
- **USART(探针实测 2026-10-10)**:`set mode USARTx Asynchronous` 是有效的(和 TIM 的 `set mode TIMx`
  一律 KO 不同);BaudRate 等于 db 默认值 115200 时 CubeMX 不写出 `USARTx.BaudRate` 行 —— 探针里
  看不到不代表命令无效(换成 9600 就出现了)。详见 `knowledge/rules/usart.yaml`。
- **ADC 时钟必配(generate 会被弹窗卡死)**:配 ADC 后必须把 ADC 时钟压到上限内(F103 是 14MHz;
  72MHz PCLK2 下用 `RCC_ADCPCLK2_DIV6` = 12MHz),否则 CubeMX 判定时钟非法,generate 时弹
  `Warning: Code Generation ... [Clock]` 对话框,`-q` 批处理没人点 → 卡到 240s 超时(强杀后
  `javaw` 还会残留,要手动 taskkill)。`cubemx_intent` 配 ADC 时已按 `.ioc` 的 APB2 频率自动补。
- **ADC 多通道 / 外部触发**:多通道用 `pins`(`set mode INn` 只挂引脚,转换序列靠
  `NbrOfConversion` + `Rank-n`/`Channel-n`/`SamplingTime-n`,工具已自动补);**外部触发必须配套
  触发源外设**(如 TIM2 的 TRGO 输出),只设 ADC 的 `ExternalTrigConv` 会让 generate 卡死。
  详见 `knowledge/rules/adc.yaml`。
- **generate 覆盖 CMakeLists**:`cmake/stm32cubemx/CMakeLists.txt` 会被重新 generate 覆盖,自定义
  源文件需重新调 `cubemx_add_source` 加入(幂等)。
- **模板必须 6.18 原生生成**:手写/非原生 .ioc 会被 6.18 拒绝。请求的芯片无模板时,报错会给出
  可用模板清单与生成方法。

## 开发 / 验证

- 单元测试:`python -m unittest test_cubemx_mcp`(165 个,其中 5 个集成测试默认跳过;都不依赖 CubeMX)
- 集成测试(真启动 CubeMX,每个用例约 10s):设 `ST_CUBEMX_RUN_INTEGRATION=1` 后再跑上面的命令
- 覆盖率门槛:`python -m pytest`(低于 80% 失败;当前 90%+)
- 风格:`ruff check .` / `ruff format --check .`
- 语法检查:`python -m py_compile cubemx_mcp.py test_cubemx_mcp.py`
- MCP 协议级冒烟:**用官方 `mcp.client.stdio.ClientSession` 连接**;手写 JSON-RPC 直连本 2.0 server 会挂起无响应。

## 原则

- 参数用"**默认值**"而非"强制":toolchain 默认 CMake、couple_files 默认 true、时钟默认 HSE 72MHz,显式传参可覆盖。
- 对本目录的一切文件修改与命令执行,向用户透明说明,重大改动前先征得同意。
