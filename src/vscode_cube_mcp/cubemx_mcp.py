#!/usr/bin/env python3
"""Vscode_cube_mcp: MCP server 封装 STM32CubeMX 官方 -q 脚本控制台(无状态子进程)。

每次调用:命令序列写入临时脚本 -> 启动 STM32CubeMX -q -> 超时强杀
-> 过滤 log4j 噪音 -> 检测 KO 失败标记 -> 返回干净输出。

Tools:
  cubemx_help(topic)               内置帮助/可用模板(陌生 agent 建议先调用)
  cubemx_raw_script(script)            任意脚本(原始脚本接口)
  cubemx_load(ioc)                 config load + 回读配置(只读)
  cubemx_configure(ioc, cmds)      load + set 命令序列 + saveas(写回 .ioc)
  cubemx_generate(ioc, project_dir) load + project generate
  cubemx_export_pinout(ioc)        csv pinout 导出(只读)
  cubemx_new_project(name, dir, mcu, cmds) 从零生成新工程(模板+set+generate)
  cubemx_intent(ioc, intents)      按配置意图改 .ioc(内部名由知识库查出来)
  cubemx_probe(ioc, cmds)          探针:在副本上试跑,输出改前改后的差异
  cubemx_remove_peripheral(ioc, peripheral) 文本方式移除外设
  cubemx_inspect_output(dir, pattern) 看生成产物(文件树/函数签名/匹配行;只读,不给全文)

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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from mcp.server import MCPServer

from . import intent_resolver, ioc_diff, ioc_editor, knowledge_loader, output_inspector
from .cubemx_result import ToolError, render, render_run

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


def _ko_command(output: str) -> str:
    """算出是哪条命令返回了 KO(CubeMX 脚本输出是"命令 / OK|KO"交替出现)。

    失败时用它说清"哪条命令挂了",而不是笼统的"命令返回 KO"。找不到则返回空串。
    """
    pending = ""
    for line in output.splitlines():
        s = line.strip()
        if s == "OK":
            pending = ""
        elif s == "KO":
            return pending
        elif s:
            pending = s
    return ""


# CubeMX 报 OK 却零产出时,日志里会写 `IP not ready for code generation: SPI1`
IP_NOT_READY_RE = re.compile(r"IP not ready for code generation:\s*([A-Za-z0-9_]+)")


def _ip_not_ready(output: str) -> list:
    """从 CubeMX 输出里抓"哪些 IP 没准备好、被跳过生成"。

    这是"某个外设参数非法"的直接原因(实测 2026-10-15:SPI 的 BaudRatePrescaler 让
    波特率超过 18 MBits/s 上限时,CubeMX 记 `IP not ready for code generation: SPI1`,
    然后跳过整个代码生成 —— 报 OK 却零产出)。有它就不该让人去猜"是不是残留实例占了单实例锁"。
    """
    return sorted(set(IP_NOT_READY_RE.findall(output or "")))


def _no_output_reason(output: str, extra: str) -> tuple:
    """零产出时给出准确的错误码与说明,返回 (error_code, extra)。

    没有任何 IP 未就绪的线索时退回 CUBEMX_NO_OUTPUT(那种多半是残留实例占着单实例锁)。
    """
    not_ready = _ip_not_ready(output)
    if not not_ready:
        return "CUBEMX_NO_OUTPUT", extra
    return (
        "IP_NOT_READY",
        extra
        + "\nCubeMX 判定这些外设未就绪、跳过了代码生成: "
        + ", ".join(not_ready)
        + "\n常见原因:该外设参数非法(实测:SPI 的 BaudRatePrescaler 让波特率超过 18 MBits/s 上限)。"
        + '先看 cubemx_load(summary=True) 摘要里的 precheck 字段,或 cubemx_help(topic="spi"|"adc")。',
    )


def _generated_artifacts(gen_dir: str) -> list:
    """列出生成目录里的 HAL 骨架产物(Core/Src/*.c)。

    返回空列表 = CubeMX 虽然报了成功,但一个文件都没生成(实测会遇到:
    残留的 CubeMX 实例占着单实例锁时,-q 进程会把命令"接受"掉却不产出)。
    """
    src = os.path.join(gen_dir, "Core", "Src")
    if not os.path.isdir(src):
        return []
    try:
        names = sorted(n for n in os.listdir(src) if n.endswith(".c"))
    except OSError:
        return []
    return [f"Core/Src/{n}" for n in names]


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


def _javaw_pids() -> set:
    """当前活着的 javaw.exe 的 PID —— CubeMX 真正干活的进程。

    STM32CubeMX.exe 只是 launcher:实测 -q 模式下它 0.1s 就 `exit 0` 并把活交给 javaw。
    所以按 launcher 的 PID 杀进程树**杀不到** javaw —— 它会变成孤儿残留、占着 CubeMX
    的单实例锁,让后续 generate 静默零产出(实测)。非 Windows 返回空集(tasklist 只在
    Windows 上有;其它平台由 _kill_process_tree 的 SIGKILL 分支兜底)。
    """
    if os.name != "nt":
        return set()
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq javaw.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001 -- 只是尽力查一下 javaw,查不到就当没有,不该让整个调用失败
        return set()
    return {m.group(1) for m in re.finditer(r'"javaw\.exe"\s*,\s*"(\d+)"', out or "")}


def _kill_new_javaw(before) -> None:
    """超时兜底:杀掉本次调用**新出现**的 javaw。

    只杀差集,不动调用前就在跑的 javaw(比如用户自己开的 CubeMX GUI)。
    """
    if os.name != "nt":
        return
    for pid in sorted(_javaw_pids() - set(before or ())):
        try:
            subprocess.run(
                ["taskkill", "/PID", pid, "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
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
            before_javaw = _javaw_pids()  # 供超时兜底:只杀本次调用新出现的 javaw
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
                _kill_new_javaw(before_javaw)
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
    ko_command = ""
    if proc.returncode != 0:
        error_code = "CUBEMX_EXIT_NONZERO"
    elif any(line.strip() == "KO" for line in output.splitlines()):
        error_code = "CUBEMX_KO"
        ko_command = _ko_command(output)
    else:
        error_code = ""
    return {
        "ok": not error_code,
        "output": output,
        "exit_code": proc.returncode,
        "stage": stage,
        "error_code": error_code,
        "ko_command": ko_command,
    }


def _ioc_path(ioc: str) -> str:
    ap = _check_path(ioc)
    if os.path.isdir(ap):
        raise ToolError(
            "IOC_NOT_FOUND",
            f"{ap} 是目录,不是 .ioc 文件 —— 这里要传工程/模板自己的 .ioc"
            f"(例如 {os.path.join(ap, os.path.basename(ap) + '.ioc')})",
        )
    if not os.path.isfile(ap):
        raise ToolError("IOC_NOT_FOUND", f".ioc 文件不存在: {ap}")
    if not ap.lower().endswith(".ioc"):
        raise ToolError("NOT_IOC_FILE", f"不是 .ioc 文件: {ap}")
    return ap


def _template_search_dirs() -> list:
    """模板查找路径:①模块同级 templates ②包目录上一级(src 布局下是仓库根)
    ③再上一级 ④安装版 data-files / pip 装 wheel 时相对 sys.prefix。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(here, "templates"),
        os.path.join(os.path.dirname(here), "templates"),
        os.path.join(os.path.dirname(os.path.dirname(here)), "templates"),
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

    什么时候不用我:已经知道要配什么外设、只是想读现有工程时 —— 那直接用 cubemx_load /
    cubemx_intent 更省(本工具只给说明,不碰你的工程)。

    陌生环境(第一次接入本 MCP 的 agent)建议先调用本工具,再开始建工程。
    不传 topic 返回完整指南;传 topic 返回该主题详细说明。

    可用 topic(大小写不敏感):
      流程:new_project / probe / remove / templates
      外设:gpio / i2c / spi / usart / tim / rcc(别名 clock)/ adc / dma / nvic /
            can / rtc / iwdg / wwdg
    传了不存在的 topic 会明确报错并列出全部可用 topic;不传 topic 返回总指南(末尾同样附清单)。
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
        if not t:
            # 总指南末尾附可用 topic 清单(从 help.yaml 算出来,不硬编码)
            text += "\n\n【可用 topic】" + " / ".join(knowledge_loader.help_topics()) + " / templates"
        return text
    if t:
        # 未知 topic 不再静默返回总指南:明确报错 + 列出可用 topic。
        # (宿主的工具描述可能被截断,agent 往往看不到 docstring 里的 topic 清单,
        #  只能靠这个错误发现有哪些 topic —— 实测踩过)
        avail = knowledge_loader.help_topics()
        raise ToolError(
            "BAD_ARGUMENT",
            f"未知 topic: '{topic}';可用 topic: " + " / ".join(avail) + " / templates(不传 topic 返回总指南)",
        )
    guide = knowledge_loader.help_text("")
    if guide:
        return guide + "\n\n【可用 topic】" + " / ".join(knowledge_loader.help_topics()) + " / templates"
    return (
        "知识库读取失败:找不到 knowledge/rules/help.yaml。"
        "请检查 ST_CUBEMX_KNOWLEDGE 环境变量,或重新跑 tools/gen_knowledge_from_db.py"
    )


@mcp.tool()
def cubemx_raw_script(script: str) -> str:
    """执行任意 CubeMX 脚本命令序列(原始脚本接口,原样传给 -q)。

    什么时候不用我:**主链路上没有它的位置**。能用 cubemx_intent 表达就优先用它
    (intent 会自动补时钟/DMA/NVIC 与 CubeMX 认的隐含键);要预演改动就用 cubemx_probe。
    只有 intent / probe 都表达不了的写法,才降到本工具。

    常用命令示例:
      config load "C:/path/proj.ioc"
      set mode USART1 Asynchronous
      set pin PB13 GPIO_Output
      set gpio parameters PB13 GPIO_Label=LED
      config saveas "C:/path/proj.ioc"
      project generate

    不要用 `project path`(实测:CubeMX 6.18 对该命令返回 KO,且带它的批处理里随后的
    project generate 会静默零产出、甚至卡死到超时)。要把工程生成到别的目录,用
    cubemx_generate(ioc, project_dir=...)。
    """
    if re.search(r"(?mi)^\s*project\s+path\b", script):
        raise ToolError(
            "BAD_ARGUMENT",
            "脚本里有 `project path`:CubeMX 6.18 已不支持该命令(返回 KO,并会让随后的 "
            "project generate 静默零产出或卡死)。要生成到别的目录请用 "
            "cubemx_generate(ioc, project_dir=...),它会把 .ioc 复制过去就地生成。",
        )
    r = _run_script(script)
    return render_run("cubemx_raw_script", r)


@mcp.tool()
def cubemx_load(ioc: str, summary: bool = True, via_cubemx: bool = False) -> str:
    """加载 .ioc 工程并回读关键配置(默认只读文本,不修改任何文件)。

    什么时候不用我:要**改**配置时 —— 本工具只读回读,不写任何文件。

    summary=True(默认)时返回一份**结构化摘要** —— 直接解析 .ioc 文本得到的
    IP 列表 / 引脚→信号 / DMA 请求与参数 / 中断 / 时钟 / 各外设参数 / 初始化顺序,
    省得为了"现在配了什么"去读整个 .ioc 原文(摘要里都是 .ioc 原始字符串,
    "这样写合不合法"由知识库判定)。

    其中 `init_order` 是 `.ioc` 里 `ProjectManager.functionlistsort` 的**直读**结果 ——
    它可能暂时落后于生成的代码(例如"移除外设再重新加回"之后:那段不会自动补回,
    要等下一次 generate 由 CubeMX 重建)。离线预检会提示这种情况;想确认真实调用顺序,
    用 `cubemx_inspect_output(project_dir, pattern="MX_.*_Init")` 读 main.c。

    via_cubemx=False(默认)时**只解析文本,秒回**;传 True 才会额外跑一次
    `config load` 让 CubeMX 自己读一遍(用于确认"这份 .ioc CubeMX 认不认",
    代价是要等它启动,通常 10~30s)。传了 True 时返回里会**明确写出结论**
    (`CubeMX 接受这份 .ioc` / `CubeMX 拒绝/加载失败(...)`),不必自己去读 CubeMX 日志。

    参数:
      ioc:        工程 .ioc 文件绝对路径(须在允许目录内)
      summary:    是否返回结构化摘要(默认 True;传 False 则只返回 via_cubemx 的输出)
      via_cubemx: 是否额外让 CubeMX 真加载一次(默认 False,快)

    注意:summary 与 via_cubemx 不能同时为 False(那样没有任何输出,直接报 BAD_ARGUMENT)。
    另外 CubeMX 会省掉一部分参数行(例如 ADC 的 `ContinuousConvMode` / `ScanConvMode`
    不在 .ioc 里),摘要里没有这些键不代表没配 —— 想知道**最终值**就用
    `cubemx_inspect_output(project_dir, pattern="ContinuousConvMode|ScanConvMode")` 读生成的 adc.c。
    """
    ap = _ioc_path(ioc)
    if not summary and not via_cubemx:
        raise ToolError("BAD_ARGUMENT", "summary 和 via_cubemx 至少要一个为 True,否则没有任何输出")
    out = ""
    if via_cubemx:
        r = _run_script(f'config load "{ap}"')
        # 明确给出"认不认"的结论 —— 以前只回显 CubeMX 原始日志,调用方看不出这算过还是没过
        # (实测:有 agent 为此白跑一趟)
        if r.get("ok"):
            verdict = f"[OK] CubeMX 接受这份 .ioc(config load 成功,exit={r.get('exit_code')})"
        else:
            why = f",返回 KO 的命令: {r['ko_command']}" if r.get("ko_command") else ""
            verdict = f"[FAIL] CubeMX 拒绝/加载失败({r.get('error_code')}{why}),见下面的原始输出"
        out = verdict + "\n" + render_run("cubemx_load", r)
    if summary:
        try:
            data = ioc_editor.summarize(ap)
        except OSError as exc:
            return (out + f"\n(结构化摘要解析失败: {exc})").lstrip()
        out += ("\n" if out else "") + "--- 结构化摘要 ---\n" + json.dumps(data, ensure_ascii=False, indent=2)
    return out


@mcp.tool()
def cubemx_configure(ioc: str, commands: list) -> str:
    """加载 .ioc,依次执行 set 命令,最后 saveas 写回原文件。

    什么时候不用我:常规配置优先用 `cubemx_intent` —— 它会自动补时钟/DMA/NVIC 与隐含键;
    手写 set 命令很容易踩"CubeMX 静默忽略"的坑(实测 `set mode ADC1 IN0` 不会补 ADC 时钟,
    下一次 generate 会弹窗卡死)。只有 intent 表达不了的写法才用本工具。

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

    什么时候不用我:只想看或改 .ioc 时(那用 cubemx_load / cubemx_intent) —— 本工具会真的
    启动 CubeMX 跑一遍生成(每次 10~30s)。

    参数:
      ioc: 工程 .ioc 文件绝对路径
      project_dir: 生成目标目录;留空则在 .ioc 所在目录生成。
                   传了别的目录时,先把 .ioc 复制到那里再就地生成(原 .ioc 不动)。
                   强烈建议指向副本/测试目录,避免覆盖现有工程。

    实现说明:CubeMX 6.18 的 `project path` 命令已失效(实测返回 KO,且带它的批处理里
    随后的 project generate 不产出任何文件、甚至卡死到 240s 超时),所以本工具不再发它,
    改用"把 .ioc 复制到目标目录"达到同样效果;生成后还会校验文件是否真的落盘。
    """
    ap = _ioc_path(ioc)
    if project_dir:
        pd = _check_path(project_dir)
        os.makedirs(pd, exist_ok=True)
        if os.path.abspath(pd) != os.path.dirname(ap):
            dst = os.path.join(pd, os.path.basename(ap))
            if os.path.abspath(dst) != os.path.abspath(ap):
                shutil.copy2(ap, dst)
            ap = dst
    gen_dir = os.path.dirname(ap)
    r = _run_script(f'config load "{ap}"\nproject generate')
    if not r["ok"]:
        return render_run("cubemx_generate", r)
    arts = _generated_artifacts(gen_dir)
    if not arts:
        code, why = _no_output_reason(r.get("output", ""), f"生成目录: {gen_dir}")
        return render("cubemx_generate", False, r.get("exit_code"), code, output=r.get("output", ""), extra=why)
    return render_run(
        "cubemx_generate",
        r,
        extra=f"生成目录: {gen_dir}\n生成文件({len(arts)}): "
        + ", ".join(arts)
        + "\n"
        + output_inspector.tree_line(output_inspector.scan(gen_dir)),
    )


@mcp.tool()
def cubemx_inspect_output(project_dir: str, pattern: str = "") -> str:
    """看生成出来的工程产物(只读):文件树 + Core/Src 每个 .c 的函数签名;给 pattern 再回匹配行。

    什么时候不用我:要改配置时(只读);本工具也不展开 Drivers/ 等第三方目录、不返回文件全文。

    什么时候用:
      - 想知道"到底生成了哪些文件"(generate / new_project 返回里的清单只覆盖 Core/Src/*.c)
      - 想知道某个参数的**最终值** —— CubeMX 会省掉一些参数行(例如 ADC 的
        ContinuousConvMode / ScanConvMode 不在 .ioc 里),只有在生成的代码里能看到;
        这时用 pattern="ContinuousConvMode|ScanConvMode" 最直接
      - 想知道某个 .c 里有哪些函数、或某个宏/枚举在代码里是怎么写的

    参数:
      project_dir: 工程目录(须在 ST_CUBEMX_ALLOWED_ROOTS 白名单内),
                   如 cubemx_new_project 的 project_dir / cubemx_generate 的生成目录
      pattern:     可选正则(标准正则语法,可用 `|` 连接多个关键词);给了就在 **整个 Core/ 下**
                   的 .c/.h/.txt/.s/.cmake 里取匹配行(最多 100 行)—— 不只是 Core/Src,
                   Core/Inc 里的宏与声明也会命中

    只返回文件树、函数签名与命中的行,**不返回文件全文**,也不展开 Drivers/ 这类第三方目录。
    """
    info = output_inspector.scan(_check_path(project_dir), pattern)
    lines = [f"[OK] cubemx_inspect_output — {info['root']}", f"文件共 {info['files']} 个:"]
    lines += info["tree"]
    for name, count in sorted(info["folded"].items()):
        lines.append(f"  {name}/  ({count} 个文件,第三方代码已折叠)")
    if info["functions"]:
        lines.append("--- Core/Src 函数 ---")
        for path, sigs in info["functions"].items():
            lines.append(f"  {path}:")
            lines += [f"    {s}" for s in sigs]
    if pattern:
        lines.append(f"--- 匹配 pattern={pattern!r} ---")
        lines += info["matches"] or ["  (无匹配)"]
    lines += [f"  ⚠ {note}" for note in info["notes"]]
    return "\n".join(lines)


@mcp.tool()
def cubemx_export_pinout(ioc: str) -> str:
    """加载 .ioc 并导出当前引脚配置 CSV(只读,CSV 写系统临时目录后读回)。

    什么时候不用我:只想看"哪些引脚被占了"的结构化信息时 —— cubemx_load(summary=True) 更直接;
    本工具产出的是给人/表格软件看的 CSV。

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
    intents: list = None,
    template: str = "",
    toolchain: str = "CMake",
    couple_files: bool = True,
    clock_source: str = "HSE",
    pll_mul: int = 9,
) -> str:
    """从零生成一个新的 HAL 工程(基于内置模板改造)。

    什么时候不用我:工程已经存在、只想加/改外设时 —— 那用 cubemx_intent 就够了
    (本工具会新建整个工程目录、改工程名、写工具链配置)。

    流程:从 templates/ 选 6.18 原生 .ioc 模板(或显式 template)复制到
    project_dir,改写工程名,依次执行意图 / set 命令,注入中断与 DMA 这类"脚本配不了"的键,
    再 project generate 生成 HAL 代码。这样无需预先手写 .ioc,即"从零开始"。

    传 intents 时走的是与 cubemx_intent **同一套**解析:配 ADC 自动补合法时钟、配 DMA 查
    通道表、配 TIM 内部时钟注入标准表达。推荐用它,而不是在 commands 里手写这些命令 ——
    手写 `set mode ADC1 IN0` 不会自动补 ADC 时钟(36MHz 超 F103 上限),generate 会
    弹 [Clock] 对话框把 -q 批处理卡死到超时(实测)。

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
      commands:     set 命令列表(原始接口),如 ["set pin PB13 GPIO_Output",
                    "set ip parameters RCC PLLMUL RCC_PLL_MUL9"]
      intents:      配置意图列表(推荐;与 cubemx_intent 同一套解析),如
                    [{"peripheral": "ADC1", "pins": ["PA0", "PA1"], "dma": {"mode": "circular"}},
                     {"peripheral": "TIM3", "mode": "internal_clock", "prescaler": 72, "period": 100},
                     {"peripheral": "I2C1", "pins": ["PB8", "PB9"]}]
                    intent 的命令排在显式 commands 之前(显式给的可覆盖意图)
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

    # 意图(intents):与 cubemx_intent 共用同一套解析 —— 配 ADC 自动补合法时钟、配 DMA 查
    # 通道表、配 TIM 内部时钟注入标准表达。intent 的命令排在显式 commands 之前(可被覆盖)。
    plan = {"notes": [], "commands": [], "ioc_lines": [], "dma": []}
    if intents:
        plan = intent_resolver.apply_intents(ioc_dst, intents)
    all_commands = [*plan["commands"], *(commands or [])]

    lines = [f'config load "{ioc_dst}"']
    lines.extend(all_commands)
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
    if all_commands:
        for cmd in all_commands:
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
    # 注入类配置(NVIC 中断行、DMA 键组)必须排在 CubeMX saveas 之后(否则被覆盖)、
    # generate 之前(这样本次 generate 就带上它们)。
    added = ioc_editor.add_lines(ioc_dst, plan.get("ioc_lines") or [])
    if added:
        plan["notes"].append("已注入 .ioc 行: " + "; ".join(added))
    for params in plan.get("dma") or []:
        plan["notes"].append(ioc_editor.inject_dma(ioc_dst, **params))

    lines = [f'config load "{ioc_dst}"']
    lines.append("project generate")
    r2 = _run_script("\n".join(lines))
    if not r2["ok"]:
        return render_run(
            "cubemx_new_project",
            r2,
            steps=[("locate_template", "ok"), ("compose_ioc", "ok"), ("run_cubemx", "FAIL")],
            extra=f"配置已写进 .ioc,但 project generate 失败;.ioc 在: {ioc_dst}",
        )
    r = r2
    note_internal = "\n".join(tim_notes) if tim_notes else ""
    note_intent = "\n".join(plan["notes"]) if plan["notes"] else ""
    note_decisions = _decisions_block(plan)
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
    steps = [("locate_template", "ok"), ("compose_ioc", "ok")]
    if intents:
        steps.append(("resolve_intent", "ok"))
    steps.append(("run_cubemx", "ok"))
    extra = f"工程目录: {pd}{note}"
    if note_intent:
        extra += f"\n{note_intent}"
    if note_internal:
        extra += f"\n{note_internal}"
    extra += note_decisions
    # 产出校验:CubeMX 报 OK 也可能一个文件都没生成(残留实例占着单实例锁时会这样),
    # 这种情况必须显式失败 —— 否则调用方会拿着一个空目录继续往下走。
    arts = _generated_artifacts(pd) or _generated_artifacts(gen_dir)
    if not arts:
        code, why = _no_output_reason(
            r.get("output", ""), f"工程目录: {pd}\n.ioc 已生成: {ioc_dst}(但没有 HAL 骨架产出)"
        )
        return render(
            "cubemx_new_project",
            False,
            r.get("exit_code"),
            code,
            steps=[*steps, ("post_check", "FAIL")],
            output=r.get("output", ""),
            extra=why,
        )
    extra += f"\n生成文件({len(arts)}): " + ", ".join(arts)
    extra += "\n" + output_inspector.tree_line(output_inspector.scan(pd))
    problems = ioc_editor.check_semantics(ioc_dst)
    if problems:
        extra += f"\n⚠ 离线预检(纯文本,不需要 CubeMX)发现 {len(problems)} 处问题:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
    return render_run("cubemx_new_project", r, steps=steps, extra=extra, warnings=warnings)


# 探针能做的"键注入"动作(脚本 set 配不了的写法都走这里)
_PROBE_INJECTIONS = {
    "dma": ioc_editor.inject_dma,
    "tim_internal_clock": ioc_editor.inject_tim_internal_clock,
    "tim_pwm": ioc_editor.inject_tim_pwm,
    "tim_input_capture": ioc_editor.inject_tim_input_capture,
}


def _apply_probe_injects(ioc_path: str, injects: list) -> list:
    """在探针副本上执行注入动作,返回说明列表。

    每项形如 {"type": "dma", ...其余参数透传给对应注入函数}:
      {"type": "dma", "request": "ADC1", "channel": "DMA1_Channel1", "mode": "DMA_CIRCULAR"}
      {"type": "tim_internal_clock", "target_tim": "TIM2", "prescaler": 72, "period": 100}
      {"type": "lines", "lines": ["NVIC.USART2_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:true\\:true"]}
    """
    notes = []
    for item in injects:
        if not isinstance(item, dict):
            raise ToolError("BAD_ARGUMENT", f"inject 每一项都要是对象,收到: {item!r}")
        kind = str(item.get("type", "")).strip().lower()
        args = {k: v for k, v in item.items() if k != "type"}
        if kind == "lines":
            added = ioc_editor.add_lines(ioc_path, args.get("lines") or [])
            notes.append("已注入 .ioc 行: " + "; ".join(added) if added else "没有新增行(基线里已有)")
            continue
        func = _PROBE_INJECTIONS.get(kind)
        if func is None:
            known = ", ".join(["lines", *sorted(_PROBE_INJECTIONS)])
            raise ToolError("BAD_ARGUMENT", f"不认识的 inject type {kind!r};可用: {known}")
        notes.append(func(ioc_path, **args))
    return notes


@mcp.tool()
def cubemx_probe(ioc: str, commands: list = None, inject: list = None, keep_copy: bool = False) -> str:
    """跑一次探针:在副本上执行命令或键注入,对比改前改后,拿到可写进知识库的真实差异。

    什么时候不用我:写法已经探明、要正式落盘时 —— 那时该用 cubemx_intent(常规)或
    cubemx_configure(intent 表达不了的 set 命令)。

    用在某个外设/写法的还没收录的时候:先跑出真实差异,再整理进 knowledge/rules/<外设>.yaml。
    基线 .ioc 只读,不会被改动。

    两种输入(可同时给):
      commands: CubeMX 脚本命令(set 类写法),如 ["set mode USART2 Asynchronous"]
      inject:   .ioc 键注入(脚本配不了的写法:DMA / TIM 内部时钟 / NVIC 行等),每项形如
                {"type": "dma", "request": "ADC1", "channel": "DMA1_Channel1"}
                {"type": "tim_internal_clock", "target_tim": "TIM2", "prescaler": 72, "period": 100}
                {"type": "lines", "lines": ["NVIC.USART2_IRQn=true\\:0\\:0\\:…"]}
    给了 inject 时,工具会先把键写进副本,再让 CubeMX load + saveas 走一遍 —— 输出里会明确
    告诉你 CubeMX 是**原样保留**还是**改写/丢弃**了注入内容(这是"它认不认"的关键情报),
    并附第二段差异(注入后 vs 归一化后)。

    参数:
      ioc:       基线 .ioc 绝对路径(只读,不改)
      commands:  CubeMX 脚本命令列表
      inject:    键注入动作列表(见上)
      keep_copy: 是否保留副本(默认执行完删掉);保留时会告诉你在哪
    """
    if not commands and not inject:
        raise ToolError("BAD_ARGUMENT", "commands 和 inject 至少要给一个,否则探针没有可执行的动作")
    ap = _ioc_path(ioc)
    probe = f"{ap[:-4]}.probe.ioc"
    shutil.copy2(ap, probe)
    notes = []
    try:
        if commands:
            script = "\n".join([f'config load "{probe}"', *commands, f'config saveas "{probe}"'])
            result = _run_script(script)
            if not result["ok"]:
                return render_run("cubemx_probe", result, steps=[("check_path", "ok"), ("run_cubemx", "FAIL")])
        injected_text = ""
        after_text = ""
        if inject:
            notes.extend(_apply_probe_injects(probe, inject))
            with open(probe, encoding="utf-8", errors="replace") as f:
                injected_text = f.read()
            result = _run_script(f'config load "{probe}"\nconfig saveas "{probe}"')
            if not result["ok"]:
                return render_run(
                    "cubemx_probe",
                    result,
                    steps=[("check_path", "ok"), ("compose_ioc", "ok"), ("run_cubemx", "FAIL")],
                )
            with open(probe, encoding="utf-8", errors="replace") as f:
                after_text = f.read()
            notes.append(
                "CubeMX **原样保留**了注入内容(load + saveas 没改写)"
                if after_text == injected_text
                else "CubeMX **改动了**注入内容 —— 第二段差异就是它改了哪些"
            )
        text = ioc_diff.render_diff(ioc_diff.diff_files(ap, probe))
        if inject and after_text != injected_text:
            text += "\n\n--- 注入后 vs CubeMX 归一化后 ---\n"
            text += ioc_diff.render_diff(ioc_diff.diff_texts(injected_text, after_text))
        if notes:
            text = "\n".join(notes) + "\n\n" + text
    finally:
        if not keep_copy:
            try:
                os.remove(probe)
            except OSError:
                pass
    if keep_copy:
        text += f"\n(副本保留在: {probe})"
    return text


def _decisions_block(plan: dict) -> str:
    """渲染"本次采用的默认值/推导值" —— 工具自报,省得调用方去猜没写的那几个字段。"""
    items = plan.get("decisions") or []
    if not items:
        return ""
    return "\n本次采用的默认值/推导值(没显式指定的都在这里):\n" + "\n".join(f"  - {d}" for d in items)


@mcp.tool()
def cubemx_intent(ioc: str, intents: list, generate: bool = False) -> str:
    """按"配置意图"改 .ioc:意图里只说要什么,内部名由知识库查出来。

    什么时候不用我:外设/模式不在支持列表里(先用 cubemx_help 看能力),或是"手写 .ioc 键"
    那类 intent 表达不了的写法(那用 cubemx_configure,必要时先 cubemx_probe 预演)。

    意图示例(业务参数,代码按定时器时钟换算):
      {"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "freq_hz": 10000, "duty": 0.5}
    也可以直接给寄存器参数(显式给的优先):
      {"peripheral": "TIM3", "mode": "pwm", "pin": "PA6", "prescaler": 72, "period": 100, "pulse": 50}
    其它已支持:
      {"peripheral": "GPIO", "pin": "PB13", "mode": "output", "label": "LED"}
      {"peripheral": "I2C1", "pins": ["PB8", "PB9"]}

    DMA(挂在外设意图下,等价 GUI 里"在该外设的 DMA Settings 里 Add 一次"):
      {"peripheral": "ADC1", "pins": ["PA0", "PA1"], "dma": {"mode": "circular"}}
      {"peripheral": "USART2", "pins": ["PA2", "PA3"], "dma": {"request": "USART2_RX"}}
    通道从 db 导出的表查出(不猜),对齐/优先级交给 CubeMX 补默认;字段见 rules/dma.yaml。

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
    # 脚本配不了的靠注入 .ioc 行(中断 NVIC、DMA 键组):必须放在 CubeMX saveas 之后
    # (否则会被 saveas 覆盖),又要在 generate 之前(这样本次 generate 就带上它们)。
    added = ioc_editor.add_lines(ap, plan.get("ioc_lines") or [])
    if added:
        plan["notes"].append("已注入 .ioc 行: " + "; ".join(added))
    for params in plan.get("dma") or []:
        plan["notes"].append(ioc_editor.inject_dma(ap, **params))
    if generate:
        result = _run_script(f'config load "{ap}"\nproject generate')
        if not result["ok"]:
            return render_run("cubemx_intent", result, steps=[*steps, ("run_cubemx", "FAIL")])
        arts = _generated_artifacts(os.path.dirname(ap))
        if not arts:
            code, why = _no_output_reason(result.get("output", ""), f"生成目录: {os.path.dirname(ap)}")
            return render(
                "cubemx_intent",
                False,
                result.get("exit_code"),
                code,
                steps=[*steps, ("run_cubemx", "ok"), ("post_check", "FAIL")],
                output=result.get("output", ""),
                extra=why,
            )
        steps.append(("run_cubemx", "ok"))
    extra = "\n".join(plan["notes"]) + _decisions_block(plan)
    problems = ioc_editor.check_semantics(ap)
    if problems:
        extra += f"\n⚠ 离线预检(纯文本,不需要 CubeMX)发现 {len(problems)} 处问题:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
    return render("cubemx_intent", True, steps=steps, extra=extra)


@mcp.tool()
def cubemx_remove_peripheral(ioc: str, peripheral: str) -> str:
    """从 .ioc 中移除一个外设(文本方式)。

    什么时候不用我:只是想改引脚/参数时(那是 cubemx_intent 的活) —— 本工具会把该外设的
    引脚、参数、DMA 关联一起删掉。

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
            f"删除 {len(info['removed'])} 行外设配置、{len(info.get('removed_pins') or [])} 行引脚"
            f"(含 SH 句柄)、{len(info.get('removed_dma') or [])} 行 DMA 配置"
            f"(含通道中断);外设列表已重排(Mcu.IPNb={len(info['keep_ips'])})"
        ),
    )


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
