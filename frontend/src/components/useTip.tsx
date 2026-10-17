import { useRef, useState, type ReactNode } from "react";

interface TipState {
  x: number;
  y: number;
  node: ReactNode;
}

/**
 * One tooltip per chart. Attach `ref` to the chart's positioned wrapper; `show` takes pointer coordinates,
 * `showAt` takes coordinates relative to the wrapper (used for keyboard focus). Content is always React
 * children, so series and category names (untrusted data) are escaped, never injected as HTML.
 */
export function useTip() {
  const ref = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<TipState | null>(null);

  const showAt = (x: number, y: number, node: ReactNode) => setState({ x, y, node });
  const show = (e: { clientX: number; clientY: number }, node: ReactNode) => {
    const r = ref.current?.getBoundingClientRect();
    if (r) showAt(e.clientX - r.left, e.clientY - r.top, node);
  };
  const showForElement = (el: HTMLElement, node: ReactNode) => {
    const r = ref.current?.getBoundingClientRect();
    const b = el.getBoundingClientRect();
    if (r) showAt(b.left - r.left + Math.min(b.width, 120), b.top - r.top, node);
  };
  const hide = () => setState(null);

  const width = ref.current?.clientWidth ?? 0;
  const flip = state ? state.x > width * 0.6 : false;
  const tip = state ? (
    <div
      className="tip" role="tooltip"
      style={{ left: flip ? state.x - 14 : state.x + 14, top: Math.max(0, state.y - 10), transform: flip ? "translateX(-100%)" : undefined }}
    >
      {state.node}
    </div>
  ) : null;

  return { ref, tip, show, showAt, showForElement, hide };
}

/** Value-first tooltip row: a short line key in the series colour, the value strong, the name secondary. */
export function TipRow({ colorVar, value, name }: { colorVar: string; value: string; name: string }) {
  return (
    <div className="tip__row">
      <span className="tip__key" style={{ background: `var(${colorVar})` }} />
      <span className="tip__value">{value}</span>
      <span className="tip__name">{name}</span>
    </div>
  );
}
