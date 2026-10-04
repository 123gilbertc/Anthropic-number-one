import { time } from "../api";
import { useStore } from "../store";

export function Audit() {
  const d = useStore((s) => s.decisions);
  return (
    <section className="card">
      <p className="muted">Every change of action and every approval, taken or not. Newest last.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Contract</th><th>Action</th><th>Why</th><th>Model</th></tr></thead>
          <tbody>
            {d.map((x) => (
              <tr key={x.decision_id}>
                <td>{time(x.decision_time)}</td><td>{x.contract_id}</td><td>{x.action}</td>
                <td className="small">{x.explanation.join(" · ")}</td>
                <td className="small">{x.model_version ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
