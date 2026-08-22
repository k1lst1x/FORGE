import { useEffect, useState } from "react";

import {
  fetchObservability,
  injectMode,
  restoreFactory,
  type ObservabilitySnapshot,
  type Scorecard,
} from "./api";

const INJECT_LABELS = [
  { mode: 1 as const, label: "1 Headers" },
  { mode: 2 as const, label: "2 Secret shape" },
  { mode: 3 as const, label: "3 /docs /admin" },
  { mode: 4 as const, label: "4 Outage" },
];

export default function Dashboard() {
  const [data, setData] = useState<ObservabilitySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setData(await fetchObservability());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Backend unreachable");
    }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  async function runInject(mode: 1 | 2 | 3 | 4) {
    setBusy(true);
    try {
      const result = await injectMode(mode);
      setData(result.observability);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Inject failed");
    } finally {
      setBusy(false);
    }
  }

  async function runRestore() {
    setBusy(true);
    try {
      const result = await restoreFactory();
      setData(result.observability);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Restore failed");
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <p className="status-line">
        {error
          ? `Operator dashboard waiting on API: ${error}. Start the backend on :8000.`
          : "Loading operator dashboard…"}
      </p>
    );
  }

  const firing = data.alert.would_fire;
  const triage = data.panels.triage.by_classification;
  const outcomes = data.panels.fix_outcomes.by_result;
  const findings = data.panels.open_findings.by_severity;

  return (
    <section className="ops" aria-label="Zafar operator dashboard">
      <div className="ops-toolbar">
        <div>
          <p className="ops-kicker">Zafar lane · five panels · 10-second read</p>
          <h2>Loop B, live</h2>
        </div>
        <div className={`alert-pill ${firing ? "firing" : "quiet"}`}>
          {data.outage
            ? "OUTAGE — no fix runs"
            : firing
              ? "ALERT FIRING — grade below Silver"
              : "Alert quiet — worst grade Silver or better"}
        </div>
      </div>

      {error ? <p className="status-line">{error}</p> : null}

      <div className="inject-row">
        <span>Break it</span>
        {INJECT_LABELS.map((item) => (
          <button
            key={item.mode}
            type="button"
            disabled={busy}
            onClick={() => void runInject(item.mode)}
          >
            {item.label}
          </button>
        ))}
        <button type="button" className="restore" disabled={busy} onClick={() => void runRestore()}>
          Restore
        </button>
      </div>

      <div className="panel-grid">
        <article className="panel panel-wide">
          <h3>1 · Security grade over time</h3>
          <p className="panel-why">Injection is a cliff. The fix is the recovery. Point here on camera.</p>
          <div className="grade-row">
            {data.scorecards.map((card) => (
              <GradeCard key={card.route} card={card} />
            ))}
          </div>
          <Sparkline history={data.panels.security_grade.history} />
        </article>

        <article className="panel">
          <h3>2 · Open findings</h3>
          <StackedBar
            segments={[
              { label: "HIGH", value: findings.HIGH ?? 0, tone: "high" },
              { label: "MED", value: findings.MED ?? 0, tone: "med" },
              { label: "LOW", value: findings.LOW ?? 0, tone: "low" },
            ]}
          />
          <ul className="finding-list">
            {data.panels.open_findings.items.slice(0, 4).map((item) => (
              <li key={item.id}>
                <strong>
                  {item.check_id} {item.severity}
                </strong>{" "}
                {item.route} — {item.title}
              </li>
            ))}
            {data.panels.open_findings.items.length === 0 ? <li>No findings recorded.</li> : null}
          </ul>
        </article>

        <article className="panel">
          <h3>3 · Triage decisions</h3>
          <p className="panel-why">The panel nobody else will have.</p>
          <ul className="count-list">
            {Object.entries(triage).map(([name, count]) => (
              <li key={name}>
                <span>{name}</span>
                <strong>{count}</strong>
              </li>
            ))}
          </ul>
        </article>

        <article className="panel">
          <h3>4 · Fix outcomes</h3>
          <StackedBar
            segments={[
              { label: "attempted", value: outcomes.attempted ?? 0, tone: "low" },
              { label: "verified", value: outcomes.verified ?? 0, tone: "gold" },
              { label: "rejected", value: outcomes.rejected ?? 0, tone: "med" },
              { label: "escalated", value: outcomes.escalated ?? 0, tone: "high" },
            ]}
          />
        </article>

        <article className="panel">
          <h3>5 · Audit duration + errors</h3>
          <p className="duration">
            p50 {formatMs(data.panels.audit.p50_ms)} · p95 {formatMs(data.panels.audit.p95_ms)}
          </p>
          <ul className="finding-list">
            {data.panels.audit.recent_errors.slice(0, 4).map((item) => (
              <li key={`${item.source}-${item.message}`}>
                <strong>{item.source}</strong> — {item.message}
              </li>
            ))}
            {data.panels.audit.recent_errors.length === 0 ? (
              <li>No error logs. Click a log line in SigNoz to jump to its trace.</li>
            ) : null}
          </ul>
        </article>
      </div>

      <article className="panel project-panel">
        <h3>Port project record</h3>
        <p>
          <strong>Goal.</strong> {data.project.properties.goal}
        </p>
        <p>
          <strong>Technical choices.</strong> {data.project.properties.technical_choices}
        </p>
        <p>
          <strong>Risks.</strong> {data.project.properties.known_risks}
        </p>
      </article>
    </section>
  );
}

function GradeCard({ card }: { card: Scorecard }) {
  return (
    <div className={`grade-card grade-${card.grade.toLowerCase()}`}>
      <p className="grade-route">{card.route}</p>
      <p className="grade-value">{card.grade}</p>
      <p className="grade-meta">
        score {card.score} · HIGH {card.open_findings_high} · MED {card.open_findings_med}
        {card.port_level ? ` · Port ${card.port_level}` : " · Port below Bronze"}
      </p>
    </div>
  );
}

function StackedBar({
  segments,
}: {
  segments: Array<{ label: string; value: number; tone: string }>;
}) {
  const total = segments.reduce((sum, item) => sum + item.value, 0) || 1;
  return (
    <div>
      <div className="stack" aria-hidden="true">
        {segments.map((item) => (
          <span
            key={item.label}
            className={`stack-seg ${item.tone}`}
            style={{ width: `${(item.value / total) * 100}%` }}
          />
        ))}
      </div>
      <ul className="count-list">
        {segments.map((item) => (
          <li key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Sparkline({
  history,
}: {
  history: ObservabilitySnapshot["panels"]["security_grade"]["history"];
}) {
  const points = [...history].reverse().slice(-16);
  if (points.length === 0) {
    return <p className="panel-why">No grade history yet. Inject mode 1 to draw the cliff.</p>;
  }
  return (
    <div className="spark" aria-label="Grade history">
      {points.map((point, index) => (
        <span
          key={`${point.created_at}-${index}`}
          className={`spark-bar grade-${point.grade.toLowerCase()}`}
          style={{ height: `${(point.score / 3) * 100}%` }}
          title={`${point.route} ${point.grade}`}
        />
      ))}
    </div>
  );
}

function formatMs(value: number | null) {
  return value == null ? "—" : `${Math.round(value)}ms`;
}
