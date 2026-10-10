# ⚠️ 本项目已废弃(Deprecated)

**vscode-cube-mcp 已停止维护,不建议在任何项目中使用。**

## 为什么停止

- 让AI直接生成一个完整的.ioc文件太难了，AI幻觉太过严重，不是在瞎猜，就是在瞎编。
  我想更换路线，不是让AI生成完整.ioc文件，而是生成一个结构化的配置意图
  例如：
  {
    "peripheral": "USART2",
    "params": {
      "baudrate": 115200,
      "pins": ["PA2", "PA3"],
      "dma_rx": true
    }
  }
  然后再通过python翻译成.ioc文件。
- 在当前能力下无力继续修复缺陷,继续保留只会误导使用者。

## 恢复计划

**待作者补齐 MCP 开发能力后,再考虑继续本项目。** 在那之前不会再有更新或修复。

## 现状

- PyPI 上的 `vscode-cube-mcp` 仍可安装,但不会再更新,请勿使用;
  已在依赖里的请尽快移除。
- GitHub 仓库保持公开;PyPI 项目未归档,0.4.3 是目前最后一个版本。

## 建议替代方案

- STM32CubeMX 官方图形界面
- STM32CubeIDE / STM32Cube for Visual Studio Code 官方扩展
