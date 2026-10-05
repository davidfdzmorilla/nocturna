# Flujo de desarrollo

## Requisitos en la máquina

- Claude Code instalado y logueado con la cuenta **Max personal** (`claude`, luego `/login`). No con la cuenta Team del trabajo.
- `python3`, `uv`, `pnpm`, Docker con Compose.
- Repositorio clonado; `docker compose up -d` levanta PostgreSQL.

## Ciclo de una tarea

```
/status                 → dónde estamos
/next-task [Txx]        → architect produce el plan · el autor aprueba o pide cambios
/execute-task           → subagentes implementan · tester · reviewer (x2 si gasto) · docs-keeper
/commit-prepare         → committer muestra ficheros y mensaje · el autor aprueba
/commit-execute         → committer ejecuta · /status
```

Nunca se salta un paso. Si el orquestador propone implementar sin plan aprobado, se le recuerda `/next-task`. Si intenta hacer commit fuera de `/commit-execute`, el hook lo bloquea.

## Qué hace el autor en cada punto

- **Tras `/next-task`**: leer el plan entero. Mirar sobre todo "Impacto en control de gasto" y "Decisiones abiertas". Responder "ok" o dictar cambios.
- **Durante `/execute-task`**: nada, salvo que el orquestador pare por una decisión bloqueante.
- **Tras `/execute-task`**: leer el veredicto del `reviewer` y las decisiones abiertas nuevas. Resolver las que pueda en `docs/OPEN_DECISIONS.md` (o decírselo al orquestador para que lo haga `docs-keeper`).
- **Tras `/commit-prepare`**: leer el mensaje. Aprobar o corregir.

## Primera sesión (fase 0 → T00)

Pegar el contenido de `docs/PROMPT_INICIAL.md` como primer mensaje de la sesión.

## Reglas que no cambian

- Un commit por tarea salvo que el `committer` justifique dos.
- Commits en inglés, imperativo, conventional commits. Sin atribución a IA, nunca.
- Ningún test llama a Claude. El único que lo hace (`-m manual`) lo lanza el autor a mano.
- Lo no definido va a `OPEN_DECISIONS.md`. Inventar requisitos es un fallo de revisión.
- Nada de despliegue, Traefik, dominios ni secretos de Hetzner hasta el plan de fase 2.

## Merge de una tarea con migración

La noche la lanza `launchd` a las 00:05 desde el árbol principal, en `main`, y no ejecuta Alembic. Una tarea que añade una migración se mergea **por la mañana**, nunca entre 00:00 y 04:45, y el mismo día, antes de la noche, se ejecuta en el árbol principal (`backend/`):

1. `git pull` de `main` y `uv run alembic upgrade head`; comprobar `uv run alembic current`.
2. Si la tarea trae script de relleno, lanzarlo primero con `--dry-run` y revisar la salida. En T79: `uv run python scripts/backfill_exoplanet_match.py --dry-run` y **comprobar que TOI-6981 b (2609.37597) aparece entre los primeros `max_items_per_night` de la cola, marcado y con variante v3** (dentro de los marcados el orden es por `fetched_at`, así que puede no ser el primero).
3. El relleno real (sin `--dry-run`).
4. `uv run nocturna run-night --dry-run` y revisar ingesta, plan de gasto y reparto v3/v2. Desde T87 el `--dry-run` no escribe nada en la base.

Si se mergea sin migrar, la noche falla: con T79, antes de gastar tokens; con T72 habría gastado el Popularizer sin poder guardar candidatos.

En T81 (`archive-snapshot`), tras migrar: `uv run nocturna archive-snapshot --dry-run` y revisar el informe; `uv run nocturna archive-snapshot` (carga completa inicial); una segunda ejecución debe salir incremental con 0 cambios; después, instalar el agente de launchd desde `backend/scripts/com.nocturna.archive-snapshot.plist.template` (sustituir `__REPO_ROOT__` y `__HOME__`, copiar a `~/Library/LaunchAgents/` y cargarlo). Anotar las cifras en el cierre de T81.

En T88 (`tension_evaluation`), tras migrar y antes del viernes a las 10:00: `uv run nocturna evaluate-tensions --dry-run` y revisar los estados (HIP 67522 b `evaluated` frente a Chakraborty 2026; c `consistent_with_limit`; TOI-6981 b y TOI-210 b `awaiting_reference`); después `uv run nocturna evaluate-tensions` y una segunda ejecución que no debe cambiar nada.

En T89 (findings de medida), tras migrar y antes de la noche: `uv run nocturna evaluate-tensions --dry-run` debe terminar con **0 fallos de resolución** (las filas `awaiting_reference` anteriores a D16 dan por ausente cualquier respuesta del alias distinta de `OK`; si aparece alguno, se revisa a mano antes de la noche); después `uv run nocturna run-night --dry-run` y comprobar la sección de findings de medida: TOI-6981 b (radio) y TOI-210 b (masa y radio) como `primera_medida`, y HIP 67522 b como confirmación "bloqueado (confirmation_enabled=false)". Los envoltorios de launchd se leen del árbol principal, así que el cambio de `run-night-scheduled.sh` entra con el `git pull`.

## Si algo va mal

- Hook bloquea algo legítimo → el autor lo dice; se ajusta el script en `.claude/hooks/` en un commit `chore(hooks): ...`, con explicación en el mensaje.
- El orquestador pierde el hilo → `/status` y, si hace falta, nueva sesión leyendo `docs/`. Ese es el criterio de calidad de la base documental.
- El pipeline consumió más de lo esperado → `/calibrate <%>` cada mañana durante T60; no tocar `nightly_tokens` a mano sin registrar la fila.
