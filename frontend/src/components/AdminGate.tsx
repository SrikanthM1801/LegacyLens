import { useState } from "react";
import { Icon } from "./Icon";

/** Shown when the API wants an admin key. The key is kept in sessionStorage only and sent as X-Admin-Key. */
export function AdminGate({ onSubmit, message }: { onSubmit: (key: string) => void; message?: string }) {
  const [value, setValue] = useState("");
  return (
    <form className="card gate" onSubmit={(e) => { e.preventDefault(); if (value.trim()) onSubmit(value.trim()); }}>
      <h2 className="card__title"><Icon name="shield" /> Admin access</h2>
      <p className="card__sub">
        This dashboard shows what LegacyLens reads and stores, so it is protected. Enter the admin key set as
        <b> ADMIN_API_KEY</b> on the server. It is kept for this browser tab only.
      </p>
      <input type="password" autoComplete="off" aria-label="Admin key" placeholder="Admin key" value={value} onChange={(e) => setValue(e.target.value)} />
      {message && <p className="error" role="alert">{message}</p>}
      <div className="row"><button className="btn" type="submit">Open dashboard</button></div>
    </form>
  );
}
