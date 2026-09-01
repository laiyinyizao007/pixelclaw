#!/usr/bin/env python3
"""
小红书AI博主推荐任务 - Pixel 8a版
使用PixelClaw项目在Pixel设备上完成：
1. 截图三个AI博主主页
2. 发布推荐帖子
"""

import sys
import time
sys.path.insert(0, '/home/averypi/Projects/pixelclaw')

from skills.xhs_automation_skill import XHSAutomationSkill

skill = XHSAutomationSkill()

def click_search_box():
    """点击搜索框"""
    # 点击右上角搜索按钮 (约980, 165)
    skill.tap(980, 165)
    time.sleep(1)

def clear_search():
    """清除搜索框"""
    # 点击X按钮清除 (约780, 90)
    skill.tap(780, 90)
    time.sleep(0.5)

def search_and_screenshot(blogger_name, filename):
    """搜索博主并截图"""
    print(f"\n{'='*60}")
    print(f"搜索博主: {blogger_name}")
    print('='*60)

    # 1. 点击搜索按钮
    print("\n[1/6] 点击搜索按钮...")
    click_search_box()

    # 2. 清除现有内容
    print("[2/6] 清除搜索框...")
    clear_search()

    # 3. 输入搜索词（使用拼音）
    print(f"[3/6] 输入搜索词拼音...")
    # 将中文转换为拼音输入
    pinyin_map = {
        "程序员三千": "chengxuyuansanqian",
        "大周小王出海笔记": "dazhouxiaowangchuhai",
        "Xuan酱": "xuanjiang"
    }
    pinyin = pinyin_map.get(blogger_name, blogger_name)

    # 点击搜索框
    skill.tap(540, 90)
    time.sleep(0.5)

    # 输入拼音
    skill._adb_cmd(f"shell input text '{pinyin}'")
    time.sleep(2)

    # 4. 点击搜索按钮
    print("[4/6] 点击搜索...")
    skill.tap(885, 90)
    time.sleep(3)

    # 5. 切换到用户标签
    print("[5/6] 切换到用户标签...")
    skill.tap(250, 165)  # 用户标签位置
    time.sleep(2)

    # 6. 截图
    print(f"[6/6] 截图保存...")
    filepath = skill.screenshot(filename)
    print(f"✅ 已保存: {filepath}")

    return True

def publish_post():
    """发布推荐帖子"""
    print(f"\n{'='*60}")
    print("发布推荐帖子")
    print('='*60)

    # 1. 回到首页
    print("\n[1/8] 回到首页...")
    skill._adb_cmd("shell input keyevent 4")
    time.sleep(0.5)
    skill._adb_cmd("shell input keyevent 4")
    time.sleep(1)

    # 2. 点击底部+按钮
    print("[2/8] 点击发布按钮...")
    skill.tap(540, 1270)
    time.sleep(2)

    # 3. 选择图文模式 (第二个选项)
    print("[3/8] 选择图文模式...")
    skill.tap(400, 600)
    time.sleep(2)

    # 4. 从相册选择图片
    print("[4/8] 选择图片...")
    # 点击相册第一张图
    skill.tap(270, 400)
    time.sleep(0.5)
    skill.tap(540, 400)
    time.sleep(0.5)
    skill.tap(810, 400)
    time.sleep(1)

    # 点击下一步
    skill.tap(980, 165)
    time.sleep(2)

    # 5. 输入标题
    print("[5/8] 输入标题...")
    skill.tap(540, 200)
    time.sleep(0.5)
    skill._adb_cmd("shell input text 'tuijian3geAIganhuobozhu'")
    time.sleep(1)

    # 6. 输入正文
    print("[6/8] 输入正文...")
    skill.tap(540, 400)
    time.sleep(0.5)

    # 分段输入正文
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
    print("[7/8] 添加标签...")
    skill.tap(540, 800)
    time.sleep(0.5)

    tags = ["AIgongju", "xiaolv", "bozhutuijian", "zidonghua"]
    for tag in tags:
        skill._adb_cmd(f"shell input text '{tag}'")
        skill._adb_cmd("shell input keyevent 66")
        time.sleep(0.3)

    # 8. 截图预览
    print("[8/8] 截图预览...")
    skill.screenshot("post_preview_final.png")

    # 9. 点击发布
    print("点击发布...")
    skill.tap(980, 165)
    time.sleep(3)

    print("✅ 帖子发布完成！")

if __name__ == "__main__":
    print("="*60)
    print("小红书AI博主推荐任务 - Pixel 8a")
    print("="*60)

    # 搜索并截图三个博主
    bloggers = [
        ("程序员三千", "xhs_blogger1_程序员三千.png"),
        ("大周小王出海笔记", "xhs_blogger2_大周小王.png"),
        ("Xuan酱", "xhs_blogger3_Xuan酱.png")
    ]

    for blogger_name, filename in bloggers:
        search_and_screenshot(blogger_name, filename)
        time.sleep(1)

    # 发布推荐帖子
    publish_post()

    print("\n" + "="*60)
    print("✅ 任务完成！")
    print("="*60)
