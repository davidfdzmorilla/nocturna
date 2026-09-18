/**
 * Los tres niveles de lectura de un hallazgo. Funciones puras, sin IO.
 *
 * Nombres en español porque son texto de interfaz (`LEVEL_LABELS`) y
 * parámetro de URL visible al usuario (`levelHref`), no un identificador
 * técnico interno.
 */

import type { FindingDetail } from "./api/types";

export type Level = "curioso" | "aficionado" | "tecnico";

export const DEFAULT_LEVEL: Level = "curioso";

export const LEVEL_LABELS: Record<Level, string> = {
  curioso: "Curioso",
  aficionado: "Aficionado",
  tecnico: "Técnico",
};

const LEVELS: readonly Level[] = ["curioso", "aficionado", "tecnico"];

function isLevel(value: string): value is Level {
  return (LEVELS as readonly string[]).includes(value);
}

/**
 * Interpreta el parámetro `nivel` de la URL. Cualquier valor ausente o
 * desconocido cae al default `curioso`: un nivel inválido no es un 404,
 * es el nivel de entrada.
 */
export function parseLevelParam(raw: string | string[] | undefined): Level {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (value !== undefined && isLevel(value)) {
    return value;
  }
  return DEFAULT_LEVEL;
}

export function levelText(finding: FindingDetail, level: Level): string {
  switch (level) {
    case "curioso":
      return finding.level_curious;
    case "aficionado":
      return finding.level_amateur;
    case "tecnico":
      return finding.level_technical;
  }
}

/** El nivel por defecto no ensucia la URL con `?nivel=curioso`. */
export function levelHref(id: string, level: Level): string {
  return level === DEFAULT_LEVEL ? `/hallazgo/${id}` : `/hallazgo/${id}?nivel=${level}`;
}
