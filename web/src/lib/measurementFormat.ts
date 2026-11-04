/**
 * Formato de números y unidades de las medidas, espejo de
 * `backend/src/nocturna/application/measurement_finding_texts.py`:
 * coma decimal, valor en su representación más corta (sin notación
 * exponencial), errores simétricos "± e" o asimétricos "+a / −b" (U+2212),
 * σ con dos decimales. Funciones puras.
 */

import type {
  ArchiveValue,
  MeasurementValue,
  Parameter,
  Unit,
} from "./api/types";

const MINUS = "−";

export const UNIT_LABELS: Record<Unit, string> = {
  M_earth: "M⊕",
  R_earth: "R⊕",
  M_jup: "M♃",
  R_jup: "R♃",
  day: "d",
};

export const PARAMETER_LABELS: Record<Parameter, string> = {
  mass: "masa",
  radius: "radio",
  period: "periodo",
};

/** Expande una notación exponencial de JS (`1e-7`, `1.5e21`) a posicional. */
function toPositional(text: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?e([+-]?\d+)$/i.exec(text);
  if (match === null) {
    return text;
  }
  const [, sign, int, frac = "", expRaw] = match;
  const exp = Number(expRaw);
  const digits = int + frac;
  const pointAt = int.length + exp;
  let out: string;
  if (pointAt <= 0) {
    out = `0.${"0".repeat(-pointAt)}${digits}`;
  } else if (pointAt >= digits.length) {
    out = digits + "0".repeat(pointAt - digits.length);
  } else {
    out = `${digits.slice(0, pointAt)}.${digits.slice(pointAt)}`;
  }
  return sign + out;
}

/** Representación más corta del número, posicional y con coma decimal. */
export function formatNumber(value: number): string {
  return toPositional(String(value)).replace(".", ",");
}

/** σ con dos decimales y coma decimal. */
export function formatSigma(sigma: number): string {
  return sigma.toFixed(2).replace(".", ",");
}

/** `"13,8 ± 1 M⊕"` o `"2,8 +0,5 / −0,4 M♃"`. */
export function formatWithErrors(m: MeasurementValue): string {
  const errors =
    m.err_plus === m.err_minus
      ? `± ${formatNumber(m.err_plus)}`
      : `+${formatNumber(m.err_plus)} / ${MINUS}${formatNumber(m.err_minus)}`;
  return `${formatNumber(m.value)} ${errors} ${UNIT_LABELS[m.unit]}`;
}

/**
 * Como `formatNumber`, pero con el ",0" que Python da a los enteros
 * (`repr(5.0)` -> "5,0", `-0.0` -> "-0,0"); el JSON `1.0` llega a JS como `1`.
 * Por encima de 1e16 `repr` usa exponente y no añade ",0".
 */
function formatArchive(v: number): string {
  if (Object.is(v, -0)) {
    return "-0,0";
  }
  const text = formatNumber(v);
  return Number.isInteger(v) && Math.abs(v) < 1e16 ? `${text},0` : text;
}

const MISSING_VALUE = "sin valor";

/**
 * Espejo de `format_archive_value`
 * (`backend/src/nocturna/application/archive_digest_text.py`):
 * `"5,2 ± 0,3 M⊕"`, `"5,2 +0,4 / −0,3 M⊕"`, `"< 5,2 M⊕"` (cota) o
 * `"sin valor"`. La API ya entrega los errores en valor absoluto.
 */
export function formatArchiveValue(p: ArchiveValue | null): string {
  if (p === null || p.value === null) {
    return MISSING_VALUE;
  }
  const unit = UNIT_LABELS[p.unit];
  const number = formatArchive(p.value);
  if (p.bound === "upper") {
    return `< ${number} ${unit}`;
  }
  if (p.bound === "lower") {
    return `> ${number} ${unit}`;
  }
  if (p.err_plus === null || p.err_minus === null) {
    return `${number} ${unit}`;
  }
  if (p.err_plus === p.err_minus) {
    return `${number} ± ${formatArchive(p.err_plus)} ${unit}`;
  }
  return `${number} +${formatArchive(p.err_plus)} / ${MINUS}${formatArchive(p.err_minus)} ${unit}`;
}

/** Etiqueta de parámetro con mayúscula inicial, para inicio de frase. */
export function parameterTitle(parameter: Parameter): string {
  const label = PARAMETER_LABELS[parameter];
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** Posicional con `d` decimales y coma; sin exponente ni separador de miles. */
function fixed(v: number, d: number): string {
  const text = Math.abs(v) >= 1e21 ? toPositional(v.toExponential()) : v.toFixed(d);
  // Un cero con signo (-0, o -0,0004 redondeado) se muestra sin él.
  const unsigned = /^-0(\.0*)?$/.test(text) ? text.slice(1) : text;
  return unsigned.replace(".", ",");
}

/** Decimales para dos cifras en el error: `max(0, 1 - floor(log10(e)))`. */
function errorDecimals(smallestError: number): number {
  return Math.max(0, 1 - Math.floor(Math.log10(smallestError)));
}

/** `max(0, 3 − floor(log10|v|))` decimales, sin ceros finales. */
function fourSignificant(v: number): string {
  if (v === 0) {
    return "0";
  }
  const d = Math.max(0, 3 - Math.floor(Math.log10(Math.abs(v))));
  const text = fixed(v, d);
  return text.includes(",") ? text.replace(/0+$/, "").replace(/,$/, "") : text;
}

/**
 * Versión redondeada para lectura de `formatArchiveValue` (T102, ADR 0041):
 * con los dos errores > 0, el menor lleva dos cifras significativas y el
 * valor y los errores comparten esos decimales; si los errores redondeados
 * coinciden se usa `±`. Sin errores, con un error nulo o 0 o con cota,
 * `max(0, 3 − floor(log10|v|))` decimales del valor sin ceros finales. Nunca notación
 * exponencial ni separador de miles. El valor exacto sigue disponible con
 * `formatArchiveValue`.
 */
export function formatArchiveValueRounded(p: ArchiveValue | null): string {
  if (p === null || p.value === null) {
    return MISSING_VALUE;
  }
  const unit = UNIT_LABELS[p.unit];
  if (p.bound === "upper") {
    return `< ${fourSignificant(p.value)} ${unit}`;
  }
  if (p.bound === "lower") {
    return `> ${fourSignificant(p.value)} ${unit}`;
  }
  const { err_plus: plus, err_minus: minus } = p;
  if (plus === null || minus === null || plus <= 0 || minus <= 0) {
    return `${fourSignificant(p.value)} ${unit}`;
  }
  const d = errorDecimals(Math.min(plus, minus));
  const number = fixed(p.value, d);
  const a = fixed(plus, d);
  const b = fixed(minus, d);
  return a === b
    ? `${number} ± ${a} ${unit}`
    : `${number} +${a} / ${MINUS}${b} ${unit}`;
}
