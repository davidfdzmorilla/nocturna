# ADR 0047 · Imágenes y compose de producción para web y API

Fecha: 2026-11-06 · Estado: aceptado · Tarea: T107 · Relacionados: ADR 0011, 0044

## Contexto

ADR 0044 (A) publica la web en un VPS con Docker y Traefik. El pipeline se queda en el Mac. Hasta T107 no existían imagen ni compose de producción.

`claude-agent-sdk` estaba en las dependencias principales de `backend/pyproject.toml`. Cualquier instalación de la API lo traía, con el paquete que contiene el CLI de Claude. El criterio (c) de ADR 0044 exige cero credenciales de Claude en el VPS y en las imágenes, comprobado por test e inspección.

La regla 3 de `.claude/hooks/guard-bash.sh` bloqueaba comandos de infraestructura de producción. Era de la fase 1 y ADR 0044 autoriza el despliegue en la fase 3.

El plan de T107 tenía ocho decisiones (D1–D8). El autor resolvió D4–D8 el 2026-11-06 con la opción recomendada en cada caso. D1–D3 (dominio, VPS y correo de Let's Encrypt) siguen abiertas en `OPEN_DECISIONS.md`.

## Decisión

1. **La API no instala el SDK del agente.** `claude-agent-sdk` pasa del grupo de dependencias principal al grupo `pipeline`. Ese grupo es el de por defecto en el Mac (`[tool.uv] default-groups = ["dev", "pipeline"]`), así que el pipeline local no cambia. La imagen de la API instala `uv sync --frozen --no-default-groups --group serve`. El grupo `serve` contiene `uvicorn`. El grupo `dev` conserva `uvicorn` para `uvicorn --reload` en local. `uv.lock` refleja los grupos.
2. **Imagen de la API** (`backend/Dockerfile`, contexto: raíz del repo). Base `python:3.12.12-slim-bookworm`. Dos etapas: la de construcción instala dependencias con uv 0.9.0 y la final copia el entorno. La etapa final añade `tzdata` (cierra la deuda de `infrastructure/clock.py`), crea el usuario de sistema `app` con uid 10001 y define un `HEALTHCHECK` contra `/health`. Arranca con `uvicorn nocturna.api.app:create_app --factory`. Copia `config/pipeline.toml`, que la API lee para `window.timezone`.
3. **Imagen web** (`web/Dockerfile`, contexto: `web/`). Next.js con `output: "standalone"`, sobre `node:24.14.0-bookworm-slim`. Tres etapas (dependencias, construcción, ejecución). Usuario `node`. `HEALTHCHECK` sobre `/`.
4. **Contextos de construcción en lista blanca.** `.dockerignore` (raíz) y `web/.dockerignore` empiezan con `*`. No reincluyen `.env`, `.claude`, logs, `node_modules`, `.next`, copias de seguridad ni tests. Un test lo congela.
5. **Compose de producción aparte**: `deploy/compose.yml`. El `docker-compose.yml` de la raíz sigue siendo solo PostgreSQL local. Servicios: `traefik`, `web`, `api` y `postgres` (`postgres:16-alpine`).
6. **Proxy**: Traefik v3 (`traefik:v3.2.3`) con proveedor de fichero (`deploy/traefik/dynamic.yml`, router por `Host(\`{{ env "NOCTURNA_DOMAIN" }}\`)`). Sin proveedor Docker, por tanto sin `docker.sock`, y sin dashboard. Entrypoints 80 y 443; el 80 redirige a 443. Certificado por TLS-ALPN-01 de Let's Encrypt (resolutor `le`). La CA es configurable con `NOCTURNA_ACME_CA_SERVER`; para pruebas, la de staging.
7. **Redes y puertos.** `edge` une Traefik y web. `internal` (`internal: true`) une web, API y PostgreSQL. Solo Traefik publica puertos (80 y 443). La web es el único servicio al que llega el proxy. La API y PostgreSQL no publican puertos (ADR 0044, D4).
8. **Secretos** solo en `deploy/.env` del VPS. El fichero está ignorado por git y `deploy/env.example` no lleva valores. Ningún servicio usa `env_file`; el compose interpola `NOCTURNA_DOMAIN`, `NOCTURNA_ACME_EMAIL`, `NOCTURNA_ACME_CA_SERVER` y `POSTGRES_PASSWORD` desde ese fichero.
9. **Construcción en el VPS** (T107 D4). Se hace con `git clone` y `docker compose -f deploy/compose.yml build`. No hay registro de imágenes ni credenciales nuevas.
10. **Esquema de la base a mano** (T107 D5). `docker compose -f deploy/compose.yml run --rm api alembic upgrade head`, una vez por cambio de esquema. La migración no corre al arrancar la API.
11. **Regla 3 del guard de Bash retirada** por decisión del autor, 2026-11-06. Las reglas 1, 2, 4 y 5 siguen vigentes.
12. **Tests de ausencia de credenciales**:
    - `backend/tests/test_deploy_no_claude_credentials.py`: 28 casos estáticos que corren en `pytest` normal. Dos de ellos ejecutan `docker compose config` y se saltan sin Docker. Escanean los ficheros de despliegue y las listas blancas, comprueban que el cierre del `uv.lock` de `serve` no contiene el SDK ni `mcp`, y comprueban que `deploy/.env` está ignorado.
    - `backend/tests/deploy/test_images.py`: 6 casos con `-m docker` y `NOCTURNA_CHECK_IMAGES=1`. Construyen las imágenes con etiquetas propias y un proyecto de compose aislado (`nocturna-t107-test`). Comprueban que no hay ficheros `claude` ni `anthropic` en el sistema de ficheros, que la API no importa el SDK ni `mcp` y no tiene el binario `claude`, que la imagen no declara usuario root, que el entorno no lleva variables `ANTHROPIC*` ni `CLAUDE*`, que en el compose resuelto solo el proxy publica puertos, y un humo de arranque (migración, `postgres`, `api` y `web` sanos, `web` responde 200). El proxy no arranca en el humo.

## Alternativas descartadas

- **Import perezoso del SDK en la API.** No impide instalarlo: el paquete y su CLI siguen en la imagen.
- **`uv sync --no-install-package claude-agent-sdk`.** Deja `mcp` instalado y obliga a nombrarlo en el Dockerfile. El cierre de dependencias queda a mano.
- **Proveedor Docker de Traefik con `docker.sock` montado.** Da al proxy control del demonio de Docker. Un router de fichero basta.
- **Registro de imágenes (GHCR).** Exige token y publicar imágenes. La construcción en el VPS no necesita credenciales nuevas.
- **`docker save | ssh ... docker load` desde el Mac.** Depende de que el Mac esté encendido y construye en arm64 para un VPS que puede ser amd64.
- **Migración automática al arrancar la API.** T109 dará a la API un rol de solo lectura; la migración necesita escritura.
- **BasicAuth en Traefik mientras no haya textos publicables** (T107 D7). La web queda pública sin contenido. No se recomienda.

## Consecuencias

- **Gasto**: 0 tokens. No cambian `budget.py` ni `nightly_tokens`. No hay agentes ni CLI en el VPS.
- **Pipeline local**: `uv sync` en el Mac sigue instalando el SDK por `default-groups`. El comportamiento del pipeline no cambia.
- **Riesgos**:
  - La plantilla `{{ env "NOCTURNA_DOMAIN" }}` del proveedor de fichero no está verificada en el VPS. Si no resuelve, el router no existe y el proxy responde 404. Se verifica con `docs/DESPLIEGUE.md`, paso 8.
  - Las imágenes se han construido y probado solo en el Mac (arm64). El VPS construye las suyas (D4); esa construcción no se ha probado.
  - Versiones: `traefik:v3.2.3`, `python:3.12.12-slim-bookworm`, `node:24.14.0-bookworm-slim` y `ghcr.io/astral-sh/uv:0.9.0` están fijadas a etiqueta, sin digest. Hay que revisarlas en cada actualización. `postgres:16-alpine` no fija la versión menor: la imagen puede cambiar de 16.x sin aviso en el repo.
  - Dominio, VPS y correo de Let's Encrypt están pendientes (D1–D3). Sin ellos no hay verificación TLS. La URL con TLS es el criterio (a) de ADR 0044 y se mide en T110.
  - Los tests con `-m docker` necesitan Docker en la máquina que los lanza. No hay CI/CD.
- **Documentos**: `docs/DESPLIEGUE.md` (runbook del VPS); `docs/ARCHITECTURE.md`, sección de despliegue; `docs/TECHNICAL_DEBT.md` (tzdata saldada; `contract.test.ts` no afecta a la imagen).
