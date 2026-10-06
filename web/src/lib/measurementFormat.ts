/**
 * Formato de números y unidades de las medidas, espejo de
 * `backend/src/nocturna/application/measurement_finding_texts.py`:
 * coma decimal, valor en su representación más corta (sin notación
 * exponencial), errores simétricos "± e" o asimétricos "+a / −b" (U+2212),
 * σ con dos decimales. Funciones puras.
 */

import type { MeasurementValue, Parameter, Unit } from "./api/types";

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
