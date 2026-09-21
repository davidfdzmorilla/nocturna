# Calibración — Nocturna Fase 1

Runbook para el autor durante catorce noches de ejecución real (T60). Cada mañana, tras la ejecución de `run-night`, se sigue el procedimiento de cinco pasos y se registra una fila en la tabla de abajo. **Si no es utilizable a las siete, medio dormido, la tarea falla.**

---

## Procedimiento de la mañana (cinco pasos)

### Paso 1: Ejecutar el informe de la noche
```bash
bash backend/scripts/night-report.sh
```
Genera siete consultas de solo lectura desde PostgreSQL sobre la última noche ejecutada. Toma dos minutos. Salida esperada: cabecera, desglose por agente, pool compartido vs reserva, `interest_score` por tramo, descartes desambiguados, cola de items, candidatos huérfanos. Apunta títulos y tasa de cambio respecto a ayer.

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

**Nota importante**: las versiones de `reader-v2`, `popularizer-v2` y `editor-v1` rompen comparabilidad con cualquier dato anterior a ellas. El campo `prompt_version` en `AgentCall` permite segregar los datos históricos si es necesario.

---

## Normas de operación

### Regla de noches fallidas (crítica)
**Una noche fallida no se relanza: se anota y se espera a mañana.**

Dos ejecuciones de `run-night` en la misma ventana (00:00–04:45) autorizan dos presupuestos completos. Además, arruinan el dato de esa noche (¿cuál de las dos cuentas?). **Si algo falla durante la ejecución**:
  - Anota el `run.status` que dejó (ej. `partial`, `failed`, `killed`).
  - NO relances en la misma ventana.
  - Espera a mañana noche para otro intento, que abrirá un Run nuevo con presupuesto fresco.

### Lanzamiento planificado con `launchd`

Si decides dejar el portátil despierto catorce noches y confiar en `launchd`, el agente se lanza a las **00:05 en la hora local del sistema** cada noche (`StartCalendarInterval` usa la zona horaria del sistema, no una fija; la ventana de gasto, en cambio, usa `window.timezone` de `pipeline.toml`), invocando un envoltorio shell que valida precondiciones, impide dobles lanzamientos y captura logs en ficheros separados. El envoltorio está en `backend/scripts/run-night-scheduled.sh`. **No contiene lógica de control de gasto ni de ventana horaria**: eso es exclusivo de `application/budget.py`, y duplicarlo en bash crearía una segunda fuente de verdad. El envoltorio solo fija el entorno, valida precondiciones, aplica la guarda del centinela y separa los logs. La plantilla del agente está en `backend/scripts/com.nocturna.run-night.plist.template` (se materializa con `sed` para insertar rutas absolutas del repositorio y del home).

#### Paso 1: Crear el directorio de logs (obligatorio, previo a cualquier lanzamiento)

```bash
mkdir -p ~/nocturna-logs
```

**Por qué es previo y obligatorio**: el agente `launchd` invoca el envoltorio que redirige salida a `~/nocturna-logs/night-YYYYMMDD.{out,err}.log`. Si el directorio no existe cuando `launchd` ejecute el trabajo (00:05 de la primera noche), la apertura del fichero falla silenciosamente y el único rastro queda en `/var/log/system.log` (inutilizable, mezcla de otros procesos). Es el escenario «la calibración no ocurrió anoche y nadie se entera hasta el desayuno».

#### Paso 2: Materializar el plist desde la plantilla

```bash
# Sustituciones: __REPO_ROOT__ por ruta absoluta del repositorio, __HOME__ por ruta del home

sed -e "s|__REPO_ROOT__|$(cd ~/Desktop/development.nosync/nocturna && pwd)|g" \
    -e "s|__HOME__|$HOME|g" \
    ~/Desktop/development.nosync/nocturna/backend/scripts/com.nocturna.run-night.plist.template \
    > ~/Library/LaunchAgents/com.nocturna.run-night.plist
```

El plist resultante contendrá:
- `Label`: `com.nocturna.run-night`
- `ProgramArguments`: ruta absoluta del envoltorio
- `StartCalendarInterval`: 00:05 cada día
- `StandardOutPath` y `StandardErrorPath`: `~/nocturna-logs/launchd.log` (solo para fallos del propio agente; los logs de la noche van a ficheros distintos)
- Sin `KeepAlive`, sin claves de reintento

#### Paso 3: Cargar el agente

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nocturna.run-night.plist

# Verificar que está cargado:
launchctl print gui/$(id -u)/com.nocturna.run-night
```

**Nota**: `launchctl load` (comando obsoleto desde macOS 10.11) devuelve mensajes de diagnóstico inútiles si falla (no dice qué etiqueta duplicada, ni qué camino mal formado). `launchctl bootstrap` es el equivalente moderno y da mejores errores.

#### Paso 4: Aviso de migración (si había un agente anterior cargado)

Si en una ejecución anterior instalaste manualmente un plist con la misma etiqueta (`com.nocturna.run-night`), puede seguir cargado. Cargar el nuevo sobre una etiqueta ya presente no garantiza que se sustituya, y el riesgo real es quedarte creyendo que corre el envoltorio mientras corre el comando crudo del runbook viejo.

**Comprueba siempre después de instalar**, en vez de fiarte del resultado de `bootstrap`: `launchctl print gui/$(id -u)/com.nocturna.run-night` debe mostrar en `ProgramArguments` la ruta de `run-night-scheduled.sh`. Si muestra un `bash -c` con `uv run nocturna run-night`, estás con el agente viejo.

**Mitigación**: antes de instalar, descarga el viejo si existe:

```bash
# Comprobar si está cargado:
launchctl print gui/$(id -u)/com.nocturna.run-night 2>/dev/null && echo "Agente cargado" || echo "No cargado"

# Si está cargado, descargar:
launchctl bootout gui/$(id -u)/com.nocturna.run-night

# Luego seguir con bootstrap del nuevo plist
```

**Coexistencia de dos plist**: si instalaras el nuevo plist con otra etiqueta (por error o propósito), ambos se ejecutarían a las 00:05. Resultado: 2 × `nightly_tokens` autorizados en la misma ventana, datos de la noche son inútiles. No hagas eso.

#### Paso 5: Interpretar códigos de salida

El envoltorio propaga los códigos de `nocturna run-night` (0–8) sin modificar, más tres códigos propios de precondiciones:

| Código | Significado | Qué hacer por la mañana |
|---|---|---|
| 0 | `RunStatus.COMPLETED` | Proceder con los pasos del runbook. Rellena la fila de la tabla. |
| 1 | Una excepción escapó de `RunNight`; el `Run` se cierra como `FAILED`, con el traceback completo en el log JSON | Leer el `.err.log`, anotar la causa en `notas`. No relanzar (regla de noches fallidas). |
| 7 | `RunStatus.PARTIAL`: la noche corrió pero no completó (típicamente el presupuesto se agotó antes del Editor, así que no se publicó nada) | Anotar `status=partial` y los tokens gastados. No relanzar. |
| 8 | `RunStatus.KILLED` | **No es un error crítico.** Es el desenlace normal de un disparo fuera de ventana: `BudgetGuard` deniega con `outside_window` en el primer `authorize`, cero tokens gastados. Anotar `status=killed` y la causa. No relanzar. |
| 75 | Precondiciones no listas: Docker no responde, `docker compose up` falló, o `nocturna-postgres` no llegó a `healthy` dentro de `NOCTURNA_WAIT_S` | Verificar Docker y PostgreSQL. **No hay gasto de tokens y el centinela se borra**, así que la noche sigue disponible. Ver el `.err.log`. |
| 76 | Ya se lanzó una ejecución esta noche (centinela del fichero) | Normal si `launchd` se dispara dos veces por error (rarísimo). Borrar el centinela solo si estás seguro de que no hay un `run-night` en vuelo. Regla de noches fallidas: nunca relanzar en la misma ventana. |
| 77 | Entorno o configuración inválidos: `uv` o `claude` no resolubles en `PATH`, o `NOCTURNA_WAIT_S`/`NOCTURNA_LOG_DIR` mal formadas | Verificar las variables si las definiste y que `uv` y `claude` se resuelven. Ocurre **antes** de crear el centinela y de tocar Docker: cero gasto, noche aún disponible. Arreglado el problema, esperar a mañana noche. |

#### Paso 6: Comprender los logs

El envoltorio genera dos ficheros por noche (fuera del repo):

| Fichero | Contenido |
|---|---|
| `~/nocturna-logs/night-YYYYMMDD.out.log` | Salida estándar de `nocturna run-night` y línea final con timestamps. |
| `~/nocturna-logs/night-YYYYMMDD.err.log` | Salida de error estándar. **Mezcla telemetría JSON del pipeline con `print` en texto libre de `cli.py` (aviso de doble presupuesto, denegación de `BudgetGuard`) e invocaciones a `docker compose`.** **No es JSON puro**. |

Para extraer solo la telemetría JSON del `.err.log`:

```bash
grep '^{' ~/nocturna-logs/night-YYYYMMDD.err.log
```

No confundas esto con `.jsonl`. El `.err.log` contiene líneas soltas de JSON seguidas de líneas de texto plano.

**Desinstalación** (al cerrar T60):

```bash
# Descargar el agente:
launchctl bootout gui/$(id -u)/com.nocturna.run-night

# Borrar el plist:
rm ~/Library/LaunchAgents/com.nocturna.run-night.plist

# Borrar la tarea de despertar (si la instalaste):
sudo pmset repeat cancel

# Verificar que no hay horarios programados:
pmset -g sched

# Borrar los logs (tras volcarlos a un fichero seguro):
rm ~/nocturna-logs/night-*.{out,err}.log
rm ~/nocturna-logs/.launched-*
```

#### Paso 7: Despertar automático de la máquina

Para que `launchd` ejecute el trabajo a las 00:05, la máquina debe estar despierta. Sin configuración adicional, si tu portátil entra en suspensión, la tarea no se ejecuta.

```bash
# Programar despertar todos los días a las 00:00 (antes del lanzamiento a 00:05):
sudo pmset repeat wakeorpoweron MTWRFSU 00:00:00

# Verificar:
pmset -g sched
```

**Requisito crítico**: el portátil **debe estar enchufado**. `caffeinate -s` solo impide la suspensión mientras hay corriente alterna. Sin adaptador de energía, la máquina no se despierta a las 00:00 aunque lo pidas.

---

## El centinela: alcance y límites

El fichero `~/.launched-YYYYMMDD` es una **guarda por convención, no una barrera estructural**. Su único propósito es proteger contra un segundo disparo de `launchd` del **mismo agente** la misma noche, si algo falló al intentar cargar o descargar.

**Sí cubre**:
- Un segundo disparo accidental de `launchd` (si el proceso de carga falla y lo reintentas).
- `launchctl kickstart` (incluido con `-k`), que dispara el trabajo fuera de horario.
- Máquina que despierta a las 00:03 (de baterías) y recupera el trabajo perdido a las 00:07 (enchufada): ambos ven la misma fecha, el segundo sale con código 76.

**No cubre** (y son riesgos reales):
- `uv run nocturna run-night` a mano en paralelo, o en serie después del lanzamiento automático. El backend solo imprime una advertencia por stderr (a las 00:05 del día siguiente nadie lo lee). 2 × `nightly_tokens` se autorizan.
- Un segundo plist con otra etiqueta (por error, si alguien instaló dos). Ambas se ejecutan, es desastre.
- Borrar el centinela por error al limpiar `~/nocturna-logs`. La noche siguiente cree que es un nuevo lanzamiento.
- `NOCTURNA_LOG_DIR` o `HOME` distinto entre invocaciones (usuario distinto, o el mismo usuario en máquina distinta). Los centinelas no se ven.
- `run-item` sobre la misma noche, que abre su propio camino de gasto (no es controlado por la guarda del centinela).

La efectividad del centinela depende de una **regla social**: **durante T60 (catorce noches de calibración), el único modo de lanzar una noche es a través del envoltorio**, automático o invocado a mano con `bash backend/scripts/run-night-scheduled.sh`. Esa prescripción es lo que convierte la guarda por convención en efectiva. Ver **Regla de noches fallidas** arriba.

---

## Versión del CLI y ruptura de baseline

Cada noche, el script `backend/scripts/night-report.sh` imprime dos líneas de versión:
```
CLI version: 2.1.274
SDK version: 0.2.153
```

**Estos números deben anotarse cada día** en la tabla (`CLI` y `SDK`), porque el gasto de tokens **varía entre versiones sin que cambie nada nuestro**. Ejemplo histórico:

- T40 (2026-09-16): CLI 2.1.274, Haiku 929 tokens/sesión
- T41 (2026-09-17): **mismo CLI 2.1.274**, Haiku **1.163 tokens/sesión** (+234 tokens, +25,2%)
- T43 (2026-09-17): **mismo CLI y SDK**, Haiku 929, 1.163, 1.240 en tres sesiones idénticas (variabilidad intra-versión)

**Regla**: si **el CLI o el SDK cambian de versión entre dos noches** (incluso dentro de la misma semana), marca esa noche como **ruptura de baseline** en `notas`. Significa que esa noche **no es comparable** a las anteriores: el gasto es ruido de versión, no de comportamiento del pipeline.

La constante `k` de calibración (Regla 1, cierre de T60) solo se calcula sobre noches con **versión de CLI y SDK estables**. Si una noche tiene versión distinta, se excluye del cálculo, igual que se excluye el `uso_interactivo = sí`.

Detalle técnico: `claude-agent-sdk` se congela con `uv.lock` (no cambia entre noche y noche), pero el binario `claude` se actualiza solo (reenlace de `/opt/homebrew/bin/claude`). Entre una noche y la siguiente el autor puede no tocar nada de Nocturna, pero si Apple o Homebrew actualizó el CLI, la versión cambia. **Es información externa fuera de nuestro control**, pero es crítica para la calibración.

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
