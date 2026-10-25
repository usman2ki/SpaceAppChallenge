import { useState } from "react";
import { naiveSplice } from "../lib/calendar";
import { fmt } from "../lib/format";
import { PLATFORM_COLOR } from "../lib/scales";
import { Chart, Series, toMs } from "./Chart";

export function statusTone(code: string | undefined): string {
  if (!code) return "";
  if (code === "synthetic_demo_verified") return "syn";
  if (code === "validated_holdout") return "ok";
  if (code === "research_estimate" || code === "not_evaluated") return "warn";
  return "bad";
}

export function StatusChip({ status }: { status: any }) {
  if (!status) return null;
  return (
    <span className={"status-chip " + statusTone(status.code)} title={status.explanation}>
      <span className="dot" aria-hidden="true" />
      {status.label}
    </span>
  );
}

interface Props {
  h: any | null;
  loading: boolean;
  error: Error | null;
  view: [string, string];
  selected: string | null;
  onPick: (ws: string) => void;
  reference: string;
  source: string;
  onGoValidation: () => void;
}

export function CompareView({ h, loading, error, view, selected, onPick, reference, source, onGoValidation }: Props) {
  const [showCandidate, setShowCandidate] = useState(false);
  if (error) return <div className="state error"><h3>Harmonisation unavailable</h3>{error.message}</div>;
  if (loading && !h) return <div className="state">Fitting the calibration and bootstrap intervals for this area…</div>;
  if (!h) return null;
  const rows = (h.series as any[]).filter((r) => r.window_start >= view[0] && r.window_start <= view[1]);
  const pick = (x: number) => onPick(new Date(x).toISOString().slice(0, 10));
  const marker = selected ? toMs(selected) : null;
  const ov: [number, number][] = h.pair.overlap_start ? [[toMs(h.pair.overlap_start), toMs(h.pair.overlap_end)]] : [];
  const splice = naiveSplice(rows);
  const showHarm = h.status.show_harmonized || showCandidate;

  const rawSeries: Series[] = [
    { key: "ref", label: `${reference} raw (quality)`, color: PLATFORM_COLOR[reference], points: rows.map((r) => ({ x: toMs(r.window_start), y: r.ref_queried ? r.ref_raw : null })) },
    { key: "src", label: `${source} raw (quality)`, color: PLATFORM_COLOR[source], points: rows.map((r) => ({ x: toMs(r.window_start), y: r.src_queried ? r.src_raw : null })) },
    { key: "splice", label: `Naive splice: ${reference} then ${source}`, color: "var(--heat-3)", dash: "5 3", width: 1.4, points: splice.map((r) => ({ x: toMs(r.window_start), y: r.value })) },
  ];
  const harmSeries: Series[] = [
    { key: "refobs", label: `${reference} observed${h.mode === "exposure" ? " (coverage-adjusted)" : ""}`, color: PLATFORM_COLOR[reference], band: true,
      points: rows.map((r) => ({ x: toMs(r.window_start), y: r.ref_value ?? null, lo: r.ref_lo, hi: r.ref_hi })) },
    { key: "conv", label: `${source} converted to ${reference} scale`, color: "var(--harm)", band: true, width: 1.8,
      points: rows.map((r) => ({ x: toMs(r.window_start), y: r.src_converted ?? null, lo: r.src_conv_lo, hi: r.src_conv_hi })) },
  ];

  // effect of calibration in numbers (within the overlap shown)
  const inOv = rows.filter((r) => r.in_overlap && r.ref_value != null && r.src_converted != null);
  const sum = (k: string) => inOv.reduce((s, r) => s + (r[k] || 0), 0);

  return (
    <div>
      <div className="panel-head">
        <StatusChip status={h.status} />
        <span className="muted small">{h.status.explanation}</span>
      </div>
      {h.status.code === "not_evaluated" && (
        <div className="note warn">
          Converted values below are from a fitted but not yet validated calibration.{" "}
          <button className="btn" onClick={onGoValidation}>Run blocked validation</button>
        </div>
      )}
      {(h.warnings || []).map((w: string, i: number) => (
        <div key={i} className="note warn small">{w}</div>
      ))}
      <div className="grid-2" style={{ marginTop: 10 }}>
        <div>
          <div className="chart-title">What you get by splicing raw counts</div>
          <div className="chart-sub">Quality-filtered detections per week, each sensor on its own scale. Shaded: calibration overlap.</div>
          <Chart series={rawSeries} yLabel="detections / week" yScale="symlog" marker={marker} onPick={pick} shadeX={ov} shadeLabel="overlap"
            description={`Raw weekly detections of ${reference} and ${source} and the naive splice`} />
        </div>
        <div>
          <div className="chart-title">Harmonised: {source} on the {reference} scale</div>
          <div className="chart-sub">{h.target}. Bands: 90% intervals.</div>
          {showHarm ? (
            <Chart series={harmSeries} yLabel={`${reference}-equivalent / week`} yScale="symlog" marker={marker} onPick={pick} shadeX={ov} shadeLabel="overlap"
              description={`Reference observations and converted source series with uncertainty bands`} />
          ) : (
            <div className="state">
              <h3>No harmonised series for this selection</h3>
              <p>{h.status.explanation}</p>
              {h.model && <button className="btn" onClick={() => setShowCandidate(true)}>Show the unsupported candidate as a diagnostic</button>}
            </div>
          )}
          {showCandidate && !h.status.show_harmonized && <div className="note bad small">Diagnostic view of a calibration that did not pass validation. Do not use these values.</div>}
        </div>
      </div>
      {inOv.length > 0 && showHarm && (
        <p className="small" style={{ marginTop: 8 }}>
          In the overlap shown, {reference} recorded <b className="num">{fmt(sum("ref_value"))}</b>, raw {source} <b className="num">{fmt(sum("src_raw"))}</b> (a factor of{" "}
          {fmt(sum("src_raw") / Math.max(1, sum("ref_value")), 1)}), converted {source} <b className="num">{fmt(sum("src_converted"))}</b>. The overlap fit is in-sample; held-out agreement is on the Validation tab.
        </p>
      )}

      {h.model && (
        <div className="grid-2" style={{ marginTop: 18 }}>
          <div>
            <div className="chart-title">Estimated sensor ratio through the year</div>
            <div className="chart-sub">{source} detections per {reference} detection for the same activity{h.mode === "exposure" ? " and the same number of valid looks" : ""}. Bands: 90% block-bootstrap intervals.</div>
            <Chart
              xType="linear"
              xDomain={[1, 365]}
              xTickFormat={(d) => ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Math.min(11, Math.floor((d - 1) / 30.5))]}
              yLabel="ratio"
              series={h.model.ratio_curve.map((c: any, i: number) => ({
                key: c.lc_class,
                label: c.lc_class === "all" ? "all land cover" : c.lc_class,
                color: ["var(--ink)", "var(--s-snpp)", "var(--s-noaa20)", "var(--s-aqua)", "var(--s-terra)"][i % 5],
                band: true,
                points: c.doy.map((d: number, j: number) => ({ x: d, y: c.ratio[j], lo: c.lo?.[j], hi: c.hi?.[j] })),
              }))}
              description="Sensor ratio by day of year and land cover"
            />
          </div>
          <div>
            <div className="chart-title">Model and diagnostics</div>
            <dl className="kv" style={{ marginTop: 6 }}>
              <dt>Model</dt><dd>{h.model.label}</dd>
              <dt>Mode</dt><dd>{h.mode === "exposure" ? "with valid-observation exposure" : "hotspot-only (coverage unknown)"}</dd>
              <dt>Overlap</dt><dd>{h.pair.overlap_start} to {h.pair.overlap_end}</dd>
              <dt>Paired cell-weeks</dt><dd className="num">{fmt(h.pair.n_paired_active_units)} with detections of {fmt(h.pair.n_units)}</dd>
              <dt>Seasonal order</dt><dd>{h.model.harmonic_order} (chosen by leave-year-out deviance on training data)</dd>
              <dt>Dispersion</dt><dd className="num">{fmt(h.model.dispersion_cell_week, 2)} cell-week, {fmt(h.model.dispersion_window, 2)} week totals</dd>
              <dt>Activity dependence</dt>
              <dd>{fmt(h.diagnostics.proportionality.coef_log_activity, 3)} ± {fmt(h.diagnostics.proportionality.se, 3)} {h.diagnostics.proportionality.flag ? "(flagged)" : "(not flagged)"}</dd>
              <dt>Drift per year</dt>
              <dd>{fmt(h.diagnostics.drift.log_ratio_per_year, 3)} ± {fmt(h.diagnostics.drift.se, 3)} {h.diagnostics.drift.flag ? "(flagged)" : "(not flagged)"}</dd>
              <dt>Zeros</dt>
              <dd>{h.diagnostics.zero_check.note || `${fmt(h.diagnostics.zero_check.observed_zero_fraction_ref * 100, 1)}% observed vs ${fmt(h.diagnostics.zero_check.nb_expected_zero_fraction_ref * 100, 1)}% expected under NB`}</dd>
            </dl>
            <details style={{ marginTop: 8 }}>
              <summary>Coefficients (log scale, quasi-likelihood SE)</summary>
              <table className="data">
                <thead><tr><th>Term</th><th>Estimate</th><th>SE</th></tr></thead>
                <tbody>
                  {h.model.coefficients.map((c: any) => (
                    <tr key={c.term}><td>{c.term}</td><td>{fmt(c.estimate, 3)}</td><td>{fmt(c.se_quasi, 3)}</td></tr>
                  ))}
                </tbody>
              </table>
            </details>
            <details>
              <summary>Evidence sufficiency checks</summary>
              <ul className="checklist">
                {h.sufficiency.checks.map((c: any) => (
                  <li key={c.check} className={c.passed ? "pass" : "fail"}>
                    <span className="mark">{c.passed ? "✓" : "✗"}</span>
                    {c.check}: {fmt(c.value)} (needs {fmt(c.required)})
                  </li>
                ))}
              </ul>
            </details>
          </div>
        </div>
      )}
      {!h.model && (
        <div className="note bad">
          Calibration refused.{" "}
          {h.sufficiency.checks.filter((c: any) => !c.passed).map((c: any) => `${c.check}: ${fmt(c.value)} of ${fmt(c.required)} needed`).join("; ")}.
        </div>
      )}
      <p className="small muted" style={{ marginTop: 10 }}>
        Information loss: harmonised values exist only for {h.pair.grid_km} km cell-weeks and coarser. Individual {source} detections are never relabelled as {reference} detections, and the finer 375 m
        detail stays visible in the raw layers.
      </p>
    </div>
  );
}
