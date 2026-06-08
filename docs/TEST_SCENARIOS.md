# RPA Data-to-AI — 테스트 시나리오

| 항목 | 내용 |
|---|---|
| 대상 | `data-lake` FastAPI 애플리케이션 (수집/워커/스토리지/카탈로그/서빙) |
| 범위 | 기능 테스트 · 입력검증/보안 · **대용량 부하 테스트** · 적재 현황(DB/MinIO) · 쿼리 엔드포인트 |
| 실행 위치 | centos9(Linux) 컨테이너 내부, `/workspace/data-lake` |
| 데이터셋 | `production` — 컬럼 `production_date, line_id, product_id, qty, defect_qty` |

> ⚠️ **엔진 변경 안내**: 적재/서빙이 **Apache Iceberg(PyIceberg) + PyArrow** 로 교체되었습니다.
> warehouse 경로는 `warehouse/iceberg/lake/production/...`(데이터 parquet + metadata json + manifest avro)이며,
> 카탈로그는 PostgreSQL `iceberg_tables` 입니다. 아래 부록의 **부하 측정 수치는 이전(DuckDB/Parquet) 구현 기준**이라
> Iceberg 재측정이 필요합니다(시나리오 절차 자체는 동일하게 동작).

> 모든 명령은 **centos9 컨테이너 안**에서 실행합니다.
> ```powershell
> docker exec -it centos9 bash
> cd /workspace/data-lake
> ```

---

## 0. 테스트 모드

두 가지 모드로 동일 시나리오를 수행할 수 있습니다.

| 모드 | 백엔드 | 기동 방법 | 용도 |
|---|---|---|---|
| **A. 폴백(개발)** | 로컬 FS · 인프로세스 워커 · SQLite · DuckDB | `bash run.sh` 만 | 미들웨어 없이 빠른 기능 검증 |
| **B. 운영 백엔드** | MinIO · Celery/Redis · PostgreSQL · DuckDB | `bash middleware.sh start && bash run.sh` | 실제 미들웨어 통합·부하 검증 |

- 모드 A: `.env` 가 없거나 기본값일 때. 적재 현황은 `./data/`(로컬 FS)와 `./data/catalog.db`(SQLite)에서 확인.
- 모드 B: `.env` 에 `STORAGE_BACKEND=minio / TASK_BACKEND=celery / CATALOG_BACKEND=postgres` 설정. 적재 현황은 **MinIO 버킷**과 **PostgreSQL `ingestions` 테이블**에서 확인.

아래 시나리오는 **모드 B(운영 백엔드)** 기준으로 작성하되, 모드 A 차이는 각 절에 표기합니다.

### 0.1 서버 기동 / 헬스 확인

```bash
bash middleware.sh start      # (모드 B) MinIO + Celery 기동, Redis/Postgres 상태 출력
bash middleware.sh status     # Redis/MinIO/Celery/Postgres UP 확인
nohup bash run.sh > /tmp/api.log 2>&1 &   # API(:8000) 기동

curl -s http://localhost:8000/health      # {"status":"ok",...}
curl -s http://localhost:8000/info        # 활성 백엔드 확인
```

**합격 기준**: `/info.backends` 가 의도한 백엔드(`storage/task/catalog/query_engine`)를 정확히 표시.

---

## 시나리오 요약

| ID | 시나리오 | 핵심 검증 |
|---|---|---|
| TS-01 | 자동 테스트(pytest) | 회귀 5건 통과 |
| TS-02 | 스키마 조회 | `GET /agent/tools/schema` |
| TS-03 | 기본 적재(소형) → 조회 | 수집→처리→쿼리 happy path |
| TS-04 | 멱등성(중복 적재) | content-hash 중복 차단 |
| TS-05 | 입력검증/보안 | 잘못된 인자 422 · 빈 파일 400 |
| TS-06 | DLQ(파싱 실패) | 깨진 파일 → `failed` + dlq 격리 |
| TS-07 | **대용량 부하(10MB)** | 단건 처리시간·동시성 처리량 |
| TS-08 | **적재 현황 — DB** | `ingestions` 테이블 집계 |
| TS-09 | **적재 현황 — MinIO** | 버킷 객체수/용량·파티션 트리 |
| TS-10 | 쿼리 엔드포인트 | 파티션 프루닝·limit·미존재 파티션 |

---

## TS-01. 자동 테스트 (pytest)

```bash
.venv/bin/pytest -q
```
**기대**: `5 passed`. (TestClient 기반 E2E — 추가 서비스 불필요, 항상 폴백 백엔드로 동작)

---

## TS-02. 스키마 조회

```bash
curl -s http://localhost:8000/agent/tools/schema | python3 -m json.tool
```
**기대**: `dataset=production`, `partition_keys=["production_date","line_id"]`,
`schema.properties` 에 5개 컬럼(`qty/defect_qty` 등) 포함.

---

## TS-03. 기본 적재(소형) → 상태 → 조회  *(happy path)*

```bash
# 1) 적재 — 202 즉시 응답
RESP=$(curl -s -X POST http://localhost:8000/ingest \
  -F "file=@sample_data/production_2026-05-29.csv" -F "source_id=rpa-bot-1")
echo "$RESP"
TID=$(echo "$RESP" | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['task_id'])")

# 2) 상태 폴링 — done 대기
until curl -s http://localhost:8000/ingest/status/$TID | grep -q '"done"'; do sleep 0.5; done
curl -s http://localhost:8000/ingest/status/$TID

# 3) 조회
curl -s -X POST http://localhost:8000/agent/tools/query \
  -H "Content-Type: application/json" \
  -d '{"production_date":"2026-05-29","line_id":"FAB-1"}'
```
**기대**:
- 적재: `status=accepted`, `duplicate=false`
- 상태: `status=done`, `rows=6`, `partitions=3`
- 조회: `found=true`, `total_qty=1000`, `total_defect_qty=20`, `defect_rate=0.02`,
  `products` 에 P-100(qty 700)·P-200(qty 300)

---

## TS-04. 멱등성(중복 적재)

```bash
# TS-03 와 동일 파일을 재업로드
curl -s -X POST http://localhost:8000/ingest \
  -F "file=@sample_data/production_2026-05-29.csv" -F "source_id=rpa-bot-1" \
  | python3 -m json.tool
```
**기대**: `duplicate=true`, `status=duplicate`, 기존 `task_id` 반환(재처리 안 함).
> content-hash 가 같으면 차단됩니다. 부하 테스트 등 반복 적재 시에는 **TS-Cleanup** 으로 카탈로그를 비우거나 파일 내용을 변형해야 합니다.

---

## TS-05. 입력 검증 / 보안

```bash
# (a) 경로 주입 시도 — line_id 화이트리스트 위반
curl -s -o /dev/null -w "line_id 주입 -> %{http_code}\n" -X POST \
  http://localhost:8000/agent/tools/query -H "Content-Type: application/json" \
  -d '{"production_date":"2026-05-29","line_id":"../etc"}'

# (b) limit 상한 초과
curl -s -o /dev/null -w "limit 초과 -> %{http_code}\n" -X POST \
  http://localhost:8000/agent/tools/query -H "Content-Type: application/json" \
  -d '{"production_date":"2026-05-29","line_id":"FAB-1","limit":999999}'

# (c) 빈 파일 업로드
: > /tmp/empty.csv
curl -s -o /dev/null -w "빈 파일 -> %{http_code}\n" -X POST \
  http://localhost:8000/ingest -F "file=@/tmp/empty.csv" -F "source_id=x"
```
**기대**: (a) `422`, (b) `422`, (c) `400`.
보안 철칙(임의 SQL/파일 접근 차단, Pydantic 화이트리스트)이 작동.

---

## TS-06. DLQ (파싱 실패 격리)

```bash
# 필수 컬럼이 없는 깨진 CSV
printf "foo,bar\n1,2\n" > /tmp/broken.csv
RESP=$(curl -s -X POST http://localhost:8000/ingest -F "file=@/tmp/broken.csv" -F "source_id=bad")
TID=$(echo "$RESP" | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
until curl -s http://localhost:8000/ingest/status/$TID | grep -Eq '"(failed|done)"'; do sleep 0.5; done
curl -s http://localhost:8000/ingest/status/$TID
```
**기대**: `status=failed`, `error` 에 "필수 컬럼 누락…". 원본은 `dlq/` 버킷(모드 A: `./data/dlq/`)에 격리.

---

## TS-07. 대용량 부하 테스트 (10MB)

> 샘플 미존재 시 먼저 생성: `.venv/bin/python sample_data/generate_samples.py`
> 적재 API 는 **CSV** 를 파싱합니다(`production_10mb.csv`, 350,000행 / **1,296 파티션**).
> xlsx/parquet/jsonl 은 현재 `/ingest` 파서 대상이 아니며 DuckDB 직접 쿼리/향후 멀티포맷용입니다.

### 7-A. 단건 처리 시간 측정

```bash
# 업로드(202까지) 응답시간
curl -s -o /tmp/r.json -w "upload(202) %{time_total}s\n" -X POST \
  http://localhost:8000/ingest -F "file=@sample_data/production_10mb.csv" -F "source_id=load-1"
TID=$(.venv/bin/python -c "import json;print(json.load(open('/tmp/r.json'))['task_id'])")

# 백그라운드 처리 완료까지 벽시계 측정
START=$(date +%s)
until curl -s http://localhost:8000/ingest/status/$TID | grep -Eq '"(done|failed)"'; do sleep 1; done
END=$(date +%s)
echo "processing $((END-START))s"
curl -s http://localhost:8000/ingest/status/$TID
```
**기대/관찰**:
- 업로드는 즉시 `202`(수초 이내) — 비동기 설계 검증(C-3).
- 처리완료: `rows=350000`, `partitions=1296`.
- ⚠️ **관찰 포인트(병목)**: 현재 처리는 파티션마다 staging CSV 를 재스캔하며 parquet 를 기록합니다
  (1,296회). 따라서 처리시간이 길게 측정될 수 있습니다 → 처리량 개선 후보:
  DuckDB `COPY ... (FORMAT PARQUET, PARTITION_BY (production_date, line_id))` 단일 패스로 전환.
  이 시나리오의 목적은 **현 구조의 처리시간/병목을 정량화**하는 것입니다.

### 7-B. 동시 적재(부하) — N건 병렬

content-hash 중복을 피하려고 각 복사본에 고유 행을 덧붙여 변형합니다.

```bash
N=10
mkdir -p /tmp/load
for i in $(seq 1 $N); do
  cp sample_data/production_10mb.csv /tmp/load/f$i.csv
  echo "2026-06-27,FAB-9,P-UNIQ-$i,1,0" >> /tmp/load/f$i.csv   # 파일별 고유화
done

# 병렬 업로드(202 수신 시간)
time (for i in $(seq 1 $N); do
  curl -s -o /dev/null -X POST http://localhost:8000/ingest \
    -F "file=@/tmp/load/f$i.csv" -F "source_id=load-$i" &
done; wait)
echo "submitted $N ingests"

# 처리 소진 모니터링 (Celery 큐가 빌 때까지)
watch -n 2 'curl -s localhost:8000/info >/dev/null; \
  redis-cli llen celery 2>/dev/null | xargs -I{} echo "queue depth: {}"'
```
**기대/관찰**:
- 모든 업로드가 즉시 `202` (블로킹 없음).
- 모드 B: Celery 워커 동시성에 따라 큐가 점진적으로 소진. 워커 수를 늘리면 처리량 증가 →
  `bash middleware.sh stop` 후 `middleware.sh` 의 `--concurrency` 조정 또는 워커 다중 기동으로 비교.
- 측정 지표: 제출 처리량(req/s), 큐 소진 시간, 워커당 처리 건수.

> 워커 동시성 비교 시: Celery 워커를 `--pool=prefork --concurrency=4` 등으로 바꿔 재기동하여
> 동일 N 건의 소진 시간을 비교합니다.

---

## TS-08. 적재 현황 — PostgreSQL `ingestions` 테이블  *(모드 B)*

```bash
# 컨테이너 외부(호스트)에서 직접 조회해도 됩니다:
#   docker exec -e PGPASSWORD=flopi1234 postgres16 psql -h 127.0.0.1 -U flopi_adm -d flopi -c "..."

PSQL='docker exec -e PGPASSWORD=flopi1234 postgres16 psql -h 127.0.0.1 -U flopi_adm -d flopi'

# 상태별 집계
$PSQL -c "SELECT status, COUNT(*) AS cnt, COALESCE(SUM(rows),0) AS rows, COALESCE(SUM(partitions),0) AS parts FROM ingestions GROUP BY status ORDER BY status;"

# 최근 10건
$PSQL -c "SELECT task_id, source_id, rows, partitions, status, created_at FROM ingestions ORDER BY created_at DESC LIMIT 10;"

# 실패 건 상세(있으면)
$PSQL -c "SELECT task_id, filename, error FROM ingestions WHERE status='failed' ORDER BY updated_at DESC LIMIT 5;"

# 적재 처리율(분당)
$PSQL -c "SELECT date_trunc('minute',updated_at) m, COUNT(*) done FROM ingestions WHERE status='done' GROUP BY 1 ORDER BY 1 DESC LIMIT 10;"
```
**기대**: 상태별 건수/행수 합계가 적재 시도와 일치. `done` 건의 `rows/partitions` 가 채워져 있음.

> **모드 A(SQLite)**: 적재 이력 DB 는 `./data/audit.db`
> `.venv/bin/python -c "import sqlite3;print(sqlite3.connect('data/audit.db').execute('select status,count(*) from ingestions group by status').fetchall())"`

---

## TS-09. 적재 현황 — MinIO 객체  *(모드 B)*

```bash
# 버킷별 객체 수 / 총 용량 / Iceberg warehouse 파티션 수
.venv/bin/python - <<'PY'
from minio import Minio
c = Minio("localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False)
for b in ["raw","warehouse","dlq"]:
    objs = list(c.list_objects(b, recursive=True))
    size = sum(o.size for o in objs)
    print(f"{b:10s} objects={len(objs):>6}  size={size/1024/1024:8.2f} MB")
# Iceberg 데이터 파일/파티션 (warehouse/iceberg/lake/production/data/...)
data = [o.object_name for o in c.list_objects("warehouse", prefix="iceberg/lake/production/data/", recursive=True)]
parts = {"/".join(o.split("/")[5:7]) for o in data if o.endswith(".parquet")}
print("parquet files:", len([o for o in data if o.endswith('.parquet')]))
print("partitions:", len(parts), "sample:", sorted(parts)[:3])
PY
```
**기대**:
- `raw`: 적재 파일 수 = 성공 적재 건수. `warehouse/iceberg/lake/production/` 에 데이터(parquet)+메타(json)+매니페스트(avro).
- `partitions` = 누적 파티션 수(10MB 단건이면 1,296). DuckDB 멀티스레드로 파티션당 parquet 다수 가능.
- 콘솔 UI: 브라우저 **http://localhost:9001** (minioadmin/minioadmin).

> **모드 A(로컬 FS, file:// warehouse)**:
> ```bash
> du -sh data/raw data/iceberg data/dlq 2>/dev/null
> find data/iceberg -name '*.parquet' | wc -l        # 데이터 파일 수
> ```

---

## TS-10. 쿼리 엔드포인트 테스트

```bash
# (a) 존재하는 파티션 — 요약 집계
curl -s -X POST http://localhost:8000/agent/tools/query -H "Content-Type: application/json" \
  -d '{"production_date":"2026-01-01","line_id":"FAB-1"}' | python3 -m json.tool

# (b) limit 적용(스캔 상한) — 결과 축소 확인
curl -s -X POST http://localhost:8000/agent/tools/query -H "Content-Type: application/json" \
  -d '{"production_date":"2026-01-01","line_id":"FAB-1","limit":10}' | python3 -m json.tool

# (c) 존재하지 않는 파티션 — 빈 결과(에러 아님)
curl -s -X POST http://localhost:8000/agent/tools/query -H "Content-Type: application/json" \
  -d '{"production_date":"1999-01-01","line_id":"FAB-9"}' | python3 -m json.tool
```
**기대**:
- (a) `found=true`, `row_count>0`, `total_qty>0`, `products[]` 채워짐.
- (b) `row_count<=10` (스캔 상한 반영).
- (c) `found=false`, `note="해당 파티션에 데이터가 없습니다."`, HTTP `200`.
- **파티션 프루닝 검증**: 다른 날짜/라인을 요청해도 해당 파티션 디렉터리만 읽음(전체 스캔 아님).

---

## TS-Cleanup. 초기화 / 정리

```bash
# API 종료
pkill -f "uvicorn app.main:app"

# (모드 B) 카탈로그 비우기 — 멱등성 차단 해제하여 재테스트
docker exec -e PGPASSWORD=flopi1234 postgres16 psql -h 127.0.0.1 -U flopi_adm -d flopi -c "TRUNCATE ingestions;"
# MinIO 데이터 비우기(선택)
.venv/bin/python - <<'PY'
from minio import Minio
from minio.deleteobjects import DeleteObject
c=Minio("localhost:9000",access_key="minioadmin",secret_key="minioadmin",secure=False)
for b in ["raw","staging","warehouse","dlq"]:
    objs=[DeleteObject(o.object_name) for o in c.list_objects(b,recursive=True)]
    list(c.remove_objects(b,objs))
print("minio cleared")
PY

# 미들웨어 종료
bash middleware.sh stop

# (모드 A) 로컬 데이터/임시 정리
rm -rf data /tmp/load /tmp/*.csv /tmp/r.json
```

---

## 합격 기준 체크리스트

- [ ] `/info` 백엔드 구성이 의도와 일치 (TS-0)
- [ ] `pytest` 5 passed (TS-01)
- [ ] 소형 적재 happy path: `done(6 rows,3 parts)` + 조회 `total_qty=1000` (TS-03)
- [ ] 중복 적재 `duplicate=true` (TS-04)
- [ ] 입력검증 422 / 빈 파일 400 (TS-05)
- [ ] 파싱 실패 `failed` + DLQ 격리 (TS-06)
- [ ] 10MB 업로드 즉시 202 · 처리완료 `rows=350000, partitions=1296` (TS-07-A)
- [ ] 동시 N건 적재 비블로킹 · 큐 소진 (TS-07-B)
- [ ] DB `ingestions` 집계가 적재와 정합 (TS-08)
- [ ] MinIO 버킷 객체/파티션 수 정합 (TS-09)
- [ ] 쿼리: 존재/limit/미존재 케이스 정상 (TS-10)

## 부록. 측정 결과 (베이스라인)

> 환경: centos9(CentOS Stream 9, Python 3.9) · 모드 B(MinIO+Celery solo+PostgreSQL) · 측정일 2026-06-07.
> 동일 환경 재측정 시 비교 기준으로 사용.

### TS-07-A — 10MB CSV 단건 (350,000행 / 1,296 파티션)

업로드(202)는 모든 버전에서 **~0.5 s**(비블로킹). 차이는 **백그라운드 처리시간**.

| 처리 구현 | 처리시간 | 배수 | 비고 |
|---|---|---|---|
| ① 베이스라인 — 파티션별 COPY(1,296회 CSV 재스캔) | **311 s** | 1.0x | 최초 구현 |
| ② 단일 패스 `PARTITION_BY` + 순차 업로드 | **92 s** | 3.4x | CSV 1회 스캔 |
| ③ 단일 패스 + **병렬 업로드(16스레드)** | **45 s** | **6.9x** | 현재 구현 |

- 모든 버전 결과 동일: `done`, `rows=350000`, `partitions=1296`, 쿼리 `total_qty=139,856` 불변(정확성 보존).
- ②→③ 개선 핵심: DuckDB 멀티스레드 파티션 쓰기가 파티션당 여러 parquet(~2,000개)을 생성 →
  MinIO 업로드(`fput_object`)가 병목 → `ThreadPoolExecutor`(16) 병렬화로 92→45 s.
- 적용 위치: [`app/processing.py`](../app/processing.py) `_try_duckdb_write`
  (`COPY ... PARTITION_BY (production_date, line_id) APPEND FILENAME_PATTERN '<task>_{uuid}'`).
- 남은 비용: CSV 파싱(검증·행수, 파이썬) + DuckDB 쓰기 + 병렬 업로드. 추가 개선 여지:
  업로드 스레드 수 상향, 파티션당 단일 파일 강제, raw/staging 보존 생략 옵션.

### TS-08/09 — 적재 현황 정합

| 위치 | 값 |
|---|---|
| PostgreSQL `ingestions` | `done` 1건 · rows 350,000 · partitions 1,296 |
| MinIO `raw` | 1 객체 / 10.25 MB (원본 보존) |
| MinIO `staging` | 1 객체 / 10.25 MB |
| MinIO `warehouse` | parquet ~4.1 MB (컬럼형 압축) · 파일 수는 구현에 따라 다름† |
| MinIO `dlq` | 0 |

> 원본 10.25MB → warehouse parquet 합계 ~4.1MB. Parquet 컬럼 압축으로 ~60% 축소.
> † 베이스라인(파티션당 1파일)=1,296개 / 현재 구현(`PARTITION_BY` 멀티스레드)=~2,000개.
> 파일 수와 무관하게 쿼리는 파티션 디렉터리의 `*.parquet` 전체를 읽어 동일 결과.

### TS-10 — 쿼리(파티션 프루닝) 응답시간

| 쿼리 | 결과 | 응답시간 |
|---|---|---|
| `2026-01-01 / FAB-1` | found, row_count=276, total_qty=139,856, defect_rate=0.0319 | **0.17 s** |
| `2026-06-27 / FAB-8` | found, row_count=274, total_qty=140,266, defect_rate=0.0407 | **0.15 s** |
| `limit=10` | row_count=10 (스캔 상한 반영) | — |
| 미존재 파티션 | found=false, `200` | — |

> 1,296 파티션 중 **해당 1개 파티션만 스캔** → 0.15~0.17초. 파티션 프루닝 정상 동작(전체 스캔 아님).

**결론**: 비동기 수집(즉시 202)·격리된 계약 쿼리(저지연)·파티션 프루닝은 설계대로 동작.
적재 처리량 병목(파티션 폭증)은 **`PARTITION_BY` 단일 패스 + 병렬 업로드**로 개선 적용 완료
(**311 s → 45 s, 6.9x**, 결과 불변).

---

## 트러블슈팅

| 증상 | 원인/조치 |
|---|---|
| 적재가 계속 `accepted` 에서 안 넘어감 | (모드 B) Celery 워커 미기동 → `bash middleware.sh status` 후 `start` |
| `duplicate=true` 만 반환 | 동일 파일 재적재 → TS-Cleanup 으로 `TRUNCATE ingestions` |
| 조회 `found=false` 인데 DB엔 done | 카탈로그(영속)와 스토리지(초기화) 불일치 → 둘 다 초기화 후 재적재 |
| MinIO 접속 실패 | `middleware.sh start` 로 MinIO 기동, `MINIO_ENDPOINT/키` 확인 |
| Postgres 접속 실패 | `lake` 네트워크 연결·`POSTGRES_DSN`(호스트 `postgres16`) 확인 |
| 10MB 처리시간 과다 | 정상(파티션 1,296). 관찰 포인트(TS-07-A) 참조 — PARTITION_BY 단일패스로 개선 가능 |
