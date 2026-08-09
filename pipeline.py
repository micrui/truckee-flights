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
import concurrent.futures as cf

# ---------------------------------------------------------------- configuration
AIRPORT = (39.3200, -120.1396)      # KTRK Truckee Tahoe
FIELD_ELEV = 5904                   # ft MSL
TZ = datetime.timezone(datetime.timedelta(hours=-7))   # PDT
WINDOW = ((4, 0), (8, 30))          # local time window of interest
SLOTS = range(22, 31)               # half-hour UTC slots covering the window
                                    # (slot = UTC hour * 2; 22 -> 11:00 UTC -> 04:00 PDT)
RADIUS_NM = 20                      # discovery radius
CAND_NM, CAND_ALT = 13, 20000       # candidate filter: closer than / lower than
BASE = "https://globe.adsbexchange.com"
HDRS = {"Referer": BASE + "/", "User-Agent": "Mozilla/5.0 (noise-attribution research)"}

# ICAO type designators -> class. Extend as needed; unknowns default to piston.
JET = {'C68A','E55P','C25A','C25B','C25C','C25M','C500','C501','C510','C525','C550','C551','C560','C56X','C650','C680','C700','C750','CL30','CL35','CL60','GLF3','GLF4','GLF5','GLF6','GA5C','GA6C','GLEX','GL5T','GL7T','E35L','E545','E550','LJ31','LJ35','LJ40','LJ45','LJ60','LJ70','LJ75','F2TH','F900','FA10','FA20','FA50','FA5X','FA6X','FA7X','FA8X','HDJT','HA4T','PRM1','BE40','H25B','H25C','G150','G280','SF50','EA50','PC24'}
AIRLINER = {'E75L','E75S','E170','E190','CRJ2','CRJ7','CRJ9','B737','B738','B739','B38M','A319','A320','A321','A20N','A21N','B752','DH8D','B763','B77W'}
TURBOPROP = {'PC12','TBM7','TBM8','TBM9','B350','BE20','BE9L','BE9T','BE10','U21','C208','C08T','EPIC','E1000','P46T','M600','KODI','PC6T','SW4','DHC6','AT8T'}
HELI = {'AS50','AS55','B212','B06','B407','B429','B505','EC20','EC30','EC35','EC45','EC55','EC75','R22','R44','R66','S76','UH1','H500','MD50','A109','A139','H60','EH10'}
GLIDER = {'DISC','AS29','AS33','ASW2','LS8','LS4','LS6','DG80','DG40','DG1T','DUOD','ARCU','ARCP','NIMB','VENT','JS3','JS1','PK20','SZD5','ASK2','G103','SF25','ASW7','ASG2','ASG3','TWSH','PIK2','GROB'}

# Airports used to name origins/destinations (nearest within 6 nm of a ground fix).
AIRPORTS = [("KTRK Truckee",39.320,-120.140),("KRNO Reno",39.499,-119.768),
("KRTS Reno-Stead",39.668,-119.876),("KTVL S.Lake Tahoe",38.894,-119.995),
("KMEV Minden",38.998,-119.751),("KCXP Carson City",39.192,-119.734),
("KAUN Auburn",38.955,-121.082),("KBLU Blue Canyon",39.275,-120.708),
("KGOO Grass Valley",39.224,-121.003),("O02 Beckwourth",39.818,-120.352),
("2O1 Quincy",39.944,-120.945),("KSVE Susanville",40.376,-120.573),
("KPVF Placerville",38.724,-120.753),("KJAC Jackson Hole",43.607,-110.738),
("KSMF Sacramento Intl",38.695,-121.591),("KMCC McClellan",38.668,-121.401),
("KMHR Mather",38.554,-121.298),("KSAC Sac Exec",38.513,-121.493),
("KOAK Oakland",37.721,-122.221),("KSFO San Francisco",37.619,-122.375),
("KSJC San Jose",37.363,-121.929),("KAPC Napa",38.213,-122.281),
("KCCR Concord",37.990,-122.057),("KHWD Hayward",37.659,-122.122),
("KPAO Palo Alto",37.461,-122.115),("KSQL San Carlos",37.512,-122.250),
("KLVK Livermore",37.694,-121.820),("KVNY Van Nuys",34.210,-118.490),
("KBUR Burbank",34.201,-118.359),("KSNA John Wayne",33.676,-117.868),
("KLAS Las Vegas",36.080,-115.152),("KHND Henderson",35.973,-115.134),
("KSUN Sun Valley",43.504,-114.296),("KBZN Bozeman",45.777,-111.153),
("KSLC Salt Lake",40.788,-111.978),("KPVU Provo",40.219,-111.723),
("KBOI Boise",43.564,-116.223),("KEUG Eugene",44.125,-123.212),
("KPDX Portland",45.589,-122.597),("KSEA Seattle",47.450,-122.309),
("KBFI Boeing Fld",47.530,-122.302),("KPAE Paine Fld",47.906,-122.282),
("KSCK Stockton",37.894,-121.238),("KMOD Modesto",37.626,-120.954),
("KFAT Fresno",36.776,-119.718),("KHHR Hawthorne",33.923,-118.335),
("KSMO Santa Monica",34.016,-118.451),("KCMA Camarillo",34.214,-119.094),
("KSBA Santa Barbara",34.426,-119.840),("KMRY Monterey",36.587,-121.843)]

# ---------------------------------------------------------------- helpers

def hav_nm(lat1, lon1, lat2, lon2):
    r = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))

def nearest_airport(lat, lon):
    best = min(AIRPORTS, key=lambda ap: hav_nm(lat, lon, ap[1], ap[2]))
    if hav_nm(lat, lon, best[1], best[2]) < 6:
        return best[0]
    return f"({lat:.2f},{lon:.2f})"

def klass(t):
    if not t: return "unknown"
    if t in JET: return "jet"
    if t in AIRLINER: return "airliner"
    if t in TURBOPROP: return "turboprop"
    if t in HELI: return "helicopter"
    if t in GLIDER: return "glider"
    return "piston"

def fetch(url, path):
    """Download url to path with retry/backoff; cached if path already exists."""
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
            return path
        except Exception as e:
            if attempt == 3:
                print(f"  fetch fail {url}: {e}", file=sys.stderr)
                return None
            time.sleep(3 * (attempt + 1) + random.random() * 2)

def t2s(ts):
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%H:%M")

# ---------------------------------------------------------------- per-day run

def run_day(datestr):
    y, m, d = datestr.split("-")
    daydir = f"days/{datestr}"
    os.makedirs(f"{daydir}/heatmap", exist_ok=True)
    os.makedirs(f"{daydir}/traces", exist_ok=True)

    with cf.ThreadPoolExecutor(3) as ex:
        list(ex.map(lambda s: fetch(f"{BASE}/globe_history/{y}/{m}/{d}/heatmap/{s}.bin.ttf",
                                    f"{daydir}/heatmap/{s}.bin"), SLOTS))

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

    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda h: fetch(f"{BASE}/globe_history/{y}/{m}/{d}/traces/{h[-2:]}/trace_full_{h}.json",
                                    f"{daydir}/traces/{h}.json"), sel))

    W0 = datetime.datetime(int(y), int(m), int(d), *WINDOW[0], tzinfo=TZ).timestamp()
    W1 = datetime.datetime(int(y), int(m), int(d), *WINDOW[1], tzinfo=TZ).timestamp()
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
                    times["to"] = "LOCAL(returned)" if dest.split()[0] in AIRPORTS[0][0] else dest
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
    allops = []
    for ds in sys.argv[1:]:
        allops.extend(run_day(ds))
    json.dump(allops, open("data/all_ops.json", "w"), indent=1)
    for r in allops:
        tt = " ".join(f"{k}={v}" for k, v in r["times"].items())
        print(f'{r["date"]} {r["first"]}-{r["last"]} {r["reg"] or r["hex"]:8s} {r["type"] or "?":5s} '
              f'{r["class"]:10s} {"/".join(r["ops"]):12s} {tt} | {r["own"] or "?"}')
