#!/usr/bin/env python3
"""
在Pixel 8a上完成小红书AI博主推荐任务
"""
import sys
import time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parents[3]))

from skills.xhs import XHSAutomationSkill
from utils import web_search
from utils.logging_setup import setup_logger


def search_blogger(blogger_name, filename, skill, logger):
    """搜索博主并截图"""
    logger.info("搜索博主: %s", blogger_name)

    # 1. 点击搜索框
    logger.info("[1/5] 点击搜索框")
    skill.tap(540, 90)
    time.sleep(1)

    # 2. 输入搜索词（使用拼音）
    logger.info("[2/5] 清除并输入搜索词")
    skill.tap(780, 90)
    time.sleep(0.5)

    skill._adb_cmd(f"shell input text '{blogger_name}'")
    time.sleep(2)

    # 3. 点击搜索按钮
    logger.info("[3/5] 点击搜索")
    skill.tap(885, 90)
    time.sleep(3)

    # 4. 切换到"用户"标签
    logger.info("[4/5] 切换到用户标签")
    skill.tap(250, 165)
    time.sleep(2)

    # 5. 截图
    logger.info("[5/5] 截图保存")
    filepath = skill.screenshot(filename)
    logger.info("已保存: %s", filepath)

    return True


def publish_post(skill, logger):
    """发布推荐帖子"""
    logger.info("发布推荐帖子")

    # 1. 点击底部+按钮
    logger.info("[1/8] 点击发布按钮")
    skill.tap(540, 1270)
    time.sleep(2)

    # 2. 选择图文模式
    logger.info("[2/8] 选择图文模式")
    skill.tap(540, 600)
    time.sleep(1)

    # 3. 选择图片
    logger.info("[3/8] 选择图片")
    time.sleep(2)

    # 4. 输入标题
    logger.info("[4/8] 输入标题")
    skill.tap(540, 200)
    time.sleep(0.5)
    skill._adb_cmd("shell input text '推荐3个AI干活博主｜效率提升神器'")
    time.sleep(1)

    # 5. 输入正文
    logger.info("[5/8] 输入正文")
    skill.tap(540, 400)
    time.sleep(0.5)

    content = """今天给大家推荐3个超实用的AI干活博主！

【程序员三千】
专注AI编程工具和开源项目，分享77.7k star的AI自动化工具，适合开发者学习。

【大周小王出海笔记】
专注跨境出海和AI自动化工作流，零代码实现AI自动化，适合非技术用户。

【Xuan酱】
167k粉丝的AI工具教程大V，手把手教你搭建AI工具，内容详细易懂。

快去关注他们，让AI帮你干活吧！"""

    for line in content.split('\n'):
        skill._adb_cmd(f"shell input text '{line}'")
        skill._adb_cmd("shell input keyevent 66")
        time.sleep(0.3)

    # 6. 添加标签
    logger.info("[6/8] 添加标签")
    skill.tap(540, 800)
    time.sleep(0.5)
    tags = ["AI工具", "效率提升", "博主推荐", "AI干活", "自动化"]
    for tag in tags:
        skill._adb_cmd(f"shell input text '{tag}'")
        skill._adb_cmd("shell input keyevent 66")
        time.sleep(0.3)

    # 7. 截图预览
    logger.info("[7/8] 截图预览")
    skill.screenshot("post_preview.png")

    # 8. 点击发布
    logger.info("[8/8] 点击发布")
    skill.tap(980, 90)
    time.sleep(3)

    logger.info("帖子发布完成")


def main():
    logger = setup_logger("xhs_run_blogger", log_dir="./logs/xhs")
    skill = XHSAutomationSkill()

    bloggers = [
        ("程序员三千", "xhs_blogger1_程序员三千.png"),
        ("大周小王出海笔记", "xhs_blogger2_大周小王.png"),
        ("Xuan酱", "xhs_blogger3_Xuan酱.png"),
    ]

    succeeded = []
    failed = []

    for blogger_name, filename in bloggers:
        try:
            search_blogger(blogger_name, filename, skill, logger)
            succeeded.append(blogger_name)
        except Exception as exc:
            logger.error("处理博主 '%s' 时出现未预期异常", blogger_name, exc_info=True)
            failed.append(blogger_name)
            hints = web_search.search(f"小红书 自动化 {type(exc).__name__} {str(exc)[:80]}")
            if hints:
                logger.info("[WebSearch] 参考结果: %s", hints[0][:200])
            continue
        time.sleep(1)

    skill.go_back()
    skill.go_back()

    try:
        publish_post(skill, logger)
    except Exception as exc:
        logger.error("发布帖子时出现未预期异常", exc_info=True)
        hints = web_search.search(f"小红书 自动化发布 {type(exc).__name__} {str(exc)[:80]}")
        if hints:
            logger.info("[WebSearch] 参考结果: %s", hints[0][:200])

    print("\n" + "=" * 50)
    print(f"✅ 任务完成！成功: {len(succeeded)} 个  {succeeded}")
    if failed:
        print(f"❌ 失败: {len(failed)} 个  {failed}")
    print("=" * 50)


if __name__ == "__main__":
    main()
