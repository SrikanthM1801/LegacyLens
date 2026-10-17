import type { ReactNode } from "react";
import { Empty } from "./Card";

export interface Column<T> {
  header: string;
  render: (row: T) => ReactNode;
  num?: boolean;
  muted?: boolean;
}

export function DataTable<T>({
  columns, rows, rowKey, empty = "Nothing to show.", caption,
}: { columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string; empty?: string; caption?: string }) {
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table>
        {caption && <caption className="sr-only" style={{ position: "absolute", left: -9999 }}>{caption}</caption>}
        <thead>
          <tr>{columns.map((c) => <th key={c.header} className={c.num ? "num" : undefined}>{c.header}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c) => (
                <td key={c.header} className={[c.num ? "num" : "", c.muted ? "muted" : ""].join(" ").trim() || undefined}>{c.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
