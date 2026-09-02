#!/usr/bin/env python3
"""
小红书自动收藏脚本 - 精确版
使用方法: python3 xhs_auto_fav.py

正确的收藏方法：
1. 使用 uiautomator 获取 UI 布局
2. 解析 bounds 计算中心点
3. 精确点击坐标
"""

import subprocess
import re
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))


def run(cmd):
    """执行shell命令"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def get_ui_hierarchy():
    """获取UI布局并解析收藏按钮位置

    正确方法：使用 uiautomator dump 获取精确坐标
    """
    # 获取UI布局
    run("adb shell uiautomator dump /sdcard/ui.xml")
    run("adb pull /sdcard/ui.xml /tmp/ui.xml")

    with open('/tmp/ui.xml', 'r') as f:
        content = f.read()

    # 查找收藏按钮
    # 小红书收藏按钮特征: resource-id="com.xingin.xhs:id/noteCollectLayout"
    pattern = r'noteCollectLayout.*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    match = re.search(pattern, content)

    if match:
        x1, y1, x2, y2 = map(int, match.groups())
        # 计算中心点
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        return (center_x, center_y, x1, y1, x2, y2)

    return None


def tap(x, y):
    """精确点击指定坐标"""
    subprocess.run(f"adb shell input tap {x} {y}", shell=True)
    time.sleep(0.5)


def screenshot(filename):
    """截图保存"""
    subprocess.run(f"adb shell screencap -p /sdcard/{filename}", shell=True)
    output_path = f"/home/averypi/Projects/pixelclaw/{filename}"
    subprocess.run(f"adb pull /sdcard/{filename} {output_path}", shell=True)
    return output_path


def favorite_current_post():
    """收藏当前帖子"""
    print("="*60)
    print("小红书自动收藏")
    print("="*60)

    # 步骤1: 获取精确坐标
    print("\n[1/4] 获取收藏按钮精确坐标...")
    pos = get_ui_hierarchy()

    if not pos:
        print("❌ 未找到收藏按钮，请确保在小红书帖子详情页")
        return False

    center_x, center_y, x1, y1, x2, y2 = pos
    print(f"✓ 找到收藏按钮")
    print(f"  区域: [{x1},{y1}][{x2},{y2}]")
    print(f"  中心点: ({center_x}, {center_y})")

    # 步骤2: 截图（收藏前）
    print("\n[2/4] 截图（收藏前）...")
    before_path = screenshot("fav_before.png")
    print(f"✓ 已保存: fav_before.png")

    # 步骤3: 精确点击收藏按钮
    print("\n[3/4] 点击收藏按钮...")
    tap(center_x, center_y)
    print(f"✓ 已点击: ({center_x}, {center_y})")
    time.sleep(1)

    # 步骤4: 截图（收藏后）
    print("\n[4/4] 截图（收藏后）...")
    after_path = screenshot("fav_after.png")
    print(f"✓ 已保存: fav_after.png")

    print("\n" + "="*60)
    print("✅ 收藏完成！")
    print(f"对比截图:")
    print(f"  收藏前: {before_path}")
    print(f"  收藏后: {after_path}")
    print("="*60)

    return True


def main():
    """主函数"""
    # 检查ADB连接
    print("检查设备连接...")
    out, err, code = run("adb devices")
    if "device" not in out or out.count("\n") < 2:
        print("❌ 设备未连接，请先连接手机")
        return 1

    print("✓ 设备已连接")

    # 执行收藏
    success = favorite_current_post()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
