# 变更日志（Changelog）

所有重要变更均记录于此文件。

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，并遵循 [语义化版本号](https://semver.org/lang/zh-CN/) 规范。

## [Unreleased]

### Fixed（修复）
- `skills/boss/boss_automation_skill.py` — 搜索提交后卡死的两种形态：①结果页停留在「网络异常，请点击按钮刷新」占位页，职位列表节点永远不出现，调用方的 `wait_for_element` 只能白等到超时（实测两个关键词连续失败，`AI产品经理` 报"搜索结果页加载超时"、`FDE` 报"搜索失败"），手动点「重新加载」后 4 条职位立即加载；②回车未能提交，页面停在搜索建议浮层（ASCII 关键词走 `input text` 时尤其常见，实测 `FDE` 必现），浮层右下角另有 `tv_search`「搜索」按钮，点击后立即出结果。新增 `_submit_search()`：提交后轮询职位列表节点，未出现则查找并点击恢复按钮，最多 3 轮；`_find_recovery_button()` 在单次 dump 内依次匹配 `重新加载` / `点击重试` / `重试`，再回落到 `tv_search`。`ELEMENTS` 新增 `search_submit` 条目。`browse_jobs()` 中两处重复的「清空输入 → 输入 → 回车」代码合并到该方法
- `skills/boss/boss_automation_skill.py` — `browse_jobs()` 静默假成功：形态②下 `_submit_search()` 找不到重试按钮时原先 `return True`「交由调用方判断」，导致 `browse_jobs('FDE')` 返回 True 而 `get_job_list()` 返回 0 条，错误被掩盖到下游才暴露。改为 `return False`
- `scenarios/boss/scripts/scrape_job_details.py` — `_return_to_job_list()` 重试时被搁浅在无底栏页面：搜索浮层与「求职期望」编辑页都没有 `ly_menu` 和底部导航，`navigate_to_tab("jobs")` 在这两页是空操作，导致 `search overlay did not appear` → `search_bar and search icon both not found` ×2 → 整个关键词终止。每轮重试的 `navigate_to_tab` 前先 `press_back()` 退出无导航页面
- `scenarios/boss/scripts/dump_boss_ui.py` + `scenarios/boss/tasks/boss_greet_task.py` — 搜索关键词硬编码：dump 脚本写死 `input text Python`，打招呼任务的 `--keyword` 默认值写死 `"Python 工程师"`。两处均改为 `--keyword` 必填参数（与 `scrape_job_details.py` 对齐）。dump 脚本原先只能输入 ASCII——`shell()` 内部按空格 `split()`，多词和中文关键词都会被截断，故新增模块级 `type_text()`：ASCII 走 `input text`（空格转 `%s`），非 ASCII 切换 ADBKeyboard IME 后经 `am broadcast -a ADB_INPUT_TEXT` 发送并还原原 IME，参数以 argv 列表直传 `subprocess.run` 绕开 `split()`
- `scenarios/boss/scripts/dump_boss_ui.py` — 设备 serial 硬编码 `DEFAULT_DEVICE = "42231JEKB04971"`：改为 `--device` 可选，省略时由新增的 `resolve_device()` 从 `adb devices` 自动选取；无设备或多设备时报错退出
- `tests/` — `skills/android/` 抽取重构后 25 个测试失效，现全部修复（193 tests 全绿）。重构引入四处契约变化：①`tap()`/`swipe()`/`scroll_down()`/`press_back()` 不再调 `adb.tap()`/`adb.swipe()`，改为统一走 `adb.shell("shell input …")`，导致所有 `mock_adb.tap.assert_called_once()` 失败、`assert_not_called()` 变成空断言，且每次点击多消耗一次 `adb.shell` 调用把 `side_effect` 列表耗尽（`StopIteration`）；②`_parse_bounds` 从 `BOSSAutomationSkill` 静态方法移至模块级 `skills/android/ui_types.parse_bounds`；③`_logger` 名称改为类名（`BOSSAutomationSkill` / `XHSAutomationSkill`）而非模块路径；④`tap_element()` 未知 key 时返回 `False` 而非抛 `RuntimeError`，且不再打日志。修复手法：断言改为扫描 `adb.shell` 收到的 `"shell input tap X Y"` 命令串（新增 `_tap_calls()` / `_paged_shell()` 辅助函数），失败注入改用 `adb.shell.return_value = (False, "")`
- `tests/test_xhs_skill.py` — 6 个测试在 collection 阶段即报 `AttributeError: module 'skills.xhs.xhs_automation_skill' has no attribute 'subprocess'`：`XHSAutomationSkill` 改为继承 `AndroidSkill` 后已不再 import `subprocess`，fixture 中的 `patch("...subprocess.run")` 无目标可打。改为直接构造实例后赋值 `s.adb = MagicMock()`
- `skills/boss/boss_automation_skill.py` — `get_job_list()` 除 `title` 外所有字段恒为空：卡片容器定位错误（只向上爬 1 层落在 `cl_position`，该节点仅含标题；真正的字段容器是再上一层的 `view_job_card`），改为向上遍历至多 5 层直到命中 `view_job_card`。同时修正列表页 resource-id 映射——地点是 `tv_distance`（非详情页的 `tv_location`），HR 是合并字段 `tv_employer`（`姓名 · 职位`，按 `·` 拆分）。`JobInfo` 新增 `hr_title` 字段承接拆分结果。`hr_active` 在列表卡中不存在，保持为空，需进详情页获取。详见 `scenarios/boss/docs/atomic_operations.md` §4.1
- `skills/boss/boss_automation_skill.py` — 职位描述被「查看更多」截断：新增 `_find_description()` / `expand_description()`。`查看更多` 是 `tv_description` 内的 inline ClickableSpan（TextView 本身 `clickable=false`，XML 无独立节点），只能按坐标点击；且其 bounds 会被外层 `rv_list` RecyclerView 裁剪，需先小步滚动至 `desc.y2 < rv_list.y2 - 5` 后 bounds 方可信。实测描述 402 字 → 1032 字
- `scenarios/boss/scripts/scrape_job_details.py` — 展开结果被覆盖：滚动前的 `xml_top` dump 已含截断版 `description`，合并两次 dump 时 `if key not in detail` 逻辑会让截断版胜出，改为按长度取较长者
- `skills/boss/boss_automation_skill.py` — 同一职位被抓成两条：列表卡标题尾部带异步加载的角标占位符（字面量 `" &@ "`），同一张卡在不同 dump 中可能带也可能不带，按原文去重失效。新增模块级 `normalize_card_title()` 剥离尾部占位符（仅匹配结尾连续的 `[\s&@]`，标题中间的 `&` / `@` 如 `R&D @Home 产品经理` 不受影响）；`scroll_job_list()` 与爬取脚本统一用归一化标题作为身份键
- `skills/boss/boss_automation_skill.py` — 边缘卡片字段缺失：位于视口边缘的卡片被 RecyclerView 部分回收，只渲染出 `title`，`company` / `location` / `hr_*` 全为空。`scroll_job_list()` 改为在同一卡片再次出现时补齐首次为空的字段（已有值不覆盖）
- `scenarios/boss/scripts/scrape_job_details.py` — 同上边缘卡片问题：`_find_next_job()` 命中不完整卡片时改为小步滚动并**重新 dump**（滚动会让 `tap_x/tap_y` 失效，不能复用滚动前的对象），每张卡至多补 2 次后放行。实测 10 条职位 `list_info` 字段全部完整、无重复条目
- `scenarios/boss/scripts/scrape_job_details.py` — 详情页 `company` / `company_info` / `location` 缺失：这三个字段都来自页面底部的公司信息块，固定滚动一次抓不到（实测 description ≥ 900 字的条目全部缺失，541 字的正常）。改为循环滚动至多 `_MAX_DETAIL_SCROLLS` 次，抓到公司信息即停；两次 dump 的合并逻辑抽为模块级 `_merge_detail()`
- `scenarios/boss/scripts/scrape_job_details.py` — 上条循环仍会漏（实测 1749 / 985 字两条描述滚到底也抓不到）：真正原因不是滚动次数不够，而是退出条件 `xml_bot == prev_xml` 误判。公司信息块由 `rv_list` RecyclerView 懒加载，inflate 之前页面滚不动，**连续 3 轮 dump 字节完全相同**，第 4 轮才突然从 13880 → 22315 字符长出整块内容（`tv_com_name` / `tv_com_info` / `tv_location` / `iv_map` / `bl_location_view` 同时出现，节点数 31 → 53；`tv_description` bounds 由 `[53,0][1027,2155]` 移到 `[53,0][1027,1095]` 证明确实在滚动）。单次「XML 未变」被当成「已到底」，循环在第 2 轮就退出。改为连续 `_MAX_STALE_DUMPS = 4` 轮不变才退出，每轮不变时额外 `sleep(1.0)` 给懒加载留时间；循环抽为模块级 `_scroll_for_company_info()` 便于测试。真机验证：两个关键词共 20 条职位，此前必然失败的长描述条目（985 / 1214 / 1744 / 1749 / 1810 / 2221 字）全部抓到 `company` / `company_info`
- `scenarios/boss/scripts/scrape_job_details.py` — `_MAX_DETAIL_SCROLLS` 14 → 30：上一条修复后 `_MAX_STALE_DUMPS` 才是真正的到底判据，滚动次数上限退化为防死循环的安全网，原先按「实测只需 4 轮」设定的 14 太紧。实测 2406 字的描述（FDE「AI Native Engineer（FDE 培养方向）」）滚穿 14 次仍未到底，日志报「滚动到底仍未抓到公司信息」；其 `raw_texts` 中无 `网络异常` 字样，可据此与详情页网络占位页区分
- `tests/test_scrape_job_details.py` — 新建（9 个测试）：`TestScrollForCompanyInfo` 覆盖抓到即停、连续 3 轮不变后第 4 轮命中、达到 stale 上限放弃、空 dump 退出、`_MAX_DETAIL_SCROLLS` 天花板、stale 计数在页面变化后重置；`TestMergeDetail` 覆盖保留较长描述、`raw_texts` 去重追加、已有标量不被覆盖。脚本 import 时会把 `sys.stdout` 包成 `TextIOWrapper`（Windows 中文输出所需），该包装器被回收时会关闭底层 buffer 进而破坏 pytest 捕获流，故导入时以 `patch.object(sys, "platform", "linux")` 屏蔽该分支
- `scenarios/boss/scripts/scrape_job_details.py` — 爬取中途因 `browse_jobs` 单次失败提前终止（实测 10 条只抓到 6 条）：搜索浮层动画偶尔错过 5 秒等待窗口。`_return_to_job_list()` 改为最多重试 3 次，每次失败后回到「职位」Tab 再试
- `skills/boss/boss_automation_skill.py` + `scenarios/boss/scripts/scrape_job_details.py` — 详情页「网络异常」占位页导致整条职位只剩 `title`：实测 `AI产品经理` 第 1 条（沐瞳科技）与第 3 条（北京蓝标传媒）的 `raw_texts` 逐字节相同——`['面议', '上海', '在校/应届', '本科', '网络异常，请检查网络后重试', '重试']`，描述与公司信息块整块缺失，但脚本仍记为成功（`errors` 为空）。原因是占位页照常渲染 `tv_job_name` 与顶部 chips，`wait_for_element("tv_job_name")` 判不出异常。新增 `recover_detail_page()`：以「dump 中不再含 `网络异常`」为成功判据（而非锚点节点存在），失败则点击页面上的「重试」按钮，最多 3 轮；按钮定位直接复用既有的 `_find_recovery_button()`（其匹配列表已含 `重试`，无需改动）。脚本在首次 dump 后检测到 `网络异常` 即调用恢复，成功则重新 dump 再提取，失败则记入 `report["errors"]` 而非静默通过

### Added（新增）
- `scenarios/boss/scripts/analyze_requirements.py` — 新建职位需求分析引擎：读取爬取 JSON → 调用 Claude Haiku 提取精细需求标签（贴近原文，10-20 字/条）→ 存入 SQLite（`requirements.db`，含 jobs + requirements 两表）→ 聚合频次/薪资/公司质量 → 综合评分（频率40%+薪资30%+公司质量30%）→ 输出分类 Markdown 报告。支持增量处理（已入库 job 默认跳过）与 `--force` 重置。`.claude/skills/analyze-boss-jobs.md` 新增对应 skill 入口
- `scenarios/boss/scripts/scrape_job_details.py` — `_return_to_job_list()` 新增 Back 键快速通道：先按 Back 并检测列表是否出现（4s），成功则跳过重搜（节省 ~7s/条）；失败才回退为原有 navigate_to_tab + browse_jobs 流程。实测 100 条职位减少约 10–12 分钟耗时
- `scenarios/boss/scripts/scrape_job_details.py` — 新增 `_ai_recover()`：`except Exception` 块捕获到未处理异常时自动调用 `claude-haiku-4-5-20251001`，传入当前 UIAutomator XML（截断至 4000 字符）+ 错误信息，执行返回的 `back`/`tap`/`skip` 恢复动作；缺少 `ANTHROPIC_API_KEY` 时优雅降级为跳过该职位；正常爬取 0 token 消耗
- `scenarios/boss/scripts/scrape_job_details.py` + `scenarios/boss/docs/boss_automation_guide.md` — 跨次去重：`_load_seen_keys()` 在启动时扫描同关键词历史 JSON，以 `(normalize_card_title(title), company, hr_name)` 三元组为 key 填充 `visited_titles`；`_job_key()` 辅助函数生成 tab 分隔 key（任意空字段自动省略）。跨次重启后已抓职位自动跳过，不重复写入输出
- `scenarios/boss/config/keywords.yaml` + `scenarios/boss/scripts/scrape_job_details.py` — 关键词改为配置文件管理，支持多关键词批量爬取：`main()` 改为按关键词循环，每个关键词单独调用 `scrape()` 并输出独立 JSON（`job_details_{关键词}_{时间戳}.json`），结束后打印每关键词明细 + 合计汇总，任一关键词有错误则退出码为 1。新增 `load_config()` 读取 YAML（`keywords` 列表 + `defaults.n_jobs`）与 `--config` 参数；`--keyword` 由必填改为可选覆盖项（指定时忽略配置文件只跑该词），`--n-jobs` 默认改为 `None` 以便回落到 `defaults.n_jobs`
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
