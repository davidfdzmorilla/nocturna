# ADR 0051 · Sincronización de datos del Mac al VPS

Fecha: 2026-11-09 · Estado: aceptado · Tarea: T108 · Relacionados: ADR 0044, 0045, 0047, 0048, 0050

## Contexto

ADR 0044 (D3) deja el pipeline en el Mac y la web y su base en un VPS. Los datos que muestra la web se producen en el Mac, así que tienen que llegar al VPS después de cada noche y de cada snapshot semanal del archivo. T107 dejó PostgreSQL vacío en el VPS. T109 (ADR 0048) dio a la API el rol `nocturna_ro`, sin escritura, y `pg_restore` con `--no-privileges` quita los `GRANT`: tras cada restauración hay que reaplicar ese rol (ADR 0048 §11).

Restricciones que rigen el diseño:

- El VPS no ejecuta pipeline ni tiene credenciales de Claude (ADR 0044, 0047).
- La sincronización no gasta tokens y no puede cambiar el código de la noche ni su causa en el informe matinal (ADR 0045).
- El Mac es de confianza; el VPS, no del todo: solo debe aceptar la copia de la base y nada más.

## Decisión

1. **Transporte.** `backend/scripts/sync-to-vps.sh` (Mac) hace `pg_dump -Fc --no-owner --no-privileges` dentro del contenedor local `nocturna-postgres`, calcula el sha256 y lee la revisión de Alembic, y envía el volcado por stdin por SSH. La clave es dedicada (ed25519, sin frase de paso, con `known_hosts` propio fijado). En el VPS, `authorized_keys` la confina con `restrict,command="…/deploy/sync/restore.sh"`: la clave solo puede lanzar esa orden. El cliente aporta `restore <sha256> <revisión>` en `SSH_ORIGINAL_COMMAND`, que `restore.sh` valida con una expresión anclada, sin evaluarla.
2. **Restauración (`deploy/sync/restore.sh`, VPS).** En este orden: bloqueo con `flock` (una restauración a la vez); validación de la orden; copia del stdin a un temporal con tope de 1 GiB; sha256; la revisión declarada debe ser la `head` única del servicio `migrate` (si no, falla cerrada); `pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --exit-on-error` con `lock_timeout=60s`; y reaplicación de `deploy/postgres/10-api-readonly-role.sh`.
3. **Usuario de restauración.** `nocturna-sync`, dedicado y en el grupo `docker`. Pertenecer a `docker` equivale a root en la práctica; la protección está en la clave, que solo ejecuta `restore.sh`.
4. **Alcance del volcado.** Completo, incluida `agent_calls` (D5). El riesgo que acarrea está en el modelo de amenaza.
5. **Fecha mostrada.** `GET /data-status` devuelve `data_updated_at = greatest(max(runs.finished_at), max(archive_snapshot.taken_at))`. El pie de la web la muestra en Europe/Madrid. Si la API no responde o la fecha es nula, el pie dice «Fecha de actualización no disponible». Que falle `/data-status` no añade `noindex`.
6. **Disparo.** `run-night-scheduled.sh` lo llama tras `evaluate-tensions`, y `archive-snapshot-scheduled.sh` tras el snapshot, ambos antes del informe matinal. Su código queda en `sync=` del fichero `.status`. No cambia el `exit_code` de la noche ni su causa. No se ejecuta con `--dry-run`, ni en las salidas 75, 76 y 77, que salen antes.
7. **Límites.** Tope total `NOCTURNA_SYNC_TIMEOUT_S`, 900 s por defecto. macOS no trae `timeout`: un vigilante mata el grupo de procesos del trabajo. `ConnectTimeout=30`, `ServerAliveInterval=15`, `ServerAliveCountMax=4`. Tope de restauración `NOCTURNA_SYNC_RESTORE_TIMEOUT_S` (600 s) y de `alembic heads` y de reaplicar el rol (120 s cada uno), en el VPS.
8. **Configuración.** Tres variables en las `EnvironmentVariables` de los plist de `run-night` y de `archive-snapshot`: `NOCTURNA_SYNC_TARGET`, `NOCTURNA_SYNC_KEY` y `NOCTURNA_SYNC_KNOWN_HOSTS`. Vacías o con el marcador de la plantilla (`__NOCTURNA_SYNC_*__`), la sincronización sale con 78 sin hacer nada. Quedan fuera de git y sin `.env` (D4).
9. **Cambio de esquema.** Orden: migrar el Mac, desplegar el VPS (la imagen de `migrate` debe tener la nueva `head`) y sincronizar. Si la revisión no coincide, la restauración se rechaza con código 12 y la base queda como estaba.
10. **Primera restauración.** Sustituye a `migrate` como paso previo (D11). `migrate` queda como paso opcional.

## Alternativas descartadas

- **rsync con `systemd.path` en el VPS.** Añade un disparador y un fichero persistente en el VPS que vigila la llegada de un volcado. Los datos cambian una vez por noche; el disparador no aporta nada y deja una copia de la base en disco fuera de todo control de retención.
- **scp con cron.** Desacopla la copia de la noche: el VPS restaura a una hora fija sin saber si el volcado es de esta noche, y deja el fichero sin verificar hasta entonces.
- **Marca de fecha escrita por la restauración** (una tabla `restored_at`). Diría cuándo se restauró, no cuándo se produjeron los datos. Una restauración repetida o a medias movería la fecha sin que hubiera datos nuevos. Se prefiere derivarla de `runs` y de `archive_snapshot`, que son datos del pipeline.
- **Dump parcial** (`--exclude-table-data=agent_calls` o una lista blanca de tablas). Una base sin las filas de `agent_calls` no es copia de la del Mac, y una lista blanca hay que mantenerla con cada tabla nueva. El autor aceptó el riesgo de exposición con el volcado completo (D5).

## Consecuencias

- Si la sincronización falla, la fecha del pie se queda atrasada. Es la única señal visible: el informe matinal la registra como «Sincronización con el VPS: fallida» sin cambiar la causa de la noche, y ninguna notificación avisa de varias noches seguidas sin sincronizar (TECHNICAL_DEBT).
- La restauración es idempotente: dos seguidas dejan el mismo estado. Lo comprueba `backend/tests/deploy/test_sync_integration_t108.py` con dos PostgreSQL desechables (`NOCTURNA_CHECK_SYNC=1 uv run pytest -m docker`): misma huella, un dump truncado no cambia nada, `nocturna_ro` sigue intacto tras restaurar y `/data-status` coincide con la base.
- El VPS necesita `flock` (util-linux) y `timeout` (coreutils), `docker compose` y el checkout con permisos para `nocturna-sync` (`docs/DESPLIEGUE.md`, § Sincronización de datos).
- El volcado de hasta 1 GiB pasa por `${TMPDIR:-/tmp}` del VPS. Si `/tmp` es tmpfs, ocupa RAM.
- No se guarda ningún volcado ni en el Mac ni en el VPS. No hay copias de seguridad de la base (TECHNICAL_DEBT).
- Nada del flujo contiene credenciales de Claude. El pie y la API no llaman a Claude (ADR 0044).

## Modelo de amenaza

- **Quien tenga la clave dedicada ejecuta SQL arbitrario en la base.** `pg_restore` corre como `nocturna`, que es el superusuario del contenedor de PostgreSQL. Con `COPY … TO PROGRAM`, puede ejecutar código dentro del contenedor de PostgreSQL. Es el diseño aceptado porque el Mac es de confianza. Si la IP pública del Mac es estable, valorar `from="<IP>"` en `authorized_keys` (OD T108).
- **La revisión que se compara es la que declara el cliente**, no la que contiene el dump. La comprobación evita un desajuste de despliegue; no protege contra un Mac comprometido.
- **`timeout` mata el cliente `docker compose`**, no necesariamente el `pg_restore` que sigue dentro del contenedor. Lo acotan la transacción única y `lock_timeout`. Como el `flock` se libera al salir de `restore.sh`, una restauración nueva podría coincidir con un `pg_restore` huérfano (TECHNICAL_DEBT).
- **Un fallo de sincronización no avisa.** Varias noches seguidas sin sincronizar solo se notan por la fecha del pie (relación con el vigía exterior de T105 y T110; TECHNICAL_DEBT).
- **El volcado completo expone al VPS** textos no publicados, `notes` y recuentos de tokens. Si el VPS se compromete, esos datos salen con él (D5, riesgo aceptado).
