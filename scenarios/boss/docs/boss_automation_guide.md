# Boss直聘自动化使用指南

## 快速开始

```python
from monitors.adb_manager import ADBManager
from skills.boss import BOSSAutomationSkill

adb = ADBManager()
skill = BOSSAutomationSkill(adb)

# 启动 App
skill.launch()

# 搜索职位
skill.browse_jobs("Python 工程师")

# 获取职位列表
jobs = skill.get_job_list()
for job in jobs:
    print(job.title)

# 打招呼
skill.send_greeting("您好！对这个职位很感兴趣。")
```

## 职位详情批量爬取

`scrape_job_details.py` 是可复用的独立爬取脚本，搜索指定关键词、逐个进入职位详情页提取结构化信息，输出 JSON 文件。

### 快速上手

```bash
# 单次爬取（临时指定关键词）
python scenarios/boss/scripts/scrape_job_details.py --keyword "AI产品经理" --n-jobs 10

# 批量爬取（读取 scenarios/boss/config/keywords.yaml）
python scenarios/boss/scripts/scrape_job_details.py

# 同时截图
python scenarios/boss/scripts/scrape_job_details.py --keyword "FDE" --n-jobs 20 --screenshot

# 指定设备
python scenarios/boss/scripts/scrape_job_details.py --keyword "AI产品经理" --device 42231JEKB04971
```

### 所有参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--keyword` | 无（读配置文件） | 临时覆盖关键词，忽略配置文件，只跑这一个 |
| `--n-jobs` | 10（或配置文件 `defaults.n_jobs`） | 每个关键词抓取的职位数 |
| `--output-dir` | `scenarios/boss/output/` | JSON 和截图的输出目录；同关键词的历史 JSON 会自动参与去重（key = 职位名 + 公司 + HR） |
| `--device` | 自动选取 | ADB 设备 serial，多设备时必填 |
| `--screenshot` | 关闭 | 开启后为每条详情截图 |
| `--config` | `scenarios/boss/config/keywords.yaml` | 批量关键词配置文件路径 |

### 批量关键词配置（`keywords.yaml`）

```yaml
defaults:
  n_jobs: 10          # 每个关键词默认抓取数，可被 --n-jobs 覆盖

keywords:
  - AI产品经理
  - FDE
  - 数据工程师        # 继续追加即可
```

### 输出 JSON 结构

文件名：`job_details_{关键词}_{时间戳}.json`，保存在 `--output-dir`。

```json
{
  "keyword": "AI产品经理",
  "n_jobs_requested": 10,
  "scraped_at": "2026-09-07T03:27:44Z",
  "device_id": "42231JEKB04971",
  "jobs": [
    {
      "index": 1,
      "list_info": {
        "title": "AI产品经理",
        "company": "吉利控股集团",
        "salary": "25-35K",
        "location": "上海",
        "hr_name": "张女士",
        "hr_title": "招聘经理",
        "hr_active": ""
      },
      "detail": {
        "title": "AI产品经理",
        "salary": "25-35K·14薪",
        "location": "上海·浦东新区",
        "experience": "3-5年",
        "education": "本科",
        "description": "职位描述正文...",
        "skills": ["产品设计", "AI"],
        "company": "吉利控股集团",
        "company_info": "10000人以上·上市公司·汽车",
        "hr_name": "张女士",
        "hr_title": "招聘经理",
        "raw_texts": ["..."]
      },
      "screenshot_path": ""
    }
  ],
  "errors": []
}
```

`detail` 字段说明：
- `raw_texts`：所有未能精确映射的文本节点，保底不丢信息
- `errors`：网络异常无法恢复、弹窗跳过等情况记录在此；`[]` 表示全部成功

### 网络异常自动恢复

脚本内置两层恢复机制，无需人工干预：

1. **初始占位页**：进入详情页后首次 dump 就出现「网络异常」→ 自动点击「重试」，最多 3 次，成功后重新提取
2. **滚动途中占位页**：滚动加载公司信息时出现「网络异常」→ 提前停止滚动，自动恢复后重新完整提取

两种形态在 `errors` 为空时均已静默处理完毕；恢复失败才会记入 `errors`。

---

## 命令行使用（两阶段工作流）

> Boss直聘平台规定：双方都发过消息后，聊天页才会出现"投递简历"按钮。
> 请分两次运行脚本，中间等待 HR 回复。详见 [boss_platform_rules.md](boss_platform_rules.md)。

```bash
# 第一阶段：搜索职位并发打招呼
python scenarios/boss/tasks/boss_greet_task.py --keyword "Python 工程师"

# 等待数小时至一天，HR 回复后再执行第二阶段
# 第二阶段：检查消息Tab，有HR回复则投递简历
python scenarios/boss/tasks/boss_apply_task.py --max-apply 20

# 指定设备
python scenarios/boss/tasks/boss_greet_task.py --keyword "数据工程师" --device 192.168.1.5:5555
```

## BOSSAutomationSkill API

### 构造函数

```python
BOSSAutomationSkill(
    adb_manager: ADBManager,
    output_dir: str = "/tmp/pixelclaw_output",
    device_id: str = None,
    action_delay: float = 1.0,
)
```

### 主要方法

| 方法 | 说明 |
|------|------|
| `launch()` | 启动 Boss直聘 App |
| `browse_jobs(keyword)` | 在搜索框输入关键词并提交 |
| `get_job_list()` | 解析当前页职位列表，返回 `List[JobInfo]` |
| `apply_to_job()` | 在职位详情页点击「投递简历」 |
| `send_greeting(message)` | 在聊天页面输入消息并发送 |
| `filter_jobs(city, salary, experience)` | 打开筛选面板（具体条件需真机验证） |
| `tap_element(key)` | 按预定义键名点击 UI 元素 |
| `find_element(resource_id, text)` | 在 UIAutomator XML 中查找元素 |
| `screenshot()` | 返回当前设备截图（PIL Image） |

## 预定义 UI 元素（ELEMENTS）

已在真机 Pixel 8a（Boss直聘 v10.x）验证：

| 键名 | 真实 resource-id | 页面 | 用途 |
|------|-----------------|------|------|
| `search_bar` | `id/et_search` | 首页 | 搜索框输入 |
| `job_name` | `id/tv_position_name` | 搜索结果列表 | 职位名称 |
| `job_name_detail` | `id/tv_job_name` | 职位详情页 | 职位名称（读取）|
| `chat_btn` | `id/btn_chat` | 职位详情页 | 「立即沟通」按钮 |
| `chat_input` | `id/editText_with_scrollbar` | 聊天页 | 消息输入框 |
| — | 无 resource-id | 聊天页 | 发送按钮（用 keyevent 66 替代）|
| `filter_btn` | `id/filterBarRightTabView` | 搜索结果页 | 筛选按钮 |
| `apply_btn` | `id/btn_apply` | 聊天页（双方互发消息后）| 投递简历（仅 HR 回复后出现，见平台规则）|

## App 知识库

`config/app_knowledge/boss.json` 预存了 Boss直聘 核心 UI 元素的视觉描述与操作说明，可通过 `AppKnowledge` 类注入到 VLM 提示词中：

```python
from core.app_knowledge import AppKnowledge

knowledge = AppKnowledge("boss")
context_str = knowledge.get_prompt_context(page_name="job_detail")
print(context_str)
```

## SoM + Reflection 模式

启用 SoM 和反思机制以提升自动化准确率：

```python
from core.vision_agent import VisionAgent
from core.app_knowledge import AppKnowledge

agent = VisionAgent(
    device_connector=connector,
    som_enabled=True,        # 对截图叠加编号标注
    reflection_enabled=True, # 每步操作后检测屏幕变化
    app_knowledge=AppKnowledge("boss"),  # 注入 Boss 知识库
)

import asyncio
result = asyncio.run(agent.execute_task("搜索 Python 工程师职位并打招呼"))
```

或通过 `config/settings.yaml` 全局启用：

```yaml
agent:
  som_enabled: true
  reflection_enabled: true
  reflection_diff_threshold: 0.02

app_knowledge:
  enabled: true
  knowledge_dir: "config/app_knowledge"
```

## 平台规则

在开发或扩展 Boss直聘 自动化功能前，请阅读：

- [boss_platform_rules.md](boss_platform_rules.md)：投递前提条件、每日限制、弹窗触发条件、待研究项

## 技术说明

- 使用 `ADBManager.shell()` 代替 raw subprocess 调用
- UIAutomator XML 通过 `cat /sdcard/window_dump.xml` 读取（无需本地 pull）
- 截图通过 `adb exec-out screencap -p` 直接获取为 PIL Image
- 支持通过 `device_id` 多设备管理
