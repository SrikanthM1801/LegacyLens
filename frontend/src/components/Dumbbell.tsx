import { Icon } from "./Icon";
import { Empty } from "./Card";
import { useTip } from "./useTip";
import type { RiskImpact } from "../types";

const SCORE = (x: number) => x.toFixed(2);

/**
 * Before -> after per item on a shared 0..1 scale: one hue in two shades (light = before, dark = after),
 * joined by a 2px link. A drop in risk is the good direction, so the delta wears the success text colour
 * with an arrow and the number (never colour alone).
 */
export function Dumbbell({ items }: { items: RiskImpact[] }) {
  const { ref, tip, show, showForElement, hide } = useTip();
  if (items.length === 0) return <Empty>No interviews have been approved in this period yet.</Empty>;
  return (
    <div className="chart" ref={ref}>
      <div className="dumbbell">
        {items.map((it) => {
          const lo = Math.min(it.risk_before, it.risk_after);
          const hi = Math.max(it.risk_before, it.risk_after);
          const drop = it.risk_before - it.risk_after;
          const body = (
            <>
              <div className="tip__title">{it.topic}</div>
              <div className="tip__row"><span className="tip__value">{SCORE(it.risk_before)}</span><span className="tip__name">before the interview</span></div>
              <div className="tip__row"><span className="tip__value">{SCORE(it.risk_after)}</span><span className="tip__name">after approval</span></div>
            </>
          );
          return (
            <div
              key={`${it.topic}-${it.approved_at}`} className="dumbbell__row" tabIndex={0}
              aria-label={`${it.topic}: risk ${SCORE(it.risk_before)} before, ${SCORE(it.risk_after)} after`}
              onPointerMove={(e) => show(e, body)} onPointerLeave={hide}
              onFocus={(e) => showForElement(e.currentTarget, body)} onBlur={hide}
            >
              <span className="hbar__label" title={it.topic}>{it.topic}</span>
              <span className="dumbbell__track">
                <span className="dumbbell__axis" />
                <span className="dumbbell__link" style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }} />
                <span className="dumbbell__dot" style={{ left: `${it.risk_before * 100}%`, background: "var(--seq-before)" }} />
                <span className="dumbbell__dot" style={{ left: `${it.risk_after * 100}%`, background: "var(--seq-after)" }} />
              </span>
              <span className="dumbbell__delta">
                {drop > 0 ? <Icon name="down" /> : null}
                {drop > 0 ? `−${SCORE(drop)}` : SCORE(drop)}
              </span>
            </div>
          );
        })}
        <div className="dumbbell__scale" aria-hidden="true">
          <span />
          <div><span>0 (covered)</span><span>0.5</span><span>1 (at risk)</span></div>
          <span />
        </div>
      </div>
      <div className="legend">
        <span className="legend__item"><span className="swatch" style={{ background: "var(--seq-before)", borderRadius: "50%" }} />Before the interview</span>
        <span className="legend__item"><span className="swatch" style={{ background: "var(--seq-after)", borderRadius: "50%" }} />After approval</span>
      </div>
      {tip}
    </div>
  );
}
