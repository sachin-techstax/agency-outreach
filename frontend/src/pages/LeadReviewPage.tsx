import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Score, StatusPill, fmtAge } from "../components/Primitives";

export function LeadReviewPage() {
  const params = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const id = Number(params.id);
  const lead = useQuery({ queryKey: ["lead", id], queryFn: () => api.lead(id), enabled: Number.isFinite(id) });
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });

  const mutate = useMutation({
    mutationFn: (action: string) => api.action(id, action),
    onSuccess: (value) => {
      queryClient.setQueryData(["lead", id], value);
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  });

  if (lead.isLoading) return <div className="full-page-message">Loading lead…</div>;
  if (lead.error || !lead.data) return <div className="full-page-message error">{(lead.error as Error)?.message ?? "Lead not found"}</div>;

  const item = lead.data;
  const disabled = Boolean(meta.data?.demo_mode || mutate.isPending);
  const primary = primaryAction(item.status);

  return (
    <>
      <header className="page-header record-header">
        <div>
          <div className="breadcrumb">Workspace / Leads / {item.company}</div>
          <h1>{item.company}</h1>
          <p>{item.domain}</p>
        </div>
        <div className="header-actions"><Score value={item.score}/><StatusPill status={item.status}/><button className="button secondary" onClick={()=>navigate("/leads")}>Back to leads</button></div>
      </header>

      <section className="page-body review-workspace">
        <aside className="panel properties-panel">
          <h2>Properties</h2>
          <Property label="Status"><StatusPill status={item.status}/></Property>
          <Property label="Commercial fit"><strong>{item.score} / 100</strong></Property>
          <Property label="Contact"><span>{item.contact_email || "—"}</span></Property>
          <Property label="Contact quality"><span>{item.contact_quality || "—"}</span></Property>
          <Property label="Selected proof"><span>{item.proof_project || "—"}</span></Property>
          <Property label="Source query"><span>{item.source_query || "—"}</span></Property>
          <Property label="Last researched"><span>{fmtAge(item.updated_at)}</span></Property>
          <div className="section-rule"/>
          <h3>Activity</h3>
          <div className="timeline"><span>Research refreshed <small>{fmtAge(item.updated_at)}</small></span><span>Lead created <small>{fmtAge(item.created_at)}</small></span></div>
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
          <Label>Subject</Label><div className="draft-field single">{item.subject || "No draft subject yet"}</div>
          <Label>Message</Label><div className="draft-field message">{item.draft || "No outreach draft has been generated for this lead."}</div>
          <div className="guardrail"><span className="health-dot"/><div><strong>No automatic send</strong><p>Approval creates a Gmail draft only. Sending remains manual.</p></div></div>
          <div className="section-rule"/>
          <Label>Review action</Label>
          {mutate.error && <div className="error-banner compact">{(mutate.error as Error).message}</div>}
          <div className="review-actions">
            <button className="button danger-ghost" disabled={disabled || item.status==="sent"} onClick={()=>mutate.mutate("do-not-contact")}>Do not contact</button>
            <button className="button secondary" disabled={disabled || item.status==="sent"} onClick={()=>mutate.mutate("reject")}>Reject</button>
          </div>
          {primary && <button className="button primary full" disabled={disabled} onClick={()=>mutate.mutate(primary.action)}>{primary.label}</button>}
          {item.status==="do_not_contact" && <button className="button secondary full" disabled={disabled} onClick={()=>mutate.mutate("allow-contact")}>Allow contact again</button>}
          {meta.data?.demo_mode && <p className="demo-note">Demo mode is read-only. Actions are intentionally disabled.</p>}
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
function proofDescription(proof?:string){
  if(proof==="WingerX") return "AI automation and business orchestration platform with agents, CRM, integrations and scheduled workflows.";
  if(proof==="Forge Crew") return "Local-first multi-agent software engineering orchestrator with planner, implementation, review and human approval.";
  if(proof==="Aegis") return "Autonomous AI code-review and repository-hygiene agent with deterministic scanning and human approval.";
  return "Relevant proof project selected by PactSignal.";
}
