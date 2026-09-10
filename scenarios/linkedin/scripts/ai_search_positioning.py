#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinkedIn AI 搜索定位分析脚本

用法:
  python scenarios/linkedin/scripts/ai_search_positioning.py
  python scenarios/linkedin/scripts/ai_search_positioning.py --config scenarios/linkedin/config/positioning.yaml
  python scenarios/linkedin/scripts/ai_search_positioning.py --device 127.0.0.1:5037 --dry-run

流程:
  1. 读取配置文件中的搜索查询
  2. 对每个查询使用 deep link 打开 LinkedIn 搜索
  3. 分 result_type (all/people/companies/content) 滚动收集结果
  4. 将结果存入 SQLite (positioning.db)
  5. 调用 Claude Haiku 进行定位分析
  6. 生成 Markdown 报告
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT  = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / "scenarios" / "linkedin" / "output"
DB_PATH    = OUTPUT_DIR / "positioning.db"
CONFIG_PATH = REPO_ROOT / "scenarios" / "linkedin" / "config" / "positioning.yaml"

# ─── Prompts ──────────────────────────────────────────────────────────────────

POSITIONING_PROMPT = """You are a career positioning expert. Analyze the LinkedIn search results below and give an AI-powered market positioning report for the candidate.

Candidate resume summary:
{resume_summary}

LinkedIn search results collected (query: "{query}", result types: {result_types}):
{results_json}

Please analyze and return a JSON object with the following structure:
{{
  "market_fit_score": <0-100, overall market fit>,
  "matched_job_titles": [
    {{"title": "...", "frequency": <count>, "match_level": "high/medium/low"}}
  ],
  "target_companies": [
    {{"name": "...", "industry": "...", "why_relevant": "..."}}
  ],
  "key_skills_in_demand": [
    {{"skill": "...", "frequency": <count>, "candidate_has": true/false}}
  ],
  "seniority_match": {{
    "current_level": "...",
    "market_demand_level": "...",
    "gap": "..."
  }},
  "peer_landscape": "A 2-3 sentence description of similar professionals in the market",
  "positioning_gaps": ["gap 1", "gap 2"],
  "positioning_strengths": ["strength 1", "strength 2"],
  "resume_keyword_suggestions": ["keyword 1", "keyword 2"],
  "summary": "A 3-5 sentence executive summary of the market positioning analysis"
}}

Output JSON only, no other text."""


# ─── Database ─────────────────────────────────────────────────────────────────

CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS search_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT    NOT NULL,
    result_type TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    result_count INTEGER DEFAULT 0
);
"""

CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES search_sessions(id),
    category    TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    subtitle    TEXT    DEFAULT '',
    detail      TEXT    DEFAULT '',
    meta_json   TEXT    DEFAULT '{}',
    dedup_key   TEXT    NOT NULL,
    UNIQUE(dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_results_session ON results(session_id);
CREATE INDEX IF NOT EXISTS idx_results_category ON results(category);
"""

CREATE_ANALYSES = """
CREATE TABLE IF NOT EXISTS analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    result_json TEXT    NOT NULL,
    report_path TEXT    DEFAULT ''
);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(CREATE_SESSIONS)
    conn.executescript(CREATE_RESULTS)
    conn.executescript(CREATE_ANALYSES)
    conn.commit()
    return conn


def save_results(conn: sqlite3.Connection, session_id: int, results: list) -> int:
    """Save search results to DB, return count of newly inserted rows."""
    saved = 0
    for r in results:
        dedup_key = f"{r.category}:{r.title}:{r.subtitle}"[:200]
        try:
            conn.execute(
                "INSERT OR IGNORE INTO results "
                "(session_id, category, title, subtitle, detail, meta_json, dedup_key) "
                "VALUES (?,?,?,?,?,?,?)",
                (session_id, r.category, r.title, r.subtitle,
                 r.detail, json.dumps(r.meta, ensure_ascii=False), dedup_key),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                saved += 1
        except sqlite3.Error as e:
            log.warning("[DB] 保存结果失败: %s", e)
    conn.commit()
    return saved


def load_results_for_analysis(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Load all collected results for AI analysis.

    Results are stored per sub-query (e.g. "AI产品经理") not per parent query
    (e.g. "FDE产品经理 AI Agent..."), so we load everything and let the AI
    analyze the full picture across all result types.
    """
    rows = conn.execute(
        "SELECT r.category, r.title, r.subtitle, r.detail, r.meta_json "
        "FROM results r",
    ).fetchall()
    return [
        {
            "category": row[0], "title": row[1],
            "subtitle": row[2], "detail": row[3],
            "meta": json.loads(row[4] or "{}"),
        }
        for row in rows
    ]


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


def get_resume_summary(cfg: dict) -> str:
    """Read the resume file if specified, else use profile_description from config."""
    resume_path = cfg.get("resume_path", "")
    if resume_path:
        rp = Path(resume_path.replace("\\", "/"))
        if rp.exists():
            text = rp.read_text(encoding="utf-8")
            # Return first 2000 chars (enough context for AI)
            return text[:2000]
    return cfg.get("profile_description", "")


# ─── Device connection ─────────────────────────────────────────────────────────

def get_skill(device_id: str, action_delay: float):
    """Initialize LinkedInAutomationSkill with ADB connection."""
    import sys
    sys.path.insert(0, str(REPO_ROOT))

    from skills.android.adb_runner import ADBRunner
    from skills.linkedin.linkedin_automation_skill import LinkedInAutomationSkill

    adb = ADBRunner()
    skill = LinkedInAutomationSkill(
        adb_manager=adb,
        output_dir=str(OUTPUT_DIR),
        device_id=device_id,
        action_delay=action_delay,
    )
    return skill


# ─── Collection ───────────────────────────────────────────────────────────────

RESULT_TYPE_ORDER = ["all", "people", "companies", "content"]


def collect_for_query(
    skill,
    conn: sqlite3.Connection,
    query: str,
    queries_by_type: dict,
    max_results: int,
    dry_run: bool,
    dump_xml: bool = False,
) -> int:
    """Run search for one query string across all applicable result types."""
    total_saved = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for result_type in RESULT_TYPE_ORDER:
        type_queries = queries_by_type.get(result_type, [])
        if not type_queries:
            continue

        for q in type_queries:
            log.info("── 搜索 [%s] query='%s'", result_type, q)

            if dry_run:
                log.info("  [dry-run] 跳过实际设备操作")
                continue

            # Navigate via deep link
            ok = skill.search_via_deeplink(q, result_type=result_type)
            if not ok:
                log.warning("  deep link 失败，跳过")
                continue

            # Debug: dump raw XML so we can inspect parser patterns
            if dump_xml:
                xml = skill.get_ui_hierarchy(force_refresh=True)
                safe_q = re.sub(r"[^\w一-鿿]+", "_", q)[:20]
                dump_path = OUTPUT_DIR / f"debug_{result_type}_{safe_q}.xml"
                dump_path.write_text(xml, encoding="utf-8")
                log.info("  [dump] %s", dump_path)

            # Collect results with scrolling
            results = skill.collect_search_results_with_scroll(
                max_results=max_results, max_scrolls=6
            )
            log.info("  收集到 %d 条结果", len(results))

            if not results:
                continue

            # Save session
            cur = conn.execute(
                "INSERT INTO search_sessions (query, result_type, timestamp, result_count) "
                "VALUES (?,?,?,?)",
                (q, result_type, now, len(results)),
            )
            conn.commit()
            session_id = cur.lastrowid

            saved = save_results(conn, session_id, results)
            total_saved += saved
            log.info("  新增 %d 条入库 (共 %d 条去重结果)", saved, len(results))

            # Anti-detection: pause between queries
            if result_type != RESULT_TYPE_ORDER[-1]:
                time.sleep(skill.action_delay + 2.0)

    return total_saved


# ─── AI Analysis ──────────────────────────────────────────────────────────────

def analyze_positioning(
    conn: sqlite3.Connection,
    query: str,
    resume_summary: str,
) -> dict:
    """Send collected results to Claude Haiku and get positioning analysis."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN not set")

    import anthropic

    results = load_results_for_analysis(conn, query)
    if not results:
        log.warning("[analyze] 无数据可分析 (query='%s')", query)
        return {}

    log.info("[analyze] 分析 %d 条结果 (query='%s')", len(results), query)

    # Summarize results: keep top 50 to stay within token budget
    result_types = list({r["category"] for r in results})
    results_sample = results[:50]
    results_json = json.dumps(results_sample, ensure_ascii=False, indent=2)

    prompt = POSITIONING_PROMPT.format(
        resume_summary=resume_summary[:1500],
        query=query,
        result_types=", ".join(result_types),
        results_json=results_json,
    )

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        log.warning("[analyze] AI 返回内容无法解析为 JSON")
        return {"raw_response": raw}

    try:
        analysis = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        log.warning("[analyze] JSON 解析失败: %s", e)
        return {"raw_response": raw}

    # Persist analysis
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO analyses (query, timestamp, result_json) VALUES (?,?,?)",
        (query, now, json.dumps(analysis, ensure_ascii=False)),
    )
    conn.commit()

    return analysis


# ─── Report generation ────────────────────────────────────────────────────────

def generate_report(
    conn: sqlite3.Connection,
    query: str,
    analysis: dict,
    resume_summary: str,
) -> Path:
    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    safe_query = re.sub(r"[^\w一-鿿]+", "_", query)[:30]
    report_path = OUTPUT_DIR / f"positioning_report_{safe_query}_{now_str}.md"

    results = load_results_for_analysis(conn, query)
    jobs    = [r for r in results if r["category"] == "job"]
    people  = [r for r in results if r["category"] == "person"]
    posts   = [r for r in results if r["category"] == "post"]

    fit_score = analysis.get("market_fit_score", "N/A")
    summary   = analysis.get("summary", "")

    lines = [
        f"# LinkedIn 市场定位分析报告",
        f"",
        f"**搜索关键词**: {query}  |  **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**综合匹配分**: {fit_score}/100  |  **分析样本**: {len(results)} 条结果",
        f"",
        f"---",
        f"",
        f"## 执行摘要",
        f"",
        summary,
        f"",
        f"---",
        f"",
        f"## 职位匹配 TOP 10",
        f"",
        f"| # | 职位名称 | 出现频次 | 匹配度 |",
        f"|---|---------|---------|-------|",
    ]

    for i, item in enumerate(analysis.get("matched_job_titles", [])[:10], 1):
        lines.append(
            f"| {i} | {item.get('title', '')} | {item.get('frequency', '')} "
            f"| {item.get('match_level', '')} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 目标公司",
        f"",
        f"| 公司 | 行业 | 为什么相关 |",
        f"|-----|------|----------|",
    ]
    for item in analysis.get("target_companies", []):
        lines.append(
            f"| {item.get('name', '')} | {item.get('industry', '')} "
            f"| {item.get('why_relevant', '')} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 市场高频技能",
        f"",
        f"| 技能 | 频次 | 候选人是否具备 |",
        f"|-----|------|-------------|",
    ]
    for item in analysis.get("key_skills_in_demand", []):
        has = "✓" if item.get("candidate_has") else "✗"
        lines.append(
            f"| {item.get('skill', '')} | {item.get('frequency', '')} | {has} |"
        )

    seniority = analysis.get("seniority_match", {})
    lines += [
        f"",
        f"---",
        f"",
        f"## 资历定位",
        f"",
        f"- **当前水平**: {seniority.get('current_level', '')}",
        f"- **市场需求水平**: {seniority.get('market_demand_level', '')}",
        f"- **差距**: {seniority.get('gap', '')}",
        f"",
        f"---",
        f"",
        f"## 人脉市场画像",
        f"",
        analysis.get("peer_landscape", ""),
        f"",
        f"---",
        f"",
        f"## 定位优势",
        f"",
    ]
    for s in analysis.get("positioning_strengths", []):
        lines.append(f"- {s}")

    lines += [
        f"",
        f"## 定位差距",
        f"",
    ]
    for g in analysis.get("positioning_gaps", []):
        lines.append(f"- {g}")

    lines += [
        f"",
        f"---",
        f"",
        f"## 简历关键词建议",
        f"",
    ]
    for kw in analysis.get("resume_keyword_suggestions", []):
        lines.append(f"- `{kw}`")

    lines += [
        f"",
        f"---",
        f"",
        f"## 搜索结果原始数据",
        f"",
        f"**职位 ({len(jobs)} 条)**",
        f"",
    ]
    for j in jobs[:15]:
        lines.append(f"- {j['title']} @ {j['subtitle']} | {j['detail']}")

    lines += [f"", f"**人脉 ({len(people)} 条)**", f""]
    for p in people[:10]:
        lines.append(f"- {p['title']}: {p['subtitle']}")

    lines += [f"", f"**帖子/动态 ({len(posts)} 条)**", f""]
    for po in posts[:5]:
        lines.append(f"- [{po['title']}] {po['subtitle']}")

    lines += ["", "---", "", f"*生成工具: pixelclaw ai-search-positioning*"]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("报告已生成: %s", report_path)

    # Update analyses table with report path (subquery needed — SQLite has no ORDER BY in UPDATE)
    conn.execute(
        "UPDATE analyses SET report_path=? WHERE id=("
        "SELECT id FROM analyses WHERE query=? ORDER BY id DESC LIMIT 1)",
        (str(report_path), query),
    )
    conn.commit()

    return report_path


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LinkedIn AI 搜索定位分析")
    parser.add_argument("--config",  default=str(CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--device",  default=None, help="ADB 设备 ID (默认自动检测)")
    parser.add_argument("--db",      default=str(DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--skip-collect", action="store_true", help="跳过数据收集, 直接分析已有数据")
    parser.add_argument("--skip-analyze", action="store_true", help="跳过 AI 分析, 只收集数据")
    parser.add_argument("--dry-run", action="store_true", help="不连接设备, 只打印计划")
    parser.add_argument("--dump-xml", action="store_true", help="每次搜索后 dump 原始 XML 到 output/ (用于调试解析器)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    action_delay = float(cfg.get("action_delay", 3.0))
    max_results  = int(cfg.get("max_results_per_query", 20))
    queries_by_type: dict = cfg.get("search_queries", {})
    resume_summary = get_resume_summary(cfg)

    conn = init_db(Path(args.db))

    # ── Phase 1: Collect ──────────────────────────────────────────────────────
    if not args.skip_collect:
        if args.dry_run:
            log.info("[dry-run] 配置加载成功，搜索计划:")
            for rt, qs in queries_by_type.items():
                for q in qs:
                    log.info("  [%s] %s", rt, q)
        else:
            skill = get_skill(args.device or "", action_delay)

        # Use "all" queries as the canonical query keys for analysis
        all_queries = queries_by_type.get("all", [])
        if not all_queries:
            # Fallback: flatten all queries
            all_queries = list({q for qs in queries_by_type.values() for q in qs})

        for q in all_queries:
            log.info("═══ 开始收集: %s ═══", q)
            if args.dry_run:
                continue
            total = collect_for_query(
                skill, conn, q, queries_by_type, max_results, dry_run=False,
                dump_xml=args.dump_xml,
            )
            log.info("═══ 完成: %s (%d 条新增) ═══", q, total)

    # ── Phase 2: Analyze ──────────────────────────────────────────────────────
    if not args.skip_analyze:
        # Analyze for each "all" query
        all_queries = queries_by_type.get("all", [])
        if not all_queries:
            all_queries = list({q for qs in queries_by_type.values() for q in qs})

        for q in all_queries:
            log.info("═══ 开始分析: %s ═══", q)
            try:
                analysis = analyze_positioning(conn, q, resume_summary)
                if analysis:
                    generate_report(conn, q, analysis, resume_summary)
            except Exception as e:
                log.error("分析失败 (%s): %s", q, e)

    conn.close()
    log.info("完成")


if __name__ == "__main__":
    main()
