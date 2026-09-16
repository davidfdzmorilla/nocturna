# Decisiones abiertas

Formato: `- [ ] <tarea> · <pregunta> · opciones vistas: … ` → al resolver: `- [x] … · resuelto <fecha>: <respuesta>`.

- [x] T00 · Nombre del proyecto y del paquete Python · resuelto 2026-09-15: **Nocturna**, paquete `nocturna`
- [ ] T00 · ¿Qué estrategia de ramas sigue el proyecto? · opciones vistas: rama por tarea + PR a `main` (practicado en T00); commits directos en `main`
- [ ] T00 · ¿Qué licencia lleva el repositorio? · opciones vistas: sin licencia (privado), MIT, AGPL
- [ ] T00 · ¿El repositorio es público en GitHub o privado? · opciones vistas: público, privado
- [x] T01 · `.claude/agents/` ya está completo en el commit inicial: ¿se cierra T01 como verificación de los ocho ficheros contra `AGENTS.md`, o se reescriben? · resuelto 2026-09-16: cierre como verificación documentada más un delta mínimo de seis ediciones; los ocho ficheros ya satisfacían rol, herramientas y reglas, pero la definición de `backend` no alcanzaba para delegar T02
- [ ] T01 · ¿Qué subagente tiene la propiedad de `.claude/agents/`, `.claude/commands/`, `.claude/skills/` y `.claude/hooks/`? · opciones vistas: `docs-keeper` (practicado en T01, por ser markdown de proceso), `backend`, subagente nuevo
- [ ] T01 · `Bash(git commit*)` no está en `allow` ni en `deny` de `.claude/settings.json`, así que el commit dispara prompt al autor: ¿es deliberado como refuerzo de "preparar ≠ ejecutar"? · opciones vistas: dejarlo así, añadirlo a `allow`
- [ ] T01 · `docs-keeper.md` prescribe una línea `Cerrada: <fecha>` en `PLAN_TAREAS.md` que la cabecera del fichero no declara y que T00 y T01 no llevan: ¿se declara el campo en la cabecera y se regulariza, o se elimina de `docs-keeper.md`? · opciones vistas: declarar el campo, eliminar la prescripción
- [x] T02 · `tester.md` exige `addopts = "-m 'not manual'"` en `pyproject.toml`, pero nada se lo indica a `backend`, que es quien lo escribe: ¿se resuelve en el plan de T02? · resuelto 2026-09-16: `addopts = "-m 'not manual'"` y registro del marcador `manual` fijos en `backend/pyproject.toml` antes del primer test
- [ ] T02 · ¿Qué día y hora reinicia el límite semanal de la suscripción? (`budget.weekly_reset_weekday`, `weekly_reset_hour`) · opciones vistas: lunes 00:00 (placeholder actual, inerte mientras `reset_day_multiplier = 1.0`); observarlo en Settings > Usage durante T60
- [ ] T02 · ¿Es correcto `budget.editor_reserve_tokens = 60000` (20% del presupuesto nocturno)? · opciones vistas: mantenerlo y calibrarlo en T60; otro porcentaje
- [ ] T02/T30 · `Settings.config_path` está declarado pero nadie lo usa: `load_pipeline_config` calcula su propia ruta · opciones vistas: borrar el campo; cablearlo de forma consciente y documentada (útil en tests, peligroso fuera)
- [ ] T02/T30 · Zona horaria de la ventana: `CLAUDE.md` dice "hora local" · opciones vistas: hora local del sistema (comportamiento actual, sin clave); clave IANA explícita `window.timezone` en el TOML
- [ ] T02/T30 · ¿Puede `application/budget.py` importar `BudgetConfig`/`LimitsConfig`/`WindowConfig` de `infrastructure/config.py` solo para anotar? · opciones vistas: importar desde infraestructura (rompe la flecha `application → domain` aunque sean datos puros); mover el esquema a `application/` y dejar solo el lector en `infrastructure/`
- [ ] T02/T40 · Modelos por rol como alias (`"sonnet"`, `"opus"`) frente a IDs de modelo fijados · opciones vistas: mantener alias (actual); fijar IDs exactos cuando T40 consulte la documentación vigente del SDK
- [ ] T02 · CLAUDE.md "Estado actual" está fuera de fecha (dice "Fase 0: base documental. Sin código.") · opciones vistas: actualizar en T02; dejar como decisión abierta para siguiente sesión
- [ ] T20 · Categorías arXiv iniciales · opciones vistas: `astro-ph.EP` + `astro-ph.GA`; añadir `astro-ph.HE`
- [ ] T41 · Versionar prompts con `prompt_version` en `AgentCall` · recomendado, coste bajo
- [ ] T42 · Idioma de los hallazgos publicados · opciones vistas: solo español; español + inglés desde el principio
- [ ] Fase 2 · Autenticación del CLI de Claude Code en el VPS · **no se resuelve en fase 1**
