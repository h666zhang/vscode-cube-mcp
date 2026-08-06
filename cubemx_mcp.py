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
    tpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    tpl = os.path.join(tpl_dir, f"{mcu}.ioc")
    if not os.path.isfile(tpl):
        raise ValueError(f"未找到模板 {tpl};请将 6.18 原生 .ioc 命名为 {mcu}.ioc 放入 templates/ 目录,或用 template 参数指定")
    return tpl


def _patch_ioc_identity(ioc_src: str, ioc_dst: str, project_name: str) -> None:
    """把模板 .ioc 复制到目标位置,并改写工程标识(ProjectName/ProjectFileName)。"""
    os.makedirs(os.path.dirname(ioc_dst), exist_ok=True)
    with open(ioc_src, encoding="utf-8", errors="replace") as f:
        text = f.read()
    text = re.sub(r"^ProjectManager\.ProjectName=.*$", f"ProjectManager.ProjectName={project_name}", text, flags=re.MULTILINE)
    text = re.sub(r"^ProjectManager\.ProjectFileName=.*$", f"ProjectManager.ProjectFileName={project_name}.ioc", text, flags=re.MULTILINE)
    with open(ioc_dst, "w", encoding="utf-8") as f:
        f.write(text)


# ------------------------------------------------------------------ tools
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
                       commands: list = None, template: str = "") -> str:
    """从零生成一个新的 HAL 工程(基于内置模板改造)。

    流程:从 templates/ 选 6.18 原生 .ioc 模板(或显式 template)复制到
    project_dir,改写工程名,依次执行 set 命令,再 project generate 生成 HAL 代码。
    这样无需预先手写 .ioc,即"从零开始"。

    参数:
      project_name: 工程名(将生成 {project_name}.ioc 与同名 HAL 工程)
      project_dir:  生成目标目录(须在白名单内;已存在目录会直接使用)
      mcu:          芯片型号,用于匹配 templates/{mcu}.ioc(默认 STM32F103C8T6)
      commands:     set 命令列表,如 ["set pin PB13 GPIO_Output", "set ip parameters RCC PLLMUL RCC_PLL_MUL9"]
      template:     可选,直接指定模板 .ioc 路径(优先于 mcu 查找)
    """
    if not project_name or not re.fullmatch(r"[A-Za-z0-9_]+", project_name):
        raise ValueError(f"非法工程名(仅允许字母/数字/下划线): {project_name!r}")
    pd = _check_path(project_dir)
    tpl = _project_template(mcu, template)
    ioc_dst = os.path.join(pd, f"{project_name}.ioc")
    _patch_ioc_identity(tpl, ioc_dst, project_name)

    lines = [f'config load "{ioc_dst}"']
    if commands:
        lines.extend(commands)
    lines.append(f'config saveas "{ioc_dst}"')
    lines.append("project generate")
    r = _run_script("\n".join(lines))
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
    return f"[{'OK' if r['ok'] else 'FAIL'} exit={r['exit_code']}]\n{r['output']}\n工程目录: {pd}{note}"

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