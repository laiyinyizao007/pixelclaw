# 3 平台差异详细对比

> 主文档：`SKILL.md` 第二节是速查表；本文是完整版。
> 来源：3 个 sister 脚本（`scenarios/{boss,zhilian,liepin}/scripts/smart_match_greet.py`）的代码差异分析。

## 1. 打招呼机制

### Boss直聘
- **流程**：自定义文本 → 写入输入框 → 点击发送按钮 → `verify_message_sent()` 6s 轮询气泡
- **DB 记录**：`_record_greeting()` 同时 upsert `job_details` + insert `greetings`
- **失败兜底**：`verify=False` 仍记 `greetings`（标记"已发未确认"），下次 dedup 跳过

### 智联招聘
- **流程**：点「先聊聊」按钮 → App 自动发送**默认招呼**（无自定义）
- **DB 记录**：只记 `zhilian_*` 表，HR 回复后才升级到「打招呼成功」
- **特殊**：无 verify 步骤（默认招呼几乎不会失败）

### 猎聘
- **流程**：点「直聊」/「与 TA 直聊」→ 进直聊窗口 → 通过 **ADBKeyboard** 发自定义消息
- **依赖**：手机必须装 ADBKeyboard 输入法并设为默认
- **DB 记录**：记 `liepin_*` 表

## 2. 弹窗/Dialog 系统

### Boss
- 有 `DialogType` 枚举，13 种状态：
  - 致命（终止脚本）：`DAILY_LIMIT` / `LOGIN_REQUIRED`
  - 跳过：`JOB_OFFLINE`
  - 继续：`EXISTING_CHAT` / `DISMISSED` / `WARM_REMINDER`
- 进入聊天页后有安全提示浮层需关闭

### 智联 / 猎聘
- 无 DialogType 系统
- 进入聊天页后**只有安全提示浮层**需关闭
- 没有"已发过招呼"等特殊弹窗

## 3. HR 活跃度显示

| 平台 | 显示格式 | 解析方式 |
|------|----------|----------|
| Boss | "今日活跃" / "3天前活跃" | 天数格式 |
| 智联 | "今日回复50+次" / "34分钟前回复" | 频率 + 时间 |
| 猎聘 | "今日活跃" / "3天前活跃" | 天数格式（同 Boss） |

活跃度影响评分权重：
- Boss：HR 活跃度是评分维度之一
- 智联：用 `hr_response`（回复频率）替代活跃度
- 猎聘：同 Boss

## 4. 薪资解析

| 平台 | 显示格式 | 解析示例 |
|------|----------|----------|
| Boss | "K·13薪" | "30K·13薪" → 30 * 1000 = 30000 |
| 智联 | "万" | "1.5-2.5万" → 15000-25000 |
| 猎聘 | "K/万" 混用 | "30K" 或 "1.5万" → 看具体单位 |

`--min-salary` 参数**统一按 K（千）算**：
- Boss："30K" → 30
- 智联："1.5万" → 15
- 猎聘：取决于实际显示

## 5. DB Schema 差异

### Boss
```sql
CREATE TABLE job_details (
  dedup_key TEXT PRIMARY KEY,
  title TEXT, company TEXT, hr_name TEXT, ...
);
CREATE TABLE greetings (
  id INTEGER PRIMARY KEY, dedup_key TEXT,
  greeting TEXT, sent_at TEXT, verified INTEGER,
  FOREIGN KEY (dedup_key) REFERENCES job_details(dedup_key)
);
```

### 智联
- 表名加 `zhilian_` 前缀
- 结构基本同 boss，少 `verified` 字段（因为没 verify 步骤）

### 猎聘
- 表名加 `liepin_` 前缀
- 结构同 boss，有 `verified`（走 ADBKeyboard verify）

## 6. 每日沟通上限

| 平台 | 上限 | 检测方式 |
|------|------|----------|
| Boss | 110 | `DialogType.DAILY_LIMIT` 检测 |
| 智联 | 150 | UI 文案匹配（"今日已达上限"） |
| 猎聘 | 100 | UI 文案匹配 |

## 7. Prompt 文件

每个平台独立：

- `scenarios/<platform>/config/prompts/match_score.md` —— 评分 prompt
- `scenarios/<platform>/config/prompts/strict_rules.md` —— 严格模式规则
- `scenarios/<platform>/config/candidate_profile.yaml` —— 候选人画像

修改 prompt 不用碰代码（脚本启动时 mtime 检测自动重载）。

## 8. 入口模式

| 平台 | 命令格式 | 备注 |
|------|----------|------|
| Boss | `python scenarios/boss/scripts/smart_match_greet.py ...` | 直接路径 |
| 智联 | `python -m scenarios.zhilian.scripts.smart_match_greet ...` | **包模式**（sys.path 处理） |
| 猎聘 | `python -m scenarios.liepin.scripts.smart_match_greet ...` | 同上 |

> ⚠️ 智联/猎聘必须用 `-m` 包模式，否则 `sys.path.insert(0, parents[3])` 路径错误。

## 9. 共享 vs 平台特有

### 共享
- `anthropic.Anthropic` SDK 调用模式
- `_make_client()` 函数（env var 加载 + 主备 relay 切换）
- `extract_resume_summary()` resume 处理
- `score_job()` LLM 评分主流程
- `_record_greeting()` DB 去重

### 平台特有
- 打招呼按钮定位（UI selector 不同）
- 详情页元素验证（boss 用 `tv_job_name`，智联用 `tv_job_name_new`）
- Dialog 状态机（只有 boss 有）
- 薪资/活跃度解析
- DB 表前缀
