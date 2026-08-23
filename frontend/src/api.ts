export type Grade = "Gold" | "Silver" | "Bronze";

export interface Scorecard {
  route: string;
  grade: Grade;
  score: number;
  port_level: string | null;
  open_findings_high: number;
  open_findings_med: number;
  open_findings_low: number;
  alert_would_fire: boolean;
}

export interface ObservabilitySnapshot {
  generated_at: string;
  outage: boolean;
  alert: {
    name: string;
    would_fire: boolean;
    worst_score: number;
    threshold: number;
    suppressed_reason: string | null;
    note: string;
  };
  scorecards: Scorecard[];
  project: {
    title: string;
    properties: {
      goal: string;
      technical_choices: string;
      known_risks: string;
    };
  };
  panels: {
    security_grade: {
      title: string;
      routes: Scorecard[];
      history: Array<{
        route: string;
        grade: string;
        score: number;
        created_at: string;
      }>;
    };
    open_findings: {
      title: string;
      by_severity: Record<string, number>;
      items: Array<{
        id: string;
        check_id: string;
        severity: string;
        route: string;
        title: string;
        evidence: string;
      }>;
    };
    triage: {
      title: string;
      by_classification: Record<string, number>;
    };
    fix_outcomes: {
      title: string;
      by_result: Record<string, number>;
    };
    audit: {
      title: string;
      p50_ms: number | null;
      p95_ms: number | null;
      recent_errors: Array<{ source: string; message: string }>;
    };
  };
}

const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8100";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(sessionStorage.getItem("forge_access_token")
        ? { Authorization: `Bearer ${sessionStorage.getItem("forge_access_token")}` }
        : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function fetchObservability() {
  return request<ObservabilitySnapshot>("/factory/observability");
}

export function injectMode(mode: 1 | 2 | 3 | 4) {
  return request<{ observability: ObservabilitySnapshot }>("/factory/inject", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

export function restoreFactory() {
  return request<{ observability: ObservabilitySnapshot }>("/factory/restore", {
    method: "POST",
  });
}
