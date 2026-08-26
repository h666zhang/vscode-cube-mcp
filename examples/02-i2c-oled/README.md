# 案例 2:I2C OLED(外设 + 自定义源文件)

**场景**:从零建工程,配 I2C1(PB8=SCL / PB9=SDA)驱动 OLED,并把自写的 `Core/Src/OLED.c` 加入 CMake 编译。

## 给 AI 的指令(直接复制)

> 用 STM32F103C8T6 从零建一个工程到 `C:\MINE\STM32Project\examples\Demo2`,工程名 Demo2,
> LED 在 PB13,带 I2C1(PB8=SCL、PB9=SDA),然后把 Core/Src/OLED.c 加入编译。

## 等价工具调用

```
# 第一步:建工程 + 配 GPIO/I2C
cubemx_new_project(
  project_name="Demo2",
  project_dir="C:/MINE/STM32Project/examples/Demo2",
  mcu="STM32F103C8T6",
  commands=[
    "set pin PB13 GPIO_Output",
    "set gpio parameters PB13 GPIO_Label LED",
    "set pin PB8 I2C1_SCL",
    "set pin PB9 I2C1_SDA",
    "set mode I2C1 I2C",
  ],
)
# 第二步:把自定义 OLED 驱动加入编译(工程生成后再调)
cubemx_add_source(ioc="C:/MINE/STM32Project/examples/Demo2/Demo2.ioc",
                  source_file="Core/Src/OLED.c")
```

## 预期结果

- 生成 `i2c.c` + `MX_I2C1_Init()`(100kHz 标准模式);
- `cmake/stm32cubemx/CMakeLists.txt` 的 `MX_Application_Src` 中加入 `Core/Src/OLED.c`。

## 注意

- OLED.c / OLED.h 需要你自己放进 `Core/Src` / `Core/Inc`;
- CubeMX 重新 generate 会**覆盖** CMakeLists.txt,自定义源文件条目会丢;
  重新生成后再调一次 `cubemx_add_source`(幂等,重复调用不重复加)。
