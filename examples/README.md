# examples —— 真实可复现案例

每个子目录一个**可以直接跑通**的 STM32 工程配置案例:把「给 AI 的指令」原样发给接入了本 MCP server 的 AI 助手,
即可生成对应的 HAL 工程(需要本机已按 README 配置好 `ST_CUBEMX_EXE` / `ST_CUBEMX_ALLOWED_ROOTS`)。

| 案例 | 覆盖能力 |
|------|----------|
| [01-blink-led](01-blink-led/) | 从零建工程 + GPIO(LED 点灯),最简入门 |
| [02-i2c-oled](02-i2c-oled/) | I2C1 + 自定义源文件(OLED)加入 CMake 源列表 |
| [03-tim-pwm-input-capture](03-tim-pwm-input-capture/) | TIM3 PWM 输出 + TIM2 输入捕获(0.4.1 注入器) |

约定:

- 每个案例的目标目录建议放在白名单内(默认 `C:\MINE\STM32Project\`),如 `C:\MINE\STM32Project\examples\Demo1`;
- 命令中的 `set ip parameters ...` 语法是 **server 专用注入指令**,不是 CubeMX 原生脚本命令,不要单独拿去跑 `-q`;
- 生成后若需要改引脚/外设,继续用 `cubemx_configure`;重新 generate 后自定义源文件丢失,用 `cubemx_add_source` 补回。
