import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import GlobalError from "./error";

describe("error.tsx", () => {
  it("texto nuevo, botones y sin message ni digest", () => {
    const html = renderToStaticMarkup(
      GlobalError({
        error: Object.assign(new Error("secreto-interno"), { digest: "abc123" }),
        reset: () => {},
      }),
    );
    expect(html).toContain("<h1");
    expect(html).toContain("No se ha podido cargar esta página");
    expect(html).toContain("Vuelve a intentarlo en unos minutos.");
    expect(html).toContain("Reintentar");
    expect(html).toContain("Volver al feed");
    expect(html).not.toContain("secreto-interno");
    expect(html).not.toContain("abc123");
  });
});
