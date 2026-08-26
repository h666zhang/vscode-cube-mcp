# 案例 1:LED 点灯(从零建工程 + GPIO)

**场景**:从零生成一个 STM32F103C8T6 的 CMake HAL 工程,板载 LED 接在 PB13(推挽输出,标签 LED)。

## 给 AI 的指令(直接复制)

> 用 STM32F103C8T6 从零建一个工程到 `C:\MINE\STM32Project\examples\Demo1`,工程名 Demo1,
> LED 在 PB13(GPIO_Output,标签 LED)。

## 等价工具调用

```
cubemx_new_project(
  project_name="Demo1",
  project_dir="C:/MINE/STM32Project/examples/Demo1",
  mcu="STM32F103C8T6",
  commands=[
    "set pin PB13 GPIO_Output",
    "set gpio parameters PB13 GPIO_Label LED",
  ],
)
```

## 预期结果

- 生成 `Demo1.ioc` + CMake 工程(默认 72MHz,8MHz 晶振 × PLL9);
- `Core/Src/gpio.c` 中 `MX_GPIO_Init()` 配置 PB13 为推挽输出,`main.h` 定义 `LED_Pin` / `LED_GPIO_Port`;
- 在 `main()` 里翻转 `HAL_GPIO_WritePin(LED_GPIO_Port, LED_Pin, GPIO_PIN_SET/RESET)` 即可点灯。

## 验证

1. 打开生成的 `Demo1.ioc`,在 CubeMX GUI 中确认 PB13 = `GPIO_Output`,标签 LED;
2. 编译烧录后 LED 随代码翻转。
