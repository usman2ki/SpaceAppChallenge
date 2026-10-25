"""Common temporal windows.

* day  : UTC calendar day (FIRMS ``acq_date`` is UTC).
* week : 7-day bins anchored on 1 January of each year: bin k covers
         day-of-year 7k+1 .. 7k+7 for k = 0..50, and bin 51 covers day 358 to
         the end of the year (8 or 9 days). Anchoring per year keeps
         "week 34" at the same seasonal position in every year, which is what
         seasonal baselines and anomaly tests need. Bin length is reported
         (``n_days``) so rates are never confused with totals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def week_index(dates: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    d = pd.to_datetime(dates)
    doy = np.asarray(d.dayofyear if isinstance(d, pd.DatetimeIndex) else d.dt.dayofyear)
    return np.minimum((doy - 1) // 7, 51)


def window_start(dates, window: str) -> pd.Series:
    """Window start date (YYYY-MM-DD) for each date. Computed once per unique
    date and mapped back, which keeps it fast for millions of detections."""
    if window not in ("day", "week"):
        raise ValueError("window must be 'day' or 'week'")
    s = pd.Series(dates)
    if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
        s = pd.to_datetime(s).dt.strftime("%Y-%m-%d")
    s = s.astype(str)
    if window == "day":
        return s.reset_index(drop=True)
    uniq = pd.unique(s)
    d = pd.to_datetime(pd.Series(uniq), format="%Y-%m-%d")
    k = week_index(d)
    starts = (pd.to_datetime(d.dt.year.astype(str) + "-01-01", format="%Y-%m-%d") + pd.to_timedelta(k * 7, unit="D")).dt.strftime("%Y-%m-%d")
    lookup = dict(zip(uniq, starts))
    return s.map(lookup).reset_index(drop=True)


def window_table(date_start: str, date_end: str, window: str) -> pd.DataFrame:
    """All windows intersecting [date_start, date_end] with their clipped day span."""
    days = pd.date_range(date_start, date_end, freq="D")
    if len(days) == 0:
        return pd.DataFrame(columns=["window_start", "window_end", "n_days", "year", "week", "doy_mid"])
    ws = window_start(days, window).to_numpy()
    df = pd.DataFrame({"day": days, "window_start": ws})
    g = df.groupby("window_start", sort=True)["day"].agg(["min", "max", "count"]).reset_index()
    g.columns = ["window_start", "first_day", "last_day", "n_days"]
    full_end = []
    for s in g["window_start"]:
        s_dt = pd.Timestamp(s)
        if window == "day":
            full_end.append(s_dt)
        else:
            k = int(week_index(pd.DatetimeIndex([s_dt]))[0])
            end = pd.Timestamp(f"{s_dt.year}-12-31") if k == 51 else s_dt + pd.Timedelta(days=6)
            full_end.append(end)
    g["window_end"] = [e.strftime("%Y-%m-%d") for e in full_end]
    g["full_days"] = [(pd.Timestamp(e) - pd.Timestamp(s)).days + 1 for s, e in zip(g["window_start"], g["window_end"])]
    g["complete"] = g["n_days"] == g["full_days"]
    st = pd.to_datetime(g["window_start"])
    g["year"] = st.dt.year
    g["week"] = week_index(st) if window == "week" else st.dt.dayofyear
    mid = st + pd.to_timedelta((g["full_days"] - 1) / 2.0, unit="D")
    g["doy_mid"] = mid.dt.dayofyear + 0.0
    return g[["window_start", "window_end", "n_days", "full_days", "complete", "year", "week", "doy_mid"]]
