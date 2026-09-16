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
- [ ] T02 · `tester.md` exige `addopts = "-m 'not manual'"` en `pyproject.toml`, pero nada se lo indica a `backend`, que es quien lo escribe: ¿se resuelve en el plan de T02? · opciones vistas: instrucción en el plan de T02, línea fija en `backend.md`
- [ ] T20 · Categorías arXiv iniciales · opciones vistas: `astro-ph.EP` + `astro-ph.GA`; añadir `astro-ph.HE`
- [ ] T41 · Versionar prompts con `prompt_version` en `AgentCall` · recomendado, coste bajo
- [ ] T42 · Idioma de los hallazgos publicados · opciones vistas: solo español; español + inglés desde el principio
- [ ] Fase 2 · Autenticación del CLI de Claude Code en el VPS · **no se resuelve en fase 1**
