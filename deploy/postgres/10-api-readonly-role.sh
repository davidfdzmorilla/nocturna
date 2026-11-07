#!/bin/sh
# Crea o reaplica el rol de solo lectura de la API (T109).
# Lo lanza initdb y se puede reaplicar con:
#   docker compose -f deploy/compose.yml exec -T postgres sh /docker-entrypoint-initdb.d/10-api-readonly-role.sh
set -eu

if [ -z "${NOCTURNA_API_DB_PASSWORD:-}" ]; then
  echo "NOCTURNA_API_DB_PASSWORD vacia" >&2
  exit 1
fi
case "$NOCTURNA_API_DB_PASSWORD" in
  *[!A-Za-z0-9]*)
    echo "NOCTURNA_API_DB_PASSWORD solo admite [A-Za-z0-9]" >&2
    exit 1
    ;;
esac

PGOPTIONS="-c nocturna.ro_role=nocturna_ro -c nocturna.owner_role=$POSTGRES_USER -c nocturna.ro_password=$NOCTURNA_API_DB_PASSWORD" \
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /nocturna/api-readonly-role.sql
