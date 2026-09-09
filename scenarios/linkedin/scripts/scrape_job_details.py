#!/usr/bin/env python3
"""
LinkedIn 职位详情爬取脚本

工作流：
  1. 唤醒屏幕，启动 LinkedIn App
  2. 搜索指定关键词，进入职位列表
  3. 逐个进入职位详情页，点击 "Show more" 展开描述，滚动获取更多字段
  4. 提取结构化数据，写入 SQLite（job_details 表）
  5. 跨次去重（dedup_key = normalize(title) + TAB + company + TAB + location）

使用方式：
    # 按 keywords.yaml 批量抓取
    python scenarios/linkedin/scripts/scrape_job_details.py

    # 单关键词抓取
    python scenarios/linkedin/scripts/scrape_job_details.py --keyword "Product Manager" --max-jobs 10

    # 截图模式
    python scenarios/linkedin/scripts/scrape_job_details.py --keyword "PM" --max-jobs 5 --screenshot

注意：
  - LinkedIn App 必须预先登录，且 session 未过期
  - 每日浏览上限：≤100 条（详见 docs/linkedin_platform_rules.md）
  - 触发 SESSION_EXPIRED 或 RATE_LIMITED 时立即停止
"""

import argparse
import io
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

import yaml

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT))

from skills.android.adb_runner import ADBRunner
from skills.linkedin import (
    DialogType,
    JobInfo,
    LinkedInAutomationSkill,
    normalize_job_title,
)
from utils.logging_setup import setup_logger

_DEFAULT_CONFIG = ROOT / "scenarios" / "linkedin" / "config" / "keywords.yaml"
DB_PATH         = ROOT / "scenarios" / "linkedin" / "output" / "linkedin_jobs.db"

_FATAL_DIALOGS  = {DialogType.SESSION_EXPIRED, DialogType.RATE_LIMITED, DialogType.CAPTCHA}
_SKIP_DIALOGS   = {DialogType.JOB_CLOSED}

_MAX_DETAIL_SCROLLS = 8
_MAX_STALE_DUMPS    = 5   # consecutive identical XML dumps → list exhausted
_DETAIL_STAY        = 2.5  # seconds to stay on each detail page (anti-bot)
_SCROLL_DELAY       = 1.0  # seconds between scrolls in job detail
_KEYWORD_DELAY      = 60.0 # seconds between keyword batches


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

CREATE_JOB_DETAILS = """
CREATE TABLE IF NOT EXISTS job_details (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword         TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    company         TEXT,
    location        TEXT,
    description     TEXT,
    applicant_count TEXT,
    posted_time     TEXT,
    seniority_level TEXT,
    employment_type TEXT,
    job_function    TEXT,
    industries      TEXT,
    easy_apply      INTEGER DEFAULT 0,
    skills_json     TEXT,
    raw_texts_json  TEXT,
    dedup_key       TEXT    NOT NULL UNIQUE,
    scraped_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_li_keyword ON job_details(keyword);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(CREATE_JOB_DETAILS)
    conn.commit()
    return conn


def load_seen_keys(conn: sqlite3.Connection, keyword: str) -> Set[str]:
    rows = conn.execute(
        "SELECT dedup_key FROM job_details WHERE keyword = ?", (keyword,)
    ).fetchall()
    return {r[0] for r in rows}


def insert_job_detail(conn: sqlite3.Connection, keyword: str, job: JobInfo,
                      detail: dict, dedup_key: str) -> bool:
    """Insert a new job_detail row; silently skip on UNIQUE constraint."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        conn.execute(
            """
            INSERT INTO job_details
                (keyword, title, company, location, description, applicant_count,
                 posted_time, seniority_level, employment_type, job_function,
                 industries, easy_apply, skills_json, raw_texts_json, dedup_key, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                keyword,
                detail.get("title") or job.title,
                job.company or detail.get("company"),
                job.location or detail.get("location"),
                detail.get("description", ""),
                detail.get("applicant_count", ""),
                detail.get("posted_time") or job.posted_time,
                detail.get("seniority_level", ""),
                detail.get("employment_type", ""),
                detail.get("job_function", ""),
                detail.get("industries", ""),
                1 if (detail.get("easy_apply") or job.easy_apply) else 0,
                json.dumps(detail.get("skills", []), ensure_ascii=False),
                json.dumps(detail.get("raw_texts", []), ensure_ascii=False),
                dedup_key,
                now,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # dedup_key already exists


# ---------------------------------------------------------------------------
# Job key
# ---------------------------------------------------------------------------

def _job_key(title: str, company: str, location: str) -> str:
    return normalize_job_title(title) + "\t" + company + "\t" + location


# ---------------------------------------------------------------------------
# Find next unvisited job (with stale-XML detection)
# ---------------------------------------------------------------------------

def _find_next_job(
    skill: LinkedInAutomationSkill,
    visited: Set[str],
    max_stale: int = _MAX_STALE_DUMPS,
    logger: Optional[logging.Logger] = None,
) -> Optional[JobInfo]:
    """
    Scan the current job list for the first unvisited job.
    Scrolls down if nothing new is visible. Returns None when list is exhausted
    (max_stale consecutive identical XML dumps) or if not on search results page.
    """
    prev_xml = ""
    stale    = 0

    while True:
        xml = skill.get_ui_hierarchy(force_refresh=True)

        if not LinkedInAutomationSkill._is_search_results_page(xml):
            if logger:
                logger.warning("  [_find_next_job] 当前不在搜索结果页，停止滚动")
            return None

        if xml == prev_xml:
            stale += 1
            if stale >= max_stale:
                if logger:
                    logger.info("  [_find_next_job] 连续 %d 轮 XML 未变，列表已到底", stale)
                return None
            time.sleep(1.0)
        else:
            stale = 0
        prev_xml = xml

        for job in skill.get_job_list(xml=xml):
            key = _job_key(normalize_job_title(job.title), job.company or "", job.location or "")
            if key and key not in visited:
                return job

        skill.scroll_down(start_y=1800, end_y=600)
        time.sleep(_SCROLL_DELAY)


# ---------------------------------------------------------------------------
# Extract detail (with scroll + merge)
# ---------------------------------------------------------------------------

def _merge_detail(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if k == "raw_texts":
            seen = set(dst.get("raw_texts", []))
            dst.setdefault("raw_texts", [])
            dst["raw_texts"] += [t for t in v if t not in seen]
        elif k in ("skills",):
            existing = set(dst.get(k, []))
            dst.setdefault(k, [])
            dst[k] += [t for t in v if t not in existing]
        elif k == "description":
            if len(str(v)) > len(str(dst.get(k, ""))):
                dst[k] = v
        elif k not in dst:
            dst[k] = v


def _extract_detail(
    skill: LinkedInAutomationSkill,
    job_title: str,
    logger: logging.Logger,
    report: dict,
) -> dict:
    # 1. Expand description ("Show more" / "See more")
    skill.expand_description()
    time.sleep(0.5)

    # 2. Initial dump
    detail = skill.get_job_detail()

    # 3. Scroll down to load remaining fields (seniority, employment type, etc.)
    prev_xml = ""
    stale    = 0
    for scroll_n in range(_MAX_DETAIL_SCROLLS):
        xml = skill.get_ui_hierarchy(force_refresh=True)
        if xml == prev_xml:
            stale += 1
            if stale >= 3:
                break
        else:
            stale = 0
        prev_xml = xml

        scroll_detail = skill.get_job_detail(xml=xml)
        _merge_detail(detail, scroll_detail)

        # Stop early if we have all key fields
        has_all = all(detail.get(f) for f in ("description", "seniority_level", "employment_type"))
        if has_all:
            break

        skill.scroll_down(start_y=1600, end_y=800)
        time.sleep(_SCROLL_DELAY)

    # 4. AI recovery if description is missing
    if not detail.get("description"):
        logger.warning("  ⚠ [%s] 描述为空，尝试 AI 恢复", job_title)
        recovered = _ai_recover(skill, job_title, logger)
        if recovered:
            _merge_detail(detail, recovered)

    return detail


def _ai_recover(
    skill: LinkedInAutomationSkill,
    job_title: str,
    logger: logging.Logger,
) -> Optional[dict]:
    """Use Claude Haiku to extract structured data from raw UIAutomator XML."""
    import os
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("  [ai_recover] ANTHROPIC_API_KEY 未设置，跳过")
        return None

    xml = skill.get_ui_hierarchy(force_refresh=True)
    if not xml:
        return None

    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "You are extracting structured job information from a LinkedIn Android app UIAutomator XML dump.\n"
        f"Job title: {job_title}\n\n"
        "Extract these fields (return valid JSON, missing fields as empty string):\n"
        "title, company, location, description, applicant_count, posted_time, "
        "seniority_level, employment_type, job_function, industries, easy_apply (bool)\n\n"
        f"XML (first 6000 chars):\n{xml[:6000]}"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Extract JSON from response
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        logger.warning("  [ai_recover] 失败: %s", e)
    return None


# ---------------------------------------------------------------------------
# Main scrape function
# ---------------------------------------------------------------------------

def scrape(
    keyword: str,
    max_jobs: int,
    db_conn: sqlite3.Connection,
    device_id: Optional[str],
    take_screenshot: bool,
    logger: logging.Logger,
) -> dict:
    adb   = ADBRunner()
    skill = LinkedInAutomationSkill(
        adb_manager=adb,
        device_id=device_id,
        output_dir=str(DB_PATH.parent / "screenshots"),
    )
    skill._logger = logger  # route skill logs to the scrape logger

    report: dict = {
        "keyword":         keyword,
        "max_jobs":        max_jobs,
        "scraped_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device_id":       device_id or "auto",
        "jobs_scraped":    0,
        "jobs_skipped":    0,
        "errors":          [],
    }

    def _unlock():
        skill._adb("shell input keyevent 224")   # WAKEUP
        time.sleep(1.0)
        skill._adb("shell wm dismiss-keyguard")
        time.sleep(0.5)
        skill._adb("shell input swipe 540 1800 540 600 300")
        time.sleep(0.5)
        skill._adb("shell input keyevent 4")

    # -- 0. Wake & keep-awake -----------------------------------------------
    logger.info("[0] 唤醒屏幕…")
    skill._adb("shell svc power stayon true")
    skill._adb("shell settings put system screen_off_timeout 2147483647")
    _unlock()
    time.sleep(0.5)

    # -- 1. Launch ----------------------------------------------------------
    logger.info("[1] 启动 LinkedIn App…")
    if not skill.launch():
        msg = "无法启动 LinkedIn，请确认已安装且 ADB 已连接"
        logger.error(msg)
        report["errors"].append(msg)
        return report
    _unlock()
    time.sleep(1.0)

    if not skill.ensure_ready():
        msg = "ensure_ready 失败（致命弹窗或设备未连接）"
        logger.error(msg)
        report["errors"].append(msg)
        return report
    logger.info("  ✓ App 已启动")

    # -- 2. Search ----------------------------------------------------------
    logger.info("[2] 搜索职位：「%s」…", keyword)
    if not skill.browse_jobs(keyword):
        msg = f"搜索失败：{keyword}"
        logger.error(msg)
        report["errors"].append(msg)
        return report
    time.sleep(2.0)
    logger.info("  ✓ 搜索完成，进入职位列表")

    # -- 3. Incremental scrape -----------------------------------------------
    logger.info("[3] 逐个抓取职位详情（增量模式）…")
    visited: Set[str] = load_seen_keys(db_conn, keyword)
    if visited:
        logger.info("  跨次去重：已有历史记录 %d 条", len(visited))

    idx = 0
    while idx < max_jobs:
        if not skill.ensure_ready():
            err = "ensure_ready 失败，停止任务"
            logger.error(err)
            report["errors"].append(err)
            break

        job = _find_next_job(skill, visited, logger=logger)
        if job is None:
            logger.info("  已无更多新职位，采集结束（共 %d 条）", idx)
            break

        title   = normalize_job_title(job.title)
        key     = _job_key(title, job.company or "", job.location or "")
        visited.add(key)
        idx += 1
        logger.info("→ [%d/%d] %s  @  %s", idx, max_jobs, title, job.company)

        nav_ok = False
        try:
            if not skill.navigate_to_job(job):
                logger.warning("  [%s] 导航失败，跳过", title)
                report["jobs_skipped"] += 1
                continue

            nav_ok = True
            time.sleep(_DETAIL_STAY)  # Stay on page to appear human

            # Check for dialogs after navigation
            dialog = skill.detect_dialog()
            if dialog != DialogType.NONE:
                skill.dismiss_dialog(dialog)
                if dialog in _FATAL_DIALOGS:
                    logger.error("致命弹窗 (%s)，终止任务", dialog)
                    report["errors"].append(f"致命弹窗：{dialog}")
                    return report
                if dialog in _SKIP_DIALOGS:
                    logger.info("  ⚠ 跳过（%s）", dialog)
                    report["jobs_skipped"] += 1
                    nav_ok = False

            if nav_ok:
                detail = _extract_detail(skill, title, logger, report)
                written = insert_job_detail(db_conn, keyword, job, detail, key)
                if written:
                    report["jobs_scraped"] += 1
                    logger.info("  ✓ 已写入数据库（%s）",
                                "，".join(f for f in ("description", "seniority_level",
                                                       "employment_type", "company")
                                          if detail.get(f)))
                else:
                    logger.info("  — 已有记录（dedup），跳过")
                    report["jobs_skipped"] += 1

                if take_screenshot:
                    fname = f"detail_{idx:03d}_{title[:20].replace(' ','_')}.png"
                    skill.screenshot(fname)

        except Exception as exc:
            logger.exception("  [%s] 未预期异常：%s", title, exc)
            report["errors"].append(f"{title}: {exc}")
        finally:
            back_ok = skill.navigate_back_from_job()
            time.sleep(0.5)
            if not back_ok:
                logger.warning("  [recovery] 不在搜索结果页，force-stop → 重新搜索「%s」", keyword)
                if not skill.browse_jobs(keyword):
                    logger.error("  [recovery] 重新搜索失败，终止任务")
                    report["errors"].append("recovery: browse_jobs 重新搜索失败")
                    break
                time.sleep(1.0)

    logger.info("[完成] 关键词「%s」抓取完毕：%d 条写入，%d 条跳过",
                keyword, report["jobs_scraped"], report["jobs_skipped"])
    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _resolve_device() -> Optional[str]:
    import subprocess
    r = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    serials = [ln.split()[0] for ln in r.stdout.splitlines()[1:]
               if ln.strip().endswith("device")]
    if len(serials) == 1:
        return serials[0]
    if len(serials) > 1:
        print(f"[ERROR] 多台设备 {serials}，请用 --device 指定")
        sys.exit(1)
    print("[ERROR] 未检测到 ADB 设备")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="LinkedIn 职位爬取脚本")
    parser.add_argument("--keyword",   default=None,   help="搜索关键词（覆盖 keywords.yaml）")
    parser.add_argument("--max-jobs",  type=int, default=None, help="每个关键词最多抓取条数")
    parser.add_argument("--device",    default=None,   help="ADB 设备 serial")
    parser.add_argument("--screenshot",action="store_true", help="每条职位保存截图")
    parser.add_argument("--db",        default=str(DB_PATH), help="SQLite 数据库路径")
    args = parser.parse_args()

    logger = setup_logger("scrape_linkedin", log_dir="./logs/linkedin")

    config: dict = {}
    if _DEFAULT_CONFIG.exists():
        config = yaml.safe_load(_DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}

    keywords = [args.keyword] if args.keyword else config.get("keywords", ["Product Manager"])
    default_n = config.get("defaults", {}).get("n_jobs", 20)
    max_jobs  = args.max_jobs if args.max_jobs is not None else default_n
    device_id = args.device or _resolve_device()

    db_conn = init_db(Path(args.db))

    for i, kw in enumerate(keywords):
        if i > 0:
            logger.info("[关键词间隔] 等待 %.0f 秒…", _KEYWORD_DELAY)
            time.sleep(_KEYWORD_DELAY)

        logger.info("=" * 60)
        logger.info("关键词 [%d/%d]: %s  (最多 %d 条)", i + 1, len(keywords), kw, max_jobs)
        logger.info("=" * 60)

        report = scrape(
            keyword=kw,
            max_jobs=max_jobs,
            db_conn=db_conn,
            device_id=device_id,
            take_screenshot=args.screenshot,
            logger=logger,
        )

        if report.get("errors"):
            logger.warning("  本轮错误 %d 条：%s",
                           len(report["errors"]), "; ".join(str(e) for e in report["errors"][:3]))

        # Stop all keywords if fatal dialog was hit (session expired, rate limited)
        fatal_msgs = ("致命弹窗", "SESSION_EXPIRED", "RATE_LIMITED", "CAPTCHA")
        if any(any(f in str(e) for f in fatal_msgs) for e in report.get("errors", [])):
            logger.error("检测到致命错误，停止所有关键词任务")
            break

    db_conn.close()
    logger.info("全部关键词抓取完毕")


if __name__ == "__main__":
    main()
