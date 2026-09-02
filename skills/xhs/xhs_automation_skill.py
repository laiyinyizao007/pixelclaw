#!/usr/bin/env python3
"""
小红书自动化 Skill

提供基于 UI Automator 的精确坐标获取和自动化操作。

使用方法:
    from skills.xhs import XHSAutomationSkill

    xhs = XHSAutomationSkill()
    xhs.favorite_current_post()  # 收藏当前帖子
    xhs.like_current_post()      # 点赞当前帖子
"""

import subprocess
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass


@dataclass
class UIElement:
    """UI元素"""
    resource_id: str
    text: str
    content_desc: str
    bounds: Tuple[int, int, int, int]  # x1, y1, x2, y2

    @property
    def center(self) -> Tuple[int, int]:
        """计算中心点坐标"""
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @property
    def width(self) -> int:
        x1, _, x2, _ = self.bounds
        return x2 - x1

    @property
    def height(self) -> int:
        _, y1, _, y2 = self.bounds
        return y2 - y1


class XHSAutomationSkill:
    """
    小红书自动化 Skill

    核心方法:
    - 使用 UI Automator 获取精确坐标
    - 自适应不同屏幕分辨率
    - 支持动态UI布局
    """

    # 小红书常用按钮的 resource-id
    ELEMENTS = {
        'like': 'com.xingin.xhs:id/noteLikeLayout',
        'collect': 'com.xingin.xhs:id/noteCollectLayout',
        'comment': 'com.xingin.xhs:id/noteCommentLayout',
        'follow': 'com.xingin.xhs:id/followBtn',
        'back': 'com.xingin.xhs:id/backIV',
        'input_comment': 'com.xingin.xhs:id/inputCommentTV',
        'share': 'com.xingin.xhs:id/shareBtn',
        'more': 'com.xingin.xhs:id/moreOperateIV',
    }

    def __init__(self, device_id: Optional[str] = None, output_dir: Optional[str] = None):
        self.device_id = device_id
        self.output_dir = output_dir or "/tmp/pixelclaw_output"
        self.last_ui_dump = None

    def _adb_cmd(self, cmd: str) -> Tuple[str, str, int]:
        """执行ADB命令"""
        device_flag = f"-s {self.device_id} " if self.device_id else ""
        full_cmd = f"adb {device_flag}{cmd}"

        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode

    def get_ui_hierarchy(self, force_refresh: bool = True) -> str:
        """
        获取UI布局层次结构

        这是核心方法，使用 uiautomator 获取精确的UI元素位置

        Returns:
            UI布局XML字符串
        """
        if not force_refresh and self.last_ui_dump:
            return self.last_ui_dump

        # 获取UI布局
        self._adb_cmd("shell uiautomator dump /sdcard/window_dump.xml")

        # 拉取到本地
        stdout, _, code = self._adb_cmd("pull /sdcard/window_dump.xml /tmp/window_dump.xml")

        if code != 0:
            raise RuntimeError("获取UI布局失败")

        # 读取内容
        with open('/tmp/window_dump.xml', 'r', encoding='utf-8') as f:
            self.last_ui_dump = f.read()

        return self.last_ui_dump

    def find_element(self, resource_id: Optional[str] = None,
                     text: Optional[str] = None,
                     content_desc: Optional[str] = None) -> Optional[UIElement]:
        """
        查找UI元素

        Args:
            resource_id: 元素的resource-id
            text: 元素的文本内容
            content_desc: 元素的content-desc

        Returns:
            UIElement对象，包含精确坐标

        Example:
            >>> skill = XHSAutomationSkill()
            >>> elem = skill.find_element(resource_id='com.xingin.xhs:id/noteCollectLayout')
            >>> print(f"中心点: {elem.center}")
        """
        ui_xml = self.get_ui_hierarchy()

        # 构建正则表达式
        if resource_id:
            pattern = rf'<node[^>]*resource-id="{re.escape(resource_id)}"[^>]*>'
        elif text:
            pattern = rf'<node[^>]*text="{re.escape(text)}"[^>]*>'
        elif content_desc:
            pattern = rf'<node[^>]*content-desc="{re.escape(content_desc)}"[^>]*>'
        else:
            return None

        match = re.search(pattern, ui_xml)

        if not match:
            return None

        # 解析元素属性
        node_str = match.group(0)

        # 提取 bounds
        bounds_match = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node_str)
        if not bounds_match:
            return None

        x1, y1, x2, y2 = map(int, bounds_match.groups())

        # 提取其他属性
        text_match = re.search(r'text="([^"]*)"', node_str)
        desc_match = re.search(r'content-desc="([^"]*)"', node_str)
        id_match = re.search(r'resource-id="([^"]*)"', node_str)

        return UIElement(
            resource_id=id_match.group(1) if id_match else "",
            text=text_match.group(1) if text_match else "",
            content_desc=desc_match.group(1) if desc_match else "",
            bounds=(x1, y1, x2, y2)
        )

    def tap(self, x: int, y: int, duration: int = 0):
        """
        精确点击指定坐标

        Args:
            x: X坐标
            y: Y坐标
            duration: 长按持续时间（毫秒），0表示普通点击
        """
        if duration > 0:
            # 长按: swipe with zero distance
            self._adb_cmd(f"shell input swipe {x} {y} {x} {y} {duration}")
        else:
            self._adb_cmd(f"shell input tap {x} {y}")
        time.sleep(0.3)

    def tap_element(self, element_name: str):
        """
        点击指定名称的元素（自动查找坐标）

        Args:
            element_name: 元素名称，如 'like', 'collect', 'comment'
        """
        resource_id = self.ELEMENTS.get(element_name)
        if not resource_id:
            raise ValueError(f"未知元素: {element_name}")

        element = self.find_element(resource_id=resource_id)
        if not element:
            raise RuntimeError(f"未找到元素: {element_name}")

        center_x, center_y = element.center
        print(f"点击 {element_name}: ({center_x}, {center_y})")
        self.tap(center_x, center_y)

    def favorite_current_post(self) -> bool:
        """
        收藏当前帖子

        使用 UI Automator 获取精确坐标，一键收藏

        Returns:
            是否成功
        """
        print("=" * 50)
        print("小红书 - 收藏当前帖子")
        print("=" * 50)

        # 获取收藏按钮
        print("\n[1/3] 获取收藏按钮坐标...")
        collect_btn = self.find_element(
            resource_id=self.ELEMENTS['collect']
        )

        if not collect_btn:
            print("❌ 未找到收藏按钮，请确保在帖子详情页")
            return False

        center_x, center_y = collect_btn.center
        print(f"✓ 收藏按钮位置: ({center_x}, {center_y})")
        print(f"  区域: {collect_btn.bounds}")

        # 点击前截图
        print("\n[2/3] 截图（收藏前）...")
        before = self.screenshot("fav_before.png")

        # 精确点击
        print("\n[3/3] 点击收藏...")
        self.tap(center_x, center_y)
        time.sleep(0.5)

        # 点击后截图
        after = self.screenshot("fav_after.png")

        print("\n" + "=" * 50)
        print("✅ 收藏操作完成！")
        print(f"  截图: {before} → {after}")
        print("=" * 50)

        return True

    def like_current_post(self) -> bool:
        """点赞当前帖子"""
        print("小红书 - 点赞当前帖子")

        like_btn = self.find_element(resource_id=self.ELEMENTS['like'])
        if not like_btn:
            print("❌ 未找到点赞按钮")
            return False

        center_x, center_y = like_btn.center
        print(f"✓ 点赞按钮: ({center_x}, {center_y})")
        self.tap(center_x, center_y)
        print("✅ 已点赞")
        return True

    def scroll_down(self, start_y: int = 1500, end_y: int = 800):
        """向下滑动（加载更多）"""
        self._adb_cmd(f"shell input swipe 540 {start_y} 540 {end_y} 300")
        time.sleep(0.5)

    def go_back(self):
        """返回上一页"""
        # 优先使用UI查找返回按钮，否则使用keyevent
        back_btn = self.find_element(resource_id=self.ELEMENTS['back'])
        if back_btn:
            self.tap(*back_btn.center)
        else:
            self._adb_cmd("shell input keyevent 4")
        time.sleep(0.3)

    def screenshot(self, filename: str) -> str:
        """截图并保存"""
        local_path = f"{self.output_dir}/{filename}"
        self._adb_cmd(f"shell screencap -p /sdcard/{filename}")
        self._adb_cmd(f"pull /sdcard/{filename} {local_path}")
        return local_path

    def get_screen_size(self) -> Tuple[int, int]:
        """获取屏幕尺寸"""
        stdout, _, _ = self._adb_cmd("shell wm size")
        match = re.search(r'(\d+)x(\d+)', stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1080, 2400


# CLI 接口
if __name__ == "__main__":
    import sys

    skill = XHSAutomationSkill()

    if len(sys.argv) < 2:
        print("小红书自动化 Skill")
        print("\n用法:")
        print(f"  python3 {sys.argv[0]} fav    # 收藏当前帖子")
        print(f"  python3 {sys.argv[0]} like   # 点赞当前帖子")
        print(f"  python3 {sys.argv[0]} back   # 返回")
        sys.exit(1)

    action = sys.argv[1]

    if action == "fav":
        skill.favorite_current_post()
    elif action == "like":
        skill.like_current_post()
    elif action == "back":
        skill.go_back()
    else:
        print(f"未知操作: {action}")
