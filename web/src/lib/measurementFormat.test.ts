import { describe, expect, it } from "vitest";
import type { MeasurementValue } from "./api/types";
import {
  UNIT_LABELS,
  formatNumber,
  formatSigma,
  formatWithErrors,
} from "./measurementFormat";

const m = (
  value: number,
  err_plus: number,
  err_minus: number,
  unit: MeasurementValue["unit"],
): MeasurementValue => ({ value, err_plus, err_minus, unit });

describe("formatWithErrors", () => {
  it("simétrico con ±", () => {
    expect(formatWithErrors(m(13.8, 1, 1, "M_earth"))).toBe("13,8 ± 1 M⊕");
  });

  it("asimétrico con +a / −b (U+2212)", () => {
    const out = formatWithErrors(m(2.8, 0.5, 0.4, "M_jup"));
    expect(out).toBe("2,8 +0,5 / −0,4 M♃");
    expect(out).not.toContain("-");
  });

  it("0,969 ± 0,017 R♃", () => {
    expect(formatWithErrors(m(0.969, 0.017, 0.017, "R_jup"))).toBe(
      "0,969 ± 0,017 R♃",
    );
  });

  it("2,8 ± 0,5 M♃", () => {
    expect(formatWithErrors(m(2.8, 0.5, 0.5, "M_jup"))).toBe("2,8 ± 0,5 M♃");
  });
});

describe("unidades", () => {
  it("las cinco etiquetas", () => {
    expect(UNIT_LABELS).toEqual({
      M_earth: "M⊕",
      R_earth: "R⊕",
      M_jup: "M♃",
      R_jup: "R♃",
      day: "d",
    });
  });
});

describe("formatNumber", () => {
  it("nunca usa notación exponencial", () => {
    for (const v of [1e-7, 1.5e-10, 1e21, 2.5e22, 123456789012, 0.000001]) {
      const out = formatNumber(v);
      expect(out).not.toMatch(/e/i);
    }
    expect(formatNumber(1e-7)).toBe("0,0000001");
    expect(formatNumber(1.5e-7)).toBe("0,00000015");
    expect(formatNumber(1e21)).toBe("1000000000000000000000");
    expect(formatNumber(2.5e21)).toBe("2500000000000000000000");
  });

  it("enteros y negativos", () => {
    expect(formatNumber(1)).toBe("1");
    expect(formatNumber(-0.5)).toBe("-0,5");
  });
});

describe("formatSigma", () => {
  it("dos decimales con coma", () => {
    expect(formatSigma(3.37)).toBe("3,37");
    expect(formatSigma(3)).toBe("3,00");
    expect(formatSigma(3.456)).toBe("3,46");
  });
});
