import { FormEvent, type ReactNode, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, clearAccessToken, getAccessToken, setAccessToken } from "./api";
import { Shell } from "./components/Shell";
import { DashboardPage } from "./pages/DashboardPage";
import { DiscoveryPage } from "./pages/DiscoveryPage";
import { FollowupsPage } from "./pages/FollowupsPage";
import { LeadReviewPage } from "./pages/LeadReviewPage";
import { LeadsPage } from "./pages/LeadsPage";
import { RunsPage } from "./pages/RunsPage";

const client = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false }
  }
});

function TokenScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    setAccessToken(token);
    try {
      await api.meta();
      client.clear();
      onAuthenticated();
    } catch (err) {
      clearAccessToken();
      setError(err instanceof Error ? err.message : "Access denied");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="brand-mark">P</div>
          <div>
            <div className="brand-name">Nuntago</div>
            <div className="brand-subtitle">Partner intelligence & outreach</div>
          </div>
        </div>

        <div className="auth-copy">
          <span className="auth-kicker">Private operator workspace</span>
          <h1>Unlock Nuntago</h1>
          <p>Enter the operator API token for this browser session.</p>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label>
            <span>Access token</span>
            <input
              autoComplete="off"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              required
              autoFocus
            />
          </label>
          {error ? <div className="error-banner compact">{error}</div> : null}
          <button className="button primary auth-submit" disabled={submitting} type="submit">
            {submitting ? "Checking…" : "Unlock"}
          </button>
        </form>
      </div>
    </div>
  );
}

function AuthGate({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);

  useEffect(() => {
    api.meta()
      .then(() => setAuthenticated(true))
      .catch(() => setAuthenticated(false));
  }, []);

  if (authenticated === null) {
    return <div className="full-page-message"><strong>Nuntago</strong><span>Checking operator access…</span></div>;
  }

  if (!authenticated) {
    return <TokenScreen onAuthenticated={() => setAuthenticated(true)} />;
  }

  return <>{children}</>;
}

export function signOutOperator(): void {
  clearAccessToken();
  client.clear();
  window.location.assign("/");
}

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <AuthGate>
        <BrowserRouter>
          <Routes>
            <Route element={<Shell />}>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/leads" element={<LeadsPage />} />
              <Route path="/leads/:id" element={<LeadReviewPage />} />
              <Route path="/discovery" element={<DiscoveryPage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="/follow-ups" element={<FollowupsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthGate>
    </QueryClientProvider>
  );
}
