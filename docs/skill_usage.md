# PixelClaw Skill 使用指南

## 小红书自动化 Skill

### 快速开始

```python
from pixelclaw.skills import XHSAutomationSkill

# 创建 skill 实例
skill = XHSAutomationSkill()

# 收藏当前帖子
skill.favorite_current_post()

# 点赞当前帖子
skill.like_current_post()
```

### 命令行使用

```bash
# 收藏
python3 -m pixelclaw.skills.xhs_automation_skill fav

# 点赞
python3 -m pixelclaw.skills.xhs_automation_skill like

# 返回
python3 -m pixelclaw.skills.xhs_automation_skill back
```

### 核心方法

#### 1. 获取UI布局
```python
ui_xml = skill.get_ui_hierarchy()
```

#### 2. 查找元素
```python
# 通过 resource-id 查找
element = skill.find_element(
    resource_id='com.xingin.xhs:id/noteCollectLayout'
)

# 通过文本查找
element = skill.find_element(text='关注')

# 获取中心点坐标
center_x, center_y = element.center
```

#### 3. 精确点击
```python
# 点击指定坐标
skill.tap(821, 2263)

# 或自动查找并点击
skill.tap_element('collect')  # 收藏
skill.tap_element('like')     # 点赞
```

### 预定义元素

| 名称 | resource-id | 用途 |
|------|-------------|------|
| like | noteLikeLayout | 点赞按钮 |
| collect | noteCollectLayout | 收藏按钮 |
| comment | noteCommentLayout | 评论按钮 |
| follow | followBtn | 关注按钮 |
| back | backIV | 返回按钮 |
| share | shareBtn | 分享按钮 |

### 完整示例：自动浏览并收藏

```python
from pixelclaw.skills import XHSAutomationSkill
import time

skill = XHSAutomationSkill()

# 浏览5个帖子并收藏
for i in range(5):
    print(f"\n处理第 {i+1} 个帖子...")

    # 收藏
    if skill.favorite_current_post():
        print("✓ 收藏成功")

    # 滑动到下一个
    skill.scroll_down()
    time.sleep(1)
```

---

## 技术原理

### 为什么使用 UI Automator？

传统方法的缺点：
- ❌ 固定坐标：不同手机分辨率不同
- ❌ 手动测量：容易出错
- ❌ UI变化：布局更新后失效

UI Automator 的优势：
- ✅ 动态获取：实时解析当前UI
- ✅ 精确坐标：获取元素实际 bounds
- ✅ 自适应：自动适应不同分辨率

### 核心流程

```
┌─────────────────┐
│ uiautomator dump │  获取UI布局XML
└────────┬────────┘
         ▼
┌─────────────────┐
│ 解析XML bounds   │  提取 [x1,y1][x2,y2]
└────────┬────────┘
         ▼
┌─────────────────┐
│ 计算中心点       │  center = ((x1+x2)/2, (y1+y2)/2)
└────────┬────────┘
         ▼
┌─────────────────┐
│ adb input tap    │  精确点击
└─────────────────┘
```
