#!/usr/bin/env python3
"""
LinkedIn UI 全页面系统性探索脚本

功能：
  1. 逐一导航到 LinkedIn 的主要页面（首页/Jobs搜索/职位列表/职位详情/个人主页/消息）
  2. 在每个页面保存 UIAutomator XML dump 到 tmp_dumps/linkedin/
  3. 从所有 dump 中提取所有 resource-id，生成去重报告

运行：
  python scenarios/linkedin/scripts/dump_linkedin_ui.py --keyword "Product Manager"
  python scenarios/linkedin/scripts/dump_linkedin_ui.py --keyword "PM" --device 42231JEKB04971
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

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parents[3]
DUMP_DIR = ROOT / "tmp_dumps" / "linkedin"
LINKEDIN_PACKAGE = "com.linkedin.android"
ADBKEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
SCREEN_W = 1080
SCREEN_H = 2400


# ---------------------------------------------------------------------------
# Low-level ADB helpers
# ---------------------------------------------------------------------------

def _adb(cmd: str, device: str, timeout: int = 30) -> Tuple[bool, str]:
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


def type_text(text: str, device: str) -> bool:
    base = ["adb", "-s", device, "shell"]
    if text.isascii():
        args = base + ["input", "text", text.replace(" ", "%s")]
        prev_ime = ""
    else:
        _, prev_ime = shell("settings get secure default_input_method", device)
        shell(f"ime enable {ADBKEYBOARD_IME}", device)
        shell(f"ime set {ADBKEYBOARD_IME}", device)
        time.sleep(0.3)
        args = base + ["am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text]
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    if prev_ime.strip():
        shell(f"ime set {prev_ime.strip()}", device)
    return ok


def resolve_device(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    r = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    serials = [ln.split()[0] for ln in r.stdout.splitlines()[1:]
               if ln.strip().endswith("device")]
    if not serials:
        print("ERROR: 未检测到已连接的设备")
        sys.exit(1)
    if len(serials) > 1:
        print(f"ERROR: 多台设备 {serials}，请用 --device 指定")
        sys.exit(1)
    return serials[0]


def back(device: str) -> bool:
    ok, _ = shell("input keyevent 4", device)
    return ok


def keyevent(code: int, device: str) -> bool:
    ok, _ = shell(f"input keyevent {code}", device)
    return ok


# ---------------------------------------------------------------------------
# UI dump + parse helpers
# ---------------------------------------------------------------------------

def dump_xml(device: str) -> str:
    shell("uiautomator dump /sdcard/window_dump.xml", device, timeout=15)
    ok, xml = shell("cat /sdcard/window_dump.xml", device, timeout=15)
    if ok and xml.strip().startswith("<?xml"):
        return xml
    return ""


def save_dump(xml: str, name: str) -> Path:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    path = DUMP_DIR / f"{name}.xml"
    path.write_text(xml, encoding="utf-8")
    print(f"  [dump] Saved {path.name} ({len(xml):,} bytes)")
    return path


def extract_resource_ids(xml: str) -> Set[str]:
    ids: Set[str] = set()
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            if rid and LINKEDIN_PACKAGE in rid:
                ids.add(rid)
    except ET.ParseError:
        pass
    return ids


def find_by_text(xml: str, text: str) -> Optional[Tuple[int, int]]:
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


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------

def launch_linkedin(device: str) -> bool:
    ok, _ = shell(
        f"monkey -p {LINKEDIN_PACKAGE} -c android.intent.category.LAUNCHER 1",
        device
    )
    if ok:
        time.sleep(4)
    return ok


def tap_bottom_tab(xml: str, labels: List[str], device: str, tab_index: int = -1) -> bool:
    """
    Tap a bottom navigation tab by text/content-desc, or by index (0-based, 5 tabs total).
    LinkedIn has 5 bottom tabs: Home(0), My Network(1), Jobs(2), Messaging(3), Me(4).
    """
    for label in labels:
        coord = find_by_text(xml, label)
        if not coord:
            coord = find_by_content_desc_contains(xml, label)
        if coord:
            print(f"  [nav] Found tab '{label}' at {coord}")
            tap(*coord, device)
            return True

    if tab_index >= 0:
        tab_y = SCREEN_H - 80
        tab_x = SCREEN_W * (2 * tab_index + 1) // 10  # 5 tabs, evenly spaced
        print(f"  [nav] Using estimated position for tab {tab_index}: ({tab_x}, {tab_y})")
        tap(tab_x, tab_y, device)
        return True

    return False


# ---------------------------------------------------------------------------
# Exploration steps
# ---------------------------------------------------------------------------

def explore_home(device: str, all_ids: Set[str]) -> str:
    print("\n[1/8] 首页 (Home Tab)")
    xml = dump_xml(device)
    save_dump(xml, "01_home")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_jobs_tab(device: str, all_ids: Set[str]) -> str:
    print("\n[2/8] Jobs Tab")
    xml = dump_xml(device)
    # LinkedIn Jobs tab is the 3rd tab (index 2)
    tapped = tap_bottom_tab(xml, ["Jobs", "职位", "Find a job"], device, tab_index=2)
    if not tapped:
        print("  [WARN] 找不到 Jobs Tab，跳过")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "02_jobs_tab")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_job_search(device: str, all_ids: Set[str], jobs_xml: str, keyword: str) -> str:
    print(f"\n[3/8] 职位搜索 (Job Search) — 关键词: {keyword}")
    # Try to find search box by common content-desc or text
    coord = (find_by_text_contains(jobs_xml, "Search jobs") or
             find_by_text_contains(jobs_xml, "Search") or
             find_by_content_desc_contains(jobs_xml, "Search jobs") or
             find_by_rid(jobs_xml, "search"))
    if not coord:
        print("  [WARN] 找不到搜索框，跳过")
        return jobs_xml

    tap(*coord, device)
    time.sleep(1.5)
    type_text(keyword, device)
    time.sleep(0.5)
    keyevent(66, device)  # Enter
    time.sleep(3.0)
    xml = dump_xml(device)
    save_dump(xml, "03_job_search_results")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_job_detail(device: str, all_ids: Set[str], search_xml: str) -> str:
    print("\n[4/8] 职位详情页 (Job Detail)")
    # Try multiple ways to find first job in list
    coord = None
    for hint in ("job_title", "title", "position", "Job title"):
        coord = find_by_rid(search_xml, hint) or find_by_text_contains(search_xml, hint)
        if coord:
            break
    # Fallback: tap first clickable item in middle of screen
    if not coord:
        coord = (SCREEN_W // 2, 600)
        print(f"  [WARN] 找不到职位卡片，尝试点击屏幕中央: {coord}")

    tap(*coord, device)
    time.sleep(2.5)
    xml = dump_xml(device)
    save_dump(xml, "04_job_detail")
    all_ids.update(extract_resource_ids(xml))

    # Try to expand "Show more" / "See more"
    print("  [4b] 尝试展开职位描述 (See more)")
    see_more = (find_by_text(xml, "Show more") or
                find_by_text(xml, "See more") or
                find_by_text_contains(xml, "Show more") or
                find_by_content_desc_contains(xml, "Show more"))
    if see_more:
        tap(*see_more, device)
        time.sleep(1.0)
        xml_expanded = dump_xml(device)
        save_dump(xml_expanded, "04b_job_detail_expanded")
        all_ids.update(extract_resource_ids(xml_expanded))
    else:
        print("  [INFO] 未找到 Show more 按钮（描述可能未折叠）")

    # Scroll down to see all fields
    print("  [4c] 向下滚动获取更多信息")
    swipe(SCREEN_W // 2, 1600, SCREEN_W // 2, 800, 600, device)
    time.sleep(1.5)
    xml_bottom = dump_xml(device)
    save_dump(xml_bottom, "04c_job_detail_scrolled")
    all_ids.update(extract_resource_ids(xml_bottom))

    back(device)
    time.sleep(1.0)
    return xml


def explore_messaging_tab(device: str, all_ids: Set[str]) -> str:
    print("\n[5/8] 消息 Tab (Messaging)")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["Messaging", "消息"], device, tab_index=3)
    if not tapped:
        print("  [WARN] 找不到 Messaging Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "05_messaging_tab")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_profile_tab(device: str, all_ids: Set[str]) -> str:
    print("\n[6/8] 我的主页 (Me/Profile Tab)")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["Me", "我"], device, tab_index=4)
    if not tapped:
        print("  [WARN] 找不到 Me Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "06_profile_tab")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_network_tab(device: str, all_ids: Set[str]) -> str:
    print("\n[7/8] 我的网络 Tab (My Network)")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["My Network", "我的人脉"], device, tab_index=1)
    if not tapped:
        print("  [WARN] 找不到 My Network Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "07_network_tab")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_notifications(device: str, all_ids: Set[str]) -> str:
    print("\n[8/8] 通知 Tab (Notifications) — 尝试查找")
    xml = dump_xml(device)
    # LinkedIn has Notifications as the bell icon, sometimes between Network and Jobs
    coord = find_by_content_desc_contains(xml, "Notifications")
    if coord:
        tap(*coord, device)
        time.sleep(2.0)
        xml = dump_xml(device)
        save_dump(xml, "08_notifications")
        all_ids.update(extract_resource_ids(xml))
    else:
        print("  [INFO] 通知 Tab 未在当前屏幕找到，跳过")
    return xml


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(all_ids: Set[str], dumps_dir: Path):
    report_path = dumps_dir / "resource_ids_report.txt"

    per_page: Dict[str, Set[str]] = {}
    for xml_file in sorted(dumps_dir.glob("*.xml")):
        try:
            xml = xml_file.read_text(encoding="utf-8", errors="replace")
            ids = extract_resource_ids(xml)
            per_page[xml_file.stem] = ids
        except Exception:
            pass

    lines = [
        "=" * 70,
        "LinkedIn UI 探索报告 — resource-id 汇总",
        "=" * 70,
        f"总计唯一 resource-id: {len(all_ids)}",
        "",
        "── 按页面分组 ──",
    ]

    for page, ids in sorted(per_page.items()):
        lines.append(f"\n[{page}]  ({len(ids)} 个 resource-id)")
        for rid in sorted(ids):
            short = rid.replace(f"{LINKEDIN_PACKAGE}:id/", "")
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
    print("\n[提示] 将重要的 resource-id 填写到 skills/linkedin/linkedin_automation_skill.py 的 ELEMENTS 字典中")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LinkedIn UI 全页面系统性探索脚本")
    parser.add_argument("--keyword", required=True, help="搜索关键词（必需）")
    parser.add_argument("--device", default=None,
                        help="ADB 设备 serial（省略时自动选择）")
    parser.add_argument("--no-launch", action="store_true",
                        help="跳过启动步骤（App 已在前台）")
    args = parser.parse_args()

    device = resolve_device(args.device)
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    all_ids: Set[str] = set()

    print("LinkedIn UI 探索脚本")
    print(f"设备: {device}")
    print(f"关键词: {args.keyword}")
    print(f"输出目录: {DUMP_DIR}")
    print("=" * 60)
    print("[重要] 探索前请确认：")
    print("  1. LinkedIn App 已登录")
    print("  2. 当前显示 LinkedIn App（不在锁屏）")
    print("=" * 60)

    if not args.no_launch:
        print("\n[0] 启动 LinkedIn App…")
        if not launch_linkedin(device):
            print("  [ERROR] 启动失败")
            sys.exit(1)
        print("  ✓ App 已启动")
    else:
        time.sleep(1.0)

    home_xml = explore_home(device, all_ids)
    jobs_xml = explore_jobs_tab(device, all_ids)
    search_xml = explore_job_search(device, all_ids, jobs_xml or home_xml, args.keyword)
    explore_job_detail(device, all_ids, search_xml)

    launch_linkedin(device)
    time.sleep(2.0)

    explore_messaging_tab(device, all_ids)
    explore_profile_tab(device, all_ids)

    launch_linkedin(device)
    time.sleep(2.0)

    explore_network_tab(device, all_ids)
    explore_notifications(device, all_ids)

    generate_report(all_ids, DUMP_DIR)
    print("\n探索完成！")


if __name__ == "__main__":
    main()
