#!/usr/bin/env python3
"""Weekly collection: run the pipeline for a week of mornings, record every
takeoff and landing in the early-morning window, and regenerate the
quiet-hours flight log (docs/quiet-hours.html).

Default (no arguments): process the most recent complete Monday-to-Sunday
week. This is what the scheduled GitHub Action runs every Monday.

  python3 weekly.py                        # last complete Mon-Sun week
  python3 weekly.py --dates 2026-08-01,2026-08-08   # explicit range (inclusive)
  python3 weekly.py --note "Study week"    # attach a note to the entry
  python3 weekly.py --render-only          # just rebuild docs/quiet-hours.html

Standard library only.
"""
import argparse, datetime, glob, html, json, os, sys
from collections import Counter
from pipeline import run_day, TZ
from summarize import build, operator_role

CLASSES = ["jet", "turboprop", "piston", "helicopter", "glider", "unknown"]


def daily(window="full", date=None, allow_loss=False):
    """Add one night to its week file and re-render, fetching only that night.

    ADS-B Exchange publishes each UTC day shortly after it ends, so the night
    that finished at 08:30 this morning becomes available in the evening. The
    default target is yesterday's UTC date; pass date for a backfill. Only the
    target day is fetched (about 46 requests); the rest of the week is merged
    from the existing week file, which keeps a cacheless CI runner far below
    the archive's rate limit. Runs are idempotent.
    """
    target = date or (datetime.datetime.now(datetime.timezone.utc).date()
                      - datetime.timedelta(days=1))
    monday = target - datetime.timedelta(days=target.weekday())
    sunday = monday + datetime.timedelta(days=6)
    ds = target.isoformat()
    path = f"data/weekly/{monday.isoformat()}_{sunday.isoformat()}.json"
    week = json.load(open(path)) if os.path.exists(path) else new_week(monday, sunday, window)
    if ds in week["days"]:
        print(f"{target} already collected; nothing to do")
        return None
    try:
        add_night(week, ds, window)
    except Exception as e:
        print(f"collection failed (archive may not be published yet): {e}")
        return False
    save_week(path, week, allow_loss)
    print(f"wrote {path}: added {ds}; {week['total_ops']} ops, "
          f"{week['quiet_hours_ops']} during quiet hours")
    return True


def new_week(start, end, window, note=None):
    return {"start": start.isoformat(), "end": end.isoformat(), "days": [], "partial": True,
            "window_local": "22:00-08:30" if window == "full" else "04:00-08:30",
            "note": note, "events": [], "airborne_quiet": [], "overflights": [],
            "outside_events": [], "ladd_operators": []}


def add_night(week, ds, window):
    """Fetch one night and put it into the week, replacing that night's entries if stored.
    daily() and collect() both use it. Nothing is written here; see save_week()."""
    run_day(ds, window=window)
    events, airborne_early, aircraft, overflights, outside = build([ds], window=window)
    week["days"] = sorted(set(week["days"]) | {ds})
    span = (datetime.date.fromisoformat(week["end"]) - datetime.date.fromisoformat(week["start"])).days + 1
    week["partial"] = len(week["days"]) < span
    for key, new in (("events", events), ("airborne_quiet", airborne_early),
                     ("overflights", overflights), ("outside_events", outside)):
        merged = [e for e in week.get(key, []) if e["date"] != ds] + new
        merged.sort(key=entry_key)
        week[key] = merged
    week.update(tally(week["events"], set(week.get("ladd_operators", []))
                      | {a["reg"] for a in aircraft if a["ladd"]}))


def save_week(path, week, allow_loss=False):
    """The one way a week file is written. Unless allow_loss, every night already stored must
    keep at least as many takeoffs and landings and as many "airborne nearby" entries as
    before; otherwise RuntimeError, and nothing is written. On 2026-08-17 a collection run
    replaced three good nights with an incomplete download; this makes that impossible."""
    if os.path.exists(path):
        old = json.load(open(path))
        lost = []
        for d in old.get("days", []):
            counts = [sum(1 for e in w.get(k, []) if e["date"] == d)
                      for w in (old, week) for k in ("events", "overflights")]
            if d not in week.get("days", []) or counts[2] < counts[0] or counts[3] < counts[1]:
                lost.append(f"night {d}: takeoffs and landings {counts[0]} -> {counts[2]}, "
                            f"airborne nearby {counts[1]} -> {counts[3]}")
        if lost and not allow_loss:
            raise RuntimeError(f"{path}: not written, it would lose stored entries: " + "; ".join(lost))
        for line in lost:
            print(f"{path}: losing, as allowed: {line}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(week, f, indent=1)


def entry_key(e):
    return (e["date"], e.get("day", ""), e.get("hm") or e.get("first") or "")


def tally(events, ladd):
    """A week's aggregate figures from its events. collect(), daily() and reread() all use it.
    ladd: registrations on the FAA LADD list; only those with an event this week are kept."""
    quiet = [e for e in events if e["pre7"]]
    regs = {e["reg"] for e in events}
    return {
        "total_ops": len(events),
        "unique_aircraft": len(regs),
        "by_class": dict(Counter(e["cls"] for e in events)),
        "quiet_hours_ops": len(quiet),
        "quiet_hours_by_class": dict(Counter(e["cls"] for e in quiet)),
        "quiet_hours_events": quiet,
        "local_flights": sum(1 for e in events if e["other"].startswith("Local flight")),
        "ladd_operators": sorted(r for r in ladd if r in regs),
    }


REREAD_RULE = ("on the ground: the transponder's ground flag, or within 1.5 nm of the field, "
               "30 kt or slower, at field level (geometric altitude 5,830 ft, within 75 ft)")


def reread():
    """Rebuild, from the raw files kept under days/, every stored night that has them.
    No network. Nights without raw files stay exactly as stored."""
    today = datetime.date.today().isoformat()
    for path in sorted(glob.glob("data/weekly/*.json")):
        week = json.load(open(path))
        stored_week = json.loads(json.dumps(week))
        ladd = set(week.get("ladd_operators", []))
        stored = tally(week["events"], ladd)
        assert all(week[k] == v for k, v in stored.items()), f"{path}: tally() differs from stored figures"
        raw = [d for d in week["days"] if os.path.exists(f"days/{d}/ops.json")]
        if not raw:
            continue
        window = "full" if week.get("window_local", "").startswith("22") else "morning"
        events, airborne_early, aircraft, overflights, outside = build(raw, window=window)
        for key, new in (("events", events), ("airborne_quiet", airborne_early),
                         ("overflights", overflights), ("outside_events", outside)):
            merged = [e for e in week.get(key, []) if e["date"] not in raw] + new
            merged.sort(key=entry_key)
            week[key] = merged
        week.update(tally(week["events"], ladd | {a["reg"] for a in aircraft if a["ladd"]}))
        stored_on = (stored_week.get("reread") or {}).get("on")
        week["reread"] = {"nights": raw, "on": stored_on, "rule": REREAD_RULE}
        if week == stored_week:
            continue   # nothing changed since the last re-read: keep its date, write nothing
        week["reread"]["on"] = today
        save_week(path, week, allow_loss=True)   # the deliberate path: it removes false entries
        print(f"re-read {path}: {len(raw)} nights from raw files; {week['total_ops']} ops, "
              f"{week['quiet_hours_ops']} during quiet hours, {len(outside)} outside the recorded hours")


def last_complete_week():
    today = datetime.datetime.now(TZ).date()
    last_sunday = today - datetime.timedelta(days=(today.weekday() + 1) % 7 or 7)
    monday = last_sunday - datetime.timedelta(days=6)
    return [monday + datetime.timedelta(days=i) for i in range(7)]


def collect(dates, note=None, window="full", frame=None, recollect=False, allow_loss=False):
    """Collect the given dates into their week file. frame=(monday, sunday) pins the week
    file the dates belong to. An existing week file is merged into, not replaced: nights
    already stored are neither fetched nor replaced unless recollect, and note (unless a new
    one is given) and reread are kept. The write goes through save_week()."""
    datestrs = [d.isoformat() for d in dates]
    start = frame[0] if frame else dates[0]
    end = frame[1] if frame else dates[-1]
    path = f"data/weekly/{start.isoformat()}_{end.isoformat()}.json"
    week = json.load(open(path)) if os.path.exists(path) else new_week(start, end, window)
    todo = [ds for ds in datestrs if recollect or ds not in week["days"]]
    for ds in todo:
        add_night(week, ds, window)
    if note is not None:
        week["note"] = note
    save_week(path, week, allow_loss)
    print(f"wrote {path}: fetched {len(todo)} of {len(datestrs)} nights; "
          f"{week['total_ops']} ops, {week['quiet_hours_ops']} during quiet hours")
    return week


def render():
    weeks = []
    for path in sorted(glob.glob("data/weekly/*.json"), reverse=True):
        weeks.append(json.load(open(path)))

    def fmt_range(w):
        s = datetime.date.fromisoformat(w["start"])
        e = datetime.date.fromisoformat(w["end"])
        if s.month == e.month:
            return f"{s.strftime('%b %-d')} to {e.strftime('%-d, %Y')}"
        return f"{s.strftime('%b %-d')} to {e.strftime('%b %-d, %Y')}"

    rows, details = [], []
    for w in weeks:
        qc = w["quiet_hours_by_class"]
        qparts = ", ".join(f"{qc[c]} {c}" for c in CLASSES if qc.get(c)) or "none"
        wtag = "" if w.get("window_local", "").startswith("22") else ' <span class="tag">mornings only</span>'
        if w.get("partial"):
            wtag += f' <span class="tag">in progress: {len(w.get("days", []))} of 7 nights</span>'
        note = (f' <span class="tag">{html.escape(w["note"])}</span>' if w.get("note") else "") + wtag
        wk_href = f'weeks/{w["start"]}_{w["end"]}.html'
        rows.append(
            f'<tr><td><a href="{wk_href}">{fmt_range(w)}</a>{note}</td>'
            f'<td class="num">{w["total_ops"]}</td>'
            f'<td class="num">{w["local_flights"]}</td>'
            f'<td>{w["quiet_hours_ops"]} ({qparts})</td></tr>')
        if w["quiet_hours_events"]:
            def group_of(e):
                r = (e.get("role") or "").lower()
                if "fire" in r:
                    return "fire"
                if "ambulance" in r:
                    return "medical"
                return "other"

            # Rows go in the order things happened and carry the date they happened. A night
            # is filed under the date of its morning; real_date() puts a 10:00 pm to midnight
            # row on the evening before.
            def ev_table(evs):
                def other_cell(e):
                    o = e["other"] or ""
                    if o.startswith("Local flight") or o.startswith("KTRK"):
                        return "local flight"
                    return html.escape(o)
                body = "".join(
                    f'<tr><td class="mono">{real_date(e["date"], e["hm"])[5:]}</td><td class="mono num">{e["hm"]}</td>'
                    f'<td>{"Landed" if e["ev"] == "ARR" else "Took off"}</td>'
                    f'<td class="mono">{html.escape(e["reg"] or "?")}</td>'
                    f'<td class="mono">{type_link(e["type"], e.get("desc"))}</td><td>{e["cls"]}</td>'
                    f'<td>{other_cell(e) if e["ev"] == "ARR" else "KTRK Truckee"}</td>'
                    f'<td>{other_cell(e) if e["ev"] == "DEP" else "KTRK Truckee"}</td>'
                    f'<td>{html.escape(e["own"] or "")}</td></tr>'
                    for e in sorted(evs, key=lambda e: real_time(e["date"], e["hm"])))
                return (f'<div class="table-scroll"><table>'
                        f'<thead><tr><th>Date</th><th>Time</th><th>What</th><th>Tail #</th>'
                        f'<th>Type</th><th>Class</th><th>From</th><th>To</th><th>Registered owner</th></tr></thead>'
                        f'<tbody>{body}</tbody></table></div>')

            groups = {"other": [], "fire": [], "medical": []}
            for e in w["quiet_hours_events"]:
                groups[group_of(e)].append(e)
            sections = []
            for key, title in (("other", "Everyone else"),
                               ("fire", "Fire operations"),
                               ("medical", "Medical flights")):
                if groups[key]:
                    sections.append(f'<h3 class="grp">{title} ({len(groups[key])})</h3>'
                                    + ev_table(groups[key]))
            quiet_ovf = [o for o in w.get("overflights", []) if o.get("quiet_min", 0) > 0]
            if quiet_ovf:
                ovf_body = "".join(
                    f'<tr><td class="mono">{real_date(o["date"], o["first"])[5:]}</td>'
                    f'<td class="mono num">{o["first"]}–{o["last"]}</td>'
                    f'<td class="mono">{html.escape(o["reg"] or "?")}</td>'
                    f'<td class="mono">{type_link(o["type"], o.get("desc"))}</td><td>{o["cls"]}</td>'
                    f'<td class="num">{(o.get("alt_at_closest") or o["min_alt"]):,} ft at {o["min_dist"]} nm</td>'
                    f'<td class="num">{o["quiet_min"]}</td>'
                    f'<td>{html.escape(o.get("own") or "")}</td></tr>'
                    for o in sorted(quiet_ovf, key=lambda o: real_time(o["date"], o["first"])))
                sections.append(
                    f'<h3 class="grp">Airborne nearby, no landing ({len(quiet_ovf)})</h3>'
                    f'<div class="table-scroll"><table>'
                    f'<thead><tr><th>Date</th><th>On scene</th><th>Tail #</th><th>Type</th>'
                    f'<th>Class</th><th>Closest pass</th><th>Quiet-hours min.</th><th>Registered owner</th></tr></thead>'
                    f'<tbody>{ovf_body}</tbody></table></div>')
            counts = ", ".join(f"{len(groups[k])} {lbl}" for k, lbl in
                               (("other", "other"), ("fire", "fire"), ("medical", "medical"))
                               if groups[k])
            details.append(f'''
  <details>
    <summary>{fmt_range(w)}: {w["quiet_hours_ops"]} takeoffs/landings during quiet hours ({counts}) (<a href="{wk_href}">timeline</a>)</summary>
    {"".join(sections)}
  </details>''')

    page = f'''<meta charset="utf-8">
<title>truckee-flights: quiet-hours flight log</title>
<style>
  :root {{
    color-scheme: light;
    --page: #f6f7f9; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
    --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --accent: #2a78d6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      color-scheme: dark;
      --page: #0c0e11; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
      --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --accent: #3987e5;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --page: #0c0e11; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --accent: #3987e5;
  }}
  :root[data-theme="light"] {{
    color-scheme: light;
    --page: #f6f7f9; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
    --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --accent: #2a78d6;
  }}
  body {{ background: var(--page); color: var(--ink); font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    line-height: 1.55; margin: 0; padding: 48px 20px 72px; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  .mono {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }}
  .eyebrow {{ font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted);
    margin: 0 0 10px; font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }}
  .eyebrow a {{ color: inherit; }}
  h1 {{ font-size: clamp(26px, 4vw, 36px); font-weight: 700; letter-spacing: -0.02em; margin: 0 0 14px; }}
  .standfirst {{ font-size: 15.5px; color: var(--ink-2); max-width: 66ch; margin: 0 0 28px; }}
  details.howto {{ margin: -12px 0 24px; max-width: 74ch; }}
  details.howto summary {{ cursor: pointer; font-size: 13.5px; color: var(--accent); }}
  details.howto p {{ font-size: 13.5px; color: var(--ink-2); margin: 8px 0 0; }}
  h3.grp {{ font-size: 13px; font-weight: 650; color: var(--ink-2); margin: 16px 0 4px;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
  th {{ text-align: left; font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
    font-weight: 600; padding: 6px 12px 6px 0; border-bottom: 1px solid var(--axis); }}
  td {{ padding: 7px 12px 7px 0; border-bottom: 1px solid var(--grid); vertical-align: top; }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  .tag {{ font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
    font-family: ui-monospace, "SF Mono", Menlo, monospace; }}
  .table-scroll {{ overflow-x: auto; }}
  details {{ margin: 14px 0; }}
  summary {{ cursor: pointer; font-size: 14px; color: var(--ink-2); }}
  h2 {{ font-size: 18px; font-weight: 700; margin: 40px 0 10px; }}
  a {{ color: var(--accent); }}
  .foot {{ margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--grid); color: var(--muted); font-size: 12.5px; max-width: 74ch; }}
</style>
<div class="wrap">
  <p class="eyebrow"><a href="./">truckee-flights</a> · quiet-hours flight log</p>
  <h1>Quiet-hours flight log</h1>
  <p class="standfirst">
    Every takeoff and landing at Truckee Tahoe Airport in the early-morning window
    (10:00 pm to 8:30 am), collected from public flight-tracking data. The monitored window
    covers the airport's voluntary quiet hours from 10:00 pm through 7:00 am plus the
    first 90 minutes after they end. The Fly Quiet program also defines Shoulder Hour Protocols
    for flights that need the edges of the quiet window. Every entry (a takeoff, a landing or an "airborne nearby"
    entry) shows the calendar date on which it happened. The recorded hours run from 10:00 pm on one day to
    8:30 am on the next, so one night\'s entries carry two dates. Context for night operations: the district\'s own
    <a href="https://pilots.truckeetahoeairport.com/fuel.html">fuel service hours</a> are 7:00 am to
    7:00 pm, with after-hours fueling available by arrangement. Per-aircraft tallies and a full log for every tail
    number are on the <a href="operators.html">operators page</a>. Quiet-hours activity is listed
    flight by flight below, air ambulances and other working flights included.
  </p>
  <p class="standfirst" style="margin-top:-14px;"><b>Collection stopped:</b> the last night in the log is the
  night ending 2026-10-27, and no later night is in it. The log is built from ADS-B Exchange\'s public
  archive of aircraft position reports. Since 2026-10-29 that archive has refused every request from the
  job that collects each night (the answer is HTTP 403). The nightly collection was paused on 2026-11-18.</p>
  <details class="howto">
    <summary>Heard something? How to look up a moment</summary>
    <p>Find the week below, open its timeline, and hover the marks near your time; each night
    runs 22:00 to 08:30, left to right. To see the actual sky at an exact moment, use
    ADS-B Exchange's replay: <span class="mono">globe.adsbexchange.com/?replay=YYYY-MM-DD-HH:MM&amp;lat=39.32&amp;lon=-120.14&amp;zoom=11.5</span>
    with the time in UTC (Pacific time plus 7 hours in summer). Aircraft flying more than
    10 nautical miles out, or with location blocked at the source, are not in this log.</p>
  </details>
  <div class="table-scroll"><table>
    <thead><tr><th>Week</th><th>Takeoffs + landings</th><th>Local departures</th><th>During quiet hours (before 7:00 am)</th></tr></thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table></div>

  <h2>Quiet-hours detail</h2>
  {"".join(details) if details else "<p>No quiet-hours activity recorded yet.</p>"}

  <div class="foot">
    <p>Underlying per-flight data for each week is in
    <a href="https://github.com/micrui/truckee-flights/tree/main/data/weekly">data/weekly/</a>;
    method and limits in <a href="https://github.com/micrui/truckee-flights/blob/main/METHOD.md">METHOD.md</a>.
    Aircraft without transponders do not appear.</p>
    <p>Corrected 2026-11-16: before this date, within each week's tables, entries timed between 10:00 pm
    and midnight came after the morning entries that carry the same date, so 36 takeoffs and landings and
    31 "airborne nearby" entries were listed after an entry that happened later. Every table is now in time
    order: an entry timed between 10:00 pm and midnight comes before the morning entries that carry the same
    date. Only the order of rows changed; the dates shown did not, and each night is still listed under the
    date of its morning.</p>
    <p>Corrected 2026-11-17: until this date, an entry timed between 10:00 pm and midnight showed the date
    of the following morning. Every entry now shows the date on which it happened. In the weekly tables, 82
    of 190 takeoffs and landings and 41 of 175 "airborne nearby" entries now show one day earlier. The order
    of rows did not change. The note of 2026-11-16 says the dates did not change and that each night is
    listed under its morning\'s date; both stopped being true on 2026-11-17. The "Notable this week" lists
    on the chart pages changed the same way: 11 dates on 8 of the 17 pages. On 2 of those pages, two items
    swapped places, putting the list in time order. Each strip on a weekly chart is still one night,
    labelled with the date of its morning; marks left of midnight happened on the evening before.</p>
    <p>Re-read 2026-11-17: the raw position files for 48 of the 114 recorded nights (those ending 2026-07-06
    to 2026-08-25, except 2026-08-16, 2026-08-23 and 2026-08-24) were read again with a changed test for
    when an aircraft is on the ground. The other 66 nights have no raw files and are unchanged. The method
    is in <a href="https://github.com/micrui/truckee-flights/blob/main/METHOD.md">METHOD.md</a>. A scheduled collection on 2026-08-17 had replaced the nights ending 2026-08-13 to
    2026-08-15 with an incomplete download; 34 takeoffs and landings were missing from 2026-08-17 to
    2026-11-17 and are restored. Takeoffs and landings in the recorded hours, whole record: 1,013 before,
    1,066 after. Of those, in quiet hours (10:00 pm to 7:00 am): 190 before, 199 after. Weekly totals that
    changed (week beginning: before, after): 2026-07-06: 105, 109; 2026-07-13: 80, 83; 2026-07-20: 75, 79;
    2026-07-27: 116, 114; 2026-08-03: 79, 83; 2026-08-10: 43, 80; 2026-08-17: 74, 77. Quiet-hours counts
    that changed: 2026-07-13: 23, 24; 2026-07-20: 16, 17; 2026-08-10: 17, 23; 2026-08-17: 14, 15. The night
    ending 2026-08-16 shows no takeoffs or landings. The same collection of 2026-08-17 wrote it. Its raw
    files were not kept, so that zero cannot be checked. The week is marked. The two notes above give
    counts from before this re-read.</p>
  </div>
</div>
'''
    open("docs/quiet-hours.html", "w").write(page)
    print(f"rendered docs/quiet-hours.html with {len(weeks)} week(s)")

    # one interactive timeline page per week
    tpl_path = "templates/week.html"
    if os.path.exists(tpl_path):
        os.makedirs("docs/weeks", exist_ok=True)
        tpl = open(tpl_path).read()
        asc = sorted(weeks, key=lambda w: w["start"])
        for i, w in enumerate(asc):
            rng = fmt_range(w)
            if w.get("window_local", "").startswith("22"):
                sub = "Window: 10:00 pm through 8:30 am."
            else:
                sub = "This week was collected mornings only (4:00 to 8:30 am); evening quiet hours are not shown."
            nav = []
            if i > 0:
                p = asc[i-1]; nav.append(f'<a href="{p["start"]}_{p["end"]}.html">&larr; {fmt_range(p)}</a>')
            if i < len(asc) - 1:
                n = asc[i+1]; nav.append(f'<a href="{n["start"]}_{n["end"]}.html">{fmt_range(n)} &rarr;</a>')
            nav.append('<a href="../quiet-hours.html">all weeks</a>')
            payload = {k: w.get(k) for k in ("start", "end", "days", "window_local", "events", "airborne_quiet", "overflights")}
            page_w = (tpl.replace("%%RANGE%%", rng)
                        .replace("%%SUBTITLE%%", sub)
                        .replace("%%NOTABLES%%", notables(w))
                        .replace("%%PREVNEXT%%", " &middot; ".join(nav))
                        .replace("%%DATA%%", json.dumps(payload, separators=(",", ":"))))
            open(f'docs/weeks/{w["start"]}_{w["end"]}.html', "w").write(page_w)
        print(f"rendered {len(asc)} week timeline page(s)")

    render_operators(weeks)


OP_CSS = """
  body { background: var(--page); color: var(--ink); font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    line-height: 1.55; margin: 0; padding: 48px 20px 72px; }
  :root { color-scheme: light; --page:#f6f7f9; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10); --accent:#2a78d6; }
  @media (prefers-color-scheme: dark) { :root:where(:not([data-theme='light'])) { color-scheme: dark;
    --page:#0c0e11; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781; --grid:#2c2c2a;
    --axis:#383835; --border:rgba(255,255,255,0.10); --accent:#3987e5; } }
  :root[data-theme='dark'] { color-scheme: dark; --page:#0c0e11; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10); --accent:#3987e5; }
  :root[data-theme='light'] { color-scheme: light; --page:#f6f7f9; --surface:#fcfcfb; --ink:#0b0b0b;
    --ink-2:#52514e; --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10); --accent:#2a78d6; }
  .wrap { max-width: 900px; margin: 0 auto; }
  .mono { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; }
  .eyebrow { font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted);
    margin: 0 0 10px; font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; }
  .eyebrow a { color: inherit; }
  h1 { font-size: clamp(26px, 4vw, 36px); font-weight: 700; letter-spacing: -0.02em; margin: 0 0 14px; }
  h3 { font-size: 15px; font-weight: 650; margin: 30px 0 4px; scroll-margin-top: 20px; }
  h3 .own { color: var(--ink-2); font-weight: 400; font-size: 13px; }
  .standfirst { font-size: 15.5px; color: var(--ink-2); max-width: 68ch; margin: 0 0 26px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th { text-align: left; font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted);
    font-weight: 600; padding: 5px 10px 5px 0; border-bottom: 1px solid var(--axis); }
  td { padding: 5px 10px 5px 0; border-bottom: 1px solid var(--grid); vertical-align: top; font-variant-numeric: tabular-nums; }
  .tag { font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
    font-family: ui-monospace, 'SF Mono', Menlo, monospace; }
  .table-scroll { overflow-x: auto; }
  .inferred { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
  table.log td:not(:nth-child(4)) { white-space: nowrap; }   /* only From / to wraps */
  tr.outside td { color: var(--muted); }
  a { color: var(--accent); }
  .foot { margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--grid); color: var(--muted); font-size: 12.5px; max-width: 74ch; }
"""


def type_link(code, desc=""):
    """Type designator as a reference link with the aircraft's name as title."""
    if not code or code in ("?", "GLID"):
        label = html.escape(desc or code or "?")
        return f'<span title="{label}">{html.escape(code or "?")}</span>'
    t = html.escape(desc or code)
    return (f'<a href="https://doc8643.com/aircraft/{html.escape(code)}" title="{t}" '
            f'target="_blank" rel="noopener">{html.escape(code)}</a>')


def _rel_min(hm):
    m = int(hm[:2]) * 60 + int(hm[3:])
    return m - 1320 if m >= 1320 else m + 120


def notables(w):
    """Rule-based weekly notes, identical rules every week, every class.
    Rules: quiet-hours ground stops under 45 minutes; aircraft with four or
    more quiet-hours movements; the longest non-landing presence."""
    items = []
    byreg = {}
    for e in w.get("events", []):
        byreg.setdefault((e["date"], e["reg"]), []).append(e)
    for (date, reg), evs in sorted(byreg.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        evs.sort(key=lambda e: _rel_min(e["hm"]))
        for i in range(len(evs) - 1):
            a, b = evs[i], evs[i+1]
            if a["ev"] == "ARR" and b["ev"] == "DEP" and (a["pre7"] or b["pre7"]):
                dt = _rel_min(b["hm"]) - _rel_min(a["hm"])
                if 0 < dt <= 45:
                    role = f' ({a["role"]})' if a.get("role") else ""
                    name = a.get("desc") or a.get("type") or ""
                    items.append((real_time(date, a["hm"]).isoformat(), a["hm"],
                        f'<b>{html.escape(a["hm"])}, {real_date(date, a["hm"])[5:]}:</b> {html.escape(name)} '
                        f'{html.escape(reg or "")}{html.escape(role)} stopped for {dt} minutes during quiet hours, '
                        f'in from {html.escape(a["other"])}, out to {html.escape(b["other"])}.'))
    qcount = {}
    for e in w.get("events", []):
        if e["pre7"]:
            qcount[e["reg"]] = qcount.get(e["reg"], 0) + 1
    for reg, n in sorted(qcount.items(), key=lambda kv: -kv[1]):
        if n < 4: break
        e = next(x for x in w["events"] if x["reg"] == reg)
        role = f' ({e["role"]})' if e.get("role") else ""
        name = e.get("desc") or e.get("type") or ""
        items.append(("0000", "",
            f'<b>{html.escape(reg or "")}</b> {html.escape(name)}{html.escape(role)} '
            f'made {n} quiet-hours takeoffs or landings this week.'))
    ovf = [o for o in w.get("overflights", []) if o.get("quiet_min", 0) > 0]
    if ovf:
        o = max(ovf, key=lambda o: o["dwell_min"])
        if o["dwell_min"] >= 30:
            role = f' ({o["role"]})' if o.get("role") else ""
            name = o.get("desc") or o.get("type") or ""
            items.append(("0001", "",
                f'<b>Longest non-landing presence:</b> {html.escape(name)} {html.escape(o["reg"] or "")}'
                f'{html.escape(role)}, airborne nearby {o["first"]}–{o["last"]} on {real_date(o["date"], o["first"])[5:]} '
                f'({o["dwell_min"]} minutes) without landing.'))
    items.sort()
    if not items:
        return ""
    lis = "".join(f"<li>{t}</li>" for _, _, t in items[:8])
    return ('<div class="panel" style="margin-top:14px;"><h2 style="font-size:14px;font-weight:650;margin:0 0 6px;">'
            'Notable this week</h2><p style="font-size:12px;color:var(--muted);margin:0 0 8px;">'
            'Generated by the same fixed rules every week, for every aircraft class: ground stops under 45 minutes '
            'during quiet hours; aircraft with four or more quiet-hours movements; the longest non-landing presence.</p>'
            f'<ul style="font-size:13.5px;padding-left:18px;margin:0;">{lis}</ul></div>')


def real_time(date, hm):
    """When a log entry happened. A night is filed under the date of its morning, so an
    entry from the evening part of the window (10:00 pm to midnight) happened on the
    day before the date it carries."""
    d = datetime.date.fromisoformat(date)
    h, m = (int(x) for x in hm.split(":"))
    if h >= 12:
        d -= datetime.timedelta(days=1)
    return datetime.datetime(d.year, d.month, d.day, h, m)


def real_date(date, hm):
    """The calendar date (YYYY-MM-DD) a log entry happened; see real_time()."""
    return real_time(date, hm).date().isoformat()


def role_tag(a):
    r = operator_role(a.get("own"), a.get("type"))
    return f' <span class="tag">{r}</span>' if r else ""


# Where two takeoffs or two landings follow each other, the earlier row says what the log
# lacks. {when} is the later row's real date and time.
GAP_DEP_UNRECORDED = "Return not recorded: 8:30 am to 10:00 pm is not recorded. It came before the next takeoff, {when}."
GAP_DEP_REREAD = ("Return not in the raw files kept, which do not cover every hour. "
                  "It came before the next takeoff, {when}.")
GAP_DEP_ONE_NIGHT = "Return not recorded in this night's data. It came before the next takeoff, {when}."
GAP_ARR_UNRECORDED = ("Next takeoff not recorded: 8:30 am to 10:00 pm is not recorded. "
                      "It came before the landing on {when}.")
GAP_ARR_REREAD = ("Next takeoff not in the raw files kept, which do not cover every hour. "
                  "It came before the landing on {when}.")
GAP_ARR_ONE_NIGHT = "Next takeoff not recorded in this night's data. It came before the landing on {when}."
GAP_UNLISTED = "Other takeoffs and landings outside recorded hours follow before {when} and are not listed."
NOTE_SLOT = "\x00note\x00"   # where an inferred note goes in an event row's From / to cell
# An outside-hours entry closes a recorded one only within this many hours of it: a day file
# and its neighbour give continuous coverage inside 24 hours, and none beyond.
OUTSIDE_MAX_H = 24
OUTSIDE_WHAT = {"ARR": "Landed, outside recorded hours", "DEP": "Took off, outside recorded hours"}


def outside_time(e):
    """When an outside-hours entry happened; it carries its real date in day."""
    h, m = (int(x) for x in e["hm"].split(":"))
    return datetime.datetime.fromisoformat(e["day"]).replace(hour=h, minute=m)


def render_operators(weeks):
    """Aggregate every recorded event across all weeks into per-aircraft logs."""
    seen, all_events = set(), []
    ladd = set()
    for w in weeks:
        ladd.update(w.get("ladd_operators", []))
        for e in w.get("events", []):
            key = (e["date"], e["hm"], e["reg"], e["ev"])
            if key in seen:
                continue
            seen.add(key)
            all_events.append(e)
    out_seen, all_out, reread_nights = set(), [], set()
    for w in weeks:
        reread_nights.update((w.get("reread") or {}).get("nights", []))
        for e in w.get("outside_events", []):
            key = (e["day"], e["hm"], e["reg"], e["ev"])
            if key not in out_seen:
                out_seen.add(key)
                all_out.append(e)
    ovf_seen, all_ovf = set(), []
    for w in weeks:
        for o in w.get("overflights", []):
            key = (o["date"], o["reg"], o["first"])
            if key in ovf_seen:
                continue
            ovf_seen.add(key)
            all_ovf.append(o)

    ac = {}
    for e in all_events:
        a = ac.setdefault(e["reg"] or "?", {"reg": e["reg"] or "?", "type": e["type"] or "?",
                                            "cls": e["cls"], "own": e["own"], "desc": e.get("desc", ""),
                                            "events": [], "ovf": []})
        a["events"].append(e)
    for o in all_ovf:
        a = ac.setdefault(o["reg"] or "?", {"reg": o["reg"] or "?", "type": o["type"] or "?",
                                            "cls": o["cls"], "own": o["own"], "desc": o.get("desc", ""),
                                            "events": [], "ovf": []})
        a["ovf"].append(o)
    for a in ac.values():
        a["events"].sort(key=lambda e: real_time(e["date"], e["hm"]))
        a["ovf"].sort(key=lambda o: real_time(o["date"], o["first"]))
        a["quiet"] = sum(1 for e in a["events"] if e["pre7"])
        a["qdates"] = [d[5:] for d in sorted({real_date(e["date"], e["hm"])
                                              for e in a["events"] if e["pre7"]})]
        a["q_ovf_min"] = sum(o.get("quiet_min", 0) for o in a["ovf"])
        a["q_ovf_dates"] = [d[5:] for d in sorted({real_date(o["date"], o["first"])
                                                   for o in a["ovf"] if o.get("quiet_min", 0) > 0})]
    ranked = sorted(ac.values(), key=lambda a: (-a["quiet"], -len(a["events"]), -a["q_ovf_min"], a["reg"]))

    rows = []
    for a in ranked:
        if a["quiet"] == 0:
            continue
        badge = ' <span class="tag">blocked on flightaware</span>' if a["reg"] in ladd else ""
        rows.append(f'<tr><td class="mono"><a href="#{html.escape(a["reg"])}">{html.escape(a["reg"])}</a>{badge}</td>'
                    f'<td class="mono">{type_link(a["type"], a.get("desc"))}</td><td>{a["cls"]}</td>'
                    f'<td>{a["quiet"]}</td><td>{len(a["events"])}</td>'
                    f'<td class="mono">{", ".join(a["qdates"])}</td>'
                    f'<td>{html.escape(a["own"] or "")}{role_tag(a)}</td></tr>')

    QCHIP = '<span class="tag">quiet hours</span>'
    dw_rows = []
    for a in sorted(ac.values(), key=lambda a: (-a["q_ovf_min"], a["reg"])):
        if a["q_ovf_min"] < 10:
            continue
        badge = ' <span class="tag">blocked on flightaware</span>' if a["reg"] in ladd else ""
        n_sess = sum(1 for o in a["ovf"] if o.get("quiet_min", 0) > 0)
        dw_rows.append(f'<tr><td class="mono"><a href="#{html.escape(a["reg"])}">{html.escape(a["reg"])}</a>{badge}</td>'
                       f'<td class="mono">{type_link(a["type"], a.get("desc"))}</td><td>{a["cls"]}</td>'
                       f'<td>{a["q_ovf_min"]}</td><td>{n_sess}</td>'
                       f'<td class="mono">{", ".join(a["q_ovf_dates"])}</td>'
                       f'<td>{html.escape(a["own"] or "")}{role_tag(a)}</td></tr>')

    # An outside-hours entry is shown only when it closes a recorded one: a landing right
    # after a recorded takeoff, or a takeoff right before a recorded landing. Shown entries
    # count in no tally.
    out_by_reg = {}
    for e in all_out:
        out_by_reg.setdefault(e["reg"] or "?", []).append(e)
    shown_out = 0
    for a in ac.values():
        merged = sorted([(real_time(e["date"], e["hm"]), e, False) for e in a["events"]]
                        + [(outside_time(e), e, True) for e in out_by_reg.get(a["reg"], [])],
                        key=lambda x: x[0])
        a["out"] = []
        for i, (when, e, out) in enumerate(merged):
            if not out:
                continue
            near = datetime.timedelta(hours=OUTSIDE_MAX_H)
            if (e["ev"] == "ARR" and i > 0 and not merged[i - 1][2] and merged[i - 1][1]["ev"] == "DEP"
                    and when - merged[i - 1][0] <= near):
                a["out"].append((when, e))
            elif (e["ev"] == "DEP" and i + 1 < len(merged) and not merged[i + 1][2]
                    and merged[i + 1][1]["ev"] == "ARR" and merged[i + 1][0] - when <= near):
                a["out"].append((when, e))
        shown_out += len(a["out"])

    sections, gaps = [], {}
    for a in ranked:
        badge = ' <span class="tag">blocked on flightaware</span>' if a["reg"] in ladd else ""
        log_rows = [(real_time(e["date"], e["hm"]), e,
                     f'<tr><td class="mono">{real_date(e["date"], e["hm"])}</td><td class="mono">{e["hm"]}</td>'
                     f'<td>{"Landed" if e["ev"] == "ARR" else "Took off"}</td>'
                     f'<td>{html.escape(e["other"])}{NOTE_SLOT}</td>'
                     f'<td>{QCHIP if e["pre7"] else ""}</td></tr>')
                    for e in a["events"]]
        log_rows += [(when, e,
                      f'<tr class="outside"><td class="mono">{e["day"]}</td><td class="mono">{e["hm"]}</td>'
                      f'<td>{OUTSIDE_WHAT[e["ev"]]}</td><td>{html.escape(e["other"])}{NOTE_SLOT}</td><td></td></tr>')
                     for when, e in a["out"]]
        log_rows += [(real_time(o["date"], o["first"]), None,
                      f'<tr><td class="mono">{real_date(o["date"], o["first"])}</td><td class="mono">{o["first"]}–{o["last"]}</td>'
                      f'<td>Airborne nearby, no landing ({o["dwell_min"]} min)</td>'
                      f'<td>{o.get("alt_at_closest") or o["min_alt"]:,.0f} ft at closest pass ({o["min_dist"]} nm)</td>'
                      f'<td>{QCHIP if o.get("quiet_min", 0) > 0 else ""}</td></tr>')
                     for o in a["ovf"]]
        log_rows.sort(key=lambda kv: kv[0])
        # Two takeoffs in a row, or two landings in a row: the earlier row gets a note saying
        # what the log lacks. Two entries filed under one night key are in one recorded night
        # (their printed dates can differ across midnight); otherwise hours the log does not
        # record lie between them. Where outside-hours entries of the missing kind lie between
        # them (not listed, as they close nothing), the note says so; otherwise, where either
        # night was re-read from raw files, it says the files do not show one.
        out_rows, prev, prev_when, prev_i = [], None, None, None
        unlisted = [(outside_time(x), x["ev"]) for x in out_by_reg.get(a["reg"], [])]
        for when, e, row in log_rows:
            if e is not None:
                if prev is not None and prev["ev"] == e["ev"]:
                    dep = e["ev"] == "DEP"
                    if any(prev_when < t < when and ev != e["ev"] for t, ev in unlisted):
                        text, name = GAP_UNLISTED, "unlisted"
                    elif prev["date"] in reread_nights or e["date"] in reread_nights:
                        text, name = (GAP_DEP_REREAD if dep else GAP_ARR_REREAD), "re-read"
                    elif prev["date"] == e["date"]:
                        text, name = (GAP_DEP_ONE_NIGHT if dep else GAP_ARR_ONE_NIGHT), "one night"
                    else:
                        text, name = (GAP_DEP_UNRECORDED if dep else GAP_ARR_UNRECORDED), "not recorded"
                    note = text.format(when=when.strftime("%Y-%m-%d %H:%M"))
                    out_rows[prev_i] = out_rows[prev_i].replace(
                        NOTE_SLOT, f'<span class="inferred">{html.escape(note)}</span>')
                    gaps[(name, e["ev"])] = gaps.get((name, e["ev"]), 0) + 1
                prev, prev_when, prev_i = e, when, len(out_rows)
            out_rows.append(row)
        out_rows = [r.replace(NOTE_SLOT, "") for r in out_rows]
        ev_rows = "".join(out_rows)
        sections.append(
            f'<h3 id="{html.escape(a["reg"])}" class="mono">{html.escape(a["reg"])} · {type_link(a["type"], a.get("desc"))} · {a["cls"]}{badge}'
            f' <span class="own">{html.escape(a.get("desc") or "")}{" · " if a.get("desc") and a.get("own") else ""}{html.escape(a["own"] or "")}</span>{role_tag(a)}</h3>'
            f'<div class="table-scroll"><table class="log"><thead><tr><th>Date</th><th>Time</th><th>What</th><th>From / to</th><th></th></tr></thead>'
            f'<tbody>{ev_rows}</tbody></table></div>')

    nights = sorted({d for w in weeks for d in w.get("days", [])})
    span = f"{nights[0]} to {nights[-1]}" if nights else ""
    page = f"""<meta charset="utf-8">
<title>truckee-flights: operators</title>
<style>{OP_CSS}</style>
<div class="wrap">
  <p class="eyebrow"><a href="./">truckee-flights</a> · <a href="quiet-hours.html">quiet-hours log</a> · operators</p>
  <h1>Operators, by quiet-hours activity</h1>
  <p class="standfirst">
    This page lists every aircraft recorded taking off or landing at the airport ({span}), including air
    ambulances and other working flights, and tallies each aircraft's takeoffs and landings during the
    airport's voluntary quiet hours, 10:00 pm to 7:00 am. The log records and counts takeoffs and landings
    from 10:00 pm to 8:30 am the next day. Each entry shows its calendar date, so one night's entries show two
    dates. On the 48 of the 114 recorded nights whose raw position files were kept, a grey row marked
    "outside recorded hours" shows the landing after a recorded takeoff, or the takeoff before a recorded
    landing, if the aircraft's file shows it within 24 hours. These rows are not counted. Other takeoffs and
    landings between 8:30 am and 10:00 pm are not listed. Where an aircraft's list shows two takeoffs in a row,
    or two landings in a row, the earlier entry carries a note: the landing or takeoff in between is not
    recorded, and the note gives the date and time before which it happened. Aircraft marked "blocked on FlightAware" have asked commercial tracking sites not to display them.
  </p>
  <div class="table-scroll"><table>
    <thead><tr><th>Tail #</th><th>Type</th><th>Class</th><th>Quiet-hours ops</th><th>All ops</th><th>Quiet-hours dates</th><th>Registered owner</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
  <h1 style="font-size:20px;margin-top:44px;">Airborne over the area during quiet hours, no landing</h1>
  <p class="standfirst">Aircraft that spent 10 or more minutes airborne within 10 nautical miles of the field
  during quiet hours without taking off or landing here, ranked by total time. Separate visits are counted as
  separate sessions. Includes working aircraft (fire and utility helicopters) alongside recreational overflights.</p>
  <div class="table-scroll"><table>
    <thead><tr><th>Tail #</th><th>Type</th><th>Class</th><th>Quiet-hours minutes</th><th>Sessions</th><th>Dates</th><th>Registered owner</th></tr></thead>
    <tbody>{"".join(dw_rows)}</tbody>
  </table></div>
  <h1 style="font-size:20px;margin-top:44px;">Per-aircraft logs</h1>
  {"".join(sections)}
  <div class="foot"><p>Generated from the weekly log data in
  <a href="https://github.com/micrui/truckee-flights/tree/main/data/weekly">data/weekly/</a>; overlapping
  weeks are de-duplicated. Aircraft identity and ownership as recorded in the FAA registry.</p>
  <p>Corrected 2026-11-16: each night is listed under the date of its morning, so a takeoff or landing
  between 10:00 pm and midnight is listed under the following day's date. Before this date the page did
  not say so, and it placed those entries after that date's morning entries, out of time order. The 82
  takeoffs and landings and 45 "airborne nearby" entries affected are now placed before that date's
  morning entries, in time order, and the introductory paragraph states the rule. Before this date the
  page also gave the period covered as ending 2026-11-01; the last night recorded is the night ending
  2026-10-27.</p>
  <p>Corrected 2026-11-17: until this date, an entry timed between 10:00 pm and midnight showed the date of
  the following morning. The introductory paragraph and the note of 2026-11-16 described that rule; it no
  longer applies, and every entry shows the date on which it happened. 82 of 1,013 takeoffs and landings
  and 45 of 639 "airborne nearby" entries now show one day earlier. In the two summary tables, 22 of 61
  "Quiet-hours dates" cells and 15 of 34 "Dates" cells changed the same way. The order of rows did not
  change. The note of 2026-11-16 said all 82 and 45 had been out of time order. 22 and 8 had been: each
  was listed after a later entry of the same aircraft. The other 60 and 37 were in time order: no later
  entry of the same aircraft was listed ahead of them.</p>
  <p>Re-read 2026-11-17: the raw position files for 48 of the 114 recorded nights, those ending 2026-07-06
  to 2026-08-25 except 2026-08-16, 2026-08-23 and 2026-08-24, were read again. The other 66 have none and
  are unchanged. Before, an aircraft counted as on the ground only when its own position reports said so;
  some aircraft never say so. Now a position report within 1.5 nautical miles of the airport, at 30 knots
  or less and at runway height, also counts as on the ground. A takeoff or landing now counts only if the
  position reports next to it show the aircraft in flight. Those nights gained 23 takeoffs and landings and
  lost 4 landings (each an aircraft parked with its equipment switched on); 26 changed time by 1 to 3
  minutes. 31 "airborne nearby" entries were removed (19 were takeoffs or landings, 12 were aircraft moving
  on the ground); 3 were shortened. On 2026-08-17 a scheduled collection had replaced the nights ending
  2026-08-13 to 2026-08-15 with an incomplete download; 34 takeoffs and landings were missing until
  2026-11-17. They are restored from the raw files, with 22 "airborne nearby" entries. Totals, before and
  after: takeoffs and landings 1,013 and 1,066; "airborne nearby" entries 639 and 630; aircraft listed 644
  and 659. New: 205 rows marked "outside recorded hours" (176 landings, 29 takeoffs), not counted. 101 of
  the landings follow a takeoff labelled "Local flight". Rows marking a missing takeoff or landing fell
  from 343 to 228. The two notes above give counts from before this re-read. <a href="https://github.com/micrui/truckee-flights/blob/main/METHOD.md">METHOD.md</a> has the method and
  the check of the new ground test.</p>
  <p>Changed 2026-11-18: until this date the page had 228 separate rows, each placed between two takeoffs
  in a row or two landings in a row, saying that a landing or takeoff was missing. Those rows are gone. The
  same statement is now a note in the earlier of the two entries, with the date and time of the later
  entry. No entry, count or time changed on 2026-11-18.</p></div>
</div>
"""
    open("docs/operators.html", "w").write(page)
    print(f"rendered docs/operators.html: {len(ranked)} aircraft, {len(all_events)} events, "
          f"{sum(gaps.values())} notes on a takeoff or landing the log lacks "
          f"({', '.join(f'{n} {k} {ev}' for (k, ev), n in sorted(gaps.items()))}), "
          f"{shown_out} outside-hours rows shown")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", action="store_true",
                    help="collect the last complete Mon-Sun week (the old weekly mode)")
    ap.add_argument("--dates", help="start,end inclusive (YYYY-MM-DD,YYYY-MM-DD)")
    ap.add_argument("--date", help="backfill one night (YYYY-MM-DD) into its week")
    ap.add_argument("--note", help="label for this entry (e.g. 'Study week')")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--reread", action="store_true",
                    help="rebuild stored nights from the raw files under days/ (no network), then render")
    ap.add_argument("--recollect", action="store_true",
                    help="with --dates or --week: fetch stored nights again too (still refused if it loses entries)")
    ap.add_argument("--force", action="store_true",
                    help="write even if stored entries are lost; prints what is lost")
    ap.add_argument("--window", choices=["full", "morning"], default="full",
                    help="full = 22:00-08:30 (default); morning = 04:00-08:30 (backfill compatibility)")
    args = ap.parse_args(argv)

    failed = False
    try:
        if args.reread:
            reread()
        elif not args.render_only:
            if args.dates:
                s, e = (datetime.date.fromisoformat(x) for x in args.dates.split(","))
                dates = [s + datetime.timedelta(days=i) for i in range((e - s).days + 1)]
                collect(dates, note=args.note, window=args.window,
                        recollect=args.recollect, allow_loss=args.force)
            elif args.week:
                collect(last_complete_week(), note=args.note, window=args.window,
                        recollect=args.recollect, allow_loss=args.force)
            else:
                # None = already collected (idle, fine); False = collection failed
                date = datetime.date.fromisoformat(args.date) if args.date else None
                failed = daily(window=args.window, date=date, allow_loss=args.force) is False
    except RuntimeError as e:
        # A refused write or an incomplete download: stop non-zero so the workflow commits nothing.
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)
    render()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
