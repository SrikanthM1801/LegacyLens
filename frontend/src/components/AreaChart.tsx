import { useState, type KeyboardEvent, type PointerEvent } from "react";
import { compact, fmtDay, niceTicks } from "../format";
import { useWidth } from "../hooks";
import { TipRow, useTip } from "./useTip";

export interface SeriesPoint {
  x: string;
  y: number;
}

const M = { l: 40, r: 16, t: 12, b: 26 };

/**
 * One series over time: 2px line, ~10% area wash, hairline grid, end dot with a 2px surface ring.
 * A crosshair snaps to the nearest day (pointer or arrow keys) and a tooltip shows the value.
 */
export function AreaChart({
  data, colorVar, name, height = 200, xLabel = fmtDay,
}: { data: SeriesPoint[]; colorVar: string; name: string; height?: number; xLabel?: (x: string) => string }) {
  const { ref, tip, showAt, hide } = useTip();
  const width = useWidth(ref);
  const [active, setActive] = useState<number | null>(null);

  const n = data.length;
  const iw = width - M.l - M.r;
  const ih = height - M.t - M.b;
  const ticks = niceTicks(Math.max(0, ...data.map((d) => d.y)));
  const yMax = ticks[ticks.length - 1];
  const xAt = (i: number) => M.l + (n > 1 ? (i / (n - 1)) * iw : iw / 2);
  const yAt = (v: number) => M.t + ih - (v / yMax) * ih;
  const color = `var(${colorVar})`;

  const line = data.map((d, i) => `${i ? "L" : "M"}${xAt(i).toFixed(1)},${yAt(d.y).toFixed(1)}`).join("");
  const area = n ? `${line}L${xAt(n - 1).toFixed(1)},${M.t + ih}L${xAt(0).toFixed(1)},${M.t + ih}Z` : "";
  const labelIdx = n > 2 ? [0, Math.floor((n - 1) / 2), n - 1] : data.map((_, i) => i);

  const focus = (i: number) => {
    if (!Number.isFinite(i)) return; // a pointer with no usable position must never throw
    const idx = Math.min(n - 1, Math.max(0, i));
    setActive(idx);
    const d = data[idx];
    showAt(xAt(idx), yAt(d.y), (
      <>
        <div className="tip__title">{xLabel(d.x)}</div>
        <TipRow colorVar={colorVar} value={compact(d.y)} name={name} />
      </>
    ));
  };
  const clear = () => { setActive(null); hide(); };
  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    if (!(box.width > 0)) return;
    focus(n > 1 ? Math.round(((e.clientX - box.left) / box.width) * (n - 1)) : 0);
  };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const cur = active ?? n - 1;
    const next = { ArrowLeft: cur - 1, ArrowRight: cur + 1, Home: 0, End: n - 1 }[e.key];
    if (next !== undefined) { e.preventDefault(); focus(next); }
    else if (e.key === "Escape") clear();
  };

  if (n === 0) return null;
  const last = data[n - 1];
  return (
    <div className="chart" ref={ref}>
      <svg
        width={width} height={height} tabIndex={0} role="img" onKeyDown={onKey} onFocus={() => focus(n - 1)} onBlur={clear}
        aria-label={`${name} per day, ${n} days, latest ${compact(last.y)}. Use the left and right arrow keys to read each day.`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className={t === 0 ? "baseline" : "gridline"} x1={M.l} x2={width - M.r} y1={yAt(t)} y2={yAt(t)} />
            <text x={M.l - 8} y={yAt(t) + 4} textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        <path d={area} fill={color} fillOpacity={0.1} />
        <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {labelIdx.map((i) => (
          <text key={i} x={xAt(i)} y={height - 6} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>
            {xLabel(data[i].x)}
          </text>
        ))}
        {active !== null && (
          <line className="cross" x1={xAt(active)} x2={xAt(active)} y1={M.t} y2={M.t + ih} />
        )}
        <circle cx={xAt(n - 1)} cy={yAt(last.y)} r={4} fill={color} stroke="var(--surface)" strokeWidth={2} />
        {active !== null && active !== n - 1 && (
          <circle cx={xAt(active)} cy={yAt(data[active].y)} r={4} fill={color} stroke="var(--surface)" strokeWidth={2} />
        )}
        {/* the hit area is the whole plot, not the 2px line */}
        <rect x={M.l} y={M.t} width={iw} height={ih} fill="transparent" onPointerMove={onMove} onPointerLeave={clear} />
      </svg>
      {tip}
    </div>
  );
}
