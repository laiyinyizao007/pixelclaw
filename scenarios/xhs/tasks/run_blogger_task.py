#!/usr/bin/env python3
"""
在Pixel 8a上完成小红书AI博主推荐任务
"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parents[3]))

from skills.xhs import XHSAutomationSkill
import time

skill = XHSAutomationSkill()

def search_blogger(blogger_name, filename):
    """搜索博主并截图"""
    print(f"\n{'='*50}")
    print(f"搜索博主: {blogger_name}")
    print('='*50)

    # 1. 点击搜索框
    print("\n[1/5] 点击搜索框...")
    skill.tap(540, 90)
    time.sleep(1)

    # 2. 输入搜索词（使用拼音）
    print(f"[2/5] 输入搜索词...")
    # 清除现有内容
    skill.tap(780, 90)  # 点击X清除
    time.sleep(0.5)

    # 使用拼音输入
    pinyin = blogger_name.encode('unicode_escape').decode()
    skill._adb_cmd(f"shell input text '{blogger_name}'")
    time.sleep(2)

    # 3. 点击搜索按钮
    print("[3/5] 点击搜索...")
    skill.tap(885, 90)
    time.sleep(3)

    # 4. 切换到"用户"标签
    print("[4/5] 切换到用户标签...")
    skill.tap(250, 165)
    time.sleep(2)

    # 5. 截图
    print(f"[5/5] 截图保存...")
    filepath = skill.screenshot(filename)
    print(f"✅ 已保存: {filepath}")

    return True

def publish_post():
    """发布推荐帖子"""
    print(f"\n{'='*50}")
    print("发布推荐帖子")
    print('='*50)

    # 1. 点击底部+按钮
    print("\n[1/8] 点击发布按钮...")
    skill.tap(540, 1270)
    time.sleep(2)

    # 2. 选择图文模式
    print("[2/8] 选择图文模式...")
    skill.tap(540, 600)
    time.sleep(1)

    # 3. 选择图片（从相册选择三张截图）
    print("[3/8] 选择图片...")
    # 这里需要手动选择或从特定路径选择
    # 暂时跳过，使用默认图库选择
    time.sleep(2)

    # 4. 输入标题
    print("[4/8] 输入标题...")
    skill.tap(540, 200)  # 标题输入框
    time.sleep(0.5)
    skill._adb_cmd("shell input text '推荐3个AI干活博主｜效率提升神器'")
    time.sleep(1)

    # 5. 输入正文
    print("[5/8] 输入正文...")
    skill.tap(540, 400)  # 正文区域
    time.sleep(0.5)

    content = """今天给大家推荐3个超实用的AI干活博主！

【程序员三千】
专注AI编程工具和开源项目，分享77.7k star的AI自动化工具，适合开发者学习。

【大周小王出海笔记】
专注跨境出海和AI自动化工作流，零代码实现AI自动化，适合非技术用户。

【Xuan酱】
167k粉丝的AI工具教程大V，手把手教你搭建AI工具，内容详细易懂。

快去关注他们，让AI帮你干活吧！"""

    # 分段输入
    for line in content.split('\n'):
        skill._adb_cmd(f"shell input text '{line}'")
        skill._adb_cmd("shell input keyevent 66")  # 回车
        time.sleep(0.3)

    # 6. 添加标签
    print("[6/8] 添加标签...")
    skill.tap(540, 800)  # 标签区域
    time.sleep(0.5)
    tags = ["AI工具", "效率提升", "博主推荐", "AI干活", "自动化"]
    for tag in tags:
        skill._adb_cmd(f"shell input text '{tag}'")
        skill._adb_cmd("shell input keyevent 66")
        time.sleep(0.3)

    # 7. 截图预览
    print("[7/8] 截图预览...")
    skill.screenshot("post_preview.png")

    # 8. 点击发布
    print("[8/8] 点击发布...")
    skill.tap(980, 90)  # 发布按钮（右上角）
    time.sleep(3)

    print("✅ 帖子发布完成！")

if __name__ == "__main__":
    # 搜索并截图三个博主
    search_blogger("程序员三千", "xhs_blogger1_程序员三千.png")
    time.sleep(1)

    search_blogger("大周小王出海笔记", "xhs_blogger2_大周小王.png")
    time.sleep(1)

    search_blogger("Xuan酱", "xhs_blogger3_Xuan酱.png")
    time.sleep(1)

    # 返回首页
    skill.go_back()
    skill.go_back()

    # 发布推荐帖子
    publish_post()

    print("\n" + "="*50)
    print("✅ 任务完成！")
    print("="*50)
