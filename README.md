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

> 如果提示 `pip` 不是命令或装到了奇怪的位置,先看文末 FAQ「pip 报错 / 装不上」。

### 第 2 步:配置环境变量(永久生效)

用 `setx` 写入**用户级环境变量**(`$env:` 的临时写法只对当前窗口有效,重启客户端后就没用了,不要用):

```powershell
setx ST_CUBEMX_EXE "C:\MINE\STM\STM\STM32CubeMX.exe"
setx ST_CUBEMX_ALLOWED_ROOTS "C:\MINE\STM32Project"
```

把路径换成你自己的:
- `ST_CUBEMX_EXE`:本机 `STM32CubeMX.exe` 的完整路径;
- `ST_CUBEMX_ALLOWED_ROOTS`:允许 AI 访问的工程根目录(多个用 `;` 分隔,如 `C:\MINE\STM32Project;C:\MINE\OTHER`)。

设置完成后**关掉并重新打开客户端**(Reasonix / VS Code 等),环境变量才会被读到。

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

> 说明:
> - `env` 里的路径和上面第 2 步二选一即可(都设也行,`env` 优先)。如果不写 `env`,就必须依赖第 2 步的系统环境变量;
> - JSON 里 Windows 路径建议用正斜杠(`C:/...`)或双反斜杠(`C:\\...`),避免转义问题;
> - `"command": "python"` 要求 `python` 在 PATH 里且就是装有本包的解释器;如果不对,改成 `"command": "py", "args": ["-m", "cubemx_mcp"]` 或写解释器完整路径(见 FAQ)。

### 验证

1. 打开客户端,发一条消息让 AI 调用 `cubemx_load`(给它一个 .ioc 路径);
2. 或在终端手动冒烟:`python -m cubemx_mcp` 启动后**不报错、不立刻退出**,即 server 正常(它是 stdio 服务,会挂起等待输入,`Ctrl+C` 退出)。

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
 {"peripheral": "GPIO", "pin": "PB13", "mode": "output", "label": "LED"}]
```

参数两种给法都行:业务参数(`freq_hz` / `duty`)由代码按定时器时钟换算,或者直接给寄存器参数
(`prescaler` / `period` / `pulse`),显式给的优先。查不到的外设 / 引脚会被**拒绝执行**,
并告诉你差什么、怎么补。

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

**Q:pip 装上了,但 `python -m cubemx_mcp` 报 `ModuleNotFoundError: No module named 'cubemx_mcp'`**
装的解释器和 `python` 指向的不是同一个。确认:
```powershell
python -m pip show vscode-cube-mcp   # 能显示才算装在当前 python 上
```
若 `python` 指向 msys2/Git/系统 Store 的 python,换成官方 Python(`py -m pip install vscode-cube-mcp`,`py -m cubemx_mcp`),或直接写解释器全路径到客户端配置。

**Q:客户端里 AI 报找不到 STM32CubeMX / `_find_cubemx` 失败**
环境变量没传进 server 进程。检查:① 是否用了 `setx`(临时 `$env:` 会失效);② 是否重启了客户端;③ `ST_CUBEMX_EXE` 路径是否存在(在 PowerShell 里 `Test-Path "C:\...\STM32CubeMX.exe"` 应为 `True`)。

**Q:报错说路径不在允许范围内(allowed roots)**
把工程所在目录加进 `ST_CUBEMX_ALLOWED_ROOTS`(多个用 `;`),改完重启客户端。如果写在客户端 `env` 里,检查 JSON 的 `;` 和路径是否被转义破坏了。

**Q:CubeMX 调用很慢或超时**
首次启动 CubeMX 较慢是正常的;把 `ST_CUBEMX_TIMEOUT` 调大(如 `600`)。另外确认没有残留的 CubeMX / Java 进程占着工程文件。

**Q:CubeMX 弹「Resolve Clock Issues」**
通常是 .ioc 时钟树不自洽(如 HSE 未启用但 PLL 选了 HSE)。这个属于工程配置问题,详见 [`README.dev-notes.md`](README.dev-notes.md) 的「从零配置时钟实战」。

**Q:`cubemx_intent` 报 `UNKNOWN_PERIPHERAL` / `UNKNOWN_PARAM`**
知识库还没收录这个外设或这种模式(不是查不到,是**故意不猜**)。三种办法:① 用 `cubemx_help`
看已收录哪些;② 直接发 `cubemx_configure` 的 `set` 命令;③ 用 `cubemx_probe` 在副本上试跑一遍,
把结果整理进 `knowledge/rules/<外设>.yaml`(见 dev-notes 的「加一个外设的 SOP」)。

**Q:`cubemx_intent` 报 `UNKNOWN_PIN_SIGNAL` / `PIN_CONFLICT`**
前者是这个引脚不支持你指定的信号(报错里会列出它支持哪些);后者是引脚已被占用(可能是当前
`.ioc` 里已有,也可能是这一批意图里重复了)。按提示换引脚即可。

**Q:报 `RULE_NOT_VERIFIED`**
这条规则还没实测过(比如早期的 USART)。为避免写出 CubeMX 不认的东西,工具会拒绝执行。
先用 `cubemx_probe` 验证,再把该规则改成 `verified: true`。

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

- [`examples/`](examples/)：真实可复现案例（LED 点灯 / I2C OLED / TIM3 PWM + 输入捕获，指令可直接复制）
- [`docs/install-claude-code.md`](docs/install-claude-code.md):Claude Code(终端版)安装配置指南
- [`README.dev-notes.md`](README.dev-notes.md):开发笔记——ST 扩展识别工程踩坑、从零配置时钟实战、各外设实测状态与能力边界、TIM 配置补丁原理

## 更新日志

### 0.4.4.1(2026-10-10)

重构:把 STM32 的事实从代码里搬进知识库,并加了"配置意图"这一层。(**未发布**,版本号先定下来)

- **新增知识库 `knowledge/`**:引脚 / 模式 / 参数从 CubeMX 自带的 `db/` 导出
  (`tools/gen_knowledge_from_db.py`);实测规则放 `knowledge/rules/*.yaml`,每条带 `verified` 与来源
- **新增 `cubemx_intent`**:只写"要什么"(外设 / 模式 / 引脚 / 频率 / 占空比),`.ioc` 内部名由知识库查出
- **新增 `cubemx_probe`**:在 `.ioc` 副本上试跑命令并输出改前改后差异(用来收录没实测过的外设)
- **报错可定位**:统一的步骤名 + 20 个错误码,失败时能看出卡在哪一步、为什么、怎么办
- **`cubemx_help` 与 TIM 注入器改为读 YAML**:`cubemx_mcp.py` 里的硬编码事实全部删掉(1295 → 642 行)
- **`.ioc` 编辑独立成层**(`ioc_editor.py`):写前自动备份 `.bak`,写后回读校验,不一致报 `READBACK_MISMATCH`
- **探针实测收录 USART**:`set mode USARTx Asynchronous` 有效(与 TIM 不同);BaudRate 等于默认值
  115200 时 CubeMX 不写出该行 —— 探针看不到不代表命令无效
- **集成测试**:真跑 CubeMX 验证注入出的 `.ioc` 能被它接受(默认跳过,`ST_CUBEMX_RUN_INTEGRATION=1` 才跑)
- 测试 72 → 133(含 5 个集成测试);覆盖率按 6 个模块统计为 92.7%
- 依赖:新增 `pyyaml>=6.0`

### 0.4.2(2026-08-27)
- **工程化补强**(非功能迭代,为「全外设完善」里程碑铺路):
  - CI:新增 GitHub Actions(windows-latest,Python 3.10-3.13 矩阵;`ruff check` + `ruff format --check` + `pytest` 覆盖率门槛 **80%**)
  - 测试:47 → **72 个**,覆盖率 71% → **94.9%**(新增模板补丁、新建工程调度层、工具包装层、help topics 等测试;覆盖率 <80% 即失败)
  - 代码质量:配置 Ruff(保守规则集)+ pre-commit 钩子;TIM 注入器硬编码枚举抽为 `_TIM_*` 常量对照表(6.18 实测来源);**修复 Python 3.10/3.11 的 f-string 反斜杠语法兼容问题**(此前低版本无法导入)
  - 新目录 [`examples/`](examples/):3 个可复现案例(LED 点灯 / I2C OLED / TIM3 PWM + 输入捕获)
  - 文档:README 增加「项目定位自白」与「用之前 vs 用之后」说明;**明确能力边界**(只做 .ioc 配置与 HAL 骨架,应用业务代码由用户编写);术语统一为通用说法(最小模板 / 配置补丁 / 内置帮助 / 基准样本等);

### 0.4.1(2026-08-27)
- **新功能:TIM PWM 配置生成**(`_inject_tim_pwm`)——命令
  `set ip parameters TIM3 PWM <pin> <signal> [Prescaler n] [Period n] [Pulse n]`
  例:`set ip parameters TIM3 PWM PA6 S_TIM3_CH1 Prescaler 72 Period 100 Pulse 50` → 10kHz / 50%
- **新功能:TIM 输入捕获配置生成**(`_inject_tim_input_capture`)——命令
  `set ip parameters TIM2 InputCapture <pin> <signal> [Prescaler n] [Period n]`
  例:`set ip parameters TIM2 InputCapture PA0-WKUP S_TIM2_CH1_ETR Prescaler 72 Period 65535`
  自动配 IC1 上升沿 + IC2 下降沿参数,TIM2 中断自动启用
- 配置生成逻辑复用公共骨架 `_rebuild_ioc_lines` / `_finish_ioc_write`(IP/Pin 重建、
  IPNb/PinsNb 同步、functionlistsort 段、幂等),与 `_inject_tim_internal_clock` 同款机制
- .ioc 内部枚举名对照表(6.18 实测):`PWM Generation1 CH1` / `Input_Capture1_from_TI1`
  (GUI 名 ≠ .ioc 内部名,写错会被 CubeMX load 时静默丢弃);PA0 信号名是组合名
  `S_TIM2_CH1_ETR`;PWM 模式名空格转义 `\ `;Channel 键值 `TIM_CHANNEL_N`
- 已知限制(实测):CubeMX 只生成 IC1 的 `sConfigIC`,测占空比的 IC2 需在 main.c
  手动 `HAL_TIM_IC_ConfigChannel` 补齐(示例见 `cubemx_help(topic="tim")`)
- 文档:`cubemx_help` tim topic 补充 PWM/输入捕获命令、F103 引脚→信号映射表、
  IC2 补配示例;README.dev-notes.md 新增「0.4.1」实测记录(基准样本逐行对齐)
- 测试:40 → **47 个**(新增 PWM/输入捕获配置生成回归:幂等、替换、非法参数、Channel 键)
- 真机验证:注入 .ioc 经 6.18 generate 后与基准样本(`PWM_IC_OLED.ioc`)逐行一致,
  生成 tim.c 参数正确(PWM 10kHz/50% + IC RISING)
- 版本规划:0.5.0 预留给"完善所有外设"里程碑;全部外设完善前,功能迭代走 0.4.x

### 0.4.0(2026-08-26)
- **架构:最小模板 + 外设配置补丁**——模板从"整机配置"精简为每芯片 1 个基础种子
  (`STM32F103C8T6.ioc` = 芯片标识 + 72MHz/SWD/SysTick,**无外设**),外设全部由
  `cubemx_new_project` 的 `commands` set 现配,模板数量不再随配置组合爆炸
- 新功能:`cubemx_help` 内置帮助工具(第 9 个工具)——完整指南 + templates/ 动态扫描 +
  分主题(gpio/i2c/tim/rcc/remove/add_source),陌生 agent 接入后第一件事调用它
- TIM 内部时钟:配置补丁(`_inject_tim_internal_clock`)替代借壳法——命令含
  `set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL` 时自动注入 6.18 验证过的
  标准表达(任意 TIM/芯片,可附 `Prescaler`/`Period` 自定义);**借壳法退役删除**
- 模板库精简:删除旧组合模板 `tim_template.ioc` / `tim2_internal.ioc` 与 `templates/README.md`
  (知识迁入 README.dev-notes.md);缺芯片模板时报错带可用模板清单与生成指引
- 修复:
  - 安全:`_check_path` 白名单前缀绕过漏洞;`cubemx_add_source` 输入校验(拒绝路径穿越/绝对路径/换行注入)
  - `cubemx_add_source` 锚点找不到时静默假成功 → 改抛错
  - `cubemx_remove_peripheral` 补 `Mcu.Pin` 重排与 `Mcu.PinsNb` 修正;functionlistsort 段删除泛化
  - `_run_script` 超时后强杀 CubeMX 进程树(防残留 Java 进程占工程文件锁)
  - `cubemx_new_project` set 命令失败立即中止;支持 `Prescaler`/`Period` 自定义
  - 配置生成/移除逻辑的行尾换行匹配、IPNb/PinsNb 缺失防御等健壮性修复
- 测试:20 → **40 个**(新增回归测试覆盖上述修复)

### 0.3.1(2026-08-07)
- 修复:pip 安装版 `cubemx_new_project` 找不到模板(`_project_template` 补 `sys.prefix/templates` 查找路径——data-files 安装时把模板放在 `sys.prefix` 下)

### 0.3.0(2026-08-07)
- 新功能:`cubemx_new_project` 参数化,设计原则 **"默认值而非强制"**:
  - `toolchain` 默认 `"CMake"`,可覆盖(EWARM V8.32 / MDK-ARM 等)
  - `couple_files` 默认 `True`(每个外设生成独立 `.c/.h`),可覆盖为 `False` 集中到 main.c
  - `clock_source` 默认 `"HSE"` + `pll_mul` 默认 `9`(8MHz×9=72MHz),写 .ioc 时按默认值补回 `RCC.PLLSourceVirtual=HSE`;生成后 HSE 仍丢失会返回 ⚠ 时钟警告
- 文档:README 新增 `cubemx_new_project` 参数说明;`.gitignore` 忽略 `*.bak`

### 0.2.2(2026-08-06)
- 修复:pyproject 作者元数据(`pip show` 的 Author 字段显示正确)

### 0.2.1(2026-08-06)
- 文档:README 重写为面向用户的手册;新增 Claude Code 安装指南

### 0.2.0(2026-08-06)
- 新功能:从零生成 HAL 工程(`cubemx_new_project`,内置模板库)
- 新功能:外设管理工具(`cubemx_remove_peripheral` / `cubemx_add_source`)
- 新功能:TIM 内部时钟工程化(借壳法,`templates/STM32F103C8T6_tim2_internal.ioc`)
- 打包:templates 随 wheel 分发(PyPI 正式发布)

### 0.1.1(2026-08-05)
- 文档:补充构建链说明(CMake+Ninja)

### 0.1.0(2026-08-05)
- 首个版本:MCP server 封装 STM32CubeMX 命令行脚本模式(`-q`)
