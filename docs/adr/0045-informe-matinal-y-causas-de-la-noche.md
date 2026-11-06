# ADR 0045 · Informe matinal y causas de la noche

Fecha: 2026-11-05 · Estado: aceptado · Tarea: T105 · Relacionados: ADR 0011, 0019, 0022, 0044

## Contexto

ADR 0044 (C) exige un aviso de las noches `partial`, `killed` o sin `Run`, con su causa clasificada, antes de las 09:00. Hasta T105 la revisión de cada mañana era manual (`docs/CALIBRACION.md`, cinco pasos) y ninguna noche se avisaba.

En el periodo 2026-09-30 a 2026-11-03 hubo 8 noches `partial`: 5 por `ArxivUnavailable` en la ingesta, 2 con 0 tokens (2026-10-29 y 10-30) y 1 sin causa anotada. Las dos de octubre llegaron a través del 429 de la suscripción, que el proveedor recibió como `agent_error` y no como `LLMRateLimited`; el cortacircuitos cortó la noche.

## Decisión

1. **Fuente de la clasificación: solo datos estructurados.** `runs` (`status`, `notes` con `ingest=` y `end=`), recuentos de `agent_calls` (estado y tokens), los ficheros `night-YYYYMMDD.status` y `archive-YYYYMMDD.status` (líneas `clave=código`), la existencia del centinela `.launched-YYYYMMDD` y de los logs. El contenido de los logs no se lee nunca.
2. **Clasificación pura** en `domain/night_report.py`: enum `NightCause` (21 valores), `classify_night` y `parse_run_notes`. Lo que no encaja da `unclassified` con el dato bruto (p. ej. `end=<motivo>`, `status=<estado>`, `run-night=<código>`).
3. **Noches del 10-29 y 10-30: `circuit_breaker`**, no `rate_limited`. La causa queda en el dato del cortacircuitos (`end=circuit_breaker_<rol>`, llamadas con error y con 0 tokens). El 429 no se distingue aquí: es T111.
4. **Disparos.** Tras `run-night` (`run-night-scheduled.sh`, con `--night` igual a la fecha del `night_key`; también tras los abortos 75), tras `archive-snapshot` (`archive-snapshot-scheduled.sh`) y por launchd a las 07:30 con `RunAtLoad` (`morning-report-scheduled.sh`). Los códigos 76 y 77 de `run-night` no disparan. `evaluate-tensions` y `solution-tensions` sueltos no disparan: ya van dentro de `run-night` y `archive-snapshot`.
5. **Salida.** `morning-YYYYMMDD.txt` en `NOCTURNA_LOG_DIR`, permisos 0600, escritura atómica (fichero temporal y `os.replace`). Una línea por informe en `morning-deliveries.log` con `notified=ok|fail|skip`. Ambos ficheros son la evidencia del criterio (e) de ADR 0044 para T110. Sin borrado automático: ≈1 KB por noche.
6. **Aviso.** `osascript` con título y cuerpo como argumentos (`argv`), nunca interpolados en el AppleScript. Los disparos tras `run-night` y `archive-snapshot` avisan siempre. El programado avisa si la noche no es `completed` o si no hay entrega previa con `notified=ok` para esa noche.
7. **Sin secretos.** El informe y los logs no llevan DSN, variables de entorno ni rutas absolutas. De una excepción solo se registra el nombre de la clase. Si la base no responde, el informe se escribe igual con la causa «base de datos inaccesible».
8. **Sin LLM ni gasto.** Ningún import de proveedor, SDK ni `budget.py`. Solo `SELECT`.
9. **Identificación del `Run` de la noche:** heurística por `notes` (`is_night_run`), sin migración.
10. **Idioma:** español.

El plan de T105 se aprobó el 2026-11-05 con las diez decisiones recomendadas. Las preguntas que el autor respondió (hora del job, cuándo notifica, retención, idioma, identificación del `Run`, salida 77, Mac apagado, diagnóstico del 429) están resueltas con la opción recomendada en `docs/OPEN_DECISIONS.md`, sección T105. Queda abierta una: si `notes` guarda un solo motivo `end=` o todos (referida a T106).

## Alternativas descartadas

- **`terminal-notifier`**: dependencia nueva sin ventaja sobre `osascript`, que ya está en macOS.
- **Heurística de 429 sobre el texto de los logs**: frágil, y expone contenido que puede llevar secretos.
- **Columna `kind` en `runs`, con migración**: separaría `run-night` de `run-item` sin heurística. Coste de esquema para un problema que `notes` resuelve.
- **Correo SMTP**: descartado en ADR 0044 (D6).
- **Job matinal a 06:00 u 08:30**: se eligió 07:30, con `RunAtLoad` para cubrir el arranque tardío.

## Consecuencias

- **Gasto:** 0 tokens. No cambia `budget.py`, `nightly_tokens` ni la ventana.
- **Límites conocidos:**
  - Con el Mac apagado durante toda la ventana, el informe sale al iniciar sesión, hasta pasadas las 09:00. Límite físico; anotado para T110.
  - Con `NOCTURNA_LOG_DIR` inválido, el envoltorio sale con 77 antes de cualquier informe. Solo queda `launchd.log`. El vigía exterior de T108 lo cubre.
  - `notes` guarda un solo motivo `end=`. Una noche con varios motivos se clasifica por el primero. Abierta para T106.
  - Una noche no `completed` se vuelve a avisar en cada sesión iniciada y en cada 07:30 del mismo día, hasta que se resuelva. Es deliberado (decisión 6).
  - Un `Run` de `run-item` que acaba `FAILED` sin `notes` puede tomarse por el de la noche (`is_night_run`). Registrado en `TECHNICAL_DEBT.md`.
- **Documentos:** `ARCHITECTURE.md` (flujo de la noche y módulo del informe), `CALIBRACION.md` (instalación del plist, Paso 8) y `CLAUDE.md` (comando).
