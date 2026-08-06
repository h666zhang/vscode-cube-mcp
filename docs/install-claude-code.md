# 在 Claude Code 中安装与配置 vscode-cube-mcp

> 适用于:Windows 系统 + Claude Code(Anthropic 终端版 CLI)。
> 目标是让 Claude Code 直接获得 STM32CubeMX 工程配置能力(加载 .ioc、改引脚/外设、生成 HAL 代码)。

## 前提条件

使用本 MCP server 前,目标机器必须已安装:

| 依赖 | 版本/说明 | 获取方式 |
|------|-----------|----------|
| Python | >= 3.10,安装时务必勾选 **Add Python to PATH** | [python.org](https://www.python.org/downloads/) |
| STM32CubeMX | 6.x(任意版本,含 `-q` 脚本模式) | [ST 官网免费下载](https://www.st.com/en/development-tools/stm32cubemx.html) |

> **为什么必须安装 STM32CubeMX**:本 MCP 的所有能力(解析 .ioc、配置时钟树、生成 HAL 代码)均由本机 `STM32CubeMX.exe` 的官方命令行模式完成,server 只负责拼接脚本、调用进程、过滤输出。它不包含、也不修改 CubeMX 的任何代码。请遵守 ST 的许可条款。

## 第 1 步:安装 MCP 包

打开 PowerShell(**新开窗口**,确保能读到最新 PATH),执行:

```powershell
pip install vscode-cube-mcp
```

验证安装:

```powershell
python -m pip show vscode-cube-mcp
```

能显示版本信息即为安装成功。

> **常见错误**:若提示 `pip 不是内部或外部命令`,说明 Python 未加入 PATH(重装时勾选 Add to PATH);若装包成功但启动时报 `No module named 'cubemx_mcp'`,说明 `python` 指向了其他解释器,将下文命令中的 `python` 统一替换为 `py` 重试。

## 第 2 步:注册到 Claude Code

使用 `claude mcp add` 注册 stdio server,并通过 `--env` 传入两个必填环境变量:

```powershell
claude mcp add vscode-cube-mcp `
  --env ST_CUBEMX_EXE="C:\你的\STM32CubeMX.exe完整路径" `
  --env ST_CUBEMX_ALLOWED_ROOTS="C:\你的\工程根目录" `
  -- python -m cubemx_mcp
```

参数说明:

| 参数 | 说明 |
|------|------|
| `ST_CUBEMX_EXE` | `STM32CubeMX.exe` 的完整路径(必填) |
| `ST_CUBEMX_ALLOWED_ROOTS` | 允许 MCP 访问的工程根目录;多个目录用 `;` 分隔(必填) |
| `ST_CUBEMX_TIMEOUT` | 可选,单次 CubeMX 调用超时秒数,默认 `240`;机器慢可调大,如 `--env ST_CUBEMX_TIMEOUT=600` |

> 语法要点:`--`(双横线)之后是 server 的启动命令与参数,`--env` 等选项必须放在 `--` 之前;`--env` 支持多个 `KEY=value` 对。

若需要覆盖到其他 MCP 客户端(非 Claude Code),也可直接写 JSON 配置(如 `~/.claude.json` 的 `mcpServers` 段或项目级 `.mcp.json`):

```json
{
  "mcpServers": {
    "vscode-cube-mcp": {
      "command": "python",
      "args": ["-m", "cubemx_mcp"],
      "env": {
        "ST_CUBEMX_EXE": "C:/你的/STM32CubeMX.exe完整路径",
        "ST_CUBEMX_ALLOWED_ROOTS": "C:/你的/工程根目录"
      }
    }
  }
}
```

> JSON 中 Windows 路径建议使用正斜杠(`C:/...`)或双反斜杠(`C:\\...`)。

## 第 3 步:验证连接

```powershell
claude mcp list
```

状态为 `✔ Connected` 即注册成功。随后**重开一个新的 claude 会话**(使配置生效),并让 AI 执行一次真实调用验证,例如:

> 用 `cubemx_load` 加载 `C:\你的\工程目录\xxx.ioc`,回读关键配置。

能返回 .ioc 的配置内容即完全就绪。

## 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `claude mcp list` 显示 `✘ Failed to connect` | 环境变量路径错误;或 `python` 指向的解释器未安装本包 | 核对路径;命令中 `python` 改为 `py` 后重试 |
| AI 报"找不到 STM32CubeMX" | `ST_CUBEMX_EXE` 路径不正确,或修改后未重启会话 | 用 `Test-Path "路径"` 确认文件存在;重开 claude 会话 |
| 报"路径不在允许范围内" | 工程目录不在 `ST_CUBEMX_ALLOWED_ROOTS` 白名单内 | 将工程目录加入白名单(多个用 `;`)后重启 |
| 调用慢或超时 | CubeMX 冷启动慢属正常;或超时阈值过低 | 增加 `ST_CUBEMX_TIMEOUT`(如 600);确认无残留 CubeMX/Java 进程 |
| CubeMX 弹出 "Resolve Clock Issues" | 工程 .ioc 时钟树不自洽(工程配置问题,非本 MCP 缺陷) | 见仓库 `README.dev-notes.md`「从零配置时钟实战」 |

## 卸载

```powershell
claude mcp remove vscode-cube-mcp
pip uninstall vscode-cube-mcp
```

## 相关文档

- [`README.md`](../README.md):项目总览、快速开始、完整环境变量表、通用 FAQ
- [`README.dev-notes.md`](../README.dev-notes.md):开发笔记、踩坑记录、能力边界
- [`templates/README.md`](../templates/README.md):各外设(GPIO/I2C/TIM/时钟)配置命令与实测状态
