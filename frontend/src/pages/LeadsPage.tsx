import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { EmptyState, LeadAvatar, Score, StatusPill, fmtAge } from "../components/Primitives";

export function LeadsPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const leads = useQuery({
    queryKey: ["leads", q, status, minScore],
    queryFn: () => api.leads({ q, status: status || undefined, minScore, limit: 100 })
  });

  useEffect(() => {
    if (!selectedId && leads.data?.items[0]) setSelectedId(leads.data.items[0].id);
  }, [leads.data, selectedId]);

  const selected = useQuery({
    queryKey: ["lead", selectedId],
    queryFn: () => api.lead(selectedId as number),
    enabled: selectedId != null
  });

  return (
    <>
      <header className="page-header">
        <div>
          <div className="breadcrumb">Workspace / Leads</div>
          <h1>Leads</h1>
          <p>Research, qualify and move prospects through outreach.</p>
        </div>
      </header>

      <section className="page-body">
        <section className="panel leads-toolbar">
          <div><strong>{leads.data?.total ?? "—"} leads</strong><span>sorted by commercial fit</span></div>
          <label className="search-input"><Search size={14}/><input value={q} onChange={(e)=>setQ(e.target.value)} placeholder="Search company, domain or proof" /></label>
          <select value={status} onChange={(e)=>setStatus(e.target.value)}>
            <option value="">All statuses</option><option value="drafted">Drafted</option><option value="approved">Approved</option><option value="qualified">Qualified</option><option value="sent">Sent</option><option value="rejected-fit">Rejected fit</option><option value="do_not_contact">Do not contact</option>
          </select>
          <select value={minScore} onChange={(e)=>setMinScore(Number(e.target.value))}>
            <option value={0}>Any fit</option><option value={70}>Fit ≥ 70</option><option value={80}>Fit ≥ 80</option>
          </select>
        </section>

        <div className="leads-grid">
          <section className="panel lead-table-panel">
            <div className="table-head lead-columns"><span>Company</span><span>Fit</span><span>Status</span><span>Contact</span><span>Proof</span><span>Updated</span></div>
            {leads.isLoading ? <div className="loading-line">Loading leads…</div> : leads.data?.items.length ? leads.data.items.map((lead)=>(
              <button key={lead.id} className={`record-row lead-columns ${selectedId===lead.id?"selected":""}`} onClick={()=>setSelectedId(lead.id)}>
                <span className="company-cell"><LeadAvatar lead={lead}/><span><strong>{lead.company}</strong><small>{lead.domain}</small></span></span>
                <Score value={lead.score}/><StatusPill status={lead.status}/>
                <span className="truncate">{lead.contact_email || "—"}</span><span>{lead.proof_project || "—"}</span><span className="muted">{fmtAge(lead.updated_at)}</span>
              </button>
            )) : <EmptyState>No leads match these filters.</EmptyState>}
          </section>

          <aside className="panel lead-preview">
            {selected.data ? <>
              <div className="preview-title"><div><h2>{selected.data.company}</h2><p>{selected.data.domain}</p></div><Score value={selected.data.score}/></div>
              <div className="section-rule"/>
              <Label>Why it fits</Label><p className="preview-copy">{selected.data.fit_reason || selected.data.summary || "No fit narrative yet."}</p>
              <Label>Selected proof</Label><div className="proof-card"><strong>{selected.data.proof_project || "—"}</strong><p>{selected.data.outreach_angle || "Proof selection will appear after qualification."}</p></div>
              <Label>Contact</Label><div className="contact-line"><strong>{selected.data.contact_email || "No public email found"}</strong><span>{selected.data.contact_quality ? `${selected.data.contact_quality} confidence` : "—"}</span></div>
              <div className="section-rule"/>
              <Label>Next action</Label><button className="button primary full" onClick={()=>navigate(`/leads/${selected.data.id}`)}>Open lead review</button>
              <p className="preview-foot">{selected.data.draft ? "Draft exists · human approval required" : "No outreach draft yet"}</p>
            </> : <EmptyState>Select a lead to preview its intelligence.</EmptyState>}
          </aside>
        </div>
      </section>
    </>
  );
}

function Label({children}:{children:ReactNode}){return <div className="field-label">{children}</div>;}
