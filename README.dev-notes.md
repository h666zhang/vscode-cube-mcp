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
| `cubemx_script` | 任意 CubeMX 脚本命令序列(原始脚本接口) |
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
| `STM32F103C8T6.ioc` | **最小模板**:F103C8,HSE 8MHz + PLL ×9 = 72MHz,SWD,SysTick,**无外设**(外设由 commands 现配) |

> 新增芯片:把 6.18 原生生成的 .ioc 复制到 `templates/{芯片型号}.ioc` 即可;模板必须是 6.18 原生文件(否则 6.18 加载会报错)。

**配套工具**:

| 工具 | 用途 |
|------|------|
| `cubemx_remove_peripheral` | 从 .ioc 移除外设(文本方式,解决 `set noparam` 无效) |
| `cubemx_add_source` | 把自定义源文件加入 CMake 源列表(generate 覆盖后可重补) |

> **TIM 内部时钟**:命令含 `set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL` 时,
> server 自动注入 6.18 验证过的内部时钟标准表达(配置补丁,0.4.0 起),详见下文「TIM 标准写法与实测坑」。

> 📚 **各外设的配置命令、实测状态与坑**:调 `cubemx_help(topic="gpio"/"i2c"/"tim"/"rcc")` 获取;
> 详细实测结论见下文「TIM 标准写法与实测坑」与「能力边界」。

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

---

## 0.4.0 规划:最小模板 + 外设配置补丁(2026-08-26 实测)

**背景**:模板"整机配置"模式会按 芯片×配置组合 爆炸(每新组合手动 GUI 配一遍再存模板),
不符合"配置应现配、模板应少而薄"的方向。

**实测结论(6.18-RC3,真实 CubeMX 验证)**:
- 构造 420 字节 / 15 行"激进最小种子":仅 `File.Version` + `Mcu.CPN/Family/Name/Package/UserName`
  + `MxCube.Version` + `MxDb.Version` + 基本 ProjectManager,**IPNb=0、PinsNb=0,无任何外设/时钟/引脚**
- `config load` → **OK**;`project generate` → **OK**,生成完整空白 HAL 工程
  (main.c / stm32f1xx_it / hal_msp / hal_conf、HAL 驱动 + CMSIS、CMake 工具链)
- 结论:**6.18 接受极简 .ioc,最小模板路线完全可行**;load 不会改写种子文件(saveas 前原样)

**改造方向(0.4.0)**:
1. **种子化**:模板从"整机配置"减为"芯片标识 + 基础时钟"文件(每芯片 1 个),数量按芯片线性增长
2. **外设 set 现配**:GPIO / I2C / USART 等可靠外设全部由 `commands` set 配置(已验证)
3. **TIM 内部时钟配置补丁**:标准表达直接写入(VP 内联 Mcu.Pin 列表、IPNb/PinsNb 同步、
   functionlistsort 段),已实现并实测通过(见下)
4. **`cubemx_new_project` 流程**:最小模板 → set 现配 → 配置补丁(仅 set 不可靠的外设)→ generate

**借壳法已退役(2026-08-26)**:`_tim_make_internal_clock` 删除,替换为 `_inject_tim_internal_clock`;
基础模板 + TIM2 配置补丁经真实 CubeMX 验证(load/generate OK,
`sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL`)。旧组合模板 `tim_template.ioc`/`tim2_internal.ioc` 已从 templates/ 移除。

**遗留坑(与种子无关)**:
- CubeMX `project path` 参数有路径拼接 bug(gen 时 sysmem/syscalls 报 FileNotFoundException),
  generate 尽量用默认行为(.ioc 同目录生成),别依赖 project path 重定向。

## 迭代日志(2026-08-26,针对 0.4.0 代码)

对 cubemx_mcp.py(0.4.0:最小模板 + TIM 配置补丁)做了 3 轮自查修复:

**逻辑与能力**:
- `_inject_tim_internal_clock` 的 functionlistsort 段删除改为**按段重组**(原来用两个 re.sub,
  当 functionlistsort 只剩 TIM 单段时删除失败 → 注入后出现重复 MX_TIMx_Init 段)
- 增加 IPNb/PinsNb 缺失防御:非标准 .ioc 没有这两行时,原逻辑会丢弃全部 IP/Pin 行(数据丢失),
  现在在末尾补全
- `cubemx_new_project` 支持命令附 `Prescaler <n> Period <n>` 覆盖默认 1s 参数
  (例:`set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL Prescaler 720 Period 1000`,
  默认值而非强制)

**边界/健壮性**:
- 修复 4 处**行尾 `\r?\n` 匹配遗漏**:`.ioc` 最后一行无换行符时,配置生成逻辑的 IP/Pin 收集、
  `cubemx_remove_peripheral` 的外设定位(`fullmatch` 带 `\r?\n`)与 keep_ips 收集都会漏匹配
  → 改为不依赖行尾换行(`\s*` / 裸 `(.*)`)
- `_patch_ioc_identity` 补 ProjectName/ProjectFileName 缺失防御(模板缺行时追加,
  否则 CubeMX 用模板默认工程名)

**冗余清理 + 回归测试**:
- help 冗余:`_PERIPHERAL_HELP` 的 `clock` key 与 `rcc` 内容重复 → 删除 `clock` key,
  `cubemx_help(topic="clock")` 改为返回 rcc 内容(别名,单一维护点)
- 冗余检查结论:`_GUIDE` 与 `_NEW_PROJECT_HELP` 的流程/参数表存在**有意的主题重复**
  (完整指南 vs 定向查询),保留;README.dev-notes.md 无重复章节;模板删除后引用已全部清理
- 新增 3 个回归测试(单段 functionlistsort 不重复、缺 IPNb/PinsNb 不丢数据、
  最后一行无换行的外设可删除),测试 37 → **40 全绿**

## TIM 标准写法与实测坑(2026-08-26 自 templates/README.md 迁移,原文件已删)

> templates/README.md 已删除(与 cubemx_help/主 README 大量重复);以下为其**独有知识**。

**TIM 内部时钟标准写法**(6.18 GUI 原生形态,`_inject_tim_internal_clock` 注入的正是此表达):

```
Mcu.IP4=TIM2
Mcu.Pin8=VP_TIM2_VS_ClockSourceINT
NVIC.TIM2_IRQn=true\:0\:0\:false\:false\:true\:true\:true\:true
TIM2.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE
TIM2.IPParameters=Period,AutoReloadPreload,Prescaler      ← 无 ClockFilter/ClockPolarity/CounterMode
TIM2.Period=10000-1
TIM2.Prescaler=7200-1
VP_TIM2_VS_ClockSourceINT.Mode=Internal
VP_TIM2_VS_ClockSourceINT.Signal=TIM2_VS_ClockSourceINT
```

- 1s 中断参数:72MHz 下 `Prescaler=7200-1` + `Period=10000-1`(`72-1`/`1000-1` = 1ms,常见错误!)
- TIM3 默认参数版:只写 `Mcu.IP5=TIM3` + `Mcu.Pin9=VP_TIM3_VS_ClockSourceINT` + `NVIC.TIM3_IRQn` +
  `VP_TIM3_VS_ClockSourceINT.Mode=Internal/Signal`,**无 Prescaler/Period/IPParameters 行** = 默认 0/65535

**TIM2 外部引脚(ETR)写法**(GUI 原生形态,仅供参考;外部时钟**无 VP_TIM2**):

```
Mcu.IP4=TIM2
Mcu.Pin2=PA0-WKUP                          ← 物理引脚
PA0-WKUP.Signal=S_TIM2_CH1_ETR             ← 引脚绑定信号
SH.S_TIM2_CH1_ETR.0=TIM2_ETR,ClockSourceETR_Mode2   ← 信号句柄:ETR 模式
SH.S_TIM2_CH1_ETR.ConfNb=1
NVIC.TIM2_IRQn=true\:0\:0\:false\:false\:true\:true\:true\:true
TIM2.IPParameters=Period,AutoReloadPreload,Prescaler
TIM2.Period=10000-1
TIM2.Prescaler=7200-1
```

> 生成代码含 `ClockSource=TIM_CLOCKSOURCE_ETRMODE2` + PA0 配置为 TIM2_ETR 输入;
> ClockPolarity/ClockPrescaler/ClockFilter 用默认值(NONINVERTED/DIV1/0),.ioc 不写这些行。
> PWM 方式待验证。

**启动 TIM 中断前清标志位(实测,2026-08-06)**:用 `HAL_TIM_Base_Start_IT()` 前**必须手动清除
UPDATE 标志位**,否则启动前残留的标志会立刻触发一次中断(表现为显示/计数提前出现一次):

```c
__HAL_TIM_CLEAR_FLAG(&htim2, TIM_FLAG_UPDATE);  /* 先清标志 */
HAL_TIM_Base_Start_IT(&htim2);                  /* 再开中断 */
```

参考实例:`C:\MINE\STM32Project\STM32VScode\TIM2\Core\Src\main.c`。

**坑速查**(其余命令见 cubemx_help 各 topic):
| 坑 | 说明 |
|----|------|
| `set rcc` 不存在 | RCC 参数用 `set ip parameters RCC ...`;HSE 靠 PD0-OSC_IN/PD1-OSC_OUT 引脚,`RCC.HSEState` 键会被静默删除 |
| `set mode TIMx` 无效 | 一律 KO,TIM 靠配置补丁/原生模板激活 |
| `set ip parameters TIMx` 未激活时静默忽略 | 不报错也不生效,先激活再设参 |
| `set noparam TIMx` 删除无效 | 返回 OK 但外设仍在,需用 cubemx_remove_peripheral 文本移除 |
| 重新 generate 覆盖 CMakeLists | `cmake/stm32cubemx/CMakeLists.txt` 被重写,手动加的源文件需重新 cubemx_add_source |

## 0.4.2:工程化补强(2026-08-27)

非功能迭代,为「全外设完善」(0.5.0)铺路:

- **CI**:`.github/workflows/ci.yml` —— windows-latest,Python 3.10-3.13 矩阵;步骤 = `ruff check .` + `ruff format --check .` + `python -m pytest`(pyproject 的 `[tool.pytest.ini_options]` 带 `--cov=cubemx_mcp --cov-fail-under=80`)。测试不依赖 CubeMX 实体,CI 可全自动跑。
- **测试**:47 → 72 个,覆盖率 71% → 94.9%。新增:模板补丁全分支、`cubemx_new_project` 调度层(mock `_run_script`)、工具包装层、help topics、输入捕获非法信号等。
- **代码质量**:`[tool.ruff]`(line-length 120,select E4/E7/E9/F/I/UP)+ `.pre-commit-config.yaml`;TIM 枚举名抽为 `_TIM_*` 常量对照表;顺带修复 py3.10/3.11 f-string 反斜杠语法错误(F841 未用变量一并清掉)。
- **术语统一**:薄种子→最小模板、片段注入→配置补丁、注入器→配置生成逻辑、权威枚举名→.ioc 内部枚举名对照表、黄金样本→基准样本、逃生通道→原始脚本接口、自述→内置帮助(借壳法保留,仅历史记录);README/examples/dev-notes/cubemx_help 同步。
- **文档**:README 增加项目定位自白(自嗨型项目声明)与「用之前 vs 用之后」示例;中文标点全角化。
- **能力边界**(定位声明,2026-08-27 定):工具**只做配置层**(.ioc 配置 + HAL 骨架),**不做应用层**(LED 闪烁、OLED 显示、传感器驱动、中断逻辑等业务代码由用户编写);`cubemx_help` 指南开头与 README 均已声明此边界。以后加功能遵循同一原则:配置到骨架为止,不替用户写业务逻辑。

## 0.4.1:PWM + 输入捕获配置生成(2026-08-27,PWM_IC_OLED 实测)

> 版本规划:0.5.0 预留给"完善所有外设"里程碑;在全部外设完善前,功能迭代一律走 0.4.x。

**背景**:0.4.0 只注入 TIM 内部时钟;TIM3 PWM / TIM2 输入捕获此前需手写 .ioc,且 GUI 名 ≠ 6.18 .ioc 枚举名(写错会被 CubeMX load 时静默丢弃)。本次把实测验证过的表达固化成配置生成逻辑。

**新增命令**(cubemx_new_project 的 commands 里):
```
# PWM:set ip parameters <TIM> PWM <pin> <signal> [Prescaler n] [Period n] [Pulse n]
set ip parameters TIM3 PWM PA6 S_TIM3_CH1 Prescaler 72 Period 100 Pulse 50
# 输入捕获:set ip parameters <TIM> InputCapture <pin> <signal> [Prescaler n] [Period n]
set ip parameters TIM2 InputCapture PA0-WKUP S_TIM2_CH1_ETR Prescaler 72 Period 65535
```

**新增函数**:`_inject_tim_pwm` / `_inject_tim_input_capture`,公共骨架 `_rebuild_ioc_lines` / `_finish_ioc_write`(IP/Pin 重建、IPNb/PinsNb 同步、functionlistsort 段、幂等)。

**.ioc 内部枚举名(6.18,F103)**:来源 `C:\MINE\STM\STM\db\mcu\IP\TIM1_8F1-gptimer2_v1_x_Cube_Modes.xml`:
| 功能 | .ioc 内部名(Name) | GUI 名(UserName) |
|------|------|------|
| PWM CH1 | `PWM Generation1 CH1`(带序号) | `PWM Generation CH1` |
| 输入捕获 IC1 | `Input_Capture1_from_TI1` | `Input Capture direct mode` |
| 输入捕获 IC2(间接) | 无独立模式,用参数行 | `Input Capture indirect mode` |

**基准样本(实测)**:`C:\MINE\STM32Project\STM32VScode\PWM_IC_OLED\PWM_IC_OLED.ioc`(手写注入 + 6.18 generate 后保留的规范化形态),关键行:
```
SH.S_TIM2_CH1_ETR.0=TIM2_CH1,Input_Capture1_from_TI1
SH.S_TIM2_CH1_ETR.ConfNb=1
TIM2.Channel-Input_Capture1_from_TI1=TIM_CHANNEL_1     ← 只有 IC1 有 Channel 键
TIM2.IC2Polarity=TIM_ICPOLARITY_FALLING               ← IC2 是参数行,无 Channel 键
TIM2.IC2Selection=TIM_ICSELECTION_INDIRECTTI
TIM3.Channel-PWM\ Generation1\ CH1=TIM_CHANNEL_1      ← PWM 模式名中的空格转义为 \ 
TIM3.IPParameters=Prescaler,Period,OCMode,Pulse,Channel-PWM Generation1 CH1
```

**实测结论**:
- PA0 的 .ioc 信号名是**组合名** `S_TIM2_CH1_ETR`(不是 `S_TIM2_CH1`);
- SH 键 = `SH.` + 信号全名(`SH.S_TIM3_CH1`),SH 值第一段 = 信号本体去 `S_` 前缀(ETR 还要去 `_ETR` 后缀,如 `TIM2_CH1`);
- Channel 键值用 HAL 枚举 `TIM_CHANNEL_1`(不带 TIM 编号前缀);
- **CubeMX 只生成 IC1 的 sConfigIC**(`HAL_TIM_IC_ConfigChannel` 仅 1 次);测占空比的 IC2 需 main.c 手动补 `HAL_TIM_IC_ConfigChannel(&htim2, ic2, TIM_CHANNEL_2)`(示例见 cubemx_help tim topic);
- 注入后的 .ioc 经 6.18 load+generate 后与基准样本逐行一致,生成 tim.c 参数正确(PWM 10kHz/50% + IC RISING)。

**MCP 开发模式配置坑(2026-08-27)**:config.toml 的 `[[plugins]]` 用 `command = python.exe` + `args = ["-m", "cubemx_mcp"]` 时,server 从 site-packages 导入,**cwd 字段不被 MCP 启动器支持(静默忽略)**;可靠写法是 `args = ['C:\MINE\STM32Project\Vscode_cube_mcp\cubemx_mcp.py']` 直接执行开发目录脚本。改配置需重启会话生效。
