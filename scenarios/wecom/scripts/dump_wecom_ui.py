#!/usr/bin/env python3
"""
企业微信 (WeCom) UI 全页面系统性探索脚本

功能：
  1. 逐一导航到企业微信的主要页面（会话列表 / 搜索页 / 搜索结果 /
     1-on-1 聊天 / 群聊）
  2. 在每个页面保存 UIAutomator XML dump 到 tmp_dumps/
  3. 从所有 dump 中提取所有 resource-id，生成去重、分页报告

运行：
  python scenarios/wecom/scripts/dump_wecom_ui.py --contact Avery
  python scenarios/wecom/scripts/dump_wecom_ui.py --contact 行业资讯 --device 42231JEKB04971
  python scenarios/wecom/scripts/dump_wecom_ui.py --contact Avery --no-launch
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
DUMP_DIR = ROOT / "tmp_dumps"
WECOM_PACKAGE = "com.tencent.wework"
ADBKEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
SCREEN_W = 1080
SCREEN_H = 2400

# Known stable IDs (verified from prior dumps)
SEARCH_ENTRY_ID = "n68"    # search button in conversation list header
SEARCH_INPUT_ID = "lhb"    # search EditText on search page
GROUP_CHAT_NAME = "行业资讯"  # group chat to explore for message_sender


# ---------------------------------------------------------------------------
# Low-level ADB helpers (self-contained, no project dependencies)
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


def back(device: str) -> bool:
    ok, _ = shell("input keyevent 4", device)
    return ok


def keyevent(code: int, device: str) -> bool:
    ok, _ = shell(f"input keyevent {code}", device)
    return ok


def type_text(text: str, device: str) -> bool:
    """Type text into the focused field.

    ASCII → plain `input text`.
    Non-ASCII → ADBKeyboard broadcast (requires com.android.adbkeyboard on device).
    """
    base = ["adb", "-s", device, "shell"]
    prev_ime = ""
    if text.isascii():
        args = base + ["input", "text", text.replace(" ", "%s")]
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
        print("ERROR: 未检测到已连接的设备，请先 adb connect 或指定 --device")
        sys.exit(1)
    if len(serials) > 1:
        print(f"ERROR: 检测到多台设备 {serials}，请用 --device 指定")
        sys.exit(1)
    return serials[0]


# ---------------------------------------------------------------------------
# UI dump + parse helpers
# ---------------------------------------------------------------------------

def dump_xml(device: str) -> str:
    shell("uiautomator dump /sdcard/window_dump.xml", device, timeout=15)
    ok, xml = shell("cat /sdcard/window_dump.xml", device, timeout=15)
    if ok and xml.strip().startswith("<?xml"):
        return xml
    # Strip leading non-XML prefix (e.g. "UI hierchary dumped to: ...")
    if ok and "<?xml" in xml:
        return xml[xml.index("<?xml"):]
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
            if rid and WECOM_PACKAGE in rid:
                ids.add(rid)
    except ET.ParseError:
        pass
    return ids


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


def list_nodes_with_text(xml: str) -> List[Dict]:
    """Return all nodes that have non-empty text and a resource-id."""
    result = []
    try:
        root = ET.fromstring(xml)
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            text = node.attrib.get("text", "")
            if rid and WECOM_PACKAGE in rid and text:
                result.append({
                    "rid": rid.replace(f"{WECOM_PACKAGE}:id/", ""),
                    "text": text,
                    "class": node.attrib.get("class", "").split(".")[-1],
                    "bounds": node.attrib.get("bounds", ""),
                    "clickable": node.attrib.get("clickable", "false"),
                })
    except ET.ParseError:
        pass
    return result


def wait_for_rid(device: str, rid: str, timeout: float = 6.0) -> bool:
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

def launch_wecom(device: str) -> bool:
    ok, _ = shell(
        f"monkey -p {WECOM_PACKAGE} -c android.intent.category.LAUNCHER 1",
        device
    )
    if ok:
        time.sleep(3)
    return ok


def go_to_conversation_list(device: str) -> bool:
    """Tap the '消息' tab to reach the conversation list."""
    xml = dump_xml(device)
    coord = find_by_text(xml, "消息")
    if coord:
        tap(*coord, device)
        time.sleep(1.5)
        return True
    return False


# ---------------------------------------------------------------------------
# Exploration steps
# ---------------------------------------------------------------------------

def explore_conversation_list(device: str, all_ids: Set[str]) -> str:
    print("\n[1/5] 会话列表页 (Conversation List)")
    xml = dump_xml(device)
    save_dump(xml, "wecom_01_conversation_list")
    all_ids.update(extract_resource_ids(xml))
    nodes = list_nodes_with_text(xml)
    print(f"  发现 {len(nodes)} 个带文本节点：")
    for n in nodes[:15]:
        print(f"    {n['rid']:8} | {n['class']:20} | click={n['clickable']} | {n['text'][:30]}")
    return xml


def explore_search_page(device: str, all_ids: Set[str], list_xml: str) -> str:
    print("\n[2/5] 搜索页 (Search Page)")
    coord = find_by_rid(list_xml, SEARCH_ENTRY_ID)
    if not coord:
        print(f"  [WARN] 找不到 search_entry (id/{SEARCH_ENTRY_ID})，跳过")
        return ""
    print(f"  [nav] tap search_entry at {coord}")
    tap(*coord, device)
    time.sleep(1.5)
    xml = dump_xml(device)
    save_dump(xml, "wecom_02_search_page")
    all_ids.update(extract_resource_ids(xml))
    nodes = list_nodes_with_text(xml)
    print(f"  发现 {len(nodes)} 个带文本节点：")
    for n in nodes:
        print(f"    {n['rid']:8} | {n['class']:20} | click={n['clickable']} | {n['text'][:30]}")
    return xml


def explore_search_results(device: str, all_ids: Set[str],
                           search_xml: str, contact: str) -> str:
    print(f"\n[3/5] 搜索结果页 (Search Results) — 联系人: {contact}")
    # Find and tap the search input
    coord = find_by_rid(search_xml, SEARCH_INPUT_ID)
    if not coord:
        # Try to find any EditText on the search page
        coord = find_by_rid(search_xml, "lhb")
    if not coord:
        print(f"  [WARN] 找不到 search_input (id/{SEARCH_INPUT_ID})，跳过")
        return ""
    print(f"  [nav] tap search_input at {coord}")
    tap(*coord, device)
    time.sleep(0.5)

    # Type the contact name
    print(f"  [input] 输入联系人名称: {contact}")
    if not type_text(contact, device):
        print("  [WARN] 输入可能失败（中文需设备安装 ADBKeyboard）")
    time.sleep(1.5)

    xml = dump_xml(device)
    save_dump(xml, "wecom_03_search_results")
    all_ids.update(extract_resource_ids(xml))

    print("  所有带文本节点（含 clickable）：")
    nodes = list_nodes_with_text(xml)
    for n in nodes:
        print(f"    {n['rid']:8} | {n['class']:20} | click={n['clickable']} | {n['text'][:40]}")

    print("\n  可点击节点（候选 session_item）：")
    for n in nodes:
        if n["clickable"] == "true":
            print(f"    >>> {n['rid']:8} | {n['text'][:40]}")
    return xml


def explore_1on1_chat(device: str, all_ids: Set[str], results_xml: str) -> str:
    print("\n[4/5] 1-on-1 聊天页 (1-on-1 Chat)")
    # Try clicking the first clickable result
    entered = False
    try:
        root = ET.fromstring(results_xml)
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            if WECOM_PACKAGE in rid and node.attrib.get("clickable") == "true":
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                             node.attrib.get("bounds", ""))
                if m:
                    x1, y1, x2, y2 = (int(g) for g in m.groups())
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    # Skip elements in top bar (y < 300)
                    if cy < 300:
                        continue
                    print(f"  [nav] tap first result at ({cx},{cy}) "
                          f"id/{rid.replace(WECOM_PACKAGE+':id/','')}")
                    tap(cx, cy, device)
                    entered = True
                    break
    except ET.ParseError:
        pass

    if not entered:
        print("  [WARN] 未找到可点击的搜索结果，跳过")
        return ""

    time.sleep(2.0)
    xml = dump_xml(device)
    save_dump(xml, "wecom_04_1on1_chat")
    all_ids.update(extract_resource_ids(xml))

    print("  聊天页带文本节点：")
    nodes = list_nodes_with_text(xml)
    for n in nodes:
        print(f"    {n['rid']:8} | {n['class']:20} | {n['text'][:40]}")
    return xml


def explore_group_chat(device: str, all_ids: Set[str]) -> str:
    print(f"\n[5/5] 群聊页 (Group Chat) — 寻找 {GROUP_CHAT_NAME}")
    # Go back to conversation list
    for _ in range(4):
        back(device)
        time.sleep(0.4)

    # Navigate to 消息 tab
    go_to_conversation_list(device)
    time.sleep(1.0)

    xml = dump_xml(device)
    coord = find_by_text(xml, GROUP_CHAT_NAME)
    if not coord:
        coord = find_by_text_contains(xml, GROUP_CHAT_NAME[:3])
    if not coord:
        print(f"  [WARN] 找不到群聊 '{GROUP_CHAT_NAME}'，尝试坐标估算")
        # Estimate: conversation list items start at y~590, each ~190px tall
        # 行业资讯 is roughly the 3rd item
        coord = (540, 830)

    print(f"  [nav] tap {GROUP_CHAT_NAME} at {coord}")
    tap(*coord, device)
    time.sleep(2.0)

    xml = dump_xml(device)
    save_dump(xml, "wecom_05_group_chat")
    all_ids.update(extract_resource_ids(xml))

    print("  群聊页带文本节点（重点关注发送人 resource-id）：")
    nodes = list_nodes_with_text(xml)
    for n in nodes:
        print(f"    {n['rid']:8} | {n['class']:20} | {n['text'][:40]}")
    return xml


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_report(all_ids: Set[str], dumps_dir: Path):
    report_path = dumps_dir / "wecom_resource_ids_report.txt"
    per_page: Dict[str, Set[str]] = {}
    for xml_file in sorted(dumps_dir.glob("wecom_*.xml")):
        try:
            xml = xml_file.read_text(encoding="utf-8", errors="replace")
            ids = extract_resource_ids(xml)
            per_page[xml_file.stem] = ids
        except Exception:
            pass

    lines = [
        "=" * 70,
        "企业微信 UI 探索报告 — resource-id 汇总",
        "=" * 70,
        f"总计唯一 resource-id: {len(all_ids)}",
        "",
        "── 按页面分组 ──",
    ]
    for page, ids in sorted(per_page.items()):
        lines.append(f"\n[{page}]  ({len(ids)} 个 resource-id)")
        for rid in sorted(ids):
            short = rid.replace(f"{WECOM_PACKAGE}:id/", "")
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
    parser = argparse.ArgumentParser(description="企业微信 UI 全页面系统性探索脚本")
    parser.add_argument("--contact", required=True,
                        help="搜索联系人/群名（用于探索搜索结果页）")
    parser.add_argument("--device", default=None,
                        help="ADB 设备 serial（省略时自动选择唯一连接的设备）")
    parser.add_argument("--no-launch", action="store_true",
                        help="跳过启动步骤（App 已在前台）")
    args = parser.parse_args()

    device = resolve_device(args.device)
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    all_ids: Set[str] = set()

    print("企业微信 UI 探索脚本")
    print(f"设备: {device}")
    print(f"搜索联系人: {args.contact}")
    print(f"输出目录: {DUMP_DIR}")
    print("=" * 60)

    if not args.no_launch:
        print("\n[0] 启动企业微信 App…")
        if not launch_wecom(device):
            print("  [ERROR] 启动失败")
            sys.exit(1)
        print("  ✓ App 已启动")
        go_to_conversation_list(device)
    else:
        time.sleep(1.0)

    # Step 1: Conversation list
    list_xml = explore_conversation_list(device, all_ids)

    # Step 2: Search page (tap search entry)
    search_xml = explore_search_page(device, all_ids, list_xml)

    # Step 3: Search results
    results_xml = ""
    if search_xml:
        results_xml = explore_search_results(device, all_ids, search_xml, args.contact)

    # Step 4: 1-on-1 chat (tap first search result)
    if results_xml:
        explore_1on1_chat(device, all_ids, results_xml)

    # Step 5: Group chat (行业资讯)
    explore_group_chat(device, all_ids)

    generate_report(all_ids, DUMP_DIR)
    print("\n探索完成！")
    print("\n重点关注项（填入 ELEMENTS）：")
    print("  search_entry:   com.tencent.wework:id/n68  [已确认]")
    print("  search_input:   com.tencent.wework:id/lhb  [已确认]")
    print("  message_text:   com.tencent.wework:id/j1l  [已确认]")
    print("  message_time:   com.tencent.wework:id/j2e  [已确认]")
    print("  session_item:   ← 查看 wecom_03_search_results.xml 中的可点击节点")
    print("  message_sender: ← 查看 wecom_05_group_chat.xml 中带发送人名的节点")


if __name__ == "__main__":
    main()
