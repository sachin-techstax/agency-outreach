import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { EmptyState, LeadAvatar, Score, StatusPill, fmtAge } from "../components/Primitives";
import type { RunRow } from "../types";

const BATCH_OPTIONS = [5, 10, 20];

export function DashboardPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 8000 });
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const [batchSize, setBatchSize] = useState(10);
  const [runError, setRunError] = useState("");

  const discovery = useMutation({
    mutationFn: () => api.runDiscovery(20),
    onSuccess: () => {
      setRunError("");
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (err: Error) => setRunError(err.message)
  });

  const process = useMutation({
    mutationFn: () => api.runProcess(batchSize),
    onSuccess: () => {
      setRunError("");
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (err: Error) => setRunError(err.message)
  });

  const data = dashboard.data;
  const run = data?.latest_run;
  const runRow = data?.latest_run_row;
  const demoMode = Boolean(meta.data?.demo_mode);
  const runActive = runRow?.status === "running" || runRow?.status === "queued";
  const anyPending = discovery.isPending || process.isPending || runActive;

  return (
    <>
      <header className="page-header">
        <div>
          <div className="breadcrumb">Workspace / Overview</div>
          <h1>Agency pipeline</h1>
          <p>Fresh prospects, review queue and outreach state.</p>
        </div>
        <div className="header-actions">
          <div className="global-search"><Search size={14} /><span>Search leads or domains</span></div>
          <button className="button secondary">Filter</button>
          <button
            className="button secondary"
            disabled={demoMode || anyPending}
            onClick={() => discovery.mutate()}
            title="Run read-only Serper discovery, filtering and ranking"
          >
            {discovery.isPending ? "Discovering…" : "Run discovery"}
          </button>
          <div className="batch-selector">
            <select value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value))} disabled={demoMode || anyPending}>
              {BATCH_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <button
              className="button primary"
              disabled={demoMode || anyPending}
              onClick={() => process.mutate()}
              title="Discover, qualify, draft outreach for fresh prospects. Does NOT send email."
            >
              {process.isPending ? "Processing…" : "Process prospects"}
            </button>
          </div>
        </div>
      </header>

      <section className="page-body">
        {dashboard.isLoading && <div className="loading-line">Loading Nuntago…</div>}
        {runError && <div className="error-banner">{runError}</div>}
        {runRow && <ActiveRunBanner run={runRow} />}

        {data && (
          <>
            <section className="metrics-strip">
              <Metric label="Fresh / retryable" value={data.counts.retryable} detail="available to process" tone="blue" />
              <Metric label="Drafted" value={data.counts.drafted} detail="ready for review" tone="violet" />
              <Metric label="Approved" value={data.counts.approved} detail="awaiting Gmail" tone="green" />
              <Metric label="Sent" value={data.counts.sent} detail={`${data.due_followups} follow-ups due`} tone="amber" />
            </section>

            <div className="dashboard-grid">
              <section className="panel review-panel">
                <div className="panel-header">
                  <div>
                    <h2>Priority review</h2>
                    <p>Fresh and drafted leads sorted by commercial fit</p>
                  </div>
                  <button className="text-button" onClick={() => navigate("/leads")}>View all</button>
                </div>
                <div className="tabs"><span className="active">Needs review</span><span>Approved</span><span>Sent</span></div>
                <div className="table-head review-columns">
                  <span>Company</span><span>Fit</span><span>Status</span><span>Proof</span><span>Updated</span>
                </div>
                {data.review_queue.length === 0 ? (
                  <EmptyState>No leads are waiting for review.</EmptyState>
                ) : data.review_queue.map((lead) => (
                  <button className="record-row review-columns" key={lead.id} onClick={() => navigate(`/leads/${lead.id}`)}>
                    <span className="company-cell"><LeadAvatar lead={lead} /><span><strong>{lead.company}</strong><small>{lead.domain}</small></span></span>
                    <Score value={lead.score} />
                    <StatusPill status={lead.status} />
                    <span>{lead.proof_project || "—"}</span>
                    <span className="muted">{fmtAge(lead.updated_at)}</span>
                  </button>
                ))}
              </section>

              <aside className="panel run-panel">
                <div className="panel-header compact">
                  <div><h2>Latest run</h2><p>{run ? `${run.query_count ?? "—"} search queries` : "No UI-triggered run yet"}</p></div>
                </div>
                {run ? (
                  <div className="run-stages">
                    <RunStage label="Discovered" value={run.raw_candidate_domains} max={run.raw_candidate_domains} tone="blue" />
                    <RunStage label="Eligible" value={run.candidate_domains} max={run.raw_candidate_domains} tone="blue" />
                    <RunStage label="Fresh" value={run.fresh_retryable_pool} max={run.raw_candidate_domains} tone="violet" />
                    <RunStage label="Attempted" value={run.attempted} max={run.raw_candidate_domains} tone="amber" />
                    <RunStage label="Qualified" value={run.qualified} max={run.raw_candidate_domains} tone="green" />
                  </div>
                ) : <EmptyState>Run discovery from Nuntago to populate this panel.</EmptyState>}
                <div className="insight-box">
                  <span>Pipeline signal</span>
                  <p>{run?.qualified != null && run?.attempted ? `${run.qualified} of ${run.attempted} attempted prospects passed commercial-fit qualification.` : "Commercial-fit signals appear here after an outreach run."}</p>
                </div>
              </aside>
            </div>
          </>
        )}
      </section>
    </>
  );
}

function ActiveRunBanner({ run }: { run: RunRow }) {
  if (run.status !== "running" && run.status !== "queued") return null;
  const progress = run.progress as { stage?: string; attempted?: number; target?: number; current_domain?: string } | null;
  const label = run.type === "discovery" ? "Discovery" : "Process prospects";
  return (
    <div className="run-banner">
      <span className="spinner" />
      <div>
        <strong>{label} run in progress</strong>
        <span>Started {fmtAge(run.started_at)}{progress?.current_domain ? ` · crawling ${progress.current_domain}` : ""}{progress?.attempted != null && progress?.target ? ` · ${progress.attempted}/${progress.target}` : ""}</span>
      </div>
    </div>
  );
}

function Metric({ label, value, detail, tone }: { label: string; value: number; detail: string; tone: string }) {
  return <div className="metric"><span className="metric-label">{label}</span><strong>{value}</strong><span className="metric-detail"><i className={`dot ${tone}`} />{detail}</span></div>;
}

function RunStage({ label, value, max, tone }: { label: string; value?: number; max?: number; tone: string }) {
  const safe = value ?? 0;
  const percent = max ? Math.max(4, Math.min(100, (safe / max) * 100)) : 4;
  return <div className="run-stage"><div><span>{label}</span><strong>{value ?? "—"}</strong></div><div className="progress"><i className={tone} style={{ width: `${percent}%` }} /></div></div>;
}
