"""
最小诊断：直接对真实 Boss app 做几次不同 duration 的 swipe，每次 dump
首张可见职位卡片（tv_position_name）的 y 坐标，确认 swipe 在这台设备上
是否真生效，以及 fling 持续时间和距离的最佳值。

不需要搜索 / 评分 / 打招呼，只是验证 swipe → 列表滚动 这一段。

运行前提：
- 设备已连接，adb 可用
- Boss app 当前在职位列表页（不在首页 / 详情页 / 弹窗）

运行：
    PYTHONUTF8=1 python scenarios/boss/scripts/diag_swipe.py
"""
from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from skills.android.adb_runner import ADBRunner
from skills.boss.boss_automation_skill import BOSSAutomationSkill


CARD_RID = "com.hpbr.bosszhipin:id/tv_position_name"
SCREEN_W = 540
SCREEN_H = 2400


def get_top_card_y(skill: BOSSAutomationSkill) -> tuple[int, str]:
    """返回 (y, title) —— 当前第一张可见卡片的中心 y 和标题。"""
    xml = skill.get_ui_hierarchy()
    el = skill.find_element(resource_id=CARD_RID, xml=xml)
    if el is None:
        return (-1, "")
    title = ""
    for node in ET.fromstring(xml).iter("node"):
        if node.attrib.get("resource-id", "") == CARD_RID:
            title = node.attrib.get("text", "")
            break
    bounds = el.bounds  # (x1, y1, x2, y2)
    center_y = (bounds[1] + bounds[3]) // 2
    return (center_y, title)


def do_swipe(skill: BOSSAutomationSkill, duration_ms: int) -> None:
    """finger top→bottom (content scrolls up)。"""
    skill.swipe(SCREEN_W, int(SCREEN_H * 0.15), SCREEN_W, int(SCREEN_H * 0.85), duration_ms)
    time.sleep(1.2)  # 等滚动停下


def main() -> int:
    adb = ADBRunner()
    skill = BOSSAutomationSkill(adb, action_delay=0.3)

    # 0. 校验当前页面是职位列表
    page = skill.get_current_page()
    print(f"[diag] current_page = {page}")
    if page not in ("job_list", "recommend"):
        print(f"[diag] 错误：当前不在职位列表页（page={page}），请手动打开 Boss 并搜索关键词")
        return 1

    # 1. 基线 y
    base_y, base_title = get_top_card_y(skill)
    print(f"[diag] 基线 首卡 y={base_y} title={base_title!r}")

    # 2. 测多组 (duration, repeats) 组合
    cases = [
        ("100ms × 5",  100, 5),
        ("200ms × 5",  200, 5),
        ("300ms × 5",  300, 5),
        ("500ms × 3",  500, 3),
        ("600ms × 3",  600, 3),
        ("800ms × 2",  800, 2),
    ]

    for label, dur, repeats in cases:
        # 每组前先回到基线位置
        skill.swipe(SCREEN_W, int(SCREEN_H * 0.85), SCREEN_W, int(SCREEN_H * 0.15), 300)
        time.sleep(1.0)
        start_y, start_title = get_top_card_y(skill)

        prev_y = start_y
        print(f"\n[diag] === {label} (start y={start_y}, title={start_title!r}) ===")
        for i in range(repeats):
            do_swipe(skill, dur)
            y, title = get_top_card_y(skill)
            moved = y - prev_y
            print(f"  swipe {i+1}/{repeats}: y={y} Δ={moved:+d} title={title!r}")
            prev_y = y

    print("\n[diag] 完成。如果所有 Δ 都接近 0，说明 swipe 没生效，"
          "需要检查 device input swipe 是否被丢弃。")
    return 0


if __name__ == "__main__":
    sys.exit(main())