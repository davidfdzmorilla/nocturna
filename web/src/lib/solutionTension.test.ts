import { describe, expect, it } from "vitest";
import {
  NO_OTHERS_TEXT,
  SIGMA_NOTE,
  SOLUTION_TENSION_LABELS,
  formatReleaseDate,
  knownLabels,
  referenceRoleText,
} from "./solutionTension";

describe("solutionTension", () => {
  it("etiquetas: chip y frase aprobados", () => {
    expect(SOLUTION_TENSION_LABELS.model_dependent).toEqual({
      chip: "Depende del modelo",
      sentence:
        "El mismo trabajo publica varias soluciones de este planeta en el archivo; la diferencia puede depender de la solución elegida.",
    });
    expect(SOLUTION_TENSION_LABELS.controversial).toEqual({
      chip: "Planeta controvertido",
      sentence:
        "El NASA Exoplanet Archive marca este planeta como controvertido.",
    });
  });

  it("roles de la referencia", () => {
    expect(referenceRoleText("default")).toBe(
      "Referencia: la solución por defecto del archivo.",
    );
    expect(referenceRoleText("most_recent")).toBe(
      "Referencia: la solución más reciente de otro trabajo, porque la nueva es la solución por defecto (o la por defecto es del mismo trabajo).",
    );
  });

  it("notas fijas", () => {
    expect(SIGMA_NOTE).toContain("La σ mide la diferencia");
    expect(NO_OTHERS_TEXT).toBe(
      "Ninguna otra solución publicada y confirmada para este parámetro.",
    );
  });

  it("knownLabels descarta extreme_sigma y desconocidas, conserva orden", () => {
    expect(
      knownLabels(["extreme_sigma", "controversial", "x", "model_dependent"]),
    ).toEqual(["controversial", "model_dependent"]);
    expect(knownLabels(["extreme_sigma"])).toEqual([]);
    expect(knownLabels(["toString", "constructor"])).toEqual([]);
    expect(knownLabels([])).toEqual([]);
  });

  it("formatReleaseDate sin desfase y null -> null", () => {
    expect(formatReleaseDate("2026-08-20")).toBe(
      "Entró en el archivo el 20 de agosto de 2026.",
    );
    expect(formatReleaseDate("2026-09-01")).toBe(
      "Entró en el archivo el 1 de septiembre de 2026.",
    );
    expect(formatReleaseDate(null)).toBeNull();
  });
});
