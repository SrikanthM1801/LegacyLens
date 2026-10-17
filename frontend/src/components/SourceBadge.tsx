import type { DataSource } from "../types";
import { Icon, type IconName } from "./Icon";

const META: Record<DataSource, { label: string; hint: string; icon: IconName; tone: string }> = {
  azure: { label: "Live: Azure audit trail", hint: "Every number comes from the audit blobs and consent tables in Azure Storage.", icon: "check", tone: "good" },
  memory: { label: "Offline: this session only", hint: "Events recorded by this running server, in memory. Nothing is read from Azure.", icon: "info", tone: "neutral" },
  demo: { label: "Demo data", hint: "Generated for demonstration. Not read from Teams or Azure.", icon: "alert", tone: "warning" },
};

/** Always visible: says exactly where the numbers on the page come from. */
export function SourceBadge({ source }: { source: DataSource }) {
  const m = META[source];
  return (
    <span className="chip" title={m.hint} role="status" aria-label={`Data source: ${m.label}. ${m.hint}`}>
      <Icon name={m.icon} className={`ico--${m.tone}`} />
      {m.label}
    </span>
  );
}
