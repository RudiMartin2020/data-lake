#!/usr/bin/env bash
# 미들웨어(.env 기반) 기동/종료/상태 — centos9(Linux) 안에서 실행
#
#   bash middleware.sh start     # STORAGE_BACKEND=minio 면 MinIO, TASK_BACKEND=celery 면 워커 기동
#   bash middleware.sh stop
#   bash middleware.sh status
#
# Redis 는 OS 네이티브 서비스로 이미 떠 있다고 가정한다(redis-server).
# PostgreSQL 은 별도 컨테이너이므로 여기서 관리하지 않는다(POSTGRES_DSN 으로 접속만).
set -u
cd "$(dirname "$0")"
ROOT="$(pwd)"

# --- .env 로드(있으면) ---
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

STORAGE_BACKEND="${STORAGE_BACKEND:-local}"
TASK_BACKEND="${TASK_BACKEND:-inprocess}"
MINIO_BIN="${MINIO_BIN:-/workspace/offline_repo/redis/minio}"
MINIO_DATA_DIR="${MINIO_DATA_DIR:-$ROOT/data/minio}"
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

minio_running() { curl -sf "http://${MINIO_ENDPOINT:-localhost:9000}/minio/health/live" >/dev/null 2>&1; }
celery_running() { pgrep -f "celery -A app.tasks" >/dev/null 2>&1; }
redis_running()  { redis-cli ${REDIS_CLI_ARGS:-} ping >/dev/null 2>&1; }

start() {
    echo "[*] Redis: $(redis_running && echo 'UP (네이티브)' || echo 'DOWN — redis-server 를 먼저 기동하세요')"

    if [ "$STORAGE_BACKEND" = "minio" ]; then
        if minio_running; then
            echo "[*] MinIO: 이미 UP (${MINIO_ENDPOINT:-localhost:9000})"
        else
            echo "[*] MinIO 기동: $MINIO_BIN -> $MINIO_DATA_DIR"
            mkdir -p "$MINIO_DATA_DIR"
            MINIO_ROOT_USER="${MINIO_ACCESS_KEY:-minioadmin}" \
            MINIO_ROOT_PASSWORD="${MINIO_SECRET_KEY:-minioadmin}" \
            nohup "$MINIO_BIN" server "$MINIO_DATA_DIR" \
                --address ":9000" --console-address ":9001" \
                >"$RUN_DIR/minio.log" 2>&1 &
            echo $! > "$RUN_DIR/minio.pid"
            for i in $(seq 1 30); do minio_running && break; sleep 0.5; done
            minio_running && echo "    -> UP (console :9001)" || { echo "    -> 실패"; tail -5 "$RUN_DIR/minio.log"; }
        fi
    else
        echo "[*] MinIO: 건너뜀 (STORAGE_BACKEND=$STORAGE_BACKEND)"
    fi

    if [ "$TASK_BACKEND" = "celery" ]; then
        if celery_running; then
            echo "[*] Celery: 이미 실행 중"
        else
            echo "[*] Celery 워커 기동"
            nohup .venv/bin/celery -A app.tasks:celery_app worker --pool=solo -l info \
                >"$RUN_DIR/celery.log" 2>&1 &
            echo $! > "$RUN_DIR/celery.pid"
            sleep 4
            celery_running && echo "    -> UP" || { echo "    -> 실패"; tail -8 "$RUN_DIR/celery.log"; }
        fi
    else
        echo "[*] Celery: 건너뜀 (TASK_BACKEND=$TASK_BACKEND)"
    fi
}

stop() {
    if [ -f "$RUN_DIR/celery.pid" ]; then kill "$(cat "$RUN_DIR/celery.pid")" 2>/dev/null; rm -f "$RUN_DIR/celery.pid"; fi
    pkill -f "celery -A app.tasks" 2>/dev/null
    if [ -f "$RUN_DIR/minio.pid" ]; then kill "$(cat "$RUN_DIR/minio.pid")" 2>/dev/null; rm -f "$RUN_DIR/minio.pid"; fi
    pkill -f "$MINIO_BIN server" 2>/dev/null
    echo "[*] Celery/MinIO 종료 요청 완료 (Redis 는 건드리지 않음)"
}

status() {
    echo "STORAGE_BACKEND=$STORAGE_BACKEND  TASK_BACKEND=$TASK_BACKEND  CATALOG_BACKEND=${CATALOG_BACKEND:-sqlite}"
    echo "  Redis : $(redis_running && echo UP || echo DOWN)"
    echo "  MinIO : $(minio_running && echo "UP (${MINIO_ENDPOINT:-localhost:9000})" || echo DOWN)"
    echo "  Celery: $(celery_running && echo UP || echo DOWN)"
    if [ -n "${POSTGRES_DSN:-}" ]; then
        .venv/bin/python - <<PY 2>/dev/null || echo "  Postgres: DOWN (접속 실패)"
import psycopg, os
with psycopg.connect(os.environ["POSTGRES_DSN"], connect_timeout=4) as c:
    c.execute("select 1"); print("  Postgres: UP")
PY
    fi
}

case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    restart) stop; sleep 1; start ;;
    status) status ;;
    *) echo "사용법: bash middleware.sh [start|stop|restart|status]"; exit 1 ;;
esac
