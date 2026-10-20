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
import subprocess
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
from utils.device_lock import DeviceBusyError, device_lock
from utils.logging_setup import setup_logger

# Module-level logger reference — used by helpers called BEFORE live_greet_loop()
# configures its handlers. setup_logger() is idempotent and configures this same
# logger object, so log lines emitted here flow to the same console/file.
logger = logging.getLogger("smart_match_greet")

# ─── 路径 ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parents[3]
DB_PATH = REPO_ROOT / "scenarios" / "boss" / "output" / "requirements.db"
# 简历路径优先级：$PIXELCLAW_RESUME_PATH 环境变量 > Pi 路径回退 (~$HOME/Projects/resume-renew/resume/current.md)
# Windows 端用法：在 .env 或系统环境变量里设 PIXELCLAW_RESUME_PATH=C:/Dev/projects/resume-renew/resume/current.md
import os as _os_for_default
RESUME_DEFAULT = Path(
    (
        _os_for_default.environ.get("PIXELCLAW_RESUME_PATH")
        or str(Path.home() / "Projects" / "resume-renew" / "resume" / "current.md")
    )
)

# ─── 弹窗分类 ─────────────────────────────────────────────────────────────────
_FATAL_DIALOGS = {DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED}
_SKIP_DIALOGS = {DialogType.JOB_OFFLINE}
_CONTINUE_DIALOGS = {DialogType.EXISTING_CHAT, DialogType.DISMISSED, DialogType.WARM_REMINDER}

# ─── Haiku Prompt ─────────────────────────────────────────────────────────────

# ─── Prompt 加载 ─────────────────────────────────────────────────────────────
# Prompt 从 scenarios/boss/config/prompts/*.md 读取，业务侧改 prompt 不用碰代码。
# 启动时缓存 + mtime 检测：编辑 md 后下一次 score_job / extract_resume_summary 调用自动读新版本。

_PROMPT_DIR = Path(__file__).parents[1] / "config" / "prompts"
_PROFILE_PATH = _PROMPT_DIR.parent / "candidate_profile.yaml"
_PROMPT_FILES = {
    "match_score":  _PROMPT_DIR / "match_score.md",
    "strict_rules": _PROMPT_DIR / "strict_rules.md",
}
_prompt_cache: dict[str, str] = {}


def _load_candidate_profile() -> dict:
    """Load candidate_profile.yaml; return empty dict if absent."""
    if not _PROFILE_PATH.exists():
        return {}
    try:
        import yaml  # pyyaml; standard dependency
        with open(_PROFILE_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"[profile] 读取 candidate_profile.yaml 失败，使用默认值：{exc}")
        return {}


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
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            title             TEXT    NOT NULL,
            company           TEXT    NOT NULL,
            hr_name           TEXT,
            keyword           TEXT    NOT NULL,
            score             INTEGER,
            greeted           INTEGER NOT NULL DEFAULT 0,
            greeting_text     TEXT,
            skip_reason       TEXT,
            visited_at        TEXT    NOT NULL,
            salary            TEXT,
            experience        TEXT,
            company_info      TEXT,
            description       TEXT,
            top_matches       TEXT,
            mismatch_concerns TEXT
        )
    """)
    # 为旧数据库补列（ALTER TABLE 不支持 IF NOT EXISTS，用 try/except 兜底）
    for col, coltype in [
        ("salary",            "TEXT"),
        ("experience",        "TEXT"),
        ("company_info",      "TEXT"),
        ("description",       "TEXT"),
        ("top_matches",       "TEXT"),
        ("mismatch_concerns", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE job_visits ADD COLUMN {col} {coltype}")
        except Exception:
            pass
    conn.commit()


def _bigram_overlap(a: str, b: str) -> float:
    """Return bigram overlap coefficient between two strings: |A∩B| / min(|A|, |B|).

    用途：检测「品牌短名 vs 公司全名」场景下的 company 字段不一致——
    既不是子串（INSTR 失败）也不是字符集完全相同（共同 char-set 都偏弱），
    但 2-char token 集合有显著重叠（≥ 2/3）。
    例：「融和科技」 ↔ 「北京融和友信科技股份有限公司」：
        bigrams(A) = {融和, 和科, 科技} = 3
        bigrams(B) = {北京, 京融, 融和, 和友, 友信, 信科, 科技, 技股, 股份, 份有, 有限, 限公, 公司} = 13
        intersection = {融和, 科技} = 2
        overlap = 2 / 3 = 0.667 ≥ 0.66 → 视为同一家公司

    阈值建议 ≥ 0.66（来自 2026-10-19 bug 边界：2/3 = 0.667 刚好通过）。
    长度守护：list 长度 < 3 时 bigrams 数 < 2，overlap 易过 0.5 但语义模糊，
    此时仍退回到 INSTR 子串匹配。

    Returns 0.0 for empty / len<2 inputs.
    """
    if not a or not b or len(a) < 2 or len(b) < 2:
        return 0.0
    bg_a = {a[i:i + 2] for i in range(len(a) - 1)}
    bg_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not bg_a or not bg_b:
        return 0.0
    return len(bg_a & bg_b) / min(len(bg_a), len(bg_b))


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
    # Fourth layer: same title + same hr_name regardless of company.
    # Catches empty-company first encounter: dedup_key stores '' for company,
    # but second encounter has company populated → all previous layers miss it.
    if hr_name:
        row4 = conn.execute(
            "SELECT COUNT(*) FROM job_visits WHERE title = ? AND hr_name = ? AND greeted = 1",
            (normalize_card_title(title), hr_name),
        ).fetchone()
        if row4 and row4[0] > 0:
            return True
    # Fifth layer: title-only match for unique titles (length >= 10 chars after normalize).
    # Catches the "both empty on first visit + populated on second visit" gap.
    # Example: visit 36 stored (title, company='', hr_name=''); visit 37 stored
    # (title, company='ATB', hr_name='沈女士') → all four previous layers miss it.
    # Risk: over-dedup for common short titles like "产品经理" (4 chars). Mitigated
    # by length >= 10 — common short titles all < 10 chars after normalize.
    # Refined 2026-10-13: only trigger if the previous greeting was itself a
    # "data gap" (empty hr_name). If previous greeting has a specific HR but
    # new visit has a DIFFERENT HR, that's strong evidence of a different job
    # — don't block (爱化身/喂车科技漏访问的根因).
    norm_title = normalize_card_title(title)
    if len(norm_title) >= 10:
        if not hr_name:
            # new visit has no hr_name → list view data gap, conservative block
            row5 = conn.execute(
                "SELECT COUNT(*) FROM job_visits WHERE title = ? AND greeted = 1",
                (norm_title,),
            ).fetchone()
        else:
            # new visit has hr_name → only block if previous greeting had empty
            # hr_name (data gap). Previous with specific HR = different job.
            row5 = conn.execute(
                """SELECT COUNT(*) FROM job_visits
                   WHERE title = ? AND greeted = 1
                   AND (hr_name = '' OR hr_name IS NULL)""",
                (norm_title,),
            ).fetchone()
        if row5 and row5[0] > 0:
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
    job_dict: dict | None = None,
    scored_result: dict | None = None,
) -> None:
    """Record every job detail page visit regardless of whether a greeting was sent."""
    import json as _json
    jd = job_dict or {}
    sc = scored_result or {}
    conn.execute(
        """INSERT INTO job_visits
               (title, company, hr_name, keyword, score, greeted, greeting_text, skip_reason, visited_at,
                salary, experience, company_info, description, top_matches, mismatch_concerns)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            normalize_card_title(title), company, hr_name or "", keyword, score,
            1 if greeted else 0, greeting_text, skip_reason,
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            jd.get("salary_raw") or jd.get("salary"),
            jd.get("experience"),
            jd.get("company_info"),
            jd.get("description"),
            _json.dumps(sc.get("top_matches"), ensure_ascii=False) if sc.get("top_matches") else None,
            _json.dumps(sc.get("mismatch_concerns"), ensure_ascii=False) if sc.get("mismatch_concerns") else None,
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
        max_tokens=768,
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
    resume: str,
    job: dict,
    strict: bool = False,
    fallback: anthropic.Anthropic | None = None,
    scoring_dimensions: dict | None = None,
    big_tech_whitelist: dict | None = None,
) -> dict:
    """Call Claude Haiku to score one job and generate a personalized greeting.

    Args:
        big_tech_whitelist: candidate_profile.yaml 的 big_tech_whitelist 段（parents +
            subsidiaries）。注入到 prompt 作为 LLM 判定 is_big_tech 的参考清单（不是
            硬过滤——LLM 仍可识别名单外的大厂）。None 或空时不注入，LLM 自主判断。
    """
    description = job.get("description") or ""
    big_tech_context = _build_big_tech_context(big_tech_whitelist)
    prompt = _load_prompt("match_score").format(
        resume=resume,
        title=job.get("title", ""),
        company=job.get("company", ""),
        salary=job.get("salary_raw") or "未知",
        experience=job.get("experience") or "未知",
        company_info=job.get("company_info") or "未知",
        hr_name=job.get("hr_name") or "（未知）",
        description_excerpt=description if description else "（无）",
        requirements=job.get("requirements_text") or "  无标签",
        strict_rules=_load_prompt("strict_rules") if strict else "",
        scoring_rubric=_build_scoring_rubric(scoring_dimensions or {}),
        big_tech_context=big_tech_context,
        big_tech_judge_hint=(
            "本字段用于 --only-big-tech 模式过滤：True 表示该公司属于大厂，"
            "False 表示不属于。当公司名 + 公司信息 + 描述指向不同时以描述为准 "
            "（有些大厂用新主体名招聘；有些公司名撞名但实为 startup）。"
        ) if big_tech_context else "本字段为额外判定，可直接根据公司名 + 信息 + 描述综合判断。",
    )
    response = _call_with_fallback(
        primary, fallback,
        fallback_model="claude-haiku-4-5-20251001",
        model="MiniMax-M3",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _strip_json_fences(response.content[0].text.strip())
    # 兜底：LLM 偶发在 _analysis 字段输出字符串内真换行/制表符，严格 JSON 解析会抛
    # Invalid control character。strict=False 接受字符串内 \n \t \r（HTTP/JSON 实战常见）。
    try:
        result = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        # 二次重试：把失败的 text 喂回 LLM，让它修正 JSON（实测 1 次重试几乎都成功）
        # 错误模式：「Expecting ',' delimiter: line N column M」= LLM 漏字段分隔符
        logger.warning(
            "  JSON 解析失败（attempt 1）：%s — 写入 debug + 触发 LLM 重试", exc
        )
        _dbg_dir = Path("./logs/boss/json_parse_errors")
        _dbg_dir.mkdir(parents=True, exist_ok=True)
        _dbg_dir.joinpath(f"parse_err_{int(time.time())}.txt").write_text(
            text, encoding="utf-8"
        )
        response = _call_with_fallback(
            primary, fallback,
            fallback_model="claude-haiku-4-5-20251001",
            model="MiniMax-M3",
            max_tokens=2048,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        f"你上次的输出不是合法 JSON（Python json.loads 报错：{exc}）。"
                        "请重新输出 **严格合法** 的 JSON：\n"
                        "1. 字段之间必须用英文逗号 `,` 分隔\n"
                        "2. 字符串值用英文双引号 `\"`，内部如有引号用反斜杠转义\n"
                        "3. 禁止 markdown 围栏（```）、禁止多余解释文字\n"
                        "4. 字段名与顺序保持与上次一致，不要省略任何字段"
                    ),
                },
            ],
        )
        text = _strip_json_fences(response.content[0].text.strip())
        result = json.loads(text, strict=False)  # 二次仍失败 → 抛给调用方按现有 error 流程处理
    # 兜底修正 LLM 漏姓 / 用字面 "HR" 的情况（即便 prompt 已强化仍偶发）
    import re as _re
    greeting_raw = result.get("greeting", "") or ""
    greeting_fixed = _re.sub(r"^(总好|HR好|老师好|XX总好)", "您好", greeting_raw)
    # 兜底 2：greeting 必须单行——LLM 偶发在字符串内输出真 \n \t 会破坏 4 段结构
    greeting_fixed = _re.sub(r"[\n\t]+", " ", greeting_fixed).strip()
    # 兜底 3：收尾归一化——强制末尾以首选话术收尾，LLM 不可决定收尾
    greeting_fixed = _normalize_greeting_closing(greeting_fixed)
    if greeting_fixed != greeting_raw:
        logger.warning("  greeting 兜底修正：%r → %r", greeting_raw[:20], greeting_fixed[:20])
        result["greeting"] = greeting_fixed
    return {**job, **result}


def _regenerate_greeting(
    primary: anthropic.Anthropic,
    fallback: anthropic.Anthropic | None,
    scored: dict,
    job: dict,
) -> str:
    """Generate a greeting for a job that already passed scoring but LLM forgot to fill it.

    2026-10-15 现象：score_job 偶发只输出 match_score / top_matches / mismatch_concerns，
    漏掉 greeting 字段（~10% 损耗：AI产品经理 120 跑 17/135 张 8-9 分卡被「or not greeting」吃掉）。
    调一次简短的 LLM call 把 greeting 补上；如果再失败，从 top_matches 拼模板降级。

    Returns the greeting text (>= 30 chars to pass _normalize_greeting_closing filter) or "".
    """
    import re as _re

    top_matches = scored.get("top_matches") or []
    if isinstance(top_matches, str):
        top_matches = [top_matches]
    title = job.get("title", "")
    company = job.get("company", "")
    hr_name = job.get("hr_name") or ""
    # 称呼：HR 姓氏 + 总；空则"您好"
    if hr_name and hr_name not in ("（未知）", ""):
        # 取首字符作姓（"王女士" → "王总"）；如果整词就是姓 + 头衔则不重复
        surname = hr_name[0]
        salutation = f"{surname}总好"
    else:
        salutation = "您好"

    # 第一道：调 LLM 短补一次。Prompt 把已有 top_matches 全塞进去让 LLM 写打招呼。
    if top_matches:
        tm_text = "；".join(str(t) for t in top_matches[:3])
        prompt = (
            "你之前给一份 JD 打过匹配分，结论是" + str(scored.get("match_score", "?")) + "/10 高分，"
            "已写好匹配点但漏了打招呼正文。请**严格按 4 段结构**补写打招呼：\n"
            "① 称呼（HR 姓氏+总，或「您好」）\n"
            "② JD 钩子（≤ 20 字，引用 JD 实际技术栈）\n"
            "③ 候选人对位证据（≤ 25 字）\n"
            "④ 收尾（≤ 15 字，**必须以「方便发您一份简历看看吗」结尾**，不要用「聊聊/搭把手」等词）\n\n"
            "已有匹配点：\n" + tm_text + "\n\n"
            "严格输出 JSON：{\"greeting\": \"<正文>\"}，禁止任何额外文字、禁止 markdown 围栏。"
        )
        try:
            response = _call_with_fallback(
                primary, fallback,
                fallback_model="claude-haiku-4-5-20251001",
                model="MiniMax-M3",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _strip_json_fences(response.content[0].text.strip())
            data = json.loads(text, strict=False)
            g = (data.get("greeting") or "").strip()
            if len(g) >= 30:
                logger.info("  [regen-greeting] LLM 补生成成功（%d 字）", len(g))
                return _normalize_greeting_closing(_re.sub(r"[\n\t]+", " ", g))
        except Exception as exc:
            logger.warning("  [regen-greeting] LLM 补生成失败：%s", exc)

    # 第二道：模板降级（top_matches 第一项拼招呼 + 固定收尾）
    if top_matches:
        first = str(top_matches[0])
        # 截前 25 字作对位证据
        evidence = first[:25].rstrip("，,。;； ") if len(first) > 25 else first
        # 不写结尾问句——交给 _normalize_greeting_closing 统一拼"方便发您一份简历看看吗"
        template = (
            f"{salutation}，看到{company or '贵司'}招{title}，"
            f"我过去{evidence}"
        )
        logger.info("  [regen-greeting] 模板降级（top_matches[0]=%d 字）", len(first))
        return _normalize_greeting_closing(_re.sub(r"[\n\t]+", " ", template))

    # 第三道：彻底没 top_matches 时
    template = (
        f"{salutation}，看到{company or '贵司'}招{title}我比较感兴趣，"
        f"过去 2 年做 FDE 型产品经理（Agent OS 三层产品 + 跨职能交付 + 客户蓝图）"
    )
    logger.info("  [regen-greeting] 模板降级（无 top_matches，通用能力对位）")
    return _normalize_greeting_closing(_re.sub(r"[\n\t]+", " ", template))


# 收尾归一化：用户（2026-10-12）决定硬性固定收尾，不让 LLM 决定。
# 规则：招呼末尾 ≤ 25 字内必须含首选/备选/次选话术之一，否则截掉末尾标点 + 拼首选。
# 收到此函数前，prompt 已禁止全文出现「搭把手/聊聊」等随意见面词。
_PREFERRED_CLOSINGS = (
    "方便发您一份简历看看吗",       # 首选（用户钦定）
    "方便发您一份简历过目吗",       # 备选
    "不知是否方便发简历给您过目",   # 备选
    "冒昧请教，方便发简历吗",       # 次选
    "不知是否合适，盼进一步沟通",   # 次选
)
_BANNED_CLOSING_TAILS = (
    "搭把手", "搭个手", "搭下手", "搭手",
    "聊聊", "聊一下", "聊一聊",
    "方便吗", "方便聊聊", "方便聊一下",
    "能搭把手吗", "能聊一下吗", "能聊聊吗", "能搭把手吗？",
    "能了解一下吗", "可以搭手",
)


def _normalize_greeting_closing(greeting: str) -> str:
    """Force greeting to end with a preferred closing phrase.

    行为：
    1. 若末尾 25 字内已含首选/备选/次选话术 → 不动
    2. 若末尾 25 字内出现禁词尾巴 → 截掉禁词及之前 8 字 + 拼首选
    3. 否则 → 截掉末尾标点 + 拼首选话术「方便发您一份简历看看吗？」

    调用前 greeting 已单行化（兜底 2）。
    """
    if not greeting:
        return greeting
    import re as _re_norm
    tail = greeting[-25:]
    # Case 1: 已是合规收尾
    for ok in _PREFERRED_CLOSINGS:
        if ok in tail:
            return greeting
    # Case 2: 禁词尾巴
    for bad in _BANNED_CLOSING_TAILS:
        if bad in tail:
            greeting = _re_norm.sub(rf"[，,;；]?[^，。；,;]{{0,8}}{_re_norm.escape(bad)}[。？]?\s*$", "", greeting)
            break
    # Case 3: 都没中——截末尾标点 + 拼首选
    greeting = greeting.rstrip("。？!！，,;； ")
    return greeting + "，方便发您一份简历看看吗？"


def _parse_salary_range_k(salary_raw: str) -> tuple[float, float] | None:
    """Return (low_k, high_k) monthly salary in K, or None if unparseable.

    Handles formats like '20-30K·13薪', '25K', '25-35K'.
    Both bounds are annualised to monthly equivalents when a multiplier is present.
    """
    import re
    if not salary_raw:
        return None
    raw = salary_raw.strip().upper()
    m = re.search(r"·(\d+)薪", raw)
    multiplier = int(m.group(1)) if m else 12
    rng = re.search(r"(\d+(?:\.\d+)?)[- ](\d+(?:\.\d+)?)K", raw)
    if rng:
        lo = float(rng.group(1)) * multiplier / 12
        hi = float(rng.group(2)) * multiplier / 12
        return lo, hi
    single = re.search(r"(\d+(?:\.\d+)?)K", raw)
    if single:
        v = float(single.group(1)) * multiplier / 12
        return v, v
    return None


def _parse_salary_low_k(salary_raw: str) -> float | None:
    """Return lower bound monthly salary in K, or None if unparseable."""
    r = _parse_salary_range_k(salary_raw)
    return r[0] if r else None


_profile_cache = _load_candidate_profile()
_TECH_ROLE_BLOCKLIST: list[str] = _profile_cache.get("role_blocklist", [
    "Java工程师", "C++工程师", "C#工程师", "Go工程师", "Rust工程师",
    "前端工程师", "后端工程师", "全栈工程师",
    "算法工程师", "机器学习工程师", "深度学习工程师", "NLP工程师", "CV工程师",
    "大数据工程师", "数据工程师", "数据开发", "推荐算法",
    "测试工程师", "QA工程师", "运维工程师", "SRE", "DevOps工程师",
    "iOS工程师", "Android工程师", "嵌入式工程师",
])
_HYBRID_EXEMPTIONS: list[str] = _profile_cache.get("role_exemptions", [
    "产品", "PM", "经理", "负责人", "AI产品", "研发管理",
])


def _pre_filter_job(title: str) -> bool:
    """返回 True 表示应跳过（职位标题命中纯技术岗黑名单且无产品职能词）。"""
    t = title.strip()
    if any(ex in t for ex in _HYBRID_EXEMPTIONS):
        return False
    return any(kw in t for kw in _TECH_ROLE_BLOCKLIST)


def _description_filter_job(
    job: dict,
    industry_blocklist: list[str],
    min_base_salary_k: float,
) -> str | None:
    """基于详情页内容过滤。返回跳过原因字符串，或 None 表示通过。"""
    desc = job.get("description") or ""
    company_info = job.get("company_info") or ""
    full_text = desc + " " + company_info

    for kw in industry_blocklist:
        if kw in full_text:
            return f"行业关键词「{kw}」"

    if min_base_salary_k > 0:
        import re
        m = re.search(
            r"(?:底薪|基本工资)[：:\s约不低于]*(\d+(?:\.\d+)?)\s*([kKwW万]?)",
            full_text,
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


def _extract_job_location(job: dict, known_cities: list[str]) -> str:
    """Extract city from job location field, then description + company_info text.

    Checks dedicated location field first, then first 500 chars of description
    and all of company_info.
    Returns 'full_remote', 'remote', a city name, or 'unknown'.
    """
    # 1. 优先用详情页专用地点字段（tv_area_district / tv_job_area）
    loc_field = job.get("location") or ""
    if loc_field:
        if "全远程" in loc_field or "居家办公" in loc_field:
            return "全远程"
        if "远程" in loc_field:
            return "远程"
        for city in known_cities:
            if city not in ("全远程", "远程", "default") and city in loc_field:
                return city

    # 2. 兜底：扫描 company_info + description 前500字
    text = (job.get("company_info") or "") + " " + (job.get("description") or "")[:500]
    if "全远程" in text or "居家办公" in text:
        return "全远程"
    if "远程" in text:
        return "远程"
    for city in known_cities:
        if city not in ("全远程", "远程", "default") and city in text:
            return city
    return "unknown"


def _build_scoring_rubric(dimensions: dict) -> str:
    """Build the scoring rubric string from candidate_profile.yaml scoring_dimensions."""
    if not dimensions:
        return (
            "评分标准（0-10）：10=完全匹配，7-9=良好匹配，5-6=部分匹配，3-4=方向不同，1-2=基本不相关"
        )
    total_w = sum(d.get("weight", 0) for d in dimensions.values())
    lines = [
        f"请按以下 {len(dimensions)} 个维度独立评分（每维度 0-10 分），"
        f"最终得分 = Σ(维度分 × 权重)，四舍五入到整数（权重合计 {total_w:.0%}）：",
    ]
    for dim in dimensions.values():
        w = int(dim.get("weight", 0) * 100)
        lines.append(f"- **{dim.get('label', '未命名')}**（权重 {w}%）：{dim.get('desc', '')}")
    return "\n".join(lines)


def _is_big_tech_string_match(company: str, whitelist: dict | None) -> bool:
    """硬字符串 fallback（仅在 LLM 未输出 is_big_tech 字段时使用）。

    与 2026-10-20 之前的 `is_big_tech()` 函数行为完全一致——保留代码便于回滚，
    但不再作为主要判断路径（已被 LLM 评替代，理由：枚举难维护/撞名/未列公司
    无法命中）。whitelist=None → True（视作未启用过滤）。
    """
    if not whitelist:
        return True
    if not company:
        return False
    parents = whitelist.get("parents", []) or []
    subsidiaries = whitelist.get("subsidiaries", {}) or {}
    if any(p and p in company for p in parents):
        return True
    for brands in subsidiaries.values():
        if any(b and b in company for b in (brands or [])):
            return True
    return False


def _build_big_tech_context(whitelist: dict | None) -> str:
    """Build the big-tech reference section injected into match_score.md prompt.

    Returns "" when whitelist is None or empty (LLM judges purely from training data).
    Returns formatted text listing parents + subsidiary brands when provided.

    Why a reference, not a hard rule:
        Hard-coding 40+ companies misses new entrants (e.g., a startup that just IPO'd
        in 2027) and breaks on company-name collisions. LLM can use the list as a
        hint but still recognize 未列公司.
    """
    if not whitelist:
        return ""
    parts = ["## 大厂参考清单（用户口径，仅供参考；你仍可独立判定名单外的公司）"]
    parts.append(
        "判定标准：互联网头部（BAT/TMD/华为/京东/拼多多/小米/网易/快手）+ AI 新势力 "
        "（月之暗面/智谱/MiniMax/阶跃星辰/深度求索/百川/零一万物）+ 半导体/芯片 "
        "（寒武纪/地平线/摩尔线程/燧原）+ 自动驾驶（小马智行/Momenta/文远知行）+ "
        "外企中国（Google/Microsoft/Meta/OpenAI/Anthropic）+ 准大厂 "
        "（SHEIN/Shopee/米哈游/莉莉丝/鹰角/理想/蔚来/小鹏/B站/知乎/得物）。"
    )
    parents = whitelist.get("parents") or []
    if parents:
        # 截断到前 30 防 prompt 过长；LLM 见到关键样本即可推断其他
        sample = parents[:30]
        parts.append("**母公司样本**（共 {} 家）：{}".format(
            len(parents), "、".join(sample) + (" …等" if len(parents) > 30 else "")
        ))
    subsidiaries = whitelist.get("subsidiaries") or {}
    if subsidiaries:
        # 每个母公司只展示前 5 个品牌
        lines = []
        for parent, brands in list(subsidiaries.items())[:8]:
            lines.append(f"- {parent}: {'/'.join(brands[:5])}")
        parts.append("**旗下品牌样本**（每个母公司取前 5 个）：\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _extract_publish_days(job: dict) -> float | None:
    """Parse publish time from raw_texts. Returns days (float) or None."""
    import re as _re
    for text in (job.get("raw_texts") or []):
        t = text.strip()
        if "刚刚" in t:
            return 0.0
        m = _re.search(r"(\d+)\s*小时前", t)
        if m:
            return int(m.group(1)) / 24.0
        m = _re.search(r"(\d+)\s*天前", t)
        if m:
            return float(m.group(1))
        m = _re.search(r"(\d+)\s*周前", t)
        if m:
            return float(m.group(1)) * 7
        m = _re.search(r"(\d+)\s*个月前", t)
        if m:
            return float(m.group(1)) * 30
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


def _parse_company_info(company_info: str) -> tuple[str, str]:
    """Parse 'company_info' field (e.g. '不需要融资 • 1000-9999人 • 智能硬件').
    Returns (scale, financing) strings for dict lookup."""
    import re as _re
    scale = ""
    financing = ""
    for part in company_info.split("•"):
        p = part.strip()
        if _re.search(r"\d", p) and "人" in p:
            scale = p
        elif any(kw in p for kw in ["融资", "上市", "天使", "种子", "轮", "不需要"]):
            financing = p
    return scale, financing


def _extract_hr_active_days(hr_active: str) -> float | None:
    """Parse hr_active text → days (float) or None.
    Handles: "今天活跃"→0, "3天前活跃"→3, "本周活跃"→5, "本月活跃"→20.
    """
    import re as _re
    t = (hr_active or "").strip()
    if not t:
        return None
    if "今天" in t:
        return 0.0
    m = _re.search(r"(\d+)\s*天前", t)
    if m:
        return float(m.group(1))
    if "本周" in t:
        return 5.0
    if "本月" in t:
        return 20.0
    m = _re.search(r"(\d+)\s*个月前", t)
    if m:
        return float(m.group(1)) * 30
    return None


def _get_hr_active_weight(days: float | None, cfg: dict) -> float:
    if days is None:
        return cfg.get("unknown", 1.0)
    if days <= 0:
        return cfg.get("today", 1.1)
    if days <= 3:
        return cfg.get("within_3d", 1.05)
    if days <= 7:
        return cfg.get("within_7d", 1.0)
    if days <= 30:
        return cfg.get("within_30d", 0.95)
    return cfg.get("older", 0.9)


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
    resume: str,
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
    location_weights: dict | None = None,
    description_filters: dict | None = None,
    fallback: anthropic.Anthropic | None = None,
    company_size_weights: dict | None = None,
    recency_weights: dict | None = None,
    financing_weights: dict | None = None,
    scoring_dimensions: dict | None = None,
    company_tier_weights: dict | None = None,
    hr_active_weights: dict | None = None,
    big_tech_whitelist: dict | None = None,  # candidate_profile.yaml 的 big_tech_whitelist 段；注入 score_job prompt 作为 LLM 参考
    only_big_tech: bool = False,             # True 时 LLM 输出 is_big_tech=False 则 skip（仍需评分但不发问候）
    daily_hard_limit: int = 0,  # 0 = 不设硬限（Boss 无公布每日上限）
    max_list_scrolls: int = 12,    # 列表无新卡时最大连滑次数（默认 12，原硬编码 5）
    scroll_px: int = 500,          # 单次下滑距离 px（默认 500，原硬编码 320）
    scroll_wait_secs: float = 2.5, # 滑后等待懒加载秒数（默认 2.5，原硬编码 1.5）
    strong_scroll_px: int = 1100,  # 强滑距离 px（no_target_scrolls >= scroll_after_n_misses 时启用）
    strong_scroll_wait_secs: float = 4.0,  # 强滑后等待懒加载秒数
    scroll_after_n_misses: int = 3,        # 连续无新卡 N 次后切换强滑
) -> dict:
    """
    Single-phase live loop: for each job card, enter detail page → score → send if OK.
    Returns result dict with greeted / skipped / errors lists.
    """
    logger = setup_logger("smart_match_greet", log_dir="./logs/boss")
    adb = ADBRunner()
    skill = BOSSAutomationSkill(adb, device_id=device_id)
    # ── 2026-10-13 排查埋点：让 BOSSAutomationSkill 子 logger 也能输出 ──
    # android_skill.py:53 用 logging.getLogger("BOSSAutomationSkill") 拿到的 logger
    # 既没自己的 handler，propagate 也不到 root（root 默认 WARNING），所以 skill 内部
    # 50+ 条 INFO 全部静默。给它加一个 StreamHandler（指向 stderr，跟外层 logger 区分）
    # 不改 setup_logger 本体（避免影响其他脚本），不改 BOSSAutomationSkill.__init__。
    import logging as _logging
    _skill_log = _logging.getLogger("BOSSAutomationSkill")
    if not _skill_log.handlers:
        _sh = _logging.StreamHandler()
        _sh.setFormatter(_logging.Formatter("%(asctime)s [SKILL] %(levelname)-7s %(message)s", "%H:%M:%S"))
        _skill_log.addHandler(_sh)
        _skill_log.setLevel(_logging.INFO)
        _skill_log.propagate = False
    result: dict = {"greeted": [], "skipped": [], "errors": [], "all_scores": []}

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
        return _run_loop(skill, resume, client, keyword, threshold, max_greet,
                         strict, min_salary_k, verify_send, db_conn, score_only, result, logger,
                         location_weights=location_weights,
                         description_filters=description_filters,
                         fallback=fallback,
                         company_size_weights=company_size_weights,
                         recency_weights=recency_weights,
                         financing_weights=financing_weights,
                         scoring_dimensions=scoring_dimensions,
                         company_tier_weights=company_tier_weights,
                         hr_active_weights=hr_active_weights,
                         big_tech_whitelist=big_tech_whitelist,
                         only_big_tech=only_big_tech,
                         daily_hard_limit=daily_hard_limit,
                         max_list_scrolls=max_list_scrolls,
                         scroll_px=scroll_px,
                         scroll_wait_secs=scroll_wait_secs,
                         strong_scroll_px=strong_scroll_px,
                         strong_scroll_wait_secs=strong_scroll_wait_secs,
                         scroll_after_n_misses=scroll_after_n_misses)
    finally:
        skill._adb("shell svc power stayon false")
        skill._adb(f"shell settings put system screen_off_timeout {_orig_timeout}")
        skill._adb(f"shell settings put secure lock_screen_lock_after_timeout {_orig_lock_timeout}")
        skill._adb(f"shell settings put secure adaptive_sleep {_orig_adaptive}")


def _find_next_job(
    skill: "BOSSAutomationSkill",
    visited_keys: set[str],
    initial_jobs: list,
    db_conn: sqlite3.Connection,
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
        db_conn: SQLite connection. Used to disambiguate title-only
            visited_keys hits via _is_already_greeted (2026-10-13 fix:
            同 title 不同 company 永久误杀的根因).

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
        f"{normalize_card_title(j.title)}\t{j.company or ''}\t{j.hr_name or ''}": j for j in initial_jobs
    }

    # 2026-10-13：每次屏幕 dump 都把全部可见卡的判定结果打出来（visited_keys 命中 /
    # DB 命中 / CANDIDATE），让用户能直接看到「屏幕上有什么 + 为什么没访问 + 选了哪张」。
    # 之前默认静默 continue 漏掉了「天仪基业/牧唐技术/帅仕」这类漏访问的根因排查。
    # 用外层 logger（已 setup_stream handler），不用 skill._logger（没 handler、propagate
    # 到 root、默认 WARNING 级看不到 INFO）。
    if visible:
        logger.info(
            "[find_next] 屏幕共 %d 张卡（visited_keys size=%d）", len(visible), len(visited_keys),
        )
    for _idx, v in enumerate(visible):
        logger.info(
            "[find_next]   屏幕卡 y=%d  %r  /  %r  /  hr=%r",
            v.tap_y, v.title, v.company, v.hr_name,
        )

    for v in visible:
        v_key = f"{normalize_card_title(v.title)}\t{v.company or ''}\t{v.hr_name or ''}"
        title_only_key = f"{normalize_card_title(v.title)}\t\t{v.hr_name or ''}"
        # v_key 精确命中（title+company+hr_name 都一致）→ 本 session 内真访问过。
        # 2026-10-13 修复（item 4 强化版）：visited_keys 假命中兜底 —— 检查 job_visits
        # 是否有**任何记录**（greeted=1 OR 0），不只查 _is_already_greeted（只查 greeted=1
        # 会让低分/导航失败卡误判为「未处理」，导致同卡无限重试）。
        # 边界场景：阿里巴巴·陈香雨 vs 丁玲芝 同 title+company 不同 hr（item 1 已分 key，
        # 不会 hit；万一 hit 兜底也会放行，因为 job_visits 没陈香雨记录）。
        if v_key in visited_keys:
            # 2026-10-15 修复（公司名跨视图不一致兜底放行死循环） + 2026-10-19 扩展（品牌名 vs 公司全名）：
            # 列表卡 RecyclerView 懒加载字段常空/截短/品牌化：
            #   - company 短名 vs detail 全名（如「纵维立方科技」 vs「深圳市纵维立方科技有限公司」）— INSTR 修复
            #   - company 品牌短名 vs detail 公司全名（如「融和科技」 vs「北京融和友信科技股份有限公司」）— INSTR 双向都不匹配
            #     （被"友"字打断），需要 bigram 重叠系数（见 `_bigram_overlap`）
            #   - hr_name 列表卡空 vs detail 有值（如 列表 hr='' / detail hr='张女士'）— hr INSTR 修复
            # job_visits 写的是详情页字段；COUNT=0 → 误判「未访问」 → 兜底放行 → 同卡死循环评分 ≥10 次
            # （实测 22 次 send 失败后才发现，浪费 LLM 配额且进度 0）。
            # 改写：拉所有同 title 候选行，Python 端用 INSTR + bigram 重叠 4-tier 判定 company 是否同一公司，
            # hr_name 仍走 INSTR（中文姓名无需 bigram）。
            # 阈值：bigram overlap ≥ 0.66 且 list company 长度 ≥ 3（避免短 brand 误匹配）。
            _candidates = db_conn.execute(
                "SELECT company, COALESCE(hr_name, '') FROM job_visits WHERE title = ?",
                (normalize_card_title(v.title),),
            ).fetchall()
            list_co = v.company or ""
            list_hr = v.hr_name or ""
            _matched = False
            for _row_co, _row_hr in _candidates:
                # ── company 4-tier: 精确 / INSTR (双向) / bigram 重叠 / list 空退化
                if list_co == "":
                    _co_ok = True
                else:
                    _co_ok = (
                        _row_co == list_co
                        or (list_co and list_co in _row_co)
                        or (_row_co and _row_co in list_co)
                        or (len(list_co) >= 3
                            and _bigram_overlap(list_co, _row_co) >= 0.66)
                    )
                if not _co_ok:
                    continue
                # ── hr_name 3-tier: 精确 / INSTR (双向) / list 空退化
                if list_hr == "":
                    _hr_ok = True
                else:
                    _hr_ok = (
                        _row_hr == list_hr
                        or (list_hr and list_hr in _row_hr)
                        or (_row_hr and _row_hr in list_hr)
                    )
                if not _hr_ok:
                    continue
                _matched = True
                break
            if _matched:
                logger.info(
                    "[find_next]   ↳ skip: visited_keys命中 + job_visits有记录 (v_key=%r, candidates=%d)",
                    v_key, len(_candidates),
                )
                continue
            # job_visits 无记录 → 假命中（key 错配或 visited_keys 外部污染），兜底放行
            logger.info(
                "[find_next]   ↳ OK: visited_keys命中但job_visits无记录，兜底放行 (v_key=%r, hr_name=%r)",
                v_key, v.hr_name or "",
            )
            # 不 continue：fall through 到 by_key lookup 路径
        # title-only 命中：可能是 (a) 同 job 在 RecyclerView 复用位置被重复抓取
        # （line 1477 的占位 key 闭环），也可能是 (b) 同 title 不同 company 的另一张卡。
        # 区分 (a)/(b) 由 _is_already_greeted 的 Layer 5 second branch 决定——
        # 当新 visit hr_name 非空时，second branch 只在「历史有 greeted=1 且 hr_name 空」
        # 的情况下才 block，所以同 title 不同 HR 不会误杀。
        # 修 2026-10-13：之前直接 continue 会让「同 title 不同 company」永久被
        # visited_keys 假命中挡住（row 467「前沿部署工程师 FDE / 北京工场」之后
        # 所有「前沿部署工程师 FDE / 其他公司」包括天仪基业都被误杀）。
        if title_only_key in visited_keys:
            if _is_already_greeted(db_conn, v.title, v.company, v.hr_name):
                logger.info(
                    "[find_next]   ↳ skip: title-only命中 + DB已打过招呼 (title=%r)",
                    v.title,
                )
                continue
            else:
                logger.info(
                    "[find_next]   ↳ OK: title-only命中但DB未打过招呼，放行 (title=%r)",
                    v.title,
                )
        full = by_key.get(v_key)
        if full is None:
            # 屏幕卡不在 initial 集合里（重新搜索后 Boss 换了排序/出新卡）——
            # 用屏幕卡自身返回：tap 坐标新鲜，字段可能不全，
            # 但 Layer 1/2/3 校验已能兜底，不应静默丢弃新卡。
            logger.info(
                "[find_next]   ↳ OK: 屏幕卡不在 initial 集合（new card），返回 v (y=%d)",
                v.tap_y,
            )
            return v
        # 用 full 的完整字段 + v 的新鲜坐标
        logger.info(
            "[find_next]   ↳ OK: 选中 (y=%d) %r / %r",
            v.tap_y, v.title, v.company,
        )
        return replace(full, tap_x=v.tap_x, tap_y=v.tap_y)

    return None


# Boss App 在搜索结果列表末尾展示的"已加载完毕"提示文案（多个变体）。
# 通过 UIAutomator dump 出的 XML 检测这些文案，比"连滑 N 次无新卡"更准。
_LIST_EXHAUSTED_MARKERS = (
    "已显示全部",
    "已加载全部",
    "没有更多了",
    "没有更多职位",
    "没有更多数据",
    "暂无更多",
    "— 暂无更多数据 —",
    "已到底部",
    "已无更多",
)


def _is_list_exhausted_by_marker(skill) -> bool:
    """通过 UI XML 文案检测 Boss 列表是否已到底。

    比 ``_find_next_job`` 返 None + 连滑 N 次的间接判断更可靠。
    若 XML 中含任一 ``_LIST_EXHAUSTED_MARKERS`` 字符串即视为到底。
    """
    xml = skill.get_ui_hierarchy()
    if not xml:
        return False
    for marker in _LIST_EXHAUSTED_MARKERS:
        if marker in xml:
            return True
    return False


def _dumpsys_keyguard_state(skill) -> str:
    """Return keyguard/wakefulness state for diagnosing unlock failures.

    Format: 'Wakefulness=X | DreamingLockscreen=Y | isInteractive=Z'
    """
    try:
        # _adb returns Tuple[bool, str] — unpack stdout only
        _, out = skill._adb("shell dumpsys deviceidle | grep -E 'mWakefulness|mDreamingLockscreen'")
        _, is_int = skill._adb("shell dumpsys power | grep -E 'mWakefulness=|isInteractive'")
        return f"{out.strip()} || {is_int.strip()}"
    except Exception as e:
        return f"<dumpsys failed: {e}>"


def _check_dumpsys_or_abort(skill, step_label: str, raw_output: str) -> bool:
    """dumpsys 输出非空且 ADB 仍连接才放行。返 False 时 caller 必须中止。

    Why: P0 入口检查 (live_greet_loop:1432-1445) 只挡"进入即断开"，但 ADB 可能在
    _try_auto_unlock 5 步执行途中掉线（2026-10-19 run_fde_40_rerun 实测）。
    _dumpsys_keyguard_state() 内部用 `_, out = skill._adb(...)` 丢弃 success bool，
    返回 " || " 拼接两个空字符串 → caller 完全无法察觉 ADB 已掉。

    判定策略：
      1. raw_output 非空（正常情况）→ 放行，返 True
      2. raw_output 为空 + is_device_connected()=False → 中途掉线：
         logger.error 附 step_label + adb devices 输出，返 False
      3. raw_output 为空 + 设备仍 connected → 罕见瞬态空（grep pipe race），
         logger.warning 继续（不 false-positive 中断成功路径）

    Args:
        skill: BOSSAutomationSkill 实例
        step_label: 当前 step 标签（用于 log 排查，如 "0" / "1" / "3" / "5"）
        raw_output: dumpsys 调用的 std 输出（可能为空字符串）

    Returns:
        bool: True=可继续；False=ADB 已断开，caller 必须中止并返 False
    """
    if raw_output.strip():
        return True
    # is_device_connected 在 adb server 挂掉时会抛异常而非返 False；
    # 捕获后按「断开」处理——宁可中止也不能把 PIN 打进未知 focus。
    try:
        connected = skill.adb.is_device_connected(skill.device_id)
    except Exception as e:
        logger.error(
            "❌ [step %s] is_device_connected 调用异常（按断开处理）: %s", step_label, e
        )
        return False
    if not connected:
        logger.error(
            "❌ [step %s] dumpsys 返回空 + ADB 设备已断开，中止自动解锁",
            step_label,
        )
        try:
            _, devs = skill._adb("devices")  # 诊断用
        except Exception:
            devs = ""
        logger.error("    当前 adb devices: %s", devs.strip() or "<empty>")
        return False
    logger.warning(
        "⚠️  [step %s] dumpsys 返回空但 ADB 仍连接（疑似瞬态），继续",
        step_label,
    )
    return True


def _try_auto_unlock(skill) -> bool:
    """Auto-unlock via PIN from PIXELCLAW_DEVICE_PIN env var.

    适用：
    - 仅数字 PIN（4-6 位），不支持图案 / 指纹 / 面部
    - env var 未设或非数字 → 跳过自动解锁，返回 False（调用方需走手动等待路径）

    Pixel 设备 unlock 流程（2026-10-10 实测）：
    1. KEYCODE_WAKEUP → 屏幕亮，但只显示 AOD clock
    2. **关键**：必须 `input tap 540 1200`（中点 tap）才能让 keyguard 取得焦点，
       否则后续 swipe 会被 AOD 吞掉不响应——单 WAKEUP+swipe 是不行的
    3. **fast swipe (30ms duration) 从底部 (540, 2400) 到上方 (540, 200)**
       ——慢 swipe 会被 AOD 吞掉不响应，必须 ≤100ms 才触发 PIN pad
    4. `input text $PIN` 输 PIN
    5. KEYCODE_ENTER 提交
    6. 等待 3s 系统验证 + 跳转到 launcher（mDreamingLockscreen=false 才是真解锁成功）

    Returns:
        True if PIN 解锁已尝试（不论成功与否，调用方需自己验证解锁状态）
        False if 未启用自动解锁（env 未设 / 非数字 PIN）
    """
    pin = _os_for_default.environ.get("PIXELCLAW_DEVICE_PIN", "").strip()
    if not pin:
        return False
    if not pin.isdigit():
        logger.warning(
            "  PIXELCLAW_DEVICE_PIN 含非数字字符，跳过自动解锁（仅支持数字 PIN）"
        )
        return False
    logger.info("  尝试用 PIN 自动解锁（%d 位）", len(pin))
    state_before = _dumpsys_keyguard_state(skill)
    if not _check_dumpsys_or_abort(skill, "0", state_before.split(" || ")[0]):
        return False  # ADB 中途断开：不打 PIN，立即中止
    logger.info("    [step 0] 解锁前状态: %s", state_before)
    # Step 1: wake screen
    skill._adb("shell input keyevent KEYCODE_WAKEUP")
    time.sleep(1.0)
    state_after_wakeup = _dumpsys_keyguard_state(skill)
    if not _check_dumpsys_or_abort(skill, "1", state_after_wakeup.split(" || ")[0]):
        return False
    logger.info("    [step 1] WAKEUP 已发, 状态: %s", state_after_wakeup)
    # Step 2: tap to focus keyguard
    skill._adb("shell input tap 540 1200")
    time.sleep(0.8)
    state_after_tap = _dumpsys_keyguard_state(skill)
    if not _check_dumpsys_or_abort(skill, "2", state_after_tap.split(" || ")[0]):
        return False
    logger.info("    [step 2] tap(540,1200) 完成, 状态: %s", state_after_tap)
    # Step 3: fast swipe to reveal PIN pad
    skill._adb("shell input swipe 540 2400 540 200 30")
    time.sleep(1.5)
    # 验证：PIN pad 是否弹出？通过 dumpsys window 看 mFocusedApp 是否切换到 keyguard PIN 输入界面
    _, cur_focus_raw = skill._adb("shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -2")
    if not _check_dumpsys_or_abort(skill, "3", cur_focus_raw):
        return False
    cur_focus = cur_focus_raw.strip().replace("\n", " | ")
    logger.info("    [step 3] swipe 完成, 当前焦点: %s", cur_focus)
    # Step 4: input PIN
    skill._adb(f"shell input text '{pin}'")
    time.sleep(0.5)
    logger.info("    [step 4] PIN 已输入（%d 位）", len(pin))
    # Step 5: ENTER
    skill._adb("shell input keyevent KEYCODE_ENTER")
    time.sleep(3.0)
    # 验证：mDreamingLockscreen=false 才算真解锁
    post_state = _dumpsys_keyguard_state(skill)
    if not _check_dumpsys_or_abort(skill, "5", post_state.split(" || ")[0]):
        return False
    is_unlocked = "mDreamingLockscreen=false" in post_state or "mDreamingLockscreen=Doze" in post_state
    logger.info("    [step 5] ENTER 已发, 解锁后: %s → %s",
                post_state, "✓ 已解锁" if is_unlocked else "✗ 仍锁屏")
    return True


def _run_loop(
    skill: "BOSSAutomationSkill",
    resume: str,
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
    location_weights: dict | None = None,
    description_filters: dict | None = None,
    fallback: anthropic.Anthropic | None = None,
    company_size_weights: dict | None = None,
    recency_weights: dict | None = None,
    financing_weights: dict | None = None,
    scoring_dimensions: dict | None = None,
    company_tier_weights: dict | None = None,
    hr_active_weights: dict | None = None,
    big_tech_whitelist: dict | None = None,  # candidate_profile.yaml 的 big_tech_whitelist 段；注入 score_job prompt 作为 LLM 参考
    only_big_tech: bool = False,             # True 时 LLM 输出 is_big_tech=False 则 skip（仍需评分但不发问候）
    daily_hard_limit: int = 0,  # 0 = 不设硬限（Boss 无公布每日上限）
    max_list_scrolls: int = 12,    # 列表无新卡时最大连滑次数（默认 12，原硬编码 5）
    scroll_px: int = 500,          # 单次下滑距离 px（默认 500，原硬编码 320）
    scroll_wait_secs: float = 2.5, # 滑后等待懒加载秒数（默认 2.5，原硬编码 1.5）
    strong_scroll_px: int = 1100,  # 强滑距离 px（no_target_scrolls >= scroll_after_n_misses 时启用）
    strong_scroll_wait_secs: float = 4.0,  # 强滑后等待懒加载秒数
    scroll_after_n_misses: int = 3,        # 连续无新卡 N 次后切换强滑
) -> dict:
    # P0: fail fast if the device isn't even reachable.  Without this guard,
    # every adb call in _try_auto_unlock returns (False, "") silently, so we
    # burn 2 min in 12 retries before surfacing the real error.  Symptom
    # observed 2026-10-13: phone ADB dropped (USB unplugged / wireless sleep),
    # 12 unlock retries × 6 steps = 72 empty adb commands, all `dumpsys`
    # returns empty string, script only fails after 120s wait.
    if not skill.adb.is_device_connected(skill.device_id):
        logger.error(
            "❌ ADB 设备未连接（device_id=%r），跳过 unlock", skill.device_id
        )
        result["errors"].append(
            "ADB 设备未连接：请检查 USB / 无线调试，或运行 `adb devices` 确认"
        )
        return result

    # 确认屏幕亮着且手机已解锁（即 UI 中有 App 内容而非只有 SystemUI 锁屏）
    # 注意：若 PIN 自动解锁已配置，**先尝试自动解锁**再走 fallback 路径。
    # 因为 `skill._wake_screen()` 内部用 `wm dismiss-keyguard`，会把设备
    # 留在一个特殊状态（mWakefulness=Awake 但 mDreamingLockscreen=true），
    # 后续 PIN 解锁序列（tap + fast swipe）会失效。所以顺序很重要：
    # PIN 配置 → 直接 _try_auto_unlock（它自带 KEYCODE_WAKEUP，无需预唤醒）。
    _pin_configured = bool(_os_for_default.environ.get("PIXELCLAW_DEVICE_PIN", "").strip())
    if _pin_configured and not skill._is_screen_on():
        # PIN 已配置时，跳过 _wake_screen，直接交给 _try_auto_unlock 处理唤醒+解锁
        if _try_auto_unlock(skill) and skill._is_screen_on():
            logger.info("  ✓ PIN 自动唤醒成功")
        else:
            logger.warning("⚠️  PIN 自动唤醒失败，等待手动解锁（最多 120 秒）…")
            logger.warning("    当前状态: %s", _dumpsys_keyguard_state(skill))
            _unlocked = False
            for attempt in range(1, 13):
                # P1: if first attempt already failed AND device is gone, bail
                # out now instead of spinning 11 more useless retries.  Catches
                # the case where P0 early-check passed but ADB drops during
                # the long 12×10s sleep window (e.g., wireless ADB sleep).
                if attempt == 1 and not skill.adb.is_device_connected(skill.device_id):
                    logger.error("❌ ADB 设备在 unlock 过程中断开，停止重试")
                    result["errors"].append("ADB 设备已断开连接")
                    return result
                time.sleep(10)
                logger.info("    [retry %d/12] 重试 _try_auto_unlock...", attempt)
                if _try_auto_unlock(skill) and skill._is_screen_on():
                    _unlocked = True
                    break
            if not _unlocked:
                logger.error("    12 次重试后仍锁屏，最终状态: %s", _dumpsys_keyguard_state(skill))
                result["errors"].append("手机锁屏未解，无法启动")
                return result
    elif not skill._is_screen_on():
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
        # 锁屏界面在前。尝试 PIN 自动解锁
        if _try_auto_unlock(skill) and not _is_lock_screen(skill.get_ui_hierarchy()):
            logger.info("  ✓ PIN 自动解锁成功")
        else:
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

    logger.info("[1/2] 启动 Boss直聘…")
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

    logger.info("[2/2] 搜索职位：「%s」…", keyword)
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

    # 无基线：直接从当前屏幕开始处理；hr_name 等懒加载字段由详情页补全
    jobs: list = []

    visited_keys: set[str] = set()
    # ── 2026-10-13 修复：导航失败 max retry 计数（item 3 配套，避免 discard 后无限重试） ──
    _NAV_FAIL_MAX = 2
    _nav_fail_count: dict[str, int] = {}
    greeted_count = 0
    no_target_scrolls = 0
    processed = 0  # 用于日志显示的「第 N 条」计数器

    # ── 每日上限保护（主动检查，不依赖弹窗被动检测）──
    # 2026-10-12 修订：用户实测 Boss 直聘**无公布每日硬限**——dialy_hard_limit=0
    # 表示不设硬限（仅受 Boss 内部限流机制影响，触发时手动退出即可）。
    DAILY_HARD_LIMIT = daily_hard_limit  # 来自 candidate_profile.yaml daily_hard_limit
    DAILY_WARN_LIMIT = (DAILY_HARD_LIMIT - 20) if DAILY_HARD_LIMIT > 0 else 0
    today_count = _count_today_greeted(db_conn)
    logger.info("  今日已发送 %d 条招呼", today_count)
    if DAILY_HARD_LIMIT > 0 and today_count >= DAILY_HARD_LIMIT:
        logger.error(
            "今日已发送 %d 条，已达每日上限（%d），退出。明日再运行。",
            today_count, DAILY_HARD_LIMIT,
        )
        return result
    if DAILY_WARN_LIMIT > 0 and today_count >= DAILY_WARN_LIMIT:
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
        target = _find_next_job(skill, visited_keys, jobs, db_conn)
        if target is None:
            no_target_scrolls += 1
            # ── 修复方向 3：先看 Boss UI 是否有"已显示全部"等到底提示，有则立即结束
            if _is_list_exhausted_by_marker(skill):
                logger.info(
                    "  列表已无未访问目标（UI 文案检测到底），结束"
                )
                break
            if no_target_scrolls > max_list_scrolls:
                logger.info(
                    "  列表已无未访问目标（连滑 %d 次都没新卡），结束",
                    no_target_scrolls - 1,
                )
                break
            # ── 修复方向 1+2：连续失败 >= scroll_after_n_misses 时切换强滑
            # 强滑（更大距离 + 更长等待）触发 Boss "上拉加载更多"模式，
            # 解决轻滑被忽略、列表卡在原页的问题
            if no_target_scrolls >= scroll_after_n_misses:
                px = strong_scroll_px
                wait = strong_scroll_wait_secs
                tag = "强滑"
            else:
                px = scroll_px
                wait = scroll_wait_secs
                tag = "短滑"
            logger.info(
                "  当前屏幕无未访问目标，列表%s %d/%d (px=%d, wait=%.1fs)",
                tag, no_target_scrolls, max_list_scrolls, px, wait,
            )
            skill.scroll_down(start_y=1300, end_y=1300 - px, duration=600)
            time.sleep(wait)
            continue
        no_target_scrolls = 0

        # 注意：列表卡 hr_name/company 可能因 RecyclerView 懒加载为空，
        # 不再轮询等待（60s 浪费 ADB 带宽，2026-10-10 移除 _wait_for_list_card_data）。
        # 详情页是唯一可信源 —— 进详情后 `get_job_detail()` 必返回完整字段，
        # 下游 job_dict / effective_hr_name / 第 2 次 _is_already_greeted 都用 detail 数据。

        processed += 1
        title = target.title or ""
        company = target.company or ""
        hr_name = target.hr_name or ""
        # ── 2026-10-14 修复（兜底死循环 v2）：所有「在写 visited_keys 之前」的
        # skip 路径必须 **自己** 写 job_visits（_record_visit），否则下次
        # _find_next_job item 4 兜底查 job_visits COUNT=0 → 兜底放行 → fall through
        # → _run_loop 又走到这里 → 死循环。
        #
        # 具体案例 2026-10-14 13:50+ 跑 FDE：「FDE部署工程师（飞书+企业豆包 AI交付）」
        # company='' / hr_name='' —— visited_keys size 卡在 13，每 6s 一次循环，
        # `[visited_keys] + 精确 key` log 出现但 `[N] → ...` 从未出现，详情页日志全无。
        # 可能 throw 路径在 visited_keys.add 之后、薪酬/prefilter 检查之前的某处
        # （find_and_navigate_to_job 内部 / score_job 内部异常被外层 try 吞），或者
        # _run_loop 本身的某条无人知晓的 silent-skip 路径。无论如何：把 visited_keys.add
        # **推迟** 到所有「绝对能 continue」的检查之后 + 「绝对要 log `[N] → ...`」
        # 的位置之前，并把 _record_visit 补到每条没写的 skip 路径上，根除污染。

        # 去重检查（已落库 greetings 的不再打招呼）—— **此时还没 add visited_keys**，
        # 后续 skip_brecord_visit 会保证兜底查询命中。
        if _is_already_greeted(db_conn, title, company, hr_name):
            # 2026-10-14 修复（item 4 兜底死循环）：补 _record_visit 让 job_visits 有记录。
            # 不写的话下次 _find_next_job item 4 兜底（line 1089-1106）查 job_visits 会
            # 返回 0 记录 → 兜底放行 → fall through → _run_loop 又走到这里 → 死循环。
            # 具体案例：「AI运营专家（FDE方向）/ 腾宝科技 / 魏女士」—— DB 旧记录 hr=张女士
            # (id=409)，Layer 3 title+company+greeted=1 命中 skip，但 job_visits 不写，
            # 第二次 _find_next_job 兜底放行 → 死循环。Plan agent 验证其他 7 处 skip
            # 路径都已 record_visit，唯独此处缺失。list view 阶段 job_dict=None 安全
            # （_record_visit 内部 jd = job_dict or {} 兜底）。
            _record_visit(db_conn, title, company, hr_name, keyword, None,
                          skip_reason="已打过招呼")
            logger.info("  [%d] 跳过（已打过招呼）：%s", processed, title)
            result["skipped"].append(f"{title}（{company}）：已打过招呼")
            # 仍然加 visited_keys（精确 key + title-only）—— 这是「明确判断为已打过」，
            # 后续 _find_next_job 看到这两 key 都命中 → job_visits 已有 1 记录 → 跳过。
            _key_precise = f"{normalize_card_title(title)}\t{company}\t{hr_name}"
            _key_titleonly = f"{normalize_card_title(title)}\t\t{hr_name}"
            visited_keys.add(_key_precise)
            visited_keys.add(_key_titleonly)
            logger.info("  [visited_keys] + 精确+title-only 已打过招呼: size=%d",
                        len(visited_keys))
            continue

        # 薪资预过滤：仅当对方薪资上限也低于我的下限时才跳过 —— 2026-10-14 修复补 _record_visit
        # 2026-10-14 v3 修复：薪资 skip 也要 add visited_keys（_record_visit + visited_keys 双保险）。
        # 否则下次 _find_next_job 看到「屏幕卡不在 initial 集合（new card），返回 v」会再次
        # 返回同一张卡 → _run_loop 又走到这里 → 死循环（v2 没修干净）。
        if min_salary_k > 0:
            sal_range = _parse_salary_range_k(target.salary or "")
            if sal_range is not None and sal_range[1] < min_salary_k:
                _record_visit(db_conn, title, company, hr_name, keyword, None,
                              skip_reason=f"薪资上限{sal_range[1]:.0f}K<下限{min_salary_k:.0f}K")
                # 加 visited_keys 双 key：精确 + title-only，避免下次 _find_next_job 再次返回
                _key_precise = f"{normalize_card_title(title)}\t{company}\t{hr_name}"
                _key_titleonly = f"{normalize_card_title(title)}\t\t{hr_name}"
                visited_keys.add(_key_precise)
                visited_keys.add(_key_titleonly)
                logger.info("  [visited_keys] + 精确+title-only 薪资过滤: size=%d",
                            len(visited_keys))
                logger.info("  [%d] 跳过（薪资）：%s", processed, title)
                result["skipped"].append(
                    f"{title}：薪资上限 {sal_range[1]:.0f}K < 下限 {min_salary_k:.0f}K"
                )
                continue

        # 职位标题预过滤（明显技术岗，不进 LLM）—— 2026-10-14 v3 同上
        if _pre_filter_job(title):
            _record_visit(db_conn, title, company, hr_name, keyword, None,
                          skip_reason="技术岗预过滤")
            _key_precise = f"{normalize_card_title(title)}\t{company}\t{hr_name}"
            _key_titleonly = f"{normalize_card_title(title)}\t\t{hr_name}"
            visited_keys.add(_key_precise)
            visited_keys.add(_key_titleonly)
            logger.info("  [visited_keys] + 精确+title-only 技术岗过滤: size=%d",
                        len(visited_keys))
            logger.info("  [%d] 跳过（技术岗）：%s", processed, title)
            result["skipped"].append(f"{title}：纯技术岗跳过")
            continue

        # ── 所有「绝对能 continue」的检查都过了 —— 现在才加 visited_keys ─────
        # 精确 key（title+company+hr_name），保证同职位重复出现时立即跳过
        # 2026-10-13 修复（item 1）：key 加 hr_name —— 阿里巴巴·陈香雨 vs 丁玲芝
        # 同 title+company 不同 hr 时，old key (title+company) 会被旧卡的 key 假命中
        # 误杀；新 key 含 hr_name 后两张卡 key 区分。
        _key = f"{normalize_card_title(title)}\t{company}\t{hr_name}"
        visited_keys.add(_key)
        # ── 2026-10-13 排查埋点：精确 key 入参 + 拼接结果（key 不含 hr_name，Alibaba 假命中嫌疑） ──
        logger.info("  [visited_keys] + 精确 key: title=%r, company=%r, hr_name=%r → key=%r (size=%d)",
                    title, company, hr_name, _key, len(visited_keys))

        # 未打过招呼的 job 才加 title-only，闭环「visit 时 company 已填充 +
        # 下次循环 RecyclerView 复用位置 company 为空」的死循环场景（2026-10-10 复现）
        _key = f"{normalize_card_title(title)}\t\t{hr_name}"
        visited_keys.add(_key)
        # ── 2026-10-13 排查埋点：title-only key 入参（item 1 同步加 hr_name） ──
        logger.debug("  [visited_keys] + title-only: title=%r, company=%r, hr_name=%r → key=%r (size=%d)",
                     title, company, hr_name, _key, len(visited_keys))

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
            # ── 2026-10-13 修复（item 3 + 2 + max retry）：导航失败 4 步处理 ──
            logger.warning("  ⚠️ 导航失败：%s（%s）tap=(%d,%d) — skill 返回 False",
                           title, company, target.tap_x, target.tap_y)
            # item 3：回滚 visited_keys 污染（牧唐技术 6 秒内 0 log 期间已 add）
            visited_keys.discard(_key)
            _nav_fail_count[_key] = _nav_fail_count.get(_key, 0) + 1
            if _nav_fail_count[_key] >= _NAV_FAIL_MAX:
                # 连续失败达上限 → 永久加入 visited_keys + 记录到 DB（item 2 兜底）
                visited_keys.add(_key)
                _record_visit(db_conn, title, company, hr_name, keyword, None,
                              skip_reason=f"导航连续{_NAV_FAIL_MAX}次失败")
                result["errors"].append(
                    f"{title}：导航连续 {_NAV_FAIL_MAX} 次失败，放弃"
                )
                logger.warning(
                    "  → 连续 %d 次失败，已加入 visited_keys + DB 记录（item 2 触发）",
                    _nav_fail_count[_key],
                )
            else:
                # 第 1 次失败：discard + 让外层循环重试（item 4 兜底会再校验 DB）
                result["errors"].append(f"{title}：导航失败（{_nav_fail_count[_key]}/{_NAV_FAIL_MAX}）")
                logger.warning(
                    "  → 加入 errors 列表，visited_keys 已 discard (item 3)；下次循环重试",
                )
            continue

        # 等待详情页加载并验证职位标题 + 公司名都匹配
        # 防止：(a) 列表 tap 错卡片（旧坐标错位） (b) Boss 显示上一个职位的缓存数据
        _detail_ok = False
        logger.debug("  [detail-check] 等待详情页 tv_job_name，attempt 0/3")
        for _attempt in range(3):
            if not skill.wait_for_element("com.hpbr.bosszhipin:id/tv_job_name", timeout=3.0):
                logger.debug("  [detail-check] attempt %d：wait_for_element 3s 超时", _attempt)
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
            "location":         detail.get("location") or target.location or "",
            "company_info":     detail.get("company_info") or "",
            "hr_name":          detail.get("hr_name") or hr_name,
            "hr_title":         detail.get("hr_title") or (target.hr_title if hasattr(target, "hr_title") else ""),
            "hr_active":        detail.get("hr_active") or (target.hr_active if hasattr(target, "hr_active") else ""),
            "description":      detail.get("description") or "",
            "company_scale":    detail.get("company_scale") or "",
            "raw_texts":        detail.get("raw_texts") or [],
            "requirements_text": "  无标签（请从JD描述中判断）",
        }
        # 详情页是唯一可信源：列表卡 RecyclerView 懒加载字段常空，
        # 用 detail 覆盖 title/company/hr_name 让下游所有 record_visit/record_greeting
        # 拿到的都是准确值。同时把 visited_keys 的占位 key（line 1230-1233 加的）刷新为精确 key。
        _real_title = job_dict.get("title") or title
        _real_company = job_dict.get("company") or company
        if _real_title:
            title = _real_title
        if _real_company:
            company = _real_company
        if job_dict.get("hr_name"):
            hr_name = job_dict["hr_name"]
        # 始终用 detail 数据更新 visited_keys（幂等；set.add 重名无副作用）
        # 2026-10-13 修复（item 1）：detail 阶段 hr_name 已完整，key 同步加 hr_name
        visited_keys.add(f"{normalize_card_title(title)}\t{company}\t{hr_name}")
        visited_keys.add(f"{normalize_card_title(title)}\t\t{hr_name}")

        # Prefer detail-page hr_name (more complete); fall back to card-level hr_name.
        # Using this for all DB writes ensures consistent dedup across runs.
        effective_hr_name = job_dict.get("hr_name") or hr_name

        # 列表页 hr_name 可能为空（RecyclerView 懒加载），此时首层 _is_already_greeted 可能漏检；
        # 用详情页补全的 effective_hr_name 再校验一次。
        if effective_hr_name and effective_hr_name != hr_name:
            if _is_already_greeted(db_conn, title, company, effective_hr_name):
                logger.info("  [%d] 跳过（已打过招呼，详情页确认 hr=%s）：%s", processed, effective_hr_name, title)
                result["skipped"].append(f"{title}（{company}）：已打过招呼")
                continue

        # 详情页内容过滤（行业关键词 + 底薪下限）
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

        # Haiku 评分 + 生成打招呼（heartbeat 防止 ~30s API 调用期间锁屏）
        try:
            scored = _with_screen_heartbeat(
                skill, lambda: score_job(client, resume, job_dict, strict=strict, fallback=fallback, scoring_dimensions=scoring_dimensions, big_tech_whitelist=big_tech_whitelist)
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
                        skill, lambda: score_job(client, resume, job_dict, strict=strict, fallback=fallback, scoring_dimensions=scoring_dimensions, big_tech_whitelist=big_tech_whitelist)
                    )
                except Exception as exc2:
                    logger.error("  配额恢复后仍失败：%s — %s", title, exc2)
                    result["errors"].append(f"{title}：评分失败（配额恢复后仍失败：{exc2}）")
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
        greeting = scored.get("greeting", "")
        result["all_scores"].append(score)
        logger.info("  分数 %d/10  %s", score, scored.get("top_matches", []))
        dim_scores = scored.get("dimension_scores")
        if dim_scores:
            parts = " | ".join(f"{k}={v}" for k, v in dim_scores.items())
            logger.info("  维度分：%s", parts)
        if scored.get("mismatch_concerns"):
            logger.info("  差距：%s", scored["mismatch_concerns"])

        # 大厂判定（2026-10-20 改由 LLM 评，scored.is_big_tech + big_tech_reasoning）
        # 评分之后做是因为 LLM 需要看 JD 描述才能准确判断（有些公司名撞名但实为 startup，
        # 有些大厂用新主体名招聘——光看公司名不够）。
        # 注意：仅当 only_big_tech=True 时才检查；False 时不读此字段（旧行为零开销）。
        # 兜底：LLM 偶发漏输出 is_big_tech 字段 → 默认 False 会丢失所有发送机会；此
        # 时退化到硬字符串检查（big_tech_whitelist 是用户配置的口径，作为安全网）。
        if only_big_tech:
            _raw_ibt = scored.get("is_big_tech")
            _llm_judged = _raw_ibt is not None
            if _llm_judged:
                is_big = bool(_raw_ibt)
                reasoning = scored.get("big_tech_reasoning", "") or "（LLM 未给原因）"
                source = "LLM"
            else:
                logger.warning("  ⚠️ LLM 未输出 is_big_tech，回退硬字符串检查：%s", title)
                is_big = _is_big_tech_string_match(company, big_tech_whitelist)
                reasoning = "LLM 未给，硬白名单兜底"
                source = "硬白名单兜底"
            if not is_big:
                logger.info("  [%d] 跳过（%s判非大厂）：%s（%s）— %s", processed, source, title, company, reasoning)
                result["skipped"].append(f"{title}（{company}）：{source}判非大厂（{reasoning}）")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason=f"{source}判非大厂({reasoning})", job_dict=job_dict, scored_result=scored)
                continue
            logger.info("  %s判大厂：%s — %s", source, company, reasoning)

        # ── 地点权重调整 ───────────────────────────────────────────────────────
        if location_weights and score > 0:
            _lw = location_weights
            loc = _extract_job_location(job_dict, list(_lw.keys()))
            weight = _lw.get(loc, _lw.get("default", 1.0))
            if weight == 0:
                logger.info("  地点「%s」权重=0，跳过", loc)
                result["skipped"].append(f"{title}（{company}）：地点「{loc}」权重为0，跳过")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason=f"地点权重0({loc})", job_dict=job_dict, scored_result=scored)
                continue
            if weight != 1.0:
                adjusted = max(1, round(score * weight))
                logger.info("  地点「%s」权重 %.2f → 分数 %d→%d", loc, weight, score, adjusted)
                score = adjusted

        # ── 公司规模 + 融资阶段权重（从 company_info 解析）──────────────────
        if (company_size_weights or financing_weights) and score > 0:
            ci = job_dict.get("company_info") or ""
            cs, fin = _parse_company_info(ci)
            cs_weight = (company_size_weights or {}).get(cs, (company_size_weights or {}).get("default", 1.0))
            fin_weight = (financing_weights or {}).get(fin, (financing_weights or {}).get("default", 1.0))
            combined = cs_weight * fin_weight
            if combined != 1.0:
                adjusted = max(1, round(score * combined))
                logger.info(
                    "  规模「%s」×融资「%s」= %.2f×%.2f=%.3f → 分数 %d→%d",
                    cs or "未知", fin or "未知", cs_weight, fin_weight, combined, score, adjusted,
                )
                score = adjusted

        # ── 互联网大厂权重 ─────────────────────────────────────────────────────
        if company_tier_weights and score > 0:
            tier_w = 1.0
            tier_match = ""
            for pattern, w in company_tier_weights.items():
                if pattern == "default":
                    continue
                if pattern.lower() in company.lower():
                    if float(w) > tier_w:
                        tier_w = float(w)
                        tier_match = pattern
            if tier_w == 1.0:
                tier_w = float(company_tier_weights.get("default", 1.0))
            if tier_w != 1.0:
                adjusted = max(1, round(score * tier_w))
                logger.info("  大厂加权「%s」×%.2f → 分数 %d→%d", tier_match, tier_w, score, adjusted)
                score = adjusted

        # ── 发布时间权重 ──────────────────────────────────────────────────────
        if recency_weights and score > 0:
            pub_days = _extract_publish_days(job_dict)
            r_weight = _get_recency_weight(pub_days, recency_weights)
            if r_weight != 1.0:
                adjusted = max(1, round(score * r_weight))
                days_str = f"{pub_days:.1f}天前" if pub_days is not None else "未知"
                logger.info("  发布时间「%s」权重 %.2f → 分数 %d→%d", days_str, r_weight, score, adjusted)
                score = adjusted

        # ── HR 活跃度权重 ──────────────────────────────────────────────────────
        if hr_active_weights and score > 0:
            active_days = _extract_hr_active_days(job_dict.get("hr_active", ""))
            a_weight = _get_hr_active_weight(active_days, hr_active_weights)
            if a_weight != 1.0:
                adjusted = max(1, round(score * a_weight))
                days_str = f"{active_days:.0f}天前活跃" if active_days is not None else "未知"
                logger.info("  HR活跃度「%s」权重 %.2f → 分数 %d→%d", days_str, a_weight, score, adjusted)
                score = adjusted

        # ── 2026-10-15 「高分空 greeting」补救 ──
        # 现象：score_job 偶发只输出 match_score / top_matches / mismatch_concerns，
        # 漏掉 greeting 字段。AI产品经理 120 跑约 12% 卡 8-9 分被「or not greeting」吃掉。
        # 修复：score >= threshold 但 greeting 为空时调一次简短 LLM 补生成（_regenerate_greeting）。
        if not greeting and score >= threshold:
            logger.info("  [空-greeting 补救] 分数 %d ≥ %d 但 greeting 为空，调 LLM 补生成",
                        score, threshold)
            greeting = _regenerate_greeting(client, fallback, scored, job_dict)

        # ── 2026-10-13 排查埋点：score_only 模式是否把高分卡写到 skipped ──
        if score_only:
            logger.info("  [score_only] 跳过发送：%s（%s）score=%d", title, company, score)
        if score < threshold or not greeting:
            if score < threshold:
                logger.info("  → 跳过（%d < %d）", score, threshold)
                skip_msg = f"{title}（{company}）：{score}/10 低于阈值"
                skip_reason = f"低分({score}<{threshold})"
            else:
                # score >= threshold 但 greeting 仍空（LLM 补救 + 模板降级全失败）
                logger.info("  → 跳过（分数 %d 达标但 greeting 仍空，LLM+模板降级均失败）", score)
                skip_msg = f"{title}（{company}）：{score}/10 达标但 greeting 补生成失败"
                skip_reason = f"greeting补生成失败({score}分)"
            result["skipped"].append(skip_msg)
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason=skip_reason, job_dict=job_dict, scored_result=scored)
            continue

        logger.info("  ✓ 达标！打招呼：%s…", greeting[:60])

        if score_only:
            result["skipped"].append(
                f"{title}（{company}）：{score}/10 ✓ [score_only]\n    {greeting}"
            )
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason="score_only", job_dict=job_dict, scored_result=scored)
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
                                  skip_reason=f"弹窗跳过({dialog})", job_dict=job_dict, scored_result=scored)
                    continue

            if not skill.tap_element("chat_btn"):
                # ── 2026-10-13 排查埋点：聊天按钮 tap 失败（区分「导航失败但 detail 通过」vs「聊天页打开失败」） ──
                logger.warning("  ⚠️ chat_btn tap 失败：%s（%s）", title, company)
                result["errors"].append(f"{title}：无法打开聊天页")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="无聊天按钮", job_dict=job_dict, scored_result=scored)
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
                                  skip_reason=f"弹窗跳过({_pre_chat_dialog})", job_dict=job_dict, scored_result=scored)
                    continue
                time.sleep(0.5)  # 等弹窗关闭动画

            # ── 2026-10-13 排查埋点：聊天页 editText 4s 超时 ──
            if not skill.wait_for_element(
                "com.hpbr.bosszhipin:id/editText_with_scrollbar", timeout=4.0
            ):
                logger.warning("  ⚠️ 聊天页 editText 4s 超时：%s（%s）", title, company)
                result["errors"].append(f"{title}：聊天页加载超时")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="聊天页超时", job_dict=job_dict, scored_result=scored)
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
            if _chat_title_match and _chat_company_match:
                # title + company 匹配即可发送；HR 仅作 warning 不阻断
                # 原因：HR 名字在不同页（搜索列表 vs 聊天页）可能显示差异
                # （如「某总」→「某女士」），过度严格会让本来正确的发送被跳过。
                # 真正致命的"点 A 跳 B"仍会被 title 或 company 不匹配捕获。
                _chat_ok = True
                if not _chat_hr_match and _target_hr_surname:
                    logger.warning(
                        "  聊天页 HR 不匹配（仅 warning，不阻断）— 目标 hr=%s，"
                        "实际 chat_hr=%s。title+company 已匹配。",
                        effective_hr_name, _chat_title_text,
                    )
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
                              skip_reason="聊天页HR/职位/公司不匹配", job_dict=job_dict, scored_result=scored)
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
                                  skip_reason=f"弹窗跳过({dialog2})", job_dict=job_dict, scored_result=scored)
                    continue

            # 最终兜底：招呼开头不允许「总好」「HR好」「老师好」
            import re as _re_final
            if _re_final.match(r"^(总好|HR好|老师好)", greeting):
                logger.error("  greeting 仍含禁用开头，跳过发送：%r", greeting[:20])
                result["errors"].append(f"{title}：greeting 含禁用开头「{greeting[:6]}」")
                _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                              skip_reason="greeting禁用开头", job_dict=job_dict, scored_result=scored)
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
                    {"job": title, "company": company, "score": score, "action": action, "greeting": greeting, "keyword": keyword}
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
                              greeted=True, greeting_text=greeting,
                              job_dict=job_dict, scored_result=scored)
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
                              skip_reason="发送失败", job_dict=job_dict, scored_result=scored)
            time.sleep(1.0)

        except Exception as exc:
            logger.error("处理 '%s' 异常", title, exc_info=True)
            result["errors"].append(f"{title}: {exc}")
            _record_visit(db_conn, title, company, effective_hr_name, keyword, score,
                          skip_reason=f"异常:{exc}",
                          job_dict=locals().get("job_dict"),
                          scored_result=locals().get("scored"))
            if not skill._is_screen_on():
                break

    # ── 本次评分分布摘要 ──────────────────────────────────────────────────────
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
    _profile_threshold = profile.get("default_threshold", 6)
    _profile_min_salary = profile.get("min_salary_k", 0)
    location_weights: dict = profile.get("location_weights", {})
    description_filters: dict = profile.get("description_filters", {})
    company_size_weights: dict = profile.get("company_size_weights", {})
    recency_weights: dict = profile.get("recency_weights", {})
    financing_weights: dict = profile.get("financing_weights", {})
    scoring_dimensions: dict = profile.get("scoring_dimensions", {})
    company_tier_weights: dict = profile.get("company_tier_weights", {})
    hr_active_weights: dict = profile.get("hr_active_weights", {})
    big_tech_whitelist: dict = profile.get("big_tech_whitelist", {}) or {}
    daily_hard_limit: int = int(profile.get("daily_hard_limit", 0))

    parser = argparse.ArgumentParser(description="Boss直聘实时评分 + 个性化打招呼（单阶段）")
    parser.add_argument("--keyword", required=True, help="搜索关键词（必需）")
    parser.add_argument(
        "--resume",
        default=str(RESUME_DEFAULT),
        help=f"简历 Markdown 路径（默认：{RESUME_DEFAULT}）",
    )
    parser.add_argument("--threshold", type=int, default=_profile_threshold,
                        help=f"最低匹配分数 0-10（默认来自 candidate_profile.yaml：{_profile_threshold}）")
    parser.add_argument("--max-greet", type=int, default=5, help="最多发送打招呼数（默认 5）")
    parser.add_argument("--score-only", action="store_true", help="仅评分打印，不发送打招呼")
    parser.add_argument("--strict", action="store_true", help="严格评分模式，减少分数虚高")
    parser.add_argument("--min-salary", type=float, default=_profile_min_salary,
                        help=f"月薪下限（K），低于此值跳过（默认来自 candidate_profile.yaml：{_profile_min_salary}K）")
    parser.add_argument("--no-verify", action="store_true", help="发送后不验证消息已发出")
    parser.add_argument(
        "--only-big-tech", action="store_true",
        help="只对大厂白名单（candidate_profile.yaml big_tech_whitelist 段）发招呼；非白名单直接 skip",
    )
    parser.add_argument("--device", default=None, help="ADB 设备 serial")
    parser.add_argument(
        "--no-adb-retry", action="store_true",
        help="禁用 ADB 断连自动重联（默认开）",
    )
    parser.add_argument(
        "--adb-reconnect-ip", default="10.32.7.105:5555",
        help="ADB 自动重连目标 IP:端口（默认 10.32.7.105:5555）",
    )
    parser.add_argument(
        "--adb-retry-max", type=int, default=3,
        help="ADB 断连时最大自动重试次数（默认 3）",
    )
    parser.add_argument(
        "--max-list-scrolls", type=int, default=12,
        help="列表无新卡时最大连滑次数（默认 12，原硬编码 5）",
    )
    parser.add_argument(
        "--scroll-px", type=int, default=500,
        help="单次下滑距离 px（默认 500，原硬编码 320）",
    )
    parser.add_argument(
        "--scroll-wait-secs", type=float, default=2.5,
        help="滑后等待懒加载秒数（默认 2.5，原硬编码 1.5）",
    )
    parser.add_argument(
        "--strong-scroll-px", type=int, default=1100,
        help="强滑距离 px（连续无新卡 N 次后启用，默认 1100）",
    )
    parser.add_argument(
        "--strong-scroll-wait-secs", type=float, default=4.0,
        help="强滑后等待懒加载秒数（默认 4.0）",
    )
    parser.add_argument(
        "--scroll-after-n-misses", type=int, default=3,
        help="连续无新卡 N 次后切换强滑（默认 3）",
    )
    args = parser.parse_args()

    # ── 读取简历 ────────────────────────────────────────────────────────────────
    resume_path = Path(args.resume)
    if not resume_path.exists():
        print(f"错误：简历文件不存在：{resume_path}")
        return 1
    resume_text = resume_path.read_text(encoding="utf-8")
    print(f"✓ 简历已加载：{resume_path.name}（{len(resume_text)} 字符）")

    result: dict | None = None
    try:
        with device_lock(script_name=f"smart_match_greet:{args.keyword}"):
            client, fallback = _make_client()

            # ── 初始化 DB（确保表存在）──────────────────────────────────────────
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            db_conn = sqlite3.connect(DB_PATH)
            _ensure_greetings_table(db_conn)

            mode_label = "严格模式" if args.strict else "标准模式"
            score_label = "仅评分" if args.score_only else f"最多发送 {args.max_greet} 条"
            print(
                f"\n🤖 单阶段循环（关键词：{args.keyword}，阈值：{args.threshold}，{mode_label}，{score_label}）"
            )

            result = live_greet_loop(
                resume=resume_text,
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
                location_weights=location_weights,
                description_filters=description_filters,
                fallback=fallback,
                company_size_weights=company_size_weights,
                recency_weights=recency_weights,
                financing_weights=financing_weights,
                scoring_dimensions=scoring_dimensions,
                company_tier_weights=company_tier_weights,
                hr_active_weights=hr_active_weights,
                big_tech_whitelist=big_tech_whitelist,
                only_big_tech=args.only_big_tech,
                daily_hard_limit=daily_hard_limit,
                max_list_scrolls=args.max_list_scrolls,
                scroll_px=args.scroll_px,
                scroll_wait_secs=args.scroll_wait_secs,
                strong_scroll_px=args.strong_scroll_px,
                strong_scroll_wait_secs=args.strong_scroll_wait_secs,
                scroll_after_n_misses=args.scroll_after_n_misses,
            )
            db_conn.close()

            # ── 结果摘要 ────────────────────────────────────────────────────────
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

            return (0 if not result["errors"] else 1), result
    except DeviceBusyError as exc:
        print(f"⚠ 设备已被占用，跳过本次运行：{exc}")
        return 0, None


# ─── ADB 断连自动重联（外层 wrapper）───────────────────────────────────────────

_ADB_ERROR_MARKERS = (
    "ADB 设备未连接",       # P0 早筛抛出
    "ADB 设备已断开连接",   # P1 早筛抛出
)


def _is_adb_disconnect_error(errors) -> bool:
    """判断 result['errors'] 是否含 ADB 断连信号。"""
    return any(
        any(marker in (err or "") for marker in _ADB_ERROR_MARKERS)
        for err in (errors or [])
    )


def _adb_reconnect(ip: str, timeout: float = 15.0) -> bool:
    """Best-effort: ``adb disconnect`` + ``adb connect <ip>`` + 验证。

    Returns:
        True 当 ``adb devices`` 出现 ``\\tdevice`` 状态行；False 任何步骤失败。
    """
    try:
        subprocess.run(["adb", "disconnect"], capture_output=True, timeout=timeout)
    except Exception as e:
        print(f"  adb disconnect 异常：{e}")

    time.sleep(1)
    try:
        ret = subprocess.run(
            ["adb", "connect", ip], capture_output=True, text=True, timeout=timeout
        )
    except Exception as e:
        print(f"  adb connect {ip} 异常：{e}")
        return False
    out = (ret.stdout or "").strip()
    if "connected" not in out and "already" not in out:
        print(f"  adb connect 失败：{out!r}")
        return False

    time.sleep(2)
    try:
        chk = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=timeout
        )
    except Exception as e:
        print(f"  adb devices 异常：{e}")
        return False
    if "\tdevice" not in chk.stdout:
        print(f"  adb devices 验证失败：{chk.stdout.strip()!r}")
        return False
    return True


def _main_with_adb_recovery() -> int:
    """外层 wrapper：检测 ADB 断连 → 自动 reconnect + 重跑 main()。

    设计：P0/P1 早筛（见 smart_match_greet.py:1104-1142）让脚本在 ADB 断开时
    0.5s 内返回 errors=[...]。本函数检测该错误标记，调用 ``adb disconnect``
    + ``adb connect <ip>`` 重连后再次调用 main()。已发的 job 靠
    ``scenarios/boss/output/requirements.db`` greetings 表跨次运行自动去重。

    设计取舍：
    - 只对明确的 ADB 错误标记触发重联，不对其他错误（如 LLM 限流、PIN 错、
      DAILY_LIMIT）误重试。
    - 最多 --adb-retry-max 次（默认 3），避免无线 ADB 长时间不可用时
      无限重试浪费 LLM 配额。
    - ``--no-adb-retry`` 完全禁用，保留逃生口。
    """
    # 预解析（不与 main() 内部 argparse 冲突）—— 只关心 3 个 retry 相关参数
    _pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    _pre.add_argument("--no-adb-retry", action="store_true")
    _pre.add_argument("--adb-reconnect-ip", default="10.32.7.105:5555")
    _pre.add_argument("--adb-retry-max", type=int, default=3)
    pre_args, _unknown = _pre.parse_known_args()

    if pre_args.no_adb_retry:
        rc, _ = main()
        return rc

    max_attempts = max(1, int(pre_args.adb_retry_max))
    ip = pre_args.adb_reconnect_ip
    last_rc = 1
    last_result: dict | None = None

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(
                f"\n⚠️ Attempt {attempt}/{max_attempts}：自动重联 ADB {ip}…"
            )
            time.sleep(3)
            if not _adb_reconnect(ip):
                print("  ❌ 重联失败，继续下一轮…")
                continue
            print("  ✓ ADB 重联成功，重跑 main()（已发 job 靠 DB 自动去重）")

        rc, result = main()
        last_rc = rc
        last_result = result

        if not _is_adb_disconnect_error((result or {}).get("errors", [])):
            return rc
        if attempt >= max_attempts:
            print(
                f"\n❌ ADB 断连 {max_attempts} 次后仍失败，放弃（最后一次错误："
                f"{(result or {}).get('errors', [])[:1]})"
            )
            break
        print(
            f"\n⚠️ 检测到 ADB 断连（attempt {attempt}/{max_attempts}），"
            f"准备自动重联…"
        )

    return last_rc


if __name__ == "__main__":
    sys.exit(_main_with_adb_recovery())
