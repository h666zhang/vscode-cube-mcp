# 更新日志

本项目的版本变更记录。开发笔记(原理、实测、SOP)见 [README.dev-notes.md](README.dev-notes.md)。

## 0.4.4.1(2026-10-10)

重构:把 STM32 的事实从代码里搬进知识库,并加了"配置意图"这一层。(**未发布**,版本号先定下来)

- **新增知识库 `knowledge/`**:引脚 / 模式 / 参数从 CubeMX 自带的 `db/` 导出
  (`tools/gen_knowledge_from_db.py`);实测规则放 `knowledge/rules/*.yaml`,每条带 `verified` 与来源
- **新增 `cubemx_intent`**:只写"要什么"(外设 / 模式 / 引脚 / 频率 / 占空比),`.ioc` 内部名由知识库查出
- **新增 `cubemx_probe`**:在 `.ioc` 副本上试跑命令并输出改前改后差异(用来收录没实测过的外设)
- **报错可定位**:统一的步骤名 + 20 个错误码,失败时能看出卡在哪一步、为什么、怎么办
- **`cubemx_help` 与 TIM 注入器改为读 YAML**:`cubemx_mcp.py` 里的硬编码事实全部删掉(1295 → 642 行)
- **`.ioc` 编辑独立成层**(`ioc_editor.py`):写前自动备份 `.bak`,写后回读校验,不一致报 `READBACK_MISMATCH`
- **探针实测收录外设**:USART / SPI / ADC / IWDG / WWDG / CAN / RTC;
  `set mode USARTx Asynchronous` 有效(与 TIM 不同);BaudRate 等于默认值 115200 时 CubeMX 不写出该行
- **中断**:脚本配不了,但注入 `NVIC.*_IRQn` 键有效(实测 generate 后 `stm32f1xx_it.c` 里出现对应 handler)
- **DMA**:脚本与注入都不行(CubeMX 命令表里没有 DMA 项),要用请先用 GUI 配
- **集成测试**:真跑 CubeMX 验证注入出的 `.ioc` 能被它接受(默认跳过,`ST_CUBEMX_RUN_INTEGRATION=1` 才跑)
- 测试 72 → 149(含 5 个集成测试);覆盖率按 6 个模块统计约 92.7%
- 依赖:新增 `pyyaml>=6.0`

### 实测补强(2026-10-10)

- **`cubemx_help` 自述修正**:工具一览 9 → 11(此前漏了 `cubemx_intent` / `cubemx_probe`),
  docstring 的 topic 列表补齐;新增"help 必须覆盖所有已注册工具"的回归测试
- **ADC 完整化**:意图层支持多通道(`pins` 顺序即 Rank 顺序,自动补 `NbrOfConversion` +
  每个通道的 `Rank-n` / `Channel-n` / `SamplingTime-n`)、连续转换(`continuous`)、
  扫描(`scan`,多通道默认开)、外部触发(`trigger`,简写 `T2_TRGO` 自动补前缀)、
  采样时间(`sampling_time`)与 EOC 中断(`interrupt`)
- **ADC 时钟自动分频(重要坑)**:配 ADC 时按 `.ioc` 里的 APB2 频率自动选合法预分频并写
  `RCC.ADCPresc`。不配的话 ADC 时钟 36MHz 超 F103 的 14MHz 上限,CubeMX 会弹
  `Warning: Code Generation ... [Clock]` 对话框,`-q` 批处理没人点 → generate 卡死到超时
  (实测:强杀后 `javaw` 还会残留,需手动 taskkill)
- **引脚别名容错**:`PA0` / 小写写法会自动归一化成引脚表里的 `PA0-WKUP`(唯一前缀匹配,
  有歧义则报错不猜);TIM / I2C / SPI / USART 路径同样受益
- **中断向量名按外设查**:`interrupt: true` 不再一律用 `{实例}_IRQn`,而是读规则文件里的
  `interrupt_irq`(如 F103 的 ADC 用共用向量 `ADC1_2_IRQn`)
- 新实测结论:多通道必须显式补转换序列(`set mode INn` 只挂引脚);`ScanConvMode` 由 CubeMX
  按 `NbrOfConversion` 自行推导;外部触发必须配套触发源外设(如 TIM2 的 TRGO 输出),
  否则 generate 卡死;db XML 里 `NbrOfConversion` 有两份定义,导出的 `max: 1` 来自
  `Visible=false` 那份,实测可设 2
- 验证:用 `ADCTest`(手工配)/ `ADCTest2`(纯单通道意图)/ `ADCTest3`(双通道+连续+中断)
  三个工程走完"只给意图 → generate → 回读 `adc.c`/`main.c`/`stm32f1xx_it.c`"
- 测试 149 → **165**;覆盖率约 92.7%

## 0.4.2(2026-08-27)
- **工程化补强**(非功能迭代,为「全外设完善」里程碑铺路):
  - CI:新增 GitHub Actions(windows-latest,Python 3.10-3.13 矩阵;`ruff check` + `ruff format --check` + `pytest` 覆盖率门槛 **80%**)
  - 测试:47 → **72 个**,覆盖率 71% → **94.9%**(新增模板补丁、新建工程调度层、工具包装层、help topics 等测试;覆盖率 <80% 即失败)
  - 代码质量:配置 Ruff(保守规则集)+ pre-commit 钩子;TIM 注入器硬编码枚举抽为 `_TIM_*` 常量对照表(6.18 实测来源);**修复 Python 3.10/3.11 的 f-string 反斜杠语法兼容问题**(此前低版本无法导入)
  - 文档:README 增加「项目定位自白」与「用之前 vs 用之后」说明;**明确能力边界**(只做 .ioc 配置与 HAL 骨架,应用业务代码由用户编写);术语统一为通用说法(最小模板 / 配置补丁 / 内置帮助 / 基准样本等);

## 0.4.1(2026-08-27)
- **新功能:TIM PWM 配置生成**(`_inject_tim_pwm`)——命令
  `set ip parameters TIM3 PWM <pin> <signal> [Prescaler n] [Period n] [Pulse n]`
  例:`set ip parameters TIM3 PWM PA6 S_TIM3_CH1 Prescaler 72 Period 100 Pulse 50` → 10kHz / 50%
- **新功能:TIM 输入捕获配置生成**(`_inject_tim_input_capture`)——命令
  `set ip parameters TIM2 InputCapture <pin> <signal> [Prescaler n] [Period n]`
  例:`set ip parameters TIM2 InputCapture PA0-WKUP S_TIM2_CH1_ETR Prescaler 72 Period 65535`
  自动配 IC1 上升沿 + IC2 下降沿参数,TIM2 中断自动启用
- 配置生成逻辑复用公共骨架 `_rebuild_ioc_lines` / `_finish_ioc_write`(IP/Pin 重建、
  IPNb/PinsNb 同步、functionlistsort 段、幂等),与 `_inject_tim_internal_clock` 同款机制
- .ioc 内部枚举名对照表(6.18 实测):`PWM Generation1 CH1` / `Input_Capture1_from_TI1`
  (GUI 名 ≠ .ioc 内部名,写错会被 CubeMX load 时静默丢弃);PA0 信号名是组合名
  `S_TIM2_CH1_ETR`;PWM 模式名空格转义 `\ `;Channel 键值 `TIM_CHANNEL_N`
- 已知限制(实测):CubeMX 只生成 IC1 的 `sConfigIC`,测占空比的 IC2 需在 main.c
  手动 `HAL_TIM_IC_ConfigChannel` 补齐(示例见 `cubemx_help(topic="tim")`)
- 文档:`cubemx_help` tim topic 补充 PWM/输入捕获命令、F103 引脚→信号映射表、
  IC2 补配示例;README.dev-notes.md 新增「0.4.1」实测记录(基准样本逐行对齐)
- 测试:40 → **47 个**(新增 PWM/输入捕获配置生成回归:幂等、替换、非法参数、Channel 键)
- 真机验证:注入 .ioc 经 6.18 generate 后与基准样本(`PWM_IC_OLED.ioc`)逐行一致,
  生成 tim.c 参数正确(PWM 10kHz/50% + IC RISING)
- 版本规划:0.5.0 预留给"完善所有外设"里程碑;全部外设完善前,功能迭代走 0.4.x

## 0.4.0(2026-08-26)
- **架构:最小模板 + 外设配置补丁**——模板从"整机配置"精简为每芯片 1 个基础种子
  (`STM32F103C8T6.ioc` = 芯片标识 + 72MHz/SWD/SysTick,**无外设**),外设全部由
  `cubemx_new_project` 的 `commands` set 现配,模板数量不再随配置组合爆炸
- 新功能:`cubemx_help` 内置帮助工具(第 9 个工具)——完整指南 + templates/ 动态扫描 +
  分主题(gpio/i2c/tim/rcc/remove/add_source),陌生 agent 接入后第一件事调用它
- TIM 内部时钟:配置补丁(`_inject_tim_internal_clock`)替代借壳法——命令含
  `set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL` 时自动注入 6.18 验证过的
  标准表达(任意 TIM/芯片,可附 `Prescaler`/`Period` 自定义);**借壳法退役删除**
- 模板库精简:删除旧组合模板 `tim_template.ioc` / `tim2_internal.ioc` 与 `templates/README.md`
  (知识迁入 README.dev-notes.md);缺芯片模板时报错带可用模板清单与生成指引
- 修复:
  - 安全:`_check_path` 白名单前缀绕过漏洞;`cubemx_add_source` 输入校验(拒绝路径穿越/绝对路径/换行注入)
  - `cubemx_add_source` 锚点找不到时静默假成功 → 改抛错
  - `cubemx_remove_peripheral` 补 `Mcu.Pin` 重排与 `Mcu.PinsNb` 修正;functionlistsort 段删除泛化
  - `_run_script` 超时后强杀 CubeMX 进程树(防残留 Java 进程占工程文件锁)
  - `cubemx_new_project` set 命令失败立即中止;支持 `Prescaler`/`Period` 自定义
  - 配置生成/移除逻辑的行尾换行匹配、IPNb/PinsNb 缺失防御等健壮性修复
- 测试:20 → **40 个**(新增回归测试覆盖上述修复)

## 0.3.1(2026-08-07)
- 修复:pip 安装版 `cubemx_new_project` 找不到模板(`_project_template` 补 `sys.prefix/templates` 查找路径——data-files 安装时把模板放在 `sys.prefix` 下)

## 0.3.0(2026-08-07)
- 新功能:`cubemx_new_project` 参数化,设计原则 **"默认值而非强制"**:
  - `toolchain` 默认 `"CMake"`,可覆盖(EWARM V8.32 / MDK-ARM 等)
  - `couple_files` 默认 `True`(每个外设生成独立 `.c/.h`),可覆盖为 `False` 集中到 main.c
  - `clock_source` 默认 `"HSE"` + `pll_mul` 默认 `9`(8MHz×9=72MHz),写 .ioc 时按默认值补回 `RCC.PLLSourceVirtual=HSE`;生成后 HSE 仍丢失会返回 ⚠ 时钟警告
- 文档:README 新增 `cubemx_new_project` 参数说明;`.gitignore` 忽略 `*.bak`

## 0.2.2(2026-08-06)
- 修复:pyproject 作者元数据(`pip show` 的 Author 字段显示正确)

## 0.2.1(2026-08-06)
- 文档:README 重写为面向用户的手册;新增 Claude Code 安装指南

## 0.2.0(2026-08-06)
- 新功能:从零生成 HAL 工程(`cubemx_new_project`,内置模板库)
- 新功能:外设管理工具(`cubemx_remove_peripheral` / `cubemx_add_source`)
- 新功能:TIM 内部时钟工程化(借壳法,`templates/STM32F103C8T6_tim2_internal.ioc`)
- 打包:templates 随 wheel 分发(PyPI 正式发布)

## 0.1.1(2026-08-05)
- 文档:补充构建链说明(CMake+Ninja)

## 0.1.0(2026-08-05)
- 首个版本:MCP server 封装 STM32CubeMX 命令行脚本模式(`-q`)
