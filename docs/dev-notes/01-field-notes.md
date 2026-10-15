# 实战记录

从零配时钟、从零生成工程等实际做过的记录。

---

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
