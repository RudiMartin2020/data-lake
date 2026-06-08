"""PostgreSQL 적재 이력(audit) 저장소 (CATALOG_BACKEND=postgres).

SqliteAudit 와 동일한 인터페이스(create/update/get/find_by_hash)를 제공한다.
psycopg(v3) 사용. POSTGRES_DSN 으로 접속.

  예: postgresql://flopi_adm:****@host:5432/flopi
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import psycopg  # 선택 의존성(psycopg[binary])
from psycopg_pool import ConnectionPool

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
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingestions_hash ON ingestions(content_hash);
"""

_COLS = ("task_id", "dataset", "source_id", "content_hash", "filename",
         "rows", "partitions", "status", "error", "created_at", "updated_at")


def _now():
    return datetime.now(timezone.utc)


class PostgresAudit:
    def __init__(self) -> None:
        if not settings.postgres_dsn:
            raise RuntimeError("CATALOG_BACKEND=postgres 인데 POSTGRES_DSN 이 비어 있습니다.")
        # 풀 생성 시점에 1회 연결을 확인 → 잘못된 접속정보는 즉시 드러남
        self.pool = ConnectionPool(settings.postgres_dsn, min_size=1, max_size=5, open=True)
        with self.pool.connection() as conn:
            conn.execute(_DDL)
            conn.commit()

    def create(self, *, task_id: str, dataset: str, source_id: str,
               content_hash: str, filename: str, status: str = "accepted") -> None:
        now = _now()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO ingestions "
                "(task_id,dataset,source_id,content_hash,filename,rows,partitions,status,error,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,NULL,NULL,%s,NULL,%s,%s) "
                "ON CONFLICT (task_id) DO UPDATE SET status=EXCLUDED.status, updated_at=EXCLUDED.updated_at",
                (task_id, dataset, source_id, content_hash, filename, status, now, now),
            )
            conn.commit()

    def update(self, task_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=%s" for k in fields)
        with self.pool.connection() as conn:
            conn.execute(
                f"UPDATE ingestions SET {cols} WHERE task_id=%s",
                (*fields.values(), task_id),
            )
            conn.commit()

    def get(self, task_id: str) -> Optional[dict]:
        with self.pool.connection() as conn:
            cur = conn.execute("SELECT * FROM ingestions WHERE task_id=%s", (task_id,))
            row = cur.fetchone()
            return self._to_dict(row, cur) if row else None

    def find_by_hash(self, content_hash: str) -> Optional[dict]:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "SELECT * FROM ingestions WHERE content_hash=%s "
                "AND status IN ('done','processing','accepted') "
                "ORDER BY created_at DESC LIMIT 1",
                (content_hash,),
            )
            row = cur.fetchone()
            return self._to_dict(row, cur) if row else None

    @staticmethod
    def _to_dict(row, cur) -> dict:
        names = [d.name for d in cur.description]
        return dict(zip(names, row))
