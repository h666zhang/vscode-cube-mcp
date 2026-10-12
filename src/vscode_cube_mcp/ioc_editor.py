#!/usr/bin/env python3
"""STM32CubeMX .ioc 文件的文本编辑:纯文本操作,不起 CubeMX 进程。

为什么单独一层:
  - 这里只做确定性的文本改(增删行、重排 Mcu.IPx/Mcu.Pinx、补参数块);
  - 事实(枚举名、.ioc 片段)来自 knowledge/,不写在这里;
  - 上层 cubemx_mcp.py 只负责参数校验、编排和结果渲染。

对外函数:
  patch_ioc_identity(...)         复制模板并改写工程标识与时钟
  inject_tim_internal_clock(...)  把 TIM 配成内部时钟(片段注入)
  inject_tim_pwm(...)             把 TIM 配成 PWM 输出(片段注入)
  inject_tim_input_capture(...)   把 TIM 配成输入捕获(片段注入)
  inject_dma(...)                 给某个 DMA 请求注入 .ioc 键组(等价 GUI 的 DMA Settings→Add)
  remove_peripheral(...)          从 .ioc 移除一个外设
"""

from __future__ import annotations

import os
import re
import shutil

from . import ioc_diff, knowledge_loader
from .cubemx_result import ToolError


# ---------------------------------------------------------------- 写盘:备份 + 回读校验
def backup(ioc_path: str) -> str:
    """写回之前把当前内容备份成 <file>.bak(只留最近一份,出问题可手工回退)。"""
    bak = ioc_path + ".bak"
    shutil.copy2(ioc_path, bak)
    return bak


def verify_written(ioc_path: str, expected_lines) -> None:
    """回读文件,确认 expected_lines 的每一行都真的写进去了(按整行比较,忽略首尾空白)。

    对不上抛 READBACK_MISMATCH:说明内容没落盘,或者这种写法 CubeMX 认不了。
    """
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        have = {ln.strip() for ln in f.read().splitlines()}
    missing = [ln.strip() for ln in expected_lines if ln.strip() and ln.strip() not in have]
    if missing:
        shown = "\n".join(f"  {ln}" for ln in missing[:10])
        raise ToolError("READBACK_MISMATCH", f"写入 {ioc_path} 后回读,有 {len(missing)} 行没找到:\n{shown}")


# ------------------------------------------- 结构校验(按 rules/ioc_structure.yaml)
def check_structure(ioc_path: str) -> list:
    """按 knowledge/rules/ioc_structure.yaml 校验 .ioc 结构,返回问题描述(空 = 没问题)。

    检查 IP / Pin 列表:编号必须从 0 连续,且计数键与实际条目数一致 —— 这两项正是 CubeMX
    读列表的依据,编号有洞或计数对不上,它读到的外设/引脚就是错的(规则文件里写着这条)。
    """
    rules = knowledge_loader.rule("ioc_structure")
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        parsed = ioc_diff.parse_ioc(f.read())
    problems = []
    for spec in (rules.get("entries") or {}).values():
        pattern = str(spec.get("key_pattern") or "")
        count_key = str(spec.get("count_key") or "")
        if "{N}" not in pattern or not count_key:
            continue
        prefix = pattern.split("{N}", 1)[0]  # "Mcu.IP{N}={instance}" -> "Mcu.IP"
        idx = sorted(int(k[len(prefix) :]) for k in parsed if re.fullmatch(rf"{re.escape(prefix)}\d+", k))
        if idx != list(range(len(idx))):
            problems.append(f"{prefix} 编号不连续: {idx}")
        raw = str(parsed.get(count_key, ""))
        if raw.isdigit() and int(raw) != len(idx):
            problems.append(f"{count_key}={raw} 与实际 {len(idx)} 条不符")
    return problems


def _rule_data(rule_name: str) -> dict:
    """按规则名读 knowledge/rules/<name>.yaml;读不到返回空 dict(预检不该因为缺规则就炸)。"""
    try:
        return knowledge_loader.rule(rule_name) or {}
    except ToolError:
        return {}


def check_semantics(ioc_path: str) -> list:
    """离线语义预检:抓那些"配错了但 CubeMX 不报错"的组合问题(只读、零 CubeMX 启动)。

    为什么需要它:CubeMX 对很多错配是**静默**的 —— 例如 ADC 设了外部触发、触发源 TIM 却
    没配 TRGO 输出,generate 照样 exit=0,但硬件上永远不转换。这类问题纯文本就能判定,
    所以不必每次都起 CubeMX(启动一次 10~30s)。

    阈值与枚举名全部取自 knowledge/rules/*.yaml(adc 的 trigger_prefix/clock、dma 的
    ioc_keys、tim 的 precheck.master_output_for_trigger),不在这里硬编码。
    返回问题描述列表(空 = 没发现问题)。
    """
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        entries = ioc_diff.parse_ioc(f.read())
    ips = {v for k, v in entries.items() if re.fullmatch(r"Mcu\.IP\d+", k)}
    problems = []

    # ① ADC 用外部触发 → 触发源 TIM 必须存在,且它的 TRGO 必须是 UPDATE
    adc_rules = _rule_data("adc")
    want_trgo = str((_rule_data("tim").get("precheck") or {}).get("master_output_for_trigger") or "TIM_TRGO_UPDATE")
    trigger_prefix = str(adc_rules.get("trigger_prefix") or "ADC_EXTERNALTRIGCONV_")
    software_start = str(adc_rules.get("software_start") or "ADC_SOFTWARE_START")
    for key, value in sorted(entries.items()):
        if not key.endswith(".ExternalTrigConv") or value == software_start:
            continue
        adc_name = key.split(".", 1)[0]
        hit = re.match(rf"{re.escape(trigger_prefix)}T(\d+)_", value)
        if not hit:
            continue
        tim = f"TIM{hit.group(1)}"
        if tim not in ips:
            problems.append(f"{adc_name} 用外部触发 {value},但 {tim} 不在 Mcu.IPx 外设列表里")
            continue
        master = entries.get(f"{tim}.TIM_MasterOutputTrigger", "")
        if master != want_trgo:
            problems.append(
                f"{adc_name} 靠 {tim} 的 TRGO 触发,但 {tim}.TIM_MasterOutputTrigger="
                f"{master or '(未设)'}(应为 {want_trgo})—— generate 不报错,但硬件不会触发"
            )

    # ② 配了 ADC → 必须有合法 ADC 时钟(不设会让 generate 弹 [Clock] 卡死)
    clock = adc_rules.get("clock") or {}
    adc_ips = sorted(n for n in ips if n.startswith("ADC"))
    if adc_ips and clock:
        max_hz = int(clock.get("max_hz") or 0)
        presc = entries.get(f"RCC.{clock.get('parameter', 'ADCPresc')}", "")
        try:
            pclk2 = int(entries.get(clock.get("pclk2_key", ""), "") or clock.get("pclk2_default_hz", 72000000))
        except ValueError:
            pclk2 = int(clock.get("pclk2_default_hz", 72000000))
        div = int((clock.get("prescalers") or {}).get(presc, 0) or 0)
        try:
            actual = int(entries.get("RCC.ADCFreqValue") or 0)
        except ValueError:
            actual = 0
        # 优先按 ADCPresc 现算;没有该行(值等于库默认值时 CubeMX 不写它)就退回 CubeMX 自己算的
        # ADCFreqValue —— 只看"ADCPresc 在不在"会把"用了默认分频"误报成"没设"
        # (实测踩过:ADCPresc=DIV2 时文件里明明写着 ADCFreqValue=36MHz,预检却说没设,既误报
        #  又把真正的超限漏掉了)
        adcclk = int(pclk2 / div) if div else actual
        if not adcclk:
            problems.append(f"配了 {','.join(adc_ips)} 但没设 RCC.ADCPresc(generate 会弹 [Clock] 卡死)")
        elif max_hz and adcclk > max_hz:
            how = presc or "默认分频(取自 RCC.ADCFreqValue)"
            problems.append(f"ADCCLK = {adcclk} Hz 超过上限 {max_hz} Hz({how};generate 会弹 [Clock] 卡死)")

    # ③ DMA 键组要自洽:外设列表里有 DMA、请求序号对得上、通道中断已使能
    dma_keys = _rule_data("dma").get("ioc_keys") or {}
    ip_entry = str(dma_keys.get("ip_list_entry") or "DMA")
    for key, value in sorted(entries.items()):
        # 注意:parse_ioc 已经把 key 与 value 分开,所以这里匹配的是 key(不带 "=值")
        hit = re.match(r"Dma\.([A-Z0-9_]+)\.(\d+)\.Instance$", key)
        if not hit:
            continue
        request, index, channel = hit.group(1), hit.group(2), value
        if ip_entry not in ips:
            problems.append(f"有 DMA 请求 {request},但 Mcu.IPx 里没有 {ip_entry}(saveas 时可能被当孤儿键丢掉)")
        declared = entries.get(f"Dma.Request{index}", "")
        if declared != request:
            problems.append(f"Dma.{request}.{index}.* 的序号与 Dma.Request{index}={declared or '(缺)'} 对不上")
        if channel and f"NVIC.{channel}_IRQn" not in entries:
            problems.append(f"{request} 用了 {channel},但没使能 NVIC.{channel}_IRQn(DMA 完成回调不会进)")

    # ④ 外设配上了,但 .ioc 的"初始化顺序表"里没有它的 MX_*_Init 段
    #    典型成因:"移除该外设 + 重新加回" —— 移除会删掉那段,加回走 set 命令不会补回;
    #    generate 时 CubeMX 会自己重建,所以**生成的代码不受影响**,但 cubemx_load 摘要的
    #    init_order 会暂时少一项。提示一句,免得只看摘要的人以为"这外设没配上"。
    functionlist = entries.get("ProjectManager.functionlistsort", "")
    for name in sorted(ips - {"RCC", "SYS", "NVIC"}):
        if functionlist and f"-{name}-" not in functionlist:
            problems.append(
                f"{name} 已在 Mcu.IPx 里,但 ProjectManager.functionlistsort 里没有它的 MX_*_Init 段"
                "(常见于'移除外设后又加回';generate 时 CubeMX 会自己补回,生成的代码不受影响 —— "
                "但 cubemx_load 摘要的 init_order 会暂时少这一项)"
            )
    return problems


def _verify_after_write(ioc_path: str, expected_lines, baseline: list) -> None:
    """写盘后的统一收尾:回读校验 + 结构校验(只报"这次新引入的"结构问题)。

    baseline 是写盘前的 check_structure 结果 —— 用户 .ioc 本来就有的结构问题不算在工具头上,
    工具只保证自己不会把结构改坏。
    """
    verify_written(ioc_path, expected_lines)
    new_problems = [p for p in check_structure(ioc_path) if p not in baseline]
    if new_problems:
        shown = "\n".join(f"  {p}" for p in new_problems)
        raise ToolError("STRUCTURE_MISMATCH", f"写入 {ioc_path} 后新增了结构问题:\n{shown}")


def used_pins(ioc_path: str) -> dict:
    """读 .ioc,返回 {引脚名: 信号} —— 给引脚冲突检测用。"""
    out = {}
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            hit = re.match(r"^([A-Za-z0-9_\-]+)\.Signal=(.+)$", line.strip())
            if hit:
                out[hit.group(1)] = hit.group(2)
    return out


def read_key(ioc_path: str, key: str) -> str:
    """读 .ioc 里某个键的原始值(如 RCC.APB2Freq_Value);没有该键或文件不可读时返回空串。"""
    if not ioc_path or not key:
        return ""
    try:
        with open(ioc_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                head, sep, value = line.strip().partition("=")
                if sep and head == key:
                    return value
    except OSError:
        return ""
    return ""


def summarize(ioc_path: str) -> dict:
    """把 .ioc 解析成结构化摘要(只读,不改文件)。

    返回:project / mcu / clock / ips / pins / virtual_pins / dma / nvic / peripherals / init_order。
    值一律是 .ioc 里的**原始字符串**(不做语义解释、不查知识库)—— "这样写合不合法"
    由 knowledge/ 回答,这里只负责把现状讲清楚,省得 agent 去读整个 .ioc 原文。
    """
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        entries = ioc_diff.parse_ioc(f.read())

    ips = [v for k, v in sorted(entries.items()) if re.fullmatch(r"Mcu\.IP\d+", k)]
    pins = {}
    virtual_pins = {}
    for k, v in entries.items():
        if not k.endswith(".Signal"):
            continue
        pin = k[: -len(".Signal")]
        (virtual_pins if pin.startswith("VP_") else pins)[pin] = v

    dma_configs = {}
    for k, v in sorted(entries.items()):
        hit = re.match(r"Dma\.([A-Z0-9_]+)\.\d+\.(\w+)$", k)
        if hit:
            dma_configs.setdefault(hit.group(1), {})[hit.group(2)] = v
    dma_requests = [
        {"index": int(k[len("Dma.Request") :]), "name": v}
        for k, v in sorted(entries.items(), key=lambda kv: kv[0])
        if re.fullmatch(r"Dma\.Request\d+", k)
    ]

    nvic = {k.split(".", 1)[1]: v for k, v in entries.items() if k.startswith("NVIC.") and k != "NVIC.PriorityGroup"}
    clock_keys = (
        "RCC.PLLSourceVirtual",
        "RCC.PLLMUL",
        "RCC.SYSCLKSource",
        "RCC.SYSCLKFreq_VALUE",
        "RCC.HCLKFreq_Value",
        "RCC.APB1CLKDivider",
        "RCC.APB1Freq_Value",
        "RCC.APB2Freq_Value",
        "RCC.ADCPresc",
        "RCC.ADCFreqValue",
    )
    clock = {k.split(".", 1)[1]: entries[k] for k in clock_keys if k in entries}

    peripherals = {}
    ip_names = set(ips)
    for k, v in sorted(entries.items()):
        head, sep, name = k.partition(".")
        if not sep or head not in ip_names or head in ("RCC", "NVIC", "SYS"):
            continue
        # IPParameters 的值是一长串参数名,拆成列表更好读;其它参数原样
        peripherals.setdefault(head, {})[name] = [p for p in v.split(",") if p] if name == "IPParameters" else v

    init_order = [
        seg.split("-")[1]
        for seg in entries.get("ProjectManager.functionlistsort", "").split(",")
        if seg.strip() and len(seg.split("-")) > 1
    ]
    return {
        "project": {
            "name": entries.get("ProjectManager.ProjectName", ""),
            "toolchain": entries.get("ProjectManager.TargetToolchain", ""),
            "couple_files": entries.get("ProjectManager.CoupleFile", ""),
        },
        "mcu": {
            "cpn": entries.get("Mcu.CPN", ""),
            "name": entries.get("Mcu.Name", ""),
            "family": entries.get("Mcu.Family", ""),
            "package": entries.get("Mcu.Package", ""),
            "cube_version": entries.get("MxCube.Version", ""),
        },
        "clock": clock,
        "ips": ips,
        "pins": pins,
        "virtual_pins": virtual_pins,
        "dma": {
            "requests_nb": entries.get("Dma.RequestsNb", ""),
            "requests": dma_requests,
            "configs": dma_configs,
        },
        "nvic": nvic,
        "peripherals": peripherals,
        "init_order": init_order,
        # 离线预检:配错了但 CubeMX 不报错的组合问题(纯文本判定,不启动 CubeMX)
        "precheck": check_semantics(ioc_path),
    }


def add_lines(ioc_path: str, lines) -> list:
    """把若干 .ioc 行插到 board=custom 之前(锚点和 TIM 注入一致)。

    已经存在的行跳过(幂等);写前备份、写后回读校验。返回真正新增的行。
    """
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    have = {ln.strip() for ln in text.splitlines()}
    wanted = [ln for ln in lines if ln.strip() and ln.strip() not in have]
    if not wanted:
        return []
    block = "".join(ln if ln.endswith("\n") else ln + "\n" for ln in wanted)
    if "board=custom" in text:
        text = text.replace("board=custom", block + "board=custom", 1)
    else:
        text += block
    baseline = check_structure(ioc_path)
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    _verify_after_write(ioc_path, wanted, baseline)
    return wanted


def _ensure_ip_param(text: str, name: str) -> str:
    """确保 RCC.IPParameters 列表包含 name(CubeMX 只加载列表里声明的字段)。"""
    m = re.search(r"^RCC\.IPParameters=(.*)$", text, flags=re.MULTILINE)
    if not m:
        return text
    params = [p.strip() for p in m.group(1).split(",") if p.strip()]
    if name not in params:
        idx = params.index("PLLMUL") + 1 if "PLLMUL" in params else len(params)
        params.insert(idx, name)
        text = text[: m.start()] + f"RCC.IPParameters={','.join(params)}" + text[m.end() :]
    return text


def _remove_ip_param(text: str, name: str) -> str:
    """从 RCC.IPParameters 列表移除 name。"""
    m = re.search(r"^RCC\.IPParameters=(.*)$", text, flags=re.MULTILINE)
    if not m:
        return text
    params = [p.strip() for p in m.group(1).split(",") if p.strip() and p.strip() != name]
    return text[: m.start()] + f"RCC.IPParameters={','.join(params)}" + text[m.end() :]


def patch_ioc_identity(
    ioc_src: str,
    ioc_dst: str,
    project_name: str,
    toolchain: str = "CMake",
    couple_files: bool = True,
    clock_source: str = "HSE",
    pll_mul: int = 9,
) -> None:
    """把模板 .ioc 复制到目标位置,并改写工程标识(ProjectName/ProjectFileName)。

    toolchain: 目标工具链,默认 "CMake"(与用户 CMake+ninja+arm-gcc 环境匹配);
               是"默认值"而非强制,传其他值(如 "EWARM V8.32")即可覆盖。
    couple_files: 每个外设生成独立 .c/.h(CubeMX 的 "Generate peripheral
                initialization as a pair of '.c/.h' files per peripheral"),
                默认 True(勾选);显式传 False 则集中到 main.c。
    clock_source: PLL 时钟源,默认 "HSE"(外部晶振 8MHz,默认 72MHz);显式传 "HSI"
                则用内部 RC(HSI/2)——默认值,不强制。
    pll_mul:      PLL 倍频,默认 9(8MHz×9=72MHz);可显式覆盖(如配 "HSI" 时
                用 16 → 64MHz)。
    """
    os.makedirs(os.path.dirname(ioc_dst), exist_ok=True)
    with open(ioc_src, encoding="utf-8", errors="replace") as f:
        text = f.read()
    text = re.sub(
        r"^ProjectManager\.ProjectName=.*$", f"ProjectManager.ProjectName={project_name}", text, flags=re.MULTILINE
    )
    text = re.sub(
        r"^ProjectManager\.ProjectFileName=.*$",
        f"ProjectManager.ProjectFileName={project_name}.ioc",
        text,
        flags=re.MULTILINE,
    )
    # 防御:模板缺 ProjectName/FileName 行时补上(否则 CubeMX 用模板默认名)
    if not re.search(r"^ProjectManager\.ProjectName=", text, flags=re.MULTILINE):
        text += f"\nProjectManager.ProjectName={project_name}\n"
    if not re.search(r"^ProjectManager\.ProjectFileName=", text, flags=re.MULTILINE):
        text += f"\nProjectManager.ProjectFileName={project_name}.ioc\n"
    if re.search(r"^ProjectManager\.TargetToolchain=.*$", text, flags=re.MULTILINE):
        text = re.sub(
            r"^ProjectManager\.TargetToolchain=.*$",
            f"ProjectManager.TargetToolchain={toolchain}",
            text,
            flags=re.MULTILINE,
        )
    else:
        text += f"\nProjectManager.TargetToolchain={toolchain}\n"
    # 外设独立 .c/.h 选项默认勾选(模板本就是 true,但 CubeMX 生成/set 命令可能把它
    # 改回 false,导致所有外设初始化挤进 main.c);这里是默认值,显式传 False 可覆盖。
    couple_val = "true" if couple_files else "false"
    if re.search(r"^ProjectManager\.CoupleFile=.*$", text, flags=re.MULTILINE):
        text = re.sub(
            r"^ProjectManager\.CoupleFile=.*$", f"ProjectManager.CoupleFile={couple_val}", text, flags=re.MULTILINE
        )
    else:
        text += f"\nProjectManager.CoupleFile={couple_val}\n"
    # 时钟默认值(模板即 HSE 8MHz ×9 = 72MHz;CubeMX set RCC 命令会丢
    # PLLSourceVirtual,这里按默认值补回;显式传其他值可覆盖,不强制)。
    src = clock_source.strip().upper()
    if src not in ("HSE", "HSI"):
        raise ToolError("BAD_ARGUMENT", f"clock_source 仅支持 HSE/HSI,收到: {clock_source!r}")
    if not (2 <= pll_mul <= 16):
        raise ToolError("BAD_ARGUMENT", f"pll_mul 应在 2~16 之间,收到: {pll_mul!r}")
    if src == "HSE":
        if re.search(r"^RCC\.PLLSourceVirtual=.*$", text, flags=re.MULTILINE):
            text = re.sub(
                r"^RCC\.PLLSourceVirtual=.*$", "RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE", text, flags=re.MULTILINE
            )
        else:
            text += "\nRCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE\n"
        text = _ensure_ip_param(text, "PLLSourceVirtual")
    else:  # HSI:PLLSourceVirtual 表达删掉,CubeMX 用默认 HSI(HSI/2)
        text = re.sub(r"^RCC\.PLLSourceVirtual=.*$\n?", "", text, flags=re.MULTILINE)
        text = _remove_ip_param(text, "PLLSourceVirtual")
    if re.search(r"^RCC\.PLLMUL=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^RCC\.PLLMUL=.*$", f"RCC.PLLMUL=RCC_PLL_MUL{pll_mul}", text, flags=re.MULTILINE)
    else:
        text += f"\nRCC.PLLMUL=RCC_PLL_MUL{pll_mul}\n"
    if os.path.isfile(ioc_dst):
        backup(ioc_dst)
    with open(ioc_dst, "w", encoding="utf-8") as f:
        f.write(text)


# TIM 的枚举值与 .ioc 片段来自 knowledge/rules/tim.yaml,不再硬编码在这里。
# 改值请改那个 YAML,再跑 python -m unittest test_cubemx_mcp 确认行为不变。


def inject_tim_internal_clock(
    ioc_path: str, target_tim: str, prescaler: int = 7200, period: int = 10000, irq: bool = True
) -> str:
    """把目标 TIM(如 TIM2)做成内部时钟:标准表达直接注入(替代借壳法)。

    6.18 验证过的原生表达(GUI 生成的标准形态):
      - Mcu.IPx=TIM2 追加,IPNb 同步
      - Mcu.Pin{N}=VP_TIM2_VS_ClockSourceINT 必须内联进 Mcu.Pin 列表,PinsNb 同步
      - TIM2.* 参数 + VP_TIM2_VS_ClockSourceINT.Mode=Internal/Signal + NVIC 行
      - functionlistsort 追加 MX_TIM2_Init 段
    若该 TIM 已是内部时钟则直接返回(幂等);已有其它模式(ETR)表达则先删除再注入。
    不依赖模板里存在"可借壳"的原生内部时钟 TIM,因此比借壳法更通用。

    参数:
      ioc_path:    .ioc 文件绝对路径(会被改写)
      target_tim:  目标 TIM 名,如 TIM2
      prescaler:   预分频(接口上传"分频比",如 7200 表示 72MHz/7200;.ioc 里落成寄存器值 7199)
      period:      计数(如 10000 表示计满 10000 次;.ioc 里落成 9999。1s 中断 = 7200 / 10000)
      irq:         是否启用 NVIC 中断,默认 True
    返回:描述字符串
    """
    tim = target_tim.strip().upper()
    if not re.fullmatch(r"TIM\d+", tim):
        raise ToolError("BAD_ARGUMENT", f"非法 TIM 名(应为 TIM1~TIM17): {target_tim!r}")
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines(keepends=True)
    # 已是内部时钟则直接返回(幂等)
    if any(re.match(rf"^VP_{re.escape(tim)}_VS_ClockSourceINT\.Mode=Internal$", ln.strip()) for ln in lines):
        return f"{tim} 已是内部时钟,无需注入"

    # 收集现有 IP/Pin(剔除将被替换的旧 TIM 表达)
    ip_entries = []
    pin_entries = []
    for ln in lines:
        m = re.match(r"Mcu\.IP\d+=(.+)", ln)
        if m:
            ip_entries.append(m.group(1).strip())
        m = re.match(r"Mcu\.Pin\d+=(.+)", ln)
        if m:
            pin_entries.append(m.group(1).strip())
    ip_entries = [n for n in ip_entries if n != tim]
    pin_entries = [p for p in pin_entries if not p.startswith(f"VP_{tim}_VS_ClockSourceINT")]
    new_ips = ip_entries + [tim]
    new_pins = pin_entries + [f"VP_{tim}_VS_ClockSourceINT"]

    # 接口上的 prescaler/period 是"分频比 / 计数",而 .ioc 里要写寄存器值(各减 1)。
    # 必须在这里算好:把 "72-1" 这种未求值表达式写进 .ioc 时,CubeMX 会原样抄进 C 代码
    # (实测:htim3.Init.Prescaler = 72-1; —— 合法但难看)。
    prescaler, period = int(prescaler) - 1, int(period) - 1
    values = knowledge_loader.rule("tim")["values"]
    fragment = knowledge_loader.rule_mode("tim", "internal_clock")["ioc_fragment"]
    if not irq:
        # 片段里的 NVIC 行是可选的,irq 为 False 时去掉
        fragment = "\n".join(ln for ln in fragment.splitlines() if not ln.startswith("NVIC."))
    tim_params = knowledge_loader.render_fragment(fragment, values, tim=tim, prescaler=prescaler, period=period) + "\n"

    out = []
    inserted_params = False
    ipnb_seen = False
    pinsnb_seen = False
    for ln in lines:
        s = ln.strip()
        # 删除旧 TIM 表达(参数/VP/SH/NVIC 行;IP/Pin 行在下方统一重建)
        if (
            s.startswith(f"{tim}.")
            or s.startswith(f"SH.S_{tim}")
            or re.match(rf"^NVIC\.{re.escape(tim)}_IRQn=", s)
            or re.match(rf"^VP_{re.escape(tim)}_VS_ClockSourceINT", s)
        ):
            continue
        if re.match(r"Mcu\.IP\d+=", s) or re.match(r"Mcu\.Pin\d+=", s):
            continue
        if re.match(r"Mcu\.IPNb=", s):
            ipnb_seen = True
            for i, name in enumerate(new_ips):
                out.append(f"Mcu.IP{i}={name}\n")
            out.append(f"Mcu.IPNb={len(new_ips)}\n")
            continue
        if re.match(r"Mcu\.PinsNb=", s):
            pinsnb_seen = True
            for i, name in enumerate(new_pins):
                out.append(f"Mcu.Pin{i}={name}\n")
            out.append(f"Mcu.PinsNb={len(new_pins)}\n")
            continue
        if s.startswith("ProjectManager.functionlistsort="):
            # 按段拆分,剔除旧 {tim} 段(任意序号/flag),再追加新段
            segs = [x for x in ln.split("=", 1)[1].split(",") if x.strip()]
            segs = [
                x
                for x in segs
                if not re.search(rf"MX_{re.escape(tim)}_Init-{re.escape(tim)}-false-HAL-(?:true|false)$", x)
            ]
            segs.append(f"{len(segs) + 1}-MX_{tim}_Init-{tim}-false-HAL-true")
            out.append(ln.split("=", 1)[0] + "=" + ",".join(segs) + "\n")
            continue
        if s == "board=custom":
            if not inserted_params:
                out.append(tim_params)
                inserted_params = True
            out.append(ln)
            continue
        out.append(ln)
    # 防御:非标准 .ioc 缺失 IPNb/PinsNb 行时,在末尾补全(否则上面的重建会丢数据)
    if not ipnb_seen:
        for i, name in enumerate(new_ips):
            out.append(f"Mcu.IP{i}={name}\n")
        out.append(f"Mcu.IPNb={len(new_ips)}\n")
    if not pinsnb_seen:
        for i, name in enumerate(new_pins):
            out.append(f"Mcu.Pin{i}={name}\n")
        out.append(f"Mcu.PinsNb={len(new_pins)}\n")
    if not inserted_params:
        out.append(tim_params)
    baseline = check_structure(ioc_path)
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out)
    _verify_after_write(ioc_path, tim_params.splitlines(), baseline)
    return (
        f"{tim} 已注入内部时钟标准表达(Prescaler={prescaler}, Period={period}, "
        f"NVIC={'开' if irq else '关'});IPNb={len(new_ips)}, PinsNb={len(new_pins)}"
    )


# ---------------------------------------------------------------- 公共:IP/Pin/functionlistsort 重建
def _rebuild_ioc_lines(lines, tim, extra_ips=(), extra_pins=(), drop_prefixes=(), keep_extra_tims=()):
    """通用 .ioc 重建骨架(供各 TIM 配置生成逻辑复用):
      - 剔除 {tim} 的旧参数/SH/NVIC/VP 行,以及 drop_prefixes 命中的行
      - 重建 Mcu.IPx / Mcu.Pinx 列表(追加 extra_ips / extra_pins,IP 追加到 keep_extra_tims 之后)
      - 重建 functionlistsort(剔除旧 {tim} 段,追加 MX_{tim}_Init 段)
    返回 (out_lines, new_ips, new_pins, inserted_params);非标准 .ioc 缺失 IPNb/PinsNb 由调用方补全。
    """
    ip_entries, pin_entries = [], []
    for ln in lines:
        m = re.match(r"Mcu\.IP\d+=(.+)", ln)
        if m:
            ip_entries.append(m.group(1).strip())
        m = re.match(r"Mcu\.Pin\d+=(.+)", ln)
        if m:
            pin_entries.append(m.group(1).strip())

    ip_entries = [n for n in ip_entries if n != tim]
    pin_entries = [
        p
        for p in pin_entries
        if not p.startswith(f"VP_{tim}_VS_ClockSourceINT") and not any(p.startswith(d) for d in drop_prefixes)
    ]
    new_ips = ip_entries + [n for n in keep_extra_tims if n not in ip_entries] + list(extra_ips)
    new_pins = pin_entries + [p for p in extra_pins if p not in pin_entries]

    out = []
    ipnb_seen = pinsnb_seen = False
    for ln in lines:
        s = ln.strip()
        if (
            s.startswith(f"{tim}.")
            or s.startswith(f"SH.S_{tim}")
            or re.match(rf"^NVIC\.{re.escape(tim)}_IRQn=", s)
            or re.match(rf"^VP_{re.escape(tim)}_VS_ClockSourceINT", s)
            or any(s.startswith(d) for d in drop_prefixes)
        ):
            continue
        if re.match(r"Mcu\.IP\d+=", s) or re.match(r"Mcu\.Pin\d+=", s):
            continue
        if re.match(r"Mcu\.IPNb=", s):
            ipnb_seen = True
            for i, name in enumerate(new_ips):
                out.append(f"Mcu.IP{i}={name}\n")
            out.append(f"Mcu.IPNb={len(new_ips)}\n")
            continue
        if re.match(r"Mcu\.PinsNb=", s):
            pinsnb_seen = True
            for i, name in enumerate(new_pins):
                out.append(f"Mcu.Pin{i}={name}\n")
            out.append(f"Mcu.PinsNb={len(new_pins)}\n")
            continue
        if s.startswith("ProjectManager.functionlistsort="):
            segs = [x for x in ln.split("=", 1)[1].split(",") if x.strip()]
            segs = [
                x
                for x in segs
                if not re.search(rf"MX_{re.escape(tim)}_Init-{re.escape(tim)}-false-HAL-(?:true|false)$", x)
            ]
            segs.append(f"{len(segs) + 1}-MX_{tim}_Init-{tim}-false-HAL-true")
            out.append(ln.split("=", 1)[0] + "=" + ",".join(segs) + "\n")
            continue
        if s == "board=custom":
            out.append(ln)
            continue
        out.append(ln)
    return out, new_ips, new_pins, (ipnb_seen, pinsnb_seen)


def _finish_ioc_write(ioc_path, out_lines, new_ips, new_pins, counts, params_block):
    """通用收尾:补全缺失的 IPNb/PinsNb、把 params_block 插到 board=custom 之前,写回文件。"""
    ipnb_seen, pinsnb_seen = counts
    if not ipnb_seen:
        for i, name in enumerate(new_ips):
            out_lines.append(f"Mcu.IP{i}={name}\n")
        out_lines.append(f"Mcu.IPNb={len(new_ips)}\n")
    if not pinsnb_seen:
        for i, name in enumerate(new_pins):
            out_lines.append(f"Mcu.Pin{i}={name}\n")
        out_lines.append(f"Mcu.PinsNb={len(new_pins)}\n")
    if params_block:
        # 插到 board=custom 之前(与 inject_tim_internal_clock 一致,键序更接近原生)
        for i, ln in enumerate(out_lines):
            if ln.strip() == "board=custom":
                out_lines[i:i] = [params_block]
                break
        else:
            out_lines.append(params_block)
    baseline = check_structure(ioc_path)
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out_lines)
    _verify_after_write(
        ioc_path,
        list((params_block or "").splitlines()) + [f"Mcu.IPNb={len(new_ips)}", f"Mcu.PinsNb={len(new_pins)}"],
        baseline,
    )


def inject_tim_pwm(
    ioc_path: str, target_tim: str, pin: str, signal: str, prescaler: int = 7200, period: int = 10000, pulse: int = 5000
) -> str:
    """把目标 TIM 配成 PWM 输出(6.18 标准表达,内部枚举名对照见 README.dev-notes.md)。

    注入的 6.18 原生形态(与 inject_tim_internal_clock 同款骨架):
      Mcu.IPx=TIM3 / Mcu.Pin{N}=PA6
      PA6.Mode=PWM Generation1 CH1        ← 内部名(带序号),GUI 名是 "PWM Generation CH1"
      PA6.Signal=S_TIM3_CH1
      SH.S_TIM3_CH1.0=TIM3_CH1,PWM Generation1 CH1
      SH.S_TIM3_CH1.ConfNb=1
      TIM3.IPParameters=Prescaler,Period,OCMode,Pulse
      TIM3.OCMode=TIM_OCMODE_PWM1 / Period={period} / Prescaler={prescaler} / Pulse={pulse}
      (片段的 {period}/{prescaler} 已是寄存器值:接口传 100 计数 → .ioc 里 99)
    引脚由调用方显式给出(pin + signal),如 PA6 / S_TIM3_CH1;本函数不猜引脚。
    若该 TIM 已有 PWM 表达则先删除再注入(幂等)。
    """
    tim = target_tim.strip().upper()
    if not re.fullmatch(r"TIM\d+", tim):
        raise ToolError("BAD_ARGUMENT", f"非法 TIM 名(应为 TIM1~TIM17): {target_tim!r}")
    ch = re.search(r"CH(\d)", signal)
    if not ch:
        raise ToolError("BAD_ARGUMENT", f"无法从信号名解析通道: {signal!r}(应为 S_TIM3_CH1 形式)")
    n = ch.group(1)
    # 接口上传"分频比 / 计数",.ioc 里写寄存器值(各减 1);见 inject_tim_internal_clock 的说明
    prescaler, period = int(prescaler) - 1, int(period) - 1
    values = knowledge_loader.rule("tim")["values"]
    mode_name = values["pwm_mode"].format(n=n)
    mode_esc = mode_name.replace(" ", "\\ ")  # .ioc 键名里空格要转义,见 knowledge/rules/tim.yaml
    sig_body = signal[2:] if signal.startswith("S_") else signal  # S_TIM3_CH1 -> TIM3_CH1

    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines(keepends=True)
    # 幂等:已有同信号 PWM 表达
    if any(
        re.match(rf"^SH\.{re.escape(signal)}\.0={re.escape(sig_body)},{re.escape(mode_name)}$", ln.strip())
        for ln in lines
    ):
        return f"{tim} 已注入 PWM 表达,无需重复注入"

    out, new_ips, new_pins, counts = _rebuild_ioc_lines(
        lines, tim, extra_ips=[tim], extra_pins=[pin], drop_prefixes=(f"{pin}.Mode", f"{pin}.Signal", f"SH.{signal}")
    )
    params = (
        knowledge_loader.render_fragment(
            knowledge_loader.rule_mode("tim", "pwm")["ioc_fragment"],
            values,
            tim=tim,
            pin=pin,
            signal=signal,
            n=n,
            prescaler=prescaler,
            period=period,
            pulse=pulse,
            mode_name=mode_name,
            mode_name_in_key=mode_esc,
            signal_body=sig_body,
            channel_key=values["channel_key"].format(n=n),
        )
        + "\n"
    )
    _finish_ioc_write(ioc_path, out, new_ips, new_pins, counts, params)
    return (
        f"{tim} 已注入 PWM 标准表达(引脚 {pin}/{signal},Prescaler={prescaler}, "
        f"Period={period}, Pulse={pulse});IPNb={len(new_ips)}, PinsNb={len(new_pins)}"
    )


def inject_tim_input_capture(
    ioc_path: str, target_tim: str, pin: str, signal: str, prescaler: int = 72, period: int = 65535
) -> str:
    """把目标 TIM 配成输入捕获(6.18 标准表达,IC1 上升沿 + IC2 下降沿)。

    PA0 在 .ioc 里信号名是组合名 S_TIM2_CH1_ETR(不是 S_TIM2_CH1);
    IC1 用 Input_Capture1_from_TI1(direct,带 Channel 键);
    IC2 是同一信号的下降沿捕获(IC2Polarity=FALLING + IC2Selection=INDIRECTTI 参数行,
    无独立模式/Channel 键——实测基准样本即此形态)。
    CubeMX 据此生成 IC1+IC2 两个 sConfigIC,无需再手动补 IC2。
    """
    tim = target_tim.strip().upper()
    if not re.fullmatch(r"TIM\d+", tim):
        raise ToolError("BAD_ARGUMENT", f"非法 TIM 名(应为 TIM1~TIM17): {target_tim!r}")
    ch = re.search(r"CH(\d)", signal)
    if not ch:
        raise ToolError("BAD_ARGUMENT", f"无法从信号名解析通道: {signal!r}(应为 S_TIM2_CH1_ETR 形式)")
    n = ch.group(1)
    # 接口上传"分频比 / 计数",.ioc 里写寄存器值(各减 1);见 inject_tim_internal_clock 的说明
    prescaler, period = int(prescaler) - 1, int(period) - 1
    values = knowledge_loader.rule("tim")["values"]
    sig_body = signal[2:] if signal.startswith("S_") else signal  # S_TIM2_CH1_ETR -> TIM2_CH1_ETR
    sig_body = re.sub(r"_ETR$", "", sig_body)  # TIM2_CH1_ETR -> TIM2_CH1
    mode1 = values["ic_mode"].format(n=n)

    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines(keepends=True)
    if any(
        re.match(rf"^SH\.{re.escape(signal)}\.0={re.escape(sig_body)},{re.escape(mode1)}$", ln.strip()) for ln in lines
    ):
        return f"{tim} 已注入输入捕获表达,无需重复注入"

    out, new_ips, new_pins, counts = _rebuild_ioc_lines(
        lines, tim, extra_ips=[tim], extra_pins=[pin], drop_prefixes=(f"{pin}.Mode", f"{pin}.Signal", f"SH.{signal}")
    )
    params = (
        knowledge_loader.render_fragment(
            knowledge_loader.rule_mode("tim", "input_capture")["ioc_fragment"],
            values,
            tim=tim,
            pin=pin,
            signal=signal,
            n=n,
            prescaler=prescaler,
            period=period,
            ic_mode=mode1,
            signal_body=sig_body,
            channel_key=values["channel_key"].format(n=n),
        )
        + "\n"
    )
    _finish_ioc_write(ioc_path, out, new_ips, new_pins, counts, params)
    return (
        f"{tim} 已注入输入捕获标准表达(引脚 {pin}/{signal},IC1 上升沿 + IC2 下降沿,"
        f"Prescaler={prescaler}, Period={period});IPNb={len(new_ips)}, PinsNb={len(new_pins)}"
    )


# help 文本来自 knowledge/rules/help.yaml(cubemx_help 直接读它),不再硬编码在这里。


# ---------------------------------------------------------------- DMA 请求(注入 .ioc 键组)
def _insert_sorted_line(out_lines: list, line: str, prefix: str) -> bool:
    """把 line 按 key 的字母序插到以 prefix 开头的行段里(CubeMX 自己写出的段大致是字典序)。"""
    key = line.split("=", 1)[0]
    last = None
    for i, ln in enumerate(out_lines):
        s = ln.strip()
        if not s.startswith(prefix):
            continue
        last = i
        if s.split("=", 1)[0] > key:
            out_lines.insert(i, line)
            return True
    if last is not None:
        out_lines.insert(last + 1, line)
        return True
    return False


def inject_dma(
    ioc_path: str,
    request: str,
    channel: str,
    *,
    index: int = -1,
    direction: str = "",
    mode: str = "",
    periph_inc: str = "",
    mem_inc: str = "",
    periph_align: str = "",
    mem_align: str = "",
    priority: str = "",
    nvic: bool = True,
) -> str:
    """给某个 DMA 请求注入 .ioc 键组 —— 等价于 GUI 里 DMA Settings → Add(实测)。

    6.18 实测(见 knowledge/rules/dma.yaml):脚本 set 配不了 DMA,但注入键组后
    config saveas / project generate 都会认,生成的 HAL 骨架里 dma.c 的 MX_DMA_Init、
    各外设 x.c 里的 hdma_xxx + __HAL_LINKDMA、stm32f1xx_it.c 里的 <通道>_IRQHandler 齐全。
    只写必需参数(Instance/Direction/PeriphInc/MemInc/Mode),对齐与优先级缺省时
    交给 CubeMX 按请求类型补默认(ADC→HALFWORD、USART→BYTE)。

    参数:
      ioc_path:     .ioc 文件绝对路径(会被改写)
      request:      DMA 请求名(来自 db,如 ADC1 / USART2_RX / SPI1_TX);查表见
                    knowledge/mcu/<芯片>.dma.yaml
      channel:      DMA 通道(如 DMA1_Channel1);可显式给,或由调用方查表得到
      index:        请求序号(-1 = 自动:该请求已存在则复用,否则追加)
      direction / mode / periph_inc / mem_inc / periph_align / mem_align / priority:
                    枚举值(留空 = 用 rules/dma.yaml 的默认值,对齐/优先级留空则交给 CubeMX)
      nvic:         是否同时注入 NVIC.<通道>_IRQn(默认 True,和 GUI Add 之后一致)
    幂等:同一请求重复注入只重写它自己的参数键,不会重复添加。
    """
    rules = knowledge_loader.require_rule_verified("dma")
    keys = rules.get("ioc_keys") or {}
    values = rules.get("values") or {}
    defaults = values.get("defaults") or {}
    prefix_tpl = keys.get("param_prefix") or "Dma.{request}.{index}."
    param_order = list(keys.get("param_order") or [])

    req = str(request).strip().upper()
    ch = str(channel).strip()
    if not re.fullmatch(r"DMA\d+_Channel\d+", ch):
        raise ToolError("BAD_ARGUMENT", f"非法 DMA 通道名(应形如 DMA1_Channel1): {channel!r}")
    if not re.fullmatch(r"[A-Z0-9_]+", req):
        raise ToolError("BAD_ARGUMENT", f"非法 DMA 请求名(应形如 ADC1 / USART2_RX): {request!r}")

    def _checked(value: str, group: str, label: str) -> str:
        """枚举值必须来自知识库(values 段);留空返回空串(交给默认值/CubeMX)。"""
        raw = str(value or "").strip()
        if not raw:
            return ""
        allowed = sorted((values.get(group) or {}).values())
        if raw not in allowed:
            raise ToolError("BAD_ARGUMENT", f"DMA {label} 只支持 {', '.join(allowed)};收到 {value!r}")
        return raw

    pairs = [
        ("Instance", ch),
        ("Direction", _checked(direction, "direction", "direction") or defaults.get("direction", "")),
        ("PeriphInc", _checked(periph_inc, "periph_inc", "periph_inc") or defaults.get("periph_inc", "")),
        ("MemInc", _checked(mem_inc, "mem_inc", "mem_inc") or defaults.get("mem_inc", "")),
        ("Mode", _checked(mode, "mode", "mode") or defaults.get("mode", "")),
    ]
    optional = (
        ("PeriphDataAlignment", "periph_align", periph_align),
        ("MemDataAlignment", "mem_align", mem_align),
        ("Priority", "priority", priority),
    )
    for name, group, value in optional:
        got = _checked(value, group, group)
        if got:
            pairs.append((name, got))
    if param_order:
        pairs.sort(key=lambda kv: param_order.index(kv[0]) if kv[0] in param_order else len(param_order))

    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines(keepends=True)

    # 现有请求声明:序号 -> 请求名(用来复用序号 / 计算新序号)
    existing = {}
    for ln in lines:
        hit = re.match(r"Dma\.Request(\d+)=(.+)$", ln.strip())
        if hit:
            existing[int(hit.group(1))] = hit.group(2).strip()
    if index < 0:
        index = next((i for i, name in existing.items() if name == req), -1)
        if index < 0:
            index = max(existing) + 1 if existing else 0
    requests = dict(existing)
    requests[index] = req

    prefix = prefix_tpl.format(request=req, index=index)
    body = [f"{prefix}{name}={value}\n" for name, value in pairs]
    body.append(f"{prefix}RequestParameters={','.join(name for name, _ in pairs)}\n")
    for i in sorted(requests):
        body.append(f"{keys.get('request_index_key', 'Dma.Request{index}').format(index=i)}={requests[i]}\n")
    body.append(f"{keys.get('requests_nb_key', 'Dma.RequestsNb')}={len(requests)}\n")

    ip_entry = keys.get("ip_list_entry", "DMA")

    def _ip_lines() -> list:
        """重建 Mcu.IPx 列表:DMA 按字母序插进去(与 GUI 写出的顺序一致)。"""
        new_ips = [n for n in ip_entries if n != ip_entry]
        pos = next((i for i, n in enumerate(new_ips) if n > ip_entry), len(new_ips))
        new_ips.insert(pos, ip_entry)
        return [f"Mcu.IP{i}={name}\n" for i, name in enumerate(new_ips)] + [f"Mcu.IPNb={len(new_ips)}\n"]

    out = []
    dma_positions = []
    ip_entries = []
    ipnb_seen = False
    anchor = None
    for ln in lines:
        s = ln.strip()
        # 本请求的旧键、所有请求声明行、旧 NVIC 行:统一剔除后重建
        if re.match(rf"^Dma\.{re.escape(req)}\.\d+\.", s):
            continue
        if re.match(r"^Dma\.Request\d+=", s) or s.startswith(keys.get("requests_nb_key", "Dma.RequestsNb") + "="):
            continue
        if nvic and re.match(rf"^NVIC\.{re.escape(ch)}_IRQn=", s):
            continue
        if s.startswith("Dma."):
            dma_positions.append(len(out))
        if re.match(r"Mcu\.IP\d+=", s):
            ip_entries.append(s.split("=", 1)[1].strip())
            continue
        if re.match(r"Mcu\.IPNb=", s):
            ipnb_seen = True
            out.extend(_ip_lines() if ip_entries else [ln])
            continue
        if s == "board=custom" and anchor is None:
            anchor = len(out)
        out.append(ln)
    if ip_entries and not ipnb_seen:
        out.extend(_ip_lines())  # 非标准 .ioc 缺 IPNb 行时补全(防丢 IP 列表)

    # 插入位置:Dma 段末尾(已有其它 Dma 键时),否则 CAD.* 之后(与 GUI 写出的位置一致)
    if dma_positions:
        pos = dma_positions[-1] + 1
    else:
        pos = next((i + 1 for i, ln in enumerate(out) if ln.strip().startswith("CAD.")), None)
        if pos is None:
            pos = anchor if anchor is not None else len(out)
    out[pos:pos] = body

    if nvic:
        nvic_line = (keys.get("nvic_pattern") or "").replace("{channel}", ch) + "\n"
        if not _insert_sorted_line(out, nvic_line, "NVIC."):
            out.append(nvic_line)

    # functionlistsort 补 MX_DMA_Init(DMA 的 NVIC 初始化在 MX_DMA_Init 里)
    segment = keys.get("functionlist_segment") or "MX_DMA_Init-DMA-false-HAL-true"
    for i, ln in enumerate(out):
        if not ln.strip().startswith("ProjectManager.functionlistsort="):
            continue
        segs = [x for x in ln.split("=", 1)[1].split(",") if x.strip()]
        if not any(re.search(r"MX_DMA_Init-DMA-false-HAL-", x) for x in segs):
            segs.append(f"{len(segs) + 1}-{segment}")
        out[i] = ln.split("=", 1)[0] + "=" + ",".join(segs) + "\n"
        break

    baseline = check_structure(ioc_path)
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out)
    _verify_after_write(ioc_path, [ln.rstrip("\n") for ln in body], baseline)
    return f"{req} 已注入 DMA 键组(通道 {ch},序号 {index},{'含' if nvic else '不含'} NVIC;请求总数 {len(requests)})"


def _dma_requests_of(lines, peripheral: str) -> dict:
    """挑出属于该外设的 DMA 请求:{请求名: {"channel", "index"}}。

    请求名就是 CubeMX db 里的 DMA_Request 名:与实例同名(ADC1),或以 "{实例}_" 开头
    (USART2_RX / TIM2_UP / I2C1_TX)。不猜 —— 名字本身来自 db。
    """
    found = {}
    for ln in lines:
        hit = re.match(r"Dma\.([A-Z0-9_]+)\.(\d+)\.Instance=(\S+)\s*$", ln.strip())
        if not hit:
            continue
        name = hit.group(1)
        if name == peripheral or name.startswith(f"{peripheral}_"):
            found[name] = {"channel": hit.group(3), "index": int(hit.group(2))}
    return found


def _peripheral_pins(lines, peripheral: str, patterns=()) -> dict:
    """挑出绑在该外设上的引脚:{引脚名: 信号名}。

    判据是引脚行的 Signal 值:含实例名(I2C1_SCL / S_TIM3_CH1 / USART2_TX),
    或匹配 rules/<外设>.yaml 的 remove.pin_signal_patterns —— ADC 的引脚信号是 ADCx_IN0,
    里面没有 "ADC1",所以那种情况必须由规则给出模式。
    """
    pins = {}
    for ln in lines:
        hit = re.match(r"([A-Za-z0-9_\-]+)\.Signal=(.+)$", ln.strip())
        if not hit:
            continue
        pin, signal = hit.group(1), hit.group(2).strip()
        if peripheral in signal or any(signal.startswith(p) for p in patterns):
            pins[pin] = signal
    return pins


def _remove_rules(peripheral: str) -> dict:
    """取该外设规则文件里"移除时要用的信息"(共用中断向量、引脚信号模式);查不到返回空 dict。"""
    rule_name = knowledge_loader.rule_name_for(peripheral)
    if not rule_name:
        return {}
    try:
        return knowledge_loader.rule(rule_name) or {}
    except ToolError:
        return {}


def remove_peripheral(ioc_path: str, peripheral: str) -> dict:
    """从 .ioc 里移除一个外设,连带清掉它的引脚、中断、DMA 请求与初始化段。

    删除范围(由内到外):
      - Mcu.IPx=<外设> 与 {外设}.* 参数行、虚拟引脚 VP_{外设}*;
      - 按 Signal 值判定属于它的物理引脚({pin}.* 各行)与对应的 SH.{信号}* 行,
        并从 Mcu.Pin* 里摘掉;
      - NVIC.{外设}_IRQn;规则文件声明了共用向量时(如 ADC 的 ADC1_2_IRQn)一并删,
        但同族实例还在时不删;
      - DMA 连带:Dma.{请求}.* 、Dma.Request<n>、剩余请求重新连续编号(键前缀序号同步改)、
        Dma.RequestsNb;被删请求占用的 NVIC.<通道>_IRQn;请求清空时连 Mcu.IPx=DMA 与
        functionlistsort 里的 MX_DMA_Init 段一起删;
      - functionlistsort 里该外设的 MX_*_Init 段;IP/Pin 编号重排、Nb 修正。

    返回 {"removed", "keep_ips", "removed_dma", "removed_pins"};没有该外设抛 PERIPHERAL_NOT_FOUND。
    """
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    ip_line_idx = None
    for i, ln in enumerate(lines):
        if re.fullmatch(rf"Mcu\.IP\d+={re.escape(peripheral)}\s*", ln):
            ip_line_idx = i
            break
    if ip_line_idx is None:
        raise ToolError("PERIPHERAL_NOT_FOUND", f".ioc 中未找到外设 {peripheral}(检查大小写,如 TIM3/I2C1)")

    rules = _remove_rules(peripheral)
    shared_irq = str(rules.get("interrupt_irq") or "").strip()
    pin_patterns = tuple((rules.get("remove") or {}).get("pin_signal_patterns") or ())

    dma_reqs = _dma_requests_of(lines, peripheral)
    dma_channels = sorted({info["channel"] for info in dma_reqs.values()})
    victim_pins = _peripheral_pins(lines, peripheral, pin_patterns)
    victim_signals = sorted(set(victim_pins.values()))

    all_reqs = {}
    for ln in lines:
        hit = re.match(r"Dma\.Request(\d+)=(.+)$", ln.strip())
        if hit:
            all_reqs[int(hit.group(1))] = hit.group(2).strip()
    keep_reqs = [name for _idx, name in sorted(all_reqs.items()) if name not in dma_reqs]
    renumber = {name: new for new, name in enumerate(keep_reqs)}

    keep_ips = [
        m.group(2).strip()
        for ln in lines
        if (m := re.match(r"Mcu\.IP(\d+)=(.*)", ln)) and m.group(2).strip() != peripheral
    ]
    if dma_reqs and not keep_reqs:
        keep_ips = [n for n in keep_ips if n != "DMA"]  # 没有 DMA 请求了,连 DMA 实例一起摘掉
    # 共用向量(如 ADC1_2_IRQn):同族实例还在就保留,否则跟着删
    keep_shared_irq = bool(shared_irq) and any(
        knowledge_loader.rule_name_for(n) == knowledge_loader.rule_name_for(peripheral) for n in keep_ips
    )

    def _dma_block() -> list:
        """重建 Dma.Request<n> / RequestsNb(剩余请求按原顺序重新连续编号)。"""
        out_lines = [f"Dma.Request{i}={name}\n" for i, name in enumerate(keep_reqs)]
        if keep_reqs:
            out_lines.append(f"Dma.RequestsNb={len(keep_reqs)}\n")
        return out_lines

    drop = {ip_line_idx}
    removed = [lines[ip_line_idx].strip()]
    removed_dma = []
    removed_pins = []
    new_out = []
    ip_counter = 0
    pin_counter = 0
    dma_block_written = False
    for i, ln in enumerate(lines):
        if i in drop:
            continue
        s = ln.strip()
        # ---- DMA:被删请求的键、请求声明行统一在这里处理(顺带重排剩余请求的编号)
        if s.startswith("Dma."):
            hit = re.match(r"Dma\.([A-Z0-9_]+)\.(\d+)\.", s)
            if hit and not dma_block_written and hit.group(1) in dma_reqs:
                new_out.extend(_dma_block())
                dma_block_written = True
            if hit:
                name, old_idx = hit.group(1), int(hit.group(2))
                if name in dma_reqs:
                    removed_dma.append(s)
                    continue
                if name in renumber and renumber[name] != old_idx:
                    ln = ln.replace(f"Dma.{name}.{old_idx}.", f"Dma.{name}.{renumber[name]}.", 1)
                new_out.append(ln)
                continue
            if re.match(r"Dma\.Request\d+=", s) or s.startswith("Dma.RequestsNb="):
                if not dma_block_written:
                    new_out.extend(_dma_block())
                    dma_block_written = True
                continue
            new_out.append(ln)
            continue
        # ---- 被删外设的虚拟引脚条目
        if re.match(rf"Mcu\.Pin\d+=VP_{re.escape(peripheral)}", s):
            removed_pins.append(s)
            continue
        # ---- 引脚列表重排:属于该外设的物理引脚一并摘掉
        if re.match(r"Mcu\.Pin\d+=", s):
            pin_name = s.split("=", 1)[1].strip()
            if pin_name in victim_pins:
                removed_pins.append(s)
                continue
            new_out.append(f"Mcu.Pin{pin_counter}={pin_name}\n")
            pin_counter += 1
            continue
        if re.match(r"Mcu\.PinsNb=", s):
            new_out.append(f"Mcu.PinsNb={pin_counter}\n")
            continue
        # ---- 属于该外设的引脚行 / 信号句柄行
        pin_hit = re.match(r"([A-Za-z0-9_\-]+)\.", s)
        if pin_hit and pin_hit.group(1) in victim_pins:
            removed_pins.append(s)
            continue
        if s.startswith("SH.") and any(s.startswith(f"SH.{sig}") for sig in victim_signals):
            removed_pins.append(s)
            continue
        # ---- 外设参数行 / 虚拟引脚表达
        if s.startswith(f"{peripheral}.") or s.startswith(f"VP_{peripheral}") or s.startswith(f"SH.S_{peripheral}"):
            removed.append(s)
            continue
        # ---- 中断:自己的向量 + 规则声明的共用向量
        if re.match(rf"NVIC\.{re.escape(peripheral)}_IRQn=", s) or (
            shared_irq and not keep_shared_irq and re.match(rf"NVIC\.{re.escape(shared_irq)}=", s)
        ):
            removed.append(s)
            continue
        # ---- 被删 DMA 请求占用的通道中断
        if any(re.match(rf"NVIC\.{re.escape(ch)}_IRQn=", s) for ch in dma_channels):
            removed_dma.append(s)
            continue
        # ---- IP 列表重排(不输出的:DMA 已被摘掉时)
        if re.match(r"Mcu\.IP\d+=", s):
            ip_name = s.split("=", 1)[1].strip()
            if ip_name == peripheral or ip_name not in keep_ips:
                continue
            new_out.append(f"Mcu.IP{ip_counter}={ip_name}\n")
            ip_counter += 1
            continue
        if re.match(r"Mcu\.IPNb=", s):
            new_out.append(f"Mcu.IPNb={len(keep_ips)}\n")
            continue
        # ---- functionlistsort:去掉该外设的段(请求清空时连 MX_DMA_Init 一起)
        if "functionlistsort" in ln:
            ln = re.sub(rf",\d+-[A-Za-z0-9_]*_Init-{re.escape(peripheral)}-false-HAL-(?:true|false)", "", ln)
            ln = re.sub(rf"(^|,)\d+-[A-Za-z0-9_]*_Init-{re.escape(peripheral)}-false-HAL-(?:true|false),", r"\1", ln)
            if dma_reqs and not keep_reqs:
                ln = re.sub(r",\d+-MX_DMA_Init-DMA-false-HAL-(?:true|false)", "", ln)
                ln = re.sub(r"(^|,)\d+-MX_DMA_Init-DMA-false-HAL-(?:true|false),", r"\1", ln)
            new_out.append(ln)
            continue
        new_out.append(ln)
    if dma_reqs and not dma_block_written:
        new_out.extend(_dma_block())
    baseline = check_structure(ioc_path)
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(new_out)
    _verify_after_write(ioc_path, [f"Mcu.IPNb={len(keep_ips)}"], baseline)
    return {
        "removed": removed,
        "keep_ips": keep_ips,
        "removed_dma": removed_dma,
        "removed_pins": removed_pins,
    }
