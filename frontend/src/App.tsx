import { type ReactNode, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, setApiMode } from "./api";
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

function BootstrapGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    api.site()
      .then((site) => {
        setApiMode(site.mode);
        client.clear();
        return api.meta();
      })
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Nuntago access check failed");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="full-page-message error">
        <strong>Nuntago</strong>
        <span>{error}</span>
        <small>
          The public showcase is available at nuntago.ergorum.com. Operator access
          requires the protected console.
        </small>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="full-page-message">
        <strong>Nuntago</strong>
        <span>Opening workspace…</span>
      </div>
    );
  }

  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <BootstrapGate>
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
      </BootstrapGate>
    </QueryClientProvider>
  );
}
