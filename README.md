# truckee-flights

Independent, reproducible flight data for Truckee Tahoe Airport (KTRK).

Public discussion of the airport, its traffic, its noise, and its voluntary curfew generally draws on two kinds of sources: the airport district's own reporting (board presentations, the [Fly Quiet program](https://truckeetahoeairport.com/current-projects/fly-quiet), the [PlaneNoise comment portal](https://www.planenoise.com/ktrk/)) and the Town of Truckee's planning documents (the [General Plan Noise Element](https://www.townoftruckee.gov/DocumentCenter/View/738/Chapter-8---Noise-Element-PDF)). Both are useful. Neither can be independently checked by a resident without tooling.

Aircraft broadcast their positions publicly over ADS-B, so most factual claims about who flies here, when, and from where are directly verifiable. This repository is tooling and data for doing that. It is not affiliated with the airport district, the town, or any operator, and it takes no position on airport policy.

## What is here

| Path | Contents |
|---|---|
| `pipeline.py` | Rebuilds an operations log for any date range from ADS-B Exchange public replay data. Python 3.9+, standard library only, no API key. Works for any airport by editing the configuration block. |
| `summarize.py` | Reduces pipeline output to discrete arrival and departure events and per-aircraft records, including FAA privacy-program flags. |
| `weekly.py` | Collects the most recent complete week and rebuilds the quiet-hours flight log. Runs every Monday via GitHub Actions (`.github/workflows/weekly.yml`); can also be run by hand. |
| `data/` | Derived datasets for completed studies, committed so findings are checkable without rerunning anything. |
| `docs/` | The public site, served at [micrui.github.io/truckee-flights](https://micrui.github.io/truckee-flights/): landing page, [quiet-hours flight log](https://micrui.github.io/truckee-flights/quiet-hours.html), and study reports. |
| `METHOD.md` | Data formats, algorithms, validation, and known limits. |

## Study 1: early-morning operations, August 1 to 8, 2026

Full report with an interactive timeline: **[micrui.github.io/truckee-flights/study-2026-08-dawn.html](https://micrui.github.io/truckee-flights/study-2026-08-dawn.html)** (source in [docs/](docs/)). Question examined: during the voluntary quiet hours (10:00 pm to 7:00 am), who actually operates at the field, and how much early-morning aircraft noise is attributable to KTRK operations at all.

Summary of what the data shows for those 8 mornings, 4:00 to 8:30 am PDT:

1. **122 takeoffs and landings** at the field in the window, by 59 distinct aircraft. 12 of the 122 occurred before 7:00.
2. **Business jets operated after 7:00.** 34 jet operations by 19 distinct jets; zero conventional jet movements before 7:00. Earliest jet movement each morning: 7:03, 7:12, 7:20, 7:29, 7:37, 7:41, 7:59, 8:11. Two jets powered up well before 7:00 (6:16 and 6:41) and departed after it.
3. **One light jet is the exception:** a Pilatus PC-24 landed at 6:46 and departed at 6:49 on August 3, one stop in a Truckee, Reno, Palo Alto rotation it flew all week.
4. **The other pre-7:00 operations:** 8 turboprop movements (Pilatus PC-12s and Epic E1000s on Bay Area legs between 6:13 and 6:44), 1 piston departure, and 1 medevac helicopter arrival at 5:12 am.
5. **Post-7:00 volume is substantial:** 110 operations between 7:00 and 8:30, roughly 14 per morning. About 40 percent are local flights that depart KTRK and return to it, including flight-school and glider-tow activity.
6. **Not all early noise is KTRK traffic.** Each morning, two or three aircraft crossed within 10 nm of the field below 13,000 ft without landing: transiting light aircraft 2,400 to 4,500 ft above the valley, turboprops climbing through, and Reno airline arrivals descending 17 nm east. An operations log for KTRK does not capture these.
7. **Tracking visibility varies by aircraft.** 7 of the 59 operators are enrolled in the FAA's [LADD program](https://www.faa.gov/pilots/ladd), which asks commercial tracking sites such as FlightAware not to display them; the 7 include the two most frequent pre-7:00 operators. None used the stronger PIA anonymized-registration program. The raw ADS-B broadcast remains public regardless, which is what this analysis uses. Aircraft ownership entities (LLCs and trusts) are standard registration practice and are reported here as they appear in the FAA registry.

Scope notes: one summer week; ADS-B-equipped aircraft only, so light-aircraft counts are floors; this study does not measure sound and cannot test multi-year traffic trends. The district's five-year operations summaries (roughly 55 percent piston, 17 percent jet per [staff reports to the board](https://citizenportal.ai/articles/6669375/california/nevada-county/truckee-town/California/Board-schedules-noise-deep-dive-hears-traffic-and-complaint-statistics-community-calls-for-easier-complaint-process)) are the source for long-run mix and trend questions.

## Ongoing: quiet-hours flight log

A scheduled job collects the most recent complete Monday-to-Sunday week every Monday and publishes it to the [quiet-hours flight log](https://micrui.github.io/truckee-flights/quiet-hours.html): total early-morning takeoffs and landings, local round trips, and a flight-by-flight list of everything that moved during the voluntary quiet hours. Per-week data lands in [`data/weekly/`](data/weekly/). The monitored window is 4:00 to 8:30 am; late-evening quiet hours are not yet covered.

## Reproducing

```bash
python3 pipeline.py 2026-08-01 2026-08-02 2026-08-03 2026-08-04 2026-08-05 2026-08-06 2026-08-07 2026-08-08
python3 summarize.py
```

The first run downloads roughly 300 MB of replay data per day from ADS-B Exchange and caches it under `days/`. Outputs land in `data/`. Details and validation notes in [METHOD.md](METHOD.md).

## Sources

- [ADS-B Exchange](https://globe.adsbexchange.com/) replay heatmaps and per-aircraft traces (unfiltered; includes LADD aircraft)
- FAA aircraft registry (type, ownership) via ADS-B Exchange enrichment
- [Truckee Tahoe Airport District: Fly Quiet](https://truckeetahoeairport.com/current-projects/fly-quiet) and [noise abatement procedures](https://pilots.truckeetahoeairport.com/noise-abatement.html)
- [FAA LADD program](https://www.faa.gov/pilots/ladd)

License: MIT.
