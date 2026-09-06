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
