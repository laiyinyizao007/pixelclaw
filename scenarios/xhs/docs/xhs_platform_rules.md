# 小红书 平台规则

> **来源说明**：本文档综合官方社区规范、行业研究及实测结果整理而成。
> 每条规则标注来源类型：`[官方]` / `[实测]` / `[推断]`。
> 探索新功能前请先阅读本文档并更新待验证项（`TODO`）。

---

## 核心操作规则

### 内容发布

| 项目 | 内容 |
|------|------|
| **规则** | 平台明确打击 AI 托管批量养号、AI 生成虚假内容、批量刷量等行为 |
| **来源** | `[官方]` 小红书社区规范 2026版 |
| **处罚** | 偶发违规 → 限流警告；全程 AI 托管账号 → 直接封禁 |
| **代码影响** | 操作间需加随机延迟（`action_delay`），避免机械频率；每次发帖/互动后停顿 ≥ 2 秒 |

---

### 每日互动频率限制

| 项目 | 内容 |
|------|------|
| **规则** | 点赞、收藏、关注存在每日操作频次限制，频繁操作触发风控 |
| **来源** | `[推断]` 基于行业实测报告 |
| **UI 表现** | 弹出提示，含文字"操作过于频繁" / "请稍后再试" / "今日次数已达上限" |
| **代码影响** | 检测到此弹窗应暂停互动操作；`DialogType.RATE_LIMIT`（待实现） |
| **TODO** | 具体次数上限（随账号等级/版本变化，需真机实测） |

---

### 登录态要求

| 项目 | 内容 |
|------|------|
| **规则** | 浏览帖子无需登录；点赞、收藏、关注、评论、发布均需已登录 |
| **来源** | `[实测]` |
| **UI 表现** | 弹出登录页/引导弹窗，含文字"登录" / "立即登录" / "登录后查看" |
| **代码影响** | `DialogType.LOGIN_REQUIRED`（待实现）；检测到时停止自动化并通知用户 |

---

### 私信/评论限制

| 项目 | 内容 |
|------|------|
| **规则** | 评论、私信不得含引流外链、联系方式、营销词，否则触发限流或处罚 |
| **来源** | `[官方]` |
| **UI 表现** | 发送时弹出"内容违规" / "包含违禁内容" |
| **代码影响** | 发评论/私信前过滤敏感词；发布文案使用拼音规避部分检测 |

---

### 账号封禁条件

| 行为 | 处罚力度 |
|------|---------|
| 偶发违规内容（低质、营销等） | 限流警告 |
| 批量虚假评论/水军行为 | 限流 + 禁言 |
| 全程 AI 托管账号 | 直接封禁 |
| 引导站外交易 | 最高永久封号 |
| 批量刷量（关注/点赞/收藏）| 账号降权或封禁 |

---

## 弹窗检测快速参考

`DialogType._SIGNATURES`（`skills/xhs/xhs_automation_skill.py` 待实现）通过文本包含匹配识别弹窗：

| 弹窗类型 | 关键词（推断，需真机验证）|
|---------|--------|
| `LOGIN_REQUIRED` | `"登录"` / `"立即登录"` / `"登录后查看"` |
| `RATE_LIMIT` | `"操作过于频繁"` / `"请稍后再试"` / `"今日次数已达上限"` |
| `CONTENT_VIOLATION` | `"内容违规"` / `"包含违禁内容"` |
| `NETWORK_ERROR` | `"网络异常"` / `"加载失败"` / `"请检查网络"` |

---

## 页面导航规则

- **底部导航 Tab**：✅ 优先使用 resource-id 定位（语义化，稳定）
  - `index_home` / `index_store` / `index_post` / `index_message` / `index_me`
  - 文字"首页"/"视频"/"消息"/"我"作为备用 fallback
- **帖子互动区域**：✅ `noteLikeLayout` / `noteCollectLayout` / `noteCommentLayout` 均已真机确认（旧版图文帖）
- **新版视频帖互动**：✅ `followBtn` / `navBarShareBtn` / `bottomComment` 均已登录态真机确认
- **返回**：优先 `com.xingin.xhs:id/backIV`（已验证）；fallback 使用 keyevent 4
- **登录弹窗**：`com.xingin.xhs:id/close` 关闭按钮已验证；检测条件：同时出现 `dialog` + `mWeiChatLoginView`
- **页面状态检测**：通过特征元素判断当前页面（见下表）

| 页面 | 特征 resource-id | 备注 |
|------|-----------------|------|
| 首页 | `com.xingin.xhs:id/exploreCoordinator` | ✅ 已验证 |
| 旧版图文帖详情 | `com.xingin.xhs:id/noteDetailRoot` | ✅ 已验证 |
| 新版视频帖详情 | `com.xingin.xhs:id/matrix_video_feed_note_detail_list` | ✅ 已验证（登录态） |
| 消息页 | `com.xingin.xhs:id/msgRecyclerView` | ✅ 已验证（登录态） |
| 个人中心 | `com.xingin.xhs:id/matrix_profile_new_page_coordinator_layout` | ✅ 已验证（登录态） |

---

## Resource-ID 验证记录

> **更新 1（2026-09-04，非登录态）**：在 Pixel 8a (device 42231JEKB04971) 上运行 `dump_xhs_ui.py`，发现 **175 个唯一 resource-id**，覆盖 8 个页面。
>
> **更新 2（2026-09-04，登录态）**：用户登录后重新运行，发现 **288 个唯一 resource-id**，覆盖 9 个页面（新增消息页、个人中心完整数据、发布菜单）。
>
> XHS 核心 UI 元素大多使用**语义化命名**，并非混淆短名。

### 帖子详情互动元素

| 友好名 | resource-id | 验证状态 | 帖子类型 |
|--------|-------------|---------|---------|
| `like` | `com.xingin.xhs:id/noteLikeLayout` | ✅ 已验证 | 旧版图文帖 |
| `collect` | `com.xingin.xhs:id/noteCollectLayout` | ✅ 已验证 | 旧版图文帖 |
| `comment` | `com.xingin.xhs:id/noteCommentLayout` | ✅ 已验证 | 旧版图文帖 |
| `input_comment` | `com.xingin.xhs:id/inputCommentTV` | ✅ 已验证 | 旧版图文帖 |
| `more` | `com.xingin.xhs:id/moreOperateIV` | ✅ 已验证 | 旧版图文帖 |
| `back` | `com.xingin.xhs:id/backIV` | ✅ 已验证 | 旧版图文帖 |
| `follow` | `com.xingin.xhs:id/followBtn` | ✅ 已验证（登录态） | 新版视频帖 |
| `share` | `com.xingin.xhs:id/navBarShareBtn` | ✅ 已验证（登录态） | 新版视频帖 |
| `bottom_comment` | `com.xingin.xhs:id/bottomComment` | ✅ 已验证（登录态） | 新版视频帖 |

> **注意**：旧版图文帖与新版视频帖使用不同的互动元素集合。通过 `noteDetailRoot`（旧版）vs `matrix_video_feed_note_detail_list`（新版）区分帖子类型。
> `shareBtn` 并非实际 resource-id（为早期推测值）；真实分享入口为 `navBarShareBtn`。

### 新版视频帖底部互动栏坐标（Canvas 渲染，无 resource-id）

新版视频帖（`matrix_video_feed_note_detail_list`）的互动按钮（❤️⭐💬）用 Canvas 绘制，uiautomator dump 中找不到对应元素，只能用坐标点击。

**设备**：Pixel 8a, 1080×2400 原始分辨率, 420dpi（2026-09-04 像素扫描实测 + 真机验证）

| 按钮 | x（原始） | y（原始） | 验证状态 |
|------|-----------|-----------|---------|
| ❤️ 点赞 | 534 | 2286 | ✅ 推算值（由 ⭐ 位置±207px 估算） |
| ⭐ 收藏 | 741 | 2286 | ✅ **真机验证**（toast "收藏成功"，计数 +1，图标变金色） |
| 💬 评论 | 928 | 2286 | ✅ 推算值 |

**注意**：
- 这些坐标**仅适用于 Pixel 8a（1080×2400）**；其他分辨率设备需重新测量。
- y=2286 在导航栏（y=2337-2400）之上约 51px，是内容区底部边缘。
- 互动栏的上方还有两行内容：话题标签条（y≈2150）和相关搜索条（y≈2200），不要误点。

### 底部导航

| 友好名 | resource-id | 验证状态 |
|--------|-------------|---------|
| 首页 Tab | `com.xingin.xhs:id/index_home` | ✅ 已验证 |
| 视频 Tab | `com.xingin.xhs:id/index_store` | ✅ 已验证 |
| 发布按钮 | `com.xingin.xhs:id/index_post` | ✅ 已验证 |
| 消息 Tab | `com.xingin.xhs:id/index_message` | ✅ 已验证 |
| 我 Tab | `com.xingin.xhs:id/index_me` | ✅ 已验证 |

### 搜索流程

| 友好名 | resource-id | 验证状态 |
|--------|-------------|---------|
| 首页搜索入口 | `com.xingin.xhs:id/search` | ✅ 已验证 |
| 搜索输入框 | `com.xingin.xhs:id/mSearchToolBarEt` | ✅ 已验证 |
| 搜索执行按钮 | `com.xingin.xhs:id/mSearchToolBarSearchBtn` | ✅ 已验证 |
| 搜索结果 Tab 栏 | `com.xingin.xhs:id/mSearchResultTabBar` | ✅ 已验证 |
| 搜索结果列表 | `com.xingin.xhs:id/mSearchResultListContentTRv` | ✅ 已验证 |

### 消息页（登录态）

| 友好名 | resource-id | 验证状态 |
|--------|-------------|---------|
| 消息列表 | `com.xingin.xhs:id/msgRecyclerView` | ✅ 已验证 |
| 评论和@ | `com.xingin.xhs:id/commentAndAt` | ✅ 已验证 |
| 赞和收藏 | `com.xingin.xhs:id/likeAndCollect` | ✅ 已验证 |
| 新增关注者 | `com.xingin.xhs:id/fans` | ✅ 已验证 |
| 消息搜索 | `com.xingin.xhs:id/iv_search` | ✅ 已验证 |

### 个人中心（登录态）

| 友好名 | resource-id | 验证状态 |
|--------|-------------|---------|
| 头像 | `com.xingin.xhs:id/matrix_profile_user_head_img` | ✅ 已验证 |
| 昵称 | `com.xingin.xhs:id/profile_new_page_avatar_card_nickname` | ✅ 已验证 |
| 关注数 | `com.xingin.xhs:id/follow_count` | ✅ 已验证 |
| 粉丝数 | `com.xingin.xhs:id/fans_count` | ✅ 已验证 |
| 获赞与收藏数 | `com.xingin.xhs:id/fav_count` | ✅ 已验证 |
| 个人中心搜索 | `com.xingin.xhs:id/profileSearchEntrance` | ✅ 已验证 |
| 个人中心分享 | `com.xingin.xhs:id/profileActionBarShareView` | ✅ 已验证 |

### 发布菜单（登录态）

| 友好名 | resource-id | 验证状态 |
|--------|-------------|---------|
| 发布选项 1（图文）| `com.xingin.xhs:id/rlFirst` | ✅ 已验证 |
| 发布选项 2 | `com.xingin.xhs:id/rlSecondWithSubtitle` | ✅ 已验证 |
| 发布选项 3 | `com.xingin.xhs:id/rlThird` | ✅ 已验证 |
| 取消 | `com.xingin.xhs:id/tvCancel` | ✅ 已验证 |

### 登录弹窗

| 友好名 | resource-id | 验证状态 |
|--------|-------------|---------|
| 关闭按钮 | `com.xingin.xhs:id/close` | ✅ 已验证 |
| 弹窗容器 | `com.xingin.xhs:id/dialog` | ✅ 已验证 |
| 微信登录 | `com.xingin.xhs:id/mWeiChatLoginView` | ✅ 已验证 |

---

## 已知问题

### `03_post_detail` 导航失败（持续存在）

`dump_xhs_ui.py` 的 `explore_post_detail()` 函数点击首页 feed item 后 XHS 未导航至帖子详情，dump 仍为首页内容（`exploreCoordinator` / `homeViewPager` 等 ID）。

**影响**：`03_post_detail.xml` 实际是首页内容，非帖子详情。
**旧版图文帖 ID 来源**：`04_blogger_profile.xml`（通过博主主页路径采到的图文帖详情）。
**已知旧版 ID**：`noteDetailRoot`, `noteLikeLayout`, `noteCollectLayout`, `noteCommentLayout`。
**新版视频帖 ID 来源**：`04_blogger_profile.xml`（登录态，点击博主区域后进入视频 feed）。
**修复方向**：在 `explore_post_detail()` 改用 `adb shell input swipe`（向上滑入帖子）或使用 intent 直接打开特定 noteId。

---

## 待研究（TODO）

- [ ] 点赞/收藏/关注每日次数上限的精确值（需真机触发验证）
- [ ] 弹窗文字的真实措辞（需真机触发 `RATE_LIMIT`/`CONTENT_VIOLATION` 弹窗验证）
- [x] ~~搜索框、搜索按钮的实际 resource-id~~ → `mSearchToolBarEt` / `mSearchToolBarSearchBtn` ✅
- [x] ~~底部 + 发布按钮的 resource-id~~ → `index_post` ✅
- [x] ~~`followBtn` / `shareBtn` 真实 resource-id~~ → `followBtn` ✅（已验证）；真实分享为 `navBarShareBtn` ✅
- [x] ~~私信页面（需登录后探索 `05_messages`）~~ → 消息页完整 ID 已验证 ✅
- [ ] `03_post_detail.xml` 导航问题：脚本进入帖子详情失败，需修复 `explore_post_detail()`
- [ ] 是否有"发现/推荐"和"关注"feed 的区分方式（`exploreTabLayoutV2` 可能相关）
- [ ] 评论发送按钮 resource-id（`noteCommentLayout` 点击后的下一步，需实际触发）
- [ ] 私信对话页面 resource-id（进入具体聊天后）
- [ ] 不同账号等级下 `followBtn` 文字变化（关注 vs 已关注 vs 相互关注）
