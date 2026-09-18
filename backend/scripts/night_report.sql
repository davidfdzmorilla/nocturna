-- night_report.sql — Informe de la mañana (T60).
--
-- Siete consultas de solo lectura sobre la última noche ejecutada, pensadas
-- para leerse con café en dos minutos en vez de reconstruirse a mano cada
-- mañana durante las catorce noches de calibración. Uso:
--
--     psql -f backend/scripts/night_report.sql <conninfo>
--
-- (normalmente invocado por `backend/scripts/night-report.sh`, que ya
-- apunta al PostgreSQL de `docker compose`).
--
-- Cada consulta abre su propio `WITH n AS (...)` porque una CTE no
-- sobrevive al `;` que separa una sentencia de la siguiente en un script de
-- psql -- no hay forma de compartirla entre las siete sin repetirla. `n` es
-- siempre la última fila de `runs` por `started_at`, es decir, la noche que
-- se quiere revisar esta mañana.
--
-- Ninguna consulta escribe: todas son SELECT. `agent_calls` no tiene
-- timestamp propio (ver `infrastructure/db/models.py`), solo
-- `duration_ms`, así que ninguna consulta de este fichero puede situar una
-- llamada en el tiempo dentro de la noche, solo agregarla.

\pset border 2
\pset pager off

-- reserva del Editor en tokens: debe seguir a `editor_reserve_tokens` de
-- `config/pipeline.toml`. Si esa clave cambia, actualizar este valor a
-- mano -- no hay forma de leer un fichero TOML desde `psql`.
\set reserva 60000

\echo '=== Q1 · Cabecera de la noche ==='
WITH n AS (
    SELECT * FROM runs ORDER BY started_at DESC LIMIT 1
),
tokens_reales AS (
    SELECT COALESCE(SUM(ac.tokens_in + ac.tokens_out), 0) AS tokens
    FROM agent_calls ac
    JOIN n ON ac.run_id = n.id
)
SELECT
    n.id AS run_id,
    n.started_at AT TIME ZONE 'Europe/Madrid' AS started_at_local,
    ROUND(EXTRACT(EPOCH FROM (COALESCE(n.finished_at, now()) - n.started_at))::numeric, 1)
        AS elapsed_s,
    n.status,
    n.budget_tokens,
    n.items_fetched,
    n.items_read,
    n.findings_published,
    tr.tokens AS tokens_reales_agent_calls,
    ROUND(100.0 * tr.tokens / n.budget_tokens, 1) AS pct_presupuesto,
    n.notes
FROM n, tokens_reales tr;

\echo ''
\echo '=== Q2 · Desglose por agente / prompt_version / status (ROLLUP) ==='
WITH n AS (
    SELECT * FROM runs ORDER BY started_at DESC LIMIT 1
)
SELECT
    CASE WHEN GROUPING(ac.agent) = 1 THEN '(todos)' ELSE ac.agent::text END AS agent,
    CASE
        WHEN GROUPING(ac.prompt_version) = 1 THEN '(todas)'
        WHEN ac.prompt_version IS NULL THEN '(sin registrar)'
        ELSE ac.prompt_version
    END AS prompt_version,
    CASE WHEN GROUPING(ac.status) = 1 THEN '(todos)' ELSE ac.status::text END AS status,
    COUNT(*) AS llamadas,
    COUNT(DISTINCT ac.item_id) AS items_distintos,
    SUM(ac.tokens_in + ac.tokens_out) AS tokens,
    ROUND(AVG(ac.tokens_in + ac.tokens_out)) AS tokens_media,
    MIN(ac.tokens_in + ac.tokens_out) AS tokens_min,
    MAX(ac.tokens_in + ac.tokens_out) AS tokens_max,
    ROUND(AVG(ac.duration_ms)) AS duration_ms_media
FROM agent_calls ac
JOIN n ON ac.run_id = n.id
GROUP BY ROLLUP (ac.agent, ac.prompt_version, ac.status)
ORDER BY agent, prompt_version, status;

\echo ''
\echo '=== Q3 · Pool compartido (Reader+Popularizer) frente a reserva del Editor ==='
-- La cifra más importante del informe: la primera noche real acabó al
-- 97,8% del pool compartido. `:reserva` se define al principio del fichero
-- y debe seguir a `editor_reserve_tokens` de `config/pipeline.toml`.
WITH n AS (
    SELECT * FROM runs ORDER BY started_at DESC LIMIT 1
),
por_agente AS (
    SELECT ac.agent, SUM(ac.tokens_in + ac.tokens_out) AS tokens
    FROM agent_calls ac
    JOIN n ON ac.run_id = n.id
    GROUP BY ac.agent
),
resumen AS (
    SELECT
        COALESCE(SUM(tokens) FILTER (WHERE agent IN ('reader', 'popularizer')), 0) AS pool_usado,
        COALESCE(SUM(tokens) FILTER (WHERE agent = 'editor'), 0) AS reserva_usada
    FROM por_agente
)
SELECT
    n.budget_tokens,
    :reserva AS reserva_tokens,
    n.budget_tokens - :reserva AS pool_tokens,
    r.pool_usado,
    ROUND(100.0 * r.pool_usado / (n.budget_tokens - :reserva), 1) AS pct_pool,
    r.reserva_usada,
    ROUND(100.0 * r.reserva_usada / :reserva, 1) AS pct_reserva
FROM n, resumen r;

\echo ''
\echo '=== Q4 · interest_score de la noche: qué pasó con cada tramo ==='
-- readings no tiene run_id: la lectura se ata a la noche por las llamadas
-- del Reader (agent_calls con agent = 'reader' y status = 'ok'), único
-- rastro de qué item_id leyó el Reader en esta noche concreta.
WITH n AS (
    SELECT * FROM runs ORDER BY started_at DESC LIMIT 1
),
items_leidos AS (
    SELECT DISTINCT ac.item_id
    FROM agent_calls ac
    JOIN n ON ac.run_id = n.id
    WHERE ac.agent = 'reader' AND ac.status = 'ok' AND ac.item_id IS NOT NULL
),
findings_noche AS (
    SELECT f.item_id, f.published_at
    FROM findings f
    JOIN n ON f.run_id = n.id
)
SELECT
    r.interest_score,
    COUNT(*) AS lecturas,
    COUNT(fn.item_id) AS candidatos,
    COUNT(fn.item_id) FILTER (WHERE fn.published_at IS NOT NULL) AS publicados
FROM items_leidos il
JOIN readings r ON r.item_id = il.item_id
LEFT JOIN findings_noche fn ON fn.item_id = il.item_id
GROUP BY r.interest_score
ORDER BY r.interest_score;

\echo ''
\echo '=== Q5 · Descartes desambiguados (poco interesante vs JSON inválido del Popularizer) ==='
-- Decisión abierta de T42: un Item DISCARDED puede serlo por
-- interest_score bajo (nunca se llamó al Popularizer), porque el
-- Popularizer agotó los reintentos sin JSON válido, o porque el Editor no
-- lo aprobó (Finding sin publicar, ver PopularizeReading/EditNight). Esta
-- consulta separa las dos primeras cruzando con agent_calls de rol
-- popularizer en estado invalid_output, y añade la tercera como cajón
-- aparte para no confundirla con las otras dos ni perderla del recuento.
WITH n AS (
    SELECT * FROM runs ORDER BY started_at DESC LIMIT 1
),
items_leidos AS (
    SELECT DISTINCT ac.item_id
    FROM agent_calls ac
    JOIN n ON ac.run_id = n.id
    WHERE ac.agent = 'reader' AND ac.status = 'ok' AND ac.item_id IS NOT NULL
),
popularizer_invalido AS (
    SELECT DISTINCT ac.item_id
    FROM agent_calls ac
    JOIN n ON ac.run_id = n.id
    WHERE ac.agent = 'popularizer' AND ac.status = 'invalid_output' AND ac.item_id IS NOT NULL
),
findings_noche AS (
    SELECT DISTINCT f.item_id
    FROM findings f
    JOIN n ON f.run_id = n.id
),
descartes AS (
    SELECT i.id AS item_id
    FROM items i
    JOIN items_leidos il ON il.item_id = i.id
    WHERE i.status = 'discarded'
)
SELECT
    CASE
        WHEN fn.item_id IS NOT NULL THEN 'no aprobado por el Editor'
        WHEN pi.item_id IS NOT NULL THEN 'Popularizer: JSON inválido tras reintentos'
        ELSE 'poco interesante (interest_score bajo el umbral)'
    END AS motivo,
    COUNT(*) AS items
FROM descartes d
LEFT JOIN popularizer_invalido pi ON pi.item_id = d.item_id
LEFT JOIN findings_noche fn ON fn.item_id = d.item_id
GROUP BY 1
ORDER BY items DESC;

\echo ''
\echo '=== Q6 · Cola de items en new ==='
-- No parte de `n`: la cola de items sin leer es un estado global de la
-- base, no algo que pertenezca a una noche concreta (un item `new` no
-- tiene ninguna llamada de agente que lo ate a ningún run todavía).
SELECT
    COUNT(*) AS items_en_cola,
    MIN(fetched_at) AS mas_antiguo_fetched_at,
    ROUND(EXTRACT(EPOCH FROM (now() - MIN(fetched_at))) / 3600.0, 1) AS antiguedad_horas
FROM items
WHERE status = 'new';

\echo ''
\echo '=== Q7 · Candidatos huérfanos (Finding sin publicar, Item aún READ) ==='
WITH n AS (
    SELECT * FROM runs ORDER BY started_at DESC LIMIT 1
)
SELECT
    f.id AS finding_id,
    f.item_id,
    f.title,
    f.level_curious
FROM findings f
JOIN n ON f.run_id = n.id
JOIN items i ON i.id = f.item_id
WHERE f.published_at IS NULL AND i.status = 'read'
ORDER BY f.title;
