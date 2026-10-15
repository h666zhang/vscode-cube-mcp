#!/usr/bin/env python3
"""把配置意图翻译成动作:查知识库拿信号名与参数,再交给 ioc_editor 或生成 set 命令。

意图只描述"要什么",例如:
    {"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "freq_hz": 10000, "duty": 0.5}

.ioc 内部名一律从 knowledge/ 查出来,不写死也不猜:
  - 引脚支不支持这个信号   -> knowledge/mcu/<芯片>.pins.yaml
  - 信号在 .ioc 里叫什么   -> knowledge/ip/<IP>.yaml 里 RefSignal 的组名
  - 片段怎么写、默认值多少 -> knowledge/rules/<外设>.yaml

查不到就报错(UNKNOWN_PIN_SIGNAL / UNKNOWN_PARAM / UNKNOWN_PERIPHERAL / RULE_NOT_VERIFIED)。

参数两种给法都支持:
  - 业务参数:freq_hz / duty,由代码按定时器时钟换算成 prescaler/period/pulse;
  - 寄存器参数:直接给 prescaler / period / pulse,显式值优先。
"""

from __future__ import annotations

import re

from . import ioc_editor, knowledge_loader
from .cubemx_result import ToolError

MAX_PRESCALER = 65536
MAX_PERIOD = 65535


def tim_clock_mhz() -> int:
    """定时器时钟(MHz)。STM32F103 上 TIM2~TIM4 挂 APB1,定时器时钟等于系统时钟 72MHz。

    这个数字来自 knowledge/rules/rcc.yaml,不在这里写死。
    """
    defaults = knowledge_loader.rule("rcc").get("defaults") or {}
    return int(defaults.get("sysclk_mhz", 72))


def split_divider(total: int, max_period: int = MAX_PERIOD) -> tuple:
    """把"总计数周期数"拆成 (prescaler, period),尽量让实际频率贴近目标。"""
    if total <= 0:
        raise ToolError("BAD_ARGUMENT", f"计数周期必须是正数,收到 {total}")
    if total <= max_period:
        return 1, total
    prescaler = (total + max_period - 1) // max_period
    if prescaler > MAX_PRESCALER:
        raise ToolError("BAD_ARGUMENT", f"频率太低,分频到不了(需要 prescaler={prescaler})")
    period = max(1, round(total / prescaler))
    return prescaler, min(period, max_period)


def tim_params(mode: str, intent: dict) -> dict:
    """算出一个 TIM 模式的 prescaler/period(/pulse):显式给的优先,没给的按 freq_hz/duty 换算。"""
    defaults = knowledge_loader.rule_mode("tim", mode)["defaults"]
    prescaler = intent.get("prescaler")
    period = intent.get("period")
    freq = intent.get("freq_hz")
    if (prescaler is None or period is None) and freq:
        total = round(tim_clock_mhz() * 1_000_000 / float(freq))
        prescaler, period = split_divider(total)
    prescaler = int(defaults["prescaler"] if prescaler is None else prescaler)
    period = int(defaults["period"] if period is None else period)
    out = {"prescaler": prescaler, "period": period}
    if mode == "pwm":
        pulse = intent.get("pulse")
        if pulse is None:
            duty = intent.get("duty")
            pulse = round(period * float(duty)) if duty is not None else defaults["pulse"]
        out["pulse"] = int(pulse)
    if not (1 <= prescaler <= MAX_PRESCALER):
        raise ToolError("BAD_ARGUMENT", f"prescaler 应在 1~{MAX_PRESCALER} 之间,收到 {prescaler}")
    if not (1 <= period <= MAX_PERIOD):
        raise ToolError("BAD_ARGUMENT", f"period 应在 1~{MAX_PERIOD} 之间,收到 {period}")
    if "pulse" in out and not (0 <= out["pulse"] <= period):
        raise ToolError("BAD_ARGUMENT", f"pulse 应在 0~period({period}) 之间,收到 {out['pulse']}")
    return out


def ioc_signal_name(instance: str, channel: str) -> str:
    """把 db 里的通道角色(如 CH1)换成 .ioc 里的信号名(如 S_TIM3_CH1)。

    .ioc 的名字来自 db 的 RefSignal:
      - ExclusiveGroupName 形如 "S_TIM2_CH1_ETR:TIM2" —— 该引脚上这个 IP 的独占名;
      - ShareableGroupName 形如 "S_$IpInstance_CH1:TIM1,TIM3,..." —— 把 $IpInstance 换成实例名。
    """
    signals = knowledge_loader.ip_facts(instance).get("signals") or {}
    item = signals.get(channel)
    if not item:
        raise ToolError("UNKNOWN_PIN_SIGNAL", f"{instance} 在 CubeMX db 里没有通道 {channel}")
    exclusive = item.get("exclusive_group", "")
    if exclusive:
        name, _, owner = exclusive.partition(":")
        if owner == instance:
            return name
    shareable = item.get("shareable_group", "")
    if shareable:
        return shareable.partition(":")[0].replace("$IpInstance", instance)
    raise ToolError("UNKNOWN_PIN_SIGNAL", f"{instance} 的通道 {channel} 在 db 里没有可用的信号名")


def find_channel(instance: str, pin: str) -> str:
    """在这个引脚支持的信号里找属于该实例的通道,如 PA6 上的 TIM3 -> CH1。"""
    signals = knowledge_loader.pin_signals(pin)
    pattern = re.compile(rf"^{re.escape(instance)}_CH(\d)$")
    for name in signals:
        hit = pattern.match(name)
        if hit:
            return f"CH{hit.group(1)}"
    raise ToolError(
        "UNKNOWN_PIN_SIGNAL",
        f"{pin} 上没有 {instance} 的通道信号;这个引脚支持: {', '.join(signals)}",
    )


def gpio_commands(intent: dict) -> list:
    """GPIO 走 CubeMX 的 set 命令(不需要注入 .ioc 片段)。

    引脚名先归一化:`.ioc` 里带后缀的引脚(PA0-WKUP / PA13)如果只写短名,
    `set pin` 还能被 CubeMX 接受,但 **`set gpio parameters` 会返回 KO**
    —— 实测 2026-10-15(LEDRUN 用例):8 个引脚的模式都配上了、只有标签没设上,
    工具却整体报 `CUBEMX_KO`。
    """
    pin = str(intent.get("pin", "")).strip()
    if not pin:
        raise ToolError("BAD_ARGUMENT", "GPIO 意图需要 pin(如 PB13)")
    pin = knowledge_loader.resolve_pin_name(pin)
    role = str(intent.get("mode", "output")).strip().lower()
    mode_map = {
        "output": "GPIO_Output",
        "input": "GPIO_Input",
        "analog": "GPIO_Analog",
        "gpio_output": "GPIO_Output",
        "gpio_input": "GPIO_Input",
    }
    ioc_mode = mode_map.get(role, str(intent.get("mode", "")).strip())
    if not ioc_mode:
        raise ToolError("BAD_ARGUMENT", f"GPIO 的 mode 无法识别: {intent.get('mode')!r}")
    commands = [f"set pin {pin} {ioc_mode}"]
    label = str(intent.get("label", "")).strip()
    if label:
        commands.append(f"set gpio parameters {pin} GPIO_Label {label}")
    return commands


def _pin_commands(instance: str, pins: list) -> list:
    """把一组引脚换成 set pin 命令;角色(TX/RX、SCL/SDA)从引脚表里查,不靠调用方指定。"""
    commands = []
    for pin in pins:
        signals = knowledge_loader.pin_signals(pin)
        hit = [s for s in signals if s.startswith(f"{instance}_")]
        if not hit:
            raise ToolError(
                "UNKNOWN_PIN_SIGNAL",
                f"{pin} 上没有 {instance} 的信号;这个引脚支持: {', '.join(signals)}",
            )
        commands.append(f"set pin {pin} {hit[0]}")
    return commands


def i2c_commands(intent: dict) -> list:
    """I2C 走 set 命令;引脚角色(SCL/SDA)从引脚表里查。"""
    instance = str(intent.get("peripheral", "")).strip().upper()
    pins = intent_pins(intent)
    if not pins:
        raise ToolError("BAD_ARGUMENT", f'{instance} 意图需要 pins(如 ["PB8", "PB9"])')
    return [*_pin_commands(instance, pins), f"set mode {instance} I2C"]


def usart_commands(intent: dict) -> list:
    """USART 走 set 命令。

    实测(CubeMX 6.18,见 knowledge/rules/usart.yaml):set mode USARTx Asynchronous 有效,
    引脚写成 {pin}.Mode=Asynchronous + {pin}.Signal=USARTx_TX/RX。
    BaudRate 等于默认值 115200 时 .ioc 里不写出这一行,但不影响配置生效。
    """
    instance = str(intent.get("peripheral", "")).strip().upper()
    knowledge_loader.require_rule_verified("usart")  # 防幻觉闸门:没实测过就拒绝
    commands = [f"set mode {instance} Asynchronous"]
    baudrate = intent.get("baudrate")
    if baudrate:
        commands.append(f"set ip parameters {instance} BaudRate {int(baudrate)}")
    pins = intent_pins(intent)
    if pins:
        commands.extend(_pin_commands(instance, pins))
    return commands


def spi_commands(intent: dict, ioc_path: str) -> list:
    """SPI 走 set 命令(实测:set mode SPI1 Full_Duplex_Master 有效)。

    模式名用下划线形式,可选值见 knowledge/rules/spi.yaml 的 modes。
    另外必须配波特率分频:用默认值 2 时波特率会超过 SPI 上限,CubeMX 判定该 IP
    未就绪 ⇒ generate 报 OK 却零产出(还可能弹窗卡死),见 spi_baudrate_command。
    """
    instance = str(intent.get("peripheral", "")).strip().upper()
    rules = knowledge_loader.require_rule_verified("spi")
    modes = rules.get("modes") or {}
    role = str(intent.get("mode", "full_duplex_master")).strip().lower()
    mode = modes.get(role)
    if not mode:
        raise ToolError("UNKNOWN_PARAM", f"SPI 的 mode 要写其中之一: {', '.join(sorted(modes))}")
    commands = [f"set mode {instance} {mode}"]
    pins = intent_pins(intent)
    if not pins:
        raise ToolError("BAD_ARGUMENT", f'{instance} 意图需要 pins(如 ["PA5", "PA6", "PA7"])')
    commands.extend(_pin_commands(instance, pins))
    commands.extend(spi_baudrate_commands(intent, ioc_path))
    return commands


def _spi_baudrate(intent: dict, ioc_path: str) -> tuple:
    """算出 SPI 该用的 (分频枚举名, 分频比, 该总线频率 Hz);非法值直接抛 ToolError。

    依据 rules/spi.yaml 的 baudrate 段(上限 / 实例→总线 / 候选分频),从 .ioc 读该 SPI
    所在总线的频率,选满足 波特率 ≤ 上限 的最小分频(SPI1@72MHz → 4、SPI2@36MHz → 2)。
    意图里给 prescaler 可显式覆盖(枚举名或数字),超限/非法值都会被拒绝。
    返回 ("", 0, 0) 表示知识库没写 baudrate 段,调用方跳过这块。
    """
    baud = knowledge_loader.require_rule_verified("spi").get("baudrate") or {}
    prescalers = baud.get("prescalers") or {}
    if not prescalers:
        return "", 0, 0
    instance = str(intent.get("peripheral", "")).strip().upper()
    bus = (baud.get("instances") or {}).get(instance) or (baud.get("default") or {})
    max_hz = int(baud.get("max_hz") or 0)
    fclk = _read_int(ioc_editor.read_key(ioc_path, bus.get("freq_key", "")), bus.get("default_hz", 72000000))

    explicit = str(intent.get("prescaler", "")).strip()
    if explicit:
        name = explicit
        if name not in prescalers:
            # 也允许写数字(4 → SPI_BAUDRATEPRESCALER_4)
            by_div = {str(div): key for key, div in prescalers.items()}
            if explicit in by_div:
                name = by_div[explicit]
            else:
                legal = ", ".join(sorted(prescalers))
                nums = ", ".join(str(d) for d in sorted(int(v) for v in prescalers.values()))
                raise ToolError("BAD_ARGUMENT", f"prescaler 只能写 {legal}(或其中任一数字 {nums});收到 {explicit!r}")
        div = int(prescalers[name])
        if max_hz and fclk / div > max_hz:
            fits = [k for k, d in prescalers.items() if fclk / int(d) <= max_hz]
            best = min(fits, key=lambda k: int(prescalers[k])) if fits else ""
            raise ToolError(
                "BAD_ARGUMENT",
                f"prescaler={name} 会让 {instance} 的波特率 = {int(fclk / div)} Hz 超过上限 {max_hz} Hz,"
                "CubeMX 会判定该 IP 未就绪(generate 报 OK 却零产出,还可能弹对话框卡死到超时)。"
                + (f"当前 {bus.get('freq_key')} = {int(fclk)} Hz,这个频率下请用 {best}。" if best else ""),
            )
        return name, div, fclk
    for key, div in sorted(prescalers.items(), key=lambda kv: int(kv[1])):
        if max_hz and fclk / int(div) > max_hz:
            continue
        return key, int(div), fclk
    key = max(prescalers, key=lambda k: int(prescalers[k]))  # 最大的分频仍超限:只能退到它
    return key, int(prescalers[key]), fclk


def spi_baudrate_commands(intent: dict, ioc_path: str) -> list:
    """SPI 波特率命令 —— **两条**:预分频 + 波特率值。

    为什么要两条(实测 2026-10-15):CubeMX 的参数校验看的是 `SPIx.CalculateBaudRate` 这个键,
    而 `set ip parameters SPIx BaudRatePrescaler ...` **不会重算它** —— 只改预分频时 .ioc 里
    CalculateBaudRate 仍是旧值(实测 36.0 MBits/s),CubeMX 于是记 "IP not ready for code
    generation: SPI1" 并跳过代码生成:报 OK 却一个文件都不产出,还可能弹 Parameter Error
    对话框把 -q 批处理卡死到超时。两条都发之后:15.4s 正常生成,`spi.c` 里
    `BaudRatePrescaler = SPI_BAUDRATEPRESCALER_4`、`.ioc` 里 `CalculateBaudRate=18000000`。
    """
    chosen, div, fclk = _spi_baudrate(intent, ioc_path)
    if not chosen:
        return []
    baud = knowledge_loader.require_rule_verified("spi").get("baudrate") or {}
    instance = str(intent.get("peripheral", "")).strip().upper()
    return [
        f"set ip parameters {instance} {baud.get('parameter', 'BaudRatePrescaler')} {chosen}",
        f"set ip parameters {instance} {baud.get('rate_key', 'CalculateBaudRate')} {int(fclk / div)}",
    ]


def _read_int(text: str, default) -> int:
    """把 .ioc 里读到的值转成整数;读不到或不合法时用默认值。"""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return int(default)


def adc_clock_command(intent: dict, ioc_path: str) -> str:
    """ADC 预分频命令 —— 缺它必踩的坑:CubeMX 判定时钟参数非法,generate 时弹
    "Warning: Code Generation ... [Clock]" 对话框;-q 批处理下没人点,进程卡到超时。

    依据 knowledge/rules/adc.yaml 的 clock 段(上限 / 候选分频 / PCLK2 键名),
    从 .ioc 读当前 APB2 频率,选满足 ADCCLK ≤ 上限的最小分频。
    意图里给 adc_clock_div 可显式覆盖(默认值而非强制);yaml 没有 clock 段则返回空串。
    """
    clock = knowledge_loader.require_rule_verified("adc").get("clock") or {}
    prescalers = clock.get("prescalers") or {}
    if not prescalers:
        return ""
    explicit = str(intent.get("adc_clock_div", "")).strip()
    if explicit:
        if explicit not in prescalers:
            raise ToolError(
                "BAD_ARGUMENT",
                f"adc_clock_div 只能写 {', '.join(sorted(prescalers))};收到 {explicit!r}",
            )
        # 显式给的值同样要校验上限:超限时 generate 会弹 [Clock] 对话框、-q 批处理没人点,
        # 卡到 240s 超时(实测)。以前只校验"值在候选表里",超限值会被静默接受。
        div = int(prescalers[explicit])
        max_hz = int(clock.get("max_hz") or 0)
        if max_hz:
            pclk2 = _read_int(
                ioc_editor.read_key(ioc_path, clock.get("pclk2_key", "")), clock.get("pclk2_default_hz", 72000000)
            )
            if pclk2 / div > max_hz:
                fits = [n for n, d in prescalers.items() if pclk2 / int(d) <= max_hz]
                best = min(fits, key=lambda n: int(prescalers[n])) if fits else ""
                raise ToolError(
                    "BAD_ARGUMENT",
                    f"adc_clock_div={explicit} 会让 ADCCLK = {int(pclk2 / div)} Hz 超过上限 {max_hz} Hz,"
                    "generate 时会弹 [Clock] 对话框把批处理卡死到超时。"
                    + (f"当前 APB2 = {int(pclk2)} Hz,这个频率下请用 {best}。" if best else ""),
                )
        chosen = explicit
    else:
        max_hz = int(clock.get("max_hz") or 0)
        pclk2 = _read_int(
            ioc_editor.read_key(ioc_path, clock.get("pclk2_key", "")), clock.get("pclk2_default_hz", 72000000)
        )
        chosen = ""
        for name, div in sorted(prescalers.items(), key=lambda kv: int(kv[1])):
            if max_hz and pclk2 / int(div) > max_hz:
                continue
            chosen = name
            break
        if not chosen:  # 理论上到不了:候选里最大的分频仍超限,退到最大分频
            chosen = max(prescalers, key=lambda k: int(prescalers[k]))
    return f"set ip parameters {clock.get('set_ip', 'RCC')} {clock.get('parameter', 'ADCPresc')} {chosen}"


def _adc_channels(instance: str, intent: dict) -> list:
    """解析 ADC 意图的通道:pin(单个)或 pins(多个,顺序即 Rank 顺序)。

    返回 [(引脚归一化名, 信号名, 通道号), ...];引脚可写短名(PA0 -> PA0-WKUP)。
    """
    raw = []
    if str(intent.get("pin", "")).strip():
        raw.append(str(intent["pin"]).strip())
    raw.extend(str(p).strip() for p in (intent.get("pins") or []) if str(p).strip())
    if not raw:
        raise ToolError("BAD_ARGUMENT", f'{instance} 意图需要 pin 或 pins(如 PA0 或 ["PA0", "PA1"])')
    out = []
    for name in raw:
        pin = knowledge_loader.resolve_pin_name(name)
        signals = knowledge_loader.pin_signals(pin)
        hits = [s for s in signals if s.startswith(f"{instance}_IN")]
        if not hits:
            raise ToolError(
                "UNKNOWN_PIN_SIGNAL", f"{pin} 上没有 {instance} 的输入通道;这个引脚支持: {', '.join(signals)}"
            )
        out.append((pin, hits[0], hits[0].rsplit("_IN", 1)[-1]))
    return out


def adc_commands(intent: dict, ioc_path: str = "") -> list:
    """ADC 走 set 命令(全部实测,见 knowledge/rules/adc.yaml)。

    意图字段(默认值而非强制):
      pin / pins     单通道写 pin;多通道写 pins,顺序即 Rank 顺序。引脚可写短名 PA0
      sampling_time  采样时间;多通道不给时用 db 默认值补齐各通道
      continuous     连续转换(默认 False)
      scan           扫描模式;多通道默认 True,单通道默认 False
      trigger        外部触发源:简写 T2_TRGO 或完整枚举 ADC_EXTERNALTRIGCONV_T2_TRGO
      adc_clock_div  ADC 预分频;默认按 .ioc 里的 APB2 频率自动选合法值
      interrupt      写 true 时由上层注入 NVIC.ADC1_2_IRQn(见 adc.yaml 的 interrupt_irq)

    实测要点:第 2 个通道起 `set mode INn` 只挂引脚、不写转换序列,必须补
    NbrOfConversion + 每个通道一组 Rank-n / Channel-n / SamplingTime-n。
    """
    instance = str(intent.get("peripheral", "")).strip().upper()
    rules = knowledge_loader.require_rule_verified("adc")
    channels = _adc_channels(instance, intent)
    db_default = ((rules.get("parameters_from_db") or {}).get("sampling_time") or {}).get("default", "")
    sampling = str(intent.get("sampling_time", "")).strip() or str(db_default)
    if str(intent.get("sampling_time", "")).strip():
        # 写错的采样时间 CubeMX 会静默忽略 ⇒ 对着 db 的取值表先拦一道
        _check_db_enum(sampling, "ADC1", "SamplingTime", f"{instance} 的 sampling_time")
    commands = []
    for pin, signal, channel in channels:
        commands += [f"set mode {instance} IN{channel}", f"set pin {pin} {signal}"]
    if len(channels) > 1:
        # 多通道:补转换序列(实测:set mode INn 不写 Rank-n)
        commands.append(f"set ip parameters {instance} NbrOfConversion {len(channels)}")
        for rank, (_pin, _signal, channel) in enumerate(channels[1:], start=1):
            commands += [
                f"set ip parameters {instance} Rank-{rank}#ChannelRegularConversion {rank + 1}",
                f"set ip parameters {instance} Channel-{rank}#ChannelRegularConversion ADC_CHANNEL_{channel}",
                f"set ip parameters {instance} SamplingTime-{rank}#ChannelRegularConversion {sampling}",
            ]
    if str(intent.get("sampling_time", "")).strip():
        tpl = (rules.get("sampling_time") or {}).get("command") or ""
        if not tpl:
            raise ToolError("UNKNOWN_PARAM", "knowledge/rules/adc.yaml 里没有 sampling_time.command,写不了采样时间")
        commands.append(tpl.format(instance=instance, value=sampling))
    want_scan = intent.get("scan")
    if want_scan is None:
        want_scan = len(channels) > 1
    if want_scan:
        commands.append(f"set ip parameters {instance} ScanConvMode ADC_SCAN_ENABLE")
    if intent.get("continuous"):
        commands.append(f"set ip parameters {instance} ContinuousConvMode ENABLE")
    trigger = str(intent.get("trigger", "")).strip()
    if trigger:
        prefix = str(rules.get("trigger_prefix") or "ADC_EXTERNALTRIGCONV_")
        value = trigger if trigger.upper().startswith("ADC_") else f"{prefix}{trigger.upper()}"
        _check_adc_trigger(value, prefix)
        commands.append(f"set ip parameters {instance} ExternalTrigConv {value}")
    clock = adc_clock_command(intent, ioc_path)
    if clock:
        commands.append(clock)
    return commands


def _check_db_enum(value: str, instance: str, name_prefix: str, what: str) -> None:
    """把取值对照 CubeMX db 校验:不合法直接报错并列出合法值。

    CubeMX 对不认识的枚举**静默忽略**(不报错,但配置无效),所以这类值必须在改 .ioc
    之前拦下来;知识库里查不到该参数的取值时放行(不能因此把工具卡死)。
    """
    try:
        values = knowledge_loader.ip_parameter_values(instance, name_prefix)
    except ToolError:
        return
    if values and value not in values:
        raise ToolError(
            "BAD_ARGUMENT",
            f"{what} 取值 '{value}' 不在 CubeMX 允许的列表里;可用: " + "、".join(values),
        )


def _check_adc_trigger(value: str, prefix: str) -> None:
    """校验 ADC 外部触发取值:必须是 CubeMX db 里 ExternalTrigConv 的合法值之一。

    猜错时的表现最坑 —— CubeMX 对不认识的枚举**静默忽略**(不报错,但硬件永不转换),
    所以这里直接对着 knowledge/ip/ 导出的取值表拦下来,并把合法值列给调用方。
    """
    try:
        values = knowledge_loader.ip_parameter_values("ADC1", "ExternalTrigConv")
    except ToolError:
        return  # 知识库里查不到就不拦(不能因为这个把工具卡死)
    if not values or value in values:
        return
    shorts = "、".join(v.removeprefix(prefix) if v.startswith(prefix) else v for v in values)
    raise ToolError(
        "BAD_ARGUMENT",
        f"ADC 外部触发取值 '{value}' 不在 CubeMX 允许的取值里;可用(可写简写,工具自动补 {prefix} 前缀):{shorts}",
    )


# 实例名 -> 规则文件名:映射在 knowledge_loader.rule_name_for(**唯一**一份定义)


def default_irq_name(instance: str) -> str:
    """中断向量名:优先取规则文件里的 interrupt_irq,没有就用 {instance}_IRQn。

    F103 上 ADC1/ADC2 共用一个向量(ADC1_2_IRQn),这类"实例名推导不出向量名"的情况
    由 knowledge/rules/<外设>.yaml 的 interrupt_irq 声明,不靠猜。
    """
    rule_name = knowledge_loader.rule_name_for(instance)
    if rule_name:
        try:
            irq = str(knowledge_loader.rule(rule_name).get("interrupt_irq") or "").strip()
        except ToolError:
            irq = ""
        if irq:
            return irq
    return f"{instance}_IRQn"


def interrupt_line(intent: dict) -> str:
    """意图里带 interrupt 时,返回要注入的 NVIC 行(没有就返回空串)。

    interrupt 可以写 true(用规则里的 interrupt_irq,否则 `{实例}_IRQn`)
    或直接写中断名(如 I2C1_EV_IRQn)。
    行的形态取自 knowledge/rules/nvic.yaml —— 实测有效:注入后 generate,
    Core/Src/stm32f1xx_it.c 里会出现对应的 <IRQ>Handler。
    """
    flag = intent.get("interrupt")
    if not flag:
        return ""
    rules = knowledge_loader.require_rule_verified("nvic")
    pattern = (rules.get("ioc_key") or {}).get("pattern", "")
    if not pattern:
        raise ToolError("UNKNOWN_PARAM", "knowledge/rules/nvic.yaml 里没有 ioc_key.pattern")
    instance = str(intent.get("peripheral", "")).strip().upper()
    irq = default_irq_name(instance) if flag is True else str(flag).strip()
    return pattern.replace("{irq}", irq)


def dma_request_name(instance: str, options: dict) -> str:
    """定出 DMA 请求名:显式给的最优先;否则实例名本身就是请求名(ADC1/TIM2 这类)。

    再否则看 "{实例}_*" 是不是只剩一个候选:USART2 有 USART2_RX / USART2_TX 两个,
    必须显式写在 dma.request 里(有歧义就报错,不替用户选)。
    """
    explicit = str(options.get("request", "")).strip().upper()
    if explicit:
        return explicit
    known = knowledge_loader.dma_requests()
    if instance in known:
        return instance
    hits = [r for r in known if r.startswith(f"{instance}_")]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ToolError("BAD_ARGUMENT", f"{instance} 有多个 DMA 请求: {', '.join(hits)};请在 dma.request 里写明要哪个")
    raise ToolError(
        "UNKNOWN_PARAM",
        f"知识库里没有 {instance} 的 DMA 请求(已收录: {', '.join(known[:15])} ...)",
    )


def dma_fields(intent: dict) -> dict | None:
    """把意图里的 dma 子对象翻译成 ioc_editor.inject_dma 的参数;没有 dma 字段返回 None。

    写法(挂在具体外设意图下,和 GUI 里"在该外设上 Add 一个 DMA 请求"是同一件事):
      {"peripheral": "ADC1", "pins": ["PA0"], "dma": {"mode": "circular"}}
      {"peripheral": "USART2", "pins": ["PA2", "PA3"], "dma": {"request": "USART2_RX"}}
      {"peripheral": "ADC1", "pins": ["PA0"], "dma": true}          # 全默认

    字段(默认值而非强制):request / channel / direction / mode / periph_inc / mem_inc /
    periph_align / mem_align / priority / interrupt。
    通道与方向默认查 db 导出的表(knowledge/mcu/<芯片>.dma.yaml),不猜;对齐与优先级留空
    时交给 CubeMX 按请求类型补默认(实测 ADC→HALFWORD、USART→BYTE)。
    """
    raw = intent.get("dma")
    if not raw:
        return None
    options = raw if isinstance(raw, dict) else {}
    instance = str(intent.get("peripheral", "")).strip().upper()
    request = dma_request_name(instance, options)
    values = knowledge_loader.require_rule_verified("dma").get("values") or {}

    def pick(field: str, raw_value) -> str:
        """简写(circular / halfword)与完整枚举名都收;查不到就列可选项报错。"""
        text = str(raw_value or "").strip()
        if not text:
            return ""
        table = values.get(field) or {}
        key = text.lower().replace("-", "_")
        if key in table:
            return str(table[key])
        for enum in table.values():
            if str(enum).lower() == text.lower():
                return str(enum)
        raise ToolError("BAD_ARGUMENT", f"DMA {field} 不支持 {raw_value!r};可选: {', '.join(sorted(table))}")

    channel = str(options.get("channel", "")).strip() or knowledge_loader.dma_channel(request)
    irq = options.get("interrupt", options.get("irq", True))
    return {
        "request": request,
        "channel": channel,
        "direction": pick("direction", options.get("direction")) or knowledge_loader.dma_direction(request),
        "mode": pick("mode", options.get("mode")),
        "periph_inc": pick("periph_inc", options.get("periph_inc")),
        "mem_inc": pick("mem_inc", options.get("mem_inc")),
        "periph_align": pick("periph_align", options.get("periph_align")),
        "mem_align": pick("mem_align", options.get("mem_align")),
        "priority": pick("priority", options.get("priority")),
        "nvic": bool(irq),
    }


def switch_commands(intent: dict, rule_name: str) -> list:
    """给"开/关型"外设(IWDG / WWDG / CAN)生成命令:set mode <实例> <模式名> [set pin ...]。"""
    instance = str(intent.get("peripheral", "")).strip().upper()
    rules = knowledge_loader.require_rule_verified(rule_name)
    modes = rules.get("modes") or {}
    role = str(intent.get("mode", "activate")).strip().lower()
    mode = modes.get(role, "")
    if not mode:
        raise ToolError("UNKNOWN_PARAM", f"{rule_name} 的 mode 要写其中之一: {', '.join(sorted(modes))}")
    # 有的外设模式是"组合"(如 RTC 的 enabled_calendar 要连配两个),值写成列表就展开成多条命令
    commands = [f"set mode {instance} {m}" for m in (mode if isinstance(mode, list) else [mode])]
    pins = intent_pins(intent)
    if pins:
        commands.extend(_pin_commands(instance, pins))
    return commands


def intent_pins(intent: dict) -> list:
    """这个意图会用到哪些引脚(用于冲突检测)。引脚名归一化:PA0 -> PA0-WKUP。"""
    pins = []
    if intent.get("pin"):
        pins.append(str(intent["pin"]).strip())
    pins.extend(str(p).strip() for p in (intent.get("pins") or []) if str(p).strip())
    return [knowledge_loader.resolve_pin_name(p) for p in pins]


def _apply_tim(ioc_path: str, instance: str, intent: dict) -> dict:
    mode = str(intent.get("mode", "")).strip().lower()
    if not mode:
        raise ToolError("BAD_ARGUMENT", f"{instance} 意图需要 mode(如 pwm / input_capture / internal_clock)")
    knowledge_loader.rule_mode("tim", mode)  # 防幻觉闸门:没实测过的模式在这里被拦住
    if mode == "internal_clock":
        params = tim_params(mode, intent)
        # 注入片段自带 NVIC 行;要"只当触发源、不要更新中断"就写 irq: false
        # (写 interrupt: false 同义 —— 那个字段本来就是给 interrupt_line 用的)
        irq = intent.get("irq", intent.get("interrupt", True))
        note = ioc_editor.inject_tim_internal_clock(
            ioc_path, instance, prescaler=params["prescaler"], period=params["period"], irq=bool(irq)
        )
        return {"note": note, "commands": [], "decisions": tim_decisions(instance, mode, intent)}
    if mode not in ("pwm", "input_capture"):
        raise ToolError("UNKNOWN_PARAM", f"意图层还不支持 TIM 的 {mode}(已收录: pwm / input_capture / internal_clock)")
    raw_pin = str(intent.get("pin", "")).strip()
    if not raw_pin:
        raise ToolError("BAD_ARGUMENT", f"{instance} 的 {mode} 需要 pin(如 PA6)")
    pin = knowledge_loader.resolve_pin_name(raw_pin)  # PA0 -> PA0-WKUP 这类别名
    channel = find_channel(instance, pin)
    signal = ioc_signal_name(instance, channel)
    params = tim_params(mode, intent)
    if mode == "pwm":
        note = ioc_editor.inject_tim_pwm(
            ioc_path,
            instance,
            pin,
            signal,
            prescaler=params["prescaler"],
            period=params["period"],
            pulse=params["pulse"],
        )
    else:
        note = ioc_editor.inject_tim_input_capture(
            ioc_path, instance, pin, signal, prescaler=params["prescaler"], period=params["period"]
        )
    return {
        "note": f"{note};引脚 {pin} -> {signal}(通道 {channel} 由引脚表查出)",
        "commands": [],
        "decisions": tim_decisions(instance, mode, intent),
    }


def tim_decisions(instance: str, mode: str, intent: dict) -> list:
    """TIM 意图实际采用的值(含默认与推导) —— 给返回值"自报"用,不参与配置。"""
    params = tim_params(mode, intent)
    clock = tim_clock_mhz()
    if mode == "internal_clock":
        irq = intent.get("irq", intent.get("interrupt", True))
        return [
            f"{instance}: 定时器时钟 {clock}MHz × 分频比 {params['prescaler']} → 计数 {params['period']}"
            f"(寄存器 Prescaler={params['prescaler'] - 1}、Period={params['period'] - 1})",
            f"{instance}: 更新中断{'开(注入 NVIC,会生成 TIMx_IRQHandler)' if irq else '关(只当触发源、不注入 NVIC)'}",
        ]
    return [
        f"{instance}({mode}): 定时器时钟 {clock}MHz,Prescaler={params['prescaler']} / "
        f"Period={params['period']}(寄存器 {params['prescaler'] - 1}/{params['period'] - 1})"
    ]


def adc_decisions(instance: str, intent: dict, ioc_path: str) -> list:
    """ADC 意图实际采用的值:默认的、推导的、工具自动补的,全写出来(省得调用方回读)。"""
    rules = knowledge_loader.require_rule_verified("adc")
    db_default = ((rules.get("parameters_from_db") or {}).get("sampling_time") or {}).get("default", "")
    channels = _adc_channels(instance, intent)
    sampling = str(intent.get("sampling_time", "")).strip() or str(db_default)
    want_scan = intent.get("scan")
    if want_scan is None:
        want_scan = len(channels) > 1
    out = [
        f"{instance}: {len(channels)} 个通道(" + ", ".join(f"IN{c}" for _p, _s, c in channels) + ")",
        f"{instance}: 采样时间 {sampling}" + ("" if intent.get("sampling_time") else "(默认值)"),
        f"{instance}: ContinuousConvMode="
        + ("ENABLE" if intent.get("continuous") else "DISABLE(默认;有外部触发时=每个触发转一轮)"),
        f"{instance}: ScanConvMode=" + ("ENABLE" if want_scan else "DISABLE") + "(多通道默认开)",
    ]
    clock = adc_clock_command(intent, ioc_path)
    out.append(f"{instance}: ADC 时钟 " + (clock.split()[-1] if clock else "(模板已配,未改)"))
    if intent.get("trigger"):
        out.append(f'{instance}: 外部触发 {intent["trigger"]}(触发源 TIM 的 TRGO 要自己配,见 topic="tim")')
    return out


def spi_decisions(instance: str, intent: dict, ioc_path: str) -> list:
    """SPI 意图实际采用的值(含默认与工具自动补的波特率分频)。"""
    chosen, div, fclk = _spi_baudrate(intent, ioc_path)
    baud = knowledge_loader.require_rule_verified("spi").get("baudrate") or {}
    how = "显式指定" if str(intent.get("prescaler", "")).strip() else "按总线频率取满足上限的最小分频"
    rate = f"{int(fclk / div)} Hz" if div else "?"
    return [
        f"{instance}: 波特率分频 {chosen or '(未改,沿用 CubeMX 默认)'}({how}) → {rate}"
        f"(上限 {baud.get('max_hz')} Hz;同时会写 {baud.get('rate_key', 'CalculateBaudRate')} —— "
        "只改预分频的话 CubeMX 会判定该 IP 未就绪)",
        f"{instance}: 模式 {intent.get('mode', 'full_duplex_master')},"
        f"引脚 {', '.join(intent_pins(intent)) or '(未给)'}",
    ]


def dma_decisions(plan: dict) -> list:
    """DMA 请求实际采用的值(通道/方向/模式/通道中断都是工具推导或默认的)。"""
    return [
        f"DMA({plan['request']}) → {plan['channel']}: Mode={plan['mode'] or '(CubeMX 默认)'}, "
        f"Direction={plan['direction']}, 通道中断{'注入(NVIC 里有该通道)' if plan['nvic'] else '不注入'}"
    ]


def apply_one(ioc_path: str, intent: dict) -> dict:
    """执行一个意图,返回 {"note": 说明, "commands": 需要交给 CubeMX 的 set 命令}。"""
    if not isinstance(intent, dict):
        raise ToolError("BAD_ARGUMENT", f"每个意图都要是对象,收到: {intent!r}")
    peripheral = str(intent.get("peripheral", "")).strip().upper()
    if not peripheral:
        raise ToolError("BAD_ARGUMENT", "意图里缺少 peripheral(如 TIM3 / GPIO / I2C1)")
    if re.fullmatch(r"TIM\d+", peripheral):
        return _apply_tim(ioc_path, peripheral, intent)
    if peripheral == "GPIO":
        commands = gpio_commands(intent)
        return {"note": f"GPIO {intent.get('pin')} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
    if re.fullmatch(r"I2C\d+", peripheral):
        commands = i2c_commands(intent)
        return {"note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
    if re.fullmatch(r"USART\d+", peripheral):
        commands = usart_commands(intent)
        return {"note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
    if re.fullmatch(r"SPI\d+", peripheral):
        commands = spi_commands(intent, ioc_path)
        return {
            "note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}",
            "commands": commands,
            "decisions": spi_decisions(peripheral, intent, ioc_path),
        }
    if re.fullmatch(r"ADC\d+", peripheral):
        commands = adc_commands(intent, ioc_path)
        note = f"{peripheral} 用 set 命令配置: {'; '.join(commands)}"
        if intent.get("trigger"):
            note += (
                "\n警告:外部触发要触发源外设自己配好(如 TIM2 的 TRGO 输出);"
                "只设 ADC 的 ExternalTrigConv 会让 generate 卡死到超时(实测)"
            )
        return {
            "note": note,
            "commands": commands,
            "decisions": adc_decisions(peripheral, intent, ioc_path),
        }
    if peripheral in ("IWDG", "WWDG"):
        commands = switch_commands(intent, peripheral.lower())
        return {"note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
    if re.fullmatch(r"CAN\d*", peripheral):
        commands = switch_commands(intent, "can")
        return {"note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
    if peripheral == "RTC":
        commands = switch_commands(intent, "rtc")
        return {"note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
    raise ToolError(
        "UNKNOWN_PERIPHERAL",
        f"意图层还不支持 {peripheral}(目前:TIMx 的 pwm/input_capture/internal_clock,"
        f"以及 GPIO / I2Cx / USARTx / SPIx / ADCx / IWDG / WWDG / CAN / RTC)。"
        f'DMA 要挂到具体外设意图下写(如 {{"peripheral": "ADC1", "pins": ["PA0"], "dma": true}});'
        f"中断在意图里写 interrupt 字段",
    )


def apply_intents(ioc_path: str, intents: list) -> dict:
    """把一组意图落到 .ioc:先查引脚冲突,再逐个执行。

    返回 {"notes", "commands", "ioc_lines", "dma"}:
      commands  —— 要交给 CubeMX 脚本的 set 命令(TIM/中断/DMA 之外的部分);
      ioc_lines —— 直接注入的 .ioc 行(中断 NVIC);
      dma       —— 要交给 ioc_editor.inject_dma 的 DMA 键组计划(每项一个请求)。
    """
    if not intents:
        raise ToolError("BAD_ARGUMENT", "intents 是空的")
    used = ioc_editor.used_pins(ioc_path)
    planned = {}
    for intent in intents:
        if not isinstance(intent, dict):
            raise ToolError("BAD_ARGUMENT", f"每个意图都要是对象,收到: {intent!r}")
        who = str(intent.get("peripheral", "?"))
        for pin in intent_pins(intent):
            if pin in planned:
                raise ToolError("PIN_CONFLICT", f"引脚 {pin} 在这批意图里被用了两次({planned[pin]} 和 {who})")
            if pin in used:
                raise ToolError("PIN_CONFLICT", f"引脚 {pin} 已被占用(当前 .ioc 里是 {used[pin]})")
            planned[pin] = who
    notes = []
    commands = []
    ioc_lines = []
    dma_plans = []
    decisions = []
    for intent in intents:
        result = apply_one(ioc_path, intent)
        notes.append(result["note"])
        commands.extend(result["commands"])
        # 自报"本次采用了哪些默认值/推导值":没有专门解释的外设给一条通用说明,
        # 省得调用方去猜"我没写的那几个字段是什么"(实测:agent 会为此反复回读/追问)
        who = str(intent.get("peripheral", "?")).upper()
        decisions.extend(result.get("decisions") or [f"{who}: 只配引脚/模式,其余参数保持 CubeMX 默认(本工具未改动)"])
        line = interrupt_line(intent)
        if line:
            ioc_lines.append(line)
        plan = dma_fields(intent)
        if plan:
            dma_plans.append(plan)
            decisions.extend(dma_decisions(plan))
            notes.append(
                f"DMA 请求 {plan['request']} → 通道 {plan['channel']}"
                f"({'含' if plan['nvic'] else '不含'}通道中断;注入 .ioc 键组,见 rules/dma.yaml)"
            )
    return {
        "notes": notes,
        "decisions": decisions,
        "commands": commands,
        "ioc_lines": ioc_lines,
        "dma": dma_plans,
    }
