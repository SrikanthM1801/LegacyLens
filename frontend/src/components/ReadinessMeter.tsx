import type { Readiness } from "../types";
import { SegmentMeter, type Segment } from "./SegmentMeter";

const META: Record<Readiness, Omit<Segment, "key" | "count">> = {
  ready: { label: "Ready", icon: "check", tone: "good", fill: "var(--good)" },
  almost: { label: "Almost ready", icon: "clock", tone: "warning", fill: "var(--warning)" },
  needs_more_time: { label: "Needs more time", icon: "alert", tone: "serious", fill: "var(--serious)" },
};
const ORDER: Readiness[] = ["ready", "almost", "needs_more_time"];

/** The spread of onboarding readiness across a group. Only ever rendered for a group, never for one person. */
export function ReadinessMeter({ counts }: { counts: Record<Readiness, number> }) {
  const segments: Segment[] = ORDER.map((k) => ({ key: k, count: counts[k], ...META[k] }));
  return <SegmentMeter segments={segments} ariaLabel="Onboarding readiness across the group" />;
}
