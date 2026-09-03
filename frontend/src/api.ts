import type { DashboardData, Lead, LeadList, Meta } from "./types";

const TOKEN_KEY = "pactsignal_api_token";

export function getAccessToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function setAccessToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token.trim());
}

export function clearAccessToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");

  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(path, {
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
  runDiscovery: (limit = 20) =>
    request<Record<string, unknown>>(`/api/runs/discovery?limit=${limit}`, { method: "POST" }),
  runOutreach: (limit = 10) =>
    request<Record<string, unknown>>(`/api/runs/outreach?limit=${limit}`, { method: "POST" })
};
