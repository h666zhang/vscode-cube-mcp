# examples —— 真实可复现案例

每个子目录一个**可以直接跑通**的 STM32 工程配置案例：把「给 AI 的指令」原样发给接入了本 MCP server 的 AI 助手，
即可生成对应的 HAL 工程（需要本机已按 README 配置好 `ST_CUBEMX_EXE` / `ST_CUBEMX_ALLOWED_ROOTS`）。

| 案例 | 覆盖能力 |
|------|----------|
| [01-tim-pwm-input-capture](01-tim-pwm-input-capture/) | TIM3 PWM 输出 + TIM2 输入捕获（0.4.1 配置生成） |

约定：

- 每个案例的目标目录建议放在白名单内（默认 `C:\MINE\STM32Project\`），如 `C:\MINE\STM32Project\examples\Demo1`；
- 命令中的 `set ip parameters ...` 语法是 **server 专用注入指令**，不是 CubeMX 原生脚本命令，不要单独拿去跑 `-q`；
- 生成后若需要改引脚/外设，继续用 `cubemx_configure`；重新 generate 后自定义源文件丢失，用 `cubemx_add_source` 补回。
