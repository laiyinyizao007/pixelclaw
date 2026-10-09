# 错误→修复速查表（8+ 项）

> 来源：`scenarios/boss/scripts/smart_match_greet.py` 实际错误处理 + 项目历史故障记录。
> 完整工作流见 `SKILL.md`；本文档只列错误模式。

| # | 错误信号 | 根本原因 | 修复方法 | 自动恢复？ |
|---|----------|----------|----------|-----------|
| 1 | `两端 API 配额耗尽` + 倒计时 | 主+fallback relay 都满 | 等 resetAt+10s 自动重试 | ✅ 是 |
| 2 | `DialogType.DAILY_LIMIT` 触发退出 | App 当日沟通名额满 | 次日 0 点重置 | ❌ 否（等） |
| 3 | `无法返回职位列表` 失败 3 次 | App 不在列表页 | 手动回搜索页重跑 | ❌ 否 |
| 4 | `发送未确认` / `verify=False` | 8s 轮询漏抓气泡（`boss_automation_skill.py:966` 默认 `timeout=8.0`） | **App 手动确认**；确认发出去就加 `--no-verify` | ❌ 否 |
| 5 | `API 404 + hang 30s+` | `minimax.chat` vs `minimaxi.com` 错 | `.env` 改 `https://api.minimaxi.com/anthropic` | ❌ 否 |
| 6 | `手机锁屏未解` | 屏幕锁定 | 解锁（脚本等 120s） | ✅ 是 |
| 7 | `DeviceBusyError` | 互斥锁被其他脚本占 | 杀其他 smart_match_greet / monitor 进程 | ❌ 否 |
| 8 | `详情页加载超时` | 网络抖动 | 自动跳过当前 job 继续 | ✅ 是 |
| 9 | `搜索失败：{keyword}` | `browse_jobs()` 返回 False | 看 `output/debug_search_fail.xml` | ❌ 否 |
| 10 | `ResumeNotFound` | 简历路径不存在 | 设 `PIXELCLAW_RESUME_PATH` 或放简历到 `~/Projects/resume-renew/resume/current.md` | ❌ 否 |
| 11 | `EmptyResponse` from LLM | 模型返回空 / ThinkingBlock 不兼容 | 换模型（`MiniMax-M3` ✅ / `M2.7` ❌ ThinkingBlock） | ❌ 否 |
| 12 | `ModelNotFoundError` | 模型名不在该 relay | 检查 `.env` 的 `ANTHROPIC_BASE_URL` 对应的模型清单 | ❌ 否 |

## 严重程度排序

### P0（阻塞，必须修）
- #5（URL 错）—— 跑不通
- #10（简历无）—— 跑不通
- #12（模型名错）—— 跑不通

### P1（功能受损，能跑但有偏差）
- #1（配额耗尽）—— 慢但能跑
- #4（false negative）—— 误报
- #11（LLM 空响应）—— 评分异常

### P2（边界情况，少见）
- #2（每日上限）—— 业务侧限制
- #3（无法返回）—— App 状态异常
- #6（锁屏）—— 用户操作
- #7（设备忙）—— 多脚本冲突
- #8（详情超时）—— 网络抖动
- #9（搜索失败）—— 关键词无结果

## 修复时长估算

- #5 / #10 / #12：< 1 分钟（改环境变量 / 路径 / 模型名）
- #4：30 秒（看 App + 加 flag 重跑）
- #1：自动，可能 5 分钟 - 数小时
- #2：必须等次日
- #3 / #9：1-2 分钟（手动操作 App + 重跑）

## 配合工具

- `scripts/diagnose-fail.sh <error_log>` —— 自动从日志中匹配上述 12 项，给出建议
- `references/platform-differences.md` —— 平台差异对照（部分错误有平台特异性）
