"""
DBReader: SQLite 读写服务

支持 job_visits / greetings / jobs / job_details / requirements 五张表的
CRUD 操作，使用参数化查询防止 SQL 注入。
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = str(Path(__file__).resolve().parents[2] / "scenarios/boss/output/requirements.db")

# 允许前端操作的表及其可写字段白名单
_ALLOWED_TABLES: Dict[str, Dict] = {
    "job_visits": {
        "pk": "id",
        "writable": {"score", "skip_reason", "greeted", "greeting_text", "top_matches", "mismatch_concerns"},
    },
    "greetings": {
        "pk": "id",
        "writable": {"greeting_text", "action"},
    },
    "jobs": {
        "pk": "id",
        "writable": {"company_quality"},
    },
    "job_details": {
        "pk": "id",
        "writable": {"description", "hr_name", "hr_title"},
    },
    "requirements": {
        "pk": "id",
        "writable": {"tag", "category", "is_bonus"},
    },
}

# 可作为过滤条件的字段（每张表）
_FILTER_FIELDS: Dict[str, set] = {
    "job_visits":  {"keyword", "greeted", "score", "title", "company", "visited_at"},
    "greetings":   {"keyword", "action", "sent_at", "job_details_id"},
    "jobs":        {"keyword", "title", "company", "salary_low_k", "salary_high_k"},
    "job_details": {"dedup_key", "hr_name"},
    "requirements": {"job_id", "tag", "category", "is_bonus"},
}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_rows(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
    page: int = 1,
    page_size: int = 50,
    order_by: str = "id",
    order_dir: str = "DESC",
) -> Tuple[List[Dict], int]:
    """
    分页查询，返回 (rows, total_count)。
    filters 支持精确匹配；前缀 __gte / __lte / __like 用于范围/模糊查询。
    示例: {"score__gte": 70, "keyword__like": "AI"}
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"不允许的表名: {table}")
    if order_dir.upper() not in ("ASC", "DESC"):
        order_dir = "DESC"

    allowed_filter_fields = _FILTER_FIELDS.get(table, set())
    where_parts: List[str] = []
    params: List[Any] = []

    for key, val in (filters or {}).items():
        if val is None or val == "":
            continue
        if key.endswith("__gte"):
            field = key[:-5]
            if field in allowed_filter_fields:
                where_parts.append(f"{field} >= ?")
                params.append(val)
        elif key.endswith("__lte"):
            field = key[:-5]
            if field in allowed_filter_fields:
                where_parts.append(f"{field} <= ?")
                params.append(val)
        elif key.endswith("__like"):
            field = key[:-6]
            if field in allowed_filter_fields:
                where_parts.append(f"{field} LIKE ?")
                params.append(f"%{val}%")
        else:
            if key in allowed_filter_fields:
                where_parts.append(f"{key} = ?")
                params.append(val)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    offset = (page - 1) * page_size

    with _conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM {table} {where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM {table} {where_sql} ORDER BY {order_by} {order_dir} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()

    return [dict(r) for r in rows], total


def get_row(table: str, pk: int) -> Optional[Dict]:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"不允许的表名: {table}")
    pk_col = _ALLOWED_TABLES[table]["pk"]
    with _conn() as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {pk_col} = ?", (pk,)
        ).fetchone()
    return dict(row) if row else None


def update_row(table: str, pk: int, data: Dict[str, Any]) -> bool:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"不允许的表名: {table}")
    writable = _ALLOWED_TABLES[table]["writable"]
    pk_col = _ALLOWED_TABLES[table]["pk"]

    safe_data = {k: v for k, v in data.items() if k in writable}
    if not safe_data:
        return False

    set_parts = ", ".join(f"{k} = ?" for k in safe_data)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE {table} SET {set_parts} WHERE {pk_col} = ?",
            list(safe_data.values()) + [pk],
        )
        conn.commit()
    return cur.rowcount > 0


def delete_row(table: str, pk: int) -> bool:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"不允许的表名: {table}")
    pk_col = _ALLOWED_TABLES[table]["pk"]
    with _conn() as conn:
        cur = conn.execute(f"DELETE FROM {table} WHERE {pk_col} = ?", (pk,))
        conn.commit()
    return cur.rowcount > 0


def delete_rows(table: str, pks: List[int]) -> int:
    """批量删除，返回实际删除行数"""
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"不允许的表名: {table}")
    if not pks:
        return 0
    pk_col = _ALLOWED_TABLES[table]["pk"]
    placeholders = ",".join("?" * len(pks))
    with _conn() as conn:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE {pk_col} IN ({placeholders})", pks
        )
        conn.commit()
    return cur.rowcount


def get_boss_stats() -> Dict[str, Any]:
    """今日统计 + 总体统计"""
    with _conn() as conn:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        total_greetings = conn.execute("SELECT COUNT(*) FROM greetings").fetchone()[0]
        total_visits = conn.execute("SELECT COUNT(*) FROM job_visits").fetchone()[0]
        greeted_visits = conn.execute(
            "SELECT COUNT(*) FROM job_visits WHERE greeted = 1"
        ).fetchone()[0]

        today_greetings = conn.execute(
            "SELECT COUNT(*) FROM greetings WHERE date(sent_at) = date('now', 'localtime')"
        ).fetchone()[0]
        today_visits = conn.execute(
            "SELECT COUNT(*) FROM job_visits WHERE date(visited_at) = date('now', 'localtime')"
        ).fetchone()[0]

        avg_score = conn.execute(
            "SELECT ROUND(AVG(score), 1) FROM job_visits WHERE score IS NOT NULL"
        ).fetchone()[0]

        top_keywords = conn.execute(
            "SELECT keyword, COUNT(*) as cnt FROM job_visits GROUP BY keyword ORDER BY cnt DESC LIMIT 10"
        ).fetchall()

    return {
        "total_jobs": total_jobs,
        "total_greetings": total_greetings,
        "total_visits": total_visits,
        "greeted_visits": greeted_visits,
        "today_greetings": today_greetings,
        "today_visits": today_visits,
        "avg_score": avg_score,
        "top_keywords": [{"keyword": r[0], "count": r[1]} for r in top_keywords],
    }
