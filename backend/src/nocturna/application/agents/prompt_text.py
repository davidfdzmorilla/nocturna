"""Saneado de datos que se incrustan en los prompts de usuario (Editor, redactor)."""

import math


def inline(text: str) -> str:
    """Una sola línea, sin `<` ni `>`: un dato nunca debe poder cerrar la marca
    que envuelve el bloque (`</candidates>`, `</tension>`) ni abrir líneas nuevas."""
    return " ".join(text.replace("<", " ").replace(">", " ").split())


def num(value: float) -> str:
    return repr(float(value))


def round_to_error(
    value: float, err_plus: float | None, err_minus: float | None
) -> tuple[str, str, str] | None:
    """Valor y errores redondeados según el error menor (T96, regla de presentación).

    Decimales = los necesarios para que el menor error en valor absoluto tenga
    2 cifras significativas; el mismo número de decimales en valor y errores,
    sin notación exponencial. Nunca menos de 0 decimales: con un error menor
    de 100 o más, el error sale con todas sus cifras enteras. Si el redondeo
    sube de orden de magnitud (0.0996 → "0.100") quedan 3 cifras. Devuelve
    `None` si falta algún error o el menor es 0 (no hay base para redondear:
    el llamador deja el valor tal cual). Valores y errores finitos y errores
    no negativos los garantiza `CatalogSolution`.
    """
    if err_plus is None or err_minus is None:
        return None
    smallest = min(abs(err_plus), abs(err_minus))
    if smallest == 0:
        return None
    decimals = max(0, 1 - math.floor(math.log10(smallest)))
    return (
        f"{value:.{decimals}f}",
        f"{abs(err_plus):.{decimals}f}",
        f"{abs(err_minus):.{decimals}f}",
    )


def percent_4sf(fraction: float) -> str:
    """Una fracción como porcentaje con 4 cifras significativas, sin exponente."""
    pct = abs(fraction) * 100
    if pct == 0:
        return "0"
    decimals = max(0, 3 - math.floor(math.log10(pct)))
    return f"{pct:.{decimals}f}"
