# 案例 3:TIM3 PWM + TIM2 输入捕获(0.4.1 注入器)

**场景**:从零建工程,TIM3_CH1(PA6)输出 10kHz / 50% PWM;TIM2_CH1(PA0-WKUP)输入捕获测外部信号频率/占空比。
这是 0.4.1 的「TIM PWM / 输入捕获注入器」实测用例(黄金样本 `PWM_IC_OLED2`)。

## 给 AI 的指令(直接复制)

> 用 STM32F103C8T6 从零建一个工程到 `C:\MINE\STM32Project\examples\Demo3`,工程名 Demo3,
> TIM3 的 PA6 输出 PWM(Prescaler 72、Period 100、Pulse 50),
> TIM2 的 PA0-WKUP 做输入捕获(Prescaler 72、Period 65535)。

## 等价工具调用

```
cubemx_new_project(
  project_name="Demo3",
  project_dir="C:/MINE/STM32Project/examples/Demo3",
  mcu="STM32F103C8T6",
  commands=[
    "set ip parameters TIM3 PWM PA6 S_TIM3_CH1 Prescaler 72 Period 100 Pulse 50",
    "set ip parameters TIM2 InputCapture PA0-WKUP S_TIM2_CH1_ETR Prescaler 72 Period 65535",
  ],
)
```

## 预期结果

- PWM:72MHz ÷ 72(Prescaler)÷ 100(Period)= **10kHz**,Pulse 50/100 = **50% 占空比**;
  生成的 `tim.c` 里 `TIM3.OCMode=TIM_OCMODE_PWM1`、`sConfigOC` 参数正确;
- 输入捕获:自动配 IC1 上升沿(direct)+ IC2 下降沿(INDIRECTTI),TIM2 中断自动启用;
  `tim.c` 生成 `MX_TIM2_Init()` + `HAL_TIM_IC_Start_IT` 示例。

## 已知限制(实测)

- CubeMX 只生成 IC1 的 `sConfigIC`;测占空比需在 `main.c` 手动补 IC2 配置:

```c
TIM_IC_InitTypeDef ic2 = {0};
ic2.ICPolarity  = TIM_INPUTCHANNELPOLARITY_FALLING;
ic2.ICSelection = TIM_ICSELECTION_INDIRECTTI;
ic2.ICPrescaler = TIM_ICPSC_DIV1;
ic2.ICFilter    = 0;
HAL_TIM_IC_ConfigChannel(&htim2, &ic2, TIM_CHANNEL_2);
HAL_TIM_IC_Start_IT(&htim2, TIM_CHANNEL_2);
```

- PWM/IC 注入依赖**模板里的芯片引脚映射**:PA6 对应 `S_TIM3_CH1`、PA0-WKUP 对应
  `S_TIM2_CH1_ETR`(组合名,不是 `S_TIM2_CH1`),这是 6.18 的 .ioc 内部命名,勿改。
