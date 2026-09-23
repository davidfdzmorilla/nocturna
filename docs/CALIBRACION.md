# Calibración — Nocturna Fase 1

Runbook para el autor durante catorce noches de ejecución real (T60). Cada mañana, tras la ejecución de `run-night`, se sigue el procedimiento de cinco pasos y se registra una fila en la tabla de abajo. **Si no es utilizable a las siete, medio dormido, la tarea falla.**

---

## Procedimiento de la mañana (cinco pasos)

### Paso 1: Ejecutar el informe de la noche
```bash
bash backend/scripts/night-report.sh
```
Genera siete consultas de solo lectura desde PostgreSQL sobre la última noche ejecutada. Toma dos minutos. Salida esperada: cabecera, desglose por agente, pool compartido vs reserva, `interest_score` por tramo, descartes desambiguados, cola de items, candidatos huérfanos. Apunta títulos y tasa de cambio respecto a ayer.

### Paso 1.5: Contar eventos de reintentos de arXiv (si aplican)

La ingesta usa `Retrier` con política configurable en `pipeline.toml`. Si la ingesta tuvo éxito con arXiv disponible, no habrá eventos de reintento. Si hubo 406, 429, 5xx o error de transporte transitorio:

Los eventos van al log de **errores** de la noche, no al de salida: la telemetría JSON del pipeline se escribe en `stderr`.

```bash
LOG=~/nocturna-logs/night-$(date +%Y%m%d).err.log

# Recuperaciones: fallos transitorios que se recuperaron tras reintentar
grep -c '"event": "arxiv.retry_recovered"' "$LOG" || true

# Agotamientos: reintentos que no bastaron
grep -c '"event": "arxiv.retry_exhausted"' "$LOG" || true

# (Opcional) Inspeccionar el primero, con sus campos
grep '"event": "arxiv.retry_recovered"' "$LOG" | head -1 | jq .
```

`grep -c` ya imprime `0` cuando no hay coincidencias y sale con código 1; el `|| true` solo evita que ese código corte un script con `set -e`.

**Anotar en la tabla**: en la columna `notas`, con el formato `ingesta: N recuperados, M agotados`, y solo cuando alguno sea distinto de cero. **No se añaden columnas nuevas**: catorce noches con dos columnas casi siempre a cero no compensan ensanchar una tabla que ya tiene veintisiete. Si tras las primeras noches el 406 resulta frecuente, se reconsidera.

**Interpretación**: Un valor > 0 en `retry_exhausted` significa que la ingesta sufrió un fallo que no se pudo recuperar, así que `run.status` podría ser `partial` por motivo `ingest_error`. Ver nota al final de este paso.

#### Qué hacer si ves `retry_exhausted` por la mañana

**No es una noche perdida: es la ingesta de esa noche.** El Run queda en `partial`, pero las fases Reader, Popularizer y Editor siguen corriendo sobre los `Item` en estado `NEW` que ya hubiera en base. Si la cola traía pendientes, esa noche publica con normalidad y su dato de gasto sigue siendo válido para la calibración.

Procedimiento:

1. **No relances.** Rige la «Regla de noches fallidas»: se anota y se espera a mañana.
2. Anota en `notas`: `ingesta: 406 agotado` y el número de ítems leídos, para distinguir «no hubo ingesta pero sí noche» de «no hubo noche».
3. **Vigila la cola, que es el riesgo real.** Un 406 suelto es inocuo; varias noches seguidas sin ingesta vacían los `NEW` pendientes y entonces sí hay noches en blanco. Si ves dos agotamientos seguidos, comprueba cuántos ítems quedan:

   ```bash
   docker exec nocturna-postgres psql -U nocturna -d nocturna \
     -c "select count(*) from items where status = 'new';"
   ```

**Qué se sabe del 406** (observado el 2026-09-21 y el 2026-09-22): lo devuelve el CDN de arXiv (cabeceras Fastly/Varnish), con cuerpo vacío y sin `Retry-After`, y es **intermitente por episodios**. Uno de los episodios observados duró **más de 40 s**: los cuatro intentos cayeron dentro y se agotaron, mientras que minutos después la misma consulta pasaba a la primera. Es decir, el reintento de T60.b **cubre los episodios cortos, no los largos**, y no hay forma de saber de antemano cuál te toca.

Descartados como causa: `User-Agent`, `Accept`, `Accept-Encoding`, versión HTTP, codificación de `%3A`, `max_results` y el uso de cliente síncrono o asíncrono. La hipótesis de la huella del cliente (`curl` pasaba donde `httpx` fallaba) **no está confirmada** y perseguirla se parecería a evadir una protección del CDN, así que no se persigue. Ver ADR 0009 y `OPEN_DECISIONS.md`.

**Qué hacer con los valores de reintento**: nada todavía. `retry_max_attempts`, `retry_base_delay_s` y `retry_max_elapsed_s` son provisionales y se calibran con estas catorce noches. Subir el tope para cubrir episodios de 40 s largos come tiempo de noche en todas las noches para salvar unas pocas; esa cuenta solo se puede hacer con la frecuencia real medida. Si al cerrar T60 hay varios `retry_exhausted`, entra en la decisión final junto a las siete reglas de cierre.

### Paso 2: Abrir la web y emitir juicios de calidad

**Requisitos previos**: asegúrate de que compose, la API y la web están ejecutándose:
```bash
# Terminal 1: PostgreSQL
docker compose up -d

# Terminal 2: API de lectura (desde backend/)
cd backend && uv run uvicorn nocturna.api.app:create_app --factory --reload --port 8000

# Terminal 3: Web (desde web/)
cd web && pnpm dev
```

Una vez levantadas:
- Navega a `http://localhost:3000` (o tu dev server de Next.js).
- Repasa cada hallazgo publicado de la noche (lista cronológica). Por cada uno: **«¿lo dejarías publicado con tu nombre?»** → anota en columna `calidad_pub` el formato `X/Y`: X hallazgos de Y publicados que dejarías. Ej. 9/10 = 9 de 10 publicados son aceptables; 5/10 = 5 de 10 tienen problemas.
- Abre el informe Q7 del paso 1 (candidatos huérfanos, Finding sin publicar, Item aún en estado READ). Por cada uno, lee el `level_curious` mostrado: **«¿lo habrías publicado tú?»** → anota en columna `rescatables` el número que sí de N descartados. Máximo 10 para no depender de memoria.
- Identifica el peor hallazgo publicado de la noche (el que menos te gusta de los aprobados) y etiquétalo con **una sola palabra** del vocabulario cerrado: `impreciso` (falta contexto o contiene error de hecho), `trivial` (no tiene novedad), `ilegible` (exposición confusa o estructura rota), `alucinado` (afirma algo que no está en el paper). Anota en columna `etiqueta_peor`. Si no hay hallazgos publicados, deja vacío.
  - Estos son ~14 juicios binarios: cuatro minutos.

### Paso 3: Leer Settings > Usage (solo el autor puede ver esto)
- Abre Settings > Usage en el panel de Claude (requiere autenticación personal del autor).
- **Dos lecturas por noche, no una por semana**: el número absoluto mezcla su uso interactivo con el del pipeline. Lo único atribuible a la noche es el delta.
  - **`A` (antes de lanzar, si es posible)**: captura el contador exacto de tokens de `Usage` cinco minutos antes de ejecutar `run-night` (ej. 2026-09-19 23:55).
  - **`B` (a la mañana siguiente)**: el contador de mañana (ej. 2026-09-20 09:00). Esto es **obligatorio**.
- **Protocolo de `A`**:
  - Si lanzas `run-night` a mano a medianoche, captura `A` unos minutos antes (es la lectura difícil).
  - Si usas `cron` o `launchd`, `A` es imposible captar en el momento exacto: toma la **`B` de la noche anterior** — válido solo si `uso_interactivo = no` en esa noche anterior.
  - Si falta `A` y no tienes `B` de ayer: escribe `—` y **no** reconstruyas de memoria. Una serie con muchas faltas de `A` es inútil.
- **Columna `uso_interactivo`**: si usaste Claude de forma interactiva entre `A` y `B` (en los ejemplos: noches de 2026-09-19 a 2026-09-20), escribe `sí`. Si no: escribe `no`.
  - Impacto: si `uso_interactivo = sí`, esa noche se **excluye del cálculo de la constante de conversión tokens→% semanal** (porque el delta es ruido de tu uso, no del pipeline). Se usa solo para dar contexto y saber que el número semanal no es limpio.
- **Columna `Δ%`**: `B − A` (delta de Settings), convertido a porcentaje del presupuesto semanal. Fórmula: `delta_tokens / presupuesto_semanal × 100%`.
- **Día de reinicio**: cuando el contador caiga a ~0 (reinicio semanal), **anota la fecha exacta y hora** (ej. `2026-09-21 00:15 UTC`). Eso responde por observación la decisión abierta de T02 sobre `weekly_reset_weekday`.

### Paso 4: Pegar una fila en la tabla
Rellena la plantilla de fila (ver abajo), con la fecha del día, los tokens de `run.status` desde Q1, `interest_score` desde Q4, descartes desde Q5, tus juicios de calidad. Pega la fila al final de la tabla "Noches reales (T60)", arriba de las notas.

### Paso 5: Actualizar los comandos de referencia del final
Si hoy cambió `config/pipeline.toml` o las versiones de CLI/SDK: aumenta `config_version` en la sección "Marcadores de baseline" y documenta en qué cambió. Los humos siempre registran CLI y SDK; anota si alguno se salió del rango nominal 2.1.274 / 0.2.153.

### Nota importante: cómo interpretar `run.status = partial`

**Un `Run.status = partial` no siempre significa "noche mala".** Tres escenarios:

1. **Presupuesto agotado durante fase A o B** (normal, incluso esperado durante calibración):
   - Reader/Popularizer llegó al tope de `nightly_tokens − editor_reserve_tokens`
   - Los ítems leídos se procesaron correctamente; solo hay pocos candidatos porque el pool se agotó
   - `items_published > 0` es posible si fase B completó antes del agotamiento
   - **Interpretación**: noche exitosa con margen ajustado, no fracaso. Calibración de T60 la usa para validar el umbral de `interest_score`

2. **Ingesta falló** (transitorio de arXiv, red, etc.):
   - `items_fetched = 0` o muy bajo; evento `ingest_error` en logs
   - BUT: cola de `new` de noches anteriores se procesó normalmente (fase A saca de `next_unread`, no solo ítems nuevos)
   - Reader/Popularizer/Editor corrieron con la cola vieja, publicaron hallazgos
   - `run.status = partial` **por código de salida 7 (`ingest_error`)**, pero noche funcionó correctamente
   - **Interpretación**: parcial "virtuoso" — no es una noche perdida, solo la ingesta no trajo nuevas fuentes

3. **Presupuesto agotado en fase C** (Editor):
   - Reader y Popularizer completaron; Editor rechazó porque no hay presupuesto en la reserva
   - `items_published = 0` pero `candidatos > 0`
   - **Interpretación**: noche estrecha, el Editor no pudo ejecutar. Revisar `%reserva` en la tabla

**Cómo distinguirlos en la tabla**:
- Columna `status`: anota `partial` en todos los casos
- Columna `notas`: **escribe el motivo explícitamente**:
  - "pool agotado en fase B" (caso 1)
  - "ingesta falló pero cola procesada" (caso 2) — también busca `ingest_error` en logs
  - "presupuesto Editor insuficiente" (caso 3) — revisa Q3 de paso 1

Sin esa claridad, una serie de catorce noches que incluya varios "partial" se malinterpretará como «funciona mal». Necesitamos distinguir "el sistema está calibrado" (casos 1 y 2) de "el sistema está roto" (caso 3 sin explicación).

---

## Tabla: Noches reales (T60 la rellenará)

**Columnas**: `fecha · lanzamiento · CLI · SDK · prompt_versions · config_version · status · items_fetched · items_read · items_failed · candidatos · tasa_candidatos% · publicados · tokens · %presupuesto · pool_usado/pool_tokens · reserva_usada/60000 · reintentos R/P/E · elapsed_s · usage_A · usage_B · Δ% · uso_interactivo · calidad_pub · rescatables · etiqueta_peor · notas`

### Plantilla de fila vacía (copiar y pegar)
```
| 2026-09-XX | — | — | — | — | — | — | — | — | — | — | —% | — | — | —% | —% | —% | —/—/— | — | — | — | — | — | —/— | —/— | — | |
```

**Leyenda de la plantilla**:
- `lanzamiento`: `manual` si lo ejecutaste a mano, `planif.` si fue cron/launchd.
- `CLI/SDK`: versión del cliente y el SDK (`claude --version` y `uv pip show claude-agent-sdk`).
- `prompt_versions`: r1/p1/e1, r2/p2/e1, etc. Versiones de Reader / Popularizer / Editor en `agent_calls`.
- `config_version`: etiqueta de configuración de baseline (ej. `cfg-2026-09-18`). Si no cambió desde ayer: repite el de ayer.
- `status`: `completed`, `partial`, `killed`, `failed`. De `run.status`.
- `items_fetched`, `items_read`, `items_failed`: de `run`, en tres columnas separadas. Ej. 34 traídos, 39 leídos, 1 fallido = columnas "34 | 39 | 1".
- `candidatos`: de Q4, suma de candidatos en todas las filas (los que llegaron a Popularizer).
- `tasa_candidatos%`: `candidatos / items_read × 100`.
- `publicados`: de Q1.
- `tokens`: total `agent_calls`, de Q1 (no usa `run.tokens_used`).
- `%presupuesto`: Q1, `tokens / run.budget_tokens × 100`.
- `%pool`: Q3, `pool_usado / pool_tokens × 100` (proporción de los 240.000 tokens de Reader+Popularizer que se consumieron).
- `%reserva`: Q3, `reserva_usada / 60000 × 100` (proporción de los 60.000 tokens reservados para el Editor que se consumieron).
- `reintentos R/P/E`: de Q2 (contar llamadas − items_distintos para Reader / Popularizer / Editor). Ej. 2/1/0 significa Reader reintentó 2 ítems, Popularizer 1, Editor 0.
- `elapsed_s`: tiempo total, en segundos (Q1).
- `usage_A`: tokens de Settings antes de ejecutar (si disponible).
- `usage_B`: tokens de Settings la mañana después (obligatorio).
- `Δ%`: porcentaje del presupuesto semanal que consumió esta noche. Fórmula abajo.
- `uso_interactivo`: sí/no.
- `calidad_pub/rescatables`: X/Y significa X hallazgos de Y publicados que dejarías, Y rescatables de Z descartados que sí habrías publicado. Ej. 9/10 significa 9 de 10 publicados. Ej. 2/4 significa 2 de 4 descartados que habrías rescatado.
- `etiqueta_peor`: una sola palabra de vocabulario cerrado.
- `notas`: cualquier otra observación relevante.

### Noches reales (T60 las rellenará)

| Fecha | Lanzamiento | CLI | SDK | Prompt | Config | Status | Items | Read | Failed | Candidatos | Tasa% | Pub | Tokens | %Presup | %Pool | %Reserva | Reintentos | Elapsed | A | B | Δ% | Interactivo | Calidad | Rescatables | Peor | Notas |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-18 | manual | 2.1.274 | 0.2.153 | r2/p2/e1 | cfg-2026-09-18 | killed | 0/0 | 0 | 0 | 0 | —% | 0 | 0 | 0% | —% | —% | 0/0/0 | 0,1 | — | — | — | no | —/— | —/— | — | Ejecución a las 11:44 (fuera de ventana 00:00–04:45). `hard_stop` denegó con `outside_window`; Run cerrado como `KILLED` sin gasto de tokens ni procesamiento. Validación de guarda funcionó exactamente como se diseñó. Ingesta corrió pero falló: `ingest=error:ArxivFeedError fetched=0`. |
| 2026-09-18 | manual | 2.1.274 | 0.2.153 | r2/p2/e1 | cfg-2026-09-18 | killed | 34/0 | 0 | 0 | 0 | —% | 0 | 0 | 0% | —% | —% | 0/0/0 | 1,2 | — | — | — | no | —/— | —/— | — | Ejecución a las 12:30:59 (fuera de ventana). Ingesta completó exitosamente: 34 ítems nuevos traídos de arXiv. Sin embargo, `hard_stop` externo denegó al primer `authorize` de la fase Reader, por `outside_window`. Run cerrado como `KILLED` sin procesamiento LLM ni gasto de tokens. Los 34 ítems en estado `new` quedaron en base de datos, reutilizables en la siguiente noche (que es precisamente la de las 12:34). |
| 2026-09-18 | manual | 2.1.274 | 0.2.153 | r2/p2/e1 | cfg-2026-09-18 | completed | 0/34 | 39 | 1 | 14 | 35,9% | 10 | 246.608 | 82,2% | 97,8% | 19,8% | 1/1/0 | 613,8 | — | — | — | no | — | — | — | Ejecución a las 12:34 (fuera de ventana nominal: 00:00–04:45, con `hard_stop` ampliado a 23:59 para validación del circuito). Ciclo completo: ingesta → Reader → Popularizer → Editor → persistencia. `items_fetched = 0` porque los 34 ítems venían del Run anterior (12:30:59); no hay nuevos de arXiv. Ejecución real: 41 llamadas Reader (153.753 tokens, media 3.750), 15 llamadas Popularizer (80.965 tokens, media 5.398, 7,1% reintento), 1 Editor (11.890 tokens). Q7: 0 candidatos huérfanos. Los 4 no publicados son descartes del Editor, no huérfanos. **Juicios de calidad sin emitir**: esta noche se registró antes de que existiera el runbook, así que `calidad_pub`, `rescatables` y `etiqueta_peor` quedan vacíos. Pool al 97,8% — margen mínimo. |

---

## Protocolo de Settings > Usage y cálculo de Δ%

### Ejemplo de noche típica: 2026-09-20 a 2026-09-21

Asume que el límite semanal es 1.000.000 de tokens (dato ficticio, el real depende de tu suscripción).

**A (antes de lanzar, viernes ~23:55)**: 847.832 tokens totales en Settings.
**B (la mañana, sábado ~09:00)**: 901.356 tokens totales.
**Δ (delta bruto)**: 901.356 − 847.832 = 53.524 tokens.

Si esa noche `tokens` (de Q1) = 46.000:
- El delta bruto incluye: 46.000 de pipeline + gasto lateral del CLI (Haiku de sesión no visible en `BudgetGuard`).
- Si `uso_interactivo = no`: el delta es **casi limpio**, atribuible al pipeline. El delta observado (53.524) vs tokens (46.000) = diferencia de 7.524 tokens de gasto lateral del CLI. El gasto lateral del CLI es estimado en ~95.366 tokens/noche ≈ **31,8%** del presupuesto nightly (82 sesiones × ~1.163 Haiku, según entrada T42/T43 de TECHNICAL_DEBT.md), así que un delta de 7.524 sobre 46.000 = **16,4%** es conservador.
- Si `uso_interactivo = sí`: no sabemos cuánto es pipeline y cuánto es tuyo. Excluye esta noche del cálculo de la constante de conversión.

**Constante de conversión**: una vez tengas ~7 noches con `uso_interactivo = no` y delta > 0, calcula `k = suma(delta) / suma(tokens)`. El delta observado (`B − A`) **ya incluye** el gasto lateral del CLI; `k` mide esa relación (delta observado / tokens contabilizados). Usa esa `k` para todas las demás noches:
- `Δ% = delta / tokens_semanales × 100%` (el delta ya es bruto, no necesita multiplicar por `k`).

**Ejemplo**: si delta = 53.524 tokens y presupuesto semanal es 1.000.000:
- Δ% = 53.524 / 1.000.000 = 5,35% de tu presupuesto semanal.

**Reinicio semanal**: cuando veas el contador caer de ~900.000 a ~100.000 (o similar, dependiendo de tu día de semana), **anota la hora exacta en el campo "Día de reinicio" del paso 3**. Eso cierra la decisión abierta T02.

---

## Marcadores de baseline: versiones de configuración

Cada vez que se toque `config/pipeline.toml`, se incrementa el `config_version` y se documenta qué cambió y por qué. Sin esto la serie no es comparable consigo misma.

- **cfg-2026-09-18** (inicial, T60 arranque):
  - `nightly_tokens = 300.000`
  - `editor_base_tokens = 2500` (rebajado de 4.000)
  - `editor_tokens_per_candidate = 850` (subido de 700)
  - Motivo: Primera observación real del Editor. Dos puntos (N=3 → 3.381 tokens; N=14 → 11.890 tokens) permiten ajuste lineal. Estimación anterior (4.000 + 700N) subestimaba en el peor caso (N=40: estimaba 32.000, real ~32.002). La recta **observada** es `1.060 + 774×N`; la **estimación nueva** (`2.500 + 850×N`) queda por encima de ella en todo el rango 1–40 (`estimación − observada = 1.440 + 76·N`, siempre positiva), con margen mínimo 1,14× en N=40. Ojo: por debajo de N=10 la estimación nueva es *menos* conservadora que la vieja (en N=3, 1,49× frente a 1,80×); las dos se cruzan exactamente en N=10. **Provisional**: dos observaciones para dos parámetros es un sistema exactamente determinado (residuo cero); se reajusta por regresión sobre ~14 noches al cierre de T60.
  - Prompt versions en uso: `reader-v2`, `popularizer-v2`, `editor-v1`. Estos cambios rompen comparabilidad con datos previos.
  - CLI 2.1.274 + SDK 0.2.153 (valores en el momento de T44, antes de T60).

- **cfg-2026-09-22** (T60.b, robustez de la ingesta frente a 406):
  - Añadidas `[sources.arxiv].retry_max_attempts = 4`, `retry_base_delay_s = 5.0`, `retry_max_elapsed_s = 60.0` (antes: sin reintentos, ver `infrastructure/arxiv/client.py`). Resto de la sección `[sources.arxiv]` y del resto del fichero sin cambios.
  - Motivo: 406 transitorio de Fastly/Varnish observado el 2026-09-21 (cuerpo vacío, sin `Retry-After`, la misma consulta devolvió 200 minutos después). Backoff exponencial con jitter (`infrastructure/arxiv/retry.py::Retrier`), reintentable también en 429, 5xx y `httpx.TransportError`. `retry_max_attempts` es un tope, no una garantía (`retry_max_elapsed_s` puede cortar antes con fallos lentos); `retry_max_elapsed_s` acota cuándo puede iniciarse un nuevo intento, no la duración total de la secuencia -- el techo real por página incluye el timeout HTTP del intento en curso (del orden de 93 s, no 60 s). Ver el comentario de `config/pipeline.toml` para la aritmética completa.
  - PROVISIONAL: sin datos de campo sobre cuánto dura el 406 del CDN más allá del caso único observado; se recalibra en T60 si vuelve a aparecer.
  - No afecta a `reader-v2`/`popularizer-v2`/`editor-v1` ni a las constantes de gasto (`nightly_tokens`, reservas del Editor, etc.): la serie de calibración de esas magnitudes sigue comparable.

**Nota importante**: las versiones de `reader-v2`, `popularizer-v2` y `editor-v1` rompen comparabilidad con cualquier dato anterior a ellas. El campo `prompt_version` en `AgentCall` permite segregar los datos históricos si es necesario.

---

## Normas de operación

### Regla de noches fallidas (crítica)
**Una noche fallida no se relanza: se anota y se espera a mañana.**

Dos ejecuciones de `run-night` en la misma ventana (00:00–04:45) autorizan dos presupuestos completos. Además, arruinan el dato de esa noche (¿cuál de las dos cuentas?). **Si algo falla durante la ejecución**:
  - Anota el `run.status` que dejó (ej. `partial`, `failed`, `killed`).
  - NO relances en la misma ventana.
  - Espera a mañana noche para otro intento, que abrirá un Run nuevo con presupuesto fresco.

### Lanzamiento planificado con `launchd` (fragmento)

Si decides dejar el portátil despierto catorce noches y confiar en `launchd`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nocturna.run-night</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>bash</string>
        <string>-c</string>
        <string>cd /Users/davidferanandezmorilla/Desktop/development.nosync/nocturna/backend &amp;&amp; uv run nocturna run-night >> ~/nocturna-logs/night-$(date +\%Y\%m\%d).jsonl 2>&amp;1</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>0</integer>
        <key>Minute</key>
        <integer>5</integer>
    </dict>
</dict>
</plist>
```

**Advertencias**:
1. **Sin `KeepAlive` ni reintento**: si la máquina duerme, la tarea no se ejecuta. Necesitas `sudo pmset repeat wakeorpoweron` previamente para despertar automáticamente; además, el portátil debe estar enchufado.
2. **Logs**: `launchd` redirige `stderr` a `/var/log/system.log` (mezcla de otros procesos, inutilizable). La línea de arriba redirige explícitamente a `~/nocturna-logs/night-YYYYMMDD.jsonl`, un fichero por noche, **fuera del repositorio**. Sin rotación: después de dos semanas tendrás 14 ficheros. Se borran manualmente.
3. **Máquina dormida**: si duerme, la ejecución no sucede. Anota esa noche como «no ejecutada» en la tabla (status `—` o nota "máquina dormida") y **la serie se extiende** — no es un fallo de calibración, es un dato que no existe.

**Instalación**:
```bash
# Crear directorio de logs fuera del repo:
mkdir -p ~/nocturna-logs

# Guardar el plist en ~/Library/LaunchAgents/:
cat > ~/Library/LaunchAgents/com.nocturna.run-night.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nocturna.run-night</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>bash</string>
        <string>-c</string>
        <string>cd /Users/davidferanandezmorilla/Desktop/development.nosync/nocturna/backend &amp;&amp; uv run nocturna run-night >> ~/nocturna-logs/night-$(date +\%Y\%m\%d).jsonl 2>&amp;1</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>0</integer>
        <key>Minute</key>
        <integer>5</integer>
    </dict>
</dict>
</plist>
EOF

# Cargar:
launchctl load ~/Library/LaunchAgents/com.nocturna.run-night.plist

# Verificar:
launchctl list | grep nocturna
```

---

## Decisión final: criterios de cierre de T60

Al final de catorce noches (o cuando haya mínimo **7 noches con par (A,B) limpio** en `uso_interactivo = no`), **se toma la decisión de ajustar `pipeline.toml`** con estos criterios:

### Regla 1: Constante de conversión `k` (gasto lateral del CLI)
- Calcula `k` a partir de noches limpias: `k = suma(delta) / suma(tokens)`.
- Sea cual sea `k`, **ajusta `nightly_tokens` para mantener el 30% semanal** con `nightly_tokens_ajustado = nightly_tokens / k` (el umbral de 1,30 no es una condición para aplicar la regla: solo marca cuándo el desvío ya es grande). Por ejemplo: si k = 1,25 y objetivo = 300.000 (30% del presupuesto semanal), los tokens reales gastados serían 300.000 × 1,25 = 375.000, que es 37,5% del semanal. Reducir a `300.000 / 1,25 = 240.000` en `pipeline.toml` garantiza que el gasto real (240.000 × 1,25 = 300.000) se alinee con el 30%. **Esto debe valer en `pipeline.toml`, no deducirse de memoria.**

### Regla 2: Umbral de `interest_score`
- Agregada todas las noches, calcula el percentil del `interest_score` donde se publica (Q4). Hoy es 4.
- Si el percentil observado es distinto (ej. 95% de los candidatos tienen score >= 4), **el umbral es correcto**.
- Si ves clustering no esperado (ej. muchos score 3, muchos score 5, pocos score 4), la decisión es abierta para T61: ¿subir a 5? ¿bajar a 3? Una noche experimental autoriza respuesta (ver decisión abierta C de abajo).

### Regla 3: Constantes del Editor (regresión lineal)
- Recopila todos los puntos (N candidatos, tokens gastados) de las N=14 noches.
- Ajusta recta `tokens = a + b×N` por mínimos cuadrados (ej. usando `numpy.polyfit`).
- Si `a` y `b` difieren >20% de los valores de `config/pipeline.toml`, **actualiza los valores redondeando hacia el conservador** (margen mayor, nunca menor). Documenta el cambio en un nuevo `config_version`.

### Regla 4: Máximo de ítems por noche
- `max_items_per_night = 40` hoy. Si todas las noches leen 40 y Popularizer no agota candidatos (hay margen), es seguro subir a 50.
- Si alguna noche el Reader no alcanza 40 (ej. arXiv devuelve 30), es normal. Mantener el tope.

### Regla 5: Distribución pool / reserva
- Hoy: 300.000 total, 60.000 reserva Editor (20%), 240.000 pool Reader+Popularizer (80%).
- Si el pool se agota regularmente (<5% de margen, ej. Q3 `pct_pool` > 95%) y la reserva queda intacta, **subir la reserva** no ayuda: el problema es que hay candidatos. Opciones: (a) subir umbral `interest_score`; (b) subir `nightly_tokens` global (Regla 1).
- Si la reserva se agota (ej. Q3 `pct_reserva` > 95%), bajar `editor_base_tokens` o `editor_tokens_per_candidate` por Regla 3.

### Regla 6: Timeouts por p95
- Registra `elapsed_s` en cada noche. **Calcula p95 del tiempo total.**
- `run_timeout_s` debe ser >= p95 + 60 segundos de seguridad. Hoy 16.200 s (4 h 30 min) cubre la ventana (17.100 s de 00:00 a 04:45).
- `item_timeout_s` (Reader + Popularizer) y `editor_timeout_s` (Editor): calcula p95 por agente en Q2 (`duration_ms_media`). Si alguno roza su timeout, **subir 30 segundos**.

### Regla 7: Topes de fallos
- `max_consecutive_failures = 5` hoy. Si observas fallos monótonos (Reader o Popularizer cae durante X intentos seguidos), el contador ha funcionado. Si ves fallos alternos que no se acumulan, el contador es inerte y se puede dejar.
- `max_calls_per_item = 2` (un intento + reintento): si tasa de reintento por ítem es <10%, es raro. Si es >20%, afina el prompt (T42 padeció esto con saltos de línea).

---

## Interpretación de los datos

### Calidad percibida: por qué binarios, no escalas

El autor es juez único durante catorce días. Una escala 1–5 es **inconsistente consigo mismo** a dos semanas de distancia: un hallazgo que ayer te pareció `3/5` hoy puede parecer `4/5` sin razón, solo por contexto de ánimo. **Binario es legible**: «¿lo dejarías publicado?» → sí o no. Agregable al cierre: si la suma de publicados que dejarías es alta, el Editor acierta; si muchos descartados resultan rescatables, el Editor es demasiado restrictivo.

### Pool compartido y margen crítico

La primera noche real mostró 97,8% de uso del pool, margen mínimo para un candidato más. **Cálculo del freno del pool observado (38,2% de candidatos)**: con Reader real 153.753 tokens ÷ 39 ítems leídos ≈ 3.943 tokens/ítem, y Popularizer real 80.965 tokens ÷ 15 llamadas (14 candidatos con reintento incluido) ≈ 5.783 tokens/candidato: si Reader cuesta 39 × 3.943 = 153.777, quedan 240.000 − 153.777 = 86.223 tokens para Popularizer. Con 86.223 ÷ 5.783 ≈ 14,9 candidatos máximo, el pool se agota al **38,2%** de 39 ítems leídos (14,9 ÷ 39). **Escenarios de colas largas** (Reader 4.618 tokens/sesión, Popularizer 5.675 tokens/sesión): 41 llamadas Reader × 4.618 = 189.338 tokens, quedan 50.662 para Popularizer. Con 50.662 ÷ 5.675 ≈ 8,9 candidatos, el pool se agota al **22,9%** de 39 ítems (8,9 ÷ 39). Es decir: una noche con colas largas cierra antes y el Run queda `partial`, no por `completed`.

**Implicación**: el freno del pool es dinámico y depende de la composición observada del Reader. Una noche de descartes altos (Reader rápido) deja más pool; una noche de reintentos (JSON inválido, timeouts) lo aprieta. Vigilar Q3 `pct_pool` cada noche. Si regularmente cae a <5% de margen, las opciones son: (a) subir `nightly_tokens`; (b) subir `interest_score` para reducir candidatos; (c) compartir dinámicamente (algoritmo complejo, fase 2).

### Gasto lateral del CLI

El Haiku que el CLI de Claude Code consume internamente (~1.163 tokens/sesión) no aparece en `usage` leído por `BudgetGuard`, solo en `model_usage` y `total_cost_usd`. Se contabiliza sumando máximo entre `usage` y suma de `model_usage`, pero no se configura desde `pipeline.toml`. **La constante `k` de Regla 1 lo captura**: si el delta de Settings es siempre 1,20× los tokens de la noche, eso es gasto lateral.

### Hueco de observabilidad: candidatos perdidos sin traza

Un `Item` en estado `READ` (Reader produjo `Reading`) que el Popularizer rechaza por `TIMEOUT`, `AGENT_ERROR` o `RATE_LIMIT` vuelve a `next_unread` la noche siguiente, reutilizable. Pero un candidato que el Popularizer deja en estado `READ` **sin crear `Finding`** es invisible en los reportes: no aparece ni en Q5 (descartes desambiguados) ni en Q7 (candidatos huérfanos). Se detecta solo como diferencia `lecturas − candidatos` en Q4 (tramos ≥4).

Registra en `notas` cada noche si hay diferencia unexplained. Ejemplo: 39 lecturas, 14 candidatos → faltan 25. Si todos son `interest_score < 4` es correcto; si hay algunos `>= 4` sin `Finding`, es falta de llamada Popularizer o fallo silencioso. El número aporta indicio de problemas con colas o presupuesto erosionado.

---

## Datos de línea base (pre-T60, humos reales del 2026-09-17)

Para referencia y comparación:

| Métrica | Valor | Origen |
|---|---|---|
| Reader tokens/ítem | ~3.056 | T41 real (con cache), 2026-09-17 humo |
| Popularizer tokens/candidato sin reintento | ~4.560 | T42 real, 2026-09-17 humo |
| Popularizer con reintento (2 intentos) | ~9.120 | T42 real, 2026-09-17 humo |
| Editor tokens (N=3) | 3.381 | T43 real (Opus), 2026-09-17 humo |
| Gasto lateral CLI (Haiku)/sesión | ~1.163 | T42 real, CLI 2.1.274 + SDK 0.2.153 |
| Sesiones estimadas/noche | ~82 | 40 ítems × 2 turnos + 1 Editor + overhead |
| Gasto lateral total/noche **estimado** | ~95.366 | 82 sesiones × ~1.163 = 31,8% del presupuesto de 300.000. **Estimación, no medida**: no hay observación directa del gasto lateral. |

Estos datos proceden de humos manuales (T41–T43) ejecutados antes de la noche real. Rompen comparabilidad con ellos los cambios de prompt (`reader-v2`, `popularizer-v2`, `editor-v1`).

---

## Primera noche real observada (2026-09-18)

Ver tabla de arriba, fila única. Resumen:
- **Ejecución**: 12:34 (fuera de ventana nominal, con `hard_stop` ampliado a 23:59 para validación sin esperar medianoche). Ciclo completo: ingesta → Reader → Popularizer → Editor → persistencia.
- **Ingesta**: 34 ítems traídos de arXiv (todos duplicados de ejecución anterior), así que `items_fetched = 0` en `Run`.
- **Reader**: 39 ítems leídos, 1 fallido (2,5%). Total 41 llamadas (reintento de JSON).
- **Candidatos**: 14 con `interest_score >= 4` (35,9% de 39 leídos).
- **Popularizer**: 15 llamadas para 14 candidatos (7,1% de reintento).
- **Editor**: 1 intento, 14 candidatos, publicó 10, descartó 4 (confidence 0,60–0,95).
- **Gasto**: 246.608 de 300.000 tokens (82,2%). Desglose: Reader 153.753 (62%), Popularizer 80.965 (33%), Editor 11.890 (5%).
- **Pool**: 234.718 de 240.000 (97,8%) — margen mínimo.
- **Tiempo**: 613,7 segundos (10 min 14 s).
- **Calidad**: sin juzgar. La noche se ejecutó antes de que existiera este runbook, así que no hay juicios del autor y la fila no alimenta la precisión editorial agregada.

Datos de calibración decisivos: Hallazgo 1 (margen de pool crítico) y Hallazgo 2 (Editor reparametrizado).
