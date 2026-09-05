import { Activity, LayoutDashboard, LogOut, Radar, Rows3, Send, Sparkles } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { signOutOperator } from "../App";

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/leads", label: "Leads", icon: Rows3 },
  { to: "/discovery", label: "Discovery", icon: Radar },
  { to: "/runs", label: "Outreach runs", icon: Send },
  { to: "/follow-ups", label: "Follow-ups", icon: Activity }
];

export function Shell() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });

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
            <strong>Pipeline healthy</strong>
            <span>Serper · OpenAI · Gmail</span>
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="operator-row">
            <Sparkles size={14} />
            <strong>Sachin</strong>
          </div>
          <span>{meta.data?.demo_mode ? "Demo mode · read-only" : "Private mode"}</span>
          <button
            className="sidebar-logout"
            type="button"
            onClick={signOutOperator}
          >
            <LogOut size={12} />
            Sign out
          </button>
        </div>
      </aside>
      <main className="workspace">
        <Outlet />
      </main>
    </div>
  );
}
