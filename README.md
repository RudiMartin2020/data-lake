# RPA Data-to-AI 통합 시스템 (data-lake)

사내 RPA 시스템 및 제조 설비에서 발생하는 **정형 데이터(Excel/CSV)** 를 안정적으로 통합 수집하고,
사내 **AI 에이전트(LLM)** 가 보안 규칙을 준수하며 조회할 수 있는 **Data-to-AI 통로(`tool_api`)** 를 구축하는 프로젝트입니다.

> 📐 상세 설계: [`docs/RPA_Data_to_AI_기술설계서_NoDocker.md`](docs/RPA_Data_to_AI_기술설계서_NoDocker.md)
>
> ℹ️ 설계 방침은 **네이티브 구동(Docker 미사용)** 입니다. 다만 개발 호스트가 **Windows** 라서
> Linux 환경을 확보하기 위해 **부득이하게 컨테이너로 Linux를 띄웠을 뿐**이며,
> **MinIO·Redis 등 미들웨어는 그 Linux 안에서 네이티브 프로세스로 직접 구동**합니다.
> 즉 컨테이너는 아키텍처 구성요소가 아니라 *Windows 위의 Linux 박스* 역할입니다.

---

## 1. 현재 상태

| 항목 | 내용 |
|---|---|
| 단계 | **M5 완료** — 수집·Iceberg 적재·서빙 Tool API + 스키마 진화 검증 완료 (운영 배포 M6 남음) |
| 코드 | FastAPI 앱 구현 완료 (`app/`) — 수집/워커/Iceberg/카탈로그/서빙 |
| 검증 | `pytest` 6 passed · 실 미들웨어(MinIO/S3+Celery+PostgreSQL) E2E + 스키마 진화 통과 |
| 산출물 | 기술 설계서(`docs/`) · FastAPI 애플리케이션(`app/`) |
| 배포 목표 | On-premise · Air-gapped(폐쇄망) · OSS only |

> 백엔드는 **환경변수만으로** 개발 폴백(로컬 FS·인프로세스·SQLite) ↔ 실 미들웨어(MinIO·Celery·PostgreSQL)
> 전환된다. 상세는 §9 참고.

---

## 2. 아키텍처

```
[RPA 서버 / 제조 설비]  --(Excel·CSV)-->  Ingestion API (FastAPI/uvicorn :8000)
                                              |
                                              | enqueue
                                              v
                                          Redis (:6379)  -->  Celery Worker
                                                                  |
                            parse · validate(Pydantic) · Iceberg(Parquet) 적재
                                                                  |
                          +---------------------------------------+
                          v                                       v
                  MinIO (:9000/:9001)                    PostgreSQL (:5432)
                  raw/staging/warehouse/dlq               카탈로그 · 적재 이력

[AI Agent / LLM]  --(GET /schema · POST /query)-->  Serving Tool API (FastAPI :8000)
                                                          |  파티션 프루닝(Iceberg) + DuckDB in-memory 집계
                                                          v
                                                     요약 JSON 반환
```

**설계 원칙**
- **Write/Read 경로 분리** — 수집 부하와 조회 부하를 독립.
- **에이전트 격리** — LLM은 DB 직접 접근·임의 SQL·파일시스템 접근 불가. Pydantic 계약 Tool API만 경유.
- **컨텍스트 효율** — 서빙 결과는 요약·정제 JSON으로 반환해 LLM context window 절약.

---

## 3. 기술 스택

| 레이어 | 기술 | 비고 |
|---|---|---|
| API | FastAPI + Uvicorn | async, 자동 OpenAPI |
| Validation | Pydantic v2 | 입력 화이트리스트로 에이전트 격리 |
| Broker | Redis | 경량 메시지 브로커 |
| Worker | Celery | 비동기 적재 처리, 재시도/스케줄링 |
| Object Storage | MinIO | 폐쇄망 S3 대체(단일 정적 바이너리) |
| Table Format | **Apache Iceberg / PyIceberg** | ACID·스키마 진화·스냅샷 (구현 완료) |
| File Format | Parquet | predicate/projection pushdown |
| Catalog | PostgreSQL | Iceberg SQL 카탈로그 + 적재 이력 |
| Query Engine | **DuckDB** | PyIceberg 프루닝 결과(PyArrow)를 in-memory SQL 집계. 확장 불필요(폐쇄망 안전) |
| Vector DB | Milvus (선택) | 유사도 검색(RAG) |

---

## 4. 현재 런타임 구조 (Windows 위의 Linux 박스)

운영 방침은 네이티브 구동이지만 개발 호스트가 **Windows 11** 이라, Linux 환경 확보를 위해
컨테이너로 Linux를 띄웠습니다. **그 Linux(centos9) 안에서 미들웨어를 네이티브 프로세스로 직접 구동**합니다.
호스트 `C:\workspace` 를 Linux `/workspace` 로 마운트합니다.

### 4.1 Linux 박스 / DB

| 컨테이너 | 이미지 | 역할 | 포트(호스트→컨테이너) |
|---|---|---|---|
| **centos9** | `centos9-dev:v1` | **Linux 호스트** — 앱·Redis·MinIO를 네이티브 프로세스로 구동 | `6379`, `8000`, `9000`, `9001` |
| **postgres16** | `postgres:16` | PostgreSQL 카탈로그/이력 DB | `5432` |

### 4.2 centos9 (Linux 호스트) 상세

- OS: CentOS Stream 9 / **Python 3.12.12** (uv 관리, `.python-version` 고정)
- **네이티브 구동 중**: `redis-server` (`:6379`)
- **네이티브 구동(MinIO)**: `/workspace/offline_repo/redis/minio` 단일 바이너리를 직접 실행 (`:9000`/`:9001`)
- 설치됨: FastAPI 0.128.8, Uvicorn 0.39.0, Pydantic 2.13.4
- 앱 포트: `8000`(FastAPI)

> 컨테이너는 *Windows 위의 Linux 박스*일 뿐이며, 미들웨어는 Docker 서비스가 아니라
> 이 Linux 안의 **OS 네이티브 프로세스**로 동작합니다. 운영 환경에서는 동일 구성을
> 실제 Linux 서버 + systemd로 옮깁니다.

### 4.3 postgres16 컨테이너 상세

| 항목 | 값 |
|---|---|
| 데이터베이스 | `flopi` |
| 사용자 | `flopi_adm` |
| 포트 | `5432` |

### 4.4 마운트 / 폐쇄망 반입

| 호스트 경로 | 컨테이너 경로 | 용도 |
|---|---|---|
| `C:\workspace` | `/workspace` | 프로젝트 소스 · MinIO 데이터 |
| `C:\offline_repo` | `/offline_repo` | 오프라인 패키지(OS 패키지·Python 휠·바이너리) 반입 |

---

## 5. 포트 요약

| 포트 | 서비스 |
|---|---|
| 8000 | FastAPI (Ingestion / Serving Tool API) |
| 6379 | Redis (Celery 브로커) |
| 9000 | MinIO API (S3) |
| 9001 | MinIO 콘솔 |
| 5432 | PostgreSQL |

---

## 6. 스토리지 레이아웃 (MinIO 버킷)

```
raw/         # 원본 파일(감사·재처리 보존)
staging/     # 수집 직후 임시
warehouse/iceberg/   # Iceberg 테이블: 데이터(Parquet) + 메타데이터(json) + 매니페스트(avro)
dlq/         # 파싱 실패 격리
```

**파티셔닝**: `production_date`(일) + `line_id` 기준(Iceberg identity 파티션).

> **Apache Iceberg 적용 완료**: 적재는 PyIceberg `table.append`(파일은 Parquet), 카탈로그는 PostgreSQL,
> warehouse는 MinIO/S3. **스키마 진화**(`add column` 무중단)·ACID·스냅샷을 지원하며, 신규 컬럼이 들어오면
> `GET /agent/tools/schema` 에 **자동 반영**된다. 조회는 **PyIceberg 파티션 프루닝 → DuckDB in-memory 집계**(제로카피)
> 하이브리드로, DuckDB iceberg/httpfs 확장 없이 폐쇄망에서 동작한다.

---

## 7. 주요 API (설계)

| Method / Path | 설명 |
|---|---|
| `POST /ingest` | 원천 데이터 비동기 수집. `202 Accepted` + `{task_id, status}` 즉시 응답 후 워커가 백그라운드 처리 |
| `GET /agent/tools/schema` | 카탈로그 컬럼/메타데이터를 JSON Schema로 반환 |
| `POST /agent/tools/query` | 계약된 인자값(`{"production_date":"2026-05-29","line_id":"FAB-1"}`)으로 조회 → 요약 JSON 반환 |
| `POST /ingest/reprocess/{task_id}` | 실패(DLQ) 건을 보존된 원본(raw/)에서 재적재 |
| `GET /metrics` | Prometheus 메트릭(수집/완료/실패/재시도/쿼리 카운터 + 큐 깊이) |

> **보안 철칙**: 에이전트는 임의 SQL·파일시스템 접근 금지. Pydantic 화이트리스트 인자만 허용.
>
> **인증(서비스 토큰)**: `INGEST_TOKEN`/`SERVING_TOKEN` 설정 시 헤더 `X-Service-Token` 검증.
> 수집(`/ingest*`)·서빙(`/agent/tools/*`)을 **분리 인증**. 미설정 시 비활성(개발).
>
> **재시도 정책**: 파싱/검증 오류=영구→DLQ, 인프라 오류=일시→Celery 재시도(소진 시 DLQ).

---

## 8. 프로젝트 구조

```
data-lake/
├─ app/
│  ├─ main.py            # FastAPI 진입점 (라우터 등록 · 부팅 부트스트랩)
│  ├─ config.py          # 환경설정 + 개발용 폴백 기본값
│  ├─ dataset.py         # 데이터셋 계약(컬럼·파티션 키) — production
│  ├─ schemas.py         # Pydantic 계약 모델(에이전트 격리)
│  ├─ storage.py         # 스토리지 추상화 (local 기본 / minio 선택)
│  ├─ catalog.py         # 적재 이력(audit) (sqlite 기본 / postgres 선택)
│  ├─ catalog_pg.py      # PostgreSQL 이력 구현 (psycopg + 풀)
│  ├─ iceberg_io.py      # Apache Iceberg 적재/조회/스키마진화 (PyIceberg)
│  ├─ auth.py            # 서비스 토큰 인증(수집/서빙 분리)
│  ├─ metrics.py         # Prometheus 메트릭 + 큐 깊이
│  ├─ tasks.py           # 작업 실행 (inprocess 기본 / celery 선택)
│  ├─ processing.py      # Write Path: parse→validate→Parquet 변환→적재
│  ├─ query.py           # Read Path: Iceberg 프루닝(PyArrow) + DuckDB in-memory 집계
│  ├─ routers/
│  │  ├─ ingest.py       # POST /ingest, GET /ingest/status/{id}
│  │  ├─ agent.py        # GET/POST /agent/tools/*
│  │  └─ health.py       # GET /health, /info
│  └─ static/            # Swagger UI·ReDoc 에셋(CDN 미사용·오프라인)
├─ tests/test_api.py     # E2E 스모크 테스트(추가 서비스 불필요)
├─ sample_data/          # 테스트용 CSV
├─ requirements.txt
├─ pytest.ini            # pytest 설정
├─ setup.sh              # venv 생성 + 의존성 설치
├─ run.sh                # venv 기반 API 서버 기동 (.env 자동 로드)
├─ middleware.sh         # MinIO+Celery 기동/종료/상태 (.env 기반)
├─ .env.example
└─ .venv/                # 가상환경 (git 제외)
```

> **바로 테스트 가능 설계**: 모든 외부 백엔드(MinIO/Redis/Celery/PostgreSQL)는 *선택*이며,
> 미설정 시 단일 프로세스에서 동작하는 폴백(로컬 FS · 인프로세스 워커 · SQLite · file:// Iceberg warehouse)을 사용한다.
> 운영 전환 시 환경변수만 바꾸면 실제 미들웨어로 연결된다.

## 9. 빠른 시작 (venv · 바로 테스트)

centos9(Linux) 안에서 실행한다. `C:\workspace` ↔ `/workspace` 가 마운트되어 있어 호스트에서 편집한 코드가 즉시 반영된다.
의존성은 **현재 폴더의 `.venv`(가상환경)** 에 격리 설치한다(시스템 Python 오염 방지).

```powershell
# 호스트에서 컨테이너 진입
docker exec -it centos9 bash
```

```bash
# (컨테이너 내부) /workspace/data-lake
cd /workspace/data-lake

# 0) venv 생성 + 의존성 설치 (최초 1회) — uv + Python 3.12.12
bash setup.sh
#   또는 수동(uv):
#   uv python install 3.12.12
#   uv venv --python 3.12.12 .venv && uv pip install -r requirements.txt

# (선택) 셸에 venv 활성화 — 이후 python/pytest/uvicorn 을 바로 사용
source .venv/bin/activate

# 1) 테스트 — 추가 서비스 없이 5개 통과
.venv/bin/pytest -q          # 활성화했다면 그냥 'pytest -q'
#   전체 테스트 시나리오(부하/적재현황/쿼리): docs/TEST_SCENARIOS.md

# 2) 서버 기동
bash run.sh                  # = .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

호스트 브라우저에서 **http://localhost:8000/docs** (Swagger UI) · **/redoc** (ReDoc) 로 접속한다.

> 📦 **오프라인 문서(C-1)**: Swagger UI·ReDoc 의 JS/CSS/favicon 을 CDN(jsdelivr 등) 대신
> `app/static/` 에 동봉해 로컬에서 서빙한다. 폐쇄망에서도 `/docs`·`/redoc` 가 그대로 동작한다.

```bash
# 3) 수집 → 조회 E2E (다른 셸에서)
curl -X POST http://localhost:8000/ingest \
     -F "file=@sample_data/production_2026-05-29.csv" -F "source_id=rpa-bot-1"
# -> {"task_id":"...","status":"accepted",...}

curl http://localhost:8000/ingest/status/<task_id>
# -> {"status":"done","rows":6,"partitions":3}

curl -X POST http://localhost:8000/agent/tools/query \
     -H "Content-Type: application/json" \
     -d '{"production_date":"2026-05-29","line_id":"FAB-1"}'
# -> {"found":true,"total_qty":1000,"total_defect_qty":20,"defect_rate":0.02,...}
```

### 선택 백엔드 연결 (운영 전환) — ✅ 실제 검증됨

**`.env` 채우고 두 줄이면** 로컬 폴백 → 실제 미들웨어(MinIO/Celery/PostgreSQL)로 전환된다. 코드 수정 불필요.

```bash
cp .env.example .env        # 접속정보 입력 (이 환경은 .env 가 이미 채워져 있음)

bash middleware.sh start    # .env 보고 MinIO + Celery 워커 기동 (Redis 는 네이티브 상시)
bash run.sh                 # API 기동 (.env 자동 로드)

bash middleware.sh status   # Redis/MinIO/Celery/Postgres 상태 한눈에
bash middleware.sh stop     # 미들웨어 종료
```

`middleware.sh` 는 `.env` 의 `STORAGE_BACKEND`/`TASK_BACKEND` 를 보고 필요한 것만 기동한다
(예: `STORAGE_BACKEND=local` 이면 MinIO 건너뜀). `run.sh`/`middleware.sh` 모두 `.env` 를 자동 로드하므로
**API 와 워커가 동일 설정을 공유**한다.

> **접속 주의 (검증 결과)**
> - **Redis · MinIO** = centos9 안에서 네이티브 구동 → `localhost`.
> - **PostgreSQL** = *별도 컨테이너(postgres16)*. 같은 user-defined network(`lake`)에 묶어
>   **이름으로 접속**(`postgres16:5432`)하도록 구성 완료 → 컨테이너 IP 변동과 무관. (scram 비밀번호 인증)
>   ```bash
>   docker network create lake
>   docker network connect lake centos9 && docker network connect lake postgres16
>   # → POSTGRES_DSN=postgresql://flopi_adm:****@postgres16:5432/flopi
>   ```
> - 잘못된 접속정보는 **조용히 폴백하지 않고 기동 시 예외**로 드러난다(설정 오류 가시화).

---

## 10. 구축 마일스톤

| 단계 | 산출물 | 상태 |
|---|---|---|
| M1 | 미들웨어 기동(PostgreSQL/Redis/MinIO) | ✅ Redis·MinIO 네이티브 가동, PostgreSQL(`lake` 네트워크) 연결 검증 |
| M2 | Ingestion API + 스토리지 적재 | ✅ `POST /ingest` + 로컬/원본 보존 |
| M3 | 비동기화(워커) | ✅ 인프로세스 워커 / Celery 전환 가능 |
| M4 | Iceberg 테이블 + 카탈로그 | ✅ PyIceberg 적재(MinIO/S3) + PostgreSQL SQL 카탈로그 + **스키마 진화** |
| M5 | Tool API 에이전트 연동 | ✅ `/agent/tools/*` 파티션 프루닝 조회 + 동적 스키마 |
| M6 | 운영 배포(네이티브 systemd 전환) | ⬜ |
| M7 | (선택) Milvus 유사도 검색 | ⬜ |

> M2~M5는 개발용 폴백 백엔드로 **기능 검증 완료**. 운영 미들웨어(MinIO/Celery/PostgreSQL) 연결은 환경변수 전환으로 활성화한다(§9).

---

## 11. 제약 조건

| ID | 제약 |
|---|---|
| C-1 | **Air-gapped** — 외부 인터넷·퍼블릭 클라우드 연동 불가, 사내 인프라 100% 독립 동작 |
| C-2 | **OSS only** — 100% 오픈소스(무료 라이선스)만 사용 |
| C-3 | **비동기 처리** — 대용량 파일 유입 시 타임아웃 방지(Queue-Worker 패턴 강제) |
| C-4 | **컨테이너 미사용(네이티브)** — 미들웨어/앱은 OS 네이티브 프로세스로 구동. (개발 시 Windows 한계로 Linux만 컨테이너로 확보, 그 안은 네이티브) |

---

## 저장소

- Git: `https://github.com/RudiMartin2020/data-lake.git`
- 문서: [`docs/`](docs/)
