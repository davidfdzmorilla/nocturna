import type { FindingDetail } from "@/lib/api/types";
import { CatalogTensionData } from "./CatalogTensionData";
import { ConfirmationData } from "./ConfirmationData";
import { FirstMeasurementData } from "./FirstMeasurementData";

type FindingDataProps = {
  finding: FindingDetail;
};

/**
 * Bloque "Datos del contraste": los números salen del dato estructurado
 * (ADR 0012 §13), no del texto. Si el payload del tipo falta, no pinta nada.
 */
export function FindingData({ finding }: FindingDataProps) {
  let body = null;
  switch (finding.type) {
    case "catalog_tension":
      body = finding.catalog_tension ? (
        <CatalogTensionData
          data={finding.catalog_tension}
          sourceUrl={finding.source_url}
        />
      ) : null;
      break;
    case "primera_medida":
      body = finding.first_measurement ? (
        <FirstMeasurementData data={finding.first_measurement} />
      ) : null;
      break;
    case "confirmacion_independiente":
      body = finding.independent_confirmation ? (
        <ConfirmationData data={finding.independent_confirmation} />
      ) : null;
      break;
    case "paper_explained":
      body = null;
      break;
  }
  if (body === null) {
    return null;
  }
  return (
    <section aria-labelledby="datos-contraste" className="mt-8">
      <h2 id="datos-contraste" className="text-lg font-semibold text-text">
        Datos del contraste
      </h2>
      {body}
    </section>
  );
}
