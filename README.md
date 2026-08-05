# Vscode_cube_mcp

> 全部由 deepseek-v4-flash 生成

MCP(Model Context Protocol) server,封装 **STM32CubeMX** 官方命令行脚本模式(`-q`),
让 AI 助手可以直接加载 .ioc 工程、改引脚/外设配置、生成 HAL 代码、导出引脚表。

每次调用:命令序列写入临时脚本 → 启动 `STM32CubeMX -q` → 超时强杀
→ 过滤 log4j 噪音 → 检测 KO 失败标记 → 返回干净输出。

## 功能

| 工具 | 说明 |
|------|------|
| `cubemx_script` | 任意 CubeMX 脚本命令序列(逃生通道) |
| `cubemx_load` | 加载 .ioc 并回读关键配置(只读) |
| `cubemx_configure` | 加载 .ioc,执行 `set` 命令序列,`saveas` 写回 |
| `cubemx_generate` | 加载 .ioc 并 `project generate` 生成 HAL 工程 |
| `cubemx_export_pinout` | 导出引脚配置 CSV(只读) |

## 要求

- Python **>= 3.10**,安装依赖 `mcp>=2.0`
- **STM32CubeMX**(ST 专有软件,请从 ST 官网免费下载并自行遵守其许可)—— 本工具仅运行时调用其命令行,不包含、不修改其任何代码

## 安装

```bash
pip install vscode-cube-mcp        # 从 PyPI(发布后)
# 或本地开发安装
pip install -e .
```

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|------|------|------|
| `ST_CUBEMX_EXE` | PATH 中的 `STM32CubeMX` / 常见 Windows 安装位置 | STM32CubeMX 可执行文件完整路径 |
| `ST_CUBEMX_ALLOWED_ROOTS` | 当前工作目录 | .ioc 允许访问的根目录,`os.pathsep` 分隔(`;` for Windows) |
| `ST_CUBEMX_TIMEOUT` | `240` | CubeMX 子进程超时秒数 |

示例(Windows PowerShell):

```powershell
$env:ST_CUBEMX_EXE = "C:\MINE\STM\STM\STM32CubeMX.exe"
$env:ST_CUBEMX_ALLOWED_ROOTS = "C:\MINE\STM32Project\STM32VScode"
```

> 安全设计:所有 .ioc / 生成路径都必须在 `ST_CUBEMX_ALLOWED_ROOTS` 白名单内,
> 白名单外的路径会被拒绝(路径校验见 `_check_path`)。

## 使用(接入 MCP 客户端)

把 server 注册到支持 MCP 的客户端(如 Reasonix / Claude Desktop 等),
stdio 方式启动:

```json
{
  "mcpServers": {
    "Vscode_cube_mcp": {
      "command": "python",
      "args": ["-m", "cubemx_mcp"]
    }
  }
}
```

或直接用 console 入口:

```bash
vscode-cube-mcp
```

## 开发

```bash
python -m unittest test_cubemx_mcp -v   # 运行单元测试(不依赖 CubeMX)
```

测试覆盖:`_cleanup` 噪音过滤、`_check_path` 白名单校验、`_find_cubemx` /
`_allowed_roots` 配置解析、`_run_script`(mock 子进程)的 KO 检测与超时路径。

## 许可与依赖声明

- **本工具代码**:MIT License(见 `LICENSE`)
- **mcp SDK**(唯一 Python 依赖):MIT License(modelcontextprotocol/python-sdk)
- **STM32CubeMX**:ST 专有软件,运行时外部调用,需用户自备并遵守其许可条款

## 实践经验(2026-08-05,OLED_MCP 项目踩坑记录)

用 `cubemx_generate` 生成的新工程,在 VSCode 里用 **STM32 VS Code Extension** 打开时可能遇到:
Run and Debug 迟迟不出现 / ST 扩展识别工程很慢 / 报 `OLED_MCPsettings\ide.store.json` 之类 ENOENT。

**根因**:CubeMX CLI 生成的新工程缺少 ST 扩展识别工程所需的文件:

| 文件 | 作用 |
|------|------|
| `.settings/ide.store.json` | 声明 `sourceType=STM32CubeMX`、`device`、`core`(扩展识别硬件的关键) |
| `.settings/bundles.store.json`、`bundles-lock.store.json` | bundles(工具链)版本锁定 |
| `.vscode/settings.json` | cube-cmake / clangd 配置 |
| `.vscode/c_cpp_properties.json` | compile_commands.json 索引 |
| `.clangd` | clangd 配置 |

**正确做法(对齐实例工程,如 OLED_HAL)**:
1. 生成新工程后,**不要手写 launch.json**(实例工程没有,ST 扩展会自动提供调试配置);
2. 在 VSCode 里**重新加载窗口**(Reload Window),扩展会识别工程并自动补齐上述文件(device 名取自 `.ioc`,如 `STM32F103C8T6`);
3. 若扩展没自动补齐,可从同芯片的实例工程复制 `.settings/`、`.vscode/`、`.clangd`(注意核对 `ide.store.json` 里的 `device` 是否一致)。

**其他教训**:
- 若确实要手写 launch.json,ST 扩展的调试器类型是 `stlinkgdbtarget`,`deviceName` 必须与 `.settings/ide.store.json` 的 `device` 一致(如 `STM32F103C8T6`,不是 `STM32F103C8Tx`),否则扩展可能解析异常;
- 本机 STM32 VS Code Extension 全家桶调试类型:`stlinkgdbtarget`(ST-Link)/ `jlinkgdbtarget`(J-Link)/ `stgdbtarget`(通用 GDB);
- `cubemx_generate` 的 `project path` 对已存在目录返回 KO 是正常现象,generate 默认在 .ioc 同目录生成,结果不受影响。

## 能力边界:不是真正意义上的"从零开始"

所有工具都要求先有一个 `.ioc` 文件(`cubemx_load` / `cubemx_configure` / `cubemx_generate` 起手都是 `config load`,且 `_ioc_path()` 校验文件必须存在):

- CubeMX 的 `-q` 脚本模式**没有 `new project` 命令**,"新建工程 → 选芯片"是 GUI 独有的流程;
- 因此"生成新工程"的实际做法是:**复制一个 6.18 原生生成的 .ioc(TEST / OLED_HAL 这类)改造**(改 `ProjectManager.ProjectName` / `ProjectFileName`,再用 `set` 命令加外设/引脚)后 `cubemx_generate`;
- 手写全新 .ioc 理论可行,但 6.18-RC3 对手写/非原生 .ioc 加载会 NPE,不推荐。