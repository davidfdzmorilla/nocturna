import { describe, expect, it } from "vitest";
import type { ArchiveValue, MeasurementValue } from "./api/types";
import {
  UNIT_LABELS,
  formatArchiveValue,
  formatArchiveValueRounded,
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

describe("formatArchiveValue (espejo de format_archive_value en Python)", () => {
  const v = (
    value: number | null,
    err_plus: number | null,
    err_minus: number | null,
    unit: ArchiveValue["unit"],
    bound: ArchiveValue["bound"] = "none",
  ): ArchiveValue => ({ value, err_plus, err_minus, bound, unit });

  it("sin valor: null y value null", () => {
    expect(formatArchiveValue(null)).toBe("sin valor");
    expect(formatArchiveValue(v(null, null, null, "M_earth"))).toBe(
      "sin valor",
    );
    expect(formatArchiveValue(v(null, 0.1, 0.1, "day", "upper"))).toBe(
      "sin valor",
    );
  });

  it("asimétrico, simétrico y periodo (cadenas fijadas con el Python)", () => {
    expect(formatArchiveValue(v(5.69, 0.51, 0.5, "M_earth"))).toBe(
      "5,69 +0,51 / −0,5 M⊕",
    );
    expect(formatArchiveValue(v(1.8, 0.12, 0.12, "R_earth"))).toBe(
      "1,8 ± 0,12 R⊕",
    );
    expect(formatArchiveValue(v(1.64, 0.12, 0.11, "R_earth"))).toBe(
      "1,64 +0,12 / −0,11 R⊕",
    );
    expect(formatArchiveValue(v(1.305962, 0.000071, 0.00007, "day"))).toBe(
      "1,305962 +0,000071 / −0,00007 d",
    );
    expect(formatArchiveValue(v(1.3060011, 0.0000102, 0.0000102, "day"))).toBe(
      "1,3060011 ± 0,0000102 d",
    );
  });

  it("cotas: upper '<' y lower '>', con o sin errores", () => {
    expect(formatArchiveValue(v(5.2, null, null, "M_earth", "upper"))).toBe(
      "< 5,2 M⊕",
    );
    expect(formatArchiveValue(v(5.2, null, null, "R_earth", "lower"))).toBe(
      "> 5,2 R⊕",
    );
    expect(formatArchiveValue(v(5.2, 0.3, 0.3, "M_earth", "upper"))).toBe(
      "< 5,2 M⊕",
    );
  });

  it("errores nulos y bound null: solo valor y unidad", () => {
    expect(formatArchiveValue(v(5.2, null, null, "M_earth"))).toBe("5,2 M⊕");
    expect(formatArchiveValue(v(5.2, 0.3, null, "M_earth"))).toBe("5,2 M⊕");
    expect(formatArchiveValue(v(5.2, null, 0.3, "M_earth", null))).toBe(
      "5,2 M⊕",
    );
    expect(formatArchiveValue(v(5.2, 0.3, 0.3, "M_jup", null))).toBe(
      "5,2 ± 0,3 M♃",
    );
  });

  it("sin notación exponencial", () => {
    expect(formatArchiveValue(v(1e-7, 1e-8, 2e-8, "day"))).toBe(
      "0,0000001 +0,00000001 / −0,00000002 d",
    );
  });

  it("enteros con ',0' como Python (cadenas de format_archive_value)", () => {
    expect(formatArchiveValue(v(5, 1, 1, "M_earth"))).toBe("5,0 ± 1,0 M⊕");
    expect(formatArchiveValue(v(5, 2, 1, "M_earth"))).toBe(
      "5,0 +2,0 / −1,0 M⊕",
    );
    expect(formatArchiveValue(v(-0, 0.5, 0.5, "M_earth"))).toBe(
      "-0,0 ± 0,5 M⊕",
    );
    expect(formatArchiveValue(v(1e16, 1, 1, "M_earth"))).toBe(
      "10000000000000000 ± 1,0 M⊕",
    );
    expect(formatArchiveValue(v(4, null, null, "M_earth", "lower"))).toBe(
      "> 4,0 M⊕",
    );
    expect(formatArchiveValue(v(4, null, null, "M_earth", "upper"))).toBe(
      "< 4,0 M⊕",
    );
  });
});

describe("formatArchiveValueRounded (T102, solo lectura)", () => {
  const av = (
    value: number | null,
    err_plus: number | null,
    err_minus: number | null,
    unit: ArchiveValue["unit"],
    bound: ArchiveValue["bound"] = "none",
  ): ArchiveValue => ({ value, err_plus, err_minus, bound, unit });

  it.each([
    [
      av(708.75734761, 305.11527072, 244.72787339, "M_earth"),
      "709 +305 / −245 M⊕",
    ],
    [av(411.58985, 19.70546, 19.70546, "M_earth"), "412 ± 20 M⊕"],
    [av(577.6, 1.328, 1.328, "day"), "577,6 ± 1,3 d"],
    [av(1.3060011, 0.0000102, 0.0000102, "day"), "1,306001 ± 0,000010 d"],
    [
      av(1.305962, 0.000071, 0.00007, "day"),
      "1,305962 +0,000071 / −0,000070 d",
    ],
    [av(32.6, null, null, "R_earth"), "32,6 R⊕"],
    [av(5.0, 1.0, 1.0, "M_jup"), "5,0 ± 1,0 M♃"],
  ])("%j -> %s", (value, expected) => {
    expect(formatArchiveValueRounded(value)).toBe(expected);
  });

  it("errores distintos que redondean igual: usa ±", () => {
    expect(formatArchiveValueRounded(av(100.4, 20.4, 20.1, "M_earth"))).toBe(
      "100 ± 20 M⊕",
    );
  });

  it("sin valor", () => {
    expect(formatArchiveValueRounded(null)).toBe("sin valor");
    expect(formatArchiveValueRounded(av(null, null, null, "day"))).toBe(
      "sin valor",
    );
  });

  it("cotas: max(0, 3 − orden de magnitud) decimales, sin ceros finales", () => {
    expect(
      formatArchiveValueRounded(av(5.2345, null, null, "M_earth", "upper")),
    ).toBe("< 5,234 M⊕");
    expect(
      formatArchiveValueRounded(av(1234.5678, 3, 3, "M_earth", "lower")),
    ).toBe("> 1235 M⊕");
    expect(
      formatArchiveValueRounded(av(5, null, null, "M_earth", "upper")),
    ).toBe("< 5 M⊕");
  });

  it("error nulo o 0: sin errores, mismo redondeo que las cotas", () => {
    expect(formatArchiveValueRounded(av(12.3456, 0, 0, "R_earth"))).toBe(
      "12,35 R⊕",
    );
    expect(formatArchiveValueRounded(av(12.3456, 0.5, null, "R_earth"))).toBe(
      "12,35 R⊕",
    );
    expect(formatArchiveValueRounded(av(12.3456, 0, 0.5, "R_earth"))).toBe(
      "12,35 R⊕",
    );
  });

  it("-0 y valores que redondean a cero no llevan signo", () => {
    expect(formatArchiveValueRounded(av(-0, null, null, "M_earth"))).toBe(
      "0 M⊕",
    );
    expect(formatArchiveValueRounded(av(0, null, null, "M_earth"))).toBe(
      "0 M⊕",
    );
  });

  it("un valor negativo que redondea a cero no lleva signo, con errores", () => {
    expect(formatArchiveValueRounded(av(-0.0004, 0.5, 0.5, "M_earth"))).toBe(
      "0,00 ± 0,50 M⊕",
    );
  });

  it("sin notación exponencial", () => {
    for (const v of [1e-7, 1e-8]) {
      const out = formatArchiveValueRounded(av(v, null, null, "R_jup"));
      expect(out).not.toMatch(/e[+-]?\d/i);
      expect(out).toMatch(/^0,0{6,7}1 R♃$/);
      const withErr = formatArchiveValueRounded(av(v, v / 10, v / 10, "R_jup"));
      expect(withErr).not.toMatch(/e[+-]?\d/i);
    }
  });

  it("sin separador de miles", () => {
    expect(
      formatArchiveValueRounded(av(123456.789, 12.34, 12.34, "M_earth")),
    ).toBe("123457 ± 12 M⊕");
  });
});
