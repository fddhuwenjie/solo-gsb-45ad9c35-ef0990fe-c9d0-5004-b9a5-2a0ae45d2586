"""FastAPI 入口：法规修订链引用审查 API。

端点
----
* ``POST /corpus``            写入一个文档版本（条款/修订关系/引用），重复写入返回 409
* ``GET  /corpus/{doc}/{ver}``查看已写入的文档版本
* ``POST /audit``             按目标日期解析引用，分类并返回可追溯链路
* ``GET  /health``            健康检查
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import database as db
from .resolver import Graph, run_audit
from .response import AuditResponse, CorpusResponse, HealthResponse
from .schemas import AuditRequest, CorpusIn


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    conn = sqlite3.connect(db.get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    app.state.conn = conn
    try:
        yield
    finally:
        conn.close()


app = FastAPI(
    title="法规修订链引用审查 API",
    version="1.0.0",
    description=(
        "沿可分支的法规修订链解析条款引用，区分当日有效、已废止、"
        "尚未生效、歧义与循环，并返回完整可追溯链路。"
    ),
    lifespan=lifespan,
)

# 单条共享 SQLite 连接被多线程复用；用 RLock 串行化所有游标访问
# （WAL 保证与容器外其它连接的读并发；本服务内请求量级下足够）。
_db_lock = threading.RLock()


def get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """业务校验错误统一为 422。"""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health(conn: sqlite3.Connection = Depends(get_conn)):
    with _db_lock:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": db.get_db_path()}


@app.post(
    "/corpus",
    response_model=CorpusResponse,
    status_code=201,
    tags=["corpus"],
    summary="写入一个文档版本（幂等：相同文档版本重复写入返回 409）",
)
def create_corpus(payload: CorpusIn, conn: sqlite3.Connection = Depends(get_conn)):
    with _db_lock:
        existing = db.document_version_exists(
            conn, payload.doc_id, payload.version
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"文档版本 {payload.doc_id}@{payload.version} 已写入，"
                    "相同文档版本不得重复写入；如需修订请使用新的版本号"
                ),
            )
        try:
            result = db.insert_corpus(conn, payload)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "doc_id": payload.doc_id,
        "version": payload.version,
        "name": payload.name,
        "effective_at": (
            payload.effective_at.isoformat() if payload.effective_at else None
        ),
        "created": True,
        "counts": {
            "clauses": result["clauses"],
            "revisions": result["revisions"],
            "citations": result["citations"],
        },
        "warnings": result["warnings"],
    }


@app.get(
    "/corpus/{doc_id}/{version}",
    tags=["corpus"],
    summary="查看已写入的文档版本详情",
)
def get_corpus(doc_id: str, version: str,
               conn: sqlite3.Connection = Depends(get_conn)):
    with _db_lock:
        doc = db.fetch_document(conn, doc_id, version)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档版本不存在")
    return doc


@app.post(
    "/audit",
    response_model=AuditResponse,
    tags=["audit"],
    summary="按目标日期解析全部（或过滤后的）引用并分类",
)
def audit(
    request: Optional[AuditRequest] = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    req = request or AuditRequest()
    target = req.target_at or datetime.now(timezone.utc)

    with _db_lock:
        citations = db.list_citations(
            conn,
            source_doc=req.source_doc,
            source_version=req.source_version,
            source_clause=req.source_clause,
            target_doc=req.target_doc,
        )
        graph = Graph(db.list_clauses(conn), db.list_edges(conn))
    return run_audit(graph, citations, target)
