#!/usr/bin/env python3
"""比较两份 .ioc 内容,输出结构化差异 —— 给"探针"(改前 vs 改后)用。

.ioc 里有一批跟"这次配置"无关、每次都会变的键:面板状态、库版本、工程文件名等。
默认忽略它们,免得差异里全是噪音;忽略名单可以传参覆盖或清空。

用法(命令行):
    python ioc_diff.py 旧.ioc 新.ioc
"""

from __future__ import annotations

import sys

# 默认忽略的键(前缀匹配):GUI 面板状态、库版本、与配置无关的工程属性
DEFAULT_IGNORE = (
    "PCC.",
    "PinOutPanel.",
    "KeepUserPlacement",
    "MxDb.Version",
    "MxCube.Version",
    "Mcu.UserName",
    "ProjectManager.ProjectFileName",
    "ProjectManager.ProjectName",
    "ProjectManager.ProjectStructure",
    "ProjectManager.PreviousToolchain",
    "ProjectManager.ProjectBuild",
    "ProjectManager.CustomerFirmwarePackage",
    "ProjectManager.FirmwarePackage",
    "ProjectManager.LastFirmware",
    "ProjectManager.ToolChainLocation",
    "ProjectManager.FirmwarePackageVersion",
)


def parse_ioc(text: str) -> dict:
    """把 .ioc 文本解析成 {键: 值};同一键出现多次时保留最后一个。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def _is_ignored(key: str, ignore) -> bool:
    return any(key.startswith(prefix) for prefix in ignore)


def diff_texts(old_text: str, new_text: str, ignore=DEFAULT_IGNORE) -> dict:
    """比较两份 .ioc 文本,返回 {"added": [...], "removed": [...], "changed": [...]}。"""
    old = parse_ioc(old_text)
    new = parse_ioc(new_text)
    added = [f"{k}={v}" for k, v in new.items() if k not in old and not _is_ignored(k, ignore)]
    removed = [f"{k}={v}" for k, v in old.items() if k not in new and not _is_ignored(k, ignore)]
    changed = [
        f"{k}: {old[k]} -> {new[k]}" for k in new if k in old and old[k] != new[k] and not _is_ignored(k, ignore)
    ]
    return {"added": sorted(added), "removed": sorted(removed), "changed": sorted(changed)}


def diff_files(old_path: str, new_path: str, ignore=DEFAULT_IGNORE) -> dict:
    """比较两个 .ioc 文件,额外带一个 summary 字符串。"""
    with open(old_path, encoding="utf-8", errors="replace") as f:
        old_text = f.read()
    with open(new_path, encoding="utf-8", errors="replace") as f:
        new_text = f.read()
    result = diff_texts(old_text, new_text, ignore)
    result["summary"] = f"+{len(result['added'])} -{len(result['removed'])} ~{len(result['changed'])}"
    return result


def render_diff(diff: dict, limit: int = 40) -> str:
    """把差异渲染成给人看的文本(每类最多列 limit 行)。"""
    from .cubemx_result import render

    lines = [f"  差异: {diff['summary']}"]
    for title, key in (("新增", "added"), ("修改", "changed"), ("删除", "removed")):
        items = diff.get(key) or []
        if not items:
            continue
        lines.append(f"--- {title} ({len(items)}) ---")
        lines.extend(f"  {item}" for item in items[:limit])
        if len(items) > limit:
            lines.append(f"  ...(还有 {len(items) - limit} 行)")
    return render("cubemx_probe", True, steps=[("check_path", "ok"), ("run_cubemx", "ok")], extra="\n".join(lines))


def main() -> None:
    if len(sys.argv) != 3:
        print("用法: python ioc_diff.py 旧.ioc 新.ioc")
        raise SystemExit(2)
    print(render_diff(diff_files(sys.argv[1], sys.argv[2])))


if __name__ == "__main__":
    main()
