#!/bin/sh
# Backup lógico do Postgres (formato custom, comprimido) com retenção de BACKUP_KEEP_DAYS dias.
# Uso: backup.sh <pasta-de-destino>   (variáveis PG* definem a conexão)
set -eu
DEST="${1:?informe a pasta de destino}"
KEEP="${BACKUP_KEEP_DAYS:-7}"
mkdir -p "$DEST"
ARQ="$DEST/autoclip-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --file="$ARQ.tmp"
pg_restore --list "$ARQ.tmp" > /dev/null   # confere que o arquivo é legível antes de aceitar
mv "$ARQ.tmp" "$ARQ"
find "$DEST" -name 'autoclip-*.dump' -mtime "+$KEEP" -delete
echo "backup ok: $ARQ ($(du -h "$ARQ" | cut -f1))"
