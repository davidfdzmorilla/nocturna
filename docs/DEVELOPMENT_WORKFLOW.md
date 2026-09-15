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

## Si algo va mal

- Hook bloquea algo legítimo → el autor lo dice; se ajusta el script en `.claude/hooks/` en un commit `chore(hooks): ...`, con explicación en el mensaje.
- El orquestador pierde el hilo → `/status` y, si hace falta, nueva sesión leyendo `docs/`. Ese es el criterio de calidad de la base documental.
- El pipeline consumió más de lo esperado → `/calibrate <%>` cada mañana durante T60; no tocar `nightly_tokens` a mano sin registrar la fila.
