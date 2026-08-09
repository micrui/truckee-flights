#!/usr/bin/env python3
"""Refresh data/airports.csv from the OurAirports public-domain dataset.

Filters to US, Canada, and Mexico, drops closed fields, keeps heliports
(medevac destinations resolve to them). Run occasionally; commit the result.
"""
import csv, io, urllib.request

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
raw = urllib.request.urlopen(URL, timeout=120).read().decode("utf-8")
out = []
for r in csv.DictReader(io.StringIO(raw)):
    if r["iso_country"] not in ("US", "CA", "MX") or r["type"] == "closed":
        continue
    try:
        lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
    except ValueError:
        continue
    out.append((r["gps_code"] or r["ident"], f"{lat:.4f}", f"{lon:.4f}",
                r["municipality"] or r["name"], r["type"]))
with open("data/airports.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ident", "lat", "lon", "label", "type"])
    w.writerows(out)
print(f"wrote data/airports.csv: {len(out)} entries")
