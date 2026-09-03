import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { EmptyState, LeadAvatar, Score, StatusPill, fmtAge } from "../components/Primitives";

export function DashboardPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const discovery = useMutation({
    mutationFn: () => api.runDiscovery(20),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["dashboard"] })
  });

  const data = dashboard.data;
  const run = data?.latest_run;

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
          <button className="button primary" disabled={meta.data?.demo_mode || discovery.isPending} onClick={() => discovery.mutate()}>
            {discovery.isPending ? "Discovering…" : "Run discovery"}
          </button>
        </div>
      </header>

      <section className="page-body">
        {dashboard.isLoading && <div className="loading-line">Loading PactSignal…</div>}
        {dashboard.error && <div className="error-banner">{(dashboard.error as Error).message}</div>}

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
                ) : <EmptyState>Run discovery from PactSignal to populate this panel.</EmptyState>}
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

function Metric({ label, value, detail, tone }: { label: string; value: number; detail: string; tone: string }) {
  return <div className="metric"><span className="metric-label">{label}</span><strong>{value}</strong><span className="metric-detail"><i className={`dot ${tone}`} />{detail}</span></div>;
}

function RunStage({ label, value, max, tone }: { label: string; value?: number; max?: number; tone: string }) {
  const safe = value ?? 0;
  const percent = max ? Math.max(4, Math.min(100, (safe / max) * 100)) : 4;
  return <div className="run-stage"><div><span>{label}</span><strong>{value ?? "—"}</strong></div><div className="progress"><i className={tone} style={{ width: `${percent}%` }} /></div></div>;
}
