# Method

Technical notes for anyone who wants to check the work or run it elsewhere.

## Data source

ADS-B Exchange serves two public artifact types from `globe.adsbexchange.com` that together allow full historical reconstruction without an API key:

1. **Replay heatmaps**: `/globe_history/YYYY/MM/DD/heatmap/NN.bin.ttf`, one file per half-hour UTC slot (`NN` = UTC hour × 2). Binary, global coverage, roughly 25 to 40 MB per slot. Each 16-byte little-endian record is `int32 icao_hex, int32 lat*1e6, int32 lon*1e6, int16 alt/25, int16 gs*10`. Records with hex `0x0e7f7c9d` are timestamp separators; records with high-byte flags set are non-position entries and are skipped.
2. **Full-day traces**: `/globe_history/YYYY/MM/DD/traces/XX/trace_full_<hex>.json` where `XX` is the last two hex digits. Contains every position for that airframe that day, plus registration, ICAO type designator, model description, FAA registry owner text, and `dbFlags` (bit 3 = LADD, bit 2 = PIA).

ADS-B Exchange does not filter LADD or PIA aircraft, which is why the privacy-flag analysis is possible at all. Requests carry a browser-style Referer header and are retried with backoff; heavier parallelism gets connection-reset.

## Where the collector runs

Since 2026-10-29 the archive serves history files only to a browser session it has verified (the map page shows a verification box; without it every history request gets HTTP 403, "Historical data requires a verified session."). The collector therefore runs on the maintainer's workstation in a real browser window (Chromium with its own saved profile), and the maintainer answers the verification in person: `tools/browser_fetch.py --login` opens the map in the same Chromium, started plainly on that profile with nothing driving the window, and the maintainer closes it once the map has loaded. A verification lasts about an hour. With `TRUCKEE_FETCH_HELPER` set to that script, `pipeline.py` downloads through it: the open map page fetches the same files the map itself loads, with the browser's own session. It keeps at most two downloads in flight, waits a random 0.4 to 1.6 seconds before each one and 2 to 6 seconds after the page opens. Nothing answers the verification automatically: when the session is not verified, the script says so and the night is not collected. The 21 nights ending 2026-10-28 to 2026-11-17 were collected this way on 2026-11-18, and their raw files are kept under `days/`, as are those of every night collected since.

## Pipeline (`pipeline.py`)

Per requested date:

1. Download the heatmap slots covering the recorded hours: 10:00 pm the evening before to 08:30 local (slots 10 through 30 in summer time, 12 through 32 in winter time; `window_slots()` works them out from Pacific time. Until 2026-11-18 the clock was fixed at UTC-7; every night collected before then fell in summer time, so none was affected). Each night is filed under the date of its morning. Study 1 and some early backfills used the original morning window, 04:00 to 08:30 (slots 22 through 30); every week file in `data/weekly/` now carries `window_local` 22:00-08:30.
2. Parse all records; keep positions within 20 nm of the airport reference point. This yields the day's discovery set (110 to 126 aircraft per morning in the study week).
3. Reduce to candidates: minimum distance ≤ 13 nm and minimum altitude ≤ 20,000 ft anywhere in the window. This drops enroute overflights and, for KTRK, the Reno-Tahoe arrival stream 17 nm east, both of which otherwise dominate the count.
4. Download each candidate's full-day trace.
5. Segment each trace into **ground blocks** at the field: contiguous runs of on-ground points within 1.5 nm of the reference point, split on gaps over 15 minutes. A block whose start falls inside the window with airborne points before it is an arrival; a block whose end falls inside the window with airborne points after it is a departure. Origin and destination come from the nearest field in the OurAirports public-domain dataset (`data/airports.csv`, US/CA/MX including heliports, refreshed by `tools/fetch_airports.py`; within 5 nm, airports preferred over heliports unless the point is within 1.5 nm of neither) to the previous or next on-ground fix in the trace; a destination that resolves back to the same field is labeled a local flight.
6. Aircraft that never touch the field are classified `PATTERN/LOW` (below field elevation + 1,200 ft within 3 nm), `TRANSIT_LOW` (within 10 nm below 13,000 ft), or discarded.

Ground blocks are computed over the full day, not the clipped window. This matters: computing them inside the window fabricates a departure at the window edge for any aircraft still parked at 08:30.

`pipeline.py` writes `days/<date>/ops.json`, the index of the night's aircraft. The events in `data/weekly/` are derived by `summarize.py`, with the ground rule below.

## On the ground

A trace row is on the ground when the transponder says so (altitude sent as "ground"), or, for a row without that flag, when all of these hold (`ground_rule()` in `summarize.py`):

- it is within 1.5 nm of the airport reference point;
- its ground speed is 30 kt or less, or no speed is sent;
- it is at field level: geometric (GPS) altitude within 75 ft of 5,830 ft, which is what aircraft rolling on the runways report (the field is 5,904 ft above sea level; the geometric figure is about 75 ft lower); without a geometric altitude, the pressure altitude corrected by that trace's own offset (the median of geometric minus pressure altitude over its rows within 3 nm and within 1,000 ft of field level, when there are at least 5) within 75 ft of 5,830 ft; without an offset, the pressure altitude within 600 ft of 5,904 ft;
- a row with no altitude at all counts on position and speed alone. Without this, the minute after a transponder is switched on at the parking spot reads as airborne and makes a landing that did not happen.

A ground block's start is a landing, and its end a takeoff, only when the airborne rows within 600 s of that edge include at least one that shows flight: farther than 1.5 nm from the reference point, pressure altitude more than 300 ft above field elevation, or ground speed above 50 kt (`FLIGHT_NM`, `FLIGHT_FT`, `FLIGHT_KT` in `summarize.py`). While a parked transponder's GPS settles, its geometric altitude can read 255 to 480 ft below runway level, so the rows read as airborne; parked GPS noise reaches 42 kt in speed. On the 48 re-read nights this test removes 3 movements, all parked aircraft that never moved (one recorded landing, two outside the recorded hours). With 1.0 nm, 600 ft and 80 kt it removes the same 3, so no real takeoff or landing is near the thresholds.

Why: some aircraft never send the ground flag, and others drop it while still standing on the ramp. In the 48 nights below, 25 aircraft-nights (7 aircraft) were at field level within 1.5 nm without a single flagged row, and showed up as "airborne nearby" at about field elevation.

Calibration (2026-11-17), on the 646 trace files of those 48 nights whose aircraft do send the flag: the rule alone, ignoring the flag, against the flag, both with the flight test. 1,315 flagged takeoffs and landings. The rule places takeoffs within 7 s of the flag's time (median; 90% within 19 s) and landings within 16 s (90% within 30 s); 99.7% of matched ones within 2 minutes. It misses 7 and adds 5; some of the added ones are real landings the flag missed. It calls 7 of 4,563 rows on the ground while the aircraft was clearly airborne. Raw pressure altitude in place of the geometric test calls 601 of 6,463 such rows on the ground; a speed limit of 45 kt or more starts swallowing takeoff and landing rolls (40 or more missed). Low approaches and go-arounds are faster than 30 kt and never qualify.

Effect on the 48 nights against the published record: 562 takeoffs and landings unchanged (within 2 minutes; 24 of them moved by 1 or 2 minutes), 2 moved by 3 minutes (a landing 3 minutes earlier, at touchdown; a takeoff 3 minutes later, at the real start of the takeoff roll), 4 removed (landings made by parked transponders: three switching between airborne and ground, one switched on at the parking spot), 20 added outside the three restored nights, and 34 "airborne nearby" entries that were takeoffs, landings or taxiing replaced.

## Summary (`summarize.py`)

Re-derives every individual arrival and departure event (an aircraft can have several per morning; a per-aircraft dict would silently overwrite multi-leg shuttles), tags each event as before or after the 07:00 curfew boundary, extracts pre-07:00 airborne activity near the field, and collects per-airframe metadata including LADD and PIA flags.

## The overwrite guard

On 2026-08-17 a scheduled run re-collected a whole week from the network, got an incomplete download, and replaced three good stored nights with it (34 takeoffs and landings lost; see Re-read below). Three checks now stop that. `pipeline.run_day` refuses a night when any heatmap slice of the window is missing, or when any trace download failed for a reason other than HTTP 404 (a trace not in the archive is skipped and counted); nothing is stored for that night. Every week file is written through `save_week()` in `weekly.py`, which refuses to write when any night already stored would end up with fewer takeoffs and landings or fewer "airborne nearby" entries, and names the night and the counts. `collect()` (`--week`, `--dates`) adds only nights not yet stored and keeps the week's note and re-read record; `--recollect` fetches stored nights again under the same check, and only `--force` lets a write lose entries, printing what it loses. The deliberate re-read from raw files (`--reread`) may remove entries. A refusal ends the run non-zero, so the scheduled job stops before its commit step. `tests/test_guard.py` checks all of this offline.

## Outside the recorded hours

Each kept trace file holds one aircraft's whole UTC day, 5:00 pm the evening before to 5:00 pm Pacific, so it also shows takeoffs and landings outside the recorded hours. `summarize.py` returns them as `outside_events` (with the real calendar date in `day`), from every trace file kept for the night, counting once where files overlap. The operators page shows one, muted, only where it closes a recorded takeoff or landing within 24 hours: a landing right after a recorded takeoff, or a takeoff right before a recorded landing. A day file and its neighbour give continuous coverage inside 24 hours; beyond that there is none. In the 48 nights re-read so far that is 205 rows (176 landings, 29 takeoffs). They count in no tally, total or week figure.

This is not a daytime log. Trace files exist only for aircraft that were near the field during the recorded hours, so outside-hours movements of other aircraft are absent, and a file ends at 5:00 pm. 228 entries still lack the takeoff or landing that followed them (176 a landing, 52 a takeoff); the earlier of the two entries carries a muted note saying so and that it came before the later entry's date and time. In 24 of them the files do show outside-hours takeoffs and landings in between that close nothing on their own, and the note says so; in 57 more, one of the two nights was re-read and the files show none. The rest are mostly aircraft that landed at another airport and came back after their file ended, or aircraft with no file for the following night (only 8% of aircraft-nights have one).

## Re-read of 2026-11-17

`python3 weekly.py --reread` rebuilds, without fetching anything, every stored night that still has its raw files under `days/`: 48 nights from 2026-07-06 to 2026-08-25 (none for 2026-08-16, 2026-08-23 and 2026-08-24, nor for any night from 2026-08-26). The other 66 nights stay as stored. Each re-read week file lists its re-read nights under `reread`.

The re-read restored three nights the record had lost. The scheduled run of 2026-08-17 (commit 4126ecf) re-collected the whole week from the network and wrote the nights ending 2026-08-13 (after 00:32), 2026-08-14 and 2026-08-15 with 34 fewer takeoffs and landings and 24 fewer "airborne nearby" entries than the version before it (commit 7b9fe9b), which the re-read reproduces exactly. The same run wrote the night ending 2026-08-16 with nothing in it; its raw files were not kept, so that zero cannot be checked.

## Classification

ICAO type designators map to classes (jet, airliner, turboprop, piston, helicopter, glider) via the sets at the top of `pipeline.py`. Airliners near KTRK are Reno traffic and are excluded from field statistics by the candidate filters. Unknown designators default to piston; the study week had none among field operators.

## Validation done during the study week

- Trace-derived arrivals and departures were spot-checked against the live `globe.adsbexchange.com` UI for several airframes.
- The two long ramp holds (transponder on 6:16, departure 7:53; transponder on 6:41, departure 7:41) were confirmed in the raw traces as continuous on-ground blocks, not coverage gaps.
- One apparent 1-minute land-and-depart by a Gulfstream 650 is retained in the data but flagged in the caveats; it is consistent with either a touch-and-go or a ground-flag artifact.

## Known limits

- ADS-B coverage at the field surface is good (ground blocks resolve cleanly) but not guaranteed for every airframe; non-equipped aircraft do not appear.
- The heatmap discovery step samples positions at roughly 30-second intervals per slot, so an aircraft present under a few minutes could in principle be missed; the trace step recovers any that appear in even one heatmap record.
- Altitudes shown on the pages are pressure altitudes, uncorrected. Near the field they read 100 to 450 ft low over these nights (aircraft standing at the field reported 5,450 to 5,800 ft against an elevation of 5,904 ft), and the error moves with the weather. The ground rule uses geometric altitude for that reason.
- Short flights inside one ground block: ground rows less than 15 minutes apart join one block, so a flight shorter than that (a glider tow, a quick lap of the pattern) disappears inside it. In the 48 re-read nights, 56 blocks in 33 aircraft-nights hide 101 such flights (13 blocks during recorded hours), mostly glider tow planes and a training Cessna 172. They are not counted anywhere.
- Parked transponders: aircraft standing still often send airborne rows with a pressure altitude (3,620 rows within 1 nm in 97 files of the 48 nights). The ground rule treats them as on the ground; nights not re-read still carry what they produced, including landings that did not happen and early takeoff times.
- Trace files exist only for aircraft seen during the recorded hours, so outside-hours rows cover those aircraft only.
- 66 of the 114 recorded nights (2026-08-16, 2026-08-23, 2026-08-24 and every night from 2026-08-26) were not re-read: their raw files were not kept, and they are as first collected, without the ground rule.
- The files for the night ending 2026-08-08 are rolling 24-hour files that end about 1:00 pm, not UTC days; they overlap the previous night's files, and that night's outside-hours following stops early.
- Local time is fixed at UTC-7 in configuration. Adjust `TZ` and `SLOTS` together for other timezones or DST changes.
