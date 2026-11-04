import { describe, expect, it } from "vitest";
import type { FindingSummary } from "./api/types";
import { groupByNight } from "./feedGroups";

const f = (id: string, published_at: string): FindingSummary => ({
  id,
  type: "paper_explained",
  title: id,
  published_at,
  level_curious: "x",
});

describe("groupByNight", () => {
  it("vacío", () => {
    expect(groupByNight([])).toEqual([]);
  });

  it("un solo grupo", () => {
    const groups = groupByNight([
      f("a", "2026-11-04T03:10:00Z"),
      f("b", "2026-11-04T03:11:00Z"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe("4 de noviembre de 2026");
    expect(groups[0].isoDay).toBe("2026-11-04");
    expect(groups[0].items.map((i) => i.id)).toEqual(["a", "b"]);
  });

  it("un 23:30Z cae al día siguiente en Europe/Madrid", () => {
    const groups = groupByNight([
      f("a", "2026-11-04T03:10:00Z"),
      f("b", "2026-11-03T23:30:00Z"),
      f("c", "2026-11-03T03:05:00Z"),
    ]);
    expect(groups.map((g) => g.isoDay)).toEqual(["2026-11-04", "2026-11-03"]);
    expect(groups[0].items.map((i) => i.id)).toEqual(["a", "b"]);
    expect(groups[1].items.map((i) => i.id)).toEqual(["c"]);
  });

  it("conserva el orden de llegada", () => {
    const groups = groupByNight([
      f("a", "2026-11-02T03:00:00Z"),
      f("b", "2026-11-04T03:00:00Z"),
    ]);
    expect(groups.map((g) => g.items[0].id)).toEqual(["a", "b"]);
  });

  it("en horario de verano (UTC+2) un 22:30Z también cae al día siguiente", () => {
    const groups = groupByNight([
      f("a", "2026-07-02T03:00:00Z"),
      f("b", "2026-07-01T22:30:00Z"),
      f("c", "2026-07-01T21:30:00Z"),
    ]);
    expect(groups.map((g) => g.isoDay)).toEqual(["2026-07-02", "2026-07-01"]);
    expect(groups[0].items.map((i) => i.id)).toEqual(["a", "b"]);
  });

  it("isoDay sale del día de Madrid aunque el primero del grupo sea un 23:30Z", () => {
    const groups = groupByNight([f("b", "2026-11-03T23:30:00Z")]);
    expect(groups[0].key).toBe("4 de noviembre de 2026");
    expect(groups[0].isoDay).toBe("2026-11-04");
  });
});
