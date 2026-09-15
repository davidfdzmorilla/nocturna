---
description: Ejecuta el plan aprobado de la tarea en curso delegando en los subagentes en orden
---

Actúas como orquestador. Solo se invoca con un plan aprobado por el autor en esta misma conversación; si no lo hay, di que falta `/next-task` y para.

1. Sigue la sección "Orden de ejecución" del plan aprobado, delegando en cada subagente (`backend`, `database`, `frontend`, `tester`) con el fragmento del plan que le corresponde y el resultado del subagente anterior. Un subagente por paso; no paralelices pasos que dependan entre sí.
2. Si un subagente reporta una decisión abierta, no la resuelvas: anótala y sigue con la opción reversible que indique el plan. Si es bloqueante, para y pregunta al autor.
3. Si un subagente reporta un fallo de tests, reasigna al responsable del código (no al `tester`) con el fallo literal. Máximo dos iteraciones; a la tercera, para y muestra el problema al autor.
4. Al terminar la implementación, delega en `reviewer`. Si la tarea toca `budget.py`, `LLMProvider` o un agente: dos pasadas del `reviewer`, la segunda centrada en control de gasto.
5. Si el veredicto es RECHAZADO, corrige los bloqueantes (vuelta al paso 1 solo para esos puntos) y repite la revisión. Máximo dos ciclos.
6. Con veredicto APROBADO, delega en `docs-keeper` para cerrar la tarea en `docs/`.
7. Presenta al autor: resumen de lo hecho (5 líneas máximo), resultado de tests, veredicto de revisión, decisiones abiertas nuevas. Termina con: "Listo para `/commit-prepare`."

No hagas commit. No hagas push.
