# Method

Technical notes for anyone who wants to check the work or run it elsewhere.

## Data source

ADS-B Exchange serves two public artifact types from `globe.adsbexchange.com` that together allow full historical reconstruction without an API key:

1. **Replay heatmaps**: `/globe_history/YYYY/MM/DD/heatmap/NN.bin.ttf`, one file per half-hour UTC slot (`NN` = UTC hour × 2). Binary, global coverage, roughly 25 to 40 MB per slot. Each 16-byte little-endian record is `int32 icao_hex, int32 lat*1e6, int32 lon*1e6, int16 alt/25, int16 gs*10`. Records with hex `0x0e7f7c9d` are timestamp separators; records with high-byte flags set are non-position entries and are skipped.
2. **Full-day traces**: `/globe_history/YYYY/MM/DD/traces/XX/trace_full_<hex>.json` where `XX` is the last two hex digits. Contains every position for that airframe that day, plus registration, ICAO type designator, model description, FAA registry owner text, and `dbFlags` (bit 3 = LADD, bit 2 = PIA).

ADS-B Exchange does not filter LADD or PIA aircraft, which is why the privacy-flag analysis is possible at all. Requests carry a browser-style Referer header and are retried with backoff; heavier parallelism gets connection-reset.

## Pipeline (`pipeline.py`)

Per requested date:

1. Download the heatmap slots covering 04:00 to 08:30 local (slots 22 through 30 for UTC-7).
2. Parse all records; keep positions within 20 nm of the airport reference point. This yields the day's discovery set (110 to 126 aircraft per morning in the study week).
3. Reduce to candidates: minimum distance ≤ 13 nm and minimum altitude ≤ 20,000 ft anywhere in the window. This drops enroute overflights and, for KTRK, the Reno-Tahoe arrival stream 17 nm east, both of which otherwise dominate the count.
4. Download each candidate's full-day trace.
5. Segment each trace into **ground blocks** at the field: contiguous runs of on-ground points within 1.5 nm of the reference point, split on gaps over 15 minutes. A block whose start falls inside the window with airborne points before it is an arrival; a block whose end falls inside the window with airborne points after it is a departure. Origin and destination come from the nearest known airport (within 6 nm) to the previous or next on-ground fix in the trace; a destination that resolves back to the same field is labeled a local flight.
6. Aircraft that never touch the field are classified `PATTERN/LOW` (below field elevation + 1,200 ft within 3 nm), `TRANSIT_LOW` (within 10 nm below 13,000 ft), or discarded.

Ground blocks are computed over the full day, not the clipped window. This matters: computing them inside the window fabricates a departure at the window edge for any aircraft still parked at 08:30.

## Summary (`summarize.py`)

Re-derives every individual arrival and departure event (an aircraft can have several per morning; a per-aircraft dict would silently overwrite multi-leg shuttles), tags each event as before or after the 07:00 curfew boundary, extracts pre-07:00 airborne activity near the field, and collects per-airframe metadata including LADD and PIA flags.

## Classification

ICAO type designators map to classes (jet, airliner, turboprop, piston, helicopter, glider) via the sets at the top of `pipeline.py`. Airliners near KTRK are Reno traffic and are excluded from field statistics by the candidate filters. Unknown designators default to piston; the study week had none among field operators.

## Validation done during the study week

- Trace-derived arrivals and departures were spot-checked against the live `globe.adsbexchange.com` UI for several airframes.
- The two long ramp holds (transponder on 6:16, departure 7:53; transponder on 6:41, departure 7:41) were confirmed in the raw traces as continuous on-ground blocks, not coverage gaps.
- One apparent 1-minute land-and-depart by a Gulfstream 650 is retained in the data but flagged in the caveats; it is consistent with either a touch-and-go or a ground-flag artifact.

## Known limits

- ADS-B coverage at the field surface is good (ground blocks resolve cleanly) but not guaranteed for every airframe; non-equipped aircraft do not appear.
- The heatmap discovery step samples positions at roughly 30-second intervals per slot, so an aircraft present under a few minutes could in principle be missed; the trace step recovers any that appear in even one heatmap record.
- Altitudes are barometric, uncorrected; the analysis only uses them with kilofeet margins.
- Local time is fixed at UTC-7 in configuration. Adjust `TZ` and `SLOTS` together for other timezones or DST changes.
