"""E2E 스모크 테스트 — 추가 서비스 없이 단일 프로세스에서 동작.

실행:  pytest -q
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE = (
    "production_date,line_id,product_id,qty,defect_qty\n"
    "2026-05-29,FAB-1,P-100,500,12\n"
    "2026-05-29,FAB-1,P-200,300,5\n"
    "2026-05-29,FAB-1,P-100,200,3\n"
)


def _wait_done(task_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/ingest/status/{task_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.2)
    raise AssertionError("처리 타임아웃")


def test_health_and_info():
    assert client.get("/health").json()["status"] == "ok"
    assert "backends" in client.get("/info").json()


def test_schema():
    body = client.get("/agent/tools/schema").json()
    assert body["dataset"] == "production"
    assert body["partition_keys"] == ["production_date", "line_id"]
    assert "qty" in body["schema"]["properties"]


def test_ingest_then_query():
    # 1) 업로드 → 202
    r = client.post(
        "/ingest",
        files={"file": ("p.csv", SAMPLE, "text/csv")},
        data={"source_id": "rpa-bot-1"},
    )
    assert r.status_code == 202
    task_id = r.json()["task_id"]

    # 2) 백그라운드 처리 완료 대기
    done = _wait_done(task_id)
    assert done["status"] == "done", done
    assert done["rows"] == 3

    # 3) 멱등성: 동일 파일 재업로드 → duplicate
    r2 = client.post(
        "/ingest",
        files={"file": ("p.csv", SAMPLE, "text/csv")},
        data={"source_id": "rpa-bot-1"},
    )
    assert r2.json()["duplicate"] is True

    # 4) 에이전트 조회 → 요약 JSON
    q = client.post(
        "/agent/tools/query",
        json={"dataset": "production",
              "filters": {"production_date": "2026-05-29", "line_id": "FAB-1"}},
    )
    assert q.status_code == 200
    s = q.json()
    assert s["found"] is True
    assert s["totals"]["qty"] == 1000          # 500+300+200
    assert s["totals"]["defect_qty"] == 20     # 12+5+3
    assert s["row_count"] == 3


def test_schema_evolution():
    """새 컬럼이 포함된 데이터를 적재하면 Iceberg 스키마가 무중단 진화하고
    /schema 에 자동 반영된다. 기존 데이터 조회도 정상 유지."""
    evolved = (
        "production_date,line_id,product_id,qty,defect_qty,operator\n"
        "2026-05-29,FAB-1,P-100,100,2,kim\n"
    )
    r = client.post(
        "/ingest",
        files={"file": ("e.csv", evolved, "text/csv")},
        data={"source_id": "evolve"},
    )
    assert r.status_code == 202
    done = _wait_done(r.json()["task_id"])
    assert done["status"] == "done", done

    # /schema 에 새 컬럼(operator) 반영
    schema = client.get("/agent/tools/schema").json()
    assert "operator" in schema["schema"]["properties"]

    # 기존 데이터 + 신규 합산(1000 + 100), 행 3 + 1
    q = client.post(
        "/agent/tools/query",
        json={"dataset": "production",
              "filters": {"production_date": "2026-05-29", "line_id": "FAB-1"}},
    ).json()
    assert q["totals"]["qty"] == 1100
    assert q["row_count"] == 4


def test_query_input_validation():
    # 화이트리스트 위반(경로 주입 시도) → 422
    bad = client.post(
        "/agent/tools/query",
        json={"dataset": "production",
              "filters": {"production_date": "2026-05-29", "line_id": "../etc"}},
    )
    assert bad.status_code == 422
    # 알 수 없는 dataset → 422
    bad2 = client.post(
        "/agent/tools/query",
        json={"dataset": "nope", "filters": {"production_date": "2026-05-29", "line_id": "FAB-1"}},
    )
    assert bad2.status_code == 422


def test_query_missing_partition():
    q = client.post(
        "/agent/tools/query",
        json={"dataset": "production",
              "filters": {"production_date": "1999-01-01", "line_id": "FAB-9"}},
    )
    assert q.json()["found"] is False


def test_query_limit_cap():
    """limit 은 QUERY_ROW_LIMIT 설정으로 클램프된다."""
    from app.config import settings

    settings.query_row_limit = 2
    try:
        q = client.post(
            "/agent/tools/query",
            json={"dataset": "production",
                  "filters": {"production_date": "2026-05-29", "line_id": "FAB-1"},
                  "limit": 1000},
        ).json()
        # 클램프(2)로 스캔 → row_count <= 2
        assert q["row_count"] <= 2
    finally:
        settings.query_row_limit = 10000


def test_multi_dataset():
    """두 번째 데이터셋(sensor_readings)이 production 과 독립적으로 적재/조회된다."""
    sensor = (
        "reading_date,sensor_id,metric,value\n"
        "2026-06-09,S-1,temperature,21.5\n"
        "2026-06-09,S-1,temperature,22.5\n"
        "2026-06-09,S-1,humidity,40.0\n"
    )
    r = client.post(
        "/ingest",
        files={"file": ("s.csv", sensor, "text/csv")},
        data={"source_id": "sensor-bot", "dataset": "sensor_readings"},
    )
    assert r.status_code == 202
    assert r.json()["dataset"] == "sensor_readings"
    done = _wait_done(r.json()["task_id"])
    assert done["status"] == "done", done

    # 스키마(sensor_readings)
    sc = client.get("/agent/tools/schema", params={"dataset": "sensor_readings"}).json()
    assert sc["dataset"] == "sensor_readings"
    assert sc["partition_keys"] == ["reading_date", "sensor_id"]

    # 조회: value 합계 = 21.5+22.5+40.0 = 84.0, row_count=3
    q = client.post(
        "/agent/tools/query",
        json={"dataset": "sensor_readings",
              "filters": {"reading_date": "2026-06-09", "sensor_id": "S-1"}},
    ).json()
    assert q["found"] is True
    assert q["row_count"] == 3
    assert abs(q["totals"]["value"] - 84.0) < 1e-6
    # 그룹(metric)별 — temperature 2건 합 44.0
    by_metric = {g["metric"]: g["value"] for g in q["groups"]}
    assert abs(by_metric["temperature"] - 44.0) < 1e-6


def test_metrics_endpoint():
    body = client.get("/metrics").text
    assert "ingest_accepted_total" in body
    assert "query_total" in body


def test_ingest_auth_token():
    """INGEST_TOKEN 설정 시 헤더 없으면 401, 맞으면 통과."""
    from app.config import settings

    settings.ingest_token = "s3cret"
    try:
        # 헤더 없음 → 401
        r = client.post(
            "/ingest",
            files={"file": ("a.csv", SAMPLE, "text/csv")},
            data={"source_id": "x"},
        )
        assert r.status_code == 401
        # 잘못된 토큰 → 401
        r2 = client.post(
            "/ingest",
            files={"file": ("a.csv", SAMPLE, "text/csv")},
            data={"source_id": "x"},
            headers={"X-Service-Token": "wrong"},
        )
        assert r2.status_code == 401
        # 올바른 토큰 → 202/그 외(인증 통과)
        r3 = client.post(
            "/ingest",
            files={"file": ("a.csv", SAMPLE, "text/csv")},
            data={"source_id": "x"},
            headers={"X-Service-Token": "s3cret"},
        )
        assert r3.status_code in (202,)
    finally:
        settings.ingest_token = ""  # 다른 테스트에 영향 없도록 복구


def test_reprocess_errors():
    # 없는 task → 404
    assert client.post("/ingest/reprocess/nope").status_code == 404
    # done 상태 재처리 시도 → 409 (failed 만 허용)
    r = client.post(
        "/ingest",
        files={"file": ("p2.csv", SAMPLE, "text/csv")},
        data={"source_id": "rp"},
    )
    # 동일 내용이면 duplicate 일 수 있으니 task_id 확보
    tid = r.json()["task_id"]
    _wait_done(tid)
    assert client.post(f"/ingest/reprocess/{tid}").status_code == 409
