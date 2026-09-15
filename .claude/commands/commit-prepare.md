---
description: Prepara el commit de la tarea en curso (ficheros + mensaje) y espera aprobación. No ejecuta nada.
---

Delega en el subagente `committer` en modo PREPARE. Muestra al autor exactamente lo que devuelva: ficheros y mensaje.

Termina con la línea: "Si apruebas, ejecuta `/commit-execute`. Si quieres cambios en el mensaje, dímelos."

No hagas `git add`, no crees `.claude/.commit-approved`, no ejecutes el commit.
