# 变更日志（Changelog）

所有重要变更均记录于此文件。

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，并遵循 [语义化版本号](https://semver.org/lang/zh-CN/) 规范。

## [Unreleased]

### Fixed（修复）
- `tests/` — `skills/android/` 抽取重构后 25 个测试失效，现全部修复（193 tests 全绿）。重构引入四处契约变化：①`tap()`/`swipe()`/`scroll_down()`/`press_back()` 不再调 `adb.tap()`/`adb.swipe()`，改为统一走 `adb.shell("shell input …")`，导致所有 `mock_adb.tap.assert_called_once()` 失败、`assert_not_called()` 变成空断言，且每次点击多消耗一次 `adb.shell` 调用把 `side_effect` 列表耗尽（`StopIteration`）；②`_parse_bounds` 从 `BOSSAutomationSkill` 静态方法移至模块级 `skills/android/ui_types.parse_bounds`；③`_logger` 名称改为类名（`BOSSAutomationSkill` / `XHSAutomationSkill`）而非模块路径；④`tap_element()` 未知 key 时返回 `False` 而非抛 `RuntimeError`，且不再打日志。修复手法：断言改为扫描 `adb.shell` 收到的 `"shell input tap X Y"` 命令串（新增 `_tap_calls()` / `_paged_shell()` 辅助函数），失败注入改用 `adb.shell.return_value = (False, "")`
- `tests/test_xhs_skill.py` — 6 个测试在 collection 阶段即报 `AttributeError: module 'skills.xhs.xhs_automation_skill' has no attribute 'subprocess'`：`XHSAutomationSkill` 改为继承 `AndroidSkill` 后已不再 import `subprocess`，fixture 中的 `patch("...subprocess.run")` 无目标可打。改为直接构造实例后赋值 `s.adb = MagicMock()`
- `skills/boss/boss_automation_skill.py` — `get_job_list()` 除 `title` 外所有字段恒为空：卡片容器定位错误（只向上爬 1 层落在 `cl_position`，该节点仅含标题；真正的字段容器是再上一层的 `view_job_card`），改为向上遍历至多 5 层直到命中 `view_job_card`。同时修正列表页 resource-id 映射——地点是 `tv_distance`（非详情页的 `tv_location`），HR 是合并字段 `tv_employer`（`姓名 · 职位`，按 `·` 拆分）。`JobInfo` 新增 `hr_title` 字段承接拆分结果。`hr_active` 在列表卡中不存在，保持为空，需进详情页获取。详见 `scenarios/boss/docs/atomic_operations.md` §4.1
- `skills/boss/boss_automation_skill.py` — 职位描述被「查看更多」截断：新增 `_find_description()` / `expand_description()`。`查看更多` 是 `tv_description` 内的 inline ClickableSpan（TextView 本身 `clickable=false`，XML 无独立节点），只能按坐标点击；且其 bounds 会被外层 `rv_list` RecyclerView 裁剪，需先小步滚动至 `desc.y2 < rv_list.y2 - 5` 后 bounds 方可信。实测描述 402 字 → 1032 字
- `scenarios/boss/scripts/scrape_job_details.py` — 展开结果被覆盖：滚动前的 `xml_top` dump 已含截断版 `description`，合并两次 dump 时 `if key not in detail` 逻辑会让截断版胜出，改为按长度取较长者
- `skills/boss/boss_automation_skill.py` — 同一职位被抓成两条：列表卡标题尾部带异步加载的角标占位符（字面量 `" &@ "`），同一张卡在不同 dump 中可能带也可能不带，按原文去重失效。新增模块级 `normalize_card_title()` 剥离尾部占位符（仅匹配结尾连续的 `[\s&@]`，标题中间的 `&` / `@` 如 `R&D @Home 产品经理` 不受影响）；`scroll_job_list()` 与爬取脚本统一用归一化标题作为身份键
- `skills/boss/boss_automation_skill.py` — 边缘卡片字段缺失：位于视口边缘的卡片被 RecyclerView 部分回收，只渲染出 `title`，`company` / `location` / `hr_*` 全为空。`scroll_job_list()` 改为在同一卡片再次出现时补齐首次为空的字段（已有值不覆盖）
- `scenarios/boss/scripts/scrape_job_details.py` — 同上边缘卡片问题：`_find_next_job()` 命中不完整卡片时改为小步滚动并**重新 dump**（滚动会让 `tap_x/tap_y` 失效，不能复用滚动前的对象），每张卡至多补 2 次后放行。实测 10 条职位 `list_info` 字段全部完整、无重复条目
- `scenarios/boss/scripts/scrape_job_details.py` — 详情页 `company` / `company_info` 缺失：公司信息块位于页面底部，描述越长被推得越远，固定滚动一次抓不到（实测 description ≥ 900 字的 3 条全部缺失，541 字的正常）。改为循环滚动至多 `_MAX_DETAIL_SCROLLS = 14` 次，抓到公司信息或页面不再变化（已到底）即停；两次 dump 的合并逻辑抽为模块级 `_merge_detail()`。每次滚动约 800px，实测 1032 字描述需 ~3 次、1729 字需 10 次以上，上限设 6 时仍会漏
- `scenarios/boss/scripts/scrape_job_details.py` — 爬取中途因 `browse_jobs` 单次失败提前终止（实测 10 条只抓到 6 条）：搜索浮层动画偶尔错过 5 秒等待窗口。`_return_to_job_list()` 改为最多重试 3 次，每次失败后回到「职位」Tab 再试

### Added（新增）
- `tests/test_boss_skill_enhanced.py`：新增 `TestNormalizeCardTitle`（6 个测试，覆盖尾部角标剥离、重复角标、省略号截断标题、标题中间 `&`/`@` 保留、空值）与 `TestScrollJobListDedupAndMerge`（4 个测试，覆盖角标变体去重、标题归一化存储、空字段补齐、已有值不被覆盖）
- `scenarios/boss/docs/atomic_operations.md`：新增 §4.1 列表卡层级与 resource-id 映射表、§6.2.1 展开「查看更多」折叠描述，含 6 个实测陷阱
- `skills/boss/boss_automation_skill.py`：新增 `ensure_ready()` 方法及两个私有辅助方法 `_is_screen_on()` / `_wake_screen()`。`ensure_ready()` 在每次迭代前依次检查：ADB 连通性 → 屏幕亮屏状态（熄屏则唤醒）→ App 前台状态（UNKNOWN 则重启）→ 残留弹窗清理（DAILY_LIMIT / LOGIN_REQUIRED 返回 False 停止任务，其余类型 dismiss 后继续）
- `scenarios/boss/tasks/boss_greet_task.py`：每次 job 迭代加 `try/except Exception` 防止单个职位异常崩溃全程；迭代开始调用 `ensure_ready()`（失败则 break）；修复 L126 `navigate_to_job()` 返回值被忽略的 bug
- `scenarios/boss/tasks/boss_apply_task.py`：同上防御性包裹；新增 `DialogType` 导入；result 新增 `"errors"` 键；`navigate_to_chat()` 成功后、`can_apply()` 前新增弹窗检测（DAILY_LIMIT/LOGIN_REQUIRED 停止任务，其余 dismiss）；修复 back-press fire-and-forget bug（现在捕获返回值并记录警告）；main() 输出新增系统错误汇总
- `tests/test_boss_skill_pages.py`：新增 `TestEnsureReady`（5 个测试），覆盖 ADB 断连 / UNKNOWN 页重启成功 / UNKNOWN 页重启失败 / 致命弹窗 / 可 dismiss 弹窗五条路径

- `docs/GETTING_STARTED.md`：新建端到端使用指南（中文），涵盖环境安装、Pixel 8a 设备连接（ADB 配对/连接/验证、Shizuku 可选配置）、无设备运行测试、Boss直聘两阶段工作流（boss_greet_task.py → 等待 HR 回复 → boss_apply_task.py）、XHS 场景脚本、视觉代理 API 示例及常见问题排查
- `docs/app_research_checklist.md`：新建通用 App 研究清单模板，规定探索新 App 前必须研究官方帮助中心、开放平台文档及社区资料，回答功能前提条件、速率限制、反自动化等核心问题
- `scenarios/boss/docs/boss_platform_rules.md`：新建 Boss直聘 平台规则文档，记录投递前提（双方互发消息）、每日沟通名额限制、弹窗触发关键词、两阶段工作流说明及待研究项
- `scenarios/boss/tasks/boss_apply_task.py`：新建第二阶段任务——切换到消息Tab，逐个检查HR回复，`can_apply()` 为 True 则调用 `apply_to_job()` 投递简历，输出已投递/跳过/失败统计
- `tests/test_boss_skill_pages.py`：新增 `test_navigate_to_chat_then_can_apply` 测试，验证 `btn_apply` 仅在进入聊天页后（HR 已回复）通过 `can_apply()` 可检测

### Changed（变更）
- `scenarios/boss/tasks/boss_greet_task.py`（原 `boss_job_search_task.py`）：重命名，移除无效的 `apply` 参数分支，模块 docstring 说明第一阶段用途及平台规则
- `scenarios/boss/docs/boss_automation_guide.md`：修正过时内容（删除 `--apply` 选项，修正 `apply_btn` 说明为"仅 HR 回复后出现"，新增平台规则链接）
- `skills/boss/boss_automation_skill.py`：修正 `apply_btn` ELEMENTS 注释（说明双向消息前提条件）；`can_apply()` / `apply_to_job()` docstring 补充平台规则和正确调用时机
- `PROJECTWIKI.md`：在 BOSSAutomationSkill 方法表后新增"两阶段工作流"说明；维护建议新增"新增 App 场景前必须研究官方文档"规范

### Added（新增）
- 新增 `tests/test_boss_skill_pages.py`（47 个新测试）：覆盖 7 个新 PageState（RECOMMEND/MESSAGES/PROFILE/FILTER_PANEL/RESUME/COMPANY_DETAIL/APPLICATIONS）、`navigate_to_tab`、`get_message_list`/`ChatEntry` 字段+坐标、`navigate_to_chat`、`set_filter`、`_TAB_TEXTS` 映射
- `skills/boss/boss_automation_skill.py`：新增 7 个 PageState 常量（RECOMMEND/MESSAGES/PROFILE/FILTER_PANEL/COMPANY_DETAIL/RESUME/APPLICATIONS）；新增 `ChatEntry` dataclass；新增 `navigate_to_tab()`/`get_message_list()`/`navigate_to_chat()`/`set_filter()` 方法；`get_current_page()` 检测链扩展至 13 种状态（优先级：dialog > filter_panel > chat > job_detail > resume > profile > messages > recommend > job_list > home > unknown）；ELEMENTS 新增 12 个 verified 条目（cl_tab_1-4/tv_tab_1-4/btn_confirm/btn_reset/contact_vp/recyclerView/boss_job_card_view/myGeekRoot/basic_info/rv_list）
- `config/app_knowledge/boss.json`：知识库从 18 条扩充至 55 条，新增底部导航栏 Tab（cl_tab_1-4/tv_tab_1-4）、消息 Tab（contact_vp/recyclerView/tv_name/tv_position/tv_msg/tv_time/et_input）、个人中心页（myGeekRoot/tv_my_resume/cl_post_resume_container/cl_interview_container/iv_general_settings）、简历页（basic_info/rv_list/sdv_avatar/tvPosition/tvSalary/tvCities/job_status）、筛选面板（btn_confirm/btn_reset/tv_option_name/ll_salary/img_close）、推荐 Tab（boss_job_card_view/tv_salary_statue/tv_employer/tv_active_status/tv_distance）；所有条目标注 verified/inferred/text-matched
- `PROJECTWIKI.md`：更新 PageState 常量表（新增7项）、新增 ChatEntry 说明、更新方法表（新增 navigate_to_tab/get_message_list/navigate_to_chat/set_filter）、页面状态检测 Mermaid 流程图扩展至 13 种状态、更新技术债务清单
- `scenarios/boss/scripts/dump_boss_ui.py`：UIAutomator XML 探索脚本，逐一 dump 11 个页面到 `tmp_dumps/`，生成含 221 unique resource-id 的 `resource_ids_report.txt`
- 新增 `tests/test_boss_skill_enhanced.py`（44 个新测试）：覆盖 `get_current_page`、`detect_dialog`、`dismiss_dialog`、`get_job_list` 坐标+富字段、`navigate_to_job`、`can_apply`/`apply_to_job`、`scroll_job_list`、`wait_for_element`、`send_greeting(verify=True)`、`verify_message_sent`
- `skills/boss/boss_automation_skill.py`：新增 `PageState` / `DialogType` 常量类；`JobInfo` 扩展 `company / salary / location / hr_name / hr_active / tap_x / tap_y` 字段；新增 `get_current_page`、`detect_dialog`、`dismiss_dialog`、`navigate_to_job`、`scroll_job_list`、`wait_for_element`、`verify_message_sent` 方法；`get_job_list` 重写为 parent-map XML 遍历（捕获坐标+兄弟字段）；`send_greeting` 支持 `verify` 参数；`apply_to_job` 前置 `can_apply` 检查
- `config/app_knowledge/boss.json`：知识库扩充 8→18 个元素，新增公司/薪资/地点/HR/弹窗相关条目
- `scenarios/boss/tasks/boss_job_search_task.py`：使用新 API 重写工作流（`navigate_to_job` 替换 `tap_element`、`scroll_job_list`、`detect_dialog`/`dismiss_dialog`、`wait_for_element`、`send_greeting(verify=True)`）
- `PROJECTWIKI.md`：补充 Boss直聘模块详细文档（`PageState`/`DialogType` 说明、`JobInfo` 字段表、关键方法表、页面检测状态机 Mermaid 图、弹窗检测机制表、导航 bug 修复 ADR）

### Fixed（修复）
- Boss直聘多职位导航死循环：`get_job_list()` 记录每个职位卡片的中心坐标 `tap_x/tap_y`，`navigate_to_job(job)` 使用坐标直接点击，彻底消除始终点第一个节点的 bug
- `apply_to_job()`：增加 `can_apply()` 前置检查，无 `btn_apply` 时立即返回 False，不再 tap 空节点
- `filter_jobs()`：实现文本匹配筛选（之前是 stub）

### Added（新增）
- 新增 `tests/` 单元测试目录（70 个测试，全部通过）：`test_som_annotator.py`（18 tests）、`test_reflection_strategy.py`（12 tests）、`test_som_strategy.py`（8 tests）、`test_app_knowledge.py`（13 tests）、`test_boss_skill.py`（18 tests + 1）
- 新增 `conftest.py`（项目根）和 `tests/conftest.py`：在 `sys.modules` 中预置 `cv2 / torch / torchvision / paddleocr` 等重型依赖的 stub，保证无 GPU 环境的 CI 能正常收集测试
- 新增 `pytest.ini`：配置 `testpaths = tests`、`--import-mode=importlib` 避免包级 `__init__.py` 冲突

### Fixed（修复）
- `monitors/adb_manager.py`：`type_text()` 改用 ADBKeyboard IME 广播（`am broadcast -a ADB_INPUT_TEXT`）支持中文/Unicode 输入；新增 `_type_text_unicode()` 私有方法，回退逻辑完整
- `scenarios/boss/tasks/boss_job_search_task.py` + `skills/boss/boss_automation_skill.py`：多职位导航修复——操作后调用新增 `press_back()` 回到列表，`is_on_job_list()` 验证当前页面
- `strategies/som_strategy.py`：`som_id` 不在 mapping 时改为保留原始 action 而非丢弃坐标；`int(som_id)` 转换失败时优雅降级（`ValueError/TypeError` → 日志警告 + 透传）；params 改为深拷贝避免污染历史记录
- `core/vision_agent.py`：引入 `_consecutive_failures` 计数器替换原有的 `_step_count < error_recovery_attempts` 比较；`action is None` 时正确递增失败计数并调用 `memory.finalize_memory_record()`；`som_enabled=True` 时将 `SoMAnnotator` 实例传入 `FallbackManager` 完成集成
- `strategies/fallback_manager.py`：新增 `som_annotator` 参数；`_init_strategies()` 的三处 `print()` 改为 `logger.warning()`；策略初始化后自动将 Step-1V 包裹为 `SOMStrategy`
- `strategies/reflection_strategy.py`：补充 `before_img / after_img` 为 `None` 时的前置检查；尺寸不同时用 `LANCZOS` resize 而非直接返回 `True`；新增异常阈值 `_ANOMALY_THRESHOLD = 0.95`，ratio > 0.95 视为崩溃/黑屏返回 `(False, "屏幕异常变化…")`
- `core/som_annotator.py`：`_load_font()` 尝试系统 TrueType 字体（Arial/DejaVu），失败 fallback 到 PIL 内置字体；`_parse_xml()` 基于 `(x1,y1,x2,y2)` 集合去重，过滤完全重叠的父子节点；`annotate()` 改用 `textbbox` 精确计算标签尺寸，标签框 clamp 到图像边界
- `core/app_knowledge.py`：路径穿越防护（`os.path.basename` + `re.sub(r'\.\.+', '')`）；`load()` 异常追加 app_name 和路径上下文；`save()` 改为 `copy.deepcopy(data)` 防止外部引用修改缓存
- `pixelclaw/__init__.py`：所有重型 import 包裹 `try/except ImportError: pass`，允许无 ML 依赖环境收集测试

### Added（新增）
- 新增 `core/som_annotator.py`：`SoMAnnotator` 解析 UIAutomator XML，在截图上叠加编号红框（SoM 标注），VLM 可通过 `som_id` 引用元素而非原始坐标
- 新增 `strategies/reflection_strategy.py`：`ReflectionStrategy` 通过像素 RMS 差比较前后截图，判断操作是否生效，反馈注入下一轮 prompt
- 新增 `strategies/som_strategy.py`：`SOMStrategy` 包装任意 `BaseStrategy`，将 VLM 返回的 `som_id` 解析为真实坐标
- 新增 `core/app_knowledge.py`：`AppKnowledge` 加载 AppAgent 格式 JSON 知识库，转换为 System Context prompt 字符串注入 VLM
- 新增 `config/app_knowledge/boss.json`：Boss直聘核心 UI 元素知识库（搜索框、职位列表、立即沟通、聊天输入、发送、筛选、投递）
- 新增 `skills/boss/`：`BOSSAutomationSkill` 基于 `ADBManager`（非 raw subprocess），实现 Boss直聘职位搜索、投递、打招呼、筛选等操作
- 新增 `scenarios/boss/tasks/boss_job_search_task.py`：Boss直聘完整求职工作流（启动 App → 搜索 → 浏览 → 打招呼/投递）
- 新增 `scenarios/boss/docs/boss_automation_guide.md`：Boss直聘场景使用文档

### Fixed（修复）
- `skills/boss/boss_automation_skill.py`：通过真机 ADB 验证，修正所有 `ELEMENTS` resource-id；`search_bar` → `et_search`，`job_name` → `tv_position_name`，`chat_input` → `editText_with_scrollbar`，`filter_btn` → `filterBarRightTabView`；发送按钮无 resource-id，`send_greeting()` 改为 KEYCODE_ENTER（keyevent 66）
- `config/app_knowledge/boss.json`：同步更新所有 `element_id` 为已验证值，补充发送按钮说明

### Changed（变更）
- `core/vision_agent.py`：新增 `som_enabled`、`reflection_enabled`、`app_knowledge` 参数；`execute_task()` 主循环集成 SoM 标注、知识库注入和 Reflection 反思步骤
- `config/settings.yaml`：新增 `agent:` 配置节（`som_enabled`、`reflection_enabled`、`reflection_diff_threshold`）和 `app_knowledge:` 配置节
- 整理项目根目录文件结构：新建 `tasks/` 目录，将 9 个任务脚本从根目录移入；将 3 个文档文件移入已有 `docs/` 目录
- 解耦通用自动化基础设施与小红书场景代码：重组 `skills/` 为按场景子目录（`skills/xhs/`），新增 `scenarios/xhs/`（含 tasks/scripts/docs 子目录），将 7 个 XHS 任务脚本、1 个工具脚本和 2 个 XHS 文档迁移至 `scenarios/xhs/`
- 修复 `XHSAutomationSkill.screenshot()` 硬编码路径，改为可配置的 `output_dir` 参数（默认 `/tmp/pixelclaw_output`）
- 重构 `MemoryManager._extract_goal_tags()`：提取 `DEFAULT_TAG_RULES` 常量，支持通过构造函数注入自定义规则，消除硬编码 App 关键字

<!-- 比对链接（将 <REPO_URL> 替换为实际仓库地址） -->
[Unreleased]: <REPO_URL>/compare/v0.1.0...HEAD
