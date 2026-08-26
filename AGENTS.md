# Vscode_cube_mcp — AGENTS.md(项目约定,agent 必读)

本目录是 Vscode_cube_mcp:一个封装 **STM32CubeMX 命令行(-q)** 的 MCP server。
任何 agent 接入本目录后,**先读本文件,再动手**。

## 第一优先:接入后先调 cubemx_help

- 本 server 的核心自述工具是 `cubemx_help`:调用它获取完整工具清单、可用芯片模板、各外设配置方法与已知坑。
- **动手建工程 / 改配置前,必须先调用 `cubemx_help`**,不要凭经验猜 set 命令语法(实测有多个坑,见下)。

## 项目结构

- `cubemx_mcp.py` — MCP server 源码(9 个工具)
- `templates/` — **薄种子**基底模板(6.18 原生 .ioc,按芯片命名;仅芯片标识+基础时钟 72MHz/SWD/SysTick,**无外设**,外设全部由 `cubemx_new_project` 的 `commands` set 现配;新增芯片用 CubeMX GUI 新建后存为 `templates/{mcu}.ioc`)
- `test_cubemx_mcp.py` — 单元测试(不依赖 CubeMX)
- `README.md` — 面向用户的手册(安装/配置/FAQ)
- `README.dev-notes.md` — 开发笔记(片段注入原理、外设实测、发布流程)

## 工具一览(9 个)

`cubemx_help` / `cubemx_script` / `cubemx_load` / `cubemx_configure` / `cubemx_generate`
/ `cubemx_export_pinout` / `cubemx_new_project` / `cubemx_remove_peripheral` / `cubemx_add_source`

## 已知坑(实测结论,勿重踩)

- **TIM 内部时钟**:`set mode TIMx` 一律 KO;`set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL`
  只改参数不改 SH/VP 表达(GUI 仍显示 ETR)。**可靠做法(0.4.0 片段注入)**:`cubemx_new_project` 的
  commands 里含 `set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL` 时,server 自动注入
  6.18 验证过的标准表达,任意 TIM/芯片适用,可附 `Prescaler`/`Period` 自定义;详见 `cubemx_help(topic="tim")`。
- **RCC 时钟**:对 RCC 执行 set 命令(如 PLLMUL)会把 `RCC.PLLSourceVirtual=HSE` 弄丢,时钟静默降级
  HSI(64MHz 而非 72MHz)。改时钟优先用 `cubemx_new_project` 的 `clock_source` / `pll_mul` 参数。
- **generate 覆盖 CMakeLists**:`cmake/stm32cubemx/CMakeLists.txt` 会被重新 generate 覆盖,自定义
  源文件需重新调 `cubemx_add_source` 加入(幂等)。
- **模板必须 6.18 原生生成**:手写/非原生 .ioc 会被 6.18 拒绝。请求的芯片无模板时,报错会给出
  可用模板清单与生成方法。

## 开发 / 验证

- 单元测试:`python -m unittest test_cubemx_mcp`(40 个,不依赖 CubeMX)
- 语法检查:`python -m py_compile cubemx_mcp.py test_cubemx_mcp.py`
- MCP 协议级冒烟:**用官方 `mcp.client.stdio.ClientSession` 连接**;手写 JSON-RPC 直连本 2.0 server 会挂起无响应。

## 原则

- 参数用"**默认值**"而非"强制":toolchain 默认 CMake、couple_files 默认 true、时钟默认 HSE 72MHz,显式传参可覆盖。
- 对本目录的一切文件修改与命令执行,向用户透明说明,重大改动前先征得同意。
