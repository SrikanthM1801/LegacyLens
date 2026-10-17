import type { ReactNode } from "react";
import { compact } from "../format";
import { Empty } from "./Card";
import { useTip } from "./useTip";

export interface BarItem {
  key: string;
  label: string;
  value: number;
  /** Secondary text after the value, e.g. a failure count. */
  detail?: ReactNode;
  /** Tooltip body; defaults to "label: value". */
  tip?: ReactNode;
}

/**
 * Horizontal bars for nominal categories: one hue for every bar (colour never re-encodes the length),
 * 16px thick with a 4px rounded data end and a square baseline, the value at the tip. The row is the
 * hover/focus target, so there is no pinpoint to hit.
 */
export function HBars({
  items, colorVar = "--series-1", format = compact, empty = "Nothing to show for this period.",
}: { items: BarItem[]; colorVar?: string; format?: (n: number) => string; empty?: string }) {
  const { ref, tip, show, showForElement, hide } = useTip();
  const max = Math.max(...items.map((i) => i.value), 0);
  if (items.length === 0 || max === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="chart" ref={ref}>
      <div className="hbars" role="list">
        {items.map((it) => {
          const body = it.tip ?? <><div className="tip__title">{it.label}</div><div className="tip__value">{format(it.value)}</div></>;
          return (
            <div
              key={it.key} className="hbar" role="listitem" tabIndex={0} aria-label={`${it.label}: ${format(it.value)}`}
              onPointerMove={(e) => show(e, body)} onPointerLeave={hide}
              onFocus={(e) => showForElement(e.currentTarget, body)} onBlur={hide}
            >
              <span className="hbar__label" title={it.label}>{it.label}</span>
              <span className="hbar__track">
                <span className="hbar__bar" style={{ background: `var(${colorVar})`, width: `calc((100% - 110px) * ${it.value / max})` }} />
                <span className="hbar__value">{format(it.value)}</span>
                {it.detail && <span className="hbar__detail">{it.detail}</span>}
              </span>
            </div>
          );
        })}
      </div>
      {tip}
    </div>
  );
}
