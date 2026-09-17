#!/usr/bin/env python3
"""
smart_match_greet.py — 实时评分 + 个性化打招呼（单阶段 App 内循环）

工作流：
  1. Haiku 提炼简历画像（一次）
  2. 打开 Boss直聘 → 搜索关键词 → 滚动列表采集卡片
  3. 逐条：进详情页 → 提取完整 JD → Haiku 评分 + 生成打招呼 →
     分数达标则立即发送 → 记录 greetings 表去重 → 返回列表
  4. 达到 max_greet 或列表扫完为止

使用方式：
  # 仅评分，不发送（调试用）
  python scenarios/boss/scripts/smart_match_greet.py --keyword "AI产品经理" --score-only

  # 跑通一条（阈值 6）
  python scenarios/boss/scripts/smart_match_greet.py --keyword "AI产品经理" --max-greet 1 --threshold 6

  # 正式运行（最多 5 条，阈值 7，严格模式）
  python scenarios/boss/scripts/smart_match_greet.py --keyword "AI产品经理" --strict
"""

import argparse
import json
import logging
import sqlite3
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

import anthropic
from anthropic import APIStatusError, InternalServerError, RateLimitError
from skills.android.adb_runner import ADBRunner
from skills.boss import BOSSAutomationSkill, DialogType, PageState, normalize_card_title
from utils.logging_setup import setup_logger

# Module-level logger reference — used by helpers called BEFORE live_greet_loop()
# configures its handlers. setup_logger() is idempotent and configures this same
# logger object, so log lines emitted here flow to the same console/file.
logger = logging.getLogger("smart_match_greet")

# ─── 路径 ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parents[3]
DB_PATH = REPO_ROOT / "scenarios" / "boss" / "output" / "requirements.db"
RESUME_DEFAULT = Path("C:/Dev/projects/resume-renew/resume/current.md")

# ─── 弹窗分类 ─────────────────────────────────────────────────────────────────
_FATAL_DIALOGS = {DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED}
_SKIP_DIALOGS = {DialogType.JOB_OFFLINE}
_CONTINUE_DIALOGS = {DialogType.EXISTING_CHAT, DialogType.DISMISSED, DialogType.WARM_REMINDER}

# ─── Haiku Prompt ─────────────────────────────────────────────────────────────

# ─── Prompt 加载 ─────────────────────────────────────────────────────────────
# Prompt 从 scenarios/boss/config/prompts/*.md 读取，业务侧改 prompt 不用碰代码。
# 启动时缓存 + mtime 检测：编辑 md 后下一次 score_job / extract_resume_summary 调用自动读新版本。

_PROMPT_DIR = Path(__file__).parents[1] / "config" / "prompts"
_PROMPT_FILES = {
    "resume_extract": _PROMPT_DIR / "resume_extract.md",
    "match_score":    _PROMPT_DIR / "match_score.md",
    "strict_rules":   _PROMPT_DIR / "strict_rules.md",
}
_prompt_cache: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    """Read prompt from md file, reloading if mtime changed since last read.

    Cache by mtime so editing the md between calls takes effect on the next
    call — no need to restart the script.
    """
    path = _PROMPT_FILES[name]
    if not path.exists():
        raise FileNotFoundError(
            f"prompt 文件缺失：{path}。可从 git 历史恢复或参考 .py 里旧的硬编码常量。"
        )
    mtime = path.stat().st_mtime
    cached = _prompt_cache.get(name)
    if cached is not None:
        cached_text, cached_mtime = cached  # type: ignore[misc]
        if mtime == cached_mtime:
            return cached_text
    text = path.read_text(encoding="utf-8")
    _prompt_cache[name] = (text, mtime)
    # 模块顶层调用时 logger 还没初始化（main() 里才 setup_logger），用 print 兜底
    print(f"[prompt] 加载 {name} (mtime={mtime:.0f})")
    return text


# ─── DB 工具 ──────────────────────────────────────────────────────────────────

def _ensure_greetings_table(conn: sqlite3.Connection) -> None:
    """Create greetings, job_details, and job_visits tables if they don't exist (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_details (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key   TEXT UNIQUE,
            description TEXT,
            hr_name     TEXT,
            hr_title    TEXT,
            hr_active   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS greetings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_details_id INTEGER NOT NULL,
            keyword        TEXT,
            greeting_text  TEXT,
            sent_at        TEXT    NOT NULL,
            action         TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_visits (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT    NOT NULL,
            company       TEXT    NOT NULL,
            hr_name       TEXT,
            keyword       TEXT    NOT NULL,
            score         INTEGER,
            greeted       INTEGER NOT NULL DEFAULT 0,
            greeting_text TEXT,
            skip_reason   TEXT,
            visited_at    TEXT    NOT NULL
        )
    """)
    conn.commit()


def _is_already_greeted(
    conn: sqlite3.Connection, title: str, company: str, hr_name: str | None
) -> bool:
    """Check if a greeting has already been sent to this HR+job combo."""
    # Primary check: exact dedup_key in greetings history
    dedup_key = f"{normalize_card_title(title)}\t{company}\t{hr_name or ''}"
    row = conn.execute(
        """SELECT COUNT(g.id) FROM job_details jd
           JOIN greetings g ON g.job_details_id = jd.id
           WHERE jd.dedup_key = ?""",
        (dedup_key,),
    ).fetchone()
    if row and row[0] > 0:
        return True
    # Secondary check: same HR at same company already greeted in any job_visits row
    # Catches duplicate sends when title varies slightly across runs
    if hr_name:
        row2 = conn.execute(
            "SELECT COUNT(*) FROM job_visits WHERE hr_name = ? AND company = ? AND greeted = 1",
            (hr_name, company),
        ).fetchone()
        if row2 and row2[0] > 0:
            return True
    # Third layer: same normalized title + same company, ignoring hr_name.
    # Fixes cross-run hr_name inconsistency (empty on first run, populated on second).
    row3 = conn.execute(
        "SELECT COUNT(*) FROM job_visits WHERE title = ? AND company = ? AND greeted = 1",
        (normalize_card_title(title), company),
    ).fetchone()
    if row3 and row3[0] > 0:
        return True
    return False


def _record_greeting(
    conn: sqlite3.Connection,
    title: str,
    company: str,
    hr_name: str | None,
    keyword: str,
    greeting_text: str,
    action: str,
) -> None:
    """Upsert job_details entry and insert a greetings row."""
    dedup_key = f"{normalize_card_title(title)}\t{company}\t{hr_name or ''}"
    conn.execute(
        "INSERT OR IGNORE INTO job_details (dedup_key, hr_name) VALUES (?, ?)",
        (dedup_key, hr_name),
    )
    jd_id = conn.execute(
        "SELECT id FROM job_details WHERE dedup_key = ?", (dedup_key,)
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO greetings (job_details_id, keyword, greeting_text, sent_at, action)
           VALUES (?, ?, ?, ?, ?)""",
        (jd_id, keyword, greeting_text, time.strftime("%Y-%m-%dT%H:%M:%S"), action),
    )
    conn.commit()


def _record_visit(
    conn: sqlite3.Connection,
    title: str,
    company: str,
    hr_name: str | None,
    keyword: str,
    score: int | None,
    *,
    greeted: bool = False,
    greeting_text: str | None = None,
    skip_reason: str | None = None,
) -> None:
    """Record every job detail page visit regardless of whether a greeting was sent."""
    conn.execute(
        """INSERT INTO job_visits
               (title, company, hr_name, keyword, score, greeted, greeting_text, skip_reason, visited_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            normalize_card_title(title), company, hr_name or "", keyword, score,
            1 if greeted else 0, greeting_text, skip_reason,
            time.strftime("%Y-%m-%dT%H:%M:%S"),
        ),
    )
    conn.commit()


# ─── 评分 ──────────────────────────────────────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    """Extract JSON object from response, tolerating LLM's common formatting quirks.

    Handles three common cases:
    1. ```json\\n{...}\\n``` fenced block (preferred by LLM)
    2. ```\\n{...}\\n``` fenced block without language tag
    3. Bare text with a JSON object embedded (LLM added preamble like "Sure! Here is the result:")
       — falls back to scanning for the first '{' and last balanced '}'
    """
    # Case 1 & 2: fenced block
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            elif inner.startswith("JSON"):
                inner = inner[4:]
            text = inner.rsplit("```", 1)[0].strip()
            return text

    # Case 3: bare text — find first '{' and last balanced '}'
    first_brace = text.find("{")
    if first_brace < 0:
        return text
    # Track depth to find matching close
    depth = 0
    last_match = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text[first_brace:], start=first_brace):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_match = i
                break
    if last_match > first_brace:
        return text[first_brace:last_match + 1]
    return text


def _make_client() -> tuple[anthropic.Anthropic, anthropic.Anthropic | None]:
    """Return (primary_client, fallback_client). fallback is None if not configured.

    Loads .env from the repo root with override=True so the env in this script
    is always consistent with what the .env file declares, regardless of any
    shell-set values that might point at a different relay.

    Primary uses the minnimax relay + MiniMax-M3.
    Fallback uses the klugai relay + claude-3-5-haiku-20241022
    (klugai supports standard Claude model IDs, not MiniMax-M* names).
    _call_with_fallback handles the model name switch via fallback_model=.
    """
    import os
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=True)

    primary_key = os.environ.get("ANTHROPIC_BACKUP_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    primary_url = os.environ.get("ANTHROPIC_BACKUP_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
    if not primary_key:
        raise RuntimeError("No API key found in .env (ANTHROPIC_API_KEY or ANTHROPIC_BACKUP_API_KEY)")
    primary = anthropic.Anthropic(api_key=primary_key, base_url=primary_url)

    backup_key = os.environ.get("ANTHROPIC_API_KEY")
    backup_url = os.environ.get("ANTHROPIC_BASE_URL")
    fallback = None
    if backup_key and (backup_key != primary_key or backup_url != primary_url):
        fallback = anthropic.Anthropic(api_key=backup_key, base_url=backup_url)
    return primary, fallback


def _extract_reset_at(exc: Exception) -> datetime | None:
    """Return the UTC reset time embedded in a klugai cost-limit 429 response, or None."""
    try:
        body = getattr(exc, "body", None)
        if not isinstance(body, dict):
            return None
        # klugai top-level: {"resetAt": "2026-...", ...}
        reset_str = body.get("resetAt") or (body.get("error") or {}).get("resetAt")
        if reset_str:
            return datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def _call_with_fallback(
    primary: anthropic.Anthropic,
    fallback: anthropic.Anthropic | None,
    fallback_model: str | None = None,
    **kwargs,
) -> anthropic.types.Message:
    """Call primary client; on transient errors switch to fallback if available.

    As of 2026-09-16 the relays we use intermittently return 500 ("No available
    Claude accounts support the requested model") or 403 ("not allowed in your
    plan"). Both are recoverable by retrying on the same client, and falling
    back to the secondary relay if the primary keeps failing.

    Args:
        fallback_model: If set, overrides the `model` kwarg when calling the
            fallback client — useful when primary/fallback relays advertise
            different model namespaces (e.g. minnimax uses "MiniMax-M3" while
            klugai uses standard Claude model IDs).
    """
    import time as _time

    last_exc: Exception | None = None
    for client, label in ((primary, "primary"), (fallback, "fallback") if fallback else (None, None)):
        if client is None:
            continue
        call_kwargs = dict(kwargs)
        if label == "fallback" and fallback_model:
            call_kwargs["model"] = fallback_model
        for attempt in range(2):
            try:
                return client.messages.create(**call_kwargs)
            except (RateLimitError, InternalServerError, anthropic.APIStatusError) as exc:
                last_exc = exc
                wait = 1.5 * (attempt + 1)
                logger.warning(
                    "[%s] 暂态错误 %s (attempt %d)，%.1fs 后重试: %s",
                    label, type(exc).__name__, attempt + 1, wait, str(exc)[:160],
                )
                _time.sleep(wait)
            except Exception:
                # Non-transient (auth, validation, etc.) — don't retry, don't fall back.
                raise
    assert last_exc is not None  # only reachable if both clients raised
    raise last_exc


def extract_resume_summary(
    primary: anthropic.Anthropic,
    resume: str,
    fallback: anthropic.Anthropic | None = None,
) -> str:
    """Call Haiku once to distill the full resume into a compact structured JSON string."""
    prompt = _load_prompt("resume_extract").format(resume=resume)
    response = _call_with_fallback(
        primary, fallback,
        fallback_model="claude-haiku-4-5-20251001",
        model="MiniMax-M3",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _strip_json_fences(response.content[0].text.strip())
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return resume[:2000]


def score_job(
    primary: anthropic.Anthropic,
    resume_summary: str,
    job: dict,
    strict: bool = False,
    fallback: anthropic.Anthropic | None = None,
) -> dict:
    """Call Claude Haiku to score one job and generate a personalized greeting."""
    description = job.get("description") or ""
    prompt = _load_prompt("match_score").format(
        resume_summary=resume_summary,
        title=job.get("title", ""),
        company=job.get("company", ""),
        salary=job.get("salary_raw") or "未知",
        experience=job.get("experience") or "未知",
        company_info=job.get("company_info") or "未知",
        hr_name=job.get("hr_name") or "（未知）",
        description_excerpt=description if description else "（无）",
        requirements=job.get("requirements_text") or "  无标签",
        strict_rules=_load_prompt("strict_rules") if strict else "",
    )
    response = _call_with_fallback(
        primary, fallback,
        fallback_model="claude-haiku-4-5-20251001",
        model="MiniMax-M3",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _strip_json_fences(response.content[0].text.strip())
    result = json.loads(text)
    return {**job, **result}


def _parse_salary_low_k(salary_raw: str) -> float | None:
    """Return lower bound monthly salary in K, or None if unparseable."""
    import re
    if not salary_raw:
        return None
    raw = salary_raw.strip().upper()
    m = re.search(r"·(\d+)薪", raw)
    multiplier = int(m.group(1)) if m else 12
    rng = re.search(r"(\d+(?:\.\d+)?)[- ](\d+(?:\.\d+)?)K", raw)
    if rng:
        return float(rng.group(1)) * multiplier / 12
    single = re.search(r"(\d+(?:\.\d+)?)K", raw)
    if single:
        return float(single.group(1)) * multiplier / 12
    return None


# ─── App 导航 ─────────────────────────────────────────────────────────────────

def _return_to_job_list(skill: BOSSAutomationSkill, keyword: str = "") -> bool:
    # Dismiss any overlay first
    if skill.get_current_page() == PageState.DIALOG:
        skill.press_back()
        time.sleep(0.8)

    # Fast path: up to 3 Back presses, checking after each.
    # After sending a greeting the back-stack is: chat → detail → list,
    # so we need two presses, not one.
    #
    # IMPORTANT: check via get_current_page() == JOB_LIST, NOT
    # wait_for_element("tv_position_name"). tv_position_name renders on
    # BOTH the search-results page AND the home recommend feed, so the
    # element-based check used to "succeed" while we were actually parked
    # on the wrong tab — a one-BACK press from the detail page lands on
    # the recommend feed, which has tv_position_name but is NOT a job list.
    for _ in range(3):
        skill.press_back()
        time.sleep(1.2)
        if skill.get_current_page() == PageState.JOB_LIST:
            return True

    # Fallback: navigate to jobs tab and re-search (only when keyword is known)
    if keyword:
        skill.navigate_to_tab("jobs")
        time.sleep(1.0)
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
        # Wait briefly for the page to settle, then verify it's actually JOB_LIST.
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if skill.get_current_page() == PageState.JOB_LIST:
                return True
            time.sleep(0.5)
        return False

    # No keyword → just try more backs
    for _ in range(3):
        skill.press_back()
        time.sleep(0.8)
        if skill.get_current_page() == PageState.JOB_LIST:
            return True
    return False


def _count_today_greeted(db_conn: sqlite3.Connection) -> int:
    """Return the number of greetings sent today (UTC+8 calendar day)."""
    today = datetime.now().strftime("%Y-%m-%d")
    row = db_conn.execute(
        "SELECT COUNT(*) FROM greetings WHERE sent_at >= ?",
        (today + " 00:00:00",),
    ).fetchone()
    return row[0] if row else 0


def _with_screen_heartbeat(skill: "BOSSAutomationSkill", fn):
    """Keep device awake during fn() using KEYCODE_WAKEUP every 5 s.

    KEYCODE_WAKEUP (224) signals the screen to stay on without interacting
    with the App UI, avoiding accidental back-swipe from left-edge taps.
    """
    stop = threading.Event()

    def _worker():
        while True:
            try:
                skill._adb("shell input keyevent 224")
            except Exception:
                pass
            if stop.wait(5.0):
                break

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        return fn()
    finally:
        stop.set()
        t.join(2.0)


def live_greet_loop(
    resume_summary: str,
    client: anthropic.Anthropic,
    keyword: str,
    threshold: int,
    max_greet: int,
    strict: bool,
    min_salary_k: float,
    verify_send: bool,
    device_id: str | None,
    db_conn: sqlite3.Connection,
    score_only: bool = False,
    fallback: anthropic.Anthropic | None = None,
) -> dict:
    """
    Single-phase live loop: for each job card, enter detail page → score → send if OK.
    Returns result dict with greeted / skipped / errors lists.
    """
    logger = setup_logger("smart_match_greet", log_dir="./logs/boss")
    adb = ADBRunner()
    skill = BOSSAutomationSkill(adb, device_id=device_id)
    result: dict = {"greeted": [], "skipped": [], "errors": []}

    # Keep screen on (all charge types) + push lock/screen-off timeouts to 30 min.
    # Disable Adaptive Sleep (Pixel face-detection that bypasses screen_off_timeout).
    # Save originals so the finally block restores exactly what the user had.
    _, _orig_timeout      = skill._adb("shell settings get system screen_off_timeout")
    _, _orig_lock_timeout = skill._adb("shell settings get secure lock_screen_lock_after_timeout")
    _, _orig_adaptive     = skill._adb("shell settings get secure adaptive_sleep")
    _orig_timeout      = (_orig_timeout or "").strip()      or "60000"
    _orig_lock_timeout = (_orig_lock_timeout or "").strip() or "60000"
    _orig_adaptive     = (_orig_adaptive or "").strip()     or "1"

    skill._adb("shell svc power stayon true")
    skill._adb("shell settings put system screen_off_timeout 1800000")
    skill._adb("shell settings put secure lock_screen_lock_after_timeout 1800000")
    skill._adb("shell settings put secure adaptive_sleep 0")
    try:
        return _run_loop(skill, resume_summary, client, keyword, threshold, max_greet,
                         strict, min_salary_k, verify_send, db_conn, score_only, result, logger,
                         fallback=fallback)
    finally:
        skill._adb("shell svc power stayon false")
        skill._adb(f"shell settings put system screen_off_timeout {_orig_timeout}")
        skill._adb(f"shell settings put secure lock_screen_lock_after_timeout {_orig_lock_timeout}")
        skill._adb(f"shell settings put secure adaptive_sleep {_orig_adaptive}")


def _find_next_job(
    skill: "BOSSAutomationSkill",
    visited_keys: set[str],
    initial_jobs: list,
) -> object | None:
    """Return the next unvisited job visible on the CURRENT screen (fresh coords).

    Implements the "正确模式（增量）" documented in
    scenarios/boss/docs/atomic_operations.md § 4.1:

        陷阱：调用 `_return_to_job_list()` 重新搜索后，列表回到顶部，
        旧的 `tap_y` 指向不同的职位。必须在每次导航前重新获取坐标。

    Single `get_ui_hierarchy()` + `get_job_list()` call — no scrolling.
    Returns a JobInfo whose fields come from `initial_jobs` (for stable
    dedup_key fields like hr_name that list cards often lack) and whose
    tap_x/tap_y come from the CURRENT screen.

    Args:
        skill: Boss skill instance.
        visited_keys: Set of normalize_card_title(title) + "\t" + company
            strings that this loop has already tried (greeted, skipped,
            or detail-mismatch).
        initial_jobs: The 60 cards collected at the top of _run_loop.

    Returns:
        Next unvisited JobInfo with fresh tap coordinates, or None if
        every visible card has been visited (caller should scroll_down
        or break).
    """
    xml = skill.get_ui_hierarchy()
    visible = skill.get_job_list(xml=xml)  # List[JobInfo]，只含当前屏幕

    # Build a map of (normalize_title + company) → JobInfo from initial_jobs.
    # We use this to fill in fields the list-card XML often lacks (e.g. hr_active)
    # and to compare against visited_keys.
    by_key: dict[str, object] = {
        f"{normalize_card_title(j.title)}\t{j.company or ''}": j for j in initial_jobs
    }

    for v in visible:
        v_key = f"{normalize_card_title(v.title)}\t{v.company or ''}"
        if v_key in visited_keys:
            continue
        full = by_key.get(v_key)
        if full is None:
            # 屏幕卡不在 initial 集合里（重新搜索后 Boss 换了排序/出新卡）——
            # 用屏幕卡自身返回：tap 坐标新鲜，字段可能不全，
            # 但 Layer 1/2/3 校验已能兜底，不应静默丢弃新卡。
            return v
        # 用 full 的完整字段 + v 的新鲜坐标
        return replace(full, tap_x=v.tap_x, tap_y=v.tap_y)

    return None


def _run_loop(
    skill: "BOSSAutomationSkill",
    resume_summary: str,
    client: anthropic.Anthropic,
    keyword: str,
    threshold: int,
    max_greet: int,
    strict: bool,
    min_salary_k: float,
    verify_send: bool,
    db_conn: sqlite3.Connection,
    score_only: bool,
    result: dict,
    logger: logging.Logger,
    fallback: anthropic.Anthropic | None = None,
) -> dict:
    # 确认屏幕亮着且手机已解锁（即 UI 中有 App 内容而非只有 SystemUI 锁屏）
    if not skill._is_screen_on():
        skill._wake_screen()
        time.sleep(2.0)
    if not skill._is_screen_on():
        logger.warning("⚠️  手机未唤醒，等待解锁（最多 120 秒）…")
        _unlocked = False
        for _ in range(12):
            time.sleep(10)
            skill._wake_screen()
            time.sleep(2.0)
            if skill._is_screen_on():
                _unlocked = True
                break
        if not _unlocked:
            result["errors"].append("手机锁屏未解，无法启动")
            return result

    # Extra check: screen may be "Awake" but the lock screen is still in front.
    # The lock screen's root window is always "legacy_window_root" (com.android.systemui).
    # When any real app is in the foreground the root changes to that app's window.
    def _is_lock_screen(xml: str) -> bool:
        return xml is not None and "legacy_window_root" in xml[:400]

    _ui_xml = skill.get_ui_hierarchy()
    if _is_lock_screen(_ui_xml):
        logger.warning("⚠️  检测到锁屏，请解锁手机后继续（最多等待 60 秒）…")
        _unlocked2 = False
        for _ in range(12):
            time.sleep(5)
            skill._wake_screen()
            time.sleep(1.0)
            if not _is_lock_screen(skill.get_ui_hierarchy()):
                _unlocked2 = True
                break
        if not _unlocked2:
            result["errors"].append("手机锁屏未解，无法启动")
            return result

    logger.info("[1/3] 启动 Boss直聘…")
    skill._adb(f"shell am force-stop {skill.APP_PACKAGE}")
    time.sleep(2.0)
    # Launch directly to MainActivity to bypass WelcomeActivityAlias1 which often shows
    # a full-screen WebView promotional page that covers the bottom nav and et_search.
    ok_start, _ = skill._adb(
        f"shell am start -W "
        f"{skill.APP_PACKAGE}/.module.main.activity.MainActivity"
    )
    if not ok_start:
        # Fallback to monkey launch
        if not skill.launch(wait=6.0):
            result["errors"].append("无法启动 App，请确认 ADB 已连接")
            return result
    else:
        time.sleep(5.0)
    # Collapse any notification shade that may have expanded during startup.
    skill._adb("shell cmd statusbar collapse")
    time.sleep(0.5)

    # Wait for BOSS to reach the foreground and get to a page where et_search is visible.
    # Strategy:
    #   1. If et_search is already visible → done.
    #   2. Try navigate_to_tab("recommend") to switch to the home tab.
    #   3. If that fails (resource-id mismatch in current BOSS version), press BACK once
    #      to dismiss any full-screen overlay (e.g. WebView promotion), then retry.
    #      IMPORTANT: stop pressing BACK as soon as et_search appears — over-pressing
    #      backs us out past the home page into the Android launcher.
    for _startup_try in range(10):
        xml = skill.get_ui_hierarchy()
        # BOSS not present → lock screen, home screen, or lingering overlay.
        if not xml or "com.hpbr.bosszhipin" not in xml:
            if _startup_try == 0:
                logger.info("  BOSS 未在前台，重新拉起…")
                skill._adb("shell cmd statusbar collapse")
                skill._adb(f"shell am start {skill.APP_PACKAGE}/.module.main.activity.MainActivity")
                time.sleep(3.0)
            else:
                logger.warning("  ⚠️  等待手机解锁或 BOSS 显示… (第 %d 次)", _startup_try)
                time.sleep(3.0)
            continue
        # If search bar (et_search) is already visible we're on the home page — proceed.
        if skill.find_element(resource_id=skill.ELEMENTS["search_bar"], xml=xml):
            logger.info("  搜索框已可见，直接进入搜索")
            break
        d = skill.detect_dialog(xml)
        if d == DialogType.DAILY_LIMIT:
            result["errors"].append("今日沟通名额已满")
            return result
        if d not in ("none", DialogType.EXISTING_CHAT, DialogType.DISMISSED):
            logger.info("  启动弹窗 [%s]，关闭中…", d)
            skill.dismiss_dialog(d, xml)
            time.sleep(1.2)
            continue
        # Dismiss unrecognised generic overlays (update nags, ads, etc.)
        _dismissed_unknown = False
        for _btn_txt in ("我知道了", "关闭", "以后再说", "跳过"):
            _btn = skill.find_element(text=_btn_txt, xml=xml)
            if _btn and _btn.center:
                logger.info("  发现弹窗按钮「%s」，关闭中…", _btn_txt)
                skill.tap(*_btn.center)
                time.sleep(1.0)
                _dismissed_unknown = True
                break
        if _dismissed_unknown:
            continue
        # Try native bottom-nav tab switch first.
        if skill.navigate_to_tab("recommend"):
            time.sleep(1.5)
            break
        # navigate_to_tab failed (resource-id mismatch): press BACK once to dismiss any
        # full-screen overlay (WebView promo, activity on top of home), then re-check.
        logger.info("  导航到推荐页失败，尝试返回上一页…")
        skill._adb("shell input keyevent 4")
        time.sleep(1.5)

    logger.info("[2/3] 搜索职位：「%s」…", keyword)
    if not skill.browse_jobs(keyword):
        # Save debug XML for diagnosis
        _dbg = skill.get_ui_hierarchy()
        if _dbg:
            _dbg_path = Path(skill.output_dir) / "debug_search_fail.xml"
            _dbg_path.write_text(_dbg[:12000], encoding="utf-8")
            logger.error("  搜索失败，调试 XML 已保存至 %s", _dbg_path)
        result["errors"].append(f"搜索失败：{keyword}")
        return result
    if not skill.wait_for_element("com.hpbr.bosszhipin:id/tv_position_name", timeout=6.0):
        result["errors"].append("职位列表加载超时")
        return result

    logger.info("[3/3] 采集职位列表（基线，用于补全字段）…")
    jobs = skill.scroll_job_list(n_jobs=60, max_scrolls=20)
    logger.info("  ✓ 采集到 %d 条职位卡片", len(jobs))

    visited_keys: set[str] = set()
    greeted_count = 0
    no_target_scrolls = 0
    processed = 0  # 用于日志显示的「第 N 条」计数器

    # ── 每日上限保护（主动检查，不依赖弹窗被动检测）──
    DAILY_HARD_LIMIT = 110  # 到达此数直接退出（留 ~10 条 buffer）
    DAILY_WARN_LIMIT = 90   # 到达此数打 WARNING
    today_count = _count_today_greeted(db_conn)
    logger.info("  今日已发送 %d 条招呼", today_count)
    if today_count >= DAILY_HARD_LIMIT:
        logger.error(
            "今日已发送 %d 条，已达每日上限（%d），退出。明日再运行。",
            today_count, DAILY_HARD_LIMIT,
        )
        return result
    if today_count >= DAILY_WARN_LIMIT:
        logger.warning(
            "今日已发送 %d 条，接近每日上限（%d），请注意剩余名额。",
            today_count, DAILY_HARD_LIMIT,
        )

    while greeted_count < max_greet:
        # 0. 如果上一条操作后离开了列表页（detail / chat / search-results），
        #    先回到 JOB_LIST，再 _find_next_job 看屏幕
        if skill.get_current_page() != PageState.JOB_LIST:
            if not _return_to_job_list(skill, keyword):
                result["errors"].append("无法返回职位列表")
                break

        # 1. 在当前屏幕找一个未访问的目标（按 doc §4.1：每次导航前重取坐标）
        target = _find_next_job(skill, visited_keys, jobs)
        if target is None:
            no_target_scrolls += 1
            if no_target_scrolls > 3:
                logger.info("  列表已无未访问目标（连滑 %d 次都没新卡），结束", no_target_scrolls - 1)
                break
            logger.info("  当前屏幕无未访问目标，列表下滑 %d/3", no_target_scrolls)
            skill.scroll_down(start_y=1800, end_y=600)
            time.sleep(0.8)
            continue
        no_target_scrolls = 0

        processed += 1
        title = target.title or ""
        company = target.company or ""
        hr_name = target.hr_name or ""
        visited_keys.add(f"{normalize_card_title(title)}\t{company}")

        # 去重检查（已落库 greetings 的不再打招呼）
        if _is_already_greeted(db_conn, title, company, hr_name):
            logger.info("  [%d] 跳过（已打过招呼）：%s", processed, title)
            result["skipped"].append(f"{title}（{company}）：已打过招呼")
            continue

        # 薪资预过滤（利用列表卡片信息快速跳过）
        if min_salary_k > 0:
            low_k = _parse_salary_low_k(target.salary or "")
            if low_k is not None and low_k < min_salary_k:
                result["skipped"].append(f"{title}：薪资偏低")
                continue

        # 只检查屏幕是否亮着（详情页 get_current_page() 返回 UNKNOWN 属正常，
        # 下一步 navigate_to_job 会导航到正确页面）
        if not skill._is_screen_on():
            logger.warning("⚠️  屏幕熄灭，等待解锁（最多 90 秒）…")
            _recovered = False
            for _ in range(9):
                time.sleep(10)
                skill._wake_screen()
                time.sleep(2.0)
                if skill._is_screen_on():
                    logger.info("  ↩ 解锁后恢复成功")
                    _recovered = True
                    break
            if not _recovered:
                result["errors"].append("屏幕未解锁，停止任务")
                break

        # 进入详情页（用 _find_next_job 给的新鲜坐标）
        logger.info("  [%d] → %s（%s）", processed, title, company)
        if not skill.find_and_navigate_to_job(title, company, target):
            result["errors"].append(f"{title}：导航失败")
            continue

        # 等待详情页加载并验证职位标题 + 公司名都匹配
        # 防止：(a) 列表 tap 错卡片（旧坐标错位） (b) Boss 显示上一个职位的缓存数据
        _detail_ok = False
        for _attempt in range(3):
            if not skill.wait_for_element("com.hpbr.bosszhipin:id/tv_job_name", timeout=3.0):
                time.sleep(1.0)
                continue
            _quick = skill.get_job_detail()
            _detail_title = _quick.get("title") or ""
            _detail_company = _quick.get("company") or ""
            _title_match = (
                not title
                or not _detail_title
                or title in _detail_title       # 列表 title 是详情 title 的子串
                or _detail_title in title       # 详情 title 被列表 title 截断
            )
            _company_match = (
                not company
                or not _detail_company
                or company[:6] in _detail_company
                or _detail_company[:6] in company
            )
            if _title_match and _company_match:
                _detail_ok = True
                break
            logger.warning(
                "  详情页不匹配（期望 title=%s/company=%s，实际 title=%s/company=%s），重试 %d/3",
                title, company, _detail_title, _detail_company, _attempt + 1,
            )
            time.sleep(1.0)
        if not _detail_ok:
            result["errors"].append(f"{title}：详情页加载超时或内容不匹配")
            _record_visit(db_conn, title, company, hr_name, keyword, None,
                          skip_reason="详情页title或公司名不匹配")
            continue

        # 检测网络异常占位页
        raw_check = skill.get_job_detail()
        if any("网络异常" in t for t in raw_check.get("raw_texts", [])):
            logger.warning("  详情页网络异常，跳过")
            result["errors"].append(f"{title}：详情页网络异常")
            continue

        # 展开"查看更多"，重新提取完整详情
        skill.expand_description()
        detail = skill.get_job_detail()

        job_dict = {
            "title":            detail.get("title") or title,
            "company":          detail.get("company") or company,
            "salary_raw":       detail.get("salary") or target.salary,
            "experience":       detail.get("experience") or "",
            "company_info":     detail.get("company_info") or "",
            "hr_name":          detail.get("hr_name") or hr_name,
            "hr_title":         detail.get("hr_title") or (target.hr_title if hasattr(target, "hr_title") else ""),
            "hr_active":        detail.get("hr_active") or (target.hr_active if hasattr(target, "hr_active") else ""),
            "description":      detail.get("description") or "",
            "requirements_text": "  无标签（请从JD描述中判断）",
        }
        # Prefer detail-page hr_name (more complete); fall back to card-level hr_name.
        # Using this for all DB writes ensures consistent dedup across runs.
        effective_hr_name = job_dict.get("hr_name") or hr_name

        # Haiku 评分 + 生成打招呼（heartbeat 防止 ~30s API 调用期间锁屏）
        try:
            scored = _with_screen_heartbeat(
                skill, lambda: score_job(client, resume_summary, job_dict, strict=strict, fallback=fallback)
            )
        except Exception as exc:
            reset_at = _extract_reset_at(exc)
            if reset_at:
                wait_secs = max(5.0, (reset_at - datetime.now(timezone.utc)).total_seconds() + 10.0)
                logger.warning(
                    "  两端 API 配额耗尽，等待 %.0f 秒至 %s UTC 后重试：%s",
                    wait_secs, reset_at.strftime("%H:%M:%S"), title,
                )
                time.sleep(wait_secs)
                try:
                    scored = _with_screen_heartbeat(
                        skill, lambda: score_job(client, resume_summary, job_dict, strict=strict, fallback=fallback)
                    )
                except Exception as exc2:
                    logger.error("  配额恢复后仍失败：%s — %s", title, exc2)
                    result["errors"].append(f"{title}：评分失败（配额恢复后仍失败：{exc2}）")
                    continue
            else:
                logger.error("  评分失败：%s — %s", title, exc)
                result["errors"].append(f"{title}：评分失败（{exc}）")
                continue

        score = scored.get("match_score", 0)
        greeting = scored.get("greeting", "")
        logger.info("  分数 %d/10  %s", score, scored.get("top_matches", [])[:2])

        if score < threshold or not greeting:
            logger.info("  → 跳过（%d < %d）", score, threshold)
            result["skipped"].append(f"{title}（{company}）：{score}/10 低于阈值")
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason=f"低分({score}<{threshold})")
            continue

        logger.info("  ✓ 达标！打招呼：%s…", greeting[:60])

        if score_only:
            result["skipped"].append(
                f"{title}（{company}）：{score}/10 ✓ [score_only]\n    {greeting}"
            )
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason="score_only")
            continue

        # 发送打招呼
        try:
            dialog = skill.detect_dialog()
            if dialog != DialogType.NONE:
                skill.dismiss_dialog(dialog)
                if dialog in _FATAL_DIALOGS:
                    result["errors"].append(f"致命弹窗（{dialog}），终止")
                    break
                if dialog in _SKIP_DIALOGS:
                    result["skipped"].append(f"{title}（{dialog}）")
                    _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                                  skip_reason=f"弹窗跳过({dialog})")
                    continue

            if not skill.tap_element("chat_btn"):
                result["errors"].append(f"{title}：无法打开聊天页")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="无聊天按钮")
                continue

            # ── 温馨提示弹窗可能在进入聊天页瞬间出现，提前dismiss ──
            time.sleep(0.8)
            _pre_chat_dialog = skill.detect_dialog()
            if _pre_chat_dialog != DialogType.NONE:
                logger.info("  进入聊天页前检测到弹窗：%s，尝试关闭", _pre_chat_dialog)
                skill.dismiss_dialog(_pre_chat_dialog)
                if _pre_chat_dialog in _FATAL_DIALOGS:
                    result["errors"].append(f"进入聊天前致命弹窗（{_pre_chat_dialog}），终止")
                    break
                if _pre_chat_dialog not in _CONTINUE_DIALOGS:
                    result["skipped"].append(f"{title}（{_pre_chat_dialog}）")
                    _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                                  skip_reason=f"弹窗跳过({_pre_chat_dialog})")
                    continue
                time.sleep(0.5)  # 等弹窗关闭动画

            if not skill.wait_for_element(
                "com.hpbr.bosszhipin:id/editText_with_scrollbar", timeout=4.0
            ):
                result["errors"].append(f"{title}：聊天页加载超时")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="聊天页超时")
                continue

            # ── 第 2 层校验：聊天页的 HR / 职位 / 公司名是否真的是目标 ──
            # 防止：详情页显示 HR A 但聊天页跳到 HR B（同公司多 HR 代理），
            # 或者详情页校验通过但聊天页又跳回了上个会话的窗口。
            _chat_ok = False
            _chat_xml = skill.get_ui_hierarchy()
            _chat_title_elem = skill.find_element(
                resource_id="com.hpbr.bosszhipin:id/tv_title", xml=_chat_xml
            )
            _chat_position_elem = skill.find_element(
                resource_id="com.hpbr.bosszhipin:id/tv_position_name", xml=_chat_xml
            )
            _chat_company_elem = skill.find_element(
                resource_id="com.hpbr.bosszhipin:id/tv_company_name", xml=_chat_xml
            )
            _chat_title_text = (_chat_title_elem.text if _chat_title_elem else "") or ""
            _chat_position_text = (_chat_position_elem.text if _chat_position_elem else "") or ""
            _chat_company_text = (_chat_company_elem.text if _chat_company_elem else "") or ""

            _chat_title_match = (
                not title
                or not _chat_position_text
                or title in _chat_position_text
                or _chat_position_text in title
            )
            _chat_company_match = (
                not company
                or not _chat_company_text
                or company[:6] in _chat_company_text
                or _chat_company_text[:6] in company
            )
            _target_hr_surname = (effective_hr_name or "")[:2]
            _chat_hr_match = (
                not _target_hr_surname
                or _target_hr_surname in _chat_title_text
            )
            if _chat_title_match and _chat_company_match and _chat_hr_match:
                _chat_ok = True
            else:
                logger.error(
                    "  聊天页内容不匹配 — 目标 title=%s company=%s hr=%s，"
                    "实际 chat_position=%s chat_company=%s chat_hr=%s。",
                    title, company, effective_hr_name,
                    _chat_position_text, _chat_company_text, _chat_title_text,
                )
                _dbg_chat = Path(skill.output_dir) / "debug_chat_mismatch.xml"
                _dbg_chat.write_text(_chat_xml or "", encoding="utf-8")
                result["errors"].append(
                    f"{title}：聊天页 HR/职位/公司不匹配 "
                    f"（hr={_chat_title_text!r}, pos={_chat_position_text!r}, co={_chat_company_text!r}）"
                )
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="聊天页HR/职位/公司不匹配")
                # 不发送、不污染 greetings 表，直接 continue
                continue

            dialog2 = skill.detect_dialog()
            if dialog2 != DialogType.NONE:
                skill.dismiss_dialog(dialog2)
                if dialog2 in _FATAL_DIALOGS:
                    result["errors"].append(f"致命弹窗（{dialog2}），终止")
                    break
                if dialog2 not in _CONTINUE_DIALOGS:
                    result["skipped"].append(f"{title}（{dialog2}）")
                    _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                                  skip_reason=f"弹窗跳过({dialog2})")
                    continue

            ok = skill.send_greeting(greeting, verify=verify_send)

            # ── 发送后补检弹窗（DAILY_LIMIT 可能在发送完成后才弹出）──
            _post_dialog = skill.detect_dialog()
            if _post_dialog != DialogType.NONE:
                skill.dismiss_dialog(_post_dialog)
                if _post_dialog in _FATAL_DIALOGS:
                    logger.error("  发送后出现致命弹窗（%s），终止", _post_dialog)
                    result["errors"].append(f"发送后致命弹窗（{_post_dialog}），终止")
                    break

            if ok:
                action = "打招呼成功"
                result["greeted"].append(
                    {"job": title, "company": company, "score": score, "action": action, "greeting": greeting}
                )
                logger.info("  ✓ 打招呼成功 — %s", greeting[:40])
                greeted_count += 1
                today_count += 1
                if greeted_count % 10 == 0:
                    logger.info(
                        "  📊 进度：本次已发 %d/%d，今日累计约 %d 条",
                        greeted_count, max_greet, today_count,
                    )
                _record_greeting(db_conn, title, company, effective_hr_name, keyword, greeting, action)
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              greeted=True, greeting_text=greeting)
                # ── 第 3 层兜底：刚发的招呼 hr_name 不能为空 ──
                # 防止：effective_hr_name 为空时把空字符串写到 job_details 表，
                # 影响后续按 hr_name + company 去重。
                # greetings 表只存 FK；hr_name 存在 job_details 里，需要 JOIN。
                _last = db_conn.execute(
                    """SELECT jd.hr_name, jd.dedup_key
                       FROM greetings g
                       JOIN job_details jd ON g.job_details_id = jd.id
                       ORDER BY g.id DESC LIMIT 1"""
                ).fetchone()
                if _last and (not _last[0] or _last[0] == ""):
                    logger.warning(
                        "  兜底告警：刚发的招呼 hr_name 为空（dedup_key=%s），"
                        "后续仅靠 title+company 去重",
                        _last[1],
                    )
            else:
                result["errors"].append(f"{title}：send_greeting 失败（未找到发送按钮或文字未进入输入框）")
                logger.error("  ✗ 打招呼失败 — %s", title)
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="发送失败")
            time.sleep(1.0)

        except Exception as exc:
            logger.error("处理 '%s' 异常", title, exc_info=True)
            result["errors"].append(f"{title}: {exc}")
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason=f"异常:{exc}")
            if not skill._is_screen_on():
                break

    return result


# ─── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Boss直聘实时评分 + 个性化打招呼（单阶段）")
    parser.add_argument("--keyword", required=True, help="搜索关键词（必需）")
    parser.add_argument(
        "--resume",
        default=str(RESUME_DEFAULT),
        help=f"简历 Markdown 路径（默认：{RESUME_DEFAULT}）",
    )
    parser.add_argument("--threshold", type=int, default=6, help="最低匹配分数 0-10（默认 6）")
    parser.add_argument("--max-greet", type=int, default=5, help="最多发送打招呼数（默认 5）")
    parser.add_argument("--score-only", action="store_true", help="仅评分打印，不发送打招呼")
    parser.add_argument("--strict", action="store_true", help="严格评分模式，减少分数虚高")
    parser.add_argument("--min-salary", type=float, default=0, help="月薪下限（K），低于此值跳过（默认 0=不过滤）")
    parser.add_argument("--no-verify", action="store_true", help="发送后不验证消息已发出")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    args = parser.parse_args()

    # ── 读取简历 ────────────────────────────────────────────────────────────────
    resume_path = Path(args.resume)
    if not resume_path.exists():
        print(f"错误：简历文件不存在：{resume_path}")
        return 1
    resume_text = resume_path.read_text(encoding="utf-8")
    print(f"✓ 简历已加载：{resume_path.name}（{len(resume_text)} 字符）")

    # ── 提取简历画像（一次性 Haiku 调用）──────────────────────────────────────
    client, fallback = _make_client()
    print("⏳ 提取简历画像（Haiku）…", end="", flush=True)
    resume_summary = extract_resume_summary(client, resume_text, fallback=fallback)
    print(" 完成")

    # ── 初始化 DB（确保表存在）──────────────────────────────────────────────
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db_conn = sqlite3.connect(DB_PATH)
    _ensure_greetings_table(db_conn)

    mode_label = "严格模式" if args.strict else "标准模式"
    score_label = "仅评分" if args.score_only else f"最多发送 {args.max_greet} 条"
    print(
        f"\n🤖 单阶段循环（关键词：{args.keyword}，阈值：{args.threshold}，{mode_label}，{score_label}）"
    )

    result = live_greet_loop(
        resume_summary=resume_summary,
        client=client,
        keyword=args.keyword,
        threshold=args.threshold,
        max_greet=args.max_greet,
        strict=args.strict,
        min_salary_k=args.min_salary,
        verify_send=not args.no_verify,
        device_id=args.device,
        db_conn=db_conn,
        score_only=args.score_only,
        fallback=fallback,
    )
    db_conn.close()

    # ── 结果摘要 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("执行结果摘要")
    print("=" * 60)
    if args.score_only:
        above = [s for s in result["skipped"] if "✓" in s]
        print(f"✓ 达到阈值 {args.threshold}+：{len(above)} 条")
        for item in above:
            print(f"  · {item}")
    else:
        print(f"✓ 打招呼成功：{len(result['greeted'])} 条")
        for item in result["greeted"]:
            print(f"  · {item['job']}（{item['company']}）{item['score']}/10 —— {item['action']}")
            print(f"    {item['greeting'][:80]}")
    if result["skipped"]:
        print(f"- 跳过：{len(result['skipped'])} 条")
    if result["errors"]:
        print(f"✗ 错误：{len(result['errors'])} 个")
        for err in result["errors"]:
            print(f"  · {err}")

    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
