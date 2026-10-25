import { geoGraticule10, geoMercator, geoPath } from "d3-geo";
import { select } from "d3-selection";
import { zoom, zoomIdentity, ZoomTransform } from "d3-zoom";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmt } from "../lib/format";
import { useWidth } from "../lib/hooks";
import { rewindForD3 } from "../lib/geo";
import { heatColor, PLATFORM_COLOR } from "../lib/scales";

const R = 6378137;

interface Props {
  aoi: any | null; // GeoJSON geometry
  cells: any | null; // FeatureCollection with raw_*/q_* properties
  detections: any | null; // FeatureCollection of points
  platforms: string[];
  colorBy: string; // platform name or "__pooled__"
  basemap: boolean;
  drawing: boolean;
  onDrawn: (ring: [number, number][]) => void;
  onCancelDraw: () => void;
  onCellPick: (props: any | null) => void;
  pickedCell: string | null;
  caption: string;
}

function lonExtent(geom: any): { center: number; crosses: boolean } {
  const lons: number[] = [];
  const walk = (c: any) => (typeof c[0] === "number" ? lons.push(c[0]) : c.forEach(walk));
  walk(geom.coordinates);
  const min = Math.min(...lons);
  const max = Math.max(...lons);
  if (max - min > 180) {
    const un = lons.map((l) => (l < 0 ? l + 360 : l));
    const c = (Math.min(...un) + Math.max(...un)) / 2;
    return { center: c > 180 ? c - 360 : c, crosses: true };
  }
  return { center: (min + max) / 2, crosses: false };
}

function cellValue(p: any, colorBy: string, platforms: string[]): number {
  if (colorBy === "__pooled__") return platforms.reduce((s, pl) => s + (Number(p["q_" + pl]) || 0), 0);
  return Number(p["q_" + colorBy]) || 0;
}

export function MapView(props: Props) {
  const { detections, platforms, colorBy, basemap, drawing, onDrawn, onCancelDraw, onCellPick, pickedCell, caption } = props;
  const aoi = useMemo(() => rewindForD3(props.aoi), [props.aoi]);
  const cells = useMemo(() => rewindForD3(props.cells), [props.cells]);
  const [wrapRef, width] = useWidth<HTMLDivElement>(640);
  const height = Math.round(Math.min(520, Math.max(300, width * 0.68)));
  const svgRef = useRef<SVGSVGElement>(null);
  const [t, setT] = useState<ZoomTransform>(zoomIdentity);
  const [verts, setVerts] = useState<[number, number][]>([]);
  const [tileFail, setTileFail] = useState(false);

  const projection = useMemo(() => {
    const p = geoMercator();
    if (aoi) {
      const { center } = lonExtent(aoi);
      p.rotate([-center, 0]).fitExtent(
        [
          [16, 16],
          [width - 16, height - 16],
        ],
        { type: "Feature", geometry: aoi, properties: {} } as any,
      );
    } else {
      p.fitExtent(
        [
          [0, 0],
          [width, height],
        ],
        { type: "Sphere" } as any,
      );
    }
    return p;
  }, [aoi, width, height]);

  // effective projection includes the zoom transform (used for tiles, footprints, drawing)
  const eff = useMemo(() => {
    const p = geoMercator()
      .rotate(projection.rotate())
      .scale(projection.scale() * t.k)
      .translate([projection.translate()[0] * t.k + t.x, projection.translate()[1] * t.k + t.y]);
    return p;
  }, [projection, t]);

  const path = useMemo(() => geoPath(projection), [projection]);

  useEffect(() => {
    if (!svgRef.current) return;
    const z = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.5, 64])
      .filter((ev: any) => !ev.button && ev.type !== "dblclick")
      .on("zoom", (ev) => setT(ev.transform));
    const sel = select(svgRef.current);
    sel.call(z as any);
    sel.call(z.transform as any, zoomIdentity);
    return () => {
      sel.on(".zoom", null);
    };
  }, [projection]);

  useEffect(() => {
    if (!drawing) setVerts([]);
    const onKey = (e: KeyboardEvent) => {
      if (drawing && e.key === "Escape") {
        setVerts([]);
        onCancelDraw();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing, onCancelDraw]);

  const maxCell = useMemo(() => {
    if (!cells) return 0;
    return cells.features.reduce((m: number, f: any) => Math.max(m, cellValue(f.properties, colorBy, platforms)), 0);
  }, [cells, colorBy, platforms]);

  // ---------------------------------------------------------------- basemap tiles (online only)
  const tiles = useMemo(() => {
    if (!basemap || tileFail) return [];
    const zf = Math.log2((2 * Math.PI * eff.scale()) / 256);
    const zl = Math.max(0, Math.min(17, Math.round(zf)));
    const n = 2 ** zl;
    const tl = eff.invert!([0, 0]);
    const br = eff.invert!([width, height]);
    if (!tl || !br) return [];
    let w = tl[0];
    let e = br[0];
    if (e < w) e += 360;
    const toX = (lon: number) => Math.floor(((lon + 180) / 360) * n);
    const toY = (lat: number) => {
      const r = (Math.max(-85, Math.min(85, lat)) * Math.PI) / 180;
      return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n);
    };
    const x0 = toX(w), x1 = toX(e), y0 = Math.max(0, toY(tl[1])), y1 = Math.min(n - 1, toY(br[1]));
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > 120) return [];
    const lonOf = (x: number) => (x / n) * 360 - 180;
    const latOf = (y: number) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
    const out = [];
    for (let x = x0; x <= x1; x++) {
      for (let y = y0; y <= y1; y++) {
        const a = eff([lonOf(x), latOf(y)]);
        const b = eff([lonOf(x + 1), latOf(y + 1)]);
        if (!a || !b || b[0] - a[0] <= 0 || b[1] - a[1] <= 0 || b[0] - a[0] > width * 4) continue;
        const wx = ((x % n) + n) % n;
        out.push({ key: `${zl}/${x}/${y}`, href: `https://tile.openstreetmap.org/${zl}/${wx}/${y}.png`, x: a[0], y: a[1], w: b[0] - a[0], h: b[1] - a[1] });
      }
    }
    return out;
  }, [basemap, tileFail, eff, width, height]);

  function clientToLonLat(ev: React.MouseEvent): [number, number] | null {
    const box = svgRef.current!.getBoundingClientRect();
    const sx = ((ev.clientX - box.left) / box.width) * width;
    const sy = ((ev.clientY - box.top) / box.height) * height;
    const ll = eff.invert!([sx, sy]);
    if (!ll) return null;
    let lon = ll[0];
    if (lon > 180) lon -= 360;
    if (lon < -180) lon += 360;
    return [Math.round(lon * 1e5) / 1e5, Math.round(ll[1] * 1e5) / 1e5];
  }

  const graticule = useMemo(() => path(geoGraticule10()), [path]);
  const pxPerKm = (lat: number) => (eff.scale() * 1000) / (R * Math.cos((lat * Math.PI) / 180));

  return (
    <div className={drawing ? "drawing" : undefined}>
      <div className="map-wrap" ref={wrapRef}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={caption}
          onClick={(ev) => {
            if (!drawing) return;
            const ll = clientToLonLat(ev);
            if (ll) setVerts((v) => [...v, ll]);
          }}
          onDoubleClick={(ev) => {
            if (!drawing) return;
            ev.preventDefault();
            if (verts.length >= 3) {
              onDrawn(verts);
              setVerts([]);
            }
          }}
        >
          <g>
            {tiles.map((tl) => (
              <image key={tl.key} href={tl.href} x={tl.x} y={tl.y} width={tl.w + 0.5} height={tl.h + 0.5} opacity={0.55} onError={() => setTileFail(true)} preserveAspectRatio="none" />
            ))}
          </g>
          <g transform={t.toString()}>
            <path d={graticule || ""} fill="none" stroke="var(--rule-strong)" strokeWidth={0.6 / t.k} />
            {cells &&
              cells.features.map((f: any) => {
                const v = cellValue(f.properties, colorBy, platforms);
                const picked = f.properties.cell_id === pickedCell;
                return (
                  <path
                    key={f.properties.cell_id}
                    d={path(f) || ""}
                    fill={v > 0 ? heatColor(v, maxCell) : "transparent"}
                    fillOpacity={v > 0 ? 0.78 : 0}
                    stroke={picked ? "var(--ink)" : "var(--rule-strong)"}
                    strokeWidth={(picked ? 2.2 : 0.5) / t.k}
                    onClick={(e) => {
                      if (drawing) return;
                      e.stopPropagation();
                      onCellPick(picked ? null : f.properties);
                    }}
                    style={{ cursor: drawing ? "crosshair" : "pointer" }}
                  >
                    <title>{`${f.properties.cell_id}: ${fmt(v)} quality detections`}</title>
                  </path>
                );
              })}
            {aoi && <path d={path(aoi) || ""} fill="none" stroke="var(--ink)" strokeWidth={1.8 / t.k} strokeDasharray={`${5 / t.k} ${3 / t.k}`} />}
          </g>
          <g>
            {detections &&
              detections.features.map((f: any, i: number) => {
                const [lon, lat] = f.geometry.coordinates;
                const p = eff([lon, lat]);
                if (!p) return null;
                const k = pxPerKm(lat);
                const w = (f.properties.scan_km || (f.properties.instrument === "MODIS" ? 1 : 0.375)) * k;
                const h = (f.properties.track_km || (f.properties.instrument === "MODIS" ? 1 : 0.375)) * k;
                const color = PLATFORM_COLOR[f.properties.platform] || "var(--ink)";
                const faded = !f.properties.quality;
                if (w < 3) {
                  return <circle key={i} cx={p[0]} cy={p[1]} r={f.properties.instrument === "MODIS" ? 2.6 : 1.7} fill={color} opacity={faded ? 0.3 : 0.9} />;
                }
                return <rect key={i} x={p[0] - w / 2} y={p[1] - h / 2} width={w} height={h} fill={color} fillOpacity={faded ? 0.15 : 0.45} stroke={color} strokeWidth={1} />;
              })}
          </g>
          {drawing && verts.length > 0 && (
            <g>
              <polyline
                points={verts.map((v) => eff(v)!.join(",")).join(" ")}
                fill="color-mix(in srgb, var(--focus) 15%, transparent)"
                stroke="var(--focus)"
                strokeWidth={2}
              />
              {verts.map((v, i) => {
                const p = eff(v)!;
                return <circle key={i} cx={p[0]} cy={p[1]} r={4} fill="var(--panel)" stroke="var(--focus)" strokeWidth={2} />;
              })}
            </g>
          )}
        </svg>
        {tiles.length > 0 && <div className="attribution">© OpenStreetMap contributors</div>}
      </div>
      {drawing && (
        <div className="map-toolbar" style={{ marginTop: 8 }}>
          <span className="muted small">Click to add corners ({verts.length} so far). Double-click or finish when done. Escape cancels.</span>
          <button className="btn primary" disabled={verts.length < 3} onClick={() => { onDrawn(verts); setVerts([]); }}>
            Finish area
          </button>
          <button className="btn" disabled={!verts.length} onClick={() => setVerts((v) => v.slice(0, -1))}>
            Undo corner
          </button>
          <button className="btn" onClick={() => { setVerts([]); onCancelDraw(); }}>
            Cancel
          </button>
        </div>
      )}
      {basemap && tileFail && <p className="map-note">Basemap tiles could not be loaded (offline or blocked). Analysis layers are unaffected.</p>}
    </div>
  );
}
