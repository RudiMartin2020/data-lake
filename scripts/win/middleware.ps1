# Windows 런처 — 미들웨어(MinIO+Celery) 기동/종료/상태.
#   사용:  .\scripts\win\middleware.ps1 start | stop | status | restart
param(
    [string]$Action = "status",
    [string]$Container = "centos9",
    [string]$ProjectDir = "/workspace/data-lake"
)
docker exec $Container bash -lc "cd $ProjectDir && bash middleware.sh $Action"
