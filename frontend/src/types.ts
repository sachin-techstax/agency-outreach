export type Lead = {
  id: number;
  company: string;
  domain: string;
  website: string;
  source_query?: string;
  source_url?: string;
  summary?: string;
  services?: string;
  services_list?: string[];
  team_hint?: string;
  score: number;
  score_reasons?: string;
  score_reason_list?: string[];
  fit_reason?: string;
  proof_project?: string;
  outreach_angle?: string;
  contact_name?: string;
  contact_role?: string;
  contact_email?: string;
  contact_source?: string;
  contact_quality?: string;
  subject?: string;
  draft?: string;
  status: string;
  gmail_draft_id?: string;
  created_at?: string;
  updated_at?: string;
  last_contact_at?: string | null;
  followup_due_at?: string | null;
};

export type LatestRun = {
  type?: string;
  query_count?: number;
  search_results_total?: number;
  raw_candidate_domains?: number;
  rejected_candidate_domains?: number;
  candidate_domains?: number;
  ranked_candidate_domains?: number;
  suppressed_existing?: number;
  fresh_retryable_pool?: number;
  attempted?: number;
  processed?: number;
  qualified?: number;
  drafted?: number;
  below_score?: number;
  failed?: number;
  duration_s?: number;
};

export type DashboardData = {
  mode: "demo" | "private";
  counts: {
    total: number;
    drafted: number;
    approved: number;
    gmail_drafted: number;
    sent: number;
    do_not_contact: number;
    retryable: number;
  };
  due_followups: number;
  review_queue: Lead[];
  latest_run: LatestRun | null;
};

export type Meta = {
  product: string;
  descriptor: string;
  demo_mode: boolean;
  minimum_score: number;
  external_actions_enabled: boolean;
};

export type LeadList = {
  items: Lead[];
  total: number;
};
