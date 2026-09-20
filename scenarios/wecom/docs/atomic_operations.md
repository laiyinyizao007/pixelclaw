# 企业微信 ADB 自动化 — 原子操作手册

> 按**页面**组织，记录每个操作的前置页面、触发方式、完成标志和常见陷阱。
> 新功能开发时以此为查询起点，不必重新 dump XML。
> resource-id 详见 `config/app_knowledge/wecom.json`。

---

## 目录

1. [基础设施 — ADB / 设备层](#1-基础设施--adb--设备层)
2. [启动页](#2-启动页)
3. [会话列表页 (Conversation List)](#3-会话列表页-conversation-list)
4. [搜索页 (Search)](#4-搜索页-search)
5. [联系人资料页 (Contact Profile)](#5-联系人资料页-contact-profile)
6. [聊天页 (Chat)](#6-聊天页-chat)
7. [错误恢复](#7-错误恢复)
8. [页面状态检测速查](#8-页面状态检测速查)

---

## 1. 基础设施 — ADB / 设备层

### 1.1 防止锁屏

```python
skill._adb("shell svc power stayon true")
skill._adb("shell settings put system screen_off_timeout 2147483647")
```

- **陷阱**：仅设置 `screen_off_timeout` 不够；`svc power stayon true` 必须同时设置。
- **持久性**：USB 断连后失效，每次连接要重设。

### 1.2 唤醒屏幕 + 清除锁屏

```python
skill._adb("shell input keyevent 224")      # WAKEUP
time.sleep(1.0)
skill._adb("shell wm dismiss-keyguard")
time.sleep(0.5)
skill._adb("shell input swipe 540 1800 540 600 300")  # 上滑保底
```

### 1.3 启动企业微信

```python
skill._adb("shell monkey -p com.tencent.wework -c android.intent.category.LAUNCHER 1")
time.sleep(3.0)
```

- **返回页面**：消息 Tab 会话列表（通常）。
- **陷阱**：如果企业微信弹出"发现新版本"对话框，需要先关闭（点击「稍后再说」或按 Back）。

---

## 2. 启动页

### 2.1 关闭「发现新版本」弹窗

- **触发条件**：进入会话列表前，App 有时弹出版本更新提示。
- **处理方式**：
  ```python
  xml = skill.get_ui_hierarchy()
  # 检测是否有「稍后再说」按钮
  elem = skill.find_element(text="稍后再说", xml=xml)
  if elem and elem.center:
      skill.tap(*elem.center)
  ```
- **陷阱**：不处理此弹窗会导致后续 tap 坐标偏移或操作失败。

---

## 3. 会话列表页 (Conversation List)

**页面特征**：底部 Tab「消息」高亮；顶部右侧有搜索图标（`n68`）；列表显示各会话条目（`iql` = 会话名）。

### 3.1 进入搜索页

```python
skill.tap_element("search_entry")   # resource-id: com.tencent.wework:id/n68
time.sleep(1.0)
```

- **完成标志**：页面顶部出现搜索输入框（`lhb`）。
- **陷阱**：点击后有约 0.5–1s 动画；过早 dump XML 会拿到旧页面。

### 3.2 导航到「消息」Tab（从任意页面恢复）

```python
xml = skill.get_ui_hierarchy()
coord = skill.find_element(text="消息", xml=xml)
if coord and coord.center:
    skill.tap(*coord.center)
    time.sleep(1.5)
```

---

## 4. 搜索页 (Search)

**页面特征**：顶部有 EditText（`lhb`，hint=「搜索」）；软键盘弹出。

### 4.1 输入搜索关键词

```python
skill.tap_element("search_input")   # resource-id: com.tencent.wework:id/lhb
time.sleep(0.3)
skill.type_text(contact_name)       # ASCII 用 input text；中文用 ADBKeyboard
time.sleep(1.5)
```

- **中文输入**：需设备安装 `com.android.adbkeyboard`（ADBKeyboard）；否则中文会静默失败，需手动输入。
- **陷阱**：`type_text()` 对中文会先 `ime set AdbIME`，操作完后恢复原 IME。

### 4.2 清空搜索框重新搜索

```python
skill.tap_element("search_input")
skill._adb("shell input keyevent 123")   # KEYCODE_MOVE_END
skill._adb("shell input keyevent --longpress 67")  # 长按 DEL 全选并清除
skill.type_text(new_contact_name)
```

---

## 5. 联系人资料页 (Contact Profile)

**页面特征**：显示联系人头像和名称；底部或中部有「进入」按钮（`ge0`）。

**导航路径**：会话列表 → 搜索 → 输入联系人名 → 自动跳转到联系人资料页

### 5.1 进入会话

```python
# 搜索后当前页面为联系人资料页
skill.tap_element("session_item")   # resource-id: com.tencent.wework:id/ge0（「进入」按钮）
time.sleep(2.0)
```

- **完成标志**：进入聊天页，出现消息列表和输入框。
- **陷阱**：搜索结果可能直接显示资料页（仅一个匹配时），或显示列表（多个匹配时）。`ge0` 仅在资料页存在；多结果列表页需要不同处理。

---

## 6. 聊天页 (Chat)

**页面特征**：顶部标题为联系人/群名；消息列表（`jcp` ListView）；底部输入栏（`j28`）。

### 6.1 获取消息列表（parse_messages）

```python
messages = skill.parse_messages()
# 返回 List[ChatMessage]，每条含 sender / text / time_str
```

**消息气泡层级（1-on-1）**：
```
eyy (行容器)
├── j7h → j7j → j2e  (时间戳头)
└── j5p
    ├── ja3  (头像 ImageView)
    └── jaf → j5a → j5_ → iql → irb → jbr → j1l  (消息正文)
```

**时间戳说明**：`j2e` 是「消息组」级别的时间头，每隔一段时间显示一次，不是每条消息都有。`parse_messages()` 中 time_str 使用 XML 中出现顺序的最近时间头。

**机器人卡片消息（如企业微信内置「一周小结」服务）**：
```
eyy
└── j5_
    ├── j97  (卡片标题)
    ├── j99 / j8_ / j8f / j9a  (卡片内容行)
```
- 机器人卡片不包含发送人信息；`message_sender` 匹配为空。
- 注意：`一周小结` 是 WeCom 内置服务频道，非普通联系人；`行业资讯` 同理，且使用 WebView 渲染。

### 6.2 滚动加载更多历史消息

```python
# 向上滑动加载更早消息（坐标基于 1080×2400 分辨率）
skill._adb("shell input swipe 540 600 540 1800 500")
time.sleep(1.5)
```

### 6.3 退出聊天页（返回会话列表）

```python
skill._adb("shell input keyevent 4")   # BACK
time.sleep(0.8)
```

---

## 7. 错误恢复

### 7.1 设备切换到其他 App / 主屏

```python
skill._adb("shell monkey -p com.tencent.wework -c android.intent.category.LAUNCHER 1")
time.sleep(3.0)
```

### 7.2 搜索结果为空 / 联系人未找到

- **症状**：`open_conversation()` 返回 False，日志输出 "搜索结果中未找到会话"。
- **处理**：
  1. 检查 `contacts.yaml` 中名称拼写是否与企业微信中完全一致（包含全/半角空格）。
  2. 中文名称需确认 ADBKeyboard 已安装并生效。
  3. 手动在设备上搜索验证名称。

### 7.3 ELEMENTS 未就绪

- **症状**：日志 "ELEMENTS 尚未探测（缺失: ...），open_conversation/parse_messages 不可用"。
- **处理**：检查 `WeComAutomationSkill.ELEMENTS` 中必填项（search_entry/search_input/session_item/message_text/message_time）是否已填入真实 resource-id。

---

## 8. 页面状态检测速查

| 特征 resource-id | 含义 |
|---|---|
| `n68` | 在会话列表页（搜索入口可见） |
| `lhb` | 在搜索页（搜索输入框可见） |
| `ge0` | 在联系人资料页（「进入」按钮可见） |
| `j28` | 在聊天页（消息输入框可见） |
| `jcp` | 在聊天页（消息 ListView 可见） |

```python
def detect_page(skill) -> str:
    xml = skill.get_ui_hierarchy()
    if skill.find_element(resource_id="com.tencent.wework:id/j28", xml=xml):
        return "chat"
    if skill.find_element(resource_id="com.tencent.wework:id/ge0", xml=xml):
        return "contact_profile"
    if skill.find_element(resource_id="com.tencent.wework:id/lhb", xml=xml):
        return "search"
    if skill.find_element(resource_id="com.tencent.wework:id/n68", xml=xml):
        return "conversation_list"
    return "unknown"
```
