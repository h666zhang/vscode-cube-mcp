# Vscode_cube_mcp

> ## ⚠️ 已废弃(Deprecated)
> 本项目已停止维护、存在已知缺陷,不建议使用;待作者想好改进措施后再考虑继续。详见 [DEPRECATED.md](DEPRECATED.md)。

[![GitHub](https://img.shields.io/badge/GitHub-h666zhang%2Fvscode--cube--mcp-181717%3Flogo%3Dgithub)](https://github.com/h666zhang/vscode-cube-mcp)  [![PyPI](https://img.shields.io/pypi/v/vscode-cube-mcp)](https://pypi.org/project/vscode-cube-mcp/)  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **项目定位**：
· 自动化流水线：CI/CD中根据配置文件自动生成初始化代码。
· 自然语言配置：用聊天描述硬件需求，直接产出可编译的工程。
· 批量变体：为不同板子快速生成不同配置。
· 版本控制友好：.ioc文本可diff，比GUI点来点去可追溯。

MCP(Model Context Protocol) server,封装 **STM32CubeMX** 官方命令行脚本模式(`-q`),
让 AI 助手可以直接加载 .ioc 工程、改引脚/外设配置、生成 HAL 代码、导出引脚表,全程无需打开 CubeMX GUI。

> **能力边界**：本工具只生成**工程配置与 HAL 骨架**（引脚、外设、时钟、工具链），
> 不负责应用逻辑代码（LED 闪烁、OLED 显示内容、传感器驱动等）——业务代码请在生成后的工程里自己写。

## 功能一览

| 工具 | 说明 |
|------|------|
| `cubemx_help` | **server 内置帮助**:工具清单、可用模板、外设配置方法(陌生 agent 建议先调用) |
| `cubemx_intent` | **按意图配置外设**:只写"要什么"(外设/模式/引脚/频率),`.ioc` 内部名由知识库查出来 —— 优先用它 |
| `cubemx_new_project` | **从零生成新工程**(内置模板库,无需预先 .ioc) |
| `cubemx_generate` | 加载 .ioc 并生成 HAL 工程 |
| `cubemx_load` | 加载 .ioc 并回读关键配置(只读) |
| `cubemx_configure` | 加载 .ioc,执行 `set` 命令序列并写回 |
| `cubemx_probe` | **探针**:在 .ioc 副本上试跑命令,输出改前改后的差异(用来收录还没实测过的外设写法) |
| `cubemx_export_pinout` | 导出引脚配置 CSV(只读) |
| `cubemx_remove_peripheral` | 从 .ioc 移除外设 |
| `cubemx_add_source` | 把自定义源文件加入 CMake 源列表 |
| `cubemx_script` | 任意 CubeMX 脚本命令序列(高级/原始脚本接口) |

## 要求

- **Python >= 3.10**(Windows 建议用官方安装版,不要用 msys2/Git 自带的 python,见 FAQ)
- **STM32CubeMX 6.x**(ST 专有软件,请从 [ST 官网](https://www.st.com/en/development-tools/stm32cubemx.html) 免费下载并自行遵守其许可;本工具仅运行时调用其命令行,不包含、不修改其代码)

## 快速开始(Windows)

三步,约 2 分钟。以下命令都在 **PowerShell** 里执行。

### 第 1 步:安装

```powershell
pip install vscode-cube-mcp
```

装完后先验证一下(能打印出版本号就说明装好了):

```powershell
python -m pip show vscode-cube-mcp
```

> 提示 `pip` 不是命令、或装到了奇怪的位置,见文末 FAQ。

### 第 2 步:设置两个环境变量(永久生效)

```powershell
setx ST_CUBEMX_EXE "C:\你的路径\STM32CubeMX.exe"      # CubeMX 可执行文件
setx ST_CUBEMX_ALLOWED_ROOTS "C:\你的工程目录"          # 允许 AI 访问的根目录(多个用 ; 分隔)
```

- 用 `setx` 写**用户级永久变量**;`$env:` 只对当前窗口有效,重启客户端就没了;
- **设完要重开客户端**才会被读到;也可以写在第 3 步的 `env` 里(二选一,`env` 优先)。

### 第 3 步:接入 MCP 客户端

把下面配置加到你所用客户端的 MCP server 列表里(以 `mcpServers` 配置为例):

```json
{
  "mcpServers": {
    "Vscode_cube_mcp": {
      "command": "python",
      "args": ["-m", "cubemx_mcp"],
      "env": {
        "ST_CUBEMX_EXE": "C:/MINE/STM/STM/STM32CubeMX.exe",
        "ST_CUBEMX_ALLOWED_ROOTS": "C:/MINE/STM32Project"
      }
    }
  }
}
```

- Windows 路径在 JSON 里建议用正斜杠(`C:/...`)或双反斜杠(`C:\\...`),避免转义问题;
- `"command": "python"` 要求 `python` 在 PATH 里、且就是装了本包的解释器;不对就改成 `"py"` 或写解释器完整路径。

### 验证

让 AI 调一次 `cubemx_help`(或给个 `.ioc` 路径调 `cubemx_load`),能返回内容就通了。
终端手动冒烟:`python -m cubemx_mcp` 不报错、不立刻退出即为正常(stdio 服务会挂起等输入,`Ctrl+C` 退出)。

## 配置项(环境变量)

| 变量 | 默认 | 说明 |
|------|------|------|
| `ST_CUBEMX_EXE` | PATH 中的 `STM32CubeMX` / 常见 Windows 安装位置 | STM32CubeMX 可执行文件完整路径 |
| `ST_CUBEMX_ALLOWED_ROOTS` | 当前工作目录 | .ioc / 生成路径允许访问的根目录,`;` 分隔(Windows) |
| `ST_CUBEMX_TIMEOUT` | `240` | 单次 CubeMX 调用超时秒数,机器慢可调大 |
| `ST_CUBEMX_KNOWLEDGE` | 与代码同级的 `knowledge/` | 知识库目录;一般不用设(源码运行与 pip 安装都会自动找到) |

> 安全设计:所有 .ioc / 生成路径**必须**在 `ST_CUBEMX_ALLOWED_ROOTS` 白名单内,白名单外的路径会被拒绝。

## 使用示例

让 AI 助手做的事情都会通过上述 11 个工具完成,例如:

- 「第一次用你,先看看你能干什么、有哪些芯片模板」→ `cubemx_help`(陌生 agent 建议第一个调用)
- 「用 STM32F103C8T6 从零建一个工程」→ `cubemx_new_project`
- 「TIM3 的 PA6 出 10kHz、占空比 50% 的 PWM;USART2 接 PA2/PA3,波特率 9600」→ **`cubemx_intent`**
- 「把 PB13 改成 GPIO_Output 并加标签 LED」→ `cubemx_intent`(也可以直接发 `cubemx_configure` 的 set 命令)
- 「把 OLED.c 加进编译,重新 generate 后也保留」→ `cubemx_add_source`

### 用意图配置外设(推荐)

`cubemx_intent` 只描述"要什么";`.ioc` 里的内部名(`S_TIM3_CH1`、`PWM Generation1 CH1` 这类)
全部由知识库查出来,AI 不需要记住它们:

```json
[{"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "freq_hz": 10000, "duty": 0.5},
 {"peripheral": "TIM2", "mode": "input_capture", "pin": "PA0-WKUP"},
 {"peripheral": "I2C1", "pins": ["PB8", "PB9"]},
 {"peripheral": "USART2", "pins": ["PA2", "PA3"], "baudrate": 9600},
 {"peripheral": "ADC1", "pins": ["PA0", "PA1"], "sampling_time": "ADC_SAMPLETIME_55CYCLES_5",
  "continuous": true, "interrupt": true},
 {"peripheral": "GPIO", "pin": "PB13", "mode": "output", "label": "LED"}]
```

参数两种给法都行:业务参数(`freq_hz` / `duty`)由代码按定时器时钟换算,或者直接给寄存器参数
(`prescaler` / `period` / `pulse`),显式给的优先。查不到的外设 / 引脚会被**拒绝执行**,
并告诉你差什么、怎么补。引脚可以写短名(`PA0` 会自动归一化成 `PA0-WKUP`)。

有些"必做但容易漏"的项,工具会**自己补上**(依据都写在 `knowledge/rules/*.yaml` 里,带实测来源):

- 配 ADC 时按 `.ioc` 里的 APB2 频率自动选合法的 ADC 预分频 —— 不配的话 CubeMX 会弹
  `[Clock]` 对话框,`-q` 批处理没人点 → generate 卡死到超时;
- ADC 多通道时自动补转换序列(`NbrOfConversion` + 每个通道的 `Rank-n` / `Channel-n` / `SamplingTime-n`);
- `interrupt: true` 自动注入对应的 NVIC 键,并按外设选对向量名(如 ADC 用共用的 `ADC1_2_IRQn`)。

## 知识库(`knowledge/`)

工具不靠"记忆"写 `.ioc`:事实只有两个来源,都在 `knowledge/` 里(纯文本,可 diff、可审):

| 目录 | 内容 | 来源 |
|------|------|------|
| `knowledge/ip/` | 每个 IP 的合法模式名、参数与取值范围 | 从 CubeMX 安装目录的 `db/mcu/IP/*.xml` **导出** |
| `knowledge/mcu/` | 芯片引脚表:每个引脚支持哪些信号 | 从 CubeMX 的 `db/mcu/<芯片>.xml` **导出** |
| `knowledge/rules/` | 命令写法、`.ioc` 片段、已知坑 | **用探针实测**,每条带 `verified` 与来源 |

- 只有 `verified: true` 的规则会被执行;`false` 的会被拒绝(提示先用 `cubemx_probe` 验证)。
- 换芯片 / 升级 CubeMX 后重新导出:`python tools/gen_knowledge_from_db.py --mcu STM32F103C8T6`
- 目录可用 `ST_CUBEMX_KNOWLEDGE` 指定,一般不用设(源码运行与 pip 安装都会自动找到)。

内置模板库(`templates/`,按芯片型号命名的**最小模板**:仅芯片标识 + 基础时钟 72MHz/SWD/SysTick,**无外设**,如 `STM32F103C8T6.ioc`);外设全部由 `cubemx_new_project` 的 `commands` set 命令现配,TIM 内部时钟由配置补丁自动处理。新增芯片只需把该芯片 6.18 原生 .ioc 放进 `templates/`。

### `cubemx_new_project` 参数(设计原则:默认值而非强制)

| 参数 | 默认 | 说明 |
|------|------|------|
| `project_name` | 必填 | 工程名(仅字母/数字/下划线) |
| `project_dir` | 必填 | 生成目标目录(须在白名单内) |
| `mcu` | `STM32F103C8T6` | 芯片型号,匹配 `templates/{mcu}.ioc` |
| `template` | 按 mcu 查找 | 指定模板 .ioc 路径,优先于 mcu |
| `toolchain` | `"CMake"` | 目标工具链,可覆盖为 `"EWARM V8.32"` / `"MDK-ARM"` / `"STM32CubeIDE"` 等 |
| `couple_files` | `True` | 每个外设生成独立 `.c/.h`(CubeMX 的 "Generate peripheral initialization as a pair of '.c/.h' files per peripheral");`False` 则全部初始化集中到 main.c |
| `clock_source` | `"HSE"` | PLL 时钟源,默认外部晶振 72MHz;`"HSI"` 用内部 RC |
| `pll_mul` | `9` | PLL 倍频(8MHz×9=72MHz),可覆盖(如 HSI 配 16 → 64MHz) |
| `commands` | 空 | `set` 命令列表,如 `["set pin PB13 GPIO_Output"]` |

> **默认值而非强制**:默认生成 CMake 工具链 + 外设独立 `.c/.h` + 72MHz(HSE×9),
> 显式传其他值即覆盖,不会被工具卡死。写 .ioc 时会按默认值补回 `RCC.PLLSourceVirtual=HSE`
> (CubeMX 的 set RCC 命令可能把该字段弄丢,导致时钟静默降级 HSI);生成后若仍丢失,返回值会附 ⚠ 时钟警告。

## 升级到新版本

升级到 PyPI 最新版:

```powershell
python -m pip install -U vscode-cube-mcp
python -m pip show vscode-cube-mcp   # 确认版本号
```

升级后**必须重启客户端**(Reasonix / Claude 等),MCP server 进程才会加载新代码;
若重启后工具参数仍是旧的,等几秒或新开一个会话(host 侧工具快照可能滞后)。

> 维护者发布新版流程(改版本号 → `python -m build` → `twine upload` → `git tag` + push)
> 见 [`README.dev-notes.md`](README.dev-notes.md)。

## 常见问题 FAQ

**pip 装上了,但 `python -m cubemx_mcp` 报 `ModuleNotFoundError: No module named 'cubemx_mcp'`**
装的解释器和 `python` 指向的不是同一个。`python -m pip show vscode-cube-mcp` 能显示才算装对;
若 `python` 指向 msys2/Git/Store 版,改用官方 Python(`py -m pip install vscode-cube-mcp`),或在客户端配置里写解释器完整路径。

**客户端里 AI 报找不到 STM32CubeMX**
环境变量没传进 server 进程。检查:① 是否用了 `setx`(临时 `$env:` 会失效);② 是否重启了客户端;
③ `Test-Path "C:\...\STM32CubeMX.exe"` 是否为 `True`。

**报错说路径不在允许范围内(allowed roots)**
把工程目录加进 `ST_CUBEMX_ALLOWED_ROOTS`(多个用 `;`),改完重启客户端。写在客户端 `env` 里时注意 JSON 转义。

**CubeMX 调用很慢或超时**
首次启动 CubeMX 较慢是正常的;把 `ST_CUBEMX_TIMEOUT` 调大(如 `600`)。另外确认没有残留的 CubeMX / Java 进程占着工程文件。

**`cubemx_intent` 报 `UNKNOWN_PERIPHERAL` / `UNKNOWN_PARAM`**
知识库还没收录这个外设或这种模式(不是查不到,是**故意不猜**)。三种办法:① `cubemx_help` 看已收录哪些;
② 直接发 `cubemx_configure` 的 `set` 命令;③ 用 `cubemx_probe` 试跑一遍,把结果整理进 `knowledge/rules/<外设>.yaml`。

**`cubemx_intent` 报 `UNKNOWN_PIN_SIGNAL` / `PIN_CONFLICT`**
前者是这个引脚不支持你指定的信号(报错里会列出它支持哪些);后者是引脚已被占用。按提示换引脚即可。

**报 `RULE_NOT_VERIFIED`**
这条规则还没实测过(如早期的 USART)。为避免写出 CubeMX 不认的东西,工具会拒绝执行。先 `cubemx_probe` 验证,再把规则改成 `verified: true`。

**CubeMX 弹「Resolve Clock Issues」**
通常是 .ioc 时钟树不自洽(如 HSE 未启用但 PLL 选了 HSE),属于工程配置问题,详见 [`README.dev-notes.md`](README.dev-notes.md) 的「从零配置时钟实战」。

## 开发与测试

```bash
python -m unittest test_cubemx_mcp             # 133 个测试(其中 5 个集成测试默认跳过)
python -m pytest                              # 带覆盖率门槛(低于 80% 失败)
ruff check . && ruff format --check .          # 风格
```

真跑 CubeMX 的集成测试(每个用例约 10s,会启动 CubeMX,用于验证"写出来的 .ioc CubeMX 真的认"):

```powershell
$env:ST_CUBEMX_RUN_INTEGRATION=1; python -m unittest test_cubemx_mcp.TestCubeMXIntegration
```

技术栈:Python >= 3.10 + mcp SDK 2.x(stdio);打包 setuptools + build + twine;目标平台 Windows(跨平台可用)。

## 许可与依赖声明

- 本工具代码:MIT License(见 `LICENSE`)
- mcp SDK(唯一 Python 依赖):MIT License(modelcontextprotocol/python-sdk)
- STM32CubeMX:ST 专有软件,运行时外部调用,需用户自备并遵守其许可条款

## 更多资料

- [`README.dev-notes.md`](README.dev-notes.md):开发笔记 —— 知识库怎么来的、加外设 / 加芯片的 SOP、报错码表、各外设实测结论
- [`CHANGELOG.md`](CHANGELOG.md):版本变更记录
- [`AGENTS.md`](AGENTS.md):给 agent 的项目约定(接入本目录前先读)
- [`docs/install-claude-code.md`](docs/install-claude-code.md):Claude Code(终端版)安装配置指南

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。
