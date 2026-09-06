"""
Boss直聘求职自动化 — 第一阶段：搜索职位并发打招呼

工作流：
  1. 启动 Boss直聘 App
  2. 搜索目标职位关键词
  3. 滚动加载职位列表（含去重）
  4. 逐个处理：弹窗检测 → 发打招呼消息 → 消息验证

注意：Boss直聘 平台规定，只有双方都发过消息后才会出现"投递简历"按钮。
      请先运行此脚本发送打招呼，等待 HR 回复后，再运行 boss_apply_task.py 投递简历。

使用方式：
    python scenarios/boss/tasks/boss_greet_task.py [--keyword 关键词]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from monitors.adb_manager import ADBManager
from skills.boss import BOSSAutomationSkill, DialogType, PageState
from utils import web_search
from utils.logging_setup import setup_logger

DEFAULT_GREETING = "您好！我对这个职位很感兴趣，期待与您进一步沟通。"

# Dialog types that are fatal to the whole session
_FATAL_DIALOGS = {DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED}

# Dialog types that mean we should skip the current job
_SKIP_DIALOGS = {DialogType.JOB_OFFLINE}

# Dialog types where we can proceed despite the dialog
_CONTINUE_DIALOGS = {DialogType.EXISTING_CHAT, DialogType.DISMISSED}


def _return_to_job_list(skill: BOSSAutomationSkill) -> bool:
    """Press back until we're on the job list page (max 4 tries)."""
    for _ in range(4):
        page = skill.get_current_page()
        if page == PageState.JOB_LIST:
            return True
        skill.press_back()
        time.sleep(0.6)
    return skill.get_current_page() == PageState.JOB_LIST


def run_job_search(
    keyword: str,
    greeting: str = DEFAULT_GREETING,
    max_jobs: int = 5,
    verify_send: bool = True,
    device_id: str = None,
) -> dict:
    """
    Execute the Boss直聘 greeting workflow (Phase 1).

    Args:
        keyword:     Job search keyword (e.g. 'Python 工程师').
        greeting:    Message to send when greeting an HR.
        max_jobs:    Maximum number of job listings to inspect.
        verify_send: If True, confirm the greeting message was sent.
        device_id:   ADB device serial; uses first connected device if None.

    Returns:
        Result dict with 'jobs_found', 'actions_taken', 'errors'.
    """
    logger = setup_logger("boss_greet_task", log_dir="./logs/boss")

    adb = ADBManager()
    skill = BOSSAutomationSkill(adb, device_id=device_id)

    result = {"jobs_found": [], "actions_taken": [], "errors": []}

    # Step 1 — launch
    logger.info("[1/4] 启动 Boss直聘 App…")
    if not skill.launch():
        msg = "无法启动 Boss直聘 App，请确认已安装且 ADB 已连接"
        logger.error(msg)
        result["errors"].append(msg)
        return result
    logger.info("  ✓ App 已启动")

    # Step 2 — search
    logger.info("[2/4] 搜索职位：「%s」…", keyword)
    if not skill.browse_jobs(keyword):
        msg = f"搜索失败：{keyword}"
        logger.error(msg)
        result["errors"].append(msg)
        return result
    # Wait until the job list is actually rendered
    elem = skill.wait_for_element(
        f"com.hpbr.bosszhipin:id/tv_position_name", timeout=6.0
    )
    if not elem:
        msg = "搜索结果页加载超时，流程中止"
        logger.error(msg)
        result["errors"].append(msg)
        return result
    logger.info("  ✓ 搜索完成，职位列表已加载")

    # Step 3 — scroll + collect
    logger.info("[3/4] 加载职位列表（含滚动翻页）…")
    jobs = skill.scroll_job_list(n_jobs=max_jobs, max_scrolls=8)
    result["jobs_found"] = [
        {"title": j.title, "company": j.company, "salary": j.salary, "location": j.location}
        for j in jobs
    ]
    logger.info("  ✓ 采集到 %d 条职位", len(jobs))
    for i, job in enumerate(jobs, 1):
        suffix = f"  {job.company}  {job.salary}  {job.location}".rstrip()
        logger.info("    %d. %s%s", i, job.title, suffix)

    if not jobs:
        logger.info("  未找到匹配职位，流程结束。")
        return result

    # Step 4 — process each job
    logger.info("[4/4] 逐个处理职位…")
    for idx, job in enumerate(jobs, 1):
        logger.info("→ [%d/%d] %s  %s", idx, len(jobs), job.title, job.company)
        try:
            if not skill.ensure_ready():
                msg = "ensure_ready 失败，停止任务"
                logger.error(msg)
                result["errors"].append(msg)
                break

            # Ensure we're on the job list before navigating
            if idx > 1:
                if not _return_to_job_list(skill):
                    err = "无法返回职位列表，中止后续处理"
                    logger.error(err)
                    result["errors"].append(err)
                    break

            # Navigate into the job detail using stored coordinates
            if not skill.navigate_to_job(job):
                err = f"{job.title}: 导航失败"
                logger.warning(err)
                result["errors"].append(err)
                continue
            detail_elem = skill.wait_for_element(
                "com.hpbr.bosszhipin:id/tv_job_name", timeout=4.0
            )
            if not detail_elem:
                err = f"职位详情页加载超时：{job.title}"
                logger.warning(err)
                result["errors"].append(err)
                continue

            # Check for immediate dialogs (job offline, etc.)
            dialog = skill.detect_dialog()
            if dialog != DialogType.NONE:
                skill.dismiss_dialog(dialog)
                if dialog in _FATAL_DIALOGS:
                    msg = f"致命弹窗（{dialog}），终止全部任务"
                    logger.error(msg)
                    result["errors"].append(f"致命弹窗：{dialog}")
                    break
                if dialog in _SKIP_DIALOGS:
                    logger.info("  ⚠ 跳过（%s）", dialog)
                    result["actions_taken"].append({"job": job.title, "action": f"跳过（{dialog}）"})
                    continue

            # --- open chat and greet ---
            if not skill.tap_element("chat_btn"):
                action = "无法打开聊天页"
            else:
                # Wait for chat input to appear
                chat_elem = skill.wait_for_element(
                    "com.hpbr.bosszhipin:id/editText_with_scrollbar", timeout=4.0
                )
                if not chat_elem:
                    action = "聊天页加载超时"
                else:
                    # Re-check dialog after opening chat (daily limit, existing chat, etc.)
                    dialog2 = skill.detect_dialog()
                    if dialog2 != DialogType.NONE:
                        skill.dismiss_dialog(dialog2)
                        if dialog2 in _FATAL_DIALOGS:
                            msg = f"致命弹窗（{dialog2}），终止全部任务"
                            logger.error(msg)
                            result["errors"].append(f"致命弹窗：{dialog2}")
                            result["actions_taken"].append(
                                {"job": job.title, "action": f"终止（{dialog2}）"}
                            )
                            break
                        if dialog2 not in _CONTINUE_DIALOGS:
                            action = f"跳过（{dialog2}）"
                            result["actions_taken"].append({"job": job.title, "action": action})
                            logger.info("  ⚠ %s", action)
                            continue

                    ok = skill.send_greeting(greeting, verify=verify_send)
                    action = "打招呼成功" if ok else "打招呼失败（或消息未确认）"

            result["actions_taken"].append({"job": job.title, "action": action})
            logger.info("  ✓ %s", action)
            time.sleep(1.0)
        except Exception as exc:
            logger.error("处理职位 '%s' 时出现未预期异常", job.title, exc_info=True)
            result["errors"].append(f"{job.title}: {exc}")
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
    parser = argparse.ArgumentParser(description="Boss直聘求职自动化 — 第一阶段：搜索+打招呼")
    parser.add_argument("--keyword", default="Python 工程师", help="搜索关键词")
    parser.add_argument("--greeting", default=DEFAULT_GREETING, help="打招呼消息内容")
    parser.add_argument("--max-jobs", type=int, default=5, help="最多处理职位数")
    parser.add_argument("--no-verify", action="store_true", help="发送后不验证消息已发出")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    args = parser.parse_args()

    result = run_job_search(
        keyword=args.keyword,
        greeting=args.greeting,
        max_jobs=args.max_jobs,
        verify_send=not args.no_verify,
        device_id=args.device,
    )

    print("\n" + "=" * 60)
    print("执行结果摘要")
    print("=" * 60)
    print(f"找到职位：{len(result['jobs_found'])} 条")
    print(f"执行操作：{len(result['actions_taken'])} 次")
    if result["errors"]:
        print(f"错误：{len(result['errors'])} 个")
        for err in result["errors"]:
            print(f"  - {err}")
    else:
        print("无错误")

    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
