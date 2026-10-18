import { pct } from "../format";
import { Icon, type IconName } from "./Icon";
import { useTip } from "./useTip";

export interface Segment {
  key: string;
  label: string;
  count: number;
  icon: IconName;
  tone: string;     // status tone class suffix: good | warning | serious | neutral
  fill: string;     // css colour of the segment
}

/**
 * A segmented bar (2px surface gaps, no outlines) plus an always-present legend: every segment is an icon + a label +
 * a count, so the colours are never the only signal. Used for consent status and for the onboarding readiness spread.
 */
export function SegmentMeter({ segments, ariaLabel, noun = "of" }: { segments: Segment[]; ariaLabel: string; noun?: string }) {
  const { ref, tip, show, hide } = useTip();
  const total = segments.reduce((s, x) => s + x.count, 0);
  const label = segments.map((s) => `${s.count} ${s.label.toLowerCase()}`).join(", ");
  return (
    <div className="chart" ref={ref}>
      <div className="meter" role="img" aria-label={`${ariaLabel}: ${label}`}>
        {total === 0
          ? <div className="meter__seg" style={{ flex: 1, background: "var(--grid)" }} />
          : segments.filter((s) => s.count > 0).map((s) => (
            <div
              key={s.key} className="meter__seg" style={{ flex: s.count, background: s.fill }}
              onPointerMove={(e) => show(e, <><div className="tip__title">{s.label}</div><div className="tip__value">{s.count} {noun} {total}</div></>)}
              onPointerLeave={hide}
            />
          ))}
      </div>
      <div className="meter-legend">
        {segments.map((s) => (
          <div className="meter-legend__row" key={s.key}>
            <Icon name={s.icon} className={`ico--${s.tone}`} />
            <span>{s.label}</span>
            <b>{s.count}</b>
            <span style={{ color: "var(--muted)", minWidth: 40, textAlign: "right" }}>{total ? pct(s.count / total) : ""}</span>
          </div>
        ))}
      </div>
      {tip}
    </div>
  );
}
