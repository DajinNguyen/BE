#!/usr/bin/env bash
# DB 와 업로드 사진을 deploy/backups/ 에 백업하고, 14일이 지난 백업은 지운다.
# 사용: cd backend/deploy && ./backup.sh   (cron 으로 매일 돌리는 걸 권해요 — README 참고)
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
stamp=$(date +%F-%H%M)

docker compose exec -T db pg_dump -U dajin dajin | gzip > "backups/db-$stamp.sql.gz"
docker run --rm -v dajin_uploads:/data:ro -v "$PWD/backups":/out alpine \
  tar czf "/out/uploads-$stamp.tar.gz" -C /data .

find backups -type f -mtime +14 -delete
echo "백업 완료: backups/db-$stamp.sql.gz, backups/uploads-$stamp.tar.gz"
