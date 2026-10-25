import { useState } from "react";
import { fmt, windowLabel } from "../lib/format";
import { Chart } from "./Chart";

interface Props {
  a: any | null;
  loading: boolean;
  error: Error | null;
  basis: string;
  setBasis: (b: string) => void;
  platforms: string[];
  onPick: (ws: string) => void;
  selectedYear: number;
}

export function AnomalyView({ a, loading, error, basis, setBasis, platforms, onPick, selectedYear }: Props) {
  const [year, setYear] = useState<number | null>(null);
  const yr = year ?? selectedYear;
  return (
    <div>
      <div className="row" style={{ maxWidth: 560, marginBottom: 10 }}>
        <label htmlFor="basis" style={{ flex: "none", fontWeight: 600 }}>Series</label>
        <select id="basis" value={basis} onChange={(e) => setBasis(e.target.value)}>
          <option value="auto">Harmonised if supported, otherwise the reference platform</option>
          {platforms.map((p) => (
            <option key={p} value={"platform:" + p}>{p} only (unharmonised, one consistent sensor)</option>
          ))}
        </select>
      </div>
      {error && <div className="state error"><h3>Anomaly analysis unavailable</h3>{error.message}</div>}
      {loading && !a && <div className="state">Building same-season baselines from the other years…</div>}
      {a && <AnomalyBody a={a} yr={yr} setYear={setYear} onPick={onPick} />}
    </div>
  );
}

function AnomalyBody({ a, yr, setYear, onPick }: { a: any; yr: number; setYear: (y: number) => void; onPick: (ws: string) => void }) {
  const curve = a.seasonal_curve as any[];
  const enough = curve.some((c) => c.median !== undefined);
  const years: number[] = Array.from(new Set((a.annual as any[]).map((r) => r.year)));
  const thisYear = (a.windows as any[]).filter((w) => w.year === yr);
  const weekX = (w: number) => w * 7 + 4;
  return (
    <>
      <p className="muted small">
        Series: {a.basis.label}. {a.notes.join(" ")}
      </p>
      {!enough && (
        <div className="note warn">
          Not enough history: a seasonal baseline needs at least 5 other observed years for each week.
          Unobserved weeks are not counted as zero, so short imports cannot produce anomaly signals.
        </div>
      )}
      <div className="grid-2">
        <div>
          <div className="row" style={{ maxWidth: 360 }}>
            <div className="chart-title" style={{ flex: "none" }}>Seasonal baseline and</div>
            <select aria-label="Year to compare" value={yr} onChange={(e) => setYear(Number(e.target.value))}>
              {years.map((y) => (
                <option key={y}>{y}</option>
              ))}
            </select>
          </div>
          <div className="chart-sub">Weekly values of all years: median, middle 50% and 10–90% range.</div>
          <Chart
            xType="linear"
            xDomain={[1, 365]}
            xTickFormat={(d) => ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Math.min(11, Math.floor((d - 1) / 30.5))]}
            yLabel="per week"
            series={[
              { key: "q1090", label: "10–90% of years", color: "var(--slate)", width: 0, band: true, points: curve.map((c) => ({ x: weekX(c.week), y: null, lo: c.q10 ?? null, hi: c.q90 ?? null })) },
              { key: "iqr", label: "middle 50%", color: "var(--slate)", width: 0, band: true, points: curve.map((c) => ({ x: weekX(c.week), y: null, lo: c.q25 ?? null, hi: c.q75 ?? null })) },
              { key: "med", label: "median", color: "var(--slate)", dash: "4 3", points: curve.map((c) => ({ x: weekX(c.week), y: c.median ?? null })) },
              { key: "yr", label: String(yr), color: "var(--heat-2)", width: 2.2, points: thisYear.map((w) => ({ x: weekX(w.week), y: w.value })) },
            ]}
            onPick={(x) => {
              const w = thisYear.find((t) => weekX(t.week) === x);
              if (w) onPick(w.window_start);
            }}
            description={`Seasonal baseline with year ${yr}`}
          />
        </div>
        <div>
          <div className="chart-title">Year-to-year totals</div>
          <div className="chart-sub">Sum of weekly values; whiskers: approximate 90% interval (independent weekly errors assumed). Pale bars are incomplete years.</div>
          <Chart
            xType="linear"
            yLabel="per year"
            xTickFormat={(v) => String(Math.round(v))}
            series={[
              { key: "c", label: "complete years", color: "var(--heat-2)", bars: true, points: a.annual.filter((r: any) => r.complete).map((r: any) => ({ x: r.year, y: r.total, lo: r.lo, hi: r.hi })) },
              { key: "i", label: "incomplete years", color: "var(--heat-0)", bars: true, points: a.annual.filter((r: any) => !r.complete).map((r: any) => ({ x: r.year, y: r.total, lo: r.lo, hi: r.hi })) },
            ]}
            onPick={(x) => setYear(x)}
            description="Annual totals"
          />
        </div>
      </div>

      <h3 style={{ marginTop: 16 }}>Critical periods</h3>
      {a.peak_season ? (
        <p>
          Typical peak season: weeks starting around {weekToDate(a.peak_season.weeks[0])} to {weekToDate(a.peak_season.weeks[a.peak_season.weeks.length - 1])}, peaking near{" "}
          {weekToDate(a.peak_season.peak_week)}. <span className="muted small">{a.peak_season.explanation}</span>
        </p>
      ) : (
        <p className="muted">No climatological peak season can be estimated from this record.</p>
      )}
      {a.episodes.length === 0 ? (
        <p className="muted">No unusual episodes in this view after controlling the false discovery rate over {a.n_tests} tests.</p>
      ) : (
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr><th>Episode</th><th>Kind</th><th>Weeks</th><th>Peak ratio to median</th><th>Excess vs median</th><th>Smallest q</th></tr>
            </thead>
            <tbody>
              {a.episodes.map((e: any) => (
                <tr key={e.start} style={{ cursor: "pointer" }} onClick={() => onPick(e.start)}>
                  <td>{windowLabel(e.start, "week")}</td>
                  <td>{e.kind === "unusually_high" ? "Unusually high" : "Unusually low"}</td>
                  <td>{e.n_weeks}</td>
                  <td>{fmt(e.peak_ratio_to_median, 1)}×</td>
                  <td>{fmt(e.excess_vs_median)}</td>
                  <td>{e.min_q != null ? e.min_q.toPrecision(2) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted" style={{ marginTop: 8 }}>
        These are historical signals relative to other years of the same record. They are not predictions of future fire risk, which would need weather, fuel and ignition data and a separate prospective validation.
        Low-activity signals are only tested where valid-observation coverage is known.
      </p>
    </>
  );
}

function weekToDate(w: number): string {
  const d = new Date(Date.UTC(2001, 0, 1 + w * 7));
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

