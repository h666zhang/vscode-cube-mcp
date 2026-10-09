# ⚠️ 本项目已废弃(Deprecated)

**vscode-cube-mcp 已停止维护,不建议在任何项目中使用。**

## 为什么停止

- 作者具备 STM32 开发经验,但不具备 MCP 开发经验,本项目因此积累了较多
  已知缺陷,功能也未经充分验证。
- ST 已推出新一代 STM32CubeMX2(基于 Electron/Theia 重写,不再使用 Java):
  它采用与 `.ioc` 不兼容的 `.ioc2` 格式,CLI 也改为 `cube mx …` 子命令;
  从 STM32C5 起的新系列只支持 MX2。本项目基于经典 CubeMX 的 `-q` + `.ioc`
  路线,无法覆盖新系列。
- 说明:经典 CubeMX(Java 版)的 `-q` CLI 至今仍可用,本项目对 STM32C5 之外的
  旧芯片仍然有效,但已无继续维护的价值。
- 在当前能力下无力继续修复缺陷,继续保留只会误导使用者。

## 恢复计划

**待作者补齐 MCP 开发能力后,再考虑继续本项目。** 在那之前不会再有更新或修复。

## 现状

- PyPI 上的 `vscode-cube-mcp` 仍可安装,但不会再更新,请勿使用;
  已在依赖里的请尽快移除。
- GitHub 仓库保持公开;PyPI 项目未归档,0.4.3 是最后一个版本。

## 建议替代方案

- STM32CubeMX 官方图形界面
- STM32CubeIDE / STM32Cube for Visual Studio Code 官方扩展
