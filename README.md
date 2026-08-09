# Truckee dawn flights

What actually flies at Truckee Tahoe Airport (KTRK) in the early morning, from public flight-tracking data. Eight consecutive mornings, August 1 through 8, 2026, 4:00 to 8:30 am PDT.

**The findings as a visual report: [docs/index.html](docs/index.html)** (or the GitHub Pages link if enabled). The rest of this page is the plain-text version.

## Why this exists

Early-morning aircraft noise is a live argument in Truckee, and the complaint is real: people under the arrival and departure paths are being woken up. The argument goes badly because both sides guess at attribution. "The jets ignore the curfew" and "the airport is not the problem" are both claims about who is flying, when, and from where. Those claims are checkable. This repository checks them.

Every number below traces to public ADS-B data and reproduces by running two scripts. See [METHOD.md](METHOD.md).

## Findings

1. **The voluntary curfew holds for the jet fleet.** The airport asks all aircraft not to operate between 10:00 pm and 7:00 am ([Fly Quiet program](https://truckeetahoeairport.com/current-projects/fly-quiet)). Across 8 mornings and 34 business-jet operations by 19 distinct jets (NetJets and Flexjet aircraft, a Gulfstream 650, a Global 7500, Citations, a Challenger 350), zero conventional jets moved before 7:00. Earliest jet movement each morning: 7:03, 7:12, 7:20, 7:29, 7:37, 7:41, 7:59, 8:11. Two jets powered up well before 7:00 (6:16 and 6:41) and held on the ramp until after 7:00 to depart.

2. **One jet is the exception.** A Pilatus PC-24 landed at 6:46 am on August 3 after a short hop from Reno, stopped for 3 minutes, and departed at 6:49. The same aircraft appears 7 times during the week on a Truckee, Reno, Palo Alto rotation.

3. **Pre-7:00 field operations are few, and they are mostly turboprops.** 12 of the 122 takeoffs and landings in the window happened before 7:00: 8 by turboprops (Pilatus PC-12s and Epic E1000s flying Bay Area legs between 6:13 and 6:44), 2 by the PC-24 above, 1 by a piston single, and 1 by a medevac helicopter arriving at 5:12 am. The curfew request covers turboprops the same as jets.

4. **The post-7:00 volume is real.** 110 operations between 7:00 and 8:30 across the 8 mornings, roughly 14 per morning in 90 minutes. About 40 percent are local recreational and training flights that depart KTRK and return to it. For anyone under the pattern, the curfew changes when the noise happens, not how much of it there is.

5. **A meaningful share of early-morning noise is not KTRK traffic.** Every morning, two or three aircraft crossed within 10 nm of the field below 13,000 ft without ever touching the runway: transiting light aircraft at 2,400 to 4,500 ft above the valley floor, turboprops climbing through, and Reno airline arrivals descending 17 nm east. A complaint that attributes all early noise to KTRK operations will not survive a records check, and that failure weakens the parts of the complaint that are correct.

6. **This dataset cannot test "more traffic than ever."** It covers one summer week. The airport district publishes annual operations counts; that is the right source for the trend question, and extending this pipeline across seasons would be the independent check.

## On registration and tracking privacy

Most aircraft here are registered to LLCs and trusts. That alone signals little: holding an aircraft in an entity is standard liability and estate practice at most asset levels, the same as a house or a rental property.

Tracking suppression is a different matter. 7 of the 59 aircraft that operated at KTRK during the study window are enrolled in the FAA's LADD program (Limiting Aircraft Data Displayed), which asks commercial tracking sites such as FlightAware not to display them. The 7 include both of the most frequent pre-7:00 operators. None used the stronger PIA program (temporary anonymized registrations); LADD enrollment broadly increased after the celebrity jet-tracking accounts of 2022 brought attention to the data. LADD is legal, free, and not exclusive to expensive aircraft: the 7 here are a PC-24, a Citation, a Gulfstream 650, three PC-12s, and a TBM, while the wider set of blocked aircraft observed near the field that week also includes a Cirrus, a Cessna 210, and an RV-9 homebuilt. It still deserves a plain statement in a noise dispute: operations at a publicly funded airfield impose a public cost, and display suppression removes the record an affected neighbor would otherwise cite. This analysis is possible because the underlying ADS-B broadcast stays public regardless of display preferences.

## Limitations

- ADS-B only. Aircraft without transponders (some gliders, some older pistons) are invisible here, so light-aircraft counts are floors.
- One week, in summer. Winter and holiday patterns may differ.
- A landing and takeoff recorded 1 to 3 minutes apart can be a quick passenger stop, a touch-and-go, or a ground-sensor artifact.
- Event times at the 8:30 window edge are clipped.
- Ownership strings are FAA registry text and name entities, not people.

## Reproducing

Python 3.9+, standard library only, no API key.

```bash
python3 pipeline.py 2026-08-01 2026-08-02 2026-08-03 2026-08-04 2026-08-05 2026-08-06 2026-08-07 2026-08-08
python3 summarize.py
```

The first run downloads roughly 300 MB of replay data per day from ADS-B Exchange and caches it under `days/`. Outputs land in `data/`. Any airport works by editing the configuration block at the top of `pipeline.py`.

Derived data for the August 2026 study week is committed under [`data/`](data/) so the findings are checkable without rerunning the pipeline.

## Sources

- [ADS-B Exchange](https://globe.adsbexchange.com/) replay heatmaps and per-aircraft traces (unfiltered, includes LADD aircraft)
- [Truckee Tahoe Airport Fly Quiet program](https://truckeetahoeairport.com/current-projects/fly-quiet) and [noise abatement procedures](https://pilots.truckeetahoeairport.com/noise-abatement.html)
- [FAA LADD program](https://www.faa.gov/pilots/ladd) for the display-blocking mechanism
- FAA aircraft registry (via ADS-B Exchange enrichment) for type and ownership

License: MIT.
