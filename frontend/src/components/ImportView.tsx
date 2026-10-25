import { useState } from "react";
import { api } from "../lib/api";
import { fmt } from "../lib/format";

interface Props {
  aoi: any;
  products: Record<string, any>;
  onImported: () => void;
}

function Report({ r }: { r: any }) {
  if (r.status === "already_imported") return <div className="note">{r.message}</div>;
  if (r.status === "rejected_file") return <div className="note bad"><b>File rejected.</b> {r.message}</div>;
  return (
    <div className="note">
      <b>Imported {r.file_name}</b> as batch {r.batch_id} ({r.data_origin} data).
      <dl className="kv" style={{ marginTop: 6 }}>
        <dt>Rows read</dt><dd>{fmt(r.rows_read)}</dd>
        <dt>Inserted</dt><dd>{fmt(r.inserted)}</dd>
        <dt>Rejected</dt><dd>{fmt(r.rows_rejected)} {Object.keys(r.rejection_reasons || {}).length ? `(${Object.entries(r.rejection_reasons).map(([k, v]) => `${k}: ${v}`).join("; ")})` : ""}</dd>
        <dt>Duplicates</dt><dd>{fmt(r.duplicates_in_file)} in file, {fmt(r.duplicates_existing)} already stored</dd>
        <dt>Conflicts</dt><dd>{fmt(r.conflicts_in_file + r.conflicts_existing)}</dd>
        <dt>Platforms</dt><dd>{Object.entries(r.platforms || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "–"}</dd>
        <dt>Streams</dt><dd>{Object.keys(r.processing_streams || {}).join(", ") || "–"}</dd>
        <dt>Dates</dt><dd>{r.date_range ? r.date_range.join(" to ") : "–"}</dd>
      </dl>
      {(r.file_warnings || []).map((w: string, i: number) => <p key={i} className="small" style={{ marginTop: 4 }}>{w}</p>)}
      {r.example_issues?.length > 0 && (
        <details>
          <summary>First {r.example_issues.length} row issues</summary>
          <ul className="small">
            {r.example_issues.map((i: any, k: number) => <li key={k}>Line {i.line}: {i.status} — {i.reasons.join("; ")}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

export function ImportView({ aoi, products, onImported }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ dataset: "real", source_id: "", period_start: "", period_end: "", source_url: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  // FIRMS fetch: the key lives only in this component's memory.
  const [key, setKey] = useState("");
  const [fetchSrc, setFetchSrc] = useState("VIIRS_SNPP_NRT");
  const today = new Date().toISOString().slice(0, 10);
  const [fStart, setFStart] = useState(new Date(Date.now() - 4 * 86400000).toISOString().slice(0, 10));
  const [fEnd, setFEnd] = useState(today);
  const [fetchRes, setFetchRes] = useState<any>(null);
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  async function submit() {
    if (!file) return;
    setBusy(true);
    setErr(null);
    setReport(null);
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(form).forEach(([k, v]) => v && fd.append(k, v));
    try {
      setReport(await api.importCsv(fd));
      onImported();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function fetchFirms() {
    setBusy(true);
    setFetchErr(null);
    setFetchRes(null);
    try {
      const r = await api.firmsFetch({ source: fetchSrc, aoi, date_start: fStart, date_end: fEnd }, key.trim() || null);
      setFetchRes(r);
      onImported();
    } catch (e: any) {
      setFetchErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid-2">
      <section>
        <h3>Import a FIRMS CSV file</h3>
        <p className="small muted">
          Archive downloads (standard products) and API CSVs are accepted. Each row is validated; duplicates and conflicts are reported, the original file is kept unchanged, and
          importing the same file twice changes nothing.
        </p>
        <div className="field">
          <label htmlFor="csv">CSV file</label>
          <input id="csv" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </div>
        <div className="field">
          <span className="label">This file contains</span>
          <div className="seg" role="group" aria-label="Data origin">
            <button aria-pressed={form.dataset === "real"} onClick={() => setForm({ ...form, dataset: "real" })}>Real satellite data</button>
            <button aria-pressed={form.dataset === "synthetic"} onClick={() => setForm({ ...form, dataset: "synthetic" })}>Synthetic test data</button>
          </div>
        </div>
        <div className="field">
          <label htmlFor="src">FIRMS product</label>
          <select id="src" value={form.source_id} onChange={(e) => setForm({ ...form, source_id: e.target.value })}>
            <option value="">Infer from the file's columns</option>
            {Object.entries(products).map(([k, p]: [string, any]) => <option key={k} value={k}>{k}: {p.description}</option>)}
          </select>
          <p className="hint">Declaring the product lets the importer catch mixed platforms or a near-real-time row inside a standard file.</p>
        </div>
        <div className="field">
          <span className="label">Period you requested (optional but recommended)</span>
          <div className="row">
            <input type="date" aria-label="Requested start" value={form.period_start} onChange={(e) => setForm({ ...form, period_start: e.target.value })} />
            <input type="date" aria-label="Requested end" value={form.period_end} onChange={(e) => setForm({ ...form, period_end: e.target.value })} />
          </div>
          <p className="hint">Without it, the queried period is inferred from the first and last detection, and quiet days at either end are treated as not queried.</p>
        </div>
        <div className="field">
          <label htmlFor="srcurl">Where it came from</label>
          <input id="srcurl" type="text" placeholder="e.g. FIRMS archive request, 2026-10-25" value={form.source_url} onChange={(e) => setForm({ ...form, source_url: e.target.value })} />
        </div>
        <button className="btn primary" disabled={!file || busy} onClick={submit}>{busy ? "Importing…" : "Import file"}</button>
        {err && <p className="state error">{err}</p>}
        {report && <Report r={report} />}
      </section>

      <section>
        <h3>Fetch from the FIRMS API</h3>
        <p className="small muted">
          Downloads the current area's bounding box in 5-day requests and imports it as real data. You need your own free MAP_KEY from NASA FIRMS. The FIRMS API requires the key inside
          the request address, so it is sent to NASA over HTTPS. Ember Atlas keeps it in memory for this page only: it is not saved in the browser, the database, logs or error messages.
          Leave it empty to use a key configured on the server (FIRMS_MAP_KEY).
        </p>
        <div className="field">
          <label htmlFor="mapkey">MAP_KEY (this session only)</label>
          <input id="mapkey" type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} />
          <p className="hint">
            Request a key at <a href="https://firms.modaps.eosdis.nasa.gov/api/map_key/" target="_blank" rel="noreferrer">firms.modaps.eosdis.nasa.gov/api/map_key</a>. Limit: 5000 transactions per 10 minutes.
          </p>
        </div>
        <div className="field">
          <label htmlFor="fsrc">Product</label>
          <select id="fsrc" value={fetchSrc} onChange={(e) => setFetchSrc(e.target.value)}>
            {Object.entries(products).map(([k, p]: [string, any]) => <option key={k} value={k}>{k} ({p.stream === "SP" ? "standard" : "near real-time"})</option>)}
          </select>
          <p className="hint">Near-real-time products cover recent weeks; standard products replace them after roughly five months.</p>
        </div>
        <div className="row field">
          <input type="date" aria-label="Fetch start" value={fStart} onChange={(e) => setFStart(e.target.value)} />
          <input type="date" aria-label="Fetch end" value={fEnd} onChange={(e) => setFEnd(e.target.value)} />
        </div>
        <button className="btn primary" disabled={busy} onClick={fetchFirms}>{busy ? "Fetching…" : "Fetch and import"}</button>
        {fetchErr && <p className="state error">{fetchErr}</p>}
        {fetchRes && (
          <>
            <p className="small">{fetchRes.requests_made} request(s), {fetchRes.cache_hits} served from cache. {fetchRes.note}</p>
            {fetchRes.imports.map((r: any) => <Report key={r.batch_id} r={r} />)}
          </>
        )}
      </section>
    </div>
  );
}
