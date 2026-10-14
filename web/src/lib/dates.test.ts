import { describe, expect, it } from "vitest";
import { formatDay } from "./dates";

describe("formatDay", () => {
  it("fecha sin hora: el mismo día, sin correrse", () => {
    expect(formatDay("2022-02-15")).toBe("15 de febrero de 2022");
    expect(formatDay("2026-01-01")).toBe("1 de enero de 2026");
  });

  it("instante: día en Europe/Madrid (verano e invierno)", () => {
    expect(formatDay("2026-10-01T23:30:00Z")).toBe("2 de octubre de 2026");
    expect(formatDay("2026-01-01T23:30:00Z")).toBe("2 de enero de 2026");
    expect(formatDay("2026-10-09T03:00:00Z")).toBe("9 de octubre de 2026");
  });

  it("valor ilegible se devuelve tal cual", () => {
    expect(formatDay("no es fecha")).toBe("no es fecha");
  });
});
