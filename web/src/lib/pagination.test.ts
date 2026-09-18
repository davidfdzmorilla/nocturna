import { describe, expect, it } from "vitest";
import { parsePageParam, totalPages, pageHref } from "./pagination";

describe("parsePageParam", () => {
  it("valor ausente cae a la página 1", () => {
    expect(parsePageParam(undefined)).toBe(1);
  });

  it('"0" cae a la página 1 (no es un 404, es la primera página)', () => {
    expect(parsePageParam("0")).toBe(1);
  });

  it('"-3" cae a la página 1', () => {
    expect(parsePageParam("-3")).toBe(1);
  });

  it('"abc" (no numérico) cae a la página 1', () => {
    expect(parsePageParam("abc")).toBe(1);
  });

  it('"2" se interpreta como página 2', () => {
    expect(parsePageParam("2")).toBe(2);
  });

  it("un array usa el primer valor (Next puede repetir el parámetro en la query string)", () => {
    expect(parsePageParam(["3", "5"])).toBe(3);
  });

  it("un entero desbordante cae a la página 1 en vez de reenviarse a la API", () => {
    expect(parsePageParam("100000000000000000000")).toBe(1);
  });

  it('notación científica ("1e21") cae a la página 1 en vez de provocar un 422', () => {
    expect(parsePageParam("1e21")).toBe(1);
  });

  it("un entero justo por encima del máximo aceptado cae a la página 1", () => {
    expect(parsePageParam("1000000")).toBe(1);
  });

  it("el máximo aceptado (6 dígitos) se interpreta con normalidad", () => {
    expect(parsePageParam("999999")).toBe(999999);
  });
});

describe("totalPages", () => {
  it("total 0 da como mínimo 1 página", () => {
    expect(totalPages(0, 20)).toBe(1);
  });

  it("total igual al tamaño de página da 1 página", () => {
    expect(totalPages(20, 20)).toBe(1);
  });

  it("total una unidad por encima del tamaño de página da 2 páginas", () => {
    expect(totalPages(21, 20)).toBe(2);
  });
});

describe("pageHref", () => {
  it("la página 1 enlaza a la raíz, sin query string", () => {
    expect(pageHref(1)).toBe("/");
  });
});
