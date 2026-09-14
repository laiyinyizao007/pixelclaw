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
