// "Why this assessment?" Every item says what kind of evidence it is:
// OBSERVATION (recorded feed), MODEL (model calculation), CALCULATION (arithmetic on
// prices and fees) or AI (optional shadow review). Drivers are controlled sensitivity
// of the real model, not causal claims.
import { clock, pct } from "../format";
import { IconX } from "./Icons";

const KIND_STYLE: Record<string, string> = { OBSERVATION: "info", MODEL: "value", CALCULATION: "none", AI: "watch" };

export function EvidenceDrawer({ ev, subject, onClose }: { ev: any; subject: string; onClose: () => void }) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Why this assessment" data-testid="evidence-drawer">
        <div className="drawer-head">
          <div><h2>Why this assessment?</h2><div className="small muted">For {subject}. Numbers come from the model and recorded data.</div></div>
          <button className="ghost icon" onClick={onClose} aria-label="Close"><IconX /></button>
        </div>
        <div className="drawer-body">
          <Section title="Main supporting drivers">
            {ev.supporting.length === 0 && <p className="small muted">No input moves this model's estimate toward this side by more than half a point.</p>}
            {ev.supporting.map((d: any) => <Driver key={d.key} d={d} />)}
          </Section>
          <Section title="Strongest contrary evidence">
            {ev.contrary.length === 0 && <p className="small muted">Nothing contrary found by the checks.</p>}
            {ev.contrary.map((d: any, i: number) => d.key ? <Driver key={d.key} d={d} /> :
              <div key={i} className="ev-item"><Kind k={d.kind} /><span className="small">{d.label}</span></div>)}
          </Section>
          <Section title="What changed since the previous estimate">
            {!ev.changed.available && <p className="small muted">{ev.changed.reason}</p>}
            {ev.changed.available && (
              <div className="stack" style={{ gap: 8 }}>
                <div className="ev-item"><Kind k="MODEL" /><span className="small">Estimate {pct(ev.changed.previous.p, 1)} at {clock(ev.changed.previous.t)} → {pct(ev.changed.current.p, 1)} at {clock(ev.changed.current.t)}</span>
                  <span className="delta">{ev.changed.delta_pp >= 0 ? "+" : "−"}{Math.abs(ev.changed.delta_pp).toFixed(1)} pts</span></div>
                {ev.changed.ask_then != null && <div className="ev-item"><Kind k="OBSERVATION" /><span className="small">Best ask {Math.round(ev.changed.ask_then * 100)}¢ → {Math.round(ev.changed.ask_now * 100)}¢</span></div>}
                {ev.changed.events.map((e: any, i: number) => <div key={i} className="ev-item"><Kind k="OBSERVATION" /><span className="small">{clock(e.t)} · {e.label}</span></div>)}
              </div>
            )}
          </Section>
          <Section title="Missing or unreliable inputs">
            {ev.missing.map((m: any, i: number) => (
              <div key={i} className="ev-item"><span className={`pill ${m.severity === "BLOCKING" ? "bad" : m.severity === "EVIDENCE" ? "watch" : "unavailable"}`}>{m.severity.replace("_", " ").toLowerCase()}</span><span className="small">{m.label}</span></div>
            ))}
          </Section>
          <Section title="What would invalidate this assessment">
            <ul className="checklist small">{ev.invalidation.map((x: string) => <li key={x}>· {x}</li>)}</ul>
          </Section>
          <Section title="AI review">
            <p className="small muted">{ev.ai_review.note}</p>
          </Section>
          <p className="tiny muted">Drivers come from re-running the real model with one input neutralised. They depend on the model and are not proof of causation.</p>
        </div>
      </aside>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="stack" style={{ gap: 8 }}><h4 className="upper muted">{title}</h4>{children}</section>;
}
function Kind({ k }: { k: string }) {
  return <span className={`pill ${KIND_STYLE[k] ?? "none"}`} title={k}>{k.toLowerCase()}</span>;
}
function Driver({ d }: { d: any }) {
  if (!d.available) return <div className="ev-item"><Kind k="MODEL" /><span className="small">{d.label}: could not re-run the model ({d.reason})</span></div>;
  return (
    <div className="ev-item" data-testid="driver">
      <Kind k="MODEL" />
      <div className="grow small"><b style={{ fontWeight: 550 }}>{d.label}</b>
        <div className="tiny muted">Estimate {pct(d.estimate_with_input, 1)} with it, {pct(d.estimate_neutralized, 1)} without it</div></div>
      <span className={`delta ${d.delta_pp >= 0 ? "pos" : "neg"}`}>{d.delta_pp >= 0 ? "+" : "−"}{Math.abs(d.delta_pp).toFixed(1)} pts</span>
    </div>
  );
}
