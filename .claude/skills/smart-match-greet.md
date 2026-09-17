# smart-match-greet

用简历匹配评分筛选高质量职位，并对实时搜索结果发个性化打招呼。单阶段 App 内循环：进详情页 → Haiku 实时评分 + 生成打招呼 → 达标立即发送。无需预爬数据库。

## 触发场景
- 用户说"匹配职位"、"发打招呼"、"智能打招呼"、"简历匹配"
- 用户想先评分再打招呼，或只想看评分结果

## 日常使用命令

```bash
# 推荐：日常运行（最多 10 条，阈值 6，标准模式）
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword 产品经理 --max-greet 10 --threshold 6

# 严格模式（阈值 7，只有明确需要 AI/Agent 经验的职位才能高分）
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword 产品经理 --max-greet 10 --threshold 7 --strict

# 仅评分，验证匹配效果（不发送）
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword 产品经理 --max-greet 5 --score-only

# 调试单条（看一条就退出）
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword 产品经理 --max-greet 1 --threshold 6

# 薪资过滤（月薪 20K 以下跳过）
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword 产品经理 --max-greet 10 --threshold 6 --min-salary 20
```

## 前置条件
- `.env` 已配置两个 API relay（见下方"API 配置"）
- `anthropic` Python 包已安装（`pip install anthropic`）
- ADB 已连接设备（`adb devices` 可见）
- `BOSS直聘` App 已安装，且账号已登录
- 简历文件存在：`C:/Dev/projects/resume-renew/resume/current.md`（或用 `--resume` 指定）

## 参数说明

| 参数 | 默认 | 说明 |
|------|------|------|
| `--keyword` | 必填 | 搜索关键词，如"产品经理"、"AI产品经理" |
| `--threshold` | 6 | 最低匹配分数（0-10），建议 6-8 |
| `--max-greet` | 5 | 最多发送数量，达到后自动停止 |
| `--strict` | 关 | 严格评分：不明确需要 AI/Agent 经验的职位不超 8 分 |
| `--score-only` | 关 | 只评分不发送（仍需打开 App，评分依赖实时 JD） |
| `--min-salary` | 0 | 月薪下限（K），低于此值在评分前跳过（0=不过滤） |
| `--no-verify` | 关 | 不验证消息是否成功发出（发送后不等待气泡出现） |
| `--device` | 自动 | ADB 设备 serial，多设备时必填 |
| `--resume` | 见前置条件 | 简历 MD 路径 |

## 工作流程

```
启动 Boss 直聘 → 搜索关键词 → 采集 60 张基线卡片
  ↓
while 已发数 < max-greet:
  _find_next_job()  ← 实时 UI 截图，取第一张未访问的卡
  ├─ 全部访问过 → 下滑列表，连续 3 次无新卡则退出
  ├─ 已打过招呼（DB 三层去重）→ 跳过
  └─ 未访问 → 进详情页
       ├─ Layer 1: 详情页标题+公司验证
       ├─ Layer 2: 聊天页 HR 名+职位验证
       ├─ Layer 3: DB 反查去重
       ├─ Haiku 评分（primary=minnimax → fallback=klugai）
       │    └─ 两端配额耗尽 → sleep 至 resetAt + 10s → 重试一次
       ├─ 分数 < threshold → 跳过
       └─ 分数达标 → 发送打招呼 → 写 DB → greeted_count++
```

## API 配置

脚本使用两个 API relay，`.env` 中配置：

```
# primary relay（minnimax，有 5 小时 2500 请求配额）
ANTHROPIC_BACKUP_API_KEY=<key>
ANTHROPIC_BACKUP_BASE_URL=https://minnimax.chat

# fallback relay（klugai，有费用上限，每日定时重置）
ANTHROPIC_API_KEY=<key>
ANTHROPIC_BASE_URL=https://www.klugai.lol/api
```

- primary 每次先尝试 2 次，失败后切 fallback
- fallback 使用 `claude-haiku-4-5-20251001`（klugai 支持的模型 ID）
- 两端均失败且 fallback 响应含 `resetAt` → 自动 sleep 至重置时间后重试当前卡

## 数据库去重（三层）

每次打招呼前检查 `scenarios/boss/output/requirements.db`：

1. `greetings JOIN job_details`：HR + 公司 组合是否打过
2. `job_visits`：HR 名 + 公司 是否访问过
3. `job_visits`：职位名（标准化）+ 公司 是否访问过

三层任一命中即跳过，避免重复打招呼。

## 输出示例

```
✓ 打招呼成功：10 条
  · AI技术产品经理（上海海智）8/10 —— 打招呼成功
    李先生您好，看到贵司AI技术产品经理岗位方向很感兴趣…
  · AIGC产品经理（Gamehaus）7/10 —— 打招呼成功
    …
- 跳过：15 条
```

## 常见问题

**Q: 出现"两端 API 配额耗尽"日志，脚本卡住很长时间？**
- 正常行为：两个 relay 都达到限额时，脚本会 sleep 到 klugai 的 `resetAt` 时间（+10 秒缓冲），然后自动重试当前卡。等待期间日志会打印倒计时秒数。

**Q: App 显示"每日沟通次数已用完"？**
- `DialogType.DAILY_LIMIT` 触发终止逻辑，脚本自动退出并报错。次日重跑即可。

**Q: 出现"无法返回职位列表"？**
- 脚本在 `browse_jobs` 三次失败后退出循环并汇报。检查 App 是否在前台，或重新运行。

**Q: 打招呼内容正确但显示"发送未确认"？**
- 消息可能已成功发出，只是 `verify_message_sent` 轮询窗口内没抓到气泡。可进 App 手动确认。加 `--no-verify` 跳过验证步骤。

**Q: 为什么某职位分数低（<6）但感觉很匹配？**
- 查看日志中的 `top_matches` 字段，了解 AI 判断依据。如想放宽，降低 `--threshold`。若想收紧（减少误判），加 `--strict`。

**Q: 简历路径怎么改？**
- `--resume /path/to/resume.md`，或修改脚本顶部的 `DEFAULT_RESUME_PATH` 常量。
