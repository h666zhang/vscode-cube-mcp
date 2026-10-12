# Vscode_cube_mcp

> 全部由 deepseek-v4-flash 生成

[![PyPI](https://img.shields.io/pypi/v/vscode-cube-mcp)](https://pypi.org/project/vscode-cube-mcp/)  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)
- **GitHub**: https://github.com/h666zhang/vscode-cube-mcp
- **PyPI**: https://pypi.org/project/vscode-cube-mcp/

MCP(Model Context Protocol) server,封装 **STM32CubeMX** 官方命令行脚本模式(`-q`),
让 AI 助手可以直接加载 .ioc 工程、改引脚/外设配置、生成 HAL 代码、导出引脚表。

每次调用:命令序列写入临时脚本 → 启动 `STM32CubeMX -q` → 超时强杀
→ 过滤 log4j 噪音 → 检测 KO 失败标记 → 返回干净输出。

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

**内置模板库**(`templates/`,按芯片型号命名):当前只有 `STM32F103C8T6.ioc`(最小模板:
HSE 8MHz + PLL ×9 = 72MHz、SWD、SysTick,**无外设**)。新增芯片:把 6.18 原生生成的 .ioc
复制成 `templates/{芯片型号}.ioc` 即可(必须是 6.18 原生文件,否则加载会报错)。

**配套工具**:`cubemx_remove_peripheral`(文本方式移除外设,解决 `set noparam` 无效)。
删除范围不只是 `Mcu.IPx` 那一行 —— 它按下面的顺序连带清理(缺一项都会留下"孤儿配置",
下一次 generate 行为不可预期):

1. `Mcu.IPx=<外设>` 与 `{外设}.*` 参数行、虚拟引脚 `VP_{外设}*`;
2. 物理引脚 `{pin}.*` 与 `SH.{信号}*`:判据是引脚行的 `Signal` 值 —— 含实例名
   (`I2C1_SCL` / `S_TIM3_CH1` / `USART2_TX`)就算它的;ADC 的引脚信号是 `ADCx_IN0`
   (里面没有 "ADC1"),所以由 `rules/adc.yaml` 的 `remove.pin_signal_patterns` 给出模式;
3. `NVIC.{外设}_IRQn`;规则文件声明了共用向量时(如 ADC 的 `ADC1_2_IRQn`)一并删,
   但同族实例还在时不删(删 ADC1 而 ADC2 还开着 → 向量必须留着);
4. **DMA 连带**:`Dma.{请求}.*`(请求名 == 实例名或以 `{实例}_` 开头)、`Dma.Request<n>`、
   剩余请求**重新连续编号**(键前缀里的序号同步改)、`Dma.RequestsNb`、被删请求占用的
   `NVIC.<通道>_IRQn`;请求清空时连 `Mcu.IPx=DMA` 与 functionlistsort 的 `MX_DMA_Init` 段;
5. functionlistsort 里该外设的 `MX_*_Init` 段;`Mcu.IP*` / `Mcu.Pin*` 编号重排、`Nb` 修正。

> 回归保护:单元测试覆盖上述 1~5(`TestRemovePeripheral`),集成测试里有一个
> "配 I2C1 + 它的 DMA 请求 → 移除 → CubeMX 仍能 load"的真跑用例。

> 自定义源文件(自己的库)不需要工具:直接写进工程根 `CMakeLists.txt` 的用户区
> (`# Add sources to executable` / `# Add include paths`)——该文件 CubeMX 只生成一次,generate 不会覆盖。

> **TIM 内部时钟**:命令含 `set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL` 时
> server 自动注入标准表达(配置补丁),详见下文「TIM 标准写法与实测坑」。

> 📚 各外设的命令、实测结论与坑:`cubemx_help(topic="...")` —— 已有 gpio / i2c / tim / rcc /
> usart / spi / adc / can / rtc / iwdg / wwdg / dma / nvic;原始规则在 `knowledge/rules/*.yaml`。

## 能力边界:现在支持什么

**工具只做配置层**:`.ioc` 工程配置 + HAL 骨架生成(引脚 / 外设 / 时钟 / 工具链);
**不生成应用业务代码**(LED 闪烁、OLED 显示内容、传感器驱动等)。

**意图层(`cubemx_intent`)已实测收录的**:

| 外设 / 功能 | 模式 | 实测结论 |
|---|---|---|
| `TIMx` | pwm / input_capture / internal_clock | 三种都得**注入 .ioc 片段**(`set mode TIMx` 一律 KO) |
| `GPIO` / `I2Cx` / `USARTx` / `SPIx` / `ADCx` / `IWDG` / `WWDG` / `CAN` / `RTC` | 见各自 `knowledge/rules/*.yaml` | `set mode` 有效(参数细节见下) |
| **中断** | 意图里写 `interrupt` 字段 | 脚本配不了,靠**注入 `NVIC.*_IRQn`**(实测 generate 后 it.c 里有 handler) |
| **DMA** | 意图里写 `dma` 子对象 | 脚本配不了,靠**注入 `.ioc` 键组**(等价 GUI 的 DMA Settings→Add);通道/方向查 db 表,数据宽度由 CubeMX 补 |

同类外设因为规则按前缀匹配会自动支持(`USART1/3`、`SPI2`、`I2C2`、`TIM1/4` …)。

**还没实测的参数细节**:RTC 时钟源(LSE/LSI)与日历、ADC 采样时间/多通道扫描、SPI 波特率与 NSS、
CAN 位时序、USART 的同步/半双工等模式。

> **改 .ioc 的双保险**:所有写入函数写前备份 `.bak`、写后回读校验(`READBACK_MISMATCH`),
> 再按 `knowledge/rules/ioc_structure.yaml` 做**结构校验**(IP / Pin 编号必须从 0 连续、
> 计数键与实际条目数一致;只报"这次新引入的问题",抛 `STRUCTURE_MISMATCH`)——
> 那个规则文件不再只是文档,写入路径上真的执行它。

**实现机制(伪从零)**:CubeMX 的 `-q` 脚本模式没有 `new project` 命令,实际流程是
复制 `templates/{芯片型号}.ioc`(6.18 原生种子)→ 改工程名 → set 命令 / 片段注入 → generate。
模板必须是 6.18 原生 .ioc,手写 / 非原生文件 6.18 会报错;新增芯片需先用 GUI
从空白建一次该芯片工程,把 .ioc 存入 templates/ 后即可脚本化复用。

**已知坑(实测发现)**:
- `set mode TIM2 <mode>` 对 TIM 无效(一律 KO),TIM 激活靠片段注入;
- `set ip parameters TIM2 ...` 在 TIM2 未激活时会被静默忽略(不报错也不生效);
- CubeMX 的 `project path` 参数有路径拼接 bug(gen 时 sysmem/syscalls 报 FileNotFoundException),
  generate 尽量用默认行为(.ioc 同目录生成),别依赖 project path 重定向。

---

## TIM 标准写法与实测坑(2026-08-26 自 templates/README.md 迁移,原文件已删)

> templates/README.md 已删除(与 cubemx_help/主 README 大量重复);以下为其**独有知识**。

**TIM 内部时钟标准写法**(6.18 GUI 原生形态,`_inject_tim_internal_clock` 注入的正是此表达):

```
Mcu.IP4=TIM2
Mcu.Pin8=VP_TIM2_VS_ClockSourceINT
NVIC.TIM2_IRQn=true\:0\:0\:false\:false\:true\:true\:true\:true
TIM2.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE
TIM2.IPParameters=Period,AutoReloadPreload,Prescaler      ← 无 ClockFilter/ClockPolarity/CounterMode
TIM2.Period=9999
TIM2.Prescaler=7199
VP_TIM2_VS_ClockSourceINT.Mode=Internal
VP_TIM2_VS_ClockSourceINT.Signal=TIM2_VS_ClockSourceINT
```

- 1s 中断参数:72MHz 下 `Prescaler=7199` + `Period=9999`(即 7200 分频 / 10000 计数)。
  工具接口上传的是"分频比 / 计数"(7200 / 10000),注入时自动减 1 落成寄存器值 ——
  早先把 `10000-1` 这种未求值表达式写进 `.ioc`,CubeMX 会**原样抄进 C 代码**
  (`htim2.Init.Period = 10000-1;`,实测 2026-10-12),所以改成先算好。
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
TIM2.Period=9999
TIM2.Prescaler=7199
```

> (ETR 示例里的 Prescaler/Period 也按寄存器值写;`10000-1` 这种表达式会被 CubeMX 原样抄进 C 代码)

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
| 自定义源文件写错文件 | `cmake/stm32cubemx/CMakeLists.txt` 每次 generate 都被重写;写工程根 `CMakeLists.txt` 的用户区(`# Add sources to executable` / `# Add include paths`)才不会丢 |

## TIM PWM / 输入捕获的实测细节(2026-08-27,PWM_IC_OLED)

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

**MCP 开发模式配置坑(2026-08-27,2026-10-12 随 src 布局修订)**:config.toml 的 `[[plugins]]` 用 `command = python.exe` + `args = ["-m", "vscode_cube_mcp"]`;**cwd 字段不被 MCP 启动器支持(静默忽略)**,所以开发目录必须先 `pip install -e .`,否则 `-m` 找不到包。改配置需重启会话生效,详见本文末「MCP 开发模式配置」。

## 知识库是怎么来的(2026-10-10,重构后)

事实只有两个来源,都不依赖人的记忆:

| 目录 | 内容 | 怎么来的 |
|------|------|----------|
| `knowledge/ip/` | 每个 IP 的合法模式名、参数与取值范围、信号组名 | `tools/gen_knowledge_from_db.py` 解析 CubeMX 安装目录的 `db/mcu/IP/<Name>-<Version>_Modes.xml` |
| `knowledge/mcu/` | 芯片引脚表(每个引脚支持哪些信号) | 同一个脚本解析 `db/mcu/<Mcu.Name>.xml`(文件名来自模板 `.ioc` 里的 `Mcu.Name`) |
| `knowledge/rules/` | 命令写法、`.ioc` 片段、已知坑 | **探针实测**:`cubemx_probe` 在副本上跑命令,再用 `ioc_diff` 拿到真实差异 |

几条实测结论(以前靠人记、现在靠查表):

- `.ioc` 的内部枚举名就是 db 里 `RefMode` 的 `Name` 属性(如 `PWM Generation1 CH1`);
  GUI 里显示的是另一个名字(`PWM Generation CH1`),写错会被 CubeMX **静默丢弃**;
- 信号在 `.ioc` 里的名字来自 db 里 `RefSignal` 的组名:`ShareableGroupName` 形如
  `S_$IpInstance_CH1`(把 `$IpInstance` 换成实例名),`ExclusiveGroupName` 形如
  `S_TIM2_CH1_ETR:TIM2` —— PA0 的组合名 `S_TIM2_CH1_ETR` 就是这么来的,**不用猜**;
- `verified: false` 的规则会被拒绝执行(防幻觉闸门);`source` 记着这条规则的实测日期与 CubeMX 版本。

> **技巧:CubeMX 认哪些脚本命令,可以直接问它** —— `cubemx_script("help")` 会列出全部命令。
> 实测(6.18)给出的 `set` 子命令是:`mode` / `username` / `pin` / `pinreservation` / `gpio` /
> `ip` / `AllIpInContext` / `context*` / `noparam` / `userconst` / `bsp` / `tpl_path` / `dest_path` ——
> **里面没有 DMA,也没有中断相关项**,这两者都走"注入 `.ioc` 键"这条路(见 `rules/dma.yaml`、`rules/nvic.yaml`)。
> 另外 `help` 里的 `possible_value ip` 可以查某个 IP 的合法取值。

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

1. 用 CubeMX GUI 新建该芯片工程,存成 `templates/{mcu}.ioc`(**必须 6.18 原生生成**,手写会被拒);
2. 跑 `python tools/gen_knowledge_from_db.py --mcu <型号>` 导出引脚表与 IP 事实;
3. 在 `pyproject.toml` 的 `[tool.setuptools.data-files]` 里登记新模板(和 `knowledge/` 一样要随包分发);
4. 跑测试;`knowledge_loader` 会自动找到 `knowledge/mcu/<芯片>.pins.yaml`。

> 注意:知识库里每颗芯片一份引脚表,`--mcu` 换芯片就是重新导出;当前导的是 `STM32F103C8T6`
> (对应 `db/mcu/STM32F103C(8-B)Tx.xml`)。

## 开发与测试

```bash
pip install -e .                                # 首次:src 布局,把 src/ 装成可导入的包(或靠 pyproject 的 pythonpath)
python -m pytest                                # 单元测试 + 覆盖率门槛(低于 80% 失败)
ruff check . && ruff format --check .            # 风格
```

真跑 CubeMX 的集成测试(每个用例约 10s,会启动 CubeMX,用于验证"写出来的 .ioc CubeMX 真的认"):

```powershell
$env:ST_CUBEMX_RUN_INTEGRATION=1; python -m pytest tests/test_cubemx_mcp.py::TestCubeMXIntegration -v
```

技术栈:Python >= 3.10 + mcp SDK 2.x(stdio);打包 setuptools + build + twine;目标平台 Windows(跨平台可用)。

## 报错模型(错误码表)

所有失败都带"卡在哪一步 + 错误码 + 建议"。错误码定义在 `cubemx_result.ERRORS`;
参数 / 流程错误抛 `cubemx_result.ToolError`(继承 `ValueError`,消息形如
`[check_path|PATH_NOT_ALLOWED] <原消息>` + 换行 + `建议: …`)。

| 错误码 | 卡在哪一步 | 含义 | 建议 |
|--------|-----------|------|------|
| `BAD_ARGUMENT` | validate_args | 参数不合法 | 按提示改正 |
| `PATH_NOT_ALLOWED` | check_path | 路径不在白名单 | 加进 `ST_CUBEMX_ALLOWED_ROOTS` |
| `IOC_NOT_FOUND` / `NOT_IOC_FILE` | check_path | .ioc 不存在 / 不是 .ioc | 确认路径 |
| `MCU_TEMPLATE_MISSING` | locate_template | 没有该芯片模板 | 放模板或用 `template` 指定 |
| `UNKNOWN_PERIPHERAL` | load_knowledge | 知识库没收录这个外设 | 用 `cubemx_probe` 收录 |
| `UNKNOWN_PARAM` | load_knowledge | 没这种模式 / 参数 | `cubemx_help` 看支持的清单 |
| `UNKNOWN_PIN_SIGNAL` | resolve_intent | 引脚不支持该信号 | 换引脚(报错里列了支持的) |
| `PIN_CONFLICT` | resolve_intent | 引脚已被占用 | 换引脚,或先移除占用者 |
| `RULE_NOT_VERIFIED` | resolve_intent | 规则没实测过 | 先 probe 验证 |
| `PERIPHERAL_NOT_FOUND` | compose_ioc | .ioc 里没有这个外设 | 确认外设名 |
| `CUBEMX_TIMEOUT` / `CUBEMX_EXIT_NONZERO` / `CUBEMX_KO` | run_cubemx | CubeMX 超时 / 退出非 0 / 返回 KO | 看原始输出 |
| `READBACK_MISMATCH` | readback_verify | 写入后回读不一致 | 看列出的缺失行 |
| `CLOCK_DEGRADED` | post_check | HSE 被降级(**警告**,不是失败) | GUI 里把 PLL Source 选回 HSE |

## 本地开发:让宿主跑本地代码(补充上面那条旧记录)

上面那条「`args` 必须指向脚本文件本身」的结论在**旧布局**(模块平铺在仓库根、无 `__main__.py`)下成立,
src 布局后已作废:包内改用相对导入,**直连脚本文件会报**
`ImportError: attempted relative import with no known parent package`,只能走 `-m`:

```toml
[[plugins]]
name    = "Vscode_cube_mcp"
command = "C:\\...\\Python313\\python.exe"
args    = ["-m", "vscode_cube_mcp"]   # 开发目录需先 `pip install -e .`
env     = { ST_CUBEMX_ALLOWED_ROOTS = "C:\\MINE\\STM32Project" }
```

`pip install -e .` 会把 `src/` 挂进 `sys.path`;包外的 `knowledge/` 与 `templates/` 由
`knowledge_loader` / `_template_search_dirs` 向上回溯到仓库根找到。改完**重启宿主**才生效。

> 另注意:`%APPDATA%\reasonix` 是宿主的运行时状态路径,agent 无权写入(会被直接拒绝),
> 这类改动得自己动手。
