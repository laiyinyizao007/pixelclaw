# LinkedIn 平台规则

> **来源说明**：本文档综合 LinkedIn 官方帮助中心、用户协议及实测/社区经验整理而成。
> 每条规则标注来源：`[官方]` / `[实测]` / `[推断]` / `[社区]`。
> 探索新功能前请先阅读本文档并更新待验证项（`TODO`）。

---

## 核心限制：反自动化检测

LinkedIn 采用**行为分析**（服务器端）检测自动化，与客户端工具无关，ADB 方案同样适用。

| 检测信号 | 描述 | 来源 |
|---------|------|------|
| 操作速度 | 两个相关操作之间间隔 < 5-10秒 被标记为可疑 | `[官方/社区]` |
| 时间规律性 | 机械性的固定间隔（如恰好每5.0秒一次操作）触发检测 | `[社区]` |
| 非工作时间活动 | 账号所在时区的深夜（如2:00-5:00）高频操作被标记 | `[社区]` |
| 操作总量 | 每日操作总数 > 150 会系统性触发人机验证 | `[社区]` |

**ADB 方案特点**：通过模拟真实设备物理触摸，绕过浏览器指纹检测，但行为分析依然有效。

---

## 每日安全操作上限（实测参考）

| 操作类型 | 推荐上限/天 | 触发风险 |
|---------|-----------|---------|
| 浏览职位总数 | ≤ 100 条 | > 200 时风险升高 |
| 职位详情查看 | ≤ 50 条 | 建议保守估计 |
| 搜索次数 | ≤ 10 次/关键词 | `[推断]` |
| 总操作数（含滚动）| ≤ 150 | `[社区]` |

> 来源：LinkedIn Automation Limits 2026 - linkedhelper.com, zeliq.com

---

## 登录态要求

| 项目 | 内容 |
|------|------|
| **规则** | 查看职位详情（描述、申请人数等）**需要登录** |
| **来源** | `[实测]` |
| **UI 表现** | 跳转至登录页或弹出登录对话框 |
| **代码影响** | `DialogType.SESSION_EXPIRED`；检测到时停止任务并通知用户重新登录 |
| **TODO** | 验证是否所有详情字段都需要登录，还是仅特定字段 |

---

## Easy Apply

| 项目 | 内容 |
|------|------|
| **规则** | "Easy Apply" 按钮仅在 LinkedIn 内置投递的职位上显示 |
| **来源** | `[官方]` |
| **UI 表现** | 详情页顶部显示 "Easy Apply" 或 "Apply" 按钮 |
| **代码影响** | `easy_apply` 字段（0/1）；本脚本仅记录，不触发投递 |
| **TODO** | 确认 resource-id（需真机 dump 验证） |

---

## 职位描述折叠

| 项目 | 内容 |
|------|------|
| **规则** | 长描述默认折叠，需要点击展开按钮 |
| **来源** | `[实测]` |
| **UI 表现** | 按钮文字为 "Show more" 或 "See more"（随 App 语言变化） |
| **代码影响** | `expand_description()` 方法；通过文本匹配点击 |
| **TODO** | 确认按钮 resource-id；验证中文界面的按钮文字 |

---

## App 速率控制（防账号封禁）

爬取脚本使用的保守参数（比 Boss 更慢）：

| 参数 | 值 | 说明 |
|------|---|------|
| 详情页停留时间 | ≥ 2.5s | 模拟人类阅读 |
| 每次滚动间隔 | 1.0s | Boss 为 0.5-0.8s |
| 关键词批次间隔 | 60s | 防止高频搜索 |
| 每批关键词前 | 随机 5-15s | 引入随机性 |

---

## 弹窗检测快速参考

| 弹窗类型 | 关键词（英文/中文）| 处理策略 |
|---------|----------------|---------|
| `SESSION_EXPIRED` | "Sign in" / "Log in" / "登录" | 致命，立即停止 |
| `RATE_LIMITED` | "You've reached the limit" / "too many requests" | 致命，当日停止 |
| `JOB_CLOSED` | "No longer accepting applications" / "职位已关闭" | 跳过 |
| `CAPTCHA` | "verify you're human" / "人机验证" | 致命，立即停止 |

---

## 页面导航规则

- **Jobs Tab**：底部第五个 Tab（中文界面从左: 主页 → 人脉 → 发布 → 通知 → 职位） `[实测]`
- **搜索入口**：HTTPS Deep Link `https://www.linkedin.com/jobs/search/?keywords={encoded}` `[实测]`
  - 需先执行 `pm set-app-links --package com.linkedin.android 2 all`（幂等，可重复）
  - **必须先 force-stop**：LinkedIn 已在前台时 VIEW intent 会被静默吞掉
- **返回列表**：BACK 键 1-2 次；但 Compose bottom sheet 动画导致回退不可靠 `[实测]`
  - 失败时需 force-stop → 重新打开搜索 URL 恢复

---

## 实测发现的技术陷阱（2026-09-09）

### adb uiautomator dump 缓存 bug `[实测/严重]`

LinkedIn 使用 Jetpack Compose SDUI，`adb shell uiautomator dump` 在 sheet 转场后返回**过时缓存 XML**。
即使 force-stop 重启 App，stale dump 仍然持续。

**解决方案**：改用 `uiautomator2.dump_hierarchy()` 通过 AccessibilityService 获取实时 tree。

### Compose 双层渲染 `[实测]`

Accessibility tree 同时包含前景 bottom sheet 和背景 search results list 的节点。
描述提取时需过滤掉 background card 的 content-desc 文本。

### LinkedIn "经典搜索淘汰"横幅 `[实测]`

自 2026 年 9 月起，搜索结果页顶部显示 "自 9 月起，我们将逐步淘汰'经典'职位搜索" 横幅。
该横幅文字作为 text node 可能被误选为职位描述，需通过 `ui_noise` 黑名单过滤。
Deep link 搜索暂时仍正常工作。

### 描述展开按钮 `[实测]`

- Compose AnnotatedString "更多" 链接不响应坐标 `input tap`
- 必须用 uiautomator2 的 accessibility `click()` 触发展开
- 展开后 text node 可达 3000-4000 字符

### 职位卡片识别模式 `[实测]`

搜索结果页卡片有两套并存的模式：
1. **关闭按钮**：`content-desc = "关闭{title}职位"` 的 X 按钮（右侧）
2. **Button 格式**：`content-desc = "{title}, 已验证, {company}, {location}, ..., Button"`

两者共存于同一 XML dump，代码优先使用 Button 格式解析（含更多结构化信息）。

---

## 账号权限差异（免费 vs Premium）

| 功能 | 免费账户 | Premium |
|------|---------|---------|
| 查看职位 | ✓ | ✓ |
| 查看申请人数 | 受限（仅显示"100+ applicants"） | 精确数字 |
| 查看薪资信息 | ✓（如有） | ✓ |
| InMail | 无 | 有 |

---

## 待研究（TODO）

- [x] LinkedIn Jobs Tab 的真机 UIAutomator resource-id 全集 → 已通过 HTTPS Deep Link 绕过，无需 Tab 导航
- [x] 职位卡片容器的 resource-id → 无 resource-id，使用 content-desc 模式匹配（"已验证" Button 格式）
- [x] 描述展开按钮的精确 resource-id → 无 resource-id（Compose AnnotatedString），通过 u2 accessibility click 展开
- [x] 搜索框 resource-id → 使用 HTTPS Deep Link 直接打开搜索结果，无需操作搜索框
- [ ] 每日浏览上限的精确值（实测确认）
- [ ] Session 过期的具体 UI 表现（弹窗 vs 整页跳转）
- [ ] "已申请"标识的 resource-id（防止重复爬取已申请职位）
- [ ] LinkedIn "经典搜索淘汰"后新 AI 搜索的导航方案（Deep Link 失效时的备选）
