import type { ReactNode } from "react";
import type { Lead } from "../types";

const STATUS_LABELS: Record<string, string> = {
  "rejected-fit": "Rejected fit",
  gmail_drafted: "Gmail drafted",
  do_not_contact: "Do not contact"
};

export function StatusPill({ status }: { status: string }) {
  const tone =
    status === "approved" || status === "sent"
      ? "green"
      : status === "drafted"
        ? "violet"
        : status === "qualified" || status === "discovered"
          ? "blue"
          : status === "do_not_contact"
            ? "rose"
            : "amber";

  return <span className={`pill pill-${tone}`}>{STATUS_LABELS[status] ?? status}</span>;
}

export function Score({ value }: { value: number }) {
  const tone = value >= 80 ? "score-green" : value >= 75 ? "score-blue" : value >= 70 ? "score-amber" : "score-muted";
  return <span className={`score ${tone}`}>{value}</span>;
}

export function LeadAvatar({ lead }: { lead: Lead }) {
  const initials = lead.company
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  const hue = (lead.id * 47) % 360;
  return (
    <span className="lead-avatar" style={{ background: `hsl(${hue} 42% 48%)` }}>
      {initials}
    </span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

export function fmtAge(value?: string | null) {
  if (!value) return "—";
  const then = new Date(value).getTime();
  const delta = Math.max(0, Date.now() - then);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
