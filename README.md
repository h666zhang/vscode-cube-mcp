# Vscode_cube_mcp

[![GitHub](https://img.shields.io/badge/GitHub-h666zhang%2Fvscode--cube--mcp-181717%3Flogo%3Dgithub)](https://github.com/h666zhang/vscode-cube-mcp)  [![PyPI](https://img.shields.io/pypi/v/vscode-cube-mcp)](https://pypi.org/project/vscode-cube-mcp/)  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MCP(Model Context Protocol) server,封装 **STM32CubeMX** 官方命令行脚本模式(`-q`),
让 AI 助手可以直接加载 .ioc 工程、改引脚/外设配置、生成 HAL 代码、导出引脚表,全程无需打开 CubeMX GUI。

## 功能一览

| 工具 | 说明 |
|------|------|
| `cubemx_load` | 加载 .ioc 并回读关键配置(只读) |
| `cubemx_configure` | 加载 .ioc,执行 `set` 命令序列并写回 |
| `cubemx_generate` | 加载 .ioc 并生成 HAL 工程 |
| `cubemx_export_pinout` | 导出引脚配置 CSV(只读) |
| `cubemx_new_project` | **从零生成新工程**(内置模板库,无需预先 .ioc) |
| `cubemx_remove_peripheral` | 从 .ioc 移除外设 |
| `cubemx_add_source` | 把自定义源文件加入 CMake 源列表 |
| `cubemx_script` | 任意 CubeMX 脚本命令序列(高级/逃生通道) |

## 要求

- **Python >= 3.10**(Windows 建议用官方安装版,不要用 msys2/Git 自带的 python,见 FAQ)
- **STM32CubeMX 6.x**(ST 专有软件,请从 [ST 官网](https://www.st.com/en/development-tools/stm32cubemx.html) 免费下载并自行遵守其许可;本工具仅运行时调用其命令行,不包含、不修改其代码)

## 快速开始(Windows)

三步,约 2 分钟。以下命令都在 **PowerShell** 里执行。

### 第 1 步:安装

```powershell
pip install vscode-cube-mcp
```

装完后先验证一下(能打印出版本号就说明装好了):

```powershell
python -m pip show vscode-cube-mcp
```

> 如果提示 `pip` 不是命令或装到了奇怪的位置,先看文末 FAQ「pip 报错 / 装不上」。

### 第 2 步:配置环境变量(永久生效)

用 `setx` 写入**用户级环境变量**(`$env:` 的临时写法只对当前窗口有效,重启客户端后就没用了,不要用):

```powershell
setx ST_CUBEMX_EXE "C:\MINE\STM\STM\STM32CubeMX.exe"
setx ST_CUBEMX_ALLOWED_ROOTS "C:\MINE\STM32Project"
```

把路径换成你自己的:
- `ST_CUBEMX_EXE`:本机 `STM32CubeMX.exe` 的完整路径;
- `ST_CUBEMX_ALLOWED_ROOTS`:允许 AI 访问的工程根目录(多个用 `;` 分隔,如 `C:\MINE\STM32Project;C:\MINE\OTHER`)。

设置完成后**关掉并重新打开客户端**(Reasonix / VS Code 等),环境变量才会被读到。

### 第 3 步:接入 MCP 客户端

把下面配置加到你所用客户端的 MCP server 列表里(以 `mcpServers` 配置为例):

```json
{
  "mcpServers": {
    "Vscode_cube_mcp": {
      "command": "python",
      "args": ["-m", "cubemx_mcp"],
      "env": {
        "ST_CUBEMX_EXE": "C:/MINE/STM/STM/STM32CubeMX.exe",
        "ST_CUBEMX_ALLOWED_ROOTS": "C:/MINE/STM32Project"
      }
    }
  }
}
```

> 说明:
> - `env` 里的路径和上面第 2 步二选一即可(都设也行,`env` 优先)。如果不写 `env`,就必须依赖第 2 步的系统环境变量;
> - JSON 里 Windows 路径建议用正斜杠(`C:/...`)或双反斜杠(`C:\\...`),避免转义问题;
> - `"command": "python"` 要求 `python` 在 PATH 里且就是装有本包的解释器;如果不对,改成 `"command": "py", "args": ["-m", "cubemx_mcp"]` 或写解释器完整路径(见 FAQ)。

### 验证

1. 打开客户端,发一条消息让 AI 调用 `cubemx_load`(给它一个 .ioc 路径);
2. 或在终端手动冒烟:`python -m cubemx_mcp` 启动后**不报错、不立刻退出**,即 server 正常(它是 stdio 服务,会挂起等待输入,`Ctrl+C` 退出)。

## 配置项(环境变量)

| 变量 | 默认 | 说明 |
|------|------|------|
| `ST_CUBEMX_EXE` | PATH 中的 `STM32CubeMX` / 常见 Windows 安装位置 | STM32CubeMX 可执行文件完整路径 |
| `ST_CUBEMX_ALLOWED_ROOTS` | 当前工作目录 | .ioc / 生成路径允许访问的根目录,`;` 分隔(Windows) |
| `ST_CUBEMX_TIMEOUT` | `240` | 单次 CubeMX 调用超时秒数,机器慢可调大 |

> 安全设计:所有 .ioc / 生成路径**必须**在 `ST_CUBEMX_ALLOWED_ROOTS` 白名单内,白名单外的路径会被拒绝。

## 使用示例

让 AI 助手做的事情都会通过上述 8 个工具完成,例如:

- 「加载 `D:\proj\Blink.ioc`,把 PB13 改成 GPIO_Output 并加标签 LED」→ `cubemx_configure`
- 「用 STM32F103C8T6 从零建一个工程,LED 在 PB13,带 I2C1」→ `cubemx_new_project`
- 「把 OLED.c 加进编译,重新 generate 后也保留」→ `cubemx_add_source`

内置模板库(`templates/`,按芯片型号命名,如 `STM32F103C8T6.ioc`、`STM32F103C8T6_tim2_internal.ioc`),新增芯片只需把该芯片 6.18 原生 .ioc 放进 `templates/`。

## 常见问题 FAQ

**Q:pip 装上了,但 `python -m cubemx_mcp` 报 `ModuleNotFoundError: No module named 'cubemx_mcp'`**
装的解释器和 `python` 指向的不是同一个。确认:
```powershell
python -m pip show vscode-cube-mcp   # 能显示才算装在当前 python 上
```
若 `python` 指向 msys2/Git/系统 Store 的 python,换成官方 Python(`py -m pip install vscode-cube-mcp`,`py -m cubemx_mcp`),或直接写解释器全路径到客户端配置。

**Q:客户端里 AI 报找不到 STM32CubeMX / `_find_cubemx` 失败**
环境变量没传进 server 进程。检查:① 是否用了 `setx`(临时 `$env:` 会失效);② 是否重启了客户端;③ `ST_CUBEMX_EXE` 路径是否存在(在 PowerShell 里 `Test-Path "C:\...\STM32CubeMX.exe"` 应为 `True`)。

**Q:报错说路径不在允许范围内(allowed roots)**
把工程所在目录加进 `ST_CUBEMX_ALLOWED_ROOTS`(多个用 `;`),改完重启客户端。如果写在客户端 `env` 里,检查 JSON 的 `;` 和路径是否被转义破坏了。

**Q:CubeMX 调用很慢或超时**
首次启动 CubeMX 较慢是正常的;把 `ST_CUBEMX_TIMEOUT` 调大(如 `600`)。另外确认没有残留的 CubeMX / Java 进程占着工程文件。

**Q:CubeMX 弹「Resolve Clock Issues」**
通常是 .ioc 时钟树不自洽(如 HSE 未启用但 PLL 选了 HSE)。这个属于工程配置问题,详见 [`README.dev-notes.md`](README.dev-notes.md) 的「从零配置时钟实战」。

## 开发与测试

```bash
python -m unittest test_cubemx_mcp -v   # 运行单元测试(不依赖 CubeMX)
```

技术栈:Python >= 3.10 + mcp SDK 2.x(stdio);打包 setuptools + build + twine;目标平台 Windows(跨平台可用)。

## 许可与依赖声明

- 本工具代码:MIT License(见 `LICENSE`)
- mcp SDK(唯一 Python 依赖):MIT License(modelcontextprotocol/python-sdk)
- STM32CubeMX:ST 专有软件,运行时外部调用,需用户自备并遵守其许可条款

## 更多资料

- [`README.dev-notes.md`](README.dev-notes.md):开发笔记——ST 扩展识别工程踩坑、从零配置时钟实战、各外设实测状态与能力边界、借壳法原理
- [`templates/README.md`](templates/README.md):各外设(GPIO/I2C/TIM/时钟)的配置命令与坑
