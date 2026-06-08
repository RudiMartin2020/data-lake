# 테스트 결과 보고서

[`docs/TEST_SCENARIOS.md`](TEST_SCENARIOS.md) 시나리오 실행 결과.

| 항목 | 값 |
|---|---|
| 실행일 | 2026-06-08 |
| 모드 | **B (운영 백엔드)** — MinIO + Celery + PostgreSQL + Iceberg + DuckDB |
| 환경 | centos9(CentOS Stream 9, **Python 3.12.12 / uv**), postgres16 컨테이너 |
| `/info` | `storage=minio, task=celery, catalog=postgres, table_format=iceberg, query_engine=duckdb` |
| 데이터셋 | `production` (production_date, line_id, product_id, qty, defect_qty) |

## 종합 결과: ✅ 전 항목 통과 (10/10)

| ID | 시나리오 | 결과 | 핵심 측정값 |
|---|---|:---:|---|
| TS-01 | 자동 테스트(pytest) | ✅ | **10 passed** |
| TS-02 | 스키마 조회 | ✅ | 5컬럼 · 파티션키 2개 |
| TS-03 | 기본 적재→상태→조회 | ✅ | done(6행/3파티션), total_qty=1000 |
| TS-04 | 멱등성(중복 차단) | ✅ | duplicate=true |
| TS-05 | 입력검증/보안 | ✅ | 주입 422 · 빈파일 400 |
| TS-06 | DLQ(파싱 실패) | ✅ | failed + DLQ 격리 |
| TS-07 | **대용량 부하(10MB)** | ✅ | 처리 **11초** (35만행/1,296파티션) |
| TS-08 | 적재 현황(PostgreSQL) | ✅ | done 2 / failed 1, 정합 |
| TS-09 | 적재 현황(MinIO) | ✅ | warehouse 1,307객체 / 파티션 1,299 |
| TS-10 | 쿼리 엔드포인트 | ✅ | limit·미존재 정상 |

---

## 상세 결과

### TS-01 — 자동 테스트(pytest)
```
.......... [100%]   →  10 passed
```
폴백 백엔드(sqlite+file warehouse+inprocess)로 헬스/스키마/적재/멱등성/스키마진화/
입력검증/메트릭/재처리/limit 커버.

### TS-02 — 스키마 조회 `GET /agent/tools/schema`
```
dataset=production | partition_keys=['production_date','line_id']
columns=['production_date','line_id','product_id','qty','defect_qty']
```
**판정**: ✅ 기대 컬럼/파티션키 일치.

### TS-03 — 기본 적재 → 상태 → 조회 (happy path)
```
ingest : 202 accepted, duplicate=false
status : done, rows=6, partitions=3
query  : found=true, total_qty=1000, total_defect_qty=20, defect_rate=0.02
         products=[P-100(qty 700,def 15), P-200(qty 300,def 5)]
```
**판정**: ✅ 500+300+200=1000, 12+5+3=20 정확.

### TS-04 — 멱등성(중복 적재)
```
동일 파일 재업로드 → duplicate=true, status=duplicate
```
**판정**: ✅ content-hash 중복 차단.

### TS-05 — 입력검증 / 보안
```
(a) line_id="../etc" (경로 주입 시도)  → HTTP 422
(b) 빈 파일 업로드                      → HTTP 400
```
**판정**: ✅ Pydantic 화이트리스트·빈입력 차단 동작.

### TS-06 — DLQ(파싱 실패 격리)
```
깨진 CSV(foo,bar) 적재 → status=failed
  error: "validation: 필수 컬럼 누락: ['production_date','line_id',...]"
  DLQ 객체: 84523d2c...__broken.csv
```
**판정**: ✅ 영구 실패(검증) → DLQ 격리 + failed 기록.

### TS-07 — 대용량 부하 테스트 (10MB CSV)
입력: `production_10mb.csv` (350,000행 / 1,296 파티션)

| 지표 | 측정값 |
|---|---|
| 업로드 응답(202) | **0.72 s** (비블로킹) |
| 백그라운드 처리(Iceberg 적재) | **11 s** |
| 결과 | done, rows=350,000, partitions=1,296 |
| 조회(2026-01-01/FAB-1) 응답 | **0.15 s** |
| 조회 결과 | found=true, row_count=276, total_qty=139,856, defect_rate=0.0319 |

**판정**: ✅ 즉시 202(비동기 검증) · 파티션 프루닝 저지연(0.15s).

> **성능 개선 이력** (동일 10MB):
> | 구현 | 처리시간 |
> |---|---|
> | ① 베이스라인(파티션별 DuckDB COPY) | 311 s |
> | ② 단일 패스 PARTITION_BY + 병렬 업로드 | 45 s |
> | ③ **PyIceberg append (현재)** | **11 s** |
>
> Iceberg `table.append`가 파티션 분할·parquet 쓰기·메타데이터 커밋을 한 번에 처리 → **베이스라인 대비 28배**.

### TS-08 — 적재 현황 (PostgreSQL `ingestions`)
```
 status | cnt |  rows  | parts
--------+-----+--------+-------
 done   |   2 | 350006 |  1299
 failed |   1 |      0 |     0
```
**판정**: ✅ done 2건(소형 6행/3파티션 + 10MB 350,000행/1,296파티션 = 350,006/1,299), failed 1건(TS-06). 적재 시도와 정합.

### TS-09 — 적재 현황 (MinIO)
```
  raw        objects=     3     10.25 MB   (소형 + 10MB + broken 원본 보존)
  warehouse  objects=  1307      4.96 MB   (parquet + metadata.json + manifest.avro)
  dlq        objects=     1      0.00 MB   (broken.csv 격리)
  parquet files: 1299 / partitions: 1299
```
**판정**: ✅ 파티션 1,299 = TS-08 합계와 일치. 원본 10.25MB → Iceberg parquet ~4.96MB(컬럼 압축).

### TS-10 — 쿼리 엔드포인트
```
(a) limit=10        → row_count=3 (해당 파티션 3행, 상한 내)
(b) 미존재 파티션    → found=false, note="해당 파티션에 데이터가 없습니다." (HTTP 200)
```
**판정**: ✅ 파티션 프루닝·limit·미존재 케이스 정상.

### 관측성 — `GET /metrics`
```
ingest_accepted_total 3.0
ingest_duplicate_total 1.0
query_total           4.0
ingest_queue_depth    0.0
```
**판정**: ✅ Prometheus 노출 동작.
> ⚠️ `ingest_done/failed`는 **Celery 워커 프로세스**에서 집계되어 API `/metrics`엔 0으로 표시(프로세스별 레지스트리). 완전 통합은 prometheus multiprocess 모드 필요(후속).

---

## 관찰 / 비고

- ✅ **비동기 수집**: 10MB도 0.72초에 202 수락 — 타임아웃 방지(설계 C-3) 검증.
- ✅ **파티션 프루닝**: 1,299개 파티션 중 1개만 스캔 → 조회 0.15초.
- ✅ **에이전트 격리**: 경로 주입(422)·임의 SQL 차단 동작.
- ✅ **폐쇄망 적합**: DuckDB 확장 없이 PyArrow/PyIceberg로 동작(인터넷 불필요).
- ⚠️ **운영 주의**: 카탈로그(PostgreSQL)와 스토리지(MinIO)를 따로 비우면 불일치(조회 500). 항상 함께 초기화.
- ⚠️ **관측성 한계**: Celery 모드 done/failed 카운터는 워커 프로세스 분리 — multiprocess 통합 후속 과제.

## 결론
**설계 요구사항(REQ-01 비동기 수집 · REQ-02 격리 서빙)과 데이터 파이프라인(수집→Iceberg→DuckDB 서빙)이 운영 백엔드에서 전 항목 정상 동작.** 대용량 처리 성능은 베이스라인 대비 28배 개선(311s→11s).
