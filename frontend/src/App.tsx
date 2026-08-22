import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  Hammer,
  RefreshCw,
  ShieldCheck,
  UserCheck,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PromptInput } from "./components/ui/ai-chat-input";

type FactoryRun = {
  id: string;
  title: string;
  intake: "brief" | "finding";
  brief: string | null;
  trigger: string;
  status: string;
  next_gate: string | null;
  branch: string | null;
  pr_url: string | null;
  trace_id: string | null;
  outcome: string | null;
  created_at: string;
  updated_at: string;
};

type FactoryStep = {
  id: number;
  run_id: string;
  name: string;
  status: string;
  summary: string | null;
  started_at: string | null;
  completed_at: string | null;
};

type Finding = {
  id: string;
  run_id: string;
  check_id: string;
  severity: string;
  route: string;
  title: string;
  evidence: string;
  suggested_fix_hint: string | null;
  occurrences: number;
  created_at: string;
};

type FactoryRunDetail = FactoryRun & {
  steps: FactoryStep[];
  findings: Finding[];
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

const stages = [
  { label: "Build", icon: Hammer, value: "Feature request becomes working code" },
  { label: "Audit", icon: Activity, value: "Security checks run every five minutes" },
  { label: "Patch", icon: ShieldCheck, value: "Factory writes and verifies a fix" },
  { label: "Approve", icon: UserCheck, value: "Human reviews before release" },
];

const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });

  if (!response.ok) {
    let detail = "Request failed";
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }

  return (await response.json()) as T;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function statusClass(status: string): string {
  switch (status) {
    case "awaiting_human":
      return "status-badge warning";
    case "released":
      return "status-badge success";
    case "escalated":
    case "failed":
      return "status-badge danger";
    default:
      return "status-badge neutral";
  }
}

function App() {
  const [brief, setBrief] = useState(
    "Audit the app for security issues, patch the highest-risk route, and stop for human approval before release.",
  );
  const [runs, setRuns] = useState<FactoryRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FactoryRunDetail | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "FORGE is ready. Ask for a brief, a fix, or a risk audit and I’ll turn it into a tracked run.",
    },
  ]);

  const refreshRuns = useCallback(async () => {
    const nextRuns = await apiFetch<FactoryRun[]>("/factory/runs");
    setRuns(nextRuns);

    if (!nextRuns.length) {
      setSelectedRunId(null);
      setDetail(null);
      return;
    }

    setSelectedRunId((current) => {
      if (current && nextRuns.some((run) => run.id === current)) {
        return current;
      }
      return nextRuns[0].id;
    });
  }, []);

  const refreshDetail = useCallback(async (runId: string) => {
    const nextDetail = await apiFetch<FactoryRunDetail>(`/factory/runs/${runId}`);
    setDetail(nextDetail);
  }, []);

  useEffect(() => {
    void refreshRuns().catch((err: Error) => setError(err.message));
  }, [refreshRuns]);

  useEffect(() => {
    if (!selectedRunId) {
      return;
    }
    void refreshDetail(selectedRunId).catch((err: Error) => setError(err.message));
  }, [refreshDetail, selectedRunId]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshRuns().catch((err: Error) => setError(err.message));
    }, 5000);

    return () => window.clearInterval(timer);
  }, [refreshRuns]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  const handleCreateRun = async () => {
    if (!brief.trim()) {
      setError("Please enter a brief for the factory run.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const result = await apiFetch<FactoryRunDetail>("/factory/runs", {
        method: "POST",
        body: JSON.stringify({ brief, trigger: "manual", auto_start: true }),
      });
      setBrief("");
      setSelectedRunId(result.id);
      await refreshRuns();
      await refreshDetail(result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create a run.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAction = async (action: "approve" | "reject") => {
    if (!selectedRunId) {
      return;
    }

    setError(null);
    try {
      await apiFetch<FactoryRunDetail>(`/factory/runs/${selectedRunId}/${action}`, {
        method: "POST",
      });
      await refreshRuns();
      await refreshDetail(selectedRunId);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Unable to ${action} the run.`);
    }
  };

  const handlePromptSubmit = useCallback((message: string, meta: { model: string; effort: string; attachments: File[] }) => {
    const nextUserMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: message,
    };

    setMessages((current) => [...current, nextUserMessage]);

    const response = `Queued through ${meta.model} at ${meta.effort} effort. ${
      meta.attachments.length > 0
        ? `I also received ${meta.attachments.length} attachment(s) for review.`
        : "No files were attached; I’ll treat this as a text-only brief."
    }`;

    setTimeout(() => {
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: response,
        },
      ]);
    }, 250);
  }, []);

  return (
    <main className="shell">
      <section className="intro" aria-labelledby="title">
        <p className="kicker">Sat 22 Aug 2026 · Bright Data Loft, SF</p>
        <h1 id="title">FORGE</h1>
        <p className="statement">
          A factory that builds software, then finds the flaws in what it built and fixes them.
        </p>
      </section>

      <section className="status-grid" aria-label="Factory stages">
        {stages.map((stage) => {
          const Icon = stage.icon;
          return (
            <article className="stage" key={stage.label}>
              <div className="stage-icon" aria-hidden="true">
                <Icon size={20} strokeWidth={2} />
              </div>
              <h2>{stage.label}</h2>
              <p>{stage.value}</p>
            </article>
          );
        })}
      </section>

      <section className="operator-panel">
        <div className="panel-header">
          <div>
            <p className="section-eyebrow">Operator console</p>
            <h2>Factory runs</h2>
          </div>
          <button type="button" className="secondary-button" onClick={() => void refreshRuns()}>
            <RefreshCw size={15} />
            Refresh
          </button>
        </div>

        <div className="composer">
          <textarea
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
            placeholder="Describe the change request or audit trigger"
            rows={4}
          />
          <div className="composer-actions">
            <button type="button" onClick={() => void handleCreateRun()} disabled={submitting}>
              {submitting ? "Creating…" : "Create run"}
            </button>
          </div>
        </div>

        {error ? <p className="error-banner">{error}</p> : null}

        <div className="board">
          <aside className="run-list" aria-label="Run list">
            {runs.length === 0 ? (
              <div className="empty-state">No runs yet.</div>
            ) : (
              runs.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  className={selectedRun?.id === run.id ? "run-card active" : "run-card"}
                  onClick={() => setSelectedRunId(run.id)}
                >
                  <div className="run-card-topline">
                    <span className={`status-badge ${run.status === "awaiting_human" ? "warning" : run.status === "released" ? "success" : run.status === "escalated" || run.status === "failed" ? "danger" : "neutral"}`}>
                      {run.status}
                    </span>
                    <span className="run-id">{run.id}</span>
                  </div>
                  <strong>{run.title}</strong>
                  <small>{formatDate(run.updated_at)}</small>
                  {run.next_gate ? <p>{run.next_gate}</p> : null}
                </button>
              ))
            )}
          </aside>

          <section className="run-detail" aria-live="polite">
            {detail ? (
              <>
                <div className="detail-header">
                  <div>
                    <p className="section-eyebrow">Run details</p>
                    <h3>{detail.title}</h3>
                  </div>
                  <span className={statusClass(detail.status)}>{detail.status}</span>
                </div>

                <dl className="meta-grid">
                  <div>
                    <dt>Trigger</dt>
                    <dd>{detail.trigger}</dd>
                  </div>
                  <div>
                    <dt>Branch</dt>
                    <dd>{detail.branch ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>PR</dt>
                    <dd>
                      {detail.pr_url ? (
                        <a href={detail.pr_url} target="_blank" rel="noreferrer">
                          Open PR <ArrowUpRight size={14} />
                        </a>
                      ) : (
                        "—"
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>Trace ID</dt>
                    <dd>{detail.trace_id ?? "—"}</dd>
                  </div>
                </dl>

                {detail.next_gate ? <p className="next-gate">{detail.next_gate}</p> : null}

                {detail.status === "awaiting_human" ? (
                  <div className="controls">
                    <button type="button" className="approve" onClick={() => void handleAction("approve")}>
                      <CheckCircle2 size={16} />
                      Approve
                    </button>
                    <button type="button" className="reject" onClick={() => void handleAction("reject")}>
                      <XCircle size={16} />
                      Reject
                    </button>
                  </div>
                ) : null}

                <div className="detail-section">
                  <h4>Steps</h4>
                  <ul className="step-list">
                    {detail.steps.map((step) => (
                      <li key={step.id} className="step-item">
                        <div className="step-status">
                          <span className={step.status === "completed" ? "dot success" : step.status === "failed" ? "dot danger" : "dot neutral"} />
                          <strong>{step.name}</strong>
                        </div>
                        <div className="step-meta">
                          <span>{step.status}</span>
                          <span>{step.completed_at ? formatDate(step.completed_at) : "in progress"}</span>
                        </div>
                        {step.summary ? <p>{step.summary}</p> : null}
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="detail-section">
                  <h4>Findings</h4>
                  {detail.findings.length === 0 ? (
                    <p className="empty-state compact">No findings recorded yet.</p>
                  ) : (
                    <ul className="finding-list">
                      {detail.findings.map((finding) => (
                        <li key={finding.id} className="finding-item">
                          <div className="finding-head">
                            <span className={`severity ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
                            <strong>{finding.title}</strong>
                          </div>
                          <p>{finding.evidence}</p>
                          <div className="finding-meta">
                            <span>{finding.check_id}</span>
                            <span>{finding.route}</span>
                            <span>{finding.occurrences} occurrence(s)</span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            ) : (
              <div className="empty-state">Select a run to inspect details.</div>
            )}
          </section>
        </div>
      </section>

      <section className="chat-panel" aria-label="AI chat panel">
        <div className="panel-header chat-header">
          <div>
            <p className="section-eyebrow">AI chat</p>
            <h2>Prompt the assistant</h2>
          </div>
        </div>

        <div className="chat-window">
          {messages.map((message) => (
            <div key={message.id} className={`chat-bubble ${message.role}`}>
              <span className="chat-role">{message.role === "assistant" ? "Assistant" : "You"}</span>
              <p>{message.content}</p>
            </div>
          ))}
        </div>

        <div className="chat-input-wrap">
          <PromptInput onSubmit={handlePromptSubmit} placeholder="Ask the factory to inspect, patch or summarize..." />
        </div>
      </section>
    </main>
  );
}

export default App;
