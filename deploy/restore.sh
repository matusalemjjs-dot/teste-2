#!/bin/sh
# Restaura um backup em um banco (APAGA o conteúdo atual das tabelas restauradas).
# Uso: restore.sh <arquivo.dump> <banco>     (variáveis PG* definem a conexão)
# Teste de restauração recomendado: restaurar num banco vazio separado e comparar contagens (ver IMPLANTACAO.md).
set -eu
ARQ="${1:?informe o arquivo .dump}"
DB="${2:?informe o banco de destino}"
pg_restore --clean --if-exists --no-owner --dbname="$DB" "$ARQ"
psql --dbname="$DB" -tAc "SELECT 'tabelas: ' || count(*) FROM information_schema.tables WHERE table_schema='public'"
