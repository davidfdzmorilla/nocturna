# Deuda técnica

Solo deuda real, detectada en código existente. Formato: `- <fichero> · <qué> · <por qué se aceptó> · <tarea en la que se detectó>`.

- `.claude/hooks/guard-bash.sh` y `.claude/hooks/stop-check.sh` · El marcador `.commit-approved` se borra al cierre del turno, impidiendo que `/commit-execute` lo use en modo EXECUTE. Además, `guard-bash.sh` inspecciona toda la cadena del comando e impide con falsos positivos: la ruta del marcador (`.claude/.commit-approved`) contiene "claude", y cualquier creación del marcador + commit a la vez se bloquea · El flujo de "preparar" vs "ejecutar" commit requería un mecanismo de persistencia entre turnos; descubierto solo en T00 · T00
