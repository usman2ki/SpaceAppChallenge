import { fmt } from "../lib/format";
import { StatusChip } from "./CompareView";

interface Props {
  meta: any;
  dataset: "synthetic" | "real";
  harm: any | null;
  summaryMeta: any | null;
  gridKm: number;
  batches: any[] | null;
}

export function MethodView({ meta, dataset, harm, summaryMeta, gridKm, batches }: Props) {
  const ds = meta?.datasets?.[dataset];
  const m = meta?.method;
  return (
    <div>
      <div className="method-section">
        <h3>What the numbers are</h3>
        <p>
          Every value counts <b>active-fire detections</b>: satellite pixels flagged as containing a fire at overpass time. A detection is not a fire and not burned area. One large fire
          produces many 375 m VIIRS detections but fewer 1 km MODIS detections; a small fire may be seen by VIIRS only. Active-cell counts (distinct {gridKm} km equal-area cells with a
          detection) are less sensitive to pixel size. FRP sums are compared within one sensor only.
        </p>
      </div>
      <div className="method-section">
        <h3>Resolution and common grid</h3>
        <p>
          Detections keep their native position and pixel footprint. Aggregates use EASE-Grid 2.0 equal-area cells ({(meta?.method?.grid?.allowed_km || []).join(", ")} km) and UTC days or
          7-day weeks anchored on 1 January. The default {m?.grid?.default_km} km grid is several times the largest MODIS pixel near the scan edge, so a fire seen by both sensors usually
          falls in the same cell. Finer grids would claim more precision than the 1 km sensor supports.
        </p>
      </div>
      <div className="method-section">
        <h3>Observation coverage</h3>
        {ds?.coverage_available ? (
          <p>
            A valid-observation coverage table is loaded ({ds.coverage_set?.name}). Zero-detection periods are classified as observed, partly observed or not observed, and the
            calibration uses valid looks as exposure.{dataset === "synthetic" ? " This coverage is synthetic." : ""}
          </p>
        ) : (
          <p>
            <b>No coverage data is loaded for this dataset.</b> Hotspot files list detections only; they cannot show whether an empty place was observed (clouds, swath gaps, outages).
            Empty periods are therefore shown as "coverage unknown", low-activity anomaly tests are disabled, and any calibration is labelled a research estimate because coverage
            differences between sensors are absorbed into the ratio.
          </p>
        )}
      </div>
      <div className="method-section">
        <h3>Calibration status</h3>
        {harm ? (
          <>
            <p><StatusChip status={harm.status} /></p>
            <p className="small">{harm.status.explanation}</p>
          </>
        ) : (
          <p className="muted">Open the Raw vs harmonised tab to fit a calibration for this area.</p>
        )}
        <p>
          Model: for each cell-week seen by both platforms, the share of detections coming from the source sensor follows a binomial model whose log-odds is the log sensor ratio
          (plus a log ratio of valid looks when coverage exists). The shared, unknown fire activity cancels out exactly. The ratio may vary with season (Fourier terms, order chosen by
          leave-year-out deviance) and land cover. Uncertainty comes from a block bootstrap over spatial blocks × 4-week periods plus overdispersed sampling noise.
        </p>
        <p>
          Assumptions: both platforms see the same underlying activity in a cell-week; the ratio is stable outside the overlap years (checked for drift inside them); land-cover classes
          not seen in calibration use the baseline class. A harmonised value is labelled "validated" only for real data with coverage exposure that passes blocked hold-out gates, and even
          then it means agreement with the reference sensor, not ground truth.
        </p>
      </div>
      <div className="method-section">
        <h3>Limitations</h3>
        <ul>
          <li>No independent fire reference data are used; validation measures agreement between sensors.</li>
          <li>Transfer to years without overlap assumes a stationary sensor ratio. Terra and Aqua orbits drifted late in their missions, which can shift overpass times.</li>
          <li>Standard and near-real-time records are kept separate; NRT lacks the static-source flag, so industrial heat sources cannot be removed from NRT counts.</li>
          <li>Coverage on a coarser grid than the analysis grid is assumed uniform inside each coverage cell.</li>
          <li>Anomaly signals are historical and FDR-controlled; they are not forecasts.</li>
        </ul>
      </div>

      <h3>Data in this dataset</h3>
      {ds?.series?.length ? (
        <div className="table-scroll">
          <table className="data">
            <thead><tr><th>Platform</th><th>Product</th><th>Stream</th><th>Versions</th><th>First</th><th>Last</th><th>Detections</th></tr></thead>
            <tbody>
              {ds.series.map((s: any) => (
                <tr key={s.platform + s.processing_stream}>
                  <td>{s.platform}</td><td>{s.source_product}</td><td>{s.processing_stream}</td><td>{s.versions.join(", ")}</td><td>{s.first_date}</td><td>{s.last_date}</td><td>{fmt(s.detections)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">Nothing imported yet.</p>
      )}
      {summaryMeta && summaryMeta.not_gridded > 0 && <p className="small">{fmt(summaryMeta.not_gridded)} detections are poleward of the equal-area grid and are not aggregated.</p>}
      {batches && batches.filter((b) => b.data_origin === dataset).length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>Import batches</h3>
          <div className="table-scroll">
            <table className="data">
              <thead><tr><th>#</th><th>File</th><th>Status</th><th>Read</th><th>Inserted</th><th>Rejected</th><th>Duplicates</th><th>Conflicts</th><th>Imported</th></tr></thead>
              <tbody>
                {batches.filter((b) => b.data_origin === dataset).map((b) => (
                  <tr key={b.id}>
                    <td>{b.id}</td>
                    <td title={b.file_sha256}>{b.file_name}</td>
                    <td>{b.status}</td>
                    <td>{fmt(b.summary?.rows_read)}</td>
                    <td>{fmt(b.summary?.inserted)}</td>
                    <td>{fmt(b.summary?.rows_rejected)}</td>
                    <td>{fmt((b.summary?.duplicates_in_file || 0) + (b.summary?.duplicates_existing || 0))}</td>
                    <td>{fmt((b.summary?.conflicts_in_file || 0) + (b.summary?.conflicts_existing || 0))}</td>
                    <td>{b.imported_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <h3 style={{ marginTop: 14 }}>Sources</h3>
      <ul className="small">
        {meta &&
          Object.entries(meta.docs).map(([k, v]: [string, any]) => (
            <li key={k}>
              <a href={v} target="_blank" rel="noreferrer">{k.replace(/_/g, " ")}</a>
            </li>
          ))}
      </ul>
      <p className="small muted">Documentation checked {meta?.docs_retrieved}. Method {m?.method_version} (fingerprint {meta?.method_fingerprint}).</p>
    </div>
  );
}
