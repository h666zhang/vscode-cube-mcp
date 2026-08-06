# Vscode_cube_mcp

> 全部由 deepseek-v4-flash 生成

[![GitHub](https://img.shields.io/badge/GitHub-h666zhang%2Fvscode--cube--mcp-181717%3Flogo%3Dgithub)](https://github.com/h666zhang/vscode-cube-mcp)  [![PyPI](https://img.shields.io/pypi/v/vscode-cube-mcp)](https://pypi.org/project/vscode-cube-mcp/)  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
- **GitHub**: https://github.com/h666zhang/vscode-cube-mcp
- **PyPI**: https://pypi.org/project/vscode-cube-mcp/

MCP(Model Context Protocol) server,封装 **STM32CubeMX** 官方命令行脚本模式(`-q`),
让 AI 助手可以直接加载 .ioc 工程、改引脚/外设配置、生成 HAL 代码、导出引脚表。

每次调用:命令序列写入临时脚本 → 启动 `STM32CubeMX -q` → 超时强杀
→ 过滤 log4j 噪音 → 检测 KO 失败标记 → 返回干净输出。

## 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python >= 3.10 |
| MCP 框架 | mcp SDK 2.x(官方 Model Context Protocol Python SDK,stdio 传输) |
| 交互对象 | STM32CubeMX 6.x(-q 脚本模式,外部工具,运行时 subprocess 调用) |
| 打包发布 | setuptools + build + twine(PyPI) |
| 测试 | unittest(标准库,零依赖) |
| 配套构建链 | CMake + Ninja + arm-none-eabi-gcc(STM32CubeMX 生成的工程采用 CMakePresets 配置,由 Ninja 构建,arm-none-eabi 工具链链接) |
| 目标平台 | Windows(主);跨平台可用(含 os.name 分支的通用探测) |

## 功能

| 工具 | 说明 |
|------|------|
| `cubemx_script` | 任意 CubeMX 脚本命令序列(逃生通道) |
| `cubemx_load` | 加载 .ioc 并回读关键配置(只读) |
| `cubemx_configure` | 加载 .ioc,执行 `set` 命令序列,`saveas` 写回 |
| `cubemx_generate` | 加载 .ioc 并 `project generate` 生成 HAL 工程 |
| `cubemx_export_pinout` | 导出引脚配置 CSV(只读) |
| `cubemx_new_project` | **从零生成新工程**(模板 + set 命令 + generate,无需预先 .ioc) |
| `cubemx_remove_peripheral` | 从 .ioc **移除外设**(文本方式,解决 `set noparam` 无效) |
| `cubemx_add_source` | 把自定义源文件**加入 CMake 源列表**(generate 覆盖后可重补) |

## 要求

- Python **>= 3.10**,安装依赖 `mcp>=2.0`
- **STM32CubeMX**(ST 专有软件,请从 ST 官网免费下载并自行遵守其许可)—— 本工具仅运行时调用其命令行,不包含、不修改其任何代码

## 安装

```bash
pip install vscode-cube-mcp        # 从 PyPI
# 或本地开发安装
pip install -e .
```

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|------|------|------|
| `ST_CUBEMX_EXE` | PATH 中的 `STM32CubeMX` / 常见 Windows 安装位置 | STM32CubeMX 可执行文件完整路径 |
| `ST_CUBEMX_ALLOWED_ROOTS` | 当前工作目录 | .ioc 允许访问的根目录,`os.pathsep` 分隔(`;` for Windows) |
| `ST_CUBEMX_TIMEOUT` | `240` | CubeMX 子进程超时秒数 |

示例(Windows PowerShell):

```powershell
$env:ST_CUBEMX_EXE = "C:\MINE\STM\STM\STM32CubeMX.exe"
$env:ST_CUBEMX_ALLOWED_ROOTS = "C:\MINE\STM32Project\STM32VScode"
```

> 安全设计:所有 .ioc / 生成路径都必须在 `ST_CUBEMX_ALLOWED_ROOTS` 白名单内,
> 白名单外的路径会被拒绝(路径校验见 `_check_path`)。

## 使用(接入 MCP 客户端)

把 server 注册到支持 MCP 的客户端(如 Reasonix / Claude Desktop 等),
stdio 方式启动:

```json
{
  "mcpServers": {
    "Vscode_cube_mcp": {
      "command": "python",
      "args": ["-m", "cubemx_mcp"]
    }
  }
}
```

或直接用 console 入口:

```bash
vscode-cube-mcp
```

## 开发

```bash
python -m unittest test_cubemx_mcp -v   # 运行单元测试(不依赖 CubeMX)
```

测试覆盖:`_cleanup` 噪音过滤、`_check_path` 白名单校验、`_find_cubemx` /
`_allowed_roots` 配置解析、`_run_script`(mock 子进程)的 KO 检测与超时路径。

## 许可与依赖声明

- **本工具代码**:MIT License(见 `LICENSE`)
- **mcp SDK**(唯一 Python 依赖):MIT License(modelcontextprotocol/python-sdk)
- **STM32CubeMX**:ST 专有软件,运行时外部调用,需用户自备并遵守其许可条款

## 实践经验(2026-08-05,OLED_MCP 项目踩坑记录)

用 `cubemx_generate` 生成的新工程,在 VSCode 里用 **STM32 VS Code Extension** 打开时可能遇到:
Run and Debug 迟迟不出现 / ST 扩展识别工程很慢 / 报 `OLED_MCPsettings\ide.store.json` 之类 ENOENT。

**根因**:CubeMX CLI 生成的新工程缺少 ST 扩展识别工程所需的文件:

| 文件 | 作用 |
|------|------|
| `.settings/ide.store.json` | 声明 `sourceType=STM32CubeMX`、`device`、`core`(扩展识别硬件的关键) |
| `.settings/bundles.store.json`、`bundles-lock.store.json` | bundles(工具链)版本锁定 |
| `.vscode/settings.json` | cube-cmake / clangd 配置 |
| `.vscode/c_cpp_properties.json` | compile_commands.json 索引 |
| `.clangd` | clangd 配置 |

**正确做法(对齐实例工程,如 OLED_HAL)**:
1. 生成新工程后,**不要手写 launch.json**(实例工程没有,ST 扩展会自动提供调试配置);
2. 在 VSCode 里**重新加载窗口**(Reload Window),扩展会识别工程并自动补齐上述文件(device 名取自 `.ioc`,如 `STM32F103C8T6`);
3. 若扩展没自动补齐,可从同芯片的实例工程复制 `.settings/`、`.vscode/`、`.clangd`(注意核对 `ide.store.json` 里的 `device` 是否一致)。

**其他教训**:
- 若确实要手写 launch.json,ST 扩展的调试器类型是 `stlinkgdbtarget`,`deviceName` 必须与 `.settings/ide.store.json` 的 `device` 一致(如 `STM32F103C8T6`,不是 `STM32F103C8Tx`),否则扩展可能解析异常;
- 本机 STM32 VS Code Extension 全家桶调试类型:`stlinkgdbtarget`(ST-Link)/ `jlinkgdbtarget`(J-Link)/ `stgdbtarget`(通用 GDB);
- `cubemx_generate` 的 `project path` 对已存在目录返回 KO 是正常现象,generate 默认在 .ioc 同目录生成,结果不受影响。

## 从零配置时钟实战(2026-08-06,Blink_PB13:HSE 8MHz + PLL ×9 = 72MHz)

目标:生成一个新的 .ioc(芯片 STM32F103C8T6,外部晶振 8MHz,PLL ×9 到 72MHz,PB13 输出翻转)。
正确做法不是手写 RCC 键,而是**基于 6.18 原生 .ioc 改造 + set 命令**,让 CubeMX 自己写时钟树:

### 流程(全部通过 MCP 脚本/configure,无需 GUI)

1. **准备基底**:复制一个 6.18 原生生成的 .ioc(如 OLED_MCP.ioc)作为工作副本;
2. **启用 HSE(关键!)**:
   ```
   set pin PD0-OSC_IN RCC_OSC_IN
   set pin PD1-OSC_OUT RCC_OSC_OUT
   ```
   → CubeMX 自动补全整个时钟树派生频率(AHBFreq 72M / APB1 36M / TimSys 72M 等);
3. **配 PLL 与系统时钟源**:
   ```
   set ip parameters RCC PLLSourceVirtual RCC_PLLSOURCE_HSE
   set ip parameters RCC PLLMUL RCC_PLL_MUL9
   set ip parameters RCC SYSCLKSource RCC_SYSCLKSOURCE_PLLCLK
   set ip parameters RCC APB1CLKDivider RCC_HCLK_DIV2
   ```
   (`set ip parameters <IP> <参数> <值>` 是 6.18 设置 IP 参数的唯一入口)
4. **改引脚/外设**:`set pin PB13 GPIO_Output` + `set gpio parameters PB13 GPIO_Label LED`;
   移除多余外设:`set noparam I2C1`、`set pin PB8 GPIO_Input`(再手动删掉 Mcu.Pin 残留行);
5. **saveas 写回** → 加载验证(脚本 OK + csv pinout 核对)。

### 关键坑(血泪教训)

| 坑 | 说明 |
|----|------|
| **HSE 靠引脚启用,不是键** | 6.18 的 F1 里 HSE 用 `PD0-OSC_IN`/`PD1-OSC_OUT` 的 `HSE-External-Oscillator` 模式表达,`set pin ... RCC_OSC_IN/OUT` 即可;**不存在 `RCC.HSEState` 键**,手写会被静默删除 |
| **手写旧格式键会被删** | `HSEState`/`HSE_VALUE` 等手写键,6.18 不认识,saveas 时静默清理 → 时钟树悬空 → GUI 弹 "Resolve Clock Issues" |
| **`set rcc` 语法不存在** | set 命令只有 mode/pin/gpio/ip/... 类别,**没有 `set rcc`**;RCC 参数必须走 `set ip parameters RCC ...` |
| **`set gpio parameters` 是空格分隔** | `set gpio parameters PB13 GPIO_Label LED`,不是 `=` 连接 |
| **时钟树自动补全** | 一旦 HSE 引脚生效,PLLCLKFreq_Value/HCLK/APB 派生值全部由 CubeMX 自动算出,不要手填 |
| **弹窗根因** | 时钟源未启用或键无效 → 时钟树无法自洽 → GUI 弹 "Resolve Clock Issues"(点 Yes 会让求解器洗白文件) |
| **set pin 不写 Mode 行** | `set pin` 只写 Signal;`PD0-OSC_IN.Mode=HSE-External-Oscillator` 这类显示属性由 GUI/文本补,脚本加载不依赖它 |
## 从零生成:能力说明(2026-08-06 新增)

**`cubemx_new_project` 提供"从零生成"能力**:无需预先准备 .ioc,指定芯片型号 + 工程名 + set 命令即可生成完整 HAL 工程。

```
cubemx_new_project(
    project_name = "Blink",
    project_dir  = "C:/MINE/STM32Project/NewProj",
    mcu          = "STM32F103C8T6",          # 匹配 templates/{mcu}.ioc
    commands     = ["set pin PB13 GPIO_Output",
                    "set gpio parameters PB13 GPIO_Label LED"],
    template     = "",                        # 可选,直接指定模板 .ioc 路径
)
```

**流程**:`templates/` 选 6.18 原生模板(或显式 template)→ 复制到 project_dir 并改写 `ProjectName`/`ProjectFileName` → `config load` + 依次执行 set 命令 + `saveas` → `project generate`。

**内置模板库**(`templates/` 目录,按芯片型号命名):

| 模板文件 | 内容 |
|----------|------|
| `STM32F103C8T6.ioc` | F103C8,HSE 8MHz + PLL ×9 = 72MHz,SWD,PB13=LED(GPIO_Output) |
| `STM32F103C8T6_tim_template.ioc` | 上面全部 + TIM2(ETR 外部时钟,PA0)+ TIM3(内部时钟,1s)+ I2C1(PB8/PB9) |
| `STM32F103C8T6_tim2_internal.ioc` | 上面全部但 **TIM2 为内部时钟(1s)**,无 TIM3/ETR——需要 TIM2 内部时钟秒表时首选 |

> 新增芯片:把 6.18 原生生成的 .ioc 复制到 `templates/{芯片型号}.ioc` 即可;模板必须是 6.18 原生文件(否则 6.18 加载会报错)。

**配套工具**:

| 工具 | 用途 |
|------|------|
| `cubemx_remove_peripheral` | 从 .ioc 移除外设(文本方式,解决 `set noparam` 无效) |
| `cubemx_add_source` | 把自定义源文件加入 CMake 源列表(generate 覆盖后可重补) |

> **TIM 内部时钟**:首选 `STM32F103C8T6_tim2_internal.ioc` 模板;脚本模式无法把 ETR TIM 改成内部时钟,
> `cubemx_new_project` 内置借壳法(`_tim_make_internal_clock`)作兜底,详见 [`templates/README.md`](templates/README.md)。

> 📚 **各外设的配置命令、实测状态与坑**:见 [`templates/README.md`](templates/README.md)(GPIO/I2C/TIM/时钟的 set 命令与注意事项)。

## 能力边界:已验证范围与未验证外设

`cubemx_new_project` 已实现"从零生成",但**只验证了部分配置**:

**已验证 ✅**(本机实测通过):
- 时钟:HSE 8MHz + PLL ×9 = 72MHz(含 APB 分频、SWD 调试口)
- GPIO:输出引脚(PB13=LED,含 GPIO_Label)
- I2C1:PB8=SCL / PB9=SDA(经 `set pin` + `set mode I2C1 I2C`,生成 i2c.c + main.c 调用)
- TIM2/TIM3 + NVIC:经 `template=TIM2.ioc` 生成,`MX_TIM2_Init`/`MX_TIM3_Init` + `NVIC.TIMx_IRQn` 正确(tim.c 生成)

**未验证 ⚠️**(机制上应可用,但尚未实测):
- 外设:USART / SPI / ADC / DAC / DMA 等
- 中断:除 TIM 外的其它外设中断
- 高级时钟:PLL 其它倍频、MCO 输出等

**已知坑(实测发现)**:
- `set mode TIM2 <mode>` 对 TIM 无效(枚举名不可得,一律 KO),TIM 激活需靠
  "原生模板已带 TIM 配置"(`template` 参数直接指 TIM2.ioc 这类样板)或 GUI 生成的种子;
- `set ip parameters TIM2 ...` 在 TIM2 未激活时会被静默忽略(不报错也不生效)。

**实现机制(伪从零)**:CubeMX 的 `-q` 脚本模式没有 `new project` 命令,实际流程是
复制 `templates/{芯片型号}.ioc`(6.18 原生种子)→ 改工程名 → set 命令 → generate。
模板必须是 6.18 原生 .ioc,手写/非原生文件 6.18 会报错;新增芯片需先用 GUI
从空白建一次该芯片工程,把 .ioc 存入 templates/ 后即可脚本化复用。