"""Soporte del experimento T71.b: atribución de medidas del Reader experimental.

Este módulo NO es un test (pytest no lo recolecta: no empieza por `test_` ni
termina en `_test`). Es el código compartido entre el humo manual
(`tests/manual/test_reader_attribution_smoke.py`, que llama a Claude de
verdad) y los tests normales que escribirá el tester
(`tests/test_reader_attribution_experiment.py`, que no llaman a Claude,
igual que exige la skill `testing-without-claude`).

## Qué mide el experimento

`prompts/reader.md` (producción) no pide medidas numéricas. El prompt
experimental de este módulo (`reader-measures-exp1.md`, en este mismo
directorio) añade un quinto campo, `measurements`, y este módulo valida esa
salida (`ExperimentalReaderOutput`/`parse_experimental_output`), la compara
contra lo que el autor confirmó a mano para los cuatro abstracts de
`tests/fixtures/t71b/abstracts.json` (`EXPECTED`, `evaluate`), y --
opcionalmente, solo si `NOCTURNA_ALLOW_ARCHIVE_QUERY` está en el entorno --
calcula la discrepancia en sigma contra el NASA Exoplanet Archive
reutilizando las piezas de `scripts/exoplanet_viability.py` (T71),
`sigma_rows`.

## Por qué vive en `tests/experiments/`, no en `application/agents/`

No es parte del pipeline de producción: es un experimento de una sola tarea
(T71.b) para decidir si vale la pena que el Reader real extraiga medidas
algún día. Igual que `scripts/exoplanet_viability.py` no toca `src/nocturna`
para su lógica de cruce con el archivo, este módulo tampoco: solo reutiliza,
por `importlib`, las funciones puras de ese script que ya tienen su propio
contrato probado (`tests/test_exoplanet_viability_script.py`).
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from nocturna.application.agents.parsing import InvalidAgentOutput, extract_json_object
from nocturna.application.agents.reader_output import ReaderOutput
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.budget import BudgetDenied
from nocturna.application.use_cases.read_item import ReadItem
from nocturna.domain.entities import Item
from nocturna.domain.llm import AgentResult

# ---------------------------------------------------------------------------
# Versión de prompt y carga del prompt/abstracts
# ---------------------------------------------------------------------------

#: Igual criterio que `READER_PROMPT_VERSION`/`POPULARIZER_PROMPT_VERSION`
#: (`application/agents/prompt_loader.py`): constante a mano, subida si
#: `reader-measures-exp1.md` cambia. Un solo valor para todo el experimento
#: T71.b -- no hay una segunda versión todavía.
EXPERIMENT_PROMPT_VERSION = "reader-measures-exp1"

_PROMPT_PATH = Path(__file__).parent / "reader-measures-exp1.md"
_ABSTRACTS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "t71b" / "abstracts.json"


def load_experiment_prompt() -> str:
    """Lee `reader-measures-exp1.md`. Falla ruidosamente si no existe (mismo
    criterio que `application/agents/prompt_loader.py::load_prompt`): un
    `system_prompt` en blanco no debe gastar una llamada real."""
    if not _PROMPT_PATH.is_file():
        raise FileNotFoundError(f"no existe el prompt experimental en {_PROMPT_PATH}")
    return _PROMPT_PATH.read_text(encoding="utf-8")


def load_abstracts() -> list[dict[str, str]]:
    """Lee `tests/fixtures/t71b/abstracts.json` (4 abstracts confirmados)."""
    return json.loads(_ABSTRACTS_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Esquema de salida experimental
# ---------------------------------------------------------------------------

_UNIT_BY_PARAMETER: dict[str, frozenset[str]] = {
    "mass": frozenset({"M_jup", "M_earth"}),
    "radius": frozenset({"R_jup", "R_earth"}),
    "period": frozenset({"day"}),
}


class MeasurementOut(BaseModel):
    """Una medida atribuida a un planeta concreto, tal como la pide
    `reader-measures-exp1.md`. `strict=True`/`extra="ignore"`: mismo criterio
    que `ReaderOutput` (ver su docstring) -- un campo de más no cuesta un
    reintento, un tipo laxo (`"4"` en vez de `4`) sí debe verse en el primer
    intento.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    planet_name: str
    parameter: Literal["mass", "radius", "period"]
    value: float
    err_plus: float | None = None
    err_minus: float | None = None
    unit: Literal["M_jup", "M_earth", "R_jup", "R_earth", "day"]
    limit: Literal["none", "upper", "lower"]
    origin: Literal["this_work", "literature"]
    evidence: str

    @field_validator("planet_name", "evidence")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no puede estar vacío ni en blanco")
        return value

    @field_validator("value")
    @classmethod
    def _value_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("'value' debe ser un número finito")
        return value

    @field_validator("err_plus", "err_minus")
    @classmethod
    def _err_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("el error no puede ser negativo")
        return value

    @model_validator(mode="after")
    def _unit_coherent_with_parameter(self) -> MeasurementOut:
        allowed = _UNIT_BY_PARAMETER[self.parameter]
        if self.unit not in allowed:
            raise ValueError(
                f"unit={self.unit!r} no es coherente con parameter={self.parameter!r} "
                f"(esperado uno de {sorted(allowed)})"
            )
        return self


class ExperimentalReaderOutput(ReaderOutput):
    """`ReaderOutput` (producción) + `measurements`. Mismo `strict=True`/
    `extra="ignore"` heredado de `ReaderOutput` -- Pydantic conserva
    `model_config` de la clase base salvo que se sobrescriba, y aquí no se
    sobrescribe. `measurements` es obligatorio (sin default): la salida
    experimental siempre debe incluir la lista, vacía si no hay medidas, en
    vez de omitir el campo -- omitirlo cuenta como salida inválida y
    dispara el reintento del Reader, igual que un JSON mal formado.
    """

    measurements: list[MeasurementOut]


def parse_experimental_output(text: str) -> ExperimentalReaderOutput:
    """Extrae y valida la salida cruda del Reader experimental.

    Mismo patrón que `application/agents/reader_output.py::parse_reader_output`:
    compone `extract_json_object` con la validación Pydantic y traduce
    `ValidationError` a `InvalidAgentOutput`, para que `AgentRunner.run`
    trate una salida con forma inválida igual que trataría la del Reader de
    producción (un intento contabilizado, un reintento).
    """
    payload = extract_json_object(text)
    try:
        return ExperimentalReaderOutput.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAgentOutput(
            f"salida experimental del Reader con forma inválida: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Comparación de texto: evidencia literal
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def evidence_in_abstract(evidence: str, abstract: str) -> bool:
    """¿Es `evidence` una subcadena literal de `abstract`, salvo espacios?

    Normaliza espacios (colapsa repetidos, recorta extremos) en ambos lados
    antes de comparar como subcadena -- un modelo que reproduce la cita
    exacta pero con un salto de línea distinto al del JSON de origen no debe
    contar como "no literal". No normaliza mayúsculas, acentos ni
    puntuación: eso ya sería tolerar una paráfrasis, que es justo lo que
    `evidence` no puede ser (regla global de `reader-measures-exp1.md`).
    """
    return _normalize_whitespace(evidence) in _normalize_whitespace(abstract)


# ---------------------------------------------------------------------------
# Carga perezosa de scripts/exoplanet_viability.py (T71)
# ---------------------------------------------------------------------------

#: Mismo nombre de módulo que usa `tests/test_exoplanet_viability_script.py`
#: (`_load_script`), a propósito: si ese fichero ya cargó el script en esta
#: misma sesión de pytest, `sys.modules["exoplanet_viability"]` ya existe y
#: se reutiliza tal cual, sin un segundo `exec_module`.
_EXOPLANET_VIABILITY_MODULE_NAME = "exoplanet_viability"
_EXOPLANET_VIABILITY_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "exoplanet_viability.py"
)


def _load_exoplanet_viability_module() -> ModuleType:
    """Carga `scripts/exoplanet_viability.py`, reutilizando `sys.modules` si
    ya está cargado bajo `_EXOPLANET_VIABILITY_MODULE_NAME` (ver arriba).
    Importar el script no tiene efectos secundarios: `_require_opt_in()` y
    el resto del acceso a red/base de datos viven dentro de `main()`, que
    solo se ejecuta bajo `if __name__ == "__main__":` -- no se dispara al
    cargar el módulo con un nombre distinto vía `importlib`.
    """
    existing = sys.modules.get(_EXOPLANET_VIABILITY_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        _EXOPLANET_VIABILITY_MODULE_NAME, _EXOPLANET_VIABILITY_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_EXOPLANET_VIABILITY_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def to_catalog_measurement(measurement: MeasurementOut) -> object | None:
    """Traduce una `MeasurementOut` al `Measurement` de
    `scripts/exoplanet_viability.py` (mismas constantes de conversión
    Júpiter->Tierra que usa ese script), o `None` si la medida no es
    utilizable para cruzarla con el archivo:

    - `limit != "none"`: un límite no es una medida puntual comparable.
    - falta `err_plus` o `err_minus`: `sigma()` necesita un error en cada
      lado.
    - `origin == "literature"`: no es un resultado de este paper, así que
      no tiene sentido preguntar "¿cuánto se desvía este paper del
      archivo?" -- el propio valor probablemente YA es del archivo.

    Devuelve `object | None` en la firma (no el tipo del script, cargado en
    tiempo de ejecución vía `importlib`, así que no hay una anotación
    estática que lo nombre); en tiempo de ejecución es una instancia de
    `exoplanet_viability.Measurement`.
    """
    if measurement.limit != "none":
        return None
    if measurement.err_plus is None or measurement.err_minus is None:
        return None
    if measurement.origin == "literature":
        return None

    ev = _load_exoplanet_viability_module()
    if measurement.parameter == "mass":
        factor = ev.M_JUP_IN_M_EARTH if measurement.unit == "M_jup" else 1.0
        parameter = "mass_earth"
    elif measurement.parameter == "radius":
        factor = ev.R_JUP_IN_R_EARTH if measurement.unit == "R_jup" else 1.0
        parameter = "radius_earth"
    else:
        factor = 1.0
        parameter = "period_days"

    return ev.Measurement(
        parameter=parameter,
        value=measurement.value * factor,
        err_plus=measurement.err_plus * factor,
        err_minus=measurement.err_minus * factor,
    )


@dataclass(frozen=True, slots=True)
class SigmaRow:
    """Una fila de discrepancia en sigma entre una medida atribuida y una
    solución del NASA Exoplanet Archive para el mismo planeta y parámetro."""

    external_id: str
    planet_name: str
    parameter: str
    paper_value: float
    paper_unit: str
    refname: str
    default_flag: bool
    sigma: float


def sigma_rows(attributed: dict[str, Sequence[MeasurementOut]], client: object) -> list[SigmaRow]:
    """Sigma de cada medida utilizable contra las soluciones del archivo.

    `attributed` mapea `external_id` -> medidas atribuidas a ese abstract
    (normalmente `AttemptRecord.measurements` de `run_attribution`).
    `client` es un `exoplanet_viability.ArchiveClient` ya construido (con su
    propio `max_requests`, ver `test_reader_attribution_smoke.py`).

    Reutiliza, del script de T71 (`_load_exoplanet_viability_module`):
    `_fetch_index` (índice de nombres del archivo), `match_object` (empareja
    el nombre atribuido por el Reader contra ese índice), `_fetch_solutions_by_planet`
    (todas las filas de la tabla `ps` -- soluciones múltiples -- para los
    planetas emparejados) y `sigma` (la discrepancia en sigma, fórmula
    provisional de T73).

    Una medida sin `to_catalog_measurement` utilizable, o un nombre que no
    empareja con ningún planeta del archivo, no produce ninguna fila -- no
    es un error, es "no hay con qué comparar esta medida todavía".
    """
    ev = _load_exoplanet_viability_module()
    index = ev._fetch_index(client)

    resolved: list[tuple[str, MeasurementOut, str, object]] = []
    for external_id, measurements in attributed.items():
        for measurement in measurements:
            catalog_measurement = to_catalog_measurement(measurement)
            if catalog_measurement is None:
                continue
            match = ev.match_object(measurement.planet_name, index)
            if match.pl_name is None:
                continue
            resolved.append((external_id, measurement, match.pl_name, catalog_measurement))

    pl_names = sorted({pl_name for _, _, pl_name, _ in resolved})
    solutions_by_pl = ev._fetch_solutions_by_planet(client, pl_names) if pl_names else {}

    rows: list[SigmaRow] = []
    for external_id, measurement, pl_name, catalog_measurement in resolved:
        for solution in solutions_by_pl.get(pl_name, []):
            if solution.measurement is None:
                continue
            if solution.measurement.parameter != catalog_measurement.parameter:
                continue
            try:
                discrepancy = ev.sigma(catalog_measurement, solution.measurement)
            except ValueError:
                continue
            rows.append(
                SigmaRow(
                    external_id=external_id,
                    planet_name=measurement.planet_name,
                    parameter=measurement.parameter,
                    paper_value=measurement.value,
                    paper_unit=measurement.unit,
                    refname=solution.refname,
                    default_flag=solution.default_flag,
                    sigma=discrepancy,
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Ejecución secuencial contra AgentRunner
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Resultado de intentar la atribución sobre un abstract concreto.

    `outcome` es el valor de `AttemptOutcome` (`"ok"`, `"invalid_output"`,
    `"agent_error"`, `"timeout"`, `"rate_limited"`) o `"not_run"` si este
    módulo decidió no llamar siquiera (presupuesto agotado, o porque un
    intento anterior ya agotó el límite de tasa) -- ver `run_attribution`.
    `measurements` solo está informado cuando `outcome == "ok"`.
    """

    external_id: str
    output_text: str | None
    measurements: tuple[MeasurementOut, ...] | None
    attempts: int
    tokens_spent: int
    outcome: str
    note: str | None = None


def _make_build(
    captured_text: list[str],
) -> Callable[[AgentResult, object], ExperimentalReaderOutput]:
    """Fábrica del `build` que pasa `run_attribution` a `AgentRunner.run`.

    Definida fuera del bucle de `run_attribution` a propósito (B023,
    flake8-bugbear): una función anidada dentro de un `for` que cierra
    sobre la variable de bucle es un cierre tardío -- todas las clausuras
    creadas en iteraciones distintas compartirían la misma celda si el
    bucle las retuviera más allá de esa iteración. Aquí `_build` se usa y
    se descarta dentro de la misma iteración (no sobrevive al siguiente
    `for item in items`), así que no hay bug real, pero factorizarla evita
    la señal de alarma y documenta la intención: cada llamada recibe su
    propia lista `captured_text`, nunca comparte una con otro ítem.
    """

    def _build(result: AgentResult, run_id: object) -> ExperimentalReaderOutput:
        captured_text.append(result.output_text)
        return parse_experimental_output(result.output_text)

    return _build


async def run_attribution(runner: AgentRunner, items: Sequence[Item]) -> list[AttemptRecord]:
    """Ejecuta el Reader experimental sobre `items`, uno a uno, secuencial.

    `prompt` es el mismo que usaría el Reader de producción sobre este
    `Item` (`ReadItem._build_prompt`, solo título + abstract, nunca
    instrucciones -- ver su docstring): el prompt experimental vive en el
    `system_prompt` de `runner`, no aquí.

    Dos desenlaces detienen el resto de la lista sin más llamadas:

    - `BudgetDenied` (lanzado por `BudgetGuard.authorize`, dentro de
      `AgentRunner.run`, antes de llamar al proveedor): este ítem y todos
      los que quedan se registran con `outcome="not_run"` y
      `note="no ejecutado: presupuesto (<NombreDeLaSubclase>)"`, donde
      `<NombreDeLaSubclase>` es `type(exc).__name__` de la subclase concreta
      de `BudgetDenied` (`BudgetExceeded`, `CallLimitReached`,
      `OutsideExecutionWindow`, ... -- ver `application/budget.py`): "no
      ejecutado: presupuesto" a secas no distinguía agotar el presupuesto
      de cruzar la ventana horaria o el tope de llamadas por ítem, y los
      tres denegaban con la misma excepción base. Sin gastar nada más.
    - `AttemptOutcome.RATE_LIMITED` (el proveedor ya respondió que se
      alcanzó un límite de tasa; `AgentRunner.run` no lo relanza, lo
      devuelve como resultado): este ítem SÍ se registra con su gasto real
      -- la llamada ya ocurrió y ya se cobró --, pero ningún ítem posterior
      se intenta.
    """
    records: list[AttemptRecord] = []
    stop_reason: str | None = None

    for item in items:
        if stop_reason is not None:
            records.append(
                AttemptRecord(
                    external_id=item.external_id,
                    output_text=None,
                    measurements=None,
                    attempts=0,
                    tokens_spent=0,
                    outcome="not_run",
                    note=stop_reason,
                )
            )
            continue

        captured_text: list[str] = []
        _build = _make_build(captured_text)

        prompt = ReadItem._build_prompt(item)
        try:
            runner_result = await runner.run(prompt=prompt, item_id=item.id, build=_build)
        except BudgetDenied as exc:
            stop_reason = f"no ejecutado: presupuesto ({type(exc).__name__})"
            records.append(
                AttemptRecord(
                    external_id=item.external_id,
                    output_text=None,
                    measurements=None,
                    attempts=0,
                    tokens_spent=0,
                    outcome="not_run",
                    note=stop_reason,
                )
            )
            continue

        output_text = captured_text[-1] if captured_text else None
        measurements = (
            tuple(runner_result.value.measurements)
            if runner_result.outcome is AttemptOutcome.OK and runner_result.value is not None
            else None
        )
        records.append(
            AttemptRecord(
                external_id=item.external_id,
                output_text=output_text,
                measurements=measurements,
                attempts=runner_result.attempts,
                tokens_spent=runner_result.tokens_spent,
                outcome=runner_result.outcome.value,
            )
        )

        if runner_result.outcome is AttemptOutcome.RATE_LIMITED:
            stop_reason = "no ejecutado: límite de tasa alcanzado en el ítem anterior"

    return records


# ---------------------------------------------------------------------------
# Expectativas confirmadas por el autor y evaluación
# ---------------------------------------------------------------------------

#: Descripción legible de lo que el autor confirmó para cada abstract (ver
#: el mensaje de la tarea T71.b). Se usa como columna "esperado" del
#: informe; la lógica de FALLO/AVISO/OK real vive en los evaluadores
#: `_case_*` de más abajo, uno por `external_id`, porque las condiciones de
#: fallo de cada caso son específicas (nombres de objeto de comparación
#: prohibidos, trampas numéricas concretas) y no se reducen a una
#: comparación genérica de valores.
EXPECTED: dict[str, str] = {
    "2609.17025": (
        "ninguna medida (54±3 y 258±11 M_earth son los límites del régimen "
        "saturniano de la relación masa-radio, no la masa de un planeta)"
    ),
    "2609.20748": "RX J0534.0-0221 b, mass 2.8(+0.5/-0.5) M_jup, origin=this_work",
    "2609.26894": (
        "TOI-2109 b: radius=1.347±0.047 R_jup, mass=5.02±0.75 M_jup, "
        "period=0.67247479±0.00000028 day; origin=literature esperado "
        "(origin=this_work es AVISO, no FALLO)"
    ),
    "2609.30038": (
        "V1298 Tau b mass ∈ {0.52(+0.12/-0.14), 0.65(+0.21/-0.18), 0.67±0.16, "
        "0.78(+0.19/-0.20)} M_jup y V1298 Tau e mass ∈ {0.40(+0.14/-0.13), "
        "0.66(+0.17/-0.18), 0.71±0.20} M_jup, origin=this_work (basta con al "
        "menos una medida por planeta, no hace falta que aparezcan las cuatro/tres)"
    ),
}

_REL_TOL = 1e-6


def _approx(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL)


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """Una línea del informe: qué se esperaba, qué se atribuyó, y el veredicto."""

    expected: str
    attributed: str
    status: Literal["OK", "AVISO", "FALLO"]
    reason: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Veredicto de un abstract: sus filas de comparación y si pasa o no."""

    external_id: str
    rows: tuple[ComparisonRow, ...]

    @property
    def passed(self) -> bool:
        return all(row.status != "FALLO" for row in self.rows)


def _row(
    expected: str, attributed: str, status: Literal["OK", "AVISO", "FALLO"], reason: str
) -> ComparisonRow:
    return ComparisonRow(expected=expected, attributed=attributed, status=status, reason=reason)


def _global_checks(measurements: Sequence[MeasurementOut], abstract: str) -> list[ComparisonRow]:
    """Comprobaciones válidas para cualquier abstract, antes de la lógica
    específica del caso: `evidence` debe ser una cita literal, y una medida
    sin límite y sin error nunca debería haberse emitido (regla global de
    `reader-measures-exp1.md`)."""
    rows: list[ComparisonRow] = []
    for measurement in measurements:
        if not evidence_in_abstract(measurement.evidence, abstract):
            rows.append(
                _row(
                    "evidence literal del abstract",
                    f"{measurement.planet_name} {measurement.parameter}="
                    f"{measurement.value}: evidence={measurement.evidence!r}",
                    "FALLO",
                    "'evidence' no es una cita literal (salvo espacios) del abstract",
                )
            )
        if measurement.limit == "none" and (
            measurement.err_plus is None or measurement.err_minus is None
        ):
            rows.append(
                _row(
                    "medida con error, o límite con cifra (limit=upper/lower)",
                    f"{measurement.planet_name} {measurement.parameter}="
                    f"{measurement.value} (sin error, limit=none)",
                    "FALLO",
                    "un valor sin error que no es un límite con cifra no es una medida",
                )
            )
    return rows


def _case_17025(measurements: Sequence[MeasurementOut], abstract: str) -> list[ComparisonRow]:
    rows: list[ComparisonRow] = []
    trapped = False
    extra: list[MeasurementOut] = []
    for measurement in measurements:
        if (
            measurement.parameter == "mass"
            and measurement.unit == "M_earth"
            and (_approx(measurement.value, 54.0) or _approx(measurement.value, 258.0))
        ):
            trapped = True
            rows.append(
                _row(
                    EXPECTED["2609.17025"],
                    f"{measurement.planet_name} mass={measurement.value} {measurement.unit}",
                    "FALLO",
                    "54±3 / 258±11 M_earth son los límites del régimen saturniano, "
                    "no la masa de un planeta concreto",
                )
            )
        else:
            extra.append(measurement)

    if not trapped and not extra:
        rows.append(
            _row(EXPECTED["2609.17025"], "ninguna medida", "OK", "no se atribuyó ninguna medida")
        )
    for measurement in extra:
        rows.append(
            _row(
                EXPECTED["2609.17025"],
                f"{measurement.planet_name} {measurement.parameter}="
                f"{measurement.value} {measurement.unit}",
                "AVISO",
                "medida no prevista para este abstract; revisar a mano",
            )
        )
    return rows


def _case_20748(measurements: Sequence[MeasurementOut], abstract: str) -> list[ComparisonRow]:
    ev = _load_exoplanet_viability_module()
    expected_desc = EXPECTED["2609.20748"]
    rows: list[ComparisonRow] = []
    matched = False

    for measurement in measurements:
        norm = ev.normalize_name(measurement.planet_name)
        attributed_desc = (
            f"{measurement.planet_name} {measurement.parameter}={measurement.value} "
            f"{measurement.unit} origin={measurement.origin}"
        )

        if "twa 7" in norm and measurement.origin == "this_work":
            rows.append(
                _row(
                    expected_desc,
                    attributed_desc,
                    "FALLO",
                    "medida this_work atribuida a TWA 7 b (objeto de comparación, no "
                    "el planeta descubierto por este paper)",
                )
            )
            continue

        if any(_approx(measurement.value, trap) for trap in (674.0, -5.48, 79.0)):
            rows.append(
                _row(
                    expected_desc,
                    attributed_desc,
                    "FALLO",
                    "temperatura efectiva (674 K), luminosidad (-5.48 dex) o radio del "
                    "disco de escombros (79 au) tomados como masa/radio del planeta",
                )
            )
            continue

        if "rx j0534" in norm and measurement.parameter == "mass":
            ok = (
                _approx(measurement.value, 2.8)
                and measurement.unit == "M_jup"
                and measurement.origin == "this_work"
                and measurement.err_plus is not None
                and measurement.err_minus is not None
                and _approx(measurement.err_plus, 0.5)
                and _approx(measurement.err_minus, 0.5)
            )
            matched = matched or ok
            rows.append(
                _row(
                    expected_desc,
                    attributed_desc
                    + f" err_plus={measurement.err_plus} err_minus={measurement.err_minus}",
                    "OK" if ok else "AVISO",
                    "coincide con 2.8(+0.5/-0.5) M_jup, this_work"
                    if ok
                    else "planeta y parámetro correctos, pero valor/error/unidad/origin no "
                    "coincide exactamente con el abstract",
                )
            )
            continue

        rows.append(
            _row(expected_desc, attributed_desc, "AVISO", "medida no prevista para este abstract")
        )

    if not matched:
        rows.append(_row(expected_desc, "ninguna", "FALLO", "no se encontró la medida esperada"))
    return rows


def _squash_name(name: str) -> str:
    """Quita guiones y espacios de un nombre ya normalizado (`normalize_name`).

    `normalize_name` unifica variantes de guion a `"-"` y separa dígito de
    letra con un espacio, pero no quita ninguno de los dos: "WASP-12 b",
    "WASP 12 b" y "WASP12b" normalizan a formas distintas entre sí
    (`"wasp-12 b"`, `"wasp 12 b"`, `"wasp12 b"`). Comparar sobre la forma
    "sin guiones ni espacios" (`"wasp12b"`) hace la comprobación robusta a
    esa variación, en vez de asumir que el Reader siempre reproduce el
    guion tal cual aparece en el abstract.
    """
    return name.replace("-", "").replace(" ", "")


def _case_26894(measurements: Sequence[MeasurementOut], abstract: str) -> list[ComparisonRow]:
    ev = _load_exoplanet_viability_module()
    expected_by_parameter: dict[str, tuple[float, float, float, str]] = {
        "radius": (1.347, 0.047, 0.047, "R_jup"),
        "mass": (5.02, 0.75, 0.75, "M_jup"),
        "period": (0.67247479, 0.00000028, 0.00000028, "day"),
    }
    rows: list[ComparisonRow] = []
    found: set[str] = set()

    for measurement in measurements:
        norm = ev.normalize_name(measurement.planet_name)
        attributed_desc = (
            f"{measurement.planet_name} {measurement.parameter}={measurement.value} "
            f"{measurement.unit} origin={measurement.origin}"
        )

        if "wasp12" in _squash_name(norm):
            rows.append(
                _row(
                    "TOI-2109 b (ver EXPECTED)",
                    attributed_desc,
                    "FALLO",
                    "medida atribuida a WASP-12 b (objeto de comparación), no a TOI-2109 b",
                )
            )
            continue

        if any(_approx(measurement.value, trap) for trap in (-3.46, 2.13)):
            rows.append(
                _row(
                    "TOI-2109 b (ver EXPECTED)",
                    attributed_desc,
                    "FALLO",
                    "ritmo de decaimiento orbital (-3.46 ms/yr) o factor de calidad Q "
                    "(2.13x10^7) tomado como medida de mass/radius/period",
                )
            )
            continue

        expected_row = expected_by_parameter.get(measurement.parameter)
        if expected_row is None:
            rows.append(
                _row("TOI-2109 b (ver EXPECTED)", attributed_desc, "AVISO", "parámetro no esperado")
            )
            continue

        exp_value, exp_err_plus, exp_err_minus, exp_unit = expected_row
        label = f"TOI-2109 b {measurement.parameter}={exp_value} {exp_unit}"

        if not _approx(measurement.value, exp_value) or measurement.unit != exp_unit:
            rows.append(
                _row(label, attributed_desc, "FALLO", "valor o unidad no coincide con el abstract")
            )
            continue

        if (
            measurement.err_plus is None
            or measurement.err_minus is None
            or not _approx(measurement.err_plus, exp_err_plus)
            or not _approx(measurement.err_minus, exp_err_minus)
        ):
            rows.append(
                _row(
                    label,
                    attributed_desc,
                    "AVISO",
                    "el error no coincide exactamente con el abstract",
                )
            )
            found.add(measurement.parameter)
            continue

        if measurement.origin == "literature":
            rows.append(
                _row(label, attributed_desc, "OK", "coincide con el abstract, origin=literature")
            )
        else:
            rows.append(
                _row(
                    label,
                    attributed_desc,
                    "AVISO",
                    "coincide con el abstract, pero origin=this_work (aviso, no fallo: "
                    "TOI-2109 b ya era conocido antes de este paper)",
                )
            )
        found.add(measurement.parameter)

    for parameter, (exp_value, _, _, exp_unit) in expected_by_parameter.items():
        if parameter not in found:
            rows.append(
                _row(
                    f"TOI-2109 b {parameter}={exp_value} {exp_unit}",
                    "ninguna",
                    "FALLO",
                    "no se encontró la medida esperada",
                )
            )
    return rows


_V1298_B_VALUES: tuple[tuple[float, float, float], ...] = (
    (0.52, 0.12, 0.14),
    (0.65, 0.21, 0.18),
    (0.67, 0.16, 0.16),
    (0.78, 0.19, 0.20),
)
_V1298_E_VALUES: tuple[tuple[float, float, float], ...] = (
    (0.40, 0.14, 0.13),
    (0.66, 0.17, 0.18),
    (0.71, 0.20, 0.20),
)


def _matches_any(
    value: float,
    err_plus: float | None,
    err_minus: float | None,
    candidates: tuple[tuple[float, float, float], ...],
) -> bool:
    if err_plus is None or err_minus is None:
        return False
    return any(
        _approx(value, v) and _approx(err_plus, ep) and _approx(err_minus, em)
        for v, ep, em in candidates
    )


def _case_30038(measurements: Sequence[MeasurementOut], abstract: str) -> list[ComparisonRow]:
    ev = _load_exoplanet_viability_module()
    rows: list[ComparisonRow] = []
    b_found = False
    e_found = False

    for measurement in measurements:
        norm = ev.normalize_name(measurement.planet_name)
        attributed_desc = f"{measurement.planet_name} {measurement.parameter}={measurement.value}"

        if "v1298" not in norm:
            rows.append(
                _row(
                    EXPECTED["2609.30038"],
                    attributed_desc,
                    "AVISO",
                    "no se reconoce como V1298 Tau b/e",
                )
            )
            continue

        is_b = norm.endswith(" b")
        is_e = norm.endswith(" e")
        is_c_or_d = norm.endswith(" c") or norm.endswith(" d")

        if is_c_or_d:
            rows.append(
                _row(
                    "V1298 Tau c/d: solo 'upper limits' sin cifra en este paper",
                    attributed_desc,
                    "FALLO",
                    "V1298 Tau c/d solo tienen límites superiores sin cifra en este paper, "
                    "no una medida con valor",
                )
            )
            continue

        if measurement.parameter == "period":
            rows.append(
                _row(
                    "sin medida de periodo (los ~24.1/8.2/12.4/48.7 d ya eran conocidos y "
                    "se citan sin error)",
                    attributed_desc,
                    "FALLO",
                    "periodo citado sin error (p. ej. '~24.1 d'), no es una medida",
                )
            )
            continue

        if measurement.parameter != "mass" or not (is_b or is_e):
            rows.append(
                _row(
                    EXPECTED["2609.30038"],
                    attributed_desc,
                    "AVISO",
                    "medida no prevista para este abstract",
                )
            )
            continue

        matches_b = _matches_any(
            measurement.value, measurement.err_plus, measurement.err_minus, _V1298_B_VALUES
        )
        matches_e = _matches_any(
            measurement.value, measurement.err_plus, measurement.err_minus, _V1298_E_VALUES
        )

        if is_b:
            if matches_e and not matches_b:
                rows.append(
                    _row(
                        "masa de V1298 Tau b",
                        attributed_desc,
                        "FALLO",
                        "valor de la lista de e (0.40/0.66/0.71 M_jup) asignado a b",
                    )
                )
                continue
            if matches_b:
                b_found = True
                rows.append(
                    _row(
                        "masa de V1298 Tau b (una de las 4 soluciones)",
                        attributed_desc + f" origin={measurement.origin}",
                        "OK" if measurement.origin == "this_work" else "AVISO",
                        "coincide con una de las cuatro soluciones de masa de b"
                        + (
                            ""
                            if measurement.origin == "this_work"
                            else " (se esperaba origin=this_work)"
                        ),
                    )
                )
            else:
                rows.append(
                    _row(
                        "masa de V1298 Tau b (una de las 4 soluciones)",
                        attributed_desc,
                        "AVISO",
                        "no coincide con ninguna de las cuatro soluciones del abstract",
                    )
                )
        else:  # is_e
            if matches_b and not matches_e:
                rows.append(
                    _row(
                        "masa de V1298 Tau e",
                        attributed_desc,
                        "FALLO",
                        "valor de la lista de b (0.52/0.65/0.67/0.78 M_jup) asignado a e",
                    )
                )
                continue
            if matches_e:
                e_found = True
                rows.append(
                    _row(
                        "masa de V1298 Tau e (una de las 3 soluciones)",
                        attributed_desc + f" origin={measurement.origin}",
                        "OK" if measurement.origin == "this_work" else "AVISO",
                        "coincide con una de las tres soluciones de masa de e"
                        + (
                            ""
                            if measurement.origin == "this_work"
                            else " (se esperaba origin=this_work)"
                        ),
                    )
                )
            else:
                rows.append(
                    _row(
                        "masa de V1298 Tau e (una de las 3 soluciones)",
                        attributed_desc,
                        "AVISO",
                        "no coincide con ninguna de las tres soluciones del abstract",
                    )
                )

    if not b_found:
        rows.append(
            _row(
                "al menos una masa de V1298 Tau b (cualquiera de las 4)",
                "ninguna",
                "FALLO",
                "no se atribuyó ninguna masa utilizable a V1298 Tau b",
            )
        )
    if not e_found:
        rows.append(
            _row(
                "al menos una masa de V1298 Tau e (cualquiera de las 3)",
                "ninguna",
                "FALLO",
                "no se atribuyó ninguna masa utilizable a V1298 Tau e",
            )
        )
    return rows


_CASE_EVALUATORS = {
    "2609.17025": _case_17025,
    "2609.20748": _case_20748,
    "2609.26894": _case_26894,
    "2609.30038": _case_30038,
}


def evaluate(external_id: str, measurements: Sequence[MeasurementOut], abstract: str) -> CaseResult:
    """Compara las medidas atribuidas por el Reader experimental para
    `external_id` contra lo que el autor confirmó (`EXPECTED`), aplicando
    primero las comprobaciones globales (`_global_checks`) y luego la
    lógica específica de ese abstract (`_CASE_EVALUATORS`)."""
    evaluator = _CASE_EVALUATORS.get(external_id)
    if evaluator is None:
        raise ValueError(f"no hay evaluador definido para external_id={external_id!r}")
    rows = tuple(_global_checks(measurements, abstract) + evaluator(measurements, abstract))
    return CaseResult(external_id=external_id, rows=rows)


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------


def render_report(
    records: Sequence[AttemptRecord],
    results: dict[str, CaseResult],
    sigma_rows_list: Sequence[SigmaRow] | None,
    archive_error: str | None = None,
) -> str:
    """Informe en texto plano: por abstract, tabla esperado/atribuido con
    veredicto; veredicto global X/4; tokens reales totales y por llamada;
    sigma por medida utilizable (o "σ no calculado" si `sigma_rows_list` es
    `None`, el caso cuando `NOCTURNA_ALLOW_ARCHIVE_QUERY` no está en el
    entorno).

    `archive_error` es el mensaje de una excepción que interrumpió el cruce
    con el NASA Exoplanet Archive (`ArchiveClientError`, un fallo de
    transporte de `httpx`, o el tope de `max_requests`) -- se anota en el
    informe en vez de la línea de sigma habitual, sin tumbar el test que
    llama a esta función (ver `test_reader_attribution_smoke.py`). Se
    ignora si `sigma_rows_list` no es `None`: un cruce que sí terminó con
    filas no necesita anotar un error de un intento anterior."""
    lines: list[str] = [f"Informe T71.b -- prompt {EXPERIMENT_PROMPT_VERSION}", "=" * 72]

    passed = 0
    evaluated = 0
    for record in records:
        lines.append("")
        lines.append(f"--- {record.external_id} ---")
        lines.append(f"esperado: {EXPECTED.get(record.external_id, '(sin EXPECTED registrado)')}")

        if record.outcome == "not_run":
            lines.append(f"atribuido: {record.note}")
            continue
        if record.outcome != "ok":
            lines.append(
                f"atribuido: intento fallido (outcome={record.outcome}, intentos={record.attempts})"
            )
            continue

        case_result = results.get(record.external_id)
        if case_result is None:
            lines.append("atribuido: (sin evaluar)")
            continue

        evaluated += 1
        if case_result.passed:
            passed += 1
        for row in case_result.rows:
            lines.append(f"  [{row.status}] esperado={row.expected!r}")
            lines.append(f"           atribuido={row.attributed!r} -- {row.reason}")
        lines.append(f"  veredicto de este abstract: {'OK' if case_result.passed else 'FALLO'}")

    lines.append("")
    lines.append(
        f"Veredicto global: {passed}/{len(records)} (evaluados: {evaluated}/{len(records)})"
    )

    lines.append("")
    lines.append(
        "Nota: un FALLO de 'evidence' (cita no literal) en un fragmento con marcado "
        "LaTeX (p. ej. '$\\pm$', subíndices, llaves) puede deberse a una diferencia de "
        "escape o espaciado del propio LaTeX entre lo citado y el abstract, no a una "
        "paráfrasis real -- revisar a mano el fragmento antes de descartar la medida."
    )

    lines.append("")
    lines.append("Tokens reales:")
    lines.append(f"  total: {sum(r.tokens_spent for r in records)}")
    for record in records:
        lines.append(
            f"  {record.external_id}: {record.tokens_spent} tokens "
            f"({record.attempts} intento(s), outcome={record.outcome})"
        )

    lines.append("")
    if archive_error is not None and sigma_rows_list is None:
        lines.append(
            f"sigma: error al consultar el NASA Exoplanet Archive, revisar a mano -- "
            f"{archive_error}"
        )
    elif sigma_rows_list is None:
        lines.append("sigma: no calculado (NOCTURNA_ALLOW_ARCHIVE_QUERY no estaba en el entorno)")
    else:
        lines.append("sigma por medida utilizable (contra el NASA Exoplanet Archive):")
        if not sigma_rows_list:
            lines.append("  (ninguna medida utilizable emparejó con el archivo)")
        for row in sigma_rows_list:
            lines.append(
                f"  {row.external_id} {row.planet_name} {row.parameter}={row.paper_value} "
                f"{row.paper_unit}: sigma={row.sigma:.2f} refname={row.refname!r} "
                f"default_flag={row.default_flag}"
            )

        highlighted = [
            row
            for row in sigma_rows_list
            if "v1298 tau" in row.planet_name.lower()
            and ("livingston" in row.refname.lower() or "mascare" in row.refname.lower())
        ]
        if highlighted:
            lines.append("")
            lines.append(
                "  V1298 Tau b/e frente a Livingston et al. 2026 / Suárez Mascareño et al. 2022:"
            )
            for row in highlighted:
                lines.append(
                    f"    {row.planet_name} {row.parameter}: sigma={row.sigma:.2f} "
                    f"refname={row.refname!r} default_flag={row.default_flag}"
                )

    return "\n".join(lines)


def dump_json(
    records: Sequence[AttemptRecord],
    results: dict[str, CaseResult],
    sigma_rows_list: Sequence[SigmaRow] | None,
    archive_error: str | None = None,
) -> dict[str, object]:
    """Volcado JSON-serializable de todo lo que produce el experimento,
    para `attribution.json` (`test_reader_attribution_smoke.py`).

    `archive_error`: mismo significado que en `render_report` -- el mensaje
    de la excepción que interrumpió el cruce con el archivo, o `None` si no
    se intentó o si terminó sin fallo."""
    return {
        "prompt_version": EXPERIMENT_PROMPT_VERSION,
        "archive_error": archive_error,
        "records": [
            {
                "external_id": record.external_id,
                "outcome": record.outcome,
                "attempts": record.attempts,
                "tokens_spent": record.tokens_spent,
                "note": record.note,
                "output_text": record.output_text,
                "measurements": (
                    [measurement.model_dump(mode="json") for measurement in record.measurements]
                    if record.measurements is not None
                    else None
                ),
            }
            for record in records
        ],
        "results": {
            external_id: {
                "passed": case_result.passed,
                "rows": [
                    {
                        "expected": row.expected,
                        "attributed": row.attributed,
                        "status": row.status,
                        "reason": row.reason,
                    }
                    for row in case_result.rows
                ],
            }
            for external_id, case_result in results.items()
        },
        "sigma_rows": (
            None
            if sigma_rows_list is None
            else [
                {
                    "external_id": row.external_id,
                    "planet_name": row.planet_name,
                    "parameter": row.parameter,
                    "paper_value": row.paper_value,
                    "paper_unit": row.paper_unit,
                    "refname": row.refname,
                    "default_flag": row.default_flag,
                    "sigma": row.sigma,
                }
                for row in sigma_rows_list
            ]
        ),
    }
