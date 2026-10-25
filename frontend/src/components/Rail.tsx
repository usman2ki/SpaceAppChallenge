import { useState } from "react";
import { api } from "../lib/api";
import { PLATFORM_COLOR, PLATFORM_INSTRUMENT } from "../lib/scales";

export interface UiState {
  dataset: "synthetic" | "real";
  aoi: any; // preset id or GeoJSON geometry
  aoiLabel: string;
  dateStart: string;
  dateEnd: string;
  platforms: string[];
  streams: string[] | null;
  gridKm: number;
  window: "day" | "week";
  minConfidence: string;
  calSeries: string; // "harmonized" | "__pooled__" | platform
  metric: string;
  reference: string;
  source: string;
}

interface Props {
  ui: UiState;
  set: (patch: Partial<UiState>) => void;
  meta: any;
  availablePlatforms: string[];
  onStartDraw: () => void;
  drawing: boolean;
  aoiError: string | null;
  setAoiError: (e: string | null) => void;
  extent: [string, string] | null;
}

export const METRICS: Record<string, string> = {
  n_quality: "Quality-filtered detections",
  n_raw: "All detections (unfiltered)",
  active_cells: "Active equal-area cells",
  frp_sum_mw: "FRP sum (MW, per sensor only)",
};

export function Rail({ ui, set, meta, availablePlatforms, onStartDraw, drawing, aoiError, setAoiError, extent }: Props) {
  const [bbox, setBbox] = useState("");
  const [open, setOpen] = useState(false); // only matters on narrow screens
  const presets = meta?.presets || [];
  const allowedGrids: number[] = meta?.method?.grid?.allowed_km || [9, 18, 36, 72];

  async function applyGeometry(geom: any, label: string) {
    try {
      const v = await api.validateAoi(geom);
      setAoiError(v.warnings?.length ? v.warnings.join(" ") : null);
      set({ aoi: geom, aoiLabel: `${label} (${Math.round(v.area_km2).toLocaleString("en-US")} km²)` });
    } catch (e: any) {
      setAoiError(e.message);
    }
  }

  async function onUpload(file: File) {
    try {
      const obj = JSON.parse(await file.text());
      await applyGeometry(obj, file.name.replace(/\.(geo)?json$/i, ""));
    } catch (e: any) {
      setAoiError(e instanceof SyntaxError ? "That file is not valid GeoJSON." : e.message);
    }
  }

  const presetValue = typeof ui.aoi === "string" ? ui.aoi : "__custom__";
  return (
    <aside className={"rail" + (open ? " rail-open" : "")} aria-label="Analysis controls">
      <button className="btn rail-toggle" aria-expanded={open} aria-controls="rail-body" onClick={() => setOpen(!open)}>
        {open ? "Hide area, dates and filters" : `Area, dates and filters: ${ui.aoiLabel}`}
      </button>
      <div id="rail-body" className="rail-body">
      <div className="field">
        <label htmlFor="aoi-preset">Area of interest</label>
        <select
          id="aoi-preset"
          value={presetValue}
          onChange={(e) => {
            const p = presets.find((x: any) => x.id === e.target.value);
            if (p) {
              setAoiError(null);
              set({ aoi: p.id, aoiLabel: p.name });
            }
          }}
        >
          {presetValue === "__custom__" && <option value="__custom__">{ui.aoiLabel}</option>}
          {presets.map((p: any) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <div className="btn-row" style={{ marginTop: 8 }}>
          <button className="btn" onClick={onStartDraw} disabled={drawing} aria-pressed={drawing}>
            {drawing ? "Drawing on map…" : "Draw on map"}
          </button>
          <label className="btn" style={{ display: "inline-block" }}>
            Upload GeoJSON
            <input type="file" accept=".json,.geojson,application/geo+json" className="sr-only" onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <input aria-label="Bounding box: west, south, east, north" placeholder="west, south, east, north" value={bbox} onChange={(e) => setBbox(e.target.value)} />
          <button
            className="btn"
            style={{ flex: "none" }}
            onClick={() => {
              const v = bbox.split(",").map((s) => Number(s.trim()));
              if (v.length !== 4 || v.some((n) => Number.isNaN(n))) return setAoiError("Enter four numbers: west, south, east, north.");
              applyGeometry(v, "Box");
            }}
          >
            Use box
          </button>
        </div>
        {aoiError && <p className="hint" role="alert" style={{ color: "var(--bad)" }}>{aoiError}</p>}
        <p className="hint">Filtering uses the exact polygon, not its bounding box. Areas crossing 180° are split automatically.</p>
      </div>

      <div className="field">
        <span className="label">Dates (UTC)</span>
        <div className="row">
          <input type="date" aria-label="Start date" value={ui.dateStart} onChange={(e) => e.target.value && set({ dateStart: e.target.value })} />
          <input type="date" aria-label="End date" value={ui.dateEnd} onChange={(e) => e.target.value && set({ dateEnd: e.target.value })} />
        </div>
        {extent && (
          <p className="hint">
            Imported record: {extent[0]} to {extent[1]}.{" "}
            <button className="btn" style={{ padding: "1px 6px" }} onClick={() => set({ dateStart: extent[0], dateEnd: extent[1] })}>
              Show all
            </button>
          </p>
        )}
      </div>

      <div className="field">
        <span className="label">Platforms</span>
        <div className="checks">
          {["Terra", "Aqua", "Suomi-NPP", "NOAA-20", "NOAA-21"].map((p) => {
            const avail = availablePlatforms.includes(p);
            return (
              <label key={p} title={avail ? undefined : "No data imported for this platform"} style={{ opacity: avail ? 1 : 0.45 }}>
                <input
                  type="checkbox"
                  disabled={!avail}
                  checked={ui.platforms.includes(p)}
                  onChange={(e) => {
                    const next = e.target.checked ? [...ui.platforms, p] : ui.platforms.filter((x) => x !== p);
                    if (next.length) set({ platforms: next });
                  }}
                />
                <span className="swatch" style={{ background: PLATFORM_COLOR[p] }} />
                {p} <span className="muted small">{PLATFORM_INSTRUMENT[p]}</span>
              </label>
            );
          })}
        </div>
        <p className="hint">Platforms are separate series. Two VIIRS platforms are not independent confirmations.</p>
      </div>

      <div className="field">
        <label htmlFor="stream">Processing stream</label>
        <select id="stream" value={ui.streams ? ui.streams.join(",") : "SP"} onChange={(e) => set({ streams: e.target.value.split(",") })}>
          <option value="SP">Standard (science quality)</option>
          <option value="NRT,RT,URT">Near real-time (incl. RT/URT)</option>
        </select>
        <p className="hint">Standard and near-real-time records are never mixed in one series.</p>
      </div>

      <div className="field">
        <span className="label">Time window</span>
        <div className="seg" role="group" aria-label="Time window">
          {(["week", "day"] as const).map((w) => (
            <button key={w} aria-pressed={ui.window === w} onClick={() => set({ window: w })}>
              {w === "week" ? "Weekly" : "Daily"}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label htmlFor="grid">Equal-area grid</label>
        <select id="grid" value={ui.gridKm} onChange={(e) => set({ gridKm: Number(e.target.value) })}>
          {allowedGrids.map((g) => (
            <option key={g} value={g}>
              {g} km cells{g === meta?.method?.grid?.default_km ? " (default)" : ""}
            </option>
          ))}
        </select>
        <p className="hint">EASE-Grid 2.0. Cells smaller than about 9 km would imply more precision than 1 km MODIS pixels support.</p>
      </div>

      <div className="field">
        <label htmlFor="conf">Minimum confidence</label>
        <select id="conf" value={ui.minConfidence} onChange={(e) => set({ minConfidence: e.target.value })}>
          <option value="low">Low and above (all)</option>
          <option value="nominal">Nominal and above (default)</option>
          <option value="high">High only</option>
        </select>
        <p className="hint">Static sources, volcanoes and offshore detections (standard products only) are always excluded from quality counts.</p>
      </div>

      <div className="field">
        <span className="label">Harmonisation pair</span>
        <div className="row">
          <select aria-label="Reference platform" value={ui.reference} onChange={(e) => set({ reference: e.target.value })}>
            {["Aqua", "Terra"].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
          <select aria-label="Source platform" value={ui.source} onChange={(e) => set({ source: e.target.value })}>
            {["Suomi-NPP", "NOAA-20", "Terra"].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </div>
        <p className="hint">Source detections are expressed on the reference platform's scale. The reference is a measurement scale, not ground truth.</p>
      </div>
      </div>
    </aside>
  );
}
