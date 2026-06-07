"""~10MB 샘플 데이터 4종 생성 (CSV / Excel / Parquet / JSONL).

프로젝트 스키마: production_date, line_id, product_id, qty, defect_qty
포맷별 압축 특성이 달라 목표 크기(~10MB)에 맞춰 행 수를 자동 보정한다.

실행:  .venv/bin/python sample_data/generate_samples.py
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

OUT_DIR = Path(__file__).resolve().parent
TARGET = 10 * 1024 * 1024          # 10 MB
LO, HI = 9.0 * 1024 * 1024, 11.5 * 1024 * 1024
MAX_ITERS = 5

# 결정적(랜덤 미사용) 행 생성 SQL — i=0..N-1
ROWGEN = """
SELECT
    ('2026-' || lpad((((i // 800) % 6) + 1)::TEXT, 2, '0') || '-'
             || lpad(((i % 27) + 1)::TEXT, 2, '0'))        AS production_date,
    ('FAB-' || ((i % 8) + 1))                              AS line_id,
    ('P-'   || lpad((i % 5000)::TEXT, 4, '0'))             AS product_id,
    (50 + (i * 7) % 950)::INTEGER                          AS qty,
    ((i * 3) % 40)::INTEGER                                AS defect_qty
FROM range({n}) t(i)
"""


def _mb(b: int) -> str:
    return f"{b / 1024 / 1024:.2f} MB"


def _gen_table(con, n: int):
    con.execute("DROP TABLE IF EXISTS prod")
    con.execute(f"CREATE TABLE prod AS {ROWGEN.format(n=n)}")


def tune(writer, n0: int, label: str, out: Path) -> None:
    """writer(con, n, out) 로 파일 생성. 크기가 목표 범위에 들 때까지 n 보정."""
    con = duckdb.connect()
    n = n0
    for it in range(1, MAX_ITERS + 1):
        _gen_table(con, n)
        writer(con, out)
        size = out.stat().st_size
        print(f"  [{label}] iter{it}: rows={n:,} -> {_mb(size)}")
        if LO <= size <= HI:
            break
        # 선형 가정으로 행 수 재추정(살짝 보수적으로 0.98)
        scale = TARGET / size
        n = max(1000, int(n * scale * 0.98))
    con.close()
    print(f"  [{label}] DONE rows={n:,} size={_mb(out.stat().st_size)} -> {out.name}")


def write_csv(con, out: Path):
    con.execute(f"COPY prod TO '{out.as_posix()}' (FORMAT CSV, HEADER)")


def write_parquet(con, out: Path):
    # 압축을 끄면(UNCOMPRESSED) 크기가 행 수에 선형 비례 → 목표 맞추기 쉬움
    con.execute(f"COPY prod TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION UNCOMPRESSED)")


def write_jsonl(con, out: Path):
    con.execute(f"COPY prod TO '{out.as_posix()}' (FORMAT JSON, ARRAY false)")


def write_xlsx(con, out: Path):
    import openpyxl
    rows = con.execute("SELECT * FROM prod").fetchall()
    cols = [d[0] for d in con.description]
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("production")
    ws.append(cols)
    for r in rows:
        ws.append(list(r))
    wb.save(str(out))


def main():
    print(f"목표: 각 ~{_mb(TARGET)} (허용 {_mb(int(LO))}~{_mb(int(HI))})")
    # 초기 행 수 추정치(포맷별 바이트/행 차이 반영)
    tune(write_csv,     350_000,   "CSV",     OUT_DIR / "production_10mb.csv")
    tune(write_jsonl,   95_000,    "JSONL",   OUT_DIR / "production_10mb.jsonl")
    tune(write_parquet, 600_000,   "PARQUET", OUT_DIR / "production_10mb.parquet")
    tune(write_xlsx,    180_000,   "XLSX",    OUT_DIR / "production_10mb.xlsx")


if __name__ == "__main__":
    main()
