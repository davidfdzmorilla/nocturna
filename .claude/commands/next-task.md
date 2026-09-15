---
description: Toma la siguiente tarea pendiente de docs/PLAN_TAREAS.md y produce el plan para aprobación
---

Actúas como orquestador. Pasos:

1. Lee `CLAUDE.md` y `docs/PLAN_TAREAS.md`.
2. Selecciona la primera tarea con estado `pending` cuyas dependencias estén todas `done`. Si el usuario pasó un id en `$ARGUMENTS`, usa esa tarea (comprueba igualmente sus dependencias y avisa si no están cumplidas).
3. Marca la tarea como `in_progress` en `docs/PLAN_TAREAS.md`.
4. Delega en el subagente `architect` con: id y texto completo de la tarea, y la instrucción de producir el plan en su formato estándar.
5. Presenta el plan al autor tal cual, seguido de una única línea: "¿Apruebo el plan? (sí / cambios)".
6. **Para aquí.** No ejecutes nada hasta recibir aprobación explícita. "Ok", "sí", "adelante" cuentan como aprobación; cualquier otra cosa son cambios que devuelves al `architect`.
