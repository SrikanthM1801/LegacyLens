import type { ReactNode } from "react";

/** label · value · optional sub-line. `hero` is the one big figure a view leads with (exactly one per view). */
export function StatTile({
  label, value, sub, hero = false, small,
}: { label: string; value: ReactNode; sub?: ReactNode; hero?: boolean; small?: ReactNode }) {
  if (hero) {
    return (
      <div>
        <div className="tile__label">{label}</div>
        <div className="hero__value">{value}{small && <small>{small}</small>}</div>
        {sub && <p className="hero__caption">{sub}</p>}
      </div>
    );
  }
  return (
    <div className="card">
      <div className="tile__label">{label}</div>
      <div className="tile__value">{value}</div>
      {sub && <div className="tile__sub">{sub}</div>}
    </div>
  );
}
