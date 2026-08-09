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
    events, airborne_early, aircraft, overflights = build(datestrs, window=window)
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
        "airborne_quiet": airborne_early,
        "overflights": overflights,
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
        wk_href = f'weeks/{w["start"]}_{w["end"]}.html'
        rows.append(
            f'<tr><td><a href="{wk_href}">{fmt_range(w)}</a>{note}</td>'
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
    <summary>{fmt_range(w)}: {w["quiet_hours_ops"]} takeoffs/landings during quiet hours (<a href="{wk_href}">timeline</a>)</summary>
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
    first 90 minutes after they end. The Fly Quiet program also defines Shoulder Hour Protocols
    for flights that need the edges of the quiet window; the log records all movements and
    characterizes none. For evening flights (10:00 pm to midnight), the time shown belongs
    to the night before the listed date. Per-aircraft tallies and a full log for every tail
    number are on the <a href="operators.html">operators page</a>. Quiet-hours activity is listed
    flight by flight below; the record includes air ambulances and other flights most people
    would not question, and characterizes none of them.
  </p>
  <div class="table-scroll"><table>
    <thead><tr><th>Week</th><th>Takeoffs + landings</th><th>Local departures</th><th>During quiet hours (before 7:00 am)</th></tr></thead>
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
            payload = {k: w.get(k) for k in ("days", "window_local", "events", "airborne_quiet", "overflights")}
            page_w = (tpl.replace("%%RANGE%%", rng)
                        .replace("%%SUBTITLE%%", sub)
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
  a { color: var(--accent); }
  .foot { margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--grid); color: var(--muted); font-size: 12.5px; max-width: 74ch; }
"""


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
                                            "cls": e["cls"], "own": e["own"], "events": [], "ovf": []})
        a["events"].append(e)
    for o in all_ovf:
        a = ac.setdefault(o["reg"] or "?", {"reg": o["reg"] or "?", "type": o["type"] or "?",
                                            "cls": o["cls"], "own": o["own"], "events": [], "ovf": []})
        a["ovf"].append(o)
    for a in ac.values():
        a["events"].sort(key=lambda e: (e["date"], e["hm"]))
        a["ovf"].sort(key=lambda o: (o["date"], o["first"]))
        a["quiet"] = sum(1 for e in a["events"] if e["pre7"])
        a["qdates"] = sorted({e["date"][5:] for e in a["events"] if e["pre7"]})
        a["q_ovf_min"] = sum(o.get("quiet_min", 0) for o in a["ovf"])
        a["q_ovf_dates"] = sorted({o["date"][5:] for o in a["ovf"] if o.get("quiet_min", 0) > 0})
    ranked = sorted(ac.values(), key=lambda a: (-a["quiet"], -len(a["events"]), -a["q_ovf_min"], a["reg"]))

    rows = []
    for a in ranked:
        if a["quiet"] == 0:
            continue
        badge = ' <span class="tag">blocked on flightaware</span>' if a["reg"] in ladd else ""
        rows.append(f'<tr><td class="mono"><a href="#{html.escape(a["reg"])}">{html.escape(a["reg"])}</a>{badge}</td>'
                    f'<td class="mono">{html.escape(a["type"])}</td><td>{a["cls"]}</td>'
                    f'<td>{a["quiet"]}</td><td>{len(a["events"])}</td>'
                    f'<td class="mono">{", ".join(a["qdates"])}</td>'
                    f'<td>{html.escape(a["own"] or "")}</td></tr>')

    QCHIP = '<span class="tag">quiet hours</span>'
    dw_rows = []
    for a in sorted(ac.values(), key=lambda a: (-a["q_ovf_min"], a["reg"])):
        if a["q_ovf_min"] < 10:
            continue
        badge = ' <span class="tag">blocked on flightaware</span>' if a["reg"] in ladd else ""
        n_sess = sum(1 for o in a["ovf"] if o.get("quiet_min", 0) > 0)
        dw_rows.append(f'<tr><td class="mono"><a href="#{html.escape(a["reg"])}">{html.escape(a["reg"])}</a>{badge}</td>'
                       f'<td class="mono">{html.escape(a["type"])}</td><td>{a["cls"]}</td>'
                       f'<td>{a["q_ovf_min"]}</td><td>{n_sess}</td>'
                       f'<td class="mono">{", ".join(a["q_ovf_dates"])}</td>'
                       f'<td>{html.escape(a["own"] or "")}</td></tr>')

    sections = []
    for a in ranked:
        badge = ' <span class="tag">blocked on flightaware</span>' if a["reg"] in ladd else ""
        log_rows = [(f'{e["date"]} {e["hm"]}',
                     f'<tr><td class="mono">{e["date"]}</td><td class="mono">{e["hm"]}</td>'
                     f'<td>{"Landed" if e["ev"] == "ARR" else "Took off"}</td>'
                     f'<td>{html.escape(e["other"])}</td>'
                     f'<td>{QCHIP if e["pre7"] else ""}</td></tr>')
                    for e in a["events"]]
        log_rows += [(f'{o["date"]} {o["first"]}',
                      f'<tr><td class="mono">{o["date"]}</td><td class="mono">{o["first"]}–{o["last"]}</td>'
                      f'<td>Airborne nearby, no landing ({o["dwell_min"]} min)</td>'
                      f'<td>min {o["min_alt"]:,.0f} ft, {o["min_dist"]} nm from field</td>'
                      f'<td>{QCHIP if o.get("quiet_min", 0) > 0 else ""}</td></tr>')
                     for o in a["ovf"]]
        log_rows.sort(key=lambda kv: kv[0])
        ev_rows = "".join(r for k, r in log_rows)
        sections.append(
            f'<h3 id="{html.escape(a["reg"])}" class="mono">{html.escape(a["reg"])} · {html.escape(a["type"])} · {a["cls"]}{badge}'
            f' <span class="own">{html.escape(a["own"] or "")}</span></h3>'
            f'<div class="table-scroll"><table><thead><tr><th>Date</th><th>Time</th><th>What</th><th>From / to</th><th></th></tr></thead>'
            f'<tbody>{ev_rows}</tbody></table></div>')

    span = f'{min(w["start"] for w in weeks)} to {max(w["end"] for w in weeks)}' if weeks else ""
    page = f"""<meta charset="utf-8">
<title>truckee-flights: operators</title>
<style>{OP_CSS}</style>
<div class="wrap">
  <p class="eyebrow"><a href="./">truckee-flights</a> · <a href="quiet-hours.html">quiet-hours log</a> · operators</p>
  <h1>Operators, by quiet-hours activity</h1>
  <p class="standfirst">
    Every aircraft recorded taking off or landing at the field ({span}), tallied by operations during the
    voluntary quiet hours (10:00 pm to 7:00 am) and listed with its full recorded log below. Includes air
    ambulances and other flights most people would not question; the record characterizes none of them.
    Aircraft marked "blocked on FlightAware" have asked commercial tracking sites not to display them.
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
  weeks are de-duplicated. Aircraft identity and ownership as recorded in the FAA registry.</p></div>
</div>
"""
    open("docs/operators.html", "w").write(page)
    print(f"rendered docs/operators.html: {len(ranked)} aircraft, {len(all_events)} events")


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
