# 变更日志（Changelog）

所有重要变更均记录于此文件。

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，并遵循 [语义化版本号](https://semver.org/lang/zh-CN/) 规范。

## [Unreleased]

### Changed（变更）
- 整理项目根目录文件结构：新建 `tasks/` 目录，将 9 个任务脚本从根目录移入；将 3 个文档文件移入已有 `docs/` 目录
- 解耦通用自动化基础设施与小红书场景代码：重组 `skills/` 为按场景子目录（`skills/xhs/`），新增 `scenarios/xhs/`（含 tasks/scripts/docs 子目录），将 7 个 XHS 任务脚本、1 个工具脚本和 2 个 XHS 文档迁移至 `scenarios/xhs/`
- 修复 `XHSAutomationSkill.screenshot()` 硬编码路径，改为可配置的 `output_dir` 参数（默认 `/tmp/pixelclaw_output`）
- 重构 `MemoryManager._extract_goal_tags()`：提取 `DEFAULT_TAG_RULES` 常量，支持通过构造函数注入自定义规则，消除硬编码 App 关键字

<!-- 比对链接（将 <REPO_URL> 替换为实际仓库地址） -->
[Unreleased]: <REPO_URL>/compare/v0.1.0...HEAD
