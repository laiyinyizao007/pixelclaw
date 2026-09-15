# smart-match-greet

用简历匹配评分筛选高质量职位，并对实时搜索结果发个性化打招呼。单阶段 App 内循环：进详情页 → Haiku 实时评分 + 生成打招呼 → 达标立即发送。无需预爬数据库。

## 触发场景
- 用户说"匹配职位"、"发打招呼"、"智能打招呼"、"简历匹配"
- 用户想先评分再打招呼，或只想看评分结果

## 执行步骤

1. 确认工作目录为项目根目录（`C:\Dev\projects\pixelclaw`）
2. 确认 ADB 已连接设备
3. 运行命令：

```bash
# 仅评分，验证效果（仍需打开 App，评分依赖实时 JD）
python scenarios/boss/scripts/smart_match_greet.py --keyword "AI产品经理" --score-only --strict

# 正式运行（最多 5 条，阈值 7，严格模式）
python scenarios/boss/scripts/smart_match_greet.py --keyword "AI产品经理" --threshold 7 --strict

# 调试单条
python scenarios/boss/scripts/smart_match_greet.py --keyword "AI产品经理" --max-greet 1 --threshold 8 --strict
```

4. 脚本会打开 App → 搜索 → 逐条进详情评分 → 达标发送 → 结束后输出"执行结果摘要"

## 前置条件
- `ANTHROPIC_API_KEY` 环境变量已设置
- `anthropic` Python 包已安装
- ADB 已连接设备（`adb devices` 可见），`BOSS直聘` App 已安装
- **无需预爬数据库**：JD 从 App 详情页实时提取

## 参数说明

| 参数 | 默认 | 说明 |
|------|------|------|
| `--keyword` | 必填 | 搜索关键词 |
| `--threshold` | 6 | 最低匹配分数（0-10），建议 7-8 |
| `--max-greet` | 5 | 最多发送数量 |
| `--strict` | 关 | 严格评分：不明确要求 AI/Agent 经验的职位不超 8 分，纯技术岗强制 ≤5 |
| `--score-only` | 关 | 只评分不发送（但仍需打开 App） |
| `--min-salary` | 0 | 月薪下限（K），低于此值在评分前跳过 |
| `--no-verify` | 关 | 不验证消息是否成功发出 |
| `--device` | 自动 | ADB 设备 serial |
| `--resume` | 见下 | 简历 MD 路径，默认 `C:/Dev/projects/resume-renew/resume/current.md` |

## 输出示例

```
✓ 打招呼成功：1 条
  · AI 产品经理（上海腾道）8/10 —— 打招呼成功
    李女士您好，看到贵司在做智能体产品0到1，我在xxx有相关经历…
- 跳过：4 条
✗ 错误：1 个
  · agent产品经理：详情页加载超时（网络抖动，下次重跑即可）
```

"发送未确认"不代表失败——消息可能已发出，`verify_message_sent` 轮询窗口内未抓到气泡。

## 常见问题

**Q: 为什么 `--score-only` 还是打开了 App？**
- 单阶段架构中，评分依赖从 App 详情页实时提取的 JD 原文，离线操作已不再支持

**Q: 出现"无法返回职位列表"怎么办？**
- 已修复（条件为 `greeted_count > 0` 后才调用 back），如仍出现请检查 App 是否在前台

**Q: App 显示"每日沟通次数已用完"？**
- `DialogType.DAILY_LIMIT` 触发 `_FATAL_DIALOGS` 逻辑，脚本自动终止并报错
- 次日重跑即可

**Q: 详情页"网络异常"怎么处理？**
- `any("网络异常" in t for t in raw_texts)` 检测后自动跳过该条，继续下一个职位

**Q: 打招呼只发出第一个词，后续内容被截断？（已修复）**
- 症状：AI 生成 60+ 字的招呼语，设备端只收到第一个词（如"您好"或招呼语首词）
- 根因：`send_greeting` 经由 `adb_runner.shell()` 调用时，`shlex.split(posix=True)` 剥去了 `shlex.quote()` 加的单引号，导致 `subprocess.run` 将消息文字作为独立参数传给 adb；adb 拼接 shell 命令时不重新加引号，设备端 `/bin/sh` 按空格切割，只执行第一个词
- 修复：改用 `subprocess.run(["adb", "shell", shell_cmd])` 直接传完整命令串，绕过二次解析

**Q: 启动 App 每次耗时 80 秒，日志显示 10 次 navigate_to_tab 失败重试？（已修复）**
- 症状：脚本启动后长时间卡在"等待首页 Tab"阶段，耗时约 80 秒才继续
- 根因：当前版本 BOSS 直聘 App 的 Tab 控件不含 `tv_tab_N`/`cl_tab_N` resource-id，旧代码只按这两种 ID 查找，永远匹配不到
- 修复：新增文本回退逻辑——搜索 `text` 属性匹配"推荐"/"职位"/"消息"的节点并直接点击

**Q: 打招呼实际已发出，但脚本报告"发送未确认"或"发送失败"？（已修复）**
- 症状：消息气泡在 App 内可见，脚本仍输出"verify_message_sent 超时"
- 根因：`find_element(text=prefix)` 做精确匹配；对 60+ 字长招呼，`message[:15]` 永远不等于气泡完整 text，必然匹配失败
- 修复：改为 `prefix in xml`（在原始 XML 字符串中做子串搜索），同时将超时从 3 秒升至 8 秒
