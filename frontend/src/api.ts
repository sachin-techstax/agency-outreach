import type {
  DashboardData,
  DiscoveryResult,
  FollowupList,
  Lead,
  LeadList,
  Meta,
  RefreshResult,
  RegenerateResult,
  RunList,
  RunRow,
  SiteInfo
} from "./types";

export type ApiMode = "public" | "operator";

let apiMode: ApiMode = "operator";

export function setApiMode(mode: ApiMode): void {
  apiMode = mode;
}

export function getApiMode(): ApiMode {
  return apiMode;
}

function resolvePath(path: string): string {
  if (
    apiMode === "public" &&
    path.startsWith("/api/") &&
    path !== "/api/site" &&
    path !== "/api/health"
  ) {
    return `/api/public/${path.slice("/api/".length)}`;
  }
  return path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");

  const response = await fetch(resolvePath(path), {
    ...init,
    headers
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // Keep the status-based fallback.
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export const api = {
  site: () => request<SiteInfo>("/api/site"),
  meta: () => request<Meta>("/api/meta"),
  dashboard: () => request<DashboardData>("/api/dashboard"),
  leads: (params: { q?: string; status?: string; minScore?: number; limit?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.q) search.set("q", params.q);
    if (params.status) search.set("status", params.status);
    if (params.minScore) search.set("min_score", String(params.minScore));
    if (params.limit) search.set("limit", String(params.limit));
    return request<LeadList>(`/api/leads?${search.toString()}`);
  },
  lead: (id: number) => request<Lead>(`/api/leads/${id}`),
  action: (id: number, action: string) =>
    request<Lead>(`/api/leads/${id}/${action}`, { method: "POST" }),
  refreshResearch: (id: number) =>
    request<RefreshResult>(`/api/leads/${id}/refresh-research`, { method: "POST" }),
  regenerateDraft: (id: number) =>
    request<RegenerateResult>(`/api/leads/${id}/regenerate-draft`, { method: "POST" }),
  runDiscovery: (limit = 20) =>
    request<RunRow>(`/api/runs/discovery?limit=${limit}`, { method: "POST" }),
  runProcess: (limit = 10) =>
    request<RunRow>(`/api/runs/process?limit=${limit}`, { method: "POST" }),
  runOutreach: (limit = 10) =>
    request<RunRow>(`/api/runs/outreach?limit=${limit}`, { method: "POST" }),
  runs: (limit = 50) => request<RunList>(`/api/runs?limit=${limit}`),
  runsByType: (type: string, limit = 50) =>
    request<RunList>(`/api/runs?type=${type}&limit=${limit}`),
  runsByTypeAndStatus: (type: string, status: string, limit = 50) =>
    request<RunList>(`/api/runs?type=${type}&status=${status}&limit=${limit}`),
  run: (id: number) => request<RunRow>(`/api/runs/${id}`),
  followups: () => request<FollowupList>("/api/followups")
};
