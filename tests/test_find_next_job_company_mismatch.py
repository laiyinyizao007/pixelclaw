"""
Regression tests for _find_next_job 兜底 DB 查询的 company 不匹配 bug。

2026-10-15 实测：列表卡 company = '纵维立方科技'（短名），详情页 company =
'深圳市纵维立方科技有限公司'（带城市前缀全名）。job_visits 写的是详情页全名，
但 _find_next_job DB 兜底查询用的是列表卡短名 → COUNT=0 → 兜底放行 →
同卡死循环评分 N 次（实测 ≥10 次 = 5 分钟）。

修复：DB 兜底查询用双向 LIKE 子串匹配（list company 是 detail company 的子串时也命中）。

回归测试覆盖 3 个场景：
1. list company == detail company（基线） → COUNT=1, skip
2. list company 是 detail company 的子串（前缀不同） → COUNT=1, skip
3. list company 与 detail company 完全无关 → COUNT=0, 兜底放行
"""
import sqlite3
from unittest.mock import MagicMock

import pytest

from scenarios.boss.scripts import smart_match_greet  # noqa: E402  (pytest importlib mode)


# ───────────────────────────── fixtures ─────────────────────────────


@pytest.fixture
def db_conn():
    """In-memory SQLite with job_visits table."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE job_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            company TEXT,
            hr_name TEXT,
            keyword TEXT,
            score INTEGER,
            greeted INTEGER DEFAULT 0,
            greeting_text TEXT,
            skip_reason TEXT,
            visited_at TEXT,
            salary TEXT,
            experience TEXT,
            company_info TEXT,
            description TEXT,
            top_matches TEXT,
            mismatch_concerns TEXT
        )
        """
    )
    # _is_already_greeted queries these tables
    conn.execute(
        "CREATE TABLE job_details ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "dedup_key TEXT UNIQUE, hr_name TEXT)"
    )
    conn.execute(
        "CREATE TABLE greetings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "job_details_id INTEGER, keyword TEXT, "
        "greeting_text TEXT, sent_at TEXT, action TEXT)"
    )
    conn.commit()
    return conn


def _make_job_info(title, company, hr_name, tap_y=100):
    """Build a minimal JobInfo-like object for _find_next_job testing."""
    from dataclasses import replace
    from skills.boss.boss_automation_skill import JobInfo

    job = JobInfo(
        title=title, company=company, hr_name=hr_name,
        tap_x=100, tap_y=tap_y, salary="", location="",
        hr_title="", hr_active="",
    )
    return replace(job)


def _insert_visit(conn, title, company, hr_name, greeted=0, skip_reason="低分(4<7)"):
    conn.execute(
        "INSERT INTO job_visits (title, company, hr_name, score, greeted, skip_reason, visited_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, company, hr_name, 4, greeted, skip_reason, "2026-10-15T17:00:00"),
    )
    conn.commit()


# ───────────────────────────── the bug under test ─────────────────────────────


def _run_find_next(db_conn, list_company, list_hr_name, visited_keys=None, detail_company=None, title="软件代表(J10718)"):
    """Run _find_next_job with mock skill that returns one list card.

    Returns the visit count after the script's DB query. The query in
    `_find_next_job` (line 1172-1176) needs to handle company variants
    via LIKE matching, otherwise it returns 0 and triggers 兜底放行.
    """
    if visited_keys is None:
        visited_keys = set()

    skill = MagicMock()
    visible = [_make_job_info(
        title=title,
        company=list_company,
        hr_name=list_hr_name,
    )]
    skill.get_job_list.return_value = visible

    target = _make_job_info(
        title=title,
        company=list_company,
        hr_name=list_hr_name,
    )

    # Simulate having visited this card BEFORE — both with list company (line 1709)
    # and detail company (line 1845)
    from skills.boss.boss_automation_skill import normalize_card_title
    detail_co = detail_company or list_company
    visited_keys.add(f"{normalize_card_title(title)}\t{list_company}\t{list_hr_name}")
    visited_keys.add(f"{normalize_card_title(title)}\t{detail_co}\t{list_hr_name}")
    visited_keys.add(f"{normalize_card_title(title)}\t\t{list_hr_name}")

    result = smart_match_greet._find_next_job(
        skill=skill,
        visited_keys=visited_keys,
        initial_jobs=[target],
        db_conn=db_conn,
    )
    return result


# ───────────────────────────── tests ─────────────────────────────


def test_list_company_equals_detail_company_returns_skip(db_conn):
    """Baseline: list == detail → COUNT=1 → skip (handled by line 1177-1182)."""
    _insert_visit(db_conn, "软件代表(J10718)", "纵维立方科技", "颜先生")
    result = _run_find_next(
        db_conn,
        list_company="纵维立方科技",
        list_hr_name="颜先生",
        detail_company="纵维立方科技",
    )
    # Returns None = no unvisited job found (caller should scroll/break)
    assert result is None, (
        "Bug: list company == detail company should skip (returns None). "
        f"Got {result!r}"
    )


def test_list_company_is_substring_of_detail_company_returns_skip(db_conn):
    """Regression: list='纵维立方科技' (短名), detail='深圳市纵维立方科技有限公司' (全名)
    → COUNT should be 1 (via LIKE) → skip, not 兜底放行.
    """
    _insert_visit(db_conn, "软件代表(J10718)", "深圳市纵维立方科技有限公司", "颜先生")
    result = _run_find_next(
        db_conn,
        list_company="纵维立方科技",  # 列表卡短名
        list_hr_name="颜先生",
        detail_company="深圳市纵维立方科技有限公司",  # 详情页全名
    )
    assert result is None, (
        "BUG REGRESSION: list company is substring of detail company should "
        "skip (returns None) but got兜底放行 → 返回卡片 → 死循环评分. "
        f"Got {result!r}"
    )


def test_unrelated_company_returns_card(db_conn):
    """list company 与 detail company 完全无关 → COUNT=0 → 兜底放行 → 返回卡（正确）。"""
    _insert_visit(db_conn, "软件代表(J10718)", "另一个完全不同的公司名字", "颜先生")
    result = _run_find_next(
        db_conn,
        list_company="纵维立方科技",
        list_hr_name="颜先生",
        detail_company="另一个完全不同的公司名字",
    )
    # Returns the card (兜底放行) = correct behavior for genuine new card
    assert result is not None, (
        "When companies genuinely differ, card should be returned (兜底放行). "
        f"Got None"
    )


def test_empty_company_in_list_with_full_company_in_detail(db_conn):
    """Edge: list card has empty company (RecyclerView 懒加载未填充),
    detail page has '深圳市纵维立方科技有限公司' → should still skip.
    """
    _insert_visit(db_conn, "软件代表(J10718)", "深圳市纵维立方科技有限公司", "颜先生")
    result = _run_find_next(
        db_conn,
        list_company="",  # 列表卡 company 懒加载未填充
        list_hr_name="颜先生",
        detail_company="深圳市纵维立方科技有限公司",
    )
    assert result is None, (
        "Empty list company should not break dedup (title+hr_name suffices). "
        f"Got {result!r}"
    )


def test_empty_list_hr_name_with_full_detail_hr_name(db_conn):
    """Edge: list card has empty hr_name (RecyclerView 懒加载未填充),
    detail page has '张女士' → should still skip (title+company 足以匹配).
    """
    _insert_visit(db_conn, "AI产品专家", "北京有乐今天科技有限公司", "张女士")
    result = _run_find_next(
        db_conn,
        list_company="北京有乐今天科技有限公司",
        list_hr_name="",  # 列表卡 hr_name 懒加载未填充
        detail_company="北京有乐今天科技有限公司",
        title="AI产品专家",
    )
    assert result is None, (
        "Empty list hr_name should not break dedup (title+company 足以匹配). "
        f"Got {result!r}"
    )


def test_both_list_company_and_hr_name_empty(db_conn):
    """Edge: list card 全空 (company='' AND hr_name='')，detail 全填
    → 标题唯一时也能匹配（company 和 hr_name 都退化）.
    """
    _insert_visit(db_conn, "AI产品专家", "北京有乐今天科技有限公司", "张女士")
    result = _run_find_next(
        db_conn,
        list_company="",
        list_hr_name="",
        detail_company="北京有乐今天科技有限公司",
        title="AI产品专家",
    )
    assert result is None, (
        "List 全空 + detail 全填, 标题唯一时仍应 skip. "
        f"Got {result!r}"
    )
