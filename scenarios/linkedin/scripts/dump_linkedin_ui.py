#!/usr/bin/env python3
"""
LinkedIn UI 全页面系统性探索脚本

功能：
  1. 逐一导航到 LinkedIn 的主要页面（首页/Jobs搜索/职位列表/职位详情/个人主页/消息）
  2. 在每个页面保存 UIAutomator XML dump 到 tmp_dumps/linkedin/
  3. 从所有 dump 中提取所有 resource-id，生成去重报告
  4. 探索通用搜索结果（全品类：职位/人脉/公司/帖子）
  5. 探索 AI 职位搜索功能

运行：
  # 原有全页面探索
  python scenarios/linkedin/scripts/dump_linkedin_ui.py --keyword "Product Manager"

  # 仅探索通用搜索 + AI 搜索（定位功能 UI 探索）
  python scenarios/linkedin/scripts/dump_linkedin_ui.py --mode positioning --query "5年AI产品经理"

  # 仅探索通用搜索
  python scenarios/linkedin/scripts/dump_linkedin_ui.py --mode general-search --query "AI Product Manager"

  # 指定设备
  python scenarios/linkedin/scripts/dump_linkedin_ui.py --mode positioning --query "PM" --device 42231JEKB04971
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
# Positioning exploration: general search + AI job search
# ---------------------------------------------------------------------------

def _back_to_home(device: str, max_retries: int = 5) -> str:
    """Press Back until the bottom navigation bar is visible (home screen)."""
    for i in range(max_retries):
        xml = dump_xml(device)
        if "tab_jobs" in xml or "tab_feed" in xml:
            return xml
        back(device)
        time.sleep(1.0)
    return dump_xml(device)


def _find_search_tab(xml: str, label: str) -> Optional[Tuple[int, int]]:
    """Find a search result tab (People, Companies, Posts, Jobs, All) by text or content-desc."""
    coord = find_by_text(xml, label)
    if not coord:
        coord = find_by_text_contains(xml, label)
    if not coord:
        coord = find_by_content_desc_contains(xml, label)
    return coord


def explore_general_search(device: str, all_ids: Set[str], query: str) -> str:
    """Explore LinkedIn's general search bar with a descriptive query.

    Dumps XML for each search result tab: All, People, Companies, Posts, Jobs.
    """
    print("\n" + "=" * 60)
    print("[通用搜索探索] General Search Exploration")
    print(f"  搜索词: {query}")
    print("=" * 60)

    # Step 1: Return to home screen
    print("\n  [1] 返回首页...")
    xml = _back_to_home(device)

    # Step 2: Tap the search bar
    print("  [2] 点击搜索栏...")
    coord = find_by_rid(xml, "search_bar")
    if not coord:
        coord = find_by_rid(xml, "search")
    if not coord:
        coord = find_by_text_contains(xml, "Search")
    if not coord:
        coord = find_by_content_desc_contains(xml, "Search")
    if not coord:
        print("  [ERROR] 找不到搜索栏，尝试坐标点击 (540, 120)")
        coord = (540, 120)

    tap(*coord, device)
    time.sleep(1.5)

    # Dump the search input/suggestions page
    xml_input = dump_xml(device)
    save_dump(xml_input, "20_search_input_page")
    all_ids.update(extract_resource_ids(xml_input))

    # Step 3: Type query and submit
    print(f"  [3] 输入搜索词: {query[:50]}{'...' if len(query) > 50 else ''}")
    type_text(query, device)
    time.sleep(0.5)
    keyevent(66, device)  # Enter
    time.sleep(4.0)

    # Step 4: Dump "All" results tab (default)
    print("  [4] Dump 全部结果 (All Results)...")
    xml_all = dump_xml(device)
    save_dump(xml_all, "21_general_search_all")
    all_ids.update(extract_resource_ids(xml_all))

    # Step 5: Scroll down once to see more results
    print("  [5] 向下滚动查看更多结果...")
    swipe(SCREEN_W // 2, 1600, SCREEN_W // 2, 600, 600, device)
    time.sleep(2.0)
    xml_scrolled = dump_xml(device)
    save_dump(xml_scrolled, "21b_general_search_all_scrolled")
    all_ids.update(extract_resource_ids(xml_scrolled))

    # Step 6: Switch to each tab and dump
    tabs_to_explore = [
        ("People",    "人脉",  "22_general_search_people"),
        ("Jobs",      "职位",  "23_general_search_jobs"),
        ("Companies", "公司",  "24_general_search_companies"),
        ("Posts",     "帖子",  "25_general_search_posts"),
        ("Services",  "服务",  "26_general_search_services"),
    ]

    for en_label, zh_label, dump_name in tabs_to_explore:
        print(f"  [6] 切换到 {en_label} ({zh_label}) tab...")
        xml_curr = dump_xml(device)
        coord = (_find_search_tab(xml_curr, en_label) or
                 _find_search_tab(xml_curr, zh_label))
        if coord:
            tap(*coord, device)
            time.sleep(3.0)
            xml_tab = dump_xml(device)
            save_dump(xml_tab, dump_name)
            all_ids.update(extract_resource_ids(xml_tab))

            # Scroll once for more results
            swipe(SCREEN_W // 2, 1600, SCREEN_W // 2, 600, 600, device)
            time.sleep(2.0)
            xml_tab_scrolled = dump_xml(device)
            save_dump(xml_tab_scrolled, f"{dump_name}_scrolled")
            all_ids.update(extract_resource_ids(xml_tab_scrolled))
        else:
            print(f"    [WARN] 未找到 {en_label}/{zh_label} tab，跳过")

    print("\n  [通用搜索探索完成]")
    return xml_all


def explore_ai_job_search(device: str, all_ids: Set[str], query: str) -> str:
    """Explore LinkedIn's AI-powered job search feature.

    Looks for the "尝试用 AI 进行职位搜索" prompt on the Jobs tab,
    then inputs a description and dumps the AI search results.
    """
    print("\n" + "=" * 60)
    print("[AI 职位搜索探索] AI Job Search Exploration")
    print(f"  描述: {query}")
    print("=" * 60)

    # Step 1: Navigate to Jobs tab
    print("\n  [1] 返回首页并导航到 Jobs tab...")
    xml = _back_to_home(device)
    tapped = tap_bottom_tab(xml, ["Jobs", "职位"], device, tab_index=2)
    if not tapped:
        print("  [ERROR] 找不到 Jobs tab")
        return ""
    time.sleep(2.5)
    xml = dump_xml(device)

    # Step 2: Look for AI search prompt
    print("  [2] 查找 AI 搜索入口...")
    ai_prompts = [
        "尝试用 AI 进行职位搜索",
        "Try AI job search",
        "AI job search",
        "尝试用AI进行职位搜索",
        "AI 搜索",
    ]

    coord = None
    for prompt in ai_prompts:
        coord = find_by_text_contains(xml, prompt)
        if coord:
            print(f"    找到 AI 搜索入口: '{prompt}' at {coord}")
            break
        coord = find_by_content_desc_contains(xml, prompt)
        if coord:
            print(f"    找到 AI 搜索入口 (content-desc): '{prompt}' at {coord}")
            break

    if not coord:
        print("  [WARN] 未在 Jobs tab 首页找到 AI 搜索入口")
        print("  [INFO] 尝试向下滚动查找...")
        swipe(SCREEN_W // 2, 1600, SCREEN_W // 2, 800, 600, device)
        time.sleep(2.0)
        xml = dump_xml(device)
        save_dump(xml, "30_jobs_tab_scrolled_for_ai")
        all_ids.update(extract_resource_ids(xml))

        for prompt in ai_prompts:
            coord = find_by_text_contains(xml, prompt)
            if not coord:
                coord = find_by_content_desc_contains(xml, prompt)
            if coord:
                print(f"    找到 AI 搜索入口: '{prompt}' at {coord}")
                break

    if not coord:
        print("  [ERROR] 未找到 AI 搜索入口，dump 当前页面供人工检查")
        save_dump(xml, "30_jobs_tab_no_ai_entry")
        all_ids.update(extract_resource_ids(xml))
        return xml

    # Step 3: Tap AI search entry point
    print("  [3] 点击 AI 搜索入口...")
    tap(*coord, device)
    time.sleep(2.5)

    xml_ai_input = dump_xml(device)
    save_dump(xml_ai_input, "31_ai_search_input_page")
    all_ids.update(extract_resource_ids(xml_ai_input))

    # Step 4: Type description
    print(f"  [4] 输入描述: {query[:50]}{'...' if len(query) > 50 else ''}")
    type_text(query, device)
    time.sleep(0.5)

    # Dump after typing (to see autocomplete/suggestions)
    xml_typing = dump_xml(device)
    save_dump(xml_typing, "32_ai_search_typing")
    all_ids.update(extract_resource_ids(xml_typing))

    # Press Enter / Submit
    keyevent(66, device)
    time.sleep(5.0)

    # Step 5: Dump AI search results
    print("  [5] Dump AI 搜索结果...")
    xml_results = dump_xml(device)
    save_dump(xml_results, "33_ai_search_results")
    all_ids.update(extract_resource_ids(xml_results))

    # Scroll for more results
    print("  [6] 向下滚动查看更多 AI 结果...")
    swipe(SCREEN_W // 2, 1600, SCREEN_W // 2, 600, 600, device)
    time.sleep(2.0)
    xml_scrolled = dump_xml(device)
    save_dump(xml_scrolled, "33b_ai_search_results_scrolled")
    all_ids.update(extract_resource_ids(xml_scrolled))

    print("\n  [AI 搜索探索完成]")
    return xml_results


def explore_deep_link_search(device: str, all_ids: Set[str], query: str) -> str:
    """Test if general search deep links work on LinkedIn Android app.

    Tries: https://www.linkedin.com/search/results/all/?keywords={query}
    """
    import urllib.parse

    print("\n" + "=" * 60)
    print("[Deep Link 搜索测试] Deep Link Search Test")
    print(f"  搜索词: {query}")
    print("=" * 60)

    encoded = urllib.parse.quote(query)

    # Enable app links
    shell("pm set-app-links --package com.linkedin.android 2 all", device)

    # Force-stop to ensure clean state
    shell(f"am force-stop {LINKEDIN_PACKAGE}", device)
    time.sleep(1.0)

    urls_to_test = [
        ("all",       f"https://www.linkedin.com/search/results/all/?keywords={encoded}"),
        ("people",    f"https://www.linkedin.com/search/results/people/?keywords={encoded}"),
        ("companies", f"https://www.linkedin.com/search/results/companies/?keywords={encoded}"),
    ]

    last_xml = ""
    for category, url in urls_to_test:
        print(f"\n  [测试] {category}: {url}")
        shell(f"am force-stop {LINKEDIN_PACKAGE}", device)
        time.sleep(1.0)
        shell(f'am start -a android.intent.action.VIEW -d "{url}"', device)
        time.sleep(5.0)

        xml = dump_xml(device)
        dump_name = f"40_deeplink_{category}"
        save_dump(xml, dump_name)
        all_ids.update(extract_resource_ids(xml))

        # Check if it landed on a search results page or just opened LinkedIn home
        has_bottom_nav = "tab_jobs" in xml or "tab_feed" in xml
        print(f"    底部导航栏: {'可见' if has_bottom_nav else '不可见'}")
        print(f"    XML 大小: {len(xml):,} bytes")
        last_xml = xml

    print("\n  [Deep Link 测试完成]")
    return last_xml


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
    parser.add_argument("--keyword", default=None,
                        help="搜索关键词（原有全页面探索模式必需）")
    parser.add_argument("--query", default=None,
                        help="通用搜索/AI搜索的描述文字（positioning 模式必需）")
    parser.add_argument("--device", default=None,
                        help="ADB 设备 serial（省略时自动选择）")
    parser.add_argument("--no-launch", action="store_true",
                        help="跳过启动步骤（App 已在前台）")
    parser.add_argument("--mode", default="all",
                        choices=["all", "general-search", "ai-search", "deeplink-test", "positioning"],
                        help="探索模式: all=全页面(默认), general-search=通用搜索, "
                             "ai-search=AI职位搜索, deeplink-test=深链测试, "
                             "positioning=通用+AI+深链(定位功能全套)")
    args = parser.parse_args()

    # Validate args based on mode
    if args.mode == "all" and not args.keyword:
        parser.error("--keyword is required for mode 'all'")
    if args.mode in ("general-search", "ai-search", "deeplink-test", "positioning"):
        if not args.query and not args.keyword:
            parser.error("--query (or --keyword) is required for this mode")

    query = args.query or args.keyword or ""
    device = resolve_device(args.device)
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    all_ids: Set[str] = set()

    print("LinkedIn UI 探索脚本")
    print(f"设备: {device}")
    print(f"模式: {args.mode}")
    if args.keyword:
        print(f"关键词: {args.keyword}")
    if args.query:
        print(f"搜索描述: {args.query}")
    print(f"输出目录: {DUMP_DIR}")
    print("=" * 60)
    print("[重要] 探索前请确认：")
    print("  1. LinkedIn App 已登录")
    print("  2. 当前显示 LinkedIn App（不在锁屏）")
    print("=" * 60)

    if not args.no_launch:
        print("\n[0] 启动 LinkedIn App...")
        if not launch_linkedin(device):
            print("  [ERROR] 启动失败")
            sys.exit(1)
        print("  App 已启动")
    else:
        time.sleep(1.0)

    if args.mode == "all":
        # Original full-page exploration
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

    elif args.mode == "general-search":
        explore_general_search(device, all_ids, query)

    elif args.mode == "ai-search":
        explore_ai_job_search(device, all_ids, query)

    elif args.mode == "deeplink-test":
        explore_deep_link_search(device, all_ids, query)

    elif args.mode == "positioning":
        # Full positioning exploration: deep link test + general search + AI search
        print("\n>>> 定位功能 UI 全套探索 <<<")

        print("\n--- Phase 1/3: Deep Link 可行性测试 ---")
        explore_deep_link_search(device, all_ids, query)

        print("\n--- Phase 2/3: 通用搜索栏探索 ---")
        launch_linkedin(device)
        time.sleep(2.0)
        explore_general_search(device, all_ids, query)

        print("\n--- Phase 3/3: AI 职位搜索探索 ---")
        launch_linkedin(device)
        time.sleep(2.0)
        explore_ai_job_search(device, all_ids, query)

    generate_report(all_ids, DUMP_DIR)
    print("\n探索完成！")


if __name__ == "__main__":
    main()
