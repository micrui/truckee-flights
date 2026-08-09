#!/usr/bin/env python3
"""Reduce pipeline output to discrete events and aggregate statistics.

Reads days/<date>/ops.json and days/<date>/traces/*.json produced by
pipeline.py. As a script, processes every day present and writes:

  data/events.json     every arrival/departure at the field in the window, plus
                       aircraft airborne nearby before the quiet-hours boundary
  data/aircraft.json   one row per unique aircraft with type, owner, and FAA
                       privacy-program flags (LADD block list, PIA)

As a library, build(dates) does the same for a specific list of dates and
returns the structures instead of writing the study-wide files.
"""
import json, glob, os, datetime
from collections import Counter
from pipeline import hav_nm, nearest_airport, AIRPORT, TZ, window_bounds, t2s, klass

QUIET_END = (7, 0)   # local; KTRK voluntary Fly Quiet runs 22:00 to 07:00


def build(dates=None, window="morning"):
    """Extract events for the given dates (default: every day under days/).

    Returns (events, airborne_early, aircraft_list). An event is flagged quiet
    (pre7) when it falls between 22:00 the prior evening and 07:00.
    """
    events, airborne_early, aircraft = [], [], {}
    overflights = []
    opsfiles = sorted(glob.glob("days/*/ops.json"))
    if dates is not None:
        wanted = set(dates)
        opsfiles = [f for f in opsfiles if f.split("/")[1] in wanted]

    for opsfile in opsfiles:
        datestr = opsfile.split("/")[1]
        y, m, d = map(int, datestr.split("-"))
        W0, W1 = window_bounds(datestr, window)
        QE = datetime.datetime(y, m, d, *QUIET_END, tzinfo=TZ).timestamp()
        for rec in json.load(open(opsfile)):
            h = rec["hex"]
            tpath = f"days/{datestr}/traces/{h}.json"
            if not os.path.exists(tpath):
                continue
            t = json.load(open(tpath))
            base = t["timestamp"]
            allpts = [(base + e[0], e[1], e[2], (None if e[3] == "ground" else e[3]), e[3] == "ground")
                      for e in t.get("trace", [])]
            flags = t.get("dbFlags", 0) or 0
            meta = dict(date=datestr, reg=rec["reg"], type=rec["type"],
                        cls=klass(rec.get("type")), own=(rec["own"] or "")[:60])
            a = aircraft.setdefault(rec["reg"] or h, {
                "reg": rec["reg"], "hex": h, "type": rec["type"], "class": klass(rec.get("type")),
                "desc": rec.get("desc"), "owner": rec["own"],
                "ladd": bool(flags & 8), "pia": bool(flags & 4), "days_seen": []})
            a["days_seen"].append(datestr)

            gnd_all = [p for p in allpts if p[4] and hav_nm(p[1], p[2], *AIRPORT) <= 1.5]
            blocks = []
            for p in gnd_all:
                if blocks and p[0] - blocks[-1][-1][0] <= 900:
                    blocks[-1].append(p)
                else:
                    blocks.append([p])
            for b in blocks:
                b0, b1 = b[0][0], b[-1][0]
                before = [p for p in allpts if not p[4] and b0 - 7200 < p[0] < b0 - 30]
                after = [p for p in allpts if not p[4] and b1 + 30 < p[0] < b1 + 7200]
                if before and W0 <= b0 <= W1:
                    pre = [p for p in allpts if p[4] and p[0] < b0 - 600]
                    ref = pre[-1] if pre else before[0]
                    events.append(dict(meta, ev="ARR", hm=t2s(b0),
                                       other=nearest_airport(ref[1], ref[2]), pre7=b0 < QE))
                if after and W0 <= b1 <= W1:
                    post = [p for p in allpts if p[4] and p[0] > b1 + 600]
                    ref = post[0] if post else after[-1]
                    oth = nearest_airport(ref[1], ref[2])
                    if oth.startswith("KTRK"):
                        oth = "Local flight (returned to KTRK)"
                    events.append(dict(meta, ev="DEP", hm=t2s(b1), other=oth, pre7=b1 < QE))

            earlypts = [p for p in allpts if W0 <= p[0] < QE and not p[4] and p[3] is not None
                        and p[3] <= 13000 and hav_nm(p[1], p[2], *AIRPORT) <= 10]
            if earlypts:
                airborne_early.append(dict(meta, first=t2s(earlypts[0][0]), last=t2s(earlypts[-1][0]),
                                           min_alt=min(p[3] for p in earlypts)))

            # non-landing low presences across the whole window (overflights, tours,
            # transits). A gap over 20 minutes splits separate visits into separate
            # sessions so two passes hours apart never read as one long dwell.
            had_event = any(ev["date"] == datestr and ev["reg"] == rec["reg"] for ev in events)
            if not had_event:
                presence = [(p, hav_nm(p[1], p[2], *AIRPORT)) for p in allpts
                            if W0 <= p[0] <= W1 and not p[4] and p[3] is not None and p[3] <= 13000]
                presence = [(p, d) for p, d in presence if d <= 10]
                sessions, cur = [], []
                for p, d in presence:
                    if cur and p[0] - cur[-1][0][0] > 1200:
                        sessions.append(cur); cur = []
                    cur.append((p, d))
                if cur: sessions.append(cur)
                for s in sessions:
                    t0, t1 = s[0][0][0], s[-1][0][0]
                    quiet_min = max(0, int((min(t1, QE) - t0) / 60)) if t0 < QE else 0
                    overflights.append(dict(meta, first=t2s(t0), last=t2s(t1),
                                            dwell_min=int((t1 - t0) / 60), quiet_min=quiet_min,
                                            min_alt=min(p[3] for p, d in s),
                                            min_dist=round(min(d for p, d in s), 1)))

    events.sort(key=lambda e: (e["date"], e["hm"]))
    aircraft_list = sorted(aircraft.values(), key=lambda a: (a["class"], a["reg"] or ""))
    return events, airborne_early, aircraft_list, overflights


if __name__ == "__main__":
    events, airborne_early, aircraft_list, overflights = build()
    os.makedirs("data", exist_ok=True)
    json.dump({"events": events, "airborne_pre7": airborne_early},
              open("data/events.json", "w"), indent=1)
    json.dump(aircraft_list, open("data/aircraft.json", "w"), indent=1)

    pre = Counter(e["cls"] for e in events if e["pre7"])
    post = Counter(e["cls"] for e in events if not e["pre7"])
    print(f"{len(events)} field operations, {len(aircraft_list)} unique aircraft")
    for c in ["jet", "turboprop", "piston", "helicopter", "glider", "unknown"]:
        print(f"  {c:11s} before 07:00: {pre.get(c, 0):2d}   07:00 onward: {post.get(c, 0):3d}")
    op_regs = {e["reg"] for e in events}
    ladd = [a for a in aircraft_list if a["ladd"] and a["reg"] in op_regs]
    print("field operators on the FAA LADD block list:", len(ladd), ":",
          ", ".join(sorted(a["reg"] for a in ladd)))
