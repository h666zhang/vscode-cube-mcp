# 外设实测与能力边界

各外设现在支持到什么程度、TIM 的标准写法与实测细节。

---

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
