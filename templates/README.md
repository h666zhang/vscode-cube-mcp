# templates 模板库说明

本目录存放 `cubemx_new_project` 的"从零生成"基底模板:
按芯片型号命名的 **6.18 原生 .ioc** 文件,生成新工程时被复制改造。

## 模板列表

| 文件 | 芯片 | 内容 |
|------|------|------|
| `STM32F103C8T6.ioc` | STM32F103C8T6 | 72MHz 时钟(HSE 8M + PLL ×9)、SWD(PA13/PA14)、PB13=LED(GPIO_Output) |

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

### TIM(已验证 ✅,但有坑)

**`set mode TIM2 <mode>` 一律 KO,`set ip parameters TIM2 ...` 在 TIM 未激活时静默忽略**。
TIM 必须靠"原生模板已带 TIM 配置"激活:用 `template` 参数指向带 TIM 的 6.18 样板 .ioc(如
`C:\MINE\STM32Project\STM32VScode\TIM2\TIM2.ioc`),生成后再 `set ip parameters TIM2 ...` 调整参数:

```
# 用带 TIM2 的样板作模板生成(激活 TIM2)
template = "C:/MINE/STM32Project/STM32VScode/TIM2/TIM2.ioc"

# 生成后调整 TIM2 为 1s 内部时钟中断
set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL
set ip parameters TIM2 Prescaler 7200-1
set ip parameters TIM2 Period 10000-1
```

> 72MHz 下 `Prescaler=7200-1` + `Period=10000-1` = 1s 中断;
> `72-1` + `1000-1` = 1ms(常见错误!)。Period 16 位最大 65535,必须用 7200/10000 组合。
> 注意:模板可能带来寄生外设(如 TIM2 样板自带 TIM3 和 PA0=TIM2_ETR),生成后需手动清理。

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

1. **先激活**:用 `template` 参数指向带 TIM 的 6.18 原生样板 .ioc(如 `STM32VScode\TIM2\TIM2.ioc`);
2. **再设参**:激活后 `set ip parameters TIM2 ...` 才生效(未激活时静默忽略);
3. **改时钟源**:如需要内部时钟,`set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL`;
4. **清理寄生外设**:样板自带的 TIM3/引脚可能多余,用 `cubemx_remove_peripheral` 移除。
