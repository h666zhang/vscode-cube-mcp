#!/usr/bin/env python3
"""Vscode_cube_mcp: MCP server 封装 STM32CubeMX 官方 -q 脚本控制台(无状态子进程)。

每次调用:命令序列写入临时脚本 -> 启动 STM32CubeMX -q -> 超时强杀
-> 过滤 log4j 噪音 -> 检测 KO 失败标记 -> 返回干净输出。

Tools:
  cubemx_help(topic)               内置帮助/可用模板(陌生 agent 建议先调用)
  cubemx_script(script)            任意脚本(原始脚本接口)
  cubemx_load(ioc)                 config load + 回读配置(只读)
  cubemx_configure(ioc, cmds)      load + set 命令序列 + saveas(写回 .ioc)
  cubemx_generate(ioc, project_dir) load + project generate
  cubemx_export_pinout(ioc)        csv pinout 导出(只读)
  cubemx_new_project(name, dir, mcu, cmds) 从零生成新工程(模板+set+generate)
  cubemx_intent(ioc, intents)      按配置意图改 .ioc(内部名由知识库查出来)
  cubemx_probe(ioc, cmds)          探针:在副本上试跑,输出改前改后的差异
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

import intent_resolver
import ioc_diff
import ioc_editor
import knowledge_loader
from cubemx_result import ToolError, render, render_run

# .ioc 的文本编辑在 ioc_editor.py(纯文本操作,不起 CubeMX 进程)。
# 这里用原来的函数名做别名转发,内部调用与既有测试都不用改。
_inject_tim_internal_clock = ioc_editor.inject_tim_internal_clock
_inject_tim_pwm = ioc_editor.inject_tim_pwm
_inject_tim_input_capture = ioc_editor.inject_tim_input_capture
_patch_ioc_identity = ioc_editor.patch_ioc_identity
_remove_peripheral = ioc_editor.remove_peripheral


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
    raise ToolError("PATH_NOT_ALLOWED", f"路径不在白名单内(仅允许 {ALLOWED_ROOTS}): {p}")


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
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_script(script: str, stage: str = "run_cubemx") -> dict:
    """执行 CubeMX 脚本,返回 {ok, output, exit_code, stage, error_code}。

    超时会强杀进程树,防残留 Java 进程占工程文件锁。
    error_code 取值:CUBEMX_TIMEOUT / CUBEMX_EXIT_NONZERO / CUBEMX_KO,成功时为空串。
    """
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
                return {
                    "ok": False,
                    "output": f"TIMEOUT: CubeMX 子进程超过 {TIMEOUT_SECONDS}s 被终止(已强杀进程树)",
                    "exit_code": None,
                    "stage": stage,
                    "error_code": "CUBEMX_TIMEOUT",
                }
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    raw = (stdout or "") + "\n" + (stderr or "")
    output = _cleanup(raw)
    if proc.returncode != 0:
        error_code = "CUBEMX_EXIT_NONZERO"
    elif any(line.strip() == "KO" for line in output.splitlines()):
        error_code = "CUBEMX_KO"
    else:
        error_code = ""
    return {
        "ok": not error_code,
        "output": output,
        "exit_code": proc.returncode,
        "stage": stage,
        "error_code": error_code,
    }


def _ioc_path(ioc: str) -> str:
    ap = _check_path(ioc)
    if not os.path.isfile(ap):
        raise ToolError("IOC_NOT_FOUND", f".ioc 文件不存在: {ap}")
    if not ap.lower().endswith(".ioc"):
        raise ToolError("NOT_IOC_FILE", f"不是 .ioc 文件: {ap}")
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
    raise ToolError(
        "MCU_TEMPLATE_MISSING",
        f"未找到模板 {mcu}.ioc。\n"
        f"可用模板: {', '.join(available) if available else '(无)'}\n"
        f"新芯片模板生成方法:用 STM32CubeMX GUI 新建该芯片工程后保存为 templates/{mcu}.ioc;"
        f'或先调 cubemx_help(topic="templates") 查看模板说明。查找过: {search_dirs}',
    )


@mcp.tool()
def cubemx_help(topic: str = "") -> str:
    """内置帮助:返回本 server 的使用指南、可用模板与各外设配置方法。

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
            "STM32F103C8T6.ioc": "最小模板:72MHz(HSE+PLL×9)、SWD(PA13/14)、SysTick,无外设",
        }
        lines = ["【可用模板(动态扫描 templates/)】"]
        lines += [f"  {n}  {desc.get(n, '(无描述)')}" for n in avail]
        lines.append(
            "【新增芯片】用 CubeMX GUI File→New Project 选芯片,保存为 templates/{mcu}.ioc 后即可用 cubemx_new_project"
        )
        return "\n".join(lines)
    text = knowledge_loader.help_text(t)
    if text:
        return text
    # 未知 topic 与空 topic 都回到完整指南;知识库缺失时给出排查方向,而不是直接抛错
    guide = knowledge_loader.help_text("")
    if guide:
        return guide
    return (
        "知识库读取失败:找不到 knowledge/rules/help.yaml。"
        "请检查 ST_CUBEMX_KNOWLEDGE 环境变量,或重新跑 tools/gen_knowledge_from_db.py"
    )


@mcp.tool()
def cubemx_script(script: str) -> str:
    """执行任意 CubeMX 脚本命令序列(原始脚本接口,原样传给 -q)。

    常用命令示例:
      config load "C:/path/proj.ioc"
      set mode USART1 Asynchronous
      set pin PB13 GPIO_Output
      set gpio parameters PB13 GPIO_Label=LED
      config saveas "C:/path/proj.ioc"
      project generate
    """
    r = _run_script(script)
    return render_run("cubemx_script", r)


@mcp.tool()
def cubemx_load(ioc: str) -> str:
    """加载 .ioc 工程并回读关键配置(只读,不修改任何文件)。

    参数:
      ioc: 工程 .ioc 文件绝对路径(须在允许目录内)
    """
    ap = _ioc_path(ioc)
    r = _run_script(f'config load "{ap}"')
    return render_run("cubemx_load", r)


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
    return render_run("cubemx_configure", r)


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
    return render_run("cubemx_generate", r)


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
            return render_run("cubemx_export_pinout", r)
        with open(csv, encoding="utf-8", errors="replace") as f:
            content = f.read()
        return render("cubemx_export_pinout", True, output=r["output"]) + f"\n--- CSV ---\n{content}"
    finally:
        try:
            os.remove(csv)
        except OSError:
            pass


@mcp.tool()
def cubemx_new_project(
    project_name: str,
    project_dir: str,
    mcu: str = "STM32F103C8T6",
    commands: list = None,
    template: str = "",
    toolchain: str = "CMake",
    couple_files: bool = True,
    clock_source: str = "HSE",
    pll_mul: int = 9,
) -> str:
    """从零生成一个新的 HAL 工程(基于内置模板改造)。

    流程:从 templates/ 选 6.18 原生 .ioc 模板(或显式 template)复制到
    project_dir,改写工程名,依次执行 set 命令,再 project generate 生成 HAL 代码。
    这样无需预先手写 .ioc,即"从零开始"。

    知识提示(生成前建议先调 cubemx_help 查看指南与坑):
      - 模板按 mcu 匹配 templates/{mcu}.ioc(最小模板:仅芯片标识+基础时钟
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
        raise ToolError("BAD_ARGUMENT", f"非法工程名(仅允许字母/数字/下划线): {project_name!r}")
    pd = _check_path(project_dir)
    tpl = _project_template(mcu, template)
    ioc_dst = os.path.join(pd, f"{project_name}.ioc")
    _patch_ioc_identity(
        tpl,
        ioc_dst,
        project_name,
        toolchain=toolchain,
        couple_files=couple_files,
        clock_source=clock_source,
        pll_mul=pll_mul,
    )

    lines = [f'config load "{ioc_dst}"']
    if commands:
        lines.extend(commands)
    lines.append(f'config saveas "{ioc_dst}"')
    r = _run_script("\n".join(lines))
    if not r["ok"]:
        return render_run(
            "cubemx_new_project",
            r,
            steps=[("locate_template", "ok"), ("compose_ioc", "ok"), ("run_cubemx", "FAIL")],
            extra=f"set 命令序列执行失败,已中止生成(避免产出残缺工程)。.ioc 保留在: {ioc_dst}",
        )
    # TIM 内部时钟配置补丁:若命令中有 "set ip parameters TIMx ClockSource TIM_CLOCKSOURCE_INTERNAL",
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
                tim_notes.append(
                    _inject_tim_internal_clock(
                        ioc_dst,
                        m.group(1),
                        prescaler=int(pm.group(1)) if pm else 7200,
                        period=int(pd_m.group(1)) if pd_m else 10000,
                    )
                )
                continue
            # PWM 注入:set ip parameters TIM3 PWM <pin> <signal> [Prescaler n] [Period n] [Pulse n]
            # 例:set ip parameters TIM3 PWM PA6 S_TIM3_CH1 Prescaler 72 Period 100 Pulse 50
            m = re.search(r"set ip parameters (TIM\d+) PWM (\S+) (\S+)", cmd)
            if m:
                pm = re.search(r"\bPrescaler (\d+)", cmd)
                pd_m = re.search(r"\bPeriod (\d+)", cmd)
                pu_m = re.search(r"\bPulse (\d+)", cmd)
                tim_notes.append(
                    _inject_tim_pwm(
                        ioc_dst,
                        m.group(1),
                        m.group(2),
                        m.group(3),
                        prescaler=int(pm.group(1)) if pm else 7200,
                        period=int(pd_m.group(1)) if pd_m else 10000,
                        pulse=int(pu_m.group(1)) if pu_m else 5000,
                    )
                )
                continue
            # 输入捕获注入:set ip parameters TIM2 InputCapture <pin> <signal> [Prescaler n] [Period n]
            # 例:set ip parameters TIM2 InputCapture PA0-WKUP S_TIM2_CH1_ETR Prescaler 72 Period 65535
            m = re.search(r"set ip parameters (TIM\d+) InputCapture (\S+) (\S+)", cmd)
            if m:
                pm = re.search(r"\bPrescaler (\d+)", cmd)
                pd_m = re.search(r"\bPeriod (\d+)", cmd)
                tim_notes.append(
                    _inject_tim_input_capture(
                        ioc_dst,
                        m.group(1),
                        m.group(2),
                        m.group(3),
                        prescaler=int(pm.group(1)) if pm else 72,
                        period=int(pd_m.group(1)) if pd_m else 65535,
                    )
                )
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
    # 时钟保护(默认值,不强制):模板本来是 HSE 外部晶振,生成后 PLLSourceVirtual 丢了就报
    # CLOCK_DEGRADED(CubeMX 对 RCC 执行 set 命令的已知副作用:静默降级成 HSI)。
    warnings = []
    if clock_source.strip().upper() == "HSE":
        try:
            with open(ioc_dst, encoding="utf-8", errors="replace") as f:
                dst_text = f.read()
            if "RCC.PLLSourceVirtual" not in dst_text:
                warnings.append("CLOCK_DEGRADED")
        except OSError:
            pass
    return render_run(
        "cubemx_new_project",
        r,
        steps=[("locate_template", "ok"), ("compose_ioc", "ok"), ("run_cubemx", "ok")],
        extra=f"工程目录: {pd}{note}{note_internal}",
        warnings=warnings,
    )


@mcp.tool()
def cubemx_probe(ioc: str, commands: list, keep_copy: bool = False) -> str:
    """跑一次探针:在副本上执行命令,对比改前改后,拿到可写进知识库的真实差异。

    用在某个外设的写法还没收录的时候:先用它跑出真实差异,再把结果整理进
    knowledge/rules/<外设>.yaml。基线 .ioc 只读,不会被改动。

    参数:
      ioc:       基线 .ioc 绝对路径(只读,不改)
      commands:  CubeMX 脚本命令,如 ["set mode USART2 Asynchronous", "set ip parameters USART2 BaudRate 115200"]
      keep_copy: 是否保留副本(默认执行完删掉);保留时会告诉你在哪
    """
    ap = _ioc_path(ioc)
    probe = f"{ap[:-4]}.probe.ioc"
    shutil.copy2(ap, probe)
    try:
        script = "\n".join([f'config load "{probe}"', *commands, f'config saveas "{probe}"'])
        result = _run_script(script)
        if not result["ok"]:
            return render_run("cubemx_probe", result, steps=[("check_path", "ok"), ("run_cubemx", "FAIL")])
        text = ioc_diff.render_diff(ioc_diff.diff_files(ap, probe))
    finally:
        if not keep_copy:
            try:
                os.remove(probe)
            except OSError:
                pass
    if keep_copy:
        text += f"\n(副本保留在: {probe})"
    return text


@mcp.tool()
def cubemx_intent(ioc: str, intents: list, generate: bool = False) -> str:
    """按"配置意图"改 .ioc:意图里只说要什么,内部名由知识库查出来。

    意图示例(业务参数,代码按定时器时钟换算):
      {"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "freq_hz": 10000, "duty": 0.5}
    也可以直接给寄存器参数(显式给的优先):
      {"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "prescaler": 72, "period": 100, "pulse": 50}
    其它已支持:
      {"peripheral": "GPIO", "pin": "PB13", "mode": "output", "label": "LED"}
      {"peripheral": "I2C1", "pins": ["PB8", "PB9"]}

    引脚与信号名(.ioc 里的 S_TIM3_CH1 之类)一律查表得到;查不到会带错误码拒绝,
    不会猜。改之前自动备份成 .ioc.bak,改完回读校验。

    参数:
      ioc:      工程 .ioc 文件绝对路径(会被写回)
      intents:  意图列表,按顺序应用
      generate: 改完是否顺手 project generate(默认 False,只改配置)
    """
    ap = _ioc_path(ioc)
    plan = intent_resolver.apply_intents(ap, intents)
    steps = [
        ("check_path", "ok"),
        ("load_knowledge", "ok"),
        ("resolve_intent", "ok"),
        ("compose_ioc", "ok"),
    ]
    if plan["commands"]:
        script = "\n".join([f'config load "{ap}"', *plan["commands"], f'config saveas "{ap}"'])
        result = _run_script(script)
        if not result["ok"]:
            return render_run("cubemx_intent", result, steps=[*steps, ("run_cubemx", "FAIL")])
        steps.append(("run_cubemx", "ok"))
    if generate:
        result = _run_script(f'config load "{ap}"\nproject generate')
        if not result["ok"]:
            return render_run("cubemx_intent", result, steps=[*steps, ("run_cubemx", "FAIL")])
        steps.append(("run_cubemx", "ok"))
    # 中断这类"脚本配不了"的,靠注入 .ioc 行;必须放在 CubeMX saveas 之后,否则会被覆盖
    added = ioc_editor.add_lines(ap, plan.get("ioc_lines") or [])
    if added:
        plan["notes"].append("已注入 .ioc 行: " + "; ".join(added))
    return render("cubemx_intent", True, steps=steps, extra="\n".join(plan["notes"]))


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
        raise ToolError("BAD_ARGUMENT", f"非法外设名(仅允许字母/数字/下划线,如 TIM3/I2C1): {peripheral!r}")
    info = _remove_peripheral(ap, peripheral)
    return render(
        "cubemx_remove_peripheral",
        True,
        steps=[("check_path", "ok"), ("compose_ioc", "ok")],
        extra=(
            f"已从 {ap} 移除外设 {peripheral}\n"
            f"删除 {len(info['removed'])} 行相关配置,外设列表已重排(Mcu.IPNb={len(info['keep_ips'])})"
        ),
    )


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
        raise ToolError("PROJECT_NOT_GENERATED", f"找不到 {cmake_lists};请先 project generate 生成工程")
    sf = source_file.replace("\\", "/").strip()
    # 输入校验:必须是相对工程根的普通路径——拒绝绝对路径、盘符、路径穿越(../)、换行注入
    if (
        not sf
        or sf.startswith("/")
        or re.match(r"^[A-Za-z]:", sf)
        or sf.startswith("../")
        or "/../" in sf
        or sf.endswith("/..")
        or "\n" in sf
        or "\r" in sf
    ):
        raise ToolError(
            "BAD_ARGUMENT",
            f"非法 source_file(须为相对工程根的路径,如 Core/Src/OLED.c): {source_file!r}",
        )
    with open(cmake_lists, encoding="utf-8") as f:
        text = f.read()
    marker = "${CMAKE_CURRENT_SOURCE_DIR}/../../"
    entry = f"    {marker}{sf}\n"
    # 幂等:已含该条目标记(大小写不敏感,Windows 文件系统)则直接返回
    if (marker + sf).lower() in text.lower():
        return render(
            "cubemx_add_source",
            True,
            steps=[("check_path", "ok"), ("compose_ioc", "ok")],
            extra=f"{sf} 已在源列表中,无需重复添加",
        )
    # 选插入锚点:优先 stm32f1xx_it.c(接近列表尾部),退而求其次 main.c(列表首项)
    anchors = [
        f"    {marker}Core/Src/stm32f1xx_it.c",
        f"    {marker}Core/Src/main.c",
    ]
    hit = next((a for a in anchors if a in text), None)
    if hit is None:
        raise ToolError(
            "PROJECT_STRUCTURE_UNEXPECTED",
            f"无法在 {cmake_lists} 中找到插入锚点(MX_Application_Src 中 main.c/stm32f1xx_it.c "
            f"条目均不存在);请检查工程 CMakeLists.txt 结构",
        )
    text = text.replace(hit, entry + hit, 1)
    with open(cmake_lists, "w", encoding="utf-8") as f:
        f.write(text)
    return render(
        "cubemx_add_source",
        True,
        steps=[("check_path", "ok"), ("compose_ioc", "ok")],
        extra=f"已将 {sf} 加入 {cmake_lists}",
    )


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
