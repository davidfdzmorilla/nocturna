import { describe, expect, it } from "vitest";
import {
  CANDIDATE_LEGEND,
  SOLUTION_CANDIDATE_LEGEND,
  candidateLegend,
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
    ["tension-soluciones", "solution_tension"],
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

  it("solution_tension no es slug válido y tension sigue siendo catalog_tension", () => {
    expect(parseTypeParam("solution_tension")).toBeNull();
    expect(parseTypeParam("tension")).toBe("catalog_tension");
    expect(parseTypeParam("tension-soluciones")).toBe("solution_tension");
  });

  it("mayúsculas no coinciden", () => {
    expect(parseTypeParam("Tension")).toBeNull();
  });

  it("un array usa el primer valor", () => {
    expect(parseTypeParam(["tension", "articulo"])).toBe("catalog_tension");
  });
});

describe("etiquetas", () => {
  it("textos aprobados para los cinco tipos", () => {
    expect(TYPE_LABELS).toEqual({
      paper_explained: "Artículo explicado",
      catalog_tension: "Tensión con el catálogo",
      solution_tension: "Tensión entre soluciones",
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
  it("catalog_tension y solution_tension", () => {
    expect(FINDING_TYPES.filter(isCandidate)).toEqual([
      "catalog_tension",
      "solution_tension",
    ]);
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

describe("isCandidate y candidateLegend por tipo", () => {
  it.each([
    ["paper_explained", false],
    ["catalog_tension", true],
    ["solution_tension", true],
    ["primera_medida", false],
    ["confirmacion_independiente", false],
  ] as const)("%s -> %s", (type, expected) => {
    expect(isCandidate(type)).toBe(expected);
  });

  it("leyenda por tipo", () => {
    expect(candidateLegend("solution_tension")).toBe(SOLUTION_CANDIDATE_LEGEND);
    expect(SOLUTION_CANDIDATE_LEGEND).toBe(
      "Candidato: discrepancia entre soluciones del NASA Exoplanet Archive calculada automáticamente; ninguna persona la ha verificado.",
    );
    for (const t of FINDING_TYPES.filter((t) => t !== "solution_tension")) {
      expect(candidateLegend(t)).toBe(CANDIDATE_LEGEND);
    }
  });

  it("feedHref con solution_tension", () => {
    expect(feedHref(1, "solution_tension")).toBe("/?tipo=tension-soluciones");
    expect(feedHref(3, "solution_tension")).toBe(
      "/?tipo=tension-soluciones&page=3",
    );
  });
});
