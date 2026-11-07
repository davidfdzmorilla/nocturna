# ADR 0048 · Endurecimiento para exposición pública

Fecha: 2026-11-06 · Estado: aceptado · Tarea: T109 · Relacionados: ADR 0044, 0047

## Contexto

ADR 0044 (D4) fija que solo la web es pública y que la API queda en red interna. T107 (ADR 0047) cumplió la parte de red: la API y PostgreSQL no publican puertos y están en la red `internal`. Quedaba lo que la API puede hacer una vez alcanzada:

- La conexión de la API tenía permisos de escritura. ADR 0047 lo prevé: T109 daría a la API un rol sin escritura.
- `/docs`, `/redoc` y `/openapi.json` estaban encendidos. Verificado en T50 que no filtran datos internos, pero la decisión de desactivarlos era de despliegue y no estaba tomada (OD T50).
- La web respondía 200 con aviso en todas las ramas de error (T51), incluidas las de recurso inexistente, y las páginas de error se indexaban igual.

## Decisión

1. **Rol de solo lectura `nocturna_ro`.** `deploy/postgres/api-readonly-role.sql` lo crea o lo reconfigura de forma idempotente. `deploy/postgres/10-api-readonly-role.sh` lo aplica con `psql`. initdb lo ejecuta en el primer arranque del volumen. Se reaplica con:

   ```
   docker compose -f deploy/compose.yml exec -T postgres sh /docker-entrypoint-initdb.d/10-api-readonly-role.sh
   ```

   El rol tiene `LOGIN` y ningún otro atributo: no es superusuario y no tiene `CREATEDB`, `CREATEROLE`, `REPLICATION` ni `BYPASSRLS`. Privilegios: `CONNECT` sobre la base, `USAGE` sobre `public`, `SELECT` sobre las tablas existentes y, con `ALTER DEFAULT PRIVILEGES FOR ROLE nocturna`, sobre las que cree el propietario después. Nada más.

2. **Solo lectura en el rol y en la sesión.** `ALTER ROLE nocturna_ro SET default_transaction_read_only = on`. La API abre sus conexiones con `default_transaction_read_only=on` (`create_db_engine(read_only=True)` en `api/deps.py`). Vale en local y en producción. Son dos barreras: la sesión rechaza escrituras aunque se abra con otro rol.

3. **Base de datos.** `REVOKE TEMPORARY ON DATABASE nocturna FROM PUBLIC`.

4. **Contraseña.** `NOCTURNA_API_DB_PASSWORD` va en `deploy/.env`, generada con `openssl rand -hex 32` y distinta de `POSTGRES_PASSWORD`. El contenedor de PostgreSQL la recibe por entorno. El script la valida con `[A-Za-z0-9]` y la pasa a `psql` en `PGOPTIONS` como GUC `nocturna.ro_password`. El SQL la lee con `current_setting` y la usa en `ALTER ROLE … PASSWORD` con `%L`. Nunca va en argv.

5. **Migraciones con el propietario.** Servicio `migrate` con perfil `ops`, misma imagen que la API, conexión como `nocturna`:

   ```
   docker compose -f deploy/compose.yml --profile ops run --rm migrate
   ```

   Sustituye a `run --rm api alembic upgrade head` de ADR 0047, decisión 10. La API sigue sin migrar al arrancar.

6. **Documentación de la API apagada en producción.** `NOCTURNA_API_DOCS_ENABLED=false` en `deploy/compose.yml`: `/docs`, `/redoc` y `/openapi.json` no existen. Por defecto están encendidos (`Settings.api_docs_enabled = True`), para el desarrollo local.

7. **Healthcheck de PostgreSQL por TCP.** `pg_isready -h 127.0.0.1 -U nocturna -d nocturna`. Durante initdb, el servidor temporal solo escucha en el socket Unix. Con la comprobación por socket, el contenedor daría sano antes de que el script cree el rol, y `api` y `migrate` arrancarían contra un servidor que aún no lo tiene.

8. **Web: 404 reales y 200 con aviso.**
   - `not_found` (identificador que no es UUID, hallazgo inexistente o no publicado, semana fuera del archivo, ruta inexistente): `notFound()`, que da 404 con `app/not-found.tsx`.
   - `unavailable` (cualquier otro fallo de la API: caída, timeout, respuesta no válida): 200 con el aviso «No se ha podido cargar…» y `robots: { index: false, follow: false }` (`lib/noindex.ts`). En `/` solo si fallan las dos peticiones (resumen y feed). En `/archivo` y `/archivo/[semana]`, si falla su petición.
   - No se usa 500. Prueba previa en Next 15.5 standalone: lanzar el error desde la página con `error.tsx` da 500 con cuerpo vacío sin JS, porque `error.tsx` es un Client Component y sin JS no se hidrata. Sin banner ni texto.

9. **Peticiones compartidas.** Las funciones de lectura de `lib/api/client.ts` van envueltas en `React.cache`. `generateMetadata` y la página piden lo mismo, y la API recibe una sola petición por render.

10. **Texto de `app/error.tsx`** (T109 D5): h1 «No se ha podido cargar esta página», párrafo «Vuelve a intentarlo en unos minutos.», botón «Reintentar» y enlace al feed. No muestra el mensaje del error, `digest`, código de estado ni URL. Cubre los errores no previstos del render; la API caída va por la decisión 8.

11. **Contrato para T108.** Tras cada restauración de un `pg_dump` se reaplica `deploy/postgres/10-api-readonly-role.sh`. La restauración la hace el propietario `nocturna`.

## Alternativas descartadas

- **Migración de Alembic para crear el rol.** El rol es de clúster, y su contraseña no debe vivir en el esquema. Se crearía también en el Mac y en los tests.
- **Solo manual.** Se olvida en el primer arranque y tras cada restauración.
- **`run --rm -e NOCTURNA_DATABASE_URL=… api alembic upgrade head`.** La contraseña queda en el historial de la shell.
- **500 vía `error.tsx`.** Probado: Next 15.5 standalone devuelve 500 con cuerpo vacío sin JS (decisión 8).
- **Middleware con sondeo de `/health` para responder 503.** Next 15 no fija 503 en una página SSR sin hacks.

## Consecuencias

- **Gasto**: 0 tokens. No cambian `budget.py` ni `nightly_tokens`. La web no llama a Claude.
- **Volumen existente**: initdb solo corre con el volumen vacío. Una base creada antes de T109 no tiene `nocturna_ro` hasta que se reaplica el script (decisión 1). Sin ese paso, la API falla por autenticación: falla cerrada.
- **Riesgos**:
  - Si `ALTER ROLE … PASSWORD` fallara dentro del `EXECUTE`, el `CONTEXT` del error podría llevar la contraseña al log de PostgreSQL. Improbable. Mitigación posible: pasar la contraseña ya cifrada en SCRAM. No aplicada.
  - `nocturna_ro` conserva `CONNECT` y `TEMPORARY` sobre `postgres` y `template1`, como cualquier rol. Impacto despreciable.
  - `notFound()` de Next inyecta su propio `<meta name="robots" content="noindex">` en el 404. Lo hace el framework; la app no lo controla.
- **Verificación**: probado en local con los tests de T109 y con el compose de pruebas (`-m docker`). No verificado en el VPS a 2026-11-06: queda en `docs/DESPLIEGUE.md`, pendiente del autor.
- **Documentos**: `docs/DESPLIEGUE.md` (pasos 4, 5, 8 y 9), `docs/ARCHITECTURE.md` (§ Despliegue) y `docs/TECHNICAL_DEBT.md`. Este ADR sustituye la decisión 10 de ADR 0047 en lo que toca a la migración; ADR 0047 no se edita.
