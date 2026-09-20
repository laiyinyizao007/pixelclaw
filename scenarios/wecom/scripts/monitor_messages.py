#!/usr/bin/env python3
"""
monitor_messages.py — 企业微信消息监控（手动触发）

工作流：
  1. 获取设备锁（与 boss 打招呼脚本互斥，拿不到锁直接退出）
  2. 打开企业微信 → 逐个联系人（config/contacts.yaml）：
     搜索并打开会话 → dump 消息 → 与历史记录去重 → 写入 wecom_messages.db

使用方式：
  # 仅解析消息并打印，不写库
  python scenarios/wecom/scripts/monitor_messages.py --dry-run

  # 正式运行
  python scenarios/wecom/scripts/monitor_messages.py
"""

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parents[3]))

import yaml

from skills.android.adb_runner import ADBRunner
from skills.wecom import ChatMessage, WeComAutomationSkill
from utils.device_lock import DeviceBusyError, device_lock
from utils.logging_setup import setup_logger

REPO_ROOT = Path(__file__).parents[3]
SCENARIO_DIR = Path(__file__).parents[1]
CONTACTS_PATH = SCENARIO_DIR / "config" / "contacts.yaml"
DB_PATH = SCENARIO_DIR / "output" / "wecom_messages.db"

logger = setup_logger("wecom_monitor", str(REPO_ROOT / "logs" / "wecom"))


def _load_contacts() -> List[str]:
    if not CONTACTS_PATH.exists():
        return []
    data = yaml.safe_load(CONTACTS_PATH.read_text(encoding="utf-8")) or {}
    return [c["name"] for c in data.get("contacts", []) if c.get("name")]


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact TEXT NOT NULL,
            sender TEXT NOT NULL,
            time_str TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            msg_hash TEXT NOT NULL UNIQUE,
            first_seen_at TEXT NOT NULL
        )
        """
    )
    # 兼容已有库：按需补列
    for col, definition in [
        ("time_str", "TEXT NOT NULL DEFAULT ''"),
        ("url",      "TEXT NOT NULL DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {definition}")
        except Exception:
            pass
    conn.commit()


def _msg_hash(contact: str, msg: ChatMessage) -> str:
    raw = f"{contact}|{msg.sender}|{msg.text}|{msg.time_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _save_new_messages(
    conn: sqlite3.Connection, contact: str, messages: List[ChatMessage]
) -> int:
    """写入尚未记录过的消息，返回新增条数。"""
    count = 0
    now = datetime.now().isoformat()
    for msg in messages:
        if not msg.text:
            continue
        h = _msg_hash(contact, msg)
        try:
            conn.execute(
                "INSERT INTO messages (contact, sender, time_str, text, url, msg_hash, first_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (contact, msg.sender, msg.time_str, msg.text, msg.url, h, now),
            )
            count += 1
        except sqlite3.IntegrityError:
            continue  # 已存在，跳过
    conn.commit()
    return count


def run(dry_run: bool, device_id: str | None) -> int:
    contacts = _load_contacts()
    if not contacts:
        logger.warning("contacts.yaml 未配置任何联系人，退出")
        return 1

    adb = ADBRunner()
    skill = WeComAutomationSkill(adb, device_id=device_id)

    if not skill.launch():
        logger.error("企业微信启动失败")
        return 1

    conn = None
    if not dry_run:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        _ensure_tables(conn)

    total_new = 0

    for contact in contacts:
        logger.info("处理联系人: %s", contact)
        if not skill.open_conversation(contact):
            logger.warning("跳过（无法打开会话）: %s", contact)
            continue

        messages = skill.parse_messages()

        if dry_run:
            logger.info("[dry-run] %s 解析到 %d 条消息", contact, len(messages))
            for i, m in enumerate(messages, 1):
                url_suffix = f"  url={m.url}" if m.url else ""
                logger.info("  #%d [%s] %s: %s%s", i, m.time_str, m.sender, m.text, url_suffix)
        else:
            n = _save_new_messages(conn, contact, messages)
            total_new += n
            logger.info("%s: %d 条新消息已写入", contact, n)

        skill.press_back()

    skill.press_home()
    if conn:
        conn.close()

    if not dry_run:
        logger.info("完成，共 %d 条新消息写入库", total_new)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="企业微信消息监控")
    parser.add_argument("--dry-run", action="store_true", help="仅解析并打印，不写库")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    args = parser.parse_args()

    try:
        with device_lock(script_name="wecom_monitor_messages"):
            return run(dry_run=args.dry_run, device_id=args.device)
    except DeviceBusyError as exc:
        logger.warning("设备被占用，跳过本次运行: %s", exc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
