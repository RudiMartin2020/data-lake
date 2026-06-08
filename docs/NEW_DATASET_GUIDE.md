# 새로운 데이터셋 처리 가이드

새 데이터가 들어왔을 때 **무엇을 바꿔야 하는지** 판단하고 처리하는 런북.

> ✅ **멀티 데이터셋(경로 B) 구현 완료**. 현재 `production` + `sensor_readings` 2종이 공존하며,
> 새 데이터셋은 `app/dataset.py` 의 `DATASETS` 에 **항목 하나만 추가**하면 적재/조회가 동작한다.
> 적재 시 `-F "dataset=<이름>"`, 조회 시 `{"dataset":..., "filters":{...}}`.

---

## 0. 먼저 — "변화"의 종류를 구분하라

| 변화 | 예 | 처리 | 코드 작업 |
|---|---|:---:|---|
| **① 컬럼 추가** (같은 데이터셋) | `production`에 `operator` 컬럼 추가 | **자동**(스키마 진화) | ❌ 없음 |
| **② 파일 형식** (같은 데이터셋) | CSV → Excel/Parquet/JSON | reader 추가 | 🟢 작음 |
| **③ 새 데이터셋** (다른 엔티티) | `production` 외 `sensor_readings` 신규 | 온보딩 | 🟡 중간 |

> 이 문서는 주로 **③ 새 데이터셋**을 다룬다. ①②는 §5 참고.

### 판단 기준
```
데이터가 들어옴
  ├─ 기존 데이터셋인데 컬럼만 늘었나? ──── 예 → ① 자동(아무것도 안 해도 됨)
  ├─ 기존 데이터셋인데 파일 형식이 다른가? ─ 예 → ② reader 분기 추가
  └─ 파티션 키/엔티티가 다른 완전히 새 데이터? → ③ 새 데이터셋 온보딩 (이 문서)
```

---

## 1. 현재 구조 (멀티 데이터셋 — 구현됨)

`app/dataset.py` 의 `DATASETS` 레지스트리가 데이터셋별 파티션 키·필수 컬럼·집계(measure)·그룹키를 선언한다.
파이프라인(수집/적재/조회)은 전부 `dataset` 인자로 일반화되어 있다.

| 위치 | 역할 |
|---|---|
| `app/dataset.py` | **`DATASETS` 레지스트리** (데이터셋 정의) |
| `app/routers/ingest.py` | `dataset` Form 인자 + 검증 |
| `app/iceberg_io.py` | `lake.<dataset>` 테이블, 파티션 = `partition_keys` |
| `app/query.py` | `measures`/`group_by` 기반 일반 집계 |
| `app/schemas.py` | `QueryRequest(dataset+filters)` / `QuerySummary(totals/groups)` |

→ **새 데이터셋 = `DATASETS` 에 항목 추가** (§B). 다른 파티션 키도 OK.

---

## 2. 새 데이터셋 처리 — 두 가지 경로

### 경로 A. 빠른 교체 (데이터셋 1개만 바꿀 때)
기존 `production`을 다른 데이터셋으로 **대체**한다. 가장 작은 변경.

1. `app/dataset.py` 수정 — 새 컬럼/파티션키/이름
   ```python
   PRODUCTION_COLUMNS = {                # 새 스키마로 교체
       "reading_date": {"type": "date",   "description": "측정일 · 파티션 키"},
       "sensor_id":    {"type": "string", "description": "센서 ID · 파티션 키"},
       "value":        {"type": "float",  "description": "측정값"},
   }
   PARTITION_KEYS = ["reading_date", "sensor_id"]
   DATASET_NAME = "sensor_readings"
   ```
2. `app/schemas.py`의 `QueryRequest` 필드 교체 (`production_date`/`line_id` → `reading_date`/`sensor_id`)
3. `app/query.py`·`scan_partition` 의 집계 컬럼(`qty`/`defect_qty`) 을 새 측정값으로 수정
4. 테스트 데이터/케이스 수정 → `pytest`

> 단점: **한 번에 1개 데이터셋만** 가능. 여러 데이터셋 공존 불가.

### 경로 B. 멀티 데이터셋 (권장 — 일회성 리팩터링 후 무한 확장)
데이터셋을 **레지스트리**로 일반화하면, 이후 새 데이터셋은 **항목 추가**만으로 끝난다.

#### B-1. 데이터셋 레지스트리 (`app/dataset.py`)
```python
DATASETS = {
    "production": {
        "partition_keys": ["production_date", "line_id"],
        "required":       ["production_date","line_id","product_id","qty","defect_qty"],
        "measures":       ["qty", "defect_qty"],     # 집계(SUM) 대상
        "group_by":       "product_id",
    },
    "sensor_readings": {                             # ← 새 데이터셋은 여기만 추가
        "partition_keys": ["reading_date", "sensor_id"],
        "required":       ["reading_date","sensor_id","metric","value"],
        "measures":       ["value"],
        "group_by":       "metric",
    },
}
```

#### B-2. 수집 — `dataset` 지정 (`app/routers/ingest.py`)
```python
async def ingest(file: UploadFile = File(...),
                 source_id: str = Form(...),
                 dataset: str = Form("production")):   # ← 데이터셋 인자
    if dataset not in DATASETS:
        raise HTTPException(400, f"알 수 없는 dataset: {dataset}")
    ...
    enqueue(task_id, raw_key, raw, dataset)            # 워커에 dataset 전달
```
호출:
```bash
curl -X POST .../ingest -F "file=@sensor.csv" -F "source_id=rpa" -F "dataset=sensor_readings"
```

#### B-3. 적재/조회 — 테이블·파티션을 dataset 기준으로 (`app/iceberg_io.py`)
- 테이블 식별자: `lake.<dataset>` (이미 `DATASET_NAME` 한 곳만 일반화하면 됨)
- 파티션 스펙: `DATASETS[dataset]["partition_keys"]`
- `scan_partition(dataset, filters: dict, limit)` 로 일반화 (고정 date/line → 동적 필터)

#### B-4. 조회 계약 — 일반 필터 (`app/schemas.py`)
고정 필드 대신 데이터셋별 파티션 키를 받는 일반 형태:
```json
POST /agent/tools/query
{ "dataset": "sensor_readings",
  "filters": { "reading_date": "2026-06-09", "sensor_id": "S-1" },
  "limit": 1000 }
```
검증: `filters` 의 키가 해당 데이터셋의 `partition_keys` 와 일치하는지 + 값 화이트리스트.

---

## 3. 변경 대상 파일 (경로 B 기준)

| 파일 | 변경 |
|---|---|
| `app/dataset.py` | `DATASETS` 레지스트리 도입, 헬퍼(`partition_keys(ds)` 등) |
| `app/routers/ingest.py` | `dataset` Form 인자 + 검증 |
| `app/tasks.py` · `app/processing.py` | `dataset`를 enqueue/처리에 전달, 검증/적재 시 사용 |
| `app/iceberg_io.py` | `append_arrow(dataset, at)` · `scan_partition(dataset, filters, limit)` |
| `app/query.py` | dataset별 measures/group_by로 집계 |
| `app/schemas.py` | `QueryRequest`에 `dataset`+`filters`(고정필드 제거) |
| `app/routers/agent.py` | `/schema?dataset=` 지원 |
| `tests/` | 데이터셋별 케이스 |

---

## 4. 예시: `sensor_readings` 온보딩 (경로 B)

1. `DATASETS`에 `sensor_readings` 항목 추가 (§B-1)
2. 적재:
   ```bash
   curl -X POST http://localhost:8000/ingest \
     -F "file=@sensor.csv" -F "source_id=rpa" -F "dataset=sensor_readings"
   ```
3. 조회:
   ```bash
   curl -X POST http://localhost:8000/agent/tools/query \
     -H "Content-Type: application/json" \
     -d '{"dataset":"sensor_readings","filters":{"reading_date":"2026-06-09","sensor_id":"S-1"}}'
   ```
4. 결과: `lake.sensor_readings` Iceberg 테이블 자동 생성 + 파티션 적재 → 조회.
   → **production 과 독립적으로 공존**.

---

## 5. 참고: ① 컬럼 추가 · ② 파일 형식

### ① 컬럼 추가 → 자동 (작업 없음)
새 컬럼이 포함된 데이터를 그냥 적재하면 **Iceberg 스키마 진화**로 무중단 반영되고 `/schema`에 자동 노출. (검증: `test_schema_evolution`)

### ② 파일 형식 → reader 분기 (`app/processing.py`)
파싱 직후부터는 전부 PyArrow 기반이라 **읽기 단계만** 추가하면 된다:
```python
def _read(filename: str, data: bytes) -> pa.Table:
    if filename.endswith(".csv"):     return pacsv.read_csv(io.BytesIO(data))
    if filename.endswith(".parquet"): return pq.read_table(io.BytesIO(data))
    if filename.endswith(".json"):    return pajson.read_json(io.BytesIO(data))
    if filename.endswith(".xlsx"):    return _xlsx_to_arrow(data)   # openpyxl→arrow
    raise ValidationError(f"지원하지 않는 형식: {filename}")
```
→ 검증·적재·조회 코드는 **변경 없음**.

---

## 6. 온보딩 체크리스트 (새 데이터셋)

- [ ] 파티션 키 정의 (날짜 + 식별자 권장, 카디널리티 과도 주의)
- [ ] 필수 컬럼 / 측정(집계) 컬럼 정의
- [ ] `DATASETS` 레지스트리 항목 추가 (경로 B) 또는 `dataset.py` 교체 (경로 A)
- [ ] 적재 시 `dataset` 지정 확인
- [ ] 조회 필터(파티션 키) 화이트리스트 검증
- [ ] 샘플 데이터로 적재→조회 E2E + `pytest`
- [ ] (운영) 카탈로그/스토리지 정합 — 둘 다 함께 초기화

## 7. 운영 주의
- **파티션 카디널리티**: 파티션 키 조합이 너무 잘게 쪼개지면(예: 초 단위) 파일 폭증 → 날짜(일)+식별자 수준 권장.
- **카탈로그↔스토리지 정합**: 데이터셋 삭제/재처리 시 PostgreSQL(iceberg_tables)과 MinIO(warehouse)를 **함께** 정리.
- **권한**: 데이터셋 단위 접근 제어가 필요하면 서빙 토큰을 데이터셋별로 분리(설계 §7).

---

## 결론
- **컬럼 추가**: 자동 ✅
- **파일 형식**: reader 한 곳 추가 🟢
- **새 데이터셋**: 현재는 단일 고정 → **경로 B(레지스트리) 일회성 도입**을 권장.
  그 후 새 데이터셋은 `DATASETS`에 **항목 하나 추가**로 끝난다.
