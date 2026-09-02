# 小红书自动化操作指南

## 正确方法：使用 UI Automator 获取精确坐标

### 问题背景
手动猜测坐标容易出错，因为：
- 屏幕分辨率不同（1080x2400 实际显示为 900x2000）
- 按钮区域密集，偏差几十像素就点到相邻元素
- 小红书UI布局动态变化

### 正确解决方案

#### 1. 获取UI布局
```bash
adb shell uiautomator dump /sdcard/ui.xml
adb pull /sdcard/ui.xml /tmp/ui.xml
```

#### 2. 解析收藏按钮位置
收藏按钮特征：
- `resource-id="com.xingin.xhs:id/noteCollectLayout"`
- `content-desc="收藏 397"`

XML示例：
```xml
<node index="2" text="" resource-id="com.xingin.xhs:id/noteCollectLayout"
      class="android.widget.Button" content-desc="收藏 397"
      bounds="[732,2190][910,2337]" />
```

#### 3. 计算中心点
```python
x1, y1, x2, y2 = 732, 2190, 910, 2337
center_x = (x1 + x2) // 2  # 821
center_y = (y1 + y2) // 2  # 2264
```

#### 4. 精确点击
```bash
adb shell input tap 821 2264
```

---

## 各按钮坐标参考（1080x2400屏幕）

| 按钮 | resource-id | bounds | 中心点 |
|------|-------------|--------|--------|
| 点赞 | noteLikeLayout | [533,2190][732,2337] | (633, 2264) |
| 收藏 | noteCollectLayout | [732,2190][910,2337] | **(821, 2264)** |
| 评论 | noteCommentLayout | [910,2190][1080,2337] | (995, 2264) |

---

## 自动化脚本使用

### 单帖子收藏
```bash
cd /home/averypi/Projects/pixelclaw
python3 scripts/xhs_auto_fav.py
```

### 批量收藏多个帖子
```bash
python3 scripts/xhs_batch_fav.py --count 10
```

---

## 经验总结

❌ **错误方法**：
- 目测截图猜测坐标
- 使用固定坐标（不同手机分辨率不同）

✅ **正确方法**：
- 使用 `uiautomator dump` 获取实时UI布局
- 解析 bounds 属性计算中心点
- 动态适应不同屏幕分辨率

---

## 进阶技巧

### 通过文本查找按钮
```python
# 查找包含"收藏"文本的按钮
pattern = r'text="收藏".*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
```

### 等待元素出现
```python
import time

def wait_for_element(element_id, timeout=10):
    for i in range(timeout):
        pos = get_element_position(element_id)
        if pos:
            return pos
        time.sleep(1)
    return None
```

### 验证操作成功
```python
def verify_favorite():
    # 重新获取UI，检查收藏按钮状态
    content = get_ui_hierarchy()
    # 检查是否变为"已收藏"或数字变化
    return "已收藏" in content or "收藏成功" in content
```
