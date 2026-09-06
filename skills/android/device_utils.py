"""
Android 设备通用工具函数。

这些函数不依赖特定 App，可被 XHS / Boss 等各自的初始化脚本和测试脚本复用。
"""

import subprocess
import time
from typing import Callable, Optional, Tuple


def check_connected(device_id: str) -> bool:
    """
    检查 ADB 设备是否在线。

    Returns:
        True 表示设备已连接且可用。
    """
    r = subprocess.run(
        f"adb -s {device_id} get-state",
        shell=True,
        capture_output=True,
        text=True,
    )
    if r.stdout.strip() == "device":
        print(f"✅ 设备已连接: {device_id}")
        return True
    status = r.stdout.strip() or r.stderr.strip() or "无响应"
    print(f"❌ 设备未连接（{device_id}），状态: {status}")
    return False


def wake_unlock(
    adb,
    device_id: str,
    swipe: Tuple[int, int, int, int, int] = (540, 1800, 540, 800, 300),
) -> None:
    """
    唤屏并上滑解锁（适用于无 PIN / Pattern 的测试设备）。

    Args:
        adb:       ADB 接口对象（ADBRunner 或 ADBManager）。
        device_id: 设备序列号。
        swipe:     上滑参数 (x1, y1, x2, y2, duration_ms)。
    """
    adb.shell("shell input keyevent 224", device_id)   # KEYCODE_WAKEUP
    time.sleep(1.5)
    x1, y1, x2, y2, dur = swipe
    adb.shell(f"shell input swipe {x1} {y1} {x2} {y2} {dur}", device_id)
    time.sleep(2.0)


def go_to_home(
    adb,
    device_id: str,
    home_marker: str,
    get_xml: Callable[[], str],
    max_backs: int = 8,
    back_delay: float = 1.5,
) -> bool:
    """
    按 BACK 键直至首页特征出现，最多尝试 max_backs 次。

    Args:
        adb:         ADB 接口对象（ADBRunner 或 ADBManager）。
        device_id:   设备序列号。
        home_marker: 首页 XML 中的唯一特征字符串（如 resource-id 片段）。
        get_xml:     返回当前 UI XML 的无参可调用对象（通常是 skill.get_ui_hierarchy）。
        max_backs:   最大 BACK 次数。
        back_delay:  每次 BACK 后等待时间（秒）。
    Returns:
        True 表示已回到首页。
    """
    for i in range(max_backs):
        if home_marker in get_xml():
            return True
        print(f"  [back] 第 {i+1}/{max_backs} 次")
        adb.shell("shell input keyevent 4", device_id)
        time.sleep(back_delay)
    return home_marker in get_xml()
