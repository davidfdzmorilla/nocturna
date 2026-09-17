"""Logging JSON de la noche: cada registro de `logging`, una línea a `stderr`.

`application/` nunca invoca este módulo: es `cli.py` (T44, paso 3) quien
llama a `configure_json_logging()` una vez, al arrancar el proceso. Vive en
`infrastructure/` porque es un detalle de salida (el formato del log), no
una regla de negocio -- ver la distinción `stdout` (informe humano de
`cli.py`, con `print`) / `stderr` (telemetría del pipeline, con `logging`)
registrada en `docs/OPEN_DECISIONS.md`, entrada T20 (nº 42): "la regla de
`.claude/agents/backend.md` que prohíbe `print` rige la telemetría del
pipeline, no la salida legible de un comando interactivo".

Política de serialización (obligatoria a las 3 de la mañana: un log que
revienta al formatear no puede tumbar la noche entera, que es justo lo que
`application/budget.py` y `agents/runner.py` existen para evitar):

- Cada `LogRecord` se convierte en un `dict` con `ts` (ISO 8601, UTC),
  `level`, `logger`, `event` (de `extra={"event": ...}`, `None` si no se
  pasó) y `message` (`record.getMessage()`), más **todo** lo demás que
  venga en `extra=` -- cualquier atributo del registro que no sea uno de
  los que `logging.LogRecord` fija por defecto.
- El `dict` se serializa con `json.dumps(..., default=str)`: cualquier
  valor que `json` no sepa codificar de forma nativa (`UUID`, `datetime`,
  una excepción, una entidad de dominio) se degrada a su `str()` en vez de
  fallar. Se prefiere perder precisión de tipo -- una `UUID` queda como
  texto plano, no como estructura -- a que una sola línea de log lance una
  excepción.
- Si, pese a `default=str`, la serialización sigue fallando (por ejemplo,
  un objeto cuyo propio `__str__` lanza), o si el propio `record.getMessage()`
  lanza (formato `%s` con argumentos que no encajan), `format()` no
  propaga: cae a una línea JSON mínima (`ts`, `level`, `logger`, `event`,
  `message` con el problema descrito, sin los campos de `extra`) en vez de
  dejar sin registrar el evento o abortar el proceso.
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Atributos que `logging.LogRecord` fija por sí mismo (mirando una
# instancia real, no una lista mantenida a mano que pueda desincronizarse
# entre versiones de Python) más "message" y "asctime", que `Formatter`
# puede añadir en el camino estándar pero que este formateador no usa.
# Cualquier clave que no esté aquí viene de `extra=` del llamador y se
# vuelca tal cual al JSON de salida.
_RESERVED_LOG_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    """Convierte cada `LogRecord` en una línea JSON. Ver política de
    serialización en el docstring del módulo."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception as exc:  # noqa: BLE001 -- ver política del módulo
            message = f"<mensaje no formateable: {exc!r}>"

        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        event = record.__dict__.get("event")

        payload: dict[str, object] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "event": event,
            "message": message,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        try:
            return json.dumps(payload, default=str)
        except Exception:  # noqa: BLE001 -- nunca debe tumbar el proceso
            return json.dumps(
                {
                    "ts": ts,
                    "level": record.levelname,
                    "logger": record.name,
                    "event": event,
                    "message": message,
                    "log_serialization_error": True,
                },
                default=str,
            )


def configure_json_logging(level: int = logging.INFO) -> None:
    """Instala un único `StreamHandler(sys.stderr)` con formato JSON en el logger raíz.

    Sustituye cualquier handler que el logger raíz ya tuviera (idempotente:
    llamarlo varias veces no duplica líneas de log). Escribe a `stderr`,
    nunca a `stdout`, que queda reservado para el informe humano de
    `cli.py` (ver la política de serialización en el docstring del
    módulo).
    """
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
