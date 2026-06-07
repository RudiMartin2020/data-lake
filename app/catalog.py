"""카탈로그 / 적재 이력(audit).

기본: SQLite (stdlib, 추가 서비스 불필요)
선택: PostgreSQL (CATALOG_BACKEND=postgres, POSTGRES_DSN 설정 시)

테이블: ingestions(task_id, dataset, source_id, content_hash, rows,
                    partitions, status, error, created_at, updated_at)
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from .config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS ingestions (
    task_id      TEXT PRIMARY KEY,
    dataset      TEXT,
    source_id    TEXT,
    content_hash TEXT,
    filename     TEXT,
    rows         INTEGER,
    partitions   INTEGER,
    status       TEXT NOT NULL,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingestions_hash ON ingestions(content_hash);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteCatalog:
    def __init__(self) -> None:
        settings.data_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(settings.sqlite_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()

    def create(self, *, task_id: str, dataset: str, source_id: str,
               content_hash: str, filename: str, status: str = "accepted") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ingestions"
                "(task_id,dataset,source_id,content_hash,filename,rows,partitions,status,error,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, dataset, source_id, content_hash, filename, None, None,
                 status, None, _now(), _now()),
            )
            self._conn.commit()

    def update(self, task_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE ingestions SET {cols} WHERE task_id=?",
                (*fields.values(), task_id),
            )
            self._conn.commit()

    def get(self, task_id: str) -> Optional[dict]:
        cur = self._conn.execute("SELECT * FROM ingestions WHERE task_id=?", (task_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def find_by_hash(self, content_hash: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT * FROM ingestions WHERE content_hash=? AND status IN ('done','processing','accepted') "
            "ORDER BY created_at DESC LIMIT 1",
            (content_hash,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_catalog():
    if settings.catalog_backend == "postgres":
        # 운영 전환 지점. 접속 실패 시 조용히 폴백하지 않고 예외를 올린다(설정 오류 가시화).
        from .catalog_pg import PostgresCatalog  # type: ignore
        return PostgresCatalog()
    if settings.catalog_backend == "sqlite":
        return SqliteCatalog()
    raise ValueError(f"알 수 없는 CATALOG_BACKEND: {settings.catalog_backend}")


catalog = get_catalog()
