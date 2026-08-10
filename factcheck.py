#!/usr/bin/env python3
"""Validate facts.json: the registry of factual claims behind the site's pages.

Checks schema, flags stale checks, requires notes on contested/held facts, and
verifies that referenced pages exist. Exit code 1 on errors; warnings print but
pass. Stdlib only. Shared verbatim across the truckee-* repos.
"""
import json
import os
import sys
import datetime

STATUSES = {"verified", "sourced", "contested", "held"}
METHODS = {"primary-document", "agency-record", "own-data", "secondary", "derived"}
STALE_DAYS = 180


def main():
    try:
        reg = json.load(open("facts.json"))
    except FileNotFoundError:
        print("facts.json not found")
        return 1
    except json.JSONDecodeError as e:
        print(f"facts.json is not valid JSON: {e}")
        return 1

    errors, warnings = [], []
    seen = set()
    today = datetime.date.today()

    for i, f in enumerate(reg.get("facts", [])):
        tag = f.get("id", f"facts[{i}]")
        if not f.get("id"):
            errors.append(f"facts[{i}]: missing id")
        elif f["id"] in seen:
            errors.append(f"{tag}: duplicate id")
        else:
            seen.add(f["id"])
        if not f.get("claim", "").strip():
            errors.append(f"{tag}: empty claim")
        if f.get("status") not in STATUSES:
            errors.append(f"{tag}: bad status {f.get('status')!r}")
        if f.get("method") not in METHODS:
            errors.append(f"{tag}: bad method {f.get('method')!r}")
        if f.get("status") in ("verified", "sourced") and not f.get("sources"):
            errors.append(f"{tag}: {f.get('status')} fact with no sources")
        if f.get("status") in ("contested", "held") and not f.get("notes", "").strip():
            errors.append(f"{tag}: {f.get('status')} fact needs notes")
        if "—" in f.get("claim", "") + f.get("notes", ""):
            errors.append(f"{tag}: em-dash in claim or notes")
        for p in f.get("pages", []):
            if not os.path.exists(p):
                errors.append(f"{tag}: page {p} does not exist")
        try:
            checked = datetime.date.fromisoformat(f.get("checked", ""))
            if (today - checked).days > STALE_DAYS:
                warnings.append(f"{tag}: last checked {f['checked']} "
                                f"({(today - checked).days} days ago)")
        except ValueError:
            errors.append(f"{tag}: bad checked date {f.get('checked')!r}")

    counts = {}
    for f in reg.get("facts", []):
        counts[f.get("status", "?")] = counts.get(f.get("status", "?"), 0) + 1
    print(f"{len(reg.get('facts', []))} facts: " +
          ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for w in warnings:
        print("WARN:", w)
    for e in errors:
        print("ERROR:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
