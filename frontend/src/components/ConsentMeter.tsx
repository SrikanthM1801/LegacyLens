import type { ConsentStatus } from "../types";
import { SegmentMeter, type Segment } from "./SegmentMeter";
import { CONSENT_META } from "./StatusChip";

const ORDER: ConsentStatus[] = ["granted", "paused", "pending", "declined"];
const FILL: Record<ConsentStatus, string> = {
  granted: "var(--good)", paused: "var(--warning)", pending: "var(--neutral)", declined: "var(--serious)",
};

/** Where every conversation stands, by consent status (status colours, always with icon + label). */
export function ConsentMeter({ counts }: { counts: Record<ConsentStatus, number> }) {
  const segments: Segment[] = ORDER.map((k) => ({
    key: k, label: CONSENT_META[k].label, count: counts[k], icon: CONSENT_META[k].icon, tone: CONSENT_META[k].tone, fill: FILL[k],
  }));
  return <SegmentMeter segments={segments} ariaLabel="Conversations by consent" />;
}
