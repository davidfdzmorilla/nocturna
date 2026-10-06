import type { FirstMeasurement } from "@/lib/api/types";
import { safeArchiveUrl } from "@/lib/externalLinks";
import { PARAMETER_LABELS, formatWithErrors } from "@/lib/measurementFormat";

type Props = {
  data: FirstMeasurement;
};

const STATUS_TEXT: Record<FirstMeasurement["archive_status"], string> = {
  absent: "El planeta no figura en el NASA Exoplanet Archive.",
  no_comparable_solution:
    "El archivo tiene el planeta, pero ninguna solución publicada y confirmada con error en ambos sentidos para este parámetro.",
};

export function FirstMeasurementData({ data }: Props) {
  const archiveUrl = safeArchiveUrl(data.archive_url);
  const caption = `${PARAMETER_LABELS[data.parameter]} de ${data.paper_planet_name} medida en el artículo`;

  return (
    <>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <caption className="pb-2 text-left text-text-muted">{caption}</caption>
          <thead>
            <tr>
              <th scope="col" className="border-b border-border p-2">
                Planeta
              </th>
              <th scope="col" className="border-b border-border p-2">
                Medida del artículo
              </th>
            </tr>
          </thead>
          <tbody>
            {data.measurements.map((m, index) => (
              <tr key={index}>
                <th
                  scope="row"
                  className="border-b border-border p-2 font-normal"
                >
                  {data.paper_planet_name}
                </th>
                <td className="border-b border-border p-2">
                  {formatWithErrors(m)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-sm text-text-muted">
        {STATUS_TEXT[data.archive_status]}
        {data.archive_planet_name
          ? ` Nombre en el archivo: ${data.archive_planet_name}.`
          : ""}
      </p>
      {archiveUrl ? (
        <p className="mt-1 text-sm">
          <a href={archiveUrl} rel="noopener noreferrer">
            Ficha del planeta en el NASA Exoplanet Archive
          </a>
        </p>
      ) : null}
    </>
  );
}
