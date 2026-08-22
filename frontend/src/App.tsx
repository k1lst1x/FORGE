import { Activity, Hammer, ShieldCheck, UserCheck } from "lucide-react";

const stages = [
  { label: "Build", icon: Hammer, value: "Feature request becomes working code" },
  { label: "Audit", icon: Activity, value: "Security checks run every five minutes" },
  { label: "Patch", icon: ShieldCheck, value: "Factory writes and verifies a fix" },
  { label: "Approve", icon: UserCheck, value: "Human reviews before release" },
];

function App() {
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
    </main>
  );
}

export default App;
