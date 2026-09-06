"""
Boss直聘求职自动化 — 第二阶段：检查 HR 回复并投递简历

前提：
  1. 已通过 boss_greet_task.py 向目标职位发送打招呼消息
  2. 已等待足够时间（通常数小时至一天），让 HR 有机会回复

平台规则：Boss直聘 要求双方都发过消息后，聊天页才会出现"投递简历"按钮。
          本脚本通过 can_apply() 检测按钮是否出现来判断是否可以投递。

工作流：
  1. 启动 App，切换到消息 Tab
  2. 获取消息列表（已沟通的所有联系人）
  3. 逐个进入聊天页，检查是否出现"投递简历"按钮
  4. 出现则投递，未出现则跳过（HR 尚未回复）

使用方式：
    python scenarios/boss/tasks/boss_apply_task.py [--max-apply 20]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from monitors.adb_manager import ADBManager
from skills.boss import BOSSAutomationSkill, DialogType
from utils import web_search
from utils.logging_setup import setup_logger


def run_apply_task(
    max_apply: int = 20,
    device_id: str = None,
) -> dict:
    """
    Execute the Boss直聘 apply workflow (Phase 2).

    Args:
        max_apply:  Maximum number of chats to inspect for apply button.
        device_id:  ADB device serial; uses first connected device if None.

    Returns:
        Result dict with 'applied', 'skipped', 'failed', 'errors'.
    """
    logger = setup_logger("boss_apply_task", log_dir="./logs/boss")

    adb = ADBManager()
    skill = BOSSAutomationSkill(adb, device_id=device_id)

    result = {"applied": [], "skipped": [], "failed": [], "errors": []}

    # Step 1 — launch
    logger.info("[1/3] 启动 Boss直聘 App…")
    if not skill.launch():
        logger.error("无法启动 Boss直聘 App，请确认已安装且 ADB 已连接")
        return result
    time.sleep(2)
    logger.info("  ✓ App 已启动")

    # Step 2 — navigate to messages tab
    logger.info("[2/3] 切换到消息 Tab…")
    if not skill.navigate_to_tab("messages"):
        logger.error("无法切换到消息 Tab，流程中止")
        return result
    time.sleep(1.0)

    entries = skill.get_message_list()
    if not entries:
        logger.info("  消息列表为空，无需处理")
        return result
    logger.info("  ✓ 获取到 %d 条消息记录", len(entries))

    # Step 3 — iterate chats
    logger.info("[3/3] 逐个检查是否可投递（最多 %d 条）…", max_apply)
    for idx, entry in enumerate(entries[:max_apply], 1):
        logger.info("→ [%d/%d] %s  %s", idx, min(len(entries), max_apply),
                    entry.hr_name, entry.position)
        try:
            if not skill.ensure_ready():
                msg = "ensure_ready 失败，停止任务"
                logger.error(msg)
                result["errors"].append(msg)
                break

            if not skill.navigate_to_chat(entry):
                logger.warning("  ✗ 无法进入聊天页，跳过")
                result["failed"].append(entry.hr_name)
                continue

            time.sleep(1.0)

            dialog = skill.detect_dialog()
            if dialog in (DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED):
                msg = f"致命弹窗 {dialog}，停止任务"
                logger.error(msg)
                result["errors"].append(msg)
                break
            if dialog not in (DialogType.NONE, DialogType.DISMISSED):
                skill.dismiss_dialog(dialog)

            if not skill.can_apply():
                logger.info("  ○ 跳过（HR 尚未回复或投递按钮未出现）")
                result["skipped"].append(entry.hr_name)
            else:
                ok = skill.apply_to_job()
                if ok:
                    logger.info("  ✓ 已投递：%s — %s", entry.hr_name, entry.position)
                    result["applied"].append(entry.hr_name)
                else:
                    logger.warning("  ✗ 投递失败：%s", entry.hr_name)
                    result["failed"].append(entry.hr_name)

            # Return to messages list
            ok, _ = skill.adb.shell("input keyevent 4", skill.device_id)
            if not ok:
                logger.warning("返回消息列表的 back-press 失败，页面状态可能不一致")
            time.sleep(0.8)
        except Exception as exc:
            logger.error("处理联系人 '%s' 时出现未预期异常", entry.hr_name, exc_info=True)
            result["failed"].append(entry.hr_name)
            # 自动上网查找解决方法
            hints = web_search.search(
                f"Boss直聘 自动化 {type(exc).__name__} {str(exc)[:80]}"
            )
            if hints:
                logger.info("[WebSearch] 参考结果: %s", hints[0][:200])
            # 标准恢复：重新检查设备状态
            if not skill.ensure_ready():
                logger.error("[Recovery] ensure_ready 失败，停止任务")
                break
            continue

    return result


def main():
    parser = argparse.ArgumentParser(description="Boss直聘求职自动化 — 第二阶段：检查HR回复+投递简历")
    parser.add_argument("--max-apply", type=int, default=20, help="最多检查聊天数量")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    args = parser.parse_args()

    result = run_apply_task(
        max_apply=args.max_apply,
        device_id=args.device,
    )

    print("\n" + "=" * 60)
    print("投递结果摘要")
    print("=" * 60)
    print(f"已投递：{len(result['applied'])} 个  {result['applied']}")
    print(f"跳过（待回复）：{len(result['skipped'])} 个")
    if result["failed"]:
        print(f"失败：{len(result['failed'])} 个  {result['failed']}")
    else:
        print("无失败")
    if result.get("errors"):
        print(f"系统错误：{len(result['errors'])} 个")
        for err in result["errors"]:
            print(f"  - {err}")

    return 0 if not result["failed"] and not result.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
