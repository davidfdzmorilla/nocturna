import { describe, expect, it } from "vitest";
import {
  SECTION_ORDER,
  entriesByKind,
  groupByReference,
  isoWeekRange,
  parseWeekParam,
  weekLabel,
} from "./archiveDigest";
import { ALMENARA, COLLIER, SCHMERLING, makeEntry, week40 } from "./archiveFixtures";

describe("parseWeekParam", () => {
  it.each(["2026-W41", "2026-W01", "2026-W53", "2020-W53"])("acepta %s", (w) => {
    expect(parseWeekParam(w)).toBe(w);
  });

  it.each([
    "2026-W00",
    "2026-W54",
    "2026-w41",
    "2026-41",
    "../x",
    "",
    "2026-W41 ",
    " 2026-W41",
    "2026-W41\n",
    "2026-W4",
    "26-W41",
    "2026-W41/../x",
    "2026-W060",
  ])("rechaza %j", (w) => {
    expect(parseWeekParam(w)).toBeNull();
  });

  it("rechaza undefined", () => {
    expect(parseWeekParam(undefined)).toBeNull();
  });
});

describe("secciones", () => {
  it("el orden es cambiada, nuevos, recuperados, pierden", () => {
    expect(SECTION_ORDER).toEqual(["changed", "new_planet", "regained", "lost"]);
  });

  it("entriesByKind filtra y conserva el orden de la API", () => {
    expect(entriesByKind(week40.entries, "new_planet").map((e) => e.pl_name)).toEqual([
      "HD 148797 b",
      "HD 148797 c",
      "TOI-2427 c",
    ]);
    expect(entriesByKind(week40.entries, "changed")).toHaveLength(1);
    expect(entriesByKind(week40.entries, "lost")).toEqual([]);
  });
});

describe("isoWeekRange", () => {
  it("2026-W41 -> 5 a 11 de octubre", () => {
    expect(isoWeekRange("2026-W41")).toEqual({ start: "2026-10-05", end: "2026-10-11" });
  });

  it("2026-W01 empieza el 29 de diciembre de 2025 (cruza año)", () => {
    expect(isoWeekRange("2026-W01")).toEqual({ start: "2025-12-29", end: "2026-01-04" });
  });

  it("2020-W53 existe y cruza al año siguiente", () => {
    expect(isoWeekRange("2020-W53")).toEqual({ start: "2020-12-28", end: "2021-01-03" });
  });

  it("2026-W53 existe (2026 tiene 53 semanas ISO)", () => {
    expect(isoWeekRange("2026-W53")).toEqual({ start: "2026-12-28", end: "2027-01-03" });
  });

  it("una semana que cruza de mes", () => {
    expect(isoWeekRange("2026-W40")).toEqual({ start: "2026-09-28", end: "2026-10-04" });
  });
});

describe("weekLabel", () => {
  it("dentro de un mes", () => {
    expect(weekLabel("2026-W41")).toBe("Semana 41 de 2026 (5–11 de octubre)");
  });

  it("cruzando mes", () => {
    expect(weekLabel("2026-W40")).toBe("Semana 40 de 2026 (28 de septiembre – 4 de octubre)");
  });

  it("cruzando año", () => {
    expect(weekLabel("2026-W01")).toBe("Semana 1 de 2026 (29 de diciembre – 4 de enero)");
    expect(weekLabel("2020-W53")).toBe("Semana 53 de 2020 (28 de diciembre – 3 de enero)");
  });
});

describe("groupByReference", () => {
  it("agrupa por referencia nueva en orden de primera aparición", () => {
    const a = makeEntry("new_planet", "A b", null, ALMENARA);
    const b = makeEntry("new_planet", "B b", null, COLLIER);
    const c = makeEntry("new_planet", "C b", null, ALMENARA);
    const d = makeEntry("new_planet", "D b", null, SCHMERLING);
    const groups = groupByReference([a, b, c, d]);
    expect(groups.map((g) => g.reference)).toEqual([
      ALMENARA.reference,
      COLLIER.reference,
      SCHMERLING.reference,
    ]);
    expect(groups[0].entries.map((e) => e.pl_name)).toEqual(["A b", "C b"]);
  });

  it("sin referencia nueva cae en un grupo propio; vacío da []", () => {
    const lost = makeEntry("lost", "L b", ALMENARA, null);
    expect(groupByReference([lost])).toEqual([
      { reference: "(sin referencia)", entries: [lost] },
    ]);
    expect(groupByReference([])).toEqual([]);
  });
});
