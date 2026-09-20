#!/usr/bin/env python3
"""
probe_elements.py — 企业微信 UI 元素探测工具

使用方式：
  # 1. 将企业微信切换到目标界面（会话列表页 或 某个聊天页）
  # 2. 运行本脚本，输出所有 resource-id 和对应文本
  python scenarios/wecom/scripts/probe_elements.py

  # 指定设备
  python scenarios/wecom/scripts/probe_elements.py --device <serial>

  # 仅输出非空 resource-id
  python scenarios/wecom/scripts/probe_elements.py --ids-only

  # 把 XML 保存到本地查看
  python scenarios/wecom/scripts/probe_elements.py --save-xml probe_dump.xml

探测流程：
  会话列表页 → 运行 → 记录 search_entry / session_item 的 resource-id
  打开某个聊天 → 运行 → 记录 message_sender / message_text / message_time
  进入搜索输入框 → 运行 → 记录 search_input 的 resource-id

结果填入 skills/wecom/wecom_automation_skill.py 的 ELEMENTS 字典。
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from skills.android.adb_runner import ADBRunner


def _dump_xml(adb: ADBRunner, device_id: str | None) -> str:
    adb.shell("shell uiautomator dump /sdcard/window_dump.xml", device_id)
    ok, content = adb.shell("shell cat /sdcard/window_dump.xml", device_id)
    if not ok or not content.strip():
        print("ERROR: UI dump 失败，请确认设备已连接且 USB 调试已开启", file=sys.stderr)
        sys.exit(1)
    return content


def _print_elements(xml: str, ids_only: bool) -> None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        print(f"ERROR: XML 解析失败: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = []
    for node in root.iter("node"):
        rid = node.attrib.get("resource-id", "")
        text = node.attrib.get("text", "")
        cdesc = node.attrib.get("content-desc", "")
        bounds = node.attrib.get("bounds", "")
        cls = node.attrib.get("class", "").rsplit(".", 1)[-1]

        if ids_only and not rid:
            continue

        label = text or cdesc
        rows.append((rid, cls, label, bounds))

    # 去重 + 排序（按 resource-id）
    seen = set()
    unique = []
    for r in rows:
        key = (r[0], r[2])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(key=lambda x: (x[0], x[2]))

    col_w = [max(len(r[i]) for r in unique) if unique else 10 for i in range(4)]
    header = (
        f"{'resource-id':<{col_w[0]}}  {'class':<{col_w[1]}}  "
        f"{'text/content-desc':<{col_w[2]}}  {'bounds'}"
    )
    print(header)
    print("-" * len(header))
    for rid, cls, label, bounds in unique:
        print(f"{rid:<{col_w[0]}}  {cls:<{col_w[1]}}  {label:<{col_w[2]}}  {bounds}")

    print(f"\n共 {len(unique)} 条{'（含空 resource-id）' if not ids_only else ''}")
    print("\n--- 填入 ELEMENTS 参考 ---")
    print("请根据上表找到对应元素，将 resource-id 复制到 skills/wecom/wecom_automation_skill.py:")
    for key in ("search_entry", "search_input", "session_item",
                "message_sender", "message_text", "message_time"):
        print(f"  '{key}': '',  # <-- 填入对应的 resource-id")


def main() -> None:
    parser = argparse.ArgumentParser(description="企业微信 UI 元素探测")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    parser.add_argument("--ids-only", action="store_true", help="仅输出有 resource-id 的节点")
    parser.add_argument("--save-xml", default=None, metavar="FILE", help="同时把 XML 保存到本地文件")
    args = parser.parse_args()

    adb = ADBRunner()
    xml = _dump_xml(adb, args.device)

    if args.save_xml:
        Path(args.save_xml).write_text(xml, encoding="utf-8")
        print(f"XML 已保存到: {args.save_xml}\n")

    _print_elements(xml, ids_only=args.ids_only)


if __name__ == "__main__":
    main()
