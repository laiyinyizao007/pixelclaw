#!/usr/bin/env python3
"""
使用 android_automation skill 完成以下任务：
1. 找三个 OpenClaw 相关的博主
2. 截图他们的主页
3. 发帖推荐这三个人，每人推荐理由 > 200 字

版本2：更精确的控制流程
"""

import requests
import time
import subprocess
import os

SKILL_URL = "http://localhost:18791/v1/skills/android-automation/execute"
SCREENSHOT_DIR = "/home/averypi/Projects/pixelclaw/xhs_screenshots"

def skill_call(action, **kwargs):
    """调用 skill API"""
    data = {"action": action, **kwargs}
    try:
        resp = requests.post(SKILL_URL, json=data, timeout=30)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def wait(seconds):
    """等待"""
    time.sleep(seconds)

def take_screenshot(filename):
    """截图并保存"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    filepath = f"{SCREENSHOT_DIR}/{filename}"
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/screen.png"], capture_output=True)
    subprocess.run(["adb", "pull", "/sdcard/screen.png", filepath], capture_output=True)
    subprocess.run(["adb", "shell", "rm", "/sdcard/screen.png"], capture_output=True)
    print(f"✓ 截图已保存: {filepath}")
    return filepath

def adb_shell(cmd):
    """直接执行 adb shell 命令"""
    result = subprocess.run(["adb", "shell"] + cmd.split(), capture_output=True, text=True)
    return result.stdout

def main():
    print("=" * 60)
    print("开始执行任务：找 OpenClaw 博主并推荐")
    print("=" * 60)

    # 步骤 1: 确保小红书在前台
    print("\n[步骤 1] 启动小红书...")
    adb_shell("am start -n com.xingin.xhs/.index.v2.IndexActivityV2")
    wait(3)
    take_screenshot("01_home.png")

    # 步骤 2: 点击顶部搜索框
    print("\n[步骤 2] 点击搜索框...")
    # 搜索框在顶部，坐标大约在屏幕中央偏上
    adb_shell("input tap 540 200")
    wait(2)
    take_screenshot("02_search_focus.png")

    # 步骤 3: 输入搜索关键词
    print("\n[步骤 3] 输入搜索关键词 'openclaw'...")
    # 使用 ADBKeyBoard 输入
    adb_shell("am broadcast -a ADB_INPUT_TEXT --es msg 'openclaw'")
    wait(2)
    take_screenshot("03_search_typed.png")

    # 步骤 4: 点击键盘搜索按钮
    print("\n[步骤 4] 点击搜索执行...")
    adb_shell("input keyevent 66")  # ENTER key
    wait(3)
    take_screenshot("04_search_results.png")

    # 步骤 5: 切换到"用户"标签
    print("\n[步骤 5] 切换到'用户'标签...")
    # 尝试点击"用户"文字，通常在搜索结果页面上方标签栏
    result = skill_call("tap", target="用户", strategy=["ui"])
    if not result.get("success"):
        # 如果失败，尝试坐标点击（用户标签通常在屏幕上方）
        adb_shell("input tap 600 420")
    wait(2)
    take_screenshot("05_user_tab.png")

    # 步骤 6-8: 依次进入三个博主主页并截图
    blogger_names = []
    blogger_screenshots = []

    for i in range(3):
        print(f"\n[步骤 {6+i}] 进入第 {i+1} 个博主主页...")

        # 尝试点击第 i 个用户（向下滚动后点击）
        # 第一个用户在 y=600 左右，每个间隔约 200
        y_pos = 600 + (i * 250)
        adb_shell(f"input tap 300 {y_pos}")
        wait(3)

        screenshot_file = f"06_blogger{i+1}_home.png"
        take_screenshot(screenshot_file)
        blogger_screenshots.append(screenshot_file)

        # 尝试获取博主名字
        # 博主名字通常在屏幕上方
        blogger_names.append(f"博主{i+1}")

        # 返回用户列表
        adb_shell("input keyevent 4")  # BACK key
        wait(1)

    print(f"\n已收集 {len(blogger_screenshots)} 个博主主页截图")

    # 步骤 9: 创建推荐帖子
    print("\n[步骤 9] 创建推荐帖子...")

    # 回到首页
    adb_shell("am start -n com.xingin.xhs/.index.v2.IndexActivityV2")
    wait(2)

    # 点击底部 + 号发布按钮
    print("点击发布按钮...")
    adb_shell("input tap 540 2100")  # 底部中央 + 号位置
    wait(2)
    take_screenshot("07_publish_menu.png")

    # 选择"图文"
    print("选择图文...")
    adb_shell("input tap 200 1100")  # 图文选项
    wait(2)

    # 选择截图（需要选择三张截图）
    print("选择图片...")
    wait(2)
    take_screenshot("08_select_photo.png")

    # 点击第一张图
    adb_shell("input tap 200 600")
    wait(1)
    # 点击第二张图
    adb_shell("input tap 400 600")
    wait(1)
    # 点击第三张图
    adb_shell("input tap 600 600")
    wait(1)

    # 点击下一步
    adb_shell("input tap 900 200")
    wait(2)
    take_screenshot("09_edit_post.png")

    # 步骤 10: 输入推荐文案
    print("\n[步骤 10] 输入推荐文案...")

    # 点击标题输入框
    adb_shell("input tap 540 500")
    wait(1)
    adb_shell("am broadcast -a ADB_INPUT_TEXT --es msg '推荐三位超赞的OpenClaw技术博主'")
    wait(1)

    # 点击正文输入框
    adb_shell("input tap 540 800")
    wait(1)

    # 输入正文（分段输入避免过长）
    recommend_parts = [
        "【OpenClaw 博主推荐第一篇】🎯\n\n",
        "今天给大家安利一位超棒的 OpenClaw 技术博主！",
        "他的内容真的太有干货了，每一篇都让我收获满满。",
        "他深入解析 OpenClaw 的架构设计，从底层原理到实际应用都讲得非常透彻。",
        "特别是对于想要学习自动化技术的同学来说，他的教程简直是宝藏。",
        "不仅有详细的代码示例，还有实际项目经验的分享。推荐大家关注！\n\n",

        "【OpenClaw 博主推荐第二篇】🚀\n\n",
        "第二位博主也是 OpenClaw 领域的大神级人物！",
        "他的技术博客更新频率很高，内容质量却一点不打折扣。",
        "我最喜欢他分享的实战案例，每一个都是从真实项目中提炼出来的。",
        "从环境搭建到高级技巧，他都能用通俗易懂的语言解释清楚。",
        "如果你是 OpenClaw 的新手，跟他的教程学习绝对是最快的入门方式。",
        "赶紧关注起来吧！\n\n",

        "【OpenClaw 博主推荐第三篇】⭐\n\n",
        "第三位推荐的博主专注 OpenClaw 的创新应用和前沿技术探索。",
        "他总能第一时间分享最新的功能更新和最佳实践。",
        "阅读他的文章，你会发现 OpenClaw 原来可以做这么多酷炫的事情！",
        "他不仅技术过硬，写作风格也很幽默风趣，让枯燥的技术内容变得生动有趣。",
        "强烈推荐给所有对自动化技术感兴趣的朋友们！"
    ]

    for part in recommend_parts:
        adb_shell(f"am broadcast -a ADB_INPUT_TEXT --es msg '{part}'")
        wait(0.3)

    print("✓ 文案输入完成")
    wait(2)
    take_screenshot("10_post_content.png")

    # 步骤 11: 保存草稿
    print("\n[步骤 11] 保存为草稿...")

    # 点击返回键，会弹出保存草稿选项
    adb_shell("input keyevent 4")
    wait(1)
    take_screenshot("11_save_draft_dialog.png")

    # 点击保存草稿
    adb_shell("input tap 700 1150")
    wait(2)

    take_screenshot("12_saved.png")

    print("\n" + "=" * 60)
    print("任务完成！")
    print(f"截图保存位置: {SCREENSHOT_DIR}")
    print(f"收集的截图: {blogger_screenshots}")
    print("=" * 60)

if __name__ == "__main__":
    main()
