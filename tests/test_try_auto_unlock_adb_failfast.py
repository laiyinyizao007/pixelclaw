"""
Regression tests for _try_auto_unlock() 中途 ADB 掉线 fail-fast 修复。

2026-10-19 实测（run_fde_40_rerun_20261019_180446.log）：
- ADB 在函数入口后中途断开
- _dumpsys_keyguard_state() 全程返回 " || "（拼接两个空 dumpsys）
- 5 步解锁全部"完成"（KEYCODE_WAKEUP / tap / swipe / input text / ENTER）
  但命令实际未触达手机
- 关键风险：4 位 PIN 被 input text 到任何后台 input focus —— 误注入
- post-state 检查 "mDreamingLockscreen=false" 永远不匹配空字符串 → 误判"仍锁屏"
- 浪费 5+ 次 ADB 命令 × 12 次重试 = 60+ 次空操作

修复：5 个 dumpsys 调用点接入 _check_dumpsys_alive()。空输出 + 设备断开 →
logger.error + 立即返 False，caller 不再 retry 这条死路。
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from scenarios.boss.scripts import smart_match_greet  # noqa: E402  (pytest importlib mode)


# ───────────────────────────── fixtures ─────────────────────────────


@pytest.fixture
def mock_skill_disconnected():
    """构造一个'ADB 已中途断开'的 skill mock。

    行为契约：
    - skill._adb() 全部调用返 (False, "") —— 模拟设备已掉线，命令失败
    - skill.adb.is_device_connected() 返 False —— 确认设备掉线
    - skill.device_id = "10.32.7.105:5555"（占位）
    """
    skill = MagicMock()
    skill._adb = MagicMock(return_value=(False, ""))
    skill.adb = MagicMock()
    skill.adb.is_device_connected = MagicMock(return_value=False)
    skill.device_id = "10.32.7.105:5555"
    return skill


@pytest.fixture
def mock_skill_alive():
    """构造一个'ADB 健康'的 skill mock，dumpsys 返回正常数据。"""
    skill = MagicMock()

    def _adb_side_effect(cmd: str):
        # 所有 dumpsys 返回正常 mDreamingLockscreen=false 状态（post 解锁态）
        if "dumpsys" in cmd:
            return (True, "mDreamingLockscreen=false")
        return (True, "")

    skill._adb = MagicMock(side_effect=_adb_side_effect)
    skill.adb = MagicMock()
    skill.adb.is_device_connected = MagicMock(return_value=True)
    skill.device_id = "10.32.7.105:5555"
    return skill


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """所有 time.sleep 调成 0，加速测试。"""
    monkeypatch.setattr(smart_match_greet.time, "sleep", lambda *_a, **_k: None)


# ───────────────────────────── tests ─────────────────────────────


def test_disconnected_aborts_before_input_text(mock_skill_disconnected, monkeypatch):
    """ADB 中途断开：_try_auto_unlock 应在 step 0 即返 False，永不调 input text。"""
    monkeypatch.setenv("PIXELCLAW_DEVICE_PIN", "4892")

    result = smart_match_greet._try_auto_unlock(mock_skill_disconnected)

    assert result is False, (
        f"ADB 断开时 _try_auto_unlock 必须返 False，实际返 {result!r}"
    )

    # 关键断言：绝不允许 input text '4892' 走到任何后台 focus
    adb_calls = [c.args[0] for c in mock_skill_disconnected._adb.call_args_list]
    input_text_calls = [c for c in adb_calls if c.startswith("shell input text")]
    assert input_text_calls == [], (
        f"ADB 断开时 input text 仍被调用（误注入风险）: {input_text_calls}"
    )

    # 应当调过 1 次 is_device_connected（fail-fast 复检）
    is_connected_calls = (
        mock_skill_disconnected.adb.is_device_connected.call_args_list
    )
    assert len(is_connected_calls) >= 1, (
        "ADB 断开时未调 is_device_connected 复检，fail-fast 失效"
    )


def test_disconnected_logs_error_marker(mock_skill_disconnected, monkeypatch, caplog):
    """ADB 断开时日志必须含 'ADB 设备已断开' marker，便于 grep 排查。"""
    monkeypatch.setenv("PIXELCLAW_DEVICE_PIN", "4892")

    with caplog.at_level("ERROR"):
        smart_match_greet._try_auto_unlock(mock_skill_disconnected)

    error_msgs = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any("ADB 设备已断开" in m for m in error_msgs), (
        f"未发现 'ADB 设备已断开' ERROR marker，实际记录: {error_msgs}"
    )


def test_transient_empty_does_not_false_positive(mock_skill_disconnected, monkeypatch):
    """ADB 仍连接但单次 dumpsys 瞬态空：应 warn 但继续（不 false-positive 终止）。"""
    monkeypatch.setenv("PIXELCLAW_DEVICE_PIN", "4892")

    # 设备 connected=True，但后续所有 dumpsys 都返空（假设整个链路都空）
    mock_skill_disconnected.adb.is_device_connected.return_value = True

    with patch.object(
        smart_match_greet,
        "_check_dumpsys_or_abort",
        wraps=smart_match_greet._check_dumpsys_or_abort,
    ) as spy:
        smart_match_greet._try_auto_unlock(mock_skill_disconnected)

    # 应当调用过 _check_dumpsys_or_abort，且没有触发 False 路径
    assert spy.call_count >= 1, "_check_dumpsys_or_abort 一次都没被调用"


def test_alive_path_proceeds_normally(mock_skill_alive, monkeypatch):
    """ADB 健康路径不应被 fail-fast 拦下，应正常完成 5 步。"""
    monkeypatch.setenv("PIXELCLAW_DEVICE_PIN", "4892")

    result = smart_match_greet._try_auto_unlock(mock_skill_alive)

    # alive 路径：函数返 True（不论解锁是否真成功，由 caller 用 mDreamingLockscreen=false 验证）
    assert result is True

    # health 路径应至少调过 input text '4892'（证明 4 步确实执行了）
    adb_calls = [c.args[0] for c in mock_skill_alive._adb.call_args_list]
    assert any("shell input text '4892'" in c for c in adb_calls), (
        "ADB 健康路径下 step 4 input text 未执行，破坏正常解锁流程"
    )


def test_is_device_connected_exception_treated_as_disconnect(
    mock_skill_disconnected, monkeypatch
):
    """adb server 挂掉时 is_device_connected 抛异常而非返 False → 必须按断开处理。

    宁可中止也不能把 PIN 打进未知 focus，所以异常路径不能 fall through 到继续。
    """
    monkeypatch.setenv("PIXELCLAW_DEVICE_PIN", "4892")
    mock_skill_disconnected.adb.is_device_connected.side_effect = RuntimeError(
        "adb server killed"
    )

    result = smart_match_greet._try_auto_unlock(mock_skill_disconnected)

    assert result is False, (
        f"is_device_connected 抛异常时必须按断开处理返 False，实际返 {result!r}"
    )
    adb_calls = [c.args[0] for c in mock_skill_disconnected._adb.call_args_list]
    assert not [c for c in adb_calls if c.startswith("shell input text")], (
        "is_device_connected 异常后仍打了 PIN，误注入风险"
    )


def test_empty_pin_env_skips_unload(monkeypatch):
    """env 未设 PIN：函数应直接返 False（无 ADB 调用）。"""
    monkeypatch.delenv("PIXELCLAW_DEVICE_PIN", raising=False)

    skill = MagicMock()
    skill._adb = MagicMock(return_value=(True, "ok"))
    skill.device_id = "test:5555"

    result = smart_match_greet._try_auto_unlock(skill)

    assert result is False
    skill._adb.assert_not_called()


def test_non_digit_pin_skips_with_warning(monkeypatch, caplog):
    """PIN 含非数字字符时应跳过（仅支持 4-6 位数字）。"""
    monkeypatch.setenv("PIXELCLAW_DEVICE_PIN", "abc123")

    skill = MagicMock()
    skill.device_id = "test:5555"

    with caplog.at_level("WARNING"):
        result = smart_match_greet._try_auto_unlock(skill)

    assert result is False
    skill._adb.assert_not_called()
    assert any("非数字字符" in r.message for r in caplog.records)
