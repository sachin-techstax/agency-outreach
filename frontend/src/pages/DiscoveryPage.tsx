import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { EmptyState, Score } from "../components/Primitives";
import type { DiscoveryResult } from "../types";

const BATCH_OPTIONS = [5, 10, 20];
const POLL_INTERVAL_MS = 3000;

export function DiscoveryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 5000 });

  // R1-4: The Discovery page uses the most recent PERSISTED discovery run as
  // its source of truth — not dashboard.latest_run (which is overwritten when
  // a processing run executes).  This survives processing runs and container
  // restarts.
  const discoveryRuns = useQuery({
    queryKey: ["runs", "discovery"],
    queryFn: () => api.runsByType("discovery", 1),
  });

  const [batchSize, setBatchSize] = useState(10);
  const [error, setError] = useState("");
  const [activeRunId, setActiveRunId] = useState<number | null>(null);

  const demoMode = Boolean(meta.data?.demo_mode);

  // The latest persisted discovery run row (with result_json parsed).
  const latestDiscoveryRow = discoveryRuns.data?.items?.[0] ?? null;
  const discoveryResult: DiscoveryResult | null =
    latestDiscoveryRow?.result ?? null;

  // R1-5: Poll the active run until it reaches a terminal state.
  // When a run is started (discovery or processing), poll its specific row
  // every few seconds.  On terminal state, invalidate all relevant queries so
  // the page updates automatically without manual refresh.
  const activeRun = useQuery({
    queryKey: ["run", activeRunId],
    queryFn: () => api.run(activeRunId!),
    enabled: activeRunId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "completed" || status === "failed") {
        return false; // stop polling
      }
      return POLL_INTERVAL_MS;
    },
  });

  // When the active run reaches a terminal state, invalidate everything and
  // clear the active run id so the page shows the final result.
  useEffect(() => {
    if (activeRun.data && (activeRun.data.status === "completed" || activeRun.data.status === "failed")) {
      setActiveRunId(null);
      queryClient.invalidateQueries({ queryKey: ["runs", "discovery"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  }, [activeRun.data, queryClient]);

  // Also check the dashboard's latest_run_row for an active run (e.g. one
  // started from another tab/page).  If it's active, poll it too.
  const dashboardRunRow = dashboard.data?.latest_run_row;
  useEffect(() => {
    if (activeRunId === null && dashboardRunRow &&
        (dashboardRunRow.status === "running" || dashboardRunRow.status === "queued")) {
      setActiveRunId(dashboardRunRow.id);
    }
  }, [dashboardRunRow, activeRunId]);

  const runActive =
    activeRunId !== null ||
    (dashboardRunRow?.status === "running") ||
    (dashboardRunRow?.status === "queued");

  const discovery = useMutation({
    mutationFn: () => api.runDiscovery(20),
    onSuccess: (data) => {
      setError("");
      setActiveRunId(data.id);
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (err: Error) => setError(err.message)
  });

  const process = useMutation({
    mutationFn: () => api.runProcess(batchSize),
    onSuccess: (data) => {
      setError("");
      setActiveRunId(data.id);
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      navigate("/runs");
    },
    onError: (err: Error) => setError(err.message)
  });

  const ranked = discoveryResult?.ranked ?? [];
  const isDiscovery = discoveryResult?.type === "discovery";

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
        {runActive && (
          <div className="run-banner">
            <span className="spinner" />
            <div>
              <strong>Run in progress</strong>
              <span>Polling for completion…</span>
            </div>
          </div>
        )}

        {!isDiscovery && !runActive && (
          <EmptyState>No discovery result yet. Run discovery to see eligible ranked candidates.</EmptyState>
        )}

        {isDiscovery && discoveryResult && (
          <>
            <section className="metrics-strip">
              <Metric label="Raw results" value={discoveryResult.search_results_total ?? 0} detail="total search results" tone="blue" />
              <Metric label="Raw domains" value={discoveryResult.raw_candidate_domains ?? 0} detail="unique candidate domains" tone="blue" />
              <Metric label="Eligible" value={discoveryResult.candidate_domains ?? 0} detail="passed candidate filter" tone="violet" />
              <Metric label="Avg priority" value={discoveryResult.candidate_priority_avg ?? 0} detail="discovery priority score" tone="amber" />
            </section>

            <section className="panel">
              <div className="panel-header">
                <div>
                  <h2>Ranked candidates</h2>
                  <p>{discoveryResult.displayed_candidate_domains ?? ranked.length} of {discoveryResult.ranked_candidate_domains ?? ranked.length} eligible · {discoveryResult.query_count ?? "—"} queries</p>
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
