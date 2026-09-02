#!/usr/bin/env python3
"""
一键连接并打开微信
"""
import sys
import time
sys.path.insert(0, '/home/averypi/Projects')

from pixelclaw import DeviceConnector

def main():
    print("="*60)
    print("PixelClaw - 连接设备并打开微信")
    print("="*60)

    # 创建设备连接器
    connector = DeviceConnector(
        config_path='/home/averypi/Projects/pixelclaw/config/devices.json'
    )

    # 检查ADB
    if not connector.adb.is_adb_available():
        print("❌ ADB不可用，请安装Android SDK")
        return 1
    print("✓ ADB可用")

    # 检查是否已连接
    if connector.adb.is_device_connected():
        print("✓ 设备已连接")
    else:
        print("\n尝试连接设备...")
        print(f"IP: {connector.get_device_ip()}:{connector.get_device_port()}")

        # 尝试连接
        if connector.connect():
            print("✓ 连接成功！")
        else:
            print("\n❌ 连接失败")
            print("\n请检查:")
            print("  1. Pixel 8a上已启用'开发者选项' → '无线调试'")
            print("  2. 点击'使用配对码配对设备'")
            print("  3. 确保显示的IP和端口与配置匹配")
            print("\n或者使用USB连接:")
            print("  1. 用USB线连接Pixel 8a到树莓派")
            print("  2. 在手机上允许USB调试")
            print("  3. 运行: adb devices")
            return 1

    # 测试基本功能
    print("\n测试设备功能...")

    # 截图测试
    screenshot = connector.adb.screenshot()
    if screenshot:
        print(f"✓ 截图成功 ({screenshot.size[0]}x{screenshot.size[1]})")
        screenshot.save('/home/averypi/Projects/pixelclaw/screenshot_test.png')
        print("  已保存 screenshot_test.png")
    else:
        print("⚠ 截图失败")

    # 尝试打开微信
    print("\n尝试打开微信...")

    # 方法1: 通过包名启动
    packages = [
        "com.tencent.mm",  # 微信
        "com.tencent.mm/.ui.LauncherUI",
    ]

    for pkg in packages:
        print(f"  尝试: {pkg}")
        success, output = connector.adb.shell(f"monkey -p {pkg.split('/')[0]} -c android.intent.category.LAUNCHER 1")
        if success:
            print("✓ 微信启动命令已发送")
            time.sleep(2)

            # 验证（截图）
            screenshot = connector.adb.screenshot()
            if screenshot:
                screenshot.save('/home/averypi/Projects/pixelclaw/screenshot_wechat.png')
                print("✓ 已保存微信界面截图 screenshot_wechat.png")
            return 0

    # 方法2: 通过am start
    success, output = connector.adb.shell("am start -n com.tencent.mm/.ui.LauncherUI")
    if success:
        print("✓ 微信启动成功")
        return 0
    else:
        print(f"⚠ 启动可能失败: {output}")
        print("\n请手动检查设备上是否已安装微信")
        return 0 if "Warning" in output else 1

if __name__ == "__main__":
    sys.exit(main())
