/** 1,284 -> "1,284"; 12,900 -> "12.9K"; 4,200,000 -> "4.2M". Axis ticks and stat tiles use the same rule. */
export function compact(n: number): string {
  const a = Math.abs(n);
  if (a < 1000) return String(Math.round(n));
  const [v, unit] = a >= 1_000_000 ? [n / 1_000_000, "M"] : [n / 1000, "K"];
  const s = Math.abs(v) < 10 ? v.toFixed(1) : String(Math.round(v));
  return s.replace(/\.0$/, "") + unit;
}

export const pct = (x: number | null | undefined): string => (x == null ? "n/a" : `${Math.round(x * 100)}%`);

export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

export function fmtDay(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** Round-number ticks from 0 covering `max`, e.g. 0,20,40,60. Always at least [0, 1]. */
export function niceTicks(max: number, approx = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / approx;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 5, 10].find((m) => m * pow >= raw) ?? 10) * pow;
  const top = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = 0; v <= top + step / 2; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

export const titleCase = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");
