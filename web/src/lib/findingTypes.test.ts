import { describe, expect, it } from "vitest";
import {
  CANDIDATE_LEGEND,
  FINDING_TYPES,
  TYPE_LABELS,
  TYPE_SLUGS,
  feedHref,
  isCandidate,
  parseTypeParam,
} from "./findingTypes";

describe("parseTypeParam", () => {
  it.each([
    ["articulo", "paper_explained"],
    ["tension", "catalog_tension"],
    ["primera-medida", "primera_medida"],
    ["confirmacion", "confirmacion_independiente"],
  ])("slug %s -> %s", (slug, type) => {
    expect(parseTypeParam(slug)).toBe(type);
  });

  it("ausente, desconocido o vacío da null", () => {
    expect(parseTypeParam(undefined)).toBeNull();
    expect(parseTypeParam("nope")).toBeNull();
    expect(parseTypeParam("")).toBeNull();
  });

  it("los valores del enum no son slugs válidos", () => {
    expect(parseTypeParam("catalog_tension")).toBeNull();
  });

  it("mayúsculas no coinciden", () => {
    expect(parseTypeParam("Tension")).toBeNull();
  });

  it("un array usa el primer valor", () => {
    expect(parseTypeParam(["tension", "articulo"])).toBe("catalog_tension");
  });
});

describe("etiquetas", () => {
  it("textos aprobados para los cuatro tipos", () => {
    expect(TYPE_LABELS).toEqual({
      paper_explained: "Artículo explicado",
      catalog_tension: "Tensión con el catálogo",
      primera_medida: "Primera medida",
      confirmacion_independiente: "Confirmación independiente",
    });
    expect(Object.keys(TYPE_SLUGS).sort()).toEqual([...FINDING_TYPES].sort());
  });

  it("la leyenda de candidato es la aprobada", () => {
    expect(CANDIDATE_LEGEND).toBe(
      "Candidato: discrepancia calculada automáticamente frente al NASA Exoplanet Archive; ninguna persona la ha verificado.",
    );
  });
});

describe("isCandidate", () => {
  it("solo catalog_tension", () => {
    expect(FINDING_TYPES.filter(isCandidate)).toEqual(["catalog_tension"]);
  });
});

describe("feedHref", () => {
  it("combinaciones de página y tipo", () => {
    expect(feedHref(1, null)).toBe("/");
    expect(feedHref(2, null)).toBe("/?page=2");
    expect(feedHref(1, "confirmacion_independiente")).toBe(
      "/?tipo=confirmacion",
    );
    expect(feedHref(4, "paper_explained")).toBe("/?tipo=articulo&page=4");
  });
});
