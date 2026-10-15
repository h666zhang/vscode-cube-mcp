# 知识库来源与盲测记录

知识库怎么导出、以及三轮「禁读仓库」盲测暴露并修掉的缺口。

---

## 知识库是怎么来的(2026-10-10,重构后)

事实只有两个来源,都不依赖人的记忆:

| 目录 | 内容 | 怎么来的 |
|------|------|----------|
| `knowledge/ip/` | 每个 IP 的合法模式名、参数与取值范围、信号组名 | `tools/gen_knowledge_from_db.py` 解析 CubeMX 安装目录的 `db/mcu/IP/<Name>-<Version>_Modes.xml` |
| `knowledge/mcu/` | 芯片引脚表(每个引脚支持哪些信号) | 同一个脚本解析 `db/mcu/<Mcu.Name>.xml`(文件名来自模板 `.ioc` 里的 `Mcu.Name`) |
| `knowledge/rules/` | 命令写法、`.ioc` 片段、已知坑 | **探针实测**:`cubemx_probe` 在副本上跑命令,再用 `ioc_diff` 拿到真实差异 |

几条实测结论(以前靠人记、现在靠查表):

- `.ioc` 的内部枚举名就是 db 里 `RefMode` 的 `Name` 属性(如 `PWM Generation1 CH1`);
  GUI 里显示的是另一个名字(`PWM Generation CH1`),写错会被 CubeMX **静默丢弃**;
- 信号在 `.ioc` 里的名字来自 db 里 `RefSignal` 的组名:`ShareableGroupName` 形如
  `S_$IpInstance_CH1`(把 `$IpInstance` 换成实例名),`ExclusiveGroupName` 形如
  `S_TIM2_CH1_ETR:TIM2` —— PA0 的组合名 `S_TIM2_CH1_ETR` 就是这么来的,**不用猜**;
- `verified: false` 的规则会被拒绝执行(防幻觉闸门);`source` 记着这条规则的实测日期与 CubeMX 版本。

> **技巧:CubeMX 认哪些脚本命令,可以直接问它** —— `cubemx_raw_script("help")` 会列出全部命令。
> 实测(6.18)给出的 `set` 子命令是:`mode` / `username` / `pin` / `pinreservation` / `gpio` /
> `ip` / `AllIpInContext` / `context*` / `noparam` / `userconst` / `bsp` / `tpl_path` / `dest_path` ——
> **里面没有 DMA,也没有中断相关项**,这两者都走"注入 `.ioc` 键"这条路(见 `rules/dma.yaml`、`rules/nvic.yaml`)。
> 另外 `help` 里的 `possible_value ip` 可以查某个 IP 的合法取值。

## 一次"盲测"暴露的三处缺口(2026-10-12,0.4.4.2 修)


## 一次"盲测"暴露的三处缺口(2026-10-12,0.4.4.2 修)

用一份**故意不给答案**的提示词,让一个全新会话用 `cubemx_help` + `cubemx_intent` 配
「ADC 5 通道 + DMA 循环 + TIM3 TRGO 触发」,要求只走 MCP 工具、不许手写 `.ioc`。
结果:一次成功、零返工,配置与生成代码全部正确 —— 但复盘它的动作后发现它**读了源码**:

- 它 `read_file` 了 `intent_resolver.py` 全文、`ioc_editor.py`(TIM 注入段)、
  `cubemx_mcp.py`(new_project 段)—— 因为 `cubemx_help` 当时**没说清执行顺序**:
  intents 先于 CubeMX 脚本、TIM 是就地注入、commands 里的 TIM set 命令要求该 TIM 已存在。
  顺序反了 TRGO 会**静默丢失**(generate 不报错,但硬件不触发)。→ 已写进 `help.yaml`。
- 为确认 `ADC_EXTERNALTRIGCONV_T3_TRGO`,它去 `db/mcu/IP/ADC-aditf_v2_5F1_Cube_Modes.xml`
  grep 了一遍 —— 因为导出的知识库里 `ExternalTrigConv` **只有 default、没有取值域**。
  → `gen_knowledge_from_db.py` 现在导 `RefParameter` 的 `PossibleValue`;同名参数在 XML 里
  有多份定义(`Visible=false` 的精简副本),所以取**并集**,否则只能拿到 1 个值。
- 它想"TIM 只当触发源、不要更新中断"时发现做不到(`_apply_tim` 没暴露 `irq`)。
  → 意图现在支持 `irq: false`(`interrupt: false` 同义)。

顺带:`cubemx_load` 改成**默认纯文本解析**(`via_cubemx=False`)—— 回读配置是高频动作,
以前每次都要等 CubeMX 启动 10~30s。

> 结论也记在这里:**"零返工"不能单独作为知识库够用的证据** —— 载体是"能读整个仓库的 AI"。
> 要真正验证,得再做一次同样的实验,但**禁止读仓库源码**,只留 MCP 工具。

## 第二次盲测(2026-10-12,禁读仓库、禁翻 db;0.4.4.3 修)


## 第二次盲测(2026-10-12,禁读仓库、禁翻 db;0.4.4.3 修)

按上面那条结论做的对照实验:同一份提示词,但**禁止读 `Vscode_cube_mcp` 下任何文件**,
只走 MCP 工具。这次**配置层一次成功、零返工**(上一条补的执行顺序与取值域生效了:
5 通道 Rank/采样时间、`RCC.ADCPresc` 自动补、`Prescaler=71/Period=99` = 10kHz、
TRGO 顺序、`irq: false` 成功抑制 `TIM3_IRQHandler`,全对)。

但**执行层连挂三次,同一个根因** —— `project path`:

- `cubemx_new_project` 内部那次 generate **报 OK 却一个文件都没生成**(当时没被发现,
  直到第三步才炸)。
- `cubemx_generate(ioc, project_dir=…)` 报 `CUBEMX_KO`。复现确认:旧代码会发
  `project path "dir"`,而 **CubeMX 6.18 对这条命令回 KO** —— 工具把任意一行 KO 当整体失败
  (那条"`.ioc` 可能不是 6.18 原生生成的"建议就是从这里来的,属误报)。
- 手工用 `cubemx_raw_script` 复现同一序列 → **240s 卡死被强杀 + 残留 `javaw`**(要 taskkill)。

修复(0.4.4.3):不再发 `project path`(`project_dir` 指向别的目录时把 `.ioc` 复制过去
就地生成)、`cubemx_raw_script` 直接拒绝手写 `project path`、**生成后校验 `Core/Src/*.c`**
(零产出报 `CUBEMX_NO_OUTPUT`,成功时附生成文件清单)、失败时说清**哪条命令**返回了 KO。

顺带发现:**8 个集成测试里 5 个早就失修了** —— `cubemx_load` 改成默认 `via_cubemx=False`
之后它们仍断言返回里有 `[OK`,而集成测试默认跳过、CI 也没有 CubeMX,所以一直没人发现。
这也是 `project path` 这个坑能活到盲测的原因:集成测试的 generate 路径根本没覆盖
`project_dir`(`test_generate_with_project_dir` 只断言 `[OK` 和目录存在,不检查产出)。
已改用真加载断言 + 新增一条覆盖 `project_dir` 的用例。

> 教训两条:①工具自己说的"成功"必须有**产出证据**兜底(报 OK 而零产出是最难查的失败);
> ②默认跳过的测试等于没有测试 —— 改默认值/返回格式后要手动跑一次集成测试。

## 第三轮盲测(2026-10-12,同一提示词复测;0.4.4.3 续修)


## 第三轮盲测(2026-10-12,同一提示词复测;0.4.4.3 续修)

同一份提示词再跑一次(仍禁读仓库、禁翻 db)。**配置层一次成功、零返工**,而且这次它主动
交代了上次的争议点:`ContinuousConvMode=DISABLE`(每次 TRGO 转一轮、节拍由 TIM3 决定),
推理正确。但复盘暴露了四件事:

- **它跑的其实是旧版 server** —— 它看到的 ADC help 还是改前的文本;`cubemx_new_project`
  返回里没有 `post_check` 步骤和"生成文件(N)"清单(都是本次新加的);`cubemx_generate`
  也没有清单。查进程:`python` 只有 pid 5280、启动于 22:23,而源码改于 22:44 ⇒
  **Reasonix 把 MCP server 当长驻进程复用,开新会话不会重启它**;要加载新代码必须重启宿主。
  (顺带发现知识库是纯内存缓存,改 `knowledge/*.yaml` 同样要重启 ⇒ 已改成按 mtime 失效)
- **提示词给了伪需求**:我写了"(不要 DMA 中断)",而 `HAL_NVIC_EnableIRQ(DMA1_Channel1_IRQn)`
  \+ `DMA1_Channel1_IRQHandler` 恰恰是 `HAL_ADC_Start_DMA` 的**必需配套**(它内部走
  `HAL_DMA_Start_IT`,采集完成回调全靠这个向量)。agent 为此花了 1 次 help + 2 次探针去
  "关中断",还给出错误结论("非 `_IT` 所以不会被触发")。**需求本身写错时,agent 会一路硬做
  下去,不会回头质疑需求** —— 这条要写进提示词设计规范。
- **它会自行松开"只用 MCP 工具"的约束**:用 `ls`/`grep`/`read_file` 读了生成产物,并自行
  论证"禁令只覆盖仓库目录"。限制得写成"除 MCP 工具返回的信息外,不得读取文件系统"。
- **两个真缺口**(已修,见 CHANGELOG):`trigger` 取值全靠猜(它这次猜对了 —— 去掉
  `ADC_EXTERNALTRIGCONV_` 前缀写 `T3_TRGO`);`cubemx_help` 未知 topic 静默返回总指南,
  而宿主的工具描述被截断 ⇒ topic 清单它看不到,只能逐个试。

> 教训:①"零返工"不能证明修复有效 —— 这轮没走 `project_dir` 那条路径,自然不炸;
> ②**黑箱测试的前置条件是宿主真的加载了新代码**(先查 server 进程的启动时间);
> ③写进提示词的需求要先确认在硬件上成立,否则会把 agent 引到伪问题上。

### 后续:补信息缺口(0.4.4.3 续修,2026-10-12)


### 后续:补信息缺口(0.4.4.3 续修,2026-10-12)

按"先解决信息缺口、再谈别的"补了两块。

**新增 `cubemx_inspect_output`(第 11 个工具)**:文件树 + `Core/Src/*.c` 的函数签名 + 按
`pattern` 取匹配行。**只给结构 / 签名 / 命中行,不返回文件全文**,也不展开 `Drivers/`
这类第三方目录 —— 既是"看生成结果"的正当入口,又不至于变成读任意文件的后门。两个动机:

1. `generate`/`new_project` 的清单只覆盖 `Core/Src/*.c`,agent 连 `Core/Inc/*.h`、`cmake/`、
   `.ld`、`startup_*.s` 都看不到,更看不到函数名(第 3 问因此根本答不了);
2. CubeMX 会把 `ContinuousConvMode` / `ScanConvMode` 这类"取默认值"的行**从 `.ioc` 里省掉**
   (实测:`.ioc` 里就是没有这两行),摘要自然读不到 —— 唯一能看到真值的地方是生成的 `adc.c`。

要不要在摘要里"合成隐含值"?**不做**:那等于用规则表去猜,和这次刚立的"不猜"原则相反。
让它去读真实产物更硬。

**知识库/文档补 5 处**:`topic="tim"` 的意图字段表(重点:`freq_hz` 对 `internal_clock` 也生效
—— 那个 agent 手算 `7200/10000` 纯属文档没写;`prescaler/period` 是"分频比/计数"要减 1;
`irq` 默认 **true**)、规则组与注入组的触发源对照、`.ioc` 只写已使能的中断、`project_dir`
不存在会自建且已存在目录不清旧文件、`cubemx_load` docstring 的矛盾(两个输出开关不能同时关)。

> 另:盲测里那两个 `.bak`(`ADCTest4.ioc.bak` / `ADCTest4.probe.ioc.bak`)是**工具备份机制**
> 留下的 —— 给 probe 副本写 `inject` 前会 `backup()` 成 `<stem>.probe.ioc.bak`,而副本执行完
> 被删,备份就成了孤儿。不是 bug,但会残留在工程目录里,知道就行。

## 加一个外设的 SOP
