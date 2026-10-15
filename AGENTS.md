# Vscode_cube_mcp — AGENTS.md(项目约定,agent 必读)

本目录是 Vscode_cube_mcp:封装 **STM32CubeMX 命令行(-q)** 的 MCP server。

## 能力边界(最高优先)

- 只做**配置层**:`.ioc` 工程配置 + HAL 骨架生成(引脚 / 外设 / 时钟 / 工具链),**不做应用层**。
- **不生成、不修改、不负责业务代码**(LED 闪烁、OLED 显示、传感器驱动、中断处理逻辑等)——
  用户在生成后的工程里自己写。
- 给本工具提需求 / 加功能,同样到骨架为止。

## 接入后的路径

1. **先调 `cubemx_help`** —— 工具清单、可用模板、各外设写法与已知坑都在那里;
   不要凭经验猜 set 命令语法。
2. 常规顺序:`cubemx_load`(读现状)→ `cubemx_intent`(配,自动补时钟/DMA/NVIC 与隐含键)
   → `cubemx_probe`(在副本上预演)→ 落盘 → `cubemx_generate` → `cubemx_inspect_output`(验收)。
3. **11 个工具的 description 首段都写了「什么时候不用我」**,照那个走就不会选错;
   其中 `cubemx_raw_script` 是降级接口,主链路上没有它的位置。

## 项目结构

- `src/vscode_cube_mcp/` — 包源码(src 布局,入口 `python -m vscode_cube_mcp`)
  - `cubemx_mcp.py` — MCP server(11 个工具;只做参数校验、编排与结果渲染)
  - `cubemx_result.py` — 步骤名、错误码与统一渲染(失败时能看出卡在哪一步)
  - `knowledge_loader.py` — 读 `knowledge/` 的 YAML;查不到就报错,不猜
  - `ioc_editor.py` — `.ioc` 文本编辑 + **离线语义预检**(写前备份、写后回读校验)
  - `intent_resolver.py` — 意图 → 动作(信号名与参数都查表得到)
  - `ioc_diff.py` / `output_inspector.py` — 结构化 diff / 看生成产物(都只读)
- `knowledge/` — 知识库:`ip/`、`mcu/` 从 CubeMX 的 db 导出,`rules/` 是实测规则(每条带 `verified`)
- `templates/` — 6.18 原生最小模板(按芯片命名;仅芯片标识 + 72MHz/SWD/SysTick,**无外设**)
- `tests/` — 按模块拆开的单元测试(不依赖 CubeMX)+ `integration/`(真启动 CubeMX,默认跳过)
- `tools/gen_knowledge_from_db.py` — 从 CubeMX 安装目录的 `db/` 导出知识库(只读)

## agent 必须知道的坑

- **TIM 内部时钟**:`set mode TIMx` 一律 KO。用 `cubemx_intent` 的 `internal_clock`
  (工具会注入 6.18 验证过的标准表达)。
- **RCC 时钟**:对 RCC 发 set 命令(如 PLLMUL)会把 `RCC.PLLSourceVirtual=HSE` 弄丢、时钟静默降级
  (HSI 64MHz)。改时钟用 `cubemx_new_project` 的 `clock_source` / `pll_mul`。
- **USART**:`set mode USARTx Asynchronous` 有效;但 **BaudRate 等于 db 默认值(115200)时 CubeMX
  不写出那一行** —— 探针/回读里看不到不代表命令没生效(换成 9600 就出现了)。
- **有些参数不在 `.ioc` 里**:`ContinuousConvMode` / `ScanConvMode` 这类"取默认值"的行会被省掉,
  **最终值要读生成的代码**:`cubemx_inspect_output(project_dir, pattern=...)`。
- **"移除外设再加回"会留下中间态**:摘要的 `init_order` 会暂时少一项(CubeMX 下次 generate 自己重建,
  生成代码不受影响),别据此以为外设没配上。
- **模板必须 6.18 原生生成**:手写/非 6.18 的 `.ioc` 会被拒绝;缺模板时报错会列出可用模板与生成方法。
- **用户代码写哪里**:CMake 工程写工程根的 `CMakeLists.txt`(`# Add sources to executable` /
  `# Add include paths`,只生成一次、不重写);`cmake/stm32cubemx/CMakeLists.txt` 每次 generate 全量重写,别动。
  STM32CubeIDE 用 `STM32CubeIDE/Application/User/Core/`;EWARM / MDK-ARM 未实测。
- **其余"实测过的坑"和"工具已经自动处理的坑"**(ADC 时钟、SPI 波特率、`project path`、
  零产出校验……)见 `docs/README.dev-notes.md` 与 `knowledge/rules/*.yaml` —— 那些不需要 agent 记。

## 开发 / 验证

- 测试:`python -m pytest`(不依赖 CubeMX;**覆盖率门槛 80%**,当前 ~92%)
- 集成测试(真启动 CubeMX,每个用例 10~30s):`ST_CUBEMX_RUN_INTEGRATION=1` 后再跑上面的命令 ——
  改了工具默认值或返回格式后**务必跑一次**(默认跳过,断言容易悄悄过时)
- 风格:`ruff check .` / `ruff format --check .`;语法:`python -m compileall -q src tests`
- **改了 `src/` 后必须重启宿主**:Reasonix 把本 server 当长驻进程**复用**,开新会话不会重启它;
  `knowledge/*.yaml` 不受影响(缓存按文件 mtime 失效)

## 原则

- 参数用"**默认值**"而非"强制":toolchain 默认 CMake、couple_files 默认 true、时钟默认 HSE 72MHz,
  显式传参可覆盖。
- 对本目录的一切文件修改与命令执行,向用户透明说明,重大改动前先征得同意。
