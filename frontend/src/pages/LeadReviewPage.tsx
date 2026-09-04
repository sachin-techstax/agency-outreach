import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ExternalLink, RefreshCw, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Score, StatusPill, fmtAge } from "../components/Primitives";
import type { Lead } from "../types";

export function LeadReviewPage() {
  const params = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const id = Number(params.id);
  const lead = useQuery({ queryKey: ["lead", id], queryFn: () => api.lead(id), enabled: Number.isFinite(id) });
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  // R1-13: Confirmation gate for approved-stale regeneration.
  const [confirmingRegenerate, setConfirmingRegenerate] = useState(false);

  const mutate = useMutation({
    mutationFn: (action: string) => api.action(id, action),
    onSuccess: (value) => {
      queryClient.setQueryData(["lead", id], value);
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  });

  const refresh = useMutation({
    mutationFn: () => api.refreshResearch(id),
    onSuccess: (value) => {
      queryClient.setQueryData(["lead", id], value.lead);
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  });

  const regenerate = useMutation({
    mutationFn: () => api.regenerateDraft(id),
    onSuccess: (value) => {
      queryClient.setQueryData(["lead", id], value.lead);
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setConfirmingRegenerate(false);
    }
  });

  if (lead.isLoading) return <div className="full-page-message">Loading lead…</div>;
  if (lead.error || !lead.data) return <div className="full-page-message error">{(lead.error as Error)?.message ?? "Lead not found"}</div>;

  const item = lead.data;
  const demoMode = Boolean(meta.data?.demo_mode);
  const disabled = demoMode || mutate.isPending;
  const refreshDisabled = demoMode || refresh.isPending;
  const regenerateDisabled = demoMode || regenerate.isPending;
  const primary = primaryAction(item.status);
  const draftStale = Boolean(item.draft_stale);

  // R1-13: Regeneration is only offered for drafted/rejected/approved leads
  // with a stale draft.  gmail_drafted/sent/do_not_contact never show it.
  const regenerationAllowed =
    draftStale &&
    (item.status === "drafted" || item.status === "rejected" || item.status === "approved");

  // R1-13: Approved stale drafts require explicit confirmation because
  // regeneration revokes approval and returns the lead to Drafted.
  const needsConfirmation = item.status === "approved" && regenerationAllowed;

  const handleRegenerate = () => {
    if (needsConfirmation && !confirmingRegenerate) {
      setConfirmingRegenerate(true);
      return;
    }
    regenerate.mutate();
  };

  return (
    <>
      <header className="page-header record-header">
        <div>
          <div className="breadcrumb">Workspace / Leads / {item.company}</div>
          <h1>{item.company}</h1>
          <p>{item.domain}</p>
        </div>
        <div className="header-actions">
          <Score value={item.score}/>
          <StatusPill status={item.status}/>
          <button className="button secondary" onClick={()=>navigate("/leads")}>Back to leads</button>
        </div>
      </header>

      <section className="page-body review-workspace">
        <aside className="panel properties-panel">
          <h2>Properties</h2>
          <Property label="Status"><StatusPill status={item.status}/></Property>
          <Property label="Commercial fit"><strong>{item.score} / 100</strong></Property>
          <Property label="Contact"><span>{item.contact_email || "—"}</span></Property>
          <Property label="Contact source"><span className="truncate">{item.contact_source || "—"}</span></Property>
          <Property label="Contact quality"><span>{item.contact_quality || "—"}</span></Property>
          <Property label="Contact role"><span>{item.contact_role || "—"}</span></Property>
          <Property label="Selected proof"><span>{item.proof_project || "—"}</span></Property>
          <Property label="Source query"><span>{item.source_query || "—"}</span></Property>
          <Property label="Last researched"><span>{fmtAge(item.updated_at)}</span></Property>
          <div className="section-rule"/>
          <h3>Activity</h3>
          <div className="timeline"><span>Research refreshed <small>{fmtAge(item.updated_at)}</small></span><span>Lead created <small>{fmtAge(item.created_at)}</small></span></div>
          <div className="section-rule"/>
          <Label>Research action</Label>
          <button
            className="button secondary full"
            disabled={refreshDisabled}
            onClick={() => refresh.mutate()}
            title="Re-crawl and re-research this lead without changing workflow state or outreach drafts"
          >
            <RefreshCw size={13} />
            {refresh.isPending ? "Refreshing…" : "Refresh contact & research"}
          </button>
          {/* R1-15: Coherent refresh feedback using the backend message. */}
          {refresh.data?.refresh?.message && <p className="preview-foot">{refresh.data.refresh.message}</p>}
          {refresh.error && <div className="error-banner compact">{(refresh.error as Error).message}</div>}
        </aside>

        <section className="panel intelligence-panel">
          <div className="panel-header compact"><div><h2>Agency intelligence</h2><p>Research synthesized from public website signals</p></div></div>
          <Label>Why it fits</Label><p className="intel-copy">{item.fit_reason || item.summary || "No fit narrative yet."}</p>
          <Label>Services detected</Label><div className="service-list">{(item.services_list?.length ? item.services_list : ["No service tags yet"]).map((service)=><span key={service}>{service}</span>)}</div>
          <Label>Selected proof</Label><div className="proof-card large"><strong>{item.proof_project || "—"}</strong><p>{proofDescription(item.proof_project)}</p></div>
          <Label>Qualification evidence</Label><div className="evidence-list">{(item.score_reason_list?.length ? item.score_reason_list : ["No deterministic reasons stored"]).map((reason)=><div key={reason}><span>{reason}</span><strong>Signal</strong></div>)}</div>
          <div className="section-rule"/>
          <Label>AI note</Label><p className="ai-note">{item.outreach_angle || "PactSignal will show the strongest outreach angle after qualification."}</p>
          {item.website && <a className="button secondary inline-link" href={item.website} target="_blank" rel="noreferrer">Open source site <ExternalLink size={13}/></a>}
        </section>

        <aside className="panel composer-panel">
          <div className="panel-header compact"><div><h2>Outreach draft</h2><p>Human approval required before Gmail</p></div></div>
          {draftStale && (
            <div className="stale-banner">
              <AlertTriangle size={14} />
              <div>
                <strong>Draft may be stale</strong>
                <p>Research has changed since this draft was generated. Regenerate to align the draft with current research, then re-review before approving.</p>
              </div>
            </div>
          )}
          <Label>Subject</Label><div className="draft-field single">{item.subject || "No draft subject yet"}</div>
          <Label>Message</Label><div className="draft-field message">{item.draft || "No outreach draft has been generated for this lead."}</div>
          <div className="guardrail"><span className="health-dot"/><div><strong>No automatic send</strong><p>Approval creates a Gmail draft only. Sending remains manual.</p></div></div>
          <div className="section-rule"/>
          {regenerationAllowed && (
            <>
              <Label>Draft freshness</Label>
              {needsConfirmation && confirmingRegenerate && (
                <div className="stale-banner warning">
                  <AlertTriangle size={14} />
                  <div>
                    <strong>Regenerating will return this lead to Drafted and require approval again.</strong>
                    <p>The current approved draft will be replaced with fresh content from current research.</p>
                  </div>
                </div>
              )}
              <button
                className="button secondary full"
                disabled={regenerateDisabled}
                onClick={handleRegenerate}
                title="Regenerate the outreach draft from current research. Clears the stale flag."
              >
                <RotateCcw size={13} />
                {regenerate.isPending ? "Regenerating…" : needsConfirmation && !confirmingRegenerate ? "Regenerate draft (requires confirmation)" : needsConfirmation ? "Confirm: Regenerate draft" : "Regenerate draft"}
              </button>
              {regenerate.data && <p className="preview-foot">Draft regenerated from current research. Status is now Drafted — review before approving.</p>}
              {regenerate.error && <div className="error-banner compact">{(regenerate.error as Error).message}</div>}
              <div className="section-rule"/>
            </>
          )}
          {/* R1-13: gmail_drafted with stale flag — informational only, no mutation. */}
          {draftStale && !regenerationAllowed && item.status !== "do_not_contact" && item.status !== "sent" && (
            <p className="preview-foot stale-note">This lead has a stale draft but regeneration is not available in the current workflow state.</p>
          )}
          <Label>Review action</Label>
          {mutate.error && <div className="error-banner compact">{(mutate.error as Error).message}</div>}
          <div className="review-actions">
            <button className="button danger-ghost" disabled={disabled || item.status==="sent"} onClick={()=>mutate.mutate("do-not-contact")}>Do not contact</button>
            <button className="button secondary" disabled={disabled || item.status==="sent"} onClick={()=>mutate.mutate("reject")}>Reject</button>
          </div>
          {primary && <button className="button primary full" disabled={disabled || !canPrimary(item, primary.action)} onClick={()=>mutate.mutate(primary.action)}>{primary.label}</button>}
          {item.status==="do_not_contact" && <button className="button secondary full" disabled={disabled} onClick={()=>mutate.mutate("allow-contact")}>Allow contact again</button>}
          {demoMode && <p className="demo-note">Demo mode is read-only. Actions are intentionally disabled.</p>}
        </aside>
      </section>
    </>
  );
}

function Label({children}:{children:ReactNode}){return <div className="field-label">{children}</div>;}
function Property({label,children}:{label:string;children:ReactNode}){return <div className="property"><span>{label}</span><div>{children}</div></div>;}
function primaryAction(status:string){
  if(status==="drafted"||status==="rejected") return {label:"Approve for Gmail",action:"approve"};
  if(status==="approved") return {label:"Create Gmail draft",action:"gmail-draft"};
  if(status==="gmail_drafted") return {label:"Mark as sent",action:"mark-sent"};
  return null;
}
// R1-14: canPrimary now considers draft_stale.  Approve and Gmail-draft
// are disabled when the draft is stale.  Backend checks remain mandatory.
function canPrimary(item: Lead, action: string): boolean {
  if (item.draft_stale) {
    if (action === "approve" || action === "gmail-draft") {
      return false;
    }
  }
  // Disable impossible primary actions so the operator cannot trigger them.
  if (action === "approve") return Boolean(item.draft);
  if (action === "gmail-draft") return Boolean(item.contact_email && item.subject && item.draft);
  if (action === "mark-sent") return true;
  return true;
}
function proofDescription(proof?:string){
  if(proof==="WingerX") return "AI automation and business orchestration platform with agents, CRM, integrations and scheduled workflows.";
  if(proof==="Forge Crew") return "Local-first multi-agent software engineering orchestrator with planner, implementation, review and human approval.";
  if(proof==="Aegis") return "Autonomous AI code-review and repository-hygiene agent with deterministic scanning and human approval.";
  return "Relevant proof project selected by PactSignal.";
}
