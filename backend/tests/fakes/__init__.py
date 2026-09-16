"""Dobles de test compartidos por toda la suite (`FakeClock`, y en T40 `FakeLLMProvider`).

Nada de este paquete llama a Claude ni al reloj del sistema: es exactamente
lo que hace posible que los tests sean deterministas y no toquen la
suscripción (ver `CLAUDE.md`, `.claude/skills/testing-without-claude`).
"""
