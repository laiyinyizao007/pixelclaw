# Plan: P1 fail-fast — `_try_auto_unlock` 内部 dumpsys 空时早检 adb devices

**Task #**: 1
**创建日期**: 2026-10-20
**Phase**: P2 → 待审批进 P3
**根因诊断证据**:
- `scenarios/boss/output/run_fde_40_rerun_20261019_180446.log`（5 步全跑但 dumpsys 全空，最终 "手机锁屏未解"）
- `scenarios/boss/scripts/smart_match_greet.py:1326-1398`（`_try_auto_unlock` 函数体）
- 当前 `adb devices` 返回空（根因仍在）

> 注：本文件为 Task #1 单任务方案。根目录 `plan.md` 是另一未结案任务（MiniMax 官方 API 切换），互不影响。

---

## 1. Objective（目标）

在 `_try_auto_unlock()` 内部，任一 dumpsys 状态查询返回空时立即用 `skill.adb.is_device_connected()` 复检，设备断开则提前中止，避免：

1. 把 4 位 PIN `input text` 错误投递到后台 input focus（真实风险）
2. 后续 KEYCODE_ENTER 等无效命令继续空跑
3. 调用方走 12 次重试 × 6 步 = 72 次空 ADB 命令

不改变成功路径行为，不改变 5 步解锁序列本身。

## 2. Scope（范围）

### In-Scope
- 修改 `scenarios/boss/scripts/smart_match_greet.py:1326-1398`
- 新增 1 个辅助函数 `_check_dumpsys_alive`（~25 行，含注释）
- 修改 4 处 dumpsys 调用点的失败处理（line 1370/1374/1378/1394 + line 1383 step 3 dumpsys window）
- 新增 1 个 pytest 测试 `tests/test_try_auto_unlock_adb_failfast.py`
- 更新 `PROJECTWIKI.md` §5 解锁流程小节
- 更新 `CHANGELOG.md`（Keep a Changelog Fixed 分区）
- 1 个原子 commit（代码 + 文档同 SHA）

### Out-of-Scope（明确不做）
- 不修 ADB 断开的根本原因（路由器 DHCP 续期 / 无线睡眠）——那是另一个 P1
- 不改 5 步解锁序列本身（Pixel tap + fast swipe + text + ENTER）
- 不改 12 次重试循环（line 1462-1478）和 60s 手动等待回退（line 1505-1509）
- 不动其他平台（智联 / 猎聘）——它们的 `smart_match_greet.py` 不含 `_try_auto_unlock`，无需同步

## 3. Standards Compliance（CLAUDE.md 遵循）

| 规则 | 本计划如何遵守 |
|---|---|
| **G1 文档一等公民** | 代码 commit 与 PROJECTWIKI + CHANGELOG 同原子提交；引用 SHA |
| **G3 意图授权** | 用户已说"定方案"+"好"，隐式授权 P3 写入；本计划待你审批 |
| **G4 一致性** | 接口签名维持 `Tuple[bool, str]` 不变；纯内部行为升级 |
| **G6 既有架构** | 复用 `skill._adb()` 和 `skill.adb.is_device_connected()` 现有 API（line 1438 已有先例） |
| **G8 双轮代码审查** | P3 后跑 codex-code-reviewer，期望 ≥45/50 |
| **G11 智能路由** | tdd-workflow（先测试）+ systematic-debugging（根因已记录）+ codex-code-reviewer |
| **G14 副作用隔离区** | 仅改 unlock 函数 + 加测试 + 文档；无外部依赖/接口/配置变更；不需登记 |

## 4. Current Code Analysis（问题定位）

### 已有的好基础
- **P0 入口检查**（line 1432-1445）：`skill.adb.is_device_connected()` 在 `_run_loop` 头部已挡住「进入即断开」的设备，但**ADB 中途掉线漏检**。
- **`_dumpsys_keyguard_state`**（line 1326-1337）：返回 `" || "` 拼接两段 dumpsys。但**只用 `out`，丢弃 success bool**——这是核心漏洞。
- **caller 已有错误出口**（line 1456-1460, 1506-1507）：`_try_auto_unlock` 返回 False 时 caller 直接报错退出；只要函数能在 ADB 断开时早返 False 即可生效。

### 5 个 dumpsys 调用点
| 位置 | 代码 | 当前问题 |
|---|---|---|
| L1370 | `logger.info("...step 0] ..", _dumpsys_keyguard_state(skill))` | dumpsys 空时不感知 |
| L1374 | `logger.info("...step 1] ..", _dumpsys_keyguard_state(skill))` | dumpsys 空时不感知 |
| L1378 | `logger.info("...step 2] ..", _dumpsys_keyguard_state(skill))` | dumpsys 空时不感知 |
| L1383 | `_, cur_focus_raw = skill._adb(...)` | dumpsys window 空时直接拼，不检测 |
| L1394-1396 | `post_state = _dumpsys_keyguard_state(...); is_unlocked = "mDreamingLockscreen=false" in post_state` | 空字符串永远不匹配 → 误判"仍锁屏" |

**共 5 个点需处理**（其中 L1383 已弃 `_dumpsys_keyguard_state` 包装层，直接调 ADB；需统一处理）。

## 5. Implementation Plan（实施方法）

### 5.1 方法选择（已收敛）

**选 A: 函数内 fail-fast（推荐）**——每个 dumpsys 调用点后立即检查，空 → 复检 `adb devices` → 断则中止。
- 优：5 个点统一处理；中途掉线立即触发；success bool 本来就返回
- 缺：每步 ~50ms 检查开销（5 步共 ~250ms，可接受）

**弃 B: 仅改 `_dumpsys_keyguard_state` 内部加 bool 检查**——caller 都要改，复杂。
**弃 C: P0 入口检查加到循环首**——只解决"进入即断开"，不解决中途掉线。

### 5.2 新增辅助函数

```python
def _check_dumpsys_alive(skill, step_label: str, raw_output: str) -> bool:
    """若 ADB 已断，logger.error 并返回 False。caller 据此中止。

    raw_output: dumpsys 调用的 std 输出（可能为空）。
    判定策略：
      1. raw_output 非空（正常情况）→ 视为 alive，返回 True
      2. raw_output 为空 + skill.adb.is_device_connected()=False → 中途掉线，
         logger.error，附 step_label 和 adb devices 输出，返回 False
      3. raw_output 为空 + 设备仍 connected → 罕见瞬态空（grep 极端情况下偶发），
         logger.warning，继续（返回 True，不 false-positive 中断成功路径）
    """
    if raw_output.strip():
        return True
    if not skill.adb.is_device_connected(skill.device_id):
        logger.error(
            "❌ [step %s] dumpsys 返回空 + ADB 设备已断开，中止自动解锁",
            step_label,
        )
        _, devs = skill._adb("devices")  # 诊断用
        logger.error("    当前 adb devices: %s", devs.strip() or "<empty>")
        return False
    logger.warning(
        "⚠️  [step %s] dumpsys 返回空但 ADB 仍连接（疑似瞬态），继续",
        step_label,
    )
    return True
```

### 5.3 5 个调用点改造

**L1370 (step 0)**:
```python
out_before = _dumpsys_keyguard_state(skill)
if not _check_dumpsys_alive(skill, "0", out_before.split(" || ")[0]):
    return False
logger.info("    [step 0] 解锁前状态: %s", out_before)
```

**L1374 (step 1) / L1378 (step 2)**: 同 5.3 模式，step_label 改为 "1" / "2"。

**L1383 (step 3 dumpsys window)**:
```python
_, cur_focus_raw = skill._adb("shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -2")
if not _check_dumpsys_alive(skill, "3", cur_focus_raw):
    return False
cur_focus = cur_focus_raw.strip().replace("\n", " | ")
logger.info("    [step 3] swipe 完成, 当前焦点: %s", cur_focus)
```

**L1394-1396 (step 5 post-state)**:
```python
post_state = _dumpsys_keyguard_state(skill)
if not _check_dumpsys_alive(skill, "5", post_state.split(" || ")[0]):
    return False
is_unlocked = "mDreamingLockscreen=false" in post_state or "mDreamingLockscreen=Doze" in post_state
```

> 注：现有 `_try_auto_unlock` 返回 `True`（line 1398）— 改为「成功解锁返 True，ADB 断开返 False」二元语义；caller 现有 False 处理路径已就绪（line 1456-1460, 1506-1507）。

## 6. Task Breakdown（bite-sized，2-5 min/项）

按 CLAUDE.md「每个任务 2-5 分钟」原则：

| # | 任务 | 估时 | 依赖 | 验收 |
|---|---|---|---|---|
| 6.1 | 写测试 `tests/test_try_auto_unlock_adb_failfast.py`：mock `_adb` 返回空 + `is_device_connected` 返回 False，确认 `_try_auto_unlock` 在 step 0 即返 False 且不调 `input text` | 4 min | — | pytest -x fail |
| 6.2 | 加 `_check_dumpsys_alive` 辅助函数在 `_dumpsys_keyguard_state` 之后 | 3 min | — | 静态导入通过 |
| 6.3 | 改 L1370（step 0）调用点接入 | 2 min | 6.2 | 测试 6.1 通过 |
| 6.4 | 改 L1374（step 1）调用点接入 | 2 min | 6.2 | 测试通过 |
| 6.5 | 改 L1378（step 2）调用点接入 | 2 min | 6.2 | 测试通过 |
| 6.6 | 改 L1383（step 3 dumpsys window）调用点接入 | 2 min | 6.2 | 测试通过 |
| 6.7 | 改 L1394（step 5 post-state）调用点接入 | 2 min | 6.2 | 测试通过 |
| 6.8 | 跑全测试 + 静态检查 | 2 min | 6.3-6.7 | pytest pass + 无 lint |
| 6.9 | 跑 codex-code-reviewer（G8 双轮第一轮） | 5 min | 6.8 | 评分 ≥45/50 |
| 6.10 | 修 codex 高严重项 + 跑二轮 | 5 min | 6.9 | ≥45/50 |
| 6.11 | 更新 `PROJECTWIKI.md` §5 解锁流程小节 | 5 min | 6.10 | 引用 SHA |
| 6.12 | 更新 `CHANGELOG.md`（Fixed 分区）+ commit（ref #1） | 3 min | 6.11 | atomic commit |
| 6.13 | git push | 1 min | 6.12 | origin/main 同步 |

**总估时**: ~38 min

## 7. Risk Management（风险管理）

| 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|
| Pixel 瞬态 dumpsys 空（grep pipe race）误触发 abort | 中 | 低 | `_check_dumpsys_alive` 第 3 段：connected==True 时 warn 但不 return False |
| 加 5 处 `is_device_connected` 检查拖慢 unlock（每次 ~50ms） | 低 | 高 | 全程共 ~250ms，远小于现状 60s 等待 |
| 12 次重试循环（caller）每轮都触发新 fail-fast | 中 | 中 | 预期行为——每轮重试都重新走 5 步 + fail-fast，比现状 60s/次快得多 |
| 其他平台（zhilian/liepin）也有同样问题没同步修 | 低 | 低 | Out-of-Scope；如后期发现再开新 P1 |
| 函数返回语义从「尝试过」变「尝试过 + ADB 状态」可能导致 caller 误解 | 低 | 低 | caller 现有 False 处理 = 进 retry；新增的 ADB 断开也走同一路径，行为一致 |

## 8. Acceptance Criteria（验收）

- [ ] **6.1 → 6.8 全绿**：新测试通过 + 现有测试不退步
- [ ] **G8 双轮审查 ≥45/50**：无高严重项
- [ ] **人工 ADB 掉线演练**（可选）：跑场景前 `adb disconnect`，确认 step 0/1 之内 logger.error 出现且 `input text` 未发
- [ ] **失败日志形态**：`run_*.log` 中 `❌ [step X] dumpsys 返回空 + ADB 设备已断开` 关键字可 grep
- [ ] **PROJECTWIKI.md §5** 加 fail-fast 小节 + 引用 commit SHA
- [ ] **CHANGELOG.md Fixed 分区** 加本次条目 + 引用 PROJECTWIKI 章节锚
- [ ] **`git log --oneline -1`** 显示 `(ref #1)` 后缀
- [ ] **`adb devices`** 当前仍空（不阻塞审批；P3 执行前需 `adb connect` 验证回归）

## 9. Backout（回滚）

单个原子 commit，`git revert <SHA>` 即可。P0 入口检查（line 1432-1445）保留作为 fallback。无状态 / 数据变更。

## 10. Plan Quality Check

| 维度 | 标准 | 本计划 |
|---|---|---|
| Extendable | ≥2 扩展方向 | ✓ 后续可移植 zhilian/liepin；可在 step 0 之外加 adb-on-error retry |
| Detailed | 覆盖所有影响 | ✓ 5 个 dumpsys 点全部识别 |
| Robust | 错误场景 + 回滚 | ✓ section 7 + section 9 |
| Accurate | 接口与代码一致 | ✓ `Tuple[bool, str]` 签名、API 名称均核对 |

## 11. Review History

- **2026-10-20 P2 起草**: 主模型基于既有代码 + skill 文档 + 4 条 memory 反馈起草
- **未跑 multi-model 验证**: 当前环境无 `mcp__zen__*` 工具，plan-down 完整流程受限；G8 双轮 codex 审查补足（P3 阶段执行）

## 12. Revision Log

- 2026-10-20 v1: 初版（Task #1，P2 待审批）
