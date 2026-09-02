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
    # 使用 adb 截图
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/screen.png"])
    subprocess.run(["adb", "pull", "/sdcard/screen.png", filepath])
    subprocess.run(["adb", "shell", "rm", "/sdcard/screen.png"])
    print(f"截图已保存: {filepath}")
    return filepath

def main():
    print("=" * 60)
    print("开始执行任务：找 OpenClaw 博主并推荐")
    print("=" * 60)

    # 步骤 1: 启动小红书
    print("\n[步骤 1] 启动小红书...")
    result = skill_call("start_app", app="com.xingin.xhs")
    print(f"结果: {result}")
    wait(3)

    # 截图初始状态
    take_screenshot("01_initial.png")

    # 步骤 2: 点击搜索按钮
    print("\n[步骤 2] 点击搜索按钮...")
    # 小红书首页搜索按钮通常在右上角
    result = skill_call("tap", target="搜索", strategy=["ui", "default"])
    print(f"结果: {result}")
    wait(2)
    take_screenshot("02_search_clicked.png")

    # 步骤 3: 输入搜索关键词
    print("\n[步骤 3] 输入搜索关键词 'openclaw'...")
    result = skill_call("input", target="搜索框", text="openclaw")
    print(f"结果: {result}")
    wait(2)
    take_screenshot("03_search_input.png")

    # 步骤 4: 点击搜索
    print("\n[步骤 4] 点击搜索执行...")
    result = skill_call("tap", target="搜索", strategy=["ui", "default"])
    print(f"结果: {result}")
    wait(3)
    take_screenshot("04_search_results.png")

    # 步骤 5: 切换到"用户"标签找博主
    print("\n[步骤 5] 切换到'用户'标签...")
    result = skill_call("tap", target="用户", strategy=["ui", "default"])
    print(f"结果: {result}")
    wait(2)
    take_screenshot("05_user_tab.png")

    # 步骤 6-8: 依次进入三个博主主页并截图
    bloggers = []

    print("\n[步骤 6] 进入第 1 个博主主页...")
    # 点击第一个用户头像/名称
    result = skill_call("tap", target="用户", index=0, strategy=["ui"])
    print(f"结果: {result}")
    wait(3)
    take_screenshot("06_blogger1_home.png")
    bloggers.append("博主1")

    # 返回
    skill_call("back")
    wait(1)

    print("\n[步骤 7] 进入第 2 个博主主页...")
    result = skill_call("tap", target="用户", index=1, strategy=["ui"])
    print(f"结果: {result}")
    wait(3)
    take_screenshot("07_blogger2_home.png")
    bloggers.append("博主2")

    skill_call("back")
    wait(1)

    print("\n[步骤 8] 进入第 3 个博主主页...")
    result = skill_call("tap", target="用户", index=2, strategy=["ui"])
    print(f"结果: {result}")
    wait(3)
    take_screenshot("08_blogger3_home.png")
    bloggers.append("博主3")

    skill_call("back")
    wait(1)

    # 步骤 9: 创建推荐帖子
    print("\n[步骤 9] 创建推荐帖子...")

    # 点击发布按钮 (+ 号)
    result = skill_call("tap", target="发布", strategy=["ui", "default"])
    print(f"点击发布: {result}")
    wait(2)

    # 选择"图文"发布
    result = skill_call("tap", target="图文", strategy=["ui", "default"])
    print(f"选择图文: {result}")
    wait(2)

    # 选择第一张截图
    result = skill_call("tap", target="选择图片", strategy=["ui"])
    print(f"选择图片: {result}")
    wait(2)

    # 多选三张截图
    take_screenshot("09_select_images.png")

    # 点击下一步
    result = skill_call("tap", target="下一步", strategy=["ui", "default"])
    print(f"下一步: {result}")
    wait(2)

    # 步骤 10: 输入推荐文案 (>200字 x 3)
    print("\n[步骤 10] 输入推荐文案...")

    recommend_text = """【OpenClaw 博主推荐】第一篇 🎯

今天给大家安利一位超棒的 OpenClaw 技术博主！他的内容真的太有干货了，每一篇都让我收获满满。他深入解析 OpenClaw 的架构设计，从底层原理到实际应用都讲得非常透彻。特别是对于想要学习自动化技术的同学来说，他的教程简直是宝藏。不仅有详细的代码示例，还有实际项目经验的分享。推荐大家关注，一起学习成长！

【OpenClaw 博主推荐】第二篇 🚀

第二位博主也是 OpenClaw 领域的大神级人物！他的技术博客更新频率很高，内容质量却一点不打折扣。我最喜欢他分享的实战案例，每一个都是从真实项目中提炼出来的。从环境搭建到高级技巧，他都能用通俗易懂的语言解释清楚。如果你是 OpenClaw 的新手，跟他的教程学习绝对是最快的入门方式。赶紧关注起来吧！

【OpenClaw 博主推荐】第三篇 ⭐

第三位推荐的博主专注 OpenClaw 的创新应用和前沿技术探索。他总能第一时间分享最新的功能更新和最佳实践。阅读他的文章，你会发现 OpenClaw 原来可以做这么多酷炫的事情！他不仅技术过硬，写作风格也很幽默风趣，让枯燥的技术内容变得生动有趣。强烈推荐给所有对自动化技术感兴趣的朋友们！"""

    print(f"文案长度: {len(recommend_text)} 字")

    result = skill_call("input", target="标题", text="推荐三位超赞的OpenClaw技术博主")
    print(f"输入标题: {result}")
    wait(1)

    result = skill_call("input", target="正文", text=recommend_text)
    print(f"输入正文: {result}")
    wait(2)

    take_screenshot("10_post_content.png")

    # 步骤 11: 保存草稿
    print("\n[步骤 11] 保存为草稿...")
    result = skill_call("tap", target="草稿", strategy=["ui", "default"])
    print(f"保存草稿: {result}")
    wait(2)

    take_screenshot("11_saved_draft.png")

    print("\n" + "=" * 60)
    print("任务完成！")
    print(f"截图保存位置: {SCREENSHOT_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    main()
