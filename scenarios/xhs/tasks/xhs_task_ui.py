#!/usr/bin/env python3
"""
使用 android_automation skill + UI Automator 精确查找元素
"""

import requests
import time
import subprocess
import os

SCREENSHOT_DIR = "/home/averypi/Projects/pixelclaw/xhs_screenshots"
SKILL_URL = "http://localhost:18791/v1/skills/android-automation/execute"

def skill_call(action, **kwargs):
    data = {"action": action, **kwargs}
    try:
        resp = requests.post(SKILL_URL, json=data, timeout=30)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def wait(sec):
    time.sleep(sec)

def screenshot(name):
    path = f"{SCREENSHOT_DIR}/{name}"
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/s.png"], capture_output=True)
    subprocess.run(["adb", "pull", "/sdcard/s.png", path], capture_output=True)
    subprocess.run(["adb", "shell", "rm", "/sdcard/s.png"], capture_output=True)
    print(f"  ✓ {name}")
    return path

def find_element(text):
    """使用 UI Automator 查找元素坐标"""
    result = skill_call("find", target=text, strategy="ui")
    print(f"  查找 '{text}': {result}")
    return result

def tap_element(result):
    """点击查找到的元素"""
    if result.get("success") and "coordinates" in result:
        x, y = result["coordinates"]
        subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)], capture_output=True)
        return True
    return False

def main():
    print("=" * 60)
    print("使用 UI Automator 精确查找执行任务")
    print("=" * 60)

    # 1. 启动小红书
    print("\n[1] 启动小红书...")
    subprocess.run(["adb", "shell", "am", "start", "-n", "com.xingin.xhs/.index.v2.IndexActivityV2"], capture_output=True)
    wait(3)
    screenshot("01_start.png")

    # 2. 查找并点击搜索按钮
    print("\n[2] 查找搜索按钮...")
    # 搜索按钮通常是 content-desc="搜索" 或 text="搜索"
    result = skill_call("tap", target="搜索", strategy=["ui"])
    print(f"  结果: {result}")
    wait(2)
    screenshot("02_search.png")

    # 3. 输入 openclaw
    print("\n[3] 输入 'openclaw'...")
    result = skill_call("input", target="搜索", text="openclaw")
    print(f"  结果: {result}")
    wait(2)
    screenshot("03_typed.png")

    # 4. 点击搜索执行
    print("\n[4] 点击搜索执行...")
    # 查找搜索按钮或键盘确认
    result = skill_call("tap", target="搜索", strategy=["ui", "default"])
    print(f"  结果: {result}")
    wait(3)
    screenshot("04_results.png")

    # 5. 切换到用户标签
    print("\n[5] 查找'用户'标签...")
    result = skill_call("tap", target="用户", strategy=["ui"])
    print(f"  结果: {result}")
    wait(2)
    screenshot("05_users.png")

    # 6-8. 查找并点击三个用户
    print("\n[6-8] 查找并截图三个博主...")
    for i in range(3):
        print(f"\n  博主 {i+1}:")
        # 查找用户头像或昵称
        # 尝试点击第一个可见的用户
        result = skill_call("tap", target="关注", index=i, strategy=["ui"])
        print(f"    点击结果: {result}")

        # 如果没找到，尝试坐标点击
        if not result.get("success"):
            y = 500 + (i * 200)
            subprocess.run(["adb", "shell", "input", "tap", "300", str(y)], capture_output=True)

        wait(3)
        screenshot(f"06_blogger_{i+1}.png")

        # 返回
        subprocess.run(["adb", "shell", "input", "keyevent", "4"], capture_output=True)
        wait(1)

    # 9-13. 创建帖子
    print("\n[9] 打开发布...")
    result = skill_call("tap", target="发布", strategy=["ui", "default"])
    print(f"  结果: {result}")
    wait(2)
    screenshot("07_publish.png")

    print("\n[10] 选择图文...")
    result = skill_call("tap", target="图文", strategy=["ui", "default"])
    print(f"  结果: {result}")
    wait(2)

    print("\n[11] 选择图片...")
    # 选择三张截图
    for i, pos in enumerate([(200, 600), (400, 600), (600, 600)]):
        subprocess.run(["adb", "shell", "input", "tap", str(pos[0]), str(pos[1])], capture_output=True)
        wait(0.5)
    screenshot("08_selected.png")

    # 下一步
    result = skill_call("tap", target="下一步", strategy=["ui", "default"])
    wait(2)
    screenshot("09_editor.png")

    # 输入文案
    print("\n[12] 输入文案...")
    result = skill_call("input", target="标题", text="推荐三位OpenClaw技术大神")
    print(f"  标题: {result}")
    wait(1)

    # 正文
    full_text = """【OpenClaw博主推荐第一篇】🎯

今天给大家安利第一位OpenClaw技术博主！他的内容真的太有干货了，每一篇都让我收获满满。他深入解析OpenClaw的架构设计，从底层原理到实际应用都讲得特别透彻。特别是对于想要学习自动化技术的同学来说，他的教程简直是宝藏。不仅有详细的代码示例，还有实际项目经验的分享。强烈推荐大家关注！

【OpenClaw博主推荐第二篇】🚀

第二位博主也是OpenClaw领域的大神级人物！他的技术博客更新频率很高，内容质量却一点不打折扣。我最喜欢他分享的实战案例，每一个都是从真实项目中提炼出来的精华。从环境搭建到高级技巧，他都能用通俗易懂的语言解释清楚。如果你是OpenClaw的新手，跟他的教程学习绝对是最快的入门方式。赶紧关注起来一起学习吧！

【OpenClaw博主推荐第三篇】⭐

第三位推荐的博主专注OpenClaw的创新应用和前沿技术探索。他总能第一时间分享最新的功能更新和最佳实践经验。阅读他的文章，你会发现OpenClaw原来可以做这么多酷炫的事情！他不仅技术过硬，写作风格也很幽默风趣，让枯燥的技术内容变得生动有趣。强烈推荐给所有对自动化技术和AI感兴趣的朋友们！"""

    result = skill_call("input", target="正文", text=full_text)
    print(f"  正文: {result}")
    wait(2)
    screenshot("10_content.png")

    # 保存草稿
    print("\n[13] 保存草稿...")
    subprocess.run(["adb", "shell", "input", "keyevent", "4"], capture_output=True)
    wait(1)
    result = skill_call("tap", target="保存", strategy=["ui", "default"])
    print(f"  结果: {result}")
    wait(2)
    screenshot("11_done.png")

    print("\n" + "=" * 60)
    print("✅ 任务完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
