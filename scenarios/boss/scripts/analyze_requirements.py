#!/usr/bin/env python3
"""
analyze_requirements.py - 职位需求分析引擎

从 BOSS直聘爬取的 JSON 文件中提取、去重、统计并评分各职位需求。
输出 SQLite 数据库（增量，可复用）+ Markdown 分析报告。

用法：
  python scenarios/boss/scripts/analyze_requirements.py
  python scenarios/boss/scripts/analyze_requirements.py --keyword "AI产品经理"
  python scenarios/boss/scripts/analyze_requirements.py --force   # 重新提取全部
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

# ─── 路径 ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / "scenarios" / "boss" / "output"
DB_PATH = OUTPUT_DIR / "requirements.db"

# ─── 公司质量评分表 ──────────────────────────────────────────────────────────
FUNDING_SCORES = {
    "上市公司": 5, "D轮及以上": 5, "C轮": 4, "B轮": 3,
    "A轮": 2, "天使轮": 1, "不需要融资": 3,
}
SIZE_SCORES = {
    "10000人以上": 3, "2000-9999人": 2, "1000-1999人": 2,
    "500-999人": 1, "100-499人": 0, "20-99人": -1, "0-20人": -2,
}

# ─── LLM Prompt ──────────────────────────────────────────────────────────────
EXTRACT_PROMPT = """你是招聘需求分析专家。从以下职位描述中，提取所有「任职要求」（含技能、经验、学历、软素质、加分项），不要包含工作职责。

规则：
- tag 为精确的需求短语，贴近原文，10-20字以内
- 同一需求只输出一次（例如"Python"和"熟练Python编程"属同一需求）
- category 必须是这五类之一：技术技能 / 产品能力 / 行业经验 / 学历要求 / 软素质
- 加分项/优先项设 is_bonus=true
- 仅输出 JSON 数组，不要其他文字

职位：{title}（{company}）

职位描述：
{description}

输出格式（JSON数组）：
[
  {{"tag": "RAG系统开发经验", "category": "技术技能", "is_bonus": false}},
  {{"tag": "大模型fine-tuning", "category": "技术技能", "is_bonus": true}}
]"""


# ─── 薪资解析 ────────────────────────────────────────────────────────────────

def parse_salary(raw: str) -> tuple[float | None, float | None]:
    """
    "25-50K·16薪" → (low_monthly_k, high_monthly_k)
    "20-40K·15薪" → 月薪等价 = (low+high)/2 * multiplier/12，但返回原始 low/high（月薪K）
    "20-30K"       → (20.0, 30.0)
    """
    if not raw:
        return None, None
    raw = raw.strip().upper()
    multiplier_match = re.search(r"·(\d+)薪", raw)
    multiplier = int(multiplier_match.group(1)) if multiplier_match else 12
    range_match = re.search(r"(\d+(?:\.\d+)?)[- ](\d+(?:\.\d+)?)K", raw)
    if not range_match:
        single_match = re.search(r"(\d+(?:\.\d+)?)K", raw)
        if single_match:
            v = float(single_match.group(1)) * multiplier / 12
            return v, v
        return None, None
    low = float(range_match.group(1)) * multiplier / 12
    high = float(range_match.group(2)) * multiplier / 12
    return low, high


# ─── 公司信息解析 ─────────────────────────────────────────────────────────────

def parse_company_info(info: str) -> tuple[str, str, float]:
    """
    "D轮及以上 • 10000人以上 • 互联网" → (funding, size, quality_score 0-8)
    """
    if not info:
        return "", "", 1.0
    parts = [p.strip() for p in info.replace("•", "•").split("•")]
    funding = next((p for p in parts if any(k in p for k in FUNDING_SCORES)), "")
    size = next((p for p in parts if "人" in p), "")
    f_score = next((v for k, v in FUNDING_SCORES.items() if k in funding), 1)
    s_score = next((v for k, v in SIZE_SCORES.items() if k in size), 0)
    return funding, size, float(max(0, f_score + s_score))


# ─── 数据库 ──────────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword             TEXT    NOT NULL,
        job_index           INTEGER,
        title               TEXT,
        company             TEXT,
        salary_raw          TEXT,
        salary_low_k        REAL,
        salary_high_k       REAL,
        company_info        TEXT,
        company_funding     TEXT,
        company_size        TEXT,
        company_quality     REAL,
        experience          TEXT,
        education           TEXT,
        source_file         TEXT,
        processed_at        TEXT
    );

    CREATE TABLE IF NOT EXISTS requirements (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id      INTEGER NOT NULL REFERENCES jobs(id),
        tag         TEXT    NOT NULL,
        category    TEXT    NOT NULL,
        is_bonus    INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_req_job ON requirements(job_id);
    CREATE INDEX IF NOT EXISTS idx_req_tag ON requirements(tag);
    """)
    conn.commit()
    # 向后兼容：为老版本 jobs 表追加 job_details_id 列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "job_details_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN job_details_id INTEGER REFERENCES job_details(id)")
        conn.commit()


def job_exists_by_detail_id(conn: sqlite3.Connection, job_details_id: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE job_details_id=?", (job_details_id,)
    ).fetchone()
    return row[0] if row else None


def job_exists(conn: sqlite3.Connection, source_file: str, job_index: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE source_file=? AND job_index=?",
        (source_file, job_index)
    ).fetchone()
    return row[0] if row else None


def insert_job(conn: sqlite3.Connection, keyword: str, job: dict, source_file: str, job_details_id: int | None = None) -> int:
    li = job.get("list_info", {}) or {}
    dt = job.get("detail", {}) or {}
    salary_raw = li.get("salary", "") or dt.get("salary", "") or ""
    if not salary_raw and dt.get("raw_texts"):
        salary_raw = next((t for t in dt["raw_texts"] if "K" in t.upper()), "")
    low_k, high_k = parse_salary(salary_raw)
    company_info = dt.get("company_info", "") or ""
    funding, size, quality = parse_company_info(company_info)
    cur = conn.execute(
        """INSERT INTO jobs
           (keyword, job_index, title, company, salary_raw, salary_low_k, salary_high_k,
            company_info, company_funding, company_size, company_quality,
            experience, education, source_file, processed_at, job_details_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (keyword, job.get("index"),
         dt.get("title") or li.get("title", ""),
         dt.get("company") or li.get("company", ""),
         salary_raw, low_k, high_k,
         company_info, funding, size, quality,
         dt.get("experience", ""), dt.get("education", ""),
         source_file,
         datetime.now(timezone.utc).isoformat(),
         job_details_id)
    )
    conn.commit()
    return cur.lastrowid


def insert_requirements(conn: sqlite3.Connection, job_id: int, reqs: list[dict]):
    conn.executemany(
        "INSERT INTO requirements (job_id, tag, category, is_bonus) VALUES (?,?,?,?)",
        [(job_id, r["tag"], r["category"], 1 if r.get("is_bonus") else 0) for r in reqs]
    )
    conn.commit()


# ─── LLM 提取 ────────────────────────────────────────────────────────────────

def extract_requirements_llm(title: str, company: str, description: str) -> list[dict]:
    try:
        import anthropic
    except ImportError:
        log.error("缺少 anthropic 包：pip install anthropic")
        return []
    client = anthropic.Anthropic()
    desc_truncated = description[:3000] if len(description) > 3000 else description
    prompt = EXTRACT_PROMPT.format(
        title=title, company=company, description=desc_truncated
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # 提取 JSON 数组（防止模型输出额外文字）
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            log.warning("LLM 返回非 JSON：%s", text[:200])
            return []
        reqs = json.loads(match.group())
        # 校验字段
        valid = []
        for r in reqs:
            if isinstance(r, dict) and r.get("tag") and r.get("category"):
                valid.append({
                    "tag": str(r["tag"])[:30],
                    "category": str(r["category"]),
                    "is_bonus": bool(r.get("is_bonus", False)),
                })
        return valid
    except Exception as e:
        log.warning("LLM 调用失败：%s", e)
        return []


# ─── 数据加载 ────────────────────────────────────────────────────────────────

def load_json_files(output_dir: Path, keyword: str | None) -> list[tuple[str, dict]]:
    """返回 [(source_file_stem, data), ...]"""
    pattern = f"job_details_{keyword}_*.json" if keyword else "job_details_*.json"
    files = sorted(output_dir.glob(pattern))
    # 也接受 merged_*.json
    if keyword:
        merged = output_dir / f"merged_{keyword}.json"
        if merged.exists() and merged not in files:
            files.append(merged)
    results = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                results.append((f.name, json.load(fp)))
        except Exception as e:
            log.warning("读取 %s 失败：%s", f.name, e)
    return results


def _detail_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='job_details'"
    ).fetchone()
    return row is not None


def load_from_db(conn: sqlite3.Connection, keyword: str | None) -> list[tuple[str, dict]]:
    """从 job_details 表读取，返回与 load_json_files 兼容的格式。"""
    if keyword:
        rows = conn.execute(
            "SELECT * FROM job_details WHERE keyword=? ORDER BY id", (keyword,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM job_details ORDER BY keyword, id").fetchall()

    col_names = [d[0] for d in conn.execute("PRAGMA table_info(job_details)").fetchall()]

    # 按 keyword 分组，模拟 load_json_files 的 (source_name, data) 格式
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        r = dict(zip(col_names, row))
        groups[r["keyword"]].append(r)

    results = []
    for kw, rlist in groups.items():
        jobs = []
        for r in rlist:
            entry = {
                "index": r["id"],
                "_job_details_id": r["id"],
                "list_info": {
                    "title": r.get("title", ""),
                    "company": r.get("company", ""),
                    "salary": r.get("salary_raw", ""),
                    "location": r.get("location", ""),
                    "hr_name": r.get("hr_name", ""),
                    "hr_title": r.get("hr_title", ""),
                    "hr_active": r.get("hr_active", ""),
                },
                "detail": {
                    "title": r.get("title", ""),
                    "company": r.get("company", ""),
                    "salary": r.get("salary_raw", ""),
                    "description": r.get("description", ""),
                    "skills": json.loads(r["skills_json"]) if r.get("skills_json") else [],
                    "benefits": json.loads(r["benefits_json"]) if r.get("benefits_json") else [],
                    "company_info": r.get("company_info", ""),
                    "raw_texts": json.loads(r["raw_texts_json"]) if r.get("raw_texts_json") else [],
                    "experience": "",
                    "education": "",
                },
            }
            jobs.append(entry)
        results.append((f"job_details_db:{kw}", {"keyword": kw, "jobs": jobs}))
    return results


# ─── 聚合与评分 ──────────────────────────────────────────────────────────────

def aggregate_stats(conn: sqlite3.Connection, keyword: str) -> tuple[int, list[dict]]:
    """返回 (total_jobs, [requirement_stat, ...])"""
    total = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE keyword=?", (keyword,)
    ).fetchone()[0]

    rows = conn.execute("""
        SELECT
            r.tag,
            r.category,
            r.is_bonus,
            COUNT(DISTINCT j.id)                        AS cnt,
            AVG(CASE WHEN j.salary_low_k IS NOT NULL THEN j.salary_low_k END)   AS avg_low,
            AVG(CASE WHEN j.salary_high_k IS NOT NULL THEN j.salary_high_k END) AS avg_high,
            AVG(j.company_quality)                      AS avg_quality,
            GROUP_CONCAT(DISTINCT j.company)            AS companies
        FROM requirements r
        JOIN jobs j ON r.job_id = j.id
        WHERE j.keyword = ?
        GROUP BY r.tag, r.is_bonus
        ORDER BY cnt DESC
    """, (keyword,)).fetchall()

    stats = []
    for row in rows:
        tag, cat, is_bonus, cnt, avg_low, avg_high, avg_q, companies = row
        freq_score = (cnt / total * 10) if total else 0
        # salary_score：月薪 10K→0, 50K→10，线性
        avg_salary_mid = ((avg_low or 0) + (avg_high or 0)) / 2
        salary_score = max(0.0, min(10.0, (avg_salary_mid - 10) / 40 * 10))
        quality_score = min(10.0, (avg_q or 0) / 8 * 10)
        final = 0.40 * freq_score + 0.30 * salary_score + 0.30 * quality_score
        if is_bonus:
            final *= 0.7
        # 代表公司（取前5）
        company_list = list(dict.fromkeys((companies or "").split(",")))[:5]
        stats.append({
            "tag": tag,
            "category": cat,
            "is_bonus": bool(is_bonus),
            "count": cnt,
            "freq_pct": f"{cnt/total*100:.0f}%" if total else "—",
            "avg_low": avg_low,
            "avg_high": avg_high,
            "avg_quality": avg_q or 0,
            "score": round(final, 1),
            "companies": "、".join(c for c in company_list if c),
        })
    return total, stats


# ─── Markdown 报告 ───────────────────────────────────────────────────────────

CATEGORY_ORDER = ["技术技能", "产品能力", "行业经验", "学历要求", "软素质"]


def format_salary(low, high) -> str:
    if low is None and high is None:
        return "—"
    if low is None or high is None:
        v = low or high
        return f"{v:.0f}K"
    if abs(low - high) < 0.5:
        return f"{low:.0f}K"
    return f"{low:.0f}~{high:.0f}K"


def render_table(items: list[dict]) -> str:
    if not items:
        return "_（无数据）_\n"
    lines = [
        "| 需求 | 频次 | 频率 | 月薪范围 | 公司质量 | 综合分 | 代表公司 |",
        "|------|:----:|:----:|:--------:|:--------:|:------:|--------|",
    ]
    for it in items:
        salary_str = format_salary(it["avg_low"], it["avg_high"])
        lines.append(
            f"| {it['tag']} | {it['count']} | {it['freq_pct']} "
            f"| {salary_str} | {it['avg_quality']:.1f}/8 "
            f"| **{it['score']}** | {it['companies']} |"
        )
    return "\n".join(lines) + "\n"


def generate_report(conn: sqlite3.Connection, keywords: list[str]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sections = [
        f"# 职位需求分析报告\n",
        f"> 生成时间：{now}\n",
    ]

    for kw in keywords:
        total, stats = aggregate_stats(conn, kw)
        sections.append(f"\n---\n\n## {kw}（共分析 {total} 个职位）\n")
        if not stats:
            sections.append("_暂无数据_\n")
            continue

        # 硬性要求按分类分组
        hard = [s for s in stats if not s["is_bonus"]]
        bonus = [s for s in stats if s["is_bonus"]]

        # 按 category 分组
        by_cat: dict[str, list] = defaultdict(list)
        for s in hard:
            by_cat[s["category"]].append(s)

        for cat in CATEGORY_ORDER:
            items = sorted(by_cat.get(cat, []), key=lambda x: -x["score"])
            if not items:
                continue
            sections.append(f"### {cat}\n")
            sections.append(render_table(items))

        # 其余未匹配的 category
        extra_cats = set(by_cat.keys()) - set(CATEGORY_ORDER)
        for cat in sorted(extra_cats):
            items = sorted(by_cat[cat], key=lambda x: -x["score"])
            sections.append(f"### {cat}\n")
            sections.append(render_table(items))

        if bonus:
            bonus_sorted = sorted(bonus, key=lambda x: -x["score"])
            sections.append("### 加分项（仅供参考，评分已打折）\n")
            sections.append(render_table(bonus_sorted))

    return "\n".join(sections)


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def run(output_dir: Path, keyword: str | None, force: bool):
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "requirements.db"
    conn = sqlite3.connect(db_path)
    init_db(conn)

    # 优先从 job_details 表读取；表不存在则回落到 JSON 文件
    if _detail_table_exists(conn):
        file_data = load_from_db(conn, keyword)
        source_label = "DB(job_details)"
    else:
        file_data = load_json_files(output_dir, keyword)
        source_label = "JSON文件"

    if not file_data:
        log.error("未找到任何数据（%s，目录：%s）", source_label, output_dir)
        return

    log.info("数据来源：%s", source_label)
    processed_jobs = 0
    skipped_jobs = 0
    all_keywords: set[str] = set()

    for source_file, data in file_data:
        kw = data.get("keyword", "unknown")
        all_keywords.add(kw)
        jobs = data.get("jobs", [])
        log.info("处理 %s：%d 条职位 (keyword=%s)", source_file, len(jobs), kw)

        for job in jobs:
            idx = job.get("index")
            detail_id = job.get("_job_details_id")
            detail = job.get("detail") or {}
            description = detail.get("description", "")
            if not description:
                skipped_jobs += 1
                continue

            # 增量去重：DB 来源用 job_details_id，JSON 来源用 source_file+index
            if detail_id is not None:
                existing_id = job_exists_by_detail_id(conn, detail_id)
            else:
                existing_id = job_exists(conn, source_file, idx)

            if existing_id and not force:
                skipped_jobs += 1
                continue

            if existing_id and force:
                conn.execute("DELETE FROM requirements WHERE job_id=?", (existing_id,))
                conn.execute("DELETE FROM jobs WHERE id=?", (existing_id,))
                conn.commit()

            job_id = insert_job(conn, kw, job, source_file, job_details_id=detail_id)
            li = job.get("list_info", {}) or {}
            title = detail.get("title") or li.get("title", "")
            company = detail.get("company") or li.get("company", "")
            log.info("  [%d] 提取需求：%s @ %s", idx or 0, title, company)
            reqs = extract_requirements_llm(title, company, description)
            if reqs:
                insert_requirements(conn, job_id, reqs)
            processed_jobs += 1

    log.info("完成：新处理 %d 条，跳过 %d 条", processed_jobs, skipped_jobs)

    # 按参数过滤关键词
    target_kws = [keyword] if keyword else sorted(all_keywords)
    # 若 DB 中有该 keyword 的数据也一并包含
    db_kws = [r[0] for r in conn.execute("SELECT DISTINCT keyword FROM jobs").fetchall()]
    if not keyword:
        target_kws = sorted(set(db_kws))

    report = generate_report(conn, target_kws)
    conn.close()

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    kw_suffix = f"_{keyword}" if keyword else ""
    report_path = output_dir / f"requirements_analysis{kw_suffix}_{date_str}.md"
    report_path.write_text(report, encoding="utf-8")
    log.info("报告已生成：%s", report_path)
    print(f"\n[OK] Analysis done. Report: {report_path.name}\n")


def main():
    parser = argparse.ArgumentParser(description="BOSS job requirements analysis")
    parser.add_argument("--keyword", "-k", help="filter by keyword")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="output directory")
    parser.add_argument("--force", action="store_true", help="re-extract all (ignore DB cache)")
    args = parser.parse_args()
    run(Path(args.output_dir), args.keyword, args.force)


if __name__ == "__main__":
    main()
