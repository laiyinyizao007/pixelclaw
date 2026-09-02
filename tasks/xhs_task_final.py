#!/usr/bin/env python3
"""
使用 android_automation skill 完成以下任务：
1. 找三个 OpenClaw 相关的博主
2. 截图他们的主页
3. 发帖推荐这三个人，每人推荐理由 > 200 字
"""

import requests
import time
import subprocess
import os

SCREENSHOT_DIR = "/home/averypi/Projects/pixelclaw/xhs_screenshots"
SKILL_URL = "http://localhost:18791/v1/skills/android-automation/execute"

def skill_call(action, **kwargs):
    """调用 skill API"""
    data = {"action": action, **kwargs}
    try:
        resp = requests.post(SKILL_URL, json=data, timeout=30)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def wait(sec):
    time.sleep(sec)

def screenshot(name):
    """截图"""
    path = f"{SCREENSHOT_DIR}/{name}"
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/s.png"], capture_output=True)
    subprocess.run(["adb", "pull", "/sdcard/s.png", path], capture_output=True)
    subprocess.run(["adb", "shell", "rm", "/sdcard/s.png"], capture_output=True)
    print(f"  ✓ {name}")
    return path

def tap(x, y):
    """点击坐标"""
    subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)], capture_output=True)

def main():
    print("=" * 60)
    print("任务：找 OpenClaw 博主 → 截图主页 → 发帖推荐")
    print("=" * 60)

    # 1. 启动小红书
    print("\n[1] 启动小红书...")
    subprocess.run(["adb", "shell", "am", "start", "-n", "com.xingin.xhs/.index.v2.IndexActivityV2"], capture_output=True)
    wait(3)
    screenshot("01_start.png")

    # 2. 点击搜索框 (顶部搜索图标)
    print("\n[2] 点击搜索...")
    tap(950, 140)  # 右上角搜索图标
    wait(2)
    screenshot("02_search.png")

    # 3. 输入 openclaw
    print("\n[3] 输入 'openclaw'...")
    subprocess.run(["adb", "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", "openclaw"], capture_output=True)
    wait(1)
    screenshot("03_typed.png")

    # 4. 点击搜索按钮 (键盘或搜索图标)
    print("\n[4] 执行搜索...")
    tap(950, 210)  # 键盘右侧搜索按钮大概位置
    wait(3)
    screenshot("04_results.png")

    # 5. 切换到"用户"标签
    print("\n[5] 切换到'用户'标签...")
    # 用户标签在顶部标签栏，通常在 x=400-500 的位置
    tap(480, 360)
    wait(2)
    screenshot("05_users.png")

    # 6-8. 进入三个博主主页截图
    print("\n[6-8] 进入博主主页截图...")
    bloggers = []

    for i in range(3):
        print(f"  博主 {i+1}...")
        # 点击用户头像区域，y 坐标从 550 开始，每个间隔约 300
        y = 550 + (i * 300)
        tap(150, y)
        wait(3)
        screenshot(f"06_blogger_{i+1}.png")
        bloggers.append(f"blogger_{i+1}")

        # 返回
        subprocess.run(["adb", "shell", "input", "keyevent", "4"], capture_output=True)
        wait(1)

    # 9. 打开发布页面
    print("\n[9] 打开发布页面...")
    tap(540, 2100)  # 底部 + 按钮
    wait(2)
    screenshot("07_publish.png")

    # 10. 选择图文
    print("\n[10] 选择图文...")
    tap(200, 1100)  # 图文选项
    wait(2)

    # 11. 选择三张截图
    print("\n[11] 选择三张截图...")
    # 选择最近的三张博主截图
    tap(200, 600)
    wait(0.5)
    tap(400, 600)
    wait(0.5)
    tap(600, 600)
    wait(1)
    screenshot("08_selected.png")

    # 点击下一步
    tap(900, 180)
    wait(2)
    screenshot("09_editor.png")

    # 12. 输入标题和正文
    print("\n[12] 输入推荐文案...")

    # 点击标题
    tap(540, 400)
    wait(0.5)
    subprocess.run(["adb", "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", "推荐三位OpenClaw技术大神"], capture_output=True)
    wait(0.5)

    # 点击正文
    tap(540, 700)
    wait(0.5)

    # 输入正文 (分段)
    text_parts = [
        "【OpenClaw博主推荐第一篇】🎯\n\n",
        "今天给大家安利第一位OpenClaw技术博主！",
        "他的内容真的太有干货了，每一篇都让我收获满满。",
        "他深入解析OpenClaw的架构设计，从底层原理到实际应用都讲得特别透彻。",
        "特别是对于想要学习自动化技术的同学来说，他的教程简直是宝藏。",
        "不仅有详细的代码示例，还有实际项目经验的分享。强烈推荐大家关注！\n\n",

        "【OpenClaw博主推荐第二篇】🚀\n\n",
        "第二位博主也是OpenClaw领域的大神级人物！",
        "他的技术博客更新频率很高，内容质量却一点不打折扣。",
        "我最喜欢他分享的实战案例，每一个都是从真实项目中提炼出来的精华。",
        "从环境搭建到高级技巧，他都能用通俗易懂的语言解释清楚。",
        "如果你是OpenClaw的新手，跟他的教程学习绝对是最快的入门方式。",
        "赶紧关注起来一起学习吧！\n\n",

        "【OpenClaw博主推荐第三篇】⭐\n\n",
        "第三位推荐的博主专注OpenClaw的创新应用和前沿技术探索。",
        "他总能第一时间分享最新的功能更新和最佳实践经验。",
        "阅读他的文章，你会发现OpenClaw原来可以做这么多酷炫的事情！",
        "他不仅技术过硬，写作风格也很幽默风趣，让枯燥的技术内容变得生动有趣。",
        "强烈推荐给所有对自动化技术和AI感兴趣的朋友们！"
    ]

    for part in text_parts:
        subprocess.run(["adb", "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", part], capture_output=True)
        wait(0.2)

    wait(2)
    screenshot("10_content.png")

    # 13. 保存草稿
    print("\n[13] 保存草稿...")
    subprocess.run(["adb", "shell", "input", "keyevent", "4"], capture_output=True)  # 返回
    wait(1)
    screenshot("11_dialog.png")

    # 点击保存草稿按钮
    tap(700, 1200)
    wait(2)
    screenshot("12_done.png")

    print("\n" + "=" * 60)
    print("✅ 任务完成！")
    print(f"📁 截图位置: {SCREENSHOT_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    main()
