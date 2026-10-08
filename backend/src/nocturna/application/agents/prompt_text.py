"""Saneado de datos que se incrustan en los prompts de usuario (Editor, redactor)."""


def inline(text: str) -> str:
    """Una sola línea, sin `<` ni `>`: un dato nunca debe poder cerrar la marca
    que envuelve el bloque (`</candidates>`, `</tension>`) ni abrir líneas nuevas."""
    return " ".join(text.replace("<", " ").replace(">", " ").split())


def num(value: float) -> str:
    return repr(float(value))
