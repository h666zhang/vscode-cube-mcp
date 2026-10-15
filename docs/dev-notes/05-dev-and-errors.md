# 开发、测试与报错模型

---

## 开发与测试

```bash
pip install -e .                                # 首次:src 布局,把 src/ 装成可导入的包(或靠 pyproject 的 pythonpath)
python -m pytest                                # 单元测试 + 覆盖率门槛(低于 80% 失败)
ruff check . && ruff format --check .            # 风格
```

真跑 CubeMX 的集成测试(每个用例约 10s,会启动 CubeMX,用于验证"写出来的 .ioc CubeMX 真的认"):

```powershell
$env:ST_CUBEMX_RUN_INTEGRATION=1; python -m pytest tests/test_cubemx_mcp.py::TestCubeMXIntegration -v
```

技术栈:Python >= 3.10 + mcp SDK 2.x(stdio);打包 setuptools + build + twine;目标平台 Windows(跨平台可用)。

## 报错模型(错误码表)


## 报错模型(错误码表)

所有失败都带"卡在哪一步 + 错误码 + 建议"。错误码定义在 `cubemx_result.ERRORS`;
参数 / 流程错误抛 `cubemx_result.ToolError`(继承 `ValueError`,消息形如
`[check_path|PATH_NOT_ALLOWED] <原消息>` + 换行 + `建议: …`)。

| 错误码 | 卡在哪一步 | 含义 | 建议 |
|--------|-----------|------|------|
| `BAD_ARGUMENT` | validate_args | 参数不合法 | 按提示改正 |
| `PATH_NOT_ALLOWED` | check_path | 路径不在白名单 | 加进 `ST_CUBEMX_ALLOWED_ROOTS` |
| `IOC_NOT_FOUND` / `NOT_IOC_FILE` | check_path | .ioc 不存在 / 不是 .ioc | 确认路径 |
| `MCU_TEMPLATE_MISSING` | locate_template | 没有该芯片模板 | 放模板或用 `template` 指定 |
| `UNKNOWN_PERIPHERAL` | load_knowledge | 知识库没收录这个外设 | 用 `cubemx_probe` 收录 |
| `UNKNOWN_PARAM` | load_knowledge | 没这种模式 / 参数 | `cubemx_help` 看支持的清单 |
| `UNKNOWN_PIN_SIGNAL` | resolve_intent | 引脚不支持该信号 | 换引脚(报错里列了支持的) |
| `PIN_CONFLICT` | resolve_intent | 引脚已被占用 | 换引脚,或先移除占用者 |
| `RULE_NOT_VERIFIED` | resolve_intent | 规则没实测过 | 先 probe 验证 |
| `PERIPHERAL_NOT_FOUND` | compose_ioc | .ioc 里没有这个外设 | 确认外设名 |
| `CUBEMX_TIMEOUT` / `CUBEMX_EXIT_NONZERO` / `CUBEMX_KO` | run_cubemx | CubeMX 超时 / 退出非 0 / 返回 KO | 看原始输出 |
| `READBACK_MISMATCH` | readback_verify | 写入后回读不一致 | 看列出的缺失行 |
| `CLOCK_DEGRADED` | post_check | HSE 被降级(**警告**,不是失败) | GUI 里把 PLL Source 选回 HSE |

## 本地开发:让宿主跑本地代码(补充上面那条旧记录)


## 本地开发:让宿主跑本地代码(补充上面那条旧记录)

上面那条「`args` 必须指向脚本文件本身」的结论在**旧布局**(模块平铺在仓库根、无 `__main__.py`)下成立,
src 布局后已作废:包内改用相对导入,**直连脚本文件会报**
`ImportError: attempted relative import with no known parent package`,只能走 `-m`:

```toml
[[plugins]]
name    = "Vscode_cube_mcp"
command = "C:\\...\\Python313\\python.exe"
args    = ["-m", "vscode_cube_mcp"]   # 开发目录需先 `pip install -e .`
env     = { ST_CUBEMX_ALLOWED_ROOTS = "C:\\MINE\\STM32Project" }
```

`pip install -e .` 会把 `src/` 挂进 `sys.path`;包外的 `knowledge/` 与 `templates/` 由
`knowledge_loader` / `_template_search_dirs` 向上回溯到仓库根找到。改完**重启宿主**才生效。

> 另注意:`%APPDATA%\reasonix` 是宿主的运行时状态路径,agent 无权写入(会被直接拒绝),
> 这类改动得自己动手。
