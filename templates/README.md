# templates 模板库说明

本目录存放 `cubemx_new_project` 的"从零生成"基底模板:
按芯片型号命名的 **6.18 原生 .ioc** 文件,生成新工程时被复制改造。

## 模板列表

| 文件 | 芯片 | 内容 |
|------|------|------|
| `STM32F103C8T6.ioc` | STM32F103C8T6 | 72MHz 时钟(HSE 8M + PLL ×9)、SWD(PA13/PA14)、PB13=LED(GPIO_Output) |
| `STM32F103C8T6_tim_template.ioc` | STM32F103C8T6 | 上面全部 + **TIM2(ETR 外部时钟,PA0)** + **TIM3(内部时钟,1s)** + I2C1(PB8/PB9) |
| `STM32F103C8T6_tim2_internal.ioc` | STM32F103C8T6 | 上面全部但 **TIM2 为内部时钟(1s)**、无 TIM3、无 ETR(借壳法生成,见下) |

> 模板必须来自 6.18 原生生成(手写/非原生 .ioc 会被 6.18 报错)。
> 新增芯片:用 CubeMX GUI 从空白新建一次该芯片工程,把生成的 .ioc 复制为 `templates/{芯片型号}.ioc`。

## 外设配置方法(实测,2026-08-06)

`cubemx_new_project` 的 `commands` 参数接受 set 命令。各外设实测如下:

### 时钟(HSE + PLL,模板已含,一般无需改)

模板已配好 72MHz;如需改频率,在生成后对 .ioc 用 `set ip parameters RCC ...`:

```
set pin PD0-OSC_IN RCC_OSC_IN
set pin PD1-OSC_OUT RCC_OSC_OUT
set ip parameters RCC PLLSourceVirtual RCC_PLLSOURCE_HSE
set ip parameters RCC PLLMUL RCC_PLL_MUL9
set ip parameters RCC SYSCLKSource RCC_SYSCLKSOURCE_PLLCLK
set ip parameters RCC APB1CLKDivider RCC_HCLK_DIV2
```

关键:启用 HSE 靠 PD0/PD1 引脚,`set rcc` 语法不存在,RCC 参数走 `set ip parameters RCC ...`。

### GPIO(已验证 ✅)

```
set pin PB13 GPIO_Output
set gpio parameters PB13 GPIO_Label LED
```

### I2C(已验证 ✅)

```
set pin PB8 I2C1_SCL
set pin PB9 I2C1_SDA
set mode I2C1 I2C
```

生成 `i2c.c` + main.c 调用 `MX_I2C1_Init`。

### TIM(已验证 ✅,但有重要坑)

**关键教训(2026-08-06 实测)**:`set mode TIM2 <mode>` 一律 KO;`set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL`
**只改参数、不改 SH/VP 表达**,CubeMX GUI 仍显示 ETR;**手写 VP_TIMx_VS_ClockSourceINT 表达会被 generate 静默清理 TIM**。
**唯一可靠方式:用 CubeMX GUI 生成标准 .ioc 作模板**,再 `cubemx_new_project` 复用。

**TIM2 内部时钟标准写法**(GUI 生成,参考 `templates/STM32F103C8T6_tim_template.ioc` 中 TIM3 的写法原理):

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

1s 中断参数:72MHz 下 `Prescaler=7200-1` + `Period=10000-1`(`72-1`/`1000-1` = 1ms,常见错误!)。

**TIM3 内部时钟标准写法**(GUI 生成,默认参数版,即 `templates/STM32F103C8T6_tim_template.ioc` 中 TIM3 的原生写法):

```
Mcu.IP5=TIM3
Mcu.Pin9=VP_TIM3_VS_ClockSourceINT
NVIC.TIM3_IRQn=true\:0\:0\:false\:false\:true\:true\:true\:true
VP_TIM3_VS_ClockSourceINT.Mode=Internal
VP_TIM3_VS_ClockSourceINT.Signal=TIM3_VS_ClockSourceINT
```

> TIM3 无 Prescaler/Period/IPParameters 行 = 使用默认参数(Prescaler=0、Period=65535)。
> 与 TIM2 的差异:自定义参数(7200-1/10000-1)会写 `TIM2.IPParameters=Period,AutoReloadPreload,Prescaler` + 对应值行;
> 纯默认参数的 TIM 不写参数行,只写 Mcu.IP + VP + NVIC。

**TIM2 内部时钟模板的"借壳法"(2026-08-06 实测成功)**:

脚本模式无法可靠把 TIM2 从 ETR 改成内部时钟(手写 `VP_TIM2_VS_ClockSourceINT` 会被
generate 清理)。**可靠做法:借用模板里原生内部时钟的 TIM3,整体改名成 TIM2**:

1. 用 `STM32F103C8T6_tim_template.ioc` 作模板(含 TIM2 ETR + TIM3 内部时钟);
2. 文本操作:删 ETR TIM2(PA0/S_TIM2/SH/TIM2.* 参数)、删 TIM3,把 `TIM3.*`/`VP_TIM3`/`NVIC.TIM3_IRQn`
   全部替换成 `TIM2.*`/`VP_TIM2`/`NVIC.TIM2_IRQn`,Mcu.IPNb 改 5,Mcu.Pin 重排;
3. 结果 = TIM2 继承了 TIM3 的原生内部时钟表达(6.18 信任),generate 后 `MX_TIM2_Init`
   生成 `ClockSource=TIM_CLOCKSOURCE_INTERNAL`。

**产物已固化**:`templates/STM32F103C8T6_tim2_internal.ioc`(TIM2 内部时钟 1s,可直接作模板)。

**使用建议(2026-08-06)**:
- **首选**:需要 TIM2 内部时钟时,直接用 `template=".../templates/STM32F103C8T6_tim2_internal.ioc"` 生成——干净、可靠、零 hack;
- **兜底**:`cubemx_new_project` 内置 `_tim_make_internal_clock`(借壳法),当命令含
  `set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL` 且模板该 TIM 不是内部时钟时自动借用
  模板内其它内部时钟 TIM 改名;适用于非 F103 芯片或模板不匹配的场景。

**两种 TIM 内部时钟对比**:

| 项 | TIM2(自定义参数) | TIM3(默认参数) |
|---|---|---|
| Mcu.IP | `Mcu.IP4=TIM2` | `Mcu.IP5=TIM3` |
| VP 引脚 | `Mcu.Pin8=VP_TIM2_VS_ClockSourceINT` | `Mcu.Pin9=VP_TIM3_VS_ClockSourceINT` |
| VP 信号 | `VP_TIM2_VS_ClockSourceINT.Signal=TIM2_VS_ClockSourceINT` | `VP_TIM3_VS_ClockSourceINT.Signal=TIM3_VS_ClockSourceINT` |
| 时钟源 | `Mode=Internal` | `Mode=Internal` |
| 参数行 | `IPParameters=Period,AutoReloadPreload,Prescaler` + `Period/Prescaler` 值 | 无(默认 0/65535) |
| NVIC | `NVIC.TIM2_IRQn=...` | `NVIC.TIM3_IRQn=...` |

**TIM2 外部引脚(ETR)写法**(GUI 生成,即 `templates/STM32F103C8T6_tim_template.ioc` 中 TIM2 的原生写法):

```
Mcu.IP4=TIM2
Mcu.Pin2=PA0-WKUP                          ← 物理引脚
PA0-WKUP.Signal=S_TIM2_CH1_ETR             ← 引脚绑定信号
SH.S_TIM2_CH1_ETR.0=TIM2_ETR,ClockSourceETR_Mode2   ← 信号句柄:ETR 模式
SH.S_TIM2_CH1_ETR.ConfNb=1
NVIC.TIM2_IRQn=true\:0\:0\:false\:false\:true\:true\:true\:true
TIM2.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE
TIM2.IPParameters=Period,AutoReloadPreload,Prescaler
TIM2.Period=10000-1
TIM2.Prescaler=7200-1
```

> ETR 方式**无 VP_TIM2**(外部时钟不需要虚拟信号);生成代码含
> `ClockSource=TIM_CLOCKSOURCE_ETRMODE2` + PA0 配置为 TIM2_ETR 输入。
> ClockPolarity/ClockPrescaler/ClockFilter 用默认值(NONINVERTED/DIV1/0),.ioc 不写这些行。

**三种 TIM 方式对比(GUI 标准写法)**:

| 项 | TIM2 内部时钟 | TIM3 内部时钟 | TIM2 ETR(外部引脚) |
|---|---|---|---|
| 物理引脚 | 无 | 无 | `Mcu.Pin2=PA0-WKUP` + `Signal=S_TIM2_CH1_ETR` |
| 虚拟信号 | `Mcu.Pin8=VP_TIM2_VS_ClockSourceINT` + `Mode=Internal` | `Mcu.Pin9=VP_TIM3_VS_ClockSourceINT` + `Mode=Internal` | 无 VP,用 `SH.S_TIM2_CH1_ETR.0=TIM2_ETR,ClockSourceETR_Mode2` |
| 时钟源 | Internal | Internal | ETRMODE2 |
| 参数行 | `Period,AutoReloadPreload,Prescaler` + 值 | 无(默认 0/65535) | `Period,AutoReloadPreload,Prescaler` + 值 |
| NVIC | `NVIC.TIM2_IRQn` | `NVIC.TIM3_IRQn` | `NVIC.TIM2_IRQn` |

> **PWM 方式**:待验证(用户将提供对比)。

## 未验证外设 ⚠️

USART / SPI / ADC / DAC / DMA 等尚未实测;中断除 TIM 外未验证。
预计机制同上(能 set 的直接 set,不能 set 的靠模板),但需实测确认。

## 坑速查

| 坑 | 说明 |
|----|------|
| `set rcc` 不存在 | RCC 参数用 `set ip parameters RCC ...` |
| HSE 靠引脚 | PD0-OSC_IN / PD1-OSC_OUT,`RCC.HSEState` 键会被静默删除 |
| `set gpio parameters` 空格分隔 | `set gpio parameters PB13 GPIO_Label LED`(不是 `=` ) |
| `set mode TIMx` 无效 | 一律 KO,TIM 靠原生模板激活 |
| `set ip parameters TIMx` 未激活时静默忽略 | 不报错也不生效,先激活再设参 |
| `set noparam TIMx` 删除无效 | 返回 OK 但外设仍在(已在无进程状态下对照验证,确认非进程干扰),需手动编辑 .ioc 移除 |
| 重新 generate 覆盖 CMakeLists | `cmake/stm32cubemx/CMakeLists.txt` 被重写,手动加的源文件(如 OLED.c)需重新添加 |

## 启动 TIM 中断前清标志位(实测,2026-08-06)

用 `HAL_TIM_Base_Start_IT()` 启动定时器中断前,**必须手动清除 UPDATE 标志位**,否则
启动前残留的标志会立刻触发一次中断(表现为 OLED 显示/计数提前出现一次)。

```c
__HAL_TIM_CLEAR_FLAG(&htim2, TIM_FLAG_UPDATE);  /* 先清标志 */
HAL_TIM_Base_Start_IT(&htim2);                  /* 再开中断 */
```

参考实例:`C:\MINE\STM32Project\STM32VScode\TIM2\Core\Src\main.c`。

## ST 扩展识别文件(自动生成,无需手动补)

CubeMX CLI generate 生成的工程**不包含** `.vscode/`、`.settings/`、`.clangd`、`.gitignore`,
但 **ST VS Code 扩展打开工程时会自动补齐**这些识别文件(实测 2026-08-06,PB13_Low 工程验证):

- `.settings/ide.store.json`:sourceType=STM32CubeMX、device(STM32F103C8T6)、core(Cortex-M3)
- `.settings/bundles.store.json`、`bundles-lock.store.json`:工具链版本锁定
- `.vscode/settings.json`、`c_cpp_properties.json`:cube-cmake / clangd 配置
- `.clangd`、`.gitignore`

> **无需手动复制**,用 VSCode 打开工程让扩展自动生成即可(会按 .ioc 自动识别 device/core)。
> 之前"手动复制实例工程识别文件"的做法已被验证为不必要。

## 增强工具(2026-08-06 新增)

### cubemx_remove_peripheral:移除外设

CubeMX 的 `set noparam <IP>` 对部分外设(尤其 TIM)无效,用本工具直接编辑 .ioc:

```
cubemx_remove_peripheral(ioc="C:/proj/proj.ioc", peripheral="TIM3")
```

- 自动删除 Mcu.IPx 条目、关联引脚/参数、NVIC 中断、functionlistsort 段,重排外设编号
- 操作前自行备份;移除后建议重新 generate 同步代码

### cubemx_add_source:添加自定义源文件

CubeMX 重新 generate 会覆盖 `cmake/stm32cubemx/CMakeLists.txt`,手动加的自定义源文件丢失。
用本工具幂等重加:

```
cubemx_add_source(ioc="C:/proj/proj.ioc", source_file="Core/Src/OLED.c")
```

- generate 之后调用,把源文件加回 MX_Application_Src;重复调用不重复添加

### cubemx_new_project 的 project_dir 语义(已理顺)

generate 后若工程被生成到 project_dir 的**同级**(以工程名命名,如 project_dir=newproj_test 会生成
NewProjTest/),工具会自动把 .ioc 移到工程目录,保证 .ioc 与 HAL 工程同目录。

## TIM 激活方法(正确顺序)

1. **选模板**:`templates/` 已内置带 TIM 的模板 `STM32F103C8T6_tim_template.ioc`(TIM2 ETR + TIM3 内部时钟),
   用 `template="<mcp>/templates/STM32F103C8T6_tim_template.ioc"` 生成,不要引用外部工程路径(不可移植);
2. **再设参**:激活后 `set ip parameters TIM2 ...` 才生效(未激活时静默忽略);
3. **时钟源切换的真相**:脚本模式**无法可靠**把 TIM 从 ETR 改为内部时钟(`set ip parameters TIM2 ClockSource ...`
   只改参数不改 SH/VP 表达,手写 VP 会被 generate 清理)。需要内部时钟 TIM 时,用 GUI 生成标准 .ioc 作模板,
   标准写法见上文「TIM2 内部时钟标准写法」;
4. **清理寄生外设**:模板自带的 TIM3/多余引脚,用 `cubemx_remove_peripheral` 移除。
