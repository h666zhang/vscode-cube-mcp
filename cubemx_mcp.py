#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vscode_cube_mcp: MCP server 封装 STM32CubeMX 官方 -q 脚本控制台(无状态子进程)。

每次调用:命令序列写入临时脚本 -> 启动 STM32CubeMX -q -> 超时强杀
-> 过滤 log4j 噪音 -> 检测 KO 失败标记 -> 返回干净输出。

Tools:
  cubemx_help(topic)               自述指南/可用模板(陌生 agent 建议先调用)
  cubemx_script(script)            任意脚本(逃生通道)
  cubemx_load(ioc)                 config load + 回读配置(只读)
  cubemx_configure(ioc, cmds)      load + set 命令序列 + saveas(写回 .ioc)
  cubemx_generate(ioc, project_dir) load + project generate
  cubemx_export_pinout(ioc)        csv pinout 导出(只读)
  cubemx_new_project(name, dir, mcu, cmds) 从零生成新工程(模板+set+generate)
  cubemx_remove_peripheral(ioc, peripheral) 文本方式移除外设
  cubemx_add_source(ioc, source_file) 自定义源文件加入 CMake 源列表

配置(环境变量,不硬编码本机路径):
  ST_CUBEMX_EXE            STM32CubeMX 可执行文件路径;未设置时尝试 PATH 中的
                           STM32CubeMX 及常见 Windows 安装位置,最后退回命令名。
  ST_CUBEMX_ALLOWED_ROOTS  .ioc 允许访问的根目录(os.pathsep 分隔);
                           未设置时默认仅允许当前工作目录。
  ST_CUBEMX_TIMEOUT        CubeMX 子进程超时秒数,默认 240。

能力边界:CubeMX -q 脚本模式没有 new project 命令,但 cubemx_new_project 通过
"复制 templates/ 下 6.18 原生模板 + set 命令 + generate"实现从零生成,详见 README.md。
"""

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from mcp.server import MCPServer


# ------------------------------------------------------------------ config
def _find_cubemx() -> str:
    """解析 STM32CubeMX 可执行文件路径(环境变量 > PATH > 常见安装位置)。"""
    exe = os.environ.get("ST_CUBEMX_EXE", "").strip()
    if exe:
        return exe
    candidates = ["STM32CubeMX"]
    if os.name == "nt":
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_key)
            if base:
                candidates.append(
                    os.path.join(base, "STMicroelectronics", "STM32Cube", "STM32CubeMX", "STM32CubeMX.exe")
                )
    for c in candidates:
        if os.path.isfile(c) or shutil.which(c):
            return c
    return candidates[0]


def _allowed_roots() -> list:
    """解析允许访问的根目录列表(环境变量 os.pathsep 分隔,默认当前目录)。"""
    raw = os.environ.get("ST_CUBEMX_ALLOWED_ROOTS", "")
    roots = [r.strip() for r in raw.split(os.pathsep) if r.strip()]
    return roots or [os.getcwd()]


CUBEMX_EXE = _find_cubemx()
ALLOWED_ROOTS = _allowed_roots()
TIMEOUT_SECONDS = int(os.environ.get("ST_CUBEMX_TIMEOUT", "240"))

NOISE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[(INFO|WARN|DEBUG|TRACE)\]|"
    r"^(Picked up|log4j[: ]|[A-Z][a-z]{2} \d{2}, \d{4} \d{1,2}:\d{2}:\d{2} (AM|PM)|"
    r"WARNING: Could not open|Configure log4j|Cannot load)",
    re.MULTILINE,
)

# CubeMX 有单实例锁,串行化防止并发互踩
_LOCK = threading.Lock()

mcp = MCPServer("Vscode_cube_mcp")


# ---------------------------------------------------------------- helpers
def _check_path(p: str) -> str:
    """校验路径在允许根目录下(等值或 root 后紧跟分隔符,防 ProjectsX 前缀绕过),返回绝对路径。"""
    ap = os.path.abspath(p)
    for root in ALLOWED_ROOTS:
        rp = os.path.abspath(root).rstrip(os.sep) or os.sep
        if ap.lower() == rp.lower() or ap.lower().startswith(rp.lower() + os.sep):
            return ap
    raise ValueError(f"路径不在白名单内(仅允许 {ALLOWED_ROOTS}): {p}")


def _cleanup(raw: str) -> str:
    lines = []
    for ln in raw.splitlines():
        if NOISE_RE.match(ln):
            continue
        s = ln.strip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def _kill_process_tree(proc) -> None:
    """强杀进程树:Windows 用 taskkill /T /F(CubeMX 是 Java 启动器,直接 kill 会留子进程),其它平台 SIGKILL。"""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, text=True, timeout=30,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_script(script: str) -> dict:
    """执行 CubeMX 脚本,返回 {ok, output, exit_code}。超时会强杀进程树,防残留 Java 进程占工程文件锁。"""
    fd, tmp = tempfile.mkstemp(prefix="cubemx_mcp_", suffix=".txt")
    proc = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script.strip() + "\n")
            f.write("exit\n")
        with _LOCK:
            proc = subprocess.Popen(
                [CUBEMX_EXE, "-q", tmp],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.path.dirname(CUBEMX_EXE) if os.path.dirname(CUBEMX_EXE) else None,
            )
            try:
                stdout, stderr = proc.communicate(timeout=TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc)
                return {"ok": False,
                        "output": f"TIMEOUT: CubeMX 子进程超过 {TIMEOUT_SECONDS}s 被终止(已强杀进程树)",
                        "exit_code": None}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    raw = (stdout or "") + "\n" + (stderr or "")
    output = _cleanup(raw)
    ok = proc.returncode == 0 and not any(line.strip() == "KO" for line in output.splitlines())
    return {"ok": ok, "output": output, "exit_code": proc.returncode}


def _ioc_path(ioc: str) -> str:
    ap = _check_path(ioc)
    if not os.path.isfile(ap):
        raise ValueError(f".ioc 文件不存在: {ap}")
    if not ap.lower().endswith(".ioc"):
        raise ValueError(f"不是 .ioc 文件: {ap}")
    return ap


def _template_search_dirs() -> list:
    """模板查找路径:①源码目录 __file__/templates ②安装版 data-files(site-packages 上级)
    ③data-files 实际安装位置 sys.prefix/templates(pip 装 wheel 时相对 sys.prefix)。"""
    return [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
        os.path.join(sys.prefix, "templates"),
    ]


def _available_templates() -> list:
    """扫描所有模板目录,返回已存在的 .ioc 模板 basename 列表(去重,保持顺序)。"""
    seen = []
    for d in _template_search_dirs():
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.lower().endswith(".ioc") and name not in seen:
                seen.append(name)
    return seen


def _project_template(mcu: str, template: str = "") -> str:
    """解析新建工程的基底 .ioc 模板路径。

    优先用显式 template 参数;否则在 server 同目录 templates/ 下按 mcu 查找
    (如 templates/STM32F103C8T6.ioc)。模板必须是 6.18 原生生成的 .ioc,
    否则 CubeMX 6.18 加载可能报错。
    """
    if template:
        return _ioc_path(template)
    search_dirs = _template_search_dirs()
    for tpl_dir in search_dirs:
        tpl = os.path.join(tpl_dir, f"{mcu}.ioc")
        if os.path.isfile(tpl):
            return tpl
    available = _available_templates()
    raise ValueError(
        f"未找到模板 {mcu}.ioc。\n"
        f"可用模板: {', '.join(available) if available else '(无)'}\n"
        f"新芯片模板生成方法:用 STM32CubeMX GUI 新建该芯片工程后保存为 templates/{mcu}.ioc;"
        f"或先调 cubemx_help(topic=\"templates\") 查看模板说明。查找过: {search_dirs}"
    )


def _ensure_ip_param(text: str, name: str) -> str:
    """确保 RCC.IPParameters 列表包含 name(CubeMX 只加载列表里声明的字段)。"""
    m = re.search(r"^RCC\.IPParameters=(.*)$", text, flags=re.MULTILINE)
    if not m:
        return text
    params = [p.strip() for p in m.group(1).split(",") if p.strip()]
    if name not in params:
        idx = params.index("PLLMUL") + 1 if "PLLMUL" in params else len(params)
        params.insert(idx, name)
        text = text[:m.start()] + f"RCC.IPParameters={','.join(params)}" + text[m.end():]
    return text


def _remove_ip_param(text: str, name: str) -> str:
    """从 RCC.IPParameters 列表移除 name。"""
    m = re.search(r"^RCC\.IPParameters=(.*)$", text, flags=re.MULTILINE)
    if not m:
        return text
    params = [p.strip() for p in m.group(1).split(",") if p.strip() and p.strip() != name]
    return text[:m.start()] + f"RCC.IPParameters={','.join(params)}" + text[m.end():]


def _patch_ioc_identity(ioc_src: str, ioc_dst: str, project_name: str, toolchain: str = "CMake",
                       couple_files: bool = True, clock_source: str = "HSE",
                       pll_mul: int = 9) -> None:
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
    text = re.sub(r"^ProjectManager\.ProjectName=.*$", f"ProjectManager.ProjectName={project_name}", text, flags=re.MULTILINE)
    text = re.sub(r"^ProjectManager\.ProjectFileName=.*$", f"ProjectManager.ProjectFileName={project_name}.ioc", text, flags=re.MULTILINE)
    # 防御:模板缺 ProjectName/FileName 行时补上(否则 CubeMX 用模板默认名)
    if not re.search(r"^ProjectManager\.ProjectName=", text, flags=re.MULTILINE):
        text += f"\nProjectManager.ProjectName={project_name}\n"
    if not re.search(r"^ProjectManager\.ProjectFileName=", text, flags=re.MULTILINE):
        text += f"\nProjectManager.ProjectFileName={project_name}.ioc\n"
    if re.search(r"^ProjectManager\.TargetToolchain=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^ProjectManager\.TargetToolchain=.*$", f"ProjectManager.TargetToolchain={toolchain}", text, flags=re.MULTILINE)
    else:
        text += f"\nProjectManager.TargetToolchain={toolchain}\n"
    # 外设独立 .c/.h 选项默认勾选(模板本就是 true,但 CubeMX 生成/set 命令可能把它
    # 改回 false,导致所有外设初始化挤进 main.c);这里是默认值,显式传 False 可覆盖。
    couple_val = "true" if couple_files else "false"
    if re.search(r"^ProjectManager\.CoupleFile=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^ProjectManager\.CoupleFile=.*$", f"ProjectManager.CoupleFile={couple_val}", text, flags=re.MULTILINE)
    else:
        text += f"\nProjectManager.CoupleFile={couple_val}\n"
    # 时钟默认值(模板即 HSE 8MHz ×9 = 72MHz;CubeMX set RCC 命令会丢
    # PLLSourceVirtual,这里按默认值补回;显式传其他值可覆盖,不强制)。
    src = clock_source.strip().upper()
    if src not in ("HSE", "HSI"):
        raise ValueError(f"clock_source 仅支持 HSE/HSI,收到: {clock_source!r}")
    if not (2 <= pll_mul <= 16):
        raise ValueError(f"pll_mul 应在 2~16 之间,收到: {pll_mul!r}")
    if src == "HSE":
        if re.search(r"^RCC\.PLLSourceVirtual=.*$", text, flags=re.MULTILINE):
            text = re.sub(r"^RCC\.PLLSourceVirtual=.*$", "RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE", text, flags=re.MULTILINE)
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
    with open(ioc_dst, "w", encoding="utf-8") as f:
        f.write(text)


def _inject_tim_internal_clock(ioc_path: str, target_tim: str,
                               prescaler: int = 7200, period: int = 10000,
                               irq: bool = True) -> str:
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
        raise ValueError(f"非法 TIM 名(应为 TIM1~TIM17): {target_tim!r}")
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines(keepends=True)
    # 已是内部时钟则直接返回(幂等)
    if any(re.match(rf"^VP_{re.escape(tim)}_VS_ClockSourceINT\.Mode=Internal$", ln.strip())
           for ln in lines):
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

    tim_params = (
        f"{tim}.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE\n"
        f"{tim}.CounterMode=TIM_COUNTERMODE_UP\n"
        f"{tim}.IPParameters=Prescaler,Period,CounterMode,AutoReloadPreload\n"
        f"{tim}.Period={period}-1\n"
        f"{tim}.Prescaler={prescaler}-1\n"
        f"VP_{tim}_VS_ClockSourceINT.Mode=Internal\n"
        f"VP_{tim}_VS_ClockSourceINT.Signal={tim}_VS_ClockSourceINT\n"
    )
    if irq:
        tim_params += f"NVIC.{tim}_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:true\\:true\n"

    out = []
    inserted_params = False
    ipnb_seen = False
    pinsnb_seen = False
    for ln in lines:
        s = ln.strip()
        # 删除旧 TIM 表达(参数/VP/SH/NVIC 行;IP/Pin 行在下方统一重建)
        if (s.startswith(f"{tim}.")
                or s.startswith(f"SH.S_{tim}")
                or re.match(rf"^NVIC\.{re.escape(tim)}_IRQn=", s)
                or re.match(rf"^VP_{re.escape(tim)}_VS_ClockSourceINT", s)):
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
            segs = [x for x in segs
                    if not re.search(rf"MX_{re.escape(tim)}_Init-{re.escape(tim)}-false-HAL-(?:true|false)$", x)]
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
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out)
    return (f"{tim} 已注入内部时钟标准表达(Prescaler={prescaler}-1, Period={period}-1, "
            f"NVIC={'开' if irq else '关'});IPNb={len(new_ips)}, PinsNb={len(new_pins)}")


# ---------------------------------------------------------------- help 指南
_GUIDE = """Vscode_cube_mcp — 封装 STM32CubeMX 命令行(-q)的 MCP server
=============================================================
【工具一览(9 个)】
  cubemx_help                本指南(当前)
  cubemx_new_project         从零生成 HAL 工程(首选入口)
  cubemx_load                加载 .ioc 回读配置(只读)
  cubemx_configure           加载 .ioc,执行 set 命令序列并写回
  cubemx_generate            加载 .ioc 生成 HAL 代码
  cubemx_export_pinout       导出引脚配置 CSV(只读)
  cubemx_remove_peripheral   从 .ioc 移除外设
  cubemx_add_source          把自定义源文件加入 CMake 源列表
  cubemx_script              任意 CubeMX 脚本命令(逃生通道)

【从零生成工程(标准流程)】
1) 调 cubemx_help(topic="templates") 查看可用芯片模板
2) cubemx_new_project(project_name="Demo", project_dir="C:/MINE/STM32Project/Demo",
   mcu="STM32F103C8T6",
   commands=["set pin PB13 GPIO_Output", "set gpio parameters PB13 GPIO_Label LED"])
3) 工程生成后,自定义源文件(如 Core/Src/OLED.c)用 cubemx_add_source 加入编译

【cubemx_new_project 参数(默认值而非强制)】
  mcu           默认 STM32F103C8T6,匹配 templates/{mcu}.ioc
  toolchain     默认 "CMake"(可覆盖 "EWARM V8.32"/"MDK-ARM"/"STM32CubeIDE")
  couple_files  默认 True(每个外设生成独立 .c/.h);False 则集中到 main.c
  clock_source  默认 "HSE"(外部晶振,72MHz);"HSI" 用内部 RC
  pll_mul       默认 9(8MHz×9=72MHz);HSI 常用 16 → 64MHz
  commands      set 命令列表,如 ["set pin PB13 GPIO_Output"]
  template      指定模板路径,优先于 mcu 查找

【外设配置命令与已知坑】
- GPIO:set pin PB13 GPIO_Output;set gpio parameters PB13 GPIO_Label LED
- I2C:set pin PB8 I2C1_SCL;set pin PB9 I2C1_SDA;set mode I2C1 I2C
- TIM 内部时钟(重要):脚本 set mode TIM2 一律 KO;set ip parameters TIM2
  ClockSource TIM_CLOCKSOURCE_INTERNAL 只改参数不改 SH/VP 表达(GUI 仍显示
  ETR)。可靠做法:new_project 命令含 "set ip parameters TIMx ClockSource
  TIM_CLOCKSOURCE_INTERNAL" 时 server 自动注入 6.18 验证过的标准表达
- 时钟坑:对 RCC 执行 set 命令(如 PLLMUL)会丢 RCC.PLLSourceVirtual=HSE,
  时钟静默降级 HSI(64MHz 而非 72MHz);改时钟优先用 new_project 的
  clock_source/pll_mul 参数
- generate 会覆盖 cmake/stm32cubemx/CMakeLists.txt,自定义源文件需重新
  cubemx_add_source

【约束】
- 所有 .ioc / 生成路径必须在 ST_CUBEMX_ALLOWED_ROOTS 白名单内
- 工程名仅字母/数字/下划线;路径写绝对路径(Windows 建议 C:/ 正斜杠)"""

_NEW_PROJECT_HELP = """【从零生成工程(cubemx_new_project)】
流程:
1) 先调 cubemx_help(topic="templates") 查看可用芯片模板
2) cubemx_new_project(project_name="Demo", project_dir="C:/MINE/STM32Project/Demo",
   mcu="STM32F103C8T6",
   commands=["set pin PB13 GPIO_Output", "set gpio parameters PB13 GPIO_Label LED"])
3) 自定义源文件(如 Core/Src/OLED.c)用 cubemx_add_source 加入编译

参数(默认值而非强制):
  mcu           默认 STM32F103C8T6,匹配 templates/{mcu}.ioc
  toolchain     默认 "CMake"(可覆盖 "EWARM V8.32"/"MDK-ARM"/"STM32CubeIDE")
  couple_files  默认 True(每个外设独立 .c/.h);False 则集中到 main.c
  clock_source  默认 "HSE"(72MHz);"HSI" 用内部 RC
  pll_mul       默认 9(8MHz×9=72MHz);HSI 常用 16 → 64MHz
  commands      set 命令列表,如 ["set pin PB13 GPIO_Output"]
  template      指定模板路径,优先于 mcu 查找

注意:找不到 {mcu}.ioc 模板时错误信息会列出可用模板,并按指引生成新芯片模板。"""

_PERIPHERAL_HELP = {
    "gpio": """【GPIO(已验证)】
  set pin PB13 GPIO_Output
  set gpio parameters PB13 GPIO_Label LED
示例:LED 在 PB13 → 命令序列如上。""",
    "i2c": """【I2C(已验证)】
  set pin PB8 I2C1_SCL
  set pin PB9 I2C1_SDA
  set mode I2C1 I2C
生成 i2c.c + main.c 调用 MX_I2C1_Init。""",
    "tim": """【TIM 内部时钟(重要,有坑)】
- set mode TIM2 一律 KO;set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL
  只改参数、不改 SH/VP 表达,CubeMX GUI 仍显示 ETR;手写 VP_TIMx 表达会被
  generate 静默清理。
- 可靠做法:new_project 的 commands 里含
  "set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL",
  server 自动注入 6.18 验证过的内部时钟标准表达(任意 TIM 均可,不依赖模板)。
- 1s 中断参数:72MHz 下 Prescaler=7200-1 + Period=10000-1。""",
    "rcc": """【时钟(RCC,有坑)】
- 模板已配好 HSE 8MHz × PLL9 = 72MHz,一般无需改。
- 对 RCC 执行 set 命令(如 PLLMUL)会把 RCC.PLLSourceVirtual=HSE 弄丢,
  时钟静默降级 HSI(如 64MHz 而非 72MHz)。
- 改频率优先用 cubemx_new_project 的 clock_source/pll_mul 参数;
  需要手动 set 时同时补:
  set ip parameters RCC PLLSourceVirtual RCC_PLLSOURCE_HSE
  set ip parameters RCC PLLMUL RCC_PLL_MUL9
  set ip parameters RCC SYSCLKSource RCC_SYSCLKSOURCE_PLLCLK
  set ip parameters RCC APB1CLKDivider RCC_HCLK_DIV2""",
}

_REMOVE_HELP = """【移除外设(cubemx_remove_peripheral)】
- 用法:cubemx_remove_peripheral(ioc, peripheral)
  如 cubemx_remove_peripheral("C:/proj/Demo.ioc", "TIM3")
- 直接编辑 .ioc 文本(脚本 set noparam 对部分外设无效):删除 Mcu.IPx、
  关联引脚/参数、NVIC、functionlistsort 段,并重排序号。
- 注意:操作前自行备份;移除后建议重新 generate 同步代码。"""

_ADDSOURCE_HELP = """【自定义源文件加入编译(cubemx_add_source)】
- 用法:cubemx_add_source(ioc, source_file)
  如 cubemx_add_source("C:/proj/Demo.ioc", "Core/Src/OLED.c")
- 背景:CubeMX 重新 generate 会覆盖 cmake/stm32cubemx/CMakeLists.txt,
  手动加的自定义源文件会丢失;本工具用于重新添加(幂等)。
- source_file 必须相对工程根,如 Core/Src/OLED.c(拒绝 ../、绝对路径)。"""


@mcp.tool()
def cubemx_help(topic: str = "") -> str:
    """MCP 自述:返回本 server 的使用指南、可用模板与各外设配置方法。

    陌生环境(第一次接入本 MCP 的 agent)建议先调用本工具,再开始建工程。
    不传 topic 返回完整指南;传 topic 返回该主题详细说明。

    可用 topic(大小写不敏感):
      new_project  从零生成工程的流程与参数
      templates    可用芯片模板列表与各自内容
      gpio / i2c / tim / rcc / clock  外设配置命令与已知坑
      remove       移除外设
      add_source   自定义源文件加入 CMake 源列表
    """
    t = topic.strip().lower()
    if t == "templates":
        avail = _available_templates()
        if not avail:
            return "templates/ 目录为空或不存在;请放入 6.18 原生 .ioc 模板"
        desc = {
            "STM32F103C8T6.ioc": "薄种子:72MHz(HSE+PLL×9)、SWD(PA13/14)、SysTick,无外设",
        }
        lines = ["【可用模板(动态扫描 templates/)】"]
        lines += [f"  {n}  {desc.get(n, '(无描述)')}" for n in avail]
        lines.append("【新增芯片】用 CubeMX GUI File→New Project 选芯片,"
                     "保存为 templates/{mcu}.ioc 后即可用 cubemx_new_project")
        return "\n".join(lines)
    if t == "new_project":
        return _NEW_PROJECT_HELP
    if t in _PERIPHERAL_HELP:
        return _PERIPHERAL_HELP[t]
    if t == "clock":
        return _PERIPHERAL_HELP["rcc"]  # clock 是 rcc 的别名,避免重复维护
    if t == "remove":
        return _REMOVE_HELP
    if t == "add_source":
        return _ADDSOURCE_HELP
    return _GUIDE


@mcp.tool()
def cubemx_script(script: str) -> str:
    """执行任意 CubeMX 脚本命令序列(逃生通道,原样传给 -q)。

    常用命令示例:
      config load "C:/path/proj.ioc"
      set mode USART1 Asynchronous
      set pin PB13 GPIO_Output
      set gpio parameters PB13 GPIO_Label=LED
      config saveas "C:/path/proj.ioc"
      project generate
    """
    r = _run_script(script)
    return f"[{'OK' if r['ok'] else 'FAIL'} exit={r['exit_code']}]\n{r['output']}"


@mcp.tool()
def cubemx_load(ioc: str) -> str:
    """加载 .ioc 工程并回读关键配置(只读,不修改任何文件)。

    参数:
      ioc: 工程 .ioc 文件绝对路径(须在允许目录内)
    """
    ap = _ioc_path(ioc)
    r = _run_script(f'config load "{ap}"')
    return f"[{'OK' if r['ok'] else 'FAIL'} exit={r['exit_code']}]\n{r['output']}"


@mcp.tool()
def cubemx_configure(ioc: str, commands: list) -> str:
    """加载 .ioc,依次执行 set 命令,最后 saveas 写回原文件。

    参数:
      ioc: 工程 .ioc 文件绝对路径(会被写回,先备份再调用)
      commands: 命令列表,如 ["set mode USART1 Asynchronous", "set pin PB13 GPIO_Output"]
    """
    ap = _ioc_path(ioc)
    lines = [f'config load "{ap}"']
    lines.extend(commands)
    lines.append(f'config saveas "{ap}"')
    r = _run_script("\n".join(lines))
    return f"[{'OK' if r['ok'] else 'FAIL'} exit={r['exit_code']}]\n{r['output']}"


@mcp.tool()
def cubemx_generate(ioc: str, project_dir: str = "") -> str:
    """加载 .ioc 并执行 project generate 生成 HAL 代码。

    参数:
      ioc: 工程 .ioc 文件绝对路径
      project_dir: 生成目标目录;留空则在 .ioc 所在目录生成。
                   强烈建议指向副本/测试目录,避免覆盖现有工程。
    """
    ap = _ioc_path(ioc)
    lines = [f'config load "{ap}"']
    if project_dir:
        pd = _check_path(project_dir)
        os.makedirs(pd, exist_ok=True)
        lines.append(f'project path "{pd}"')
    lines.append("project generate")
    r = _run_script("\n".join(lines))
    return f"[{'OK' if r['ok'] else 'FAIL'} exit={r['exit_code']}]\n{r['output']}"


@mcp.tool()
def cubemx_export_pinout(ioc: str) -> str:
    """加载 .ioc 并导出当前引脚配置 CSV(只读,CSV 写系统临时目录后读回)。

    参数:
      ioc: 工程 .ioc 文件绝对路径
    """
    ap = _ioc_path(ioc)
    fd, csv = tempfile.mkstemp(prefix="cubemx_pinout_", suffix=".csv")
    os.close(fd)
    try:
        r = _run_script(f'config load "{ap}"\ncsv pinout "{csv}"')
        if not r["ok"]:
            return f"[FAIL exit={r['exit_code']}]\n{r['output']}"
        with open(csv, encoding="utf-8", errors="replace") as f:
            content = f.read()
        return f"[OK]\n{r['output']}\n--- CSV ---\n{content}"
    finally:
        try:
            os.remove(csv)
        except OSError:
            pass
@mcp.tool()
def cubemx_new_project(project_name: str, project_dir: str, mcu: str = "STM32F103C8T6",
                       commands: list = None, template: str = "", toolchain: str = "CMake",
                       couple_files: bool = True, clock_source: str = "HSE",
                       pll_mul: int = 9) -> str:
    """从零生成一个新的 HAL 工程(基于内置模板改造)。

    流程:从 templates/ 选 6.18 原生 .ioc 模板(或显式 template)复制到
    project_dir,改写工程名,依次执行 set 命令,再 project generate 生成 HAL 代码。
    这样无需预先手写 .ioc,即"从零开始"。

    知识提示(生成前建议先读 templates/README.md):
      - 模板按 mcu 匹配 templates/{mcu}.ioc(薄种子:仅芯片标识+基础时钟
        72MHz/SWD/SysTick,无外设);外设全部由 commands set 现配。
      - TIM 内部时钟:命令含 "set ip parameters TIMx ClockSource
        TIM_CLOCKSOURCE_INTERNAL" 时 server 自动注入 6.18 验证过的标准表达,
        无需专门模板。
      - toolchain 默认 CMake(用户环境为 CMake+ninja+arm-gcc);要其他格式
        (EWARM V8.32 / MDK-ARM / STM32CubeIDE)显式传 toolchain 参数覆盖,不强制。
      - 已知坑:对 RCC 执行 set 命令(如 PLLMUL)会把 RCC.PLLSourceVirtual=HSE 弄丢,
        时钟静默降级为 HSI(如 HSI/2*16=64MHz 而非 72MHz);改时钟请用 GUI 或显式
        set 时钟源。生成后若检测到 HSE 丢失,返回值会附带时钟警告。

    参数:
      project_name: 工程名(将生成 {project_name}.ioc 与同名 HAL 工程)
      project_dir:  生成目标目录(须在白名单内;已存在目录会直接使用)
      mcu:          芯片型号,用于匹配 templates/{mcu}.ioc(默认 STM32F103C8T6)
      toolchain:    目标工具链,默认 "CMake",可覆盖(如 "EWARM V8.32")
      couple_files:  每个外设生成独立 .c/.h(CubeMX 的 "Generate peripheral
                     initialization as a pair of '.c/.h' files per peripheral"),
                     默认 True(勾选);显式传 False 则集中到 main.c
      clock_source:  PLL 时钟源,默认 "HSE"(外部晶振,默认 72MHz);显式传 "HSI" 用内部 RC
      pll_mul:       PLL 倍频,默认 9(8MHz×9=72MHz);可显式覆盖(如 HSI 配 16 → 64MHz)
      commands:     set 命令列表,如 ["set pin PB13 GPIO_Output", "set ip parameters RCC PLLMUL RCC_PLL_MUL9"]
      template:     可选,直接指定模板 .ioc 路径(优先于 mcu 查找)
    """
    if not project_name or not re.fullmatch(r"[A-Za-z0-9_]+", project_name):
        raise ValueError(f"非法工程名(仅允许字母/数字/下划线): {project_name!r}")
    pd = _check_path(project_dir)
    tpl = _project_template(mcu, template)
    ioc_dst = os.path.join(pd, f"{project_name}.ioc")
    _patch_ioc_identity(tpl, ioc_dst, project_name, toolchain=toolchain, couple_files=couple_files,
                        clock_source=clock_source, pll_mul=pll_mul)

    lines = [f'config load "{ioc_dst}"']
    if commands:
        lines.extend(commands)
    lines.append(f'config saveas "{ioc_dst}"')
    r = _run_script("\n".join(lines))
    if not r["ok"]:
        return (f"[FAIL exit={r['exit_code']}]\n{r['output']}\n"
                f"set 命令序列执行失败,已中止生成(避免产出残缺工程)。.ioc 保留在: {ioc_dst}")
    # TIM 内部时钟片段注入:若命令中有 "set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL",
    # 自动把该 TIM 做成内部时钟(标准表达直接注入,见 _inject_tim_internal_clock)。
    # 命令里可附 Prescaler/Period 覆盖默认 1s 参数(默认值而非强制),如
    # "set ip parameters TIM2 ClockSource TIM_CLOCKSOURCE_INTERNAL Prescaler 720 Period 1000"。
    tim_notes = []
    if commands:
        for cmd in commands:
            m = re.search(r"set ip parameters (TIM\d+) ClockSource TIM_CLOCKSOURCE_INTERNAL", cmd)
            if m:
                pm = re.search(r"\bPrescaler (\d+)", cmd)
                pd_m = re.search(r"\bPeriod (\d+)", cmd)
                tim_notes.append(_inject_tim_internal_clock(
                    ioc_dst, m.group(1),
                    prescaler=int(pm.group(1)) if pm else 7200,
                    period=int(pd_m.group(1)) if pd_m else 10000))
    lines = [f'config load "{ioc_dst}"']
    lines.append("project generate")
    r2 = _run_script("\n".join(lines))
    if r2["ok"]:
        r = r2
    note_internal = "\n".join(tim_notes) if tim_notes else ""
    # 生成后:若工程被生成到 project_dir 的同级目录(以工程名命名),把 .ioc 移到工程目录,
    # 保证 .ioc 与 HAL 工程在同一目录(cubemx generate 默认行为)。
    gen_dir = os.path.join(os.path.dirname(pd), project_name)
    if os.path.isdir(gen_dir) and os.path.abspath(gen_dir) != os.path.abspath(pd):
        new_ioc = os.path.join(gen_dir, f"{project_name}.ioc")
        try:
            os.replace(ioc_dst, new_ioc)
            ioc_dst = new_ioc
            note = f"\n.ioc 已移至工程目录: {gen_dir}"
        except OSError:
            note = f"\n注:工程生成于 {gen_dir},但 .ioc 移动失败,仍在 {pd}"
    else:
        note = ""
    # 时钟保护(默认值,不强制):模板原本是 HSE 外部晶振而生成后 PLLSourceVirtual 丢失时,
    # 提醒用户确认时钟(CubeMX set RCC 参数的已知副作用会把 HSE 表达清掉,静默降级 HSI)。
    clock_note = ""
    try:
        with open(ioc_dst, encoding="utf-8", errors="replace") as f:
            dst_text = f.read()
        if (clock_source.strip().upper() == "HSE"
                and "RCC.PLLSourceVirtual" not in dst_text):
            clock_note = ("\n⚠ 时钟警告:请求的 HSE 外部晶振配置(RCC.PLLSourceVirtual)在生成后丢失,"
                          "时钟可能已降级为 HSI 内部 RC(如 64MHz 而非 72MHz)。"
                          "请用 CubeMX GUI 的 Clock Configuration 把 PLL Source 选回 HSE、"
                          "PLLMUL 设 9 后重新 Generate;或显式 set 时钟源。")
    except OSError:
        pass
    return f"[{'OK' if r['ok'] else 'FAIL'} exit={r['exit_code']}]\n{r['output']}\n工程目录: {pd}{note}{note_internal}{clock_note}"

@mcp.tool()
def cubemx_remove_peripheral(ioc: str, peripheral: str) -> str:
    """从 .ioc 中移除一个外设(文本方式)。

    CubeMX 脚本的 `set noparam <IP>` 对部分外设无效(返回 OK 但外设保留),
    因此本工具直接编辑 .ioc 文本:删除 Mcu.IPx 条目、关联引脚/参数、
    NVIC 中断、functionlistsort 中的初始化段,并修正 Mcu.IPNb。
    注意:操作前请自行备份;移除后建议重新 generate 同步代码。

    参数:
      ioc:         工程 .ioc 文件绝对路径(会被写回)
      peripheral:  外设名,如 TIM3、I2C1(大小写敏感,匹配 Mcu.IPx=XXX 的精确值)
    """
    ap = _ioc_path(ioc)
    if not re.fullmatch(r"[A-Za-z0-9_]+", peripheral):
        raise ValueError(f"非法外设名(仅允许字母/数字/下划线,如 TIM3/I2C1): {peripheral!r}")
    with open(ap, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    # 收集要删的行号:精确匹配 Mcu.IPx=<peripheral> 的行
    ip_line_idx = None
    for i, ln in enumerate(lines):
        if re.fullmatch(rf"Mcu\.IP\d+={re.escape(peripheral)}\s*", ln):
            ip_line_idx = i
            break
    if ip_line_idx is None:
        raise ValueError(f".ioc 中未找到外设 {peripheral}(检查大小写,如 TIM3/I2C1)")
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
    with open(ap, "w", encoding="utf-8", newline="") as f:
        f.writelines(new_out)
    return f"已从 {ap} 移除外设 {peripheral}\n删除 {len(removed)} 行相关配置,外设列表已重排(Mcu.IPNb={len(keep_ips)})"


@mcp.tool()
def cubemx_add_source(ioc: str, source_file: str) -> str:
    """把自定义源文件加入工程的 CMake 源列表(stm32cubemx/CMakeLists.txt)。

    CubeMX 重新 generate 会覆盖 cmake/stm32cubemx/CMakeLists.txt,手动加进
    MX_Application_Src 的自定义源文件会丢失;本工具用于重新添加(幂等,重复调用不重复加)。

    参数:
      ioc:          工程 .ioc 文件绝对路径(用于定位工程根目录)
      source_file:  源文件相对工程根的路径,如 Core/Src/OLED.c
    """
    ap = _ioc_path(ioc)
    proj_root = os.path.dirname(ap)
    cmake_lists = os.path.join(proj_root, "cmake", "stm32cubemx", "CMakeLists.txt")
    if not os.path.isfile(cmake_lists):
        raise ValueError(f"找不到 {cmake_lists};请先 project generate 生成工程")
    sf = source_file.replace("\\", "/").strip()
    # 输入校验:必须是相对工程根的普通路径——拒绝绝对路径、盘符、路径穿越(../)、换行注入
    if (not sf or sf.startswith("/") or re.match(r"^[A-Za-z]:", sf)
            or sf.startswith("../") or "/../" in sf or sf.endswith("/..")
            or "\n" in sf or "\r" in sf):
        raise ValueError(f"非法 source_file(须为相对工程根的路径,如 Core/Src/OLED.c): {source_file!r}")
    with open(cmake_lists, encoding="utf-8") as f:
        text = f.read()
    marker = "${CMAKE_CURRENT_SOURCE_DIR}/../../"
    entry = f"    {marker}{sf}\n"
    # 幂等:已含该条目标记(大小写不敏感,Windows 文件系统)则直接返回
    if (marker + sf).lower() in text.lower():
        return f"{sf} 已在源列表中,无需重复添加"
    # 选插入锚点:优先 stm32f1xx_it.c(接近列表尾部),退而求其次 main.c(列表首项)
    anchors = [
        f"    {marker}Core/Src/stm32f1xx_it.c",
        f"    {marker}Core/Src/main.c",
    ]
    hit = next((a for a in anchors if a in text), None)
    if hit is None:
        raise ValueError(
            f"无法在 {cmake_lists} 中找到插入锚点(MX_Application_Src 中 main.c/stm32f1xx_it.c "
            f"条目均不存在);请检查工程 CMakeLists.txt 结构"
        )
    text = text.replace(hit, entry + hit, 1)
    with open(cmake_lists, "w", encoding="utf-8") as f:
        f.write(text)
    return f"已将 {sf} 加入 {cmake_lists}"



def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()