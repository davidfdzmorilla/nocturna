---
description: Ejecuta el commit preparado y aprobado por el autor. Solo tras /commit-prepare.
---

Solo se invoca después de `/commit-prepare` en esta misma conversación y con el mensaje aprobado por el autor. Si no se cumple, di qué falta y para.

1. Crea el fichero `.claude/.commit-approved` con el contenido: la fecha ISO y la primera línea del mensaje aprobado. Este fichero es lo que permite al hook `guard-bash.sh` dejar pasar `git commit`; se borra automáticamente al terminar el turno.
2. Delega en `committer` en modo EXECUTE con la lista de ficheros y el mensaje aprobado, literal.
3. Muestra el `git log -1 --stat` que devuelva.
4. Borra `.claude/.commit-approved`.
5. Ejecuta `/status` para mostrar dónde queda el proyecto.
