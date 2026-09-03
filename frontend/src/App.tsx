import { FormEvent, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, type AuthSession } from "./api";
import { Shell } from "./components/Shell";
import { DashboardPage } from "./pages/DashboardPage";
import { LeadReviewPage } from "./pages/LeadReviewPage";
import { LeadsPage } from "./pages/LeadsPage";

const client = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false }
  }
});

function Placeholder({ title }: { title: string }) {
  return <div className="full-page-message"><strong>{title}</strong><span>This PactSignal workspace is next in the operator UI milestone.</span></div>;
}

function LoginScreen({ onAuthenticated }: { onAuthenticated: (session: AuthSession) => void }) {
  const [username, setUsername] = useState("sachin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const session = await api.login(username, password);
      client.clear();
      onAuthenticated(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
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
            <div className="brand-name">PactSignal</div>
            <div className="brand-subtitle">Partner intelligence & outreach</div>
          </div>
        </div>

        <div className="auth-copy">
          <span className="auth-kicker">Private operator workspace</span>
          <h1>Sign in to PactSignal</h1>
          <p>Your session is protected by a short-lived, HttpOnly JWT cookie.</p>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label>
            <span>Username</span>
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label>
            <span>Password</span>
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              autoFocus
            />
          </label>
          {error ? <div className="error-banner compact">{error}</div> : null}
          <button className="button primary auth-submit" disabled={submitting} type="submit">
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api.session()
      .then((value) => {
        if (active) setSession(value);
      })
      .catch(() => {
        if (active) setSession(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return <div className="full-page-message"><strong>PactSignal</strong><span>Checking operator session…</span></div>;
  }

  if (!session?.authenticated) {
    return <LoginScreen onAuthenticated={setSession} />;
  }

  return <>{children}</>;
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
              <Route path="/discovery" element={<Placeholder title="Discovery" />} />
              <Route path="/runs" element={<Placeholder title="Outreach runs" />} />
              <Route path="/follow-ups" element={<Placeholder title="Follow-ups" />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthGate>
    </QueryClientProvider>
  );
}
