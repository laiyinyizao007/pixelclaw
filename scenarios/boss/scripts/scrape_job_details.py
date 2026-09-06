#!/usr/bin/env python3
"""
Boss直聘职位详情爬取脚本

工作流：
  1. 启动 Boss直聘 App
  2. 搜索指定关键词
  3. 滚动加载职位列表（含去重）
  4. 逐个进入职位详情页，提取结构化信息
  5. 保存到 JSON 文件

使用方式：
    # 批量：按 scenarios/boss/config/keywords.yaml 中的关键词逐个爬取，每个关键词一个 JSON
    python scenarios/boss/scripts/scrape_job_details.py

    # 单次：临时覆盖配置文件，只跑一个关键词
    python scenarios/boss/scripts/scrape_job_details.py --keyword "AI产品经理" --n-jobs 10
    python scenarios/boss/scripts/scrape_job_details.py --keyword "FDE" --n-jobs 20 --screenshot
"""

import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT))

from skills.android.adb_runner import ADBRunner
from skills.boss import (
    BOSSAutomationSkill,
    DialogType,
    JobInfo,
    PageState,
    normalize_card_title,
)
from utils.logging_setup import setup_logger

_DEFAULT_CONFIG = ROOT / "scenarios" / "boss" / "config" / "keywords.yaml"

_FATAL_DIALOGS = {DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED}
_SKIP_DIALOGS  = {DialogType.JOB_OFFLINE}

# 公司信息块位于详情页底部，描述越长它被推得越远，固定滚一次抓不到。
# 真正的到底判据是 _MAX_STALE_DUMPS，这里只是防死循环的安全上限：实测 2406 字的
# 描述会滚穿 14 次仍未到底，故留足余量。
_MAX_DETAIL_SCROLLS = 30

# 公司信息块由 rv_list 懒加载：未 inflate 前页面滚不动，连续多轮 dump 完全一致，
# 之后才突然长出整块内容（实测连续 3 轮字节相同，第 4 轮 13880→22315 字符）。
# 因此单次「XML 未变」不能判定已到底，需连续 N 次不变才退出。
_MAX_STALE_DUMPS = 4


def _merge_detail(dst: dict, src: dict) -> None:
    """Merge a later (scrolled-down) dump into the accumulated detail dict."""
    for key, val in src.items():
        if key == "raw_texts":
            dst.setdefault("raw_texts", [])
            seen = set(dst["raw_texts"])
            dst["raw_texts"] += [t for t in val if t not in seen]
        elif key in ("skills", "benefits"):
            existing = set(dst.get(key, []))
            dst.setdefault(key, [])
            dst[key] += [t for t in val if t not in existing]
        elif key == "description":
            # 展开前的 dump 里是截断版，保留更长的那一份。
            if len(val) > len(dst.get(key, "")):
                dst[key] = val
        elif key not in dst:
            dst[key] = val


def _scroll_for_company_info(skill: BOSSAutomationSkill, detail: dict) -> None:
    """
    向下滚动详情页直到抓到公司信息块，或连续 `_MAX_STALE_DUMPS` 轮 XML 不变。

    公司信息块由 rv_list 懒加载，inflate 前页面滚不动，单次「XML 未变」不等于已到底。
    """
    prev_xml = ""
    stale = 0
    for _ in range(_MAX_DETAIL_SCROLLS):
        skill.scroll_down(start_y=1600, end_y=800)
        time.sleep(0.5)
        xml_bot = skill.get_ui_hierarchy()
        if not xml_bot:
            return
        if xml_bot == prev_xml:
            stale += 1
            if stale >= _MAX_STALE_DUMPS:
                return
            time.sleep(1.0)   # 给懒加载的公司信息块留出 inflate 时间
            continue
        stale = 0
        prev_xml = xml_bot
        _merge_detail(detail, skill.get_job_detail(xml=xml_bot))
        if "company" in detail and "company_info" in detail:
            return


def _return_to_job_list(
    skill: BOSSAutomationSkill,
    keyword: str,
) -> bool:
    """
    Re-navigate to the search-result job list after visiting a detail page.

    Strategy: navigate to the "职位" tab unconditionally (bottom nav always
    visible even when toolbar collapses), then re-run the search.  This is
    slower than a simple back press but completely reliable across all states.
    """
    # Escape any overlay / dialog first.
    page = skill.get_current_page()
    if page in (PageState.DIALOG,):
        skill.press_back()
        time.sleep(0.8)

    # Navigate to the jobs tab (bottom nav stays visible regardless of scroll).
    if not skill.navigate_to_tab("jobs"):
        # Fallback: try two back presses to reach a known tab.
        skill.press_back(); time.sleep(1.0)
        skill.press_back(); time.sleep(1.0)

    time.sleep(1.0)

    # Re-search with the keyword.  The search overlay animation occasionally
    # misses its 5 s window, so give it a couple of attempts before giving up.
    # A failed attempt can strand us on a page with no bottom nav (the search
    # overlay, the job-expectation editor), where navigate_to_tab is a no-op —
    # so back out first.
    for _ in range(3):
        if skill.browse_jobs(keyword):
            break
        time.sleep(1.5)
        skill.press_back()
        time.sleep(1.0)
        skill.navigate_to_tab("jobs")
        time.sleep(1.0)
    else:
        return False

    time.sleep(1.5)
    found = skill.wait_for_element(
        "com.hpbr.bosszhipin:id/tv_position_name", timeout=15.0
    )
    return found is not None


def _is_complete(job: JobInfo) -> bool:
    """列表卡是否已完整渲染（hr_active 在列表卡中永不存在，不参与判断）。"""
    return bool(job.company and job.location and job.hr_name)


def _find_next_job(
    skill: BOSSAutomationSkill,
    visited_titles: set,
    max_scrolls: int = 15,
    max_topups: int = 2,
) -> Optional[JobInfo]:
    """
    Scroll the current job list (from its current position) to find the first
    job whose normalized title is not in visited_titles.  Returns None when
    exhausted.

    位于视口边缘的卡片会被 RecyclerView 部分回收，只渲染出 title，其余字段为空。
    遇到这种卡片时小步滚动并重新 dump，让它完整进入视口——必须重新 dump 而非
    复用滚动前的对象，否则 tap_x/tap_y 已失效。
    """
    topups: dict = {}

    for _ in range(max_scrolls):
        xml = skill.get_ui_hierarchy()
        candidate = None
        for job in skill.get_job_list(xml=xml):
            key = normalize_card_title(job.title)
            if key and key not in visited_titles:
                candidate = (key, job)
                break

        if candidate is None:
            skill.scroll_down(start_y=1800, end_y=600)
            time.sleep(0.8)
            continue

        key, job = candidate
        if _is_complete(job) or topups.get(key, 0) >= max_topups:
            return job

        topups[key] = topups.get(key, 0) + 1
        skill.scroll_down(start_y=1500, end_y=1100)
        time.sleep(0.8)

    return None


def scrape(
    keyword: str,
    n_jobs: int,
    output_dir: Path,
    device_id: str,
    take_screenshot: bool,
) -> dict:
    logger = setup_logger("scrape_job_details", log_dir="./logs/boss")
    adb    = ADBRunner()
    skill  = BOSSAutomationSkill(adb, device_id=device_id,
                                  output_dir=str(output_dir / "screenshots"))

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {
        "keyword":          keyword,
        "n_jobs_requested": n_jobs,
        "scraped_at":       ts,
        "device_id":        device_id or "auto",
        "jobs":             [],
        "errors":           [],
    }

    def _unlock_screen():
        """Wake the screen and dismiss the keyguard; call before and after launch."""
        skill._adb("shell input keyevent 224")          # KEYCODE_WAKEUP
        time.sleep(1.0)
        skill._adb("shell wm dismiss-keyguard")
        time.sleep(0.5)
        # Swipe-up gesture as a fallback if dismiss-keyguard alone is insufficient.
        skill._adb("shell input swipe 540 1800 540 600 300")
        time.sleep(0.5)
        skill._adb("shell input keyevent 4")            # BACK to close overlays

    # ── 0. Wake & unlock screen ───────────────────────────────────────────────
    logger.info("[0/4] 唤醒屏幕并解除锁屏…")
    # Keep screen on permanently while USB-connected (survives lock-screen).
    skill._adb("shell svc power stayon true")
    skill._adb("shell settings put system screen_off_timeout 2147483647")
    _unlock_screen()
    time.sleep(0.5)

    # ── 1. Launch ─────────────────────────────────────────────────────────────
    logger.info("[1/4] 启动 Boss直聘 App…")
    if not skill.launch():
        msg = "无法启动 Boss直聘，请确认已安装且 ADB 已连接"
        logger.error(msg)
        report["errors"].append(msg)
        return report
    logger.info("  ✓ App 已启动")

    # Re-unlock in case the launch animation briefly triggered the keyguard.
    _unlock_screen()
    time.sleep(1.0)

    # 确保屏幕唤醒、App 在前台、弹窗已清理
    if not skill.ensure_ready():
        msg = "ensure_ready 失败（设备未连接或致命弹窗）"
        logger.error(msg)
        report["errors"].append(msg)
        return report

    # ── 2. Search ─────────────────────────────────────────────────────────────
    logger.info("[2/3] 搜索职位：「%s」…", keyword)
    if not skill.browse_jobs(keyword):
        msg = f"搜索失败：{keyword}"
        logger.error(msg)
        report["errors"].append(msg)
        return report

    elem = skill.wait_for_element(
        "com.hpbr.bosszhipin:id/tv_position_name", timeout=15.0
    )
    if not elem:
        msg = "搜索结果页加载超时"
        logger.error(msg)
        report["errors"].append(msg)
        return report
    logger.info("  ✓ 职位列表已加载")

    # ── 3+4. Incrementally find and scrape each unique job ───────────────────────
    # We do NOT pre-collect coordinates: after each visit the list is re-searched
    # from the top, so stored tap_y values would be stale.  Instead, find the next
    # unvisited job on the live screen before every navigation.
    logger.info("[3/4] 逐个查找并抓取职位详情（增量模式）…")
    visited_titles: set = set()
    idx = 0

    while idx < n_jobs:
        if not skill.ensure_ready():
            err = "ensure_ready 失败，停止任务"
            logger.error(err)
            report["errors"].append(err)
            break

        # Find the next job not yet visited (may scroll the list).
        job = _find_next_job(skill, visited_titles)
        if not job:
            logger.info("  已无更多新职位，采集结束（共找到 %d 条）", idx)
            break

        title = normalize_card_title(job.title)
        visited_titles.add(title)
        idx += 1
        logger.info("→ [%d/%d] %s  %s", idx, n_jobs, title, job.company)

        entry = {
            "index": idx,
            "list_info": {
                "title":     title,
                "company":   job.company,
                "salary":    job.salary,
                "location":  job.location,
                "hr_name":   job.hr_name,
                "hr_title":  job.hr_title,
                "hr_active": job.hr_active,
            },
            "detail":          {},
            "screenshot_path": "",
        }

        nav_ok = False
        try:
            # Navigate to detail using fresh coordinates just obtained above.
            if not skill.navigate_to_job(job):
                err = f"[{job.title}] 导航失败"
                logger.warning(err)
                report["errors"].append(err)
            else:
                nav_ok = True

            if nav_ok:
                detail_elem = skill.wait_for_element(
                    "com.hpbr.bosszhipin:id/tv_job_name", timeout=12.0
                )
                if not detail_elem:
                    err = f"[{job.title}] 详情页加载超时"
                    logger.warning(err)
                    report["errors"].append(err)
                    nav_ok = False

            if nav_ok:
                dialog = skill.detect_dialog()
                if dialog != DialogType.NONE:
                    skill.dismiss_dialog(dialog)
                    if dialog in _FATAL_DIALOGS:
                        logger.error("致命弹窗 (%s)，终止全部任务", dialog)
                        report["errors"].append(f"致命弹窗：{dialog}")
                        report["jobs"].append(entry)
                        skill.press_back()
                        return report
                    if dialog in _SKIP_DIALOGS:
                        logger.info("  ⚠ 跳过（%s）", dialog)
                        nav_ok = False

            if nav_ok:
                # Dump before scrolling: title, salary, tags at top of page.
                xml_top = skill.get_ui_hierarchy()
                detail  = skill.get_job_detail(xml=xml_top)

                time.sleep(0.5)
                # Expand the "查看更多" collapsed description (scrolls as needed).
                if not skill.expand_description():
                    logger.warning("  ⚠ [%s] 描述展开失败，可能仍被截断", job.title)

                _scroll_for_company_info(skill, detail)
                if "company" not in detail:
                    logger.warning("  ⚠ [%s] 滚动到底仍未抓到公司信息", job.title)

                entry["detail"] = detail
                logger.info("  ✓ 详情提取完成（字段: %s）",
                            ", ".join(k for k in detail if k != "raw_texts"))

                if take_screenshot:
                    fname = f"detail_{idx:03d}_{job.title[:20].replace(' ', '_')}.png"
                    path  = skill.screenshot(fname)
                    entry["screenshot_path"] = path
                    logger.info("  ✓ 截图: %s", path)

        except Exception as exc:
            logger.error("处理职位 '%s' 时出现异常", job.title, exc_info=True)
            report["errors"].append(f"{job.title}: {exc}")
            skill.ensure_ready()

        finally:
            report["jobs"].append(entry)
            skill.press_back()
            time.sleep(1.0)

        # Return to the top of the search-result list for the next iteration.
        if idx < n_jobs:
            if not _return_to_job_list(skill, keyword):
                logger.error("返回职位列表失败，停止")
                break

    return report


def load_config(path: Path) -> Tuple[List[str], dict]:
    """Read the keywords YAML; returns (keywords, defaults)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keywords = [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()]
    return keywords, data.get("defaults") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Boss直聘职位详情爬取脚本")
    parser.add_argument("--keyword",     default=None,          help="搜索关键词（覆盖配置文件，只跑这一个）")
    parser.add_argument("--config",      default=None,          help=f"关键词配置文件（默认 {_DEFAULT_CONFIG}）")
    parser.add_argument("--n-jobs",      type=int, default=None, help="抓取职位数（覆盖配置文件 defaults.n_jobs）")
    parser.add_argument("--output-dir",  default=None,          help="输出目录（默认 scenarios/boss/output/）")
    parser.add_argument("--device",      default=None,          help="ADB 设备 serial")
    parser.add_argument("--screenshot",  action="store_true",   help="是否为每条详情截图")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    if args.keyword:
        keywords, defaults = [args.keyword], {}
    else:
        if not config_path.exists():
            print(f"ERROR: 配置文件不存在：{config_path}（或用 --keyword 直接指定关键词）")
            return 1
        keywords, defaults = load_config(config_path)
        if not keywords:
            print(f"ERROR: {config_path} 中未配置任何关键词")
            return 1

    n_jobs = args.n_jobs if args.n_jobs is not None else int(defaults.get("n_jobs", 10))

    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "scenarios" / "boss" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    for i, keyword in enumerate(keywords, 1):
        print("\n" + "=" * 60)
        print(f"[{i}/{len(keywords)}] 关键词：{keyword}（目标 {n_jobs} 条）")
        print("=" * 60)

        report = scrape(
            keyword=keyword,
            n_jobs=n_jobs,
            output_dir=output_dir,
            device_id=args.device,
            take_screenshot=args.screenshot,
        )

        safe_kw  = keyword.replace(" ", "_").replace("/", "-")
        ts_file  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = output_dir / f"job_details_{safe_kw}_{ts_file}.json"
        out_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summaries.append((keyword, len(report["jobs"]), report["errors"], out_file))

    print("\n" + "=" * 60)
    print("批量爬取完成")
    print("=" * 60)
    total_jobs = total_errors = 0
    for keyword, n_found, errors, out_file in summaries:
        total_jobs += n_found
        total_errors += len(errors)
        print(f"\n「{keyword}」：{n_found} 条职位  →  {out_file.name}")
        for err in errors:
            print(f"  - {err}")
    print(f"\n合计：{len(summaries)} 个关键词，{total_jobs} 条职位，{total_errors} 个错误")
    print("=" * 60)

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
