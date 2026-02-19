#!/bin/bash
set -euo pipefail

BACKUP_DIR="/home/deployer/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

echo "=== Backup started at $(date) ==="

# Дамп БД из Docker контейнера
PROJECT_ROOT="/home/deployer/RAND_COFFEE_MIPT"

docker compose -f "$PROJECT_ROOT/docker-compose.yml" \
  exec -T db pg_dump -Fc -h localhost -U coffee_bot_user coffee_bot_db \
  > "$BACKUP_DIR/db_${TIMESTAMP}.dump"

SIZE=$(du -h "$BACKUP_DIR/db_${TIMESTAMP}.dump" | cut -f1)
echo "Backup saved: db_${TIMESTAMP}.dump ($SIZE)"

# Удаляем бэкапы старше KEEP_DAYS дней
find "$BACKUP_DIR" -name "db_*.dump" -mtime +$KEEP_DAYS -delete
echo "Old backups (>$KEEP_DAYS days) cleaned up."
echo "=== Backup completed ==="
