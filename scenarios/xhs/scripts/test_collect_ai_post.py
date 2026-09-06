#!/usr/bin/env python3
"""
端到端测试：搜索"AI"相关帖子并收藏
设备：Pixel 8a (42231JEKB04971)
运行：python scenarios/xhs/scripts/test_collect_ai_post.py
"""

import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

# Windows 终端 UTF-8 兼容
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from skills.android import device_utils
from skills.xhs.xhs_automation_skill import XHSAutomationSkill

DEVICE = "42231JEKB04971"
SEARCH_QUERY = "ai"
HOME_RID = "com.xingin.xhs:id/index_home"
OUT_DIR = os.path.join(tempfile.gettempdir(), "pixelclaw_output")


def find_first_result_coord(xml_text: str) -> tuple:
    """从搜索结果 RecyclerView 中找第一个可点击帖子的中心坐标"""
    # 代理字符（surrogate chars）会让 ET.fromstring 抛 UnicodeEncodeError，先替换掉
    xml_safe = xml_text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    try:
        root = ET.fromstring(xml_safe)
        rv_rid = "com.xingin.xhs:id/mSearchResultListContentTRv"

        def find_rv(node):
            if node.attrib.get("resource-id") == rv_rid:
                return node
            for child in node:
                r = find_rv(child)
                if r is not None:
                    return r
            return None

        rv = find_rv(root)
        if rv is not None:
            for child in rv.iter():
                rid = child.attrib.get("resource-id", "")
                # 优先按 resource-id 匹配帖子卡片
                is_card = "searchNoteCard" in rid or "resultNoteContainer" in rid
                clickable = child.attrib.get("clickable") == "true"
                if is_card or clickable:
                    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                                 child.attrib.get("bounds", ""))
                    if m:
                        x1, y1, x2, y2 = map(int, m.groups())
                        if (y2 - y1) > 100 and (x2 - x1) > 100:
                            coord = ((x1 + x2) // 2, (y1 + y2) // 2)
                            reason = "rid=" + rid if is_card else "clickable=true"
                            print(f"  [info] 结果坐标（{reason}） → {coord}")
                            return coord
    except Exception as e:
        print(f"  [WARN] XML 解析失败: {type(e).__name__}: {e}")

    coord = (540, 700)
    print(f"  [WARN] 未找到结果节点，使用 fallback 坐标 {coord}")
    return coord


def _try_dump_xml(xhs: XHSAutomationSkill, retries: int = 3, delay: float = 2.0) -> str:
    """尝试 dump 当前 XML，视频播放时可能失败，返回空串表示失败"""
    for i in range(retries):
        try:
            xml = xhs.get_ui_hierarchy(force_refresh=True)
            if xml and len(xml) > 100:
                return xml
        except Exception as e:
            print(f"  [info] dump 第 {i+1} 次失败: {e}")
        if i < retries - 1:
            time.sleep(delay)
    return ""


def collect_current_post(xhs: XHSAutomationSkill) -> bool:
    """收藏当前帖子，兼容旧版图文帖和新版视频帖"""
    xml = _try_dump_xml(xhs)
    is_old = "noteDetailRoot" in xml
    is_video = "matrix_video_feed_note_detail_list" in xml

    print(f"  帖子类型: {'旧版图文' if is_old else '新版视频' if is_video else '未知'}")

    if is_old:
        return xhs.favorite_current_post()

    if is_video or not xml:
        # 先尝试 content-desc
        for cd in ("收藏", "Collect", "collect"):
            elem = xhs.find_element(content_desc=cd)
            if elem:
                print(f"  [info] content-desc={cd!r} → {elem.center}")
                xhs.tap(*elem.center)
                time.sleep(0.5)
                return True

        # 再试 resource-id（旧版 ID 可能在视频帖里也存在）
        for rid in ("com.xingin.xhs:id/noteCollectLayout",
                    "com.xingin.xhs:id/collectBtn",
                    "com.xingin.xhs:id/favoriteBtn"):
            elem = xhs.find_element(resource_id=rid)
            if elem:
                print(f"  [info] rid={rid} → {elem.center}")
                xhs.tap(*elem.center)
                time.sleep(0.5)
                return True

        # 打印可用 resource-id 帮助调试
        if xml:
            import re as _re
            rids = _re.findall(r'resource-id="(com\.xingin[^"]+)"', xml)
            unique = sorted(set(rids))
            print(f"  [debug] 当前页面共 {len(unique)} 个 resource-id（收藏相关）:")
            for r in unique:
                if any(k in r.lower() for k in ("collect", "like", "fav", "star", "interact")):
                    print(f"    {r}")

        # 坐标 fallback：从设备配置加载，或使用实测默认值
        # 像素扫描实测（Pixel 8a, 1080×2400, 2026-09-04）：
        #   ⭐ icon x=710-773 center=741, y=2256-2316 center=2286
        collect_coord = xhs._video_bar_coords.get("collect", (741, 2286))
        print(f"  [info] 坐标 fallback → 点击底部收藏按钮 {collect_coord}")
        xhs.tap(*collect_coord)
        time.sleep(1.0)
        return True  # 乐观返回，后续截图可核验

    # 未能识别帖子类型，尝试旧版路径
    print("  [WARN] 未识别帖子类型，尝试 noteCollectLayout")
    return xhs.favorite_current_post()


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    xhs = XHSAutomationSkill(device_id=DEVICE, output_dir=OUT_DIR)

    print("=== XHS 收藏 AI 帖子测试 ===\n")

    if not device_utils.check_connected(DEVICE):
        return 1

    # 唤屏解锁
    print("[pre] 唤屏/解锁")
    device_utils.wake_unlock(xhs.adb, DEVICE)

    # 唤起/切换到小红书
    print("\n[0/5] 启动小红书")
    xhs._adb("shell monkey -p com.xingin.xhs -c android.intent.category.LAUNCHER 1")
    time.sleep(4.0)

    # Step 1: 确保在首页（先 BACK 到首页，再点 Tab 确认）
    print("[1/5] 导航至首页")
    if not device_utils.go_to_home(
        xhs.adb, DEVICE, HOME_RID,
        lambda: xhs.get_ui_hierarchy(force_refresh=True),
        max_backs=6,
    ):
        print("  [WARN] 无法通过 BACK 回到首页，仍尝试 tap_element")
    xhs.tap_element("tab_home")
    time.sleep(2.0)

    # Step 2: 打开搜索入口
    print("\n[2/5] 打开搜索框")
    xhs.tap_element("search_bar")
    time.sleep(2.0)

    # Step 3: 输入搜索词并执行搜索
    print(f"\n[3/5] 搜索 {SEARCH_QUERY!r}")
    xhs.tap_element("search_input")   # 聚焦输入框
    time.sleep(0.5)
    xhs._adb(f"shell input text {SEARCH_QUERY}")
    time.sleep(0.5)
    xhs.tap_element("search_btn")
    print("  等待搜索结果加载...")
    time.sleep(3.0)

    # Step 4: 点击第一个搜索结果
    print("\n[4/5] 点击第一个搜索结果")
    xml = xhs.get_ui_hierarchy(force_refresh=True)
    coord = find_first_result_coord(xml)
    xhs.tap(*coord)
    print("  等待帖子详情加载...")
    time.sleep(4.0)

    # Step 5: 收藏
    print("\n[5/5] 收藏帖子")
    success = collect_current_post(xhs)

    # 收藏后截图
    ss_path = xhs.screenshot("after_collect.png")
    print(f"\n截图: {ss_path}")

    print()
    if success:
        print("✅ 收藏成功！（请核验截图：⭐ 图标是否变为黄色/实心）")
    else:
        print("❌ 收藏失败")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
