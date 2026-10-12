#!/usr/bin/env python3
"""知识库读取器:把 knowledge/ 里的 YAML 读出来给 MCP 工具用。

knowledge/ 下的文件:
  meta.yaml                        导出信息:芯片型号、每个 IP 用的是哪个 db XML
  mcu/<mcu_name>.pins.yaml         芯片引脚与每个引脚可选的信号(从 CubeMX db 导出)
  ip/<ip>.yaml                     IP 的模式名与参数(从 CubeMX db 导出)
  rules/<peripheral>.yaml          实测出来的规则:命令写法、.ioc 片段、已知坑

两条原则:
  1. 查不到就报错(带错误码),不猜;
  2. 规则没实测过(顶层 verified 不是 true)就拒绝执行 —— 防幻觉闸门。

知识库目录默认与本模块同级,可用环境变量 ST_CUBEMX_KNOWLEDGE 覆盖。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

from .cubemx_result import ToolError

_CACHE: dict = {}
_CACHE_STAMP: dict = {}


def knowledge_dir() -> Path:
    """知识库目录。优先级:环境变量 ST_CUBEMX_KNOWLEDGE > 源码目录 > 安装位置。

    安装位置和 templates/ 一样有几种可能(见 cubemx_mcp._template_search_dirs):
    模块同级、包目录的上一级(src 布局下即仓库根)、再上一级、以及 pip 装 wheel 时的 sys.prefix。
    """
    raw = os.environ.get("ST_CUBEMX_KNOWLEDGE", "").strip()
    if raw:
        return Path(raw)
    here = Path(__file__).resolve().parent
    for candidate in (
        here / "knowledge",
        here.parent / "knowledge",
        here.parent.parent / "knowledge",
        Path(sys.prefix) / "knowledge",
    ):
        if candidate.is_dir():
            return candidate
    return here / "knowledge"  # 都不存在时返回源码位置,报错信息里能看到期望的路径


def reload() -> None:
    """清掉缓存,下次读文件重新加载(改了知识库后调它)。"""
    _CACHE.clear()
    _CACHE_STAMP.clear()


def _load(name: str):
    """读 knowledge/ 下的一个 YAML;不存在返回 None。

    缓存以文件 mtime 为准:直接改 knowledge/*.yaml(不重启 server)也能立刻生效 ——
    以前是纯内存缓存,改了 YAML 只有重启进程才看得到(实测踩过:help 一直显示旧文本)。
    """
    path = knowledge_dir() / name
    key = str(path)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = None
    if key not in _CACHE or _CACHE_STAMP.get(key) != stamp:
        if not path.is_file():
            _CACHE[key] = None
        else:
            with path.open(encoding="utf-8") as f:
                _CACHE[key] = yaml.safe_load(f) or {}
        _CACHE_STAMP[key] = stamp
    return _CACHE[key]


def meta() -> dict:
    """读 meta.yaml(导出信息)。"""
    data = _load("meta.yaml")
    if data is None:
        raise ToolError(
            "UNKNOWN_PERIPHERAL", f"找不到 {knowledge_dir() / 'meta.yaml'};请先跑 tools/gen_knowledge_from_db.py"
        )
    return data


def require_verified(data: dict, what: str) -> dict:
    """检查规则是不是实测过的;没过就拒绝执行(防幻觉闸门)。"""
    if not data.get("verified"):
        source = data.get("source", "(没写来源)")
        raise ToolError(
            "RULE_NOT_VERIFIED",
            f"{what} 的规则还没实测验证,拒绝执行。来源标记: {source}",
        )
    return data


# ------------------------------------------------------------------ 规则(rules/)


def rule(peripheral: str) -> dict:
    """读某个外设的规则,如 gpio / i2c / tim / rcc / usart。"""
    name = peripheral.strip().lower()
    data = _load(f"rules/{name}.yaml")
    if data is None:
        known = sorted(p.stem for p in (knowledge_dir() / "rules").glob("*.yaml"))
        raise ToolError(
            "UNKNOWN_PERIPHERAL",
            f"知识库里没有 {peripheral} 的规则(找的是 rules/{name}.yaml)。已收录: {', '.join(known) or '(空)'}",
        )
    return data


def rule_mode(peripheral: str, mode: str) -> dict:
    """读某个外设的某个模式,如 (tim, pwm) / (tim, internal_clock);没实测过会拒绝。"""
    data = rule(peripheral)
    modes = data.get("modes") or {}
    item = modes.get(mode)
    if item is None:
        raise ToolError(
            "UNKNOWN_PARAM",
            f"{peripheral} 没有模式 {mode}(可用模式: {', '.join(sorted(modes)) or '(无)'})",
        )
    return require_verified(item, f"{peripheral}.{mode}")


def require_rule_verified(peripheral: str) -> dict:
    """读外设规则并确认整份规则是实测过的。"""
    return require_verified(rule(peripheral), peripheral)


# 实例名 -> 规则文件名(ADC1 -> adc、TIM3 -> tim);移除/中断查找都要用
_RULE_PATTERNS = (
    (re.compile(r"ADC\d+"), "adc"),
    (re.compile(r"USART\d+"), "usart"),
    (re.compile(r"UART\d+"), "usart"),
    (re.compile(r"SPI\d+"), "spi"),
    (re.compile(r"I2C\d+"), "i2c"),
    (re.compile(r"TIM\d+"), "tim"),
    (re.compile(r"CAN\d*"), "can"),
    (re.compile(r"RTC"), "rtc"),
    (re.compile(r"GPIO"), "gpio"),
    (re.compile(r"IWDG"), "iwdg"),
    (re.compile(r"WWDG"), "wwdg"),
)


def rule_name_for(instance: str) -> str:
    """实例名 -> 规则文件名(如 ADC1 -> adc / TIM2 -> tim);没有对应规则时返回空串。"""
    name = str(instance).strip().upper()
    for pattern, rule_name in _RULE_PATTERNS:
        if pattern.fullmatch(name):
            return rule_name
    return ""


# ------------------------------------------------------------------ 芯片(mcu/)


def _mcu_name_checked(mcu: str = "") -> str:
    """校验请求的芯片和知识库导出的那颗一致,返回 mcu_name(用于定位 mcu/*.yaml)。

    mcu 留空表示"就用当前知识库那颗";给了别的型号直接报错(并提示怎么导出新芯片)。
    """
    info = meta()
    want = (mcu or "").strip().upper()
    have = str(info.get("mcu", "")).strip().upper()
    if want and have and want != have:
        raise ToolError(
            "MCU_TEMPLATE_MISSING",
            f"knowledge/ 里只有 {have} 的数据;要 {want} 请先跑 tools/gen_knowledge_from_db.py --mcu {want}",
        )
    return str(info.get("mcu_name", ""))


def mcu_pins(mcu: str = "") -> dict:
    """读芯片的引脚表。mcu 传型号(如 STM32F103C8T6);留空则用 meta 里记录的那颗。"""
    name = _mcu_name_checked(mcu)
    data = _load(f"mcu/{name}.pins.yaml")
    if data is None:
        raise ToolError("MCU_TEMPLATE_MISSING", f"找不到 mcu/{name}.pins.yaml;请先跑 tools/gen_knowledge_from_db.py")
    return data


def resolve_pin_name(pin: str, mcu: str = "") -> str:
    """把用户写的引脚名归一化成引脚表里的键(不猜:有歧义就报错)。

    三级匹配:① 精确 ② 忽略大小写 ③ 唯一前缀别名(PA0 -> PA0-WKUP、PB4 -> PB4-NJTRST)。
    这样意图里可以写硬件图上常见的短名(PA0),不必迁就 CubeMX 的长名。
    """
    pins = mcu_pins(mcu).get("pins") or {}
    name = str(pin).strip()
    if name in pins:
        return name
    upper = name.upper()
    if upper in pins:
        return upper
    hits = sorted(k for k in pins if k.upper().startswith(upper + "-"))
    examples = ", ".join(sorted(pins)[:12])
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ToolError(
            "UNKNOWN_PIN_SIGNAL",
            f"{name} 有多个候选引脚: {', '.join(hits)};请写全名。引脚名示例: {examples} ...",
        )
    raise ToolError("UNKNOWN_PIN_SIGNAL", f"{name} 不在这颗芯片的引脚表里。引脚名示例: {examples} ...")


def pin_signals(pin: str, mcu: str = "") -> list:
    """查某个引脚支持哪些信号(名字来自 CubeMX db,如 PA6 -> [..., TIM3_CH1, ...])。

    引脚名支持别名(PA0 -> PA0-WKUP)与大小写,见 resolve_pin_name。
    """
    key = resolve_pin_name(pin, mcu)
    pins = mcu_pins(mcu).get("pins") or {}
    return list((pins.get(key) or {}).get("signals") or [])


# ------------------------------------------------------------------ DMA 请求 -> 通道(mcu/)


def dma_channels(mcu: str = "") -> dict:
    """读芯片的 DMA 表(mcu/<mcu_name>.dma.yaml)。

    结构:channels(请求 -> 通道)+ directions(请求 -> db 里登记的方向)。数据来自
    CubeMX db 的 DMA Modes XML(ModeLogicOperator 树 + RefMode),由
    tools/gen_knowledge_from_db.py 导出;没导出过就报错(通道一律查表,不靠猜)。
    """
    name = _mcu_name_checked(mcu)
    data = _load(f"mcu/{name}.dma.yaml")
    if data is None:
        raise ToolError(
            "MCU_TEMPLATE_MISSING",
            f"找不到 mcu/{name}.dma.yaml(DMA 请求表);请重新跑 tools/gen_knowledge_from_db.py",
        )
    return data


def dma_requests(mcu: str = "") -> list:
    """这颗芯片上可用的 DMA 请求名(如 ADC1 / USART2_RX / SPI1_TX),查不到通道时给提示用。"""
    return sorted(dma_channels(mcu).get("channels") or {})


def dma_channel(request: str, mcu: str = "") -> str:
    """查某个 DMA 请求用哪个通道(如 ADC1 -> DMA1_Channel1);查不到就报错,不猜。

    一个请求对应多个通道时(非 F1 芯片可能出现)拒绝自动选择,要求显式指定。
    """
    req = str(request).strip().upper()
    channels = dma_channels(mcu).get("channels") or {}
    hit = channels.get(req)
    if not hit:
        known = ", ".join(sorted(channels)[:15])
        raise ToolError(
            "UNKNOWN_PARAM",
            f"知识库里没有 DMA 请求 {request!r} 的通道。已知请求: {known}{' ...' if len(channels) > 15 else ''}",
        )
    if isinstance(hit, list):
        raise ToolError(
            "UNKNOWN_PARAM",
            f"DMA 请求 {req} 有多个候选通道: {', '.join(str(x) for x in hit)};请显式指定 channel",
        )
    return str(hit)


def dma_direction(request: str, mcu: str = "") -> str:
    """查某个 DMA 请求在 db 里登记的方向(如 ADC1 -> DMA_PERIPH_TO_MEMORY);没登记返回空串。"""
    req = str(request).strip().upper()
    return str((dma_channels(mcu).get("directions") or {}).get(req) or "")


# ------------------------------------------------------------------ db 事实(ip/)


def ip_facts(instance: str) -> dict:
    """按实例名(如 TIM3 / USART2 / GPIO)读 CubeMX db 导出的模式与参数。"""
    info = meta()
    xml = (info.get("source_ip_xml") or {}).get(instance)
    if not xml:
        known = ", ".join(sorted(info.get("source_ip_xml") or {}))
        raise ToolError("UNKNOWN_PERIPHERAL", f"知识库里没有 {instance} 的 db 数据。已有: {known}")
    data = _load(f"ip/{Path(xml).stem}.yaml")
    if data is None:
        raise ToolError(
            "UNKNOWN_PERIPHERAL", f"找不到 ip/{Path(xml).stem}.yaml;请重新跑 tools/gen_knowledge_from_db.py"
        )
    return data


def ip_parameter_values(instance: str, name_prefix: str) -> list:
    """某 IP 下名字以 name_prefix 开头的参数的**合法取值**(多份定义取并集,去重保序)。

    例:`ip_parameter_values("ADC1", "SamplingTime")` → 8 个 `ADC_SAMPLETIME_*`。
    CubeMX 对不认识的枚举是**静默忽略**(不报错、配置无效),所以这类值要在改 .ioc 前校验。
    """
    params = ip_facts(instance).get("parameters") or {}
    out: list = []
    for name, item in params.items():
        if not name.startswith(name_prefix):
            continue
        for value in (item or {}).get("values") or []:
            if value not in out:
                out.append(value)
    return out


def ip_mode_names(instance: str) -> list:
    """这个 IP 在 CubeMX db 里定义的合法模式名(用来核对 .ioc 片段里写的枚举名)。"""
    return [m.get("name", "") for m in ip_facts(instance).get("modes") or []]


def ip_parameter(instance: str, name: str) -> dict:
    """这个 IP 的某个参数(默认值/范围/单位),来自 CubeMX db。"""
    params = ip_facts(instance).get("parameters") or {}
    item = params.get(name)
    if item is None:
        raise ToolError(
            "UNKNOWN_PARAM",
            f"{instance} 没有参数 {name}(已知参数: {', '.join(sorted(params)[:15])} ...)",
        )
    return item


# ------------------------------------------------------------------ help 文本

# topic 别名 -> help.yaml 里的位置
_HELP_ALIAS = {"clock": "rcc"}

# help.yaml 里的元数据键(不是 topic)
_HELP_META_KEYS = {"source", "guide", "peripheral"}


def help_text(topic: str = "") -> str | None:
    """取 cubemx_help 的文本;不是静态文本的 topic(如 templates)返回 None。"""
    data = _load("rules/help.yaml")
    if data is None:
        return None
    key = _HELP_ALIAS.get(topic.strip().lower(), topic.strip().lower())
    if not key:
        return data.get("guide")
    if key in (data.get("peripheral") or {}):
        return data["peripheral"][key]
    return data.get(key)


def help_topics() -> list:
    """cubemx_help 能回答的 topic(从 help.yaml 实际存在的键算出来,不硬编码)。

    peripheral: 下的键是外设主题,顶层非元数据的键是流程主题(guide 本身不算),
    别名(clock -> rcc)也算可用 topic。
    """
    data = _load("rules/help.yaml") or {}
    names = set((data.get("peripheral") or {}).keys())
    names |= {k for k in data if k not in _HELP_META_KEYS}
    names |= {alias for alias, target in _HELP_ALIAS.items() if target in names}
    return sorted(names)


# ------------------------------------------------------------------ 片段渲染

_VALUE_PLACEHOLDER = re.compile(r"\{v\.([A-Za-z0-9_]+)\}")


def render_fragment(fragment: str, values: dict | None = None, **params) -> str:
    """把规则文件里的 .ioc 片段渲染成实际文本。

    两类占位符:
      {v.key}  规则文件 values: 里的常量(常量值里若还带 {n} 这类占位符,会一起展开)
      {name}   调用方给的参数,如 tim / pin / n / prescaler / period / pulse / signal
    占位符对不上就报错,不会静默留空。
    """
    table = values or {}
    text = _VALUE_PLACEHOLDER.sub(lambda m: str(table.get(m.group(1), m.group(0))), fragment)
    try:
        return text.format(**params)
    except KeyError as exc:
        hint = ", ".join(sorted(params)) or "(无)"
        raise ToolError("BAD_ARGUMENT", f"片段里有没提供的占位符 {exc};当前可用的: {hint}") from exc
    except (IndexError, ValueError) as exc:
        raise ToolError("BAD_ARGUMENT", f"片段渲染失败: {exc}") from exc
