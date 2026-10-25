import { fmt, pct } from "../lib/format";

interface Props {
  ev: any | null;
  loading: boolean;
  error: Error | null;
  job: any | null;
  onRun: () => void;
  artifacts: any | null;
  synthetic: boolean;
}

const METHOD_ORDER = ["raw_substitution", "global_ratio", "nb_regression", "seasonal_ratio"];

export function ValidationView({ ev, loading, error, job, onRun, artifacts, synthetic }: Props) {
  const running = job && (job.status === "queued" || job.status === "running");
  return (
    <div>
      <div className="method-section">
        <h3>Blocked hold-out validation</h3>
        <p>
          Each candidate is fitted on training years (or spatial blocks) and scored on held-out ones, with a buffer between them so correlated fire weeks cannot leak.
          The score is agreement with the reference platform's own held-out observations. That measures cross-sensor agreement, not accuracy against an independent fire reference.
        </p>
        {synthetic && <div className="note syn">This dataset is synthetic. Passing these checks shows the method recovers a known injected sensor effect; it says nothing about real MODIS/VIIRS behaviour.</div>}
      </div>
      {error && <div className="state error">{error.message}</div>}
      {(!ev || ev.available === false) && !loading && (
        <div className="state">
          <h3>Not run for this selection yet</h3>
          <p>Validation refits the models for every fold and block-bootstraps intervals. It usually takes one to three minutes.</p>
          <button className="btn primary" onClick={onRun} disabled={running}>
            {running ? "Running validation…" : "Run blocked validation"}
          </button>
          {running && <div className="progress" style={{ marginTop: 10 }}><div /></div>}
          {job?.status === "error" && <p className="state error">Validation failed: {job.error}</p>}
        </div>
      )}
      {ev && ev.skipped && <div className="note bad">Validation skipped: {ev.skipped}.</div>}
      {ev && ev.schemes && (
        <>
          <h3 style={{ marginTop: 6 }}>Gates</h3>
          <ul className="checklist" style={{ maxWidth: "80ch" }}>
            {ev.gates.checks.map((c: any) => (
              <li key={c.check} className={c.passed ? "pass" : "fail"}>
                <span className="mark">{c.passed ? "✓" : "✗"}</span>
                <span>{c.check} <span className="muted">({c.detail})</span></span>
              </li>
            ))}
          </ul>
          {Object.entries(ev.schemes).map(([scheme, res]: [string, any]) =>
            res.methods ? (
              <div key={scheme} style={{ marginTop: 16 }}>
                <h3>{scheme === "temporal" ? "Leave-one-year-out (temporal blocks)" : "Spatial blocks"}</h3>
                <p className="small muted">
                  {res.folds.length} folds, {res.n_test_windows} held-out weeks. 90% predictive-interval coverage of the candidate: <b>{pct(res.interval_coverage)}</b> (mean width {fmt(res.interval_mean_width, 1)}).
                </p>
                <div className="table-scroll">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Method</th>
                        <th title="Mean absolute error of AOI-week reference totals">MAE (week)</th>
                        <th>RMSE (week)</th>
                        <th title="Mean Poisson deviance of AOI-week totals (lower is better)">Deviance (week)</th>
                        <th title="(predicted - observed) / observed, summed">Rel. bias</th>
                        <th title="Mean Poisson deviance per cell-week">Deviance (cell)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {METHOD_ORDER.filter((m) => res.methods[m]).map((m) => {
                        const v = res.methods[m];
                        const best = Math.min(...Object.values(res.methods).map((x: any) => x.aoi_week.mean_poisson_deviance));
                        return (
                          <tr key={m} className={v.aoi_week.mean_poisson_deviance === best ? "best" : undefined}>
                            <td>{v.label}{v.kind === "candidate" ? " (candidate)" : ""}</td>
                            <td>{fmt(v.aoi_week.mae, 1)}</td>
                            <td>{fmt(v.aoi_week.rmse, 1)}</td>
                            <td>{fmt(v.aoi_week.mean_poisson_deviance, 2)}</td>
                            <td>{pct(v.aoi_week.relative_bias)}</td>
                            <td>{fmt(v.cell_week.mean_poisson_deviance, 3)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {res.methods.seasonal_ratio?.by_season && (
                  <p className="small muted" style={{ marginTop: 6 }}>
                    Candidate stability by season (week deviance):{" "}
                    {Object.entries(res.methods.seasonal_ratio.by_season).map(([s, m]: [string, any]) => `${s} ${fmt(m.mean_poisson_deviance, 2)}`).join(", ")}.
                  </p>
                )}
                <details>
                  <summary>Folds</summary>
                  <table className="data">
                    <thead><tr><th>Fold</th><th>Train cell-weeks</th><th>Test cell-weeks</th><th>Buffer removed</th><th>Seasonal order</th><th>Coverage</th></tr></thead>
                    <tbody>
                      {res.folds.map((f: any) => (
                        <tr key={f.fold}>
                          <td>{f.fold}</td>
                          <td>{f.skipped ? f.skipped : fmt(f.train_units)}</td>
                          <td>{fmt(f.test_units)}</td>
                          <td>{fmt(f.buffer_units_removed)}</td>
                          <td>{f.harmonic_order ?? "–"}</td>
                          <td>{pct(f.interval_coverage)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </div>
            ) : null,
          )}
        </>
      )}
      {artifacts && (artifacts.synthetic_recovery || artifacts.sensitivity) && (
        <div style={{ marginTop: 22 }}>
          <h3>Offline experiments (from the artifacts folder)</h3>
          {artifacts.synthetic_recovery && <RecoverySummary r={artifacts.synthetic_recovery} />}
          {artifacts.sensitivity && <SensitivitySummary s={artifacts.sensitivity} />}
        </div>
      )}
    </div>
  );
}

function RecoverySummary({ r }: { r: any }) {
  const s = r.summary;
  return (
    <div className="note syn" style={{ maxWidth: "90ch" }}>
      <b>Synthetic recovery experiment</b> (generated {r.generated_at}, seed {s.seed}; separate from any real-data evidence)
      <div className="table-scroll">
        <table className="data" style={{ marginTop: 6 }}>
          <thead><tr><th>Quantity</th><th>Injected (effective)</th><th>Recovered</th><th>90% interval</th><th>Inside?</th></tr></thead>
          <tbody>
            {s.parameters.map((p: any) => (
              <tr key={p.name}>
                <td>{p.name}</td><td>{fmt(p.truth, 3)}</td><td>{fmt(p.estimate, 3)}</td><td>{fmt(p.lo, 3)}–{fmt(p.hi, 3)}</td><td>{p.inside ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {s.transfer && (
        <p className="small" style={{ marginTop: 6 }}>
          After the simulated end of the reference record, converted weekly totals had relative bias {pct(s.transfer.relative_bias)} against the known truth; the 90% estimate interval
          contained the truth in {pct(s.transfer.interval_coverage_truth)} of weeks. Naive splicing would have shown a bias of {pct(s.transfer.naive_splice_relative_bias)}.
        </p>
      )}
    </div>
  );
}

function SensitivitySummary({ s }: { s: any }) {
  return (
    <div className="note">
      <b>Sensitivity analysis</b> ({s.summary.data_origin} data)
      <div className="table-scroll">
        <table className="data" style={{ marginTop: 6 }}>
          <thead><tr><th>Setting</th><th>Mean ratio</th><th>Seasonal order</th><th>Dispersion</th><th>Week deviance (temporal CV)</th></tr></thead>
          <tbody>
            {s.summary.rows.map((r: any) => (
              <tr key={r.setting}>
                <td>{r.setting}</td><td>{fmt(r.mean_ratio, 2)}</td><td>{r.harmonic_order ?? "–"}</td><td>{fmt(r.dispersion_window, 2)}</td><td>{fmt(r.temporal_week_deviance, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
