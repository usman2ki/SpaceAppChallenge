import { scaleLinear, scaleSymlog, scaleTime } from "d3-scale";
import { area, line } from "d3-shape";
import { useMemo, useState } from "react";
import { fmt } from "../lib/format";
import { useWidth } from "../lib/hooks";

export interface Pt {
  x: number; // ms since epoch (time) or a number (linear)
  y: number | null;
  lo?: number | null;
  hi?: number | null;
}
export interface Series {
  key: string;
  label: string;
  color: string;
  points: Pt[];
  dash?: string;
  band?: boolean; // draw lo/hi band
  width?: number;
  bars?: boolean; // draw as bars with whiskers (annual totals)
}

interface Props {
  series: Series[];
  xType?: "time" | "linear";
  yScale?: "linear" | "symlog";
  height?: number;
  yLabel: string;
  xTickFormat?: (v: number) => string;
  marker?: number | null;
  onPick?: (x: number) => void;
  xDomain?: [number, number];
  title?: string;
  description: string;
  shadeX?: [number, number][]; // shaded x ranges (e.g. overlap period)
  shadeLabel?: string;
}

const M = { l: 52, r: 12, t: 10, b: 26 };

export function Chart({ series, xType = "time", yScale = "linear", height = 240, yLabel, xTickFormat, marker, onPick, xDomain, description, shadeX, shadeLabel }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>(600);
  const [hover, setHover] = useState<{ x: number; px: number } | null>(null);
  const all = series.flatMap((s) => s.points);
  const xs = all.map((p) => p.x);
  const xmin = xDomain ? xDomain[0] : Math.min(...xs);
  const xmax = xDomain ? xDomain[1] : Math.max(...xs);
  const ymax = Math.max(1, ...all.map((p) => Math.max(p.y ?? 0, p.hi ?? 0)));
  const x = (xType === "time" ? scaleTime() : scaleLinear()).domain([xmin, xmax]).range([M.l, width - M.r]) as any;
  const y = (yScale === "symlog" ? scaleSymlog().constant(1) : scaleLinear()).domain([0, ymax * 1.05]).range([height - M.b, M.t]).nice();
  const xticks: number[] = x.ticks(Math.max(3, Math.floor(width / 110))).map((d: any) => +d);
  const yticks: number[] = yScale === "symlog" ? symlogTicks(ymax) : (y.ticks(5) as number[]);
  const fmtX = xTickFormat || ((v: number) => (xType === "time" ? new Date(v).getUTCFullYear().toString() : String(v)));

  const nearest = useMemo(() => {
    const uniq = Array.from(new Set(xs)).sort((a, b) => a - b);
    return (px: number) => {
      const v = +x.invert(px);
      let best = uniq[0];
      for (const u of uniq) if (Math.abs(u - v) < Math.abs(best - v)) best = u;
      return best;
    };
  }, [xs.length, xmin, xmax, width]);

  const barW = Math.max(3, Math.min(28, (width - M.l - M.r) / Math.max(1, new Set(xs).size) * 0.6));

  return (
    <div className="chart" ref={ref}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={description}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * width;
          if (px < M.l || px > width - M.r || !xs.length) return setHover(null);
          const nx = nearest(px);
          setHover({ x: nx, px: x(nx) });
        }}
        onMouseLeave={() => setHover(null)}
        onClick={() => hover && onPick && onPick(hover.x)}
        style={{ cursor: onPick ? "pointer" : undefined }}
      >
        {shadeX &&
          shadeX.map(([a, b], i) => (
            <g key={i}>
              <rect x={x(a)} y={M.t} width={Math.max(0, x(b) - x(a))} height={height - M.t - M.b} fill="var(--wash)" />
              {shadeLabel && i === 0 && (
                <text x={x(a) + 4} y={M.t + 12} fontSize={11} fill="var(--slate)">
                  {shadeLabel}
                </text>
              )}
            </g>
          ))}
        <g className="axis">
          {yticks.map((t) => (
            <g key={t}>
              <line className="gridline" x1={M.l} x2={width - M.r} y1={y(t)} y2={y(t)} />
              <text x={M.l - 6} y={y(t) + 3.5} textAnchor="end">
                {fmt(t)}
              </text>
            </g>
          ))}
          {xticks.map((t) => (
            <text key={t} x={x(t)} y={height - 8} textAnchor="middle">
              {fmtX(t)}
            </text>
          ))}
          <text x={12} y={M.t + 4} transform={`rotate(-90 12 ${M.t + 4})`} textAnchor="end" fontSize={11}>
            {yLabel}
          </text>
        </g>
        {series.map((s) => {
          const pts = s.points.filter((p) => p.y !== null && p.y !== undefined).sort((a, b) => a.x - b.x);
          if (s.bars) {
            return (
              <g key={s.key}>
                {pts.map((p) => (
                  <g key={p.x}>
                    <rect x={x(p.x) - barW / 2} y={y(p.y!)} width={barW} height={Math.max(0, y(0) - y(p.y!))} fill={s.color} opacity={0.85} />
                    {p.lo != null && p.hi != null && (
                      <path d={`M${x(p.x)},${y(p.lo)}V${y(p.hi)}M${x(p.x) - 4},${y(p.lo)}h8M${x(p.x) - 4},${y(p.hi)}h8`} stroke="var(--ink)" strokeWidth={1.2} fill="none" />
                    )}
                  </g>
                ))}
              </g>
            );
          }
          const bandPts = s.points.filter((p) => p.lo != null && p.hi != null).sort((a, b) => a.x - b.x);
          const ln = line<Pt>()
            .defined((p) => p.y !== null && p.y !== undefined)
            .x((p) => x(p.x))
            .y((p) => y(p.y as number));
          const ar = area<Pt>()
            .defined((p) => p.lo != null && p.hi != null)
            .x((p) => x(p.x))
            .y0((p) => y(p.lo as number))
            .y1((p) => y(p.hi as number));
          const sorted = [...s.points].sort((a, b) => a.x - b.x);
          return (
            <g key={s.key}>
              {s.band && bandPts.length > 0 && <path d={ar(sorted) || ""} fill={s.color} opacity={0.16} />}
              <path d={ln(sorted) || ""} fill="none" stroke={s.color} strokeWidth={s.width ?? 1.6} strokeDasharray={s.dash} />
            </g>
          );
        })}
        {marker != null && marker >= xmin && marker <= xmax && <line x1={x(marker)} x2={x(marker)} y1={M.t} y2={height - M.b} stroke="var(--ink)" strokeWidth={1.2} />}
        {hover && <line x1={hover.px} x2={hover.px} y1={M.t} y2={height - M.b} stroke="var(--faint)" strokeDasharray="2 3" />}
      </svg>
      <div className="legend">
        {series.map((s) => (
          <span key={s.key} className="item">
            <svg width="18" height="10" aria-hidden="true">
              {s.bars || (s.band && s.width === 0) ? <rect width="18" height="10" fill={s.color} opacity={s.bars ? 1 : 0.3} /> : <line x1="0" x2="18" y1="5" y2="5" stroke={s.color} strokeWidth={s.width ?? 2} strokeDasharray={s.dash} />}
            </svg>
            {s.label}
            {hover && !s.bars && (() => {
              const p = s.points.find((q) => q.x === hover.x);
              return p && p.y != null ? <b className="num">&nbsp;{fmt(p.y, p.y < 10 ? 1 : 0)}</b> : null;
            })()}
          </span>
        ))}
      </div>
    </div>
  );
}

function symlogTicks(max: number): number[] {
  const out = [0];
  for (const t of [1, 3, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000]) if (t <= max * 1.05) out.push(t);
  return out;
}

export const toMs = (iso: string) => Date.parse(iso + "T00:00:00Z");
