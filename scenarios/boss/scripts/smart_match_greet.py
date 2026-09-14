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
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

import anthropic
from skills.android.adb_runner import ADBRunner
from skills.boss import BOSSAutomationSkill, DialogType, PageState, normalize_card_title
from utils.logging_setup import setup_logger

# ─── 路径 ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parents[3]
DB_PATH = REPO_ROOT / "scenarios" / "boss" / "output" / "requirements.db"
RESUME_DEFAULT = Path("C:/Dev/projects/resume-renew/resume/current.md")

# ─── 弹窗分类 ─────────────────────────────────────────────────────────────────
_FATAL_DIALOGS = {DialogType.DAILY_LIMIT, DialogType.LOGIN_REQUIRED}
_SKIP_DIALOGS = {DialogType.JOB_OFFLINE}
_CONTINUE_DIALOGS = {DialogType.EXISTING_CHAT, DialogType.DISMISSED}

# ─── Haiku Prompt ─────────────────────────────────────────────────────────────

RESUME_EXTRACT_PROMPT = """\
从以下简历中提取关键信息，仅返回 JSON，无其他文字：

{resume}

输出格式：
{{
  "name": "<姓名>",
  "title": "<当前目标职位>",
  "total_exp_years": <产品/设计/技术相关工作年数，整数>,
  "core_skills": ["技能1", "技能2", ...],
  "domains": ["领域1", "领域2", ...],
  "recent_achievements": ["成就1", "成就2", "成就3"],
  "target_salary_min_k": <期望月薪下限（K），整数，无明确期望填 0>,
  "preferred_location": "<城市名，如上海>"
}}"""

MATCH_PROMPT = """\
你是严格的求职顾问，评估候选人与职位的匹配度并生成量身定制的打招呼消息。

候选人画像：
{resume_summary}

职位信息：
标题：{title}
公司：{company}
薪资：{salary}
经验要求：{experience}
公司信息：{company_info}
HR姓名：{hr_name}
职位描述（完整原文）：
{description_excerpt}
职位需求标签：
{requirements}

评分标准（严格执行，不得随意拔高）：
9-10：职位核心要求与候选人背景几乎完全重叠
7-8 ：主体方向匹配，有 1-2 项核心要求候选人略弱
5-6 ：方向相关，但核心要求与候选人背景有明显差距
3-4 ：领域相关但岗位性质明显不同（如纯技术岗 vs 产品岗）
1-2 ：基本不相关
{strict_rules}
请返回 JSON（仅 JSON，无其他文字）：
{{
  "match_score": <0-10 整数，10 为完全匹配>,
  "top_matches": ["最强匹配点1", "最强匹配点2"],
  "greeting": "<打招呼消息，60-90字。规则：1.开头称呼HR姓氏（如「李女士」），无法判断性别则用「您好」；2.引用JD描述中一个具体场景或要求词（非泛泛「AI经验」，要具体如「智能体产品0到1」）；3.结合简历最强1-2个具体经历呼应该场景；4.语气自然友好、有礼貌、不卑不亢，像人写的而非模板>"
}}"""

STRICT_RULES = """\

严格模式额外约束：
- 职位若不明确要求 AI/Agent/产品化经验，不得给出 8 分以上
- 职位若为纯技术研发岗（工程师/算法），强制不超过 5 分
- 薪资若低于候选人期望下限 30% 以上，扣 1 分
"""


# ─── DB 工具 ──────────────────────────────────────────────────────────────────

def _ensure_greetings_table(conn: sqlite3.Connection) -> None:
    """Create greetings and job_details tables if they don't exist (idempotent)."""
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
    conn.commit()


def _is_already_greeted(
    conn: sqlite3.Connection, title: str, company: str, hr_name: str | None
) -> bool:
    """Check if a greeting has already been sent to this job."""
    dedup_key = f"{normalize_card_title(title)}\t{company}\t{hr_name or ''}"
    row = conn.execute(
        """SELECT COUNT(g.id) FROM job_details jd
           JOIN greetings g ON g.job_details_id = jd.id
           WHERE jd.dedup_key = ?""",
        (dedup_key,),
    ).fetchone()
    return bool(row and row[0] > 0)


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


# ─── 评分 ──────────────────────────────────────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return text


def extract_resume_summary(client: anthropic.Anthropic, resume: str) -> str:
    """Call Haiku once to distill the full resume into a compact structured JSON string."""
    prompt = RESUME_EXTRACT_PROMPT.format(resume=resume)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
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
    client: anthropic.Anthropic, resume_summary: str, job: dict, strict: bool = False
) -> dict:
    """Call Claude Haiku to score one job and generate a personalized greeting."""
    description = job.get("description") or ""
    prompt = MATCH_PROMPT.format(
        resume_summary=resume_summary,
        title=job.get("title", ""),
        company=job.get("company", ""),
        salary=job.get("salary_raw") or "未知",
        experience=job.get("experience") or "未知",
        company_info=job.get("company_info") or "未知",
        hr_name=job.get("hr_name") or "（未知）",
        description_excerpt=description if description else "（无）",
        requirements=job.get("requirements_text") or "  无标签",
        strict_rules=STRICT_RULES if strict else "",
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
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

def _return_to_job_list(skill: BOSSAutomationSkill) -> bool:
    for _ in range(4):
        if skill.get_current_page() == PageState.JOB_LIST:
            return True
        skill.press_back()
        time.sleep(0.6)
    return skill.get_current_page() == PageState.JOB_LIST


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
) -> dict:
    """
    Single-phase live loop: for each job card, enter detail page → score → send if OK.
    Returns result dict with greeted / skipped / errors lists.
    """
    logger = setup_logger("smart_match_greet", log_dir="./logs/boss")
    adb = ADBRunner()
    skill = BOSSAutomationSkill(adb, device_id=device_id)
    result: dict = {"greeted": [], "skipped": [], "errors": []}

    logger.info("[1/3] 启动 Boss直聘…")
    skill._adb(f"shell am force-stop {skill.APP_PACKAGE}")
    time.sleep(1.0)
    if not skill.launch():
        result["errors"].append("无法启动 App，请确认 ADB 已连接")
        return result

    logger.info("[2/3] 搜索职位：「%s」…", keyword)
    if not skill.browse_jobs(keyword):
        result["errors"].append(f"搜索失败：{keyword}")
        return result
    if not skill.wait_for_element("com.hpbr.bosszhipin:id/tv_position_name", timeout=6.0):
        result["errors"].append("职位列表加载超时")
        return result

    logger.info("[3/3] 采集职位列表…")
    jobs = skill.scroll_job_list(n_jobs=60, max_scrolls=20)
    logger.info("  ✓ 采集到 %d 条职位卡片", len(jobs))

    greeted_count = 0
    for idx, job in enumerate(jobs, 1):
        if greeted_count >= max_greet:
            logger.info("已达最大打招呼数 %d，结束", max_greet)
            break

        title = job.title or ""
        company = job.company or ""
        hr_name = job.hr_name or ""

        # 去重检查
        if _is_already_greeted(db_conn, title, company, hr_name):
            logger.info("  [%d/%d] 跳过（已打过招呼）：%s", idx, len(jobs), title)
            result["skipped"].append(f"{title}（{company}）：已打过招呼")
            continue

        # 薪资预过滤（利用列表卡片信息快速跳过）
        if min_salary_k > 0:
            low_k = _parse_salary_low_k(job.salary or "")
            if low_k is not None and low_k < min_salary_k:
                result["skipped"].append(f"{title}：薪资偏低")
                continue

        # 确保 App 就绪
        if not skill.ensure_ready():
            result["errors"].append("ensure_ready 失败，停止任务")
            break

        # 返回职位列表（首条无需返回，后续每条都需要）
        if idx > 1:
            if not _return_to_job_list(skill):
                result["errors"].append("无法返回职位列表")
                break

        # 进入详情页
        logger.info("  [%d/%d] → %s（%s）", idx, len(jobs), title, company)
        if not skill.navigate_to_job(job):
            result["errors"].append(f"{title}：导航失败")
            continue
        if not skill.wait_for_element("com.hpbr.bosszhipin:id/tv_job_name", timeout=4.0):
            result["errors"].append(f"{title}：详情页加载超时")
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
            "salary_raw":       detail.get("salary") or job.salary,
            "experience":       detail.get("experience") or "",
            "company_info":     detail.get("company_info") or "",
            "hr_name":          detail.get("hr_name") or hr_name,
            "hr_title":         detail.get("hr_title") or (job.hr_title if hasattr(job, "hr_title") else ""),
            "hr_active":        detail.get("hr_active") or (job.hr_active if hasattr(job, "hr_active") else ""),
            "description":      detail.get("description") or "",
            "requirements_text": "  无标签（请从JD描述中判断）",
        }

        # Haiku 评分 + 生成打招呼
        try:
            scored = score_job(client, resume_summary, job_dict, strict=strict)
        except Exception as exc:
            logger.error("  评分失败：%s — %s", title, exc)
            result["errors"].append(f"{title}：评分失败（{exc}）")
            continue

        score = scored.get("match_score", 0)
        greeting = scored.get("greeting", "")
        logger.info("  分数 %d/10  %s", score, scored.get("top_matches", [])[:2])

        if score < threshold or not greeting:
            logger.info("  → 跳过（%d < %d）", score, threshold)
            result["skipped"].append(f"{title}（{company}）：{score}/10 低于阈值")
            continue

        logger.info("  ✓ 达标！打招呼：%s…", greeting[:60])

        if score_only:
            result["skipped"].append(
                f"{title}（{company}）：{score}/10 ✓ [score_only]\n    {greeting}"
            )
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
                    continue

            if not skill.tap_element("chat_btn"):
                result["errors"].append(f"{title}：无法打开聊天页")
                continue
            if not skill.wait_for_element(
                "com.hpbr.bosszhipin:id/editText_with_scrollbar", timeout=4.0
            ):
                result["errors"].append(f"{title}：聊天页加载超时")
                continue

            dialog2 = skill.detect_dialog()
            if dialog2 != DialogType.NONE:
                skill.dismiss_dialog(dialog2)
                if dialog2 in _FATAL_DIALOGS:
                    result["errors"].append(f"致命弹窗（{dialog2}），终止")
                    break
                if dialog2 not in _CONTINUE_DIALOGS:
                    result["skipped"].append(f"{title}（{dialog2}）")
                    continue

            ok = skill.send_greeting(greeting, verify=verify_send)
            action = "打招呼成功" if ok else "发送未确认"
            result["greeted"].append(
                {"job": title, "company": company, "score": score, "action": action, "greeting": greeting}
            )
            logger.info("  ✓ %s — %s", action, greeting[:40])
            greeted_count += 1

            _record_greeting(db_conn, title, company, hr_name, keyword, greeting, action)
            time.sleep(1.0)

        except Exception as exc:
            logger.error("处理 '%s' 异常", title, exc_info=True)
            result["errors"].append(f"{title}: {exc}")
            if not skill.ensure_ready():
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
    client = anthropic.Anthropic()
    print("⏳ 提取简历画像（Haiku）…", end="", flush=True)
    resume_summary = extract_resume_summary(client, resume_text)
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
