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
  remove_peripheral(...)          从 .ioc 移除一个外设
"""

from __future__ import annotations

import os
import re
import shutil

import knowledge_loader
from cubemx_result import ToolError


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


def used_pins(ioc_path: str) -> dict:
    """读 .ioc,返回 {引脚名: 信号} —— 给引脚冲突检测用。"""
    out = {}
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            hit = re.match(r"^([A-Za-z0-9_\-]+)\.Signal=(.+)$", line.strip())
            if hit:
                out[hit.group(1)] = hit.group(2)
    return out


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
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    verify_written(ioc_path, wanted)
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
      prescaler:   预分频(72MHz 下 7200-1),默认 7200
      period:      周期(1s 中断 = 7200-1/10000-1),默认 10000
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
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out)
    verify_written(ioc_path, tim_params.splitlines())
    return (
        f"{tim} 已注入内部时钟标准表达(Prescaler={prescaler}-1, Period={period}-1, "
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
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out_lines)
    verify_written(
        ioc_path,
        list((params_block or "").splitlines()) + [f"Mcu.IPNb={len(new_ips)}", f"Mcu.PinsNb={len(new_pins)}"],
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
      TIM3.OCMode=TIM_OCMODE_PWM1 / Period={period}-1 / Prescaler={prescaler}-1 / Pulse={pulse}
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
        f"{tim} 已注入 PWM 标准表达(引脚 {pin}/{signal},Prescaler={prescaler}-1, "
        f"Period={period}-1, Pulse={pulse});IPNb={len(new_ips)}, PinsNb={len(new_pins)}"
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
        f"Prescaler={prescaler}-1, Period={period}-1);IPNb={len(new_ips)}, PinsNb={len(new_pins)}"
    )


# help 文本来自 knowledge/rules/help.yaml(cubemx_help 直接读它),不再硬编码在这里。


def remove_peripheral(ioc_path: str, peripheral: str) -> dict:
    """从 .ioc 里移除一个外设:删 Mcu.IPx、关联引脚/参数、NVIC、functionlistsort 段,并重排编号。

    返回 {"removed": [被删的行...], "keep_ips": [剩余外设...]};没有该外设抛 PERIPHERAL_NOT_FOUND。
    """
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    # 收集要删的行号:精确匹配 Mcu.IPx=<peripheral> 的行
    ip_line_idx = None
    for i, ln in enumerate(lines):
        if re.fullmatch(rf"Mcu\.IP\d+={re.escape(peripheral)}\s*", ln):
            ip_line_idx = i
            break
    if ip_line_idx is None:
        raise ToolError("PERIPHERAL_NOT_FOUND", f".ioc 中未找到外设 {peripheral}(检查大小写,如 TIM3/I2C1)")
    # 删除规则:先标记要删除的行(保留 Mcu.IPx 供重排)
    drop = {ip_line_idx}
    removed = [lines[ip_line_idx].strip()]
    for i, ln in enumerate(lines):
        if i == ip_line_idx:
            continue
        s = ln.strip()
        if s.startswith(f"{peripheral}.") or s.startswith(f"VP_{peripheral}") or s.startswith(f"SH.S_{peripheral}"):
            drop.add(i)
            removed.append(s)
        elif re.match(rf"NVIC\.{re.escape(peripheral)}_IRQn=", s):
            drop.add(i)
            removed.append(s)
        elif re.match(rf"Mcu\.Pin\d+=VP_{re.escape(peripheral)}", s):
            drop.add(i)
            removed.append(s)
    # 收集剩余外设(按顺序),用于重排 Mcu.IPx 和修正 Mcu.IPNb
    keep_ips = []
    for ln in lines:
        m = re.match(r"Mcu\.IP(\d+)=(.*)", ln)
        if m and m.group(2).strip() != peripheral:
            keep_ips.append(m.group(2).strip())
    # 重写所有行
    new_out = []
    ip_counter = 0
    pin_counter = 0
    for i, ln in enumerate(lines):
        if i in drop:
            continue  # 跳过被标记删除的行
        s = ln.strip()
        if re.match(rf"Mcu\.IP\d+={re.escape(peripheral)}$", s):
            continue  # 被删的外设行(已在 drop,防御)
        if re.match(r"Mcu\.IP\d+=", s):
            # 重排其余外设
            m = re.match(r"Mcu\.IP(\d+)=(.*)", s)
            new_out.append(f"Mcu.IP{ip_counter}={m.group(2)}\n")
            ip_counter += 1
            continue
        if re.match(r"Mcu\.IPNb=", s):
            new_out.append(f"Mcu.IPNb={len(keep_ips)}\n")
            continue
        if re.match(r"Mcu\.Pin\d+=", s):
            # 重排剩余引脚(被删外设的 VP 引脚已在 drop 中跳过),并修正 PinsNb
            m = re.match(r"Mcu\.Pin(\d+)=(.*)", s)
            new_out.append(f"Mcu.Pin{pin_counter}={m.group(2)}\n")
            pin_counter += 1
            continue
        if re.match(r"Mcu\.PinsNb=", s):
            new_out.append(f"Mcu.PinsNb={pin_counter}\n")
            continue
        if "functionlistsort" in ln:
            # 移除包含该外设的初始化段(段形如 ,N-MX_TIM3_Init-TIM3-false-HAL-true;HAL 使能位可 true/false)
            ln = re.sub(rf",\d+-[A-Za-z0-9_]*_Init-{re.escape(peripheral)}-false-HAL-(?:true|false)", "", ln)
            ln = re.sub(rf"(^|,)\d+-[A-Za-z0-9_]*_Init-{re.escape(peripheral)}-false-HAL-(?:true|false),", r"\1", ln)
            new_out.append(ln)
            continue
        new_out.append(ln)
    backup(ioc_path)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(new_out)
    verify_written(ioc_path, [f"Mcu.IPNb={len(keep_ips)}"])
    return {"removed": removed, "keep_ips": keep_ips}
