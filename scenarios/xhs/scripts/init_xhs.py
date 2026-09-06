#!/usr/bin/env python3
"""
XHS Canvas 按钮坐标初始化脚本

用途：在新设备或 XHS app 改版后，重新探测视频帖底部互动栏（❤️⭐💬）坐标，
      并保存到 config/devices/<device_id>.json，供 XHSAutomationSkill 自动加载。

用法：
    python scenarios/xhs/scripts/init_xhs.py --device 42231JEKB04971
    python scenarios/xhs/scripts/init_xhs.py --device 42231JEKB04971 --no-verify
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from skills.android.canvas_probe import CanvasProbe
from skills.android import device_utils
from skills.xhs.xhs_automation_skill import XHSAutomationSkill

SEARCH_QUERY = "ai"
HOME_RID = "com.xingin.xhs:id/index_home"
# 扫描范围：视频帖互动栏在导航栏之上，2100-2400 覆盖足够宽裕
BAR_Y_RANGE = (2100, 2400)
# 左→右语义：点赞 / 收藏 / 评论
BAR_SEMANTICS = ["like", "collect", "comment"]
# 黄金色像素判定（⭐ 变金色的 RGB 特征）
GOLD_R_MIN, GOLD_G_MIN, GOLD_B_MAX = 200, 150, 100
GOLD_MIN_PIXELS = 20  # 至少出现这么多金色像素才算验证通过


# ── 导航流程 ───────────────────────────────────────────────────────────────────

def navigate_to_video_post(xhs: XHSAutomationSkill) -> bool:
    """导航至搜索结果的第一个视频帖，返回是否成功进入视频帖。"""
    print("  1. 回首页...")
    if not device_utils.go_to_home(
        xhs.adb,
        xhs.device_id,
        HOME_RID,
        lambda: xhs.get_ui_hierarchy(force_refresh=True),
    ):
        print("  [ERR] 无法回到首页")
        return False
    xhs.tap_element("tab_home")
    time.sleep(2.0)

    print("  2. 打开搜索...")
    xhs.tap_element("search_bar")
    time.sleep(2.0)

    print(f"  3. 搜索 {SEARCH_QUERY!r}...")
    xhs.tap_element("search_input")
    time.sleep(0.5)
    xhs._adb(f"shell input text {SEARCH_QUERY}")
    time.sleep(0.5)
    xhs.tap_element("search_btn")
    time.sleep(3.0)

    print("  4. 点击第一个搜索结果...")
    card = xhs.find_element(resource_id="com.xingin.xhs:id/searchNoteCard")
    if card:
        print(f"  [info] searchNoteCard → {card.center}")
        xhs.tap(*card.center)
    else:
        print("  [WARN] 未找到 searchNoteCard，使用 fallback 坐标 (273, 748)")
        xhs.tap(273, 748)
    time.sleep(5.0)  # 视频帖需要等待媒体加载

    xml = xhs.get_ui_hierarchy(force_refresh=True)
    if "matrix_video_feed_note_detail_list" in xml:
        print("  ✅ 已进入新版视频帖")
        return True
    if "noteDetailRoot" in xml:
        print("  [INFO] 进入了旧版图文帖（有 resource-id 的互动按钮，无需坐标探测）")
    else:
        print("  [INFO] 页面状态不确定，继续尝试探测")
    return True  # 让后续的群数量检查决定是否失败


# ── 验证逻辑 ───────────────────────────────────────────────────────────────────

def check_golden_pixels(screenshot_rgb, cx: int, cy: int, radius: int = 30) -> bool:
    """检测 (cx, cy) 附近是否出现黄金色像素（⭐ 变金色的标志）。"""
    import numpy as np
    h, w = screenshot_rgb.shape[:2]
    region = screenshot_rgb[
        max(0, cy - radius) : min(h, cy + radius),
        max(0, cx - radius) : min(w, cx + radius),
    ]
    golden = (
        (region[:, :, 0] > GOLD_R_MIN)
        & (region[:, :, 1] > GOLD_G_MIN)
        & (region[:, :, 2] < GOLD_B_MAX)
    )
    return int(golden.sum()) >= GOLD_MIN_PIXELS


def verify_collect(probe: CanvasProbe, xhs: XHSAutomationSkill, cx: int, cy: int) -> bool:
    """点击收藏坐标，检测是否出现黄金色，成功后点回去还原状态。"""
    print(f"  [verify] 点击收藏 ({cx}, {cy})...")
    xhs.tap(cx, cy)
    time.sleep(1.5)
    after = probe.take_screenshot()
    if check_golden_pixels(after, cx, cy):
        print("  [verify] ✅ 检测到黄金色像素，收藏已触发")
        print("  [verify] 再次点击以取消收藏（还原状态）...")
        xhs.tap(cx, cy)
        time.sleep(1.0)
        return True
    print(
        "  [verify] ⚠️ 未检测到黄金色像素（可能已收藏 / 帖已删除 / 帧延迟），"
        "坐标仍将保存"
    )
    return False


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="XHS Canvas 按钮坐标初始化")
    parser.add_argument("--device", required=True, help="ADB 设备序列号")
    parser.add_argument("--no-verify", action="store_true", help="跳过收藏验证步骤")
    args = parser.parse_args()

    device_id = args.device
    do_verify = not args.no_verify

    print(f"=== XHS 坐标初始化 | 设备: {device_id} ===\n")

    if not device_utils.check_connected(device_id):
        return 1

    xhs = XHSAutomationSkill(device_id=device_id)
    probe = CanvasProbe(device_id=device_id)

    w, h = xhs.get_screen_size()
    print(f"屏幕分辨率: {w}×{h}\n")

    print("[1/4] 唤屏解锁...")
    device_utils.wake_unlock(xhs.adb, device_id)

    print("[2/4] 启动小红书...")
    xhs._adb("shell monkey -p com.xingin.xhs -c android.intent.category.LAUNCHER 1")
    time.sleep(4.0)

    print("[3/4] 导航至视频帖...")
    if not navigate_to_video_post(xhs):
        print("\n[FAIL] 导航失败，退出")
        return 1

    print("\n[4/4] 像素扫描互动栏...")
    screenshot = probe.take_screenshot()
    print(f"  截图尺寸: {screenshot.shape[1]}×{screenshot.shape[0]}")

    # 打印原始群信息，方便调试
    clusters = probe.find_icon_clusters(BAR_Y_RANGE[0], BAR_Y_RANGE[1])
    print(f"  找到 {len(clusters)} 个图标群:")
    for i, c in enumerate(clusters):
        print(
            f"    [{i}] x={c['x_start']}-{c['x_end']} (w={c['width']:3d})  "
            f"y={c['y_start']}-{c['y_end']} (h={c['height']:3d})  "
            f"center=({c['center_x']}, {c['center_y']})"
        )

    try:
        coords = probe.probe_and_save(
            app="xhs",
            section="video_post_bar",
            labels=BAR_SEMANTICS,
            y_range=BAR_Y_RANGE,
            min_clusters=2,
            screen_size=(w, h),
        )
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        print("  可能原因：视频帖尚未加载完毕，或互动栏不在扫描范围内")
        return 1

    icons = {"like": "❤️", "collect": "⭐", "comment": "💬"}
    print("\n坐标映射:")
    for k, v in coords.items():
        print(f"  {icons.get(k, k)} {k:12s}: {list(v)}")

    # 验证
    if do_verify and "collect" in coords:
        print("\n验证收藏坐标（点击后观察图标是否变金色）...")
        cx, cy = coords["collect"]
        verify_collect(probe, xhs, cx, cy)
    elif not do_verify:
        print("\n（--no-verify，跳过验证）")

    config_path = (ROOT / "config" / "devices" / f"{device_id}.json")
    print(f"\n💾 配置已保存: {config_path}")

    print("\n运行端到端测试验证坐标：")
    print("  python scenarios/xhs/scripts/test_collect_ai_post.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
