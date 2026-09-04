import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { EmptyState, Score } from "../components/Primitives";
import type { DiscoveryResult } from "../types";

const BATCH_OPTIONS = [5, 10, 20];

export function DiscoveryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  const [batchSize, setBatchSize] = useState(10);
  const [error, setError] = useState("");

  // The latest discovery result is surfaced via the dashboard's latest_run,
  // which is the in-memory result of the most recent run.  For a richer view
  // we treat the latest_run as the discovery summary when its type matches.
  const latestRun = dashboard.data?.latest_run as DiscoveryResult | null;
  const runRow = dashboard.data?.latest_run_row;
  const demoMode = Boolean(meta.data?.demo_mode);
  const runActive = runRow?.status === "running" || runRow?.status === "queued";

  const discovery = useMutation({
    mutationFn: () => api.runDiscovery(20),
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (err: Error) => setError(err.message)
  });

  const process = useMutation({
    mutationFn: () => api.runProcess(batchSize),
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate("/runs");
    },
    onError: (err: Error) => setError(err.message)
  });

  const ranked = latestRun?.ranked ?? [];
  const isDiscovery = latestRun?.type === "discovery";

  return (
    <>
      <header className="page-header">
        <div>
          <div className="breadcrumb">Workspace / Discovery</div>
          <h1>Discovery</h1>
          <p>Read-only Serper discovery, filtering and priority ranking.</p>
        </div>
        <div className="header-actions">
          <div className="batch-selector">
            <select value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value))} disabled={demoMode || runActive}>
              {BATCH_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <button
              className="button primary"
              disabled={demoMode || runActive}
              onClick={() => process.mutate()}
              title="Process the top prospects from this pool. Does NOT send email."
            >
              {process.isPending ? "Processing…" : "Process prospects"}
            </button>
          </div>
          <button
            className="button secondary"
            disabled={demoMode || runActive}
            onClick={() => discovery.mutate()}
          >
            {discovery.isPending ? "Discovering…" : "Run discovery"}
          </button>
        </div>
      </header>

      <section className="page-body">
        {error && <div className="error-banner">{error}</div>}
        {runActive && runRow && (
          <div className="run-banner">
            <span className="spinner" />
            <div>
              <strong>{runRow.type === "discovery" ? "Discovery" : "Process prospects"} run in progress</strong>
              <span>Started {new Date(runRow.started_at).toLocaleString()}</span>
            </div>
          </div>
        )}

        {!isDiscovery && !runActive && (
          <EmptyState>No discovery result yet. Run discovery to see eligible ranked candidates.</EmptyState>
        )}

        {isDiscovery && latestRun && (
          <>
            <section className="metrics-strip">
              <Metric label="Raw results" value={latestRun.search_results_total ?? 0} detail="total search results" tone="blue" />
              <Metric label="Raw domains" value={latestRun.raw_candidate_domains ?? 0} detail="unique candidate domains" tone="blue" />
              <Metric label="Eligible" value={latestRun.candidate_domains ?? 0} detail="passed candidate filter" tone="violet" />
              <Metric label="Avg priority" value={latestRun.candidate_priority_avg ?? 0} detail="discovery priority score" tone="amber" />
            </section>

            <section className="panel">
              <div className="panel-header">
                <div>
                  <h2>Ranked candidates</h2>
                  <p>{latestRun.displayed_candidate_domains ?? ranked.length} of {latestRun.ranked_candidate_domains ?? ranked.length} eligible · {latestRun.query_count ?? "—"} queries</p>
                </div>
              </div>
              <div className="table-head discovery-columns">
                <span>Rank</span><span>Domain</span><span>Priority</span><span>Category</span><span>Source query</span><span>Reasons</span>
              </div>
              {ranked.length === 0 ? (
                <EmptyState>No eligible candidates in this discovery pool.</EmptyState>
              ) : ranked.map((row) => (
                <div className="record-row discovery-columns" key={row.domain}>
                  <span>#{row.rank}</span>
                  <span className="company-cell"><span><strong>{row.domain}</strong><small>{row.title}</small></span></span>
                  <Score value={row.priority} />
                  <span>{row.category}</span>
                  <span className="truncate">{row.source_query}</span>
                  <span className="truncate muted">{row.reasons || "—"}</span>
                </div>
              ))}
            </section>
          </>
        )}
      </section>
    </>
  );
}

function Metric({ label, value, detail, tone }: { label: string; value: number; detail: string; tone: string }) {
  return <div className="metric"><span className="metric-label">{label}</span><strong>{value}</strong><span className="metric-detail"><i className={`dot ${tone}`} />{detail}</span></div>;
}
