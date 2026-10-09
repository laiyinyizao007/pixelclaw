#!/usr/bin/env python3
"""
smart_match_greet.py — 智联招聘实时评分 + 自动打招呼（单阶段 App 内循环）

工作流：
  1. 打开智联招聘 → 关闭安全弹窗 → 搜索关键词 → 等待列表加载
  2. 逐条：进详情页 → 提取完整 JD → Haiku 评分 →
     分数达标则点「先聊聊」自动发送默认招呼 → 记录 DB 去重 → 返回列表
  3. 达到 max_greet 或列表扫完为止

关键差异（vs BOSS直聘）：
  - 「先聊聊」点击即自动发送默认打招呼，无需输入自定义文字
  - HR 活跃度显示为回复频率（"今日回复50+次" / "34分钟前回复"）
  - 无 DialogType 弹窗体系；进聊天页后有安全提示浮层需关闭

使用方式：
  # 仅评分，不发送（调试用）
  python -m scenarios.zhilian.scripts.smart_match_greet --keyword "AI产品经理" --score-only

  # 跑通一条（阈值 6）
  python -m scenarios.zhilian.scripts.smart_match_greet --keyword "AI产品经理" --max-greet 1 --threshold 6

  # 正式运行（最多 5 条，阈值 7，严格模式）
  python -m scenarios.zhilian.scripts.smart_match_greet --keyword "AI产品经理" --strict
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

import anthropic
from anthropic import InternalServerError, RateLimitError
from skills.android.adb_runner import ADBRunner
from skills.zhilian import ZhilianAutomationSkill, JobInfo, PageState, normalize_card_title
from utils.device_lock import DeviceBusyError, device_lock
from utils.logging_setup import setup_logger

logger = logging.getLogger("zhilian_smart_match_greet")

# ─── 路径 ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parents[3]
DB_PATH = REPO_ROOT / "scenarios" / "zhilian" / "output" / "requirements.db"
# 简历路径优先级：$PIXELCLAW_RESUME_PATH 环境变量 > Pi 路径回退 (~$HOME/Projects/resume-renew/resume/current.md)
# Windows 端用法：在 .env 或系统环境变量里设 PIXELCLAW_RESUME_PATH=C:/Dev/projects/resume-renew/resume/current.md
import os as _os_for_default
RESUME_DEFAULT = Path(
    (
        _os_for_default.environ.get("PIXELCLAW_RESUME_PATH")
        or str(Path.home() / "Projects" / "resume-renew" / "resume" / "current.md")
    )
)

# ─── Prompt 加载 ─────────────────────────────────────────────────────────────
_PROMPT_DIR = Path(__file__).parents[1] / "config" / "prompts"
_PROFILE_PATH = _PROMPT_DIR.parent / "candidate_profile.yaml"
_PROMPT_FILES = {
    "match_score":  _PROMPT_DIR / "match_score.md",
    "strict_rules": _PROMPT_DIR / "strict_rules.md",
}
_prompt_cache: dict[str, tuple] = {}


def _load_candidate_profile() -> dict:
    if not _PROFILE_PATH.exists():
        return {}
    try:
        import yaml
        with open(_PROFILE_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"[profile] 读取 candidate_profile.yaml 失败，使用默认值：{exc}")
        return {}


def _load_prompt(name: str) -> str:
    path = _PROMPT_FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"prompt 文件缺失：{path}")
    mtime = path.stat().st_mtime
    cached = _prompt_cache.get(name)
    if cached is not None:
        cached_text, cached_mtime = cached
        if mtime == cached_mtime:
            return cached_text
    text = path.read_text(encoding="utf-8")
    _prompt_cache[name] = (text, mtime)
    print(f"[prompt] 加载 {name} (mtime={mtime:.0f})")
    return text


# ─── DB 工具 ──────────────────────────────────────────────────────────────────

def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zhilian_job_details (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key   TEXT UNIQUE,
            hr_name     TEXT,
            hr_title    TEXT,
            hr_response TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zhilian_greetings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_details_id INTEGER NOT NULL,
            keyword        TEXT,
            sent_at        TEXT NOT NULL,
            action         TEXT,
            greeting_text  TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE zhilian_greetings ADD COLUMN greeting_text TEXT")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zhilian_job_visits (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            title             TEXT    NOT NULL,
            company           TEXT    NOT NULL,
            hr_name           TEXT,
            keyword           TEXT    NOT NULL,
            score             INTEGER,
            greeted           INTEGER NOT NULL DEFAULT 0,
            skip_reason       TEXT,
            visited_at        TEXT    NOT NULL,
            salary            TEXT,
            experience        TEXT,
            company_scale     TEXT,
            description       TEXT,
            top_matches       TEXT,
            mismatch_concerns TEXT
        )
    """)
    conn.commit()


def _is_already_greeted(
    conn: sqlite3.Connection, title: str, company: str, hr_name: str | None
) -> bool:
    dedup_key = f"{normalize_card_title(title)}\t{company}\t{hr_name or ''}"
    row = conn.execute(
        """SELECT COUNT(g.id) FROM zhilian_job_details jd
           JOIN zhilian_greetings g ON g.job_details_id = jd.id
           WHERE jd.dedup_key = ?""",
        (dedup_key,),
    ).fetchone()
    if row and row[0] > 0:
        return True
    if hr_name:
        row2 = conn.execute(
            "SELECT COUNT(*) FROM zhilian_job_visits WHERE hr_name = ? AND company = ? AND greeted = 1",
            (hr_name, company),
        ).fetchone()
        if row2 and row2[0] > 0:
            return True
    row3 = conn.execute(
        "SELECT COUNT(*) FROM zhilian_job_visits WHERE title = ? AND company = ? AND greeted = 1",
        (normalize_card_title(title), company),
    ).fetchone()
    if row3 and row3[0] > 0:
        return True
    if hr_name:
        row4 = conn.execute(
            "SELECT COUNT(*) FROM zhilian_job_visits WHERE title = ? AND hr_name = ? AND greeted = 1",
            (normalize_card_title(title), hr_name),
        ).fetchone()
        if row4 and row4[0] > 0:
            return True
    return False


def _record_greeting(
    conn: sqlite3.Connection,
    title: str,
    company: str,
    hr_name: str | None,
    keyword: str,
    action: str,
    greeting_text: str = "",
) -> None:
    dedup_key = f"{normalize_card_title(title)}\t{company}\t{hr_name or ''}"
    conn.execute(
        "INSERT OR IGNORE INTO zhilian_job_details (dedup_key, hr_name) VALUES (?, ?)",
        (dedup_key, hr_name),
    )
    jd_id = conn.execute(
        "SELECT id FROM zhilian_job_details WHERE dedup_key = ?", (dedup_key,)
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO zhilian_greetings (job_details_id, keyword, sent_at, action, greeting_text)
           VALUES (?, ?, ?, ?, ?)""",
        (jd_id, keyword, time.strftime("%Y-%m-%dT%H:%M:%S"), action, greeting_text),
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
    skip_reason: str | None = None,
    job_dict: dict | None = None,
    scored_result: dict | None = None,
) -> None:
    jd = job_dict or {}
    sc = scored_result or {}
    conn.execute(
        """INSERT INTO zhilian_job_visits
               (title, company, hr_name, keyword, score, greeted, skip_reason, visited_at,
                salary, experience, company_scale, description, top_matches, mismatch_concerns)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            normalize_card_title(title), company, hr_name or "", keyword, score,
            1 if greeted else 0, skip_reason,
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            jd.get("salary"),
            jd.get("experience"),
            jd.get("company_scale"),
            jd.get("description"),
            json.dumps(sc.get("top_matches"), ensure_ascii=False) if sc.get("top_matches") else None,
            json.dumps(sc.get("mismatch_concerns"), ensure_ascii=False) if sc.get("mismatch_concerns") else None,
        ),
    )
    conn.commit()


# ─── 评分 ──────────────────────────────────────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            elif inner.startswith("JSON"):
                inner = inner[4:]
            return inner.rsplit("```", 1)[0].strip()
    first_brace = text.find("{")
    if first_brace < 0:
        return text
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
    import os
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=True)

    primary_key = os.environ.get("ANTHROPIC_BACKUP_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    primary_url = os.environ.get("ANTHROPIC_BACKUP_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
    if not primary_key:
        raise RuntimeError("No API key found in .env")
    primary = anthropic.Anthropic(api_key=primary_key, base_url=primary_url)

    backup_key = os.environ.get("ANTHROPIC_API_KEY")
    backup_url = os.environ.get("ANTHROPIC_BASE_URL")
    fallback = None
    if backup_key and (backup_key != primary_key or backup_url != primary_url):
        fallback = anthropic.Anthropic(api_key=backup_key, base_url=backup_url)
    return primary, fallback


def _extract_reset_at(exc: Exception) -> datetime | None:
    try:
        body = getattr(exc, "body", None)
        if not isinstance(body, dict):
            return None
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
    import time as _time
    last_exc: Exception | None = None
    pairs = [(primary, "primary")]
    if fallback:
        pairs.append((fallback, "fallback"))
    for client, label in pairs:
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
                raise
    assert last_exc is not None
    raise last_exc


def _build_scoring_rubric(dimensions: dict) -> str:
    if not dimensions:
        return "评分标准（0-10）：10=完全匹配，7-9=良好匹配，5-6=部分匹配，3-4=方向不同，1-2=基本不相关"
    total_w = sum(d.get("weight", 0) for d in dimensions.values())
    lines = [
        f"请按以下 {len(dimensions)} 个维度独立评分（每维度 0-10 分），"
        f"最终得分 = Σ(维度分 × 权重)，四舍五入到整数（权重合计 {total_w:.0%}）：",
    ]
    for dim in dimensions.values():
        w = int(dim.get("weight", 0) * 100)
        lines.append(f"- **{dim.get('label', '未命名')}**（权重 {w}%）：{dim.get('desc', '')}")
    return "\n".join(lines)


def score_job(
    primary: anthropic.Anthropic,
    resume: str,
    job: dict,
    strict: bool = False,
    fallback: anthropic.Anthropic | None = None,
    scoring_dimensions: dict | None = None,
) -> dict:
    """Call Claude Haiku to score one job. Returns dict with match_score + analysis."""
    description = job.get("description") or ""
    prompt = _load_prompt("match_score").format(
        resume=resume,
        title=job.get("title", ""),
        company=job.get("company", ""),
        salary=job.get("salary") or "未知",
        experience=job.get("experience") or "未知",
        company_scale=job.get("company_scale") or "未知",
        hr_name=job.get("hr_name") or "（未知）",
        hr_status=job.get("hr_response") or "（未知）",
        description_excerpt=description if description else "（无）",
        strict_rules=_load_prompt("strict_rules") if strict else "",
        scoring_rubric=_build_scoring_rubric(scoring_dimensions or {}),
    )
    response = _call_with_fallback(
        primary, fallback,
        fallback_model="claude-haiku-4-5-20251001",
        model="MiniMax-M3",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _strip_json_fences(response.content[0].text.strip())
    result = json.loads(text)
    return {**job, **result}


# ─── 权重辅助函数 ─────────────────────────────────────────────────────────────

def _parse_salary_range_k(salary_raw: str) -> tuple[float, float] | None:
    if not salary_raw:
        return None
    raw = salary_raw.strip().upper()
    # 智联格式: "2.5-3万" / "10-15K" / "2-3万/月"
    m_wan = re.search(r"(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*万", raw)
    if m_wan:
        lo = float(m_wan.group(1)) * 10
        hi = float(m_wan.group(2)) * 10
        return lo, hi
    m_k = re.search(r"(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*K", raw)
    if m_k:
        return float(m_k.group(1)), float(m_k.group(2))
    m_single_wan = re.search(r"(\d+(?:\.\d+)?)\s*万", raw)
    if m_single_wan:
        v = float(m_single_wan.group(1)) * 10
        return v, v
    m_single_k = re.search(r"(\d+(?:\.\d+)?)\s*K", raw)
    if m_single_k:
        v = float(m_single_k.group(1))
        return v, v
    return None


def _extract_publish_days(published: str) -> float | None:
    """Parse '9月16日发布' → days since today."""
    if not published:
        return None
    m = re.search(r"(\d+)月(\d+)日", published)
    if not m:
        return None
    today = datetime.now()
    month, day = int(m.group(1)), int(m.group(2))
    year = today.year
    try:
        pub_date = datetime(year, month, day)
        if pub_date > today:
            pub_date = datetime(year - 1, month, day)
        return (today - pub_date).days
    except ValueError:
        return None


def _get_recency_weight(days: float | None, cfg: dict) -> float:
    if days is None:
        return cfg.get("unknown", 1.0)
    if days <= 1:
        return cfg.get("within_1d", 1.15)
    if days <= 3:
        return cfg.get("within_3d", 1.1)
    if days <= 7:
        return cfg.get("within_7d", 1.05)
    if days <= 30:
        return cfg.get("within_30d", 1.0)
    return cfg.get("older", 0.95)


def _parse_hr_response_level(hr_status: str) -> str:
    """Classify Zhilian HR response frequency into a weight bucket."""
    t = (hr_status or "").strip()
    if not t:
        return "unknown"
    if re.search(r"今日回复|分钟前回复|小时前回复|今日活跃|高回复率", t):
        return "very_active"
    m = re.search(r"近(\d+)日", t)
    if m:
        days = int(m.group(1))
        if days <= 7:
            return "active"
        elif days <= 30:
            return "moderate"
        return "inactive"
    if re.search(r"昨天回复|本周回复|本周活跃|近期活跃", t):
        return "active"
    if re.search(r"本月回复|本月活跃", t):
        return "moderate"
    return "inactive"


def _get_hr_response_weight(level: str, cfg: dict) -> float:
    return cfg.get(level, cfg.get("unknown", 1.0))


def _extract_job_location(job: dict, known_cities: list[str]) -> str:
    loc_field = job.get("location") or ""
    if loc_field:
        if "全远程" in loc_field or "居家办公" in loc_field:
            return "全远程"
        if "远程" in loc_field:
            return "远程"
        for city in known_cities:
            if city not in ("全远程", "远程", "default") and city in loc_field:
                return city
    text = (job.get("description") or "")[:500]
    if "全远程" in text or "居家办公" in text:
        return "全远程"
    if "远程" in text:
        return "远程"
    for city in known_cities:
        if city not in ("全远程", "远程", "default") and city in text:
            return city
    return "unknown"


def _description_filter_job(
    job: dict,
    industry_blocklist: list[str],
    min_base_salary_k: float,
) -> str | None:
    desc = job.get("description") or ""
    for kw in industry_blocklist:
        if kw in desc:
            return f"行业关键词「{kw}」"
    if min_base_salary_k > 0:
        m = re.search(
            r"(?:底薪|基本工资)[：:\s约不低于]*(\d+(?:\.\d+)?)\s*([kKwW万]?)",
            desc,
        )
        if m:
            val = float(m.group(1))
            unit = m.group(2).lower()
            if unit == "k":
                val_k = val
            elif unit in ("w", "万"):
                val_k = val * 10
            elif val >= 1000:
                val_k = val / 1000
            else:
                val_k = val
            if val_k < min_base_salary_k:
                return f"底薪 {val_k:.0f}K < 要求 {min_base_salary_k:.0f}K"
    return None


_profile_cache = _load_candidate_profile()
_TECH_ROLE_BLOCKLIST: list[str] = _profile_cache.get("role_blocklist", [
    "实习", "实习生", "兼职", "外包",
])
_HYBRID_EXEMPTIONS: list[str] = _profile_cache.get("role_exemptions", [
    "产品", "PM", "经理", "负责人", "AI产品", "研发管理",
])


def _pre_filter_job(title: str) -> bool:
    t = title.strip()
    if any(ex in t for ex in _HYBRID_EXEMPTIONS):
        return False
    return any(kw in t for kw in _TECH_ROLE_BLOCKLIST)


# ─── App 导航 ─────────────────────────────────────────────────────────────────

def _return_to_job_list(skill: ZhilianAutomationSkill, keyword: str = "") -> bool:
    for _ in range(4):
        page = skill.get_current_page()
        if page == PageState.JOB_LIST:
            return True
        skill.press_back()
        time.sleep(1.2)
    # Fallback: re-search
    if keyword:
        for _ in range(2):
            if skill.browse_jobs(keyword):
                deadline = time.time() + 10.0
                while time.time() < deadline:
                    if skill.get_current_page() == PageState.JOB_LIST:
                        return True
                    time.sleep(0.5)
        return False
    for _ in range(3):
        skill.press_back()
        time.sleep(0.8)
        if skill.get_current_page() == PageState.JOB_LIST:
            return True
    return False


def _count_today_greeted(db_conn: sqlite3.Connection) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    row = db_conn.execute(
        "SELECT COUNT(*) FROM zhilian_greetings WHERE sent_at >= ?",
        (today + " 00:00:00",),
    ).fetchone()
    return row[0] if row else 0


def _with_screen_heartbeat(skill: ZhilianAutomationSkill, fn):
    """Keep device awake during fn() by sending KEYCODE_WAKEUP every 5 s."""
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


def _find_next_job(
    skill: ZhilianAutomationSkill,
    visited_keys: set[str],
    initial_jobs: list,
    logger: logging.Logger | None = None,
) -> JobInfo | None:
    """Return the next unvisited job on the current screen with fresh coordinates."""
    import logging as _logging
    _log = logger or _logging.getLogger(__name__)

    xml = skill.get_ui_hierarchy()
    visible = skill.get_job_list(xml=xml)

    by_key: dict[str, JobInfo] = {
        f"{normalize_card_title(j.title)}\t{j.company or ''}": j for j in initial_jobs
    }

    all_titles: list[str] = []
    for v in visible:
        v_key = f"{normalize_card_title(v.title)}\t{v.company or ''}"
        all_titles.append(f"{v.title or '?'}/{v.company or '?'}")

        if v_key in visited_keys:
            _log.info("    [见] ○ %s / %s — 跳过（已访问）", v.title or "?", v.company or "?")
            continue
        # Title-only fallback: if this job was ever seen with empty company, block regardless
        # of whether RecyclerView now lazy-renders a company name (key would differ otherwise)
        title_only_key = f"{normalize_card_title(v.title)}\t"
        if title_only_key in visited_keys:
            _log.info("    [见] ○ %s / %s — 跳过（title-only已访问）", v.title or "?", v.company or "?")
            continue
        if not v.company and v.hr_name:
            hr_key = f"{normalize_card_title(v.title)}\t\x00hr:{v.hr_name}"
            if hr_key in visited_keys:
                _log.info("    [见] ○ %s / ? — 跳过（已访问，hr:%s）", v.title or "?", v.hr_name)
                continue
        full = by_key.get(v_key)
        if full is None:
            return v
        return replace(full, tap_x=v.tap_x, tap_y=v.tap_y)

    if all_titles:
        _log.info("    [当前可见] %s（均已访问）", " / ".join(all_titles))
    return None


# ─── 主循环 ───────────────────────────────────────────────────────────────────

def live_greet_loop(
    resume: str,
    client: anthropic.Anthropic,
    keyword: str,
    threshold: int,
    max_greet: int,
    strict: bool,
    min_salary_k: float,
    device_id: str | None,
    db_conn: sqlite3.Connection,
    score_only: bool = False,
    location_weights: dict | None = None,
    description_filters: dict | None = None,
    fallback: anthropic.Anthropic | None = None,
    company_size_weights: dict | None = None,
    recency_weights: dict | None = None,
    scoring_dimensions: dict | None = None,
    company_tier_weights: dict | None = None,
    hr_response_weights: dict | None = None,
    daily_hard_limit: int = 150,
) -> dict:
    logger = setup_logger("zhilian_smart_match_greet", log_dir="./logs/zhilian")
    adb = ADBRunner()
    skill = ZhilianAutomationSkill(adb, device_id=device_id)
    result: dict = {"greeted": [], "skipped": [], "errors": [], "all_scores": []}

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
        return _run_loop(
            skill, resume, client, keyword, threshold, max_greet,
            strict, min_salary_k, db_conn, score_only, result, logger,
            location_weights=location_weights,
            description_filters=description_filters,
            fallback=fallback,
            company_size_weights=company_size_weights,
            recency_weights=recency_weights,
            scoring_dimensions=scoring_dimensions,
            company_tier_weights=company_tier_weights,
            hr_response_weights=hr_response_weights,
            daily_hard_limit=daily_hard_limit,
        )
    finally:
        skill._adb("shell svc power stayon false")
        skill._adb(f"shell settings put system screen_off_timeout {_orig_timeout}")
        skill._adb(f"shell settings put secure lock_screen_lock_after_timeout {_orig_lock_timeout}")
        skill._adb(f"shell settings put secure adaptive_sleep {_orig_adaptive}")


def _run_loop(
    skill: ZhilianAutomationSkill,
    resume: str,
    client: anthropic.Anthropic,
    keyword: str,
    threshold: int,
    max_greet: int,
    strict: bool,
    min_salary_k: float,
    db_conn: sqlite3.Connection,
    score_only: bool,
    result: dict,
    logger: logging.Logger,
    location_weights: dict | None = None,
    description_filters: dict | None = None,
    fallback: anthropic.Anthropic | None = None,
    company_size_weights: dict | None = None,
    recency_weights: dict | None = None,
    scoring_dimensions: dict | None = None,
    company_tier_weights: dict | None = None,
    hr_response_weights: dict | None = None,
    daily_hard_limit: int = 150,
) -> dict:
    # ── 确认屏幕已亮 ─────────────────────────────────────────────────────────
    if not skill._is_screen_on():
        skill._wake_screen()
        time.sleep(2.0)
    if not skill._is_screen_on():
        logger.warning("⚠️  手机未唤醒，等待解锁（最多 120 秒）…")
        for _ in range(12):
            time.sleep(10)
            skill._wake_screen()
            time.sleep(2.0)
            if skill._is_screen_on():
                break
        else:
            result["errors"].append("手机锁屏未解，无法启动")
            return result

    def _is_lock_screen(xml: str) -> bool:
        return xml is not None and "legacy_window_root" in xml[:400]

    _ui_xml = skill.get_ui_hierarchy()
    if _is_lock_screen(_ui_xml):
        logger.warning("⚠️  检测到锁屏，请解锁手机后继续（最多等待 60 秒）…")
        for _ in range(12):
            time.sleep(5)
            skill._wake_screen()
            time.sleep(1.0)
            if not _is_lock_screen(skill.get_ui_hierarchy()):
                break
        else:
            result["errors"].append("手机锁屏未解，无法启动")
            return result

    logger.info("[1/2] 启动智联招聘…")
    skill._adb(f"shell am force-stop {skill.APP_PACKAGE}")
    time.sleep(2.0)
    if not skill.launch_app():
        result["errors"].append("无法启动智联招聘，请确认 ADB 已连接")
        return result
    time.sleep(3.0)
    skill._adb("shell cmd statusbar collapse")
    time.sleep(0.5)

    # Wait for the list page or handle unexpected overlays
    for _startup_try in range(10):
        xml = skill.get_ui_hierarchy()
        if not xml or skill.APP_PACKAGE not in xml:
            logger.warning("  ⚠️  智联未在前台，等待… (%d/10)", _startup_try + 1)
            time.sleep(3.0)
            continue
        page = skill.get_current_page(xml)
        if page in (PageState.HOME, PageState.JOB_LIST):
            break
        if "tv_ok" in xml:
            logger.info("  关闭启动安全弹窗")
            skill._dismiss_safety_popup()
            time.sleep(1.0)
            continue
        # Dismiss generic close buttons
        _dismissed = False
        for _btn in ("我知道了", "关闭", "以后再说", "跳过", "确定"):
            elem = skill.find_element(text=_btn, xml=xml)
            if elem and elem.center:
                logger.info("  关闭启动弹窗「%s」", _btn)
                skill.tap(*elem.center)
                time.sleep(1.0)
                _dismissed = True
                break
        if not _dismissed:
            time.sleep(2.0)

    logger.info("[2/2] 搜索职位：「%s」…", keyword)
    if not skill.browse_jobs(keyword):
        _dbg = skill.get_ui_hierarchy()
        if _dbg:
            _dbg_path = Path(skill.output_dir) / "debug_search_fail.xml"
            _dbg_path.write_text(_dbg[:12000], encoding="utf-8")
            logger.error("  搜索失败，调试 XML 已保存至 %s", _dbg_path)
        result["errors"].append(f"搜索失败：{keyword}")
        return result

    jobs: list = []
    visited_keys: set[str] = set()
    greeted_count = 0
    no_target_scrolls = 0
    processed = 0

    DAILY_HARD_LIMIT = daily_hard_limit
    DAILY_WARN_LIMIT = max(1, DAILY_HARD_LIMIT - 20)
    today_count = _count_today_greeted(db_conn)
    logger.info("  今日已发送 %d 条招呼", today_count)
    if today_count >= DAILY_HARD_LIMIT:
        logger.error("今日已发送 %d 条，达每日上限（%d），退出。", today_count, DAILY_HARD_LIMIT)
        return result
    if today_count >= DAILY_WARN_LIMIT:
        logger.warning("今日已发送 %d 条，接近上限（%d）。", today_count, DAILY_HARD_LIMIT)

    while greeted_count < max_greet:
        # 0. 回到列表页
        if skill.get_current_page() != PageState.JOB_LIST:
            if not _return_to_job_list(skill, keyword):
                result["errors"].append("无法返回职位列表")
                break

        # 1. 找下一个未访问的卡片
        target = _find_next_job(skill, visited_keys, jobs, logger=logger)
        if target is None:
            no_target_scrolls += 1
            if no_target_scrolls > 8:
                logger.info("  列表已无未访问目标（连滑 %d 次），结束", no_target_scrolls - 1)
                break
            logger.info("  当前屏幕无未访问目标，下滑 %d/8", no_target_scrolls)
            skill.scroll_down(start_y=1300, end_y=980, duration=600)
            time.sleep(1.5)
            continue
        no_target_scrolls = 0

        processed += 1
        title = target.title or ""
        company = target.company or ""
        hr_name = target.hr_name or ""
        visited_keys.add(f"{normalize_card_title(title)}\t{company}")
        if not company:
            if hr_name:
                visited_keys.add(f"{normalize_card_title(title)}\t\x00hr:{hr_name}")
            else:
                visited_keys.add(f"{normalize_card_title(title)}\t")

        # 去重检查
        if _is_already_greeted(db_conn, title, company, hr_name):
            logger.info("  [%d] 跳过（已打过招呼）：%s", processed, title)
            result["skipped"].append(f"{title}（{company}）：已打过招呼")
            continue

        # 薪资预过滤
        if min_salary_k > 0:
            sal_range = _parse_salary_range_k(target.salary or "")
            if sal_range is not None and sal_range[1] < min_salary_k:
                logger.info("  [%d] 跳过（薪资偏低 %.0fK < %.0fK）：%s（%s）",
                            processed, sal_range[1], min_salary_k, title, company or "")
                result["skipped"].append(
                    f"{title}：薪资上限 {sal_range[1]:.0f}K < 下限 {min_salary_k:.0f}K"
                )
                continue

        # 标题预过滤
        if _pre_filter_job(title):
            logger.info("  [%d] 跳过（黑名单）：%s", processed, title)
            result["skipped"].append(f"{title}：标题关键词跳过")
            continue

        # 检查屏幕状态
        if not skill._is_screen_on():
            logger.warning("⚠️  屏幕熄灭，等待解锁（最多 90 秒）…")
            for _ in range(9):
                time.sleep(10)
                skill._wake_screen()
                time.sleep(2.0)
                if skill._is_screen_on():
                    logger.info("  ↩ 解锁后恢复成功")
                    break
            else:
                result["errors"].append("屏幕未解锁，停止任务")
                break

        # 进入详情页
        logger.info("  [%d] → %s（%s）", processed, title, company)
        skill.navigate_to_job(target)
        time.sleep(2.0)

        # 等待详情页加载
        _detail_ok = False
        for _attempt in range(3):
            xml = skill.get_ui_hierarchy()
            if "tv_job_name_new" in xml:
                _detail_ok = True
                break
            time.sleep(1.5)
        if not _detail_ok:
            logger.warning("  详情页加载超时，跳过")
            result["errors"].append(f"{title}：详情页加载超时")
            _record_visit(db_conn, title, company, hr_name, keyword, None,
                          skip_reason="详情页加载超时")
            continue

        # 关闭新手引导（如有）
        skill.dismiss_tutorial_if_present()

        # 提取详情
        detail = skill.get_job_detail()
        _detail_title = detail.get("title") or ""
        _detail_company = (detail.get("hr_company") or "").strip()

        # 验证详情页与目标一致（防止 tap 错卡片）
        # Normalize full-width brackets/parens to ASCII for comparison
        def _norm(s: str) -> str:
            # Normalize full-width parens and strip spaces for loose title comparison
            return s.replace("（", "(").replace("）", ")").replace("　", " ").replace(" ", "")
        _title_match = (
            not title or not _detail_title
            or _norm(title) in _norm(_detail_title) or _norm(_detail_title) in _norm(title)
        )
        _company_match = (
            not company or not _detail_company
            or company[:6] in _detail_company or _detail_company[:6] in company
        )
        if not _title_match or not _company_match:
            logger.warning(
                "  详情页不匹配（期望 %s/%s，实际 %s/%s），跳过",
                title, company, _detail_title, _detail_company,
            )
            result["errors"].append(f"{title}：详情页内容不匹配")
            _record_visit(db_conn, title, company, hr_name, keyword, None,
                          skip_reason="详情页title或公司名不匹配")
            continue

        # 检查是否已聊过
        if skill.is_already_chatted():
            logger.info("  [%d] 跳过（已聊过）：%s", processed, title)
            result["skipped"].append(f"{title}（{company}）：已聊过")
            continue

        job_dict = {
            "title":         detail.get("title") or title,
            "company":       detail.get("hr_company") or company,
            "salary":        detail.get("salary") or target.salary or "",
            "experience":    detail.get("experience") or "",
            "location":      detail.get("location") or target.location or "",
            "company_scale": detail.get("headcount") or target.company_scale or "",
            "hr_name":       detail.get("hr_name") or hr_name,
            "hr_title":      detail.get("hr_title") or target.hr_title or "",
            "hr_response":   detail.get("hr_response") or target.hr_status or "",
            "description":   detail.get("description") or "",
            "published":     detail.get("published") or "",
        }

        # 更新 visited_keys：详情页补全公司名后，追加精确 key；保留 title-only key
        # 以阻止空公司卡片在多次滚动时被重复处理
        _real_company = job_dict.get("company") or ""
        if not company and _real_company:
            visited_keys.add(f"{normalize_card_title(title)}\t{_real_company}")

        effective_hr_name = job_dict.get("hr_name") or hr_name

        # 详情页确认 hr_name 后再次去重
        if effective_hr_name and effective_hr_name != hr_name:
            if _is_already_greeted(db_conn, job_dict["title"], job_dict["company"], effective_hr_name):
                logger.info("  [%d] 跳过（已打招呼，详情页确认 hr=%s）：%s",
                            processed, effective_hr_name, title)
                result["skipped"].append(f"{title}（{company}）：已打过招呼")
                continue

        # 详情页内容过滤
        if description_filters:
            _df = description_filters
            _desc_skip = _description_filter_job(
                job_dict,
                industry_blocklist=_df.get("industry_blocklist", []),
                min_base_salary_k=float(_df.get("min_base_salary_k", 0)),
            )
            if _desc_skip:
                logger.info("  跳过（详情过滤）：%s — %s", title, _desc_skip)
                result["skipped"].append(f"{title}（{company}）：{_desc_skip}")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, None,
                              skip_reason=_desc_skip, job_dict=job_dict)
                continue

        # Haiku 评分（heartbeat 防锁屏）
        try:
            scored = _with_screen_heartbeat(
                skill,
                lambda: score_job(client, resume, job_dict, strict=strict,
                                   fallback=fallback, scoring_dimensions=scoring_dimensions),
            )
        except Exception as exc:
            reset_at = _extract_reset_at(exc)
            if reset_at:
                wait_secs = max(5.0, (reset_at - datetime.now(timezone.utc)).total_seconds() + 10.0)
                logger.warning("  两端 API 配额耗尽，等待 %.0f 秒后重试：%s", wait_secs, title)
                time.sleep(wait_secs)
                try:
                    scored = _with_screen_heartbeat(
                        skill,
                        lambda: score_job(client, resume, job_dict, strict=strict,
                                           fallback=fallback, scoring_dimensions=scoring_dimensions),
                    )
                except Exception as exc2:
                    logger.error("  配额恢复后仍失败：%s — %s", title, exc2)
                    result["errors"].append(f"{title}：评分失败（{exc2}）")
                    _record_visit(db_conn, title, company, effective_hr_name, keyword, None,
                                  skip_reason=f"评分失败:{exc2}", job_dict=job_dict)
                    continue
            else:
                logger.error("  评分失败：%s — %s", title, exc)
                result["errors"].append(f"{title}：评分失败（{exc}）")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, None,
                              skip_reason=f"评分失败:{exc}", job_dict=job_dict)
                continue

        score = scored.get("match_score", 0)
        result["all_scores"].append(score)
        logger.info("  分数 %d/10  %s", score, scored.get("top_matches", []))
        dim_scores = scored.get("dimension_scores")
        if dim_scores:
            parts = " | ".join(f"{k}={v}" for k, v in dim_scores.items())
            logger.info("  维度分：%s", parts)
        if scored.get("mismatch_concerns"):
            logger.info("  差距：%s", scored["mismatch_concerns"])

        # ── 地点权重 ───────────────────────────────────────────────────────────
        if location_weights and score > 0:
            loc = _extract_job_location(job_dict, list(location_weights.keys()))
            weight = location_weights.get(loc, location_weights.get("default", 1.0))
            if weight == 0:
                logger.info("  地点「%s」权重=0，跳过", loc)
                result["skipped"].append(f"{title}（{company}）：地点权重0")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason=f"地点权重0({loc})", job_dict=job_dict, scored_result=scored)
                continue
            if weight != 1.0:
                adjusted = max(1, round(score * weight))
                logger.info("  地点「%s」权重 %.2f → 分数 %d→%d", loc, weight, score, adjusted)
                score = adjusted

        # ── 公司规模权重 ──────────────────────────────────────────────────────
        if company_size_weights and score > 0:
            scale = job_dict.get("company_scale") or ""
            cs_weight = company_size_weights.get(scale, company_size_weights.get("default", 1.0))
            if cs_weight != 1.0:
                adjusted = max(1, round(score * cs_weight))
                logger.info("  规模「%s」权重 %.2f → 分数 %d→%d", scale or "未知", cs_weight, score, adjusted)
                score = adjusted

        # ── 大厂权重 ──────────────────────────────────────────────────────────
        if company_tier_weights and score > 0:
            tier_w = 1.0
            tier_match = ""
            for pattern, w in company_tier_weights.items():
                if pattern == "default":
                    continue
                if pattern.lower() in (job_dict.get("company") or "").lower():
                    if float(w) > tier_w:
                        tier_w = float(w)
                        tier_match = pattern
            if tier_w == 1.0:
                tier_w = float(company_tier_weights.get("default", 1.0))
            if tier_w != 1.0:
                adjusted = max(1, round(score * tier_w))
                logger.info("  大厂「%s」×%.2f → 分数 %d→%d", tier_match, tier_w, score, adjusted)
                score = adjusted

        # ── 发布时间权重 ──────────────────────────────────────────────────────
        if recency_weights and score > 0:
            pub_days = _extract_publish_days(job_dict.get("published") or "")
            r_weight = _get_recency_weight(pub_days, recency_weights)
            if r_weight != 1.0:
                adjusted = max(1, round(score * r_weight))
                days_str = f"{pub_days:.0f}天前" if pub_days is not None else "未知"
                logger.info("  发布时间「%s」权重 %.2f → 分数 %d→%d", days_str, r_weight, score, adjusted)
                score = adjusted

        # ── HR 回复频率权重 ───────────────────────────────────────────────────
        if hr_response_weights and score > 0:
            level = _parse_hr_response_level(job_dict.get("hr_response") or "")
            h_weight = _get_hr_response_weight(level, hr_response_weights)
            if h_weight != 1.0:
                adjusted = max(1, round(score * h_weight))
                logger.info("  HR回复「%s」(%s) 权重 %.2f → 分数 %d→%d",
                            job_dict.get("hr_response") or "未知", level, h_weight, score, adjusted)
                score = adjusted

        if score < threshold:
            logger.info("  → 跳过（%d < %d）", score, threshold)
            result["skipped"].append(f"{title}（{company}）：{score}/10 低于阈值")
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason=f"低分({score}<{threshold})", job_dict=job_dict, scored_result=scored)
            continue

        logger.info("  ✓ 达标！准备打招呼…")

        if score_only:
            result["skipped"].append(f"{title}（{company}）：{score}/10 ✓ [score_only]")
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason="score_only", job_dict=job_dict, scored_result=scored)
            continue

        # 发送打招呼（智联自动发送默认招呼文本，可选个性化消息）
        greeting = scored.get("greeting", "")
        try:
            ok = skill.send_greeting(message=greeting)
            if ok:
                action = "打招呼成功（智联默认招呼）" if not greeting else "打招呼成功（含个性化消息）"
                result["greeted"].append({
                    "job": title, "company": company, "score": score,
                    "action": action, "keyword": keyword,
                })
                logger.info("  ✓ 打招呼成功 — %s（%s）", title, company)
                greeted_count += 1
                today_count += 1
                if greeted_count % 10 == 0:
                    logger.info("  📊 进度：本次已发 %d/%d，今日累计约 %d 条",
                                greeted_count, max_greet, today_count)
                _record_greeting(db_conn, title, company, effective_hr_name, keyword, action,
                                 greeting_text=greeting)
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              greeted=True, job_dict=job_dict, scored_result=scored)

                if today_count >= DAILY_HARD_LIMIT:
                    logger.warning("  今日已发送 %d 条，达每日上限，停止。", today_count)
                    break
            else:
                result["errors"].append(f"{title}：send_greeting 失败（按钮未找到或页面异常）")
                logger.error("  ✗ 打招呼失败 — %s", title)
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="发送失败", job_dict=job_dict, scored_result=scored)

        except Exception as exc:
            logger.error("  处理 '%s' 异常", title, exc_info=True)
            result["errors"].append(f"{title}: {exc}")
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason=f"异常:{exc}",
                          job_dict=locals().get("job_dict"),
                          scored_result=locals().get("scored"))
            if not skill._is_screen_on():
                break

        time.sleep(1.0)

    # ── 评分分布摘要 ──────────────────────────────────────────────────────────
    all_s = result["all_scores"]
    if all_s:
        high = sum(1 for s in all_s if s >= 8)
        mid  = sum(1 for s in all_s if 6 <= s < 8)
        low  = sum(1 for s in all_s if s < 6)
        avg  = sum(all_s) / len(all_s)
        logger.info(
            "📊 评分分布（共 %d 个职位打分）: ≥8分=%d  6-7分=%d  <6分=%d  均分=%.1f  跳过=%d",
            len(all_s), high, mid, low, avg, len(result["skipped"]),
        )

    return result


# ─── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> int:
    profile = _load_candidate_profile()
    _profile_threshold = profile.get("default_threshold", 7)
    _profile_min_salary = profile.get("min_salary_k", 0)
    location_weights: dict = profile.get("location_weights", {})
    description_filters: dict = profile.get("description_filters", {})
    company_size_weights: dict = profile.get("company_size_weights", {})
    recency_weights: dict = profile.get("recency_weights", {})
    scoring_dimensions: dict = profile.get("scoring_dimensions", {})
    company_tier_weights: dict = profile.get("company_tier_weights", {})
    hr_response_weights: dict = profile.get("hr_response_weights", {})
    daily_hard_limit: int = int(profile.get("daily_hard_limit", 150))

    parser = argparse.ArgumentParser(description="智联招聘实时评分 + 自动打招呼")
    parser.add_argument("--keyword", required=True, help="搜索关键词（必需）")
    parser.add_argument(
        "--resume",
        default=str(RESUME_DEFAULT),
        help=f"简历 Markdown 路径（默认：{RESUME_DEFAULT}）",
    )
    parser.add_argument("--threshold", type=int, default=_profile_threshold,
                        help=f"最低匹配分数 0-10（默认 {_profile_threshold}）")
    parser.add_argument("--max-greet", type=int, default=5, help="最多发送打招呼数（默认 5）")
    parser.add_argument("--score-only", action="store_true", help="仅评分打印，不发送打招呼")
    parser.add_argument("--strict", action="store_true", help="严格评分模式，减少分数虚高")
    parser.add_argument("--min-salary", type=float, default=_profile_min_salary,
                        help=f"月薪下限（K），低于此值跳过（默认 {_profile_min_salary}K）")
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    args = parser.parse_args()

    resume_path = Path(args.resume)
    if not resume_path.exists():
        print(f"错误：简历文件不存在：{resume_path}")
        return 1
    resume_text = resume_path.read_text(encoding="utf-8")
    print(f"✓ 简历已加载：{resume_path.name}（{len(resume_text)} 字符）")

    try:
        with device_lock(script_name=f"zhilian_smart_match_greet:{args.keyword}"):
            client, fallback = _make_client()

            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            db_conn = sqlite3.connect(DB_PATH)
            _ensure_tables(db_conn)

            mode_label = "严格模式" if args.strict else "标准模式"
            score_label = "仅评分" if args.score_only else f"最多发送 {args.max_greet} 条"
            print(
                f"\n🤖 智联招聘单阶段循环（关键词：{args.keyword}，阈值：{args.threshold}，{mode_label}，{score_label}）"
            )

            result = live_greet_loop(
                resume=resume_text,
                client=client,
                keyword=args.keyword,
                threshold=args.threshold,
                max_greet=args.max_greet,
                strict=args.strict,
                min_salary_k=args.min_salary,
                device_id=args.device,
                db_conn=db_conn,
                score_only=args.score_only,
                location_weights=location_weights,
                description_filters=description_filters,
                fallback=fallback,
                company_size_weights=company_size_weights,
                recency_weights=recency_weights,
                scoring_dimensions=scoring_dimensions,
                company_tier_weights=company_tier_weights,
                hr_response_weights=hr_response_weights,
                daily_hard_limit=daily_hard_limit,
            )
            db_conn.close()

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
            if result["skipped"]:
                print(f"- 跳过：{len(result['skipped'])} 条")
            if result["errors"]:
                print(f"✗ 错误：{len(result['errors'])} 个")
                for err in result["errors"]:
                    print(f"  · {err}")

            return 0 if not result["errors"] else 1
    except DeviceBusyError as exc:
        print(f"⚠ 设备已被占用，跳过本次运行：{exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
