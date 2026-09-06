#!/usr/bin/env python3
"""
小红书AI博主推荐任务 - Pixel 8a版
使用PixelClaw项目在Pixel设备上完成：
1. 截图三个AI博主主页
2. 发布推荐帖子
"""

import sys
import time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parents[3]))

from skills.xhs import XHSAutomationSkill
from utils import web_search
from utils.logging_setup import setup_logger


def click_search_box(skill, logger):
    """点击搜索框"""
    logger.info("[click_search_box] 点击右上角搜索按钮")
    skill.tap(980, 165)
    time.sleep(1)


def clear_search(skill, logger):
    """清除搜索框"""
    logger.info("[clear_search] 点击X清除")
    skill.tap(780, 90)
    time.sleep(0.5)


def search_and_screenshot(blogger_name, filename, skill, logger):
    """搜索博主并截图"""
    logger.info("搜索博主: %s", blogger_name)

    # 1. 点击搜索按钮
    logger.info("[1/6] 点击搜索按钮")
    click_search_box(skill, logger)

    # 2. 清除现有内容
    logger.info("[2/6] 清除搜索框")
    clear_search(skill, logger)

    # 3. 输入搜索词（使用拼音）
    logger.info("[3/6] 输入搜索词拼音")
    pinyin_map = {
        "程序员三千": "chengxuyuansanqian",
        "大周小王出海笔记": "dazhouxiaowangchuhai",
        "Xuan酱": "xuanjiang"
    }
    pinyin = pinyin_map.get(blogger_name, blogger_name)

    skill.tap(540, 90)
    time.sleep(0.5)
    skill._adb_cmd(f"shell input text '{pinyin}'")
    time.sleep(2)

    # 4. 点击搜索按钮
    logger.info("[4/6] 点击搜索")
    skill.tap(885, 90)
    time.sleep(3)

    # 5. 切换到用户标签
    logger.info("[5/6] 切换到用户标签")
    skill.tap(250, 165)
    time.sleep(2)

    # 6. 截图
    logger.info("[6/6] 截图保存")
    filepath = skill.screenshot(filename)
    logger.info("已保存: %s", filepath)

    return True


def publish_post(skill, logger):
    """发布推荐帖子"""
    logger.info("发布推荐帖子")

    # 1. 回到首页
    logger.info("[1/8] 回到首页")
    skill._adb_cmd("shell input keyevent 4")
    time.sleep(0.5)
    skill._adb_cmd("shell input keyevent 4")
    time.sleep(1)

    # 2. 点击底部+按钮
    logger.info("[2/8] 点击发布按钮")
    skill.tap(540, 1270)
    time.sleep(2)

    # 3. 选择图文模式
    logger.info("[3/8] 选择图文模式")
    skill.tap(400, 600)
    time.sleep(2)

    # 4. 从相册选择图片
    logger.info("[4/8] 选择图片")
    skill.tap(270, 400)
    time.sleep(0.5)
    skill.tap(540, 400)
    time.sleep(0.5)
    skill.tap(810, 400)
    time.sleep(1)
    skill.tap(980, 165)
    time.sleep(2)

    # 5. 输入标题
    logger.info("[5/8] 输入标题")
    skill.tap(540, 200)
    time.sleep(0.5)
    skill._adb_cmd("shell input text 'tuijian3geAIganhuobozhu'")
    time.sleep(1)

    # 6. 输入正文
    logger.info("[6/8] 输入正文")
    skill.tap(540, 400)
    time.sleep(0.5)

    paragraphs = [
        "jintian gei dajia tuijian 3ge chao shiyong de AI ganhuo bozhu",
        "chengxuyuan sanqian: zhuanzhu AI biancheng gongju he kaiyuan xiangmu",
        "dazhou xiaowang: zhuanzhu kuajing chuhai he AI zidonghua gongzuoliu",
        "Xuanjiang: 167k fans de AI gongju jiaocheng daV",
        "kuai qu guanzhu tamen, rang AI bang ni ganhuo"
    ]

    for para in paragraphs:
        skill._adb_cmd(f"shell input text '{para}'")
        skill._adb_cmd("shell input keyevent 66")
        time.sleep(0.3)

    # 7. 添加标签
    logger.info("[7/8] 添加标签")
    skill.tap(540, 800)
    time.sleep(0.5)
    tags = ["AIgongju", "xiaolv", "bozhutuijian", "zidonghua"]
    for tag in tags:
        skill._adb_cmd(f"shell input text '{tag}'")
        skill._adb_cmd("shell input keyevent 66")
        time.sleep(0.3)

    # 8. 截图预览
    logger.info("[8/8] 截图预览")
    skill.screenshot("post_preview_final.png")

    logger.info("点击发布")
    skill.tap(980, 165)
    time.sleep(3)

    logger.info("帖子发布完成")


def main():
    logger = setup_logger("xhs_blogger_rec", log_dir="./logs/xhs")
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
            search_and_screenshot(blogger_name, filename, skill, logger)
            succeeded.append(blogger_name)
        except Exception as exc:
            logger.error("处理博主 '%s' 时出现未预期异常", blogger_name, exc_info=True)
            failed.append(blogger_name)
            hints = web_search.search(f"小红书 自动化 {type(exc).__name__} {str(exc)[:80]}")
            if hints:
                logger.info("[WebSearch] 参考结果: %s", hints[0][:200])
            continue
        time.sleep(1)

    try:
        publish_post(skill, logger)
    except Exception as exc:
        logger.error("发布帖子时出现未预期异常", exc_info=True)
        hints = web_search.search(f"小红书 自动化发布 {type(exc).__name__} {str(exc)[:80]}")
        if hints:
            logger.info("[WebSearch] 参考结果: %s", hints[0][:200])

    print("\n" + "=" * 60)
    print(f"✅ 任务完成！成功: {len(succeeded)} 个  {succeeded}")
    if failed:
        print(f"❌ 失败: {len(failed)} 个  {failed}")
    print("=" * 60)


if __name__ == "__main__":
    main()
