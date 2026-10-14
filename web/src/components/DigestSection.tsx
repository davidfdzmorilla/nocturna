import type {
  DigestEntry,
  DigestReference,
  ParameterChange,
  TransitionKind,
} from "@/lib/api/types";
import { SECTION_LABELS, groupByReference } from "@/lib/archiveDigest";
import { formatDay } from "@/lib/dates";
import { safeArchiveUrl } from "@/lib/externalLinks";
import { PARAMETER_LABELS, formatArchiveValue } from "@/lib/measurementFormat";

type Props = {
  kind: TransitionKind;
  entries: DigestEntry[];
};

const EMPTY_TEXT = "Ninguno esta semana.";
const NO_PARAMETER_CHANGES = "Sin cambios en masa, radio ni periodo.";
const NO_PROVENANCE = "–";

function PlanetName({ entry }: { entry: DigestEntry }) {
  const url = safeArchiveUrl(entry.planet_url);
  return url ? (
    <a href={url} rel="noopener noreferrer">
      {entry.pl_name}
    </a>
  ) : (
    <>{entry.pl_name}</>
  );
}

function ReferenceFields({
  label,
  reference,
}: {
  label: string;
  reference: DigestReference;
}) {
  return (
    <>
      <dt className="text-text-muted">{label}</dt>
      <dd>{reference.reference}</dd>
      <dt className="text-text-muted">Publicación</dt>
      <dd>{reference.pl_pubdate ?? "–"}</dd>
      <dt className="text-text-muted">Alta en el archivo</dt>
      <dd>{formatDay(reference.releasedate)}</dd>
    </>
  );
}

function ValueCell({
  change,
  side,
}: {
  change: ParameterChange;
  side: "old" | "new";
}) {
  const value = side === "old" ? change.old : change.new;
  const provenance =
    side === "old" ? change.old_mass_provenance : change.new_mass_provenance;
  return (
    <td className="border-b border-border p-2">
      <span className="block">{formatArchiveValue(value)}</span>
      {change.parameter === "mass" ? (
        <span className="block text-text-muted">
          Procedencia de la masa: {provenance ?? NO_PROVENANCE}
        </span>
      ) : null}
    </td>
  );
}

function ChangesTable({ entry }: { entry: DigestEntry }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <caption className="pb-2 text-left text-text-muted">
          Parámetros de {entry.pl_name} que difieren entre la referencia
          anterior y la nueva
        </caption>
        <thead>
          <tr>
            <th scope="col" className="border-b border-border p-2">
              Parámetro
            </th>
            <th scope="col" className="border-b border-border p-2">
              Referencia anterior
            </th>
            <th scope="col" className="border-b border-border p-2">
              Referencia nueva
            </th>
          </tr>
        </thead>
        <tbody>
          {entry.parameter_changes.map((change) => (
            <tr key={change.parameter} className="align-top">
              <th scope="row" className="border-b border-border p-2 font-normal">
                {PARAMETER_LABELS[change.parameter]}
              </th>
              <ValueCell change={change} side="old" />
              <ValueCell change={change} side="new" />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EntryCard({ entry }: { entry: DigestEntry }) {
  const showBoth = entry.kind === "changed";
  const single = entry.new ?? entry.old;
  return (
    <article className="rounded-md border border-border bg-surface p-4">
      <h3 className="text-lg font-semibold">
        <PlanetName entry={entry} />
      </h3>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {showBoth ? (
          <>
            {entry.old ? (
              <ReferenceFields label="Referencia anterior" reference={entry.old} />
            ) : null}
            {entry.new ? (
              <ReferenceFields label="Referencia nueva" reference={entry.new} />
            ) : null}
          </>
        ) : single ? (
          <ReferenceFields label="Referencia" reference={single} />
        ) : null}
        <dt className="text-text-muted">Detectado</dt>
        <dd>{formatDay(entry.detected_at)}</dd>
      </dl>
      {entry.parameter_changes.length > 0 ? (
        <ChangesTable entry={entry} />
      ) : showBoth ? (
        <p className="mt-3 text-sm text-text-muted">{NO_PARAMETER_CHANGES}</p>
      ) : null}
    </article>
  );
}

function NewPlanetGroups({ entries }: { entries: DigestEntry[] }) {
  return (
    <div className="flex flex-col gap-4">
      {groupByReference(entries).map((group) => (
        <div
          key={group.reference}
          className="rounded-md border border-border bg-surface p-4"
        >
          <h3 className="text-lg font-semibold">
            {group.reference} · {group.entries.length}{" "}
            {group.entries.length === 1 ? "planeta" : "planetas"}
          </h3>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {group.entries.map((entry) => (
              <li key={entry.pl_name}>
                <PlanetName entry={entry} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function DigestSection({ kind, entries }: Props) {
  return (
    <section className="mt-8">
      <h2 className="text-xl font-semibold text-text">
        {SECTION_LABELS[kind]} ({entries.length})
      </h2>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">{EMPTY_TEXT}</p>
      ) : kind === "new_planet" ? (
        <div className="mt-3">
          <NewPlanetGroups entries={entries} />
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-4">
          {entries.map((entry) => (
            <EntryCard key={`${entry.kind}:${entry.pl_name}:${entry.detected_at}`} entry={entry} />
          ))}
        </div>
      )}
    </section>
  );
}
