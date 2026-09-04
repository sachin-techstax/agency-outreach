import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { EmptyState, StatusPill, fmtAge } from "../components/Primitives";

export function FollowupsPage() {
  const navigate = useNavigate();
  const followups = useQuery({ queryKey: ["followups"], queryFn: api.followups });

  return (
    <>
      <header className="page-header">
        <div>
          <div className="breadcrumb">Workspace / Follow-ups</div>
          <h1>Follow-ups</h1>
          <p>Sent leads whose follow-up date is due. Drafting a follow-up remains a manual action.</p>
        </div>
      </header>

      <section className="page-body">
        {followups.isLoading && <div className="loading-line">Loading follow-ups…</div>}
        {followups.error && <div className="error-banner">{(followups.error as Error).message}</div>}

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Due follow-ups</h2>
              <p>{followups.data?.total ?? "—"} sent leads with a due follow-up date</p>
            </div>
          </div>
          <div className="table-head followup-columns">
            <span>Company</span><span>Contact</span><span>Status</span><span>Sent</span><span>Due</span>
          </div>
          {followups.data?.items.length ? followups.data.items.map((item) => (
            <button
              key={item.id}
              className="record-row followup-columns"
              onClick={() => navigate(`/leads/${item.id}`)}
            >
              <span className="company-cell"><span><strong>{item.company}</strong><small>{item.domain}</small></span></span>
              <span className="truncate">{item.contact_email || "—"}</span>
              <StatusPill status={item.status} />
              <span className="muted">{fmtAge(item.last_contact_at)}</span>
              <span className="muted">{fmtAge(item.followup_due_at)}</span>
            </button>
          )) : <EmptyState>No follow-ups are due. Sent leads appear here when their follow-up date passes.</EmptyState>}
        </section>

        <div className="insight-box">
          <span>Manual boundary</span>
          <p>Follow-up drafting is a deliberate operator action. PactSignal does not automatically send follow-up emails. Use the CLI <code>pactsignal followup-draft &lt;id&gt;</code> to generate a draft, then send it manually.</p>
        </div>
      </section>
    </>
  );
}
