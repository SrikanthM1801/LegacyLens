import type { HierarchyNode, Scope } from "../types";
import { Icon } from "./Icon";

/** account id the scope sits in ("" for the whole org). */
export function accountOf(root: HierarchyNode, scope: Scope): string {
  if (scope.level === "account") return scope.id;
  if (scope.level === "project") return root.children.find((a) => a.children.some((p) => p.id === scope.id))?.id ?? "";
  return "";
}

/** Org -> account -> project as two selects. Picking a project in "All accounts" also sets its account. */
export function ScopePicker({ root, scope, onChange }: { root: HierarchyNode; scope: Scope; onChange: (s: Scope) => void }) {
  const accountId = accountOf(root, scope);
  const account = root.children.find((a) => a.id === accountId);
  const projectId = scope.level === "project" ? scope.id : "";

  const pickAccount = (id: string) => onChange(id ? { level: "account", id } : { level: "org", id: root.id });
  const pickProject = (id: string) => {
    if (id) onChange({ level: "project", id });
    else onChange(accountId ? { level: "account", id: accountId } : { level: "org", id: root.id });
  };
  const projectGroups = account ? [account] : root.children;

  return (
    <>
      <div className="field">
        <label htmlFor="f-account">Account</label>
        <select id="f-account" className="select" value={accountId} onChange={(e) => pickAccount(e.target.value)}>
          <option value="">All accounts</option>
          {root.children.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor="f-project">Project</label>
        <select id="f-project" className="select" value={projectId} onChange={(e) => pickProject(e.target.value)}>
          <option value="">All projects</option>
          {projectGroups.map((a) => (
            <optgroup key={a.id} label={a.name}>
              {a.children.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </optgroup>
          ))}
        </select>
      </div>
    </>
  );
}

export const RANGES = [7, 14, 30] as const;

/** Date presets as a single choice; selection is marked with a check, not only a weight change. */
export function DateRange({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  return (
    <div className="pills" role="radiogroup" aria-label="Period">
      {RANGES.map((d) => (
        <button key={d} className="pill" role="radio" aria-checked={days === d} onClick={() => onChange(d)}>
          {days === d && <Icon name="check" size={13} />}Last {d} days
        </button>
      ))}
    </div>
  );
}
