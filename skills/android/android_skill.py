"""
AndroidSkill — Android 自动化 Skill 基类。

封装所有 App 共用的 ADB 操作、UI 层级解析、元素查找与设备配置加载。
XHSAutomationSkill 和 BOSSAutomationSkill 均继承此类，只保留各自的
App 特定方法和 ELEMENTS 映射表。
"""

import json
import logging
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
import re

from typing import Any, Dict, Optional, Tuple

from skills.android.adb_runner import ADBRunner
from skills.android.ui_types import UIElement, parse_bounds


class AndroidSkill:
    """
    Android 自动化 Skill 基类。

    Args:
        device_id:  ADB 设备序列号；None 表示使用默认设备。
        adb:        注入的 ADB 接口对象（ADBRunner 或 ADBManager）；
                    None 时自动创建 ADBRunner 实例。
        output_dir: 截图等文件的本地存储目录；None 时使用系统临时目录。
    """

    ELEMENTS: Dict[str, str] = {}

    def __init__(
        self,
        device_id: Optional[str] = None,
        adb=None,
        output_dir: Optional[str] = None,
    ):
        self.device_id = device_id
        self.adb = adb if adb is not None else ADBRunner()
        self.output_dir = output_dir or str(
            Path(tempfile.gettempdir()) / "pixelclaw_output"
        )
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self.last_ui_dump: Optional[str] = None
        self._logger = logging.getLogger(type(self).__name__)

    # ── 内部 ADB 包装 ─────────────────────────────────────────────────────────

    def _adb(self, cmd: str) -> Tuple[bool, str]:
        """调用 ``self.adb.shell(cmd, device_id)``，返回 ``(success, stdout)``。"""
        return self.adb.shell(cmd, self.device_id)

    # ── UI 层级 ───────────────────────────────────────────────────────────────

    def get_ui_hierarchy(self, force_refresh: bool = False) -> str:
        """
        通过 UIAutomator 获取当前界面 XML。

        Args:
            force_refresh: True 时忽略缓存，强制重新 dump。
        Returns:
            UI 布局 XML 字符串；失败时返回空字符串。
        """
        if not force_refresh and self.last_ui_dump:
            return self.last_ui_dump
        self._adb("shell uiautomator dump /sdcard/window_dump.xml")
        ok, content = self._adb("shell cat /sdcard/window_dump.xml")
        self.last_ui_dump = content if ok else ""
        return self.last_ui_dump

    def find_element(
        self,
        resource_id: Optional[str] = None,
        text: Optional[str] = None,
        content_desc: Optional[str] = None,
        xml: Optional[str] = None,
    ) -> Optional[UIElement]:
        """
        在 UI 层级中查找元素（ET 解析，比正则更健壮）。

        Args:
            resource_id:  resource-id 子串匹配。
            text:         文本完全匹配。
            content_desc: content-desc 完全匹配。
            xml:          已获取的 XML 字符串；None 时自动获取。
        Returns:
            首个匹配的 UIElement；未找到时返回 None。
        """
        if xml is None:
            xml = self.get_ui_hierarchy()
        if not xml:
            return None
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return None

        for node in root.iter("node"):
            attrs = node.attrib
            if resource_id and resource_id not in attrs.get("resource-id", ""):
                continue
            if text and text != attrs.get("text", ""):
                continue
            if content_desc and content_desc != attrs.get("content-desc", ""):
                continue
            bounds = parse_bounds(attrs.get("bounds", ""))
            return UIElement(
                resource_id=attrs.get("resource-id", ""),
                text=attrs.get("text", ""),
                content_desc=attrs.get("content-desc", ""),
                bounds=bounds or (),
                clickable=attrs.get("clickable") == "true",
            )
        return None

    # ── 点击与输入 ────────────────────────────────────────────────────────────

    def tap(self, x: int, y: int, duration: int = 0) -> bool:
        """
        点击坐标。

        Args:
            x, y:     设备坐标（原始分辨率）。
            duration: 长按时长（毫秒），0 表示普通点击。
        """
        if duration > 0:
            ok, _ = self._adb(f"shell input swipe {x} {y} {x} {y} {duration}")
        else:
            ok, _ = self._adb(f"shell input tap {x} {y}")
        time.sleep(0.3)
        return ok

    def tap_element(self, element_key: str, xml: Optional[str] = None) -> bool:
        """
        通过 ELEMENTS 映射表点击命名元素。

        Args:
            element_key: ELEMENTS 中定义的键（如 "search_bar"）。
            xml:         已获取的 XML；None 时自动获取。
        Returns:
            True 表示点击动作已发送，False 表示元素未找到。
        """
        rid = self.ELEMENTS.get(element_key)
        if not rid:
            return False
        elem = self.find_element(resource_id=rid, xml=xml)
        if not elem or not elem.center:
            return False
        return self.tap(*elem.center)

    def type_text(self, text: str) -> bool:
        return self.adb.type_text(text, self.device_id)

    def press_back(self) -> bool:
        """发送 KEYCODE_BACK（keycode 4）。"""
        ok, _ = self._adb("shell input keyevent 4")
        return ok

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: int = 300,
    ) -> bool:
        ok, _ = self._adb(f"shell input swipe {x1} {y1} {x2} {y2} {duration}")
        return ok

    def scroll_down(self, start_y: int = 1500, end_y: int = 800) -> None:
        self._adb(f"shell input swipe 540 {start_y} 540 {end_y} 300")
        time.sleep(0.5)

    # ── 截图 ──────────────────────────────────────────────────────────────────

    def screenshot(self, filename: str) -> str:
        """截图并保存到 output_dir，返回本地路径；失败时返回空字符串。"""
        local_path = str(Path(self.output_dir) / filename)
        ok1, _ = self._adb(f"shell screencap -p /sdcard/{filename}")
        if not ok1:
            self._logger.warning("screencap 失败: %s", filename)
            return ""
        ok2, _ = self._adb(f"pull /sdcard/{filename} {local_path}")
        if not ok2:
            self._logger.warning("pull 失败: %s → %s", filename, local_path)
            return ""
        return local_path

    # ── 设备信息 ──────────────────────────────────────────────────────────────

    def get_screen_size(self) -> Tuple[int, int]:
        """返回 (width, height)；失败时回退到 (1080, 2400)。"""
        _, out = self._adb("shell wm size")
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 1080, 2400

    # ── 等待 ──────────────────────────────────────────────────────────────────

    def wait_for_element(
        self,
        resource_id: str,
        timeout: float = 5.0,
        interval: float = 0.5,
    ) -> Optional[UIElement]:
        """
        轮询直到指定 resource-id 的元素出现。

        Returns:
            找到时返回 UIElement，超时返回 None。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            xml = self.get_ui_hierarchy(force_refresh=True)
            elem = self.find_element(resource_id=resource_id, xml=xml)
            if elem:
                return elem
            time.sleep(interval)
        return None

    # ── 设备坐标配置 ──────────────────────────────────────────────────────────

    def _load_device_config(self) -> Dict[str, Any]:
        """加载 config/devices/<device_id>.json；文件不存在时返回空字典。"""
        if not self.device_id:
            return {}
        cfg = Path(__file__).parents[2] / "config" / "devices" / f"{self.device_id}.json"
        if not cfg.exists():
            return {}
        try:
            return json.loads(cfg.read_text(encoding="utf-8"))
        except Exception as e:
            self._logger.warning("读取设备配置失败 (%s): %s", cfg, e)
            return {}

    def get_canvas_coords(
        self,
        app: str,
        section: str,
    ) -> Dict[str, Tuple[int, int]]:
        """
        从设备配置文件中读取 Canvas 坐标段。

        Returns:
            ``{label: (x, y)}`` 字典；无数据时返回空字典。
        """
        raw = self._load_device_config().get(app, {}).get(section, {})
        return {k: tuple(v) for k, v in raw.items()}

    # ── XML 工具 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _build_parent_map(root: ET.Element) -> Dict[ET.Element, ET.Element]:
        """构建整棵元素树的 child → parent 映射（用于向上遍历父容器）。"""
        return {child: parent for parent in root.iter() for child in parent}
