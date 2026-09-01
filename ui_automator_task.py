#!/usr/bin/env python3
"""
使用 UI Automator 精确控制小红书完成任务
"""

import subprocess
import time
import os

SCREENSHOT_DIR = "/home/averypi/Projects/pixelclaw/xhs_screenshots"

def shell(cmd, check=True):
    """执行 shell 命令"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

def get_ui_hierarchy():
    """获取 UI 层次结构"""
    shell("adb shell uiautomator dump /sdcard/ui.xml")
    shell("adb pull /sdcard/ui.xml /tmp/ui.xml")
    with open("/tmp/ui.xml", "r") as f:
        return f.read()

def find_element_by_text(text):
    """通过文字查找元素的 bounds"""
    import re
    ui = get_ui_hierarchy()
    # 查找包含指定 text 的元素
    pattern = f'text="{text}"[^>]*bounds="\[([^\]]*)\]"'
    match = re.search(pattern, ui)
    if match:
        bounds = match.group(1)
        # 解析 bounds [x1,y1][x2,y2]
        coords = re.findall(r'\[(\d+),(\d+)\]', bounds)
        if len(coords) == 2:
            x1, y1 = int(coords[0][0]), int(coords[0][1])
            x2, y2 = int(coords[1][0]), int(coords[1][1])
            # 返回中心点
            return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None

def find_element_by_resource_id(resource_id):
    """通过 resource-id 查找元素的 bounds"""
    import re
    ui = get_ui_hierarchy()
    pattern = f'resource-id="{resource_id}"[^>]*bounds="\[([^\]]*)\]"'
    match = re.search(pattern, ui)
    if match:
        bounds = match.group(1)
        coords = re.findall(r'\[(\d+),(\d+)\]', bounds)
        if len(coords) == 2:
            x1, y1 = int(coords[0][0]), int(coords[0][1])
            x2, y2 = int(coords[1][0]), int(coords[1][1])
            return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None

def tap(x, y):
    """点击坐标"""
    shell(f"adb shell input tap {x} {y}")
    time.sleep(1)

def tap_element(coord):
    """点击元素坐标"""
    if coord:
        tap(coord[0], coord[1])
        return True
    return False

def screenshot(name):
    """截图"""
    path = f"{SCREENSHOT_DIR}/{name}"
    shell("adb shell screencap -p /sdcard/s.png")
    shell(f"adb pull /sdcard/s.png {path}")
    print(f"  ✓ {name}")
    return path

def input_text(text):
    """输入文字"""
    shell(f"adb shell am broadcast -a ADB_INPUT_TEXT --es msg '{text}'")
    time.sleep(0.5)

def main():
    print("=" * 60)
    print("使用 UI Automator 执行任务")
    print("=" * 60)

    # 1. 启动小红书
    print("\n[1] 启动小红书...")
    shell("adb shell am start -n com.xingin.xhs/.index.v2.IndexActivityV2")
    time.sleep(3)

    # 2. 使用 UI Automator 查找搜索按钮并点击
    print("\n[2] 查找搜索按钮 (resource-id)...")
    search_btn = find_element_by_resource_id("com.xingin.xhs:id/search")
    if search_btn:
        print(f"  找到搜索按钮: {search_btn}")
        tap_element(search_btn)
    else:
        print("  未找到搜索按钮，使用默认坐标")
        tap(1008, 208)
    time.sleep(2)

    # 3. 输入 openclaw
    print("\n[3] 输入 'openclaw'...")
    # 先点击搜索框
    search_box = find_element_by_resource_id("com.xingin.xhs:id/search_et")
    if search_box:
        tap_element(search_box)
    input_text("openclaw")
    time.sleep(1)

    # 4. 点击搜索
    print("\n[4] 点击搜索...")
    search_go = find_element_by_text("搜索")
    if search_go:
        tap_element(search_go)
    else:
        tap(900, 140)
    time.sleep(3)

    screenshot("search_result.png")

    print("\n" + "=" * 60)
    print("搜索完成！由于小红书搜索结果页面较复杂，")
    print("建议使用已收集的博主截图手动完成发帖。")
    print("=" * 60)

if __name__ == "__main__":
    main()
