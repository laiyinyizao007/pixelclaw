#!/usr/bin/env python3
"""
小红书 UI 全页面系统性探索脚本

功能：
  1. 逐一导航到小红书的所有主要页面（首页/搜索结果/帖子详情/博主主页/
     消息Tab/发布页/个人中心）
  2. 在每个页面保存 UIAutomator XML dump 到 tmp_dumps/
  3. 从所有 dump 中提取所有 resource-id，生成去重、分页报告

运行：
  python scenarios/xhs/scripts/dump_xhs_ui.py [--device DEVICE_ID]
  python scenarios/xhs/scripts/dump_xhs_ui.py --device 42231JEKB04971
"""

import argparse
import io
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Force UTF-8 output on Windows where the default console encoding may be cp1252
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parents[3]
DUMP_DIR = ROOT / "tmp_dumps"
XHS_PACKAGE = "com.xingin.xhs"
XHS_ACTIVITY = "com.xingin.xhs/.index.v2.IndexActivityV2"
DEFAULT_DEVICE = "42231JEKB04971"
SCREEN_W = 1080  # Pixel 8a
SCREEN_H = 2400


# ---------------------------------------------------------------------------
# Low-level ADB helpers (no dependency on project code for portability)
# ---------------------------------------------------------------------------

def _adb(cmd: str, device: str, timeout: int = 30) -> Tuple[bool, str]:
    """Run `adb -s <device> <cmd>` and return (success, stdout)."""
    parts = ["adb", "-s", device] + cmd.split()
    try:
        r = subprocess.run(parts, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode == 0, r.stdout
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        print("ERROR: adb not found in PATH.")
        sys.exit(1)


def shell(cmd: str, device: str, timeout: int = 30) -> Tuple[bool, str]:
    """Run `adb -s <device> shell <cmd>`."""
    parts = ["adb", "-s", device, "shell"] + cmd.split()
    try:
        r = subprocess.run(parts, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode == 0, r.stdout
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        print("ERROR: adb not found in PATH.")
        sys.exit(1)


def tap(x: int, y: int, device: str) -> bool:
    ok, _ = shell(f"input tap {x} {y}", device)
    return ok


def swipe(x1: int, y1: int, x2: int, y2: int, dur: int, device: str) -> bool:
    ok, _ = shell(f"input swipe {x1} {y1} {x2} {y2} {dur}", device)
    return ok


def back(device: str) -> bool:
    ok, _ = shell("input keyevent 4", device)
    return ok


def home_key(device: str) -> bool:
    ok, _ = shell("input keyevent 3", device)
    return ok


def keyevent(code: int, device: str) -> bool:
    ok, _ = shell(f"input keyevent {code}", device)
    return ok


def input_text(text: str, device: str) -> bool:
    ok, _ = shell(f"input text {text}", device)
    return ok


# ---------------------------------------------------------------------------
# UI dump + parse helpers
# ---------------------------------------------------------------------------

def dump_xml(device: str) -> str:
    """Dump the current UI hierarchy and return the XML string."""
    shell("uiautomator dump /sdcard/window_dump.xml", device, timeout=15)
    ok, xml = shell("cat /sdcard/window_dump.xml", device, timeout=15)
    if ok and xml.strip().startswith("<?xml"):
        return xml
    return ""


def save_dump(xml: str, name: str) -> Path:
    """Save xml to tmp_dumps/<name>.xml and return the path."""
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    path = DUMP_DIR / f"{name}.xml"
    path.write_text(xml, encoding="utf-8")
    print(f"  [dump] Saved {path.name} ({len(xml):,} bytes)")
    return path


def extract_resource_ids(xml: str) -> Set[str]:
    """Return all distinct resource-id values (non-empty, in this package)."""
    ids: Set[str] = set()
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            if rid and XHS_PACKAGE in rid:
                ids.add(rid)
    except ET.ParseError:
        pass
    return ids


def find_by_text(xml: str, text: str) -> Optional[Tuple[int, int]]:
    """Return center coordinates of the first node with exactly this text."""
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            if node.attrib.get("text", "") == text:
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                             node.attrib.get("bounds", ""))
                if m:
                    x1, y1, x2, y2 = (int(g) for g in m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    except ET.ParseError:
        pass
    return None


def find_by_text_contains(xml: str, text: str) -> Optional[Tuple[int, int]]:
    """Return center of first node whose text contains the given string."""
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            if text in node.attrib.get("text", ""):
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                             node.attrib.get("bounds", ""))
                if m:
                    x1, y1, x2, y2 = (int(g) for g in m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    except ET.ParseError:
        pass
    return None


def find_by_rid(xml: str, rid: str) -> Optional[Tuple[int, int]]:
    """Return center of the first node whose resource-id contains rid."""
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            if rid in node.attrib.get("resource-id", ""):
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                             node.attrib.get("bounds", ""))
                if m:
                    x1, y1, x2, y2 = (int(g) for g in m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    except ET.ParseError:
        pass
    return None


def find_by_content_desc_contains(xml: str, desc: str) -> Optional[Tuple[int, int]]:
    """Return center of first node whose content-desc contains desc."""
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            if desc in node.attrib.get("content-desc", ""):
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                             node.attrib.get("bounds", ""))
                if m:
                    x1, y1, x2, y2 = (int(g) for g in m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    except ET.ParseError:
        pass
    return None


def find_all_by_rid(xml: str, rid: str) -> List[Tuple[int, int]]:
    """Return center coords of ALL nodes whose resource-id contains rid."""
    coords = []
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            if rid in node.attrib.get("resource-id", ""):
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                             node.attrib.get("bounds", ""))
                if m:
                    x1, y1, x2, y2 = (int(g) for g in m.groups())
                    coords.append(((x1 + x2) // 2, (y1 + y2) // 2))
    except ET.ParseError:
        pass
    return coords


def wait_for_text(device: str, text: str, timeout: float = 6.0) -> bool:
    """Poll until text appears on screen or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        xml = dump_xml(device)
        if find_by_text(xml, text) or find_by_text_contains(xml, text):
            return True
        time.sleep(0.8)
    return False


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------

def wake_and_unlock(device: str):
    """Wake the screen and swipe to dismiss keyguard if locked."""
    shell("input keyevent 224", device)  # WAKEUP
    time.sleep(0.8)
    xml = dump_xml(device)
    if "com.android.systemui" in xml and "keyguard" in xml:
        shell("input swipe 540 1800 540 800 400", device)
        time.sleep(1.5)


def dismiss_login_popup(device: str) -> bool:
    """Close XHS login popup (HalfWelcomeActivity) if present.
    Returns True if popup was detected and dismissed."""
    xml = dump_xml(device)
    close_coord = find_by_rid(xml, "close")
    if close_coord and "HalfWelcome" in xml or (
        find_by_rid(xml, "mWeiChatLoginView") or find_by_rid(xml, "dialog")
    ):
        if close_coord:
            print("  [popup] 检测到登录弹窗，关闭...")
            tap(*close_coord, device)
            time.sleep(1.5)
            return True
        # Try pressing back
        back(device)
        time.sleep(1.0)
        return True
    return False


def launch_xhs(device: str) -> bool:
    """Launch 小红书 app and dismiss any login popup."""
    ok, _ = shell(f"am start -n {XHS_ACTIVITY}", device)
    if ok:
        time.sleep(3)
    dismiss_login_popup(device)
    return ok


def get_first_device() -> Optional[str]:
    """Return the first connected ADB device serial, or None."""
    try:
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        for line in r.stdout.splitlines()[1:]:
            line = line.strip()
            if line and "\t" in line:
                serial, status = line.split("\t", 1)
                if status.strip() == "device":
                    return serial.strip()
    except FileNotFoundError:
        pass
    return None


def tap_bottom_tab(xml: str, labels: List[str], device: str) -> bool:
    """
    Tap a bottom navigation tab by text or content-desc.
    Falls back to estimated coordinates (XHS has 4 tabs).
    NOTE: XHS bottom tab labels: 首页 / 视频 / (center +) / 消息 / 我
    """
    for label in labels:
        coord = find_by_text(xml, label)
        if not coord:
            coord = find_by_content_desc_contains(xml, label)
        if coord:
            print(f"  [nav] Found tab '{label}' at {coord}")
            tap(*coord, device)
            return True

    # Fallback: estimated tab positions (bottom bar y ≈ SCREEN_H - 80)
    tab_positions = {
        "首页": (SCREEN_W * 1 // 8, SCREEN_H - 80),
        "视频": (SCREEN_W * 3 // 8, SCREEN_H - 80),
        "消息": (SCREEN_W * 5 // 8, SCREEN_H - 80),
        "我":   (SCREEN_W * 7 // 8, SCREEN_H - 80),
    }
    for label in labels:
        if label in tab_positions:
            coord = tab_positions[label]
            print(f"  [nav] Using estimated position for '{label}': {coord}")
            tap(*coord, device)
            return True
    return False


# ---------------------------------------------------------------------------
# Exploration steps
# ---------------------------------------------------------------------------

def explore_home(device: str, all_ids: Set[str]) -> str:
    print("\n[1/7] 首页（发现流 Home）")
    xml = dump_xml(device)
    save_dump(xml, "01_home")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_search_results(device: str, all_ids: Set[str], home_xml: str) -> str:
    print("\n[2/7] 搜索结果页（Search Results）")
    # Try to find search button/box by text first (XHS often uses text "搜索")
    coord = (
        find_by_text_contains(home_xml, "搜索")
        or find_by_content_desc_contains(home_xml, "搜索")
        or find_by_rid(home_xml, "search")
    )
    if not coord:
        # Fallback: top-right area where search icon typically is
        coord = (SCREEN_W - 80, 80)
        print(f"  [WARN] 找不到搜索元素，使用 fallback 坐标 {coord}")

    tap(*coord, device)
    time.sleep(1.5)

    # Re-dump to get search input page
    xml_search_input = dump_xml(device)
    save_dump(xml_search_input, "02a_search_input")
    all_ids.update(extract_resource_ids(xml_search_input))

    # Type search keyword (pinyin to avoid CJK input issues)
    input_text("meishi", device)
    time.sleep(0.5)
    keyevent(66, device)  # Enter / search
    time.sleep(2.5)

    xml = dump_xml(device)
    save_dump(xml, "02_search_results")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_post_detail(device: str, all_ids: Set[str], feed_xml: str) -> str:
    print("\n[3/7] 帖子详情页（Post Detail）")

    # 第一层：按 resource-id 定位 feed card（coverArea = 封面图，最可靠点击目标）
    coord = find_by_rid(feed_xml, "coverArea") or find_by_rid(feed_xml, "card_view")
    if coord:
        print(f"  [info] 通过 resource-id 定位帖子 → {coord}")

    # 第二层：几何遍历，跳过已知导航元素
    if not coord:
        SKIP_RIDS = {"search", "index_home", "index_store", "index_post",
                     "index_message", "index_me", "tabs", "exploreTabLayoutV2",
                     "exploreTabLayoutContainer"}
        try:
            root = ET.fromstring(feed_xml)
            for node in root.iter("node"):
                rid_short = node.attrib.get("resource-id", "").split("/")[-1]
                if rid_short in SKIP_RIDS:
                    continue
                if node.attrib.get("clickable") == "true":
                    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                                 node.attrib.get("bounds", ""))
                    if m:
                        x1, y1, x2, y2 = (int(g) for g in m.groups())
                        cy, cx = (y1 + y2) // 2, (x1 + x2) // 2
                        if 250 < cy < SCREEN_H - 200 and (y2 - y1) > 150 and (x2 - x1) > 200:
                            coord = (cx, cy)
                            print(f"  [info] 几何定位帖子 → {coord}")
                            break
        except ET.ParseError:
            pass

    # 第三层：固定坐标 fallback（Tab 条以下约 700px 通常落在第一张帖子图上）
    if not coord:
        coord = (SCREEN_W // 2, 700)
        print(f"  [WARN] 使用 fallback 坐标 {coord}")

    tap(*coord, device)
    time.sleep(4.0)
    xml = dump_xml(device)

    # 验证导航成功；失败则重试一次
    still_home = ("exploreCoordinator" in xml and
                  "noteDetailRoot" not in xml and
                  "matrix_video_feed_note_detail_list" not in xml)
    if still_home:
        print(f"  [WARN] 仍在首页，重试点击 ({SCREEN_W // 2}, 700)...")
        tap(SCREEN_W // 2, 700, device)
        time.sleep(4.0)
        xml = dump_xml(device)

    save_dump(xml, "03_post_detail")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_blogger_profile(device: str, all_ids: Set[str], post_detail_xml: str) -> str:
    print("\n[4/7] 博主主页（Blogger Profile）")
    # Try to tap the author avatar/name — usually at top of post detail
    coord = (
        find_by_rid(post_detail_xml, "authorName")
        or find_by_rid(post_detail_xml, "author")
        or find_by_rid(post_detail_xml, "avatar")
        or find_by_rid(post_detail_xml, "nickname")
    )
    if not coord:
        # Fallback: top area of post detail (author info bar)
        coord = (200, 200)
        print(f"  [WARN] 找不到博主入口，使用 fallback 坐标 {coord}")

    tap(*coord, device)
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "04_blogger_profile")
    all_ids.update(extract_resource_ids(xml))

    back(device)
    time.sleep(1.0)
    return xml


def explore_messages(device: str, all_ids: Set[str]) -> str:
    print("\n[5/7] 消息页（Messages Tab）")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["消息", "Messages"], device)
    if not tapped:
        print("  [WARN] 无法切换到消息Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "05_messages")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_publish(device: str, all_ids: Set[str]) -> str:
    print("\n[6/7] 发布页（Publish / + Button）")
    xml = dump_xml(device)

    # The "+" publish button is in the center of the bottom navigation
    coord = (
        find_by_content_desc_contains(xml, "发布")
        or find_by_text(xml, "+")
        or find_by_text_contains(xml, "发布")
    )
    if not coord:
        # Fallback: center bottom
        coord = (SCREEN_W // 2, SCREEN_H - 80)
        print(f"  [WARN] 找不到发布按钮，使用 fallback 坐标 {coord}")

    tap(*coord, device)
    time.sleep(2.5)
    xml = dump_xml(device)
    save_dump(xml, "06_publish")
    all_ids.update(extract_resource_ids(xml))

    back(device)
    time.sleep(1.0)
    return xml


def explore_profile(device: str, all_ids: Set[str]) -> str:
    print("\n[7/7] 个人中心（Profile Tab）")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["我", "Me", "Profile"], device)
    if not tapped:
        print("  [WARN] 无法切换到个人中心Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "07_profile")
    all_ids.update(extract_resource_ids(xml))
    return xml


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(all_ids: Set[str], dumps_dir: Path):
    """Write a resource-id report grouped by page from dump filenames."""
    report_path = dumps_dir / "xhs_resource_ids_report.txt"

    # Collect per-file resource-ids for grouping
    per_page: Dict[str, Set[str]] = {}
    for xml_file in sorted(dumps_dir.glob("*.xml")):
        try:
            xml = xml_file.read_text(encoding="utf-8", errors="replace")
            ids = extract_resource_ids(xml)
            if ids:
                per_page[xml_file.stem] = ids
        except Exception:
            pass

    lines = [
        "=" * 70,
        "小红书 UI 探索报告 — resource-id 汇总",
        "=" * 70,
        f"总计唯一 resource-id: {len(all_ids)}",
        "",
    ]

    lines.append("── 按页面分组 ──")
    for page, ids in sorted(per_page.items()):
        lines.append(f"\n[{page}]  ({len(ids)} 个 resource-id)")
        for rid in sorted(ids):
            short = rid.replace(f"{XHS_PACKAGE}:id/", "")
            lines.append(f"  {short}")

    lines += [
        "",
        "── 全部唯一 resource-id（完整路径，字母排序）──",
    ]
    for rid in sorted(all_ids):
        lines.append(rid)

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[报告] 已保存 {report_path}")
    print(f"       发现 {len(all_ids)} 个唯一 resource-id，覆盖 {len(per_page)} 个页面")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="小红书 UI 全页面系统性探索脚本")
    parser.add_argument("--device", default=None,
                        help="ADB 设备 serial（默认取第一台已连接设备）")
    parser.add_argument("--no-launch", action="store_true",
                        help="跳过启动步骤（App 已在前台）")
    args = parser.parse_args()

    device = args.device
    if not device:
        device = get_first_device()
        if not device:
            print("ERROR: 未找到已连接的 ADB 设备，请连接设备或指定 --device")
            sys.exit(1)

    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    all_ids: Set[str] = set()

    print("小红书 UI 探索脚本")
    print(f"设备: {device}")
    print(f"输出目录: {DUMP_DIR}")
    print("=" * 60)

    # Step 0: Wake + Launch
    print("\n[0] 唤醒设备并启动小红书 App…")
    wake_and_unlock(device)
    if not args.no_launch:
        if not launch_xhs(device):
            print("  [WARN] launch 命令返回非零，继续尝试...")
        time.sleep(2)
        dismiss_login_popup(device)
        print("  ✓ 已发送启动命令")
    else:
        wake_and_unlock(device)
        dismiss_login_popup(device)
        time.sleep(1.0)

    # Step 1: Home
    home_xml = explore_home(device, all_ids)

    # Step 2: Search results
    search_xml = explore_search_results(device, all_ids, home_xml)

    # Go back to home for post detail exploration
    print("\n[nav] 返回首页...")
    for _ in range(3):
        back(device)
        time.sleep(0.5)
    time.sleep(1.0)
    home_xml2 = dump_xml(device)

    # Step 3: Post detail (from home feed)
    post_detail_xml = explore_post_detail(device, all_ids, home_xml2)

    # Step 4: Blogger profile (from post detail)
    if post_detail_xml:
        explore_blogger_profile(device, all_ids, post_detail_xml)

    # Return to home for tab exploration
    print("\n[nav] 返回首页准备 Tab 探索…")
    launch_xhs(device)
    time.sleep(2.0)

    # Step 5: Messages tab
    explore_messages(device, all_ids)

    # Return to home for publish
    print("\n[nav] 返回首页...")
    launch_xhs(device)
    time.sleep(2.0)

    # Step 6: Publish page
    explore_publish(device, all_ids)

    # Step 7: Profile tab
    explore_profile(device, all_ids)

    # Generate report
    generate_report(all_ids, DUMP_DIR)
    print("\n探索完成！")
    print(f"下一步：查看 {DUMP_DIR}/xhs_resource_ids_report.txt")


if __name__ == "__main__":
    main()
