"""Idempotent import of FIRMS hotspot files with a full import report.

Duplicate semantics (see docs/methods.md, "Duplicates vs repeated observations"):

* record_key = (data origin, platform, processing stream, UTC acquisition
  minute, latitude and longitude to 1e-6 degrees). Two rows sharing it
  describe the same pixel observation.
* same record_key + same content  -> exact duplicate (skipped, counted)
* same record_key + other content -> conflict (first stored version kept;
  conflict recorded with its raw row for review)
* the same location at another time, from another platform, or from another
  processing stream (NRT vs standard) is a *different* observation and is kept.
  NRT and standard rows are never merged into one series.

The original file bytes are stored gzip-compressed and immutable, so any
accepted, rejected or duplicate row can be traced back to the exact input.
"""

from __future__ import annotations

import gzip
import hashlib
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from ..store.base import Store
from . import firms_schema as fs
from .normalize import PARSER_VERSION, Declaration, FileLevelError, normalize

MAX_ISSUES_IN_REPORT = 25


def import_bytes(store: Store, data: bytes, file_name: str, decl: Declaration) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    existing = store.find_batch_by_sha(sha, decl.data_origin)
    if existing is not None:
        return {
            "status": "already_imported",
            "batch_id": existing["id"],
            "file_name": file_name,
            "file_sha256": sha,
            "message": f"Identical file already imported as batch {existing['id']}; nothing changed.",
            "inserted": 0,
        }

    meta = {
        "file_name": file_name,
        "file_sha256": sha,
        "data_origin": decl.data_origin,
        "declared_source": decl.source_id,
        "source_url": decl.source_url,
        "retrieved_at": decl.retrieved_at,
        "period_start": decl.period_start,
        "period_end": decl.period_end,
        "query_bbox": decl.query_bbox,
        "parser_version": PARSER_VERSION,
        "notes": decl.notes,
    }
    batch_id = store.create_batch(meta, gzip.compress(data, mtime=0))
    started = datetime.now(timezone.utc)

    try:
        res = normalize(data, decl)
    except FileLevelError as exc:
        report = {
            "status": "rejected_file",
            "batch_id": batch_id,
            "file_name": file_name,
            "file_sha256": sha,
            "message": str(exc),
            "inserted": 0,
        }
        store.finish_batch(batch_id, report, status="rejected")
        return report

    acc = res.accepted
    issues: list[dict] = [{**r, "status": "rejected"} for r in res.rejected]

    # ----------------------------------------------------- within-file duplicates
    status = pd.Series("new", index=acc.index, dtype=object)
    if len(acc):
        first_hash = acc.groupby("record_key")["content_hash"].transform("first")
        dup_mask = acc.duplicated("record_key", keep="first")
        status[dup_mask & (acc["content_hash"] == first_hash)] = "duplicate_in_file"
        status[dup_mask & (acc["content_hash"] != first_hash)] = "conflict_in_file"

        # ------------------------------------------------- against stored data
        cand = acc[status == "new"]
        stored = store.existing_keys(cand["record_key"].tolist())
        if stored:
            same = cand["record_key"].map(stored)
            status[cand.index[(same.notna() & (same == cand["content_hash"])).to_numpy()]] = "duplicate_existing"
            status[cand.index[(same.notna() & (same != cand["content_hash"])).to_numpy()]] = "conflict_existing"

    for st in ("duplicate_in_file", "conflict_in_file", "duplicate_existing", "conflict_existing"):
        sub = acc[status == st]
        for _, r in sub.iterrows():
            issues.append(
                {
                    "line": int(r["line_number"]),
                    "status": st,
                    "reasons": [
                        {
                            "duplicate_in_file": "exact duplicate of an earlier row in this file",
                            "conflict_in_file": "same observation key as an earlier row in this file but different attribute values",
                            "duplicate_existing": "observation already stored from a previous import",
                            "conflict_existing": "same observation key as a stored record but different attribute values",
                        }[st]
                    ],
                    "raw": {"record_key": r["record_key"]} if st.startswith("duplicate") else r.drop(labels=["x_ease", "y_ease"]).to_dict(),
                }
            )

    to_insert = acc[status == "new"].copy()
    to_insert["batch_id"] = batch_id
    inserted = store.insert_detections(to_insert)
    store.record_row_issues(batch_id, issues)

    # ----------------------------------------------------- availability windows
    avail_rows = []
    warnings = list(res.file_warnings)
    if len(acc):
        groups = acc.groupby(["platform", "processing_stream"])["acq_date"].agg(["min", "max"]).reset_index()
    else:
        groups = pd.DataFrame(columns=["platform", "processing_stream", "min", "max"])
    if decl.period_start and decl.period_end:
        plats = set(groups["platform"]) if len(groups) else set()
        prod = fs.PRODUCTS.get(decl.source_id) if decl.source_id else None
        if prod:
            plats |= set(prod.platforms)
        for p in sorted(plats):
            streams = set(groups.loc[groups["platform"] == p, "processing_stream"]) or ({prod.stream} if prod else set())
            ps, pe = (decl.platform_periods or {}).get(p, (decl.period_start, decl.period_end))
            for s in sorted(streams):
                avail_rows.append(
                    {
                        "data_origin": decl.data_origin,
                        "platform": p,
                        "processing_stream": s,
                        "date_start": ps,
                        "date_end": pe,
                        "bbox": decl.query_bbox,
                        "batch_id": batch_id,
                        "basis": "declared_query",
                    }
                )
    else:
        for _, g in groups.iterrows():
            avail_rows.append(
                {
                    "data_origin": decl.data_origin,
                    "platform": g["platform"],
                    "processing_stream": g["processing_stream"],
                    "date_start": g["min"],
                    "date_end": g["max"],
                    "bbox": None,
                    "batch_id": batch_id,
                    "basis": "inferred_from_detections",
                }
            )
        if len(groups):
            warnings.append(
                "No query period was declared, so the queried period is inferred from the first and last detection "
                "dates. Days before/after are treated as 'not queried'. Declare --period-start/--period-end for exact semantics."
            )
    store.add_availability(avail_rows)

    status_counts = Counter(status.tolist())
    reason_counts = Counter(reason for r in res.rejected for reason in r["reasons"])
    report = {
        "status": "complete",
        "batch_id": batch_id,
        "file_name": file_name,
        "file_sha256": sha,
        "data_origin": decl.data_origin,
        "declared_source": decl.source_id,
        "parser_version": PARSER_VERSION,
        "rows_read": res.rows_read,
        "rows_valid": len(acc),
        "rows_rejected": len(res.rejected),
        "rejection_reasons": dict(reason_counts),
        "duplicates_in_file": status_counts.get("duplicate_in_file", 0),
        "conflicts_in_file": status_counts.get("conflict_in_file", 0),
        "duplicates_existing": status_counts.get("duplicate_existing", 0),
        "conflicts_existing": status_counts.get("conflict_existing", 0),
        "inserted": inserted,
        "field_warnings": dict(res.field_warnings),
        "file_warnings": warnings,
        "platforms": acc["platform"].value_counts().to_dict() if len(acc) else {},
        "processing_streams": acc["processing_stream"].value_counts().to_dict() if len(acc) else {},
        "versions": sorted(acc["version_raw"].dropna().unique().tolist()) if len(acc) else [],
        "date_range": [acc["acq_date"].min(), acc["acq_date"].max()] if len(acc) else None,
        "availability": [{k: v for k, v in r.items() if k != "bbox"} for r in avail_rows],
        "example_issues": sorted(issues, key=lambda r: r.get("line") or 0)[:MAX_ISSUES_IN_REPORT],
        "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
    }
    store.finish_batch(batch_id, report, status="complete")
    return report
