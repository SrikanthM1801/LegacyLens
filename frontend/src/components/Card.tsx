import { useState, type ReactNode } from "react";

/**
 * The chart container: title, caption, and (when a `table` twin is given) a toggle to the data table.
 * Every chart has that twin, so no value is reachable only by hovering or by colour.
 */
export function Card({
  title, subtitle, table, children, className,
}: { title: string; subtitle?: ReactNode; table?: ReactNode; children: ReactNode; className?: string }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <figure className={`card ${className ?? ""}`} style={{ margin: 0 }}>
      <div className="card__head">
        <div>
          <h2 className="card__title">{title}</h2>
          {subtitle && <p className="card__sub">{subtitle}</p>}
        </div>
        {table && (
          <button className="btn btn--ghost btn--small" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
            {asTable ? "Show chart" : "Show table"}
          </button>
        )}
      </div>
      {asTable && table ? table : children}
    </figure>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
