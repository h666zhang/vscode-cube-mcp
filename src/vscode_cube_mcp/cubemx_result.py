#!/usr/bin/env python3
"""调用过程的"步骤"与"错误码":任何失败都要能看出卡在哪一步、什么原因、可以怎么办。

定位原则:
  - 第一行给结论:哪一步 + 错误码 + 一句话说明;
  - 第二行起给依据:走过的步骤、建议、CubeMX 的原始输出。

依赖:无(不依赖 CubeMX,也不依赖 cubemx_mcp),可以单独测试。
"""

from __future__ import annotations

# 调用流程里可能卡住的位置,按实际执行顺序排列
STAGES = (
    "validate_args",  # 参数校验
    "check_path",  # 路径是否在允许目录内
    "locate_template",  # 找芯片模板
    "load_knowledge",  # 查知识库
    "resolve_intent",  # 把意图翻译成命令序列
    "compose_ioc",  # 改 .ioc 文本
    "run_cubemx",  # 调 CubeMX 命令行
    "readback_verify",  # 回读校验
    "post_check",  # 生成之后的检查
)

# 错误码 -> (卡在哪一步, 说明, 建议)
ERRORS = {
    "BAD_ARGUMENT": ("validate_args", "参数不合法", "按提示改正参数"),
    "PATH_NOT_ALLOWED": (
        "check_path",
        "路径不在允许目录内",
        "把工程放到 ST_CUBEMX_ALLOWED_ROOTS 里,或改这个环境变量",
    ),
    "IOC_NOT_FOUND": ("check_path", ".ioc 文件不存在", "确认路径是否写对"),
    "NOT_IOC_FILE": ("check_path", "不是 .ioc 文件", "把路径指向 .ioc 文件"),
    "MCU_TEMPLATE_MISSING": (
        "locate_template",
        "没有这个芯片的模板",
        "把该芯片的 .ioc 放进 templates/<mcu>.ioc,或用 template 参数指定一个模板",
    ),
    "PERIPHERAL_NOT_FOUND": ("compose_ioc", ".ioc 里没有这个外设", "确认外设名,如 TIM3 / I2C1"),
    # 下面几个是后面任务(知识库/意图)要用的,先定义好,避免以后改错误码
    "UNKNOWN_PERIPHERAL": ("load_knowledge", "知识库还没收录这个外设", "先在 GUI 里配一次,或跑 cubemx_probe 收录"),
    "UNKNOWN_PARAM": ("load_knowledge", "知识库还没收录这个参数", "用 cubemx_help 看这个外设支持哪些参数"),
    "UNKNOWN_PIN_SIGNAL": ("resolve_intent", "这个引脚不支持该信号", "从引脚表里挑一个支持的引脚"),
    "PIN_CONFLICT": ("resolve_intent", "引脚已经被别的外设占用", "换一个引脚,或先移除占用它的外设"),
    "RULE_NOT_VERIFIED": ("resolve_intent", "这条规则还没验证过,拒绝执行", "先跑 cubemx_probe 验证这条规则"),
    "READBACK_MISMATCH": ("readback_verify", "写入之后回读不一致", "看下面列出的差异;CubeMX 可能把这一行丢掉了"),
    "CLOCK_DEGRADED": ("post_check", "时钟被降级(HSE 丢失)", "在 GUI 的 Clock Configuration 里把 PLL Source 选回 HSE"),
    # CubeMX 子进程相关
    "CUBEMX_TIMEOUT": ("run_cubemx", "CubeMX 超时被终止", "机器慢可以把 ST_CUBEMX_TIMEOUT 调大"),
    "CUBEMX_EXIT_NONZERO": ("run_cubemx", "CubeMX 退出码不是 0", "看下面的原始输出定位"),
    "CUBEMX_KO": ("run_cubemx", "CubeMX 命令返回 KO", ".ioc 可能不是 6.18 原生生成的;先在 GUI 里打开并保存一次"),
}


def explain(code: str) -> tuple:
    """查错误码,返回 (卡在哪一步, 说明, 建议);不认识的码返回 ("", "", "")。"""
    return ERRORS.get(code, ("", "", ""))


class ToolError(ValueError):
    """带错误码的参数/流程错误。

    继承 ValueError,所以原来 `raise ValueError(...)` 的调用方写法与测试断言不变;
    文本里带上"卡在哪一步"和错误码,方便一眼看出问题在哪。

    形如:
      [check_path|PATH_NOT_ALLOWED] 路径不在白名单内(仅允许 ['C:\\proj']): C:\\evil\\x.ioc
      建议: 把工程放到 ST_CUBEMX_ALLOWED_ROOTS 里,或改这个环境变量
    """

    def __init__(self, code: str, message: str):
        self.code = code
        stage, _desc, hint = explain(code)
        self.stage = stage
        self.hint = hint
        head = f"[{stage}|{code}] " if stage else f"[{code}] "
        tail = f"\n建议: {hint}" if hint else ""
        super().__init__(head + message + tail)


def render(
    tool: str,
    ok: bool,
    exit_code=None,
    code: str = "",
    steps=None,
    output: str = "",
    extra: str = "",
    warnings=None,
) -> str:
    """把一次调用的结果渲染成文本。

    第一行是结论:成功就是 [OK exit=N],失败就是 [FAIL exit=N] tool 卡在 <步骤>: <码> - <说明>。
    之后依次是建议、走过的步骤、警告、extra(调用方要补的说明)、CubeMX 原始输出。

    参数:
      tool:      工具名,如 cubemx_generate
      ok:        是否成功
      exit_code: CubeMX 退出码;传 None 则不显示(如只读导出、纯文本编辑)
      code:      失败时的错误码(见 ERRORS)
      steps:     走过的步骤,形如 [("check_path", "ok"), ("run_cubemx", "FAIL")]
      output:    CubeMX 的原始输出(会加分隔行)
      extra:     附加说明(如工程目录)
      warnings:  警告错误码列表(如 ["CLOCK_DEGRADED"]);成功但有隐患时用
    """
    head = ("[OK" if ok else "[FAIL") + (f" exit={exit_code}" if exit_code is not None else "") + "]"
    lines = []
    if ok:
        lines.append(f"{head} {tool} 完成")
    else:
        stage, desc, hint = explain(code)
        where = stage or "未知步骤"
        if desc:
            lines.append(f"{head} {tool} 卡在 {where}: {code} - {desc}")
        else:
            lines.append(f"{head} {tool} 卡在 {where}" + (f": {code}" if code else ""))
        if hint:
            lines.append(f"  建议: {hint}")
    if steps:
        lines.append("  步骤: " + " -> ".join(f"{name}({status})" for name, status in steps))
    for warn in warnings or []:
        _wstage, wdesc, whint = explain(warn)
        lines.append(f"  警告 {warn}: {wdesc}")
        if whint:
            lines.append(f"    建议: {whint}")
    if extra:
        lines.append(extra)
    if output:
        lines.append("--- CubeMX 输出 ---")
        lines.append(output)
    return "\n".join(lines)


def render_run(tool: str, result: dict, steps=None, extra: str = "", warnings=None) -> str:
    """渲染一次 CubeMX 调用的结果(参数就是 _run_script 的返回值)。"""
    return render(
        tool,
        bool(result.get("ok")),
        result.get("exit_code"),
        result.get("error_code") or "",
        steps=steps,
        output=result.get("output", ""),
        extra=extra,
        warnings=warnings,
    )
