import { useCallback, useEffect, useMemo, useState } from "react";
import { AnomalyView } from "./components/AnomalyView";
import { Calendar, CalendarLegend, Patterns } from "./components/Calendar";
import { Chart, toMs } from "./components/Chart";
import { CompareView, StatusChip } from "./components/CompareView";
import { DetailPanel } from "./components/DetailPanel";
import { ImportView } from "./components/ImportView";
import { MapView } from "./components/MapView";
import { MethodView } from "./components/MethodView";
import { METRICS, Rail, UiState } from "./components/Rail";
import { ValidationView } from "./components/ValidationView";
import { api, SelectionReq } from "./lib/api";
import { CalCell, harmonizedCells, rawCells, yearsBetween } from "./lib/calendar";
import { fmt, windowEnd, windowLabel } from "./lib/format";
import { useAsync, useOnline } from "./lib/hooks";
import { PLATFORM_COLOR } from "./lib/scales";

const TABS = [
  ["series", "Time series"],
  ["compare", "Raw vs harmonised"],
  ["anomalies", "Seasons and anomalies"],
  ["validation", "Validation"],
  ["method", "Data and method"],
  ["import", "Import data"],
] as const;
type Tab = (typeof TABS)[number][0];

function extentOf(meta: any, dataset: string): [string, string] | null {
  const av = meta?.datasets?.[dataset]?.availability || [];
  if (!av.length) return null;
  return [av.map((a: any) => a.date_start).sort()[0], av.map((a: any) => a.date_end).sort().slice(-1)[0]];
}

export function App() {
  const online = useOnline();
  const metaQ = useAsync(() => api.meta(), "meta");
  const [metaNonce, setMetaNonce] = useState(0);
  const meta = metaQ.data;
  const [ui, setUi] = useState<UiState | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("series");
  const [drawing, setDrawing] = useState(false);
  const [aoiError, setAoiError] = useState<string | null>(null);
  const [pickedCell, setPickedCell] = useState<any | null>(null);
  const [basemap, setBasemap] = useState(true);
  const [anomBasis, setAnomBasis] = useState("auto");
  const [job, setJob] = useState<any>(null);
  const [evalNonce, setEvalNonce] = useState(0);

  useEffect(() => {
    if (metaNonce) metaQ.reload();
  }, [metaNonce]);

  // initial state once meta arrives
  useEffect(() => {
    if (!meta || ui) return;
    const dataset: "synthetic" | "real" = meta.datasets.real.series.length && !meta.datasets.synthetic.series.length ? "real" : "synthetic";
    const ext = extentOf(meta, dataset);
    const plats = Array.from(new Set((meta.datasets[dataset].series as any[]).map((s) => s.platform)));
    const pilot = meta.presets.find((p: any) => p.pilot) || meta.presets[0];
    setUi({
      dataset,
      aoi: pilot.id,
      aoiLabel: pilot.name,
      dateStart: ext ? ext[0] : "2019-01-01",
      dateEnd: ext ? ext[1] : "2019-12-31",
      platforms: plats.length ? plats : ["Terra", "Aqua", "Suomi-NPP", "NOAA-20"],
      streams: ["SP"],
      gridKm: meta.method.grid.default_km,
      window: "week",
      minConfidence: meta.method.quality.min_confidence,
      calSeries: "harmonized",
      metric: "n_quality",
      reference: meta.method.calibration.reference_platform,
      source: meta.method.calibration.default_source_platform,
    });
  }, [meta, ui]);

  const set = useCallback((patch: Partial<UiState>) => setUi((u) => (u ? { ...u, ...patch } : u)), []);

  const switchDataset = (d: "synthetic" | "real") => {
    if (!ui || !meta) return;
    const ext = extentOf(meta, d);
    const plats = Array.from(new Set((meta.datasets[d].series as any[]).map((s) => s.platform)));
    setSelected(null);
    setPickedCell(null);
    set({ dataset: d, dateStart: ext ? ext[0] : ui.dateStart, dateEnd: ext ? ext[1] : ui.dateEnd, platforms: plats.length ? plats : ui.platforms });
  };

  const req: SelectionReq | null = useMemo(
    () =>
      ui && {
        dataset: ui.dataset,
        aoi: ui.aoi,
        date_start: ui.dateStart,
        date_end: ui.dateEnd,
        platforms: ui.platforms,
        streams: ui.streams,
        grid_km: ui.gridKm,
        window: ui.window,
        min_confidence: ui.minConfidence,
        reference: ui.reference,
        source: ui.source,
      },
    [ui],
  );
  const key = JSON.stringify(req);
  const weekReq = req && { ...req, window: "week" as const };
  const harmKey = req ? JSON.stringify({ d: req.dataset, a: req.aoi, g: req.grid_km, c: req.min_confidence, r: req.reference, s: req.source, st: req.streams, e: evalNonce, m: metaNonce }) : "";

  const hasData = meta && ui && meta.datasets[ui.dataset].series.length > 0;
  const summaryQ = useAsync(hasData && req ? () => api.summary(req) : null, key + metaNonce);
  const summaryFresh = summaryQ.dataKey === key + metaNonce;
  const pairAvailable = !!(hasData && ui && meta.datasets[ui.dataset].series.some((s: any) => s.platform === ui.reference) && meta.datasets[ui.dataset].series.some((s: any) => s.platform === ui.source));
  const harmQ = useAsync(pairAvailable && weekReq ? () => api.harmonized(weekReq) : null, harmKey);
  const evalQ = useAsync(pairAvailable && weekReq ? () => api.evaluation(weekReq) : null, harmKey);
  const anomQ = useAsync(hasData && weekReq && ui?.window === "week" ? () => api.anomalies(weekReq, anomBasis) : null, harmKey + anomBasis + req?.date_start + req?.date_end + JSON.stringify(req?.platforms));
  const batchesQ = useAsync(() => api.batches(), "b" + metaNonce);
  const artifactsQ = useAsync(() => api.artifacts().catch(() => null), "art");

  const selEnd = selected && ui ? windowEnd(selected, ui.window) : null;
  const cellsQ = useAsync(hasData && req ? () => api.cells(req, selected, selEnd) : null, key + selected + metaNonce);
  const detQ = useAsync(hasData && req && selected ? () => api.detections(req, selected, selEnd!) : null, key + selected + metaNonce);
  const winQ = useAsync(hasData && req && selected ? () => api.window(req, selected) : null, key + selected + metaNonce);

  // poll a running validation job
  useEffect(() => {
    if (!job || !job.job || job.status === "done" || job.status === "error") return;
    const t = setInterval(async () => {
      try {
        const j = await api.job(job.job);
        if (j.status === "done") {
          setJob({ ...job, status: "done" });
          setEvalNonce((n) => n + 1);
        } else if (j.status === "error") setJob({ ...job, status: "error", error: j.error });
      } catch (e: any) {
        setJob({ ...job, status: "error", error: e.message });
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job]);

  const runValidation = async () => {
    if (!weekReq) return;
    try {
      const j = await api.runEvaluation(weekReq);
      if (j.status === "done") setEvalNonce((n) => n + 1);
      setJob(j);
    } catch (e: any) {
      setJob({ status: "error", error: e.message });
    }
  };

  // ------------------------------------------------------------------ calendar cells
  const h = harmQ.data;
  const harmUsable = !!(h && h.status.show_harmonized);
  const calSeries = ui?.calSeries === "harmonized" && !(harmUsable && ui.window === "week") ? null : ui?.calSeries;
  const effectiveSeries = calSeries ?? (ui ? ui.reference : "Aqua");
  const cells: CalCell[] = useMemo(() => {
    if (!ui || !summaryQ.data || !summaryFresh) return [];
    if (effectiveSeries === "harmonized" && h) return harmonizedCells(h.series, ui.dateStart, ui.dateEnd, h.mode === "exposure");
    return rawCells(summaryQ.data.series, effectiveSeries, ui.metric, ui.window);
  }, [ui, summaryQ.data, summaryFresh, h, effectiveSeries]);
  const years = ui ? yearsBetween(ui.dateStart, ui.dateEnd) : [];
  const signals: Record<string, string> = useMemo(() => {
    const out: Record<string, string> = {};
    if (ui?.window === "week" && anomQ.data) for (const w of anomQ.data.windows) out[w.window_start] = w.signal;
    return out;
  }, [anomQ.data, ui?.window]);
  const calMax = cells.reduce((m, c) => (c.status === "detections" && (c.value ?? 0) > m ? c.value ?? 0 : m), 0);
  const unit = effectiveSeries === "harmonized" ? `${ui?.reference}-equivalent detections` : METRICS[ui?.metric || "n_quality"].toLowerCase();

  if (metaQ.error) {
    return (
      <div className="state error" style={{ padding: 40 }}>
        <h3>Ember Atlas cannot reach its server</h3>
        <p>{metaQ.error.message}</p>
        <button className="btn" onClick={metaQ.reload}>Try again</button>
      </div>
    );
  }
  if (!meta || !ui || !req) return <div className="state" style={{ padding: 40 }}>Loading Ember Atlas…</div>;

  const synthetic = ui.dataset === "synthetic";
  const availablePlatforms = Array.from(new Set((meta.datasets[ui.dataset].series as any[]).map((s) => s.platform)));
  const harmRow = selected && h ? h.series.find((r: any) => r.window_start === selected) : null;
  const sigRow = selected && anomQ.data ? anomQ.data.windows.find((w: any) => w.window_start === selected) : null;
  const seriesTitle =
    effectiveSeries === "harmonized"
      ? `${ui.reference}-equivalent burning activity`
      : effectiveSeries === "__pooled__"
        ? "All platforms pooled (raw, not comparable)"
        : `${effectiveSeries} ${ui.metric === "active_cells" ? "active cells" : "detections"}`;

  return (
    <div className="app">
      <Patterns />
      <header className="topbar">
        <div className="wordmark">
          <span className="glyph" aria-hidden="true" />
          Ember Atlas
          <small>MODIS and VIIRS hotspot calendar</small>
        </div>
        <div className="dataset-switch" role="group" aria-label="Dataset">
          <button className="syn" aria-pressed={synthetic} onClick={() => switchDataset("synthetic")}>Synthetic demo</button>
          <button aria-pressed={!synthetic} onClick={() => switchDataset("real")}>Imported real data</button>
        </div>
        <span className="spacer" />
        {h && <StatusChip status={h.status} />}
      </header>
      {synthetic ? (
        <div className="synthetic-band" role="status">
          Synthetic demo data, not NASA measurements
          <span>Generated with a known sensor effect, seasonality and coverage gaps to test the method. Every export is labelled SYNTHETIC.</span>
        </div>
      ) : (
        <div className="real-band">Imported real data. Results depend on what was imported; see Data and method for provenance.</div>
      )}
      {!online && <div className="offline-band" role="status">You are offline. Basemap tiles and FIRMS downloads are unavailable; analysis against a local server still works.</div>}

      <div className="layout">
        <Rail ui={ui} set={(p) => { set(p); if (p.aoi !== undefined || p.dataset) { setSelected(null); setPickedCell(null); } }} meta={meta} availablePlatforms={availablePlatforms}
          onStartDraw={() => setDrawing(true)} drawing={drawing} aoiError={aoiError} setAoiError={setAoiError} extent={extentOf(meta, ui.dataset)} />
        <main className="main" id="main">
          {!hasData ? (
            <section className="panel state">
              <h3>No {synthetic ? "synthetic" : "real"} data imported yet</h3>
              {synthetic ? (
                <p>Load the deterministic synthetic demo from a terminal: <code>ember seed-demo</code>, then reload this page.</p>
              ) : (
                <p>Import a FIRMS CSV or fetch from the FIRMS API on the Import data tab. docs/real_data_procedure.md describes a small historical overlap sample to start with.</p>
              )}
              <button className="btn" onClick={() => setTab("import")}>Go to Import data</button>
            </section>
          ) : (
            <section aria-labelledby="cal-title">
              <div className="calendar-head">
                <div>
                  <h1 id="cal-title">{seriesTitle}</h1>
                  <div className="basis">
                    {ui.aoiLabel}, {ui.dateStart} to {ui.dateEnd}, {ui.window === "week" ? "weekly" : "daily"}, {ui.gridKm} km equal-area grid.{" "}
                    {effectiveSeries === "harmonized" && h ? h.target + "." : effectiveSeries === "__pooled__" ? "Pooling sums sensors with different sensitivity and double-counts overlapping overpasses; shown only as a baseline." : "One sensor, unharmonised."}
                  </div>
                </div>
                <span className="spacer" style={{ flex: 1 }} />
                <div className="row" style={{ flex: "none", gap: 8 }}>
                  <select aria-label="Calendar series" value={ui.calSeries} onChange={(e) => set({ calSeries: e.target.value })} style={{ width: 230 }}>
                    <option value="harmonized">Harmonised ({ui.reference} scale)</option>
                    {ui.platforms.map((p) => <option key={p} value={p}>{p} raw</option>)}
                    <option value="__pooled__">All platforms pooled (raw)</option>
                  </select>
                  {effectiveSeries !== "harmonized" && (
                    <select aria-label="Metric" value={ui.metric} onChange={(e) => set({ metric: e.target.value })} style={{ width: 210 }}>
                      {Object.entries(METRICS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  )}
                </div>
              </div>
              {ui.calSeries === "harmonized" && effectiveSeries !== "harmonized" && (
                <div className="note warn small">
                  {ui.window === "day"
                    ? "Harmonised values exist only for weeks (daily cell counts are too sparse to calibrate). Showing the reference platform's raw daily counts instead."
                    : harmQ.loading
                      ? "Fitting the calibration… showing the reference platform's raw counts meanwhile."
                      : h
                        ? `Harmonised series not shown: ${h.status.explanation} Showing ${ui.reference} raw counts.`
                        : `Harmonisation needs both ${ui.reference} and ${ui.source} data. Showing ${ui.reference} raw counts.`}
                </div>
              )}
              {summaryQ.error && <div className="state error">{summaryQ.error.message} <button className="btn" onClick={summaryQ.reload}>Retry</button></div>}
              {summaryQ.loading && !summaryFresh && <div className="state">Aggregating detections to the {ui.gridKm} km grid…</div>}
              {summaryQ.data && summaryFresh && (
                <>
                  <Calendar cells={cells} years={years} window={ui.window} selected={selected} onSelect={setSelected} signals={signals} unit={unit} />
                  <CalendarLegend unit={unit} max={calMax} showConverted={effectiveSeries === "harmonized"} showAnomalies={ui.window === "week"} />
                  {summaryQ.data.aoi.warnings?.length > 0 && <p className="small muted">{summaryQ.data.aoi.warnings.join(" ")}</p>}
                </>
              )}
            </section>
          )}

          {hasData && (
            <div className="split">
              <section className="panel" aria-label="Map">
                <div className="panel-head">
                  <h3>{selected ? `Map: ${windowLabel(selected, ui.window)}` : "Map: whole period"}</h3>
                  <span className="spacer" />
                  <label className="small" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                    <input type="checkbox" checked={basemap && online} disabled={!online} onChange={(e) => setBasemap(e.target.checked)} /> Basemap
                  </label>
                  {selected && <button className="btn" onClick={() => setSelected(null)}>Show whole period</button>}
                </div>
                {cellsQ.error && <p className="state error">{cellsQ.error.message}</p>}
                <MapView
                  aoi={summaryQ.data?.aoi.geometry || null}
                  cells={cellsQ.data}
                  detections={detQ.data}
                  platforms={ui.platforms}
                  colorBy={effectiveSeries === "harmonized" ? ui.reference : effectiveSeries}
                  basemap={basemap && online}
                  drawing={drawing}
                  onDrawn={async (ring) => {
                    setDrawing(false);
                    const geom = { type: "Polygon", coordinates: [[...ring, ring[0]]] };
                    try {
                      const v = await api.validateAoi(geom);
                      setAoiError(v.warnings?.length ? v.warnings.join(" ") : null);
                      set({ aoi: geom, aoiLabel: `Drawn area (${Math.round(v.area_km2).toLocaleString("en-US")} km²)` });
                      setSelected(null);
                    } catch (e: any) {
                      setAoiError(e.message);
                    }
                  }}
                  onCancelDraw={() => setDrawing(false)}
                  onCellPick={setPickedCell}
                  pickedCell={pickedCell?.cell_id || null}
                  caption={`Map of ${ui.aoiLabel} with ${ui.gridKm} km cells coloured by quality detections`}
                />
                <p className="map-note">
                  Cells: quality detections of {effectiveSeries === "__pooled__" ? "all selected platforms" : effectiveSeries === "harmonized" ? ui.reference : effectiveSeries}
                  {selected ? " in the selected period" : " over the whole period"}. {selected && detQ.data ? `${fmt(detQ.data.total)} detections shown as dots, or as their pixel footprint when zoomed in${detQ.data.total > detQ.data.features.length ? ` (first ${fmt(detQ.data.features.length)})` : ""}. Faded: below the quality filter.` : "Select a period to see individual detections."}
                </p>
                <div className="legend">
                  {ui.platforms.map((p) => (
                    <span className="item" key={p}><span className="swatch" style={{ background: PLATFORM_COLOR[p] }} />{p}</span>
                  ))}
                </div>
              </section>
              <DetailPanel
                windowStart={selected}
                window={ui.window}
                detail={winQ.data}
                loading={winQ.loading}
                error={winQ.error}
                harmRow={harmUsable ? harmRow : null}
                harmStatus={h?.status}
                harmTarget={h?.target}
                signal={sigRow}
                pickedCell={pickedCell}
                platforms={ui.platforms}
                gridKm={ui.gridKm}
                onUseCell={(c) => {
                  const f = cellsQ.data.features.find((x: any) => x.properties.cell_id === c.cell_id);
                  if (f) {
                    set({ aoi: f.geometry, aoiLabel: `Cell ${c.cell_id}` });
                    setPickedCell(null);
                  }
                }}
              />
            </div>
          )}

          <section aria-label="Analysis">
            <div className="tabs" role="tablist">
              {TABS.map(([id, label]) => (
                <button key={id} role="tab" aria-selected={tab === id} aria-controls={"tab-" + id} id={"tabbtn-" + id} onClick={() => setTab(id)}>
                  {label}
                </button>
              ))}
              <span className="spacer" style={{ flex: 1 }} />
              {hasData && (
                <span className="row" style={{ flex: "none", gap: 6 }}>
                  <ExportMenu req={req} />
                </span>
              )}
            </div>
            <div className="tabpanel" role="tabpanel" id={"tab-" + tab} aria-labelledby={"tabbtn-" + tab}>
              {tab === "series" && hasData && summaryQ.data && (
                <SeriesTab summary={summaryQ.data} h={harmUsable ? h : null} ui={ui} selected={selected} onPick={setSelected} />
              )}
              {tab === "compare" && (pairAvailable ? (
                <CompareView h={h} loading={harmQ.loading} error={harmQ.error} view={[ui.dateStart, ui.dateEnd]} selected={selected} onPick={setSelected}
                  reference={ui.reference} source={ui.source} onGoValidation={() => { setTab("validation"); runValidation(); }} />
              ) : <div className="state">Harmonisation needs both {ui.reference} and {ui.source} data in this dataset.</div>)}
              {tab === "anomalies" && (ui.window === "week" ? (
                <AnomalyView a={anomQ.data} loading={anomQ.loading} error={anomQ.error} basis={anomBasis} setBasis={setAnomBasis} platforms={availablePlatforms}
                  onPick={setSelected} selectedYear={selected ? Number(selected.slice(0, 4)) : Number(ui.dateEnd.slice(0, 4))} />
              ) : <div className="state">Seasonal baselines and anomaly tests use weekly windows. <button className="btn" onClick={() => set({ window: "week" })}>Switch to weekly</button></div>)}
              {tab === "validation" && (
                <ValidationView ev={evalQ.data} loading={evalQ.loading} error={evalQ.error} job={job} onRun={runValidation} artifacts={artifactsQ.data} synthetic={synthetic} />
              )}
              {tab === "method" && <MethodView meta={meta} dataset={ui.dataset} harm={h} summaryMeta={summaryQ.data?.meta} gridKm={ui.gridKm} batches={batchesQ.data} />}
              {tab === "import" && <ImportView aoi={ui.aoi} products={meta.products} onImported={() => setMetaNonce((n) => n + 1)} />}
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

function ExportMenu({ req }: { req: SelectionReq }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const options: [string, string, "csv" | "geojson"][] = [
    ["series", "Calendar series (CSV)", "csv"],
    ["harmonized", "Harmonised series with intervals (CSV)", "csv"],
    ["anomalies", "Anomaly signals (CSV)", "csv"],
    ["cells", "Grid cells (GeoJSON)", "geojson"],
    ["detections", "Detections (GeoJSON)", "geojson"],
    ["detections", "Detections (CSV)", "csv"],
  ];
  return (
    <>
      <select
        aria-label="Export"
        value=""
        disabled={busy}
        onChange={async (e) => {
          const [content, format] = e.target.value.split("|");
          if (!content) return;
          setBusy(true);
          setErr(null);
          try {
            await api.exportFile(content === "harmonized" || content === "anomalies" ? { ...req, window: "week" } : req, format as any, content);
          } catch (x: any) {
            setErr(x.message);
          } finally {
            setBusy(false);
          }
        }}
        style={{ width: 230 }}
      >
        <option value="">{busy ? "Preparing export…" : "Export with provenance…"}</option>
        {options.map(([c, l, f]) => <option key={l} value={`${c}|${f}`}>{l}</option>)}
      </select>
      {err && <span className="small" role="alert" style={{ color: "var(--bad)" }}>{err}</span>}
    </>
  );
}

function SeriesTab({ summary, h, ui, selected, onPick }: { summary: any; h: any | null; ui: UiState; selected: string | null; onPick: (ws: string) => void }) {
  const [scale, setScale] = useState<"symlog" | "linear">("symlog");
  const rows = summary.series as any[];
  const series = ui.platforms.map((p) => ({
    key: p,
    label: `${p} raw`,
    color: PLATFORM_COLOR[p],
    width: 1.3,
    points: rows.filter((r) => r.platform === p).map((r) => ({ x: toMs(r.window_start), y: r.status === "not_queried" ? null : r[ui.metric] })),
  }));
  if (h && ui.window === "week") {
    series.push({
      key: "h",
      label: `Harmonised (${ui.reference} scale)`,
      color: "var(--harm)",
      width: 2,
      band: true,
      points: h.series.filter((r: any) => r.window_start >= ui.dateStart && r.window_start <= ui.dateEnd).map((r: any) => ({ x: toMs(r.window_start), y: r.harmonized, lo: r.harm_lo, hi: r.harm_hi })),
    } as any);
  }
  return (
    <div>
      <div className="row" style={{ maxWidth: 420, marginBottom: 8 }}>
        <span className="muted small" style={{ flex: 2 }}>{METRICS[ui.metric]} per {ui.window}; click to select a period.</span>
        <div className="seg" role="group" aria-label="Vertical scale" style={{ flex: 1 }}>
          <button aria-pressed={scale === "symlog"} onClick={() => setScale("symlog")}>Log</button>
          <button aria-pressed={scale === "linear"} onClick={() => setScale("linear")}>Linear</button>
        </div>
      </div>
      <Chart series={series} yLabel={ui.metric === "frp_sum_mw" ? "MW" : "per " + ui.window} yScale={scale} height={280} marker={selected ? toMs(selected) : null}
        onPick={(x) => onPick(new Date(x).toISOString().slice(0, 10))} description="Time series of raw platform counts and the harmonised series" />
      {ui.metric === "frp_sum_mw" && <p className="small muted">FRP sums are shown per sensor and are not harmonised: pixel FRP depends on pixel size and saturation.</p>}
    </div>
  );
}
