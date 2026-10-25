// Typed client for the Ember Atlas API. The FIRMS MAP_KEY, when the user
// enters one, is passed only as a request header for that single call; it is
// never written to localStorage, sessionStorage, cookies or the URL.

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json", ...headers } : headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Cannot reach the Ember Atlas server. Check that it is running (ember serve) and that you are online if it is remote.", 0);
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
  }
  return data as T;
}

export interface SelectionReq {
  dataset: "synthetic" | "real";
  aoi: unknown;
  date_start: string;
  date_end: string;
  platforms: string[];
  streams: string[] | null;
  grid_km: number;
  window: "day" | "week";
  min_confidence: string;
  reference: string;
  source: string;
}

export const api = {
  meta: () => request<any>("GET", "/api/meta"),
  validateAoi: (aoi: unknown) => request<any>("POST", "/api/aoi/validate", { aoi }),
  summary: (r: SelectionReq) => request<any>("POST", "/api/summary", r),
  cells: (r: SelectionReq, from?: string | null, to?: string | null) =>
    request<any>("POST", "/api/map/cells", { ...r, window_from: from || undefined, window_to: to || undefined }),
  detections: (r: SelectionReq, from: string, to: string, page = 1) =>
    request<any>("POST", "/api/map/detections", { ...r, window_from: from, window_to: to, page, page_size: 3000 }),
  window: (r: SelectionReq, windowStart: string) => request<any>("POST", "/api/window", { ...r, window_start: windowStart }),
  harmonized: (r: SelectionReq) => request<any>("POST", "/api/harmonized", r),
  evaluation: (r: SelectionReq) => request<any>("POST", "/api/evaluation", r),
  runEvaluation: (r: SelectionReq) => request<any>("POST", "/api/evaluation/run", r),
  job: (id: string) => request<any>("GET", `/api/jobs/${id}`),
  anomalies: (r: SelectionReq, basis: string) =>
    request<any>("POST", "/api/anomalies", { ...r, window: "week", basis, view: [r.date_start, r.date_end] }),
  batches: () => request<any[]>("GET", "/api/batches"),
  batch: (id: number) => request<any>("GET", `/api/batches/${id}`),
  artifacts: () => request<any>("GET", "/api/artifacts"),
  firmsFetch: (body: unknown, sessionKey: string | null) =>
    request<any>("POST", "/api/firms/fetch", body, sessionKey ? { "X-FIRMS-Map-Key": sessionKey } : {}),

  async importCsv(form: FormData): Promise<any> {
    let res: Response;
    try {
      res = await fetch("/api/import", { method: "POST", body: form });
    } catch {
      throw new ApiError("Cannot reach the Ember Atlas server.", 0);
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError((data && data.error) || `Import failed (${res.status})`, res.status);
    return data;
  },

  async exportFile(r: SelectionReq, format: "csv" | "geojson", content: string): Promise<void> {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...r, format, content }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => null);
      throw new ApiError((d && d.error) || "Export failed", res.status);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="([^"]+)"/.exec(cd);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : `ember_export.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  },
};
