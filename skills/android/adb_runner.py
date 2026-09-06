"""
轻量级 ADB 子进程包装器。

接口与 monitors.ADBManager 兼容（shell / tap / swipe / keyevent / type_text /
screenshot / is_device_connected / get_screen_size），额外提供 ADBManager 没有的
pull 方法（CanvasProbe 等工具需要）。
"""

import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image


class ADBRunner:
    """
    通过子进程调用 adb 命令行工具。

    所有方法的接口与 monitors.ADBManager 对齐，两者可互换注入 AndroidSkill。
    """

    # ── 底层接口 ────────────────────────────────────────────────────────────────

    def shell(self, cmd: str, device_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        运行 ``adb [-s device_id] {cmd}``，返回 (success, stdout)。

        注意：``cmd`` 是完整的 adb 参数（如 ``"shell input tap 100 200"``），
        不含 ``adb`` 前缀。

        使用 shell=False + 参数列表，避免 MSYS/Git Bash 对 /sdcard 等路径
        做 Windows 路径转换导致设备端路径错误。
        """
        args = ["adb"]
        if device_id:
            args += ["-s", device_id]
        args += shlex.split(cmd, posix=True)
        r = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode == 0, (r.stdout or "").strip()

    # ── 高层便捷方法（与 ADBManager 接口一致）──────────────────────────────────

    def tap(self, x: int, y: int, device_id: Optional[str] = None) -> bool:
        ok, _ = self.shell(f"shell input tap {x} {y}", device_id)
        return ok

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: int = 300,
        device_id: Optional[str] = None,
    ) -> bool:
        ok, _ = self.shell(
            f"shell input swipe {x1} {y1} {x2} {y2} {duration}", device_id
        )
        return ok

    def keyevent(self, keycode: int, device_id: Optional[str] = None) -> bool:
        ok, _ = self.shell(f"shell input keyevent {keycode}", device_id)
        return ok

    _ADBKEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"

    def type_text(self, text: str, device_id: Optional[str] = None) -> bool:
        # `adb shell input text` only handles ASCII.
        # For non-ASCII (e.g. Chinese) switch to ADBKeyboard and send via broadcast.
        if text.isascii():
            ok, _ = self.shell(f"shell input text {text}", device_id)
            return ok
        # Save current IME, switch to ADBKeyboard, broadcast text, restore IME.
        _, prev_ime = self.shell("shell settings get secure default_input_method", device_id)
        self.shell(f"shell ime enable {self._ADBKEYBOARD_IME}", device_id)
        self.shell(f"shell ime set {self._ADBKEYBOARD_IME}", device_id)
        import time; time.sleep(0.3)
        args = ["adb"]
        if device_id:
            args += ["-s", device_id]
        args += ["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text]
        r = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        ok = r.returncode == 0
        # Restore original IME.
        if prev_ime:
            self.shell(f"shell ime set {prev_ime.strip()}", device_id)
        return ok

    def screenshot(self, device_id: Optional[str] = None) -> Optional[Image.Image]:
        """截图并返回 PIL Image（RGB）；失败时返回 None。"""
        tmp = Path(tempfile.gettempdir()) / f"adb_screenshot_{device_id or 'default'}.png"
        ok_cap, _ = self.shell("shell screencap -p /sdcard/_screenshot.png", device_id)
        if not ok_cap:
            return None
        ok = self.pull("/sdcard/_screenshot.png", str(tmp), device_id)
        if not ok:
            return None
        return Image.open(tmp).convert("RGB")

    def get_screen_size(self, device_id: Optional[str] = None) -> Tuple[int, int]:
        _, out = self.shell("shell wm size", device_id)
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 1080, 2400

    def is_device_connected(self, device_id: Optional[str] = None) -> bool:
        if device_id:
            ok, out = self.shell("get-state", device_id)
            return ok and out.strip() == "device"
        ok, out = self.shell("devices", None)
        return ok and any("\tdevice" in ln for ln in out.splitlines())

    # ── ADBManager 没有但工具层需要的扩展方法 ─────────────────────────────────

    def pull(
        self,
        remote: str,
        local: str,
        device_id: Optional[str] = None,
    ) -> bool:
        """将设备上的 remote 文件拉取到本地 local 路径。"""
        ok, _ = self.shell(f"pull {remote} {local}", device_id)
        return ok
