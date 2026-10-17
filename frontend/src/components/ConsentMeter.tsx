import { pct } from "../format";
import type { ConsentStatus } from "../types";
import { Icon } from "./Icon";
import { CONSENT_META } from "./StatusChip";
import { useTip } from "./useTip";

const ORDER: ConsentStatus[] = ["granted", "paused", "pending", "declined"];
const FILL: Record<ConsentStatus, string> = {
  granted: "var(--good)", paused: "var(--warning)", pending: "var(--neutral)", declined: "var(--serious)",
};

/**
 * Where every conversation stands. A segmented bar (2px surface gaps, no outlines) plus an always-present
 * legend: each status is an icon + a label + a count, so the colours are never the only signal.
 */
export function ConsentMeter({ counts }: { counts: Record<ConsentStatus, number> }) {
  const { ref, tip, show, hide } = useTip();
  const total = ORDER.reduce((s, k) => s + counts[k], 0);
  const label = ORDER.map((k) => `${counts[k]} ${CONSENT_META[k].label.toLowerCase()}`).join(", ");
  return (
    <div className="chart" ref={ref}>
      <div className="meter" role="img" aria-label={`Conversations by consent: ${label}`}>
        {total === 0
          ? <div className="meter__seg" style={{ flex: 1, background: "var(--grid)" }} />
          : ORDER.filter((k) => counts[k] > 0).map((k) => (
            <div
              key={k} className="meter__seg" style={{ flex: counts[k], background: FILL[k] }}
              onPointerMove={(e) => show(e, <><div className="tip__title">{CONSENT_META[k].label}</div><div className="tip__value">{counts[k]} of {total}</div></>)}
              onPointerLeave={hide}
            />
          ))}
      </div>
      <div className="meter-legend">
        {ORDER.map((k) => (
          <div className="meter-legend__row" key={k}>
            <Icon name={CONSENT_META[k].icon} className={`ico--${CONSENT_META[k].tone}`} />
            <span>{CONSENT_META[k].label}</span>
            <b>{counts[k]}</b>
            <span style={{ color: "var(--muted)", minWidth: 40, textAlign: "right" }}>{total ? pct(counts[k] / total) : ""}</span>
          </div>
        ))}
      </div>
      {tip}
    </div>
  );
}
