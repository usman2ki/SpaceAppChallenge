import { useMemo, useRef, useState } from "react";
import { CalCell, MONTH_NAMES, MONTH_STARTS_DOY } from "../lib/calendar";
import { fmt, fmtRange, windowLabel } from "../lib/format";
import { useWidth } from "../lib/hooks";
import { heatColor, heatStops, SIGNAL_TEXT, STATUS_TEXT } from "../lib/scales";

interface Props {
  cells: CalCell[];
  years: number[];
  window: "day" | "week";
  selected: string | null;
  onSelect: (ws: string) => void;
  signals: Record<string, string>;
  unit: string;
}

const LABEL_W = 46;
const TOP = 18;

export function Patterns() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <pattern id="hatch-unknown" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="5" height="5" fill="var(--paper)" />
          <line x1="0" y1="0" x2="0" y2="5" stroke="var(--unknown)" strokeWidth="1.6" />
        </pattern>
        <pattern id="hatch-unobserved" width="5" height="5" patternUnits="userSpaceOnUse">
          <rect width="5" height="5" fill="var(--wash)" />
          <path d="M0 0L5 5M5 0L0 5" stroke="var(--unknown)" strokeWidth="1" />
        </pattern>
        <pattern id="hatch-partial" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="var(--observed-none)" />
          <line x1="0" y1="0" x2="0" y2="6" stroke="var(--unknown)" strokeWidth="1" />
        </pattern>
      </defs>
    </svg>
  );
}

export function statusFill(c: CalCell, max: number): string {
  switch (c.status) {
    case "detections":
      return heatColor(c.value ?? 0, max);
    case "observed_none":
      return "var(--observed-none)";
    case "partially_observed_none":
      return "url(#hatch-partial)";
    case "unobserved":
      return "url(#hatch-unobserved)";
    case "none_reported_coverage_unknown":
      return "url(#hatch-unknown)";
    default:
      return "var(--paper)";
  }
}

export function Calendar({ cells, years, window, selected, onSelect, signals, unit }: Props) {
  const [wrapRef, width] = useWidth<HTMLDivElement>(900);
  const [hover, setHover] = useState<{ c: CalCell; x: number; y: number } | null>(null);
  const live = useRef<HTMLDivElement>(null);
  const cols = window === "week" ? 52 : 366;
  const cw = Math.max(1.5, (width - LABEL_W) / cols);
  const ch = window === "week" ? Math.min(22, Math.max(12, cw * 0.95)) : 14;
  // daily cells are ~2-3 px wide: no gap, or anti-aliasing washes the colours out
  const gap = window === "week" ? 1.5 : 0;
  const rowH = ch + (window === "week" ? 4 : 3);
  const height = TOP + years.length * rowH + 4;
  const max = useMemo(() => cells.reduce((m, c) => (c.status === "detections" && (c.value ?? 0) > m ? c.value ?? 0 : m), 0), [cells]);
  const byWs = useMemo(() => new Map(cells.map((c) => [c.windowStart, c])), [cells]);
  const sorted = useMemo(() => [...cells].sort((a, b) => a.windowStart.localeCompare(b.windowStart)), [cells]);
  const yIndex = new Map(years.map((y, i) => [y, i]));

  function x(c: CalCell) {
    return LABEL_W + c.col * cw;
  }
  function y(c: CalCell) {
    return TOP + (yIndex.get(c.year) ?? 0) * rowH;
  }
  function describe(c: CalCell): string {
    const parts = [windowLabel(c.windowStart, window), STATUS_TEXT[c.status] || c.status];
    if (c.value !== null && c.status === "detections") parts.push(`${fmt(c.value, c.value < 10 && c.value % 1 ? 1 : 0)} ${unit}`);
    if (c.lo != null && c.hi != null) parts.push(`90% interval ${fmtRange(c.lo, c.hi)}`);
    if (c.basis && c.basis.startsWith("converted")) parts.push("converted from " + c.basis.replace("converted_from_", ""));
    const s = signals[c.windowStart];
    if (s && s !== "typical") parts.push(SIGNAL_TEXT[s] || s);
    return parts.join(". ");
  }

  function onKey(e: React.KeyboardEvent) {
    if (!sorted.length) return;
    const i = selected ? sorted.findIndex((c) => c.windowStart === selected) : -1;
    let next: CalCell | undefined;
    if (e.key === "ArrowRight") next = sorted[Math.min(sorted.length - 1, i + 1)];
    else if (e.key === "ArrowLeft") next = sorted[Math.max(0, i - 1)];
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const cur = i >= 0 ? sorted[i] : sorted[0];
      const ty = cur.year + (e.key === "ArrowDown" ? 1 : -1);
      next = sorted.find((c) => c.year === ty && c.col >= cur.col) || cur;
    } else if (e.key === "Home") next = sorted[0];
    else if (e.key === "End") next = sorted[sorted.length - 1];
    if (next) {
      e.preventDefault();
      onSelect(next.windowStart);
      if (live.current) live.current.textContent = describe(next);
    }
  }

  const selCell = selected ? byWs.get(selected) : undefined;
  return (
    <div className="calendar calendar-wrap" ref={wrapRef}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="group"
        aria-roledescription="calendar"
        tabIndex={0}
        aria-label={`Burning-activity calendar, ${years[0]}–${years[years.length - 1]}, one row per year. Use arrow keys to move between ${window === "week" ? "weeks" : "days"}.`}
        onKeyDown={onKey}
        onMouseLeave={() => setHover(null)}
      >
        {MONTH_STARTS_DOY.map((d, i) => (
          <text key={i} className="month-label" x={LABEL_W + (window === "week" ? (d - 1) / 7 : d - 1) * cw} y={11}>
            {MONTH_NAMES[i]}
          </text>
        ))}
        {years.map((yr, i) => (
          <text key={yr} className="year-label" x={0} y={TOP + i * rowH + ch * 0.78}>
            {yr}
          </text>
        ))}
        {cells.map((c) => (
          <rect
            key={c.windowStart}
            className={"cell" + (c.windowStart === selected ? " sel" : "")}
            x={x(c) + gap / 2}
            y={y(c)}
            width={window === "week" ? Math.max(0.8, cw - gap) : cw + 0.35}
            shapeRendering={window === "day" ? "crispEdges" : undefined}
            height={ch}
            rx={window === "week" ? 2 : 0}
            fill={statusFill(c, max)}
            stroke={c.status === "not_queried" && window === "week" ? "var(--rule)" : undefined}
            strokeWidth={c.status === "not_queried" && window === "week" ? 0.6 : undefined}
            onClick={() => onSelect(c.windowStart)}
            onMouseMove={(e) => {
              const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
              setHover({ c, x: e.clientX - box.left, y: e.clientY - box.top });
            }}
          />
        ))}
        {cells
          .filter((c) => c.basis && c.basis.startsWith("converted") && window === "week")
          .map((c) => (
            <circle key={"cv" + c.windowStart} className="conv-mark" cx={x(c) + cw - gap - 3} cy={y(c) + 3} r={1.6} />
          ))}
        {cells
          .filter((c) => signals[c.windowStart] === "unusually_high" || signals[c.windowStart] === "unusually_low")
          .map((c) => (
            <rect
              key={"a" + c.windowStart}
              className={signals[c.windowStart] === "unusually_high" ? "anom-high" : "anom-low"}
              x={x(c) - 0.5}
              y={y(c) - 1.5}
              width={cw + 0.5}
              height={ch + 3}
              rx={3}
            />
          ))}
      </svg>
      {hover && (
        <div className="tooltip" style={{ left: Math.min(hover.x + 12, width - 280), top: hover.y + 14 }}>
          {describe(hover.c)}
        </div>
      )}
      <div ref={live} className="sr-only" aria-live="polite">
        {selCell ? describe(selCell) : ""}
      </div>
    </div>
  );
}

export function CalendarLegend({ unit, max, showConverted, showAnomalies }: { unit: string; max: number; showConverted: boolean; showAnomalies: boolean }) {
  const stops = heatStops(6);
  return (
    <div className="legend" aria-label="Calendar legend">
      <span className="item">
        <svg width="96" height="12" aria-hidden="true">
          {stops.map((s, i) => (
            <rect key={i} x={i * 16} y={0} width={16} height={12} fill={s} />
          ))}
        </svg>
        {unit}: 1 to {fmt(max)} (log scale)
      </span>
      <span className="item">
        <svg width="14" height="12" aria-hidden="true"><rect width="14" height="12" fill="url(#hatch-unknown)" stroke="var(--rule-strong)" /></svg>
        None reported, coverage unknown
      </span>
      <span className="item">
        <svg width="14" height="12" aria-hidden="true"><rect width="14" height="12" fill="var(--observed-none)" /></svg>
        Observed, no detections
      </span>
      <span className="item">
        <svg width="14" height="12" aria-hidden="true"><rect width="14" height="12" fill="url(#hatch-unobserved)" stroke="var(--rule-strong)" /></svg>
        Not observed
      </span>
      <span className="item">
        <svg width="14" height="12" aria-hidden="true"><rect width="14" height="12" fill="var(--paper)" stroke="var(--rule-strong)" /></svg>
        No data imported
      </span>
      {showConverted && (
        <span className="item">
          <svg width="14" height="12" aria-hidden="true"><rect width="14" height="12" fill="#d9481c" /><circle cx="10" cy="3" r="1.8" fill="#fff" /></svg>
          Converted from the source sensor
        </span>
      )}
      {showAnomalies && (
        <span className="item">
          <svg width="16" height="14" aria-hidden="true"><rect x="1" y="1" width="14" height="12" rx="3" fill="none" stroke="var(--ink)" strokeWidth="1.4" /></svg>
          Unusually high (dashed: low)
        </span>
      )}
    </div>
  );
}
