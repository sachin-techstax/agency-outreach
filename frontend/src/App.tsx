import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
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

export default function App() {
  return (
    <QueryClientProvider client={client}>
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
    </QueryClientProvider>
  );
}
