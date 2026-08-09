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
import argparse, datetime, glob, html, json, os
from collections import Counter
from pipeline import run_day, TZ
from summarize import build

CLASSES = ["jet", "turboprop", "piston", "helicopter", "glider", "unknown"]


def last_complete_week():
    today = datetime.datetime.now(TZ).date()
    last_sunday = today - datetime.timedelta(days=(today.weekday() + 1) % 7 or 7)
    monday = last_sunday - datetime.timedelta(days=6)
    return [monday + datetime.timedelta(days=i) for i in range(7)]


def collect(dates, note=None, window="full"):
    datestrs = [d.isoformat() for d in dates]
    for ds in datestrs:
        run_day(ds, window=window)
    events, airborne_early, aircraft = build(datestrs, window=window)
    quiet = [e for e in events if e["pre7"]]
    week = {
        "start": datestrs[0], "end": datestrs[-1], "days": datestrs,
        "window_local": "22:00-08:30" if window == "full" else "04:00-08:30",
        "total_ops": len(events),
        "unique_aircraft": len({e["reg"] for e in events}),
        "by_class": dict(Counter(e["cls"] for e in events)),
        "quiet_hours_ops": len(quiet),
        "quiet_hours_by_class": dict(Counter(e["cls"] for e in quiet)),
        "quiet_hours_events": quiet,
        "local_flights": sum(1 for e in events if e["other"] == "LOCAL loop"),
        "ladd_operators": sorted({a["reg"] for a in aircraft
                                  if a["ladd"] and a["reg"] in {e["reg"] for e in events}}),
        "note": note,
        "events": events,
    }
    os.makedirs("data/weekly", exist_ok=True)
    path = f"data/weekly/{week['start']}_{week['end']}.json"
    json.dump(week, open(path, "w"), indent=1)
    print(f"wrote {path}: {week['total_ops']} ops, {week['quiet_hours_ops']} during quiet hours")
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
        note = (f' <span class="tag">{html.escape(w["note"])}</span>' if w.get("note") else "") + wtag
        rows.append(
            f'<tr><td>{fmt_range(w)}{note}</td>'
            f'<td class="num">{w["total_ops"]}</td>'
            f'<td class="num">{w["local_flights"]}</td>'
            f'<td>{w["quiet_hours_ops"]} ({qparts})</td></tr>')
        if w["quiet_hours_events"]:
            ev_rows = "".join(
                f'<tr><td class="mono">{e["date"][5:]}</td><td class="mono num">{e["hm"]}</td>'
                f'<td>{"Landed" if e["ev"] == "ARR" else "Took off"}</td>'
                f'<td class="mono">{html.escape(e["reg"] or "?")}</td>'
                f'<td class="mono">{html.escape(e["type"] or "?")}</td><td>{e["cls"]}</td>'
                f'<td>{html.escape(e["other"])}</td>'
                f'<td>{html.escape(e["own"] or "")}</td></tr>'
                for e in w["quiet_hours_events"])
            details.append(f'''
  <details>
    <summary>{fmt_range(w)}: {w["quiet_hours_ops"]} takeoffs/landings during quiet hours</summary>
    <div class="table-scroll"><table>
      <thead><tr><th>Date</th><th>Time</th><th>What</th><th>Tail #</th><th>Type</th><th>Class</th><th>From / to</th><th>Registered owner</th></tr></thead>
      <tbody>{ev_rows}</tbody>
    </table></div>
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
    (10:00 pm to 8:30 am), collected weekly from public flight-tracking data. The monitored window
    covers the airport's voluntary quiet hours from 10:00 pm through 7:00 am plus the
    first 90 minutes after they end. (Weeks tagged "mornings only" were collected before
    evening coverage began and cover 4:00 to 8:30 am; for evening flights, the time shown
    belongs to the night before the listed date.) Quiet-hours activity is listed
    flight by flight below; the record includes air ambulances and other flights most people
    would not question, and characterizes none of them.
  </p>
  <div class="table-scroll"><table>
    <thead><tr><th>Week</th><th>Takeoffs + landings</th><th>Local round trips</th><th>During quiet hours (before 7:00 am)</th></tr></thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table></div>

  <h2>Quiet-hours detail</h2>
  {"".join(details) if details else "<p>No quiet-hours activity recorded yet.</p>"}

  <div class="foot">
    <p>Updated Mondays by a scheduled job. Underlying per-flight data for each week is in
    <a href="https://github.com/micrui/truckee-flights/tree/main/data/weekly">data/weekly/</a>;
    method and limits in <a href="https://github.com/micrui/truckee-flights/blob/main/METHOD.md">METHOD.md</a>.
    Aircraft without transponders do not appear.</p>
  </div>
</div>
'''
    open("docs/quiet-hours.html", "w").write(page)
    print(f"rendered docs/quiet-hours.html with {len(weeks)} week(s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", help="start,end inclusive (YYYY-MM-DD,YYYY-MM-DD)")
    ap.add_argument("--note", help="label for this entry (e.g. 'Study week')")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--window", choices=["full", "morning"], default="full",
                    help="full = 22:00-08:30 (default); morning = 04:00-08:30 (backfill compatibility)")
    args = ap.parse_args()

    if not args.render_only:
        if args.dates:
            s, e = (datetime.date.fromisoformat(x) for x in args.dates.split(","))
            dates = [s + datetime.timedelta(days=i) for i in range((e - s).days + 1)]
        else:
            dates = last_complete_week()
        collect(dates, note=args.note, window=args.window)
    render()
