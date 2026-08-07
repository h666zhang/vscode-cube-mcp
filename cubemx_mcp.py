#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vscode_cube_mcp: MCP server 封装 STM32CubeMX 官方 -q 脚本控制台(无状态子进程)。

每次调用:命令序列写入临时脚本 -> 启动 STM32CubeMX -q -> 超时强杀
-> 过滤 log4j 噪音 -> 检测 KO 失败标记 -> 返回干净输出。

Tools:
  cubemx_script(script)            任意脚本(逃生通道)
  cubemx_load(ioc)                 config load + 回读配置(只读)
  cubemx_configure(ioc, cmds)      load + set 命令序列 + saveas(写回 .ioc)
  cubemx_generate(ioc, project_dir) load + project generate
  cubemx_export_pinout(ioc)        csv pinout 导出(只读)
  cubemx_new_project(name, dir, mcu, cmds) 从零生成新工程(模板+set+generate)

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
    """校验路径在允许根目录下,返回绝对路径。"""
    ap = os.path.abspath(p)
    for root in ALLOWED_ROOTS:
        if ap.lower().startswith(os.path.abspath(root).lower()):
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


def _run_script(script: str) -> dict:
    """执行 CubeMX 脚本,返回 {ok, output, exit_code}。"""
    fd, tmp = tempfile.mkstemp(prefix="cubemx_mcp_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script.strip() + "\n")
            f.write("exit\n")
        with _LOCK:
            proc = subprocess.run(
                [CUBEMX_EXE, "-q", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT_SECONDS,
                cwd=os.path.dirname(CUBEMX_EXE) if os.path.dirname(CUBEMX_EXE) else None,
            )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"TIMEOUT: CubeMX 子进程超过 {TIMEOUT_SECONDS}s 被终止", "exit_code": None}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
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


def _project_template(mcu: str, template: str = "") -> str:
    """解析新建工程的基底 .ioc 模板路径。

    优先用显式 template 参数;否则在 server 同目录 templates/ 下按 mcu 查找
    (如 templates/STM32F103C8T6.ioc)。模板必须是 6.18 原生生成的 .ioc,
    否则 CubeMX 6.18 加载可能报错。
    """
    if template:
        return _ioc_path(template)
    # 模板查找路径:①源码目录 __file__/templates ②安装版 data-files(site-packages 上级)
    # ③data-files 实际安装位置 sys.prefix/templates(pip 装 wheel 时相对 sys.prefix)
    search_dirs = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
        os.path.join(sys.prefix, "templates"),
    ]
    for tpl_dir in search_dirs:
        tpl = os.path.join(tpl_dir, f"{mcu}.ioc")
        if os.path.isfile(tpl):
            return tpl
    raise ValueError(
        f"未找到模板 {mcu}.ioc;请将 6.18 原生 .ioc 命名为 {mcu}.ioc 放入 templates/ 目录,"
        f"或用 template 参数指定。查找过: {search_dirs}"
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
def _tim_make_internal_clock(ioc_path: str, target_tim: str) -> str:
    """借壳法:把 .ioc 中目标 TIM(如 TIM2)做成内部时钟。

    若模板里已有另一个原生内部时钟 TIM(如 TIM3,VP_TIM3_VS_ClockSourceINT + 内部参数),
    把它的表达整体改名给 target_tim,并删掉 target_tim 的 ETR 表达(PA0/SH/ETR 参数)。
    这样 target_tim 继承了 6.18 信任的原生内部时钟表达,generate 不会清理。

    参数:
      ioc_path:    .ioc 文件绝对路径(会被改写)
      target_tim:  目标 TIM 名,如 TIM2
    返回:描述字符串
    """
    with open(ioc_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines(keepends=True)

    # 找可借壳的内部时钟 TIM(有 VP_<TIM>_VS_ClockSourceINT 且 Mode=Internal 的)
    donor = None
    for ln in lines:
        m = re.match(r"VP_(TIM\d+)_VS_ClockSourceINT\.Mode=Internal", ln.strip())
        if m:
            donor = m.group(1)
            break
    if donor is None:
        return f"模板中无内部时钟 TIM 可借壳({target_tim} 无法自动改为内部时钟);请用 templates/STM32F103C8T6_tim2_internal.ioc 作模板"
    if donor == target_tim:
        return f"{target_tim} 已是内部时钟,无需处理"

    out = []
    pin_idx = 0
    seen_nvic = False
    for ln in lines:
        s = ln.strip()
        # 1) 删 target_tim 的 ETR 表达:PA0 相关、SH.S_<target>、<target>. 参数(原 ETR)
        if s.startswith("SH.S_TIM") and donor not in s:
            continue  # 删 SH.S_<target_tim> 相关(ETR 信号)
        # 删 target_tim 的 NVIC 行(改名来的那行会替代它,避免重复)
        if re.match(rf"^NVIC\.{re.escape(target_tim)}_IRQn=", s):
            continue
        if re.match(rf"^Mcu\.Pin\d+=PA0-WKUP$", s):
            continue  # 删 PA0 引脚条目
        if s.startswith("PA0-WKUP."):
            continue  # 删 PA0 参数行
        if re.match(rf"^{re.escape(target_tim)}\.", s):
            continue  # 删原 target_tim 的所有参数行(ETR 残留)
        # 2) donor 的 Mcu.IP 条目删除(它改名给 target_tim)
        if re.match(rf"^Mcu\.IP\d+={re.escape(donor)}$", s):
            continue
        # donor 参数行(TIM3.*)改名保留为 target_tim.*
        if re.match(rf"^{re.escape(donor)}\.", s):
            ln = ln.replace(donor, target_tim, 1)
            out.append(ln)
            continue
        # 3) 删 donor 的 VP 引脚条目(VP_TIM3_VS... 将被改名为 VP_TIM2 并重新编号)
        if re.match(rf"^Mcu\.Pin\d+=VP_{re.escape(donor)}_VS_ClockSourceINT$", s):
            continue
        # 4) Mcu.IPx 重排(保留其余)
        if re.match(r"Mcu\.IP\d+=", s):
            name = s.split("=", 1)[1]
            out.append(f"Mcu.IP{pin_idx}={name}\n" if False else ln)
            continue
        # 5) Mcu.Pin 重排
        if re.match(r"Mcu\.Pin\d+=", s):
            name = s.split("=", 1)[1]
            out.append(f"Mcu.Pin{pin_idx}={name}\n")
            pin_idx += 1
            continue
        if s.startswith("Mcu.PinsNb="):
            # 在 PinsNb 前内联 VP 条目(必须在 Mcu.Pin 列表内,否则 CubeMX 不认)
            out.append(f"Mcu.Pin{pin_idx}=VP_{target_tim}_VS_ClockSourceINT\n")
            pin_idx += 1
            out.append(f"Mcu.PinsNb={pin_idx}\n")
            continue
        # 6) donor -> target_tim 改名(VP、NVIC、参数、functionlistsort、信号值)
        if donor in s:
            # VP 参数行(Mode/Signal)跳过,由追加逻辑统一生成,避免重复
            if f"VP_{donor}_VS_ClockSourceINT." in s:
                continue
            # functionlistsort:删 MX_TIM4_Init 段;TIM3 段改名 TIM2 段(若已有则跳过)
            if "functionlistsort" in s:
                ln = ln.replace(",5-MX_TIM4_Init-TIM4-false-HAL-true", "")
                ln = ln.replace(f",6-MX_{donor}_Init-{donor}-false-HAL-true", "")
                ln = ln.replace(f",6-MX_{donor}_Init-{donor}-false", "")
                # 若改名的 TIM3 段和原 TIM2 段重复,只保留一个
                ln = ln.replace(f"MX_{donor}_Init", f"MX_{target_tim}_Init")
                ln = ln.replace(f"{donor}-false", f"{target_tim}-false")
                ln = ln.replace(f"_{donor}_Init", f"_{target_tim}_Init")
                ln = ln.replace(donor, target_tim)
                out.append(ln)
                continue
            ln = ln.replace(f"VP_{donor}_VS_ClockSourceINT", f"VP_{target_tim}_VS_ClockSourceINT")
            ln = ln.replace(f"{donor}_VS_ClockSourceINT", f"{target_tim}_VS_ClockSourceINT")
            # NVIC 去重:改名后的 TIMx_IRQn 若已出现则跳过
            if "NVIC." in ln and f"NVIC.{target_tim}_IRQn" in ln:
                if any(f"NVIC.{target_tim}_IRQn" in x for x in out):
                    continue
                ln = ln.replace(f"NVIC.{donor}_IRQn", f"NVIC.{target_tim}_IRQn")
            ln = ln.replace(f"MX_{donor}_Init", f"MX_{target_tim}_Init")
            ln = ln.replace(f"{donor}-false", f"{target_tim}-false")
            ln = ln.replace(f"_{donor}_Init", f"_{target_tim}_Init")
            ln = ln.replace(donor, target_tim)
            out.append(ln)
            continue
        out.append(ln)

    # 追加 VP 内部时钟参数行(在 board=custom 前;Mcu.Pin 条目已内联进列表)
    insert_before = "board=custom"
    vp_lines = (
        f"VP_{target_tim}_VS_ClockSourceINT.Mode=Internal\n"
        f"VP_{target_tim}_VS_ClockSourceINT.Signal={target_tim}_VS_ClockSourceINT\n"
    )
    new_out = []
    inserted = False
    for ln in out:
        if not inserted and ln.strip() == insert_before:
            new_out.append(vp_lines)
            inserted = True
        new_out.append(ln)
    if not inserted:
        new_out.append(vp_lines)
    # 修正 Mcu.IPNb(重新数 Mcu.IPx)
    final = []
    ip_count = 0
    for ln in new_out:
        if re.match(r"Mcu\.IP\d+=", ln):
            ip_count += 1
        if re.match(r"Mcu\.IPNb=", ln):
            final.append(f"Mcu.IPNb={ip_count}\n")
            continue
        final.append(ln)
    with open(ioc_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(final)
    return f"借壳法完成:{donor} 改名 {target_tim},{target_tim} 现为内部时钟"


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
      - 模板按 mcu 匹配 templates/{mcu}.ioc;TIM2 等需"内部时钟借壳法"的外设,
        优先用 templates/STM32F103C8T6_tim2_internal.ioc 或 STM32F103C8T6_tim_template.ioc
        作 template 参数,否则脚本 set 无法把 TIM2 从 ETR 改成内部时钟。
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
    # 借壳法:若命令中有 "set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL",
    # 自动把该 TIM 做成内部时钟(借用模板原生内部时钟 TIM 改名,见 _tim_make_internal_clock)。
    tim_notes = []
    if commands:
        for cmd in commands:
            m = re.search(r"set ip parameters (TIM\d+) ClockSource TIM_CLOCKSOURCE_INTERNAL", cmd)
            if m:
                tim_notes.append(_tim_make_internal_clock(ioc_dst, m.group(1)))
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
    with open(ap, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    # 收集要删的行号:精确匹配 Mcu.IPx=<peripheral> 的行
    ip_line_idx = None
    for i, ln in enumerate(lines):
        if re.fullmatch(rf"Mcu\.IP\d+={re.escape(peripheral)}\r?\n", ln):
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
        m = re.match(r"Mcu\.IP(\d+)=(.*)\r?\n", ln)
        if m and m.group(2).strip() != peripheral:
            keep_ips.append(m.group(2).strip())
    # 重写所有行
    new_out = []
    ip_counter = 0
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
        if "functionlistsort" in ln:
            # 移除包含该外设的初始化段(形如 ,5-MX_TIM3_Init-TIM3-false-HAL-true 或开头段)
            ln = re.sub(rf",\d+-[A-Za-z0-9_]*_Init-{re.escape(peripheral)}-false-HAL-true", "", ln)
            ln = re.sub(rf"(^|,)\d+-[A-Za-z0-9_]*_Init-{re.escape(peripheral)}-false-HAL-true,", r"\1", ln)
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
    sf = source_file.replace("\\", "/")
    with open(cmake_lists, encoding="utf-8") as f:
        text = f.read()
    marker = "${CMAKE_CURRENT_SOURCE_DIR}/../../"
    entry = f"    {marker}{sf}\n"
    if f"../../{sf}" in text:
        return f"{sf} 已在源列表中,无需重复添加"
    anchor = "    ${CMAKE_CURRENT_SOURCE_DIR}/../../Core/Src/stm32f1xx_it.c"
    if anchor not in text:
        # 退而求其次:插到 MX_Application_Src 段的第一个条目前
        anchor = "    ${CMAKE_CURRENT_SOURCE_DIR}/../../Core/Src/main.c"
    text = text.replace(anchor, entry + anchor, 1)
    with open(cmake_lists, "w", encoding="utf-8") as f:
        f.write(text)
    return f"已将 {sf} 加入 {cmake_lists}"



def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()