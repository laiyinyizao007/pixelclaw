# Boss直聘 ADB 自动化 — 原子操作手册

> 按**页面**组织，记录每个操作的前置页面、触发方式、完成标志和常见陷阱。
> 新功能开发时以此为查询起点，不必重新 dump XML。

---

## 目录

1. [基础设施 — ADB / 设备层](#1-基础设施--adb--设备层)
2. [启动页 / 锁屏](#2-启动页--锁屏)
3. [主页 (Home)](#3-主页-home)
4. [职位列表页 (Job List)](#4-职位列表页-job-list)
5. [筛选面板 (Filter Panel)](#5-筛选面板-filter-panel)
6. [职位详情页 (Job Detail)](#6-职位详情页-job-detail)
7. [聊天页 (Chat)](#7-聊天页-chat)
8. [消息列表页 (Messages Tab)](#8-消息列表页-messages-tab)
9. [全局导航 — 底部 Tab 栏](#9-全局导航--底部-tab-栏)
10. [错误恢复](#10-错误恢复)
11. [页面状态检测速查](#11-页面状态检测速查)

---

## 1. 基础设施 — ADB / 设备层

这些操作不属于任何特定页面，是所有场景的前提。

### 1.1 防止锁屏（会话级别设置，必须最先执行）

```python
skill._adb("shell svc power stayon true")              # USB 连接时屏幕永不熄灭
skill._adb("shell settings put system screen_off_timeout 2147483647")  # 超时=永不
```

- **什么时候调**：脚本启动后第一件事，在 `launch()` 之前。
- **陷阱**：仅设置 `screen_off_timeout` 不够；keyguard（锁屏遮罩）仍可在应用切换时出现。必须同时用 `svc power stayon true`。
- **持久性**：`svc power stayon` 在设备重启或 USB 断连后失效，每次连接要重设。

### 1.2 唤醒屏幕 + 清除锁屏遮罩

```python
skill._adb("shell input keyevent 224")      # KEYCODE_WAKEUP — 点亮屏幕
time.sleep(1.0)
skill._adb("shell wm dismiss-keyguard")     # 清除 keyguard
time.sleep(0.5)
skill._adb("shell input swipe 540 1800 540 600 300")  # 上滑手势（降级保底）
time.sleep(0.5)
skill._adb("shell input keyevent 4")        # BACK — 关闭可能弹出的浮层
```

- **什么时候调**：`launch()` 前 + `launch()` 后（启动动画有时会触发锁屏）。
- **陷阱**：`mWakefulness=Awake` 为 True ≠ 屏幕已解锁。keyguard 可以在屏幕亮着的情况下覆盖 App，`_is_screen_on()` 对此无感知。

### 1.3 启动 App

```python
ok = skill.launch()   # 内部等待 4 s 让 App 恢复状态
```

- **返回页面**：推荐页、主页、或职位列表页（取决于上次退出状态）。
- **完成标志**：`get_current_page() != PageState.UNKNOWN`，并且已通过 `ensure_ready()`。

### 1.4 检查 ADB 连接

```python
connected = skill.adb.is_device_connected(skill.device_id)
```

---

## 2. 启动页 / 锁屏

### 2.1 判断当前是否在锁屏

```python
ok, out = skill.adb.shell("shell dumpsys power", skill.device_id)
is_awake = "mWakefulness=Awake" in out   # 屏幕亮着（不代表已解锁）
```

注意：`PageState.UNKNOWN` 是锁屏/遮罩的最常见体现，而非 `mWakefulness=Asleep`。

### 2.2 恢复到已知状态（ensure_ready）

```python
ok = skill.ensure_ready()
# 内部逻辑：
#   1. 检查 ADB 连接
#   2. 检查 _is_screen_on() → 否则 _wake_screen()
#   3. 检查 get_current_page() == UNKNOWN → _wake_screen() + 轮询 12s
#   4. 若仍 UNKNOWN → 重启 App + _wake_screen()
#   5. 致命弹窗 → return False
```

- **返回值**：False 表示无法恢复，应终止任务。

---

## 3. 主页 (Home)

**PageState**: `HOME`  
**关键锚点**: `com.hpbr.bosszhipin:id/et_search`（搜索框）

### 3.1 在主页直接发起搜索

```python
skill.tap_element("search_bar")        # 点击 et_search
skill._adb("shell input keyevent 123") # MOVE_END（光标移到末尾）
skill._adb("shell input keyevent 67 " * 30)  # 清空
skill.type_text("AI产品经理")
skill._adb("shell input keyevent 66")  # ENTER
```

或直接调用封装：

```python
ok = skill.browse_jobs("AI产品经理")
```

- **完成标志**：`wait_for_element("com.hpbr.bosszhipin:id/tv_position_name", timeout=15.0)` 返回非 None。
- **陷阱**：主页和职位列表页的搜索入口不同（见 3.2）。

### 3.2 从非主页发起搜索（职位列表页情况）

主页搜索框 `et_search` 在职位列表页**不可见**（toolbar 会折叠）。  
正确流程：

```python
# 方案 A：通过 toolbar 右上角搜索图标（ly_menu 最右侧 img_icon）
skill._open_search_from_job_list()

# 方案 B：先导航到 职位 tab，toolbar 展开后再点搜索图标
skill.navigate_to_tab("jobs")
time.sleep(1.0)
skill._open_search_from_job_list()
```

`browse_jobs()` 内部已自动降级处理，一般不需要手动选方案。

---

## 4. 职位列表页 (Job List)

**PageState**: `JOB_LIST`  
**关键锚点**: `com.hpbr.bosszhipin:id/tv_position_name`（每张 job card 的职位名）

### 4.1 获取当前屏幕所有职位卡

```python
jobs: List[JobInfo] = skill.get_job_list()
# 每个 JobInfo 包含：
#   title, company, salary, location, hr_name, hr_title, hr_active
#   tap_x, tap_y  ← 当前 dump 时的坐标，导航前必须确保屏幕未滚动
```

**卡片层级（关键）**：`tv_position_name` 的直接父节点是 `cl_position`，**只含标题本身**。
公司/薪资/地点/HR 挂在更上层的 `view_job_card` 上，必须向上爬 2 层才能取到：

```
cl_position          ← tv_position_name 的直接父节点（只有标题）
  └ view_job_card    ← 真正的卡片容器（全部字段在这里）
     └ boss_job_card_view
        └ cl_card_container
```

**列表卡 resource-id 映射**（与详情页命名不同，勿混用）：

| 字段 | 列表卡 resource-id | 示例 |
|------|-------------------|------|
| `title` | `tv_position_name` | AI产品经理（AI工具方向） |
| `salary` | `tv_salary_statue` | 20-30K·14薪 |
| `company` | `tv_company_name` | 二三四五网络 |
| `location` | `tv_distance` | 上海  浦东新区  张江 |
| `hr_name` + `hr_title` | `tv_employer` | `刘女士 · 招聘经理`（按 `·` 拆分）|
| — | `tv_scale` | 100-499人 |
| — | `tvIndustryName` | 互联网 |
| — | `tv_digest` | 职位摘要一行 |

- **陷阱（层级）**：只 `parent_map.get(node)` 爬一层会落在 `cl_position`，除 `title` 外全部为空字符串。
- **陷阱（命名差异）**：地点在列表页是 `tv_distance`，详情页才是 `tv_location`/`tv_area_district`；HR 在列表页是合并的 `tv_employer`，详情页才拆成 `tv_boss_name`/`tv_boss_title`。
- **`hr_active` 在列表卡中不存在**，恒为 `""`，需要活跃状态只能进详情页取。
- **屏幕边缘的卡片字段会缺失**：最后一张卡常只渲染出部分子节点，属正常现象，滚动后重新 dump 即可补全。

- **陷阱（坐标失效）**：调用 `_return_to_job_list()` 重新搜索后，列表回到顶部，旧的 `tap_y` 指向不同的职位。必须在每次导航前重新获取坐标。
- **正确模式（增量）**：

```python
# 每次导航前现取坐标
job = _find_next_job(skill, visited_titles)  # 返回当前屏幕中未访问的第一个职位
skill.navigate_to_job(job)
```

### 4.2 向下滚动列表

```python
skill.scroll_down(start_y=1800, end_y=600)  # 向上滑动 = 列表向下翻
time.sleep(0.8)
```

- `start_y=1800, end_y=600`：适配 Pixel 8a（2400px 高），一次约翻 3 张卡。
- **陷阱**：滚动后旧坐标失效，必须重新 dump。

### 4.3 批量收集 N 个职位（含去重）

```python
jobs = skill.scroll_job_list(n_jobs=10, max_scrolls=15)
```

内部自动去重，但返回的坐标在 `_return_to_job_list()` 后失效（见 4.1 陷阱）。

### 4.4 进入某个职位详情

```python
ok = skill.navigate_to_job(job)   # 使用 job.tap_x / job.tap_y
time.sleep(1.0)
detail_elem = skill.wait_for_element(
    "com.hpbr.bosszhipin:id/tv_job_name", timeout=12.0
)
# detail_elem is None → 加载超时，执行 press_back()
```

### 4.5 打开筛选面板

```python
skill.tap_element("filter_btn")   # com.hpbr.bosszhipin:id/filterBarRightTabView
time.sleep(0.8)
# → 进入 PageState.FILTER_PANEL
```

---

## 5. 筛选面板 (Filter Panel)

**PageState**: `FILTER_PANEL`  
**关键锚点**: `btn_confirm` + `btn_reset` 同时存在（唯一组合）

### 5.1 按条件筛选

```python
skill.set_filter(
    city="上海",
    salary="20K-50K",
    experience="3-5年",
    education="本科",
)
# 内部：text 匹配各 chip，最后点 btn_confirm 或"确定"/"完成"
```

### 5.2 关闭筛选面板（不应用）

```python
skill.press_back()
```

---

## 6. 职位详情页 (Job Detail)

**PageState**: `JOB_DETAIL`  
**关键锚点**: `com.hpbr.bosszhipin:id/tv_job_name`

### 6.1 提取基础字段（顶部，加载即可见）

```python
xml_top = skill.get_ui_hierarchy()
detail  = skill.get_job_detail(xml=xml_top)
# 通常包含: title, hr_name, hr_title, location
```

### 6.1.1 处理「网络异常」占位页（首次 dump 后立即检查）

```python
xml_top = skill.get_ui_hierarchy()
if xml_top and "网络异常" in xml_top:
    if skill.recover_detail_page():      # 最多点 3 次「重试」
        xml_top = skill.get_ui_hierarchy()
    else:
        ...  # 记入 errors，别静默通过
detail = skill.get_job_detail(xml=xml_top)
```

- **陷阱（锚点存在 ≠ 页面正常）**：占位页照常渲染 `tv_job_name` 与顶部 chips（`面议` / 城市 / 经验 / 学历），只有描述与公司信息块整块缺失，因此 `wait_for_element("tv_job_name")` 会成功，`navigate_to_job()` 也返回 True，整条职位被当成抓取成功记下去，最终 JSON 里只有 `title` 一个字段而 `errors` 为空。
- **成功判据必须是文本检测**：`"网络异常" not in xml`，不能用锚点节点是否存在。
- **识别特征**：`raw_texts` 恰好是 `['面议', '上海', '在校/应届', '本科', '网络异常，请检查网络后重试', '重试']` 这类「顶部 chips + 占位文案 + 重试」组合（实测 `AI产品经理` 第 1、3 条逐字节相同）。与 §6.2 的滚动未到底区分：后者 `raw_texts` 中**没有** `网络异常`。
- **恢复按钮**：占位页底部的「重试」已在 `_find_recovery_button()` 的匹配列表内（`重新加载` / `点击重试` / `重试`），与搜索页共用同一套定位逻辑。

### 6.2 滚动加载完整描述 + 公司信息

```python
prev_xml, stale = "", 0
for _ in range(30):                              # _MAX_DETAIL_SCROLLS
    skill.scroll_down(start_y=1600, end_y=800)
    time.sleep(0.5)
    xml_bot = skill.get_ui_hierarchy()
    if not xml_bot:
        break
    if xml_bot == prev_xml:
        stale += 1
        if stale >= 4:                           # _MAX_STALE_DUMPS
            break
        time.sleep(1.0)                          # 等懒加载 inflate
        continue
    stale = 0
    prev_xml = xml_bot
    _merge_detail(detail, skill.get_job_detail(xml=xml_bot))
    if "company" in detail and "company_info" in detail:
        break
```

- **哪些字段在底部**：`company`（`tv_com_name`）、`company_info`（`tv_com_info`）、`location`（`tv_location`）三个都来自页面底部的公司信息块，不在顶部头部。若某条职位缺 `company`，多半 `location` 也一起缺——这就是没滚到底的信号。
- **陷阱（懒加载 ≠ 到底）**：公司信息块由外层 `rv_list` RecyclerView 懒加载，inflate 之前页面**滚不动**，连续多轮 dump 字节完全相同。实测连续 3 轮 md5 一致（13880 字符、31 节点），第 4 轮突然变成 22315 字符、53 节点，`tv_com_name` / `tv_com_info` / `tv_location` / `iv_map` / `bl_location_view` 同时出现。`tv_description` 的 bounds 由 `[53,0][1027,2155]` 移到 `[53,0][1027,1095]`，证明期间确实在滚动，只是新内容尚未 inflate。
- **因此**：`if xml == prev: break` 这种单次判定会把「内容还没加载完」误判成「已到底」，长描述（实测 1749 / 985 字）必然漏抓。必须连续 N 轮不变才退出，且每轮不变时 sleep 一下给 inflate 留时间。
- **陷阱（滚动次数上限）**：`_MAX_STALE_DUMPS` 才是真正的到底判据，`_MAX_DETAIL_SCROLLS` 只是防死循环的安全网，必须留足余量。多数职位 4 轮即可到底，但描述越长公司信息块被推得越远——实测 2406 字的描述滚穿 14 次仍未到底（日志表现为「滚动到底仍未抓到公司信息」，但 `raw_texts` 中**没有** `网络异常` 字样，可据此与网络占位页区分）。现已提升至 30。

### 6.2.1 展开"查看更多"折叠的职位描述

```python
ok = skill.expand_description()   # 内部会按需滚动 + 点击
if not ok:
    logger.warning("描述可能仍被截断")
```

- **什么时候调**：`get_job_detail()` 之前，`navigate_to_job()` 之后。
- **完成标志**：`tv_description` 的 text 中不再包含 `查看更多`。实测 402 字 → 1032 字。
- **陷阱（inline span）**：`查看更多` **不是独立 XML 节点**，而是 `tv_description` 这个 TextView 内部的 ClickableSpan，`clickable=false`。`find_element(text="查看更多")` 永远返回 None，只能按坐标点击最后一行右端 `(x2-90, y2-28)`。
- **陷阱（bounds 被裁剪）**：`tv_description` 的 bounds 会被外层 `rv_list` RecyclerView 裁剪到屏幕底部（如 `y2=2161` 恰好等于 recycler 的 `y2`）。此时算出的"最后一行"其实在屏幕外，点上去无效。必须先小步滚动（`scroll_down(1600 → 1200)`）直到 `desc.y2 < rv_list.y2 - 5`，bounds 才可信。
- **陷阱（滚动过头）**：`scroll_down(1600 → 800)` 连滚两次会直接划过描述区，`tv_description` 从 XML 中消失。展开必须在大幅滚动**之前**做。
- **陷阱（合并覆盖）**：滚动前的 `xml_top` dump 里已经有一份**截断版** `description`。合并两次 dump 时若用 `if key not in detail` 逻辑，截断版会胜出、展开结果被丢弃。必须按长度取较长者。

### 6.3 合并两次 dump 的字段

```python
for key, val in detail_bot.items():
    if key == "raw_texts":
        seen = set(detail["raw_texts"])
        detail["raw_texts"] += [t for t in val if t not in seen]
    elif key in ("skills", "benefits"):
        existing = set(detail.get(key, []))
        detail.setdefault(key, [])
        detail[key] += [t for t in val if t not in existing]
    elif key not in detail:
        detail[key] = val
```

### 6.4 `get_job_detail()` 字段映射

| 字段名 | resource-id 片段 | 说明 |
|--------|-----------------|------|
| `title` | `tv_job_name` | 详情页职位名 |
| `salary` | `tv_salary_desc` | 薪资范围 |
| `location` | `tv_location` / `tv_area_district` / `tv_job_area` | 工作地点 |
| `experience` | `tv_experience` | 工作年限要求 |
| `education` | `tv_degree` | 学历要求 |
| `description` | `tv_description` / `tv_job_desc` | 职位描述正文 |
| `company` | `tv_com_name` / `tv_company_name` | 公司名称 |
| `company_info` | `tv_com_info` | 公司规模/行业/融资阶段 |
| `hr_name` | `tv_boss_name` | HR 姓名 |
| `hr_title` | `tv_boss_title` | HR 职位 |
| `hr_active` | `boss_status` / `active_time` | HR 活跃状态 |
| `skills` | `tv_skill_tag` | 技能标签（列表）|
| `benefits` | `tv_benefit` | 福利标签（列表）|
| `raw_texts` | 所有其余文本节点 | 保底兜底，不丢信息 |

### 6.5 检测并处理弹窗

```python
dialog = skill.detect_dialog()
if dialog != DialogType.NONE:
    skill.dismiss_dialog(dialog)
    if dialog in {DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED}:
        # 致命，终止整个任务
        return report
    if dialog == DialogType.JOB_OFFLINE:
        # 跳过该职位，press_back() 返回列表
        skill.press_back()
        continue
```

### 6.6 点击"立即沟通"进入聊天

```python
ok = skill.tap_element("chat_btn")   # com.hpbr.bosszhipin:id/btn_chat
time.sleep(1.5)
# → 进入 PageState.CHAT 或 PageState.DIALOG（已建立沟通弹窗）
```

### 6.7 返回职位列表

```python
skill.press_back()
time.sleep(1.0)
# 通常回到 JOB_LIST；若导航栈异常则需 _return_to_job_list()
```

---

## 7. 聊天页 (Chat)

**PageState**: `CHAT`  
**关键锚点**: `com.hpbr.bosszhipin:id/editText_with_scrollbar`（输入框）

### 7.1 发送问候语

```python
ok = skill.send_greeting("您好，我对这个职位很感兴趣，请问方便聊聊吗？")
# 内部：tap chat_input → type_text → KEYCODE_ENTER
```

### 7.2 带验证发送

```python
ok = skill.send_greeting(message, verify=True)
# verify=True: 发送后轮询 3s 确认消息气泡出现
```

### 7.3 检查是否可以投递简历

```python
can = skill.can_apply()
# 返回 True 当且仅当双方均已发过消息（平台规则）
```

### 7.4 投递简历

```python
ok = skill.apply_to_job()
# 点击 com.hpbr.bosszhipin:id/btn_apply（需 can_apply() == True）
```

### 7.5 退出聊天页

```python
skill.press_back()   # 回到 JOB_DETAIL 或 MESSAGES，取决于入口
```

---

## 8. 消息列表页 (Messages Tab)

**PageState**: `MESSAGES`  
**关键锚点**: `com.hpbr.bosszhipin:id/contact_vp`

### 8.1 获取消息列表

```python
chats: List[ChatEntry] = skill.get_message_list()
# 每个 ChatEntry: hr_name, position, last_msg, time_str, tap_x, tap_y
```

### 8.2 进入某条对话

```python
ok = skill.navigate_to_chat(entry)
# 使用 entry.tap_x / entry.tap_y
# → 进入 PageState.CHAT
```

### 8.3 滚动消息列表

```python
skill.scroll_down(start_y=1800, end_y=600)
time.sleep(0.5)
# 继续调 get_message_list() 获取更多
```

---

## 9. 全局导航 — 底部 Tab 栏

底部 Tab 栏**在所有页面均可见**（包括 toolbar 折叠时），是最可靠的跨页导航入口。

```python
skill.navigate_to_tab("recommend")   # 推荐 tab → PageState.RECOMMEND
skill.navigate_to_tab("jobs")        # 职位 tab → PageState.JOB_LIST 或 HOME
skill.navigate_to_tab("messages")    # 消息 tab → PageState.MESSAGES
skill.navigate_to_tab("profile")     # 我的 tab → PageState.PROFILE
```

**resource-id 格式**：`com.hpbr.bosszhipin:id/cl_tab_{1|2|3|4}`  
**文字标签**：推荐 / 职位 / 消息 / 我的

### 9.1 从任意页面回到职位搜索结果（标准复归流程）

```python
def _return_to_job_list(skill, keyword) -> bool:
    # 1. 清除弹窗
    if skill.get_current_page() == PageState.DIALOG:
        skill.press_back(); time.sleep(0.8)
    # 2. 导航到 职位 tab（底部 tab 栏始终可见）
    skill.navigate_to_tab("jobs")
    time.sleep(1.0)
    # 3. 重新搜索
    ok = skill.browse_jobs(keyword)
    time.sleep(1.5)
    # 4. 等待列表加载
    return skill.wait_for_element(
        "com.hpbr.bosszhipin:id/tv_position_name", timeout=15.0
    ) is not None
```

---

## 10. 错误恢复

### 10.1 弹窗类型与处理

| DialogType | 触发关键词 | 处理方式 |
|-----------|-----------|---------|
| `DAILY_LIMIT` | 免费沟通名额已使用完 | 致命 → 终止任务 |
| `LOGIN_REQUIRED` | 立即登录 / 请登录后操作 | 致命 → 终止任务 |
| `JOB_OFFLINE` | 该职位已下线 / 暂停招聘 | 跳过该职位 |
| `EXISTING_CHAT` | 已和对方建立沟通 | 直接进入聊天，无需特殊处理 |
| `UNKNOWN_DIALOG` | 其他弹窗 | press_back() 或点"确定"/"关闭" |

### 10.2 UNKNOWN 页面恢复

```python
# ensure_ready() 内部流程：
# 1. _wake_screen()（WAKEUP + dismiss-keyguard）
# 2. 轮询 12s 等待页面恢复
# 3. 超时 → launch() + _wake_screen()
# 4. 仍 UNKNOWN → return False（放弃恢复）
```

### 10.3 常见场景与恢复方式

| 场景 | 症状 | 恢复方式 |
|------|------|---------|
| 锁屏 | `get_current_page()` = UNKNOWN | `ensure_ready()` 或手动 `_wake_screen()` |
| 浮层弹出 | `detect_dialog()` ≠ NONE | `dismiss_dialog(dialog)` |
| toolbar 折叠 | `browse_jobs()` 找不到搜索框 | `navigate_to_tab("jobs")` 后重试 |
| 坐标失效 | 点击后进入错误职位 | 重新 `_find_next_job()` 获取坐标 |
| 职位详情加载超时 | `wait_for_element("tv_job_name")` 返回 None | `press_back()` 跳过 |
| 详情页网络异常 | 锚点与顶部 chips 正常，但 dump 含 `网络异常`，只抓到 `title` | `recover_detail_page()`（见 §6.1.1） |
| App 崩溃 | ADB 连接正常但页面 UNKNOWN 且无法恢复 | `launch()` + `ensure_ready()` |

---

## 11. 页面状态检测速查

`get_current_page()` 检测优先级（高 → 低）：

| 优先级 | PageState | 唯一锚点 |
|--------|----------|---------|
| 1 | `DIALOG` | 任意 DialogType 关键词 |
| 2 | `FILTER_PANEL` | `btn_confirm` + `btn_reset` 同时存在 |
| 3 | `CHAT` | `editText_with_scrollbar` |
| 4 | `JOB_DETAIL` | `tv_job_name` |
| 5 | `RESUME` | `basic_info` + `rv_list` |
| 6 | `PROFILE` | `myGeekRoot` |
| 7 | `MESSAGES` | `contact_vp` |
| 8 | `RECOMMEND` | `boss_job_card_view` |
| 9 | `JOB_LIST` | `tv_position_name` 或 `magic_indicator` |
| 10 | `HOME` | `et_search` |
| 11 | `UNKNOWN` | 以上均未找到（通常是锁屏或加载中）|

---

*最后更新：2026-09-06*  
*对应代码版本：`skills/boss/boss_automation_skill.py`（Pixel 8a + Boss直聘 v10.x）*
