---
name: smart-greet
description: |
  USE WHEN user wants to run automated job-application greeting with resume
  matching in PixelClaw ("智能打招呼", "匹配职位", "发打招呼", "smart greet",
  "AI产品经理匹配", "简历打招呼"), OR user just ran smart_match_greet and asks
  "why did it fail" / "what does '发送未确认' mean" / "why score 0" /
  "两端 API 配额耗尽", OR user wants to switch platforms (智联/猎聘/Boss).
  Covers preflight (ADB / .env / resume / LLM endpoint), 3 platform
  differences (boss/zhilian/liepin), all CLI flags, output field
  interpretation, 8+ common error→fix mappings, multi-day DB management,
  and quota limits. Does NOT execute any ADB / send any greeting itself —
  guides the user to run the right command. References
  `PROJECTWIKI.md` §5 (single-stage design) and the `smart_match_greet.py`
  sister scripts under `scenarios/{boss,zhilian,liepin}/scripts/`.
---

# smart-greet — 智能打招呼工作流

把"实时评分 + 个性化打招呼"封装成可复用的 agent skill。覆盖 3 个平台（boss/zhilian/liepin），
从 preflight 到错误处理全闭环。

**这个 skill 不直接执行 ADB 也不替你点发送**——它引导你跑正确的命令并解读结果。

## 触发场景

- 用户说"匹配职位"、"智能打招呼"、"发打招呼"、"简历匹配"
- 用户说"AI 产品经理匹配"、"帮我在 Boss 投 10 个"等具体运行诉求
- 用户刚跑过 smart_match_greet 报错，问"为什么"、"什么意思"
- 用户想换平台（"切到智联"/"猎聘可以吗"）
- 用户想看匹配质量（"先 score-only 看看"）
- 用户问"每日沟通次数还能用吗"、"配额还剩多少"

## 3 平台差异速查

| 维度 | Boss直聘 (`boss`) | 智联招聘 (`zhilian`) | 猎聘 (`liepin`) |
|------|-------------------|----------------------|------------------|
| **打招呼方式** | 自定义文本 + verify 发送 | 点「先聊聊」即发送默认招呼 | 自定义文本 + ADBKeyboard |
| **HR 活跃度显示** | "今日活跃" / "3天前活跃" | "今日回复50+次" / "34分钟前回复" | "今日活跃" / "3天前活跃" |
| **Dialog 系统** | 有 `DialogType` 枚举（13 种状态） | 无 | 无 |
| **DB 表前缀** | `job_details` / `greetings` | `zhilian_*` | `liepin_*` |
| **薪资格式** | "K·13薪" | "万" | "K/万" 混用 |
| **每日上限** | 110 | 150 | 100 |
| **最完整实现** | ✅ 参考实现 | 简化版 | 简化版 |

> **选择建议**：boss 是参考实现，遇到问题先对照 boss；切换其他平台前确认对应差异点。

## Preflight 检查（5 项缺一不可）

在跑任何 `smart_match_greet.py` 之前，按顺序检查：

### 1. ADB 设备已连接
```bash
adb devices
# 期望输出：<serial>    device    （不是 unauthorized / offline）
```

- 单设备时 `--device` 可省略；多设备必填
- 手机必须解锁、Adaptive Sleep 关闭
- 同 WiFi 无线 ADB 走 `10.32.7.105:5555`（参见 `docs/GETTING_STARTED.md` §3）

### 2. .env 已配置（4 个关键变量）
```bash
cd /home/averypi/Projects/pixelclaw
[ -f .env ] || cp .env.example .env
grep -E "^(ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|ANTHROPIC_BACKUP_API_KEY|PIXELCLAW_RESUME_PATH)" .env
```

- `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`：主 LLM relay（hermes）
- `ANTHROPIC_BACKUP_API_KEY` / `ANTHROPIC_BACKUP_BASE_URL`：fallback（不设则不启用）
- `PIXELCLAW_RESUME_PATH`：简历 MD 路径（不设走 Pi 默认 `~/Projects/resume-renew/resume/current.md`）

### 3. 简历文件存在
```bash
[ -f "${PIXELCLAW_RESUME_PATH:-$HOME/Projects/resume-renew/resume/current.md}" ] && echo "✓" || echo "✗"
```

### 4. LLM endpoint 30s 裸 SDK 诊断

⚠️ **域名陷阱**：`api.minimax.chat/v1` 是 OpenAI-Completions 协议，Anthropic SDK 会持续 404 + hang。
正确 URL：`https://api.minimaxi.com/anthropic`（注意 `minimaxi` 不是 `minimax`）。

```bash
python3 -c "
import os, anthropic
client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'],
                              base_url=os.environ['ANTHROPIC_BASE_URL'])
resp = client.messages.create(
    model='MiniMax-M3', max_tokens=16,
    messages=[{'role':'user','content':'hi'}]
)
print('OK in', resp.usage.input_tokens, 'tokens')
"
```

**判定标准**：
- 5s 内 200/401/404 → 健康
- 30s 才出 → URL 错了，先检查 `.env` 的 `ANTHROPIC_BASE_URL`

### 5. App 已登录

- **Boss直聘**：`com.hpbr.bosszhipin`，检查「我的」Tab 头像
- **智联招聘**：在「我的」页面看是否显示用户名
- **猎聘**：同上

任一未通过 → 修复后重跑。**不要带病跑**——常见表现是脚本"莫名其妙"卡住。

## 典型运行

### Boss直聘（参考实现）
```bash
cd /home/averypi/Projects/pixelclaw

# 1) 仅评分验证（不发）
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword "AI产品经理" --score-only --max-greet 5 --strict

# 2) 调试单条（确认发送链路）
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword "AI产品经理" --max-greet 1 --threshold 8 --strict

# 3) 正式运行
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword "AI产品经理" --max-greet 10 --threshold 7 --strict
```

### 智联招聘
```bash
PYTHONUTF8=1 python -m scenarios.zhilian.scripts.smart_match_greet \
  --keyword "AI产品经理" --max-greet 10 --threshold 7 --strict
```
注：智联是「点先聊聊」即发送默认招呼（无 verify 步骤），无需自定义文本。

### 猎聘
```bash
PYTHONUTF8=1 python -m scenarios.liepin.scripts.smart_match_greet \
  --keyword "AI产品经理" --max-greet 10 --threshold 7 --strict
```
注：猎聘走 ADBKeyboard 发自定义消息，需要确认手机装了 ADBKeyboard 输入法并设为默认。

## CLI 参数表

| 参数 | 默认 | 说明 |
|------|------|------|
| `--keyword` | 必填 | 搜索关键词，如"产品经理"、"AI产品经理" |
| `--threshold` | 6 | 最低匹配分数（0-10），建议 6-8 |
| `--max-greet` | 5 | 最多发送数量，达到后自动停止 |
| `--strict` | 关 | 严格评分：不明确需要 AI/Agent 经验的不超 8 分 |
| `--score-only` | 关 | 只评分不发送（仍需打开 App，评分依赖实时 JD） |
| `--min-salary` | 0 | 月薪下限（K），低于此值评分前跳过（0=不过滤） |
| `--no-verify` | 关 | 发送后不验证消息是否成功发出 |
| `--device` | 自动 | ADB 设备 serial，多设备时必填 |
| `--resume` | `$PIXELCLAW_RESUME_PATH` 或 Pi 回退 | 简历 MD 路径 |

## 输出字段解读

跑完后重点看这些字段：

| 字段 | 含义 | 用途 |
|------|------|------|
| `top_matches` | LLM 评分依据（匹配上的点） | 调试为什么高/低分 |
| `dimension_scores` | 多维度加权分（技术/产品/行业等） | 看具体哪项扣分 |
| `mismatch_concerns` | 扣分原因 | 调整 `--threshold` 的依据 |
| `greeted` | 已发送列表 | 含 `greeting` 文本预览 |
| `skipped` | 跳过列表（含 dedup / 薪资过滤 / 分数低） | 看哪些没发 |
| `errors` | 错误列表 | 配 `diagnose-fail.sh` 用 |
| `dedup_key` | `normalize(title)\tcompany\thr_name` | 跨次去重 key |

> **greeted vs skipped 之和 ≠ max-greet 是正常的**——`errors` 项独立计数。
> `requirements.db` 的 `greetings` 表会记所有发送过的，**跨次运行自动跳过**。
>
> 跑完会输出**评分分布摘要**（`smart_match_greet.py:1584-1592`）：
> ```
> 📊 评分分布（共 N 个职位打分）: ≥8分=X  6-7分=Y  <6分=Z  均分=AVG  跳过=W
> ```
> `--score-only` 模式仍会触发同一摘要，且不消耗每日沟通次数（只写 `job_details` 表，不写 `greetings` 表；代码 `smart_match_greet.py:1404-1410`）。

## 评分调优：title 不是降分理由

LLM 评分时，**title 字面相似度不应该是 `role_fit` 的主依据**。重点是 JD 正文写的实际工作内容（职责/要求技能/day-to-day）跟候选人实际能力匹不匹配。

**Why**: FDE/PM 复合背景的候选人，title 写"产品经理"但实际是 PM+前端+设计师+全栈；如果按 title 筛会漏掉大量 title="前端工程师"但 JD 写的是 FDE/AI 应用/Agent 开发的岗位——而这些反而是最高分的匹配。10 条实测：9 分的 3 个都是 title 含"FDE/创始工程师"但 JD 写"AI 前端架构/客户现场交付"；被跳过的 5-6 分岗位里，多条是 title 表面不像（"前端（小红书）"/"资深平台软件产品经理"）但 JD 实质高度吻合。

**调优信号**：
- 看 `mismatch_concerns` 时，**凡是 "title 是 X 不是 Y" 类的扣分要警觉**
- 看 `dimension_scores.role_fit` 时，让 LLM 多看 JD 实质内容，少看 title 字面
- 提示词改造方向：title 仅用于 dedup，不参与 `role_fit` 评分

**反向信号**（这些该跳就跳）：
- title 是"FDE"但 JD 核心是 Linux/K8s/DevOps 运维 → content 不匹配，跳
- title 是"前端"但 JD 核心是 ECharts 报表/数据可视化深度 → content 不匹配，跳

**验证方法**：
```bash
# 用 score-only 跑同一批 JD，把 title 从 prompt 屏蔽，对比分数变化
PYTHONUTF8=1 python scenarios/boss/scripts/smart_match_greet.py \
  --keyword "前端工程师" --score-only --max-greet 5 --strict
# 然后人工 grep 看：title 拖累的条目升分 vs content 不匹配的条目分数不变
```

## 常见错误 → 修复（8+）

> 完整表见 `references/error-fix-table.md`。本节是高频 5 项。

### 1. `两端 API 配额耗尽` → 自动恢复
- **症状**：脚本卡在 "sleep until resetAt"，日志倒计时秒数
- **机制**：两端 relay（hermes 主 + klugai 备）都达到限额时，自动 sleep 到 klugai 的 `resetAt` 时间（+10 秒缓冲），然后重试当前卡
- **代码位置**：`scenarios/boss/scripts/smart_match_greet.py:1284-1307`（`_with_screen_heartbeat` 异常处理 + `_extract_reset_at()` + `time.sleep(wait_secs)`）
- **不需要手动干预**，等就行；如果等太久可以 Ctrl+C 重跑（已发的会去重）

### 2. `DialogType.DAILY_LIMIT` → 次日重跑
- **症状**：App 显示「每日沟通次数已用完」，脚本自动退出
- **修复**：等 24h，**次日 0 点**自动重置

### 3. `无法返回职位列表` → 手动恢复
- **症状**：`_return_to_job_list()` 失败 3 次
- **原因**：App 不在职位列表页（可能在聊天页/详情页/其他）
- **修复**：手动点回职位列表，再重跑（已发会去重）

### 4. `发送未确认` → **false negative**（极常见）
- **症状**：`verify=False` 或日志显示「消息可能已发出，但未确认」
- **原因**：`verify_message_sent()` 8s 轮询窗口内没抓到气泡（App UI 渲染延迟/网络延迟；详见 `skills/boss/boss_automation_skill.py:966`，默认 `timeout=8.0` 秒）
- **判定**：先到 App 聊天列表**手动看**消息是否真发了
  - 真发了 → 加 `--no-verify` 重跑，避免误判
  - 没发 → 重跑
- **不要仅凭 verify 结果判定失败**——这是已知 false negative 模式

### 5. `API 404 + hang` → URL 错了
- **症状**：请求 hang 30s+，最后 `404 page not found`
- **几乎一定是 URL 错了**：`api.minimax.chat/v1` 是 OpenAI 协议，不是 Anthropic
- **修复**：`.env` 的 `ANTHROPIC_BASE_URL` 改成 `https://api.minimaxi.com/anthropic`

### 6. `手机锁屏未解` → 解锁等 120s
- 脚本启动时会等 120s 让用户解锁，不会立即报错

### 7. `DeviceBusyError` → 杀其他脚本
- 多脚本互斥锁（`device_lock`），同时只能跑一个 smart_match_greet
- 杀掉其他 ADB 占用脚本后重跑

### 8. `详情页加载超时` → 自动跳过
- 网络抖动偶发，记录到 DB 后自动跳到下一条
- 不需要修；如大面积出现检查网络

## 多日 DB 管理

`scenarios/<platform>/output/requirements.db` 累积规则：

- **永不自动清理**——greetings 表记所有发送过的，跨次运行自动去重
- **何时该归档/清空**：
  - 想刷一遍已发职位列表（看历史）→ 备份后不删
  - 想重新评估所有职位（接受再投）→ 备份后清空
  - 切换关键词/平台 → 旧数据留着，新数据进新表
- **备份**：
  ```bash
  cp scenarios/boss/output/requirements.db \
     scenarios/boss/output/requirements.db.bak.$(date +%Y%m%d)
  ```
- **清空影响**：
  - 已发职位会**重新进入候选池**（dedup_key 失效）
  - 不会真的"再发一次"——因为你已经在 HR 那边发过，他们会认出你

> **多日工作流**：通常同关键词每周跑 2-3 次（间隔 1-2 天），积累 HR 回复。

## 配额管理

| 平台/Relay | 限制 | 重置时间 |
|------------|------|----------|
| MiniMax (hermes, 主) | 2500 请求 / 5 小时 | 滚动窗口 |
| Klugai (fallback) | 未知上限 + 每日重置 | 每日 0 点（北京时间） |
| Boss 每日沟通 | 110 次 | 次日 0 点 |
| 智联每日沟通 | 150 次 | 次日 0 点 |
| 猎聘每日沟通 | 100 次 | 次日 0 点 |

**经验值**：单次跑 10-30 jobs，约消耗 10-30 次 LLM 请求（每 job 1 次评分）+ 0-10 次打招呼。
单日总请求建议 ≤ 500（hermes）/ 100（3 平台总打招呼），避免触发限流。

## 跨平台切换 checklist

从 boss 切到 zhilian/liepin 前确认：

- [ ] 对应 App 已安装并登录
- [ ] 平台特有差异已理解（见上方差异表）
- [ ] DB 路径：`scenarios/zhilian/output/requirements.db`（不是 boss 的）
- [ ] 猎聘需要 ADBKeyboard 输入法
- [ ] 智联用 `-m scenarios.zhilian.scripts.smart_match_greet`（包模式），boss 用直接路径

## 参考引用

- **代码**：
  - `scenarios/boss/scripts/smart_match_greet.py`（参考实现，1700 行）
  - `scenarios/zhilian/scripts/smart_match_greet.py`（1280 行）
  - `scenarios/liepin/scripts/smart_match_greet.py`（1300 行）
  - `scenarios/boss/scripts/daily_greet.py`（3 步编排：scrape → analyze → smart_match_greet）
- **文档**：
  - `PROJECTWIKI.md` §5：单阶段 App 内循环设计、LLM Provider 配置、跨主机档约定
  - `PROJECTWIKI.md` §3.2：HERMES URL 防混淆 + 域名陷阱警告
  - `.env.example`：4 个关键 env var 模板
- **Bundled resources**：
  - `scripts/preflight.sh` —— 一键 preflight（5 项全检）
  - `scripts/diagnose-fail.sh` —— 错误关键词匹配修复
  - `references/error-fix-table.md` —— 8+ 行错误→修复表
  - `references/platform-differences.md` —— 3 平台详细差异
- **项目 memory**：
  - `~/.claude/projects/-home-averypi-Projects-pixelclaw/memory/reference_hermes_llm_endpoint.md`：HERMES URL + 模型可用性
