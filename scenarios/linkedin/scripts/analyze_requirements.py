#!/usr/bin/env python3
"""
LinkedIn 职位需求分析引擎

从 linkedin_jobs.db 的 job_details 表中读取已爬取职位，
用 Claude Haiku 提取结构化任职要求，写入同一数据库的 jobs / requirements 表，
并生成 Markdown 分析报告。

用法：
  python scenarios/linkedin/scripts/analyze_requirements.py
  python scenarios/linkedin/scripts/analyze_requirements.py --keyword "Product Manager"
  python scenarios/linkedin/scripts/analyze_requirements.py --force   # 重新提取全部
"""

import argparse
import json
import logging
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT  = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / "scenarios" / "linkedin" / "output"
DB_PATH    = OUTPUT_DIR / "linkedin_jobs.db"

# ─── LLM Prompt ──────────────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are a recruitment requirements analysis expert. From the following job description, extract all "job requirements" (skills, experience, education, soft skills, bonus qualifications). Do NOT include job responsibilities.

Rules:
- tag should be a precise requirement phrase, close to the original text, 10-20 words max
- Each requirement appears only once (e.g., "Python" and "proficient Python programming" are the same)
- category must be exactly one of: Technical Skills / Product Skills / Industry Experience / Education / Soft Skills
- Bonus/preferred items: set is_bonus=true
- Output JSON array only, no other text

Job: {title} ({company})

Job Description:
{description}

Output format (JSON array):
[
  {{"tag": "RAG system development experience", "category": "Technical Skills", "is_bonus": false}},
  {{"tag": "LLM fine-tuning", "category": "Technical Skills", "is_bonus": true}}
]"""

# ─── Scoring (no salary for LinkedIn) ────────────────────────────────────────

# freq_score: based on how many jobs mention this requirement (normalized 0-1)
# quality_score: based on category relevance to AI PM role (subjective)
CATEGORY_WEIGHTS = {
    "Technical Skills":    1.0,
    "Product Skills":      1.0,
    "Industry Experience": 0.9,
    "Education":           0.7,
    "Soft Skills":         0.6,
}

# final_score = 0.55 * freq_score + 0.45 * quality_score
FREQ_WEIGHT    = 0.55
QUALITY_WEIGHT = 0.45


# ─── Database ────────────────────────────────────────────────────────────────

CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword      TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    company      TEXT,
    location     TEXT,
    description  TEXT,
    easy_apply   INTEGER DEFAULT 0,
    seniority    TEXT,
    employment   TEXT,
    applicants   TEXT,
    dedup_key    TEXT    NOT NULL UNIQUE,
    analyzed_at  TEXT    NOT NULL
);
"""

CREATE_REQUIREMENTS = """
CREATE TABLE IF NOT EXISTS requirements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INTEGER NOT NULL REFERENCES jobs(id),
    tag          TEXT    NOT NULL,
    category     TEXT,
    is_bonus     INTEGER DEFAULT 0,
    freq_score   REAL    DEFAULT 0,
    quality_score REAL   DEFAULT 0,
    final_score  REAL    DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_req_job ON requirements(job_id);
CREATE INDEX IF NOT EXISTS idx_req_tag ON requirements(tag);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(CREATE_JOBS)
    conn.executescript(CREATE_REQUIREMENTS)
    conn.commit()
    return conn


# ─── LLM extraction ──────────────────────────────────────────────────────────

def extract_requirements(title: str, company: str, description: str) -> list[dict]:
    import os
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN not set")

    client  = anthropic.Anthropic(api_key=api_key)
    prompt  = EXTRACT_PROMPT.format(title=title, company=company or "", description=description)
    resp    = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    return json.loads(m.group(0))


# ─── Scoring ─────────────────────────────────────────────────────────────────

def compute_scores(tag_counts: dict[str, int], tag_categories: dict[str, str], n_jobs: int) -> dict:
    """
    Returns {tag: {"freq": float, "quality": float, "final": float}}.
    freq_score  = count / n_jobs  (0-1)
    quality_score = CATEGORY_WEIGHTS[category] (0-1)
    final_score = FREQ_WEIGHT * freq + QUALITY_WEIGHT * quality
    """
    scores = {}
    for tag, count in tag_counts.items():
        freq    = count / n_jobs if n_jobs else 0
        cat     = tag_categories.get(tag, "")
        quality = CATEGORY_WEIGHTS.get(cat, 0.5)
        final   = FREQ_WEIGHT * freq + QUALITY_WEIGHT * quality
        scores[tag] = {"freq": round(freq, 4), "quality": round(quality, 4),
                       "final": round(final, 4)}
    return scores


# ─── Core analysis ───────────────────────────────────────────────────────────

def analyze_keyword(conn: sqlite3.Connection, keyword: str, force: bool) -> dict:
    log.info("分析关键词：%s", keyword)

    # Fetch unanalyzed job_details rows
    if force:
        rows = conn.execute(
            "SELECT title, company, location, description, easy_apply, "
            "seniority_level, employment_type, applicant_count, dedup_key "
            "FROM job_details WHERE keyword = ?", (keyword,)
        ).fetchall()
    else:
        already = {r[0] for r in conn.execute(
            "SELECT dedup_key FROM jobs WHERE keyword = ?", (keyword,)
        ).fetchall()}
        rows = conn.execute(
            "SELECT title, company, location, description, easy_apply, "
            "seniority_level, employment_type, applicant_count, dedup_key "
            "FROM job_details WHERE keyword = ?", (keyword,)
        ).fetchall()
        rows = [r for r in rows if r[-1] not in already]

    if not rows:
        log.info("  无新职位需要分析（%s）", keyword)
        return {}

    log.info("  待分析职位：%d 条", len(rows))

    # Per-job extraction
    now         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tag_counts: dict[str, int]    = defaultdict(int)
    tag_categories: dict[str, str] = {}
    tag_bonus: dict[str, bool]     = {}
    job_id_map: dict[str, int]     = {}
    reqs_cache: dict[str, list]    = {}

    for row in rows:
        title, company, location, desc, easy_apply, seniority, employment, applicants, dkey = row
        if not desc or len(desc.strip()) < 50:
            log.warning("  [%s] 描述过短，跳过 LLM 提取", title)
            continue

        try:
            reqs = extract_requirements(title, company, desc)
        except Exception as e:
            log.warning("  [%s] LLM 提取失败：%s", title, e)
            reqs = []

        # Insert into jobs table
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO jobs "
                "(keyword, title, company, location, description, easy_apply, "
                " seniority, employment, applicants, dedup_key, analyzed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (keyword, title, company, location, desc, easy_apply or 0,
                 seniority, employment, applicants, dkey, now),
            )
            conn.commit()
            job_id = cur.lastrowid or conn.execute(
                "SELECT id FROM jobs WHERE dedup_key = ?", (dkey,)
            ).fetchone()[0]
        except sqlite3.Error as e:
            log.warning("  [%s] DB insert 失败：%s", title, e)
            continue

        job_id_map[dkey] = job_id
        reqs_cache[dkey] = reqs

        for req in reqs:
            tag  = (req.get("tag") or "").strip()
            cat  = req.get("category", "")
            bonu = bool(req.get("is_bonus", False))
            if not tag:
                continue
            tag_counts[tag] += 1
            tag_categories.setdefault(tag, cat)
            tag_bonus[tag] = bonu

    n_jobs = len(job_id_map)
    if n_jobs == 0:
        return {}

    scores = compute_scores(tag_counts, tag_categories, n_jobs)

    # Write requirements with computed scores (reuse cached LLM results)
    for dkey, job_id in job_id_map.items():
        conn.execute("DELETE FROM requirements WHERE job_id = ?", (job_id,))
        for req in reqs_cache.get(dkey, []):
            tag  = (req.get("tag") or "").strip()
            cat  = req.get("category", "")
            bonu = 1 if req.get("is_bonus") else 0
            if not tag:
                continue
            s = scores.get(tag, {})
            conn.execute(
                "INSERT INTO requirements (job_id, tag, category, is_bonus, "
                "freq_score, quality_score, final_score) VALUES (?,?,?,?,?,?,?)",
                (job_id, tag, cat, bonu,
                 s.get("freq", 0), s.get("quality", 0), s.get("final", 0)),
            )
    conn.commit()

    return {"keyword": keyword, "n_jobs": n_jobs, "scores": scores,
            "tag_counts": dict(tag_counts)}


# ─── Markdown report ─────────────────────────────────────────────────────────

def generate_report(conn: sqlite3.Connection, keyword: str, analysis: dict) -> Path:
    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUTPUT_DIR / f"requirements_analysis_{keyword.replace(' ','_')}_{now_str}.md"

    n_jobs  = analysis.get("n_jobs", 0)
    scores  = analysis.get("scores", {})
    counts  = analysis.get("tag_counts", {})

    # Sort by final_score desc
    ranked = sorted(scores.items(), key=lambda x: x[1]["final"], reverse=True)

    # Fetch job list
    jobs = conn.execute(
        "SELECT title, company, location, seniority, easy_apply "
        "FROM jobs WHERE keyword = ? ORDER BY id DESC LIMIT 30",
        (keyword,)
    ).fetchall()

    lines = [
        f"# LinkedIn 职位需求分析报告",
        f"",
        f"**关键词**: {keyword}  |  **分析时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**分析职位数**: {n_jobs}  |  **提取需求 tag 数**: {len(ranked)}",
        f"",
        f"---",
        f"",
        f"## 高频需求 TOP 30",
        f"",
        f"| # | 需求 Tag | 类别 | 频次 | 频率 | 质量分 | **综合分** |",
        f"|---|---------|------|-----|------|--------|-----------|",
    ]
    for i, (tag, s) in enumerate(ranked[:30], 1):
        cat   = tag_categories_from_scores(conn, tag, keyword)
        count = counts.get(tag, 0)
        lines.append(
            f"| {i} | {tag} | {cat} | {count} | {s['freq']:.2f} "
            f"| {s['quality']:.2f} | **{s['final']:.3f}** |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 职位列表（最近 30 条）",
        f"",
        f"| 职位 | 公司 | 地点 | 级别 | Easy Apply |",
        f"|-----|------|------|------|-----------|",
    ]
    for title, company, location, seniority, easy in jobs:
        ea = "✓" if easy else ""
        lines.append(f"| {title} | {company or ''} | {location or ''} | {seniority or ''} | {ea} |")

    lines += ["", "---", "", f"*生成工具: pixelclaw analyze-linkedin-jobs*"]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("报告已生成：%s", report_path)
    return report_path


def tag_categories_from_scores(conn: sqlite3.Connection, tag: str, keyword: str) -> str:
    row = conn.execute(
        "SELECT r.category FROM requirements r JOIN jobs j ON r.job_id = j.id "
        "WHERE j.keyword = ? AND r.tag = ? LIMIT 1",
        (keyword, tag),
    ).fetchone()
    return row[0] if row else ""


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LinkedIn 职位需求分析引擎")
    parser.add_argument("--keyword", default=None,     help="指定关键词（默认分析全部）")
    parser.add_argument("--force",   action="store_true", help="重新提取所有记录（含已分析）")
    parser.add_argument("--db",      default=str(DB_PATH), help="SQLite 数据库路径")
    args = parser.parse_args()

    conn = init_db(Path(args.db))

    if args.keyword:
        keywords = [args.keyword]
    else:
        rows = conn.execute(
            "SELECT DISTINCT keyword FROM job_details"
        ).fetchall()
        keywords = [r[0] for r in rows]

    if not keywords:
        log.warning("数据库中无数据，请先运行 scrape_job_details.py")
        return

    for kw in keywords:
        analysis = analyze_keyword(conn, kw, force=args.force)
        if analysis:
            generate_report(conn, kw, analysis)

    conn.close()
    log.info("分析完成")


if __name__ == "__main__":
    main()
