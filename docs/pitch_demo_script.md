# Pitch and demo script (about 4 minutes)

## Pitch (30 seconds)
Twenty-five years of satellite fire detections exist, but they come from
sensors that see fire differently: VIIRS, with 375 m pixels, reports many
more detections than 1 km MODIS for the same burning, and how many more
depends on fire type, season and region. Splice the records and a fire regime
can appear to multiply overnight. Ember Atlas puts MODIS
and VIIRS on one scale for any area you draw, shows how sure it is, and says
plainly when it cannot be sure.

## Demo (be explicit that this is the synthetic world)
1. **Open the app.** Point at the violet band: "This is a synthetic world with
   a known answer, so we can check the method. It is not NASA data."
2. **Area.** The pilot Cerrado area is preselected. Switch the preset to the
   irregular sub-area: counts change because filtering uses the exact polygon.
   Switch back.
3. **The misleading splice.** Open *Raw vs harmonised*. Left chart: Aqua MODIS
   (blue) and S-NPP VIIRS (green) overlap for years; the dashed red line is the
   naive splice — Aqua until its record ends (mid-2021 in this synthetic
   world), then VIIRS — and it jumps about threefold for no physical reason.
4. **The harmonised estimate.** Right chart: VIIRS converted to the Aqua scale
   (black) lies on top of Aqua inside the overlap and continues after Aqua
   ends, with a 90 % band. Below, the ratio through the year with bootstrap
   bands, higher in cropland. Point at the diagnostics (drift, activity
   dependence, zero check).
5. **Validation.** Open *Validation*: blocked leave-one-year-out and spatial
   hold-outs. The candidate beats raw substitution and the single global
   ratio; the negative-binomial regression wins cell by cell but loses on
   weekly totals — we show that rather than hide it. The status chip says
   "synthetic demo", never "validated": on real FIRMS files without coverage
   data the best it can say is "research estimate".
6. **An unusual period.** Set dates to 2019. In *Seasons and anomalies*, the
   August surge is flagged against the same weeks of the other years, with the
   number of tests and the false-discovery control stated. Select that week in
   the calendar: the map shows the detections at their pixel footprint, the
   side panel shows per-sensor counts, coverage, the harmonised value with its
   interval, and which files they came from. Then show all years again: with
   1,700 tests the same surge no longer survives — we explain why instead of
   loosening the threshold.
7. **Export.** Export the harmonised series: the file is named SYNTHETIC_…,
   starts with a provenance line and carries method version, status and
   intervals on every row.
8. **Real data path.** Import tab: upload a FIRMS archive CSV or fetch with
   your own key (kept in memory for the request only). Show an import report
   with rejected rows and duplicates.

## Closing (15 seconds)
Ember Atlas is honest about comparability: it harmonises where evidence
supports it, shows uncertainty and coverage gaps everywhere, and refuses to
calibrate when it should not. Next steps: real MODIS/VIIRS overlap samples,
coverage from Level-2 fire masks, and expert review.
