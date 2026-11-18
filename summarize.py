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
import json, glob, os, datetime, statistics
from collections import Counter
from pipeline import hav_nm, nearest_airport, AIRPORT, TZ, window_bounds, t2s, klass, FIELD_ELEV

QUIET_END = (7, 0)   # local; KTRK voluntary Fly Quiet runs 22:00 to 07:00

# On the ground: the transponder's ground flag, OR this rule for a row without the flag.
# Some aircraft never send the flag; others drop it while still standing on the ramp.
# Calibrated 2026-11-17 on the 646 trace files of 48 nights whose aircraft do send the
# flag (METHOD.md): takeoff times within 7 s of the flag's (median), landings within 16 s.
GROUND_NM = 1.5        # within this distance of the reference point
GROUND_KT = 30         # ground speed at or below this, or not sent
GEOM_FIELD = 5830      # geometric altitude of aircraft rolling on the runways, ft
GROUND_FT = 75         # geometric height within this of GEOM_FIELD
BARO_FT = 600          # last resort, no offset known: pressure altitude within this of field elevation
OFFSET_NM, OFFSET_FT = 3, 1000   # rows that set a file's geometric minus pressure offset
# A block edge is a landing or takeoff only when the airborne rows next to it (within 600 s)
# include one that shows flight: this far out, this high (pressure altitude above field
# elevation), or this fast. A transponder switched on at the parking spot sends airborne rows
# while its GPS altitude settles; parked GPS noise reaches 42 kt. On the 48 re-read nights this
# removes 3 movements, all parked aircraft; none is lost even at 1.0 nm, 600 ft and 80 kt.
FLIGHT_NM, FLIGHT_FT, FLIGHT_KT = 1.5, 300, 50


def ground_rule(e, off):
    """A trace row without the ground flag is on the ground when it is close, slow and at
    field level. Field level is tested on geometric altitude; without it, on pressure
    altitude corrected by this file's offset (off); without that, on raw pressure altitude.
    A row with no altitude at all is judged on position and speed alone."""
    if e[4] is not None and e[4] > GROUND_KT:
        return False
    if hav_nm(e[1], e[2], *AIRPORT) > GROUND_NM:
        return False
    geom = e[10] if len(e) > 10 else None
    if geom is not None:
        return abs(geom - GEOM_FIELD) <= GROUND_FT
    if e[3] is None:
        return True
    if off is not None:
        return abs(e[3] + off - GEOM_FIELD) <= GROUND_FT
    return abs(e[3] - FIELD_ELEV) <= BARO_FT


def points(t):
    """A trace's rows as (time, lat, lon, altitude or None, on ground, ground speed)."""
    rows = t.get("trace", [])
    diffs = [e[10] - e[3] for e in rows
             if len(e) > 10 and e[10] is not None and e[3] not in (None, "ground")
             and abs(e[10] - GEOM_FIELD) <= OFFSET_FT and hav_nm(e[1], e[2], *AIRPORT) <= OFFSET_NM]
    off = statistics.median(diffs) if len(diffs) >= 5 else None
    out = []
    for e in rows:
        g = e[3] == "ground" or ground_rule(e, off)
        out.append((t["timestamp"] + e[0], e[1], e[2], None if g else e[3], g, e[4]))
    return out


def movements(allpts):
    """Every landing and takeoff at the field in one trace, any hour: (ev, time, other)."""
    gnd_all = [p for p in allpts if p[4] and hav_nm(p[1], p[2], *AIRPORT) <= 1.5]
    blocks = []
    for p in gnd_all:
        if blocks and p[0] - blocks[-1][-1][0] <= 900:
            blocks[-1].append(p)
        else:
            blocks.append([p])
    def flies(p):
        return (hav_nm(p[1], p[2], *AIRPORT) > FLIGHT_NM or (p[3] is not None and p[3] > FIELD_ELEV + FLIGHT_FT)
                or (len(p) > 5 and p[5] is not None and p[5] > FLIGHT_KT))
    out = []
    for b in blocks:
        b0, b1 = b[0][0], b[-1][0]
        before = [p for p in allpts if not p[4] and b0 - 7200 < p[0] < b0 - 30]
        after = [p for p in allpts if not p[4] and b1 + 30 < p[0] < b1 + 7200]
        # A landing needs the aircraft airborne shortly before the block; a takeoff
        # needs it airborne shortly after. Otherwise the block edge is transponder
        # silence while parked, not a movement. And the airborne rows next to the edge must
        # show flight; otherwise they are a parked transponder (FLIGHT_NM above).
        if before and (b0 - before[-1][0]) <= 600 and any(flies(p) for p in allpts if not p[4] and b0 - 600 <= p[0] < b0):
            pre = [p for p in allpts if p[4] and p[0] < b0 - 600]
            ref = pre[-1] if pre else before[0]
            out.append(("ARR", b0, nearest_airport(ref[1], ref[2])))
        if after and (after[0][0] - b1) <= 600 and any(flies(p) for p in allpts if not p[4] and b1 < p[0] <= b1 + 600):
            post = [p for p in allpts if p[4] and p[0] > b1 + 600]
            ref = post[0] if post else after[-1]
            oth = nearest_airport(ref[1], ref[2])
            if oth.startswith("KTRK"):
                oth = "Local flight (returned to KTRK)"
            out.append(("DEP", b1, oth))
    return out


def outside_hours(ts, window):
    """True when ts falls in no night's recorded window."""
    d = datetime.datetime.fromtimestamp(ts, TZ).date()
    for night in (d, d + datetime.timedelta(days=1)):
        W0, W1 = window_bounds(night.isoformat(), window)
        if W0 <= ts <= W1:
            return False
    return True

# Factual role tags for recurring operators whose line of business is public
# knowledge. Matched against the FAA registry owner string; shown as context,
# never as characterization of any single flight.
OPERATOR_ROLES = [
    ("REGIONAL EMERGENCY MEDICAL", "air ambulance"),
    ("CARE FLIGHT", "air ambulance"),
    ("CAREFLIGHT", "air ambulance"),
    ("BILLINGS FLYING SERVICE", "firefighting contractor"),
    ("A&P HELICOPTERS", "fire/utility contractor"),
    ("A & P HELICOPTERS", "fire/utility contractor"),
    ("SIERRA AERO", "flight school"),
    ("FLYING START AERO", "flight school"),
    ("TRUCKEE TAHOE SOARING", "glider operation"),
    ("BAY AREA SOARING", "glider operation"),
    ("NETJETS", "fractional jet charter"),
    ("FLEXJET", "fractional jet charter"),
]

FIRE_TYPES = {"H47", "CH47"}  # Chinooks: fire work absent better information


def operator_role(own, typecode=""):
    o = (own or "").upper()
    for needle, role in OPERATOR_ROLES:
        if needle in o:
            return role
    if "FIRE" in o:
        return "firefighting contractor"
    if (typecode or "").upper() in FIRE_TYPES:
        return "firefighting (assumed for Chinook)"
    return ""


def build(dates=None, window="morning"):
    """Extract events for the given dates (default: every day under days/).

    Returns (events, airborne_early, aircraft_list, overflights, outside). An event is
    flagged quiet (pre7) when it falls between 22:00 the prior evening and 07:00.
    outside holds every landing and takeoff the night's trace files show outside the
    recorded hours, from every trace file kept for the night, with the real calendar
    date in day; files that overlap (the 2026-08-08 rolling files) count once.
    """
    events, airborne_early, aircraft = [], [], {}
    overflights, outside = [], []
    opsfiles = sorted(glob.glob("days/*/ops.json"))
    if dates is not None:
        wanted = set(dates)
        opsfiles = [f for f in opsfiles if f.split("/")[1] in wanted]

    def meta_of(datestr, reg, typ, own, desc):
        return dict(date=datestr, reg=reg, type=typ, cls=klass(typ), own=(own or "")[:60],
                    role=operator_role(own, typ), desc=(desc or "")[:60])

    def add_outside(meta, h, moves):
        for ev, ts, other in moves:
            if outside_hours(ts, window):
                day = datetime.datetime.fromtimestamp(ts, TZ).date().isoformat()
                outside.append(dict(meta, ev=ev, hm=t2s(ts), other=other, day=day, _t=ts, _h=h))

    for opsfile in opsfiles:
        datestr = opsfile.split("/")[1]
        y, m, d = map(int, datestr.split("-"))
        W0, W1 = window_bounds(datestr, window)
        QE = datetime.datetime(y, m, d, *QUIET_END, tzinfo=TZ).timestamp()
        recs = json.load(open(opsfile))
        for rec in recs:
            h = rec["hex"]
            tpath = f"days/{datestr}/traces/{h}.json"
            if not os.path.exists(tpath):
                continue
            t = json.load(open(tpath))
            allpts = points(t)
            flags = t.get("dbFlags", 0) or 0
            meta = meta_of(datestr, rec["reg"], rec["type"], rec["own"], rec.get("desc"))
            a = aircraft.setdefault(rec["reg"] or h, {
                "reg": rec["reg"], "hex": h, "type": rec["type"], "class": klass(rec.get("type")),
                "desc": rec.get("desc"), "owner": rec["own"],
                "ladd": bool(flags & 8), "pia": bool(flags & 4), "days_seen": []})
            a["days_seen"].append(datestr)

            moves = movements(allpts)
            for ev, ts, other in moves:
                if W0 <= ts <= W1:
                    events.append(dict(meta, ev=ev, hm=t2s(ts), other=other, pre7=ts < QE))
            add_outside(meta, h, moves)

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
                    closest = min(s, key=lambda pd: pd[1])
                    overflights.append(dict(meta, first=t2s(t0), last=t2s(t1),
                                            dwell_min=int((t1 - t0) / 60), quiet_min=quiet_min,
                                            min_alt=min(p[3] for p, d in s),
                                            min_dist=round(min(d for p, d in s), 1),
                                            alt_at_closest=closest[0][3]))

        # trace files of the night that ops.json does not list: outside hours only
        listed = {rec["hex"] for rec in recs}
        for tpath in sorted(glob.glob(f"days/{datestr}/traces/*.json")):
            h = os.path.basename(tpath)[:-5]
            if h in listed:
                continue
            t = json.load(open(tpath))
            meta = meta_of(datestr, t.get("r"), t.get("t"), t.get("ownOp"), t.get("desc"))
            add_outside(meta, h, movements(points(t)))

    events.sort(key=lambda e: (e["date"], e["hm"]))
    outside.sort(key=lambda e: (e["_h"], e["ev"], e["_t"]))
    kept = []
    for e in outside:
        p = kept[-1] if kept else None
        if p and (p["_h"], p["ev"]) == (e["_h"], e["ev"]) and e["_t"] - p["_t"] <= 120:
            continue
        kept.append(e)
    kept.sort(key=lambda e: e["_t"])
    outside = [{k: v for k, v in e.items() if k not in ("_t", "_h")} for e in kept]
    aircraft_list = sorted(aircraft.values(), key=lambda a: (a["class"], a["reg"] or ""))
    return events, airborne_early, aircraft_list, overflights, outside


if __name__ == "__main__":
    events, airborne_early, aircraft_list, overflights, outside = build()
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
