# Despliegue en el VPS · runbook

Procedimiento de [ADR 0047](adr/0047-imagenes-y-compose-de-produccion.md) (T107), de [ADR 0044](adr/0044-fase-3.md), de [ADR 0048](adr/0048-endurecimiento-para-exposicion-publica.md) (T109) y de la sincronización de datos del Mac, [ADR 0051](adr/0051-sincronizacion-de-datos-mac-vps.md) (T108). Sin ejecutar en un VPS a 2026-11-09. Las decisiones pendientes del autor (dominio, VPS, correo, acceso al repositorio y sistema operativo) están en `OPEN_DECISIONS.md`, sección T107 (D1–D3, D9, D10).

## Qué hay en el VPS y qué no

- **Sí**: Traefik, la web, la API de lectura (con el rol `nocturna_ro`, solo lectura) y PostgreSQL 16, definidos en `deploy/compose.yml`.
- **No**: el pipeline, los agentes, el CLI de Claude ni ninguna credencial de Claude. El pipeline sigue en el Mac (ADR 0044).
- **La base empieza vacía.** Sus datos llegan desde el Mac con la sincronización (§ Sincronización de datos).
- **No automatizado aquí**: CI/CD, despliegue continuo y copias de seguridad (no hay copias de seguridad de la base; ver TECHNICAL_DEBT). La sincronización Mac → VPS está descrita al final de este documento.

Todos los comandos de compose de este documento usan `-f deploy/compose.yml`. El `docker-compose.yml` de la raíz es el local (solo PostgreSQL) y no se usa en el VPS.

## Antes de empezar

- Dominio para `NOCTURNA_DOMAIN` (T107 D1).
- Correo para Let's Encrypt, `NOCTURNA_ACME_EMAIL` (T107 D3). Va solo en `deploy/.env` del VPS.
- VPS en Hetzner (T107 D2). Recomendación: 2 vCPU, 4 GB de RAM y 40 GB de disco. El coste está pendiente.
- Acceso del VPS al repositorio para el `git clone` (T107 D9).
- Sistema operativo del VPS (T107 D10). Este runbook supone Ubuntu LTS.

## 1. Crear el VPS

1. Crear el servidor en Hetzner Cloud con la imagen del sistema operativo elegido y una clave SSH pública. No usar contraseña.
2. Cortafuegos: solo TCP 22, 80 y 443 abiertos. Ningún otro puerto de entrada.
3. Desactivar el acceso por contraseña en SSH. Entrar con un usuario que tenga `sudo`.
4. Anotar la IP pública (IPv4 y, si se usa, IPv6).

## 2. Instalar Docker

1. Instalar Docker Engine y el plugin `docker compose` desde el repositorio oficial de Docker, siguiendo su guía para el sistema elegido (paquetes `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin` y `docker-compose-plugin`).
2. No instalar Node, uv, Python del proyecto ni el CLI de Claude. Las imágenes lo llevan todo lo que necesitan.
3. Comprobar: `docker compose version`.

## 3. DNS

Registro A (y AAAA, si el VPS tiene IPv6) de `NOCTURNA_DOMAIN` hacia la IP del VPS. Comprobar con `dig +short <dominio>`: debe devolver la IP del VPS. Sin esa resolución, Let's Encrypt no emite el certificado.

## 4. Código y secretos

```
git clone <URL del repositorio> nocturna
cd nocturna
cp deploy/env.example deploy/.env
chmod 600 deploy/.env
git check-ignore deploy/.env        # debe imprimir deploy/.env
```

Editar `deploy/.env`:

- `NOCTURNA_DOMAIN`: el dominio, sin `https://`.
- `NOCTURNA_ACME_EMAIL`: el correo del autor.
- `POSTGRES_PASSWORD`: generar en el VPS con `openssl rand -hex 32`. No pegarla en ningún chat ni en ningún fichero del repo.
- `NOCTURNA_API_DB_PASSWORD`: generar en el VPS con `openssl rand -hex 32`, distinta de `POSTGRES_PASSWORD`. Es la contraseña del rol `nocturna_ro`, con la que la API lee la base (ADR 0048). El script la rechaza si no es alfanumérica.
- Primera pasada: descomentar `NOCTURNA_ACME_CA_SERVER=https://acme-staging-v02.api.letsencrypt.org/directory`. Staging no tiene límites de emisión que bloqueen las pruebas. Producción se activa en el paso 7.

## 5. Construir, arrancar y migrar

```
docker compose -f deploy/compose.yml build
docker compose -f deploy/compose.yml up -d
docker compose -f deploy/compose.yml --profile ops run --rm migrate
```

La migración es manual (T107 D5) y la ejecuta el servicio `migrate` con el propietario `nocturna` (T109, ADR 0048; sustituye a `run --rm api alembic upgrade head`). Desde T108 (D11), la primera restauración de la sincronización trae el esquema y sustituye a `migrate`, que queda como paso opcional. Sin restauración ni migración, la API no tiene tablas.

**Volumen de PostgreSQL ya existente.** initdb solo corre con el volumen vacío. Una base creada antes de T109 no tiene el rol `nocturna_ro`. Tras `up -d`, reaplicar el script:

```
docker compose -f deploy/compose.yml exec -T postgres sh /docker-entrypoint-initdb.d/10-api-readonly-role.sh
```

Sin ese paso la API falla por autenticación. Tras cada restauración de la sincronización, `restore.sh` lo reaplica solo (T108, ADR 0051); el comando anterior sirve para hacerlo a mano.

## 6. Verificar con el certificado de staging

El certificado de staging no es de confianza pública. Por eso las comprobaciones usan `-k`:

```
curl -skI https://$NOCTURNA_DOMAIN/                     # HTTP/2 200
docker compose -f deploy/compose.yml logs traefik | tail -n 50   # sin errores de ACME ni de plantilla
```

Confirmar que el emisor es el de staging:

```
openssl s_client -connect $NOCTURNA_DOMAIN:443 -servername $NOCTURNA_DOMAIN </dev/null 2>/dev/null | openssl x509 -noout -issuer
```

El emisor contiene `STAGING`. Si la web responde 404 del proxy, la plantilla `{{ env "NOCTURNA_DOMAIN" }}` de `deploy/traefik/dynamic.yml` no resolvió. Revisar `NOCTURNA_DOMAIN` en `deploy/.env` y los logs de Traefik.

## 7. Pasar de staging a producción

No repetir este paso en bucle: Let's Encrypt limita las emisiones de producción.

```
docker compose -f deploy/compose.yml stop traefik
docker compose -f deploy/compose.yml rm -f traefik
docker volume ls                                       # localizar el volumen ACME: <proyecto>_traefik_acme
docker volume rm <proyecto>_traefik_acme               # solo el de Traefik: contiene el certificado de staging
```

El nombre del proyecto de compose es el del directorio `deploy/` (`deploy`), así que el volumen suele llamarse `deploy_traefik_acme`. Confirmarlo con `docker volume ls` antes de borrar.

Después, en `deploy/.env`, borrar la línea `NOCTURNA_ACME_CA_SERVER` de staging. Sin esa línea, Traefik usa la CA de producción por defecto. Luego:

```
docker compose -f deploy/compose.yml up -d
```

No borrar el volumen de PostgreSQL (`<proyecto>_nocturna_pgdata`). No usar `docker compose down -v`: borra la base.

## 8. Verificación final

Esta lista es la verificación de la URL con TLS (criterio (a) de ADR 0044, primera comprobación). Su resultado se anota en `docs/PLAN_TAREAS.md`, T107, con la fecha.

- `curl -sSI https://$NOCTURNA_DOMAIN/` → `HTTP/2 200`, sin error de certificado.
- `curl -sI http://$NOCTURNA_DOMAIN/` → `301` o `308`, con `Location: https://<dominio>/`.
- `openssl s_client -connect $NOCTURNA_DOMAIN:443 -servername $NOCTURNA_DOMAIN </dev/null 2>/dev/null | openssl x509 -noout -issuer` → emisor de Let's Encrypt de producción, sin `STAGING`.
- Desde fuera del VPS (desde el Mac, por ejemplo): `nc -zv <IP> 443` abre; `nc -zv <IP> 8000` y `nc -zv <IP> 5432` fallan.
- En el VPS, `sudo ss -tlnp`: escuchan solo los puertos 22, 80 y 443. No aparecen 8000 ni 5432.
- Routing: el `200` del primer punto ya prueba que la plantilla `{{ env "NOCTURNA_DOMAIN" }}` resolvió. Un `404` del proxy indica lo contrario (paso 6).
- La documentación de la API no está expuesta. Desde la red interna, la web no obtiene `/docs` ni `/openapi.json`:
  ```
  docker compose -f deploy/compose.yml exec web node -e "fetch('http://api:8000/docs').then(r=>console.log(r.status))"
  ```
  Debe imprimir `404`. Repetir con `/openapi.json`.
- El rol de la API no escribe. Con la migración aplicada, el `SET` levanta el modo de solo lectura de la sesión para comprobar que el propio rol lo rechaza:
  ```
  docker compose -f deploy/compose.yml exec api python -c "import os, sqlalchemy as sa; c = sa.create_engine(os.environ['NOCTURNA_DATABASE_URL']).connect(); c.execute(sa.text('SET default_transaction_read_only = off')); c.execute(sa.text('INSERT INTO items DEFAULT VALUES'))"
  ```
  Debe fallar con `InsufficientPrivilege` (`permission denied for table items`). Sin el `SET`, el fallo es `ReadOnlySqlTransaction`. No se inserta ninguna fila.
- Con la API parada, la web sigue dando 200 con el aviso y sin indexar:
  ```
  docker compose -f deploy/compose.yml stop api
  curl -s -o /dev/null -w '%{http_code}\n' https://$NOCTURNA_DOMAIN/          # 200
  curl -s https://$NOCTURNA_DOMAIN/ | grep -o '<meta name="robots"[^>]*>'     # noindex, nofollow
  docker compose -f deploy/compose.yml start api
  ```
- Sin Claude en la API:
  ```
  docker compose -f deploy/compose.yml exec api python -c "import importlib.util as u; print(u.find_spec('claude_agent_sdk'))"
  ```
  Debe imprimir `None`.
- En el host del VPS: `command -v claude` no devuelve nada, y `test ! -e ~/.claude && echo ok` imprime `ok`.

## 9. Qué no hace este runbook

- No mide los criterios de cierre de la fase 3 (T110).
- No automatiza el despliegue ni las copias de seguridad.
- No avisa si la sincronización de datos falla durante varias noches seguidas (TECHNICAL_DEBT).

La sincronización de datos del Mac sí está aquí: § Sincronización de datos.

## Sincronización de datos (T108, ADR 0051)

Copia la base del Mac al VPS después de cada noche y del snapshot semanal del archivo. No gasta tokens ni llama a Claude. Lo lanzan `backend/scripts/run-night-scheduled.sh` y `backend/scripts/archive-snapshot-scheduled.sh` a través de `backend/scripts/sync-to-vps.sh`; a mano, el mismo script.

Estado a 2026-11-09: el código está en el repositorio. Pendiente del autor: el VPS, el usuario `nocturna-sync`, la clave y la primera sincronización real. Hasta entonces, el script sale con 78 y no hace nada.

### 1. Usuario `nocturna-sync` en el VPS

```
sudo useradd --create-home --shell /bin/bash nocturna-sync
sudo usermod -aG docker nocturna-sync
```

El shell no puede ser `/usr/sbin/nologin`: sshd ejecuta la orden forzada a través del shell del usuario. Pertenecer al grupo `docker` equivale a root en la práctica. Lo que lo limita es la clave: solo puede lanzar `restore.sh`.

### 2. Paquetes y permisos del checkout

`<ruta>` es el checkout del paso 4 (la ruta absoluta del repositorio en el VPS).

- `command -v flock timeout` debe imprimir dos rutas. Si falta alguno, instalar `util-linux` y `coreutils`.
- `nocturna-sync` debe poder recorrer `<ruta>` y leer `deploy/compose.yml` y `deploy/.env` (compose interpola las variables de `deploy/.env`). Comprobar con `sudo -u nocturna-sync test -r <ruta>/deploy/compose.yml && echo ok`. Si el checkout está en el home del administrador y no se puede recorrer, moverlo o ajustar los permisos de esa ruta.
- `deploy/.env` queda con grupo `nocturna-sync` y modo 640 (en el paso 4 era 600). No amplía nada: el usuario ya puede lanzar contenedores con `docker`.
- `restore.sh` debe ser ejecutable y pertenecer al administrador, no a `nocturna-sync`:
  ```
  sudo chmod 755 <ruta>/deploy/sync/restore.sh
  ```
- El bloqueo `deploy/sync/.restore.lock` se crea de antemano, propiedad de `nocturna-sync`. `deploy/sync/` sigue siendo del administrador con modo 755: `nocturna-sync` no puede crear ni sustituir ficheros ahí (ni `restore.sh` ni un enlace simbólico en lugar del bloqueo), solo abrir el bloqueo que ya existe:
  ```
  sudo install -o nocturna-sync -g nocturna-sync -m 600 /dev/null <ruta>/deploy/sync/.restore.lock
  sudo chmod 755 <ruta>/deploy/sync
  ```
  No usar `chmod 1777` en `deploy/sync/`: un directorio escribible por todos permite plantar un enlace simbólico en el lugar del bloqueo.

### 3. Clave y `known_hosts` en el Mac

```
ssh-keygen -t ed25519 -C nocturna-sync -N "" -f ~/.ssh/nocturna_sync_ed25519
chmod 600 ~/.ssh/nocturna_sync_ed25519
ssh-keyscan -t ed25519 <IP o host del VPS> > ~/.ssh/nocturna_sync_known_hosts
```

`-N ""`: sin frase de paso (ADR 0051, D3). La clave es dedicada; no reutilizar la de administración.

`ssh-keyscan` confía en la red en el primer contacto. Comparar la huella con la del propio VPS, leída desde la sesión de administración:

```
# en el VPS, con la sesión de administración
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
# en el Mac
ssh-keygen -lf ~/.ssh/nocturna_sync_known_hosts
```

Las dos huellas deben coincidir. Si no coinciden, no seguir.

### 4. Clave pública en el VPS (`authorized_keys`)

En `/home/nocturna-sync/.ssh/`: directorio 700, fichero 600, dueño `nocturna-sync`. Plantilla en `deploy/sync/authorized_keys.example`:

```
restrict,command="<ruta absoluta>/deploy/sync/restore.sh" ssh-ed25519 AAAA… nocturna-sync
```

`restrict` desactiva reenvíos, PTY y `~/.ssh/rc`. `command=` impone `restore.sh`. Si la IP pública del Mac es estable, se puede anteponer `from="<IP>",` a la línea (pendiente en OPEN_DECISIONS, T108).

Prueba sin restaurar nada, desde el Mac:

```
ssh -F none -i ~/.ssh/nocturna_sync_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$HOME/.ssh/nocturna_sync_known_hosts \
  nocturna-sync@<IP o host> true
echo $?
```

Debe imprimir `10`: `restore.sh` se ejecutó y rechazó la orden `true`. No se toca ningún dato.

### 5. Variables en los plist del Mac

Los plist de `run-night` y de `archive-snapshot`, materializados desde sus plantillas, traen tres marcadores `__NOCTURNA_SYNC_*__` sin sustituir. El `sed` del Paso 2 de `docs/CALIBRACION.md` no los sustituye, así que no volver a generar esos plist sin repetir este paso.

```
sed -i '' \
  -e "s|__NOCTURNA_SYNC_TARGET__|nocturna-sync@<IP o host>|" \
  -e "s|__NOCTURNA_SYNC_KEY__|$HOME/.ssh/nocturna_sync_ed25519|" \
  -e "s|__NOCTURNA_SYNC_KNOWN_HOSTS__|$HOME/.ssh/nocturna_sync_known_hosts|" \
  ~/Library/LaunchAgents/com.nocturna.run-night.plist \
  ~/Library/LaunchAgents/com.nocturna.archive-snapshot.plist
```

Las rutas quedan absolutas porque launchd no expande `$HOME` en `EnvironmentVariables`; el shell sí la expande en este comando. Después, recargar ambos agentes con `launchctl bootout` y `launchctl bootstrap`, como en `docs/CALIBRACION.md`, Paso 4.

Sin estas variables, o con los marcadores, la sincronización sale con 78 y no hace nada. La noche no cambia.

### 6. Primera restauración (en lugar de `migrate`)

En el VPS, tras `build` y `up -d` del paso 5, con PostgreSQL `healthy`. No ejecutar `migrate` antes: la primera restauración trae el esquema (ADR 0051, D11).

En el Mac, con `nocturna-postgres` `healthy` (`docker compose up -d` en la raíz):

```
cd <ruta del repo del Mac>
NOCTURNA_SYNC_TARGET=nocturna-sync@<IP o host> \
NOCTURNA_SYNC_KEY=$HOME/.ssh/nocturna_sync_ed25519 \
NOCTURNA_SYNC_KNOWN_HOSTS=$HOME/.ssh/nocturna_sync_known_hosts \
bash backend/scripts/sync-to-vps.sh; echo "código $?"
```

Debe imprimir `código 0`. El log es `~/nocturna-logs/sync-YYYYMMDD.log`, sin secretos. Comprobar en la web que el pie muestra «Datos actualizados el …». Una segunda ejecución debe dar `código 0` y dejar el mismo estado.

### 7. Comprobar que el volcado no lleva credenciales

Antes de la primera sincronización, y tras cambiar `sync-to-vps.sh` o la configuración de PostgreSQL:

```
DUMP="$(mktemp "${TMPDIR:-/tmp}/nocturna-check.XXXXXX")"
docker exec nocturna-postgres pg_dump -U nocturna -d nocturna -Fc --no-owner --no-privileges >"$DUMP"
docker exec -i nocturna-postgres pg_restore -f - <"$DUMP" \
  | grep -E 'sk-ant-|ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|credentials\.json'
rm -f "$DUMP"
```

La salida debe estar vacía. `grep` sale con 1 cuando no hay coincidencias: es el resultado correcto. Cualquier línea impresa es una credencial en la base y no se sincroniza hasta resolverlo.

### 8. Orden ante un cambio de esquema

1. En el Mac: `cd backend && uv run alembic upgrade head`.
2. En el VPS: actualizar el checkout, `docker compose -f deploy/compose.yml build` y `up -d`. La imagen de `migrate` debe llevar la nueva `head`.
3. Sincronizar: la próxima noche lo hace sola, o a mano con el comando del paso 6.

Si se sincroniza antes del paso 2, `restore.sh` rechaza el volcado con código 12 y la base no cambia.

### 9. Códigos de salida

| Código | Dónde | Significado |
|---|---|---|
| 0 | Mac y VPS | Sincronizado. |
| 1 | Mac | `pg_dump` falló. |
| 2 | Mac | Fallo de transporte (ssh 255 u otro código no reservado). |
| 3 | Mac | Tiempo agotado: `NOCTURNA_SYNC_TIMEOUT_S` (900 s por defecto) para todo el trabajo. |
| 10 | VPS | Orden inválida. |
| 11 | VPS | El sha256 del volcado no coincide. |
| 12 | VPS | La revisión de Alembic del volcado no es la `head` desplegada, o `alembic heads` superó 120 s. |
| 13 | VPS | `pg_restore` falló o superó su tope. Transacción única: la base queda como estaba. |
| 14 | VPS | No se pudo reaplicar el rol de solo lectura, o superó 120 s. |
| 15 | VPS | Otra restauración en curso. |
| 16 | VPS | Volcado por encima de 1 GiB. |
| 17 | VPS | Fallo interno no previsto, o `NOCTURNA_SYNC_RESTORE_TIMEOUT_S` inválido. |
| 75 | Mac | PostgreSQL local no está `healthy`. |
| 77 | Mac | Entorno inválido: clave o `known_hosts` no son ficheros, destino mal formado, tope inválido o revisión local ilegible. |
| 78 | Mac | No configurada (`NOCTURNA_SYNC_TARGET` vacío o con el marcador). |

Los códigos 10 a 17 llegan al Mac propagados. El informe matinal los muestra como «Sincronización con el VPS: fallida (código N)»; los códigos 3 y 15 tienen etiqueta propia («fallida: tiempo agotado» y «fallida: otra restauración en curso»). El código de la sincronización aparece como `sync=<n>` en el `.status` de la noche o del snapshot, y no cambia el de la noche.

### 10. Espacio temporal en el VPS

`restore.sh` copia el volcado a `${TMPDIR:-/tmp}`, hasta 1 GiB. Si `/tmp` es tmpfs, ese fichero ocupa RAM. Comprobar con `df -h /tmp` que hay al menos 1 GiB libres. Si no caben, el fallo sale con código 17.

### Qué no hace la sincronización

- No hace copias de seguridad de la base. No se guarda ningún volcado ni en el Mac ni en el VPS (TECHNICAL_DEBT).
- No avisa cuando falla. Varias noches seguidas sin sincronizar solo se notan por la fecha del pie (TECHNICAL_DEBT). Revisar `sync=` en el `.status` y `~/nocturna-logs/sync-YYYYMMDD.log`.
