# 更新日志

本项目的版本变更记录。开发笔记(原理、实测、SOP)见 [README.dev-notes.md](README.dev-notes.md)。

## 0.4.4.3(未发布)

- **新增离线语义预检**(`ioc_editor.check_semantics`):纯文本判定"配错了但 CubeMX 不报错"的
  组合问题,**不必起 CubeMX**(省掉一次 10~30s 启动)。第一批规则:
  - ADC 用了外部触发 → 触发源 TIM 必须在 `Mcu.IPx` 里,且它的 `TIM_MasterOutputTrigger`
    必须是 `TIM_TRGO_UPDATE`(否则 generate 不报错,但硬件永远不转换)
  - 配了 ADC → `RCC.ADCPresc` 必设,且 ADCCLK ≤ 上限(不设会让 generate 弹 `[Clock]` 卡死)
  - DMA 键组要成套:`Mcu.IPx` 里有 `DMA`、`Dma.Request<n>` 的序号与请求名对得上、
    `NVIC.<通道>_IRQn` 已使能
  阈值与枚举名取自 `rules/*.yaml`(`adc.trigger_prefix` / `adc.software_start` / `adc.clock` /
  `tim.precheck.master_output_for_trigger` / `dma.ioc_keys`),不硬编码。
  接入:`cubemx_load` 摘要里多 `precheck` 字段;`cubemx_intent` / `cubemx_new_project`
  改完直接把问题附在返回里
- **修复 `cubemx_generate(project_dir=…)`(第二轮盲测暴露)**:CubeMX 6.18 已不支持
  `project path` 脚本命令 —— 它返回 `KO`,而带它的批处理里随后的 `project generate`
  既不产出任何文件、还会被判定成失败(实测另有一次 240s 卡死 + 残留 `javaw`)。现在不再
  发这条命令:`project_dir` 指向别的目录时,把 `.ioc` 复制过去就地生成(原 `.ioc` 不动);
  `cubemx_script` 里手写 `project path` 直接拒绝并给出替代做法
- **生成产出校验**(同一次盲测暴露):`cubemx_generate` / `cubemx_new_project` /
  `cubemx_intent(generate=True)` 生成后校验 `Core/Src/*.c` 是否真的落盘 —— 以前"CubeMX
  报 OK 却一个文件都没生成"会被当成功回报,调用方要到第三步才发现;现在报
  `CUBEMX_NO_OUTPUT`,成功时返回里附生成文件清单
- **KO 定位到命令**:失败时返回"返回 KO 的命令: `<cmd>`",不再只有笼统的"命令返回 KO";
  那条误导性建议("`.ioc` 可能不是 6.18 原生生成的;先在 GUI 里打开并保存一次")已替换
- `cubemx_new_project` 里第二次 `project generate` 失败以前被忽略(仍报成功),现在显式失败
- `cubemx_help(topic="adc")` 补上 `continuous` / `interrupt` 的默认值(false),以及
  "外部触发下 continuous 的含义变化"(盲测里 agent 取默认值只能靠猜)
- 测试 209 → **224**(215 单元/协议 + 9 集成);顺手修集成测试失修:8 个用例里 5 个的
  断言在 `cubemx_load` 改成默认 `via_cubemx=False` 后就过时了(默认跳过所以一直没暴露),
  已改用真加载断言;新增 1 条覆盖 `project_dir` 生成路径

## 0.4.4.2(2026-10-12)

补三处"新会话实测"暴露的缺口 + 一处性能优化(完整实测记录见 dev-notes)。

- **`cubemx_load` 默认不再启动 CubeMX**(新增 `via_cubemx` 参数):只解析 `.ioc` 文本、
  返回结构化摘要,秒回;要确认"CubeMX 认不认这份 .ioc"才传 `via_cubemx=True`。
  agent 回读配置是高频动作,以前每次都要等 CubeMX 启动 10~30s
- **`cubemx_help` 补了「执行顺序与注入时机」一节**:intents 先于 CubeMX 脚本执行、
  TIM 内部时钟是就地注入、commands 里针对某 TIM 的 set 命令必须在该 TIM 已存在时才生效
  (否则被静默忽略)、注入类键(NVIC/DMA)排在 `saveas` 之后 generate 之前。
  这些以前只存在于源码里 —— 实测时 agent 是读完 `intent_resolver.py` 才知道顺序
- **TIM 意图支持 `irq: false`**:内部时钟注入片段默认带 `NVIC.TIMx_IRQn`(会生成 handler),
  只拿它当触发源时可以关掉(`interrupt: false` 同义)
- **知识库导出参数的取值域**:`RefParameter` 现在带 `values`(如 ADC 的 14 个
  `ExternalTrigConv` 合法值);同名参数在 XML 里有多份定义(其中一份是 `Visible=false` 的
  精简副本),取**并集**而不是只取第一份。以前要确认 `ADC_EXTERNALTRIGCONV_T3_TRGO`
  这种枚举名只能去翻 CubeMX 的 db XML
- 另:0.4.4.1 段里标记的"发布后修复"(`ToolError` 继承 SDK、`authors` 恢复)也随本版带出
- 测试 189 → **209**(201 单元/协议 + 8 集成)

## 0.4.4.1(2026-10-10 定号,2026-10-12 发布)

重构:把 STM32 的事实从代码里搬进知识库,并加了"配置意图"这一层。(已于 2026-10-12 发布到 PyPI)

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

### 修订(2026-10-12)

- **退役 `cubemx_add_source` 工具**(原第 11 个):它把自定义源文件写进
  `cmake/stm32cubemx/CMakeLists.txt`,而该文件每次 `project generate` 都被全量重写 —— 写进去必然丢。
  **正确做法**:工程根 `CMakeLists.txt` 是 CubeMX 明示"只生成一次、不重写"(generated only once)的文件,
  把 `.c` 加进 `# Add sources to executable` 的 `target_sources(... PRIVATE)`、头文件目录加进
  `# Add include paths` 的 `target_include_directories(... PRIVATE)`,一次写好、重新 generate 也不会丢
  (实测见 `OLED_HAL` 工程),不需要工具代劳
- 清掉因此失去引用的错误码 `PROJECT_NOT_GENERATED` / `PROJECT_STRUCTURE_UNEXPECTED`
- **删除废弃说明**:删 `DEPRECATED.md` 与 README 顶部 ⚠️ 块,项目回到正常维护状态
- **README 受众收敛**:「开发与测试」移到开发笔记;「知识库」压缩为指路;合并「更多资料 / 更新日志」;
  删掉指向不存在章节的"维护者发版流程"指引
- 工具数 11 → 10(README / AGENTS.md / 内置帮助 / `__init__` 注释)

### DMA 支持(2026-10-12 实测)

- **结论翻案**:原记录"DMA 脚本与注入都不行"只对了一半 —— 脚本 `set mode DMA ...` 确实配不了,
  但**注入完整 `.ioc` 键组有效**,等价于 GUI 里在对应外设的 DMA Settings 点 Add。实测三例
  (ADC1 / USART2_RX / USART2_RX+USART2_TX),generate 回读:`dma.c` 的 `MX_DMA_Init` +
  `HAL_NVIC_EnableIRQ(<通道>_IRQn)`、外设 `.c` 的 `hdma_xxx` + `__HAL_LINKDMA`、
  `stm32f1xx_it.c` 的 `<通道>_IRQHandler` 全部生成。当时失败的原因是只注入了 `Dma.Request0` /
  `Dma.RequestsNb` 两行:缺参数块、`Mcu.IPx=DMA` 与 `NVIC` 行,这些"孤儿键"在 saveas 时被丢掉了
- **`cubemx_intent` 支持 `dma` 子对象**(挂在外设意图下,和 GUI 的"在该外设上 Add"一个意思):
  `{"peripheral": "ADC1", "pins": ["PA0", "PA1"], "dma": {"mode": "circular"}}`
- **通道与方向查 db 表**:`db/mcu/IP/DMA-<ver>_Modes.xml` 的 `ModeLogicOperator` 树与 `RefMode`,
  由 `tools/gen_knowledge_from_db.py` 导出到 `knowledge/mcu/<芯片>.dma.yaml`(F103 49 条;
  ADC1→DMA1_Channel1、USART2_RX→DMA1_Channel6)。请求名有歧义(USART2 的 RX/TX)时报错让你选,不代猜
- **数据宽度不用猜**:只写必需参数(Instance/Direction/PeriphInc/MemInc/Mode),CubeMX 按请求类型
  补默认(实测 ADC→HALFWORD、USART→BYTE);要覆盖就写 `periph_align` / `mem_align` / `priority`
- **注入顺序修正**:中断 / DMA 这类注入改到 `project generate` **之前**,
  同一次调用里 `generate: true` 就能带上(此前注入在 generate 之后,需要再 generate 一次)
- 实测副产品:CubeMX 加载**任何**带 DMA 的 `.ioc`(含 GUI 自己生成的 `ADC.ioc`)都会打 8 条
  `(OptionalMessage_ERROR) IP (DMA) : Parameter (…) has Id (…) currently not set` —— 6.18 的噪音,
  保存与生成都正常
- `knowledge/rules/dma.yaml` 重写并置 `verified: true`,`cubemx_help(topic="dma")` 与 dev-notes
  的能力表同步
- 测试 165 → **178**(新增 `TestInjectDma` / `TestDmaIntent` 与知识库 DMA 查询用例)

### TIM 参数写法修正 + TRGO 配置(2026-10-12 实测)

- **修掉 `Prescaler = 72-1` 这种"未求值"生成代码**:注入 TIM 内部时钟 / PWM / 输入捕获时,
  片段里写的是 `{prescaler}-1`,CubeMX 会把它**原样抄进 C 代码**
  (`htim3.Init.Prescaler = 72-1;` —— 合法但难看,且与 GUI 产物不一致)。改为注入器先算好
  寄存器值(`72` 分频 → `Prescaler=71`);接口语义不变(上传的仍是"分频比 / 计数")
- **TRGO 输出可以脚本配(新实测)**:`set ip parameters TIM3 TIM_MasterOutputTrigger
  TIM_TRGO_UPDATE` 有效,CubeMX 会自动把 `TIM_MasterOutputTrigger` 补进 `TIMx.IPParameters` ——
  "用 TIM 触发 ADC 的外部触发"就靠这一步(只设 ADC 的 `ExternalTrigConv` 会让 generate 卡死)
- 实测工程:`ADC_DMA_TIM3`(I2C1 PB8/PB9 + ADC1 PA0~PA4 五通道 + DMA1_Channel1 循环 +
  DMA 中断 + TIM3 10kHz TRGO 触发),generate 回读 `tim.c` / `adc.c` / `dma.c` /
  `stm32f1xx_it.c` 全部符合预期,六个源文件 `-fsyntax-only` 无警告

### 修复与补测:移除残留 + 验证盲区(2026-10-12)

- **修 `cubemx_remove_peripheral` 的残留**(盘验证盲区时实测发现:删掉 ADC1 之后,
  `Dma.ADC1.0.*`、`Dma.Request0`、`NVIC.DMA1_Channel1_IRQn`、`PA0-WKUP.Signal=ADCx_IN0`
  全都留在 `.ioc` 里)。现在连带清理:
  - 物理引脚(`{pin}.*` 与 `SH.{信号}*`)—— 判据是引脚的 `Signal` 值含实例名;
    ADC 的 `ADCx_IN0` 里没有 "ADC1",由 `rules/adc.yaml` 新增的
    `remove.pin_signal_patterns` 认出来;
  - 共用向量中断(`ADC1_2_IRQn`)—— 同族实例(ADC2)还在时保留,不误删;
  - **DMA 连带**:`Dma.{请求}.*`(请求名 == 实例名或以 `{实例}_` 开头)、`Dma.Request<n>`、
    剩余请求**重新连续编号**(键前缀里的序号同步改)、`Dma.RequestsNb`、被删请求占用的
    `NVIC.<通道>_IRQn`;请求清空时连 `Mcu.IPx=DMA` 与 `MX_DMA_Init` 段一起清
- **补三块测试盲区**(此前零覆盖):
  - 集成测试 +3 个真跑 CubeMX 的用例:DMA 注入经得起 `load + saveas`、NVIC 注入同样、
    配 I2C1 + 它的 DMA 请求再移除后 `.ioc` 仍可 load(本机实测 76.7s 全过)
  - `tools/gen_knowledge_from_db.py` 的 XML 解析 +5 个单测(内联 XML:默认命名空间
    那个踩过的坑、`MEMTOMEM` 过滤、一请求多通道收成列表、模式/引脚解析、YAML 结构)
  - `remove_peripheral` 的 DMA 连带 +3 个单测(含共用向量保留、请求重编号)
- 「实例名 → 规则文件」的映射收敛到 `knowledge_loader.rule_name_for`(原先在
  `intent_resolver` 里,现在移除逻辑也要用,只留一份定义)
- 知识库过时说明修正:`rules/adc.yaml` 的"DMA 本身脚本配不了"改为现状;`rules/dma.yaml`
  补"移除会连带清 DMA"与"跨芯片未实测"两条;dev-notes 补 `remove_peripheral` 的删除范围
- 测试 178 → **189**(181 单元 + 8 集成);覆盖率 92.42%
- 本轮仍是**未验证**:真机烧录、F1 之外的芯片

### 工具接口补洞 + 工程卫生(2026-10-12)

- **`cubemx_new_project` 直接吃 `intents`**(与 `cubemx_intent` 同一套解析)—— 从零建工程一步到位;
  以前只能用 `commands` 手写 set 命令,而手写 `set mode ADC1 IN0` 不会自动补 ADC 时钟
  (模板默认 36MHz 超 F103 的 14MHz 上限),`project generate` 会弹 `[Clock]` 对话框把 `-q`
  批处理卡死(实测)
- **`cubemx_probe` 支持键注入**(新增 `inject` 参数):DMA / TIM 内部时钟 / PWM / 输入捕获 / NVIC 行
  都能探;给了 `inject` 时还会让 CubeMX `load + saveas` 走一遍,并明确写出它**原样保留**还是
  **改写/丢弃**了注入内容(以前收录注入类写法只能手写临时脚本 —— 这次 DMA 实测正是这么干的)
- **`cubemx_load` 附结构化摘要**(默认开,`summary=False` 关):IP / 引脚 / DMA 请求与参数 / 中断 /
  时钟 / 各外设参数 / 初始化顺序,JSON 输出,agent 判断"现在配了什么"不必再读 `.ioc` 原文
- **`ioc_structure.yaml` 从纯文档变成写入校验**:所有改 `.ioc` 的函数写盘后按它校验
  (IP / Pin 编号必须从 0 连续 + 计数一致),只报"这次新引入的问题",抛 `STRUCTURE_MISMATCH`
- **MCP 协议级测试固化**:新增 `tests/test_mcp_protocol.py`,用官方 `mcp.client.stdio.ClientSession`
  连 server,验工具清单(10 个)与错误传递;默认就跑(只碰本地知识库,不启 CubeMX)
- 修 `ioc_diff.diff_texts` 缺 `summary` 键的问题(渲染差异时会 `KeyError`,探针的
  "注入后 vs 归一化后"那段会踩到)
- 文档同步:AGENTS.md(测试数字、三个工具的新能力)、`help.yaml`(新增 `probe` topic、
  `new_project` 的 intents 示例)、README(工具表与参数表)、dev-notes(探针 SOP、写入双保险);
  修 `rules/usart.yaml` 里已过期的 todo
- **`pyproject.toml` 的 authors(两条款式是有意的,别当重复合并)**:第一条 `{ name = "…" }` 喂
  `Author` 字段、第二条带 email 的喂 `Author-email`。0.4.4.1 发布时误把它们当成重复条目合并,
  线上 `Author` 因此为空 —— 已在本仓库改回两条,下一版发布时恢复(0.4.4.1 已上传且不可覆盖)
- 测试 189 → **206**(198 单元/协议 + 8 集成);覆盖率 92.4% → **93.0%**
- 仍未验证:真机烧录、F1 之外的芯片

### 发布后修复(随 0.4.4.2 发布)

- **工具错误信息在 mcp≥2.3 下被吞(CI 抓出来的)**:mcp 2.3 起,工具里**只有**抛 SDK 自己的
  `ToolError` 才算"预期失败"(返回 `is_error=True`、消息给模型读);抛其它异常一律按崩溃处理,
  模型只看到 `Error executing tool <name>`。我们的 `ToolError` 原先只继承 `ValueError`,
  于是参数校验错误(UNKNOWN_PERIPHERAL / PIN_CONFLICT / PATH_NOT_ALLOWED …)的可读信息全丢。
  现在它同时继承 SDK `ToolError` 与 `ValueError` —— 旧版 SDK 行为不变、新版消息能到模型手里、
  测试里的 `assertRaises(ValueError)` 也不受影响
  (验证:mcp 2.0.0 与 2.3.0 下均 198 passed / 8 skipped)
- **CI 加最低依赖版本回归**(`test-min-deps` job,显式装 `mcp==2.0.0` 跑测试):上面那条正是
  "本地装 2.0.0 全绿、CI 装 2.3.0 全红"漏出来的 —— matrix 装最新依赖、这个 job 装最低支持版,
  两端行为都锁住
- 这两项 + 上面的 `authors` 恢复,都随**下一版**发布(0.4.4.1 已上传,不覆盖)

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
