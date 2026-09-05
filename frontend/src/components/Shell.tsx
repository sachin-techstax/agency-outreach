import { Activity, ExternalLink, LayoutDashboard, LockKeyhole, Radar, Rows3, Send, Sparkles } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/leads", label: "Leads", icon: Rows3 },
  { to: "/discovery", label: "Discovery", icon: Radar },
  { to: "/runs", label: "Outreach runs", icon: Send },
  { to: "/follow-ups", label: "Follow-ups", icon: Activity }
];

export function Shell() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const publicMode = meta.data?.mode === "public" || Boolean(meta.data?.demo_mode);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">N</div>
          <div>
            <div className="brand-name">Nuntago</div>
            <div className="brand-subtitle">Partner intelligence & outreach</div>
          </div>
        </div>

        <div className="sidebar-divider" />
        <div className="sidebar-kicker">Workspace</div>
        <nav className="nav-list">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
              <Icon size={15} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-divider" />
        <div className="sidebar-kicker">System</div>
        <div className="system-card">
          <span className="health-dot" />
          <div>
            <strong>{publicMode ? "Read-only showcase" : "Pipeline healthy"}</strong>
            <span>{publicMode ? "Fictional portfolio dataset" : "Serper · OpenAI · Gmail"}</span>
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="operator-row">
            {publicMode ? <LockKeyhole size={14} /> : <Sparkles size={14} />}
            <strong>{publicMode ? "Public showcase" : "Sachin"}</strong>
          </div>
          <span>{publicMode ? "Explore freely · actions disabled" : "Private console · Access protected"}</span>
          <a
            className="sidebar-console-link"
            href={publicMode ? "https://console.nuntago.ergorum.com" : "https://nuntago.ergorum.com"}
          >
            {publicMode ? "Operator console" : "View public showcase"}
            <ExternalLink size={11} />
          </a>
        </div>
      </aside>
      <main className="workspace">
        {publicMode && (
          <div className="showcase-banner">
            <LockKeyhole size={13} />
            <span><strong>Public showcase.</strong> Fictional data, real workflow. All external and persistent actions are disabled.</span>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
