# Despliegue en el VPS · runbook

Procedimiento de [ADR 0047](adr/0047-imagenes-y-compose-de-produccion.md) (T107) y de [ADR 0044](adr/0044-fase-3.md). Sin ejecutar en un VPS a 2026-11-06. Las decisiones pendientes del autor (dominio, VPS, correo, acceso al repositorio y sistema operativo) están en `OPEN_DECISIONS.md`, sección T107 (D1–D3, D9, D10).

## Qué hay en el VPS y qué no

- **Sí**: Traefik, la web, la API de lectura y PostgreSQL 16, definidos en `deploy/compose.yml`.
- **No**: el pipeline, los agentes, el CLI de Claude ni ninguna credencial de Claude. El pipeline sigue en el Mac (ADR 0044).
- **La base empieza vacía.** Sus datos llegan con T108, que no está automatizado en este runbook.
- **No automatizado aquí** (T108 o posterior): CI/CD, despliegue continuo, copias de seguridad y sincronización Mac → VPS.

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
- Primera pasada: descomentar `NOCTURNA_ACME_CA_SERVER=https://acme-staging-v02.api.letsencrypt.org/directory`. Staging no tiene límites de emisión que bloqueen las pruebas. Producción se activa en el paso 7.

## 5. Construir, arrancar y migrar

```
docker compose -f deploy/compose.yml build
docker compose -f deploy/compose.yml up -d
docker compose -f deploy/compose.yml run --rm api alembic upgrade head
```

La migración es manual (T107 D5). Hay que ejecutarla una vez en la base nueva y otra vez cada vez que cambie el esquema. Sin ella, la API no tiene tablas.

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
- Sin Claude en la API:
  ```
  docker compose -f deploy/compose.yml exec api python -c "import importlib.util as u; print(u.find_spec('claude_agent_sdk'))"
  ```
  Debe imprimir `None`.
- En el host del VPS: `command -v claude` no devuelve nada, y `test ! -e ~/.claude && echo ok` imprime `ok`.

## 9. Qué no hace este runbook

- No sincroniza datos del Mac (T108).
- No endurece la API ni crea el rol de solo lectura (T109).
- No mide los criterios de cierre de la fase 3 (T110).
- No automatiza el despliegue ni las copias de seguridad.
