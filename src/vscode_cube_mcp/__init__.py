"""vscode-cube-mcp:MCP server 封装 STM32CubeMX 命令行(-q)。

对外只暴露入口 ``main``;实现按职责拆在各自模块里,包内用相对导入互相引用:

- ``cubemx_mcp``      MCP 工具编排(10 个工具)、CubeMX 进程调用
- ``cubemx_result``   步骤名、错误码与统一渲染
- ``knowledge_loader``读 ``knowledge/`` 下的 YAML
- ``ioc_editor``      ``.ioc`` 文本编辑(备份 + 回读校验)
- ``intent_resolver`` 配置意图 -> 查表 -> 动作
- ``ioc_diff``        两份 ``.ioc`` 的结构化差异(探针用)
"""

from .cubemx_mcp import main

__all__ = ["main"]
