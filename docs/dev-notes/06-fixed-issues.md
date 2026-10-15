# 工具已经自动处理的坑(不需要 agent 记)

这些是实测踩过、**现在工具自己会处理**的坑,记在这里是为了读到相关代码或报错时能查到来历。

## ADC 时钟(不配会卡死 generate)

`cubemx_intent` 配 ADC 时按 `.ioc` 的 APB2 频率**自动补**合法分频(F103 上限 14MHz → `RCC_ADCPCLK2_DIV6`
= 12MHz)。不设时 CubeMX 判定时钟非法,generate 弹 `Warning: Code Generation ... [Clock]` 对话框,
`-q` 批处理没人点 → 卡到 240s 超时。显式给 `adc_clock_div` 超上限会被**直接拒绝**;离线预检也会报。
(注意 `RCC.ADCPresc` 那行缺失 ≠ 没设:值等于库默认分频时 CubeMX 不写它,要看 `RCC.ADCFreqValue`。)

## SPI 波特率(不配会「报 OK 却零产出」)

CubeMX 默认 `BaudRatePrescaler=2`,APB2=72MHz 下等于 36 MBits/s,超过 F103 的 SPI 上限 18 MBits/s ⇒
CubeMX 记 `IP not ready for code generation: SPI1` 并弹 `Parameter Error` 对话框,`-q` 批处理没人点 →
卡死(实测挂 134s+),**而且一个 HAL 文件都不生成**(连 `.mxproject` 都没有)。
**关键**:CubeMX 的校验看的是 `CalculateBaudRate`,而 `set ip parameters SPIx BaudRatePrescaler ...`
**不会重算它** —— 所以必须两条命令一起发(工具现在就是这么做的:SPI1→4、SPI2→2,超限直接拒绝)。
2026-10-15 实测:两条都发 → 16.5s 正常产出,`spi.c` 里 `BaudRatePrescaler=SPI_BAUDRATEPRESCALER_4`。

## ADC 多通道 / 外部触发

多通道用 `pins`(`set mode INn` 只挂引脚,转换序列靠 `NbrOfConversion` + `Rank-n`/`Channel-n`/
`SamplingTime-n`,工具已自动补)。**外部触发必须配套触发源外设**(如 TIM2 的 TRGO 输出),
只设 ADC 的 `ExternalTrigConv` 会让 generate 卡死。
触发源还要分清规则组/注入组:F103 规则组(`ExternalTrigConv`)的合法值里**没有 `T2_TRGO`**
(那是注入组 `ExternalTrigInjecConv` 的),规则组要用 TIM2 只能 `T2_CC2`/`T2_CC3`,要 TRGO 得用
`T3_TRGO`/`T8_TRGO`。写非法值时 CubeMX **静默忽略**(不报错、硬件永不转换),所以 `cubemx_intent`
现在对照 db 的 values 直接拦下并列出合法取值。

## `project path` 在 CubeMX 6.18 已失效(实测 2026-10-12)

批处理里发 `project path "dir"` 返回 KO,而且随后的 `project generate` 既不产出文件、还会被判成失败
(另有一次 240s 卡死 + 残留 `javaw`)。要生成到别的目录用 `cubemx_generate(ioc, project_dir=...)`
(工具会把 `.ioc` 复制过去就地生成);`cubemx_raw_script` 里手写 `project path` 会被**直接拒绝**。

## 生成后要校验产出

CubeMX 报 OK 也可能一个文件都没生成(实测 new_project 首次生成零产出)—— 工具现在会校验
`Core/Src/*.c`,零产出时报 `CUBEMX_NO_OUTPUT`;若日志里有 `IP not ready for code generation: <IP>`,
则报 `IP_NOT_READY` 并指出是哪个外设未就绪(通常是该外设参数非法)。

## 超时强杀会残留 `javaw`(已修)

`STM32CubeMX.exe` 只是 launcher(实测 0.1s 就 `exit 0`),真正干活的是 `javaw`;过去按 launcher 的
PID 杀进程树**杀不到**它 → 孤儿残留占着单实例锁,让后续 generate 静默零产出。
现在记录调用前的 javaw 集合,超时后按**差集**清理(不动用户自己开的 GUI)。
同类毛病:调用被外部强杀时,脚本临时文件(`cubemx_mcp_*.txt`)也可能没删掉 —— 见 `_run_script` 的 `finally`。
