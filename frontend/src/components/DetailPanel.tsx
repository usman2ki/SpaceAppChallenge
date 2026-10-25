import { Fragment } from "react";
import { fmt, fmtRange, pct, windowLabel } from "../lib/format";
import { PLATFORM_COLOR, SIGNAL_TEXT, STATUS_TEXT } from "../lib/scales";

interface Props {
  windowStart: string | null;
  window: "day" | "week";
  detail: any | null;
  loading: boolean;
  error: Error | null;
  harmRow: any | null;
  harmStatus: any | null;
  harmTarget: string | null;
  signal: any | null;
  pickedCell: any | null;
  platforms: string[];
  gridKm: number;
  onUseCell: (cellProps: any) => void;
}

export function DetailPanel({ windowStart, window, detail, loading, error, harmRow, harmStatus, harmTarget, signal, pickedCell, platforms, gridKm, onUseCell }: Props) {
  if (!windowStart) {
    return (
      <section className="panel" aria-label="Selected period">
        <h3>Selected period</h3>
        <p className="muted" style={{ marginTop: 6 }}>
          Choose a {window === "week" ? "week" : "day"} in the calendar or the time series to see per-sensor counts, coverage, provenance and uncertainty here.
          The map then shows that period's detections at their pixel footprint.
        </p>
      </section>
    );
  }
  return (
    <section className="panel" aria-label="Selected period" aria-busy={loading}>
      <div className="panel-head">
        <h3>{windowLabel(windowStart, window)}</h3>
      </div>
      {error && <p className="state error">{error.message}</p>}
      {loading && !detail && <p className="muted">Loading period details…</p>}
      {detail && (
        <>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>Platform</th>
                  <th title="All stored detections">All</th>
                  <th title="Quality-filtered detections">Quality</th>
                  <th title={`Distinct ${gridKm} km cells with a quality detection`}>Cells</th>
                  <th title="Sum of pixel fire radiative power, quality detections">FRP MW</th>
                  <th title="Valid-observation share of nominal looks">Observed</th>
                </tr>
              </thead>
              <tbody>
                {detail.platforms.map((r: any) => (
                  <tr key={r.platform}>
                    <td>
                      <span className="swatch" style={{ background: PLATFORM_COLOR[r.platform], marginRight: 6 }} />
                      {r.platform}
                    </td>
                    <td>{r.status === "not_queried" ? "–" : fmt(r.n_raw)}</td>
                    <td>{r.status === "not_queried" ? "–" : fmt(r.n_quality)}</td>
                    <td>{r.status === "not_queried" ? "–" : fmt(r.active_cells)}</td>
                    <td>{r.frp_sum_mw == null ? "–" : fmt(r.frp_sum_mw)}</td>
                    <td>{r.coverage_known ? pct(r.valid_fraction) : "unknown"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="small muted" style={{ paddingLeft: 18, margin: "8px 0" }}>
            {detail.platforms
              .filter((r: any) => r.status !== "detections")
              .map((r: any) => (
                <li key={r.platform}>
                  {r.platform}: {STATUS_TEXT[r.status] || r.status}
                </li>
              ))}
            {detail.static_or_offshore_excluded > 0 && <li>{detail.static_or_offshore_excluded} static-source/offshore detections excluded from quality counts.</li>}
          </ul>
          <p className="small muted">FRP sums depend on pixel size and saturation behaviour, so they are compared within a sensor only. None of these numbers is burned area or a count of separate fires.</p>
        </>
      )}

      {window === "week" && harmRow && (
        <div className="note" style={{ marginTop: 10 }}>
          <b>Harmonised estimate: {fmt(harmRow.harmonized, 1)}</b>{" "}
          {harmRow.harm_lo != null && <span className="num">(90% interval {fmtRange(harmRow.harm_lo, harmRow.harm_hi, 1)})</span>}
          <div className="small muted">
            {harmTarget}. Basis: {harmRow.basis === "reference_observed" ? "reference platform observed directly" : harmRow.basis.replace("converted_from_", "converted from ")}
            {harmRow.transfer_outside_overlap ? "; outside the calibration overlap (transfer assumes a stable sensor ratio)" : ""}.
            {harmStatus && ` Status: ${harmStatus.label}.`}
          </div>
        </div>
      )}
      {window === "week" && signal && signal.signal && (
        <div className={"note" + (signal.signal === "unusually_high" || signal.signal === "unusually_low" ? " warn" : "")}>
          <b>{SIGNAL_TEXT[signal.signal] || signal.signal}</b>
          {signal.baseline_median != null && (
            <div className="small muted">
              Same-season median of {signal.n_baseline_years} other years: {fmt(signal.baseline_median, 1)} (IQR {fmtRange(signal.baseline_q25, signal.baseline_q75, 1)}).
              {signal.q_excess != null && ` Excess test q = ${signal.q_excess.toPrecision(2)}.`}
              {signal.low_test && ` Low-activity test: ${signal.low_test}.`}
            </div>
          )}
        </div>
      )}

      {detail && detail.provenance_rows.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary>Provenance of these detections</summary>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Product</th>
                  <th>Stream</th>
                  <th>Version</th>
                  <th>Rows</th>
                </tr>
              </thead>
              <tbody>
                {detail.provenance_rows.map((p: any, i: number) => (
                  <tr key={i}>
                    <td title={p.source_url || ""}>{p.file_name}</td>
                    <td>{p.source_product}</td>
                    <td>{p.processing_stream}</td>
                    <td>{p.version_raw}</td>
                    <td>{fmt(p.detections)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {pickedCell && (
        <div style={{ marginTop: 12, borderTop: "1px solid var(--rule)", paddingTop: 10 }}>
          <h3>Cell {pickedCell.cell_id}</h3>
          <dl className="kv" style={{ marginTop: 6 }}>
            {platforms.map((p) => (
              <Fragment key={p}>
                <dt>{p}</dt>
                <dd className="num">
                  {fmt(pickedCell["q_" + p] ?? 0)} quality / {fmt(pickedCell["raw_" + p] ?? 0)} all
                </dd>
              </Fragment>
            ))}
            <dt>Inside the area</dt>
            <dd>{pct(pickedCell.aoi_fraction)} of the cell</dd>
          </dl>
          <button className="btn" style={{ marginTop: 8 }} onClick={() => onUseCell(pickedCell)}>
            Analyse this cell only
          </button>
        </div>
      )}
    </section>
  );
}
