import { Fragment, type ReactNode } from "react";
import { fmtDay, pct, titleCase } from "../format";
import type { Citation, DepartureStatus, InterviewStatus, RiskLevel } from "../types";
import { Chip } from "./StatusChip";
import type { IconName } from "./Icon";
import type { Tone } from "./StatusChip";

type Meta = { label: string; icon: IconName; tone: Tone };

export const LEVEL_META: Record<RiskLevel, Meta> = {
  high: { label: "High", icon: "alert", tone: "critical" },
  medium: { label: "Medium", icon: "info", tone: "warning" },
  low: { label: "Low", icon: "check", tone: "good" },
};

export const INTERVIEW_META: Record<InterviewStatus, Meta> = {
  pending: { label: "Not started", icon: "clock", tone: "neutral" },
  in_progress: { label: "Answering", icon: "info", tone: "info" },
  completed: { label: "Approved", icon: "check", tone: "good" },
};

export const DEPARTURE_META: Record<DepartureStatus, Meta> = {
  alerted: { label: "Alerted", icon: "alert", tone: "warning" },
  interviewing: { label: "Interviewing", icon: "info", tone: "info" },
  reviewing: { label: "Reviewing answers", icon: "clock", tone: "neutral" },
  publishing: { label: "Publishing", icon: "clock", tone: "neutral" },
  completed: { label: "Handed over", icon: "check", tone: "good" },
  no_risk: { label: "Nothing at risk", icon: "check", tone: "good" },
  cancelled: { label: "Cancelled", icon: "x", tone: "neutral" },
};

export const LevelChip = ({ level }: { level: RiskLevel }) => <Chip meta={LEVEL_META[level] ?? LEVEL_META.low} />;
export const InterviewChip = ({ status }: { status: InterviewStatus }) => <Chip meta={INTERVIEW_META[status] ?? INTERVIEW_META.pending} />;
export const DepartureChip = ({ status }: { status: DepartureStatus }) => <Chip meta={DEPARTURE_META[status] ?? DEPARTURE_META.alerted} />;

/** A 0..1 value as a labelled bar. `invert` marks scores where higher is better (coverage). */
export function ScoreBar({ label, value, invert = false, hint }: { label: string; value: number; invert?: boolean; hint?: string }) {
  const v = Math.max(0, Math.min(1, value));
  const bad = invert ? 1 - v : v;
  const color = bad >= 0.6 ? "var(--critical)" : bad >= 0.3 ? "var(--warning)" : "var(--good)";
  return (
    <div className="score">
      <div className="score__head"><span>{label}</span><b>{invert ? pct(v) : v.toFixed(2)}</b></div>
      <div className="score__track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={1} aria-valuenow={v}>
        <span className="score__bar" style={{ width: `${v * 100}%`, background: color }} />
      </div>
      {hint && <div className="score__hint">{hint}</div>}
    </div>
  );
}

const isLink = (u: string | null): u is string => !!u && /^https?:\/\//.test(u);

/** Numbered sources; `idPrefix` lets [n] markers in a document jump to their source. */
export function CitationList({ items, idPrefix, empty = "No sources." }: { items: Citation[]; idPrefix?: string; empty?: string }) {
  if (items.length === 0) return <p className="empty">{empty}</p>;
  return (
    <ol className="cites">
      {items.map((c, i) => (
        <li key={`${c.artifact_id}-${i}`} id={idPrefix ? `${idPrefix}-${i + 1}` : undefined}>
          <div className="cites__head">
            {isLink(c.url_or_path)
              ? <a href={c.url_or_path} target="_blank" rel="noreferrer">{c.title || c.artifact_id}</a>
              : <b>{c.title || c.artifact_id}</b>}
            <span className="cites__meta">
              {[c.artifact_type && titleCase(c.artifact_type), c.timestamp && fmtDay(c.timestamp)].filter(Boolean).join(" · ")}
            </span>
          </div>
          {c.snippet && <blockquote>{c.snippet}</blockquote>}
        </li>
      ))}
    </ol>
  );
}

/** **bold**, *italic*, `code`, links and [n] citation markers. Builds elements, never HTML strings. */
function inline(text: string, citePrefix?: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^)\s]+)\)|\[\d+\]|\*[^*\s][^*]*\*)/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const tok = m[0];
    if (m.index! > last) out.push(text.slice(last, m.index));
    const k = `${m.index}`;
    if (tok.startsWith("**")) out.push(<b key={k}>{tok.slice(2, -2)}</b>);
    else if (tok.startsWith("`")) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else if (m[2]) out.push(<a key={k} href={m[2]} target="_blank" rel="noreferrer">{tok.slice(1, tok.indexOf("]("))}</a>);
    else if (/^\[\d+\]$/.test(tok)) {
      out.push(citePrefix
        ? <sup key={k}><a href={`#${citePrefix}-${tok.slice(1, -1)}`}>{tok}</a></sup>
        : <sup key={k}>{tok}</sup>);
    } else out.push(<i key={k}>{tok.slice(1, -1)}</i>);
    last = m.index! + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** The small Markdown subset generated documents use: headings, bullet / numbered lists, paragraphs. */
export function Markdown({ text, citePrefix }: { text: string; citePrefix?: string }) {
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push(<p key={blocks.length}>{inline(para.join(" "), citePrefix)}</p>);
    para = [];
    if (list) {
      const items = list.items.map((it, i) => <li key={i}>{inline(it, citePrefix)}</li>);
      blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    }
    list = null;
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const num = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (h) {
      flush();
      const level = Math.min(h[1].length + 1, 4);
      const Tag = `h${level}` as "h2" | "h3" | "h4";
      blocks.push(<Tag key={blocks.length}>{inline(h[2], citePrefix)}</Tag>);
    } else if (bullet || num) {
      const ordered = !!num && !bullet;
      if (para.length || (list && list.ordered !== ordered)) flush();
      list = list ?? { ordered, items: [] };
      list.items.push((bullet ?? num)![1]);
    } else if (!line.trim()) {
      flush();
    } else {
      if (list) flush();
      para.push(line.trim());
    }
  }
  flush();
  return <div className="md">{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>;
}
