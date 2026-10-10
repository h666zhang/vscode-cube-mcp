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

import ioc_editor
import knowledge_loader
from cubemx_result import ToolError

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
    """GPIO 走 CubeMX 的 set 命令(不需要注入 .ioc 片段)。"""
    pin = str(intent.get("pin", "")).strip()
    if not pin:
        raise ToolError("BAD_ARGUMENT", "GPIO 意图需要 pin(如 PB13)")
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


def spi_commands(intent: dict) -> list:
    """SPI 走 set 命令(实测:set mode SPI1 Full_Duplex_Master 有效)。

    模式名用下划线形式,可选值见 knowledge/rules/spi.yaml 的 modes。
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
    return commands


def adc_commands(intent: dict) -> list:
    """ADC 走 set 命令(实测:set mode ADC1 IN0 + set pin PA0-WKUP ADC1_IN0 有效)。

    通道号从引脚表查:PA0-WKUP 支持 ADC1_IN0 -> 通道 0 -> 模式名 IN0。
    """
    instance = str(intent.get("peripheral", "")).strip().upper()
    knowledge_loader.require_rule_verified("adc")
    pin = str(intent.get("pin", "")).strip()
    if not pin:
        raise ToolError("BAD_ARGUMENT", f"{instance} 意图需要 pin(如 PA0-WKUP)")
    signals = knowledge_loader.pin_signals(pin)
    hits = [s for s in signals if s.startswith(f"{instance}_IN")]
    if not hits:
        raise ToolError("UNKNOWN_PIN_SIGNAL", f"{pin} 上没有 {instance} 的输入通道;这个引脚支持: {', '.join(signals)}")
    channel = hits[0].rsplit("_IN", 1)[-1]
    return [f"set mode {instance} IN{channel}", f"set pin {pin} {hits[0]}"]


def interrupt_line(intent: dict) -> str:
    """意图里带 interrupt 时,返回要注入的 NVIC 行(没有就返回空串)。

    interrupt 可以写 true(用 `{实例}_IRQn`)或直接写中断名(如 I2C1_EV_IRQn)。
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
    irq = f"{instance}_IRQn" if flag is True else str(flag).strip()
    return pattern.replace("{irq}", irq)


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
    """这个意图会用到哪些引脚(用于冲突检测)。"""
    pins = []
    if intent.get("pin"):
        pins.append(str(intent["pin"]).strip())
    pins.extend(str(p).strip() for p in (intent.get("pins") or []) if str(p).strip())
    return pins


def _apply_tim(ioc_path: str, instance: str, intent: dict) -> dict:
    mode = str(intent.get("mode", "")).strip().lower()
    if not mode:
        raise ToolError("BAD_ARGUMENT", f"{instance} 意图需要 mode(如 pwm / input_capture / internal_clock)")
    knowledge_loader.rule_mode("tim", mode)  # 防幻觉闸门:没实测过的模式在这里被拦住
    if mode == "internal_clock":
        params = tim_params(mode, intent)
        note = ioc_editor.inject_tim_internal_clock(
            ioc_path, instance, prescaler=params["prescaler"], period=params["period"]
        )
        return {"note": note, "commands": []}
    if mode not in ("pwm", "input_capture"):
        raise ToolError("UNKNOWN_PARAM", f"意图层还不支持 TIM 的 {mode}(已收录: pwm / input_capture / internal_clock)")
    pin = str(intent.get("pin", "")).strip()
    if not pin:
        raise ToolError("BAD_ARGUMENT", f"{instance} 的 {mode} 需要 pin(如 PA6)")
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
    return {"note": f"{note};引脚 {pin} -> {signal}(通道 {channel} 由引脚表查出)", "commands": []}


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
        commands = spi_commands(intent)
        return {"note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
    if re.fullmatch(r"ADC\d+", peripheral):
        commands = adc_commands(intent)
        return {"note": f"{peripheral} 用 set 命令配置: {'; '.join(commands)}", "commands": commands}
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
        f"DMA 脚本配不了(见 knowledge/rules/dma.yaml);中断要在意图里写 interrupt 字段",
    )


def apply_intents(ioc_path: str, intents: list) -> dict:
    """把一组意图落到 .ioc:先查引脚冲突,再逐个执行。

    返回 {"notes": [每条说明...], "commands": [要交给 CubeMX 脚本的 set 命令...]}。
    TIM 是就地注入 .ioc;GPIO / I2C / USART 返回命令,由上层拼进 config load / saveas 里跑。
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
    for intent in intents:
        result = apply_one(ioc_path, intent)
        notes.append(result["note"])
        commands.extend(result["commands"])
        line = interrupt_line(intent)
        if line:
            ioc_lines.append(line)
    return {"notes": notes, "commands": commands, "ioc_lines": ioc_lines}
