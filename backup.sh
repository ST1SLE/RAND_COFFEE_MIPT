#!/bin/bash
set -euo pipefail

BACKUP_DIR="$HOME/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

echo "=== Backup started at $(date) ==="

# Дамп БД из Docker контейнера
docker compose -f "$HOME/randomcoffeeMIPT/docker-compose.yml" \
  exec -T db pg_dump -Fc -U coffee_bot_user coffee_bot_db \
  > "$BACKUP_DIR/db_${TIMESTAMP}.dump"

SIZE=$(du -h "$BACKUP_DIR/db_${TIMESTAMP}.dump" | cut -f1)
echo "Backup saved: db_${TIMESTAMP}.dump ($SIZE)"

# Удаляем бэкапы старше KEEP_DAYS дней
find "$BACKUP_DIR" -name "db_*.dump" -mtime +$KEEP_DAYS -delete
echo "Old backups (>$KEEP_DAYS days) cleaned up."
echo "=== Backup completed ==="
