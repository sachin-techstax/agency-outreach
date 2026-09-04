import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { EmptyState, fmtAge } from "../components/Primitives";
import type { RunRow } from "../types";

const STATUS_TONE: Record<string, string> = {
  completed: "green",
  running: "violet",
  queued: "blue",
  failed: "rose"
};

export function RunsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.runs(50), refetchInterval: 5000 });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const selected = useQuery({
    queryKey: ["run", selectedId],
    queryFn: () => api.run(selectedId as number),
    enabled: selectedId != null,
    refetchInterval: (query) => {
      const data = query.state.data as RunRow | undefined;
      return data?.status === "running" || data?.status === "queued" ? 3000 : false;
    }
  });

  return (
    <>
      <header className="page-header">
        <div>
          <div className="breadcrumb">Workspace / Outreach runs</div>
          <h1>Outreach runs</h1>
          <p>Persistent run history for discovery and processing runs.</p>
        </div>
      </header>

      <section className="page-body">
        {runs.isLoading && <div className="loading-line">Loading runs…</div>}
        {runs.error && <div className="error-banner">{(runs.error as Error).message}</div>}

        <div className="leads-grid">
          <section className="panel lead-table-panel">
            <div className="table-head run-columns">
              <span>Type</span><span>Status</span><span>Started</span><span>Limit</span><span>Qualified</span><span>Drafted</span><span>Duration</span>
            </div>
            {runs.data?.items.length ? runs.data.items.map((run) => (
              <button
                key={run.id}
                className={`record-row run-columns ${selectedId === run.id ? "selected" : ""}`}
                onClick={() => setSelectedId(run.id)}
              >
                <span><strong>{run.type === "processing" ? "Process prospects" : run.type}</strong></span>
                <span className={`pill pill-${STATUS_TONE[run.status] ?? "amber"}`}>{run.status}</span>
                <span className="muted">{fmtAge(run.started_at)}</span>
                <span>{run.requested_limit ?? "—"}</span>
                <span>{run.qualified ?? "—"}</span>
                <span>{run.drafted ?? "—"}</span>
                <span className="muted">{run.duration_s != null ? `${run.duration_s}s` : "—"}</span>
              </button>
            )) : <EmptyState>No runs yet. Use Run discovery or Process prospects from the Overview.</EmptyState>}
          </section>

          <aside className="panel lead-preview">
            {selected.data ? (
              <>
                <div className="preview-title">
                  <div>
                    <h2>{selected.data.type === "processing" ? "Process prospects" : selected.data.type}</h2>
                    <p>Run #{selected.data.id} · {selected.data.status}</p>
                  </div>
                  <span className={`pill pill-${STATUS_TONE[selected.data.status] ?? "amber"}`}>{selected.data.status}</span>
                </div>
                <div className="section-rule" />
                <Detail label="Started" value={new Date(selected.data.started_at).toLocaleString()} />
                <Detail label="Completed" value={selected.data.completed_at ? new Date(selected.data.completed_at).toLocaleString() : "—"} />
                <Detail label="Requested limit" value={String(selected.data.requested_limit ?? "—")} />
                <Detail label="Duration" value={selected.data.duration_s != null ? `${selected.data.duration_s}s` : "—"} />
                <div className="section-rule" />
                <Detail label="Queries" value={String(selected.data.query_count ?? "—")} />
                <Detail label="Raw domains" value={String(selected.data.raw_candidate_domains ?? "—")} />
                <Detail label="Eligible" value={String(selected.data.candidate_domains ?? "—")} />
                <Detail label="Fresh pool" value={String(selected.data.fresh_retryable_pool ?? "—")} />
                <Detail label="Attempted" value={String(selected.data.attempted ?? "—")} />
                <Detail label="Processed" value={String(selected.data.processed ?? "—")} />
                <Detail label="Qualified" value={String(selected.data.qualified ?? "—")} />
                <Detail label="Drafted" value={String(selected.data.drafted ?? "—")} />
                <Detail label="Below score" value={String(selected.data.below_score ?? "—")} />
                <Detail label="No contact" value={String(selected.data.no_contact ?? "—")} />
                <Detail label="Skipped" value={String(selected.data.skipped ?? "—")} />
                <Detail label="Failed" value={String(selected.data.failed_count ?? "—")} />
                {selected.data.error_summary && (
                  <>
                    <div className="section-rule" />
                    <Label>Error</Label>
                    <div className="error-banner compact">{selected.data.error_summary}</div>
                  </>
                )}
                {selected.data.progress && (
                  <>
                    <div className="section-rule" />
                    <Label>Live progress</Label>
                    <pre className="progress-json">{JSON.stringify(selected.data.progress, null, 2)}</pre>
                  </>
                )}
              </>
            ) : <EmptyState>Select a run to inspect its details.</EmptyState>}
          </aside>
        </div>
      </section>
    </>
  );
}

function Label({ children }: { children: React.ReactNode }) { return <div className="field-label">{children}</div>; }
function Detail({ label, value }: { label: string; value: string }) {
  return <div className="property"><span>{label}</span><div>{value}</div></div>;
}
