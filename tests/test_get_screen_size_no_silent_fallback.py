"""
Regression tests for ADBRunner.get_screen_size() / AndroidSkill.get_screen_size()。

2026-10-19 排查 smart_match_greet PIN 注入时发现同模式反模式：_, out = skill._adb(...)
丢弃 success bool。本次把同样的反模式扩展到 get_screen_size：

- skills/android/adb_runner.py:146 get_screen_size() 返回 Tuple[int, int]，
  ADB 断或 wm size 解析失败时静默 fallback 到 (1080, 2400)
- skills/android/android_skill.py:229 get_screen_size() 同样

后果：ADB 断时所有 tap/swipe 用 fallback 坐标，UI 元素命中全错（callers:
core/action_executor.py:219,239 / scenarios/xhs/scripts/init_xhs.py:146）。

修复（PROJECTWIKI §5「项目级代码约定」2026-10-20 起生效）：
- ADB 断 / wm size 解析失败 → return None（不再是 silent fallback）
- 返回类型改为 Optional[Tuple[int, int]]，与 monitors/adb_manager.py:505 保持一致
- callers 必须显式处理 None（action_executor.py 已正确处理；xhs/init_xhs.py 需补）
"""
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ───────────────────────────── ADBRunner.get_screen_size ─────────────────────────────


class TestADBRunnerGetScreenSize:
    """skills/android/adb_runner.py:146"""

    def _runner(self, shell_return):
        """构造 ADBRunner，shell() 行为由 shell_return 控制。"""
        from skills.android.adb_runner import ADBRunner
        r = ADBRunner()
        r.shell = MagicMock(return_value=shell_return)
        return r

    def test_disconnected_returns_none_not_fallback(self):
        """ADB 断开（success=False）：必须返 None，不是 (1080, 2400)。"""
        runner = self._runner((False, ""))

        size = runner.get_screen_size("test:5555")

        assert size is None, (
            f"ADB 断开时必须返 None，实际返 {size!r}（fallback 静默错误）"
        )

    def test_empty_stdout_returns_none(self):
        """ADB 连但 wm size 返空（异常状态）：返 None 而非 fallback。"""
        runner = self._runner((True, ""))

        size = runner.get_screen_size("test:5555")

        assert size is None

    def test_unparseable_stdout_returns_none(self):
        """ADB 返非 wm size 格式（如错误信息）：返 None。"""
        runner = self._runner((True, "error: device not found"))

        size = runner.get_screen_size("test:5555")

        assert size is None

    def test_normal_physical_size(self):
        """正常 'Physical size: 1080x2400'：正确解析。"""
        runner = self._runner((True, "Physical size: 1080x2400"))

        size = runner.get_screen_size("test:5555")

        assert size == (1080, 2400)

    def test_alternate_format_without_label(self):
        """'Override size: 720x1280' 也能匹配 regex。"""
        runner = self._runner((True, "Override size: 720x1280"))

        size = runner.get_screen_size("test:5555")

        assert size == (720, 1280)


# ───────────────────────────── AndroidSkill.get_screen_size ─────────────────────────────


class TestAndroidSkillGetScreenSize:
    """skills/android/android_skill.py:229"""

    def _skill(self, adb_return):
        """构造 AndroidSkill，_adb() 行为由 adb_return 控制。"""
        from skills.android.android_skill import AndroidSkill
        skill = AndroidSkill.__new__(AndroidSkill)
        skill._adb = MagicMock(return_value=adb_return)
        return skill

    def test_disconnected_returns_none(self):
        """ADB 断开：必须返 None。"""
        skill = self._skill((False, ""))

        size = skill.get_screen_size()

        assert size is None, (
            f"ADB 断开时 AndroidSkill.get_screen_size 必须返 None，实际 {size!r}"
        )

    def test_normal_physical_size(self):
        skill = self._skill((True, "Physical size: 1440x3120"))

        size = skill.get_screen_size()

        assert size == (1440, 3120)

    def test_unparseable_returns_none(self):
        skill = self._skill((True, "garbage output"))

        size = skill.get_screen_size()

        assert size is None


# ───────────────────────────── callers 兼容 ─────────────────────────────


class TestInitXhsHandlesOptional:
    """scenarios/xhs/scripts/init_xhs.py — 改 None 后必须先 guard 再 tuple-unpack。"""

    def test_init_xhs_script_no_direct_tuple_unpack(self):
        """回归保护：`w, h = xhs.get_screen_size()` 必须改写成 size = ... + None check。"""
        from pathlib import Path

        path = (
            Path(__file__).parents[1]
            / "scenarios"
            / "xhs"
            / "scripts"
            / "init_xhs.py"
        )
        src = path.read_text()

        # 1. 不能直接 `w, h = xhs.get_screen_size()`（会 TypeError）
        assert "w, h = xhs.get_screen_size()" not in src, (
            "init_xhs.py: 仍然在直接 tuple-unpack get_screen_size()，ADB 断时崩溃"
        )

        # 2. 必须有 `size = xhs.get_screen_size()` + None 守卫
        assert "size = xhs.get_screen_size()" in src, (
            "init_xhs.py: 应改写为 `size = xhs.get_screen_size()` 再 None check"
        )
        # None guard 紧邻 size 行
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "size = xhs.get_screen_size()" in line:
                window = "\n".join(lines[i: i + 4])
                assert "if not size" in window or "is None" in window, (
                    f"init_xhs.py: size 赋值后 4 行内必须 None 守卫（早退）\n当前窗口:\n{window}"
                )
                break
