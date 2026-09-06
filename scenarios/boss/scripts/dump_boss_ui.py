#!/usr/bin/env python3
"""
Boss直聘 UI 全页面系统性探索脚本

功能：
  1. 逐一导航到 Boss直聘 的所有主要页面（首页/搜索结果/筛选面板/职位详情/
     公司详情/聊天/推荐Tab/消息Tab/我的Tab/简历/投递记录）
  2. 在每个页面保存 UIAutomator XML dump 到 tmp_dumps/
  3. 从所有 dump 中提取所有 resource-id，生成去重、分页报告

运行：
  python scenarios/boss/scripts/dump_boss_ui.py [--device DEVICE_ID]
  python scenarios/boss/scripts/dump_boss_ui.py --device 42231JEKB04971
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
BOSS_PACKAGE = "com.hpbr.bosszhipin"
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
            if rid and BOSS_PACKAGE in rid:
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


def wait_for_text(device: str, text: str, timeout: float = 6.0) -> bool:
    """Poll until text appears on screen or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        xml = dump_xml(device)
        if find_by_text(xml, text) or find_by_text_contains(xml, text):
            return True
        time.sleep(0.8)
    return False


def wait_for_rid(device: str, rid: str, timeout: float = 6.0) -> bool:
    """Poll until a resource-id appears on screen or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        xml = dump_xml(device)
        if find_by_rid(xml, rid):
            return True
        time.sleep(0.8)
    return False


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------

def launch_boss(device: str) -> bool:
    """Launch Boss直聘 app."""
    ok, _ = shell(
        f"monkey -p {BOSS_PACKAGE} -c android.intent.category.LAUNCHER 1",
        device
    )
    if ok:
        time.sleep(3)
    return ok


def tap_bottom_tab(xml: str, labels: List[str], device: str) -> bool:
    """
    Tap a bottom navigation tab by its text or content-desc labels.
    Falls back to coordinate estimation (4 equal-width tabs).
    """
    for label in labels:
        coord = find_by_text(xml, label)
        if not coord:
            coord = find_by_content_desc_contains(xml, label)
        if coord:
            print(f"  [nav] Found tab '{label}' at {coord}")
            tap(*coord, device)
            return True

    # Fallback: estimate positions (tabs at y ~ SCREEN_H - 80)
    tab_positions = {
        "推荐":   (SCREEN_W * 1 // 8, SCREEN_H - 80),
        "职位":   (SCREEN_W * 3 // 8, SCREEN_H - 80),
        "消息":   (SCREEN_W * 5 // 8, SCREEN_H - 80),
        "我的":   (SCREEN_W * 7 // 8, SCREEN_H - 80),
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
    print("\n[1/11] 首页 (Home)")
    xml = dump_xml(device)
    save_dump(xml, "01_home")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_search_results(device: str, all_ids: Set[str], home_xml: str) -> str:
    print("\n[2/11] 搜索结果页 (Search Results)")
    coord = find_by_rid(home_xml, "et_search")
    if not coord:
        print("  [WARN] 找不到搜索框，尝试文本匹配")
        coord = find_by_text_contains(home_xml, "搜索")
    if not coord:
        print("  [ERROR] 无法定位搜索框，跳过")
        return home_xml

    tap(*coord, device)
    time.sleep(1.0)
    # type search keyword
    shell("input text Python", device)
    time.sleep(0.5)
    keyevent(66, device)  # Enter
    time.sleep(2.5)
    xml = dump_xml(device)
    save_dump(xml, "02_search_results")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_filter_panel(device: str, all_ids: Set[str], job_list_xml: str) -> str:
    print("\n[3/11] 筛选面板 (Filter Panel)")
    coord = find_by_rid(job_list_xml, "filterBarRightTabView")
    if not coord:
        coord = find_by_text_contains(job_list_xml, "筛选")
        if not coord:
            coord = find_by_content_desc_contains(job_list_xml, "筛选")
    if not coord:
        print("  [WARN] 找不到筛选按钮，跳过")
        return ""

    tap(*coord, device)
    time.sleep(1.5)  # wait for panel animation
    xml = dump_xml(device)
    save_dump(xml, "03_filter_panel")
    all_ids.update(extract_resource_ids(xml))
    # Close panel
    back(device)
    time.sleep(0.8)
    return xml


def explore_job_detail(device: str, all_ids: Set[str], job_list_xml: str) -> str:
    print("\n[4/11] 职位详情页 (Job Detail)")
    coord = find_by_rid(job_list_xml, "tv_position_name")
    if not coord:
        print("  [WARN] 找不到职位列表条目，跳过")
        return ""

    tap(*coord, device)
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "04_job_detail")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_company_detail(device: str, all_ids: Set[str], job_detail_xml: str) -> str:
    print("\n[5/11] 公司详情页 (Company Detail)")
    # Try common company name resource-ids in the detail page
    for rid in ("tv_company_name", "company_name", "iv_company_logo"):
        coord = find_by_rid(job_detail_xml, rid)
        if coord:
            tap(*coord, device)
            time.sleep(2.0)
            xml = dump_xml(device)
            save_dump(xml, "05_company_detail")
            all_ids.update(extract_resource_ids(xml))
            back(device)
            time.sleep(1.0)
            return xml
    print("  [WARN] 找不到公司名入口，跳过")
    return ""


def explore_chat(device: str, all_ids: Set[str], job_detail_xml: str) -> str:
    print("\n[6/11] 聊天页 (Chat)")
    coord = find_by_rid(job_detail_xml, "btn_chat")
    if not coord:
        coord = find_by_text(job_detail_xml, "立即沟通")
    if not coord:
        print("  [WARN] 找不到立即沟通按钮，跳过")
        return ""

    tap(*coord, device)
    time.sleep(2.5)
    xml = dump_xml(device)
    save_dump(xml, "06_chat")
    all_ids.update(extract_resource_ids(xml))

    # Also try to tap "+" to reveal more chat functions
    print("  [6b] 聊天页更多功能面板")
    xml_current = xml
    more_coord = (
        find_by_rid(xml_current, "iv_more")
        or find_by_content_desc_contains(xml_current, "更多")
        or find_by_text(xml_current, "+")
    )
    if more_coord:
        tap(*more_coord, device)
        time.sleep(1.0)
        xml_more = dump_xml(device)
        save_dump(xml_more, "06b_chat_more_panel")
        all_ids.update(extract_resource_ids(xml_more))
        back(device)
        time.sleep(0.5)

    back(device)
    time.sleep(0.8)
    back(device)
    time.sleep(0.8)
    return xml


def explore_tab_recommend(device: str, all_ids: Set[str]) -> str:
    print("\n[7/11] 推荐Tab (Recommend Tab)")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["推荐", "首页", "Recommend"], device)
    if not tapped:
        print("  [WARN] 无法切换到推荐Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "07_tab_recommend")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_tab_messages(device: str, all_ids: Set[str]) -> str:
    print("\n[8/11] 消息Tab (Messages Tab)")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["消息", "Messages"], device)
    if not tapped:
        print("  [WARN] 无法切换到消息Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "08_messages_tab")
    all_ids.update(extract_resource_ids(xml))

    # If there are chat entries, tap the first one
    first_chat = find_by_rid(xml, "tv_chat_name")
    if not first_chat:
        # Try common patterns for chat list items
        for rid_pattern in ("chat_name", "chat_item", "item_name", "boss_name"):
            first_chat = find_by_rid(xml, rid_pattern)
            if first_chat:
                break

    if first_chat:
        print("  [8b] 进入消息列表中的聊天")
        tap(*first_chat, device)
        time.sleep(2.0)
        xml_chat = dump_xml(device)
        save_dump(xml_chat, "08b_messages_chat_from_list")
        all_ids.update(extract_resource_ids(xml_chat))
        back(device)
        time.sleep(0.8)
    else:
        print("  [INFO] 消息列表为空或找不到聊天条目")

    return xml


def explore_tab_profile(device: str, all_ids: Set[str]) -> str:
    print("\n[9/11] 我的Tab (Profile Tab)")
    xml = dump_xml(device)
    tapped = tap_bottom_tab(xml, ["我的", "Profile", "Me"], device)
    if not tapped:
        print("  [WARN] 无法切换到我的Tab")
        return ""
    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "09_profile_tab")
    all_ids.update(extract_resource_ids(xml))
    return xml


def explore_resume(device: str, all_ids: Set[str], profile_xml: str) -> str:
    print("\n[10/11] 简历页 (Resume)")
    if not profile_xml:
        return ""
    for candidate in ("tv_resume", "rl_resume", "简历", "我的简历"):
        coord = find_by_rid(profile_xml, candidate) or find_by_text_contains(profile_xml, candidate)
        if coord:
            tap(*coord, device)
            time.sleep(2.0)
            xml = dump_xml(device)
            save_dump(xml, "10_resume_page")
            all_ids.update(extract_resource_ids(xml))
            back(device)
            time.sleep(0.8)
            return xml
    print("  [WARN] 找不到简历入口，跳过")
    return ""


def explore_applications(device: str, all_ids: Set[str], profile_xml: str) -> str:
    print("\n[11/11] 投递记录页 (Application History)")
    if not profile_xml:
        return ""
    for candidate in ("tv_delivery_record", "rl_delivery", "投递记录", "已投递"):
        coord = (
            find_by_rid(profile_xml, candidate)
            or find_by_text_contains(profile_xml, candidate)
        )
        if coord:
            tap(*coord, device)
            time.sleep(2.0)
            xml = dump_xml(device)
            save_dump(xml, "11_applications_page")
            all_ids.update(extract_resource_ids(xml))
            back(device)
            time.sleep(0.8)
            return xml
    print("  [WARN] 找不到投递记录入口，跳过")
    return ""


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(all_ids: Set[str], dumps_dir: Path):
    """Write a resource-id report grouped by page from dump filenames."""
    report_path = dumps_dir / "resource_ids_report.txt"

    # Collect per-file resource-ids for grouping
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
        "Boss直聘 UI 探索报告 — resource-id 汇总",
        "=" * 70,
        f"总计唯一 resource-id: {len(all_ids)}",
        "",
    ]

    lines.append("── 按页面分组 ──")
    for page, ids in sorted(per_page.items()):
        lines.append(f"\n[{page}]  ({len(ids)} 个 resource-id)")
        for rid in sorted(ids):
            # strip package prefix for readability
            short = rid.replace(f"{BOSS_PACKAGE}:id/", "")
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
    parser = argparse.ArgumentParser(description="Boss直聘 UI 全页面系统性探索脚本")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="ADB 设备 serial")
    parser.add_argument("--no-launch", action="store_true",
                        help="跳过启动步骤（App 已在前台）")
    args = parser.parse_args()

    device = args.device
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    all_ids: Set[str] = set()

    print(f"Boss直聘 UI 探索脚本")
    print(f"设备: {device}")
    print(f"输出目录: {DUMP_DIR}")
    print("=" * 60)

    # Step 0: Launch
    if not args.no_launch:
        print("\n[0] 启动 Boss直聘 App…")
        if not launch_boss(device):
            print("  [ERROR] 启动失败")
            sys.exit(1)
        print("  ✓ App 已启动")
    else:
        time.sleep(1.0)

    # Step 1: Home
    home_xml = explore_home(device, all_ids)

    # Step 2: Search results
    job_list_xml = explore_search_results(device, all_ids, home_xml)

    # Step 3: Filter panel (open and close)
    if job_list_xml:
        explore_filter_panel(device, all_ids, job_list_xml)

    # Step 4: Job detail
    job_detail_xml = ""
    if job_list_xml:
        # Re-dump in case filter panel changed the XML
        current_xml = dump_xml(device)
        job_detail_xml = explore_job_detail(device, all_ids, current_xml)

    # Step 5: Company detail (from inside job detail)
    if job_detail_xml:
        explore_company_detail(device, all_ids, job_detail_xml)
        # Re-read detail XML after returning from company page
        job_detail_xml = dump_xml(device)

    # Step 6: Chat (from job detail)
    if job_detail_xml:
        explore_chat(device, all_ids, job_detail_xml)

    # Now navigate back to home for tab exploration
    print("\n[nav] 返回首页准备 Tab 探索…")
    for _ in range(5):
        back(device)
        time.sleep(0.5)
    launch_boss(device)
    time.sleep(2.0)

    # Step 7: Recommend tab
    explore_tab_recommend(device, all_ids)

    # Step 8: Messages tab
    explore_tab_messages(device, all_ids)

    # Step 9: Profile tab
    profile_xml = explore_tab_profile(device, all_ids)

    # Step 10: Resume page (from profile)
    if profile_xml:
        explore_resume(device, all_ids, profile_xml)
        # Return to profile
        profile_xml = dump_xml(device)

    # Step 11: Applications page (from profile)
    if profile_xml:
        explore_applications(device, all_ids, profile_xml)

    # Generate report
    generate_report(all_ids, DUMP_DIR)
    print("\n探索完成！")


if __name__ == "__main__":
    main()
