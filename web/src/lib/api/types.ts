/**
 * Tipos del contrato de la API de lectura de Nocturna.
 *
 * Escritos a mano a partir de `backend/src/nocturna/api/schemas.py`
 * (fuente de verdad). Sin generación desde OpenAPI: para tres endpoints
 * de solo lectura sería sobrearquitectura.
 *
 * Deliberadamente fuera de estos tipos, igual que en el backend:
 * `confidence` (nota editorial interna), `run_id` y `item_id` (telemetría
 * del pipeline). El identificador público de un hallazgo es `id`.
 */

export type FindingSummary = {
  id: string;
  title: string;
  published_at: string;
  level_curious: string;
};

export type FindingDetail = FindingSummary & {
  type: string;
  level_amateur: string;
  level_technical: string;
  source_url: string | null;
};

export type FindingsPage = {
  items: FindingSummary[];
  page: number;
  size: number;
  total: number;
};
