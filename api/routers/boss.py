"""
Boss router: job_visits / greetings / jobs / job_details / requirements 的 CRUD API。

所有写操作通过字段白名单防止意外修改，只暴露合理可编辑的字段。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.services import db_reader as db

router = APIRouter(prefix="/api/boss", tags=["boss"])


# ─── Pydantic 请求体 ───────────────────────────────────────────────


class UpdateRequest(BaseModel):
    data: Dict[str, Any]


class BatchDeleteRequest(BaseModel):
    ids: List[int]


# ─── 统计 ─────────────────────────────────────────────────────────


@router.get("/stats")
async def boss_stats():
    return db.get_boss_stats()


# ─── 通用 CRUD（按表名路由）────────────────────────────────────────
#
#  GET    /api/boss/table/{table}                  → 分页列表
#  GET    /api/boss/table/{table}/{id}             → 单条详情
#  PUT    /api/boss/table/{table}/{id}             → 更新字段
#  DELETE /api/boss/table/{table}/{id}             → 删除单条
#  POST   /api/boss/table/{table}/batch-delete     → 批量删除
#
#  允许的表: job_visits | greetings | jobs | job_details | requirements


@router.get("/table/{table}")
async def list_table(
    table: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    order_by: str = Query(default="id"),
    order_dir: str = Query(default="DESC"),
    # ── job_visits 过滤 ──
    keyword: Optional[str] = None,
    keyword__like: Optional[str] = None,
    greeted: Optional[int] = None,
    score__gte: Optional[int] = None,
    score__lte: Optional[int] = None,
    title__like: Optional[str] = None,
    company__like: Optional[str] = None,
    visited_at__gte: Optional[str] = None,
    visited_at__lte: Optional[str] = None,
    # ── greetings 过滤 ──
    action: Optional[str] = None,
    sent_at__gte: Optional[str] = None,
    sent_at__lte: Optional[str] = None,
    # ── jobs 过滤 ──
    salary_low_k__gte: Optional[float] = None,
    salary_high_k__lte: Optional[float] = None,
    # ── requirements 过滤 ──
    job_id: Optional[int] = None,
    category: Optional[str] = None,
    is_bonus: Optional[int] = None,
):
    # 把所有有值的过滤参数收集起来
    raw_filters = {
        "keyword":          keyword,
        "keyword__like":    keyword__like,
        "greeted":          greeted,
        "score__gte":       score__gte,
        "score__lte":       score__lte,
        "title__like":      title__like,
        "company__like":    company__like,
        "visited_at__gte":  visited_at__gte,
        "visited_at__lte":  visited_at__lte,
        "action":           action,
        "sent_at__gte":     sent_at__gte,
        "sent_at__lte":     sent_at__lte,
        "salary_low_k__gte":  salary_low_k__gte,
        "salary_high_k__lte": salary_high_k__lte,
        "job_id":           job_id,
        "category":         category,
        "is_bonus":         is_bonus,
    }
    filters = {k: v for k, v in raw_filters.items() if v is not None}

    try:
        rows, total = db.list_rows(
            table, filters=filters,
            page=page, page_size=page_size,
            order_by=order_by, order_dir=order_dir,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {
        "table": table,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "rows": rows,
    }


@router.get("/table/{table}/{row_id}")
async def get_row(table: str, row_id: int):
    try:
        row = db.get_row(table, row_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, f"{table} id={row_id} 不存在")
    return row


@router.put("/table/{table}/{row_id}")
async def update_row(table: str, row_id: int, body: UpdateRequest):
    try:
        ok = db.update_row(table, row_id, body.data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, f"{table} id={row_id} 不存在或无可更新字段")
    return {"ok": True, "updated_id": row_id}


@router.delete("/table/{table}/{row_id}")
async def delete_row(table: str, row_id: int):
    try:
        ok = db.delete_row(table, row_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, f"{table} id={row_id} 不存在")
    return {"ok": True, "deleted_id": row_id}


@router.post("/table/{table}/batch-delete")
async def batch_delete(table: str, body: BatchDeleteRequest):
    try:
        count = db.delete_rows(table, body.ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "deleted_count": count}


# ─── 语义化别名（前端可以用更直观的路径）────────────────────────────

@router.get("/job-visits")
async def list_job_visits(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: Optional[str] = None,
    greeted: Optional[int] = None,
    score__gte: Optional[int] = None,
    score__lte: Optional[int] = None,
    title__like: Optional[str] = None,
    company__like: Optional[str] = None,
    visited_at__gte: Optional[str] = None,
    visited_at__lte: Optional[str] = None,
):
    filters = {k: v for k, v in {
        "keyword": keyword, "greeted": greeted,
        "score__gte": score__gte, "score__lte": score__lte,
        "title__like": title__like, "company__like": company__like,
        "visited_at__gte": visited_at__gte, "visited_at__lte": visited_at__lte,
    }.items() if v is not None}
    rows, total = db.list_rows("job_visits", filters=filters, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "rows": rows}


@router.get("/greetings")
async def list_greetings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: Optional[str] = None,
    action: Optional[str] = None,
    sent_at__gte: Optional[str] = None,
    sent_at__lte: Optional[str] = None,
):
    filters = {k: v for k, v in {
        "keyword": keyword, "action": action,
        "sent_at__gte": sent_at__gte, "sent_at__lte": sent_at__lte,
    }.items() if v is not None}
    rows, total = db.list_rows("greetings", filters=filters, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "rows": rows}
