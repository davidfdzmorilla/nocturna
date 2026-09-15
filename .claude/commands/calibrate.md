---
description: Registra la calibración de una noche (T60): tokens del Run frente a porcentaje semanal observado
---

El autor pasa en `$ARGUMENTS` el porcentaje semanal consumido que ve en Settings > Usage esta mañana (por ejemplo `/calibrate 12`).

1. Lee el último `Run` de la base de datos con `uv run nocturna last-run` (si el comando no existe todavía, léelo con una consulta SQL directa vía `docker compose exec` y anota que falta el comando en `docs/TECHNICAL_DEBT.md`).
2. Añade una fila a la tabla de `docs/CALIBRACION.md` (créalo con cabecera si no existe): fecha · run_id · status · tokens_used · items_read · findings_published · % semanal observado · tokens por punto porcentual (tokens_used / %).
3. Con al menos 5 filas, calcula la media de tokens por punto porcentual y propón el `nightly_tokens` que se acerque al 30% semanal repartido entre 7 noches. Preséntalo como propuesta; no toques `config/pipeline.toml` sin aprobación.
