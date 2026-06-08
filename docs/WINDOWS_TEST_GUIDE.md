# Windows 테스트 가이드

Windows(호스트)에서 본 프로젝트를 테스트하는 방법. 앱은 **centos9(Linux) 컨테이너 안**에서 돌고,
Windows는 PowerShell로 **호출**만 합니다(`.venv`가 Linux 전용이라 Windows 직접 실행 불가).

```
PowerShell(.ps1)  ──docker exec──▶  centos9(Linux)  ──▶  uvicorn(.venv :8000)
   [Windows 진입점]                                        [실제 구동]
                         localhost:8000 / 15432 로 호스트에서 접속
```

---

## 0. 사전 준비

```powershell
# Docker Desktop 실행 중 + 컨테이너 확인
docker ps --format "{{.Names}}  {{.Status}}  {{.Ports}}"
# centos9  (8000/6379/9000/9001) , postgres16 (5432) , pgproxy(15432) 가 보여야 함
```

| 컨테이너 | 역할 | 호스트 포트 |
|---|---|---|
| centos9 | 앱 + Redis + MinIO (Linux 박스) | 8000, 6379, 9000, 9001 |
| postgres16 | PostgreSQL | (5432는 네이티브 PG18이 점유) |
| pgproxy | postgres16 → 호스트 노출 | **15432** (DBeaver용) |

> ⚠️ 호스트 5432는 **Windows 네이티브 PostgreSQL 18**이 점유 중. 우리 DB는 **15432**로 접속.

---

## 1. 최초 셋업 (한 번)

```powershell
cd C:\workspace\data-lake
.\scripts\win\setup.ps1        # uv + Python 3.12.12 venv + 의존성 설치
```

## 2. 기동

```powershell
.\scripts\win\middleware.ps1 start    # MinIO + Celery (Redis는 상시)
.\scripts\win\middleware.ps1 status   # Redis/MinIO/Celery/Postgres 모두 UP 확인
.\scripts\win\run.ps1                 # API 서버(:8000) — 이 창은 로그가 흐름(Ctrl+C로 종료)
```

> `run.ps1`은 서버가 떠 있는 동안 창을 점유합니다. **테스트는 새 PowerShell 창**에서 진행하세요.

## 3. 브라우저 접속

| 화면 | URL |
|---|---|
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| 헬스/구성 | http://localhost:8000/health · /info |
| 메트릭 | http://localhost:8000/metrics |

---

## 4. PowerShell 로 API 테스트

### 4-1. 헬스 / 구성

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/info
```

### 4-2. 적재 (파일 업로드 — `curl.exe` 사용)

PowerShell의 `Invoke-RestMethod`는 multipart가 번거로워 **Windows 내장 `curl.exe`** 를 권장합니다.

```powershell
cd C:\workspace\data-lake
curl.exe -s -X POST http://localhost:8000/ingest `
  -F "file=@sample_data\production_2026-05-29.csv" `
  -F "source_id=win-1"
# -> {"task_id":"...","status":"accepted",...}
```

### 4-3. 상태 조회

```powershell
$tid = "여기에_task_id"
Invoke-RestMethod http://localhost:8000/ingest/status/$tid
# status 가 done 이 될 때까지 반복
```

### 4-4. 에이전트 쿼리 (JSON)

```powershell
$body = '{"production_date":"2026-05-29","line_id":"FAB-1"}'
Invoke-RestMethod -Method Post -Uri http://localhost:8000/agent/tools/query `
  -ContentType "application/json" -Body $body
# -> total_qty=1000, defect_rate=0.02 ...
```

### 4-5. 스키마 / 메트릭

```powershell
Invoke-RestMethod http://localhost:8000/agent/tools/schema
(Invoke-WebRequest http://localhost:8000/metrics).Content
```

### 4-6. 한 번에 적재→조회 (복붙용)

```powershell
cd C:\workspace\data-lake
$r   = curl.exe -s -X POST http://localhost:8000/ingest -F "file=@sample_data\production_2026-05-29.csv" -F "source_id=win-e2e" | ConvertFrom-Json
$tid = $r.task_id
do { Start-Sleep -Milliseconds 500; $s = Invoke-RestMethod http://localhost:8000/ingest/status/$tid } until ($s.status -in @("done","failed"))
$s
Invoke-RestMethod -Method Post -Uri http://localhost:8000/agent/tools/query -ContentType "application/json" -Body '{"production_date":"2026-05-29","line_id":"FAB-1"}'
```

---

## 5. 데이터 직접 확인 (DBeaver)

| 필드 | 값 |
|---|---|
| Host | `localhost` |
| **Port** | **15432** (5432 아님) |
| Database | `flopi` |
| User | `flopi_adm` |
| Password | `flopi1234` |

접속 후 `flopi → public`:
- `iceberg_tables` — Iceberg 카탈로그(테이블 등록·메타데이터 위치)
- `ingestions` — 적재 이력

```sql
SELECT table_namespace, table_name FROM iceberg_tables;
SELECT source_id, rows, partitions, status, created_at FROM ingestions ORDER BY created_at DESC;
```

### MinIO 콘솔 (오브젝트 확인)
브라우저 http://localhost:9001 — `minioadmin` / `minioadmin`
→ `warehouse/iceberg/lake/production/` 에 parquet(데이터) + json(메타) + avro(매니페스트)

---

## 6. 자동 테스트 (pytest)

```powershell
docker exec centos9 bash -lc "cd /workspace/data-lake && .venv/bin/pytest -q"
# -> 10 passed (추가 서비스 없이 폴백 백엔드로 동작)
```

---

## 7. 종료 / 정리

```powershell
# run.ps1 창에서 Ctrl+C 로 API 종료, 그 후:
.\scripts\win\middleware.ps1 stop     # MinIO + Celery 종료 (Redis/postgres16/pgproxy 는 유지)
```

---

## 8. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `localhost:8000` 접속 안 됨 | API 미기동 → `.\scripts\win\run.ps1`. 또는 컨테이너 중지 → `docker start centos9` |
| DBeaver 5432 인증 실패 | 5432는 **네이티브 PG18**. **15432**로 접속하고 user는 `flopi_adm`(admin 아님) |
| 적재가 `accepted`에서 안 변함 | Celery 미기동 → `.\scripts\win\middleware.ps1 status` 후 `start` |
| `middleware status`에 MinIO DOWN | `.\scripts\win\middleware.ps1 start` (MinIO 바이너리 기동) |
| `curl.exe` 인식 안 됨 | Windows 10 1803+ 내장. 구버전이면 `Invoke-WebRequest` 사용 |
| 적재 `duplicate` 만 나옴 | 동일 파일 재적재(멱등성). 카탈로그 비우거나 파일 내용 변형 |
| 쿼리 500 + 로그에 `metadata.json … No such file` | 카탈로그(PostgreSQL)↔스토리지(MinIO) 불일치(한쪽만 비움). 아래 **리셋**으로 복구 |
| 컨테이너 이름 다름 | `.ps1 -Container <이름>` 파라미터로 지정 |

### 완전 리셋 (불일치 복구 / 깨끗한 재시작)
PostgreSQL(audit + Iceberg 카탈로그)과 MinIO(warehouse)를 **함께** 비워야 정합이 맞습니다.
```powershell
# 1) PostgreSQL: 이력 + Iceberg 카탈로그 행 제거
docker exec -e PGPASSWORD=flopi1234 postgres16 psql -h 127.0.0.1 -U flopi_adm -d flopi `
  -c "TRUNCATE ingestions; DELETE FROM iceberg_tables WHERE table_name='production'; DELETE FROM iceberg_namespace_properties WHERE namespace='lake';"
# 2) MinIO: warehouse 비우기
docker exec centos9 bash -lc "cd /workspace/data-lake && .venv/bin/python - <<'PY'
from minio import Minio
from minio.deleteobjects import DeleteObject
c=Minio('localhost:9000',access_key='minioadmin',secret_key='minioadmin',secure=False)
objs=[DeleteObject(o.object_name) for o in c.list_objects('warehouse',recursive=True)]
list(c.remove_objects('warehouse',objs)); print('cleared')
PY"
```
> 교훈: **카탈로그와 스토리지는 항상 같이 비우세요.** 한쪽만 지우면 위 500 오류가 납니다.

---

## 빠른 참조

```powershell
# 기동
cd C:\workspace\data-lake
.\scripts\win\middleware.ps1 start ; .\scripts\win\run.ps1

# 접속
start http://localhost:8000/docs

# 테스트(새 창)
Invoke-RestMethod http://localhost:8000/info
```
