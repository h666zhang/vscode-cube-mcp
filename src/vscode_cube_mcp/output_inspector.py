"""看生成出来的工程产物(只读):文件树、Core/ 下源文件的函数签名、按 pattern 取匹配行。

给 agent 一个正当的"看生成结果"入口 —— CubeMX 会把某些参数行省掉(例如 ADC 的
`ContinuousConvMode` / `ScanConvMode` 不在 `.ioc` 里),最终值只有生成出来的代码里能看到。

`pattern` 是在 **整个 `Core/`** 下的 `.c/.h/.txt/.s/.cmake` 里搜(不止 `Core/Src`),
所以 `Core/Inc` 里的宏/声明也能命中。

这里**不返回文件全文**,只给结构、函数签名和命中的行 —— 避免它变成"读任意文件"的后门。
"""

from __future__ import annotations

import os
import re

from .cubemx_result import ToolError

# 返回体积上限(别让一个大工程把上下文塞满)
MAX_TREE_LINES = 120
MAX_MATCHES = 100
MAX_MATCH_FILES = 2000
MAX_LINE_CHARS = 200
MAX_FILE_BYTES = 2 * 1024 * 1024

# 第三方代码目录:只计数不展开(它们不是用户的产物)
FOLD_DIRS = ("Drivers", "Middlewares", "Middlewares_ST", "CMSIS")

# Core/ 下参与"取匹配行"的文本类型
SCAN_SUFFIXES = (".c", ".h", ".txt", ".s", ".cmake")

# 顶层函数定义:整行形如 `返回类型 名字(参数)` —— 所以 `if (x) {` 这种控制语句不会被误认
_FUNC_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_ \t\*]*?[ \t\*])\**([A-Za-z_]\w*)[ \t]*\(([^()]*)\)[ \t]*$")
_CONTROL_WORDS = {"if", "for", "while", "switch", "return", "sizeof", "else", "do", "case"}


def function_signatures(text: str) -> list:
    """抽出顶层函数定义(保留 `static` 修饰;不返回函数体)。"""
    out = []
    for line in text.splitlines():
        m = _FUNC_LINE.match(line)
        if not m:
            continue
        prefix, name, params = m.group(1).strip(), m.group(2), m.group(3)
        if name in _CONTROL_WORDS or prefix in ("", "typedef", "extern"):
            continue
        out.append(f"{prefix} {name}({params})")
    return out


def _read(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def file_tree(root: str) -> tuple[list, int, dict, dict, list]:
    """扫文件树。

    返回 (明细行, 文件总数, {目录: 文件数}, {被折叠的目录: 文件数}, 根目录下的文件名)。
    第三方目录(Drivers/ 等)只计数不展开。
    """
    details: list = []
    per_dir: dict = {}
    folded: dict = {}
    root_files: list = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        top = "" if rel == "." else rel.split("/", 1)[0]
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if top in FOLD_DIRS:
            if top not in folded:
                folded[top] = sum(len(fs) for _, _, fs in os.walk(os.path.join(root, top)))
                total += folded[top]
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            total += 1
            per_dir[rel] = per_dir.get(rel, 0) + 1
            if rel == ".":
                root_files.append(name)
            details.append(f"  {name if rel == '.' else f'{rel}/{name}'}  ({size} B)")
    return details[:MAX_TREE_LINES], total, per_dir, folded, sorted(root_files)


def core_functions(root: str) -> dict:
    """{Core/Src/x.c: [函数签名, ...]} —— 只抽 Core/Src 下的 .c(那才是可读的骨架)。"""
    out: dict = {}
    base = os.path.join(root, "Core", "Src")
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if not name.endswith(".c"):
            continue
        text = _read(os.path.join(base, name))
        if text is None:
            continue
        sigs = function_signatures(text)
        if sigs:
            out[f"Core/Src/{name}"] = sigs
    return out


def grep_core(root: str, pattern: str) -> tuple[list, str]:
    """在 Core/ 下的 .c/.h 里取匹配行;返回 (行列表, 截断说明)。"""
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolError("BAD_ARGUMENT", f"pattern 不是合法正则: {exc}") from exc
    base = os.path.join(root, "Core")
    if not os.path.isdir(base):
        base = root
    hits: list = []
    scanned = 0
    note = ""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in FOLD_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if not name.endswith(SCAN_SUFFIXES):
                continue
            scanned += 1
            if scanned > MAX_MATCH_FILES:
                return hits, f"文件数超过 {MAX_MATCH_FILES},只扫了前面这些"
            full = os.path.join(dirpath, name)
            text = _read(full)
            if not text:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:MAX_LINE_CHARS]}")
                    if len(hits) >= MAX_MATCHES:
                        return hits, f"匹配行超过 {MAX_MATCHES},已截断"
    return hits, note


def scan(project_dir: str, pattern: str = "") -> dict:
    """扫一个工程目录,返回结构化结果(文件树 / 目录计数 / 函数签名 / 匹配行)。"""
    root = os.path.abspath(project_dir)
    if not os.path.isdir(root):
        raise ToolError("BAD_ARGUMENT", f"目录不存在: {root}(先 cubemx_new_project / cubemx_generate 生成)")
    details, total, per_dir, folded, root_files = file_tree(root)
    out = {
        "root": root,
        "files": total,
        "dirs": per_dir,
        "folded": folded,
        "root_files": root_files,
        "tree": details,
        "functions": core_functions(root),
        "matches": [],
        "notes": [],
    }
    shown = len(details)
    if total - sum(folded.values()) > shown:
        out["notes"].append(f"明细只列了前 {shown} 个文件(工程共 {total} 个,其余在折叠目录里)")
    if pattern:
        out["matches"], note = grep_core(root, pattern)
        if note:
            out["notes"].append(note)
    return out


def tree_line(info: dict, limit: int = 4) -> str:
    """一行式结构摘要,给 cubemx_generate / cubemx_new_project 的返回里附带。"""
    parts = [f"{d}/ {n}" for d, n in sorted(info["dirs"].items()) if d != "."]
    parts = parts[:limit] + ([f"…共 {len(info['dirs']) - 1} 个目录"] if len(parts) > limit else [])
    fold = "".join(f", {d}/ 折叠 {n} 个" for d, n in sorted(info["folded"].items()))
    roots = ", ".join(info["root_files"][:6]) + ("…" if len(info["root_files"]) > 6 else "")
    return f"工程文件共 {info['files']} 个:{' '.join(parts)};根目录: {roots}{fold}"
