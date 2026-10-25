export function fmt(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (Math.abs(v) >= 10000) return Math.round(v).toLocaleString("en-US");
  return v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function fmtRange(lo: number | null | undefined, hi: number | null | undefined, digits = 0): string {
  if (lo === null || lo === undefined || hi === null || hi === undefined) return "";
  return `${fmt(lo, digits)}–${fmt(hi, digits)}`;
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return `${Math.round(v * 100)}%`;
}

export function windowLabel(ws: string, window: "day" | "week"): string {
  const d = new Date(ws + "T00:00:00Z");
  const opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" };
  if (window === "day") return d.toLocaleDateString("en-GB", opts);
  const e = new Date(d.getTime() + 6 * 86400000);
  return `Week of ${d.toLocaleDateString("en-GB", opts)} – ${e.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" })}`;
}

export function addDays(iso: string, n: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Last day of the window starting at ws (weeks: 7 days, last bin of the year runs to 31 Dec). */
export function windowEnd(ws: string, window: "day" | "week"): string {
  if (window === "day") return ws;
  const end = addDays(ws, 6);
  const doy = Math.floor((Date.parse(ws + "T00:00:00Z") - Date.UTC(Number(ws.slice(0, 4)), 0, 1)) / 86400000) + 1;
  if (doy >= 358) return ws.slice(0, 4) + "-12-31";
  return end;
}
