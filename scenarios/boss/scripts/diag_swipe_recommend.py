"""
最小诊断：在 Boss app 首页 recommend feed（不是搜索结果页）上验证
uiautomator2 UiObject.scroll.backward() 是否能让 rv_list 滚动。
diag_swipe.py 已经证明 adb shell input swipe 23/23 次 Δ=0 无效，
现在确认 uiautomator2 路径是否对首页 feed 同样有效。

运行前提：
- 设备已连接，adb 可用
- Boss app 当前在首页 recommend tab（PageState=recommend）

运行：
    PYTHONUTF8=1 python scenarios/boss/scripts/diag_swipe_recommend.py
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


RV_ID = "com.hpbr.bosszhipin:id/rv_list"        # 首页 feed RecyclerView
CARD_TITLE_ID = "com.hpbr.bosszhipin:id/tv_position_name"


def get_top_card(skill: BOSSAutomationSkill) -> tuple[int, str]:
    """Return (center_y, title) of first visible job card on the page."""
    xml = skill.get_ui_hierarchy()
    el = skill.find_element(resource_id=CARD_TITLE_ID, xml=xml)
    if el is None:
        return (-1, "")
    title = ""
    for node in ET.fromstring(xml).iter("node"):
        if node.attrib.get("resource-id", "") == CARD_TITLE_ID:
            title = node.attrib.get("text", "")
            break
    bounds = el.bounds  # (x1, y1, x2, y2)
    return ((bounds[1] + bounds[3]) // 2, title)


def main() -> int:
    adb = ADBRunner()
    skill = BOSSAutomationSkill(adb, action_delay=0.3)

    page = skill.get_current_page()
    print(f"[diag-rec] current_page = {page}")
    if page != "recommend":
        print(f"[diag-rec] 错误：当前不在首页 recommend tab (page={page})，请手动回到首页")
        return 1

    base_y, base_title = get_top_card(skill)
    print(f"[diag-rec] 基线 首卡 y={base_y} title={base_title!r}")

    # 1. 确认 rv_list 存在
    try:
        import uiautomator2 as u2  # noqa: WPS433
    except ImportError:
        print("[diag-rec] uiautomator2 未安装")
        return 1
    device = u2.connect(skill.device_id) if skill.device_id else u2.connect()
    rv = device(resourceId=RV_ID)
    if not rv.wait(timeout=2.0):
        print(f"[diag-rec] 未找到 {RV_ID}")
        return 1
    print(f"[diag-rec] 已锁定 {RV_ID}")

    # 2. 用 adb swipe 复现一次（已知无效，仅做对照）
    print("\n[diag-rec] === 对照：adb shell input swipe (300ms) ===")
    skill.swipe(540, 360, 540, 2040, 300)  # finger top→bottom (content scrolls up)
    time.sleep(1.5)
    y, title = get_top_card(skill)
    print(f"  after swipe: y={y} Δ={y - base_y:+d} title={title!r}")

    # 3. 用 uiautomator2 UiScrollable.scroll.backward() 测首页
    print("\n[diag-rec] === uiautomator2 rv_list.scroll.backward() × 6 ===")
    prev_y = y
    for i in range(6):
        try:
            rv.scroll.backward()
        except Exception as exc:  # noqa: BLE001
            print(f"  scroll {i+1}: 异常 {exc}")
            break
        time.sleep(0.6)
        y, title = get_top_card(skill)
        print(f"  scroll {i+1}: y={y} Δ={y - prev_y:+d} title={title!r}")
        prev_y = y

    # 4. 滚回顶部验证
    print("\n[diag-rec] === rv_list.scroll.forward() × 6 (反向) ===")
    prev_y = y
    for i in range(6):
        try:
            rv.scroll.forward()
        except Exception as exc:  # noqa: BLE001
            print(f"  scroll {i+1}: 异常 {exc}")
            break
        time.sleep(0.6)
        y, title = get_top_card(skill)
        print(f"  scroll {i+1}: y={y} Δ={y - prev_y:+d} title={title!r}")
        prev_y = y

    print(
        "\n[diag-rec] 完成。如果 backward 多次都 Δ=0，说明 uiautomator2 也无法"
        "驱动首页 rv_list；如果 Δ 持续变化则验证通过。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
