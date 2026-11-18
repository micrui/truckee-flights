#!/usr/bin/env python3
"""Collect and classify early-morning flight operations near an airport,
from ADS-B Exchange public replay data.

For each requested date this script:
  1. downloads the half-hour "heatmap" replay slices for the analysis window,
  2. parses them to find every ADS-B aircraft inside RADIUS_NM of the airport,
  3. downloads the full-day position trace for each low/close candidate,
  4. segments each trace into ground blocks at the field to derive arrival and
     departure times, origins, and destinations.

Python 3.9+, standard library only. Usage:

    python3 pipeline.py 2026-08-01 2026-08-02 ... 2026-08-08

Outputs: days/<date>/ (cached downloads + per-day ops.json) and
data/all_ops.json. Downloads are cached; reruns are cheap.
"""
import struct, math, json, os, sys, time, random, datetime
import urllib.request
from zoneinfo import ZoneInfo
import concurrent.futures as cf

# ---------------------------------------------------------------- configuration
AIRPORT = (39.3200, -120.1396)      # KTRK Truckee Tahoe
FIELD_ELEV = 5904                   # ft MSL
TZ = ZoneInfo("America/Los_Angeles")   # Pacific time, summer and winter

# Collection windows, in local clock time. "full" covers the evening portion of quiet
# hours plus the morning: 22:00 the prior evening through 08:30. That whole span maps
# to one contiguous run of half-hour UTC slots on the MORNING's UTC date; which slots
# depends on the season (summer: 05:00 to 15:30 UTC, slots 10 to 30; winter: 06:00 to
# 16:30 UTC, slots 12 to 32), so window_slots() works them out from the clock.
# "morning" is the original 04:00-08:30 window, kept for backfill consistency.
WINDOWS = {
    "full":    {"start": (-1, 22, 0), "end": (0, 8, 30)},
    "morning": {"start": (0, 4, 0),  "end": (0, 8, 30)},
}
WINDOW = ((4, 0), (8, 30))          # legacy alias (morning); summarize imports it
RADIUS_NM = 20                      # discovery radius
CAND_NM, CAND_ALT = 13, 20000       # candidate filter: closer than / lower than
BASE = "https://globe.adsbexchange.com"
HDRS = {"Referer": BASE + "/", "User-Agent": "Mozilla/5.0 (noise-attribution research)"}

# ICAO type designators -> class. Extend as needed; unknowns default to piston.
JET = {'C68A','E55P','C25A','C25B','C25C','C25M','C500','C501','C510','C525','C550','C551','C560','C56X','C650','C680','C700','C750','CL30','CL35','CL60','GLF3','GLF4','GLF5','GLF6','GA5C','GA6C','GLEX','GL5T','GL7T','E35L','E545','E550','LJ31','LJ35','LJ40','LJ45','LJ60','LJ70','LJ75','F2TH','F900','FA10','FA20','FA50','FA5X','FA6X','FA7X','FA8X','HDJT','HA4T','PRM1','BE40','H25B','H25C','G150','G280','SF50','EA50','PC24'}
AIRLINER = {'E75L','E75S','E170','E190','CRJ2','CRJ7','CRJ9','B737','B738','B739','B38M','A319','A320','A321','A20N','A21N','B752','DH8D','B763','B77W'}
TURBOPROP = {'PC12','TBM7','TBM8','TBM9','B350','BE20','BE9L','BE9T','BE10','U21','C208','C08T','EPIC','E1000','P46T','M600','KODI','PC6T','SW4','DHC6','AT8T'}
HELI = {'AS50','AS55','B212','B06','B407','B429','B505','EC20','EC30','EC35','EC45','EC55','EC75','R22','R44','R66','S76','UH1','H500','MD50','A109','A139','H60','EH10','H47','CH47','S61','S64','K126','B105','B412','AS32'}
GLIDER = {'GLID','DISC','AS29','AS33','ASW2','LS8','LS4','LS6','DG80','DG40','DG1T','DUOD','ARCU','ARCP','NIMB','VENT','JS3','JS1','PK20','SZD5','ASK2','G103','SF25','ASW7','ASG2','ASG3','TWSH','PIK2','GROB'}

# Airports used to name origins and destinations: the OurAirports public-domain
# dataset (data/airports.csv, US/CA/MX, includes heliports), fetched by
# tools/fetch_airports.py. Positions farther than MATCH_NM from any entry keep
# raw coordinates rather than guessing.
MATCH_NM = 5.0
_AIRPORTS = None

def _load_airports():
    global _AIRPORTS
    if _AIRPORTS is None:
        import csv
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "airports.csv")
        _AIRPORTS = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                _AIRPORTS.append((r["ident"], float(r["lat"]), float(r["lon"]),
                                  r["label"], r["type"]))
    return _AIRPORTS


def hav_nm(lat1, lon1, lat2, lon2):
    r = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))

def nearest_airport(lat, lon):
    """Nearest field within MATCH_NM. Airports outrank heliports unless the
    point is truly at a helipad (no airport within 1.5 nm), since fixed-wing
    aircraft cannot use helipads but medevac helicopters do."""
    apt, apt_d, heli, heli_d = None, MATCH_NM, None, MATCH_NM
    for ident, alat, alon, label, typ in _load_airports():
        if abs(alat - lat) > 0.12 or abs(alon - lon) > 0.15:
            continue
        d = hav_nm(lat, lon, alat, alon)
        if "heli" in typ:
            if d < heli_d:
                heli, heli_d = (ident, label, typ), d
        else:
            if d < apt_d:
                apt, apt_d = (ident, label, typ), d
    best = apt if apt and (heli is None or apt_d <= 1.5 or apt_d <= heli_d) else heli
    if best is None:
        return f"({lat:.2f},{lon:.2f})"
    ident, label, typ = best
    out = f"{ident} {label}" if label and not ident.startswith(label) else (label or ident)
    if "heli" in typ:
        out += " (heliport)"
    return out


def klass(t):
    if not t: return "unknown"
    if t in JET: return "jet"
    if t in AIRLINER: return "airliner"
    if t in TURBOPROP: return "turboprop"
    if t in HELI: return "helicopter"
    if t in GLIDER: return "glider"
    return "piston"

FAILED = {}   # path -> why its last fetch failed: the HTTP status code, or the error text


def fetch(url, path):
    """Download url to path with retry/backoff; cached if path already exists.
    On failure returns None and records the reason in FAILED[path]."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    for attempt in range(4):
        req = urllib.request.Request(url, headers=HDRS)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    data = gzip.decompress(data)
            open(path, "wb").write(data)
            FAILED.pop(path, None)
            return path
        except Exception as e:
            if getattr(e, "code", None) == 404:   # not in the archive; retrying will not help
                FAILED[path] = 404
                return None
            print(f"  fetch attempt {attempt + 1}/4 {url.rsplit('/', 1)[-1]}: {e}",
                  file=sys.stderr)
            if attempt == 3:
                print(f"  fetch FAILED {url}", file=sys.stderr)
                FAILED[path] = getattr(e, "code", None) or str(e)
                return None
            if getattr(e, "code", None) == 429:
                time.sleep(30 * (attempt + 1))  # rate limited: back off hard
            else:
                time.sleep(3 * (attempt + 1) + random.random() * 2)

class FetchHelper:
    """Downloads through tools/browser_fetch.py (named by TRUCKEE_FETCH_HELPER): one process
    for the night, started at the first download that is not cached. It reads lines
    "url<TAB>path" and answers each with "OK<TAB>path<TAB>bytes", "404<TAB>path" or
    "ERR<TAB>path<TAB>reason". It exits 3 when the browser session is not verified."""

    def __init__(self, script):
        self.script, self.proc, self.err = script, None, None

    def fetch_all(self, jobs):
        todo = [(u, p) for u, p in jobs if not (os.path.exists(p) and os.path.getsize(p) > 0)]
        if todo:
            if self.proc is None:
                import subprocess, tempfile
                self.err = tempfile.TemporaryFile("w+")
                self.proc = subprocess.Popen([sys.executable, self.script], text=True,
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=self.err)
            try:
                self.proc.stdin.write("".join(f"{u}\t{p}\n" for u, p in todo))
                self.proc.stdin.flush()
            except BrokenPipeError:
                self.died()
            for _ in todo:
                line = self.proc.stdout.readline()
                if not line:
                    self.died()
                kind, path, *why = line.rstrip("\n").split("\t")
                if kind == "OK":
                    FAILED.pop(path, None)
                elif kind == "404":
                    FAILED[path] = 404
                else:
                    FAILED[path] = why[0] if why else "fetch helper error"
                    print(f"  fetch FAILED {path}: {FAILED[path]}", file=sys.stderr)
        return [p if os.path.exists(p) and os.path.getsize(p) > 0 else None for u, p in jobs]

    def died(self):
        code = self.proc.wait()
        self.err.seek(0)
        lines = [l for l in self.err.read().splitlines() if l.strip()]
        why = lines[-1] if lines else f"fetch helper exited with code {code}"
        raise RuntimeError(why if code == 3 else f"fetch helper exited with code {code}: {why}")

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
            try:
                self.proc.wait(120)
            except Exception:
                self.proc.kill()
                self.proc.wait()
            self.err.close()
            self.proc = None


def fetch_all(jobs, workers, helper):
    """Fetch (url, path) jobs; returns path or None for each, failures in FAILED."""
    if helper:
        return helper.fetch_all(jobs)
    with cf.ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda j: fetch(*j), jobs))


def t2s(ts):
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%H:%M")

# ---------------------------------------------------------------- per-day run

def window_bounds(datestr, window="morning"):
    """Return (W0, W1) epoch bounds for the window ending on datestr's morning."""
    y, m, d = map(int, datestr.split("-"))
    base = datetime.date(y, m, d)
    w = WINDOWS[window]
    d0 = base + datetime.timedelta(days=w["start"][0])
    d1 = base + datetime.timedelta(days=w["end"][0])
    W0 = datetime.datetime(d0.year, d0.month, d0.day, w["start"][1], w["start"][2], tzinfo=TZ).timestamp()
    W1 = datetime.datetime(d1.year, d1.month, d1.day, w["end"][1], w["end"][2], tzinfo=TZ).timestamp()
    return W0, W1


def window_slots(datestr, window="morning"):
    """The archive's half-hour UTC slots, on datestr's UTC date, that cover the window."""
    W0, W1 = window_bounds(datestr, window)
    y, m, d = map(int, datestr.split("-"))
    midnight = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp()
    return range(int((W0 - midnight) // 1800), int(-((midnight - W1) // 1800)))


def run_day(datestr, window="morning"):
    script = os.environ.get("TRUCKEE_FETCH_HELPER")
    helper = FetchHelper(script) if script else None
    try:
        return _run_day(datestr, window, helper)
    finally:
        if helper:
            helper.close()


def _run_day(datestr, window, helper):
    y, m, d = datestr.split("-")
    daydir = f"days/{datestr}"
    os.makedirs(f"{daydir}/heatmap", exist_ok=True)
    os.makedirs(f"{daydir}/traces", exist_ok=True)
    SLOTS = window_slots(datestr, window)

    got = fetch_all([(f"{BASE}/globe_history/{y}/{m}/{d}/heatmap/{s}.bin.ttf",
                      f"{daydir}/heatmap/{s}.bin") for s in SLOTS], 3, helper)
    print(f"{datestr}: heatmap slices {sum(1 for g in got if g)}/{len(SLOTS)}",
          file=sys.stderr)
    # An incomplete download must not be stored as a night: refuse it (weekly.py exits non-zero).
    missing = [s for s, g in zip(SLOTS, got) if not g]
    if missing:
        raise RuntimeError(f"{datestr}: heatmap slices missing: {missing}; nothing stored")

    # Parse heatmap slices. Entry format (16 bytes LE): int32 hex, int32 lat*1e6,
    # int32 lon*1e6, int16 alt/25, int16 gs*10. hex 0x0e7f7c9d is a timestamp
    # separator; high byte flags mark non-position entries.
    cand = {}
    for slot in SLOTS:
        path = f"{daydir}/heatmap/{slot}.bin"
        if not os.path.exists(path):
            continue
        data = open(path, "rb").read()
        for k in range(len(data) // 16):
            off = k * 16
            hexv, lat_i, lon_i = struct.unpack_from("<Iii", data, off)
            if hexv == 0x0e7f7c9d or (hexv & 0xff000000):
                continue
            lat, lon = lat_i/1e6, lon_i/1e6
            if abs(lat - AIRPORT[0]) > 0.34 or abs(lon - AIRPORT[1]) > 0.45:
                continue
            dd = hav_nm(lat, lon, *AIRPORT)
            if dd > RADIUS_NM:
                continue
            alt = struct.unpack_from("<h", data, off + 12)[0] * 25
            c = cand.setdefault(f"{hexv:06x}", [1e9, 1e9])
            c[0] = min(c[0], dd)
            c[1] = min(c[1], alt)
    sel = [h for h, (dd, alt) in cand.items() if dd <= CAND_NM and alt <= CAND_ALT]
    print(f"{datestr}: {len(cand)} aircraft in {RADIUS_NM}nm, {len(sel)} candidates", file=sys.stderr)
    if not cand:
        # Zero aircraft within 20nm over the whole window never happens for real;
        # it means the heatmap fetches failed (archive missing, or the source is
        # blocking this network). Refuse to record it as a quiet night.
        raise RuntimeError(f"{datestr}: heatmap yielded zero aircraft; "
                           "fetch blocked or archive not published")

    got = fetch_all([(f"{BASE}/globe_history/{y}/{m}/{d}/traces/{h[-2:]}/trace_full_{h}.json",
                      f"{daydir}/traces/{h}.json") for h in sel], 4, helper)
    print(f"{datestr}: traces {sum(1 for g in got if g)}/{len(sel)}", file=sys.stderr)
    failed = [(h, FAILED.get(f"{daydir}/traces/{h}.json")) for h, g in zip(sel, got) if not g]
    not_found = [h for h, why in failed if why == 404]
    if not_found:
        print(f"{datestr}: {len(not_found)} traces not in the archive (HTTP 404), skipped: "
              f"{', '.join(not_found)}", file=sys.stderr)
    others = [f"{h} ({why})" for h, why in failed if why != 404]
    if others:
        raise RuntimeError(f"{datestr}: trace downloads failed: {', '.join(others)}; nothing stored")

    W0, W1 = window_bounds(datestr, window)
    out = []
    for h in sel:
        path = f"{daydir}/traces/{h}.json"
        if not os.path.exists(path):
            continue
        try:
            t = json.load(open(path))
        except Exception:
            continue
        base = t["timestamp"]
        allpts = [(base + e[0], e[1], e[2], (None if e[3] == "ground" else e[3]), e[3] == "ground")
                  for e in t.get("trace", [])]
        pts = [p for p in allpts if W0 <= p[0] <= W1]
        near = [p + (hav_nm(p[1], p[2], *AIRPORT),) for p in pts]
        near = [p for p in near if p[5] <= 15]
        if not near:
            continue
        gnd = [p for p in near if p[4] and p[5] <= 1.5]
        airborne = [p for p in near if not p[4] and p[3] is not None]
        min_alt = min((p[3] for p in airborne), default=None)
        min_dist = min(p[5] for p in near)

        ops, times = [], {}
        if gnd:
            # contiguous ground blocks at the field over the FULL day (gap > 15 min splits)
            gnd_all = [p for p in allpts if p[4] and hav_nm(p[1], p[2], *AIRPORT) <= 1.5]
            blocks = []
            for p in gnd_all:
                if blocks and p[0] - blocks[-1][-1][0] <= 900:
                    blocks[-1].append(p)
                else:
                    blocks.append([p])
            for b in blocks:
                b0, b1 = b[0][0], b[-1][0]
                if b1 < W0 or b0 > W1:
                    continue
                airborne_before = [p for p in allpts if not p[4] and b0 - 7200 < p[0] < b0 - 30]
                airborne_after = [p for p in allpts if not p[4] and b1 + 30 < p[0] < b1 + 7200]
                if airborne_before and W0 <= b0 <= W1:
                    ops.append("ARR"); times["arr"] = t2s(b0)
                    pre = [p for p in allpts if p[4] and p[0] < b0 - 600]
                    ref = pre[-1] if pre else airborne_before[0]
                    times["from"] = nearest_airport(ref[1], ref[2])
                if airborne_after and W0 <= b1 <= W1:
                    ops.append("DEP"); times["dep"] = t2s(b1)
                    post = [p for p in allpts if p[4] and p[0] > b1 + 600]
                    ref = post[0] if post else airborne_after[-1]
                    dest = nearest_airport(ref[1], ref[2])
                    times["to"] = "LOCAL(returned)" if dest.startswith("KTRK") else dest
            if not ops:
                ops.append("GROUND_ONLY"); times["gnd"] = f"{t2s(gnd[0][0])}-{t2s(gnd[-1][0])}"
        else:
            lows = [p for p in airborne if p[3] < FIELD_ELEV + 1200 and p[5] < 3]
            if lows:
                ops.append("PATTERN/LOW"); times["low"] = f"{t2s(lows[0][0])}-{t2s(lows[-1][0])}"
            elif min_dist <= 10 and (min_alt or 99999) <= 13000:
                ops.append("TRANSIT_LOW")
            else:
                continue  # high overflight or nearby-airport traffic; out of scope
        out.append({"date": datestr, "hex": h, "reg": t.get("r"), "type": t.get("t"),
                    "class": klass(t.get("t")), "desc": t.get("desc"), "own": t.get("ownOp"),
                    "dbFlags": t.get("dbFlags", 0),
                    "ops": ops, "times": times, "first": t2s(near[0][0]), "last": t2s(near[-1][0]),
                    "min_dist": round(min_dist, 1), "min_alt": min_alt})
    out.sort(key=lambda r: r["first"])
    json.dump(out, open(f"{daydir}/ops.json", "w"), indent=1)
    return out

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    os.makedirs("data", exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    window = "full" if "--full" in sys.argv else "morning"
    allops = []
    for ds in args:
        allops.extend(run_day(ds, window=window))
    json.dump(allops, open("data/all_ops.json", "w"), indent=1)
    for r in allops:
        tt = " ".join(f"{k}={v}" for k, v in r["times"].items())
        print(f'{r["date"]} {r["first"]}-{r["last"]} {r["reg"] or r["hex"]:8s} {r["type"] or "?":5s} '
              f'{r["class"]:10s} {"/".join(r["ops"]):12s} {tt} | {r["own"] or "?"}')
