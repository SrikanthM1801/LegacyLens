import type { ConsentStatus, Outcome } from "../types";
import { Icon, type IconName } from "./Icon";

type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "info";

interface Meta {
  label: string;
  icon: IconName;
  tone: Tone;
}

/** Consent states wear the reserved status colours, always with an icon and a label. */
export const CONSENT_META: Record<ConsentStatus, Meta> = {
  granted: { label: "Allowed", icon: "check", tone: "good" },
  paused: { label: "Paused", icon: "pause", tone: "warning" },
  pending: { label: "Waiting for a decision", icon: "clock", tone: "neutral" },
  declined: { label: "Not now", icon: "x", tone: "serious" },
};

export const OUTCOME_META: Record<Outcome, Meta> = {
  success: { label: "OK", icon: "check", tone: "good" },
  failure: { label: "Failed", icon: "alert", tone: "critical" },
  skipped: { label: "Skipped", icon: "info", tone: "neutral" },
};

export function Chip({ meta }: { meta: Meta }) {
  return (
    <span className="chip">
      <Icon name={meta.icon} className={`ico--${meta.tone}`} />
      {meta.label}
    </span>
  );
}

export const ConsentChip = ({ status }: { status: ConsentStatus }) => <Chip meta={CONSENT_META[status]} />;
export const OutcomeChip = ({ outcome }: { outcome: Outcome }) => <Chip meta={OUTCOME_META[outcome]} />;
export type { Tone };
