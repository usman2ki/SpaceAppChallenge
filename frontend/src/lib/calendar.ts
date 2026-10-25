// Pure calendar logic (tested in tests/calendar.test.ts).

export interface CalCell {
  windowStart: string;
  year: number;
  col: number; // week index 0..51 or day-of-year index 0..365
  value: number | null;
  status: string;
  lo?: number | null;
  hi?: number | null;
  basis?: string;
}

export function dayOfYear(iso: string): number {
  const d = new Date(iso + "T00:00:00Z");
  const start = Date.UTC(d.getUTCFullYear(), 0, 1);
  return Math.floor((d.getTime() - start) / 86400000) + 1;
}

/** Week bin used by the backend: 7-day bins from 1 Jan, last bin absorbs days 358+. */
export function weekIndex(iso: string): number {
  return Math.min(Math.floor((dayOfYear(iso) - 1) / 7), 51);
}

export function yearsBetween(start: string, end: string): number[] {
  const a = Number(start.slice(0, 4));
  const b = Number(end.slice(0, 4));
  const out: number[] = [];
  for (let y = a; y <= b; y++) out.push(y);
  return out;
}

export interface RawRow {
  window_start: string;
  platform: string;
  status: string;
  [k: string]: any;
}

/** Build calendar cells for one raw platform (or the pooled sum) and metric. */
export function rawCells(rows: RawRow[], platform: string, metric: string, window: "day" | "week"): CalCell[] {
  const pick = platform === "__pooled__" ? rows : rows.filter((r) => r.platform === platform);
  const byWin = new Map<string, { v: number; statuses: string[] }>();
  for (const r of pick) {
    const e = byWin.get(r.window_start) || { v: 0, statuses: [] };
    e.v += Number(r[metric] ?? 0) || 0;
    e.statuses.push(r.status);
    byWin.set(r.window_start, e);
  }
  const out: CalCell[] = [];
  for (const [ws, e] of byWin) {
    out.push({
      windowStart: ws,
      year: Number(ws.slice(0, 4)),
      col: window === "week" ? weekIndex(ws) : dayOfYear(ws) - 1,
      value: e.v,
      status: combineStatus(e.statuses, e.v),
    });
  }
  return out;
}

/** Status of a pooled cell: detections if any value; otherwise the most
 * informative zero status that all parts share; unknown beats observed. */
export function combineStatus(statuses: string[], value: number): string {
  if (value > 0 || statuses.includes("detections")) return "detections";
  if (statuses.every((s) => s === "not_queried")) return "not_queried";
  const order = ["none_reported_coverage_unknown", "unobserved", "partially_observed_none", "observed_none"];
  for (const s of order) if (statuses.includes(s)) return s;
  return "none_reported_coverage_unknown";
}

export interface HarmRow {
  window_start: string;
  harmonized: number | null;
  harm_lo: number | null;
  harm_hi: number | null;
  basis: string;
}

export function harmonizedCells(rows: HarmRow[], start: string, end: string, coverageKnown = false): CalCell[] {
  return rows
    .filter((r) => r.window_start >= start.slice(0, 4) + "-01-01" && r.window_start <= end)
    .map((r) => ({
      windowStart: r.window_start,
      year: Number(r.window_start.slice(0, 4)),
      col: weekIndex(r.window_start),
      value: r.harmonized,
      lo: r.harm_lo,
      hi: r.harm_hi,
      basis: r.basis,
      status:
        r.basis === "none" || r.harmonized === null
          ? "not_queried"
          : r.harmonized > 0
            ? "detections"
            : coverageKnown
              ? "observed_none"
              : "none_reported_coverage_unknown",
    }));
}

export function maxValue(cells: CalCell[]): number {
  let m = 0;
  for (const c of cells) if (c.value !== null && c.value > m) m = c.value;
  return m;
}

export const MONTH_STARTS_DOY = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
export const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Naive splice of reference then source raw counts: the misleading series
 * you get by concatenating sensors without harmonisation. */
export function naiveSplice(rows: { window_start: string; ref_raw: number; src_raw: number; ref_queried: boolean; src_queried: boolean }[]) {
  return rows.map((r) => ({
    window_start: r.window_start,
    value: r.ref_queried ? r.ref_raw : r.src_queried ? r.src_raw : null,
    from: r.ref_queried ? "reference" : r.src_queried ? "source" : "none",
  }));
}
